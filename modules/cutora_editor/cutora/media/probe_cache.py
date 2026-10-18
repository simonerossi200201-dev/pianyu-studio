# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent ffprobe cache. Key = (abspath, mtime_ns, size).

Duration and alpha live in the same record. Failed probes are kept in
memory with a TTL and are never written to disk, so a later open retries.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from cutora.core.config import FFPROBE_PATH
from cutora.core.log import get_logger
from cutora.core.paths import user_cache_dir

_LOG = get_logger("cutora.media.probe_cache")

CACHE_NAME = "media_probe.json"
CACHE_VERSION = 2
MAX_ENTRIES = 4096
PARALLEL_WORKERS = 4
FAIL_TTL_SEC = 600.0
ALPHA_EXTS = {
    ".mov", ".webm", ".gif", ".png", ".apng", ".tga", ".tif", ".tiff",
}

_lock = threading.RLock()
# key -> {"duration": float, "alpha": dict}  (only successful fields)
_mem: dict[str, dict] = {}
_fail_duration: dict[str, float] = {}
_fail_alpha: dict[str, float] = {}
_loaded = False
_dirty = False
_yield_ui = False


def _cache_path() -> str:
    return os.path.join(user_cache_dir(), CACHE_NAME)


def _fingerprint(path: str):
    from cutora.audio.waveform import file_fingerprint, fingerprint_key
    fp = file_fingerprint(path)
    if fp is None:
        return None, None
    return fp, fingerprint_key(fp)


def _now() -> float:
    return time.monotonic()


def _fail_fresh(table: dict[str, float], key: str) -> bool:
    ts = table.get(key)
    if ts is None:
        return False
    return (_now() - ts) < FAIL_TTL_SEC


def _record(key: str) -> dict:
    rec = _mem.get(key)
    if rec is None:
        rec = {}
        _mem[key] = rec
    return rec


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _cache_path()
    if not os.path.isfile(path):
        return
    try:
        data = json.loads(open(path, encoding="utf-8").read() or "{}")
    except (OSError, json.JSONDecodeError):
        _LOG.exception("probe cache read failed: %s", path)
        return
    if not isinstance(data, dict):
        return
    ver = int(data.get("v") or 0)
    if ver not in (1, CACHE_VERSION):
        return
    items = data.get("items")
    if not isinstance(items, dict):
        return
    for key, rec in items.items():
        if not isinstance(key, str):
            continue
        slot: dict = {}
        if isinstance(rec, dict):
            dur = rec.get("duration")
            if isinstance(dur, (int, float)):
                try:
                    slot["duration"] = float(dur)
                except (TypeError, ValueError):
                    pass
            alpha = rec.get("alpha")
            if isinstance(alpha, dict) and alpha:
                slot["alpha"] = alpha
        elif isinstance(rec, (int, float)):
            try:
                slot["duration"] = float(rec)
            except (TypeError, ValueError):
                continue
        # None / missing duration is a failed probe: do not restore it.
        if slot:
            _mem[key] = slot


def flush() -> None:
    global _dirty
    with _lock:
        if not _dirty:
            return
        _load()
        items = {}
        for key, rec in list(_mem.items()):
            if not isinstance(rec, dict):
                continue
            out = {}
            dur = rec.get("duration")
            if isinstance(dur, (int, float)):
                out["duration"] = float(dur)
            alpha = rec.get("alpha")
            if isinstance(alpha, dict) and alpha:
                out["alpha"] = alpha
            if out:
                items[key] = out
        if len(items) > MAX_ENTRIES:
            overflow = len(items) - MAX_ENTRIES
            for key in list(items.keys())[:overflow]:
                items.pop(key, None)
                _mem.pop(key, None)
        dest = _cache_path()
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = dest + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"v": CACHE_VERSION, "items": items}, fh, ensure_ascii=False)
            os.replace(tmp, dest)
            _dirty = False
        except OSError:
            _LOG.exception("probe cache write failed: %s", dest)
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass


def clear_memory() -> None:
    """Tests only: drop the in-process map. Disk file is left alone."""
    global _loaded, _dirty
    with _lock:
        _mem.clear()
        _fail_duration.clear()
        _fail_alpha.clear()
        _loaded = False
        _dirty = False


def reset() -> None:
    """Tests: drop memory and the on-disk cache file."""
    clear_memory()
    path = _cache_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _ffprobe_duration(media_path: str) -> float | None:
    import subprocess
    from cutora.core.proc import run
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        res = run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=20)
        if res.returncode != 0:
            return None
        val = (res.stdout or "").strip()
        if not val:
            return None
        dur = float(val)
        return dur if dur > 0 else None
    except Exception:
        _LOG.exception("ffprobe duration failed: %s", media_path)
        return None


def pump_ui() -> None:
    """Let Qt timers/paint run. No-op unless yielding_to_ui() is active."""
    if not _yield_ui:
        return
    try:
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    except Exception:
        pass


def _maybe_yield() -> None:
    pump_ui()


@contextmanager
def yielding_to_ui():
    """Open-project path: after each cache miss, let the GUI pump events."""
    global _yield_ui
    prev = _yield_ui
    _yield_ui = True
    try:
        yield
    finally:
        _yield_ui = prev
        flush()


def _store_duration(key: str, dur: float | None) -> None:
    global _dirty
    with _lock:
        if dur is None:
            _fail_duration[key] = _now()
            rec = _mem.get(key)
            if rec is not None:
                rec.pop("duration", None)
                if not rec:
                    _mem.pop(key, None)
            return
        _fail_duration.pop(key, None)
        rec = _record(key)
        rec["duration"] = float(dur)
        _dirty = True


def _store_alpha(key: str, info: dict | None) -> None:
    global _dirty
    with _lock:
        if not info:
            _fail_alpha[key] = _now()
            rec = _mem.get(key)
            if rec is not None:
                rec.pop("alpha", None)
                if not rec:
                    _mem.pop(key, None)
            return
        _fail_alpha.pop(key, None)
        rec = _record(key)
        rec["alpha"] = dict(info)
        _dirty = True


def _cached_duration(key: str):
    rec = _mem.get(key)
    if rec and "duration" in rec:
        return True, rec["duration"]
    if _fail_fresh(_fail_duration, key):
        return True, None
    return False, None


def _cached_alpha(key: str):
    rec = _mem.get(key)
    if rec and "alpha" in rec:
        return True, rec["alpha"]
    if _fail_fresh(_fail_alpha, key):
        return True, None
    return False, None


def probe_duration_sec(media_path) -> float | None:
    """Cached ffprobe duration. None if missing or unreadable."""
    path = str(media_path or "")
    if not path or not os.path.isfile(path):
        return None
    _fp, key = _fingerprint(path)
    if key is None:
        return None
    with _lock:
        _load()
        hit, dur = _cached_duration(key)
        if hit:
            return dur
    dur = _ffprobe_off_main(path)
    _store_duration(key, dur)
    _maybe_yield()
    return dur


def probe_alpha(media_path) -> dict | None:
    """Cached ffprobe alpha/stream info. None if missing or unreadable."""
    path = str(media_path or "")
    if not path:
        return None
    _fp, key = _fingerprint(path) if os.path.isfile(path) else (None, None)
    if key is None and not _is_sequence_probe_path(path):
        return None
    if key is not None:
        with _lock:
            _load()
            hit, info = _cached_alpha(key)
            if hit:
                return info
    info = _ffprobe_alpha_uncached(path)
    if key is not None:
        _store_alpha(key, info)
        _maybe_yield()
    return info


def _is_sequence_probe_path(path: str) -> bool:
    try:
        from cutora.render.readers import _is_sequence_path
        return bool(_is_sequence_path(path))
    except Exception:
        return False


def _ffprobe_alpha_uncached(path: str) -> dict | None:
    from cutora.render.readers import ffprobe_video_has_alpha_uncached
    return ffprobe_video_has_alpha_uncached(path)


def _ffprobe_off_main(media_path: str) -> float | None:
    """Don't freeze the GUI thread on a synchronous ffprobe."""
    if not _yield_ui:
        return _ffprobe_duration(media_path)
    box: dict = {}

    def work():
        box["v"] = _ffprobe_duration(media_path)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while worker.is_alive():
        pump_ui()
        worker.join(timeout=0.01)
    return box.get("v")


def _wants_alpha(path: str) -> bool:
    if _is_sequence_probe_path(path):
        return True
    ext = os.path.splitext(path)[1].lower()
    return ext in ALPHA_EXTS


def collect_project_media_paths(data) -> list[str]:
    """Unique existing media paths referenced by a project JSON dict."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw):
        path = str(raw or "")
        if not path:
            return
        try:
            path = os.path.abspath(path)
        except OSError:
            return
        key = os.path.normcase(path)
        if key in seen:
            return
        seen.add(key)
        if os.path.isfile(path) or _is_sequence_probe_path(path):
            out.append(path)

    def src_path(src):
        if src is None:
            return ""
        if isinstance(src, dict):
            return src.get("path") or ""
        return getattr(src, "path", "") or ""

    for task in (data or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        add(task.get("video") or task.get("path"))
        vo = task.get("video_overlay")
        if isinstance(vo, dict):
            add(vo.get("path"))
        for ac in task.get("audio_clips") or []:
            if isinstance(ac, dict):
                add(ac.get("path"))
        for ov in (task.get("image_overlays") or []) + (task.get("text_overlays") or []):
            if isinstance(ov, dict):
                add(ov.get("path") or ov.get("src") or ov.get("file"))
        tl = task.get("timeline")
        sources = {}
        tracks = []
        if isinstance(tl, dict):
            sources = tl.get("sources") or {}
            tracks = tl.get("tracks") or []
        elif tl is not None:
            sources = getattr(tl, "sources", None) or {}
            tracks = getattr(tl, "tracks", None) or []
        if isinstance(sources, dict):
            for src in sources.values():
                add(src_path(src))
        for tr in tracks or []:
            clips = tr.get("clips") if isinstance(tr, dict) else getattr(tr, "clips", None)
            for cl in clips or []:
                if isinstance(cl, dict):
                    add(cl.get("intro_video_path") or cl.get("path"))
                else:
                    add(getattr(cl, "intro_video_path", None) or getattr(cl, "path", None))
    return out


def prefetch_paths(paths, max_workers: int = PARALLEL_WORKERS, on_progress=None,
                   include_alpha: bool = True) -> None:
    """Probe every path that is not already a successful cache hit.

    Failed in-memory entries are retried (this is a new open). None is not
    written to disk. Runs up to ``max_workers`` ffprobes at once.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        path = str(raw or "")
        if not path:
            continue
        try:
            path = os.path.abspath(path)
        except OSError:
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    jobs: list[tuple[str, str, bool, bool]] = []
    with _lock:
        _load()
        for path in unique:
            _fp, key = _fingerprint(path)
            if key is None:
                continue
            # New open: retry previous failures for these paths.
            _fail_duration.pop(key, None)
            _fail_alpha.pop(key, None)
            rec = _mem.get(key) or {}
            need_dur = "duration" not in rec
            need_alpha = bool(include_alpha and _wants_alpha(path) and "alpha" not in rec)
            if need_dur or need_alpha:
                jobs.append((path, key, need_dur, need_alpha))

    total = len(unique)
    done_n = total - len(jobs)
    if callable(on_progress) and total:
        on_progress(done_n, total)
    if not jobs:
        return

    workers = max(1, int(max_workers or PARALLEL_WORKERS))
    alpha_fn = None
    if any(need_alpha for _p, _k, _d, need_alpha in jobs):
        # Import on this thread. workers must not import Qt/cv2.
        from cutora.render.readers import ffprobe_video_has_alpha_uncached
        alpha_fn = ffprobe_video_has_alpha_uncached

    def _job(path, key, need_dur, need_alpha):
        dur = _ffprobe_duration(path) if need_dur else None
        alpha = alpha_fn(path) if need_alpha and alpha_fn is not None else None
        return path, key, need_dur, dur, need_alpha, alpha

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(_job, path, key, need_dur, need_alpha)
            for path, key, need_dur, need_alpha in jobs
        ]
        for fut in as_completed(futs):
            try:
                _path, key, need_dur, dur, need_alpha, alpha = fut.result()
            except Exception:
                _LOG.exception("prefetch probe failed")
                done_n += 1
                if callable(on_progress):
                    on_progress(min(done_n, total), total)
                pump_ui()
                continue
            if need_dur:
                _store_duration(key, dur)
            if need_alpha:
                _store_alpha(key, alpha)
            done_n += 1
            if callable(on_progress):
                on_progress(min(done_n, total), total)
            pump_ui()
    flush()
