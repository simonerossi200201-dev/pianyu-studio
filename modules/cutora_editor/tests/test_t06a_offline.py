# -*- coding: utf-8 -*-
"""T06a: missing media is flagged, CLI exits, export refuses, relink_folder repairs."""
from __future__ import annotations

import json
import os
import shutil

import pytest

from cutora.core.commands import CommandError
from cutora.model.offline import (
    DURATION_EPS,
    inspect_path,
    inspect_task,
    list_offline,
    relink_folder,
    relink_source,
    require_online,
)
from cutora.ui.command_bodies import CommandBodies


@pytest.fixture
def media_clip(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"not-really-mp4-but-exists")
    return src


def test_missing_file_is_offline(tmp_path):
    rec = inspect_path(str(tmp_path / "nope.mp4"), duration_json=20.0)
    assert rec["offline"] is True
    assert rec["duration_json"] == 20.0
    assert rec["duration_probed"] is None
    assert rec["duration_mismatch"] is False


def test_duration_mismatch_keeps_both(monkeypatch, tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        "cutora.model.offline.probe_duration_sec", lambda _p: 8.0)
    rec = inspect_path(str(path), duration_json=20.0)
    assert rec["offline"] is False
    assert rec["duration_json"] == 20.0
    assert rec["duration_probed"] == 8.0
    assert rec["duration_mismatch"] is True
    task = {"video": str(path), "path": str(path), "duration": 20.0, "timeline": {"sources": {}}}
    inspect_task(task)
    assert task["duration"] == 20.0
    assert task["duration_probed"] == 8.0
    assert task["duration_mismatch"] is True


def test_duration_eps_does_not_flag_noise(monkeypatch, tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        "cutora.model.offline.probe_duration_sec", lambda _p: 20.0 + DURATION_EPS / 2)
    rec = inspect_path(str(path), duration_json=20.0)
    assert rec["duration_mismatch"] is False


def test_cli_load_missing_exits_nonzero(tmp_path):
    from cutora.batch.cli import CliError, EXIT_FAIL, load_project_file
    proj = {
        "version": 2,
        "tasks": [{
            "video": str(tmp_path / "DOES_NOT_EXIST.mp4"),
            "path": str(tmp_path / "DOES_NOT_EXIST.mp4"),
            "duration": 20.0,
            "timeline": {"width": 1080, "height": 1920, "fps": 30, "sources": {}, "tracks": []},
        }],
    }
    p = tmp_path / "ghost.asproj.json"
    p.write_text(json.dumps(proj), encoding="utf-8")

    class Host:
        PROJECT_VERSION = 2
        PROJECT_VERSION_MIN = 1

        def _normalize_project_data(self, data):
            from cutora.model.project_io import _normalize_project_data
            _normalize_project_data(data)

        def deserialize_render_task(self, t):
            from cutora.model.project_io import deserialize_render_task
            return deserialize_render_task(t)

    with pytest.raises(CliError) as ei:
        load_project_file(Host(), str(p))
    assert ei.value.code == EXIT_FAIL
    assert "离线" in ei.value.message
    # File on disk unchanged.
    assert json.loads(p.read_text(encoding="utf-8"))["tasks"][0]["duration"] == 20.0


def test_export_refuses_offline_and_does_not_write_project(tmp_path):
    ghost = str(tmp_path / "nope.mp4")
    proj = tmp_path / "p.asproj.json"
    blob = json.dumps({"version": 2, "tasks": [{"video": ghost, "path": ghost, "duration": 20.0}]})
    proj.write_text(blob, encoding="utf-8")
    win = CommandBodies.__new__(CommandBodies)
    win.tasks = [{"video": ghost, "path": ghost, "duration": 20.0}]
    win.curr_idx = 0
    win.exports = []
    win.run_export_worker = lambda *a, **k: win.exports.append(1)
    with pytest.raises(CommandError) as ei:
        win.command_export_run(out=str(tmp_path / "out.mp4"), allow_offline=False)
    assert "离线" in str(ei.value)
    assert win.exports == []
    assert proj.read_text(encoding="utf-8") == blob
    win.command_export_run(out=str(tmp_path / "out.mp4"), allow_offline=True)
    assert win.exports == [1]


def test_relink_folder_repairs_project(tmp_path):
    old = tmp_path / "oldroot"
    new = tmp_path / "newroot"
    old.mkdir()
    new.mkdir()
    (new / "clip.mp4").write_bytes(b"x")
    task = {
        "video": str(old / "clip.mp4"),
        "path": str(old / "clip.mp4"),
        "duration": 20.0,
        "timeline": {
            "sources": {
                "abc": {"id": "abc", "path": str(old / "clip.mp4"), "duration": 20.0},
            },
            "tracks": [],
        },
    }
    inspect_task(task)
    assert task["offline"] is True
    n = relink_folder([task], str(old), str(new))
    assert n >= 1
    assert task["offline"] is False
    assert os.path.isfile(task["video"])
    assert task["timeline"]["sources"]["abc"]["offline"] is False


def test_relink_one_source(tmp_path):
    dest = tmp_path / "here.mp4"
    dest.write_bytes(b"x")
    task = {
        "video": str(tmp_path / "gone.mp4"),
        "path": str(tmp_path / "gone.mp4"),
        "timeline": {"sources": {"sid1": {"id": "sid1", "path": str(tmp_path / "gone.mp4"), "duration": 1}}},
    }
    inspect_task(task)
    assert task["offline"] is True
    n = relink_source(task, "sid1", str(dest))
    assert n >= 1
    assert task["offline"] is False
    assert task["video"] == str(dest)


def test_require_online_default_blocks():
    with pytest.raises(CommandError):
        require_online([{"video": "/no/such.mp4", "path": "/no/such.mp4"}])
    require_online([{"video": "/no/such.mp4"}], allow_offline=True)
    assert list_offline([{"video": "/no/such.mp4", "path": "/no/such.mp4"}])
