# -*- coding: utf-8 -*-
"""UI golden stills. tools/ui_golden.py is the checker."""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


@pytest.fixture(scope="module")
def ui_boot():
    from tools.ui_golden import _boot
    return _boot()


@pytest.mark.parametrize("name", ["u01c", "u02c", "u03f", "u04f", "u05c", "u06b", "u07c"])
def test_ui_golden_case(ui_boot, name):
    from tools.ui_golden import run_case
    ok, digest = run_case(name, record=False)
    assert ok, name
