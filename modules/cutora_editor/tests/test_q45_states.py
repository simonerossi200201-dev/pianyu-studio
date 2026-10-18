# -*- coding: utf-8 -*-
"""Q45: disabled sections go fully gray; interactive controls have four states."""
from __future__ import annotations

import copy
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# HSV S of Roma gold (~0.63) and stroke red (~0.87) sit above this.
# Disabled mix of those colors against bg2 lands around 0.10–0.17.
SAT_MAX = 0.42
SAT_ON_MIN = 0.50
STROKE_EM = 0.12


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q45-states"])
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
            "shadow": {"sigma_em": 0, "alpha": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _section(panel, title):
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") == title:
            return sec
    return None


def _max_sat(img):
    """HSV saturation of opaque pixels, 0–1. Uses (max-min)/max."""
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
    # ARGB32 on little-endian is BGRA in memory.
    b, g, r, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.zeros_like(mx)
    np.divide(mx - mn, mx, out=sat, where=mx > 0)
    sat = np.where(a > 8, sat, 0.0)
    return float(sat.max()) if sat.size else 0.0


def _edge_pixels(img):
    w, h = img.width(), img.height()
    out = []
    for x in range(w):
        out.append(img.pixel(x, 0))
        out.append(img.pixel(x, h - 1))
    for y in range(h):
        out.append(img.pixel(0, y))
        out.append(img.pixel(w - 1, y))
    return tuple(out)


def _selectors(qss: str):
    text = re.sub(r"/\*.*?\*/", " ", qss, flags=re.S)
    found = []
    for chunk in text.split("}"):
        if "{" not in chunk:
            continue
        sel = chunk.split("{", 1)[0]
        for part in sel.split(","):
            s = " ".join(part.split())
            if s:
                found.append(s)
    return found


def _pseudos(selector: str):
    flags = set()
    if ":hover" in selector:
        flags.add("hover")
    if ":pressed" in selector:
        flags.add("pressed")
    if ":focus" in selector:
        flags.add("focus")
    if ":disabled" in selector:
        flags.add("disabled")
    return flags


def _states_for(selectors, token: str):
    found = set()
    if "#" in token:
        needle = "#" + token.split("#", 1)[1]
        for s in selectors:
            if needle in s:
                found |= _pseudos(s)
        return found
    pat = re.compile(r"(^|[\s>])%s(?=[:#.\s]|$)" % re.escape(token))
    for s in selectors:
        if pat.search(s):
            found |= _pseudos(s)
    return found


def test_four_states_rejects_partial():
    from cutora.ui.theme.states import four_states
    try:
        four_states(
            "QPushButton",
            hover="background: x",
            pressed="background: y",
            focus="border-color: z",
            disabled="",
        )
    except ValueError as exc:
        msg = str(exc)
        assert "disabled" in msg, msg
        assert "QPushButton" in msg, msg
        return
    raise AssertionError("four_states accepted a missing disabled rule")


def test_all_interactive_have_four_states():
    from cutora.ui.theme.qss import render
    from cutora.ui.theme.states import FOCUS_RING_SELECTORS, INTERACTIVE_CLASSES
    from cutora.ui.theme.tokens import DEFAULT_THEME_ID, load_theme

    load_theme(DEFAULT_THEME_ID)
    qss = render()
    selectors = _selectors(qss)
    assert selectors, "rendered QSS had no selectors"
    need = {"hover", "pressed", "focus", "disabled"}
    missing = []
    for cls in INTERACTIVE_CLASSES:
        got = _states_for(selectors, cls)
        lost = sorted(need - got)
        if lost:
            missing.append("%s missing %s (have %s)" % (cls, lost, sorted(got)))
    for sel in FOCUS_RING_SELECTORS:
        got = _states_for(selectors, sel)
        if "focus" not in got:
            missing.append("%s missing focus (have %s)" % (sel, sorted(got)))
        if "#" in sel:
            lost = sorted(need - got)
            if lost:
                missing.append("%s missing %s (have %s)" % (sel, lost, sorted(got)))
    assert not missing, "four-state contract:\n" + "\n".join(missing)


def test_section_off_all_gray():
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
    assert dock is not None, "test_section_off_all_gray must run inside dockSubContent"
    sec.set_collapsed(False)
    panel.resize(480, 900)
    panel.show()
    QApplication.processEvents()

    content = sec._content
    content.setEnabled(True)
    QApplication.processEvents()
    swatches = [w for w in content.findChildren(ColorSwatch) if w.isVisible()]
    sliders = content.findChildren(SliderSpin)
    assert swatches, "section has no ColorSwatch — test would miss the sat leak"
    assert sliders, "section has no SliderSpin — test would miss the gold handle"
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
        "disabled section still has sat=%.3f > %s (swatch/handle not grayed)"
        % (sat_off, SAT_MAX)
    )
    panel.close()


def test_focus_ring_visible():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QPushButton,
        QSlider,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch

    app = _qapp()
    _apply_qss(app)
    host = QWidget()
    host.setObjectName("q45FocusHost")
    lay = QVBoxLayout(host)
    btn = QPushButton("Go", host)
    tool = QToolButton(host)
    tool.setText("T")
    slider = QSlider(Qt.Orientation.Horizontal, host)
    slider.setRange(0, 10)
    slider.setValue(4)
    slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    chk = QCheckBox("On", host)
    reset = QToolButton(host)
    reset.setObjectName("inspectorReset")
    reset.setText("↺")
    reset.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    swatch = ColorSwatch("#E11D48", host)
    toggle = ToggleSwitch(True, host)
    widgets = [
        ("QPushButton", btn),
        ("QToolButton", tool),
        ("QSlider", slider),
        ("QCheckBox", chk),
        ("inspectorReset", reset),
        ("ColorSwatch", swatch),
        ("ToggleSwitch", toggle),
    ]
    for _name, w in widgets:
        w.setMinimumSize(36, 24)
        lay.addWidget(w)
    host.resize(280, 420)
    host.show()
    QApplication.processEvents()
    host.activateWindow()
    QApplication.processEvents()

    failed = []
    for name, w in widgets:
        w.clearFocus()
        QApplication.processEvents()
        before = w.grab().toImage()
        w.setFocus(Qt.FocusReason.TabFocusReason)
        QApplication.processEvents()
        assert w.hasFocus(), "%s setFocus did not take" % name
        after = w.grab().toImage()
        if _edge_pixels(before) == _edge_pixels(after):
            failed.append(name)
        w.clearFocus()
        QApplication.processEvents()
    host.close()
    assert not failed, "focus ring did not change edge pixels: %s" % failed


def test_swatch_hover():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget
    from cutora.ui.theme.tokens import current, hex_rgb
    from cutora.ui.widgets.inspector import ColorSwatch

    app = _qapp()
    _apply_qss(app)
    host = QWidget()
    lay = QHBoxLayout(host)
    dummy = QPushButton("x", host)
    dummy.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    sw = ColorSwatch("#E11D48", host)
    lay.addWidget(dummy)
    lay.addWidget(sw)
    host.show()
    dummy.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert dummy.hasFocus(), "dummy did not take focus"
    assert not sw.hasFocus(), "swatch auto-focused; hover would be indistinguishable from the focus ring"
    before = sw.grab().toImage()
    ev = QEnterEvent(QPointF(4, 4), QPointF(4, 4), QPointF(4, 4))
    sw.enterEvent(ev)
    QApplication.processEvents()
    assert sw._hover is True, "enterEvent did not set ColorSwatch._hover"
    after = sw.grab().toImage()
    ar, ag, ab = hex_rgb(current().accent)
    lr, lg, lb = hex_rgb(current().line)
    # Mid-left border pixel.
    y = max(1, after.height() // 2)
    pix = after.pixelColor(0, y)
    dist_accent = abs(pix.red() - ar) + abs(pix.green() - ag) + abs(pix.blue() - ab)
    dist_line = abs(pix.red() - lr) + abs(pix.green() - lg) + abs(pix.blue() - lb)
    assert dist_accent < dist_line, (
        "hover border %s closer to line than accent (d_accent=%s d_line=%s)"
        % ((pix.red(), pix.green(), pix.blue()), dist_accent, dist_line)
    )
    assert dist_accent <= 24, (
        "hover border %s not accent %s" % ((pix.red(), pix.green(), pix.blue()), (ar, ag, ab))
    )
    assert before.pixel(0, y) != after.pixel(0, y), "enterEvent did not change the border"
    host.close()


def test_q45_slider_thumb_follows_load():
    from cutora.ui.widgets.inspector import SliderSpin

    app = _qapp()
    _apply_qss(app)
    panel = _panel()
    sp = panel.fx_stroke_width_em
    assert isinstance(sp, SliderSpin), type(sp).__name__
    hits = []
    sp.valueChanged.connect(lambda v: hits.append(float(v)))
    style = _stroke_style(width_em=STROKE_EM, color="#E11D48")
    panel.load_data(copy.deepcopy(style))
    want = int(round(STROKE_EM * sp._scale))
    assert abs(float(sp.spin.value()) - STROKE_EM) < 0.02, sp.spin.value()
    assert sp.slider.value() == want, (
        "slider thumb stayed at %s after load_data(width_em=%s); want %s"
        % (sp.slider.value(), STROKE_EM, want)
    )
    assert hits == [], "load_data emitted valueChanged: %s" % hits
    panel.close()
