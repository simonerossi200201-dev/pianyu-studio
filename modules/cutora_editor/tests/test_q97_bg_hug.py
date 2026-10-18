# -*- coding: utf-8 -*-
"""Q97: pad row steps aside when size is fixed; hug_glyphs wraps the glyphs."""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

W, H = 480, 270
# Q93 fit_text frame, and the fixed 260×100 frame captured before this change.
_FIT_MD5 = "8a063bb50cd86a78515f330072ee72a3"
_FIXED_MD5 = "587663ebde006cdba7c0a17a476a58bb"


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q97-bg"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _overlay(text="Hi", size=48, **bg_extra):
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
        "content": text,
        "start": 0.0,
        "end": 2.0,
        "id": "t",
        "x": 120,
        "y": 80,
        "w": 240,
        "h": 110,
        "font_family": "Arimo",
        "fontsize": size,
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


def _mad(a, b) -> float:
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def _paint(ov, *, clear=None, backdrop=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    if clear is None:
        img.fill(QColor("#101010"))
    else:
        img.fill(clear)
    p = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            p, _task(ov), W, H, 1.0, render_order=["text"], backdrop=backdrop)
    finally:
        p.end()
    return _bgra(img)


def _preview(ov):
    return _paint(ov)


def _export(ov, backdrop=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, W, H, QColor("#101010"))

    return _bgra(ExportRenderer.render_frame(
        _task(ov), W, H, 1.0, draw_background=_bg, backdrop=backdrop))


def _alpha(arr):
    return arr[:, :, 3]


def _glyph_core(text_arr):
    return _alpha(text_arr) > 127


def _dist_to_glyph(core):
    import cv2
    inv = np.where(core, np.uint8(0), np.uint8(255))
    return cv2.distanceTransform(inv, cv2.DIST_L2, 5)


def _q93_overlay(**bg_extra):
    """Same box Q93 hashed. Do not move x/y/size — the md5 is pinned to it."""
    ov = _overlay(**bg_extra)
    ov["x"] = 140
    ov["y"] = 90
    ov["w"] = 200
    ov["h"] = 80
    ov["fontsize"] = 48
    ov["content"] = "Hi"
    return ov


def test_q97_fit_and_fixed_match_q93_bytes():
    """fit_text and fixed frames stay the bytes Q93 shipped."""
    _qapp()
    _fonts()
    fit = _preview(_q93_overlay())
    assert _md5(fit) == _FIT_MD5
    explicit = _preview(_q93_overlay(size_mode="fit_text"))
    assert _md5(explicit) == _FIT_MD5
    exported = _export(_q93_overlay())
    assert _md5(exported) == _FIT_MD5
    assert _mad(fit, exported) == 0.0

    fixed_ov = _q93_overlay(
        size_mode="fixed", width_px=260, height_px=100,
        offset_x=24, offset_y=-16,
    )
    fixed = _preview(fixed_ov)
    assert _md5(fixed) == _FIXED_MD5
    fixed_ex = _export(fixed_ov)
    assert _md5(fixed_ex) == _FIXED_MD5
    assert _mad(fixed, fixed_ex) == 0.0


def test_q97_hug_box_follows_glyphs_not_rect():
    """Solid out to hug_pad, gone at twice that, and the bbox corners are clear."""
    _qapp()
    _fonts()
    hug_pad = 0.30
    size = 64
    ov = _overlay(
        text="Hi", size=size, size_mode="hug_glyphs",
        hug_pad_em=hug_pad, radius_em=0.0, alpha=1.0, color="#FF00AA",
    )
    bare = copy.deepcopy(ov)
    bare["fx"]["bg"]["enabled"] = False
    bare["fx"]["bg"]["type"] = "none"
    from PySide6.QtGui import QColor
    text = _paint(bare, clear=QColor(0, 0, 0, 0))
    frame = _paint(ov, clear=QColor(0, 0, 0, 0))
    core = _glyph_core(text)
    assert int(core.sum()) > 80, "glyph core missing"
    dist = _dist_to_glyph(core)
    alpha = _alpha(frame)
    r = hug_pad * float(size)
    inner = (dist >= r - 3.0) & (dist <= r - 1.0) & (~core)
    outer = (dist >= 2.0 * r) & (dist <= 2.0 * r + 3.0)
    assert int(inner.sum()) > 20, (int(inner.sum()), r)
    assert int(outer.sum()) > 20, (int(outer.sum()), r)
    assert float(np.median(alpha[inner])) > 200, float(np.median(alpha[inner]))
    assert int(alpha[outer].max()) == 0, int(alpha[outer].max())
    ys, xs = np.nonzero(alpha > 8)
    assert xs.size > 50
    corners = (
        (int(ys.min()), int(xs.min())),
        (int(ys.min()), int(xs.max())),
        (int(ys.max()), int(xs.min())),
        (int(ys.max()), int(xs.max())),
    )
    for y, x in corners:
        assert int(alpha[y, x]) == 0, (x, y, int(alpha[y, x]))


def test_q97_hug_radius_fills_notch():
    """Q99 moved the close off radius_em. 0 and 0.5 must paint the same notch."""
    _qapp()
    _fonts()
    size = 80

    def _frame(radius):
        ov = _overlay(
            text="Hi", size=size, size_mode="hug_glyphs",
            hug_pad_em=0.04, radius_em=radius, hug_join_em=0.06, alpha=1.0,
        )
        from PySide6.QtGui import QColor
        return _paint(ov, clear=QColor(0, 0, 0, 0))

    bare = _overlay(text="Hi", size=size, hug_pad_em=0.04)
    bare["fx"]["bg"]["enabled"] = False
    bare["fx"]["bg"]["type"] = "none"
    from PySide6.QtGui import QColor
    text = _paint(bare, clear=QColor(0, 0, 0, 0))
    core = _glyph_core(text)
    cols = np.nonzero(core.any(axis=0))[0]
    assert cols.size > 10
    gap = []
    for x in range(int(cols.min()) + 1, int(cols.max())):
        if not core[:, x].any():
            gap.append(x)
    assert len(gap) >= 2, "letters are not separated"
    x0 = min(gap) - 2
    x1 = max(gap) + 3
    ys = np.nonzero(core.any(axis=1))[0]
    y0 = int(ys.min() + 0.35 * (ys.max() - ys.min()))
    y1 = int(ys.min() + 0.70 * (ys.max() - ys.min()))
    sharp = _alpha(_frame(0.0))
    round_ = _alpha(_frame(0.5))
    band_s = int((sharp[y0:y1, x0:x1] > 128).sum())
    band_r = int((round_[y0:y1, x0:x1] > 128).sum())
    assert band_r == band_s, (band_s, band_r)
    assert np.array_equal(sharp, round_)


def _stripes():
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(W, H, QImage.Format.Format_RGB888)
    p = QPainter(img)
    p.fillRect(0, 0, W, H, QColor("#1030C0"))
    p.fillRect(W // 2, 0, W // 2, H, QColor("#E0C010"))
    p.end()
    return img


def _bg_only_mask(frame, text):
    """Pixels the background changed, ignoring the opaque glyphs."""
    delta = np.abs(frame.astype(np.int16) - text.astype(np.int16)).sum(axis=2)
    glyph = _alpha(text) > 200
    return (delta > 30) & (~glyph)


def test_q97_hug_glass_and_gradient_share_shape():
    """Same silhouette as the solid hug; the fill is glass or a gradient."""
    _qapp()
    _fonts()
    common = dict(
        text="Hi", size=64, size_mode="hug_glyphs",
        hug_pad_em=0.22, radius_em=0.15,
    )
    box = _overlay(**common, type="box", color="#FF00AA", alpha=1.0)
    grad = _overlay(
        **common, type="box", color="#FF00AA", alpha=1.0,
        grad_type="linear_h", grad_colors=["#00FF66", "#2266FF"],
    )
    glass = _overlay(
        **common, type="glass", color="#FFFFFF", alpha=0.25,
        glass_thickness_em=0.0,
    )
    from PySide6.QtGui import QColor
    bare = copy.deepcopy(box)
    bare["fx"]["bg"]["enabled"] = False
    bare["fx"]["bg"]["type"] = "none"
    clear = QColor(0, 0, 0, 0)
    text = _paint(bare, clear=clear)
    box_f = _paint(box, clear=clear)
    grad_f = _paint(grad, clear=clear)
    stripes = _stripes()
    glass_f = _paint(glass, clear=clear, backdrop=stripes)
    box_a = _alpha(box_f)
    grad_a = _alpha(grad_f)
    glass_a = _alpha(glass_f)
    assert float(np.abs(box_a.astype(np.int16) - grad_a.astype(np.int16)).mean()) < 1.0
    assert float(np.abs(box_a.astype(np.int16) - glass_a.astype(np.int16)).mean()) < 1.0
    region = (box_a > 200) & (_alpha(text) < 30)
    assert int(region.sum()) > 40, int(region.sum())
    def _rgb(arr):
        return arr[:, :, :3].astype(np.float32)
    box_m = _rgb(box_f)[region].mean(axis=0)
    grad_m = _rgb(grad_f)[region].mean(axis=0)
    glass_m = _rgb(glass_f)[region].mean(axis=0)
    assert float(np.abs(box_m - grad_m).mean()) > 20, (box_m, grad_m)
    assert float(np.abs(box_m - glass_m).mean()) > 20, (box_m, glass_m)
    # Gradient actually changes across the shape, not a flat tint.
    half = region.copy()
    xs = np.nonzero(region.any(axis=0))[0]
    mid = int((xs.min() + xs.max()) / 2)
    left = region.copy()
    right = region.copy()
    left[:, mid:] = False
    right[:, :mid] = False
    assert int(left.sum()) > 10 and int(right.sum()) > 10
    spread = float(np.abs(
        _rgb(grad_f)[left].mean(axis=0) - _rgb(grad_f)[right].mean(axis=0)
    ).mean())
    assert spread > 15, spread
    ys, xs = np.nonzero(box_a > 8)
    for y, x in (
        (int(ys.min()), int(xs.min())),
        (int(ys.min()), int(xs.max())),
        (int(ys.max()), int(xs.min())),
        (int(ys.max()), int(xs.max())),
    ):
        assert int(box_a[y, x]) == 0, (x, y, int(box_a[y, x]))


def test_q97_hug_preview_matches_export():
    """Box frame MAD is 0. Glass compares glyph centres, and the mask is cached."""
    _qapp()
    _fonts()
    from cutora.render.textfx.raster import clear_hug_mask_cache, hug_mask_builds

    box = _overlay(
        text="Hi", size=64, size_mode="hug_glyphs",
        hug_pad_em=0.22, radius_em=0.2, alpha=1.0, color="#FF00AA",
    )
    clear_hug_mask_cache()
    preview = _preview(box)
    exported = _export(box)
    assert hug_mask_builds() == 1, hug_mask_builds()
    assert _mad(preview, exported) == 0.0

    stripes = _stripes()
    glass = _overlay(
        text="Hi", size=64, size_mode="hug_glyphs",
        hug_pad_em=0.22, radius_em=0.2, type="glass",
        color="#FFFFFF", alpha=0.35, glass_thickness_em=0.14,
    )
    g_prev = _paint(glass, backdrop=stripes)
    g_exp = _export(glass, backdrop=stripes)
    assert _ink_center(g_prev) == pytest.approx(_ink_center(g_exp), abs=0.5)


def _ink_center(arr):
    r = arr[:, :, 2]
    g = arr[:, :, 1]
    b = arr[:, :, 0]
    ys, xs = np.nonzero((r > 220) & (g > 220) & (b > 220))
    assert xs.size > 20, "white glyphs missing"
    return float(xs.mean()), float(ys.mean())


def _expand_sections(widget):
    from cutora.ui.widgets.collapsible import CollapsibleSection
    cur = widget
    while cur is not None:
        if isinstance(cur, CollapsibleSection):
            cur.setExpanded(True)
        cur = cur.parentWidget()


def _row_index(widget):
    from PySide6.QtWidgets import QWidget
    from cutora.ui.widgets.inspector import inspector_row_of
    row = inspector_row_of(widget)
    target = row if row is not None else widget
    parent = target.parentWidget()
    lay = QWidget.layout(parent) if parent is not None else None
    if lay is None:
        return None
    return lay.indexOf(target)


def _set_mode(panel, mode):
    combo = panel.fx_bg_size_mode
    idx = combo.findData(mode)
    assert idx >= 0, mode
    combo.setCurrentIndex(idx)
    assert combo.currentData() == mode


def test_q97_hug_panel_hides_and_reopens():
    """轮廓包裹 hides width/height/pad/radius, keeps thickness, join and offset."""
    _qapp()
    _fonts()
    from PySide6.QtWidgets import QApplication
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    ov = _overlay(size_mode="fit_text")
    panel = PropertyPanel()
    panel.resize(420, 900)
    panel.show()
    panel.load_data("overlay", ov)
    panel.ov_bg_grp.setExpanded(True)
    QApplication.processEvents()
    assert panel.fx_bg_size_mode.findData("hug_glyphs") >= 0
    assert abs(float(panel.fx_bg_hug_pad_em.value()) - 0.18) < 0.021
    assert not panel.fx_bg_pad_em.isHidden()
    assert not panel.fx_bg_radius_em.isHidden()
    assert panel.fx_bg_width_px.isHidden()
    assert panel.fx_bg_hug_pad_em.isHidden()
    assert panel.fx_bg_hug_join_em.isHidden()
    assert panel.fx_bg_offset_x.isHidden()

    _set_mode(panel, "fixed")
    QApplication.processEvents()
    assert panel.fx_bg_pad_em.isHidden()
    assert not panel.fx_bg_radius_em.isHidden()
    assert panel.fx_bg_hug_join_em.isHidden()
    assert not panel.fx_bg_width_px.isHidden()
    assert not panel.fx_bg_height_px.isHidden()
    assert panel.fx_bg_hug_pad_em.isHidden()

    _set_mode(panel, "hug_glyphs")
    QApplication.processEvents()
    assert panel.fx_bg_width_px.isHidden()
    assert panel.fx_bg_height_px.isHidden()
    assert panel.fx_bg_pad_em.isHidden()
    assert panel.fx_bg_radius_em.isHidden()
    assert not panel.fx_bg_hug_pad_em.isHidden()
    assert not panel.fx_bg_hug_join_em.isHidden()
    assert not panel.fx_bg_offset_x.isHidden()
    assert not panel.fx_bg_offset_y.isHidden()

    order = [
        panel.fx_bg_size_mode, panel.fx_bg_width_px, panel.fx_bg_height_px,
        panel.fx_bg_offset_x, panel.fx_bg_offset_y, panel.fx_bg_pad_em,
        panel.fx_bg_hug_pad_em, panel.fx_bg_hug_join_em,
    ]
    idxs = [_row_index(w) for w in order]
    assert all(i is not None and i >= 0 for i in idxs), idxs
    assert idxs == sorted(idxs) and len(set(idxs)) == len(idxs), idxs

    panel.fx_bg_hug_pad_em.setValue(0.42)
    panel.fx_bg_radius_em.setValue(0.55)
    panel.fx_bg_offset_x.setValue(18)
    panel.fx_bg_offset_y.setValue(-12)
    bg = ov["fx"]["bg"]
    assert bg["size_mode"] == "hug_glyphs"
    assert abs(float(bg["hug_pad_em"]) - 0.42) < 0.021
    assert abs(float(bg["radius_em"]) - 0.55) < 0.021
    assert int(bg["offset_x"]) == 18
    assert int(bg["offset_y"]) == -12
    before = _md5(_preview(ov))
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
    assert panel2.fx_bg_size_mode.currentData() == "hug_glyphs"
    assert abs(float(panel2.fx_bg_hug_pad_em.value()) - 0.42) < 0.021
    assert abs(float(panel2.fx_bg_radius_em.value()) - 0.55) < 0.021
    assert int(panel2.fx_bg_offset_x.value()) == 18
    assert int(panel2.fx_bg_offset_y.value()) == -12
    assert panel2.fx_bg_width_px.isHidden()
    assert panel2.fx_bg_pad_em.isHidden()
    assert panel2.fx_bg_radius_em.isHidden()
    assert not panel2.fx_bg_hug_pad_em.isHidden()
    assert not panel2.fx_bg_hug_join_em.isHidden()
    after = _md5(_preview(blob[0]))
    assert after == before
    panel2.close()

    style = {
        "font_family": "Arimo",
        "fontsize": 48,
        "bold": True,
        "fx": {"bg": {
            "enabled": True, "type": "box", "size_mode": "hug_glyphs",
            "hug_pad_em": 0.33, "radius_em": 0.4,
            "pad_em": [0.4, 0.25],
        }},
    }
    sub = SubtitleStylePanel()
    sub.resize(420, 900)
    sub.show()
    sub.load_data(style)
    _expand_sections(sub.fx_bg_hug_pad_em)
    QApplication.processEvents()
    assert sub.fx_bg_size_mode.currentData() == "hug_glyphs"
    assert sub.fx_bg_width_px.isHidden()
    assert sub.fx_bg_height_px.isHidden()
    assert sub.fx_bg_pad_em.isHidden()
    assert sub.fx_bg_radius_em.isHidden()
    assert not sub.fx_bg_hug_pad_em.isHidden()
    assert not sub.fx_bg_hug_join_em.isHidden()
    assert not sub.fx_bg_offset_x.isHidden()
    sub_order = [
        sub.fx_bg_size_mode, sub.fx_bg_width_px, sub.fx_bg_height_px,
        sub.fx_bg_offset_x, sub.fx_bg_offset_y, sub.fx_bg_pad_em,
        sub.fx_bg_hug_pad_em, sub.fx_bg_hug_join_em,
    ]
    sub_idxs = [_row_index(w) for w in sub_order]
    assert sub_idxs == sorted(sub_idxs) and len(set(sub_idxs)) == len(sub_idxs), sub_idxs
    _set_mode(sub, "fit_text")
    QApplication.processEvents()
    assert not sub.fx_bg_pad_em.isHidden()
    assert not sub.fx_bg_radius_em.isHidden()
    assert sub.fx_bg_hug_join_em.isHidden()
    assert sub.fx_bg_width_px.isHidden()
    assert sub.fx_bg_hug_pad_em.isHidden()
    _set_mode(sub, "fixed")
    QApplication.processEvents()
    assert sub.fx_bg_pad_em.isHidden()
    assert not sub.fx_bg_radius_em.isHidden()
    assert sub.fx_bg_hug_join_em.isHidden()
    assert not sub.fx_bg_width_px.isHidden()
    sub.close()
