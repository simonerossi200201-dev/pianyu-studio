# -*- coding: utf-8 -*-
"""Q27: shared textfx bg, rich runs, image-element removal, overlay migrate."""
from __future__ import annotations

import hashlib
import os
import re

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t27-text-layer"])


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


def _md5(img) -> str:
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    return hashlib.md5(bytes(conv.constBits())[: conv.height() * conv.bytesPerLine()]).hexdigest()


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    h, w = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4).copy()


def _checker(w=CANVAS_W, h=CANVAS_H):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        cell = 24
        for y in range(0, h, cell):
            for x in range(0, w, cell):
                on = ((x // cell) + (y // cell)) % 2 == 0
                p.fillRect(x, y, cell, cell, QColor("#C8A24A" if on else "#1C1815"))
    finally:
        p.end()
    return img


# ── image element gone ────────────────────────────────────────────────────


def test_t27_image_element_gone():
    forbidden = (
        r'CollapsibleSection\(\s*["\']图片元素["\']',
        r"image_element_group",
        r"insert_image_text_element",
        r"insert_image_shape_element",
        r"text_add_btn",
        r"shape_add_btn",
        r'["\']图片元素["\']',
    )
    hits = []
    for root, _dirs, files in os.walk(CUTORA):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            src = open(path, encoding="utf-8").read()
            rel = os.path.relpath(path, REPO).replace("\\", "/")
            for pat in forbidden:
                for m in re.finditer(pat, src):
                    line = src[: m.start()].count("\n") + 1
                    hits.append("%s:%d %s" % (rel, line, pat))
    assert hits == [], hits

    _qapp()
    from cutora.ui.panels.property import PropertyPanel
    panel = PropertyPanel()
    labels = []
    from PySide6.QtWidgets import QLabel
    for lab in panel.findChildren(QLabel):
        labels.append(lab.text() or "")
    from cutora.ui.widgets.dock_panels import CollapsibleSection
    titles = []
    for sec in panel.findChildren(CollapsibleSection):
        titles.append(getattr(sec, "title", lambda: "")() if callable(getattr(sec, "title", None)) else "")
        titles.append(sec.objectName() or "")
        titles.append(sec.windowTitle() or "")
    blob = " ".join(labels + titles)
    assert "图片元素" not in blob, blob

    from cutora.subtitle.text_layer import migrate_open_project, REMOVED_IMAGE_ELEMENT_MSG
    data = {
        "version": 2,
        "tasks": [{
            "text_overlays": [],
            "image_overlays": [{
                "type": "image",
                "start": 0, "end": 4,
                "elements": [
                    {"kind": "text", "content": "gone"},
                    {"kind": "shape", "shape": "rect"},
                    {"kind": "image", "sticker_id": "1f525", "x": 0, "y": 0, "w": 40, "h": 40},
                ],
            }],
        }],
    }
    n, msg = migrate_open_project(data)
    assert n == 2, (n, msg)
    assert msg == REMOVED_IMAGE_ELEMENT_MSG % 2
    kept = data["tasks"][0]["image_overlays"][0]["elements"]
    assert len(kept) == 1
    assert kept[0]["sticker_id"] == "1f525"


# ── shared bg ─────────────────────────────────────────────────────────────


def test_t27_bg_shared_impl():
    from cutora.render.fx_bg import raster_bg as shared
    from cutora.render.textfx.raster import raster_bg as sub_fn
    from cutora.render.text_layer_paint import raster_bg as text_fn
    assert shared is sub_fn
    assert text_fn is sub_fn
    assert id(sub_fn) == id(text_fn) == id(shared)


# ── glass / texture ───────────────────────────────────────────────────────


def _vgrad(w=CANVAS_W, h=CANVAS_H):
    """Vertical red→blue gradient so glass must sample a real color ramp."""
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QBrush
    from PySide6.QtCore import QPointF
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        g = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        g.setColorAt(0.0, QColor("#E02020"))
        g.setColorAt(1.0, QColor("#2020E0"))
        p.fillRect(0, 0, w, h, QBrush(g))
    finally:
        p.end()
    return img


def test_t27_text_glass():
    _qapp()
    _fonts()
    backdrop = _vgrad()
    ov = _legacy_overlay(content=" ")
    ov["y"] = 80
    ov["h"] = 900
    ov["fx"] = {
        "fill": {"type": "solid", "colors": ["#F4EBD8"]},
        "stroke": {"width_em": 0},
        "bg": {
            "type": "glass",
            "alpha": 0.55,
            "radius_em": 0.4,
            "pad_em": [0.6, 0.5],
            "glass_thickness_em": 0.14,
            "glass_refraction": 0.55,
        },
        "shadow": {"distance_em": 0, "enabled": False},
    }
    for key in (
        "bg_enabled", "bg_color", "bg_opacity", "bg_padding", "bg_radius",
        "use_stroke", "stroke_width", "stroke_color",
        "shadow_enabled",
    ):
        ov.pop(key, None)
    task = {"text_overlays": [ov], "segments": [], "style": {}}
    img = _paint_layers(task, backdrop=backdrop, fill="#000000")
    arr = _bgra(img)
    # Sample glass pad above vs below the (tiny) glyph, inside the overlay box.
    x = int(ov["x"] + ov["w"] * 0.5)
    y_top = int(ov["y"] + 40)
    y_bot = int(ov["y"] + ov["h"] - 40)
    top = arr[y_top, x].astype(np.int16)  # BGRA
    bot = arr[y_bot, x].astype(np.int16)
    # Backdrop is red at top (high R=channel 2) and blue at bottom (high B=ch 0).
    assert int(top[2]) > int(top[0]) + 20, (top.tolist(), bot.tolist())
    assert int(bot[0]) > int(bot[2]) + 20, (top.tolist(), bot.tolist())
    assert int(top[2]) > int(bot[2]) + 25, (top.tolist(), bot.tolist())


def test_t27_text_texture():
    _qapp()
    _fonts()
    hashes = []
    for name in ("paper", "linen", "grid"):
        ov = _legacy_overlay(content="TEX")
        ov["fx"] = {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "bg": {
                "type": "texture",
                "texture": name,
                "texture_tint": "#F1E9D8",
                "alpha": 0.9,
                "pad_em": [0.4, 0.3],
            },
            "shadow": {"distance_em": 0},
        }
        for key in (
            "bg_enabled", "bg_color", "bg_opacity", "use_stroke",
            "stroke_width", "shadow_enabled",
        ):
            ov.pop(key, None)
        task = {"text_overlays": [ov], "segments": [], "style": {}}
        img = _paint_layers(task)
        hashes.append(_md5(img))
    assert hashes[0] != hashes[1], hashes
    assert hashes[1] != hashes[2], hashes
    assert hashes[0] != hashes[2], hashes


# ── rich runs ─────────────────────────────────────────────────────────────


def test_t27_rich_runs():
    _qapp()
    _fonts()
    base = _legacy_overlay(content="ABCDEFGH", bg_enabled=False, use_stroke=False, shadow_enabled=False)
    base["color"] = "#F4EBD8"
    base.pop("bg_color", None)
    plain = dict(base)
    rich = dict(base)
    rich["fx"] = {
        "fill": {"type": "solid", "colors": ["#F4EBD8"]},
        "stroke": {"width_em": 0},
        "bg": {"type": "none"},
        "shadow": {"distance_em": 0},
    }
    # 第 3–5 个字符 = indices 2,3,4 → CDE
    rich["runs"] = [{"start": 2, "end": 5, "overrides": {"color": "#FF2020"}}]
    img_a = _paint_layers({"text_overlays": [plain], "segments": [], "style": {}})
    img_b = _paint_layers({"text_overlays": [rich], "segments": [], "style": {}})
    a = _bgra(img_a)[:, :, 2].astype(np.float64)  # R in BGRA
    b = _bgra(img_b)[:, :, 2].astype(np.float64)
    y0, y1 = int(base["y"]), int(base["y"] + base["h"])
    x0, x1 = int(base["x"]), int(base["x"] + base["w"])
    col_a = a[y0:y1, x0:x1].mean(axis=0)
    col_b = b[y0:y1, x0:x1].mean(axis=0)
    delta = col_b - col_a
    hot = np.where(delta > 4.0)[0]
    assert len(hot) > 0, "expected redder columns on chars 3–5"
    # redder columns should sit in the middle of the box, not the whole width
    assert hot[0] > 5, hot[0]
    assert hot[-1] < (x1 - x0) - 5, (hot[-1], x1 - x0)
    cold_left = float(delta[: max(1, int(hot[0] * 0.4))].mean()) if hot[0] > 8 else 0.0
    assert cold_left < 4.0, cold_left


def _paint_new_path(ov, t=1.0, w=CANVAS_W, h=CANVAS_H, fill="#142028"):
    """Force paint_text_overlay (new path), even when ov has no fx."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.text_layer_paint import paint_text_overlay
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        local = dict(ov)
        local["_canvas_w"] = w
        local["_canvas_h"] = h
        paint_text_overlay(p, local, current_time=t, video_w=w, video_h=h)
    finally:
        p.end()
    return img


def test_t27_no_runs_is_legacy():
    """Derived fx (no stored subtree) vs stored fx, both on the new path."""
    _qapp()
    _fonts()
    from cutora.subtitle.text_layer import overlay_fx_from_legacy
    ov = _legacy_overlay()
    assert "fx" not in ov
    img_a = _paint_new_path(ov)
    ov_b = dict(ov)
    ov_b["fx"] = overlay_fx_from_legacy(ov)
    img_b = _paint_new_path(ov_b)
    assert _md5(img_a) == _md5(img_b)


def test_t27_migration():
    """Picking flats into fx then popping them must not change new-path pixels."""
    _qapp()
    _fonts()
    from cutora.subtitle.text_layer import migrate_text_overlay, overlay_fx_from_legacy
    old = _legacy_overlay()
    converted = overlay_fx_from_legacy(old)
    assert converted["bg"]["type"] == "box"
    assert converted["stroke"]["width_em"] > 0
    assert converted["fill"]["colors"][0].upper().startswith("#F5B700")
    before = _paint_new_path(old)
    migrated = migrate_text_overlay(dict(old))
    assert "fx" in migrated
    assert migrated["fx"]["bg"]["type"] == "box"
    for key in ("bg_color", "bg_opacity", "stroke_width", "shadow_offset_x"):
        assert key not in migrated, key
    after = _paint_new_path(migrated)
    assert _md5(before) == _md5(after)


def test_t27_rich_toolbar_exists():
    _qapp()
    from cutora.ui.panels.text_rich import RichTextBar, RichTextEditor
    from PySide6.QtWidgets import QTextEdit
    host = RichTextEditor()
    assert host.bar.btn_bold is not None
    assert host.bar.btn_italic is not None
    assert host.bar.btn_underline is not None
    assert host.bar.btn_color is not None
    assert host.bar.size_spin is not None
    assert host.bar.btn_clear is not None
    assert isinstance(host.edit, QTextEdit)
    host.set_text_runs("ABCDEF", [{"start": 2, "end": 5, "overrides": {"color": "#FF0000"}}], {"fontsize": 40})
    text, runs = host.edit.toPlainText(), None
    assert host.edit.toPlainText() == "ABCDEF"
