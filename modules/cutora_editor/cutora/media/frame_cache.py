# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode-once LRU of premul QImages for short looping clips (GIF / APNG).

Animated stills have a handful of frames. Re-decoding the PNG-in-MOV (or
ffmpeg bgra pipe) every timeline frame is the lag the user sees. Decode each
source frame once, keep a premultiplied QImage, loop from RAM.

Budget defaults to 512 MB. Oldest clips are dropped first.
"""
from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict

from cutora.core.log import get_logger

_LOG = get_logger("cutora.media.frame_cache")

DEFAULT_BUDGET_BYTES = 512 * 1024 * 1024
MAX_FRAMES_PER_CLIP = 400
MAX_DURATION_S = 30.0
_SKIP = object()

# Tests patch / read these.
DECODE_CALLS = 0
DECODED_KEYS: set = set()
_DECODE_LOCK = threading.Lock()


def reset_decode_stats():
    global DECODE_CALLS
    DECODE_CALLS = 0
    DECODED_KEYS.clear()


def decode_clip_frame(path, frame_index, cap=None):
    """Read one source frame as premul BGRA ndarray. Counted by tests.

    Sequential callers that pass the same ``cap`` and increasing
    ``frame_index`` do not seek. A jump seeks.
    """
    global DECODE_CALLS
    import cv2
    from cutora.render.readers import _open_video_capture

    own = False
    if cap is None:
        cap = _open_video_capture(path, hw=False)
        if cap is None:
            cap = cv2.VideoCapture(path)
        own = True
    if cap is None or not cap.isOpened():
        if own and cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None
    try:
        want = int(frame_index)
        cur = getattr(cap, "_cutora_pos", None)
        if cur is None:
            try:
                cur = cap.get(cv2.CAP_PROP_POS_FRAMES)
            except Exception:
                cur = -1
        try:
            cur_i = int(cur) if cur is not None else -1
        except (TypeError, ValueError):
            cur_i = -1
        if cur_i != want:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, want))
            try:
                cap._cutora_pos = want
            except Exception:
                pass
        ret, frame = cap.read()
        try:
            cap._cutora_pos = want + 1
        except Exception:
            pass
        if not ret or frame is None:
            return None
        key = (os.path.abspath(path), want)
        with _DECODE_LOCK:
            DECODE_CALLS += 1
            DECODED_KEYS.add(key)
        return frame
    finally:
        if own:
            try:
                cap.release()
            except Exception:
                pass


def _clip_duration_s(clip) -> float:
    if clip is None:
        return 0.0
    if isinstance(clip, dict):
        try:
            inn = float(clip.get("in_t") or 0.0)
            out = float(clip.get("out_t") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    else:
        try:
            inn = float(getattr(clip, "in_t", 0.0) or 0.0)
            out = float(getattr(clip, "out_t", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return max(0.0, out - inn)


def should_cache_clip(clip=None, path=None, src=None) -> bool:
    """Looping stills and short alpha clips. Never a long camera take.

    ``loop`` on a multi-minute plate used to return True, and the cache then
    decoded the whole file on every preview frame once it exceeded the budget.
    Animated sources (GIF / APNG / ``_cutora_anim``) stay eligible regardless
    of the clip window — the source itself is a handful of frames.
    """
    kind = None
    if src is not None:
        kind = getattr(src, "kind", None) if not isinstance(src, dict) else src.get("kind")
    p = str(path or "")
    animated = kind in ("gif", "webp", "apng", "sequence", "animated") or (
        "_cutora_anim" in os.path.basename(p))
    if animated:
        return True
    looping = False
    if clip is not None:
        looping = bool(
            getattr(clip, "loop", False) if not isinstance(clip, dict) else clip.get("loop"))
    if not looping:
        return False
    return _clip_duration_s(clip) <= MAX_DURATION_S


def _qimage_from_bgra(frame):
    from PySide6.QtGui import QImage
    import numpy as np
    if frame is None:
        return None
    src = np.ascontiguousarray(frame)
    h, w = src.shape[:2]
    if src.ndim == 3 and src.shape[2] == 4:
        fmt = QImage.Format.Format_ARGB32_Premultiplied
        q = QImage(src.data, w, h, int(src.strides[0]), fmt)
        return q.copy()
    fmt = QImage.Format.Format_BGR888
    q = QImage(src.data, w, h, int(src.strides[0]), fmt)
    return q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _nbytes_qimage(img) -> int:
    if img is None or img.isNull():
        return 0
    return int(img.height()) * int(img.bytesPerLine())


class PremulFrameCache:
    def __init__(self, budget_bytes: int = DEFAULT_BUDGET_BYTES):
        self.budget = max(16 * 1024 * 1024, int(budget_bytes))
        self._clips: OrderedDict[str, dict] = OrderedDict()
        self._bytes = 0
        self._skip: set[str] = set()
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._clips.clear()
            self._bytes = 0
            self._skip.clear()

    def _note_skip(self, key: str) -> None:
        with self._lock:
            self._skip.add(key)

    def _log_skip(self, why: str, path: str) -> None:
        _LOG.info("skip frame cache, %s: %s", why, path)

    def _evict(self, need: int):
        while self._clips and self._bytes + need > self.budget:
            _k, old = self._clips.popitem(last=False)
            self._bytes -= int(old.get("nbytes") or 0)

    def _load(self, path: str, cap=None):
        import cv2
        from cutora.render.readers import _open_video_capture

        own = False
        if cap is None or not getattr(cap, "isOpened", lambda: False)():
            cap = _open_video_capture(path, hw=False)
            if cap is None:
                cap = cv2.VideoCapture(path)
            own = True
        if cap is None or not cap.isOpened():
            return None
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            if not fps or fps <= 0:
                fps = 10.0
            n_hint = 0
            ext = os.path.splitext(str(path or ""))[1].lower()
            if ext not in (".gif", ".webp", ".apng"):
                try:
                    n_hint = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                except (TypeError, ValueError):
                    n_hint = 0
            if n_hint <= 0:
                try:
                    from cutora.media.animated import _nb_frames
                    probed = _nb_frames(path)
                    if probed:
                        n_hint = int(probed)
                except Exception:
                    n_hint = 0
            if n_hint <= 0:
                try:
                    n_hint = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                except (TypeError, ValueError):
                    n_hint = 0
            try:
                fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            except Exception:
                fw, fh = 0, 0
            n_est = n_hint if n_hint > 0 else MAX_FRAMES_PER_CLIP
            if n_hint > MAX_FRAMES_PER_CLIP:
                self._log_skip("too many frames (%s)" % n_hint, path)
                return _SKIP
            if fw > 0 and fh > 0 and fw * fh * 4 * min(n_est, MAX_FRAMES_PER_CLIP) > self.budget:
                self._log_skip("over budget", path)
                return _SKIP
            try:
                cap._cutora_pos = 0
            except Exception:
                pass
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            try:
                cap._cutora_pos = 0
            except Exception:
                pass
            frames = []
            nbytes = 0
            i = 0
            n_stop = n_hint if 0 < n_hint <= MAX_FRAMES_PER_CLIP else MAX_FRAMES_PER_CLIP
            while i < n_stop:
                arr = decode_clip_frame(path, i, cap=cap)
                if arr is None:
                    break
                qimg = _qimage_from_bgra(arr)
                if qimg is None or qimg.isNull():
                    break
                frames.append(qimg)
                nbytes += _nbytes_qimage(qimg)
                if nbytes > self.budget:
                    self._log_skip("over budget", path)
                    return _SKIP
                i += 1
            if not frames:
                return None
            return {
                "path": path,
                "fps": float(fps),
                "frames": frames,
                "nbytes": nbytes,
            }
        finally:
            if own:
                try:
                    cap.release()
                except Exception:
                    pass

    def ensure(self, path: str, cap=None) -> dict | None:
        key = os.path.abspath(path) if path else ""
        if not key:
            return None
        with self._lock:
            if key in self._skip:
                return None
            hit = self._clips.get(key)
            if hit is not None:
                self._clips.move_to_end(key)
                return hit
        loaded = self._load(path, cap=cap)
        if loaded is _SKIP or loaded is None:
            self._note_skip(key)
            return None
        with self._lock:
            hit = self._clips.get(key)
            if hit is not None:
                self._clips.move_to_end(key)
                return hit
            self._evict(int(loaded["nbytes"]))
            self._clips[key] = loaded
            self._bytes += int(loaded["nbytes"])
            return loaded

    def qimage_at(self, path: str, src_t: float, cap=None, fps_hint: float | None = None):
        rec = self.ensure(path, cap=cap)
        if rec is None:
            return None
        frames = rec["frames"]
        n = len(frames)
        if n <= 0:
            return None
        fps = float(fps_hint or rec["fps"] or 10.0)
        if fps <= 0:
            fps = 10.0
        idx = int(math.floor(float(src_t) * fps + 1e-6)) % n
        if idx < 0:
            idx = 0
        with self._lock:
            if rec["path"] in self._clips:
                self._clips.move_to_end(os.path.abspath(path))
        return frames[idx]


_CACHE = PremulFrameCache()


def cache() -> PremulFrameCache:
    return _CACHE


def set_budget_bytes(n: int) -> None:
    _CACHE.budget = max(16 * 1024 * 1024, int(n))
    with _CACHE._lock:
        _CACHE._skip.clear()


def qimage_at(path, src_t, cap=None, fps_hint=None):
    return _CACHE.qimage_at(path, src_t, cap=cap, fps_hint=fps_hint)


def ensure(path, cap=None):
    return _CACHE.ensure(path, cap=cap)


def clear():
    _CACHE.clear()
