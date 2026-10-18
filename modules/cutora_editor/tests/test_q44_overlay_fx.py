# -*- coding: utf-8 -*-
"""Q44: overlay fx key-path closure, underline, run offset, image_element gone."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280

# Overlay chrome that is not fx: geometry, anim, expand-stroke, IO.
# Everything else written by the text overlay save path must be read by
# text_layer_paint / text_layer / spec.
OVERLAY_CHROME = frozenset({
    "x", "y", "w", "h", "rotation", "opacity",
    "type", "start", "end", "id", "content", "text", "runs",
    "font_family", "font_style", "fontsize", "bold", "italic",
    "text_layout", "writing_mode", "fit_mode", "fx",
    "blend_mode", "aspect_ratio",
    "transition_preset", "transition_duration",
    "stroke_expand_enabled", "stroke_expand_layers", "stroke_expand_step",
    "stroke_expand_feather", "stroke_expand_layer_widths",
    "stroke_expand_layer_feathers", "stroke_expand_colors",
    "anim_in_type", "anim_in_duration", "anim_in_easing",
    "anim_out_type", "anim_out_duration", "anim_out_easing",
    "floating_amplitude", "floating_period",
    "flash_color", "flash_duration",
    "content_format", "rich_content",
    "source_preset_id", "_preset_name",
    "scale", "flip_x", "flip_y", "keep_aspect", "feather",
    "anim_in", "anim_out", "sticker_id", "kind", "path",
})


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q44-overlay-fx"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _legacy_overlay(**extra):
    ov = {
        "type": "text",
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
        "color": "#F5B700",
        "use_stroke": True,
        "stroke_color": "#141A26",
        "stroke_width": 3,
        "bg_enabled": True,
        "bg_color": "#3A0A14",
        "bg_opacity": 180,
        "bg_padding": 12,
        "bg_radius": 12,
        "rotation": 0.0,
        "opacity": 255,
        "blend_mode": "normal",
        "shadow_enabled": True,
        "shadow_color": "#141A26",
        "shadow_opacity": 120,
        "shadow_blur": 4,
        "shadow_offset_x": 4,
        "shadow_offset_y": 6,
    }
    ov.update(extra)
    return ov


def _paint_layers(task, t=1.0, w=CANVAS_W, h=CANVAS_H, backdrop=None, fill="#142028"):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if backdrop is not None:
            p.drawImage(0, 0, backdrop)
        FrameRenderer.draw_layers(
            p, task, w, h, t, render_order=["text"], backdrop=backdrop)
    finally:
        p.end()
    return img


def _export_frame(task, t=1.0, w=CANVAS_W, h=CANVAS_H, fill="#142028", backdrop=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, w, h, QColor(fill))
        if backdrop is not None:
            painter.drawImage(0, 0, backdrop)

    return ExportRenderer.render_frame(
        task, w, h, t, draw_background=_bg, backdrop=backdrop)


def _md5(img) -> str:
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    return hashlib.md5(bytes(conv.constBits())[: conv.height() * conv.bytesPerLine()]).hexdigest()


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
    want = {
        "on_change_ov", "_write_ov_fx", "_save_ov_anim",
        "_on_ov_runs_changed", "_apply_ov_section_toggles",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in want:
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


def test_q44_overlay_no_orphan_keys():
    prop = os.path.join(CUTORA, "ui", "panels", "property.py")
    written = _overlay_written_keys(prop)
    readers = set()
    for rel in (
        os.path.join("render", "text_layer_paint.py"),
        os.path.join("render", "textfx", "spec.py"),
        os.path.join("subtitle", "text_layer.py"),
        os.path.join("render", "textfx", "layout.py"),
        os.path.join("render", "frame_renderer.py"),
    ):
        readers |= _string_literals(os.path.join(CUTORA, rel))
    orphans = sorted(k for k in written if k not in OVERLAY_CHROME and k not in readers)
    assert orphans == [], orphans
    # The 18 silent-fail flats must no longer be written.
    banned = {
        "shadow_enabled", "shadow_opacity", "shadow_blur", "shadow_spread",
        "shadow_offset_x", "shadow_offset_y",
        "bg_color", "bg_opacity", "bg_radius", "bg_padding",
        "bg_padding_x", "bg_padding_y", "bg_fill_type", "bg_grad_dir",
        "bg_color2", "bg_color3", "bg_color4", "bg_expand_w", "bg_expand_h",
        "bg_enabled", "use_stroke", "stroke_width", "stroke_color",
    }
    still = sorted(banned & written)
    assert still == [], still


def _panel_overlay():
    _qapp()
    _fonts()
    from cutora.ui.panels.property import PropertyPanel
    from cutora.subtitle.text_layer import overlay_fx_from_legacy
    panel = PropertyPanel()
    ov = _legacy_overlay()
    ov["fx"] = overlay_fx_from_legacy(ov)
    panel.block_signals = False
    panel.load_data("overlay", ov)
    panel.block_signals = False
    panel.current_data = ov
    panel.current_type = "overlay"
    return panel, ov


def _preview_and_export(ov, fill="#142028", backdrop=None):
    task = {"text_overlays": [ov], "segments": [], "style": {}}
    preview = _paint_layers(task, fill=fill, backdrop=backdrop)
    export = _export_frame(task, fill=fill, backdrop=backdrop)
    return preview, export


@pytest.mark.parametrize("path,value", [
    ("shadow.size_em", 0.45),
    ("shadow.distance_em", 1.2),
    ("shadow.strength", 1.2),
    ("bg.alpha", 0.95),
    ("bg.radius_em", 0.8),
    ("bg.color", "#22D3EE"),
    ("stroke.width_em", 0.18),
])
def test_q44_shadow_control_changes_pixels(path, value):
    """Walk the real text panel; changing an fx widget must change pixels.

    Route A retired ov_shadow_blur; the live blur knob is fx_shadow_size_em
    (shadow.size_em). Same for bg/stroke flats → fx_fields widgets.
    """
    from cutora.ui.panels.fx_fields import apply_widget_sample, get_widget_value
    panel, ov = _panel_overlay()
    before_p, before_e = _preview_and_export(ov)
    a_p, a_e = _bgra(before_p), _bgra(before_e)
    apply_widget_sample(panel, path, value)
    assert ov is panel.current_data
    after_p, after_e = _preview_and_export(ov)
    b_p, b_e = _bgra(after_p), _bgra(after_e)
    d_p = int(np.abs(a_p.astype(np.int16) - b_p.astype(np.int16)).max())
    d_e = int(np.abs(a_e.astype(np.int16) - b_e.astype(np.int16)).max())
    assert d_p >= 8, (path, value, d_p, get_widget_value(panel, path))
    assert d_e >= 8, (path, value, d_e)
    # preview compositor vs export compositor, same overlay
    mad = float(np.mean(np.abs(b_p.astype(np.int16) - b_e.astype(np.int16))))
    assert mad < 2.5, mad
    panel.close()


def test_q44_underline_renders():
    _qapp()
    _fonts()
    base = _legacy_overlay(content="MMMMMM", bg_enabled=False, use_stroke=False,
                           shadow_enabled=False)
    base["color"] = "#F4EBD8"
    base["fx"] = {
        "fill": {"type": "solid", "colors": ["#F4EBD8"]},
        "stroke": {"width_em": 0},
        "bg": {"type": "none"},
        "shadow": {"distance_em": 0, "enabled": False},
    }
    for key in (
        "bg_enabled", "bg_color", "use_stroke", "stroke_width", "shadow_enabled",
    ):
        base.pop(key, None)
    plain = dict(base)
    under = dict(base)
    n = len(under["content"])
    under["runs"] = [{"start": 0, "end": n, "overrides": {"underline": True}}]
    task_a = {"text_overlays": [plain], "segments": [], "style": {}}
    task_b = {"text_overlays": [under], "segments": [], "style": {}}
    img_a = _paint_layers(task_a, fill="#101018")
    img_b = _paint_layers(task_b, fill="#101018")
    a, b = _bgra(img_a), _bgra(img_b)
    y0, y1 = int(base["y"]), int(base["y"] + base["h"])
    x0, x1 = int(base["x"]), int(base["x"] + base["w"])
    h = y1 - y0
    # Opaque canvas: compare the lower half of the box (under the baseline).
    d = np.abs(a[y0:y1, x0:x1].astype(np.int16) - b[y0:y1, x0:x1].astype(np.int16)).max(axis=2)
    low = int((d[int(h * 0.45):, :] > 12).sum())
    top = int((d[: int(h * 0.35), :] > 12).sum())
    assert low > 40, (low, top)
    assert low > top, (low, top)
    exp_a = _export_frame(task_a, fill="#101018")
    exp_b = _export_frame(task_b, fill="#101018")
    ea, eb = _bgra(exp_a), _bgra(exp_b)
    de = np.abs(ea[y0:y1, x0:x1].astype(np.int16) - eb[y0:y1, x0:x1].astype(np.int16)).max(axis=2)
    elow = int((de[int(h * 0.45):, :] > 12).sum())
    assert elow > 40, elow


def test_q44_multiline_run_offset():
    _qapp()
    _fonts()
    text = "AAAAAA\nBBBBBB\nCC REDWORD DD"
    start = text.index("REDWORD")
    ov = _legacy_overlay(content=text, bg_enabled=False, use_stroke=False,
                         shadow_enabled=False)
    ov["color"] = "#F4EBD8"
    ov["y"] = 160
    ov["h"] = 360
    ov["fontsize"] = 48
    ov["fx"] = {
        "fill": {"type": "solid", "colors": ["#F4EBD8"]},
        "stroke": {"width_em": 0},
        "bg": {"type": "none"},
        "shadow": {"distance_em": 0, "enabled": False},
    }
    for key in (
        "bg_enabled", "bg_color", "use_stroke", "stroke_width", "shadow_enabled",
    ):
        ov.pop(key, None)
    ov["runs"] = [{
        "start": start, "end": start + len("REDWORD"),
        "overrides": {"color": "#FF2020"},
    }]
    img = _paint_layers({"text_overlays": [ov], "segments": [], "style": {}},
                        fill="#101018")
    arr = _bgra(img)
    y0, y1 = int(ov["y"]), int(ov["y"] + ov["h"])
    x0, x1 = int(ov["x"]), int(ov["x"] + ov["w"])
    crop = arr[y0:y1, x0:x1]
    r = crop[:, :, 2].astype(np.int16)
    g = crop[:, :, 1].astype(np.int16)
    b = crop[:, :, 0].astype(np.int16)
    a = crop[:, :, 3]
    hot = (r > g + 25) & (r > b + 25) & (a > 40)
    ys, xs = np.where(hot)
    assert len(ys) > 8, "expected red pixels on REDWORD"
    # Three stacked lines of 48px: REDWORD is on line 3, not line 1 (the
    # newline-offset bug paints it ~2 glyphs left on an earlier line).
    assert float(ys.mean()) > 70, float(ys.mean())
    assert float(xs.mean()) > (x1 - x0) * 0.18, float(xs.mean())
    assert float(xs.mean()) < (x1 - x0) * 0.80, float(xs.mean())
    exp = _export_frame(
        {"text_overlays": [ov], "segments": [], "style": {}}, fill="#101018")
    earr = _bgra(exp)
    ecrop = earr[y0:y1, x0:x1]
    er = ecrop[:, :, 2].astype(np.int16)
    eg = ecrop[:, :, 1].astype(np.int16)
    eb = ecrop[:, :, 0].astype(np.int16)
    ea = ecrop[:, :, 3]
    ehot = (er > eg + 25) & (er > eb + 25) & (ea > 40)
    eys, exs = np.where(ehot)
    assert len(eys) > 8
    assert abs(float(eys.mean()) - float(ys.mean())) < 8
    assert abs(float(exs.mean()) - float(xs.mean())) < 8


def test_q44_no_image_element():
    allowed_files = {
        os.path.join("cutora", "subtitle", "text_layer.py").replace("\\", "/"),
        os.path.join("tests", "test_t27_text_layer.py").replace("\\", "/"),
        os.path.join("tests", "test_q44_overlay_fx.py").replace("\\", "/"),
        os.path.join("tests", "test_open_keeps_placed_images.py").replace("\\", "/"),
    }
    hits = []
    for root_name in ("cutora", "tests", "tools"):
        root = os.path.join(REPO, root_name)
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, REPO).replace("\\", "/")
                if rel in allowed_files:
                    continue
                src = open(path, encoding="utf-8").read()
                if "image_element" in src:
                    line = src.split("image_element", 1)[0].count("\n") + 1
                    hits.append("%s:%d" % (rel, line))
    assert hits == [], hits
    # migrate helpers themselves must still mention the token
    tl = open(os.path.join(CUTORA, "subtitle", "text_layer.py"), encoding="utf-8").read()
    assert "strip_image_elements" in tl


def test_q44_main_window_open_migrates(tmp_path):
    """project.open (the UI path) must run migrate_open_project."""
    _qapp()
    _fonts()
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
    from cutora.ui.main_window import MainWindow
    reset_bus_for_tests()
    ov = _legacy_overlay()
    data = {
        "version": 2,
        "tasks": [{
            "video": "",
            "duration": 4.0,
            "text_overlays": [ov],
            "image_overlays": [],
            "segments": [],
            "style": {},
            "timeline": {"width": CANVAS_W, "height": CANVAS_H, "fps": 30,
                         "sources": {}, "tracks": []},
        }],
    }
    proj = tmp_path / "q44.asproj.json"
    proj.write_text(json.dumps(data), encoding="utf-8")
    win = MainWindow()
    bus = CommandBus(get_registry())
    bus.bind_window(win)
    bus.execute("project.open", {"path": str(proj)}, source="api")
    tasks = list(getattr(win, "tasks", None) or [])
    assert tasks, "open left no tasks"
    got = (tasks[0].get("text_overlays") or [None])[0]
    assert isinstance(got, dict)
    assert isinstance(got.get("fx"), dict)
    for key in ("bg_color", "bg_opacity", "stroke_width", "shadow_offset_x",
                "shadow_blur", "bg_enabled"):
        assert key not in got, key
    img = _paint_layers(tasks[0])
    assert img.width() == CANVAS_W
    # Export path of the opened (migrated) task must produce a frame.
    exp = _export_frame(tasks[0])
    assert _md5(img) == _md5(exp) or float(
        np.mean(np.abs(_bgra(img).astype(np.int16) - _bgra(exp).astype(np.int16)))
    ) < 2.5
    win.close()


def test_q44_document_to_runs_counts_newlines():
    _qapp()
    from cutora.ui.panels.text_rich import RichTextEditor, document_to_runs
    host = RichTextEditor()
    text = "AAA\nBBB\nCCC"
    host.set_text_runs(
        text,
        [{"start": 8, "end": 11, "overrides": {"color": "#FF2020"}}],
        {"fontsize": 40, "color": "#F4EBD8"},
    )
    out, runs = document_to_runs(host.edit)
    assert out == text
    # Third-line CCC is indices 8-11 when newlines occupy 1 char each.
    colored = [r for r in runs if (r.get("overrides") or {}).get("color")]
    assert colored, runs
    assert colored[0]["start"] == 8, colored
    assert colored[0]["end"] == 11, colored
