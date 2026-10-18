# -*- coding: utf-8 -*-
"""BUG-14: follow-word / follow-sentence; no bare enable QCheckBox."""
from __future__ import annotations

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_bare_enable_checkbox():
    import glob
    pat = re.compile(r"QCheckBox\(.*(启用|开启|AI)")
    hits = []
    panels = glob.glob(os.path.join(REPO, "cutora", "ui", "panels", "*.py"))
    for path in panels:
        text = open(path, encoding="utf-8").read()
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append("%s:%d:%s" % (os.path.basename(path), i, line.strip()))
    assert hits == [], hits


def _follow_on(fx) -> bool:
    fw = (fx or {}).get("follow_word") if isinstance(fx, dict) else None
    if isinstance(fw, dict):
        tint = fw.get("tint") if isinstance(fw.get("tint"), dict) else {}
        box = fw.get("box") if isinstance(fw.get("box"), dict) else {}
        return bool(tint.get("enabled") or box.get("enabled"))
    return bool(fw)


def test_follow_flags_drive_fx(monkeypatch):
    from cutora.render.textfx.spec import from_style
    style = {"fx": {"follow_word": True, "follow_sentence": True, "anim": {"loop": "none"}}}
    spec = from_style(style)
    from cutora.render.textfx.anim import _anim_ids
    ids = _anim_ids(spec.anim.loop)
    assert "box_highlight" in ids
    assert "line_bar" in ids


def test_deprecated_keys_include_dead_bar():
    from cutora.subtitle.style_schema import CANONICAL_STYLE_KEYS, DEPRECATED_UNPAINTED_KEYS
    for k in (
        "dynamic_box", "adv_bar_enabled", "adv_bar_color",
        "color_high_bg", "opacity_high_bg", "dynamic_radius",
        "dyn_box_anim", "dyn_box_stroke_width", "dyn_box_font_enabled",
        "dyn_box_image_enabled",
    ):
        assert k in DEPRECATED_UNPAINTED_KEYS
        assert k not in CANONICAL_STYLE_KEYS


def _paint_sub(style, t=0.4, text="HELLO", words=None):
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
        "_style": style,
    }
    draw(p, event, style, float(t), 1080, 1920)
    p.end()
    return img


def _has_bar_pixels(img, color_hex, y0=0):
    from PySide6.QtGui import QColor
    want = QColor(color_hex)
    wr, wg, wb = want.red(), want.green(), want.blue()
    found = 0
    for y in range(max(0, y0), img.height()):
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, y)
            if c.alpha() < 40:
                continue
            if abs(c.red() - wr) < 40 and abs(c.green() - wg) < 40 and abs(c.blue() - wb) < 40:
                found += 1
                if found > 20:
                    return True
    return False


def test_follow_sentence_knob_reaches_paint(qapp=None):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["bug14"])
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    style = {
        "fontsize": 80, "font_family": "Arimo", "pos_x": 540, "pos_y": 960,
        "color_text": "#FFFFFF", "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"}},
    }
    panel = SubtitleStylePanel()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    panel.sub_adv_bar.setChecked(True)
    panel.on_change()
    assert style.get("fx", {}).get("follow_sentence") is True
    color = (style.get("fx") or {}).get("line_bar", {}).get("color") or "#F2C14E"
    on = _paint_sub(style)
    panel.sub_adv_bar.setChecked(False)
    panel.on_change()
    assert style.get("fx", {}).get("follow_sentence") is False
    off = _paint_sub(style)
    out = os.path.join(REPO, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)
    on.save(os.path.join(out, "BUG14_pair_sentence_on.png"))
    off.save(os.path.join(out, "BUG14_pair_sentence_off.png"))
    assert _has_bar_pixels(on, color), "follow-sentence ON should paint a bar"
    assert not _has_bar_pixels(off, color), "follow-sentence OFF should not paint a bar"


def test_follow_word_knob_reaches_paint(qapp=None):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["bug14w"])
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    style = {
        "fontsize": 80, "font_family": "Arimo", "pos_x": 540, "pos_y": 960,
        "color_text": "#FFFFFF", "color_high_bg": "#22D3EE",
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"},
              "emphasis": {"color": "#22D3EE"}},
    }
    panel = SubtitleStylePanel()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    panel.sub_dyn.setChecked(True)
    panel.on_change()
    assert _follow_on(style.get("fx") or {})
    on = _paint_sub(style)
    panel.sub_dyn.setChecked(False)
    panel.on_change()
    assert not _follow_on(style.get("fx") or {})
    off = _paint_sub(style)
    out = os.path.join(REPO, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)
    on.save(os.path.join(out, "BUG14_pair_word_on.png"))
    off.save(os.path.join(out, "BUG14_pair_word_off.png"))
    assert _has_bar_pixels(on, "#22D3EE") or _has_bar_pixels(on, "#F2C14E")
    assert not _has_bar_pixels(off, "#22D3EE")


def _box_centroid_x(img, color_hex):
    from PySide6.QtGui import QColor
    want = QColor(color_hex)
    wr, wg, wb = want.red(), want.green(), want.blue()
    xs, n = 0, 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.alpha() < 40:
                continue
            if abs(c.red() - wr) < 50 and abs(c.green() - wg) < 50 and abs(c.blue() - wb) < 50:
                xs += x
                n += 1
    return (xs / n) if n else None


def test_follow_word_box_moves_hello_world(qapp=None):
    """BUG14_pair_word: two words, box at t=0.3 then t=0.8."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["bug14ww"])
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    style = {
        "fontsize": 80, "font_family": "Arimo", "pos_x": 540, "pos_y": 960,
        "color_text": "#FFFFFF",
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"},
              "emphasis": {"color": "#22D3EE"},
              "box_highlight": {"color": "#22D3EE", "alpha": 0.7, "radius_em": 0.2}},
    }
    panel = SubtitleStylePanel()
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    panel.sub_dyn.setChecked(True)
    panel.on_change()
    assert _follow_on(style.get("fx") or {})
    words = [
        {"word": "HELLO", "start": 0.0, "end": 0.5},
        {"word": "WORLD", "start": 0.5, "end": 1.0},
    ]
    a = _paint_sub(style, t=0.3, text="HELLO WORLD", words=words)
    b = _paint_sub(style, t=0.8, text="HELLO WORLD", words=words)
    out = os.path.join(REPO, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)
    a.save(os.path.join(out, "BUG14_pair_word_on.png"))
    b.save(os.path.join(out, "BUG14_pair_word_off.png"))
    a.save(os.path.join(out, "BUG14_pair_word_t03.png"))
    b.save(os.path.join(out, "BUG14_pair_word_t08.png"))
    xa = _box_centroid_x(a, "#22D3EE")
    xb = _box_centroid_x(b, "#22D3EE")
    assert xa is not None and xb is not None, (xa, xb)
    assert xb > xa + 20, (xa, xb)


def test_hidden_widgets_write_no_deprecated_keys(qapp=None):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["bug14-collect"])
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
        encoding="utf-8").read()
    assert "self.sub_dyn_font_en" not in src
    assert "self.sub_dyn_font " not in src
    panel = SubtitleStylePanel()
    style = {
        "fontsize": 72, "font_family": "Arimo",
        "fx": {"anim": {"loop": "none"}, "bg": {"type": "none"}},
    }
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    got = panel.collect()
    hits = sorted(k for k in got if k in DEPRECATED_UNPAINTED_KEYS)
    assert hits == [], hits

