# -*- coding: utf-8 -*-
"""Q93: fx background size. Old projects (no size keys) stay byte-identical."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

W, H = 480, 270
# Captured before the size fields existed (same overlay, preview == export).
_FIT_MD5 = "8a063bb50cd86a78515f330072ee72a3"


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q93-bg"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _overlay(**bg_extra):
    bg = {
        "enabled": True,
        "type": "box",
        "color": "#FF00AA",
        "alpha": 1.0,
        "radius_em": 0.0,
        "pad_em": [0.4, 0.25],
    }
    bg.update(bg_extra)
    return {
        "type": "text",
        "content": "Hi",
        "start": 0.0,
        "end": 2.0,
        "id": "t",
        "x": 140,
        "y": 90,
        "w": 200,
        "h": 80,
        "font_family": "Arimo",
        "fontsize": 48,
        "bold": True,
        "color": "#FFFFFF",
        "fx": {
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "stroke": {"width_em": 0, "color": "#000000", "enabled": False},
            "glow": {"sigma_em": 0, "enabled": False},
            "shadow": {"strength": 0, "enabled": False},
            "bg": bg,
        },
    }


def _task(ov):
    return {
        "dims": (W, H),
        "segments": [],
        "text_overlays": [ov],
        "image_overlays": [],
        "style": {},
    }


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4]
    return arr.reshape((h, w, 4)).copy()


def _md5(arr) -> str:
    import hashlib
    return hashlib.md5(arr.tobytes()).hexdigest()


def _preview(ov):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#101010"))
    p = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            p, _task(ov), W, H, 1.0, render_order=["text"])
    finally:
        p.end()
    return _bgra(img)


def _export(ov):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, W, H, QColor("#101010"))

    return _bgra(ExportRenderer.render_frame(
        _task(ov), W, H, 1.0, draw_background=_bg))


def _mad(a, b) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def _bg_bbox(arr):
    """Bounding box of the magenta panel (not the white glyphs)."""
    r = arr[:, :, 2].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 0].astype(np.int16)
    mask = (r > 180) & (b > 120) & (g < 80)
    ys, xs = np.nonzero(mask)
    assert xs.size > 50, "magenta background missing"
    return (
        int(xs.min()), int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    )


def test_q93_bg_fit_text_matches_old_bytes():
    """No size keys, and an explicit fit_text, both match the pre-change frame."""
    _qapp()
    _fonts()
    old = _preview(_overlay())
    assert _md5(old) == _FIT_MD5
    explicit = _preview(_overlay(size_mode="fit_text"))
    assert _md5(explicit) == _FIT_MD5
    exported = _export(_overlay())
    assert _md5(exported) == _FIT_MD5
    assert _mad(old, exported) < 0.01


def test_q93_bg_fixed_size_px_preview_matches_export():
    """fixed width/height are canvas pixels. Preview and export agree."""
    _qapp()
    _fonts()
    ov = _overlay(
        size_mode="fixed", width_px=260, height_px=100,
        offset_x=24, offset_y=-16,
    )
    preview = _preview(ov)
    export = _export(ov)
    assert _mad(preview, export) < 0.01, _mad(preview, export)
    x, y, bw, bh = _bg_bbox(preview)
    assert abs(bw - 260) <= 1, (bw, bh, x, y)
    assert abs(bh - 100) <= 1, (bw, bh, x, y)
    fit = _preview(_overlay())
    fx, fy, fw, fh = _bg_bbox(fit)
    fit_cx = fx + fw / 2.0
    fit_cy = fy + fh / 2.0
    got_cx = x + bw / 2.0
    got_cy = y + bh / 2.0
    assert abs((got_cx - fit_cx) - 24) <= 1.0, (got_cx, fit_cx)
    assert abs((got_cy - fit_cy) - (-16)) <= 1.0, (got_cy, fit_cy)
    assert _md5(preview) != _FIT_MD5


def test_q93_bg_size_panel_updates_and_reopens():
    """背景 section: 尺寸 dropdown, px fields only in fixed. Save survives reopen."""
    _qapp()
    _fonts()
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.property import PropertyPanel

    ov = _overlay()
    before = _preview(ov)
    panel = PropertyPanel()
    panel.resize(420, 800)
    panel.show()
    panel.load_data("overlay", ov)
    panel.ov_bg_grp.setExpanded(True)
    assert panel.fx_bg_size_mode.currentData() == "fit_text"
    assert panel.fx_bg_width_px.isHidden()
    idx = panel.fx_bg_size_mode.findData("fixed")
    assert idx >= 0
    panel.fx_bg_size_mode.setCurrentIndex(idx)
    assert not panel.fx_bg_width_px.isHidden()
    assert not panel.fx_bg_height_px.isHidden()
    assert not panel.fx_bg_offset_x.isHidden()
    assert not panel.fx_bg_offset_y.isHidden()
    panel.fx_bg_width_px.setValue(260)
    panel.fx_bg_height_px.setValue(100)
    panel.fx_bg_offset_x.setValue(24)
    panel.fx_bg_offset_y.setValue(-16)
    bg = ov["fx"]["bg"]
    assert bg["size_mode"] == "fixed"
    assert int(bg["width_px"]) == 260
    assert int(bg["height_px"]) == 100
    assert int(bg["offset_x"]) == 24
    assert int(bg["offset_y"]) == -16
    after = _preview(ov)
    assert _md5(after) != _md5(before)
    x, y, bw, bh = _bg_bbox(after)
    assert abs(bw - 260) <= 1 and abs(bh - 100) <= 1, (bw, bh)

    host = type("H", (), {})()
    saved = MainWindow._sanitize_overlays(host, [ov])
    blob = json.loads(json.dumps(saved))
    panel.close()
    panel2 = PropertyPanel()
    panel2.load_data("overlay", blob[0])
    assert panel2.fx_bg_size_mode.currentData() == "fixed"
    assert int(panel2.fx_bg_width_px.value()) == 260
    assert int(panel2.fx_bg_height_px.value()) == 100
    assert int(panel2.fx_bg_offset_x.value()) == 24
    assert int(panel2.fx_bg_offset_y.value()) == -16
    panel2.ov_bg_grp.setExpanded(True)
    assert not panel2.fx_bg_width_px.isHidden()
    panel2.close()
