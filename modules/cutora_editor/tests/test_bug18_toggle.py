# -*- coding: utf-8 -*-
"""BUG-18: ToggleSwitch mouseClick flips checked, style, and pixels."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_stroke_toggle_click_flips_style_and_pixels():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    QApplication.instance() or QApplication(["bug18-toggle"])
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.collapsible import ToggleSwitch
    style = {
        "fontsize": 72, "font_family": "Arimo",
        "use_stroke": False, "border_width": 2,
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"}},
    }
    panel = SubtitleStylePanel()
    panel.resize(280, 900)
    panel.show()
    QApplication.processEvents()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    panel.sub_strk.setChecked(False)
    QApplication.processEvents()
    assert isinstance(panel.sub_strk, ToggleSwitch)
    assert panel.sub_strk.isChecked() is False
    before = panel.sub_strk.grab().toImage()
    QTest.mouseClick(panel.sub_strk, Qt.MouseButton.LeftButton)
    QTest.qWait(160)
    QApplication.processEvents()
    assert panel.sub_strk.isChecked() is True
    panel.on_change()
    stroke = ((style.get("fx") or {}).get("stroke") or {})
    assert float(stroke.get("width_em") or 0) > 0, stroke
    after = panel.sub_strk.grab().toImage()
    changed = False
    for y in range(after.height()):
        for x in range(after.width()):
            if after.pixel(x, y) != before.pixel(x, y):
                changed = True
                break
        if changed:
            break
    assert changed, "toggle pixels did not change gold/gray"
    out = os.path.join(REPO, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)
    before.save(os.path.join(out, "BUG18_toggle_off.png"))
    after.save(os.path.join(out, "BUG18_toggle_on.png"))
    assert panel.sub_strk.width() == 36
    assert panel.sub_strk.height() == 20
