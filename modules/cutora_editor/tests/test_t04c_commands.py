# -*- coding: utf-8 -*-
"""T04c headless transcript commands."""
from __future__ import annotations

import json
import os
import shutil

from cutora.core.command_schema import check_schema
from cutora.core.commands import get_bus, get_registry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")

NEW_CMDS = (
    "subtitle.lock", "subtitle.find", "subtitle.replace",
    "subtitle.transform_case", "subtitle.strip_end_punct",
    "subtitle.apply_glossary", "subtitle.check_overlong",
)


def _project(tmp_path, segs):
    media = tmp_path / "video.mp4"
    shutil.copy2(G01, media)
    proj = tmp_path / "project.asproj.json"
    data = {
        "version": 2,
        "tasks": [{
            "video": str(media),
            "path": str(media),
            "text": "",
            "layout_rules": {
                "max_chars_per_line": 42,
                "max_lines": 2,
            },
            "segments": list(segs),
        }],
    }
    proj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return media, proj


def _segs(proj):
    return json.loads(proj.read_text(encoding="utf-8"))["tasks"][0]["segments"]


def test_new_commands_registered_headless():
    reg = get_registry()
    for name in NEW_CMDS + ("subtitle.set_text", "subtitle.merge", "subtitle.split", "subtitle.delete"):
        spec = reg.get(name)
        assert spec.headless is True, name
        assert len(spec.doc_zh) >= 40, name
        check_schema(spec.params_schema)


def test_set_text_lcs_lock_headless(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path, [{
        "start": 0.0, "end": 1.0, "text": "Hello frends.",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4, "wid": 0},
            {"word": "frends.", "start": 0.5, "end": 1.0, "wid": 1},
        ],
    }])
    bus.execute("subtitle.set_text", {
        "seg_id": 0, "text": "Hello friends.",
        "media": str(media), "project": str(proj),
    }, source="cli")
    seg = _segs(proj)[0]
    assert seg["text"] == "Hello friends."
    assert seg["locked"] is True
    assert seg["words"][0]["start"] == 0.0
    assert seg["words"][0]["wid"] == 0


def test_overlong_and_glossary_and_case(tmp_path):
    bus = get_bus()
    long_text = "x" * 90
    media, proj = _project(tmp_path, [
        {"start": 0.0, "end": 2.0, "text": "Hello there.", "locked": False},
        {"start": 2.0, "end": 4.0, "text": long_text, "locked": False},
    ])
    over = bus.execute("subtitle.check_overlong", {
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert over["count"] == 1
    assert over["over"][0]["seg_id"] == 1
    csv_path = tmp_path / "dict.csv"
    csv_path.write_text("Hello,Hi\n", encoding="utf-8")
    bus.execute("subtitle.apply_glossary", {
        "csv": str(csv_path), "media": str(media), "project": str(proj),
    }, source="cli")
    bus.execute("subtitle.transform_case", {
        "mode": "upper", "seg_ids": [0],
        "media": str(media), "project": str(proj),
    }, source="cli")
    bus.execute("subtitle.strip_end_punct", {
        "seg_ids": [0], "media": str(media), "project": str(proj),
    }, source="cli")
    first = _segs(proj)[0]
    assert first["text"] == "HI THERE"
    assert first["locked"] is True


def test_split_merge_delete_lock(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path, [
        {"start": 0.0, "end": 2.0, "text": "Hello world today"},
        {"start": 2.0, "end": 3.0, "text": "Next."},
    ])
    bus.execute("subtitle.split", {
        "seg_id": 0, "at_char": 6,
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert len(_segs(proj)) == 3
    bus.execute("subtitle.merge", {
        "seg_id": 0, "direction": "next",
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert len(_segs(proj)) == 2
    bus.execute("subtitle.lock", {
        "seg_id": 0, "locked": True,
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert _segs(proj)[0]["locked"] is True
    bus.execute("subtitle.delete", {
        "seg_id": 1, "confirm": True,
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert len(_segs(proj)) == 1


def test_find_replace(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path, [
        {"start": 0.0, "end": 1.0, "text": "The cat sat."},
    ])
    hits = bus.execute("subtitle.find", {
        "query": "cat", "media": str(media), "project": str(proj),
    }, source="cli")
    assert hits["count"] == 1
    bus.execute("subtitle.replace", {
        "query": "cat", "replacement": "dog",
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert _segs(proj)[0]["text"] == "The dog sat."
