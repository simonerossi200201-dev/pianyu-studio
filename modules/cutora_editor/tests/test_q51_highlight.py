# -*- coding: utf-8 -*-
"""Q51: 高亮色 follows the current word; panel collect does not clobber; gradient."""
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
# Well into TWO so highlight fade (80 ms) and box spring (250 ms) have settled.
T_CUR = 1.25


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q51-highlight"])


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


def _base_style(**emph_kw):
    emph = {
        "words": [],
        "auto": [],
        "color": "#22D3EE",
        "type": "solid",
        "colors": ["#22D3EE"],
        "angle": 0.0,
        "palette": [],
        "scale": 1.0,
    }
    emph.update(emph_kw)
    return {
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"sigma_em": 0, "alpha": 0, "enabled": False},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": emph,
            "follow_word": True,
        },
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


def test_q51_anim_ids_keeps_three():
    """A.2: highlight+box_highlight+line_bar must not drop the bar."""
    from cutora.render.textfx.anim import _anim_ids
    from cutora.render.textfx.spec import from_style

    ids = _anim_ids("highlight+box_highlight+line_bar")
    assert ids == ["highlight", "box_highlight", "line_bar"], ids
    spec = from_style({
        "fx": {
            "follow_word": True,
            "follow_sentence": True,
            "anim": {"loop": "none"},
        },
    })
    got = _anim_ids(spec.anim.loop)
    assert "highlight" in got
    assert "box_highlight" in got
    assert "line_bar" in got


def test_q51_follow_word_tint_follows_emphasis_color():
    """follow_word=True, empty words/auto: current word tracks 高亮色, previous does not."""
    _qapp()
    _fonts()

    def _pair(color):
        style = _base_style(color=color, colors=[color], type="solid")
        owned, view, lay, _event, spec = _layout_and_raster(style)
        assert spec.follow_word
        assert spec.emphasis.words == []
        assert spec.emphasis.auto == []
        cur = _run_named(lay, "TWO")
        prev = _run_named(lay, "ONE")
        assert cur is not None and prev is not None
        cur_roi = _roi_slice(view, lay, cur)
        prev_roi = _roi_slice(view, lay, prev)
        # Trailing space on "ONE " sits next to TWO's box pad; measure the
        # glyph body, not the gap, so a moving box is not "整句染色".
        pw = prev_roi.shape[1]
        prev_roi = prev_roi[:, :max(1, int(pw * 0.55))]
        # alpha≥200 keeps glyph ink (opaque) and drops the 0.55 box, so this
        # cannot pass from the box picking up emphasis.color alone.
        cm = _opaque_mask(cur_roi, alpha_min=200)
        pm = _opaque_mask(prev_roi, alpha_min=200)
        assert int(cm.sum()) > 10, "current word ROI empty"
        assert int(pm.sum()) > 10, "previous word ROI empty"
        return _mean_rgb(cur_roi, cm), _mean_rgb(prev_roi, pm), style, lay

    red_cur, red_prev, style_r, lay_r = _pair("#FF0000")
    grn_cur, grn_prev, style_g, lay_g = _pair("#00FF00")
    assert red_cur[0] - grn_cur[0] > 40, (red_cur, grn_cur)
    assert grn_cur[1] - red_cur[1] > 40, (red_cur, grn_cur)
    assert abs(red_prev[0] - grn_prev[0]) < 5, (red_prev, grn_prev)
    assert abs(red_prev[1] - grn_prev[1]) < 5, (red_prev, grn_prev)

    prev_img = _preview_frame(style_r)
    exp_img = _export_frame(style_r)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)

    _, pview = _bgra(prev_img)
    _, eview = _bgra(exp_img)
    cur = _run_named(lay_r, "TWO")
    prev = _run_named(lay_r, "ONE")
    for view, label in ((pview, "preview"), (eview, "export")):
        cm = _opaque_mask(_canvas_roi(view, cur), alpha_min=200)
        pm = _opaque_mask(_canvas_roi(view, prev), alpha_min=200)
        assert int(cm.sum()) > 10, label
        assert int(pm.sum()) > 10, label
        cr, cg, cb = _mean_rgb(_canvas_roi(view, cur), cm)
        assert cr > cg + 20, (label, cr, cg, cb)

    prev_g = _preview_frame(style_g)
    _, gview = _bgra(prev_g)
    gg = _mean_rgb(
        _canvas_roi(gview, cur),
        _opaque_mask(_canvas_roi(gview, cur), alpha_min=200))
    rg = _mean_rgb(
        _canvas_roi(pview, cur),
        _opaque_mask(_canvas_roi(pview, cur), alpha_min=200))
    assert rg[0] - gg[0] > 40, (rg, gg)
    assert gg[1] - rg[1] > 40, (rg, gg)


def test_q51_panel_collect_does_not_clobber():
    """Real panel collect: sub_dyn + 高亮色 #FF00FF must reach paint, not #22D3EE."""
    _qapp()
    _fonts()
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    style = _base_style()
    style["fx"]["follow_word"] = False
    panel = SubtitleStylePanel()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    panel.sub_dyn.setChecked(True)
    colors_w = getattr(panel, "fx_follow_tint_colors", None)
    assert colors_w is not None
    colors_w.set_colors(["#FF00FF"], emit=False)
    panel.on_change()
    got = panel.collect()
    fx = got.get("fx") or {}
    fw = fx.get("follow_word") if isinstance(fx.get("follow_word"), dict) else {}
    tint = fw.get("tint") if isinstance(fw.get("tint"), dict) else {}
    cols = [str(c).upper() for c in (tint.get("colors") or [])]
    assert "#FF00FF" in cols, (fw, tint)
    assert bool(tint.get("enabled"))

    owned, view, lay, _event, _spec = _layout_and_raster(got)
    cur = _run_named(lay, "TWO")
    assert cur is not None
    roi = _roi_slice(view, lay, cur)
    mask = _opaque_mask(roi)
    assert int(mask.sum()) > 10
    pix = roi[mask]
    mag = (
        (pix[:, 2].astype(np.int16) > 180)
        & (pix[:, 1].astype(np.int16) < 80)
        & (pix[:, 0].astype(np.int16) > 180)
    )
    assert int(mag.sum()) > 8, (
        "no #FF00FF in current-word ROI after panel collect",
        _mean_rgb(roi, mask), bh,
    )

    prev_img = _preview_frame(got)
    exp_img = _export_frame(got)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)
    _, pview = _bgra(prev_img)
    proi = _canvas_roi(pview, cur)
    pmask = _opaque_mask(proi)
    ppix = proi[pmask]
    pmag = (
        (ppix[:, 2].astype(np.int16) > 180)
        & (ppix[:, 1].astype(np.int16) < 80)
        & (ppix[:, 0].astype(np.int16) > 180)
    )
    assert int(pmag.sum()) > 8, _mean_rgb(proi, pmask)
    panel.close()


def test_q51_emphasis_gradient():
    """emphasis gradient red→blue: current-word left R>B, right B>R."""
    _qapp()
    _fonts()
    style = _base_style(
        type="gradient",
        colors=["#FF0000", "#0000FF"],
        angle=0.0,
        color="#FF0000",
    )
    text = "ONE WIDEWORD"
    words = [
        {"word": "ONE", "start": 0.0, "end": 0.8},
        {"word": "WIDEWORD", "start": 0.8, "end": 1.8},
    ]
    owned, view, lay, _event, spec = _layout_and_raster(
        style, t=T_CUR, text=text, words=words)
    assert spec.emphasis.type == "gradient"
    assert len(spec.emphasis.colors) >= 2
    run = _run_named(lay, "WIDEWORD")
    assert run is not None
    roi = _roi_slice(view, lay, run, pad=2)
    mask = _opaque_mask(roi, alpha_min=200)
    assert int(mask.sum()) > 40, "gradient current word painted no glyphs"
    xs = mask.nonzero()[1]
    mid = float(xs.min() + xs.max()) * 0.5
    yy, xx = np.nonzero(mask)
    left_m = np.zeros(mask.shape, dtype=bool)
    right_m = np.zeros(mask.shape, dtype=bool)
    left_m[yy[xx < mid], xx[xx < mid]] = True
    right_m[yy[xx >= mid], xx[xx >= mid]] = True
    assert int(left_m.sum()) > 15 and int(right_m.sum()) > 15, (
        int(left_m.sum()), int(right_m.sum()))
    lr, lg, lb = _mean_rgb(roi, left_m)
    rr, rg, rb = _mean_rgb(roi, right_m)
    assert lr > lb, "left half should lean red, got R=%s B=%s" % (lr, lb)
    assert rb > rr, "right half should lean blue, got R=%s B=%s" % (rr, rb)

    prev_img = _preview_frame(style, t=T_CUR, text=text, words=words)
    exp_img = _export_frame(style, t=T_CUR, text=text, words=words)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)
    _, pview = _bgra(prev_img)
    proi = _canvas_roi(pview, run, pad=2)
    pmask = _opaque_mask(proi, alpha_min=200)
    pxs = pmask.nonzero()[1]
    pmid = float(pxs.min() + pxs.max()) * 0.5
    pyy, pxx = np.nonzero(pmask)
    pl = np.zeros(pmask.shape, dtype=bool)
    pr = np.zeros(pmask.shape, dtype=bool)
    pl[pyy[pxx < pmid], pxx[pxx < pmid]] = True
    pr[pyy[pxx >= pmid], pxx[pxx >= pmid]] = True
    plr, _plg, plb = _mean_rgb(proi, pl)
    prr, _prg, prb = _mean_rgb(proi, pr)
    assert plr > plb, (plr, plb)
    assert prb > prr, (prr, prb)


def test_q51_follow_word_box_off_does_not_paint_box():
    """A.3: unchecking 词底方块 keeps glyph tint and drops the box."""
    _qapp()
    _fonts()
    from cutora.render.textfx.anim import _anim_ids

    style_on = _base_style(color="#FF0000", colors=["#FF0000"], type="solid")
    style_off = copy.deepcopy(style_on)
    style_off["fx"]["follow_word_box"] = False

    owned_on, view_on, lay, _e, spec_on = _layout_and_raster(style_on)
    owned_off, view_off, lay_off, _e2, spec_off = _layout_and_raster(style_off)
    ids_on = _anim_ids(spec_on.anim.loop)
    ids_off = _anim_ids(spec_off.anim.loop)
    assert "highlight" in ids_on and "box_highlight" in ids_on, ids_on
    assert "highlight" in ids_off and "box_highlight" not in ids_off, ids_off
    assert spec_off.emphasis.words == [] and spec_off.emphasis.auto == []

    cur_on = _run_named(lay, "TWO")
    cur_off = _run_named(lay_off, "TWO")
    assert cur_on is not None and cur_off is not None
    roi_off = _roi_slice(view_off, lay_off, cur_off, pad=14)
    roi_on = _roi_slice(view_on, lay, cur_on, pad=14)
    extra = int((roi_on[..., 3].astype(np.int16) > roi_off[..., 3].astype(np.int16) + 30).sum())
    assert extra > 20, ("box-off should drop the pad vs box-on", extra)

    cm = _opaque_mask(_roi_slice(view_off, lay_off, cur_off), alpha_min=200)
    assert int(cm.sum()) > 10
    r, g, b = _mean_rgb(_roi_slice(view_off, lay_off, cur_off), cm)
    assert r > g + 40, (r, g, b)

    prev_img = _preview_frame(style_off)
    exp_img = _export_frame(style_off)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)


def test_q51_follow_word_tint_off_keeps_fill_color():
    """A.3: unchecking 换字色 keeps the box, glyphs stay the fill color."""
    _qapp()
    _fonts()
    from cutora.render.textfx.anim import _anim_ids

    def _pair(color, tint):
        style = _base_style(color=color, colors=[color], type="solid")
        style["fx"]["follow_word_tint"] = bool(tint)
        owned, view, lay, _event, spec = _layout_and_raster(style)
        cur = _run_named(lay, "TWO")
        assert cur is not None
        roi = _roi_slice(view, lay, cur)
        cm = _opaque_mask(roi, alpha_min=200)
        assert int(cm.sum()) > 10
        return _mean_rgb(roi, cm), spec, style, lay, cur

    red_off, spec_off, style_off, lay_off, cur = _pair("#FF0000", False)
    grn_off, spec_g, style_g, _lay_g, _cur_g = _pair("#00FF00", False)
    red_on, spec_on, style_on, _lay_on, _cur_on = _pair("#FF0000", True)
    ids_off = _anim_ids(spec_off.anim.loop)
    ids_on = _anim_ids(spec_on.anim.loop)
    assert "box_highlight" in ids_off and "highlight" not in ids_off, ids_off
    assert "highlight" in ids_on and "box_highlight" in ids_on, ids_on
    assert spec_off.emphasis.words == [] and spec_off.emphasis.auto == []
    # Glyphs must not track 高亮色 when 换字色 is off.
    assert abs(red_off[0] - grn_off[0]) < 8, (red_off, grn_off)
    assert abs(red_off[1] - grn_off[1]) < 8, (red_off, grn_off)
    # Fill is already high-R (#F4EBD8); tint-on kills G, tint-off keeps it.
    assert red_off[1] - red_on[1] > 40, (red_on, red_off)

    prev_img = _preview_frame(style_off)
    exp_img = _export_frame(style_off)
    assert _mad(prev_img, exp_img) < 2.5, _mad(prev_img, exp_img)


def test_q51_panel_collect_nested_flags():
    """Real panel: 换字色 / 词底方块 checkboxes round-trip through collect."""
    _qapp()
    _fonts()
    import cutora.ui.panels.property  # noqa: F401
    from cutora.render.textfx.anim import _anim_ids
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    style = _base_style()
    style["fx"]["follow_word"] = False
    panel = SubtitleStylePanel()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    panel.sub_dyn.setChecked(True)
    panel.sub_dyn_tint.setChecked(True)
    panel.sub_dyn_box.setChecked(False)
    panel.on_change()
    got = panel.collect()
    fx = got.get("fx") or {}
    fw = fx.get("follow_word") if isinstance(fx.get("follow_word"), dict) else {}
    tint = fw.get("tint") if isinstance(fw.get("tint"), dict) else {}
    box = fw.get("box") if isinstance(fw.get("box"), dict) else {}
    assert bool(tint.get("enabled")) is True
    assert bool(box.get("enabled")) is False
    ids = _anim_ids(from_style(got).anim.loop)
    assert "highlight" in ids
    assert "box_highlight" not in ids, ids

    panel.sub_dyn_tint.setChecked(False)
    panel.sub_dyn_box.setChecked(True)
    panel.on_change()
    got2 = panel.collect()
    fx2 = got2.get("fx") or {}
    fw2 = fx2.get("follow_word") if isinstance(fx2.get("follow_word"), dict) else {}
    tint2 = fw2.get("tint") if isinstance(fw2.get("tint"), dict) else {}
    box2 = fw2.get("box") if isinstance(fw2.get("box"), dict) else {}
    assert bool(tint2.get("enabled")) is False
    assert bool(box2.get("enabled")) is True
    ids2 = _anim_ids(from_style(got2).anim.loop)
    assert "highlight" not in ids2, ids2
    assert "box_highlight" in ids2
    panel.close()
