# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background import probe: media_guard + one ffprobe, cached by fingerprint.

Jobs run on QThreadPool.globalInstance() (same pool as timeline thumbs and
preview-wav). The GUI thread only queues the job and applies the result.
"""
from __future__ import annotations

import os
import threading

from cutora.core.log import get_logger

_LOG = get_logger("cutora.media.import_probe")

DURATION_PENDING_LABEL = "读取中…"
_IMPORT_CACHE_NAME = "import_probe.json"
_IMPORT_CACHE_VERSION = 1
_MAX_IMPORT_CACHE = 4096

_keep_signals: list = []
_import_mem: dict = {}
_import_loaded = False
_inflight_lock = threading.Lock()
_inflight: dict = {}


def format_duration_label(duration) -> str:
    if duration is None:
        return DURATION_PENDING_LABEL
    try:
        sec = float(duration)
    except (TypeError, ValueError):
        return DURATION_PENDING_LABEL
    if sec < 0:
        sec = 0.0
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def library_item_text(name, duration, *, error=None, pending=True) -> str:
    base = base_name(name) or "未命名"
    if error:
        return "%s  无法导入" % base
    if duration is None and not pending:
        # 老工程里没存时长，又不是正在探测 —— 别挂着「读取中…」不动
        return base
    return "%s  %s" % (base, format_duration_label(duration))


def _split_path(path: str):
    """按 / 和 \\ 一起切。工程是在 Windows 上做的，路径里是反斜杠，
    但测试和批量工具跑在别的系统上 —— 两种都要认。"""
    raw = str(path or "").replace("\\", "/")
    return [x for x in raw.split("/") if x not in ("", ".", "..")]


def base_name(path: str) -> str:
    parts = _split_path(path)
    return parts[-1] if parts else ""


def _cache_path() -> str:
    from cutora.core.paths import user_cache_dir
    return os.path.join(user_cache_dir(), _IMPORT_CACHE_NAME)


def _fp_key(path: str) -> str | None:
    from cutora.audio.waveform import file_fingerprint, fingerprint_key
    fp = file_fingerprint(path)
    if fp is None:
        return None
    return fingerprint_key(fp)


def _load_import_cache() -> None:
    global _import_loaded
    if _import_loaded:
        return
    _import_loaded = True
    path = _cache_path()
    if not os.path.isfile(path):
        return
    try:
        import json
        data = json.loads(open(path, encoding="utf-8").read() or "{}")
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or int(data.get("v") or 0) != _IMPORT_CACHE_VERSION:
        return
    items = data.get("items")
    if isinstance(items, dict):
        for k, rec in items.items():
            if isinstance(k, str) and isinstance(rec, dict):
                _import_mem[k] = rec


def _flush_import_cache() -> None:
    import json
    dest = _cache_path()
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    items = dict(_import_mem)
    if len(items) > _MAX_IMPORT_CACHE:
        for k in list(items.keys())[: len(items) - _MAX_IMPORT_CACHE]:
            items.pop(k, None)
            _import_mem.pop(k, None)
    tmp = dest + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"v": _IMPORT_CACHE_VERSION, "items": items}, fh, ensure_ascii=False)
        os.replace(tmp, dest)
    except OSError:
        _LOG.exception("import probe cache write failed: %s", dest)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def reset_import_cache() -> None:
    """Tests: drop the in-process import cache. Disk file is left to CUTORA_USER_DATA."""
    global _import_loaded
    _import_mem.clear()
    _import_loaded = False
    with _inflight_lock:
        _inflight.clear()


def lookup_import(path: str) -> dict | None:
    key = _fp_key(path)
    if key is None:
        return None
    _load_import_cache()
    rec = _import_mem.get(key)
    return rec if isinstance(rec, dict) and rec else None


def store_import(path: str, rec: dict) -> None:
    key = _fp_key(path)
    if key is None:
        return
    _load_import_cache()
    _import_mem[key] = dict(rec or {})
    _flush_import_cache()
    info = (rec or {}).get("info")
    if isinstance(info, dict) and info:
        _feed_duration_cache(path, info)


def _feed_duration_cache(path: str, info: dict) -> None:
    """Same fingerprint as probe_cache, so a later duration lookup hits."""
    try:
        dur = float(info.get("duration") or 0.0)
    except (TypeError, ValueError):
        return
    if dur <= 0:
        return
    from cutora.media.probe_cache import _fingerprint, _store_duration
    _fp, key = _fingerprint(path)
    if key is None:
        return
    _store_duration(key, dur)


def _probe_file_uncached(path: str) -> dict:
    """One inspect_clip (magic + boxes + ffprobe). Tests patch this."""
    from cutora.batch.media_guard import FATAL, inspect_clip

    issues, info = inspect_clip(path)
    fatal = [i for i in issues if getattr(i, "level", "") == FATAL]
    rec = {
        "ok": not fatal,
        "info": info,
        "issues": [i.as_dict() for i in issues],
        "fatal": [i.as_dict() for i in fatal],
        "error": "\n".join("%s：%s" % (i.name, i.msg) for i in fatal),
    }
    return rec


def probe_file_for_import(path: str) -> dict:
    """Cached import probe. Key = path + mtime + size."""
    hit = lookup_import(path)
    if hit is not None:
        return hit
    rec = _probe_file_uncached(path)
    store_import(path, rec)
    return rec


def queue_import_probe(path: str, on_done) -> None:
    """Run probe_file_for_import off the GUI thread. on_done(path, rec) on GUI.

    A second request for the same path+mtime+size while the first job is
    still running shares that job. The cache inside probe_file_for_import
    covers a request that arrives after the first one has finished.
    """
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

    key = _fp_key(path)
    if key:
        with _inflight_lock:
            waiters = _inflight.get(key)
            if waiters is not None:
                waiters.append(on_done)
                return
            _inflight[key] = [on_done]

    class _Sig(QObject):
        done = Signal(str, object)

    class _Job(QRunnable):
        def __init__(self, p, sig):
            super().__init__()
            self._p = p
            self._sig = sig
            self.setAutoDelete(True)

        def run(self):
            rec = None
            try:
                rec = probe_file_for_import(self._p)
            except Exception as exc:
                _LOG.exception("import probe failed: %s", self._p)
                rec = {
                    "ok": False,
                    "info": None,
                    "issues": [],
                    "fatal": [],
                    "error": str(exc) or "探测失败",
                }
            try:
                self._sig.done.emit(self._p, rec)
            except RuntimeError:
                pass

    sig = _Sig()
    _keep_signals.append(sig)

    def _drop(p, rec, _key=key):
        try:
            _keep_signals.remove(sig)
        except ValueError:
            pass
        if _key:
            with _inflight_lock:
                cbs = list(_inflight.pop(_key, []))
        else:
            cbs = [on_done]
        for cb in cbs:
            if callable(cb):
                cb(p, rec)

    sig.done.connect(_drop)
    QThreadPool.globalInstance().start(_Job(path, sig))
