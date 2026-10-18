# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared transition compositing + freeze-frame handles.

Preview (preview/engine.py, ui/canvas.py) and export (render_worker.py)
must call the same helpers. Out-of-range source time used to return None,
so the offscreen buffer stayed fill(0) transparent and SourceOver onto the
export canvas (filled black) produced the half-transparent-on-black mix.
"""
from __future__ import annotations


def _g(obj, key, default=0.0):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def clip_timeline_span(clip) -> tuple[float, float]:
    start = float(_g(clip, "start_t", 0.0) or 0.0)
    try:
        eff = float(getattr(clip, "effective_duration", 0.0) or 0.0)
    except Exception:
        eff = 0.0
    if eff <= 0:
        in_t = float(_g(clip, "in_t", 0.0) or 0.0)
        out_t = float(_g(clip, "out_t", in_t) or in_t)
        speed = float(_g(clip, "speed", 1.0) or 1.0)
        eff = max(0.0, (out_t - in_t) / max(0.01, speed))
    return start, start + eff


def source_duration_of(clip, cap=None, frame_state=None, source=None) -> float:
    """Best-effort source length in seconds. Falls back to clip.out_t (no extra)."""
    if isinstance(frame_state, dict):
        cached = frame_state.get("src_duration")
        if cached is not None:
            try:
                d = float(cached)
                if d > 0:
                    return d
            except (TypeError, ValueError):
                pass
    if source is not None:
        try:
            d = float(_g(source, "duration", 0.0) or 0.0)
            if d > 0:
                if isinstance(frame_state, dict):
                    frame_state["src_duration"] = d
                return d
        except (TypeError, ValueError):
            pass
    if cap is not None:
        try:
            import cv2
            n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if n > 0 and fps > 0:
                d = n / fps
                if isinstance(frame_state, dict):
                    frame_state["src_duration"] = d
                return d
        except Exception:
            pass
    out_t = float(_g(clip, "out_t", 0.0) or 0.0)
    return max(0.0, out_t)


def source_time_for_clip(clip, t, source_duration, fps=30.0, extend=False):
    """Map timeline t to a source timestamp.

    Returns ``(src_t, frozen)``. ``src_t`` is None when the clip should not
    contribute (t outside the clip and ``extend`` is False).

    When ``extend`` is True and t is outside the clip's timeline span:
      * extra source after out_t / before in_t is used first (real frames);
      * leftover is clamped to the first/last available source frame (freeze).
    In-range t is unchanged: no clamp, frozen=False.
    """
    start_t = float(_g(clip, "start_t", 0.0) or 0.0)
    in_t = float(_g(clip, "in_t", 0.0) or 0.0)
    out_t = float(_g(clip, "out_t", in_t) or in_t)
    speed = float(_g(clip, "speed", 1.0) or 1.0)
    loop = bool(_g(clip, "loop", False))
    start, end = clip_timeline_span(clip)
    t = float(t)
    in_range = start <= t < end
    if not in_range and not extend:
        return None, False
    if bool(_g(clip, "is_thumbnail_leadin", False)):
        return float(_g(clip, "freeze_source_t", in_t) or in_t), False
    elapsed = (t - start_t) * max(0.01, speed)
    src_span = max(0.01, out_t - in_t)
    if loop and src_span > 0:
        elapsed = elapsed % src_span
    src_t = in_t + elapsed
    if in_range:
        return src_t, False
    fps = max(1.0, float(fps or 30.0))
    src_dur = float(source_duration or 0.0)
    if src_dur <= 0.0:
        src_dur = max(out_t, 0.0)
    last_t = max(0.0, src_dur - 1.0 / fps)
    frozen = False
    if src_t < 0.0:
        src_t = 0.0
        frozen = True
    elif src_t > last_t:
        src_t = last_t
        frozen = True
    return src_t, frozen


def clips_for_transition(track, trans):
    """Outgoing (earliest start) and incoming (latest start) clips under *trans*."""
    if trans is None:
        return None, None
    t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
    t1 = t0 + float(getattr(trans, "duration", 0.0) or 0.0)
    bag = []
    for clip in getattr(track, "clips", []) or []:
        cs, ce = clip_timeline_span(clip)
        if cs < t1 + 1e-6 and ce > t0 - 1e-6:
            bag.append((cs, ce, clip))
    if not bag:
        return None, None
    bag.sort(key=lambda row: (row[0], row[1]))
    out_clip = bag[0][2]
    in_clip = bag[-1][2] if len(bag) > 1 else None
    if in_clip is out_clip:
        in_clip = None
    return out_clip, in_clip


def _source_of(clip, sources):
    if not sources:
        return None
    sid = _g(clip, "source_id", None)
    if not sid:
        return None
    return sources.get(sid)


def clip_freeze_deficit_sec(clip, trans, role, source_duration, fps=30.0) -> float:
    """Seconds of freeze this clip still needs inside *trans*. 0 = enough media."""
    if clip is None or trans is None:
        return 0.0
    t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
    t1 = t0 + float(getattr(trans, "duration", 0.0) or 0.0)
    start, end = clip_timeline_span(clip)
    in_t = float(_g(clip, "in_t", 0.0) or 0.0)
    out_t = float(_g(clip, "out_t", in_t) or in_t)
    speed = max(0.01, float(_g(clip, "speed", 1.0) or 1.0))
    src_dur = float(source_duration or 0.0)
    if src_dur <= 0.0:
        src_dur = max(out_t, 0.0)
    fps = max(1.0, float(fps or 30.0))
    if role == "out":
        need = max(0.0, t1 - end)
        available = max(0.0, src_dur - out_t) / speed
    else:
        need = max(0.0, start - t0)
        available = max(0.0, in_t) / speed
    deficit = need - available
    if deficit < (0.5 / fps):
        return 0.0
    return deficit


def freeze_extents(track, trans, sources=None, fps=30.0):
    """``(left_freeze_sec, right_freeze_sec)`` of the tape that is frozen.

    Incoming (B) freeze lives on the left of the tape; outgoing (A) on the right.
    """
    out_clip, in_clip = clips_for_transition(track, trans)
    left = 0.0
    right = 0.0
    if in_clip is not None:
        src = _source_of(in_clip, sources)
        left = clip_freeze_deficit_sec(
            in_clip, trans, "in", source_duration_of(in_clip, source=src), fps)
    if out_clip is not None:
        src = _source_of(out_clip, sources)
        right = clip_freeze_deficit_sec(
            out_clip, trans, "out", source_duration_of(out_clip, source=src), fps)
    return left, right


def transition_needs_freeze(track, trans, sources=None, fps=30.0) -> bool:
    left, right = freeze_extents(track, trans, sources=sources, fps=fps)
    return left > 0.0 or right > 0.0


def paint_standalone_transition(painter, track, trans, t, w, h, render_clip):
    """Find A/B, render each via *render_clip*, compose, draw onto *painter*.

    *render_clip(clip) -> QImage | None* must use extend_handles so short
    media freeze instead of returning a transparent buffer.
    """
    if trans is None:
        return
    if str(getattr(trans, "domain", "visual") or "visual") == "audio":
        return
    from cutora.render import anim_engine

    t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
    dur = float(getattr(trans, "duration", 0.0) or 0.0)
    prog = max(0.0, min(1.0, (float(t) - t0) / max(0.001, dur)))
    out_clip, in_clip = clips_for_transition(track, trans)
    out_img = render_clip(out_clip) if out_clip is not None else None
    in_img = render_clip(in_clip) if in_clip is not None else None
    if in_img is None or (hasattr(in_img, "isNull") and in_img.isNull()):
        if out_img is not None and not (hasattr(out_img, "isNull") and out_img.isNull()):
            painter.drawImage(0, 0, out_img)
        return
    if out_img is None or (hasattr(out_img, "isNull") and out_img.isNull()):
        painter.drawImage(0, 0, in_img)
        return
    comp = anim_engine.compose_standalone_transition(
        prog, getattr(trans, "preset", "cross_dissolve"),
        out_img, in_img, int(w), int(h),
        zoom_scale_start=getattr(trans, "zoom_scale_start", 0.7),
        motion_blur=getattr(trans, "motion_blur", 0.4),
        intensity=getattr(trans, "intensity", 1.0),
        easing=getattr(trans, "easing", "ease_in_out"),
        direction=getattr(trans, "direction", None),
        angle=getattr(trans, "angle", None),
        feather=getattr(trans, "feather", None),
    )
    if comp is not None and not comp.isNull():
        painter.drawImage(0, 0, comp)
