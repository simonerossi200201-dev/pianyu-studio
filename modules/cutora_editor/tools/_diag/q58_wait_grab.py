#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Grab u03d/u07/u08 with extra layout wait; compare hashes to one-shot processEvents."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from tools.ui_golden import (  # noqa: E402
    _boot, _glass_task, _save_img, _sha, case_u03d, case_u07, case_u08,
)


def grab_u03(wait_ms):
    from PySide6.QtWidgets import QApplication, QScrollArea
    from PySide6.QtTest import QTest
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    task = _glass_task()
    panel = SubtitleStylePanel()
    panel.load_data(task.get("style") or {})
    for sec in panel.findChildren(CollapsibleSection):
        sec.set_collapsed(False)
    panel.setFixedSize(280, 1100)
    panel.show()
    QApplication.processEvents()
    if wait_ms:
        QTest.qWait(wait_ms)
        QApplication.processEvents()
        inner = panel.findChild(QScrollArea).widget()
        print("  inner h=%d hint=%d wait=%d" % (
            inner.height(), inner.sizeHint().height(), wait_ms))
        from cutora.ui.widgets.inspector import InspectorRow
        sec0 = panel.findChildren(CollapsibleSection)[0]
        print("  first sec %s h=%d hint=%d rows=%d" % (
            sec0.section_title, sec0.height(), sec0.sizeHint().height(),
            len(sec0.findChildren(InspectorRow))))
    dest = os.path.join(tempfile.mkdtemp(prefix="q58w_"), "got.png")
    _save_img(panel.grab().toImage(), dest)
    panel.close()
    return dest


def main():
    _boot()
    out = os.path.join(REPO, "docs", "plan", "reports", "Q58_ui_diff")
    os.makedirs(out, exist_ok=True)
    for wait in (0, 50, 200, 400):
        p = grab_u03(wait)
        h = _sha(p)
        dest = os.path.join(out, "u03d_wait%d.png" % wait)
        import shutil
        shutil.copy2(p, dest)
        print("u03d wait=%d sha=%s" % (wait, h[:12]))


if __name__ == "__main__":
    main()
