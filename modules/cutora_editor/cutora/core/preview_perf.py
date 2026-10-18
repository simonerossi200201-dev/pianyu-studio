# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview paintEvent profiler.

Enabled only when ``CUTORA_PERF=1``. Default path is a no-op besides one
cached boolean check per call. Samples flush every 2s to
``USER_DATA/logs/preview_perf.jsonl``.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cutora.core.fps_meter import note_preview_frame_if_counting
from cutora.core.log import get_logger
from cutora.core.paths import ensure_user_layout, user_logs_dir

logger = get_logger("cutora.core.preview_perf")

ENV_FLAG = "CUTORA_PERF"
JSONL_NAME = "preview_perf.jsonl"
FLUSH_EVERY_S = 2.0
DEFAULT_FPS = 30.0
# T04f-1d-1: v2 draw() breakdown (from_style / cache_hash / layout / raster).
FX_SPAN_NAMES = (
    "fx_spec", "fx_hash", "fx_layout", "fx_raster",
    "fx_bd_fmt", "fx_glass_blur", "fx_glass_compose",
)

_enabled_cache: bool | None = None
_collector: "PreviewPerf | None" = None


def enabled() -> bool:
    """True when CUTORA_PERF=1. Cached after the first call."""
    global _enabled_cache
    if _enabled_cache is None:
        _enabled_cache = os.environ.get(ENV_FLAG, "") == "1"
    return _enabled_cache


def reset_for_tests() -> None:
    """Drop cached flag and collector. Tests only."""
    global _enabled_cache, _collector
    _enabled_cache = None
    _collector = None


def now() -> float:
    if not enabled():
        return 0.0
    return time.perf_counter()


def percentile(values: list[float], p: float) -> float:
    """Linear interpolation percentile. ``p`` is 0–100."""
    if not values:
        return 0.0
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return float(xs[0])
    k = (n - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    if lo == hi:
        return float(xs[lo])
    frac = k - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "max": round(max(values) if values else 0.0, 3),
        "mean": round(sum(values) / len(values), 3) if values else 0.0,
    }


class PreviewPerf:
    """Accumulate per-paint samples and flush rolling windows to jsonl."""

    def __init__(
        self,
        jsonl_path: str | None = None,
        window_s: float = FLUSH_EVERY_S,
        fps: float = DEFAULT_FPS,
    ) -> None:
        self.jsonl_path = jsonl_path
        self.window_s = float(window_s)
        self.frame_budget_ms = 1000.0 / float(fps)
        self.extra: dict[str, Any] = {}
        self._t0: float | None = None
        self._mark_t: float = 0.0
        self._last_begin: float | None = None
        self._interval: float = 0.0
        self._marks: dict[str, float] = {}
        self._spans: dict[str, float] = defaultdict(float)
        self._samples: list[dict[str, float]] = []
        self._dropped_window = 0
        self._dropped_total = 0
        self._window_start: float | None = None
        self._all_samples: list[dict[str, float]] = []

    def begin_frame(self) -> None:
        now = time.perf_counter()
        self._interval = (now - self._last_begin) if self._last_begin is not None else 0.0
        self._last_begin = now
        self._t0 = now
        self._mark_t = now
        self._marks = {}
        self._spans = defaultdict(float)

    def mark(self, name: str) -> None:
        if self._t0 is None:
            return
        now = time.perf_counter()
        self._marks[name] = now - self._mark_t
        self._mark_t = now

    def add_span(self, name: str, dt: float) -> None:
        if self._t0 is None or dt <= 0.0:
            return
        self._spans[name] += dt

    def end_frame(self) -> None:
        if self._t0 is None:
            return
        now = time.perf_counter()
        total_ms = (now - self._t0) * 1000.0
        rec = {
            "total_ms": total_ms,
            "interval_ms": self._interval * 1000.0,
            "video_ms": self._marks.get("video", 0.0) * 1000.0,
            "draw_layers_ms": self._marks.get("draw_layers", 0.0) * 1000.0,
            "chrome_ms": self._marks.get("chrome", 0.0) * 1000.0,
            "layout_ms": self._spans.get("layout", 0.0) * 1000.0,
            "stroke_shadow_ms": self._spans.get("stroke_shadow", 0.0) * 1000.0,
            "bg_box_ms": self._spans.get("bg_box", 0.0) * 1000.0,
            "draw_subtitle_ms": self._spans.get("draw_subtitle", 0.0) * 1000.0,
        }
        for name in FX_SPAN_NAMES:
            rec[name + "_ms"] = self._spans.get(name, 0.0) * 1000.0
        dropped = total_ms > self.frame_budget_ms
        if dropped:
            self._dropped_window += 1
            self._dropped_total += 1
        self._samples.append(rec)
        self._all_samples.append(rec)
        self._t0 = None
        if self._window_start is None:
            self._window_start = now
        if (now - self._window_start) >= self.window_s:
            self._flush_window(now)

    def _flush_window(self, now: float) -> None:
        if not self._samples:
            self._window_start = now
            self._dropped_window = 0
            return
        window_s = now - (self._window_start or now)
        n = len(self._samples)
        fps = (n / window_s) if window_s > 0 else 0.0
        layers = [s["draw_layers_ms"] for s in self._samples]
        totals = [s["total_ms"] for s in self._samples]
        mean_total = sum(totals) / n
        mean_layers = sum(layers) / n
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "window_s": round(window_s, 3),
            "n": n,
            "fps": round(fps, 3),
            "dropped": self._dropped_window,
            "frame_budget_ms": round(self.frame_budget_ms, 3),
            "draw_layers_pct_mean": round(
                100.0 * mean_layers / mean_total, 2) if mean_total else 0.0,
            "total_ms": _stats(totals),
            "interval_ms": _stats([s["interval_ms"] for s in self._samples]),
            "video_ms": _stats([s["video_ms"] for s in self._samples]),
            "draw_layers_ms": _stats(layers),
            "chrome_ms": _stats([s["chrome_ms"] for s in self._samples]),
            "layout_ms": _stats([s["layout_ms"] for s in self._samples]),
            "stroke_shadow_ms": _stats(
                [s["stroke_shadow_ms"] for s in self._samples]),
            "bg_box_ms": _stats([s["bg_box_ms"] for s in self._samples]),
            "draw_subtitle_ms": _stats(
                [s.get("draw_subtitle_ms", 0.0) for s in self._samples]),
        }
        for name in FX_SPAN_NAMES:
            line[name + "_ms"] = _stats(
                [s.get(name + "_ms", 0.0) for s in self._samples])
        if self.extra:
            line["extra"] = dict(self.extra)
        self._write_line(line)
        self._samples = []
        self._dropped_window = 0
        self._window_start = now

    def _write_line(self, line: dict[str, Any]) -> None:
        path = self.jsonl_path or default_jsonl_path()
        self.jsonl_path = path
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        if self._samples:
            self._flush_window(time.perf_counter())

    def reset_samples(self) -> None:
        self._samples = []
        self._all_samples = []
        self._dropped_window = 0
        self._dropped_total = 0
        self._window_start = None
        self._last_begin = None
        self._t0 = None

    def summary(self) -> dict[str, Any]:
        samples = self._all_samples
        n = len(samples)
        if n == 0:
            return {"n": 0, "dropped": 0}
        totals = [s["total_ms"] for s in samples]
        layers = [s["draw_layers_ms"] for s in samples]
        mean_total = sum(totals) / n
        mean_layers = sum(layers) / n
        mean_layout = sum(s["layout_ms"] for s in samples) / n
        mean_ss = sum(s["stroke_shadow_ms"] for s in samples) / n
        mean_bg = sum(s["bg_box_ms"] for s in samples) / n
        mean_video = sum(s["video_ms"] for s in samples) / n
        mean_chrome = sum(s["chrome_ms"] for s in samples) / n
        out = {
            "n": n,
            "dropped": self._dropped_total,
            "frame_budget_ms": round(self.frame_budget_ms, 3),
            "fps_from_total_mean": round(
                1000.0 / mean_total, 3) if mean_total else 0.0,
            "draw_layers_pct_of_total": round(
                100.0 * mean_layers / mean_total, 2) if mean_total else 0.0,
            "video_pct_of_total": round(
                100.0 * mean_video / mean_total, 2) if mean_total else 0.0,
            "chrome_pct_of_total": round(
                100.0 * mean_chrome / mean_total, 2) if mean_total else 0.0,
            "layout_pct_of_draw_layers": round(
                100.0 * mean_layout / mean_layers, 2) if mean_layers else 0.0,
            "stroke_shadow_pct_of_draw_layers": round(
                100.0 * mean_ss / mean_layers, 2) if mean_layers else 0.0,
            "bg_box_pct_of_draw_layers": round(
                100.0 * mean_bg / mean_layers, 2) if mean_layers else 0.0,
            "total_ms": _stats(totals),
            "interval_ms": _stats([s["interval_ms"] for s in samples]),
            "video_ms": _stats([s["video_ms"] for s in samples]),
            "draw_layers_ms": _stats(layers),
            "chrome_ms": _stats([s["chrome_ms"] for s in samples]),
            "layout_ms": _stats([s["layout_ms"] for s in samples]),
            "stroke_shadow_ms": _stats(
                [s["stroke_shadow_ms"] for s in samples]),
            "bg_box_ms": _stats([s["bg_box_ms"] for s in samples]),
            "draw_subtitle_ms": _stats(
                [s.get("draw_subtitle_ms", 0.0) for s in samples]),
            "extra": dict(self.extra),
            "jsonl": self.jsonl_path or default_jsonl_path(),
        }
        for name in FX_SPAN_NAMES:
            out[name + "_ms"] = _stats(
                [s.get(name + "_ms", 0.0) for s in samples])
        return out


def default_jsonl_path() -> str:
    ensure_user_layout()
    return os.path.join(user_logs_dir(), JSONL_NAME)


def _collector_or_none() -> PreviewPerf | None:
    global _collector
    if not enabled():
        return None
    if _collector is None:
        _collector = PreviewPerf()
        logger.info("CUTORA_PERF=1 jsonl=%s", default_jsonl_path())
    return _collector


def begin_frame(count: bool = True) -> None:
    if count:                       # Q175：播放时整幅帧由工作线程送来，到一张记一张（重画不算）
        note_preview_frame_if_counting()
    col = _collector_or_none()
    if col is not None:
        col.begin_frame()


def mark(name: str) -> None:
    col = _collector_or_none()
    if col is not None:
        col.mark(name)


def add_span(name: str, dt: float) -> None:
    col = _collector_or_none()
    if col is not None:
        col.add_span(name, dt)


def end_frame() -> None:
    col = _collector_or_none()
    if col is not None:
        col.end_frame()


def set_extra(extra: dict[str, Any]) -> None:
    col = _collector_or_none()
    if col is not None:
        col.extra = dict(extra)


def flush() -> None:
    col = _collector_or_none()
    if col is not None:
        col.flush()


def reset_samples() -> None:
    col = _collector_or_none()
    if col is not None:
        col.reset_samples()


def summary() -> dict[str, Any]:
    col = _collector_or_none()
    if col is None:
        return {"n": 0, "enabled": False}
    out = col.summary()
    out["enabled"] = True
    return out


def get_collector() -> PreviewPerf | None:
    return _collector_or_none()
