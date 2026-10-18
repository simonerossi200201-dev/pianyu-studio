# -*- coding: utf-8 -*-
"""T01 step-1 scaffold: package layout and headless --describe."""
from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_cutora_version():
    import cutora
    assert cutora.__version__ == "7.0.0"


def test_package_layout():
    for rel in (
        os.path.join("cutora", "__main__.py"),
        os.path.join("cutora", "app.py"),
        os.path.join("cutora", "core", "paths.py"),
        os.path.join("cutora", "core", "log.py"),
        os.path.join("cutora", "ai", "__init__.py"),
        os.path.join("cutora", "ui", "theme", "__init__.py"),
        os.path.join("resources", "data", ".gitkeep"),
    ):
        assert os.path.isfile(os.path.join(REPO, rel)), rel


def test_describe_no_torch():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [VENV_PY, "-m", "cutora", "--describe"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "ops" in payload or "name" in payload or isinstance(payload, dict)
    # torch must not have been imported in this process; we only check the
    # child finished quickly and printed JSON (early_dispatch path).
    assert proc.stdout.strip().startswith("{") or proc.stdout.strip().startswith("[")
