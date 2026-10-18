# -*- coding: utf-8 -*-
"""T06c: project.pack copies media and rewrites paths relative."""
from __future__ import annotations

import os

from cutora.model.project_pack import pack_tasks
from cutora.ui.command_bodies import CommandBodies


def test_pack_copy_rewrites_relative(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    clip = src_dir / "talk.mp4"
    clip.write_bytes(b"hello-media")
    task = {
        "video": str(clip),
        "path": str(clip),
        "duration": 1.0,
        "timeline": {
            "sources": {"s1": {"id": "s1", "path": str(clip), "duration": 1.0}},
            "tracks": [],
        },
    }
    out = tmp_path / "packed"
    report = pack_tasks([task], str(out), mode="copy")
    assert report["files"] == 1
    assert report["missing"] == []
    assert clip.is_file()
    packed = report["tasks"][0]
    assert packed["video"].replace("\\", "/").startswith("media/")
    dest = out / packed["video"]
    assert dest.is_file()
    assert dest.read_bytes() == b"hello-media"
    assert packed["timeline"]["sources"]["s1"]["path"].replace("\\", "/").startswith("media/")


def test_pack_reports_missing(tmp_path):
    task = {"video": str(tmp_path / "gone.mp4"), "path": str(tmp_path / "gone.mp4")}
    report = pack_tasks([task], str(tmp_path / "out"), mode="copy")
    assert report["files"] == 0
    assert report["missing"]
    win = CommandBodies.__new__(CommandBodies)
    win.tasks = [task]
    win.collect_project_data = lambda: {"version": 2, "tasks": win.tasks}
    win._write_project_json = lambda path, data: open(path, "w", encoding="utf-8").write("{}")
    out = win.command_project_pack(out_dir=str(tmp_path / "bag"), mode="copy")
    assert out["missing"]
    assert os.path.isfile(out["project"])
