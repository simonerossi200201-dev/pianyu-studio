# -*- coding: utf-8 -*-
"""Q66: section-header enabled flags survive save/reopen; one bg.type writer; shared labels."""
from __future__ import annotations

import ast
import json
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280
STROKE_KEEP = 0.2


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q66-toggle"])
    try:
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass
    return app


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None
    win._begin_open_progress = lambda *a, **k: None
    win._tick_open_progress = lambda *a, **k: None
    win._end_open_progress = lambda *a, **k: None
    win._ui_warning = lambda *a, **k: None


def _empty_task(style=None, overlays=None):
    from cutora.model.timeline import Timeline
    return {
        "name": "q66",
        "video": "",
        "path": "",
        "text": "",
        "segments": [],
        "style": style if style is not None else {},
        "duration": 4.0,
        "text_overlays": list(overlays or []),
        "image_overlays": [],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
    }


def _overlay():
    from cutora.subtitle.text_layer import overlay_fx_from_legacy
    ov = {
        "type": "text",
        "id": "q66-ov",
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
    ov["fx"] = overlay_fx_from_legacy(ov)
    return ov


def _style_stroke(width_em=STROKE_KEEP):
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {
                "width_em": width_em, "color": "#E11D48", "join": "round",
                "enabled": True,
            },
            "glow": {"sigma_em": 0, "enabled": False},
            "shadow": {"enabled": False},
            "bg": {"type": "none", "enabled": False},
        },
    }


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    hh, ww = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(hh, bpl)[:, : ww * 4].reshape(hh, ww, 4).copy()


def _paint_preview_export(style):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.model.timeline import Timeline
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.export_renderer import ExportRenderer

    event = {
        "text": "HELLO",
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": "HELLO", "start": 0.0, "end": 1.6},
        ],
    }
    task = {
        "style": style,
        "segments": [event],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#142028"))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(
            p, task, CANVAS_W, CANVAS_H, 0.8, render_order=["subs"])
    finally:
        p.end()

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#142028"))

    exp = ExportRenderer.render_frame(
        task, CANVAS_W, CANVAS_H, 0.8, draw_background=_bg)
    return img, exp


def test_q66_toggle_value_survives_save_reopen(tmp_path):
    """Stroke 0.2 → header off → project.save_as → project.open → on, still 0.2."""
    from PySide6.QtWidgets import QApplication
    from cutora.core.commands import reset_bus_for_tests
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fx_fields import apply_widget_sample, get_widget_value

    _qapp()
    _fonts()
    reset_bus_for_tests()
    dest = tmp_path / "q66-stroke.asproj"

    style = _style_stroke(STROKE_KEEP)
    ov = _overlay()
    ov["fx"]["stroke"] = {
        "width_em": STROKE_KEEP, "color": "#E11D48", "join": "round",
        "enabled": True,
    }
    task = _empty_task(style=style, overlays=[ov])

    win = MainWindow()
    try:
        _silence(win)
        win.tasks = [task]
        win.curr_idx = 0
        win.project_path = None

        # --- subtitle style ---
        sub = win.sub_panel
        sub.block_signals = False
        sub.load_data(task["style"])
        sub.block_signals = False
        sub.current_data = task["style"]
        sub.sub_strk.setChecked(True)
        QApplication.processEvents()
        apply_widget_sample(sub, "stroke.width_em", STROKE_KEEP)
        QApplication.processEvents()
        sub.sub_strk.setChecked(False)
        QApplication.processEvents()
        stroke = (task["style"].get("fx") or {}).get("stroke") or {}
        assert stroke.get("enabled") is False, stroke
        assert abs(float(stroke.get("width_em") or 0) - STROKE_KEEP) < 1e-6, stroke

        # --- text overlay ---
        prop = win.prop_panel
        prop.block_signals = False
        prop.load_data("overlay", ov)
        prop.block_signals = False
        prop.current_data = ov
        prop.current_type = "overlay"
        prop.ov_strk.setChecked(True)
        QApplication.processEvents()
        apply_widget_sample(prop, "stroke.width_em", STROKE_KEEP)
        QApplication.processEvents()
        prop.ov_strk.setChecked(False)
        QApplication.processEvents()
        ov_stroke = (ov.get("fx") or {}).get("stroke") or {}
        assert ov_stroke.get("enabled") is False, ov_stroke
        assert abs(float(ov_stroke.get("width_em") or 0) - STROKE_KEEP) < 1e-6, ov_stroke

        win.bus.execute("project.save_as", {"path": str(dest)}, source="ui")
        assert dest.is_file(), dest
        raw = json.loads(dest.read_text(encoding="utf-8"))
        saved_style = (raw.get("tasks") or [{}])[0].get("style") or {}
        saved_st = (saved_style.get("fx") or {}).get("stroke") or {}
        assert saved_st.get("enabled") is False, saved_st
        assert abs(float(saved_st.get("width_em") or 0) - STROKE_KEEP) < 1e-6, saved_st
        saved_ov = ((raw.get("tasks") or [{}])[0].get("text_overlays") or [{}])[0]
        saved_ov_st = (saved_ov.get("fx") or {}).get("stroke") or {}
        assert saved_ov_st.get("enabled") is False, saved_ov_st
        assert abs(float(saved_ov_st.get("width_em") or 0) - STROKE_KEEP) < 1e-6, saved_ov_st

        win.bus.execute("project.open", {"path": str(dest)}, source="ui")
        tasks = list(getattr(win, "tasks", None) or [])
        assert tasks, "open left no tasks"
        opened_style = tasks[0].get("style") or {}
        opened_ov = (tasks[0].get("text_overlays") or [{}])[0]

        sub.load_data(opened_style)
        sub.block_signals = False
        sub.current_data = opened_style
        QApplication.processEvents()
        assert sub.sub_strk.isChecked() is False
        assert abs(float(get_widget_value(sub, "stroke.width_em")) - STROKE_KEEP) < 1e-6
        sub.sub_strk.setChecked(True)
        QApplication.processEvents()
        got = (opened_style.get("fx") or {}).get("stroke") or {}
        assert got.get("enabled") is True, got
        assert abs(float(got.get("width_em") or 0) - STROKE_KEEP) < 1e-6, got
        assert abs(float(got.get("width_em") or 0) - 0.08) > 1e-6

        prop.load_data("overlay", opened_ov)
        prop.block_signals = False
        prop.current_data = opened_ov
        prop.current_type = "overlay"
        QApplication.processEvents()
        assert prop.ov_strk.isChecked() is False
        prop.ov_strk.setChecked(True)
        QApplication.processEvents()
        got_ov = (opened_ov.get("fx") or {}).get("stroke") or {}
        assert got_ov.get("enabled") is True, got_ov
        assert abs(float(got_ov.get("width_em") or 0) - STROKE_KEEP) < 1e-6, got_ov
    finally:
        win.close()


def test_q66_single_bg_type_writer():
    """背景色块 writes bg.type through fx_bg_type only. Glass toggle is gone."""
    from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
    from cutora.ui.widgets.inspector import layout_attr_names

    names = layout_attr_names(INSPECTOR_LAYOUT)
    assert "fx_bg_glass_toggle" not in names
    bg_sec = [s for s in INSPECTOR_LAYOUT if s.title == "背景色块"]
    assert bg_sec, "missing 背景色块"
    bg_attrs = [r.attr for r in bg_sec[0].rows]
    type_rows = [a for a in bg_attrs if a == "fx_bg_type"]
    assert type_rows == ["fx_bg_type"], type_rows
    assert "fx_bg_glass_toggle" not in bg_attrs

    hits_toggle = []
    hits_glass = []
    bg_type_section_toggles = []
    for path in _iter_ui_py():
        src = open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, CUTORA).replace("\\", "/")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "SectionToggle" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and arg0.value == "bg.type":
                        bg_type_section_toggles.append(rel)
                    if isinstance(arg0, ast.Constant) and arg0.value == "bg.enabled":
                        hits_toggle.append(rel)
            if isinstance(node, ast.Attribute) and node.attr == "fx_bg_glass_toggle":
                hits_glass.append("%s:%s" % (rel, node.lineno))
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "fx_bg_glass_toggle":
                        hits_glass.append("%s:%s" % (rel, node.lineno))

    assert not hits_glass, "fx_bg_glass_toggle still referenced: %s" % hits_glass
    assert any("subtitle_style.py" in p or "property.py" in p for p in hits_toggle), (
        "bg.enabled SectionToggle missing", hits_toggle)
    # Texture capsule (Q24) may still snap bg.type; 背景色块 itself must not.
    assert "fx_fields.py" not in " ".join(bg_type_section_toggles)


def test_q66_same_label_both_panels():
    """Same fx path → same inspector label on subtitle and text overlay panels."""
    from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.inspector import inspector_row_of, layout_attr_names

    _qapp()
    _fonts()
    sub_attrs = set(layout_attr_names(INSPECTOR_LAYOUT))
    sub = SubtitleStylePanel()
    prop = PropertyPanel()
    mismatches = []
    skipped = []
    for path, attr in FX_FIELD_WIDGETS.items():
        if attr not in sub_attrs:
            continue
        w_s = getattr(sub, attr, None)
        w_p = getattr(prop, attr, None)
        if w_s is None or w_p is None:
            skipped.append((path, attr, w_s is None, w_p is None))
            continue
        ls = _row_label(w_s)
        lp = _row_label(w_p)
        if not ls and not lp:
            continue
        if ls != lp:
            mismatches.append((path, ls, lp))
    sub.close()
    prop.close()
    assert not mismatches, mismatches


def test_q66_disabled_keeps_width_off_pixels():
    """enabled=False with a leftover width must paint like width=0 (preview and export)."""
    _qapp()
    _fonts()
    off_legacy = _style_stroke(0.0)
    off_legacy["fx"]["stroke"] = {"width_em": 0.0, "color": "#E11D48", "join": "round"}
    off_kept = _style_stroke(STROKE_KEEP)
    off_kept["fx"]["stroke"]["enabled"] = False
    on = _style_stroke(STROKE_KEEP)

    p_leg, e_leg = _paint_preview_export(off_legacy)
    p_kept, e_kept = _paint_preview_export(off_kept)
    p_on, e_on = _paint_preview_export(on)

    a, b = _bgra(p_leg), _bgra(p_kept)
    d_prev = int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())
    ae, be = _bgra(e_leg), _bgra(e_kept)
    d_exp = int(np.abs(ae.astype(np.int16) - be.astype(np.int16)).max())
    assert d_prev == 0, d_prev
    assert d_exp == 0, d_exp

    c = _bgra(p_on)
    d_on = int(np.abs(a.astype(np.int16) - c.astype(np.int16)).max())
    assert d_on >= 8, d_on
    ce = _bgra(e_on)
    d_on_e = int(np.abs(ae.astype(np.int16) - ce.astype(np.int16)).max())
    assert d_on_e >= 8, d_on_e
    mad = float(np.mean(np.abs(c.astype(np.int16) - ce.astype(np.int16))))
    assert mad < 2.5, mad


def test_q66_text_panel_grays_and_hides_follow_hint():
    """Text overlay stroke/bg/glow/shadow gray when off; no 跟随单词 hint."""
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from PySide6.QtWidgets import QApplication

    _qapp()
    _fonts()
    panel = PropertyPanel()
    ov = _overlay()
    panel.block_signals = False
    panel.load_data("overlay", ov)
    panel.block_signals = False
    panel.current_data = ov
    panel.current_type = "overlay"
    panel.ov_strk.setChecked(False)
    QApplication.processEvents()
    stroke_sec = None
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") == "描边":
            stroke_sec = sec
            break
    assert stroke_sec is not None
    assert getattr(stroke_sec, "_gray_content", False) is True
    content = getattr(stroke_sec, "_content", None)
    assert content is not None
    assert content.isEnabled() is False
    hint = getattr(panel, "fx_emphasis_auto_hint", None)
    assert hint is None, "text panel must not show 跟随单词 hint"
    hint_c = getattr(panel, "fx_emphasis_color_hint", None)
    assert hint_c is None
    panel.close()


def _row_label(widget) -> str:
    from cutora.ui.widgets.inspector import inspector_row_of
    row = inspector_row_of(widget)
    if row is None:
        return ""
    text = row.full_label_text() if hasattr(row, "full_label_text") else ""
    return str(text or "").strip().rstrip(":：")


def _iter_ui_py():
    root = os.path.join(CUTORA, "ui")
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)
