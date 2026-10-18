# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T22 track thumbnails. Cache key = path + mtime + size + t + wh. 512 MB cap."""
from __future__ import annotations

import hashlib
import os
import subprocess

from cutora.audio.waveform import (
    MAX_WAVEFORM_CACHE_BYTES,
    file_fingerprint,
    fingerprint_key,
)
from cutora.core.config import FFMPEG_PATH
from cutora.core.counters import bump as _bump
from cutora.core.paths import user_cache_dir
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

MAX_THUMB_CACHE_BYTES = MAX_WAVEFORM_CACHE_BYTES
THUMB_PREFETCH_CLIPS = 1


def thumbs_dir() -> str:
    d = os.path.join(user_cache_dir(), "tl_thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def thumb_key(path: str, t: float, w: int, h: int) -> str | None:
    fp = file_fingerprint(path)
    if fp is None:
        return None
    raw = "%s|%.3f|%dx%d" % (fingerprint_key(fp), float(t), int(w), int(h))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def thumb_path(key: str) -> str:
    return os.path.join(thumbs_dir(), key + ".jpg")


def prune_thumb_cache(max_bytes: int = MAX_THUMB_CACHE_BYTES) -> int:
    d = thumbs_dir()
    entries = []
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            stamp = getattr(st, "st_atime", None) or st.st_mtime
            entries.append((stamp, st.st_size, p))
    except OSError:
        return 0
    total = sum(sz for _t, sz, _p in entries)
    if total <= max_bytes:
        return 0
    entries.sort()
    removed = 0
    for _t, sz, p in entries:
        if total <= max_bytes:
            break
        try:
            os.remove(p)
            total -= sz
            removed += 1
        except OSError:
            pass
    return removed


def extract_frame(path: str, t: float, dest: str, w: int = 80, h: int = 45) -> bool:
    if not path or not os.path.isfile(path):
        return False
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "%.3f" % max(0.0, float(t)),
        "-i", path,
        "-frames:v", "1",
        # Q169：铺满后居中裁切（竖版视频以前是两边黑边、中间一条细图）。片段条上要的是一张整齐的小方块。
        "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,"
               "crop=%d:%d" % (int(w), int(h), int(w), int(h)),
        dest,
    ]
    try:
        r = _no_window_run(cmd, capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def peek_clip_thumb(path: str, t: float, w: int = 80, h: int = 45) -> str | None:
    """Cached jpeg only. Paint path must not spawn ffmpeg."""
    key = thumb_key(path, t, w, h)
    if not key:
        return None
    dest = thumb_path(key)
    if os.path.isfile(dest):
        try:
            os.utime(dest, None)
        except OSError:
            pass
        return dest
    return None


def get_clip_thumb(path: str, t: float, w: int = 80, h: int = 45) -> str | None:
    """Return cached jpeg path, generating if needed. None on miss/fail."""
    hit = peek_clip_thumb(path, t, w, h)
    if hit:
        return hit
    key = thumb_key(path, t, w, h)
    if not key:
        return None
    dest = thumb_path(key)
    if extract_frame(path, t, dest, w, h):
        _bump("thumb.ffmpeg_ok")
        prune_thumb_cache()
        return dest
    _bump("thumb.ffmpeg_fail")
    return None


_request_log: list[tuple] = []
_pool = None
_running: set[str] = set()
_keep_signals: list = []


def _thumb_pool():
    global _pool
    if _pool is None:
        from PySide6.QtCore import QThreadPool
        _pool = QThreadPool.globalInstance()
    return _pool


def request_clip_thumb(path: str, t: float, w: int = 80, h: int = 45, on_done=None) -> None:
    """Queue background generation. Paint must keep using peek_clip_thumb."""
    rec = (path, float(t), int(w), int(h))
    _request_log.append(rec)
    if len(_request_log) > 2000:                 # Q189f：以前无限增长（没人 take 的话）
        del _request_log[:1000]
    _bump("thumb.request")
    if peek_clip_thumb(path, t, w, h):
        _bump("thumb.cached")
        return
    key = thumb_key(path, t, w, h)
    if not key or key in _running:
        return
    _running.add(key)

    from PySide6.QtCore import QRunnable, QObject, Signal, Slot

    class _Sig(QObject):
        done = Signal(str)

    class _Job(QRunnable):
        def __init__(self, p, tt, ww, hh, k, sig):
            super().__init__()
            self._p, self._t, self._w, self._h, self._k = p, tt, ww, hh, k
            self._sig = sig
            self.setAutoDelete(True)

        def run(self):
            try:
                get_clip_thumb(self._p, self._t, self._w, self._h)
            finally:
                _running.discard(self._k)
                self._sig.done.emit(self._k)

    sig = _Sig()
    _keep_signals.append(sig)

    def _drop(_k=""):
        try:
            _keep_signals.remove(sig)
        except ValueError:
            pass
        if callable(on_done):
            on_done(_k)

    sig.done.connect(_drop)
    _thumb_pool().start(_Job(path, t, w, h, key, sig))


def take_request_log() -> list[tuple]:
    out = list(_request_log)
    _request_log.clear()
    return out
