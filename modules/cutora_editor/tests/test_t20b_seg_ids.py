# -*- coding: utf-8 -*-
"""T20b: segment uuid. Index addressing is migration-only."""
from __future__ import annotations

import copy
import json
import os

from cutora.subtitle.seg_ids import (
    ensure_segment_ids, looks_like_uuid, new_seg_id, resolve_seg_index,
)


def test_ensure_assigns_and_keeps():
    task = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 1.0, "end": 2.0, "text": "b", "id": "already-fixed-id-0000"},
    ]}
    n = ensure_segment_ids(task)
    assert n == 1
    assert looks_like_uuid(task["segments"][0]["id"])
    assert task["segments"][1]["id"] == "already-fixed-id-0000"
    assert ensure_segment_ids(task) == 0


def test_resolve_uuid_and_index():
    a, b = new_seg_id(), new_seg_id()
    task = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "a", "id": a},
        {"start": 1.0, "end": 2.0, "text": "b", "id": b},
    ]}
    assert resolve_seg_index(task, a) == 0
    assert resolve_seg_index(task, b) == 1
    assert resolve_seg_index(task, 0) == 0
    assert resolve_seg_index(task, "1") == 1
    try:
        resolve_seg_index(task, "no-such-id-00000000-0000-0000-0000-000000000000")
        assert False
    except ValueError:
        pass


def test_open_save_open_times_and_order_stable(tmp_path):
    from cutora.model.project_io import (
        deserialize_render_task, serialize_render_task,
    )
    segs = [
        {"start": 0.1, "end": 1.2, "text": "Hello friends.", "role": "default",
         "words": [{"word": "Hello", "start": 0.1, "end": 0.5}]},
        {"start": 1.3, "end": 2.4, "text": "Welcome.", "role": "default"},
    ]
    raw = {
        "video": __file__,
        "text": "Hello friends. Welcome.",
        "segments": copy.deepcopy(segs),
        "timeline": {"width": 1080, "height": 1920, "fps": 30.0,
                     "sources": {}, "tracks": []},
        "style": {},
    }
    first = deserialize_render_task(copy.deepcopy(raw))
    from cutora.subtitle.seg_ids import ensure_segment_ids
    ensure_segment_ids(first)
    assert all(looks_like_uuid(s.get("id")) for s in first["segments"])
    dumped = serialize_render_task(first)
    dest = tmp_path / "p.asproj.json"
    dest.write_text(json.dumps({"version": 2, "tasks": [dumped]}, ensure_ascii=False),
                    encoding="utf-8")
    data = json.loads(dest.read_text(encoding="utf-8"))
    second = deserialize_render_task(data["tasks"][0])
    for a, b in zip(first["segments"], second["segments"]):
        assert a["start"] == b["start"] and a["end"] == b["end"]
        assert a["text"] == b["text"]
        assert a["id"] == b["id"]
    assert [s["text"] for s in second["segments"]] == ["Hello friends.", "Welcome."]


def test_set_text_accepts_uuid():
    from cutora.subtitle.edit import set_text_task
    from cutora.subtitle.seg_ids import ensure_segment_ids, resolve_seg_index
    task = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "old"},
    ]}
    ensure_segment_ids(task)
    uid = task["segments"][0]["id"]
    i = resolve_seg_index(task, uid)
    set_text_task(task, i, "new")
    assert task["segments"][0]["text"] == "new"
