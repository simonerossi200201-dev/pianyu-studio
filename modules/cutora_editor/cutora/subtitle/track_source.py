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
    if isinstance(extra, dict):
        if "source_track_id" in extra and not getattr(tr, "source_track_id", None):
            tr.source_track_id = str(extra.get("source_track_id") or "__main__")
        if "manual_script" in extra and getattr(tr, "manual_script", None) is None:
            tr.manual_script = str(extra.get("manual_script") or "")
    ev = getattr(tr, EVENTS_KEY, None)
    if ev is None and isinstance(extra, dict):
        ev = extra.get(EVENTS_KEY)
    if ev is None:
        return
    write_events(tr, ev if isinstance(ev, list) else [])


def get_track_source_id(tr) -> str:
    """Return the target track id for subtitle transcription ('__main__' = main video)."""
    if tr is None:
        return "__main__"
    if isinstance(tr, dict):
        return str(tr.get("source_track_id") or "__main__")
    val = getattr(tr, "source_track_id", None)
    if not val:
        extra = getattr(tr, "_extra", None)
        if isinstance(extra, dict):
            val = extra.get("source_track_id")
    return str(val or "__main__")


def set_track_source_id(tr, source_id: str) -> None:
    """Persist target track id on the subtitle track (attribute + _extra)."""
    sid = str(source_id or "__main__")
    if tr is None:
        return
    if isinstance(tr, dict):
        tr["source_track_id"] = sid
        return
    tr.source_track_id = sid
    extra = getattr(tr, "_extra", None)
    if not isinstance(extra, dict):
        extra = {}
        tr._extra = extra
    extra["source_track_id"] = sid


def get_track_manual_script(tr) -> str:
    """Return user-pasted manual subtitle script stored on this subtitle track."""
    if tr is None:
        return ""
    if isinstance(tr, dict):
        return str(tr.get("manual_script") or "")
    val = getattr(tr, "manual_script", None)
    if val is None:
        extra = getattr(tr, "_extra", None)
        if isinstance(extra, dict):
            val = extra.get("manual_script")
    return str(val or "")


def set_track_manual_script(tr, script: str) -> None:
    """Persist user-pasted manual subtitle script on this subtitle track."""
    txt = str(script or "")
    if tr is None:
        return
    if isinstance(tr, dict):
        tr["manual_script"] = txt
        return
    tr.manual_script = txt
    extra = getattr(tr, "_extra", None)
    if not isinstance(extra, dict):
        extra = {}
        tr._extra = extra
    extra["manual_script"] = txt


def get_track_segments(task, tr) -> list:
    """Return the subtitle segments belonging to a specific subs track ``tr``."""
    if tr is None:
        return []
    ev = events_of(tr)
    if isinstance(ev, list):
        return ev
    if isinstance(task, dict) and tr is first_subs_track(task):
        tid = str(getattr(tr, "id", "") or "")
        return [
            s for s in (task.get("segments") or [])
            if isinstance(s, dict) and str(s.get("_track_id") or "") in ("", tid)
        ]
    return []


def set_track_segments(task, tr, segs) -> list:
    """Write ``segs`` to a specific subs track ``tr`` and sync ``task['segments']``."""
    segs = [dict(s) if isinstance(s, dict) else s for s in (segs or []) if isinstance(s, dict)]
    tid = str(getattr(tr, "id", "") or "") if tr is not None else ""
    subs_tracks = list(iter_subs_tracks(task)) if isinstance(task, dict) else []
    if tid and len(subs_tracks) > 1:
        for s in segs:
            s["_track_id"] = tid
    elif tid:
        for s in segs:
            s.setdefault("_track_id", tid)
    try:
        from cutora.subtitle.seg_ids import ensure_segment_ids
        ensure_segment_ids({"segments": segs})
    except Exception:
        pass
    if tr is not None:
        write_events(tr, segs)
    if not isinstance(task, dict):
        return segs
    if len(subs_tracks) <= 1:
        task["segments"] = segs
        return segs
    # Ensure all subs tracks have hydrated event lists before merging
    for other in subs_tracks:
        if other is not tr and events_of(other) is None:
            other_id = str(getattr(other, "id", "") or "")
            existing = [
                s for s in (task.get("segments") or [])
                if isinstance(s, dict) and str(s.get("_track_id") or "") in (other_id, "" if other is subs_tracks[0] else other_id)
            ]
            write_events(other, existing)
    combined = _read_segments(task)
    combined.sort(key=lambda s: float(s.get("start") or 0.0))
    try:
        from cutora.subtitle.seg_ids import ensure_segment_ids
        ensure_segment_ids({"segments": combined})
    except Exception:
        pass
    if isinstance(task.get("segments"), list):
        task["segments"][:] = combined
    else:
        task["segments"] = combined
    return segs


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
    subs_tracks = list(iter_subs_tracks(task))
    if len(subs_tracks) > 1:
        by_id = {str(getattr(t, "id", "") or ""): t for t in subs_tracks}
        buckets = {id(t): [] for t in subs_tracks}
        first_t = subs_tracks[0]
        for s in segs:
            if not isinstance(s, dict):
                continue
            tid = str(s.get("_track_id") or "")
            target_t = by_id.get(tid, first_t)
            buckets[id(target_t)].append(s)
        for t in subs_tracks:
            write_events(t, buckets[id(t)])
        task["segments"] = segs
        return segs
    tr = subs_tracks[0] if subs_tracks else None
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
    subs_tracks = list(iter_subs_tracks(task))
    for t in subs_tracks:
        hydrate_track(t)
    tr = subs_tracks[0] if subs_tracks else None
    track_segs = _read_segments(task)
    derived = list(task.get("segments") or [])
    if track_segs:
        if len(subs_tracks) > 1:
            task["segments"] = list(track_segs)
            return task["segments"]
        live = events_of(tr)
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
