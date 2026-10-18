# -*- coding: utf-8 -*-
"""Panel color must reach the pixels. Hash baselines only say 'changed'."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGENTA = "#FF00FF"
TOL = 2


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["panel-color"])


def _panel(style):
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    panel = SubtitleStylePanel()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    return panel


def _paint(style, t=0.4, text="HELLO", words=None):
    from PySide6.QtGui import QImage, QPainter, QColor
    from cutora.render.textfx.pipeline import draw
    img = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    if words is None:
        words = [{"word": text, "start": 0.0, "end": 1.0}]
    event = {
        "text": text,
        "start": 0.0,
        "end": 1.0,
        "words": words,
        "lines": [text],
    }
    draw(p, event, style, float(t), 1080, 1920)
    p.end()
    return img


def _count_near(img, hex_color, tol=TOL, min_alpha=240):
    from PySide6.QtGui import QColor
    want = QColor(hex_color)
    wr, wg, wb = want.red(), want.green(), want.blue()
    n = 0
    for y in range(0, img.height(), 1):
        for x in range(0, img.width(), 1):
            c = img.pixelColor(x, y)
            if c.alpha() < min_alpha:
                continue
            if (
                abs(c.red() - wr) <= tol
                and abs(c.green() - wg) <= tol
                and abs(c.blue() - wb) <= tol
            ):
                n += 1
    return n


def _mock_color(monkeypatch, hex_color):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    from cutora.ui.panels import subtitle_style
    def _get(*a, **k):
        return QColor(hex_color)
    monkeypatch.setattr(QColorDialog, "getColor", _get)
    monkeypatch.setattr(subtitle_style, "QColorDialog", QColorDialog)
    monkeypatch.setattr(subtitle_style.QColorDialog, "getColor", _get)


def test_line_bar_uses_panel_color(monkeypatch):
    _qapp()
    style = {
        "fontsize": 80, "font_family": "Arimo", "pos_x": 540, "pos_y": 960,
        "color_text": "#FFFFFF",
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"}},
    }
    panel = _panel(style)
    panel.sub_adv_bar.setChecked(True)
    panel.on_change()
    chip = getattr(panel, "fx_line_bar_color", None)
    assert chip is not None
    chip.set_hex(MAGENTA + "FF")
    got = ((style.get("fx") or {}).get("line_bar") or {}).get("color")
    assert str(got).upper().startswith(MAGENTA), got
    img = _paint(style)
    n = _count_near(img, MAGENTA)
    assert n > 40, "line_bar region must be panel color %s ±%d (got %d px)" % (
        MAGENTA, TOL, n)


def test_follow_word_uses_panel_color(monkeypatch):
    _qapp()
    style = {
        "fontsize": 80, "font_family": "Arimo", "pos_x": 540, "pos_y": 960,
        "color_text": "#FFFFFF",
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"}},
    }
    panel = _panel(style)
    panel.sub_dyn.setChecked(True)
    panel.sub_dyn_op.setValue(255)
    panel.on_change()
    _mock_color(monkeypatch, MAGENTA)
    panel.pick_box_highlight_color()
    got = ((style.get("fx") or {}).get("box_highlight") or {}).get("color")
    assert str(got).upper() == MAGENTA, got
    img = _paint(style)
    n = _count_near(img, MAGENTA)
    assert n > 40, "follow_word box must be panel color %s ±%d (got %d px)" % (
        MAGENTA, TOL, n)
