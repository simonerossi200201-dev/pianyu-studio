# -*- coding: utf-8 -*-
"""Q25: Resolve-style inspector layout. Tests written red before the widgets."""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PIXEL_CALLS = (
    "setFixedWidth",
    "setFixedHeight",
    "setFixedSize",
    "setMinimumWidth",
    "setMinimumHeight",
    "setMaximumWidth",
    "setMaximumHeight",
    "setContentsMargins",
    "setSpacing",
)
_CALL_RE = re.compile(
    r"\b(" + "|".join(_PIXEL_CALLS) + r")\s*\(([^)]*)\)"
)
_INT_RE = re.compile(r"(?<![\w.])(-?\d+)(?![\w.])")
_SPEC_PATH = os.path.join(REPO, "docs", "plan", "UI_INSPECTOR_SPEC.md")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t25-inspector"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _expand(panel):
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    from PySide6.QtWidgets import QApplication
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "set_collapsed"):
            sec.set_collapsed(False)
    QApplication.processEvents()


def test_t25_spec_lists_tokens():
    from cutora.ui.theme import tokens as tok
    assert os.path.isfile(_SPEC_PATH), _SPEC_PATH
    text = open(_SPEC_PATH, encoding="utf-8").read()
    names = [
        n for n in dir(tok)
        if n.startswith("INSPECTOR_") and n == n.upper()
    ]
    assert names, "expected INSPECTOR_* tokens"
    missing = [n for n in names if n not in text]
    assert missing == [], missing


def test_t25_no_hardcoded_px():
    """inspector.py / fx_fields.py size APIs must use tokens, not bare px > 1."""
    hits = []
    for rel in (
        os.path.join("cutora", "ui", "widgets", "inspector.py"),
        os.path.join("cutora", "ui", "panels", "fx_fields.py"),
    ):
        path = os.path.join(REPO, rel)
        assert os.path.isfile(path), path
        src = open(path, encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.split("#", 1)[0]
            for match in _CALL_RE.finditer(stripped):
                args = match.group(2)
                for num in _INT_RE.findall(args):
                    if abs(int(num)) <= 1:
                        continue
                    hits.append("%s:%d %s(%s)" % (rel, i, match.group(1), args.strip()))
    assert hits == [], hits


def test_t25_control_max_width():
    from cutora.ui.theme.tokens import (
        INSPECTOR_COLOR_MAX_W,
        INSPECTOR_COMBO_MAX_W,
        INSPECTOR_PANEL_W,
        INSPECTOR_SPIN_WIDTH,
        INSPECTOR_SWATCH_PX,
    )
    from cutora.ui.widgets.inspector import ColorSwatch, SliderSpin
    from PySide6.QtWidgets import (
        QAbstractSpinBox,
        QApplication,
        QComboBox,
        QPushButton,
    )

    _qapp()
    panel = _panel()
    _expand(panel)
    panel.setFixedWidth(INSPECTOR_PANEL_W)
    panel.show()
    QApplication.processEvents()
    panel.resize(INSPECTOR_PANEL_W, panel.height())
    QApplication.processEvents()

    too_wide = []
    for combo in panel.findChildren(QComboBox):
        if not combo.isVisible():
            continue
        if combo.width() > INSPECTOR_COMBO_MAX_W:
            too_wide.append(
                ("combo", combo.objectName() or combo.__class__.__name__,
                 combo.width())
            )
    for spin in panel.findChildren(QAbstractSpinBox):
        if not spin.isVisible():
            continue
        if spin.width() > INSPECTOR_SPIN_WIDTH:
            too_wide.append(
                ("spin", spin.objectName() or spin.__class__.__name__,
                 spin.width())
            )
    for sw in panel.findChildren(ColorSwatch):
        if not sw.isVisible():
            continue
        if sw.width() > INSPECTOR_SWATCH_PX or sw.height() > INSPECTOR_SWATCH_PX:
            too_wide.append(("swatch", sw.objectName(), sw.width(), sw.height()))
    for btn in panel.findChildren(QPushButton):
        if not btn.isVisible():
            continue
        if (btn.objectName() or "") != "swatchBtn":
            continue
        if btn.width() > INSPECTOR_COLOR_MAX_W:
            too_wide.append(("swatchBtn", btn.text(), btn.width()))
    panel.close()
    assert too_wide == [], too_wide
    _ = SliderSpin


def test_t25_color_swatch_shows_color():
    from cutora.ui.theme.tokens import INSPECTOR_SWATCH_PX
    from cutora.ui.widgets.inspector import ColorSwatch
    from PySide6.QtWidgets import QApplication

    _qapp()
    sw = ColorSwatch("#FF0000")
    sw.show()
    QApplication.processEvents()
    img = sw.grab().toImage()
    assert img.width() >= 1 and img.height() >= 1, (img.width(), img.height())
    cx = min(INSPECTOR_SWATCH_PX // 2, img.width() // 2)
    cy = min(INSPECTOR_SWATCH_PX // 2, img.height() // 2)
    pix = img.pixelColor(cx, cy)
    assert abs(pix.red() - 255) <= 8, (pix.red(), pix.green(), pix.blue())
    assert abs(pix.green() - 0) <= 8, (pix.red(), pix.green(), pix.blue())
    assert abs(pix.blue() - 0) <= 8, (pix.red(), pix.green(), pix.blue())
    sw.close()


def test_t25_reset_button():
    from cutora.ui.widgets.inspector import inspector_row_of
    from PySide6.QtWidgets import QApplication, QToolButton

    _qapp()
    panel = _panel()
    style = {
        "font_family": "Arimo",
        "fontsize": 96,
        "fx": {
            "stroke": {"width_em": 0.05, "color": "#E11D48", "join": "round"},
            "glow": {"sigma_em": 0.1, "strength": 0.4, "color": "#22D3EE"},
        },
    }
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = dict(style)
    if getattr(panel, "sub_strk", None) is not None:
        panel.sub_strk.setChecked(True)
    if getattr(panel, "sub_glow_en", None) is not None:
        panel.sub_glow_en.setChecked(True)
    _expand(panel)
    panel.show()
    QApplication.processEvents()

    glow = panel.fx_glow_strength
    sigma = panel.fx_glow_sigma_em
    glow.setValue(1.35)
    sigma.setValue(0.4)
    assert abs(float(glow.value()) - 1.35) < 1e-6
    assert abs(float(sigma.value()) - 0.4) < 1e-6

    row = inspector_row_of(glow)
    assert row is not None, "InspectorRow missing for fx_glow_strength"
    btn = row.findChild(QToolButton, "inspectorReset")
    assert btn is not None, "reset button missing"
    btn.click()
    QApplication.processEvents()

    assert abs(float(glow.value()) - 0.8) < 1e-6, glow.value()
    assert abs(float(sigma.value()) - 0.4) < 1e-6, sigma.value()
    panel.close()


def test_no_orphan_labels():
    """BUG-23 lock: InspectorRow label visible ⇒ widget visible."""
    from cutora.ui.widgets.inspector import InspectorRow
    from PySide6.QtWidgets import QApplication

    _qapp()
    panel = _panel()
    panel.load_data({"fontsize": 72, "fx": {"bg": {"type": "glass"}}})
    _expand(panel)
    panel.show()
    QApplication.processEvents()
    orphans = []
    for row in panel.findChildren(InspectorRow):
        lab = getattr(row, "label", None)
        field = getattr(row, "field", None)
        if lab is None or field is None:
            continue
        if lab.isVisible() and (lab.text() or "").strip():
            if not field.isVisible():
                orphans.append(lab.text())
    panel.close()
    assert orphans == [], orphans
