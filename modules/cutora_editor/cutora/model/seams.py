# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clip seams: adjacent-clip joins and audio TransitionItem fades."""
from __future__ import annotations


class JoinError(ValueError):
    """Raised when two clips at a seam cannot be joined."""


def _fps_of(track, fps=None) -> float:
    if fps is not None and float(fps) > 0:
        return float(fps)
    return 30.0


def _frame_sec(fps) -> float:
    return 1.0 / max(float(fps or 30.0), 1.0)


def clip_span(clip) -> tuple[float, float]:
    start = float(getattr(clip, "start_t", 0.0) or 0.0)
    dur = float(getattr(clip, "effective_duration", 0.0) or 0.0)
    if dur <= 0.0:
        inn = float(getattr(clip, "in_t", 0.0) or 0.0)
        out = float(getattr(clip, "out_t", inn) or inn)
        dur = max(0.0, out - inn)
    return start, start + dur


def find_seams(track, fps=30.0):
    """Return [(left_index, right_index, seam_t), ...] sorted by time.

    Indices are positions in ``track.clips``. A seam is two clips whose
    timeline ranges touch within one frame.
    """
    clips = list(getattr(track, "clips", []) or [])
    items = []
    for i, clip in enumerate(clips):
        start, end = clip_span(clip)
        items.append((start, end, i))
    items.sort(key=lambda row: (row[0], row[2]))
    slop = _frame_sec(fps)
    out = []
    for a, b in zip(items, items[1:]):
        if abs(a[1] - b[0]) <= slop:
            out.append((a[2], b[2], b[0]))
    return out


def transition_at_seam(track, seam_t, fps=30.0):
    slop = _frame_sec(fps)
    t = float(seam_t)
    for trans in getattr(track, "transitions", []) or []:
        t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
        dur = float(getattr(trans, "duration", 0.0) or 0.0)
        if (t0 - slop) <= t <= (t0 + dur + slop):
            return trans
    return None


def cannot_join_reason(left, right, track=None, fps=30.0) -> str | None:
    """Return a Chinese reason, or None if the pair can be joined."""
    if left is None or right is None:
        return "不能合并：接缝两侧缺少片段"
    sid_l = getattr(left, "source_id", None)
    sid_r = getattr(right, "source_id", None)
    if sid_l != sid_r:
        return "不能合并：两侧不是同一素材"
    slop = _frame_sec(fps)
    left_out = float(getattr(left, "out_t", 0.0) or 0.0)
    right_in = float(getattr(right, "in_t", 0.0) or 0.0)
    if abs(left_out - right_in) > slop:
        return "不能合并：源出入点不连续"
    speed_l = float(getattr(left, "speed", 1.0) or 1.0)
    speed_r = float(getattr(right, "speed", 1.0) or 1.0)
    if abs(speed_l - speed_r) > 1e-6:
        return "不能合并：两侧速度不同"
    if track is not None:
        _ls, left_end = clip_span(left)
        right_start, _re = clip_span(right)
        if abs(left_end - right_start) > slop:
            return "不能合并：时间线上不相邻"
        if transition_at_seam(track, right_start, fps=fps) is not None:
            return "不能合并：接缝上已有转场"
    return None


def join_clips(track, left_index, right_index, fps=30.0):
    """Merge right into left (inverse of split_at). Mutates ``track.clips``.

    Raises JoinError with a status-bar-ready Chinese reason.
    """
    clips = getattr(track, "clips", None)
    if clips is None:
        raise JoinError("不能合并：轨道没有片段")
    n = len(clips)
    li, ri = int(left_index), int(right_index)
    if li < 0 or ri < 0 or li >= n or ri >= n or li == ri:
        raise JoinError("不能合并：片段下标无效")
    left, right = clips[li], clips[ri]
    reason = cannot_join_reason(left, right, track=track, fps=_fps_of(track, fps))
    if reason:
        raise JoinError(reason)
    left.out_t = float(getattr(right, "out_t", left.out_t) or left.out_t)
    del clips[ri]
    return left


def _audio_fade_for_clip(clip, track, fps=30.0):
    fade_in = float(getattr(clip, "fade_in_dur", 0.0) or 0.0)
    fade_out = float(getattr(clip, "fade_out_dur", 0.0) or 0.0)
    curve = "tri"
    start, end = clip_span(clip)
    slop = _frame_sec(fps)
    incoming = {
        "audio_xfade_equal", "audio_xfade_linear",
        "audio_fade_in", "audio_seam_fade",
    }
    outgoing = {
        "audio_xfade_equal", "audio_xfade_linear",
        "audio_fade_out", "audio_seam_fade",
    }
    for trans in getattr(track, "transitions", []) or []:
        if str(getattr(trans, "domain", "visual") or "visual") != "audio":
            continue
        t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
        dur = float(getattr(trans, "duration", 0.0) or 0.0)
        t1 = t0 + dur
        preset = str(getattr(trans, "preset", "") or "")
        use_dur = 0.030 if preset == "audio_seam_fade" else dur
        covers_start = (t0 - slop) <= start <= (t1 + slop)
        covers_end = (t0 - slop) <= end <= (t1 + slop)
        if covers_start and preset in incoming:
            fade_in = max(fade_in, use_dur)
            if preset == "audio_xfade_equal":
                curve = "esin"
        if covers_end and preset in outgoing:
            fade_out = max(fade_out, use_dur)
            if preset == "audio_xfade_equal":
                curve = "esin"
    return fade_in, fade_out, curve


def apply_audio_transition_fades(task_data):
    """Export records: compute_export_audio_tracks plus audio TransitionItem fades.

    Does not mutate Clip objects. Each record is
    ``{clip, fade_in_dur, fade_out_dur, fade_curve}``.
    """
    from cutora.model.track_guard import compute_export_audio_tracks, iter_tracks

    export = compute_export_audio_tracks(task_data)
    export_ids = {id(c) for c in export}
    tl = task_data.get("timeline") if isinstance(task_data, dict) else None
    fps = float(getattr(tl, "fps", 30.0) or 30.0) if tl is not None else 30.0
    recs = []
    for tr in iter_tracks(task_data):
        if getattr(tr, "domain", "visual") != "audio":
            continue
        for clip in getattr(tr, "clips", []) or []:
            if id(clip) not in export_ids:
                continue
            fade_in, fade_out, curve = _audio_fade_for_clip(clip, tr, fps=fps)
            recs.append({
                "clip": clip,
                "fade_in_dur": fade_in,
                "fade_out_dur": fade_out,
                "fade_curve": curve,
            })
    return recs
