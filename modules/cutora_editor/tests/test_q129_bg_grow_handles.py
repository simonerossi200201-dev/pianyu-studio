# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q129 · 背景跟着文字走：四边扩展（任何尺寸模式）、背景不再挤窄文字、
auto_height 文本不画上下边柄、工具条紧凑化。"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

VW, VH = 1080, 1920


def _app():
    app = QApplication.instance() or QApplication(["q129"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _ov(bg=None, **kw):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.subtitle.text_layer import ensure_overlay_fx
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH)
    ov.update({"content": "When God is leading a woman toward success", "x": 200, "y": 300, "w": 680})
    ov.update(kw)
    ensure_overlay_fx(ov)
    if bg is not None:
        ov["fx"]["bg"] = dict(bg)
    return ov


def _spec(ov):
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.text_layer import overlay_to_style
    return from_style(overlay_to_style(ov, VW, VH))


# ─────────────────────────── spec ───────────────────────────

def test_parse_grow_variants_and_default_zero():
    from cutora.render.textfx.spec import _parse_grow
    assert _parse_grow(None) == (0.0, 0.0, 0.0, 0.0)
    assert _parse_grow([1, 0.5, 0.25, 2]) == (1.0, 0.5, 0.25, 2.0)
    assert _parse_grow({"left": 0.3, "bottom": 0.7}) == (0.3, 0.0, 0.0, 0.7)
    assert _parse_grow(0.4) == (0.4, 0.4, 0.4, 0.4)
    assert _parse_grow([99, -99, "x", 1]) == (4.0, -0.5, 0.0, 1.0)   # 夹在 -0.5..4，坏值 0
    spec = _spec(_ov(bg={"type": "box", "enabled": True, "size_mode": "fit_text"}))
    assert spec.bg.grow_em == (0.0, 0.0, 0.0, 0.0)
    assert spec.bg.grow_px(80.0) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("mode", ["fit_text", "fixed"])
def test_panel_rect_grows_each_side_independently(mode):
    from cutora.render.textfx.layout import bg_panel_rect
    base = _spec(_ov(bg={"type": "box", "enabled": True, "size_mode": mode, "width_px": 400, "height_px": 200}))
    grown = _spec(_ov(bg={"type": "box", "enabled": True, "size_mode": mode, "width_px": 400, "height_px": 200,
                          "grow_em": [1.0, 0.5, 0.25, 2.0]}))
    em = 40.0
    r0 = bg_panel_rect(100, 100, 300, 120, base, em)
    r1 = bg_panel_rect(100, 100, 300, 120, grown, em)
    assert r1.left() == pytest.approx(r0.left() - 40.0)
    assert r1.top() == pytest.approx(r0.top() - 20.0)
    assert r1.right() == pytest.approx(r0.right() + 10.0)
    assert r1.bottom() == pytest.approx(r0.bottom() + 80.0)


def test_hit_box_and_roi_follow_growth():
    from cutora.render.text_layer_paint import layout_for_overlay
    _app()
    plain = _ov(bg={"type": "box", "enabled": True, "size_mode": "fit_text"})
    grown = _ov(bg={"type": "box", "enabled": True, "size_mode": "fit_text", "grow_em": [2.0, 0.0, 0.0, 2.0]})
    l0, *_ = layout_for_overlay(dict(plain), VW, VH)
    l1, *_ = layout_for_overlay(dict(grown), VW, VH)
    em = l0.em
    # ROI 至少把长出来的部分装下（左边 origin 更靠左，高度更大）；
    # 文字本身的位置不变（背景往外长，不推文字）
    assert l1.text_x == pytest.approx(l0.text_x) and l1.text_w == pytest.approx(l0.text_w)
    assert l1.origin[0] <= l0.origin[0] - int(2.0 * em) + 2
    assert l1.roi_h >= l0.roi_h + int(2.0 * em) - 2
    assert l1.roi_w >= l0.roi_w + int(2.0 * em) - 2


@pytest.mark.parametrize("mode", ["hug_glyphs", "line_hug"])
def test_hug_masks_grow_asymmetrically(mode):
    from cutora.render.text_layer_paint import layout_for_overlay
    from cutora.render.textfx.raster import hug_glyph_mask
    _app()
    base = _ov(content="HELLO", bg={"type": "box", "enabled": True, "size_mode": mode})
    grown = _ov(content="HELLO", bg={"type": "box", "enabled": True, "size_mode": mode,
                                     "grow_em": [1.0, 0.0, 0.25, 0.5]})

    def _bbox(ov):
        layout, spec, *_ = layout_for_overlay(dict(ov), VW, VH)
        m = hug_glyph_mask(layout, spec)
        ys, xs = np.where(m > 0.5)
        ox, oy = layout.origin
        return xs.min() + ox, ys.min() + oy, xs.max() + ox, ys.max() + oy, layout.em

    x0, y0, x1, y1, em = _bbox(base)
    gx0, gy0, gx1, gy1, _ = _bbox(grown)
    assert gx0 == pytest.approx(x0 - 1.0 * em, abs=3)
    assert gy0 == pytest.approx(y0, abs=3)
    assert gx1 == pytest.approx(x1 + 0.25 * em, abs=3)
    assert gy1 == pytest.approx(y1 + 0.5 * em, abs=3)


def test_grow_is_in_hug_cache_key():
    from cutora.render.textfx.raster import _hug_cache_key
    from cutora.render.text_layer_paint import layout_for_overlay
    _app()
    a = _ov(bg={"type": "box", "enabled": True, "size_mode": "hug_glyphs"})
    b = _ov(bg={"type": "box", "enabled": True, "size_mode": "hug_glyphs", "grow_em": [0.5, 0, 0, 0]})
    la, sa, *_ = layout_for_overlay(dict(a), VW, VH)
    lb, sb, *_ = layout_for_overlay(dict(b), VW, VH)
    assert _hug_cache_key(la, sa) != _hug_cache_key(lb, sb)


# ─────────────────────────── 背景不挤文字 ───────────────────────────

def test_bg_and_shadow_do_not_narrow_overlay_wrap():
    """文本层：背景 / 阴影往框外长，折行宽度只看框（字幕仍然扣掉）。"""
    from cutora.render.text_layer_paint import layout_for_overlay
    from cutora.render.textfx.layout import _extra_line_pad
    _app()
    plain = _ov()
    with_bg = _ov(bg={"type": "box", "enabled": True, "size_mode": "fit_text", "pad_em": [1.5, 0.5]})
    with_bg["fx"]["shadow"] = {"enabled": True, "distance_em": 0.3, "size_em": 0.3, "strength": 1.0}
    l0, *_ = layout_for_overlay(dict(plain), VW, VH)
    l1, s1, st1, *_ = layout_for_overlay(dict(with_bg), VW, VH)
    assert [ln.text for ln in l1.lines] == [ln.text for ln in l0.lines]
    assert _extra_line_pad(st1, s1, l1.em) == 0.0
    sub_style = {k: v for k, v in st1.items() if k != "_overlay_box"}
    assert _extra_line_pad(sub_style, s1, l1.em) > 0.0


# ─────────────────────────── 面板 ───────────────────────────

def test_panel_quad_control_roundtrips_and_stays_visible_in_every_mode():
    from cutora.ui.panels.property import PropertyPanel
    app = _app()
    ov = _ov(bg={"type": "box", "enabled": True, "size_mode": "fit_text"})
    panel = PropertyPanel()
    panel.resize(420, 900)
    panel.show()
    try:
        panel.load_data("overlay", ov)
        panel.ov_bg_grp.setExpanded(True)
        app.processEvents()
        w = panel.fx_bg_grow_em
        assert w.values() == [0.0, 0.0, 0.0, 0.0]
        w.spins[0].setValue(0.5)
        w.spins[3].setValue(1.0)
        app.processEvents()
        assert ov["fx"]["bg"]["grow_em"] == [0.5, 0.0, 0.0, 1.0]
        combo = panel.fx_bg_size_mode
        for mode in ("fixed", "hug_glyphs", "line_hug", "fit_text"):
            combo.setCurrentIndex(combo.findData(mode))
            app.processEvents()
            assert not w.isHidden(), mode
        panel.load_data("overlay", ov)
        assert panel.fx_bg_grow_em.values() == [0.5, 0.0, 0.0, 1.0]
        assert _spec(ov).bg.grow_em == (0.5, 0.0, 0.0, 1.0)
    finally:
        panel.close()


# ─────────────────────────── 画布手柄 ───────────────────────────

def _canvas(ov):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(VW, VH, 30.0)
    tr = Track("text")
    tl.tracks.append(tr)
    ov["_track_id"] = tr.id
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [ov],
            "image_overlays": [], "duration": 5.0, "dims": [VW, VH]}
    c = VideoCanvas()
    c.setFixedSize(540, 960)
    c.set_data(task, 0.5)
    c.show()
    QApplication.processEvents()
    c.repaint()
    rec = next(r for r in c._hit_records if r.get("type") == "overlay")
    return c, rec


def test_auto_height_text_has_no_top_bottom_handles():
    from PySide6.QtCore import QRectF
    _app()
    c, rec = _canvas(_ov(fit_mode="auto_height"))
    try:
        keys = set(c._handle_rects_for(QRectF(rec["rect"]), rec))
        assert keys == {"nw", "ne", "sw", "se", "e", "w", "rot"}
        r = QRectF(rec["rect"])
        # 上边中点没有手柄可点
        assert c._hit_resize_handle(r.center().__class__(r.center().x(), r.top()), r, rec) is None
    finally:
        c.close()


def test_fixed_height_text_keeps_all_handles():
    from PySide6.QtCore import QRectF
    _app()
    c, rec = _canvas(_ov(fit_mode="none", h=400))
    try:
        keys = set(c._handle_rects_for(QRectF(rec["rect"]), rec))
        assert keys == {"nw", "n", "ne", "e", "se", "s", "sw", "w", "rot"}
    finally:
        c.close()


# ─────────────────────────── 工具条 ───────────────────────────

def test_context_bar_is_compact_and_opaque():
    from PySide6.QtWidgets import QWidget
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.text_context_bar import TextContextBar
    app = _app()
    host = QWidget()
    host.resize(1100, 120)
    panel = PropertyPanel()
    bar = TextContextBar(panel, host)
    ov = _ov()
    host.show()
    try:
        panel.load_data("overlay", ov)
        bar.show_for("overlay", ov)
        app.processEvents()
        assert bar.width() <= 960            # 以前 1025+，间距散
        assert bar.sp_line.prefix().startswith("行距")
        assert bar.sp_tracking.prefix().startswith("字距")
        assert bar.sp_size.width() <= 96 and bar.sp_line.width() <= 96
        assert bar.cb_font.maximumWidth() <= 176
    finally:
        host.close()
        panel.close()


def test_committed_qss_has_the_bar_and_editor_rules():
    """Q126 漏了重建 resources/theme/theme_dark.qss；这里锁住：模板里的规则必须进了成品。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "resources", "theme", "theme_dark.qss")
    text = open(path, encoding="utf-8").read()
    assert "QFrame#textContextBar" in text
    assert "QTextEdit#canvasTextEditor" in text
    assert "QFrame#textStyleGallery" in text


# ─────────────────────────── 文本整体缩放 ───────────────────────────

def test_text_scale_renders_bigger_without_changing_layout():
    import hashlib
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.text_layer_paint import layout_for_overlay, overlay_scale, paint_text_overlay
    _app()
    ov = _ov(content="Hello scale", fit_mode="auto_height")
    assert overlay_scale(ov) == 1.0
    assert overlay_scale({"scale": "x"}) == 1.0 and overlay_scale({"scale": 99}) == 5.0

    def _render(o):
        img = QImage(VW, VH, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        try:
            paint_text_overlay(p, dict(o), 0.0, None, video_w=VW, video_h=VH)
        finally:
            p.end()
        return img

    def _ink_bbox(img):
        xs, ys = [], []
        for y in range(0, img.height(), 3):
            for x in range(0, img.width(), 3):
                if img.pixelColor(x, y).alpha() > 40:
                    xs.append(x)
                    ys.append(y)
        return min(xs), min(ys), max(xs), max(ys)

    base = _render(ov)
    big = dict(ov)
    big["scale"] = 1.6
    scaled = _render(big)
    l0, *_ = layout_for_overlay(dict(ov), VW, VH)
    l1, *_ = layout_for_overlay(dict(big), VW, VH)
    assert [ln.text for ln in l0.lines] == [ln.text for ln in l1.lines]   # 排版不动
    assert l0.em == l1.em
    b0 = _ink_bbox(base)
    b1 = _ink_bbox(scaled)
    w0, w1 = b0[2] - b0[0], b1[2] - b1[0]
    assert w1 == pytest.approx(w0 * 1.6, rel=0.08)
    # 绕框中心：中心基本不动
    c0 = (b0[0] + b0[2]) / 2
    c1 = (b1[0] + b1[2]) / 2
    assert abs(c0 - c1) < 12
    assert hashlib.md5(base.constBits().tobytes()).hexdigest() != hashlib.md5(scaled.constBits().tobytes()).hexdigest()


def test_caret_map_follows_text_scale():
    from PySide6.QtCore import QPointF
    from cutora.render.text_caret import build_caret_map
    _app()
    ov = _ov(content="Scale me", scale=2.0)
    cm = build_caret_map(ov, VW, VH)
    assert cm.scale == 2.0
    x, y, w, h = cm.box
    cx, cy = x + w / 2, y + h / 2
    p = cm.transform().map(QPointF(cx + 10, cy))
    assert p.x() == pytest.approx(cx + 20)          # 绕中心放大两倍
    ux, uy = cm.unrotate(p.x(), p.y())
    assert (ux, uy) == pytest.approx((cx + 10, cy))


def test_panel_scale_row_only_for_text_and_roundtrips():
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.widgets.inspector import inspector_row_of
    app = _app()
    panel = PropertyPanel()
    panel.resize(420, 900)
    panel.show()
    try:
        ov = _ov(content="hi")
        panel.load_data("overlay", ov)
        app.processEvents()
        row = inspector_row_of(panel.ov_tscale)
        assert row is not None and not row.isHidden()
        assert panel.ov_tscale.value() == pytest.approx(1.0)
        panel.ov_tscale.setValue(1.5)
        app.processEvents()
        assert ov["scale"] == pytest.approx(1.5)
        panel.ov_tscale.setValue(1.0)
        app.processEvents()
        assert "scale" not in ov                      # 1.0 不写键
        img = {"type": "image", "content": "", "x": 0, "y": 0, "w": 100, "h": 100, "scale": 1.0}
        panel.load_data("overlay", img)
        app.processEvents()
        assert inspector_row_of(panel.ov_tscale).isHidden()
    finally:
        panel.close()
