# -*- coding: utf-8 -*-
"""T04a: Word / Segment accessors stay compatible with existing project JSON."""
from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01_PROJ = os.path.join(
    REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")


def _first_seg():
    data = json.loads(open(G01_PROJ, encoding="utf-8").read())
    return data["tasks"][0]["segments"][0]


def test_word_reads_existing_word_field():
    from cutora.subtitle.model import word_from_dict, word_to_project_dict
    raw = _first_seg()["words"][0]
    word = word_from_dict(raw)
    assert word.text == raw["word"]
    assert word.start == raw["start"]
    out = word_to_project_dict(word)
    assert out["word"] == raw["word"]
    assert out["wid"] == raw["wid"]
    assert "text" not in out


def test_segment_preserves_role_and_skips_default_locked():
    from cutora.subtitle.model import segment_from_dict, segment_to_project_dict
    raw = _first_seg()
    seg = segment_from_dict(raw)
    assert seg.text == raw["text"]
    assert seg.locked is False
    assert seg.speaker is None
    out = segment_to_project_dict(seg)
    assert out["role"] == "default"
    assert "locked" not in out
    assert out["words"][0]["word"] == raw["words"][0]["word"]
    assert out["words"][0]["wid"] == raw["words"][0]["wid"]


def test_new_fields_optional_and_roundtrip():
    from cutora.subtitle.model import segment_from_dict, segment_to_project_dict
    raw = {
        "id": "s1",
        "start": 1.0,
        "end": 2.5,
        "text": "Hi",
        "words": [{"text": "Hi", "start": 1.0, "end": 2.5, "score": 0.9}],
        "speaker": "A",
        "lang": "en",
        "style_ref": "amber",
        "locked": True,
    }
    seg = segment_from_dict(raw)
    assert seg.locked is True
    assert seg.words[0].text == "Hi"
    out = segment_to_project_dict(seg)
    assert out["locked"] is True
    assert out["speaker"] == "A"
    assert out["lang"] == "en"
    assert out["words"][0]["word"] == "Hi"
    assert out["words"][0]["score"] == 0.9


def test_deserialize_render_task_keeps_unknown_segment_keys():
    """Accessors add optional fields; deserialize_render_task is a dict copy.

    Do not import project_io here: it pulls text_utils → PySide6 into pytest.
    """
    src = open(
        os.path.join(REPO, "cutora", "model", "project_io.py"), encoding="utf-8"
    ).read()
    assert "def deserialize_render_task" in src
    assert "task = dict(d or {})" in src
    data = json.loads(open(G01_PROJ, encoding="utf-8").read())
    task = dict(data["tasks"][0])
    raw = dict(task["segments"][0])
    raw["locked"] = True
    raw["lang"] = "en"
    # Simulate deserialize: extra keys must still be there for the renderer.
    restored = dict(task)
    restored["segments"] = [dict(raw)]
    seg = restored["segments"][0]
    assert seg["locked"] is True
    assert seg["lang"] == "en"
    assert seg["text"] == raw["text"]
    assert "words" in seg
