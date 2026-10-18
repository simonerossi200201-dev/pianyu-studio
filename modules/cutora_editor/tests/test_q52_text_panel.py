# -*- coding: utf-8 -*-
"""Q52: text overlay inspector — no orphan keys, labels fit, bg rows hide, toggles keep values."""
from __future__ import annotations

import ast
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280

# Functions on PropertyPanel that persist a *text* overlay.
_TEXT_SAVE_FUNCS = frozenset({
    "_save_text_overlay",
    "_write_ov_fx",
    "_apply_ov_section_toggles",
    "_on_ov_runs_changed",
})

_GLASS_ATTRS = (
    "fx_bg_glass_sigma_em", "fx_bg_glass_border",
    "fx_bg_glass_thickness_em", "fx_bg_glass_refraction",
    "fx_bg_glass_bevel", "fx_bg_glass_sheen",
)
_TEXTURE_ATTRS = ("fx_bg_texture", "fx_bg_texture_tint")
_BUBBLE_ATTRS = ("fx_bg_align", "fx_bg_line_gap_em")


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q52-text-panel"])
    try:
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass
    return app


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _legacy_overlay(**extra):
    ov = {
        "type": "text",
        "id": "q52-ov",
        "content": "HELLO",
        "start": 0.0,
        "end": 4.0,
        "x": 80,
        "y": 200,
        "w": 560,
        "h": 160,
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "italic": False,
        "rotation": 0.0,
        "opacity": 255,
    }
    ov.update(extra)
    return ov


def _paint_layers(task, t=1.0, w=CANVAS_W, h=CANVAS_H, fill="#142028"):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(p, task, w, h, t, render_order=["text"])
    finally:
        p.end()
    return img


def _export_frame(task, t=1.0, w=CANVAS_W, h=CANVAS_H, fill="#142028"):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, w, h, QColor(fill))

    return ExportRenderer.render_frame(task, w, h, t, draw_background=_bg)


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    hh, ww = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(hh, bpl)[:, : ww * 4].reshape(hh, ww, 4).copy()


def _string_literals(path):
    src = open(path, encoding="utf-8").read()
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def _overlay_written_keys(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in _TEXT_SAVE_FUNCS:
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Subscript) and isinstance(
                            t.slice, ast.Constant) and isinstance(t.slice.value, str):
                        keys.add(t.slice.value)
            if isinstance(n, ast.Call):
                func = n.func
                if isinstance(func, ast.Attribute) and func.attr == "pop":
                    if n.args and isinstance(n.args[0], ast.Constant) and isinstance(
                            n.args[0].value, str):
                        keys.add(n.args[0].value)
    return keys


def _reader_keys():
    readers = set()
    paint = os.path.join(CUTORA, "render", "text_layer_paint.py")
    readers |= _string_literals(paint)
    readers |= _string_literals(os.path.join(CUTORA, "subtitle", "text_layer.py"))
    fx_root = os.path.join(CUTORA, "render", "textfx")
    for dirpath, _dirs, files in os.walk(fx_root):
        for name in files:
            if name.endswith(".py"):
                readers |= _string_literals(os.path.join(dirpath, name))
    return readers


def test_q52_no_orphan_keys():
    """Every key the text panel writes must be read by the paint path.

    Scans property.py text-save functions vs text_layer_paint + textfx + the
    overlay_to_style adapter. No file allowlist.
    """
    prop = os.path.join(CUTORA, "ui", "panels", "property.py")
    written = _overlay_written_keys(prop)
    readers = _reader_keys()
    orphans = sorted(k for k in written if k not in readers)
    assert orphans == [], orphans
    banned = {
        "blend_mode", "transition_preset", "transition_duration",
        "stroke_expand_enabled", "stroke_expand_layers", "stroke_expand_step",
        "stroke_expand_feather", "anim_in_type", "anim_out_type",
        "floating_amplitude", "flash_duration",
    }
    still = sorted(banned & written)
    assert still == [], still


def _panel_overlay(content="HELLO", **fx_extra):
    _qapp()
    _fonts()
    from cutora.ui.panels.property import PropertyPanel
    from cutora.subtitle.text_layer import overlay_fx_from_legacy
    panel = PropertyPanel()
    ov = _legacy_overlay(content=content)
    ov["fx"] = overlay_fx_from_legacy(ov)
    if fx_extra:
        fx = ov["fx"]
        for key, val in fx_extra.items():
            if isinstance(val, dict) and isinstance(fx.get(key), dict):
                fx[key].update(val)
            else:
                fx[key] = val
    panel.block_signals = False
    panel.load_data("overlay", ov)
    panel.block_signals = False
    panel.current_data = ov
    panel.current_type = "overlay"
    return panel, ov


def _expand(panel):
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from PySide6.QtWidgets import QApplication
    for sec in panel.findChildren(CollapsibleSection):
        if hasattr(sec, "setExpanded"):
            sec.setExpanded(True)
    QApplication.processEvents()


def _preview_and_export(ov, fill="#142028"):
    task = {"text_overlays": [ov], "segments": [], "style": {}}
    preview = _paint_layers(task, fill=fill)
    export = _export_frame(task, fill=fill)
    return preview, export


def test_q52_label_not_clipped():
    """Every visible inspector label's advance fits the width it was given at dock min."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import DOCK_RIGHT_MIN
    from cutora.ui.widgets.inspector import InspectorRow

    panel, _ov = _panel_overlay()
    panel.setFixedWidth(DOCK_RIGHT_MIN)
    panel.show()
    QApplication.processEvents()
    _expand(panel)
    panel.resize(DOCK_RIGHT_MIN, max(panel.height(), 1200))
    QApplication.processEvents()
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.widgets.inspector import apply_section_label_widths
    for sec in panel.findChildren(CollapsibleSection):
        apply_section_label_widths(sec)
    QApplication.processEvents()

    clipped = []
    for row in panel.findChildren(InspectorRow):
        lab = row.label
        text = row.full_label_text() if hasattr(row, "full_label_text") else lab.text()
        if not lab.isVisible() or not (text or "").strip():
            continue
        fm = QFontMetrics(lab.font())
        adv = fm.horizontalAdvance(text)
        given = lab.width()
        if adv > given:
            clipped.append((text, adv, given))
    panel.close()
    assert clipped == [], clipped[:12]


def test_q52_bg_rows_hidden_by_type():
    """bg.type=box hides glass / texture / bubble rows (not merely disabled)."""
    from cutora.ui.panels.fx_fields import apply_widget_sample
    from PySide6.QtWidgets import QApplication

    panel, ov = _panel_overlay()
    _expand(panel)
    panel.show()
    QApplication.processEvents()
    apply_widget_sample(panel, "bg.type", "box")
    QApplication.processEvents()

    missing = []
    visible = []
    for attr in _GLASS_ATTRS + _TEXTURE_ATTRS + _BUBBLE_ATTRS:
        w = getattr(panel, attr, None)
        if w is None:
            missing.append(attr)
            continue
        if w.isVisible():
            visible.append(attr)
        row = None
        from cutora.ui.widgets.inspector import inspector_row_of
        row = inspector_row_of(w)
        if row is not None and row.isVisible():
            visible.append(attr + ".row")
    panel.close()
    assert missing == [], missing
    assert visible == [], visible
    assert (ov.get("fx") or {}).get("bg", {}).get("type") == "box"


def test_q52_section_toggle_keeps_value():
    """Stroke width 0.2 → header off → header on still 0.2, not default 0.08."""
    from cutora.ui.panels.fx_fields import apply_widget_sample, get_widget_value
    from PySide6.QtWidgets import QApplication

    panel, ov = _panel_overlay()
    panel.ov_strk.setChecked(True)
    QApplication.processEvents()
    apply_widget_sample(panel, "stroke.width_em", 0.2)
    QApplication.processEvents()
    assert abs(float(get_widget_value(panel, "stroke.width_em")) - 0.2) < 1e-6
    assert abs(float((ov.get("fx") or {}).get("stroke", {}).get("width_em") or 0) - 0.2) < 1e-6

    panel.ov_strk.setChecked(False)
    QApplication.processEvents()
    stroke_off = (ov.get("fx") or {}).get("stroke") or {}
    assert stroke_off.get("enabled") is False, stroke_off
    assert abs(float(stroke_off.get("width_em") or 0) - 0.2) < 1e-6, stroke_off

    # Preview refresh reloads the same overlay; the snapshot must survive that.
    panel.load_data("overlay", ov)
    panel.block_signals = False
    panel.current_data = ov
    panel.current_type = "overlay"
    QApplication.processEvents()

    panel.ov_strk.setChecked(True)
    QApplication.processEvents()
    got = float((ov.get("fx") or {}).get("stroke", {}).get("width_em") or 0)
    panel.close()
    assert abs(got - 0.2) < 1e-6, got
    assert abs(got - 0.08) > 1e-6


def _pixel_delta(ov_a, ov_b):
    pa, ea = _preview_and_export(ov_a)
    pb, eb = _preview_and_export(ov_b)
    a_p, b_p = _bgra(pa), _bgra(pb)
    a_e, b_e = _bgra(ea), _bgra(eb)
    d_p = int(np.abs(a_p.astype(np.int16) - b_p.astype(np.int16)).max())
    d_e = int(np.abs(a_e.astype(np.int16) - b_e.astype(np.int16)).max())
    mad = float(np.mean(np.abs(b_p.astype(np.int16) - b_e.astype(np.int16))))
    return d_p, d_e, mad


@pytest.mark.parametrize("path,value,content", [
    ("tracking_em", 0.35, "HELLO"),
    ("fonts.body", "Roboto", "HELLO"),
    ("emphasis.words", ["HELLO"], "HELLO"),
    ("scatter.size_jitter", 0.55, "HELLO"),
])
def test_q52_new_sections_reach_pixels(path, value, content):
    """Layout / dual-font / auto-color / scatter knobs must change preview and export pixels."""
    from cutora.ui.panels.fx_fields import apply_widget_sample, get_widget_value
    import copy

    extra = {}
    if path.startswith("scatter."):
        extra["scatter"] = {"enabled": True, "size_jitter": 0.0,
                            "pos_jitter_em": [0.0, 0.0], "rot_jitter_deg": 0.0}
    if path.startswith("emphasis."):
        extra["emphasis"] = {
            "auto": [], "words": [], "type": "solid",
            "colors": ["#FF20C0"], "scale": 1.8, "anim": "none",
        }
    panel, ov = _panel_overlay(content=content, **extra)
    if path.startswith("scatter."):
        apply_widget_sample(panel, "scatter.enabled", True)
    before = copy.deepcopy(ov)
    apply_widget_sample(panel, path, value)
    got = get_widget_value(panel, path)
    d_p, d_e, mad = _pixel_delta(before, ov)
    panel.close()
    assert d_p >= 8, (path, value, d_p, got)
    assert d_e >= 8, (path, value, d_e)
    assert mad < 2.5, mad
