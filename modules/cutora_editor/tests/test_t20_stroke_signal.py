# -*- coding: utf-8 -*-
"""Q20: 描边 binds fx.stroke; v1 border_width / color_outline / opacity_outline are dead."""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
V1_STROKE_KEYS = (
    "border_width", "color_outline", "opacity_outline", "use_stroke",
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t20-stroke-signal"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _opaque_mask(view, alpha_min=40):
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


def _base_fx(**stroke_kw):
    stroke = {
        "width_em": 0,
        "color": "#E11D48",
        "join": "round",
    }
    stroke.update(stroke_kw)
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": stroke,
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"sigma_em": 0, "alpha": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _ring_mask(core):
    import cv2
    import numpy as np
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    dilated = cv2.dilate(core.astype(np.uint8), k).astype(bool)
    return dilated & ~core


def test_t20_stroke_signal():
    """width_em 0 → 0.08: opaque pixels on the expanded glyph ring > 1.5× baseline."""
    _qapp()
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()

    off = _base_fx(width_em=0)
    on = _base_fx(width_em=0.08)
    _o0, view0, _l0, _e0 = _paint_line(off, text="HELLO")
    _o1, view1, _l1, _e1 = _paint_line(on, text="HELLO")
    core = _opaque_mask(view0)
    assert int(core.sum()) > 80, "fill painted no glyphs"
    ring = _ring_mask(core)
    assert int(ring.sum()) > 40, "expanded ring is empty"
    base_n = int((_opaque_mask(view0) & ring).sum())
    on_n = int((_opaque_mask(view1) & ring).sum())
    assert on_n > 50, on_n
    assert on_n > max(base_n, 1) * 1.5, (on_n, base_n)


def test_t20_stroke_alpha():
    """Stroke #FF000080 is clearly more transparent on the ring than #FF0000FF."""
    _qapp()
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()

    solid = _base_fx(width_em=0.08, color="#FF0000FF")
    faded = _base_fx(width_em=0.08, color="#FF000080")
    _os, views, _ls, _es = _paint_line(solid, text="HELLO")
    _of, viewf, _lf, _ef = _paint_line(faded, text="HELLO")
    core = _opaque_mask(_paint_line(_base_fx(width_em=0), text="HELLO")[1])
    ring = _ring_mask(core)
    solid_pix = views[ring]
    fade_pix = viewf[ring]
    a_solid = solid_pix[solid_pix[:, 3] > 8][:, 3]
    a_fade = fade_pix[fade_pix[:, 3] > 8][:, 3]
    assert a_solid.size > 20 and a_fade.size > 20, (a_solid.size, a_fade.size)
    mean_s = float(a_solid.mean())
    mean_f = float(a_fade.mean())
    assert mean_s > 80, mean_s
    assert mean_f < mean_s * 0.75, (mean_f, mean_s)


def test_t20_stroke_toggle():
    """Off writes enabled=False and keeps width_em; on restores 0.12, not 0.08."""
    _qapp()
    panel = _panel()
    style = _base_fx(width_em=0.12, color="#3A0A14")
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    panel.fx_stroke_width_em.setValue(0.12)
    panel.sub_strk.setChecked(True)
    got = panel.collect()
    assert abs(float(got["fx"]["stroke"]["width_em"]) - 0.12) < 1e-6, got["fx"]["stroke"]

    panel.sub_strk.setChecked(False)
    off = panel.collect()
    assert off["fx"]["stroke"].get("enabled") is False, off["fx"]["stroke"]
    assert abs(float(off["fx"]["stroke"]["width_em"]) - 0.12) < 1e-6, off["fx"]["stroke"]

    panel.sub_strk.setChecked(True)
    on = panel.collect()
    restored = float(on["fx"]["stroke"]["width_em"])
    assert abs(restored - 0.12) < 1e-6, restored
    assert abs(restored - 0.08) > 1e-6, restored
    panel.close()


def test_t20_no_v1_stroke_keys():
    """collect() has no v1 stroke keys; dead widgets are gone; labels are unique."""
    from PySide6.QtWidgets import QLabel
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    _qapp()
    panel = _panel()
    style = _base_fx(width_em=0.08)
    style["border_width"] = 4
    style["color_outline"] = "#111111"
    style["opacity_outline"] = 180
    style["use_stroke"] = True
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    got = panel.collect()
    for key in V1_STROKE_KEYS:
        assert key not in got, (key, got.keys())
        fx = got.get("fx") or {}
        assert key not in fx, (key, fx.keys())
        stroke = (fx.get("stroke") or {})
        assert key not in stroke, (key, stroke)

    assert getattr(panel, "sub_stroke", None) is None
    assert getattr(panel, "sub_c_out", None) is None
    assert getattr(panel, "sub_out_op", None) is None
    assert getattr(panel, "sub_out_op_spin", None) is None
    assert getattr(panel, "fx_stroke_width_em", None) is not None
    assert getattr(panel, "fx_stroke_color", None) is not None
    assert getattr(panel, "fx_stroke_join", None) is not None

    labels = []
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") != "描边":
            continue
        for lab in sec.findChildren(QLabel):
            text = (lab.text() or "").strip()
            if text:
                labels.append(text)
    blob = " ".join(labels)
    assert "粗细" not in blob, labels
    assert "描边宽度" in blob, labels
    assert "描边颜色" in blob, labels
    assert "描边拐角" in blob, labels
    panel.close()


def test_t20_stroke_section_live():
    """T18 pixel lock: every 描边 control must change export pixels."""
    from test_t18_no_dead_controls import (
        _base_style, _backdrop, _catalog, _merge_collect, _nudge, _panel,
        _qapp, _render, _install_color_mock,
    )
    _qapp()
    _install_color_mock()
    catalog = [row for row in _catalog() if row[1] == "描边"]
    assert catalog, "描边 section is empty"
    names = [row[0] for row in catalog]
    assert "sub_stroke" not in names
    assert "sub_c_out" not in names
    assert "sub_out_op" not in names
    assert "sub_strk" in names
    assert "fx_stroke_width_em" in names
    assert "fx_stroke_color" in names
    assert "fx_stroke_join" in names

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
    assert not dead, "描边 still dead: %s" % dead
