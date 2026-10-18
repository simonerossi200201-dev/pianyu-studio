# -*- coding: utf-8 -*-
"""Optional export stage probe. Active only when CUTORA_PROFILE_EXPORT=1.

Nested sibling stages (decode / scale / compose) are exclusive of their
open parent span so they cannot be summed twice. Raster-row buckets
(raster_miss / layer_blit / layout / word_fx / other) are a split of the
overlay span and are *not* subtracted from it.
"""
from __future__ import annotations

import os
import time

from cutora.core.log import get_logger

ENV = "CUTORA_PROFILE_EXPORT"
_LOG = get_logger("cutora.render.export_probe")

# Sibling stages that used to nest inside overlay (the old "raster" row).
SIBLING_STAGES = frozenset({"decode", "scale", "compose"})
RASTER_PARTS = ("raster_miss", "layer_blit", "layout", "word_fx", "other")
LEAF_STAGES = (
    "decode", "scale", "compose", "glass", "pipe",
) + RASTER_PARTS + ("instrumentation",)

_MS_KEYS = (
    "decode", "scale", "compose", "overlay", "pipe", "glass",
    "raster", "raster_miss", "layer_blit", "layout", "word_fx", "other",
    "instrumentation",
)

_on = os.environ.get(ENV, "") == "1"
_t0 = time.perf_counter()
_counts: dict[str, int] = {"frames": 0, "seek": 0, "read": 0, "grab": 0, "glass": 0, "cache_hit": 0}
_ms: dict[str, float] = {k: 0.0 for k in _MS_KEYS}
_family_ms: dict[str, float] = {}
_family_n: dict[str, int] = {}
_cpu_samples: list[float] = []
_cpu_proc = None
_open: list[str] = []
_span_start: dict[str, float] = {}
_span_nested: dict[str, float] = {}
_instr_ms = 0.0
_sample_fail_logged = False


def on() -> bool:
    return _on


def configure(enabled: bool | None = None) -> None:
    """Set probe on/off. ``None`` re-reads CUTORA_PROFILE_EXPORT."""
    global _on
    if enabled is None:
        _on = os.environ.get(ENV, "") == "1"
    else:
        _on = bool(enabled)


def _credit_instr(t_enter: float) -> None:
    global _instr_ms
    _instr_ms += max(0.0, (time.perf_counter() - t_enter) * 1000.0)


def note(kind: str, n: int = 1) -> None:
    if not _on:
        return
    t_i = time.perf_counter()
    _counts[kind] = _counts.get(kind, 0) + int(n)
    _credit_instr(t_i)


def add_ms(kind: str, seconds: float) -> None:
    if not _on:
        return
    t_i = time.perf_counter()
    sec = float(seconds)
    if sec < 0:
        sec = 0.0
    _ms[kind] = _ms.get(kind, 0.0) + sec * 1000.0
    if kind in SIBLING_STAGES and _open:
        for pk in _open:
            if pk == kind:
                continue
            _span_nested[pk] = _span_nested.get(pk, 0.0) + sec
    _credit_instr(t_i)


def add_family(family: str, seconds: float) -> None:
    if not _on:
        return
    sec = float(seconds)
    if sec < 0:
        sec = 0.0
    add_ms("raster_miss", sec)
    fam = str(family or "other")
    _family_ms[fam] = _family_ms.get(fam, 0.0) + sec * 1000.0
    _family_n[fam] = _family_n.get(fam, 0) + 1


def begin(kind: str) -> None:
    if not _on:
        return
    t_i = time.perf_counter()
    now = t_i
    if kind in _span_start:
        _LOG.warning("export_probe begin(%s) while already open %s", kind, list(_open))
        end(kind)
        now = time.perf_counter()
    _open.append(kind)
    _span_start[kind] = now
    _span_nested[kind] = 0.0
    _credit_instr(t_i)


def end(kind: str) -> None:
    if not _on:
        return
    t_i = time.perf_counter()
    now = t_i
    if kind not in _span_start:
        _LOG.warning("export_probe end(%s) with no matching begin; stack=%s",
                     kind, list(_open))
        _credit_instr(t_i)
        return
    t0 = _span_start.pop(kind)
    nested = _span_nested.pop(kind, 0.0)
    exclusive = (now - t0) - nested
    if exclusive < 0:
        _LOG.warning(
            "export_probe end(%s) exclusive %.4f s (nested %.4f > wall %.4f)",
            kind, exclusive, nested, now - t0)
        exclusive = 0.0
    _ms[kind] = _ms.get(kind, 0.0) + exclusive * 1000.0
    try:
        _open.remove(kind)
    except ValueError:
        _LOG.warning("export_probe end(%s) not in stack %s", kind, list(_open))
    _credit_instr(t_i)


def attach_ffmpeg(pid) -> None:
    global _cpu_proc
    if not _on:
        return
    _cpu_proc = None
    try:
        import psutil
        _cpu_proc = ("psutil", psutil.Process(int(pid)))
        _cpu_proc[1].cpu_percent(None)
        return
    except Exception:
        _LOG.warning("export_probe: psutil ffmpeg attach failed", exc_info=True)
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            _cpu_proc = ("win", handle, None, None)
            return
        _LOG.warning("export_probe: OpenProcess(%s) returned null", pid)
    except Exception:
        _LOG.warning("export_probe: win32 ffmpeg attach failed", exc_info=True)
        _cpu_proc = None


def sample_ffmpeg() -> None:
    global _cpu_proc, _sample_fail_logged
    if not _on or _cpu_proc is None:
        return
    kind = _cpu_proc[0]
    if kind == "psutil":
        try:
            pct = float(_cpu_proc[1].cpu_percent(None))
            if pct >= 0.0:
                _cpu_samples.append(pct)
        except Exception:
            if not _sample_fail_logged:
                _LOG.warning("export_probe: psutil cpu sample failed", exc_info=True)
                _sample_fail_logged = True
        return
    if kind == "win":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD),
                            ("dwHighDateTime", wintypes.DWORD)]

            def _q(ft):
                return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

            ctime = FILETIME(); etime = FILETIME(); ktime = FILETIME(); utime = FILETIME()
            ok = kernel32.GetProcessTimes(
                _cpu_proc[1], ctypes.byref(ctime), ctypes.byref(etime),
                ctypes.byref(ktime), ctypes.byref(utime))
            if not ok:
                return
            now = time.perf_counter()
            cpu = _q(ktime) + _q(utime)
            prev_t, prev_cpu = _cpu_proc[2], _cpu_proc[3]
            _cpu_proc = ("win", _cpu_proc[1], now, cpu)
            if prev_t is None or now <= prev_t:
                return
            cpu_s = (cpu - prev_cpu) / 10_000_000.0
            wall_s = now - prev_t
            ncpu = os.cpu_count() or 1
            pct = 100.0 * cpu_s / (wall_s * ncpu)
            if pct >= 0.0:
                _cpu_samples.append(round(pct, 1))
        except Exception:
            if not _sample_fail_logged:
                _LOG.warning("export_probe: win32 cpu sample failed", exc_info=True)
                _sample_fail_logged = True


def reset() -> None:
    global _t0, _cpu_proc, _instr_ms, _sample_fail_logged
    _t0 = time.perf_counter()
    for k in list(_counts):
        _counts[k] = 0
    for k in list(_ms):
        _ms[k] = 0.0
    _family_ms.clear()
    _family_n.clear()
    _cpu_samples.clear()
    _cpu_proc = None
    _open.clear()
    _span_start.clear()
    _span_nested.clear()
    _instr_ms = 0.0
    _sample_fail_logged = False
    try:
        from cutora.render.textfx.pipeline import reset_draw_counters
        reset_draw_counters()
    except Exception:
        _LOG.warning("export_probe.reset: draw counters not reset", exc_info=True)


def raster_breakdown_ok(stats: dict, tol: float = 0.05) -> bool:
    totals = stats.get("ms_total") or {}
    per = stats.get("ms_per_frame") or {}
    src = totals if totals else per
    parts = sum(float(src.get(k) or 0) for k in RASTER_PARTS)
    raster = float(src.get("raster") or src.get("overlay") or 0)
    if raster <= 1e-9:
        return parts <= 1e-9
    return abs(parts - raster) <= float(tol) * raster


def stage_sum_s(stats: dict) -> float:
    totals = stats.get("ms_total") or {}
    if totals:
        return sum(float(totals.get(k) or 0) for k in LEAF_STAGES) / 1000.0
    n = int(stats.get("frames") or 0)
    per = stats.get("ms_per_frame") or {}
    return sum(float(per.get(k) or 0) for k in LEAF_STAGES) * n / 1000.0


def stages_le_wall(stats: dict, slack_s: float = 0.001) -> bool:
    """``wall_s`` is rounded to 1 ms; allow one report-unit of slack."""
    wall = float(stats.get("wall_s") or 0)
    return stage_sum_s(stats) <= wall + float(slack_s)


def dump() -> dict:
    t_i = time.perf_counter()
    n = max(1, int(_counts.get("frames") or 0))
    # Raster row = overlay exclusive of sibling video stages.
    _ms["raster"] = float(_ms.get("overlay") or 0.0)
    raster = {}
    try:
        from cutora.render.textfx.pipeline import draw_counters
        raster = draw_counters()
    except Exception:
        _LOG.warning("export_probe.dump: draw_counters failed", exc_info=True)
        raster = {}
    hits = int(raster.get("roi_cache_hits") or 0) + int(raster.get("layout_cache_hits") or 0)
    misses = int(raster.get("roi_cache_misses") or 0) + int(raster.get("layout_cache_misses") or 0)
    hit_den = hits + misses
    cpu_avg = round(sum(_cpu_samples) / len(_cpu_samples), 1) if _cpu_samples else None
    cpu_max = round(max(_cpu_samples), 1) if _cpu_samples else None
    families = {}
    for fam in sorted(set(list(_family_ms) + list(_family_n))):
        n_f = int(_family_n.get(fam) or 0)
        ms_f = float(_family_ms.get(fam) or 0.0)
        families[fam] = {
            "n": n_f,
            "ms": round(ms_f, 3),
            "ms_each": round(ms_f / n_f, 3) if n_f else None,
        }
    _credit_instr(t_i)
    _ms["instrumentation"] = float(_instr_ms)
    # Raster row split: 其他 absorbs overlay exclusive leftover after the
    # four named buckets (canvas fill, video-path remainder, draw leftovers).
    named = sum(float(_ms.get(k) or 0.0) for k in
                ("raster_miss", "layer_blit", "layout", "word_fx"))
    overlay_ms = float(_ms.get("overlay") or 0.0)
    _ms["other_draw"] = float(_ms.get("other") or 0.0)
    remainder = overlay_ms - named
    _ms["other"] = remainder if remainder >= 0.0 else float(_ms.get("other") or 0.0)
    wall = time.perf_counter() - _t0
    totals = {k: round(float(_ms.get(k) or 0.0), 3) for k in
              sorted(set(list(_ms) + list(_MS_KEYS)))}
    per = {k: round(v / n, 3) for k, v in totals.items()}
    parts = sum(float(totals.get(k) or 0) for k in RASTER_PARTS)
    raster_ms = float(totals.get("raster") or 0)
    ratio = (parts / raster_ms) if raster_ms > 1e-9 else (0.0 if parts <= 1e-9 else None)
    stats = {
        "wall_s": round(wall, 3),
        "frames": int(_counts.get("frames") or 0),
        "seek": int(_counts.get("seek") or 0),
        "read": int(_counts.get("read") or 0),
        "grab": int(_counts.get("grab") or 0),
        "cache_hit": int(_counts.get("cache_hit") or 0),
        "glass_frames": int(_counts.get("glass") or 0),
        "probe_on": bool(_on),
        "ms_per_frame": per,
        "ms_total": totals,
        "raster": raster,
        "raster_hit_rate": round(hits / hit_den, 3) if hit_den else None,
        "raster_miss_by_family": families,
        "ffmpeg_cpu_avg_pct": cpu_avg,
        "ffmpeg_cpu_max_pct": cpu_max,
        "ffmpeg_cpu_samples": len(_cpu_samples),
        "closure": {
            "breakdown_vs_raster": None if ratio is None else round(ratio, 4),
            "breakdown_ok": False,
            "stage_sum_s": 0.0,
            "stage_sum_le_wall": False,
        },
    }
    stats["closure"]["breakdown_ok"] = raster_breakdown_ok(stats)
    stats["closure"]["stage_sum_s"] = round(stage_sum_s(stats), 4)
    stats["closure"]["stage_sum_le_wall"] = stages_le_wall(stats)
    return stats
