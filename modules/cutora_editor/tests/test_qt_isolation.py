# -*- coding: utf-8 -*-
"""Qt tests must not load PySide6 into the pytest parent (order-dependent abort)."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qt_isolate import ISOLATE_FILES, module_level_qt_import  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
TESTS = os.path.join(REPO, "tests")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def test_isolate_list_covers_module_level_qt_imports():
    missing = []
    extra_ok = set(ISOLATE_FILES)
    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(TESTS, name)
        if module_level_qt_import(path) and name not in ISOLATE_FILES:
            missing.append(name)
        extra_ok.discard(name)
    assert missing == [], "module-level PySide6/script_stitcher not isolated: %s" % missing


def test_collect_only_parent_does_not_import_pyside6():
    code = r"""
import os, sys
sys.path.insert(0, r"%s")
os.chdir(r"%s")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
rc = pytest.main(["tests", "--collect-only", "-q", "-p", "no:cacheprovider"])
qt = [m for m in sys.modules if m == "PySide6" or m.startswith("PySide6.")]
print("QT_MODULES", qt)
print("RC", rc)
assert not qt, qt
assert rc == 0
""" % (REPO.replace("\\", "\\\\"), REPO.replace("\\", "\\\\"))
    proc = subprocess.run(
        [VENV_PY, "-c", code], cwd=REPO, env=_env(), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "QT_MODULES []" in (proc.stdout or ""), out


def test_about_dialog_nodeid_does_not_native_abort():
    proc = subprocess.run(
        [VENV_PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=line",
         "tests/test_startup_order.py::test_about_dialog_mark_and_tagline"],
        cwd=REPO, env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    rc = proc.returncode
    assert rc not in (3221226505, -1073740791, 3221225477, -1073741819), out
    assert rc == 0, out


def test_stitcher_then_about_does_not_native_abort():
    proc = subprocess.run(
        [VENV_PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=line",
         "tests/test_script_stitcher_edge.py",
         "tests/test_startup_order.py::test_about_dialog_mark_and_tagline"],
        cwd=REPO, env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    rc = proc.returncode
    assert rc not in (3221226505, -1073740791, 3221225477, -1073741819), out
    assert rc == 0, out
