# -*- coding: utf-8 -*-
"""T06b: sidecar autosave survives a crash; restore equals the snapshot."""
from __future__ import annotations

import json
import os

from cutora.core.autosave import (
    clear_snapshot,
    leftover_for,
    project_payload,
    read_snapshot,
    reset_throttle_for_tests,
    sidecar_path,
    write_snapshot,
)
from cutora.core.commands import CommandError, get_bus, reset_bus_for_tests
from cutora.ui.command_bodies import CommandBodies


def test_sidecar_name_next_to_project(tmp_path):
    p = str(tmp_path / "film.asproj.json")
    assert sidecar_path(p).endswith("film.autosave.json")
    assert os.path.dirname(sidecar_path(p)) == str(tmp_path)


def test_crash_leaves_file_restore_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    reset_throttle_for_tests()
    proj = str(tmp_path / "job.asproj.json")
    data = {"version": 2, "tasks": [{"video": "a.mp4", "duration": 20.0, "text": "hello"}]}
    open(proj, "w", encoding="utf-8").write(json.dumps({"version": 2, "tasks": []}))
    side = write_snapshot(proj, data)
    assert os.path.isfile(side)
    assert leftover_for(proj) == side
    restored = project_payload(read_snapshot(side))
    assert restored["tasks"][0]["text"] == "hello"
    assert restored["tasks"][0]["duration"] == 20.0
    assert "_autosave" not in restored
    # crash = do not clear. Then restore via command.
    win = CommandBodies.__new__(CommandBodies)
    win.project_path = proj
    win.tasks = []
    win.applied = None

    def apply(d):
        win.applied = d
        win.tasks = list(d.get("tasks") or [])

    win.apply_project_data = apply
    out = win.command_autosave_restore()
    assert out["restored"] is True
    assert win.applied["tasks"][0]["text"] == "hello"
    clear_snapshot(proj)
    assert leftover_for(proj) is None


def test_save_clears_autosave(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    proj = str(tmp_path / "p.asproj.json")
    data = {"version": 2, "tasks": []}
    open(proj, "w", encoding="utf-8").write("{}")
    write_snapshot(proj, data)
    assert leftover_for(proj)
    win = CommandBodies.__new__(CommandBodies)
    win.project_path = proj
    win.tasks = []
    win.collect_project_data = lambda: data
    win._write_project_json = lambda path, d: open(path, "w", encoding="utf-8").write(json.dumps(d))
    win.setWindowTitle = lambda *_a, **_k: None
    from cutora.core import history as hist
    monkeypatch.setattr(hist, "_record_recent_project", lambda *_a, **_k: None)
    win._save_project_quiet(proj)
    assert leftover_for(proj) is None


def test_status_command_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    reset_bus_for_tests()
    out = get_bus().execute("project.autosave.status", {}, source="cli")
    assert "minutes" in out
    assert out["minutes"] >= 1
    reset_bus_for_tests()
