# -*- coding: utf-8 -*-
"""Fingerprint / dirty-tree gating for tools/golden_check.py (no Qt)."""
from __future__ import annotations

import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    path = os.path.join(REPO, "tools", "golden_check.py")
    spec = importlib.util.spec_from_file_location("cutora_golden_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_dirty_paths():
    m = _mod()
    assert m.is_source_dirty_path("cutora/render/textfx/layout.py")
    assert m.is_source_dirty_path("tests/golden/g01_vertical_basic/expected.sha256")
    assert m.is_source_dirty_path("resources/fonts/Arimo-Bold.ttf")
    assert m.is_source_dirty_path("tools/golden_check.py")
    assert not m.is_source_dirty_path("docs/plan/reports/T04f-4c_golden.txt")
    assert not m.is_source_dirty_path("build/fx_contact_sheet_dark.png")
    assert not m.is_source_dirty_path("tests/test_t04f_emphasis.py")


def test_fingerprint_lists_dirty_and_banner_constant():
    m = _mod()
    fp = {
        "head": "deadbeef",
        "porcelain": [" M cutora/render/textfx/layout.py"],
        "dirty_files": [" M cutora/render/textfx/layout.py"],
        "source_dirty": True,
        "ffmpeg": "ffmpeg version test",
        "cv2": "4.0",
        "numpy": "1.0",
        "cv2_threads": "4",
        "pyside6": "6.0",
        "cutora_env": {"CUTORA_USER_DATA": "/tmp"},
    }
    text = m.format_fingerprint(fp)
    assert "HEAD=deadbeef" in text
    assert "source_dirty=yes" in text
    assert "cutora/render/textfx/layout.py" in text
    assert "cv2.getNumThreads()=4" in text
    assert "CUTORA_USER_DATA=/tmp" in text
    assert "PySide6=6.0" in text
    assert m.DIRTY_BANNER == "DIRTY TREE — 本次结果不代表任何提交"


def test_fingerprint_reads_pyside6_version_without_importing_qt():
    m = _mod()
    fp = m.collect_fingerprint()
    ver = fp.get("pyside6") or ""
    assert ver and "pending" not in ver.lower(), ver
    assert ver[0].isdigit(), ver
