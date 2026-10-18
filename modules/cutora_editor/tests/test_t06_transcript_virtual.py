# -*- coding: utf-8 -*-
"""T06-3: 1000-segment transcript fill stays under 50 ms."""
from __future__ import annotations

import os
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_thousand_seg_fill_under_50ms(qtbot=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.transcript import TranscriptPanel

    app = QApplication.instance() or QApplication(["t06-3"])
    _ = app
    panel = TranscriptPanel(window=None)
    segs = [
        {"start": float(i), "end": float(i) + 0.8, "text": "word %d sample line" % i}
        for i in range(1000)
    ]
    t0 = time.perf_counter()
    panel.model.reset_segs(segs)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    assert panel.model.rowCount() == 1000
    assert dt_ms < 50.0, dt_ms
    print("T06_VIRT_OK ms=%.2f" % dt_ms, flush=True)
