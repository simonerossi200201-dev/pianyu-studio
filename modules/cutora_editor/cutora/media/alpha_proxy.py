# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview proxy for heavy alpha video (ProRes 4444). Export always uses original."""
from __future__ import annotations

import json
import os
import threading
import time

import numpy as np

from cutora.core.config import FFMPEG_PATH
from cutora.core.log import get_logger
from cutora.core.paths import user_cache_dir
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

_LOG = get_logger("cutora.media.alpha_proxy")

PROXY_THRESHOLD_MS = 8.0
ENCODING_FLOOR_MAD = 18.0
PROXY_MAX_EDGE = 1280
_LOCK = threading.Lock()
_INFLIGHT = set()
_PENDING = {}  # abspath -> {item_json, host}
_HUB = None


def measure_decode_ms(path: str, n: int = 8) -> float:
    from cutora.render.readers import _open_video_capture
    cap = _open_video_capture(path, hw=False)
    if cap is None or not cap.isOpened():
        _LOG.warning("measure_decode_ms: cannot open %s", path)
        return 0.0
    try:
        cap.read()
        t0 = time.perf_counter()
        got = 0
        for _ in range(max(1, int(n))):
            ok, fr = cap.read()
            if not ok or fr is None:
                break
            got += 1
        dt = time.perf_counter() - t0
        if got <= 0:
            return 0.0
        return (dt / got) * 1000.0
    finally:
        try:
            cap.release()
        except Exception:
            _LOG.exception("release after measure_decode_ms")


def proxy_path_for(src: str) -> str:
    from cutora.audio.waveform import file_fingerprint
    root = os.path.join(user_cache_dir(), "alpha_proxy")
    os.makedirs(root, exist_ok=True)
    fp = file_fingerprint(src)
    if fp is None:
        tag = "missing"
    else:
        _p, mtime_ns, size = fp
        tag = "%d_%d" % (int(mtime_ns), int(size))
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(root, "%s._proxy.%s" % (stem, tag))


def preview_path_for(src: str) -> str:
    base = proxy_path_for(src)
    mov = base + ".mov"
    if os.path.isfile(mov):
        return mov
    webm = base + ".webm"
    if os.path.isfile(webm):
        return webm
    first = os.path.join(base, "f_0001.png")
    if os.path.isfile(first):
        return os.path.join(base, "f_%04d.png")
    return src


def report_proxy_status(host, msg: str) -> None:
    """Main-thread only. Worker threads must emit a signal instead."""
    _LOG.warning("%s", msg)
    if host is None:
        return
    bar_fn = getattr(host, "statusBar", None)
    if callable(bar_fn):
        try:
            bar = bar_fn()
            show = getattr(bar, "showMessage", None)
            if callable(show):
                show(msg, 8000)
        except Exception:
            _LOG.exception("statusBar.showMessage failed for alpha proxy")


def _scale_vf(width: int, height: int, max_edge: int = PROXY_MAX_EDGE) -> str | None:
    w, h = int(width or 0), int(height or 0)
    if w <= 0 or h <= 0:
        return None
    long = max(w, h)
    if long <= max_edge:
        return None
    s = max_edge / float(long)
    nw = max(2, int(round(w * s / 2.0) * 2))
    nh = max(2, int(round(h * s / 2.0) * 2))
    return "scale=%d:%d" % (nw, nh)


def _encode_prores_proxy(src: str, dest: str, vf: list[str]) -> bool:
    import subprocess
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    cmd = [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-an"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", dest]
    r = _no_window_run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0 or not os.path.isfile(dest):
        _LOG.error("ProRes 4444 proxy failed: %s", (r.stderr or b"")[-400:])
        try:
            if os.path.isfile(dest):
                os.remove(dest)
        except Exception:
            _LOG.exception("remove failed ProRes proxy")
        return False
    return _proxy_has_alpha(dest)


def _encode_qtrle_proxy(src: str, dest: str, vf: list[str]) -> bool:
    import subprocess
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    cmd = [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-an"]
    chain = list(vf) + ["format=argb"]
    cmd += ["-vf", ",".join(chain), "-c:v", "qtrle", "-pix_fmt", "argb", dest]
    r = _no_window_run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0 or not os.path.isfile(dest):
        _LOG.error("qtrle proxy failed: %s", (r.stderr or b"")[-400:])
        try:
            if os.path.isfile(dest):
                os.remove(dest)
        except Exception:
            _LOG.exception("remove failed qtrle proxy")
        return False
    return _proxy_has_alpha(dest)


def _encode_png_seq(src: str, dest_dir: str, vf: list[str]) -> bool:
    import subprocess
    os.makedirs(dest_dir, exist_ok=True)
    pattern = os.path.join(dest_dir, "f_%04d.png")
    cmd = [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    chain = list(vf) + ["format=rgba"]
    if chain:
        cmd += ["-vf", ",".join(chain)]
    cmd += ["-vsync", "0", "-pix_fmt", "rgba", pattern]
    r = _no_window_run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0 or not os.path.isfile(os.path.join(dest_dir, "f_0001.png")):
        _LOG.error("PNG-sequence proxy failed: %s", (r.stderr or b"")[-400:])
        return False
    return True


def _proxy_has_alpha(dest: str) -> bool:
    from cutora.render.readers import _open_video_capture, _probe_video_has_alpha
    info = _probe_video_has_alpha(dest)
    if not info or not info.get("has_alpha"):
        _LOG.error("proxy has no alpha plane (pix=%s), discarding",
                   (info or {}).get("pix_fmt"))
        try:
            os.remove(dest)
        except Exception:
            _LOG.exception("remove failed proxy without alpha")
        return False
    cap = _open_video_capture(dest, hw=False)
    try:
        ok, frame = (cap.read() if cap is not None else (False, None))
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                _LOG.exception("release proxy probe")
    if (not ok) or frame is None or getattr(frame, "ndim", 0) != 3 or frame.shape[2] != 4:
        _LOG.error("proxy decoded without BGRA")
        try:
            os.remove(dest)
        except Exception:
            _LOG.exception("remove failed proxy")
        return False
    if float(np.var(frame[..., 3].astype(np.float64))) <= 0:
        _LOG.error("proxy alpha variance is 0")
        try:
            os.remove(dest)
        except Exception:
            _LOG.exception("remove failed proxy")
        return False
    return True


def _build_proxy(src: str) -> str | None:
    base = proxy_path_for(src)
    mov = base + ".mov"
    if os.path.isfile(mov):
        return mov
    webm = base + ".webm"
    if os.path.isfile(webm):
        return webm
    seq_dir = base
    if os.path.isfile(os.path.join(seq_dir, "f_0001.png")):
        return os.path.join(seq_dir, "f_%04d.png")
    from cutora.render.readers import _probe_video_has_alpha
    info = _probe_video_has_alpha(src) or {}
    vf = []
    scale = _scale_vf(int(info.get("width") or 0), int(info.get("height") or 0))
    if scale:
        vf.append(scale)
    if _encode_prores_proxy(src, mov, vf):
        return mov
    if _encode_qtrle_proxy(src, mov, vf):
        return mov
    if _encode_png_seq(src, seq_dir, vf):
        return os.path.join(seq_dir, "f_%04d.png")
    return None


def _src_key(src: str) -> str:
    return os.path.normcase(os.path.abspath(src))


def apply_preview_path(src: str, proxy: str, item_json: str | None = None,
                       host=None) -> None:
    """Write item.json and live MediaSource.preview_path. Main thread."""
    if proxy:
        if item_json and os.path.isfile(item_json):
            try:
                with open(item_json, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data["preview_path"] = proxy
                    with open(item_json, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, ensure_ascii=False, indent=2)
                        fh.write("\n")
            except Exception:
                _LOG.exception("write preview_path failed: %s", item_json)
        key = _src_key(src)
        hosts = []
        if host is not None:
            hosts.append(host)
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                for w in app.topLevelWidgets():
                    if w not in hosts:
                        hosts.append(w)
        except Exception:
            _LOG.exception("scan top-level widgets for proxy write-back")
        for h in hosts:
            _patch_host_sources(h, key, proxy)


def _patch_host_sources(host, src_key: str, proxy: str) -> None:
    tasks = getattr(host, "tasks", None)
    if not tasks:
        return
    for task in tasks:
        tl = None
        if isinstance(task, dict):
            tl = task.get("timeline")
        else:
            tl = getattr(task, "timeline", None)
        sources = getattr(tl, "sources", None) if tl is not None else None
        if not sources:
            continue
        for s in sources.values():
            path = getattr(s, "path", None)
            if path is None and isinstance(s, dict):
                path = s.get("path")
            if not path:
                continue
            try:
                same = _src_key(path) == src_key
            except Exception:
                same = False
            if not same:
                continue
            if isinstance(s, dict):
                s["preview_path"] = proxy
            else:
                s.preview_path = proxy


def _on_hub_ready(src: str, proxy: str) -> None:
    with _LOCK:
        pending = _PENDING.pop(_src_key(src), {}) or {}
    apply_preview_path(
        src, proxy, item_json=pending.get("item_json"), host=pending.get("host"))
    host = pending.get("host")
    report_proxy_status(host, "透明素材代理已就绪：%s" % os.path.basename(src))


def _on_hub_failed(src: str, msg: str) -> None:
    with _LOCK:
        pending = _PENDING.pop(_src_key(src), {}) or {}
    report_proxy_status(pending.get("host"), msg)


def _hub():
    """QObject that lives on the GUI thread. None if Qt is not running."""
    global _HUB
    if _HUB is not None:
        return _HUB
    try:
        from PySide6.QtCore import QObject, Qt, Signal
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    app = QApplication.instance()
    if app is None:
        return None

    class _Hub(QObject):
        ready = Signal(str, str)
        failed = Signal(str, str)

    hub = _Hub()
    try:
        hub.moveToThread(app.thread())
    except Exception:
        _LOG.exception("move proxy hub to GUI thread")
    hub.ready.connect(_on_hub_ready, Qt.ConnectionType.QueuedConnection)
    hub.failed.connect(_on_hub_failed, Qt.ConnectionType.QueuedConnection)
    _HUB = hub
    return _HUB


def _emit_proxy_result(src: str, proxy: str | None, fail_msg: str | None) -> None:
    hub = _hub()
    if hub is not None:
        if proxy:
            hub.ready.emit(src, proxy)
        else:
            hub.failed.emit(src, fail_msg or "透明素材代理生成失败")
        return
    with _LOCK:
        pending = _PENDING.pop(_src_key(src), {}) or {}
    if proxy:
        apply_preview_path(
            src, proxy, item_json=pending.get("item_json"),
            host=pending.get("host"))
    else:
        _LOG.error("%s", fail_msg or "alpha proxy failed")


def _run_build(src: str) -> str | None:
    """Worker-thread body: no widget access."""
    try:
        out = _build_proxy(src)
        if not out:
            _emit_proxy_result(
                src, None,
                "透明素材代理生成失败，预览走原件（导出仍用原件）：%s"
                % os.path.basename(src))
            return None
        _LOG.info("alpha proxy ready: %s -> %s", src, out)
        _emit_proxy_result(src, out, None)
        return out
    except Exception:
        _LOG.exception("alpha proxy crashed: %s", src)
        _emit_proxy_result(
            src, None,
            "透明素材代理异常，预览走原件：%s" % os.path.basename(src))
        return None
    finally:
        with _LOCK:
            _INFLIGHT.discard(os.path.abspath(src))


def ensure_alpha_proxy(src: str, host=None, threshold_ms: float | None = None,
                       sync: bool = False, baseline_ms: float | None = None,
                       item_json: str | None = None) -> str | None:
    """If decode is > threshold above baseline, make a preview proxy.

    Never blocks import when ``sync=False``. Failure → status bar (main
    thread), returns None.
    """
    if not src or not os.path.isfile(src):
        return None
    with _LOCK:
        _PENDING[_src_key(src)] = {"item_json": item_json, "host": host}
    existing = preview_path_for(src)
    if existing != src:
        _emit_proxy_result(src, existing, None)
        return existing
    limit = PROXY_THRESHOLD_MS if threshold_ms is None else float(threshold_ms)
    try:
        cost = measure_decode_ms(src, n=6)
    except Exception:
        _LOG.exception("measure_decode_ms failed: %s", src)
        _emit_proxy_result(
            src, None,
            "透明素材测速失败，预览将走原件：%s" % os.path.basename(src))
        return None
    base = float(baseline_ms or 0.0)
    delta = cost - base
    if delta <= limit:
        _LOG.info("alpha decode +%.2f ms/frame ≤ %.2f, no proxy (%s)", delta, limit, src)
        if limit > 0:
            with _LOCK:
                _PENDING.pop(_src_key(src), None)
            return None
    key = os.path.abspath(src)
    with _LOCK:
        if key in _INFLIGHT and not sync:
            return None
        _INFLIGHT.add(key)

    if sync:
        return _run_build(src)
    threading.Thread(target=_run_build, args=(src,), name="alpha-proxy", daemon=True).start()
    return None


def frames_mad(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 1e9
    aa = a[..., 3] if a.ndim == 3 and a.shape[2] == 4 else None
    if aa is not None:
        mask = aa > 40
        if not mask.any():
            return 0.0
        return float(np.abs(a[..., :3].astype(np.int16) - b[..., :3].astype(np.int16))[mask].mean())
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
