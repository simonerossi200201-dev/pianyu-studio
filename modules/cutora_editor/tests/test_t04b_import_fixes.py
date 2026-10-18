# -*- coding: utf-8 -*-
"""T04b commit 1: no new_track, TXT stays unaligned, GUI auto goes through the bus."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil

from cutora.core.command_schema import check_schema
from cutora.core.commands import get_bus, get_registry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def _project(tmp_path, segs=None, text=""):
    media = tmp_path / "video.mp4"
    shutil.copy2(G01, media)
    proj = tmp_path / "project.asproj.json"
    data = {
        "version": 2,
        "tasks": [{
            "video": str(media),
            "path": str(media),
            "text": text,
            "segments": list(segs or []),
            "style": {"font_family": "Arimo", "fontsize": 48, "pos_y": 900},
        }],
    }
    proj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return media, proj


def test_import_mode_enum_has_no_new_track():
    spec = get_registry().get("subtitle.import_file")
    check_schema(spec.params_schema)
    mode = spec.params_schema["properties"]["mode"]
    assert mode.get("enum") == ["replace", "append"]
    assert "new_track" not in spec.doc_zh


def test_txt_import_does_not_create_segments(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path)
    txt = tmp_path / "copy.txt"
    txt.write_text("Hello friends. Welcome to the studio.\n", encoding="utf-8")
    out = bus.execute("subtitle.import_file", {
        "path": str(txt), "media": str(media), "project": str(proj),
        "mode": "replace",
    }, source="cli")
    data = json.loads(proj.read_text(encoding="utf-8"))
    task = data["tasks"][0]
    assert task["segments"] == []
    assert "Hello friends" in task["text"]
    assert "Welcome to the studio" in task["text"]
    assert out["segment_count"] == 0


def test_txt_import_does_not_make_subtitle_auto_skip(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path)
    txt = tmp_path / "copy.txt"
    txt.write_text("Peace be with you.\n", encoding="utf-8")
    bus.execute("subtitle.import_file", {
        "path": str(txt), "media": str(media), "project": str(proj),
        "mode": "replace",
    }, source="cli")
    status = bus.execute("subtitle.status", {
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert status["has_segments"] is False
    data = json.loads(proj.read_text(encoding="utf-8"))
    task = data["tasks"][0]
    assert task.get("segments") in ([], None)
    assert (task.get("text") or "").strip()
    # command_subtitle_auto skip_if_present uses task["segments"] truthiness.
    assert not task.get("segments")


def test_gui_auto_subtitle_goes_through_the_bus():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert 'tr("自动字幕"), self.run_align' not in src
    assert "btn_ai.clicked.connect(self.run_align)" not in src
    assert "b2.clicked.connect(self.run_align)" not in src
    assert "_ui_auto_subtitle" in src
    assert 'bus.execute("subtitle.auto"' in src
    assert "def run_align" in src


def test_example_val_uses_first_enum():
    path = os.path.join(REPO, "tools", "gen_api_docs.py")
    spec = importlib.util.spec_from_file_location("gen_api_docs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._example_val({"type": "string", "enum": ["replace", "append"]}) == "replace"
    assert mod._example_val({"type": "string"}) == ""
    assert mod._example_val({"type": "integer"}) == 0
