# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Subs track is the unique subtitle source. task['segments'] is a derived view.

Write through ``set_segments``. Read through ``get_segments``. Never keep two
live copies that can drift.
"""
from __future__ import annotations

from cutora.core.log import get_logger

logger = get_logger("cutora.subtitle.track_source")

EVENTS_KEY = "subtitle_events"
SUBS_TYPES = frozenset(("subs", "subtitle"))


def _track_type(tr) -> str:
    ttype = getattr(tr, "type", None)
    if ttype is None and isinstance(tr, dict):
        ttype = tr.get("type")
    return str(ttype or "")


def _tracks_of(task):
    if not isinstance(task, dict):
        return []
    tl = task.get("timeline")
    if tl is None:
        return []
    tracks = getattr(tl, "tracks", None)
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    return list(tracks or [])


def iter_subs_tracks(task):
    for tr in _tracks_of(task):
        if _track_type(tr) in SUBS_TYPES:
            yield tr


def first_subs_track(task):
    for tr in iter_subs_tracks(task):
        return tr
    return None


def events_of(tr):
    """Return the live subtitle-event list on a subs track, or None."""
    if tr is None:
        return None
    if isinstance(tr, dict):
        ev = tr.get(EVENTS_KEY)
        return ev if isinstance(ev, list) else None
    ev = getattr(tr, EVENTS_KEY, None)
    if isinstance(ev, list):
        return ev
    extra = getattr(tr, "_extra", None)
    if isinstance(extra, dict):
        ev = extra.get(EVENTS_KEY)
        if isinstance(ev, list):
            return ev
    return None


def write_events(tr, segs) -> list:
    """Store ``segs`` on the track (attribute + _extra for serialize)."""
    segs = list(segs or [])
    if tr is None:
        return segs
    if isinstance(tr, dict):
        tr[EVENTS_KEY] = segs
        return segs
    setattr(tr, EVENTS_KEY, segs)
    extra = getattr(tr, "_extra", None)
    if not isinstance(extra, dict):
        extra = {}
        tr._extra = extra
    extra[EVENTS_KEY] = segs
    return segs


def hydrate_track(tr) -> None:
    """After deserialize: lift ``_extra['subtitle_events']`` onto the attribute."""
    if tr is None or isinstance(tr, dict):
        return
    extra = getattr(tr, "_extra", None) or {}
    ev = getattr(tr, EVENTS_KEY, None)
    if ev is None and isinstance(extra, dict):
        ev = extra.get(EVENTS_KEY)
    if ev is None:
        return
    write_events(tr, ev if isinstance(ev, list) else [])


def _clip_text(clip) -> str:
    if clip is None:
        return ""
    if isinstance(clip, dict):
        return str(clip.get("text") or clip.get("content") or "")
    return str(getattr(clip, "text", None) or getattr(clip, "content", None) or "")


def _clips_of(tr) -> list:
    clips = getattr(tr, "clips", None)
    if clips is None and isinstance(tr, dict):
        clips = tr.get("clips")
    return list(clips or [])


def track_event_count(tr) -> int:
    """How many subtitle items live on this track (events + text clips)."""
    n = 0
    ev = events_of(tr)
    if isinstance(ev, list):
        n += len(ev)
    for clip in _clips_of(tr):
        if _clip_text(clip).strip():
            n += 1
    return n


def count_track_subs(task) -> int:
    return sum(track_event_count(tr) for tr in iter_subs_tracks(task))


def count_task_segments(task) -> int:
    if not isinstance(task, dict):
        return 0
    segs = task.get("segments") or []
    return len(segs) if isinstance(segs, list) else 0


def _read_segments(task) -> list:
    out = []
    for tr in iter_subs_tracks(task):
        ev = events_of(tr)
        if not isinstance(ev, list):
            continue
        for item in ev:
            if isinstance(item, dict):
                out.append(item)
    return out


def get_segments(task) -> list:
    """Read from the subs track only. Never from ``task['segments']``."""
    try:
        return _read_segments(task)
    except Exception:
        logger.exception("subtitle track read failed")
        return []


def _ensure_subs_track(task):
    tr = first_subs_track(task)
    if tr is not None:
        return tr
    if not isinstance(task, dict):
        return None
    tl = task.get("timeline")
    if tl is None:
        return None
    if isinstance(tl, dict):
        rec = {
            "type": "subs",
            "clips": [],
            "transitions": [],
            EVENTS_KEY: [],
        }
        tl.setdefault("tracks", []).append(rec)
        return rec
    if hasattr(tl, "add_track"):
        from cutora.model.timeline_model import Track
        tr = Track("subs")
        tl.add_track(tr)
        return tr
    if hasattr(tl, "tracks"):
        from cutora.model.timeline_model import Track
        tr = Track("subs")
        tl.tracks.append(tr)
        return tr
    return None


def set_segments(task, segs) -> list:
    """Write subtitles to the subs track; keep ``task['segments']`` as the same list."""
    if not isinstance(task, dict):
        return []
    segs = list(segs or [])
    # Q139：写回之前把缺 id / 重复 id 的补齐，任何一条字幕都不许和别的同 id
    try:
        from cutora.subtitle.seg_ids import ensure_segment_ids
        ensure_segment_ids({"segments": segs})
    except Exception:
        pass
    tr = first_subs_track(task)
    if tr is None and segs:
        tr = _ensure_subs_track(task)
    if tr is not None:
        write_events(tr, segs)
    task["segments"] = segs
    return segs


def bind_segments(task) -> list:
    """On load: track wins. If the track is empty, migrate ``task['segments']`` onto it."""
    if not isinstance(task, dict):
        return []
    tr = first_subs_track(task)
    if tr is not None:
        hydrate_track(tr)
    track_segs = _read_segments(task)
    derived = list(task.get("segments") or [])
    if track_segs:
        live = events_of(first_subs_track(task))
        if isinstance(live, list):
            task["segments"] = live
            return [s for s in live if isinstance(s, dict)]
        return track_segs
    if derived:
        return set_segments(task, derived)
    if tr is not None:
        task["segments"] = events_of(tr) if isinstance(events_of(tr), list) else []
    else:
        task.setdefault("segments", [])
    return get_segments(task)


def snapshot_source(task) -> tuple[int, int]:
    return count_task_segments(task), count_track_subs(task)


def log_subtitle_source(path: str, task) -> tuple[int, int]:
    """One line per user path: len(task['segments']) vs subs-track count."""
    n_seg, n_track = snapshot_source(task)
    logger.info(
        "T29 source path=%s segments=%d track_subs=%d", path, n_seg, n_track)
    print(
        "[T29] path=%s segments=%d track_subs=%d" % (path, n_seg, n_track),
        flush=True,
    )
    return n_seg, n_track
