# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T22 timeline tools: razor, edge-trim vs speed, selected-clip split."""
from __future__ import annotations

import copy
import uuid

TOOL_SELECT = "select"
TOOL_RAZOR = "razor"
TOOL_SPEED = "speed"
TOOLS = (TOOL_SELECT, TOOL_RAZOR, TOOL_SPEED)
MIN_DUR = 0.05


def trim_clip_edge(clip, side: str, new_time: float, *, speed_mode: bool = False,
                   min_dur: float = MIN_DUR):
    """Default: trim in_t/out_t, keep speed. speed_mode: right edge changes speed."""
    start_t = float(getattr(clip, "start_t", 0.0))
    in_t = float(getattr(clip, "in_t", 0.0))
    out_t = float(getattr(clip, "out_t", in_t))
    speed = float(getattr(clip, "speed", 1.0) or 1.0)
    src_dur = max(min_dur, out_t - in_t)
    eff_dur = src_dur / max(0.01, speed)
    end_t = start_t + eff_dur
    if side == "right":
        new_end = max(float(new_time), start_t + min_dur)
        new_eff = max(min_dur, new_end - start_t)
        if speed_mode:
            clip.speed = max(0.1, min(10.0, src_dur / new_eff))
            return clip
        clip.out_t = in_t + new_eff * max(0.01, speed)
        return clip
    new_start = min(float(new_time), end_t - min_dur)
    new_start = max(0.0, new_start)
    delta_tl = new_start - start_t
    clip.start_t = new_start
    clip.in_t = in_t + delta_tl * max(0.01, speed)
    return clip


def split_overlay(ov: dict, t: float) -> dict | None:
    if not isinstance(ov, dict):
        return None
    start = float(ov.get("start") or 0.0)
    end = float(ov.get("end") or 0.0)
    if t <= start + MIN_DUR or t >= end - MIN_DUR:
        return None
    right = copy.deepcopy(ov)
    ov["end"] = float(t)
    right["start"] = float(t)
    if right.get("id"):
        right["id"] = "ov_split_%s" % uuid.uuid4().hex[:12]
    from cutora.render.keyframe import has_keyframes, split_keyframes
    if has_keyframes(ov):
        cut_local = float(t) - start
        split_keyframes(
            ov, right, cut_local,
            left_dur=float(t) - start,
            right_dur=end - float(t),
        )
    return right


def split_subtitle_at(task: dict, seg_id: int, t: float) -> bool:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs) or not isinstance(segs[seg_id], dict):
        return False
    seg = segs[seg_id]
    start = float(seg.get("start") or 0.0)
    end = float(seg.get("end") or 0.0)
    if t <= start + MIN_DUR or t >= end - MIN_DUR:
        return False
    try:
        from cutora.subtitle.edit import split_task
        split_task(task, seg_id, time=t)
        return True
    except ValueError:
        left = dict(seg)
        right = dict(seg)
        left["end"] = float(t)
        right["start"] = float(t)
        segs[seg_id:seg_id + 1] = [left, right]
        from cutora.subtitle.track_source import set_segments
        set_segments(task, segs)
        return True


def razor_clip(clip, t: float):
    split = getattr(clip, "split_at", None)
    if not callable(split):
        return None
    return split(t)


def razor_selected(task: dict, playhead_sec: float, targets: list[dict]) -> int:
    """Split every selected target at playhead. targets: {kind, ...}.

    kind: video|audio|image|text|subtitle
    """
    n = 0
    t = float(playhead_sec)
    tl = task.get("timeline") if isinstance(task, dict) else None
    for item in list(targets or []):
        kind = str((item or {}).get("kind") or "")
        if kind in ("video", "audio"):
            clip = item.get("clip")
            track = item.get("track")
            right = razor_clip(clip, t) if clip is not None else None
            if right is None:
                continue
            clips = getattr(track, "clips", None) if track is not None else None
            if clips is not None and clip in clips:
                pos = clips.index(clip)
                clips.insert(pos + 1, right)
            n += 1
        elif kind in ("image", "text"):
            key = "image_overlays" if kind == "image" else "text_overlays"
            idx = item.get("idx")
            idx = int(idx) if idx is not None else -1
            bag = list(task.get(key) or [])
            if idx < 0 or idx >= len(bag):
                continue
            right = split_overlay(bag[idx], t)
            if right is None:
                continue
            bag.insert(idx + 1, right)
            task[key] = bag
            n += 1
        elif kind == "subtitle":
            idx = item.get("idx")
            idx = int(idx) if idx is not None else -1
            if split_subtitle_at(task, idx, t):
                n += 1
    return n
