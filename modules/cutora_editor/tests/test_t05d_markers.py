# -*- coding: utf-8 -*-
"""T05d markers: uuid model, old projects, snap hook."""
from __future__ import annotations

from cutora.model.markers import (
    add_marker, markers_of, remove_markers, rename_marker, move_marker,
    set_marker_color,
)
from cutora.ui.timeline.snap import collect_edges, nearest, marker_times


def test_missing_markers_is_empty_list():
    task = {"segments": []}
    assert markers_of(task) == []
    assert task["markers"] == []


def test_add_rename_move_color_remove():
    task = {}
    rec = add_marker(task, 1.25, name="副歌", color="#F5B700")
    assert rec["id"] and len(rec["id"]) >= 16
    assert abs(rec["t"] - 1.25) < 1e-9
    rename_marker(task, rec["id"], "间奏")
    move_marker(task, rec["id"], 4.0)
    set_marker_color(task, rec["id"], "#112233")
    found = markers_of(task)[0]
    assert found["name"] == "间奏"
    assert abs(found["t"] - 4.0) < 1e-9
    assert found["color"] == "#112233"
    remove_markers(task, [rec["id"]])
    assert markers_of(task) == []


def test_snap_collects_marker_t():
    task = {}
    rec = add_marker(task, 12.34, name="hook")
    times = marker_times(task)
    assert 12.34 in times
    edges = collect_edges(
        clips_by_track=[],
        segments=[],
        playhead_s=0.0,
        markers=times,
        skip=set(),
    )
    hit = nearest(12.30, edges, 0.1)
    assert hit is not None
    assert abs(hit["t"] - 12.34) < 1e-9
    assert hit["kind"] == "marker"
    _ = rec


def test_marker_commands_are_not_portable():
    from cutora.core.commands import get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    reg = get_registry()
    for name in (
        "marker.add", "marker.remove", "marker.rename",
        "marker.move", "marker.set_color",
    ):
        assert reg.get(name).portable is False, name
    reset_bus_for_tests()
