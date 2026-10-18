# -*- coding: utf-8 -*-
"""BUG-09: razor goes through the command bus; overlay.split exists."""
from __future__ import annotations

import json
import os

from cutora.core.commands import CommandError, Context, get_registry
from cutora.core.cmds.light import cmd_overlay_split
from cutora.model.timeline_model import Clip, Track
from cutora.model.timeline import Timeline
from cutora.ui.timeline.tools import razor_selected, split_overlay


def test_overlay_split_in_registry_not_portable():
    spec = get_registry().get("overlay.split")
    assert spec.undoable is True
    assert spec.portable is False
    rz = get_registry().get("timeline.razor")
    assert rz.undoable is True
    assert rz.portable is False


def test_overlay_split_mutates_and_lock_leaves_json():
    tl = Timeline(1080, 1920, 30)
    v = Track("video")
    v.clips.append(Clip("s", 0, 4, 0))
    tl.tracks.append(v)
    task = {
        "timeline": tl,
        "text_overlays": [{"start": 0.0, "end": 4.0, "content": "hi", "type": "text"}],
        "image_overlays": [{"start": 0.0, "end": 4.0, "type": "image", "elements": []}],
        "segments": [],
    }
    class W:
        tasks = [task]
        curr_idx = 0
    ctx = Context(window=W(), source="ui")
    cmd_overlay_split(ctx, {"kind": "text", "idx": 0, "t": 2.0, "media_id": 0})
    assert len(task["text_overlays"]) == 2
    assert abs(task["text_overlays"][0]["end"] - 2.0) < 1e-9

    v.locked = True
    before = json.dumps(task["image_overlays"], sort_keys=True)
    try:
        cmd_overlay_split(ctx, {"kind": "image", "idx": 0, "t": 1.0, "media_id": 0})
        raised = False
    except CommandError as exc:
        raised = True
        assert "锁定" in str(exc)
    assert raised
    assert json.dumps(task["image_overlays"], sort_keys=True) == before


def test_widget_razor_uses_command_not_razor_selected():
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "ui", "timeline", "widget.py"),
        encoding="utf-8",
    ).read()
    assert '_bus_execute("timeline.razor"' in src
    assert "razor_selected(" not in src.split("def razor_at_playhead")[1][:800]


def test_five_kinds_still_split():
    v = Clip("s", 0, 4, 0)
    a = Clip("s", 0, 4, 0)
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
    right = split_overlay({"start": 0.0, "end": 2.0}, 1.0)
    assert right is not None
