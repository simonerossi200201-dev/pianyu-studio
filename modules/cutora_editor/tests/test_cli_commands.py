# -*- coding: utf-8 -*-
"""cutora_cli commands / cmd must stay on the early-dispatch path."""
from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run(args, timeout=30):
    return subprocess.run(
        [VENV_PY, "-m", "cutora", *args],
        cwd=REPO, capture_output=True, text=True, timeout=timeout,
        env=_env(), encoding="utf-8",
    )


def test_commands_lists_at_least_60():
    proc = _run(["commands"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert len(payload["commands"]) >= 60
    names = {c["name"] for c in payload["commands"]}
    assert "app.version" in names
    assert "batch.stitch" in names
    assert "app.theme.set" in names


def test_cmd_app_version_json():
    proc = _run(["cmd", "app.version"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["result"]["version"]


def test_cmd_gui_command_fails_without_window():
    proc = _run(["cmd", "project.new", "--json", "{}"])
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload.get("code") == "needs_gui"


def test_commands_does_not_import_torch_or_pyside6():
    code = (
        "import sys\n"
        "from cutora.batch.cli import early_dispatch\n"
        "rc = early_dispatch(['cutora', 'commands'])\n"
        "assert rc == 0, rc\n"
        "bad = [m for m in sys.modules if m == 'torch' or m.startswith('PySide6')]\n"
        "assert not bad, bad\n"
        "print('clean')\n"
    )
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO, capture_output=True, text=True, timeout=30, env=_env(),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "clean" in proc.stdout
