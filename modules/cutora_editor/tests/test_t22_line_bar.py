# -*- coding: utf-8 -*-
"""Q22: line_bar spec + word-draw anim + styles; v1 adv_bar_* are dead."""
from __future__ import annotations

import copy
import hashlib
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 1080, 1920
V1_BAR_KEYS = (
    "adv_bar_enabled", "adv_bar_opacity", "adv_bar_padding", "adv_bar_offset_y",
    "adv_bar_radius", "adv_bar_glow", "adv_bar_style", "adv_bar_border_width",
    "adv_bar_color",
)
LINE_BAR_KEYS = (
    "color", "height_em", "pad_em", "offset_y_em",
    "radius_em", "glow_em", "style", "anim",
)
STYLES = ("solid", "dashed", "dotted", "wave", "brush")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t22-line-bar"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _event(text="ONE TWO THREE FOUR"):
    words = text.split()
    step = 0.4
    return {
        "text": text,
        "start": 0.0,
        "end": step * len(words),
        "id": "s0",
        "lines": [text],
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }


def _base_style(line_bar=None):
    lb = {
        "color": "#F2C14E",
        "alpha": 0.7,
        "height_em": 0.22,
        "pad_em": 0.18,
        "offset_y_em": 0.06,
    }
    if line_bar:
        lb.update(line_bar)
    return {
        "font_family": "Arimo",
        "fontsize": 64,
        "bold": True,
        "pos_x": 540,
        "pos_y": 960,
        "wrap_lines": 1,
        "fx": {
            "follow_sentence": True,
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "glow": {"sigma_em": 0, "strength": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "line_bar": lb,
        },
    }


def _layout(style, text="ONE TWO THREE FOUR"):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    event = _event(text)
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    return event, spec, lay


def _paint_bar(style, t, text="ONE TWO THREE FOUR"):
    from cutora.render.textfx.raster import as_bgra
    from cutora.render.textfx.word_fx import draw_line_bar

    event, spec, lay = _layout(style, text)
    img = draw_line_bar(None, lay, spec, float(t), event, style=style)
    owned, view = as_bgra(img)
    return owned, view, lay, spec


def _md5(view):
    return hashlib.md5(view.tobytes()).hexdigest()


def _rightmost(view, min_alpha=24):
    a = view[..., 3]
    cols = np.where(a.max(axis=0) >= min_alpha)[0]
    assert cols.size > 0, "no bar pixels"
    return int(cols.max())


def _onto_canvas(view, lay):
    canvas = np.zeros((CANVAS_H, CANVAS_W, 4), dtype=np.uint8)
    ox, oy = int(lay.origin[0]), int(lay.origin[1])
    h, w = view.shape[:2]
    y1 = min(CANVAS_H, oy + h)
    x1 = min(CANVAS_W, ox + w)
    if y1 > oy and x1 > ox:
        canvas[oy:y1, ox:x1] = view[: y1 - oy, : x1 - ox]
    return canvas


def test_t22_line_bar_spec(caplog):
    """FxSpec.line_bar exists; from_style parses all 8 keys; unknown key warns once."""
    import logging
    from cutora.render.textfx import spec as spec_mod
    from cutora.render.textfx.spec import from_style

    spec_mod._WARNED.clear()
    caplog.set_level(logging.WARNING, logger="cutora.render.textfx.spec")
    raw = {
        "color": "#22D3EE80",
        "height_em": 0.3,
        "pad_em": 0.1,
        "offset_y_em": -0.05,
        "radius_em": 0.2,
        "glow_em": 0.15,
        "style": "wave",
        "anim": "draw",
        "not_a_real_key": 1,
    }
    spec = from_style({"fx": {"line_bar": raw}})
    assert hasattr(spec, "line_bar"), spec
    lb = spec.line_bar
    assert lb.color == "#22D3EE80", lb.color
    assert abs(lb.height_em - 0.3) < 1e-6
    assert abs(lb.pad_em - 0.1) < 1e-6
    assert abs(lb.offset_y_em + 0.05) < 1e-6
    assert abs(lb.radius_em - 0.2) < 1e-6
    assert abs(lb.glow_em - 0.15) < 1e-6
    assert lb.style == "wave"
    assert lb.anim == "draw"
    hits = [r.message for r in caplog.records if "not_a_real_key" in r.message]
    assert len(hits) == 1, hits
    spec2 = from_style({"fx": {"line_bar": raw}})
    hits2 = [r.message for r in caplog.records if "not_a_real_key" in r.message]
    assert len(hits2) == 1, hits2
    _ = spec2


def test_t22_draw_progress():
    """anim=draw: bar right edge strictly increases; 100% ≈ line right ±2px."""
    _qapp()
    style = _base_style({
        "anim": "draw",
        "style": "solid",
        "pad_em": 0.0,
        "glow_em": 0.0,
        "color": "#FF0000FF",
        "alpha": 1.0,
    })
    event, spec, lay = _layout(style)
    line = lay.lines[0]
    assert line.runs, "expected runs on the line"
    t0 = float(line.runs[0].t_start)
    t1 = float(line.runs[-1].t_end)
    span = max(1e-6, t1 - t0)
    rights = []
    from cutora.render.textfx.raster import as_bgra
    from cutora.render.textfx.word_fx import draw_line_bar

    for frac in (0.25, 0.50, 1.00):
        t = t0 + frac * span
        img = draw_line_bar(None, lay, spec, t, event, style=style)
        _owned, view = as_bgra(img)
        rights.append(_rightmost(view))
    assert rights[0] < rights[1] < rights[2], rights
    ox = lay.origin[0]
    line_right = line.x - ox + line.w
    assert abs(rights[2] - line_right) <= 2.0, (rights[2], line_right)


def test_t22_styles():
    """Five line styles render pairwise-different md5."""
    _qapp()
    hashes = {}
    for name in STYLES:
        style = _base_style({
            "anim": "none",
            "style": name,
            "glow_em": 0.0,
            "color": "#FF0000FF",
            "alpha": 1.0,
        })
        _o, view, _lay, _spec = _paint_bar(style, t=0.8)
        hashes[name] = _md5(view)
        assert view[..., 3].max() > 20, name
    dup = [
        (a, b) for i, a in enumerate(STYLES)
        for b in STYLES[i + 1:]
        if hashes[a] == hashes[b]
    ]
    assert not dup, (dup, hashes)


def test_t22_glow():
    """glow_em 0 → 0.2 grows non-transparent pixels in a 6px ring outside the bar."""
    _qapp()
    off = _base_style({
        "anim": "none", "style": "solid", "glow_em": 0.0,
        "color": "#FF0000FF", "alpha": 1.0, "pad_em": 0.05,
    })
    on = copy.deepcopy(off)
    on["fx"]["line_bar"]["glow_em"] = 0.2
    _o0, v0, lay0, spec0 = _paint_bar(off, t=0.8)
    _o1, v1, lay1, spec1 = _paint_bar(on, t=0.8)
    line = lay0.lines[0]
    em = lay0.em
    lb = spec0.line_bar
    pad = float(lb.pad_em) * em
    bar_h = max(3.0, float(lb.height_em) * em)
    off_y = float(lb.offset_y_em) * em
    x0 = int(round(line.x - pad))
    y0 = int(round(line.y + line.h + off_y))
    x1 = int(round(x0 + line.w + 2 * pad))
    y1 = int(round(y0 + bar_h))
    c0 = _onto_canvas(v0, lay0)
    c1 = _onto_canvas(v1, lay1)
    yy, xx = np.ogrid[:CANVAS_H, :CANVAS_W]
    inner = (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1)
    ring = (xx >= x0 - 6) & (xx < x1 + 6) & (yy >= y0 - 6) & (yy < y1 + 6) & (~inner)
    n0 = int(((c0[..., 3] >= 16) & ring).sum())
    n1 = int(((c1[..., 3] >= 16) & ring).sum())
    assert n1 > n0 + 20, (n0, n1)


def test_t22_color_alpha():
    """#FF000080 vs #FF0000FF: mean alpha in the bar region differs."""
    _qapp()
    a_style = _base_style({
        "anim": "none", "style": "solid", "glow_em": 0.0,
        "color": "#FF000080", "pad_em": 0.05,
    })
    b_style = _base_style({
        "anim": "none", "style": "solid", "glow_em": 0.0,
        "color": "#FF0000FF", "pad_em": 0.05,
    })
    _o0, v0, _l0, _s0 = _paint_bar(a_style, t=0.8)
    _o1, v1, _l1, _s1 = _paint_bar(b_style, t=0.8)
    m0 = v0[..., 3] > 8
    m1 = v1[..., 3] > 8
    assert int(m0.sum()) > 30 and int(m1.sum()) > 30
    mean0 = float(v0[..., 3][m0].mean())
    mean1 = float(v1[..., 3][m1].mean())
    assert mean1 > mean0 + 20, (mean0, mean1)


def test_t22_anim_none_is_legacy():
    """anim=none (or missing) matches the pre-Q22 filled round-rect byte-for-byte."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.textfx.raster import _qcolor, _round_rect_path, as_bgra

    _qapp()
    style = _base_style()  # no anim/style/glow keys — legacy path
    _owned, view, lay, spec = _paint_bar(style, t=0.8)
    event, spec2, lay2 = _layout(style)
    line = lay.lines[0]
    ox, oy = lay.origin
    em = lay.em
    pad = 0.18 * em
    bar_h = max(3.0, 0.22 * em)
    off_y = 0.06 * em
    radius = float(spec.bg.radius_em) * em
    ref = QImage(lay.roi_w, lay.roi_h, QImage.Format.Format_ARGB32_Premultiplied)
    ref.fill(0)
    rect = QRectF(
        line.x - ox - pad,
        line.y - oy + line.h + off_y,
        line.w + 2 * pad,
        bar_h,
    )
    pnt = QPainter(ref)
    try:
        pnt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pnt.setPen(Qt.PenStyle.NoPen)
        pnt.setBrush(_qcolor("#F2C14E", 0.7))
        pnt.drawPath(_round_rect_path(rect, radius))
    finally:
        pnt.end()
    _ro, ref_view = as_bgra(ref)
    assert view.tobytes() == ref_view.tobytes()
    style_none = _base_style({"anim": "none"})
    _o2, v2, _l2, _s2 = _paint_bar(style_none, t=0.8)
    assert v2.tobytes() == view.tobytes()
    _ = (event, spec2, lay2)


def test_t22_no_v1_bar_keys():
    """collect() drops adv_bar_*; dead v1 widgets gone; square color chip."""
    from cutora.ui.panels.fx_fields import ColorSwatch

    _qapp()
    panel = _panel()
    style = _base_style()
    for key in V1_BAR_KEYS:
        style[key] = 1
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    got = panel.collect()
    blob = str(got)
    for key in V1_BAR_KEYS:
        assert key not in got, (key, got.keys())
        fx = got.get("fx") or {}
        assert key not in fx, (key, fx.keys())
        assert key not in blob, key
    for attr in (
        "sub_adv_bar_c", "sub_adv_bar_op", "sub_adv_bar_op_spin",
        "sub_adv_bar_pad", "sub_adv_bar_off_y", "sub_adv_bar_rad",
        "sub_adv_bar_glow", "sub_adv_bar_style", "sub_adv_bar_border_w",
    ):
        assert getattr(panel, attr, None) is None, attr
    assert getattr(panel, "sub_adv_bar", None) is not None
    chip = getattr(panel, "fx_line_bar_color", None)
    assert chip is not None
    assert isinstance(chip, ColorSwatch)
    assert chip.width() == chip.height(), (chip.width(), chip.height())
    assert chip.width() >= 24
    lb = (got.get("fx") or {}).get("line_bar") or {}
    for key in LINE_BAR_KEYS:
        assert key in lb, (key, lb)
    panel.close()
