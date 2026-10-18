# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Track mute / solo / locked helpers. Locked is enforced at the command layer.

Guard raises ValueError. Command bus / _mutate_edit wrap it as CommandError.
"""
from __future__ import annotations


def iter_tracks(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    return list(getattr(tl, "tracks", []) or []) if tl is not None else []


def find_track(task, track_id=None, track_type=None):
    """Exactly one of track_id / track_type. Id is a full pass, never mixed with type."""
    has_id = track_id is not None and str(track_id) != ""
    has_type = track_type is not None
    if has_id and has_type:
        raise ValueError("find_track: track_id 与 track_type 只能传一个")
    if not has_id and not has_type:
        raise ValueError("find_track: 需要 track_id 或 track_type")
    tracks = iter_tracks(task)
    if has_id:
        want = str(track_id)
        for tr in tracks:
            if str(getattr(tr, "id", "") or "") == want:
                return tr
        return None
    for tr in tracks:
        if getattr(tr, "type", None) == track_type:
            return tr
    return None


def track_label(tr) -> str:
    return str(getattr(tr, "type", None) or getattr(tr, "id", "") or "轨道")


def assert_unlocked(task, track_id=None, track=None, track_type=None):
    """Raise if the named track is locked or a selector does not resolve.

    Passing no selector at all is a no-op (that call should not exist).
    """
    has_obj = track is not None
    has_id = track_id is not None and str(track_id) != ""
    has_type = track_type is not None
    n = int(has_obj) + int(has_id) + int(has_type)
    if n == 0:
        return
    if n > 1 and not has_obj:
        raise ValueError("assert_unlocked: track_id 与 track_type 只能传一个")
    tr = track
    if tr is None:
        tr = find_track(task, track_id=track_id, track_type=track_type)
    if tr is None:
        raise ValueError("找不到轨道 %s" % (track_id or track_type or ""))
    if getattr(tr, "locked", False):
        raise ValueError("轨道已锁定：%s" % track_label(tr))


def iter_tracks_of_type(task, track_type):
    return [tr for tr in iter_tracks(task) if getattr(tr, "type", None) == track_type]


def assert_type_unlocked(task, track_type):
    """Every track of this type must be unlocked. Missing type is a no-op."""
    for tr in iter_tracks_of_type(task, track_type):
        assert_unlocked(task, track=tr)


def assert_ripple_unlocked(task, track_id=None):
    """Ripple also shifts linked clips and subtitles. Any of those locked → reject.

    Link key matches T05e: link_group_id first, source_id as fallback.
    """
    if track_id is None or str(track_id) == "":
        raise ValueError("缺少 track_id")
    tr = find_track(task, track_id=track_id)
    if tr is None:
        raise ValueError("找不到轨道 %s" % track_id)
    assert_unlocked(task, track=tr)
    gids = set()
    sids = set()
    for clip in getattr(tr, "clips", []) or []:
        gid = getattr(clip, "link_group_id", None)
        if gid:
            gids.add(gid)
        sid = getattr(clip, "source_id", None)
        if sid:
            sids.add(sid)
    for other in iter_tracks(task):
        if other is tr:
            continue
        for clip in getattr(other, "clips", []) or []:
            gid = getattr(clip, "link_group_id", None)
            sid = getattr(clip, "source_id", None)
            if (gid and gid in gids) or (sid and sid in sids):
                assert_unlocked(task, track=other)
                break
    assert_type_unlocked(task, "subs")


def track_is_muted(track, task=None, solo_ids=None) -> bool:
    if getattr(track, "mute", False):
        return True
    if not solo_ids:
        return False
    domain = getattr(track, "domain", "visual")
    solos = set()
    for tr in iter_tracks(task) if task is not None else [track]:
        tid = str(getattr(tr, "id", ""))
        if tid in solo_ids and getattr(tr, "domain", "visual") == domain:
            solos.add(tid)
    if not solos:
        return False
    return str(getattr(track, "id", "")) not in solos


def track_outputs(track, task=None, solo_ids=None) -> bool:
    """Preview/export: visible and not muted (solo only for preview)."""
    if not getattr(track, "visible", True):
        return False
    return not track_is_muted(track, task=task, solo_ids=solo_ids)


def video_tracks_for_render(task, *, solo_ids=None):
    """Video tracks that preview and export both paint.

    Same rule in canvas, PreviewEngine, and RenderWorker: type=video,
    visible, not muted, and at least one clip. solo_ids is preview-only.
    """
    out = []
    for tr in iter_tracks(task):
        if getattr(tr, "type", None) != "video":
            continue
        if not track_outputs(tr, task=task, solo_ids=solo_ids):
            continue
        if not (getattr(tr, "clips", []) or []):
            continue
        out.append(tr)
    return out


def compute_audio_tracks(task_data, solo_ids=None):
    """Clips that should reach the mixer, in timeline order.

    Routing is clip.audio_role on Track(domain=audio). solo_ids is
    preview-only; export callers must use compute_export_audio_tracks.
    """
    out = []
    for tr in iter_tracks(task_data):
        if getattr(tr, "domain", "visual") != "audio":
            continue
        if not track_outputs(tr, task=task_data, solo_ids=solo_ids):
            continue
        for clip in getattr(tr, "clips", []) or []:
            if getattr(clip, "hidden", False):   # Q152-G：图层面板里静音的那一层
                continue
            out.append(clip)
    return out


def compute_export_audio_tracks(task_data):
    """Export path. No solo_ids argument — session solo cannot reach the file."""
    return compute_audio_tracks(task_data, solo_ids=None)
