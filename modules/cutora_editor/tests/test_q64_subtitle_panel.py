# -*- coding: utf-8 -*-
"""Q64: drop dead subtitle controls, flatten group boxes, gray disabled, grid."""
from __future__ import annotations

import copy
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
# HSV S of Roma gold (~0.63) and stroke red (~0.87). Warm bg2 sits ~0.34;
# disabled mix of accent against bg2 lands around 0.10–0.19.
SAT_MAX = 0.42
SAT_ON_MIN = 0.50
STROKE_EM = 0.12


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q64-subtitle"])
    app.setStyle(QStyleFactory.create("Fusion"))
    return app


def _apply_qss(app):
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme
    load_theme(DEFAULT_THEME_ID)
    app.setStyleSheet(render())


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _stroke_style(width_em=STROKE_EM, color="#E11D48"):
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": {"width_em": width_em, "color": color, "join": "round"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"enabled": False},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _section(panel, title):
    from cutora.ui.widgets.collapsible import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") == title:
            return sec
    return None


def _max_sat(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    if w <= 0 or h <= 0:
        return 0.0
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4].copy()
    arr = arr.reshape((h, w, 4)).astype(np.float32)
    b, g, r, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.zeros_like(mx)
    np.divide(mx - mn, mx, out=sat, where=mx > 0)
    sat = np.where(a > 8, sat, 0.0)
    return float(sat.max()) if sat.size else 0.0


def _norm_label(text: str) -> str:
    return re.sub(r"[\s:：]+", "", str(text or ""))


def test_q64_no_dead_sections():
    from PySide6.QtWidgets import QApplication
    from cutora.ui.widgets.collapsible import CollapsibleSection

    _qapp()
    panel = _panel()
    QApplication.processEvents()
    titles = [
        getattr(sec, "section_title", "")
        for sec in panel.findChildren(CollapsibleSection)
    ]
    assert "音谱动画" not in titles, titles
    assert "多层扩展描边" not in titles, titles
    assert getattr(panel, "fx_bg_grad_enabled", None) is None
    names = [w.objectName() for w in panel.findChildren(object) if hasattr(w, "objectName")]
    assert "fx_bg_grad_enabled" not in names
    panel.close()


def test_q64_no_duplicate_labels():
    from PySide6.QtWidgets import QApplication
    from cutora.ui.widgets.inspector import InspectorRow

    _qapp()
    panel = _panel()
    QApplication.processEvents()
    seen = {}
    dupes = []
    for row in panel.findChildren(InspectorRow):
        key = _norm_label(row.full_label_text())
        if not key:
            continue
        if key in seen:
            dupes.append((key, seen[key], row.full_label_text()))
        else:
            seen[key] = row.full_label_text()
    panel.close()
    assert not dupes, "duplicate inspector labels: %s" % dupes


def test_q64_no_groupbox():
    from PySide6.QtWidgets import QApplication, QGroupBox

    src = open(
        os.path.join(CUTORA, "ui", "panels", "subtitle_style.py"),
        encoding="utf-8",
    ).read()
    count = src.count("QGroupBox(")
    assert count == 0, "subtitle_style.py still has %d QGroupBox(" % count

    _qapp()
    panel = _panel()
    QApplication.processEvents()
    leftover = panel.findChildren(QGroupBox)
    panel.close()
    assert leftover == [], [w.objectName() or w.title() for w in leftover]


def test_q64_section_off_really_gray():
    """Disabled 描边 in the live dockSubContent inspector must fully desaturate.

    Gold slider handles under QWidget#dockSubContent used to keep accent
    because the ID selector beat QSlider:disabled. This grabs that path.
    """
    from PySide6.QtWidgets import QApplication
    from cutora.ui.widgets.inspector import ColorSwatch, SliderSpin

    app = _qapp()
    _apply_qss(app)
    panel = _panel()
    style = _stroke_style(width_em=STROKE_EM, color="#E11D48")
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    if getattr(panel, "sub_strk", None) is not None:
        panel.sub_strk.setChecked(True)
    sec = _section(panel, "描边")
    assert sec is not None, "描边 section missing"
    dock = None
    cur = sec
    while cur is not None:
        if (cur.objectName() or "") == "dockSubContent":
            dock = cur
            break
        cur = cur.parentWidget()
    assert dock is not None, "描边 is not inside dockSubContent"
    sec.set_collapsed(False)
    panel.resize(480, 900)
    panel.show()
    QApplication.processEvents()

    content = sec._content
    content.setEnabled(True)
    QApplication.processEvents()
    swatches = [w for w in content.findChildren(ColorSwatch) if w.isVisible()]
    sliders = content.findChildren(SliderSpin)
    assert swatches, "section has no ColorSwatch — sat leak would be missed"
    assert sliders, "section has no SliderSpin — gold handle would be missed"
    on_img = content.grab().toImage()
    sat_on = _max_sat(on_img)
    assert sat_on > SAT_ON_MIN, (
        "on-state section sat=%.3f; expected a vivid swatch/handle (>%s)"
        % (sat_on, SAT_ON_MIN)
    )

    panel.sub_strk.setChecked(False)
    QApplication.processEvents()
    assert content.isEnabled() is False, "gray_content did not disable body"
    off_img = content.grab().toImage()
    sat_off = _max_sat(off_img)
    assert sat_off <= SAT_MAX, (
        "disabled dockSubContent 描边 still has sat=%.3f > %s "
        "(gold slider handle or swatch not grayed)"
        % (sat_off, SAT_MAX)
    )
    handle_off = _max_sat(sliders[0].slider.grab().toImage())
    assert handle_off <= SAT_MAX, (
        "disabled slider handle sat=%.3f > %s" % (handle_off, SAT_MAX)
    )
    from cutora.ui.theme.qss import render
    qss = render()
    need = (
        "QWidget#dockSubContent QSlider::handle:horizontal:disabled",
        "QWidget#dockSubContent QSlider::sub-page:horizontal:disabled",
        "QWidget#dockSubContent QSpinBox:disabled",
        "QWidget#dockSubContent QLabel:disabled",
        "QWidget#dockSubContent QPushButton:disabled",
    )
    missing = [sel for sel in need if sel not in qss]
    assert not missing, "dockSubContent disabled QSS missing: %s" % missing
    panel.close()


def test_q64_row_height_uniform():
    from PySide6.QtWidgets import QApplication
    # Q83: inspector rows use INSPECTOR_ROW_H (32); ROW_H (24) is for the
    # timeline / top bar only.
    from PySide6.QtWidgets import QLabel
    from cutora.ui.theme.tokens import INSPECTOR_ROW_H as ROW_H
    from cutora.ui.widgets.inspector import InspectorRow

    app = _qapp()
    _apply_qss(app)
    panel = _panel()
    panel.resize(480, 1400)
    panel.show()
    QApplication.processEvents()
    from cutora.ui.widgets.collapsible import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        sec.set_collapsed(False)
    QApplication.processEvents()

    rows = [r for r in panel.findChildren(InspectorRow) if r.isVisible() and r.height() > 0]
    assert rows, "no visible InspectorRow"
    heights = sorted({int(r.height()) for r in rows})
    # Single-line controls lock to ROW_H. Tall fields (word list, color stops,
    # wrapped hints) keep min ROW_H but may grow; they are excluded here.
    standard = []
    for row in rows:
        field = getattr(row, "field", None)
        hint_h = 0
        if field is not None:
            hint_h = int(field.sizeHint().height())
        if hint_h > ROW_H + 4:
            continue
        if isinstance(field, QLabel) and field.wordWrap():
            continue  # wrapped hint text sizes to its lines
        standard.append(row)
    assert standard, "no single-line InspectorRow to measure"
    got = sorted({int(r.height()) for r in standard})
    panel.close()
    assert got == [ROW_H], (
        "InspectorRow heights %s (all visible %s); expected [%s]"
        % (got, heights, ROW_H)
    )
