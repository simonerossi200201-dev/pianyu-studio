# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q204a · A/V link helpers. No Qt. No second mapping table.

The only persistent relation is ``clip.link_group_id``. Index only keeps
groups with size > 1 (a lone gid is treated as unlinked).
"""
from __future__ import annotations

import uuid


def index_link_groups(tl) -> dict:
    """O(n) once. n = clips on the current timeline (one talking-head, not 40).

    Only gids with ``len(group) > 1`` are returned.
    """
    bags = {}
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            gid = getattr(c, "link_group_id", None)
            if gid:
                bags.setdefault(str(gid), []).append((tr, c))
    return {g: rows for g, rows in bags.items() if len(rows) > 1}


def partners(index, clip) -> list:
    gid = getattr(clip, "link_group_id", None)
    rows = index.get(str(gid) if gid else "", ())
    return [(tr, c) for tr, c in rows if c is not clip]


def rebind_link_groups_after_split(tl, t, lefts, rights) -> None:
    """lefts/rights = [(track, clip), ...]. Group by the gid at split time.

    Left half keeps the old gid. Right halves that shared a gid share one new
    uuid. A side that is only video or only audio still gets a new uuid on the
    right so V-left / V-right / A cannot form a triangle.
    """
    bags = {}
    for tr, c in list(lefts or []):
        gid = getattr(c, "link_group_id", None)
        if not gid:
            continue
        bags.setdefault(str(gid), {"left": [], "right": []})["left"].append((tr, c))
    for tr, c in list(rights or []):
        gid = getattr(c, "link_group_id", None)
        if not gid:
            continue
        bags.setdefault(str(gid), {"left": [], "right": []})["right"].append((tr, c))
    for _gid, sides in bags.items():
        right_rows = sides.get("right") or []
        if not right_rows:
            continue
        new_gid = uuid.uuid4().hex
        for _tr, c in right_rows:
            c.link_group_id = new_gid


def clip_timeline_span(clip, *, audio=False):
    """(start, end) on the timeline. Q205: audio uses effective_duration too."""
    del audio
    start = float(getattr(clip, "start_t", 0.0) or 0.0)
    try:
        dur = float(clip.effective_duration)
    except Exception:
        in_t = float(getattr(clip, "in_t", 0.0) or 0.0)
        out_t = float(getattr(clip, "out_t", in_t) or in_t)
        speed = max(0.01, float(getattr(clip, "speed", 1.0) or 1.0))
        dur = max(0.0, (out_t - in_t) / speed)
    return start, start + max(0.0, dur)


def sync_linked_constant_speed(tl, video_clip):
    """Copy a video clip's constant speed onto linked voice clips.

    Skips BGM/SFX and any partner that already has a speed_curve.
    Returns the clips whose speed changed.
    """
    changed = []
    if tl is None or video_clip is None:
        return changed
    try:
        speed = float(getattr(video_clip, "speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = min(10.0, max(0.1, speed))
    index = index_link_groups(tl)
    for tr, clip in partners(index, video_clip):
        if str(getattr(tr, "type", "") or "") != "audio":
            continue
        role = str(getattr(clip, "audio_role", "") or "")
        if role in ("bgm", "sfx"):
            continue
        if getattr(clip, "speed_curve", None):
            continue
        try:
            old = float(getattr(clip, "speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            old = 1.0
        if abs(old - speed) <= 1e-6:
            continue
        clip.speed = speed
        changed.append(clip)
    return changed


def apply_edge_delta(clip, side, delta, *, audio=False, min_dur=0.08):
    """Same-edge same-timeline-delta trim. Does not change speed.

    Relative start offset is preserved when both members get the same delta.
    """
    start, end = clip_timeline_span(clip, audio=audio)
    in_t = float(getattr(clip, "in_t", 0.0) or 0.0)
    speed = max(0.01, float(getattr(clip, "speed", 1.0) or 1.0))
    delta = float(delta)
    min_dur = float(min_dur)
    right = str(side or "") in ("right", "end")
    if right:
        new_end = max(start + min_dur, end + delta)
        new_dur = new_end - start
        clip.out_t = in_t + new_dur * speed
        return
    new_start = min(end - min_dur, start + delta)
    new_start = max(0.0, new_start)
    d = new_start - start
    clip.start_t = new_start
    clip.in_t = in_t + d * speed
