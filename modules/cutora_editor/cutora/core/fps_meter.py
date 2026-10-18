# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview/export FPS math. No Qt — the status-bar capsule lives in ui.widgets.

Timestamps are a deque of the last RING_SIZE composite/paint instants.
FPS = (n - 1) / (t_last - t_first). One append per frame; no extra thread.
"""
from __future__ import annotations

from collections import deque

import time

RING_SIZE = 30
THROTTLE_S = 0.5
WARN_RATIO = 0.80
ERR_RATIO = 0.50
DEFAULT_TARGET_FPS = 30.0

_ring: deque[float] = deque(maxlen=RING_SIZE)
_counting = False


class FpsRing:
    """Last ``size`` timestamps. fps = (n-1) / span."""

    def __init__(self, size: int = RING_SIZE) -> None:
        self._ts: deque[float] = deque(maxlen=max(2, int(size)))

    def push(self, t: float | None = None) -> None:
        self._ts.append(time.perf_counter() if t is None else float(t))

    def fps(self) -> float:
        n = len(self._ts)
        if n < 2:
            return 0.0
        span = self._ts[-1] - self._ts[0]
        if span <= 0.0:
            return 0.0
        return (n - 1) / span

    def count(self) -> int:
        return len(self._ts)

    def clear(self) -> None:
        self._ts.clear()

    def last_t(self) -> float | None:
        return self._ts[-1] if self._ts else None


def color_token(fps: float, target: float = DEFAULT_TARGET_FPS) -> str:
    """``ok`` / ``warn`` / ``err`` vs target. warn < 80%, err < 50%."""
    tgt = float(target)
    if tgt <= 0.0:
        return "ok"
    ratio = float(fps) / tgt
    if ratio < ERR_RATIO:
        return "err"
    if ratio < WARN_RATIO:
        return "warn"
    return "ok"


def export_fps(frames: int, elapsed_s: float) -> float:
    """frames / elapsed seconds. Zero when either side is not positive."""
    if elapsed_s <= 0.0 or frames <= 0:
        return 0.0
    return float(frames) / float(elapsed_s)


def format_remain(seconds: float) -> str:
    s = max(0, int(round(float(seconds))))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return "%d 小时 %d 分 %d 秒" % (h, m, s)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def format_preview_tooltip(fps: float, target: float, n: int) -> str:
    tgt = float(target)
    tgt_s = str(int(tgt)) if tgt == int(tgt) else ("%.1f" % tgt)
    return "预览 %.1f fps（目标 %s）· 最近 %d 帧" % (fps, tgt_s, int(n))


def format_export_tooltip(
    fps: float,
    done: int = 0,
    total: int = 0,
    remain_s: float | None = None,
) -> str:
    parts = ["导出 %.1f fps" % float(fps)]
    if int(total) > 0 and int(done) >= 0:
        parts.append("已 %d/%d 帧" % (int(done), int(total)))
    if remain_s is not None and remain_s > 0:
        parts.append("预计剩余 %s" % format_remain(remain_s))
    return " · ".join(parts)


def note_preview_frame(t: float | None = None) -> None:
    _ring.append(time.perf_counter() if t is None else float(t))


def note_preview_frame_if_counting(t: float | None = None) -> None:
    if not _counting:
        return
    note_preview_frame(t)


def set_preview_counting(on: bool) -> None:
    global _counting
    was = _counting
    _counting = bool(on)
    if was and not _counting:
        _ring.clear()


def preview_fps() -> float:
    n = len(_ring)
    if n < 2:
        return 0.0
    span = _ring[-1] - _ring[0]
    if span <= 0.0:
        return 0.0
    return (n - 1) / span


def preview_ring_count() -> int:
    return len(_ring)


def reset_for_tests() -> None:
    global _counting
    _counting = False
    _ring.clear()
