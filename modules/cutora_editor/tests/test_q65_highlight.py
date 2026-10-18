# -*- coding: utf-8 -*-
"""Q65: follow_word.tint / follow_word.box each own their color."""
from __future__ import annotations

import copy
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
TEXT = "ONE TWO"
WORDS = [
    {"word": "ONE", "start": 0.0, "end": 0.8},
    {"word": "TWO", "start": 0.8, "end": 1.8},
]
T_CUR = 1.25


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q65-highlight"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _mean_rgb(view, mask):
    pix = view[mask]
    b = float(pix[:, 0].mean())
    g = float(pix[:, 1].mean())
    r = float(pix[:, 2].mean())
    return r, g, b


def _opaque_mask(view, alpha_min=40):
    return view[..., 3] >= alpha_min


def _bgra(img):
    from cutora.render.textfx.raster import as_bgra
    owned, view = as_bgra(img)
    return owned, view


def _run_named(lay, name):
    key = str(name).strip()
    for line in lay.lines:
        for run in line.runs:
            if str(run.text or "").strip() == key:
                return run
    return None


def _roi_slice(view, lay, run, pad=1):
    ox, oy = lay.origin
    h, w = view.shape[:2]
    x0 = int(max(0, run.x - ox - pad))
    y0 = int(max(0, run.y - oy - pad))
    x1 = int(min(w, run.x - ox + max(1.0, run.w) + pad))
    y1 = int(min(h, run.y - oy + max(1.0, run.h) + pad))
    return view[y0:y1, x0:x1]


def _canvas_roi(view, run, pad=1):
    h, w = view.shape[:2]
    x0 = int(max(0, run.x - pad))
    y0 = int(max(0, run.y - pad))
    x1 = int(min(w, run.x + max(1.0, run.w) + pad))
    y1 = int(min(h, run.y + max(1.0, run.h) + pad))
    return view[y0:y1, x0:x1]


def _q51_style(**extra_fx):
    """Q51-era JSON: bool follow_word + sibling flags + emphasis / bg / box_highlight."""
    fx = {
        "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
        "stroke": {"width_em": 0, "color": "#141A26"},
        "glow": {"sigma_em": 0, "strength": 0},
        "shadow": {"sigma_em": 0, "alpha": 0, "enabled": False},
        "bg": {"type": "none", "color": "#0B1A33", "alpha": 0.55, "radius_em": 0.4},
        "anim": {"in": "none", "loop": "none", "out": "none"},
        "emphasis": {
            "words": [],
            "auto": [],
            "color": "#22D3EE",
            "type": "solid",
            "colors": ["#22D3EE"],
            "angle": 90.0,
            "palette": [],
            "scale": 1.0,
        },
        "follow_word": True,
        "follow_word_tint": True,
        "follow_word_box": True,
    }
    fx.update(extra_fx)
    return {
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": fx,
    }


def _event(style, text=TEXT, words=None, t=T_CUR):
    words = WORDS if words is None else words
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": list(words),
        "_style": style,
    }


def _layout_and_raster(style, t=T_CUR, text=TEXT, words=None):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_roi
    from cutora.render.textfx.spec import from_style

    event = _event(style, text=text, words=words, t=t)
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=True, t=t, event=event,
    )
    owned, view = _bgra(img)
    return owned, view, lay, event, spec


def _task(style, t=T_CUR, text=TEXT, words=None):
    from cutora.model.timeline import Timeline
    words = WORDS if words is None else words
    return {
        "style": copy.deepcopy(style),
        "segments": [{
            "text": text,
            "start": 0.0,
            "end": 2.0,
            "id": "s0",
            "words": list(words),
        }],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }


def _preview_frame(style, t=T_CUR, text=TEXT, words=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer

    task = _task(style, t=t, text=text, words=words)
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#142028"))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(
            p, task, CANVAS_W, CANVAS_H, t, render_order=["subs"])
    finally:
        p.end()
    return img


def _export_frame(style, t=T_CUR, text=TEXT, words=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    task = _task(style, t=t, text=text, words=words)

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#142028"))

    return ExportRenderer.render_frame(
        task, CANVAS_W, CANVAS_H, t, draw_background=_bg)


def _mad(a, b):
    va = _bgra(a)[1].astype(np.int16)
    vb = _bgra(b)[1].astype(np.int16)
    return float(np.mean(np.abs(va - vb)))


def _glyph_rgb(view, lay, run):
    roi = _roi_slice(view, lay, run)
    mask = _opaque_mask(roi, alpha_min=230)
    assert int(mask.sum()) > 10, "glyph ROI empty"
    return _mean_rgb(roi, mask)


def _box_rgb(view, lay, run):
    """Mid-alpha pad around the current word (the box, not the glyph)."""
    wide = _roi_slice(view, lay, run, pad=16)
    tight = _roi_slice(view, lay, run, pad=1)
    # Compare alpha in the pad ring of `wide`.
    a = wide[..., 3]
    ring = (a >= 30) & (a < 180)
    # Knock out the inner glyph-sized patch so we don't mix ink.
    ih, iw = tight.shape[:2]
    yh0 = max(0, (wide.shape[0] - ih) // 2)
    xh0 = max(0, (wide.shape[1] - iw) // 2)
    inner = np.zeros(wide.shape[:2], dtype=bool)
    inner[yh0:yh0 + ih, xh0:xh0 + iw] = tight[..., 3] >= 200
    ring = ring & ~inner
    assert int(ring.sum()) > 20, ("box pad empty", int(ring.sum()), int((a >= 30).sum()))
    return _mean_rgb(wide, ring)


def _nested_follow(tint_color, box_color, tint_on=True, box_on=True):
    style = _q51_style(
        emphasis={
            "words": [], "auto": [], "color": "#22D3EE", "type": "solid",
            "colors": ["#22D3EE"], "angle": 90.0, "palette": [], "scale": 1.0,
        },
        follow_word={
            "tint": {
                "enabled": bool(tint_on), "type": "solid",
                "colors": [tint_color], "angle": 90.0,
            },
            "box": {
                "enabled": bool(box_on), "color": box_color,
                "alpha": 0.55, "pad_em": 0.18, "radius_em": 0.2,
            },
        },
    )
    style["fx"].pop("follow_word_tint", None)
    style["fx"].pop("follow_word_box", None)
    style["fx"].pop("box_highlight", None)
    return style


def test_q65_box_color_independent_of_tint():
    """Box color is its own knob. Unchecking 换字色 must not retint the box."""
    _qapp()
    _fonts()
    red_box = _nested_follow("#00FF00", "#FF0000", tint_on=True, box_on=True)
    blu_box = _nested_follow("#00FF00", "#0000FF", tint_on=True, box_on=True)

    owned_r, view_r, lay_r, _e, spec_r = _layout_and_raster(red_box)
    owned_b, view_b, lay_b, _e2, spec_b = _layout_and_raster(blu_box)
    assert spec_r.follow_word.tint.colors[0].upper() == "#00FF00"
    assert spec_r.follow_word.box.color.upper() == "#FF0000"
    cur_r = _run_named(lay_r, "TWO")
    cur_b = _run_named(lay_b, "TWO")
    assert cur_r is not None and cur_b is not None
    g_r = _glyph_rgb(view_r, lay_r, cur_r)
    g_b = _glyph_rgb(view_b, lay_b, cur_b)
    # Same tint, different box → glyphs stay green.
    assert abs(g_r[0] - g_b[0]) < 8, (g_r, g_b)
    assert abs(g_r[1] - g_b[1]) < 8, (g_r, g_b)
    assert g_r[1] > g_r[0] + 30, g_r
    box_r = _box_rgb(view_r, lay_r, cur_r)
    box_b = _box_rgb(view_b, lay_b, cur_b)
    assert box_r[0] - box_b[0] > 40, (box_r, box_b)
    assert box_b[2] - box_r[2] > 40, (box_r, box_b)

    tint_off = _nested_follow("#00FF00", "#FF0000", tint_on=False, box_on=True)
    owned_o, view_o, lay_o, _e3, spec_o = _layout_and_raster(tint_off)
    assert spec_o.follow_word.tint.enabled is False
    assert spec_o.follow_word.box.enabled is True
    cur_o = _run_named(lay_o, "TWO")
    assert cur_o is not None
    g_off = _glyph_rgb(view_o, lay_o, cur_o)
    # Fill #F4EBD8 keeps R; green tint-on kills R relative to G.
    assert g_off[0] > g_r[0] + 20, (g_off, g_r)
    box_off = _box_rgb(view_o, lay_o, cur_o)
    # Unchecking 换字色 must not jump the box off red.
    assert box_off[0] - box_off[1] > 30, ("box jumped off red", box_off, box_r)
    assert abs(box_off[0] - box_r[0]) < 12, (box_off, box_r)

    prev_img = _preview_frame(red_box)
    exp_img = _export_frame(red_box)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)


def test_q65_highlight_not_locked():
    """New project: 当前词字色 is in 跟随单词; no 配色方案 lock to flip first."""
    _qapp()
    _fonts()
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.widgets.collapsible import CollapsibleSection

    style = _q51_style()
    style["fx"]["follow_word"] = False
    panel = SubtitleStylePanel()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    assert getattr(panel, "sub_fill_en", None) is None
    fill_sec = None
    follow_sec = None
    for sec in panel.findChildren(CollapsibleSection):
        title = getattr(sec, "section_title", "")
        if title == "配色方案":
            fill_sec = sec
        if title == "跟随单词":
            follow_sec = sec
    assert fill_sec is not None
    assert getattr(fill_sec, "_toggle_cb", None) is None
    assert fill_sec._content.isEnabled() is True
    assert follow_sec is not None
    panel.sub_dyn.setChecked(True)
    panel.sub_dyn_tint.setChecked(True)
    colors_w = getattr(panel, "fx_follow_tint_colors", None)
    assert colors_w is not None, "当前词字色 widget missing"
    colors_w.set_colors(["#FF0000"], emit=False)
    panel.on_change()
    got = panel.collect()
    fw = (got.get("fx") or {}).get("follow_word") or {}
    tint = fw.get("tint") if isinstance(fw, dict) else {}
    cols = [str(c).upper() for c in (tint.get("colors") or [])]
    assert "#FF0000" in cols, fw
    owned, view, lay, _e, spec = _layout_and_raster(got)
    cur = _run_named(lay, "TWO")
    assert cur is not None
    r, g, b = _glyph_rgb(view, lay, cur)
    assert r > g + 40, (r, g, b)
    prev_img = _preview_frame(got)
    exp_img = _export_frame(got)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)
    panel.close()


def test_q65_current_word_beats_emphasis():
    """Current word in the keyword list paints follow_word.tint, not 强调色."""
    _qapp()
    _fonts()
    style = _q51_style(
        emphasis={
            "words": ["TWO"],
            "auto": [],
            "color": "#00FF00",
            "type": "solid",
            "colors": ["#00FF00"],
            "angle": 90.0,
            "palette": [],
            "scale": 1.0,
        },
        follow_word={
            "tint": {
                "enabled": True, "type": "solid",
                "colors": ["#FF0000"], "angle": 90.0,
            },
            "box": {"enabled": False, "color": "#0B1A33"},
        },
    )
    style["fx"].pop("follow_word_tint", None)
    style["fx"].pop("follow_word_box", None)
    owned, view, lay, _e, spec = _layout_and_raster(style)
    cur = _run_named(lay, "TWO")
    prev = _run_named(lay, "ONE")
    assert cur is not None and prev is not None
    assert bool(cur.emphasized), "TWO must be in the keyword list"
    r, g, b = _glyph_rgb(view, lay, cur)
    assert r > g + 40, ("current word should be follow tint red, not emphasis green", r, g, b)
    pr, pg, pb = _glyph_rgb(view, lay, prev)
    # ONE is not current and not a keyword → fill cream, not red.
    assert abs(pr - pg) < 40, (pr, pg, pb)
    prev_img = _preview_frame(style)
    exp_img = _export_frame(style)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)
    _, pview = _bgra(prev_img)
    cr, cg, cb = _mean_rgb(
        _canvas_roi(pview, cur),
        _opaque_mask(_canvas_roi(pview, cur), alpha_min=200))
    assert cr > cg + 20, (cr, cg, cb)


def test_q65_karaoke_gradient():
    """Karaoke sweep uses follow_word.tint gradient, not a single stop."""
    _qapp()
    _fonts()
    text = "ONE WIDEWORD"
    words = [
        {"word": "ONE", "start": 0.0, "end": 0.5},
        {"word": "WIDEWORD", "start": 0.5, "end": 2.0},
    ]
    # Late in WIDEWORD so the sweep covers ~90% and both gradient stops show.
    t = 1.85
    style = _q51_style(
        follow_word={
            "tint": {
                "enabled": True,
                "type": "gradient",
                "colors": ["#FF0000", "#0000FF"],
                "angle": 0.0,
            },
            "box": {"enabled": False, "color": "#0B1A33"},
        },
        anim={"in": "none", "loop": "karaoke", "out": "none"},
    )
    owned, view, lay, _e, spec = _layout_and_raster(
        style, t=t, text=text, words=words)
    assert spec.follow_word.tint.type == "gradient"
    run = _run_named(lay, "WIDEWORD")
    assert run is not None
    roi = _roi_slice(view, lay, run, pad=2)
    mask = _opaque_mask(roi, alpha_min=200)
    assert int(mask.sum()) > 40, "karaoke current word painted no glyphs"
    xs = mask.nonzero()[1]
    x0, x1 = int(xs.min()), int(xs.max())
    # Swept ~90%. Sample left-of-cut vs near the cut, plus a fill strip on the right.
    cut = x0 + int(round((x1 - x0) * 0.90))
    yy, xx = np.nonzero(mask)
    left_m = np.zeros(mask.shape, dtype=bool)
    right_m = np.zeros(mask.shape, dtype=bool)
    swept = (xx < cut)
    if int(swept.sum()) < 20:
        swept = np.ones(xx.shape, dtype=bool)
    xs_s = xx[swept]
    lo = float(np.percentile(xs_s, 15))
    hi = float(np.percentile(xs_s, 85))
    left_m[yy[xx <= lo], xx[xx <= lo]] = True
    right_m[yy[(xx >= hi) & (xx < cut)], xx[(xx >= hi) & (xx < cut)]] = True
    assert int(left_m.sum()) > 10 and int(right_m.sum()) > 8, (
        int(left_m.sum()), int(right_m.sum()), cut, x0, x1)
    lr, lg, lb = _mean_rgb(roi, left_m)
    rr, rg, rb = _mean_rgb(roi, right_m)
    assert lr > lb + 15, "swept left should lean red, got R=%s B=%s" % (lr, lb)
    assert rb > rr + 10, "swept right should lean blue, got R=%s B=%s" % (rr, rb)

    # Early in the word the right half is still fill, not the blue stop.
    owned_e, view_e, lay_e, _ee, _se = _layout_and_raster(
        style, t=0.58, text=text, words=words)
    run_e = _run_named(lay_e, "WIDEWORD")
    assert run_e is not None
    roi_e = _roi_slice(view_e, lay_e, run_e, pad=2)
    mask_e = _opaque_mask(roi_e, alpha_min=200)
    ey, ex = np.nonzero(mask_e)
    emid = float(ex.min() + ex.max()) * 0.5
    eright = np.zeros(mask_e.shape, dtype=bool)
    eright[ey[ex >= emid], ex[ex >= emid]] = True
    assert int(eright.sum()) > 10
    er, eg, eb = _mean_rgb(roi_e, eright)
    assert eg > eb + 8 and eb < 230, (
        "early karaoke right half should stay fill, not blue", er, eg, eb)

    prev_img = _preview_frame(style, t=t, text=text, words=words)
    exp_img = _export_frame(style, t=t, text=text, words=words)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)


def test_q65_migration():
    """Q51-era JSON rasters byte-identical to the nested equivalent."""
    _qapp()
    _fonts()
    old = _q51_style(
        bg={"type": "none", "color": "#00AA00", "alpha": 0.85, "radius_em": 0.4},
        emphasis={
            "words": [], "auto": [], "color": "#FF0000", "type": "solid",
            "colors": ["#FF0000"], "angle": 90.0, "palette": [], "scale": 1.0,
        },
    )
    from cutora.render.textfx.spec import from_style
    spec = from_style(old)
    assert spec.follow_word.tint.enabled is True
    assert spec.follow_word.box.enabled is True
    tint_cols = [c.upper() for c in spec.follow_word.tint.colors]
    assert "#FF0000" in tint_cols, tint_cols
    # Q51 tint_also: box fell back to bg.color.
    assert spec.follow_word.box.color.upper() == "#00AA00", spec.follow_word.box.color

    nested = copy.deepcopy(old)
    nested["fx"]["follow_word"] = {
        "tint": {
            "enabled": True,
            "type": spec.follow_word.tint.type,
            "colors": list(spec.follow_word.tint.colors),
            "angle": spec.follow_word.tint.angle,
        },
        "box": {
            "enabled": True,
            "color": spec.follow_word.box.color,
            "alpha": spec.follow_word.box.alpha,
            "pad_em": spec.follow_word.box.pad_em,
            "radius_em": spec.follow_word.box.radius_em,
            "offset_y_em": spec.follow_word.box.offset_y_em,
        },
    }
    nested["fx"].pop("follow_word_tint", None)
    nested["fx"].pop("follow_word_box", None)
    nested["fx"].pop("box_highlight", None)

    owned_old, view_old, lay_old, _e1, _s1 = _layout_and_raster(old)
    owned_new, view_new, lay_new, _e2, _s2 = _layout_and_raster(nested)
    assert view_old.shape == view_new.shape
    diff = np.abs(view_old.astype(np.int16) - view_new.astype(np.int16))
    assert int(diff.max()) == 0, ("migration not byte-identical", int(diff.max()),
                                  float(diff.mean()))
    cur = _run_named(lay_old, "TWO")
    assert cur is not None
    g = _glyph_rgb(view_old, lay_old, cur)
    assert g[0] > g[1] + 30, g
    b = _box_rgb(view_old, lay_old, cur)
    assert b[1] > b[0] + 20, b

    prev_img = _preview_frame(old)
    exp_img = _export_frame(old)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)
    prev_n = _preview_frame(nested)
    assert _mad(prev_img, prev_n) < 0.5, _mad(prev_img, prev_n)
