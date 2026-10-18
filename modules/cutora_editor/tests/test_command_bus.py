# -*- coding: utf-8 -*-
"""Command bus: schema, NeedsGui, audit log, secret stripping, undo meta."""
from __future__ import annotations

import json
import os

import pytest

from cutora.core.command_schema import SchemaError
from cutora.core.commands import (
    CommandError, NeedConfirm, NeedsGui, get_bus, reset_bus_for_tests,
)
from cutora.core.history import HistoryManager


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_bus_for_tests()
    yield
    reset_bus_for_tests()


def test_app_version_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    bus = get_bus()
    result = bus.execute("app.version", {}, source="cli")
    assert result["app"] == "CUTORA"
    assert result["version"]


def test_needs_gui_for_project_new():
    bus = get_bus()
    with pytest.raises(NeedsGui):
        bus.execute("project.new", {}, source="cli")


def test_schema_rejects_bad_params():
    bus = get_bus()
    with pytest.raises(SchemaError):
        bus.execute("media.import", {"paths": "not-a-list"}, source="ui")


def test_dangerous_confirm_only_for_api():
    bus = get_bus()
    with pytest.raises(NeedConfirm):
        bus.execute("media.remove", {"ids": [0]}, source="api")
    with pytest.raises(NeedsGui):
        bus.execute("media.remove", {"ids": [0], "confirm": True}, source="api")


def test_settings_strips_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    bus = get_bus()
    with pytest.raises(CommandError):
        bus.execute("app.settings.set", {"key": "openai_api_key", "value": "sk-test"}, source="cli")
    bus.execute("app.settings.set", {"key": "export_video_mode", "value": "compressed"}, source="cli")
    got = bus.execute("app.settings.get", {}, source="cli")
    assert "openai_api_key" not in json.dumps(got)
    assert got["settings"]["export_video_mode"] == "compressed"


def test_audit_log_written_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    bus = get_bus()
    bus.execute("app.version", {"openai_api_key": "sk-should-not-appear"}, source="cli")
    log = os.path.join(str(tmp_path), "logs", "commands.jsonl")
    assert os.path.isfile(log)
    text = open(log, encoding="utf-8").read()
    assert "app.version" in text
    assert "sk-should-not-appear" not in text
    assert "openai_api_key" not in text


def test_history_push_keeps_command_meta():
    mgr = HistoryManager(max_steps=40)
    mgr.push("新建", {"tasks": []}, extra={
        "command": "project.new", "params": {}, "source": "ui",
    })
    assert mgr.history[0]["command"] == "project.new"
    assert mgr.history[0]["source"] == "ui"
    assert "state" in mgr.history[0]
