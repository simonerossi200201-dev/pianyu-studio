# -*- coding: utf-8 -*-
"""T11e: auto-credit applies to every video in a multi-project queue."""
from __future__ import annotations

import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDIT_ID = "credit.default"


@pytest.fixture
def preset_store(tmp_path, monkeypatch):
    path = tmp_path / "text_presets.json"
    monkeypatch.setattr(
        "cutora.subtitle.text_preset._user_path", lambda: str(path))
    return path


def _task(w, h, duration=8.0, extra_ov=None):
    ovs = []
    if extra_ov is not None:
        ovs.append(extra_ov)
    return {
        "dims": [w, h],
        "duration": duration,
        "text_overlays": ovs,
        "segments": [{"start": 0, "end": duration, "text": "hi"}],
    }


class _FakeWin:
    def __init__(self, tasks):
        self.tasks = list(tasks)


def _credits(task):
    return [ov for ov in (task.get("text_overlays") or [])
            if isinstance(ov, dict) and ov.get("source_preset_id") == CREDIT_ID]


def test_queue_bus_apply_hits_every_task_in_every_project(preset_store):
    """Drive the same bus loop the queue uses over 2 projects × 2+ tasks."""
    from cutora.batch.queue_credit import apply_auto_credit_from_queue_options
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests

    reset_bus_for_tests()
    bus = CommandBus(get_registry())
    win = _FakeWin([])
    bus.bind_window(win)

    projects = [
        [
            _task(1080, 1920, extra_ov={"id": "keep_a", "content": "note"}),
            _task(1920, 1080),
            _task(1080, 1920),
        ],
        [
            _task(1920, 1080),
            _task(1080, 1920, extra_ov={"id": "keep_b", "content": "other"}),
        ],
    ]
    opts_off = {"auto_credit": False, "credit_preset_id": CREDIT_ID}
    opts_on = {"auto_credit": True, "credit_preset_id": CREDIT_ID}

    # Default OFF: nothing is written, even with a preset id present.
    for tasks in projects:
        win.tasks = tasks
        n = apply_auto_credit_from_queue_options(bus, win.tasks, opts_off, source="ui")
        assert n == 0
        for task in tasks:
            assert _credits(task) == []

    seen = 0
    for tasks in projects:
        win.tasks = tasks
        n = apply_auto_credit_from_queue_options(bus, win.tasks, opts_on, source="ui")
        assert n == len(tasks)
        seen += n
        # Second pass (queue retry / resume) must stay idempotent.
        n2 = apply_auto_credit_from_queue_options(bus, win.tasks, opts_on, source="ui")
        assert n2 == len(tasks)
        for task in tasks:
            hits = _credits(task)
            assert len(hits) == 1
            assert hits[0].get("source_preset_id") == CREDIT_ID
        ids = [str(ov.get("id") or "") for task in tasks
               for ov in (task.get("text_overlays") or [])]
        if tasks is projects[0]:
            assert "keep_a" in ids
        else:
            assert "keep_b" in ids

    assert seen == 5
    assert all(len(_credits(t)) == 1 for tasks in projects for t in tasks)


def test_empty_queue_options_auto_credit_default_off():
    from cutora.batch.cli import empty_queue
    opts = empty_queue()["options"]
    assert opts.get("auto_credit") is False
    assert opts.get("credit_preset_id") == ""


def test_menu_entry_and_checkbox_default_off_in_source():
    mw = open(os.path.join(REPO, "cutora", "ui", "main_window.py"),
              encoding="utf-8").read()
    assert 'tr("复制署名到队列每条视频")' in mw
    assert 'tr("配方：工程队列署名…")' in mw
    assert "open_auto_credit_dialog" in mw
    assert "copy_credit_overlay_to_queue" in mw
    assert "apply_auto_credit_from_queue_options" in mw
    assert "text_style_presets.json" not in mw.split("open_auto_credit_dialog", 1)[-1][:800]

    qu = open(os.path.join(REPO, "cutora", "batch", "queue_ui.py"),
              encoding="utf-8").read()
    assert 'class AutoCreditDialog' in qu
    assert qu.count("chk_auto_credit.setChecked(False)") >= 2
    assert "text_style_presets.json" not in qu

    qc = open(os.path.join(REPO, "cutora", "batch", "queue_credit.py"),
              encoding="utf-8").read()
    assert '"text_preset.apply"' in qc
    assert '"idempotent": True' in qc
    assert "text_style_presets.json" not in qc


def test_cli_apply_to_project_file_hits_every_task(tmp_path, preset_store):
    from cutora.subtitle.text_preset import apply_to_project_file

    paths = []
    for i in range(2):
        p = tmp_path / ("q%d.asproj.json" % i)
        data = {"tasks": [_task(1080, 1920), _task(1920, 1080)]}
        p.write_text(json.dumps(data), encoding="utf-8")
        paths.append(p)
    for p in paths:
        apply_to_project_file(str(p), CREDIT_ID, idempotent=True)
        apply_to_project_file(str(p), CREDIT_ID, idempotent=True)
    n = 0
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["tasks"]) == 2
        for task in data["tasks"]:
            hits = _credits(task)
            assert len(hits) == 1
            n += 1
    assert n == 4
