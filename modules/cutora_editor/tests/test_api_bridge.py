# -*- coding: utf-8 -*-
"""API QtBridge: timeout must not run the stale job later."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.core.runtime import init_torch_runtime

try:
    init_torch_runtime(render_child=False)
except RuntimeError:
    pass

from PySide6.QtWidgets import QApplication

from cutora.api.bridge import _make_bridge_class


def test_submit_timeout_does_not_run_fn_later():
    app = QApplication.instance() or QApplication(["cutora-bridge-test"])
    ran = []

    def fn():
        ran.append("ran")
        time.sleep(2)

    bridge = _make_bridge_class()()
    try:
        bridge.submit(fn, timeout=0.2)
        raise AssertionError("expected TimeoutError")
    except TimeoutError:
        pass
    app.processEvents()
    time.sleep(0.05)
    app.processEvents()
    assert ran == []
