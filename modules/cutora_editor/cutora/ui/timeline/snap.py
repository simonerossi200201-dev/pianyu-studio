# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline magnet snap. Interaction-only: no commands, no undo.

Threshold is in pixels so zoom feel stays constant, then capped in seconds
so a fully-zoomed-out 8 px cannot jump tens of seconds.
"""
from __future__ import annotations

SNAP_PX = 5.0
SNAP_MAX_SEC = 0.5
MIN_DUR = 0.08


def threshold_sec(pixels_per_second, snap_px=SNAP_PX, max_sec=SNAP_MAX_SEC) -> float:
    pps = max(float(pixels_per_second), 1e-6)
    return min(float(max_sec), float(snap_px) / pps)


def clip_span(clip) -> tuple[float, float]:
    start = float(getattr(clip, "start_t", 0.0) or 0.0)
    dur = float(getattr(clip, "effective_duration", 0.0) or 0.0)
    if dur <= 0.0:
        inn = float(getattr(clip, "in_t", 0.0) or 0.0)
        out = float(getattr(clip, "out_t", inn) or inn)
        dur = max(0.0, out - inn)
    return start, start + dur


def ranges_overlap(a0, a1, b0, b1, eps=1e-9) -> bool:
    return float(a0) < float(b1) - eps and float(b0) < float(a1) - eps


def collect_edges(
    *,
    clips_by_track=None,
    segments=None,
    overlays=None,
    playhead_s=None,
    markers=None,
    in_point=None,
    out_point=None,
    skip=None,
    silence_times=None,
) -> list[dict]:
    """Build snap targets. ``skip`` is a set of ('clip', track_id, i) or
    ('seg', i) or ('ov', list_name, i) for the object being dragged.
    Marker times are accepted now so T05d can plug in without an API change.
    ``silence_times`` is a list of (t, key) from waveform silence detection;
    clip-gap "silence" is not a snap kind (it duplicated clip edges).
    """
    skip = set(skip or ())
    edges = [{"t": 0.0, "kind": "zero", "key": ("zero",)}]
    if playhead_s is not None:
        edges.append({
            "t": float(playhead_s), "kind": "playhead", "key": ("playhead",),
        })
    if in_point is not None:
        edges.append({
            "t": float(in_point), "kind": "in_point", "key": ("in_point",),
        })
    if out_point is not None:
        edges.append({
            "t": float(out_point), "kind": "out_point", "key": ("out_point",),
        })
    for m in markers or []:
        mt = float(m)
        edges.append({"t": mt, "kind": "marker", "key": ("marker", mt)})
    for tid, clips in clips_by_track or []:
        for i, clip in enumerate(clips or []):
            start, end = clip_span(clip)
            if ("clip", tid, i) not in skip:
                edges.append({
                    "t": start, "kind": "clip_start",
                    "key": ("clip", tid, i, "start"),
                })
                edges.append({
                    "t": end, "kind": "clip_end",
                    "key": ("clip", tid, i, "end"),
                })
    for i, seg in enumerate(segments or []):
        if ("seg", i) in skip or not isinstance(seg, dict):
            continue
        edges.append({
            "t": float(seg.get("start") or 0.0), "kind": "seg_start",
            "key": ("seg", i, "start"),
        })
        edges.append({
            "t": float(seg.get("end") or 0.0), "kind": "seg_end",
            "key": ("seg", i, "end"),
        })
    for list_name, rows in overlays or []:
        for i, ov in enumerate(rows or []):
            if ("ov", list_name, i) in skip or not isinstance(ov, dict):
                continue
            edges.append({
                "t": float(ov.get("start") or 0.0), "kind": "ov_start",
                "key": ("ov", list_name, i, "start"),
            })
            edges.append({
                "t": float(ov.get("end") or 0.0), "kind": "ov_end",
                "key": ("ov", list_name, i, "end"),
            })
    for row in silence_times or []:
        if isinstance(row, dict):
            t = float(row.get("t") or 0.0)
            key = row.get("key") or ("silence", t)
        else:
            t = float(row[0])
            key = row[1] if len(row) > 1 else ("silence", t)
        # Skip silence that belongs to the clip being dragged.
        if isinstance(key, tuple) and len(key) >= 3 and key[0] == "silence":
            if ("clip", key[1], key[2]) in skip:
                continue
        edges.append({"t": t, "kind": "silence", "key": key})
    return edges


def nearest(value, edges, threshold_sec):
    best = None
    best_d = None
    v = float(value)
    thr = float(threshold_sec)
    for ed in edges or []:
        d = abs(float(ed["t"]) - v)
        if d <= thr + 1e-12 and (best_d is None or d < best_d - 1e-15):
            best, best_d = ed, d
    return best


def move_valid(new_start, duration, neighbors, min_dur=MIN_DUR) -> bool:
    if duration < float(min_dur) - 1e-9:
        return False
    if new_start < -1e-9:
        return False
    new_end = float(new_start) + float(duration)
    for ns, ne in neighbors or []:
        if ranges_overlap(new_start, new_end, ns, ne):
            return False
    return True


def trim_valid(new_start, new_end, neighbors, min_dur=MIN_DUR) -> bool:
    if float(new_end) - float(new_start) < float(min_dur) - 1e-9:
        return False
    if new_start < -1e-9:
        return False
    for ns, ne in neighbors or []:
        if ranges_overlap(new_start, new_end, ns, ne):
            return False
    return True


def snap_move(start, end, edges, threshold_sec, neighbors, min_dur=MIN_DUR):
    """Snap a moving interval. Returns (new_start, hit_or_None)."""
    start = float(start)
    end = float(end)
    dur = end - start
    pairs = []
    for edge_t, which in ((start, "start"), (end, "end")):
        hit = nearest(edge_t, edges, threshold_sec)
        if hit is None:
            continue
        pairs.append((abs(float(hit["t"]) - edge_t), which, hit))
    if not pairs:
        return start, None
    pairs.sort(key=lambda row: row[0])
    _d, which, hit = pairs[0]
    if which == "start":
        new_start = float(hit["t"])
    else:
        new_start = float(hit["t"]) - dur
    new_start = max(0.0, new_start)
    if not move_valid(new_start, dur, neighbors, min_dur=min_dur):
        return start, None
    return new_start, hit


def snap_trim(proposed, other, is_start, edges, threshold_sec, neighbors,
              min_dur=MIN_DUR):
    """Snap one trimmed edge. ``other`` is the unmoved edge.
    Returns (new_edge, hit_or_None).
    """
    hit = nearest(proposed, edges, threshold_sec)
    if hit is None:
        return proposed, None
    snapped = float(hit["t"])
    if is_start:
        new_start, new_end = snapped, float(other)
    else:
        new_start, new_end = float(other), snapped
    if not trim_valid(new_start, new_end, neighbors, min_dur=min_dur):
        return proposed, None
    return snapped, hit


def marker_times(task) -> list[float]:
    """T05d hook: read task['markers'] if present, else empty."""
    raw = (task or {}).get("markers") if isinstance(task, dict) else None
    out = []
    for m in raw or []:
        if isinstance(m, (int, float)):
            out.append(float(m))
        elif isinstance(m, dict):
            raw_t = m.get("t", m.get("time"))
            if raw_t is None:
                continue
            try:
                out.append(float(raw_t))
            except (TypeError, ValueError):
                continue
    return out
