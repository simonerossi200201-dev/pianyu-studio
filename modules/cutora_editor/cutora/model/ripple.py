# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ripple delete / insert / close-gaps. Qt-free. One call = one atomic edit."""
from __future__ import annotations

from cutora.subtitle.track_source import set_segments
from cutora.ui.timeline.snap import MIN_DUR, clip_span

EPS = 1e-9


def _tl(task):
    return task.get("timeline") if isinstance(task, dict) else None


def classify(start, end, t0, t1) -> str:
    s, e = float(start), float(end)
    if e <= t0 + EPS:
        return "before"
    if s >= t1 - EPS:
        return "after"
    if s >= t0 - EPS and e <= t1 + EPS:
        return "contained"
    if s < t0 and e <= t1 + EPS:
        return "span_left"
    if s >= t0 - EPS and e > t1:
        return "span_right"
    return "span_through"


def _crop_words(words, t0, t1, delta):
    if not isinstance(words, list):
        return []
    out = []
    for w in words:
        if not isinstance(w, dict):
            continue
        ws = float(w.get("start") or 0.0)
        we = float(w.get("end") or 0.0)
        kind = classify(ws, we, t0, t1)
        nw = dict(w)
        if kind == "before":
            out.append(nw)
        elif kind == "after":
            nw["start"] = ws - delta
            nw["end"] = we - delta
            out.append(nw)
        elif kind == "contained":
            continue
        elif kind == "span_left":
            nw["end"] = t0
            if nw["end"] - ws >= MIN_DUR - EPS:
                out.append(nw)
        elif kind == "span_right":
            nw["start"] = t0
            nw["end"] = we - delta
            if nw["end"] - nw["start"] >= MIN_DUR - EPS:
                out.append(nw)
        else:
            nw["end"] = we - delta
            if nw["end"] - ws >= MIN_DUR - EPS:
                out.append(nw)
    return out


def _apply_interval_to_pair(start, end, t0, t1, delta):
    """Return ('keep', ns, ne) or ('delete', None, None)."""
    kind = classify(start, end, t0, t1)
    if kind == "before":
        return "keep", start, end
    if kind == "after":
        return "keep", start - delta, end - delta
    if kind == "contained":
        return "delete", None, None
    if kind == "span_left":
        ne = t0
        if ne - start < MIN_DUR - EPS:
            return "delete", None, None
        return "keep", start, ne
    if kind == "span_right":
        ns, ne = t0, end - delta
        if ne - ns < MIN_DUR - EPS:
            return "delete", None, None
        return "keep", ns, ne
    ne = end - delta
    if ne - start < MIN_DUR - EPS:
        return "delete", None, None
    return "keep", start, ne


def _find_track(tl, track_id):
    if tl is None:
        return None
    for tr in getattr(tl, "tracks", []) or []:
        if str(getattr(tr, "id", "")) == str(track_id):
            return tr
    return None


def _linked_clips(tl, clip):
    gid = getattr(clip, "link_group_id", None)
    out = []
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            if c is clip or (gid and getattr(c, "link_group_id", None) == gid):
                out.append((tr, c))
    return out or [(None, clip)]


def _shift_clip(clip, delta):
    clip.start_t = float(getattr(clip, "start_t", 0.0)) + float(delta)


def _trim_clip_end(clip, new_end):
    s, _e = clip_span(clip)
    new_eff = float(new_end) - s
    speed = max(0.01, float(getattr(clip, "speed", 1.0) or 1.0))
    clip.out_t = float(getattr(clip, "in_t", 0.0)) + new_eff * speed


def _trim_clip_start_keep_tail(clip, t0, t1):
    """Survive the tail after t1, park it at t0."""
    s, e = clip_span(clip)
    new_eff = e - t1
    speed = max(0.01, float(getattr(clip, "speed", 1.0) or 1.0))
    consumed = (t1 - s) * speed
    clip.in_t = float(getattr(clip, "in_t", 0.0)) + consumed
    clip.start_t = float(t0)


def _repair_transitions(track):
    if track is None or not hasattr(track, "transitions"):
        return
    clips = list(getattr(track, "clips", []) or [])
    kept = []
    for trans in list(getattr(track, "transitions", []) or []):
        ts = float(getattr(trans, "start_t", 0.0))
        te = ts + float(getattr(trans, "duration", 0.0) or 0.0)
        outgoing = incoming = None
        for c in clips:
            cs, ce = clip_span(c)
            if abs(ce - te) < 0.2:
                outgoing = c
            if abs(cs - ts) < 0.2:
                incoming = c
        if outgoing is None or incoming is None or outgoing is incoming:
            continue
        _os, oe = clip_span(outgoing)
        ins, ie = clip_span(incoming)
        tdur = float(getattr(trans, "duration", 0.0) or 0.0)
        if (oe - _os) + EPS < tdur or (ie - ins) + EPS < tdur:
            continue
        kept.append(trans)
    track.transitions = kept


def apply_delete_range(task, t0, t1, track_id, skip_clips=None):
    """Delete time [t0, t1) on track_id; shift/truncate others. Returns stats."""
    t0, t1 = float(t0), float(t1)
    if t1 <= t0 + EPS:
        return {"delta": 0.0, "removed_segs": 0, "removed_markers": 0}
    delta = t1 - t0
    skip = set(id(c) for c in (skip_clips or []))
    tl = _tl(task)
    track = _find_track(tl, track_id)
    removed_clips = []

    def each_clip(tr, clip):
        if id(clip) in skip:
            return
        s, e = clip_span(clip)
        kind = classify(s, e, t0, t1)
        if kind == "before":
            return
        if kind == "after":
            _shift_clip(clip, -delta)
        elif kind == "contained":
            tr.clips.remove(clip)
            removed_clips.append(clip)
        elif kind == "span_left":
            if t0 - s < MIN_DUR - EPS:
                tr.clips.remove(clip)
                removed_clips.append(clip)
            else:
                _trim_clip_end(clip, t0)
        elif kind == "span_right":
            if e - t1 < MIN_DUR - EPS:
                tr.clips.remove(clip)
                removed_clips.append(clip)
            else:
                _trim_clip_start_keep_tail(clip, t0, t1)
        else:
            if t0 - s < MIN_DUR - EPS:
                if e - t1 < MIN_DUR - EPS:
                    tr.clips.remove(clip)
                    removed_clips.append(clip)
                else:
                    _trim_clip_start_keep_tail(clip, t0, t1)
            else:
                # Clone the tail before trimming the head. A clone taken
                # after _trim_clip_end only has the left piece left.
                if e - t1 >= MIN_DUR - EPS:
                    tail = clip.clone()
                    _trim_clip_start_keep_tail(tail, t0, t1)
                    tr.clips.append(tail)
                _trim_clip_end(clip, t0)

    if tl is not None:
        tracks = []
        if track is not None:
            tracks.append(track)
            for c in list(getattr(track, "clips", []) or []):
                for tr2, c2 in _linked_clips(tl, c):
                    if tr2 is not None and tr2 not in tracks:
                        tracks.append(tr2)
        for tr in tracks:
            for clip in list(getattr(tr, "clips", []) or []):
                each_clip(tr, clip)
            for trans in list(getattr(tr, "transitions", []) or []):
                ts = float(getattr(trans, "start_t", 0.0))
                te = ts + float(getattr(trans, "duration", 0.0) or 0.0)
                kind = classify(ts, te, t0, t1)
                if kind == "after":
                    trans.start_t = ts - delta
                elif kind != "before":
                    tr.transitions.remove(trans)
            _repair_transitions(tr)

    segs = list(task.get("segments") or [])
    kept_segs = []
    removed_segs = 0
    for seg in segs:
        if not isinstance(seg, dict):
            kept_segs.append(seg)
            continue
        s = float(seg.get("start") or 0.0)
        e = float(seg.get("end") or 0.0)
        action, ns, ne = _apply_interval_to_pair(s, e, t0, t1, delta)
        if action == "delete":
            removed_segs += 1
            continue
        seg["words"] = _crop_words(seg.get("words"), t0, t1, delta)
        seg["start"] = ns
        seg["end"] = ne
        kept_segs.append(seg)
    set_segments(task, kept_segs)

    markers = list(task.get("markers") or [])
    kept_mk = []
    removed_mk = 0
    for rec in markers:
        if not isinstance(rec, dict):
            kept_mk.append(rec)
            continue
        t = float(rec.get("t") or 0.0)
        if t <= t0 + EPS:
            kept_mk.append(rec)
        elif t >= t1 - EPS:
            rec["t"] = t - delta
            kept_mk.append(rec)
        else:
            removed_mk += 1
    if "markers" in task or kept_mk:
        task["markers"] = kept_mk

    tid = str(track_id or "")
    for key in ("text_overlays", "image_overlays"):
        rows = list(task.get(key) or [])
        kept = []
        for ov in rows:
            if not isinstance(ov, dict):
                kept.append(ov)
                continue
            ov_tid = str(ov.get("_track_id") or "")
            if ov_tid and ov_tid != tid:
                kept.append(ov)
                continue
            s = float(ov.get("start") or 0.0)
            e = float(ov.get("end") or 0.0)
            action, ns, ne = _apply_interval_to_pair(s, e, t0, t1, delta)
            if action == "delete":
                continue
            ov["start"] = ns
            ov["end"] = ne
            kept.append(ov)
        if key in task:
            task[key] = kept

    return {
        "delta": delta, "t0": t0, "t1": t1,
        "removed_segs": removed_segs, "removed_markers": removed_mk,
    }


def apply_insert(task, t, duration, track_id, skip_clips=None):
    """Insert `duration` at `t` on track_id. Spanning objects shift whole."""
    t = float(t)
    duration = float(duration)
    if duration <= EPS:
        return {"nudged": []}
    skip = set(id(c) for c in (skip_clips or []))
    tl = _tl(task)
    track = _find_track(tl, track_id)
    nudged = []

    def maybe_shift_clip(clip, name):
        if id(clip) in skip:
            return
        s, e = clip_span(clip)
        if e <= t + EPS:
            return
        if s >= t - EPS:
            _shift_clip(clip, duration)
            return
        if s < t < e:
            _shift_clip(clip, duration)
            nudged.append(name or "clip")

    if tl is not None:
        tracks = []
        if track is not None:
            tracks.append(track)
            for c in list(getattr(track, "clips", []) or []):
                for tr2, c2 in _linked_clips(tl, c):
                    if tr2 is not None and tr2 not in tracks:
                        tracks.append(tr2)
        for tr in tracks:
            for clip in list(getattr(tr, "clips", []) or []):
                maybe_shift_clip(clip, getattr(clip, "source_id", None) or "clip")
            for trans in list(getattr(tr, "transitions", []) or []):
                ts = float(getattr(trans, "start_t", 0.0))
                te = ts + float(getattr(trans, "duration", 0.0) or 0.0)
                if te <= t + EPS:
                    continue
                if ts >= t - EPS:
                    trans.start_t = ts + duration
                else:
                    tr.transitions.remove(trans)
            _repair_transitions(tr)

    for seg in list(task.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        s = float(seg.get("start") or 0.0)
        e = float(seg.get("end") or 0.0)
        if e <= t + EPS:
            continue
        from cutora.subtitle.edit import apply_move
        if s >= t - EPS:
            apply_move(seg, s + duration)
        elif s < t < e:
            apply_move(seg, s + duration)
            nudged.append(str(seg.get("text") or "字幕")[:16])

    for rec in list(task.get("markers") or []):
        if not isinstance(rec, dict):
            continue
        mt = float(rec.get("t") or 0.0)
        if mt >= t - EPS:
            rec["t"] = mt + duration

    tid = str(track_id or "")
    for key in ("text_overlays", "image_overlays"):
        for ov in list(task.get(key) or []):
            if not isinstance(ov, dict):
                continue
            ov_tid = str(ov.get("_track_id") or "")
            if ov_tid and ov_tid != tid:
                continue
            s = float(ov.get("start") or 0.0)
            e = float(ov.get("end") or 0.0)
            if e <= t + EPS:
                continue
            if s >= t - EPS or (s < t < e):
                dur = e - s
                if s < t < e:
                    nudged.append(str(ov.get("type") or key))
                ov["start"] = s + duration
                ov["end"] = ov["start"] + dur

    return {"nudged": nudged, "t": t, "duration": duration}


def ripple_delete_targets(task, targets):
    """Delete selected clips/segs and ripple their time ranges, right to left."""
    tl = _tl(task)
    intervals = []
    skip = []
    segs = list(task.get("segments") or [])
    for row in targets or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "clip")
        if kind == "seg":
            sid = int(row.get("seg_id", row.get("clip_index", -1)))
            if 0 <= sid < len(segs) and isinstance(segs[sid], dict):
                s = float(segs[sid].get("start") or 0.0)
                e = float(segs[sid].get("end") or 0.0)
                intervals.append((s, e, None, segs[sid]))
        else:
            tr = _find_track(tl, row.get("track_id"))
            idx = int(row.get("clip_index", -1))
            clips = list(getattr(tr, "clips", []) or []) if tr is not None else []
            if tr is None or not (0 <= idx < len(clips)):
                continue
            clip = clips[idx]
            for tr2, c2 in _linked_clips(tl, clip):
                skip.append(c2)
                if tr2 is not None and c2 in (getattr(tr2, "clips", []) or []):
                    tr2.clips.remove(c2)
            s, e = clip_span(clip)
            intervals.append((s, e, getattr(tr, "id", None), None))
    # selected segs that are fully in an interval get deleted via range;
    # selected segs we also drop explicitly if contained.
    for s, e, _tid, seg in intervals:
        if seg is not None:
            try:
                segs.remove(seg)
            except ValueError:
                pass
    if segs != list(task.get("segments") or []):
        set_segments(task, segs)
    # merge intervals on same track, process from the right
    work = [(float(s), float(e), tid) for s, e, tid, _seg in intervals if e > s + EPS]
    work.sort(key=lambda r: r[0])
    merged = []
    for s, e, tid in work:
        if merged and tid == merged[-1][2] and s <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e), tid)
        else:
            merged.append((s, e, tid))
    stats = []
    for s, e, tid in reversed(merged):
        stats.append(apply_delete_range(task, s, e, tid, skip_clips=skip))
    return {"ranges": stats, "count": len(merged)}


def close_gaps(task, track_id, at_t=None):
    tl = _tl(task)
    track = _find_track(tl, track_id)
    if track is None:
        raise ValueError("找不到轨道")
    clips = sorted(
        list(getattr(track, "clips", []) or []),
        key=lambda c: clip_span(c)[0],
    )
    gaps = []
    for i in range(len(clips) - 1):
        _s, e = clip_span(clips[i])
        n, _ne = clip_span(clips[i + 1])
        if n > e + EPS:
            gaps.append((e, n))
    if at_t is not None:
        at = float(at_t)
        gaps = [g for g in gaps if g[0] - EPS <= at < g[1] + EPS]
    closed = 0
    for t0, t1 in reversed(gaps):
        apply_delete_range(task, t0, t1, track_id)
        closed += 1
    return {"closed": closed, "track_id": str(track_id)}
