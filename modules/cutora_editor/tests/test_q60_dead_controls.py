# -*- coding: utf-8 -*-
"""Q60: first-touch subtitle controls actually change the picture / reset."""
from __future__ import annotations

import copy
import hashlib
import logging
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
T_S = 0.55
TEXT = "HELLO WORLD"
WORDS = [
    {"word": "HELLO", "start": 0.0, "end": 0.8},
    {"word": "WORLD", "start": 0.8, "end": 1.8},
]


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q60-dead-controls"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    _fonts()
    return SubtitleStylePanel()


def _event(text=TEXT, words=None):
    words = WORDS if words is None else words
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": list(words),
    }


def _task(style, text=TEXT, words=None):
    from cutora.model.timeline import Timeline
    return {
        "style": copy.deepcopy(style),
        "segments": [_event(text=text, words=words)],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }


def _bgra(img):
    from cutora.render.textfx.raster import as_bgra
    owned, view = as_bgra(img)
    return owned, view


def _preview_frame(style, t=T_S, text=TEXT, words=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer

    task = _task(style, text=text, words=words)
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#142028"))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(
            p, task, CANVAS_W, CANVAS_H, t, render_order=["subs"])
    finally:
        p.end()
    return img


def _export_frame(style, t=T_S, text=TEXT, words=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    task = _task(style, text=text, words=words)

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#142028"))

    return ExportRenderer.render_frame(
        task, CANVAS_W, CANVAS_H, t, draw_background=_bg)


def _mad(a, b):
    va = _bgra(a)[1].astype(np.int16)
    vb = _bgra(b)[1].astype(np.int16)
    return float(np.mean(np.abs(va - vb)))


def _max_abs(a, b):
    va = _bgra(a)[1].astype(np.int16)
    vb = _bgra(b)[1].astype(np.int16)
    return int(np.abs(va - vb).max())


def _md5(img):
    view = _bgra(img)[1]
    return hashlib.md5(view.tobytes()).hexdigest()


def _is_descendant(widget, ancestor):
    cur = widget
    while cur is not None:
        if cur is ancestor:
            return True
        cur = cur.parentWidget()
    return False


def test_q60_font_changes_pixels():
    """Apply a builtin pack, change 基本排版 → 字体, preview and export pixels move."""
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack

    _qapp()
    _fonts()
    panel = _panel()
    pack = load_pack(os.path.join(builtin_dir(), "chant"))
    task = {"style": {}, "segments": []}
    apply_pack_to_task(task, pack, apply_animation=False)
    style = task["style"]
    pack_body = ((style.get("fx") or {}).get("fonts") or {}).get("body")
    assert pack_body, "chant pack must set fx.fonts.body (this is the dead-dropdown bug)"
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)

    before_p = _preview_frame(copy.deepcopy(panel.collect()))
    before_e = _export_frame(copy.deepcopy(panel.collect()))
    assert _mad(before_p, before_e) < 2.5, _mad(before_p, before_e)

    want = "Roboto"
    assert panel.sub_font.findText(want) >= 0, "Roboto missing from 字体 combo"
    assert panel.sub_font.currentText() != want, (
        panel.sub_font.currentText(), want)
    panel.sub_font.setCurrentText(want)
    got = panel.collect()
    body = ((got.get("fx") or {}).get("fonts") or {}).get("body")
    assert body == want, body
    after_p = _preview_frame(copy.deepcopy(got))
    after_e = _export_frame(copy.deepcopy(got))
    panel.close()
    assert _md5(after_p) != _md5(before_p), (
        "preview pixels unchanged after 字体 — fonts.body still shadows the dropdown")
    assert _md5(after_e) != _md5(before_e), "export pixels unchanged after 字体"
    assert _max_abs(before_p, after_p) > 20, _max_abs(before_p, after_p)
    assert _max_abs(before_e, after_e) > 20, _max_abs(before_e, after_e)
    assert _mad(after_p, after_e) < 2.5, _mad(after_p, after_e)


def test_q60_rotation_changes_pixels():
    """sub_rot writes style.rotation; textfx rotates the ROI around the text center."""
    _qapp()
    _fonts()
    style0 = {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "rotation": 0,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"enabled": False},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "fonts": {"body": "Arimo"},
        },
    }
    style1 = copy.deepcopy(style0)
    style1["rotation"] = 25
    p0 = _preview_frame(style0)
    e0 = _export_frame(style0)
    p1 = _preview_frame(style1)
    e1 = _export_frame(style1)
    assert _mad(p0, e0) < 2.5, _mad(p0, e0)
    assert _mad(p1, e1) < 2.5, _mad(p1, e1)
    assert _md5(p1) != _md5(p0), "preview rotation is a no-op"
    assert _md5(e1) != _md5(e0), "export rotation is a no-op"
    assert _max_abs(p0, p1) > 20, _max_abs(p0, p1)
    assert _max_abs(e0, e1) > 20, _max_abs(e0, e1)


def test_q60_box_offset_moves_block():
    """box_highlight.offset_y_em shifts the highlight block's vertical centroid."""
    _qapp()
    _fonts()

    def _style(off):
        return {
            "font_family": "Arimo",
            "fontsize": 72,
            "bold": True,
            "pos_x": 360,
            "pos_y": 640,
            "fx": {
                "fill": {"type": "solid", "colors": ["#F4EBD8"]},
                "stroke": {"width_em": 0},
                "glow": {"sigma_em": 0, "strength": 0},
                "shadow": {"enabled": False},
                "bg": {"type": "none"},
                "anim": {"in": "none", "loop": "none", "out": "none"},
                "follow_word": True,
                "follow_word_tint": False,
                "follow_word_box": True,
                "box_highlight": {
                    "color": "#00FF40",
                    "alpha": 0.95,
                    "pad_em": 0.2,
                    "offset_y_em": off,
                    "radius_em": 0.08,
                },
                "fonts": {"body": "Arimo"},
            },
        }

    def _green_cy(img):
        view = _bgra(img)[1]
        b = view[..., 0].astype(np.int16)
        g = view[..., 1].astype(np.int16)
        r = view[..., 2].astype(np.int16)
        a = view[..., 3]
        mask = (a >= 40) & (g > r + 40) & (g > b + 40)
        assert int(mask.sum()) > 30, "box highlight not visible"
        ys = np.where(mask)[0].astype(np.float64)
        return float(ys.mean())

    s0 = _style(0.0)
    s1 = _style(0.45)
    p0 = _preview_frame(s0, t=0.35)
    e0 = _export_frame(s0, t=0.35)
    p1 = _preview_frame(s1, t=0.35)
    e1 = _export_frame(s1, t=0.35)
    assert _mad(p0, e0) < 2.5, _mad(p0, e0)
    assert _mad(p1, e1) < 2.5, _mad(p1, e1)
    cy_p0, cy_p1 = _green_cy(p0), _green_cy(p1)
    cy_e0, cy_e1 = _green_cy(e0), _green_cy(e1)
    # 0.45 em × 72 px ≈ 32 px downward (Qt y grows down).
    assert cy_p1 - cy_p0 > 12, (cy_p0, cy_p1)
    assert cy_e1 - cy_e0 > 12, (cy_e0, cy_e1)


def _read_value(w):
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QSlider,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, SliderSpin
    if isinstance(w, SliderSpin):
        return ("spin", w.value())
    if isinstance(w, ColorSwatch):
        return ("hex", w.hex())
    if isinstance(w, QComboBox):
        return ("combo", w.currentIndex(), w.currentText())
    if isinstance(w, (QCheckBox, ToggleSwitch)):
        return ("chk", w.isChecked())
    if isinstance(w, (QAbstractSpinBox, QSlider)):
        return ("val", w.value())
    if isinstance(w, QPlainTextEdit):
        return ("txt", w.toPlainText())
    if hasattr(w, "flags") and callable(w.flags):
        return ("flags", tuple(w.flags()))
    if hasattr(w, "colors") and callable(w.colors):
        return ("cols", tuple(w.colors() or ()))
    if hasattr(w, "pair") and callable(w.pair):
        return ("pair", tuple(w.pair()))
    return None


def _perturb(w):
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QSlider,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, SliderSpin
    if isinstance(w, SliderSpin) or isinstance(w, (QAbstractSpinBox, QSlider)):
        lo, hi = w.minimum(), w.maximum()
        cur = w.value()
        nxt = hi if cur < (float(lo) + float(hi)) / 2.0 else lo
        if nxt == cur:
            return False
        w.setValue(nxt)
        return True
    if isinstance(w, (QCheckBox, ToggleSwitch)):
        w.setChecked(not w.isChecked())
        return True
    if isinstance(w, QComboBox) and w.count() > 1:
        w.setCurrentIndex((w.currentIndex() + 1) % w.count())
        return True
    if isinstance(w, QPlainTextEdit):
        w.setPlainText((w.toPlainText() or "") + "\nQ60")
        return True
    if isinstance(w, ColorSwatch):
        w.set_hex("#FF00AA", emit=False)
        return True
    if hasattr(w, "set_flags"):
        w.set_flags(["numbers"], emit=False)
        return True
    if hasattr(w, "set_colors"):
        try:
            w.set_colors(["#FF00AA"], emit=False)
        except TypeError:
            w.set_colors(["#FF00AA"])
        return True
    if hasattr(w, "set_pair"):
        w.set_pair([1.0, 1.0], emit=True)
        return True
    return False


def _value_widgets(root):
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QSlider,
        QWidget,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch
    from cutora.ui.widgets.inspector import ColorSwatch, SliderSpin

    kinds = (
        SliderSpin, ColorSwatch, QComboBox, QCheckBox, ToggleSwitch,
        QPlainTextEdit, QAbstractSpinBox, QSlider,
    )
    found = []
    stack = [root]
    seen = set()
    while stack:
        w = stack.pop()
        if w is None or id(w) in seen:
            continue
        seen.add(id(w))
        if isinstance(w, kinds):
            parent = w.parentWidget()
            if isinstance(w, (QAbstractSpinBox, QSlider)) and isinstance(parent, SliderSpin):
                pass
            else:
                found.append(w)
        if isinstance(w, QWidget):
            for child in w.children():
                if isinstance(child, QWidget):
                    stack.append(child)
    return found


def test_q60_no_dead_reset_buttons(caplog):
    """Every visible ↺ restores a value; none of them only log a warning."""
    from PySide6.QtWidgets import QApplication, QToolButton
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.widgets.inspector import InspectorRow

    _qapp()
    panel = _panel()
    style = {
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0.08, "color": "#E11D48"},
            "glow": {"sigma_em": 0.18, "strength": 0.8},
            "shadow": {"enabled": True, "distance_em": 0.2, "strength": 0.5},
            "bg": {"type": "box", "color": "#0B1A33", "alpha": 0.5},
            "fonts": {"body": "Arimo", "emphasis": "Anton"},
            "emphasis": {"auto": ["caps"], "words": ["HELLO"]},
        },
    }
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.show()
    panel.resize(480, 900)
    for sec in panel.findChildren(CollapsibleSection):
        sec.set_collapsed(False)
        tg = getattr(sec, "_toggle_cb", None)
        if tg is not None and hasattr(tg, "setChecked"):
            tg.setChecked(True)
    QApplication.processEvents()

    resets = []
    for btn in panel.findChildren(QToolButton):
        if (btn.text() or "") != "↺":
            continue
        if not btn.isVisible() or not btn.isEnabled():
            continue
        resets.append(btn)
    assert resets, "no visible ↺ on the subtitle inspector"

    dead = []
    caplog.set_level(logging.WARNING)
    n_before = len(caplog.records)
    for btn in resets:
        row = None
        sec = None
        cur = btn.parentWidget()
        while cur is not None:
            if isinstance(cur, InspectorRow) and row is None:
                row = cur
            if isinstance(cur, CollapsibleSection) and sec is None:
                sec = cur
            cur = cur.parentWidget()
        is_section = sec is not None and btn is getattr(sec, "_reset_btn", None)
        scope = sec if is_section else (row if row is not None else sec)
        label = ""
        if row is not None and not is_section:
            label = row.full_label_text() or (row.field.objectName() if row.field else "")
        elif sec is not None:
            label = "section:%s" % getattr(sec, "section_title", "")
        if scope is None:
            dead.append(("no-scope", btn.toolTip(), label))
            continue
        widgets = [w for w in _value_widgets(scope) if _read_value(w) is not None]
        if not widgets:
            dead.append(("no-values", label))
            continue

        def _fire():
            if is_section:
                sec._reset_section()
            elif row is not None:
                row.trigger_reset()
            else:
                btn.click()
            QApplication.processEvents()

        original = [_read_value(w) for w in widgets]
        _fire()
        after = [_read_value(w) for w in widgets]
        if after != original:
            continue
        perturbed = False
        for w in widgets:
            if _perturb(w):
                perturbed = True
        QApplication.processEvents()
        mid = [_read_value(w) for w in widgets]
        if not perturbed or mid == original:
            dead.append(("perturb-failed", label, [type(w).__name__ for w in widgets]))
            continue
        _fire()
        restored = [_read_value(w) for w in widgets]
        if restored == mid:
            dead.append(("no-restore", label, original, mid, restored))
    warn = [
        r.getMessage()
        for r in caplog.records[n_before:]
        if "reset" in r.getMessage().lower() or "↺" in r.getMessage()
    ]
    panel.close()
    assert not warn, warn
    assert not dead, dead


def test_q60_layout_complete_all_widgets():
    """Every QWidget attribute on the panel is in INSPECTOR_LAYOUT or owned by one."""
    from PySide6.QtWidgets import QWidget
    from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
    from cutora.ui.widgets.inspector import layout_attr_names

    _qapp()
    panel = _panel()
    names = layout_attr_names(INSPECTOR_LAYOUT)
    listed = []
    missing = []
    for name in names:
        w = getattr(panel, name, None)
        if w is None:
            if name == "chk_detached":
                continue
            missing.append(name)
            continue
        if isinstance(w, QWidget):
            listed.append(w)
    assert not missing, "table names missing on panel: %s" % missing

    orphans = []
    for name, w in list(panel.__dict__.items()):
        if not isinstance(w, QWidget):
            continue
        if name.startswith("_") and name not in names:
            continue
        owners = [owner for owner in listed if _is_descendant(w, owner)]
        if not owners:
            orphans.append(name)
    panel.close()
    assert not orphans, "QWidget attrs not in INSPECTOR_LAYOUT: %s" % orphans

    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    from cutora.ui.widgets.inspector import Row, Section, build_inspector
    host = QWidget()
    box = QVBoxLayout(host)
    scroll = QScrollArea(host)
    box.addWidget(scroll)
    raised = None
    try:
        build_inspector(
            host,
            [Section("probe", rows=(Row("x", "no_such_q60_widget"),))],
            scroll,
        )
    except AttributeError as exc:
        raised = exc
    assert raised is not None, "missing attr must raise, not skip"
    assert "no_such_q60_widget" in str(raised)
