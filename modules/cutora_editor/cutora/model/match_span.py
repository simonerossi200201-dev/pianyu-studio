# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""How long a loop+match clip should run.

The checkbox used to copy QMediaPlayer.duration(). That clock is whichever
file the transport loaded (often a short proxy), so the overlay stopped
early and the next tick wrote a new target. Host length is the timeline:
clips that are themselves matching contribute only their source span, so
two overlays cannot chase each other.
"""
from __future__ import annotations


def _num(obj, key, default=0.0) -> float:
    if isinstance(obj, dict):
        raw = obj.get(key, default)
    else:
        raw = getattr(obj, key, default)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _is_match(clip) -> bool:
    if isinstance(clip, dict):
        return bool(clip.get("match_duration"))
    return bool(getattr(clip, "match_duration", False))


def unmatched_span(clip) -> float:
    """Timeline seconds of one play of the source. Ignores _match_target_dur."""
    inn = _num(clip, "in_t", 0.0)
    out = _num(clip, "out_t", inn)
    speed = _num(clip, "speed", 1.0)
    if isinstance(clip, dict):
        curve = clip.get("speed_curve") or []
    else:
        curve = getattr(clip, "speed_curve", None) or []
    src = max(0.0, out - inn)
    if curve:
        from cutora.audio.speed_curve import integrated_out_dur
        return float(integrated_out_dur(src, speed, curve))
    return src / max(0.01, speed)


def _placed_span(clip) -> float:
    if _is_match(clip):
        return unmatched_span(clip)
    if not isinstance(clip, dict):
        try:
            span = float(getattr(clip, "effective_duration", 0.0) or 0.0)
        except Exception:
            span = 0.0
        if span > 0.0:
            return span
    return unmatched_span(clip)


def _tracks(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    return tracks or []


def host_end_sec(task) -> float:
    """Seconds the edit runs, without match-clips inflating each other."""
    if not isinstance(task, dict):
        return 0.0
    end = 0.0
    for tr in _tracks(task):
        clips = getattr(tr, "clips", None)
        if clips is None and isinstance(tr, dict):
            clips = tr.get("clips") or []
        for clip in clips or []:
            end = max(end, _num(clip, "start_t", 0.0) + _placed_span(clip))
    for key in ("duration", "trim_end_sec"):
        end = max(end, _num(task, key, 0.0))
    return end


def match_target_sec(task, clip) -> float:
    """Seconds this clip should cover from its own start_t."""
    return max(0.0, host_end_sec(task) - _num(clip, "start_t", 0.0))


def refresh_match_targets(task, *, video_only=True):
    """Write _match_target_dur. Returns (clip, old, new) for real changes.

    A target under 0.05 s is ignored so a half-loaded timeline cannot wipe
    a saved length.
    """
    changed = []
    if not isinstance(task, dict):
        return changed
    for tr in _tracks(task):
        if video_only and getattr(tr, "type", None) != "video":
            continue
        clips = getattr(tr, "clips", None)
        if clips is None and isinstance(tr, dict):
            clips = tr.get("clips") or []
        for clip in clips or []:
            if not _is_match(clip):
                continue
            target = match_target_sec(task, clip)
            if target <= 0.05:
                continue
            old = _num(clip, "_match_target_dur", 0.0)
            if abs(old - target) <= 0.01:
                continue
            if isinstance(clip, dict):
                clip["_match_target_dur"] = target
            else:
                clip._match_target_dur = target
            changed.append((clip, old, target))
    return changed
