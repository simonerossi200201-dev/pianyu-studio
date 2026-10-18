# -*- coding: utf-8 -*-
"""T22-b: seam join restores project JSON; different source is rejected."""
from __future__ import annotations

import json

import pytest

from cutora.model.project_io import _rj_serialize_timeline
from cutora.model.seams import JoinError, join_clips
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, Track, TransitionItem


def _dump(tl) -> str:
    return json.dumps(_rj_serialize_timeline(tl), ensure_ascii=False, indent=2)


def _video_task(clips, transitions=None, fps=30.0):
    tl = Timeline(1080, 1920, fps)
    tr = Track("video")
    tr.id = "track-video-1"
    for c in clips:
        tr.clips.append(c)
    if transitions:
        tr.transitions.extend(transitions)
    tl.tracks.append(tr)
    return tl, tr


def test_seam_join_restores_json():
    clip = Clip("src-a", 0.0, 4.0, 0.0)
    clip.speed = 1.0
    tl, tr = _video_task([clip])
    before = _dump(tl)

    right = clip.split_at(2.0)
    assert right is not None
    tr.clips.append(right)
    assert _dump(tl) != before
    assert len(tr.clips) == 2

    join_clips(tr, 0, 1, fps=30.0)
    assert _dump(tl) == before
    assert len(tr.clips) == 1
    assert abs(tr.clips[0].in_t - 0.0) < 1e-12
    assert abs(tr.clips[0].out_t - 4.0) < 1e-12
    assert abs(tr.clips[0].start_t - 0.0) < 1e-12


def test_seam_join_rejects_different_source():
    left = Clip("src-a", 0.0, 2.0, 0.0)
    right = Clip("src-b", 2.0, 4.0, 2.0)
    left.speed = right.speed = 1.0
    tl, tr = _video_task([left, right])
    before = _dump(tl)

    with pytest.raises(JoinError, match="同一素材|同一 source|不同"):
        join_clips(tr, 0, 1, fps=30.0)

    assert _dump(tl) == before
    assert len(tr.clips) == 2
    assert tr.clips[0] is left and tr.clips[1] is right


def test_seam_join_rejects_transition():
    left = Clip("src-a", 0.0, 2.0, 0.0)
    right = Clip("src-a", 2.0, 4.0, 2.0)
    trans = TransitionItem(start_t=1.825, duration=0.35, preset="cross_dissolve")
    tl, tr = _video_task([left, right], transitions=[trans])
    before = _dump(tl)

    with pytest.raises(JoinError, match="转场"):
        join_clips(tr, 0, 1, fps=30.0)

    assert _dump(tl) == before
