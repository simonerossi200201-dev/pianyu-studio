# -*- coding: utf-8 -*-
"""T05a-2: segment trim/move/retime/delete keep words in the command."""
from __future__ import annotations

from cutora.subtitle.edit import (
    apply_move, apply_retime, apply_trim,
    delete_segments_task, move_segments_task,
    retime_segments_task, trim_segment_task,
)


def _seg():
    return {
        "start": 1.0, "end": 3.0, "text": "aa bb",
        "words": [
            {"word": "aa", "start": 1.0, "end": 2.0},
            {"word": "bb", "start": 2.0, "end": 3.0},
        ],
    }


def test_apply_trim_maps_words_linearly():
    seg = _seg()
    apply_trim(seg, 1.0, 5.0)
    assert abs(seg["end"] - 5.0) < 1e-9
    assert abs(seg["words"][0]["end"] - 3.0) < 1e-9
    assert abs(seg["words"][1]["end"] - 5.0) < 1e-9


def test_apply_move_shifts_words():
    seg = _seg()
    apply_move(seg, 3.0)
    assert abs(seg["start"] - 3.0) < 1e-9
    assert abs(seg["end"] - 5.0) < 1e-9
    assert abs(seg["words"][0]["start"] - 3.0) < 1e-9
    assert abs(seg["words"][1]["end"] - 5.0) < 1e-9


def test_apply_retime_scales_around_pivot():
    seg = _seg()
    apply_retime(seg, pivot=1.0, scale=2.0)
    assert abs(seg["start"] - 1.0) < 1e-9
    assert abs(seg["end"] - 5.0) < 1e-9
    assert abs(seg["words"][1]["start"] - 3.0) < 1e-9
    assert abs(seg["words"][1]["end"] - 5.0) < 1e-9


def test_task_wrappers_and_delete_order():
    task = {"segments": [_seg(), {
        "start": 4.0, "end": 5.0, "text": "cc",
        "words": [{"word": "cc", "start": 4.0, "end": 5.0}],
    }]}
    trim_segment_task(task, 0, 1.0, 2.5)
    assert abs(task["segments"][0]["end"] - 2.5) < 1e-9
    move_segments_task(task, [{"seg_id": 1, "start": 6.0}])
    assert abs(task["segments"][1]["start"] - 6.0) < 1e-9
    retime_segments_task(task, [1], pivot=6.0, scale=2.0)
    assert abs(task["segments"][1]["end"] - 8.0) < 1e-9
    delete_segments_task(task, [1, 0])
    assert task["segments"] == []
