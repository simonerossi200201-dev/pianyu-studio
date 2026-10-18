# -*- coding: utf-8 -*-
from cutora.ui.jkl import next_rate, rate_label
from cutora.ui.timeline.snap import collect_edges, nearest


def test_jkl_ladder():
    assert next_rate(0, True) == 1
    assert next_rate(1, True) == 2
    assert next_rate(2, True) == 4
    assert next_rate(4, True) == 8
    assert next_rate(8, True) == 8
    assert next_rate(0, False) == -1
    assert next_rate(-1, False) == -2
    assert next_rate(-2, False) == -4
    assert next_rate(-8, False) == -8
    assert next_rate(4, False) == -1
    assert next_rate(-2, True) == 1
    assert "−" in rate_label(-2)
    assert rate_label(0) == "停止"


def test_io_points_are_snap_targets():
    edges = collect_edges(in_point=1.5, out_point=8.0, playhead_s=0.0)
    kinds = {e["kind"] for e in edges}
    assert "in_point" in kinds and "out_point" in kinds
    hit = nearest(1.52, edges, 0.1)
    assert hit["kind"] == "in_point"


def test_old_task_io_missing_is_none():
    task = {"segments": []}
    assert task.get("in_point") is None
    assert task.get("out_point") is None
