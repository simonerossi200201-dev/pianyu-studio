# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q208: regional speed curve on the clip. Empty curve is not a filter.

t is seconds inside the source span. Pieces are constant speed, then concat.
rubberband is used only when this ffmpeg lists it; otherwise atempo.
"""
from __future__ import annotations

import os

from cutora.core.proc import run as _no_window_run

_RUBBER = None


def normalize_points(curve) -> list:
    pts = []
    for row in curve or []:
        if not isinstance(row, dict):
            continue
        try:
            t = float(row.get("t"))
            speed = float(row.get("speed"))
        except (TypeError, ValueError):
            continue
        if speed <= 0:
            continue
        pts.append((max(0.0, t), min(10.0, max(0.1, speed))))
    pts.sort(key=lambda item: item[0])
    return pts


def segments(src_span, speed, curve) -> list:
    """[(source_t, source_dur, speed), ...]. Empty when the curve is off."""
    try:
        span = float(src_span or 0.0)
    except (TypeError, ValueError):
        span = 0.0
    pts = normalize_points(curve)
    if span <= 0 or not pts:
        return []
    try:
        base = min(10.0, max(0.1, float(speed or 1.0)))
    except (TypeError, ValueError):
        base = 1.0
    if pts[0][0] > 1e-4:
        pts = [(0.0, base)] + pts
    rows = []
    for i, (t, sp) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else span
        nxt = min(span, max(t, nxt))
        dur = nxt - t
        if dur <= 1e-4:
            continue
        rows.append((t, dur, sp))
    return rows


def integrated_out_dur(src_span, speed, curve) -> float:
    rows = segments(src_span, speed, curve)
    if not rows:
        try:
            sp = min(10.0, max(0.1, float(speed or 1.0)))
            span = max(0.0, float(src_span or 0.0))
        except (TypeError, ValueError):
            return 0.0
        return span / sp
    return sum(dur / sp for _t, dur, sp in rows)


def default_curve(src_span) -> list:
    span = max(0.2, float(src_span or 1.0))
    return [
        {"t": 0.0, "speed": 1.0},
        {"t": round(span * 0.5, 3), "speed": 0.5},
        {"t": round(span, 3), "speed": 1.0},
    ]


def rubberband_available(ffmpeg=None) -> bool:
    """Probe once. Failure means atempo, not a new dependency."""
    global _RUBBER
    if _RUBBER is not None:
        return _RUBBER
    _RUBBER = False
    if not ffmpeg:
        try:
            from cutora.core.config import FFMPEG_PATH
            ffmpeg = FFMPEG_PATH
        except Exception:
            return False
    if not ffmpeg or not os.path.isfile(ffmpeg):
        return False
    try:
        proc = _no_window_run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, timeout=8)
        text = (proc.stdout or b"").decode("utf-8", "replace")
        _RUBBER = " rubberband " in (" " + text + " ")
    except Exception:
        _RUBBER = False
    return _RUBBER


def curve_graph(label_in, uid, trim_in, src_span, speed, curve, target_sr, *,
                use_rubberband=None) -> tuple | None:
    """(graph, joined_label) or None when the curve is empty."""
    rows = segments(src_span, speed, curve)
    if not rows:
        return None
    from cutora.render.render_worker import atempo_chain
    if use_rubberband is None:
        use_rubberband = rubberband_available()
    n = len(rows)
    outs = "".join("[sc%s_%d]" % (uid, i) for i in range(n))
    parts = [
        "[%s]atrim=start=%s:duration=%s,asetpts=N/SR/TB,aresample=%s,asplit=%d%s"
        % (label_in, trim_in, src_span, int(target_sr), n, outs)
    ]
    labels = []
    for i, (t, dur, sp) in enumerate(rows):
        if use_rubberband:
            tempo = ",rubberband=tempo=%s:pitch=1" % sp
        else:
            tempo = atempo_chain(sp)
        parts.append(
            "[sc%s_%d]atrim=start=%s:duration=%s,asetpts=N/SR/TB%s[sp%s_%d]"
            % (uid, i, t, dur, tempo, uid, i)
        )
        labels.append("[sp%s_%d]" % (uid, i))
    joined = "j%s" % uid
    parts.append("%sconcat=n=%d:v=0:a=1[%s]" % ("".join(labels), n, joined))
    return ";".join(parts), joined
