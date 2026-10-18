# -*- coding: utf-8 -*-
"""T04a: import/export commands are headless, schema-valid, and persist."""
from __future__ import annotations

import json
import os
import shutil

from cutora.core.command_schema import check_schema
from cutora.core.commands import CommandError, NeedsGui, get_bus, get_registry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
G01_PROJ = os.path.join(
    REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")


def _project(tmp_path, segs=None):
    media = tmp_path / "video.mp4"
    shutil.copy2(G01, media)
    proj = tmp_path / "project.asproj.json"
    data = {
        "version": 2,
        "tasks": [{
            "video": str(media),
            "path": str(media),
            "text": "",
            "segments": list(segs or []),
            "style": {"font_family": "Arimo", "fontsize": 48, "pos_y": 900},
        }],
    }
    proj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return media, proj


def test_import_export_commands_registered_headless():
    reg = get_registry()
    for name in ("subtitle.import_file", "subtitle.export_file"):
        spec = reg.get(name)
        assert spec.headless is True
        assert len(spec.doc_zh) >= 40
        check_schema(spec.params_schema)
        check_schema(spec.returns_schema)


def test_headless_import_replace_append(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path)
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    extra = tmp_path / "in2.srt"
    extra.write_text("1\n00:00:01,000 --> 00:00:02,000\nWorld\n", encoding="utf-8")
    out = bus.execute("subtitle.import_file", {
        "path": str(srt), "media": str(media), "project": str(proj),
        "mode": "replace",
    }, source="cli")
    assert out["count"] == 1
    data = json.loads(proj.read_text(encoding="utf-8"))
    assert data["tasks"][0]["segments"][0]["text"] == "Hello"
    bus.execute("subtitle.import_file", {
        "path": str(extra), "media": str(media), "project": str(proj),
        "mode": "append",
    }, source="cli")
    data = json.loads(proj.read_text(encoding="utf-8"))
    texts = [s["text"] for s in data["tasks"][0]["segments"]]
    assert texts == ["Hello", "World"]


def test_headless_export_all_formats(tmp_path):
    bus = get_bus()
    media, proj = _project(tmp_path)
    shutil.copy2(G01_PROJ, proj)
    # Point the copied golden project at our copied media name via media= basename match.
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()
    for ext in ("srt", "vtt", "ass", "txt", "json"):
        dest = dest_dir / ("subs." + ext)
        result = bus.execute("subtitle.export_file", {
            "path": str(dest),
            "media": str(media),
            "project": str(G01_PROJ),
        }, source="cli")
        assert os.path.isfile(result["path"])
        assert os.path.getsize(result["path"]) > 0


def test_import_file_does_not_need_gui():
    bus = get_bus()
    try:
        bus.execute("subtitle.import_file", {"path": ""}, source="cli")
    except NeedsGui:
        raise AssertionError("headless command raised NeedsGui")
    except CommandError:
        pass


def test_menu_entries_exist():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert 'tr("导入字幕文件' in src
    assert 'tr("导出字幕文件' in src
    assert "subtitle.import_file" in src
    assert "subtitle.export_file" in src
