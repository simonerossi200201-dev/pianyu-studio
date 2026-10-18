# -*- coding: utf-8 -*-
"""Q100: line_hug is a per-line slab, not a glyph outline and not one rectangle."""
from __future__ import annotations

import json
import os
from dataclasses import replace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Q93 / Q97 fit_text frame. Fixed 260×100 is the other byte Q97 pinned.
_FIT_MD5 = "8a063bb50cd86a78515f330072ee72a3"
_FIXED_MD5 = "587663ebde006cdba7c0a17a476a58bb"
_QW, _QH = 480, 270
_W, _H = 900, 520


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q100-line"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _overlay(text="ABCDEF\naoe", size=72, *, w=820, h=440, bold=True, **bg_extra):
    bg = {
        "enabled": True,
        "type": "box",
        "color": "#FF00AA",
        "alpha": 1.0,
        "radius_em": 0.0,
        "pad_em": [0.4, 0.25],
        "size_mode": "line_hug",
        "hug_pad_em": 0.0,
    }
    bg.update(bg_extra)
    return {
        "type": "text",
        "content": text,
        "start": 0.0,
        "end": 2.0,
        "id": "t",
        "x": 40,
        "y": 40,
        "w": w,
        "h": h,
        "font_family": "Arimo",
        "fontsize": size,
        "bold": bold,
        "color": "#FFFFFF",
        "text_layout": {"align": "center"},
        "fx": {
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "stroke": {"width_em": 0, "color": "#000000", "enabled": False},
            "glow": {"sigma_em": 0, "enabled": False},
            "shadow": {"strength": 0, "enabled": False},
            "bg": bg,
        },
    }


def _q93_overlay(**bg_extra):
    """Same box Q93 hashed. Do not move x/y/size — the md5 is pinned to it."""
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


def _task(ov, w, h):
    return {
        "dims": (w, h),
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


def _mad(a, b) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def _alpha(arr):
    return arr[:, :, 3]


def _paint(ov, *, w=_W, h=_H, clear=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#101010") if clear is None else clear)
    p = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            p, _task(ov, w, h), w, h, 1.0, render_order=["text"])
    finally:
        p.end()
    return _bgra(img)


def _export(ov, *, w=_W, h=_H, clear="#101010"):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, w, h, QColor(clear))

    return _bgra(ExportRenderer.render_frame(
        _task(ov, w, h), w, h, 1.0, draw_background=_bg))


def _placed(ov, w=_W, h=_H):
    from cutora.render.text_layer_paint import (
        _apply_first_indent, _fit_to_box, _place_in_box, _recompute_roi,
    )
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.text_layer import (
        overlay_event, overlay_text_layout, overlay_to_style,
    )
    style = overlay_to_style(ov, w, h)
    event = overlay_event(ov)
    spec = from_style(style)
    layout = layout_event(event, style, spec, w, h)
    layout, spec, style = _fit_to_box(
        layout, (ov["x"], ov["y"], ov["w"], ov["h"]), style, spec, event, w, h)
    _apply_first_indent(layout, float(style.get("_first_indent") or 0))
    lay = overlay_text_layout(ov)
    _place_in_box(
        layout,
        (float(ov["x"]), float(ov["y"]), float(ov["w"]), float(ov["h"])),
        str(lay.get("align") or "left"), lay)
    _recompute_roi(layout, spec, w, h)
    return layout, spec


def _blit_alpha(canvas, alpha, ox, oy):
    """Paste an ROI alpha. Q142 origin may sit outside the canvas."""
    ah, aw = alpha.shape[:2]
    ch, cw = canvas.shape[:2]
    x0 = int(ox)
    y0 = int(oy)
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    dw = min(aw - sx0, cw - dx0)
    dh = min(ah - sy0, ch - dy0)
    if dw <= 0 or dh <= 0:
        return
    canvas[dy0:dy0 + dh, dx0:dx0 + dw] = alpha[sy0:sy0 + dh, sx0:sx0 + dw]


def _bg_alpha(ov, w=_W, h=_H, spec=None):
    """Background layer only, in canvas pixels. Glyphs are not in this image."""
    from cutora.render.textfx.raster import as_bgra, raster_bg
    layout, built = _placed(ov, w, h)
    if spec is None:
        spec = built
    img = raster_bg(layout, spec, None, w, h, skip_blur=False)
    canvas = np.zeros((h, w), np.float32)
    if img is None:
        return canvas, layout, spec
    _owned, bgra = as_bgra(img)
    alpha = bgra[:, :, 3].astype(np.float32)
    ox, oy = layout.origin
    _blit_alpha(canvas, alpha, ox, oy)
    return canvas, layout, spec


def _drop_layer_cache():
    """Finished layers live in two places. A stub or a forced mask miss
    never runs while either copy is still stored."""
    from cutora.render.textfx import clear_roi_cache
    from cutora.render.text_layer_paint import clear_static_cache
    clear_roi_cache()
    clear_static_cache()


def _row_span(alpha, y, thr=128):
    xs = np.nonzero(alpha[int(y)] > thr)[0]
    assert xs.size > 0, y
    return int(xs.min()), int(xs.max()), int(xs.max() - xs.min() + 1)


def _col_runs(alpha, x, thr=128):
    ys = np.nonzero(alpha[:, int(x)] > thr)[0]
    assert ys.size > 0, x
    runs = []
    start = prev = int(ys[0])
    for y in ys[1:]:
        y = int(y)
        if y > prev + 1:
            runs.append((start, prev, prev - start + 1))
            start = y
        prev = y
    runs.append((start, prev, prev - start + 1))
    return runs


def _top_bot(boxes):
    return (boxes[0], boxes[1]) if boxes[0][1] <= boxes[1][1] else (boxes[1], boxes[0])


def test_q100_not_one_rectangle():
    """Two centered lines: the wide line's right edge overhangs, and the
    big rectangle's bottom-right pixel stays clear."""
    _qapp()
    _fonts()
    ov = _overlay(radius_em=0.0, hug_pad_em=0.0)
    alpha, layout, spec = _bg_alpha(ov)
    from cutora.render.textfx.raster import line_hug_boxes
    boxes = line_hug_boxes(layout, spec)
    assert len(boxes) == 2
    top, bot = _top_bot(boxes)
    assert (top[2] - top[0]) > (bot[2] - bot[0]) + 40
    y_top = int(round((top[1] + top[3]) * 0.5))
    y_bot = int(round((bot[1] + bot[3]) * 0.5))
    _x0, top_right, _w = _row_span(alpha, y_top)
    _x1, bot_right, _w2 = _row_span(alpha, y_bot)
    text_half = (layout.lines[0].w - layout.lines[1].w) * 0.5
    assert top_right - bot_right >= text_half - 2, (
        top_right, bot_right, text_half)
    x = int(np.floor(max(b[2] for b in boxes))) - 1
    y = int(np.floor(max(b[3] for b in boxes))) - 1
    assert int(alpha[y, x]) == 0, (x, y, int(alpha[y, x]))
    from PySide6.QtGui import QColor
    preview = _paint(ov, clear=QColor(0, 0, 0, 0))
    assert int(_alpha(preview)[y, x]) == 0
    assert _mad(_paint(ov), _export(ov)) == 0.0


def test_q100_two_lines_share_em_height():
    """ABCDEF and aoe slabs differ by at most 1px. Ink height would not."""
    _qapp()
    _fonts()
    ov = _overlay(text="ABCDEF\naoe", radius_em=0.0, hug_pad_em=0.0)
    alpha, layout, spec = _bg_alpha(ov)
    from cutora.render.textfx.raster import line_hug_boxes
    top, bot = _top_bot(line_hug_boxes(layout, spec))
    x_over = int(round((bot[2] + top[2]) * 0.5))
    h1 = _col_runs(alpha, x_over)[0][2]
    x_mid = int(round((bot[0] + bot[2]) * 0.5))
    runs = _col_runs(alpha, x_mid)
    if len(runs) == 1:
        h2 = runs[0][2] - h1
    else:
        h2 = runs[-1][2]
    assert h1 > 80 and h2 > 80, (h1, h2)
    assert abs(h1 - h2) <= 1, (h1, h2, runs)


def test_q100_concave_corner_is_round():
    """The step between a wide line and a narrow one fills in as radius grows,
    and the 45° walk through that corner is a ramp, not one 255 drop."""
    _qapp()
    _fonts()
    from cutora.render.textfx.raster import line_hug_boxes

    def _diag(radius):
        ov = _overlay(radius_em=radius, hug_pad_em=0.0)
        alpha, layout, spec = _bg_alpha(ov)
        top, bot = _top_bot(line_hug_boxes(layout, spec))
        if (top[2] - top[0]) >= (bot[2] - bot[0]):
            seam_x, seam_y, dx, dy = bot[2], top[3], 1, 1
        else:
            seam_x, seam_y, dx, dy = top[2], bot[1], 1, -1
        cx, cy = int(round(seam_x)), int(round(seam_y))
        alphas = []
        for k in range(32):
            x, y = cx + dx * k, cy + dy * k
            if 0 <= y < alpha.shape[0] and 0 <= x < alpha.shape[1]:
                alphas.append(int(alpha[y, x]))
            else:
                alphas.append(0)
        x0 = int(np.floor(min(top[2], bot[2])))
        x1 = int(np.ceil(max(top[2], bot[2])))
        y0 = int(np.floor(min(top[3], bot[1])))
        y1 = int(np.ceil(max(top[3], bot[3])))
        filled = int((alpha[y0:y1, x0:x1] > 128).sum())
        return alphas, filled

    sharp, filled0 = _diag(0.0)
    round_, filled4 = _diag(0.4)
    mids = [a for a in round_ if 5 < a < 250]
    assert len(mids) >= 3, round_[:16]
    drops = [abs(round_[i] - round_[i + 1]) for i in range(len(round_) - 1)]
    assert max(drops) < 200, max(drops)
    def _cross(alphas):
        for i, a in enumerate(alphas):
            if a < 128:
                return i
        return len(alphas)
    assert _cross(round_) >= _cross(sharp) + 4, (sharp[:12], round_[:12])
    assert filled4 >= filled0 + 80, (filled0, filled4)


def _corner_pixels(box):
    x0 = int(np.floor(box[0]))
    y0 = int(np.floor(box[1]))
    x1 = int(np.ceil(box[2])) - 1
    y1 = int(np.ceil(box[3])) - 1
    return ((x0, y0), (x1, y0), (x0, y1), (x1, y1))


def test_q100_convex_corners_are_round():
    """Outer corners of each slab are painted at radius 0 and clear at 0.4.
    The seam corners (covered by the other line) stay painted."""
    _qapp()
    _fonts()
    from cutora.render.textfx.raster import line_hug_boxes
    sharp_a, layout, spec = _bg_alpha(
        _overlay(radius_em=0.0, hug_pad_em=0.12))
    round_a, _l2, _s2 = _bg_alpha(
        _overlay(radius_em=0.4, hug_pad_em=0.12))
    boxes = line_hug_boxes(layout, spec)
    outer = 0
    inner = 0
    for box in boxes:
        for x, y in _corner_pixels(box):
            a0 = int(sharp_a[y, x])
            a1 = int(round_a[y, x])
            if a0 < 200:
                outer += 1
                assert a0 > 80, (x, y, a0)
                assert a1 < 16, (x, y, a1)
            else:
                inner += 1
                assert a1 > 200, (x, y, a0, a1)
    assert outer >= 6, outer
    assert inner >= 1, inner
    wide = max(boxes, key=lambda b: b[2] - b[0])
    wide_outer = 0
    for x, y in _corner_pixels(wide):
        if int(sharp_a[y, x]) < 200:
            wide_outer += 1
            assert int(round_a[y, x]) < 16
    assert wide_outer == 4, wide_outer


def test_q100_shrink_is_narrower_than_the_line():
    """hug_pad_em -0.1 pulls each edge in, so the slab is 0.2 em narrower."""
    _qapp()
    _fonts()
    ov = _overlay(text="ABCDEF", radius_em=0.0, hug_pad_em=-0.1)
    alpha, layout, _spec = _bg_alpha(ov)
    line = layout.lines[0]
    y = int(round(line.baseline))
    _x0, _x1, width = _row_span(alpha, y)
    expect = 0.1 * float(layout.em) * 2.0
    assert abs((float(line.w) - width) - expect) <= 2.0, (
        line.w, width, expect)
    assert _mad(_paint(ov), _export(ov)) == 0.0


def test_q100_radius_is_clamped_so_the_slab_survives():
    """radius_em 2 is far past the line height. The opening must not erase it."""
    _qapp()
    _fonts()
    from cutora.render.textfx.raster import line_hug_boxes, line_hug_radius_px, raster_bg
    ov = _overlay(text="ABCDEF\naoe", radius_em=2.0, hug_pad_em=0.0)
    _alpha_img, layout, spec = _bg_alpha(ov)
    forced = replace(spec, bg=replace(spec.bg, radius_em=2.0))
    boxes = line_hug_boxes(layout, forced)
    min_h = min(b[3] - b[1] for b in boxes)
    min_w = min(b[2] - b[0] for b in boxes)
    radius = line_hug_radius_px(layout, forced)
    assert radius <= min_h * 0.5 + 1e-6
    assert radius <= min_w * 0.5 + 1e-6
    assert radius < 2.0 * float(layout.em) * 0.5
    img = raster_bg(layout, forced, None, _W, _H, skip_blur=False)
    assert img is not None
    from cutora.render.textfx.raster import as_bgra
    _owned, bgra = as_bgra(img)
    assert int((bgra[:, :, 3] > 0).sum()) > 0
    preview = _paint(ov)
    assert _mad(preview, _export(ov)) == 0.0
    assert int((_alpha(preview) > 0).sum()) > 1000


def test_q100_gradient_stub_matches_solid_mask():
    """Stubbing the gradient painter to a flat fill makes the whole frame
    match the solid slab. The mask, not the fill, is the shape."""
    _qapp()
    _fonts()
    common = dict(radius_em=0.25, hug_pad_em=0.12, alpha=1.0, color="#FF00AA")
    box = _overlay(**common, type="box")
    grad = _overlay(
        **common, type="box",
        grad_type="linear_h", grad_colors=["#00FF66", "#2266FF"],
    )
    raw_box = _paint(box)
    raw_grad = _paint(grad)
    assert _mad(raw_box, raw_grad) > 0.5, _mad(raw_box, raw_grad)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    import cutora.render.textfx.raster as raster

    def _solid(painter, local, local_path, spec, overlay):
        painter.save()
        try:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(spec.bg.color))
            painter.setOpacity(float(spec.bg.alpha))
            painter.drawPath(local_path)
        finally:
            painter.restore()
        return True

    orig = raster._paint_bg_grad
    raster._paint_bg_grad = _solid
    try:
        _drop_layer_cache()
        flat_box = _paint(box)
        flat_grad = _paint(grad)
    finally:
        raster._paint_bg_grad = orig
    assert _mad(flat_box, flat_grad) == 0.0
    assert _mad(_paint(box), _export(box)) == 0.0


def test_q100_preview_matches_export_and_cache_is_used(monkeypatch):
    """One mask build covers preview and export. A cache that always misses
    builds it twice."""
    _qapp()
    _fonts()
    import cutora.render.textfx.raster as raster
    ov = _overlay(radius_em=0.3, hug_pad_em=0.16)
    raster.clear_hug_mask_cache()
    preview = _paint(ov)
    exported = _export(ov)
    assert raster.hug_mask_builds() == 1, raster.hug_mask_builds()
    assert _mad(preview, exported) == 0.0

    real = raster.hug_glyph_mask

    def _always_miss(layout, spec):
        with raster._HUG_LOCK:
            raster._HUG_CACHE.clear()
        return real(layout, spec)

    monkeypatch.setattr(raster, "hug_glyph_mask", _always_miss)
    _drop_layer_cache()
    raster.clear_hug_mask_cache()
    _paint(ov)
    # Export would reuse the layer _paint just stored and never rebuild.
    _drop_layer_cache()
    _export(ov)
    assert raster.hug_mask_builds() == 2, raster.hug_mask_builds()


def test_q100_negative_pad_does_not_shrink_glyph_hug():
    """hug_glyphs clamps hug_pad_em at 0, so a negative value matches pad 0."""
    _qapp()
    _fonts()
    zero = _overlay(
        text="Hi", size=64, size_mode="hug_glyphs",
        hug_pad_em=0.0, radius_em=0.0, alpha=1.0,
    )
    neg = _overlay(
        text="Hi", size=64, size_mode="hug_glyphs",
        hug_pad_em=-0.1, radius_em=0.0, alpha=1.0,
    )
    assert np.array_equal(_paint(zero), _paint(neg))
    assert _mad(_paint(zero), _export(zero)) == 0.0


def test_q100_fit_and_fixed_match_q93_bytes():
    """The three older modes keep the bytes Q93 / Q97 shipped."""
    _qapp()
    _fonts()
    fit = _paint(_q93_overlay(), w=_QW, h=_QH)
    assert _md5(fit) == _FIT_MD5
    explicit = _paint(_q93_overlay(size_mode="fit_text"), w=_QW, h=_QH)
    assert _md5(explicit) == _FIT_MD5
    exported = _export(_q93_overlay(), w=_QW, h=_QH)
    assert _md5(exported) == _FIT_MD5
    assert _mad(fit, exported) == 0.0
    fixed_ov = _q93_overlay(
        size_mode="fixed", width_px=260, height_px=100,
        offset_x=24, offset_y=-16,
    )
    fixed = _paint(fixed_ov, w=_QW, h=_QH)
    assert _md5(fixed) == _FIXED_MD5
    assert _mad(fixed, _export(fixed_ov, w=_QW, h=_QH)) == 0.0


def _row_label(widget):
    from PySide6.QtWidgets import QWidget
    from cutora.ui.widgets.inspector import inspector_row_of
    row = inspector_row_of(widget)
    if row is not None:
        return row._label_text
    parent = widget.parentWidget()
    lay = QWidget.layout(parent) if parent is not None else None
    if lay is None:
        return ""
    lab = lay.labelForField(widget)
    return lab.text() if lab is not None else ""


def _set_mode(panel, mode):
    combo = panel.fx_bg_size_mode
    idx = combo.findData(mode)
    assert idx >= 0, mode
    combo.setCurrentIndex(idx)
    assert combo.currentData() == mode


def _expand(widget):
    from cutora.ui.widgets.collapsible import CollapsibleSection
    cur = widget
    while cur is not None:
        if isinstance(cur, CollapsibleSection):
            cur.setExpanded(True)
        cur = cur.parentWidget()


def test_q100_panel_line_hug_rows_roundtrip():
    """行轮廓 shows 扩展/收缩, 圆角 and 偏移, hides 宽/高/内边距. Values stick."""
    _qapp()
    _fonts()
    from PySide6.QtWidgets import QApplication
    from cutora.render.textfx.spec import HUG_PAD_EM_DEFAULT, HUG_PAD_EM_MIN, from_style
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    missing = from_style({"fx": {"bg": {"type": "box", "enabled": True}}})
    assert missing.bg.hug_pad_em == pytest.approx(HUG_PAD_EM_DEFAULT)
    assert missing.bg.size_mode == "fit_text"
    low = from_style({"fx": {"bg": {"hug_pad_em": -0.9, "size_mode": "line_hug"}}})
    assert low.bg.hug_pad_em == pytest.approx(HUG_PAD_EM_MIN)
    assert low.bg.size_mode == "line_hug"
    high = from_style({"fx": {"bg": {"hug_pad_em": 1.5}}})
    assert high.bg.hug_pad_em == pytest.approx(1.0)

    ov = _overlay(size_mode="fit_text", type="box")
    panel = PropertyPanel()
    panel.resize(420, 900)
    panel.show()
    panel.load_data("overlay", ov)
    panel.ov_bg_grp.setExpanded(True)
    QApplication.processEvents()
    combo = panel.fx_bg_size_mode
    idx = combo.findData("line_hug")
    assert idx >= 0
    assert combo.itemText(idx) == "行轮廓"
    assert _row_label(panel.fx_bg_hug_pad_em) == "扩展 / 收缩"
    assert panel.fx_bg_hug_pad_em.spin.minimum() == pytest.approx(-0.3)
    assert panel.fx_bg_hug_pad_em.spin.maximum() == pytest.approx(1.0)
    assert panel.fx_bg_hug_pad_em.isHidden()
    assert not panel.fx_bg_radius_em.isHidden()

    _set_mode(panel, "line_hug")
    QApplication.processEvents()
    assert not panel.fx_bg_hug_pad_em.isHidden()
    assert not panel.fx_bg_radius_em.isHidden()
    assert not panel.fx_bg_offset_x.isHidden()
    assert not panel.fx_bg_offset_y.isHidden()
    assert panel.fx_bg_hug_join_em.isHidden()
    assert panel.fx_bg_width_px.isHidden()
    assert panel.fx_bg_height_px.isHidden()
    assert panel.fx_bg_pad_em.isHidden()

    panel.fx_bg_hug_pad_em.setValue(-0.1)
    panel.fx_bg_radius_em.setValue(0.4)
    panel.fx_bg_offset_x.setValue(8)
    panel.fx_bg_offset_y.setValue(-4)
    host = type("H", (), {})()
    saved = MainWindow._sanitize_overlays(host, [ov])
    blob = json.loads(json.dumps(saved))
    panel.close()
    panel2 = PropertyPanel()
    panel2.resize(420, 900)
    panel2.show()
    panel2.load_data("overlay", blob[0])
    panel2.ov_bg_grp.setExpanded(True)
    QApplication.processEvents()
    assert panel2.fx_bg_size_mode.currentData() == "line_hug"
    assert abs(float(panel2.fx_bg_hug_pad_em.value()) - (-0.1)) < 0.011
    assert abs(float(panel2.fx_bg_radius_em.value()) - 0.4) < 0.011
    assert int(panel2.fx_bg_offset_x.value()) == 8
    assert int(panel2.fx_bg_offset_y.value()) == -4
    assert not panel2.fx_bg_hug_pad_em.isHidden()
    assert not panel2.fx_bg_radius_em.isHidden()
    assert panel2.fx_bg_width_px.isHidden()
    assert panel2.fx_bg_pad_em.isHidden()
    assert panel2.fx_bg_hug_join_em.isHidden()
    panel2.close()

    sub = SubtitleStylePanel()
    sub.resize(420, 900)
    sub.show()
    sub.load_data({
        "font_family": "Arimo", "fontsize": 48,
        "fx": {"bg": {
            "enabled": True, "type": "box", "size_mode": "line_hug",
            "hug_pad_em": -0.2, "radius_em": 0.35,
        }},
    })
    _expand(sub.fx_bg_hug_pad_em)
    QApplication.processEvents()
    assert sub.fx_bg_size_mode.currentData() == "line_hug"
    assert not sub.fx_bg_hug_pad_em.isHidden()
    assert not sub.fx_bg_radius_em.isHidden()
    assert not sub.fx_bg_offset_x.isHidden()
    assert sub.fx_bg_hug_join_em.isHidden()
    assert sub.fx_bg_width_px.isHidden()
    assert sub.fx_bg_height_px.isHidden()
    assert sub.fx_bg_pad_em.isHidden()
    assert abs(float(sub.fx_bg_hug_pad_em.value()) - (-0.2)) < 0.011
    assert _row_label(sub.fx_bg_hug_pad_em) == "扩展 / 收缩"
    _set_mode(sub, "hug_glyphs")
    QApplication.processEvents()
    assert sub.fx_bg_radius_em.isHidden()
    assert not sub.fx_bg_hug_join_em.isHidden()
    assert not sub.fx_bg_hug_pad_em.isHidden()
    sub.close()
