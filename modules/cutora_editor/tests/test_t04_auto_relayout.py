# -*- coding: utf-8 -*-
"""Appendix B: auto-relayout after ASR, skip locked/old, never on import."""
from __future__ import annotations

import json
import os
import shutil

from cutora.subtitle.layout import (
    AUTO_RELAYOUT_KEY,
    auto_relayout_enabled,
    fingerprints,
    relayout_new_segments,
    relayout_task,
)
from cutora.core.commands import get_bus

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def test_auto_relayout_default_on_and_can_disable():
    assert auto_relayout_enabled({}) is True
    assert auto_relayout_enabled({AUTO_RELAYOUT_KEY: True}) is True
    assert auto_relayout_enabled({AUTO_RELAYOUT_KEY: False}) is False


def test_relayout_new_skips_existing_and_locked():
    old = [
        {"start": 0.0, "end": 1.0, "text": "Hello friends.", "locked": False},
        {"start": 1.0, "end": 2.0, "text": "Keep me.", "locked": True},
    ]
    prev = fingerprints(old)
    task = {"segments": [
        dict(old[0]),
        dict(old[1]),
        {
            "start": 2.0, "end": 5.0, "lang": "en",
            "text": "They waited in the shade of the old church for a friend from town.",
        },
    ]}
    out = relayout_new_segments(task, prev)
    assert out["changed"] == 1
    assert "lines" not in task["segments"][0]
    assert "lines" not in task["segments"][1]
    lines = task["segments"][2]["lines"]
    assert len(lines) == 2
    assert lines[0].split()[-1].lower() != "the"


def test_relayout_task_skips_locked():
    task = {"segments": [
        {"start": 0.0, "end": 3.0, "lang": "en", "locked": True,
         "text": "They waited in the shade of the old church for a friend from town."},
        {"start": 3.0, "end": 6.0, "lang": "en",
         "text": "They waited in the shade of the old church for a friend from town."},
    ]}
    out = relayout_task(task)
    assert out["changed"] == 1
    assert "lines" not in task["segments"][0]
    assert len(task["segments"][1]["lines"]) == 2


def test_import_srt_does_not_write_lines(tmp_path):
    media = tmp_path / "video.mp4"
    shutil.copy2(G01, media)
    proj = tmp_path / "project.asproj.json"
    proj.write_text(json.dumps({
        "version": 2,
        "tasks": [{"video": str(media), "path": str(media), "segments": []}],
    }), encoding="utf-8")
    srt = tmp_path / "in.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n"
        "They waited in the shade of the old church for a friend from town.\n",
        encoding="utf-8",
    )
    bus = get_bus()
    bus.execute("subtitle.import_file", {
        "path": str(srt), "media": str(media), "project": str(proj),
        "mode": "replace",
    }, source="cli")
    segs = json.loads(proj.read_text(encoding="utf-8"))["tasks"][0]["segments"]
    assert segs
    assert "lines" not in segs[0]


def test_on_align_done_calls_relayout_new():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8",
    ).read()
    assert "relayout_new_segments" in src
    assert "auto_relayout_enabled" in src
    assert 'tr("已按排版规则断行")' in src or "已按排版规则断行" in src
    settings = open(
        os.path.join(REPO, "cutora", "ui", "settings_dialog.py"), encoding="utf-8",
    ).read()
    assert 'tr("字幕")' in settings
    assert "chk_auto_relayout" in settings
    qss = open(
        os.path.join(REPO, "cutora", "ui", "theme", "theme_dark.qss.j2"),
        encoding="utf-8",
    ).read()
    assert "transcriptEditor" in qss
    assert "font_ui" in qss.split("transcriptEditor")[1][:400]


def test_relayout_menu_uses_selection_when_present():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8",
    ).read()
    assert 'params["scope"] = "selected"' in src
    assert "selected_indices" in src


def test_relayout_new_reads_task_layout_rules():
    prev = set()
    task = {
        "layout_rules": {
            "max_chars_per_line": 24, "max_lines": 2, "balance_lines": True,
            "line_break": "space",
        },
        "segments": [{
            "start": 0.0, "end": 4.0, "lang": "en",
            "text": "They waited in the shade of the old church for a friend from town.",
        }],
    }
    out = relayout_new_segments(task, prev)
    assert out["changed"] == 1
    lines = task["segments"][0]["lines"]
    assert len(lines) >= 2
    assert all(len(ln) <= 24 for ln in lines[:-1])
