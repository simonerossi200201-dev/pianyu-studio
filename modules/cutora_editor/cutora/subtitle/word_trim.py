# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Word-edge trim on a subtitle segment. Adjacent word yields; others stay.

Used by subtitle.trim_word (command bus) and the timeline word-block drag.
One edge of one word moves; the shared boundary with the neighbor follows.
No overlap, no non-positive duration. Extra keys on word dicts are kept.
"""
from __future__ import annotations

from cutora.subtitle.track_source import set_segments

MIN_WORD_DUR = 0.02


def _as_word(item) -> dict:
    if not isinstance(item, dict):
        raise ValueError("word 不是对象")
    return item


def _span(word: dict) -> tuple[float, float]:
    start = float(word.get("start") or 0.0)
    end = float(word.get("end") or start)
    return start, end


def clamp_word_edge(words, index, edge, new_t, min_dur=MIN_WORD_DUR,
                    seg_start=None, seg_end=None) -> float:
    """Return the clamped time for ``edge`` of ``words[index]``.

    First-word left and last-word right stay inside the subtitle segment
    when ``seg_start`` / ``seg_end`` are given.
    """
    rows = list(words or [])
    idx = int(index)
    if idx < 0 or idx >= len(rows):
        raise ValueError("word_index 越界")
    kind = str(edge or "").strip().lower()
    if kind not in ("left", "right"):
        raise ValueError("edge 必须是 left 或 right")
    this = _as_word(rows[idx])
    start, end = _span(this)
    lo = start + float(min_dur) if kind == "right" else 0.0
    hi = end - float(min_dur) if kind == "left" else end
    if kind == "right":
        lo = start + float(min_dur)
        if idx + 1 < len(rows):
            _ns, nxt_end = _span(_as_word(rows[idx + 1]))
            hi = nxt_end - float(min_dur)
        elif seg_end is not None:
            hi = float(seg_end)
        else:
            hi = max(lo, float(new_t))
    else:
        hi = end - float(min_dur)
        if idx - 1 >= 0:
            prev_start, _pe = _span(_as_word(rows[idx - 1]))
            lo = prev_start + float(min_dur)
        elif seg_start is not None:
            lo = float(seg_start)
        else:
            lo = 0.0
    if hi < lo:
        return lo if kind == "right" else hi
    t = float(new_t)
    if t < lo:
        t = lo
    if t > hi:
        t = hi
    return t


def apply_word_trim(words, index, edge, new_t, min_dur=MIN_WORD_DUR,
                    seg_start=None, seg_end=None) -> list:
    """Copy ``words``, move one edge, yield the adjacent word, leave the rest.

    Dragging word i's right edge changes word i ``end`` and word i+1 ``start``.
    Dragging word i's left edge changes word i ``start`` and word i-1 ``end``.
    """
    rows = [dict(_as_word(w)) for w in (words or [])]
    idx = int(index)
    if idx < 0 or idx >= len(rows):
        raise ValueError("word_index 越界")
    kind = str(edge or "").strip().lower()
    t = clamp_word_edge(
        rows, idx, kind, new_t, min_dur=min_dur,
        seg_start=seg_start, seg_end=seg_end)
    if kind == "right":
        rows[idx]["end"] = t
        if idx + 1 < len(rows):
            rows[idx + 1]["start"] = t
    else:
        rows[idx]["start"] = t
        if idx - 1 >= 0:
            rows[idx - 1]["end"] = t
    return rows


def word_snap_times(words, skip_index=None, extra=None) -> list[float]:
    """Other word boundaries plus caller extras (playhead, silence, …)."""
    out = []
    skip = skip_index
    for i, w in enumerate(words or []):
        if not isinstance(w, dict):
            continue
        start, end = _span(w)
        if skip is None or i != int(skip):
            out.append(start)
            out.append(end)
        elif i == int(skip):
            # Own far edge is still a snap target for the dragged edge.
            pass
    for t in extra or []:
        try:
            out.append(float(t))
        except (TypeError, ValueError):
            continue
    return out


def snap_time_px(value, targets, pixels_per_second, snap_px) -> float:
    """Snap ``value`` to the nearest target within ``snap_px`` pixels."""
    pps = max(float(pixels_per_second), 1e-6)
    thr = float(snap_px) / pps
    v = float(value)
    best = None
    best_d = None
    for raw in targets or []:
        t = float(raw)
        d = abs(t - v)
        if d <= thr + 1e-12 and (best_d is None or d < best_d - 1e-15):
            best, best_d = t, d
    return v if best is None else best


def trim_word_task(task: dict, seg_id: int, word_index: int, edge: str,
                   new_t: float, min_dur=MIN_WORD_DUR) -> dict:
    segs = list(task.get("segments") or [])
    if seg_id < 0 or seg_id >= len(segs) or not isinstance(segs[seg_id], dict):
        raise ValueError("seg_id 越界")
    seg = segs[seg_id]
    words = list(seg.get("words") or [])
    new_words = apply_word_trim(
        words, word_index, edge, new_t, min_dur=min_dur,
        seg_start=float(seg.get("start") or 0.0),
        seg_end=float(seg.get("end") or 0.0),
    )
    seg["words"] = new_words
    set_segments(task, segs)
    kind = str(edge or "").strip().lower()
    w = new_words[int(word_index)]
    return {
        "seg_id": int(seg_id),
        "word_index": int(word_index),
        "edge": kind,
        "t": float(w["end"] if kind == "right" else w["start"]),
    }
