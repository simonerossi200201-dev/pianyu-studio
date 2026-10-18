# -*- coding: utf-8 -*-
"""T05c magnet snap: px threshold, skip-self, overlap/MIN_DUR reject, zoom cap."""
from __future__ import annotations

from cutora.ui.timeline.snap import (
    MIN_DUR, SNAP_MAX_SEC, SNAP_PX, collect_edges, marker_times,
    move_valid, nearest, snap_move, snap_trim, threshold_sec, trim_valid,
)


class _Clip:
    def __init__(self, start, end):
        self.start_t = start
        self.in_t = 0.0
        self.out_t = end - start
        self.speed = 1.0

    @property
    def effective_duration(self):
        return self.out_t - self.in_t


def test_threshold_caps_at_half_second():
    # 8 px at 10 px/s = 0.8 s, capped to 0.5 s
    assert abs(threshold_sec(10.0) - SNAP_MAX_SEC) < 1e-9
    # 8 px at 40 px/s = 0.2 s, under the cap
    assert abs(threshold_sec(40.0) - (SNAP_PX / 40.0)) < 1e-9


def test_nearest_picks_closest():
    edges = [
        {"t": 1.0, "kind": "a", "key": ("a",)},
        {"t": 1.04, "kind": "b", "key": ("b",)},
        {"t": 2.0, "kind": "c", "key": ("c",)},
    ]
    hit = nearest(1.03, edges, 0.1)
    assert hit["kind"] == "b"


def test_skip_self_edges():
    clips = [(_Clip(0.0, 2.0),), ]
    # use collect: one track two clips
    edges = collect_edges(
        clips_by_track=[("t1", [_Clip(0.0, 2.0), _Clip(5.0, 6.0)])],
        segments=[{"start": 1.0, "end": 3.0}],
        playhead_s=4.0,
        skip={("clip", "t1", 0)},
    )
    kinds = {(e["kind"], e["key"][:3] if e["kind"].startswith("clip") else e["kind"])
             for e in edges}
    assert ("clip_start", ("clip", "t1", 0)) not in {
        (e["kind"], e["key"][:3]) for e in edges if e["kind"].startswith("clip")
    }
    assert any(e["kind"] == "playhead" for e in edges)
    assert any(e["kind"] == "zero" for e in edges)
    assert any(e["key"] == ("clip", "t1", 1, "start") for e in edges)
    assert not any(e["key"][:3] == ("clip", "t1", 0) for e in edges)
    _ = kinds


def test_snap_move_rejects_overlap():
    neighbors = [(2.0, 4.0)]
    edges = [{"t": 2.5, "kind": "x", "key": ("x",)}]
    start, hit = snap_move(0.0, 1.5, edges, 1.0, neighbors)
    assert hit is None
    assert abs(start - 0.0) < 1e-9


def test_snap_move_allows_abut():
    neighbors = [(2.0, 4.0)]
    edges = [{"t": 2.0, "kind": "n", "key": ("n",)}]
    start, hit = snap_move(0.05, 2.05, edges, 0.2, neighbors)
    assert hit is not None
    assert abs(start - 0.0) < 1e-9 or abs(start + 2.0 - 2.0) < 1e-6
    # moving [0.05, 2.05] end snaps to 2.0 → start = 0.0, abuts neighbor
    assert abs(start - 0.0) < 1e-9
    assert move_valid(start, 2.0, neighbors)


def test_snap_trim_rejects_below_min_dur():
    edges = [{"t": 1.02, "kind": "x", "key": ("x",)}]
    proposed, hit = snap_trim(1.5, 1.08, True, edges, 0.6, neighbors=[])
    assert hit is None
    assert abs(proposed - 1.5) < 1e-9
    assert not trim_valid(1.02, 1.08, [], min_dur=MIN_DUR)


def test_marker_hook_empty_and_present():
    assert marker_times({}) == []
    assert marker_times(None) == []
    assert marker_times({"markers": [1.5, {"time": 3.0}]}) == [1.5, 3.0]
    assert marker_times({"markers": [{"t": 12.34, "id": "x"}]}) == [12.34]


def test_cross_track_edges_are_targets():
    edges = collect_edges(
        clips_by_track=[
            ("video", [_Clip(0.0, 1.0)]),
            ("audio", [_Clip(2.0, 3.0)]),
        ],
        skip={("clip", "video", 0)},
    )
    assert any(e["key"] == ("clip", "audio", 0, "start") for e in edges)
    hit = nearest(1.98, edges, 0.1)
    assert hit["key"] == ("clip", "audio", 0, "start")
