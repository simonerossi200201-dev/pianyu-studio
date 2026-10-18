#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Measure SubtitleStylePanel natural height vs the 1100px golden lock."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from tools.ui_golden import _boot, _glass_task, _save_img  # noqa: E402


def main():
    _boot()
    from PySide6.QtWidgets import QApplication, QScrollArea
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.widgets.inspector import InspectorRow
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    task = _glass_task()
    panel = SubtitleStylePanel()
    panel.load_data(task.get("style") or {})
    for sec in panel.findChildren(CollapsibleSection):
        sec.set_collapsed(False)
    panel.show()
    QApplication.processEvents()

    print("sizeHint", panel.sizeHint().width(), panel.sizeHint().height())
    print("minHint", panel.minimumSizeHint().width(), panel.minimumSizeHint().height())

    scroll = panel.findChild(QScrollArea)
    inner = scroll.widget() if scroll else None
    print("scroll viewport", scroll.viewport().width(), scroll.viewport().height() if scroll else None)
    print("inner sizeHint", inner.sizeHint().width(), inner.sizeHint().height() if inner else None)
    print("inner minHint", inner.minimumSizeHint().width(), inner.minimumSizeHint().height() if inner else None)
    print("inner geom", inner.width(), inner.height() if inner else None)
    print("widgetResizable", scroll.widgetResizable() if scroll else None)

    print("--- sections after expand, before fixed size ---")
    for sec in panel.findChildren(CollapsibleSection):
        rows = sec.findChildren(InspectorRow)
        print("  %-16s h=%4d min=%4d hint=%4d collapsed=%s rows=%d contentH=%d" % (
            (sec.section_title or "")[:16],
            sec.height(), sec.minimumSizeHint().height(), sec.sizeHint().height(),
            sec._is_collapsed, len(rows),
            sec._content.height() if hasattr(sec, "_content") else -1))

    # Natural: give inner its hint
    if inner is not None:
        hint_h = max(int(inner.sizeHint().height()), 1)
        panel.setFixedSize(480, min(hint_h + 40, 4000))
        panel.show()
        QApplication.processEvents()
        dest = os.path.join(REPO, "docs", "plan", "reports", "Q58_ui_diff", "u07_natural.png")
        _save_img(panel.grab().toImage(), dest)
        print("wrote natural grab", dest, "panel", panel.width(), panel.height(),
              "inner", inner.width(), inner.height(), "hint", inner.sizeHint().height())

    panel.setFixedSize(480, 1100)
    QApplication.processEvents()
    print("--- after setFixedSize 480x1100 ---")
    print("inner geom", inner.width(), inner.height(), "hint", inner.sizeHint().height(),
          "min", inner.minimumSizeHint().height())
    for sec in panel.findChildren(CollapsibleSection):
        rows = sec.findChildren(InspectorRow)
        print("  %-16s h=%4d hint=%4d rows=%d contentH=%d" % (
            (sec.section_title or "")[:16],
            sec.height(), sec.sizeHint().height(), len(rows),
            sec._content.height() if hasattr(sec, "_content") else -1))
    panel.close()


if __name__ == "__main__":
    main()
