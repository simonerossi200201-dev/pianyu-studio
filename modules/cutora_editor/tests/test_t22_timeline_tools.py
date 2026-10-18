# -*- coding: utf-8 -*-
"""T22: razor on five clip kinds; edge drag trims out_t, speed stays."""
from __future__ import annotations

from cutora.model.timeline_model import Clip, Track
from cutora.ui.timeline.tools import (
    razor_selected, split_overlay, split_subtitle_at, trim_clip_edge,
)
from cutora.ui.timeline.thumbs import MAX_THUMB_CACHE_BYTES, thumb_key
from cutora.audio.waveform import MAX_WAVEFORM_CACHE_BYTES


def _clip(in_t=0.0, out_t=4.0, start_t=0.0, speed=1.0):
    c = Clip("src", in_t, out_t, start_t)
    c.speed = speed
    return c


def test_t22_trim_right_keeps_speed_changes_out_t():
    c = _clip(0.0, 10.0, 0.0, speed=1.0)
    trim_clip_edge(c, "right", 6.0, speed_mode=False)
    assert abs(c.speed - 1.0) < 1e-9
    assert abs(c.out_t - 6.0) < 1e-6


def test_t22_speed_tool_changes_speed():
    c = _clip(0.0, 10.0, 0.0, speed=1.0)
    trim_clip_edge(c, "right", 5.0, speed_mode=True)
    assert abs(c.out_t - 10.0) < 1e-6
    assert abs(c.speed - 2.0) < 1e-6


def test_t22_razor_five_kinds():
    v = _clip(0.0, 4.0, 0.0)
    a = _clip(0.0, 4.0, 0.0)
    vt, at = Track("video"), Track("audio")
    vt.clips.append(v)
    at.clips.append(a)
    task = {
        "segments": [{"start": 0.0, "end": 4.0, "text": "hello world", "words": [
            {"word": "hello", "start": 0.0, "end": 2.0},
            {"word": "world", "start": 2.0, "end": 4.0},
        ]}],
        "text_overlays": [{"start": 0.0, "end": 4.0, "content": "hi", "type": "text"}],
        "image_overlays": [{"start": 0.0, "end": 4.0, "type": "image", "elements": []}],
    }
    n = razor_selected(task, 2.0, [
        {"kind": "video", "clip": v, "track": vt},
        {"kind": "audio", "clip": a, "track": at},
        {"kind": "text", "idx": 0},
        {"kind": "image", "idx": 0},
        {"kind": "subtitle", "idx": 0},
    ])
    assert n == 5
    assert len(vt.clips) == 2 and len(at.clips) == 2
    assert abs(v.out_t - 2.0) < 1e-6
    assert abs(vt.clips[1].in_t - 2.0) < 1e-6
    assert len(task["text_overlays"]) == 2
    assert len(task["image_overlays"]) == 2
    assert len(task["segments"]) == 2


def test_t22_split_overlay_and_subtitle():
    ov = {"start": 1.0, "end": 5.0, "content": "x"}
    right = split_overlay(ov, 3.0)
    assert right is not None
    assert abs(ov["end"] - 3.0) < 1e-9
    assert abs(right["start"] - 3.0) < 1e-9
    task = {"segments": [{"start": 0.0, "end": 2.0, "text": "ab", "words": [
        {"word": "a", "start": 0.0, "end": 1.0},
        {"word": "b", "start": 1.0, "end": 2.0},
    ]}]}
    assert split_subtitle_at(task, 0, 1.0)
    assert len(task["segments"]) == 2


def test_t22_thumb_cache_cap_matches_waveform():
    assert MAX_THUMB_CACHE_BYTES == MAX_WAVEFORM_CACHE_BYTES
    assert MAX_THUMB_CACHE_BYTES == 512 * 1024 * 1024
    assert thumb_key(__file__, 0.5, 80, 45)


def test_t22_toolbar_in_container_source():
    import os
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "ui", "timeline", "container.py"),
        encoding="utf-8",
    ).read()
    assert "切刀" in src and "变速" in src and "磁铁" in src
    wsrc = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "ui", "timeline", "widget.py"),
        encoding="utf-8",
    ).read()
    assert "trim_clip_edge" in wsrc
    assert "TOOL_SPEED" in wsrc
    assert "razor_at_playhead" in wsrc
