# -*- coding: utf-8 -*-
"""Q19: 配色方案 binds fx.fill / fx.emphasis; v1 color_text / color_high are dead."""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
CHIP_MIN = 24


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t19-fill-signal"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _mean_rgb(view, mask):
    import numpy as np
    pix = view[mask]
    # ARGB32_Premultiplied on LE is B,G,R,A
    b = float(pix[:, 0].mean())
    g = float(pix[:, 1].mean())
    r = float(pix[:, 2].mean())
    return r, g, b


def _opaque_mask(view, alpha_min=80):
    return view[..., 3] >= alpha_min


def _paint_line(style, text="HELLO", t=0.55):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style

    words = text.split()
    step = 0.4
    event = {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=True, t=t, event=event,
    )
    owned, view = as_bgra(img)
    return owned, view, lay, event


def _base_fx(**fill_kw):
    fill = {
        "type": "solid",
        "colors": ["#F4EBD8"],
        "angle": 0.0,
        "metal": "gold",
    }
    fill.update(fill_kw)
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": fill,
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"sigma_em": 0, "alpha": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def test_t19_fill_signal():
    """fill.type=gradient red→blue: left half redder, right half bluer, not black."""
    _qapp()
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()

    style = _base_fx(
        type="gradient",
        colors=["#FF0000", "#0000FF"],
        angle=0.0,
    )
    _owned, view, lay, _event = _paint_line(style, text="MMMMMMMM")
    mask = _opaque_mask(view)
    assert int(mask.sum()) > 80, "gradient fill painted no glyphs"

    xs = mask.nonzero()[1]
    mid = float(xs.min() + xs.max()) * 0.5
    import numpy as np
    yy, xx = np.nonzero(mask)
    left_m = np.zeros(mask.shape, dtype=bool)
    right_m = np.zeros(mask.shape, dtype=bool)
    left_m[yy[xx < mid], xx[xx < mid]] = True
    right_m[yy[xx >= mid], xx[xx >= mid]] = True
    assert int(left_m.sum()) > 20 and int(right_m.sum()) > 20, (
        int(left_m.sum()), int(right_m.sum()))

    lr, lg, lb = _mean_rgb(view, left_m)
    rr, rg, rb = _mean_rgb(view, right_m)
    assert lr + lg + lb > 60, (lr, lg, lb)
    assert rr + rg + rb > 60, (rr, rg, rb)
    assert lr > lb, "left half should lean red, got R=%s B=%s" % (lr, lb)
    assert rb > rr, "right half should lean blue, got R=%s B=%s" % (rr, rb)

    # <2 colors must fall back to the first stop as solid, never black.
    one = _base_fx(type="gradient", colors=["#FF6600"], angle=0.0)
    _o2, view2, _l2, _e2 = _paint_line(one, text="HELLO")
    m2 = _opaque_mask(view2)
    assert int(m2.sum()) > 40
    r2, g2, b2 = _mean_rgb(view2, m2)
    assert r2 + g2 + b2 > 60, (r2, g2, b2)
    assert r2 > b2 and r2 > g2, (r2, g2, b2)


def test_t19_emphasis_signal():
    """Changing emphasis.color moves the mean of the highlighted word's ROI."""
    _qapp()
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()

    def _style(color):
        st = _base_fx(type="solid", colors=["#F4EBD8"])
        st["fontsize"] = 72
        st["fx"]["emphasis"] = {
            "words": ["PIXEL"],
            "color": color,
            "palette": [],
            "scale": 1.0,
        }
        return st

    def _word_mean(color):
        import numpy as np
        owned, view, lay, _event = _paint_line(
            _style(color), text="Hello PIXEL Lock", t=0.55)
        run = None
        for line in lay.lines:
            for item in line.runs:
                if item.text == "PIXEL":
                    run = item
                    break
        assert run is not None, "PIXEL run missing"
        ox, oy = lay.origin
        x0 = int(max(0, run.x - ox))
        y0 = int(max(0, run.y - oy))
        x1 = int(min(view.shape[1], x0 + max(1, int(run.w))))
        y1 = int(min(view.shape[0], y0 + max(1, int(run.h))))
        roi = view[y0:y1, x0:x1]
        mask = _opaque_mask(roi, alpha_min=40)
        assert int(mask.sum()) > 10, (x0, y0, x1, y1, roi.shape)
        return _mean_rgb(roi, mask)

    red = _word_mean("#FF0000")
    blue = _word_mean("#0000FF")
    assert red[0] + red[1] + red[2] > 60, red
    assert blue[0] + blue[1] + blue[2] > 60, blue
    assert red[0] > red[2], red
    assert blue[2] > blue[0], blue
    assert red[0] - blue[0] > 40, (red, blue)
    assert blue[2] - red[2] > 40, (red, blue)


def test_t19_no_v1_color_keys():
    """collect() must not emit color_text / color_high."""
    _qapp()
    panel = _panel()
    style = _base_fx(type="solid", colors=["#ABCDEF"])
    style["color_text"] = "#111111"
    style["color_high"] = "#222222"
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    got = panel.collect()
    assert "color_text" not in got, got.keys()
    assert "color_high" not in got, got.keys()
    fx = got.get("fx") or {}
    fill = fx.get("fill") or {}
    assert fill.get("type") == "solid"
    assert fill.get("colors")
    panel.close()


def test_t19_no_v1_color_widgets():
    """panel_text / panel_high must be deleted, not hidden."""
    _qapp()
    panel = _panel()
    assert getattr(panel, "panel_text", None) is None
    assert getattr(panel, "panel_high", None) is None
    assert getattr(panel, "fx_fill_type", None) is not None
    assert getattr(panel, "fx_fill_colors", None) is not None
    assert getattr(panel, "fx_fill_angle", None) is not None
    assert getattr(panel, "fx_fill_metal", None) is not None
    assert getattr(panel, "fx_emphasis_color", None) is not None
    assert getattr(panel, "fx_emphasis_palette", None) is not None
    assert getattr(panel, "fx_emphasis_scale", None) is not None
    panel.close()


def test_t19_fill_mode_mutex():
    """Solid / gradient / metal controls are mutually visible, not stacked."""
    _qapp()
    panel = _panel()
    combo = panel.fx_fill_type
    colors = panel.fx_fill_colors
    angle = panel.fx_fill_angle
    metal = panel.fx_fill_metal

    def _set(kind):
        idx = combo.findData(kind)
        assert idx >= 0, kind
        combo.setCurrentIndex(idx)

    def _shown(w):
        return w is not None and not w.isHidden()

    _set("solid")
    assert _shown(colors)
    assert not _shown(angle)
    assert not _shown(metal)
    chips = [c for c in colors._swatches if _shown(c)]
    assert len(chips) == 1, len(chips)

    _set("gradient")
    assert _shown(colors)
    assert _shown(angle)
    assert not _shown(metal)
    chips = [c for c in colors._swatches if _shown(c)]
    assert 2 <= len(chips) <= 5, len(chips)

    _set("metal")
    assert not _shown(colors)
    assert not _shown(angle)
    assert _shown(metal)
    panel.close()


def test_t19_gradient_swatches_are_squares():
    """Gradient stops are ≥24×24 color chips, not a long unlabeled bar."""
    _qapp()
    panel = _panel()
    idx = panel.fx_fill_type.findData("gradient")
    panel.fx_fill_type.setCurrentIndex(idx)
    chips = [c for c in panel.fx_fill_colors._swatches if not c.isHidden()]
    assert len(chips) >= 2
    for chip in chips:
        assert chip.width() >= CHIP_MIN and chip.height() >= CHIP_MIN, (
            chip.width(), chip.height())
        assert chip.width() <= 48 and chip.height() <= 48, (
            chip.width(), chip.height())
        assert chip.text() in ("", None)
    panel.close()


def test_t19_color_section_live():
    """T18 pixel lock: every 配色方案 control must change export pixels."""
    from test_t18_no_dead_controls import (
        _base_style, _backdrop, _catalog, _merge_collect, _nudge, _panel,
        _qapp, _render, _install_color_mock,
    )
    _qapp()
    _install_color_mock()
    catalog = [row for row in _catalog() if row[1] == "配色方案"]
    assert catalog, "配色方案 section is empty"
    names = [row[0] for row in catalog]
    assert "panel_text" not in names
    assert "panel_high" not in names
    assert "fx_fill_type" in names
    assert "fx_fill_colors" in names
    assert "fx_fill_angle" in names
    assert "fx_fill_metal" in names
    assert "fx_emphasis_color" not in names

    backdrop = _backdrop()
    base = _base_style()
    panel = _panel()
    panel.load_data(copy.deepcopy(base))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(base)
    md5_a = _render(_merge_collect(panel, panel.collect()), backdrop)
    dead = []
    for attr, section, key, kind, evidence in catalog:
        style = copy.deepcopy(base)
        panel.load_data(style)
        panel.block_signals = False
        panel.current_data = style
        widget = getattr(panel, attr, None)
        assert widget is not None, attr
        _nudge(panel, attr, widget)
        collected = panel.collect()
        style_b = _merge_collect(panel, collected)
        md5_b = _render(style_b, backdrop)
        if md5_a == md5_b:
            dead.append(attr)
    panel.close()
    assert not dead, "配色方案 still dead: %s" % dead
