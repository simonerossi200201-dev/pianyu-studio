# -*- coding: utf-8 -*-
"""T05e ripple invariants, spanning cases, gap close."""
from __future__ import annotations

import copy

from cutora.model.ripple import (
    MIN_DUR, apply_delete_range, apply_insert, close_gaps, ripple_delete_targets,
)
from cutora.ui.timeline.snap import clip_span


class _Clip:
    def __init__(self, start, end, link=None):
        self.start_t = float(start)
        self.in_t = 0.0
        self.out_t = float(end) - float(start)
        self.speed = 1.0
        self.link_group_id = link
        self.source_id = "s"

    @property
    def effective_duration(self):
        return (self.out_t - self.in_t) / max(0.01, self.speed)

    def clone(self):
        return copy.deepcopy(self)


class _Track:
    def __init__(self, tid, clips=None):
        self.id = tid
        self.type = "video"
        self.clips = list(clips or [])
        self.transitions = []


class _Tl:
    def __init__(self, tracks):
        self.tracks = tracks


def _task(clips, segs, markers=None, tid="v"):
    tr = _Track(tid, clips)
    return {"timeline": _Tl([tr]), "segments": segs, "markers": list(markers or [])}


def test_spanning_delete_three_shapes():
    # delete [10, 15)
    segs = [
        {"start": 8.0, "end": 12.0, "text": "L",
         "words": [{"word": "a", "start": 8.0, "end": 10.0},
                   {"word": "b", "start": 10.0, "end": 12.0}]},
        {"start": 13.0, "end": 18.0, "text": "R",
         "words": [{"word": "c", "start": 13.0, "end": 16.0},
                   {"word": "d", "start": 16.0, "end": 18.0}]},
        {"start": 9.0, "end": 17.0, "text": "T",
         "words": [{"word": "e", "start": 9.0, "end": 11.0},
                   {"word": "f", "start": 15.0, "end": 17.0}]},
    ]
    task = _task([_Clip(0, 8), _Clip(10, 15), _Clip(16, 20)], segs,
                 markers=[{"id": "m1", "t": 8.0}, {"id": "m2", "t": 12.0},
                          {"id": "m3", "t": 18.0}])
    apply_delete_range(task, 10.0, 15.0, "v")
    for seg in task["segments"]:
        assert float(seg["end"]) >= float(seg["start"])
        assert float(seg["end"]) - float(seg["start"]) >= MIN_DUR - 1e-9
        for w in seg.get("words") or []:
            assert float(seg["start"]) - 1e-9 <= float(w["start"])
            assert float(w["end"]) <= float(seg["end"]) + 1e-9
    # After collapse, nothing still occupies the deleted hole as a
    # pre-shift remainder: surviving pieces sit at/before t0 or in the
    # shifted tail (old t1+). The three spanning shapes are checked below.
    left = [s for s in task["segments"] if s["text"] == "L"][0]
    assert abs(left["end"] - 10.0) < 1e-6
    right = [s for s in task["segments"] if s["text"] == "R"][0]
    assert abs(right["start"] - 10.0) < 1e-6
    assert abs(right["end"] - 13.0) < 1e-6
    thru = [s for s in task["segments"] if s["text"] == "T"][0]
    assert abs(thru["start"] - 9.0) < 1e-6
    assert abs(thru["end"] - 12.0) < 1e-6
    mk = {m["id"]: m["t"] for m in task["markers"]}
    assert abs(mk["m1"] - 8.0) < 1e-6
    assert "m2" not in mk
    assert abs(mk["m3"] - 13.0) < 1e-6


def test_insert_does_not_change_durations():
    clips = [_Clip(0, 5), _Clip(8, 12)]
    segs = [
        {"start": 1.0, "end": 3.0, "text": "a", "words": [
            {"word": "a", "start": 1.0, "end": 3.0}]},
        {"start": 6.0, "end": 10.0, "text": "cross", "words": [
            {"word": "x", "start": 6.0, "end": 10.0}]},
        {"start": 11.0, "end": 12.0, "text": "b", "words": [
            {"word": "b", "start": 11.0, "end": 12.0}]},
    ]
    before = [(s["text"], s["end"] - s["start"]) for s in segs]
    task = _task(clips, copy.deepcopy(segs))
    out = apply_insert(task, 7.0, 2.0, "v")
    after = [(s["text"], s["end"] - s["start"]) for s in task["segments"]]
    assert after == before
    cross = [s for s in task["segments"] if s["text"] == "cross"][0]
    assert abs(cross["start"] - 8.0) < 1e-6
    assert "cross" in "".join(out["nudged"]) or out["nudged"]
    late = [s for s in task["segments"] if s["text"] == "b"][0]
    assert abs(late["start"] - 13.0) < 1e-6
    early = [s for s in task["segments"] if s["text"] == "a"][0]
    assert abs(early["start"] - 1.0) < 1e-6


def test_relative_spacing_and_total_duration():
    c0, c1, c2, c3 = _Clip(0, 4), _Clip(5, 8), _Clip(10, 12), _Clip(14, 16)
    gap23 = 14.0 - 12.0
    task = _task([c0, c1, c2, c3], [])
    apply_delete_range(task, 5.0, 8.0, "v")
    behind = sorted(
        [c for c in task["timeline"].tracks[0].clips if c.start_t >= 4.5],
        key=lambda c: c.start_t)
    assert len(behind) == 2
    assert abs(clip_span(behind[1])[0] - clip_span(behind[0])[1] - gap23) < 1e-6
    total = sum(clip_span(c)[1] - clip_span(c)[0] for c in task["timeline"].tracks[0].clips)
    assert abs(total - 8.0) < 1e-6  # 4+2+2, deleted 3s clip


def test_close_gaps_no_internal_gap():
    task = _task([_Clip(0, 2), _Clip(5, 7), _Clip(10, 11)], [])
    close_gaps(task, "v")
    clips = sorted(task["timeline"].tracks[0].clips, key=lambda c: c.start_t)
    for a, b in zip(clips, clips[1:]):
        assert abs(clip_span(b)[0] - clip_span(a)[1]) < 1e-6


def test_ripple_delete_targets_atomic_seg_and_clip():
    task = _task(
        [_Clip(0, 5), _Clip(5, 10)],
        [{"start": 6.0, "end": 8.0, "text": "x", "words": [
            {"word": "x", "start": 6.0, "end": 8.0}]}],
    )
    ripple_delete_targets(task, [{"kind": "clip", "track_id": "v", "clip_index": 1}])
    assert len(task["timeline"].tracks[0].clips) == 1
    assert task["segments"] == []


def test_intro_shorten_shifts_main_and_subs():
    intro = _Clip(0, 5)
    intro.is_intro_video_leadin = True
    main = _Clip(5, 15)
    segs = [{"start": 6.0, "end": 8.0, "text": "s", "words": [
        {"word": "s", "start": 6.0, "end": 8.0}]}]
    task = _task([intro, main], segs)
    intro.out_t = 3.0
    apply_delete_range(task, 3.0, 5.0, "v", skip_clips=[intro])
    assert abs(clip_span(intro)[1] - 3.0) < 1e-6
    mains = [c for c in task["timeline"].tracks[0].clips if c is not intro]
    assert abs(mains[0].start_t - 3.0) < 1e-6
    assert abs(task["segments"][0]["start"] - 4.0) < 1e-6


def test_marker_commands_false_portable_ripple():
    from cutora.core.commands import get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    reg = get_registry()
    for name in (
        "timeline.ripple_delete", "timeline.ripple_insert", "timeline.close_gaps",
    ):
        assert reg.get(name).portable is False, name
    reset_bus_for_tests()
