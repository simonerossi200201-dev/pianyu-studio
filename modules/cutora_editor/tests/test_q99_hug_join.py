# -*- coding: utf-8 -*-
"""Q99: hug close radius is hug_join_em, capped by the stroke, not radius_em."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Q93 / Q97 fit_text frame. Fixed 260×100 is the other byte Q97 pinned.
_FIT_MD5 = "8a063bb50cd86a78515f330072ee72a3"
_FIXED_MD5 = "587663ebde006cdba7c0a17a476a58bb"
_QW, _QH = 480, 270


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q99-join"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _overlay(text="Hi", size=48, *, w=640, h=400, bold=True, **bg_extra):
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
        "x": 40,
        "y": 30,
        "w": w,
        "h": h,
        "font_family": "Arimo",
        "fontsize": size,
        "bold": bold,
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


def _paint(ov, *, w, h, clear=None, backdrop=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#101010") if clear is None else clear)
    p = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            p, _task(ov, w, h), w, h, 1.0,
            render_order=["text"], backdrop=backdrop)
    finally:
        p.end()
    return _bgra(img)


def _export(ov, *, w, h, backdrop=None, clear="#101010"):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, w, h, QColor(clear))

    return _bgra(ExportRenderer.render_frame(
        _task(ov, w, h), w, h, 1.0, draw_background=_bg, backdrop=backdrop))


def _alpha(arr):
    return arr[:, :, 3]


def _glyph_core(text_arr):
    return _alpha(text_arr) > 127


def _q93_overlay(**bg_extra):
    ov = _overlay(**bg_extra)
    ov["x"] = 140
    ov["y"] = 90
    ov["w"] = 200
    ov["h"] = 80
    ov["fontsize"] = 48
    ov["content"] = "Hi"
    return ov


def _notch_band(text_arr, bg_arr):
    """Q97's inter-letter notch: full-height empty columns, mid vertical band."""
    core = _glyph_core(text_arr)
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
    covered = int((_alpha(bg_arr)[y0:y1, x0:x1] > 128).sum())
    return covered


def _counter_window(core):
    """3×3 in the upper counter of H, between the stems and clear of the bar.

    The vertical midpoint of H is the crossbar, so "between the stems" is
    the bay above it.
    """
    ys, xs = np.nonzero(core)
    assert xs.size > 40, "glyph missing"
    y0, y1 = int(ys.min()), int(ys.max())
    sub = core[y0:y1 + 1]
    yrel = int(round(0.28 * (sub.shape[0] - 1)))
    row = sub[yrel]
    best = None
    i = 0
    while i < len(row):
        if row[i]:
            i += 1
            continue
        j = i
        while j < len(row) and not row[j]:
            j += 1
        if i > 0 and j < len(row) and row[i - 1] and row[j]:
            if best is None or (j - i) > (best[1] - best[0]):
                best = (i, j)
        i = j
    assert best is not None, "H has no counter between the stems"
    gx0, gx1 = best
    assert gx1 - gx0 >= 8, (gx0, gx1)
    cx = (gx0 + gx1) // 2
    y = y0 + yrel
    return y - 1, y + 2, cx - 1, cx + 2


def _layout_radius(ov, w, h):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import hug_close_radius_px
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.text_layer import overlay_event, overlay_to_style
    style = overlay_to_style(ov, w, h)
    spec = from_style(style)
    lay = layout_event(overlay_event(ov), style, spec, w, h)
    return hug_close_radius_px(lay, spec)


def test_q99_join_fills_notch():
    """join 0.15 covers more of the gap between two letters than join 0."""
    _qapp()
    _fonts()
    from PySide6.QtGui import QColor
    w, h = 720, 480
    clear = QColor(0, 0, 0, 0)
    bare = _overlay(text="Hi", size=80, w=640, h=400)
    bare["fx"]["bg"]["enabled"] = False
    bare["fx"]["bg"]["type"] = "none"
    text = _paint(bare, w=w, h=h, clear=clear)

    def _frame(join):
        ov = _overlay(
            text="Hi", size=80, w=640, h=400, size_mode="hug_glyphs",
            hug_pad_em=0.04, hug_join_em=join, radius_em=0.5, alpha=1.0,
        )
        return ov, _paint(ov, w=w, h=h, clear=clear)

    ov0, sharp = _frame(0.0)
    ov15, closed = _frame(0.15)
    band0 = _notch_band(text, sharp)
    band15 = _notch_band(text, closed)
    assert band15 > band0 + 40, (band0, band15)
    assert band15 > band0 * 1.5, (band0, band15)
    prev_op = _paint(ov15, w=w, h=h)
    exp_op = _export(ov15, w=w, h=h)
    assert _mad(prev_op, exp_op) == 0.0


def test_q99_join_max_leaves_h_counter_open():
    """join at the slider max must not fill the counter of H."""
    _qapp()
    _fonts()
    from PySide6.QtGui import QColor
    w, h = 640, 400
    clear = QColor(0, 0, 0, 0)

    def _check(size):
        bare = _overlay(text="H", size=size, w=580, h=360, bold=True)
        bare["fx"]["bg"]["enabled"] = False
        bare["fx"]["bg"]["type"] = "none"
        core = _glyph_core(_paint(bare, w=w, h=h, clear=clear))
        y0, y1, x0, x1 = _counter_window(core)
        assert int(core[y0:y1, x0:x1].sum()) == 0
        ov = _overlay(
            text="H", size=size, w=580, h=360, bold=True,
            size_mode="hug_glyphs", hug_pad_em=0.06, hug_join_em=0.15,
            radius_em=0.5, alpha=1.0, color="#2266FF",
        )
        frame = _paint(ov, w=w, h=h, clear=clear)
        block = _alpha(frame)[y0:y1, x0:x1]
        assert int(block.max()) <= 8, (size, int(block.max()), block.tolist())
        assert float(np.median(block)) == 0.0, (size, float(np.median(block)))
        prev = _paint(ov, w=w, h=h)
        exp = _export(ov, w=w, h=h)
        assert _mad(prev, exp) == 0.0
        # Opaque canvas: an open counter shows #101010, not the hug blue.
        hole = prev[y0:y1, x0:x1, :3].astype(np.int16)
        canvas = np.array([16, 16, 16], dtype=np.int16)
        assert int(np.abs(hole - canvas).max()) <= 8, (size, hole.reshape(-1, 3)[:3])
        hole_e = exp[y0:y1, x0:x1, :3].astype(np.int16)
        assert int(np.abs(hole_e - canvas).max()) <= 8, size

    _check(96)
    _check(192)


def test_q99_radius_em_does_not_change_hug_mask():
    """radius_em 0 and 0.5 paint the same hug mask, including export."""
    _qapp()
    _fonts()
    from PySide6.QtGui import QColor
    from cutora.render.textfx.raster import clear_hug_mask_cache
    w, h = 640, 400
    clear = QColor(0, 0, 0, 0)

    def _both(radius):
        ov = _overlay(
            text="Hi", size=80, w=580, h=340, size_mode="hug_glyphs",
            hug_pad_em=0.08, hug_join_em=0.1, radius_em=radius, alpha=1.0,
        )
        clear_hug_mask_cache()
        return ov, _paint(ov, w=w, h=h, clear=clear)

    ov0, a = _both(0.0)
    ov1, b = _both(0.5)
    assert _md5(a) == _md5(b)
    assert np.array_equal(a, b)
    assert np.array_equal(_export(ov0, w=w, h=h), _export(ov1, w=w, h=h))
    assert _mad(_paint(ov0, w=w, h=h), _export(ov0, w=w, h=h)) == 0.0


def test_q99_fit_and_fixed_match_q93_bytes():
    """fit_text / fixed frames stay the bytes Q93 and Q97 shipped."""
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
    fixed_ex = _export(fixed_ov, w=_QW, h=_QH)
    assert _md5(fixed_ex) == _FIXED_MD5
    assert _mad(fixed, fixed_ex) == 0.0


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


def test_q99_panel_shows_join_hides_radius_and_roundtrips():
    """轮廓包裹 shows 包裹厚度 / 连字 / 偏移 and hides 圆角. Values survive json."""
    _qapp()
    _fonts()
    from PySide6.QtWidgets import QApplication
    from cutora.render.textfx.spec import HUG_JOIN_EM_DEFAULT, HUG_JOIN_EM_MAX, from_style
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    missing = from_style({"fx": {"bg": {"type": "box", "enabled": True}}})
    assert missing.bg.hug_join_em == pytest.approx(HUG_JOIN_EM_DEFAULT)
    clamped = from_style({"fx": {"bg": {"hug_join_em": 0.9}}})
    assert clamped.bg.hug_join_em == pytest.approx(HUG_JOIN_EM_MAX)

    ov = _overlay(size_mode="fit_text", type="box")
    panel = PropertyPanel()
    panel.resize(420, 900)
    panel.show()
    panel.load_data("overlay", ov)
    panel.ov_bg_grp.setExpanded(True)
    QApplication.processEvents()
    assert abs(float(panel.fx_bg_hug_join_em.value()) - 0.06) < 1e-6
    assert panel.fx_bg_hug_join_em.maximum() == pytest.approx(0.15)
    assert panel.fx_bg_hug_join_em.isHidden()
    assert not panel.fx_bg_radius_em.isHidden()
    assert _row_label(panel.fx_bg_hug_join_em) == "连字"

    _set_mode(panel, "hug_glyphs")
    QApplication.processEvents()
    assert not panel.fx_bg_hug_pad_em.isHidden()
    assert not panel.fx_bg_hug_join_em.isHidden()
    assert not panel.fx_bg_offset_x.isHidden()
    assert not panel.fx_bg_offset_y.isHidden()
    assert panel.fx_bg_radius_em.isHidden()
    assert panel.fx_bg_width_px.isHidden()
    assert panel.fx_bg_height_px.isHidden()
    assert panel.fx_bg_pad_em.isHidden()

    panel.fx_bg_hug_join_em.setValue(0.12)
    panel.fx_bg_hug_pad_em.setValue(0.22)
    panel.fx_bg_offset_x.setValue(6)
    bg = ov["fx"]["bg"]
    assert abs(float(bg["hug_join_em"]) - 0.12) < 1e-6
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
    assert abs(float(panel2.fx_bg_hug_join_em.value()) - 0.12) < 1e-6
    assert abs(float(panel2.fx_bg_hug_pad_em.value()) - 0.22) < 0.021
    assert int(panel2.fx_bg_offset_x.value()) == 6
    assert not panel2.fx_bg_hug_join_em.isHidden()
    assert panel2.fx_bg_radius_em.isHidden()
    assert panel2.fx_bg_width_px.isHidden()
    assert panel2.fx_bg_pad_em.isHidden()
    panel2.close()

    sub = SubtitleStylePanel()
    sub.resize(420, 900)
    sub.show()
    sub.load_data({"font_family": "Arimo", "fontsize": 48, "fx": {"bg": {
        "enabled": True, "type": "box", "size_mode": "hug_glyphs",
        "hug_join_em": 0.09,
    }}})
    from cutora.ui.widgets.collapsible import CollapsibleSection
    cur = sub.fx_bg_hug_join_em
    while cur is not None:
        if isinstance(cur, CollapsibleSection):
            cur.setExpanded(True)
        cur = cur.parentWidget()
    QApplication.processEvents()
    assert sub.fx_bg_size_mode.currentData() == "hug_glyphs"
    assert not sub.fx_bg_hug_join_em.isHidden()
    assert not sub.fx_bg_hug_pad_em.isHidden()
    assert not sub.fx_bg_offset_x.isHidden()
    assert sub.fx_bg_radius_em.isHidden()
    assert sub.fx_bg_width_px.isHidden()
    assert sub.fx_bg_pad_em.isHidden()
    assert abs(float(sub.fx_bg_hug_join_em.value()) - 0.09) < 1e-6
    assert _row_label(sub.fx_bg_hug_join_em) == "连字"
    _set_mode(sub, "fit_text")
    QApplication.processEvents()
    assert not sub.fx_bg_radius_em.isHidden()
    assert sub.fx_bg_hug_join_em.isHidden()
    _set_mode(sub, "fixed")
    QApplication.processEvents()
    assert not sub.fx_bg_radius_em.isHidden()
    assert sub.fx_bg_hug_join_em.isHidden()
    assert not sub.fx_bg_width_px.isHidden()
    sub.close()


def test_q99_close_radius_is_smaller_on_thin_strokes():
    """The capped close radius tracks stroke width: thin < bold."""
    _qapp()
    _fonts()
    w, h = 640, 400
    thin_ov = _overlay(
        text="H", size=96, bold=False, w=580, h=360,
        size_mode="hug_glyphs", hug_join_em=0.15, hug_pad_em=0.08,
    )
    thick_ov = _overlay(
        text="H", size=96, bold=True, w=580, h=360,
        size_mode="hug_glyphs", hug_join_em=0.15, hug_pad_em=0.08,
    )
    thin = _layout_radius(thin_ov, w, h)
    thick = _layout_radius(thick_ov, w, h)
    assert thin > 0.5, thin
    assert thick > thin + 0.9, (thin, thick)
    # Both sit under the raw slider radius, so the stroke cap is what differs.
    assert thin < 0.15 * 96
    assert thick < 0.15 * 96
