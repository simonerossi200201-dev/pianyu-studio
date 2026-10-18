# -*- coding: utf-8 -*-
"""Q21: light-source shadow; v1 shadow_blur / color_shadow / opacity_shadow are dead."""
from __future__ import annotations

import copy
import hashlib
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
V1_SHADOW_KEYS = ("shadow_blur", "color_shadow", "opacity_shadow")
# Frozen from tools/_diag/t21_roi_before.py against HEAD before this task.
LEGACY_SHADOW_MD5 = "45d67f11f5bb117e80dd663f4feba505"


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t21-shadow"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


def _event(text="HELLO"):
    words = text.split()
    step = 0.4
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }


def _base_fx(shadow=None):
    sh = {
        "sigma_em": 0,
        "dx_em": 0.04,
        "dy_em": 0.06,
        "color": "#141A26",
        "alpha": 0.6,
    }
    if shadow:
        sh.update(shadow)
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": {"width_em": 0, "color": "#E11D48", "join": "round"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": sh,
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _paint(style, text="HELLO", skip_blur=False):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    event = _event(text)
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=skip_blur, t=0.55, event=event,
    )
    owned, view = as_bgra(img)
    return owned, view, lay


def _full(view, lay):
    canvas = np.zeros((CANVAS_H, CANVAS_W, 4), dtype=np.uint8)
    ox, oy = int(lay.origin[0]), int(lay.origin[1])
    h, w = view.shape[:2]
    y1 = min(CANVAS_H, oy + h)
    x1 = min(CANVAS_W, ox + w)
    canvas[oy:y1, ox:x1] = view[: y1 - oy, : x1 - ox]
    return canvas


def _md5(view):
    return hashlib.md5(view.tobytes()).hexdigest()


def _shadow_alpha(on_style, off_style, text="O"):
    _o_on, v_on, lay_on = _paint(on_style, text=text, skip_blur=False)
    _o_off, v_off, lay_off = _paint(off_style, text=text, skip_blur=False)
    a_on = _full(v_on, lay_on)[..., 3].astype(np.int16)
    a_off = _full(v_off, lay_off)[..., 3].astype(np.int16)
    diff = np.clip(a_on - a_off, 0, 255).astype(np.uint8)
    return diff, a_off, lay_on, v_on


def _centroid(mask):
    ys, xs = np.where(mask)
    assert xs.size > 20, int(xs.size)
    return float(xs.mean()), float(ys.mean())


def _new_shadow(**kw):
    sh = {
        "light": "parallel",
        "angle_deg": 45.0,
        "distance_em": 0.6,
        "size_em": 0.12,
        "falloff": 0.0,
        "color": "#141A26FF",
        "strength": 0.9,
    }
    sh.update(kw)
    return _base_fx(sh)


def _off_shadow():
    return _base_fx({"sigma_em": 0, "alpha": 0})


def test_t21_shadow_parallel():
    """angle 45 → shadow centroid bottom-right; 225 → top-left."""
    _qapp()
    off = _off_shadow()
    a45, glyph_a, _l, _v = _shadow_alpha(
        _new_shadow(light="parallel", angle_deg=45, distance_em=0.7, size_em=0.08),
        off,
    )
    a225, _g, _l2, _v2 = _shadow_alpha(
        _new_shadow(light="parallel", angle_deg=225, distance_em=0.7, size_em=0.08),
        off,
    )
    gx, gy = _centroid(glyph_a >= 40)
    x45, y45 = _centroid(a45 >= 20)
    x225, y225 = _centroid(a225 >= 20)
    assert x45 > gx, (x45, gx)
    assert y45 > gy, (y45, gy)
    assert x225 < gx, (x225, gx)
    assert y225 < gy, (y225, gy)


def test_t21_shadow_point():
    """Point light: distance 2.0 → 0.5 grows the shadow bbox."""
    _qapp()
    off = _off_shadow()
    far, _g, _l, _v = _shadow_alpha(
        _new_shadow(light="point", angle_deg=45, distance_em=2.0, size_em=0.08, falloff=0),
        off,
    )
    near, _g2, _l2, _v2 = _shadow_alpha(
        _new_shadow(light="point", angle_deg=45, distance_em=0.5, size_em=0.08, falloff=0),
        off,
    )

    def _area(a):
        ys, xs = np.where(a >= 16)
        assert xs.size > 30, int(xs.size)
        return int(xs.max() - xs.min() + 1) * int(ys.max() - ys.min() + 1)

    area_far = _area(far)
    area_near = _area(near)
    assert area_near > area_far, (area_near, area_far)


def test_t21_shadow_size():
    """size_em 0.05 → 0.5: max |d alpha / d n| along the shadow ray drops > 40%."""
    _qapp()
    off = _off_shadow()
    hard, _g, lay, _v = _shadow_alpha(
        _new_shadow(angle_deg=45, distance_em=0.8, size_em=0.05, falloff=0),
        off,
    )
    soft, _g2, _l2, _v2 = _shadow_alpha(
        _new_shadow(angle_deg=45, distance_em=0.8, size_em=0.5, falloff=0),
        off,
    )
    gx, gy = _centroid(_g >= 40)
    dx = np.sqrt(0.5)
    n = int(round(2.5 * lay.em))
    ts = np.arange(0, n, dtype=np.float32)

    def _max_diff(alpha):
        xs = np.clip(np.rint(gx + ts * dx).astype(int), 0, CANVAS_W - 1)
        ys = np.clip(np.rint(gy + ts * dx).astype(int), 0, CANVAS_H - 1)
        prof = alpha[ys, xs].astype(np.float32)
        assert float(prof.max()) > 20, float(prof.max())
        peak = int(np.argmax(prof))
        tail = prof[peak:]
        assert tail.size > 4, tail.size
        return float(np.max(np.abs(np.diff(tail))))

    d_hard = _max_diff(hard)
    d_soft = _max_diff(soft)
    assert d_hard > 4, d_hard
    assert d_soft < d_hard * 0.60, (d_soft, d_hard)


def test_t21_shadow_falloff():
    """falloff 0 → 1: far alpha drops > 50%, near stays within 25%."""
    _qapp()
    off = _off_shadow()
    none, glyph_a, lay, _v = _shadow_alpha(
        _new_shadow(angle_deg=45, distance_em=1.1, size_em=0.08, falloff=0.0),
        off,
    )
    faded, _g, _l, _v2 = _shadow_alpha(
        _new_shadow(angle_deg=45, distance_em=1.1, size_em=0.08, falloff=1.0),
        off,
    )
    gx, gy = _centroid(glyph_a >= 40)
    dx = np.sqrt(0.5)
    ys, xs = np.where(none >= 16)
    assert xs.size > 40, int(xs.size)
    proj = (xs.astype(np.float32) - gx) * dx + (ys.astype(np.float32) - gy) * dx
    lo, hi = float(proj.min()), float(proj.max())
    span = max(1.0, hi - lo)
    t = (proj - lo) / span
    near_idx = t <= 0.30
    far_idx = t >= 0.70
    assert int(near_idx.sum()) > 10 and int(far_idx.sum()) > 10, (
        int(near_idx.sum()), int(far_idx.sum()),
    )
    ny, nx = ys[near_idx], xs[near_idx]
    fy, fx = ys[far_idx], xs[far_idx]
    near0 = float(none[ny, nx].mean())
    near1 = float(faded[ny, nx].mean())
    far0 = float(none[fy, fx].mean())
    far1 = float(faded[fy, fx].mean())
    assert far0 > 12, far0
    assert far1 < far0 * 0.50, (far1, far0)
    assert abs(near1 - near0) / max(near0, 1.0) < 0.25, (near1, near0)


def test_t21_shadow_roi():
    """Shadow is not clipped: 1px ROI border is fully transparent."""
    _qapp()
    style = _new_shadow(angle_deg=45, distance_em=1.6, size_em=0.25, falloff=0)
    owned, view, lay = _paint(style, text="O", skip_blur=False)
    a = view[..., 3]
    assert int(a.max()) > 40, int(a.max())
    border = np.concatenate([
        a[0, :], a[-1, :], a[:, 0], a[:, -1],
    ])
    assert int(border.max()) == 0, int(border.max())
    off = _off_shadow()
    _o, v_off, lay_off = _paint(off, text="O", skip_blur=False)
    extra = int((_full(view, lay)[..., 3] > 16).sum()) - int(
        (_full(v_off, lay_off)[..., 3] > 16).sum()
    )
    assert extra > 80, extra


def test_t21_shadow_toggle():
    """Section header off matches no shadow. distance_em=0 is contact, not off."""
    _qapp()
    none = _off_shadow()
    off = _new_shadow(enabled=False, distance_em=0.5, strength=0.9)
    on = _new_shadow(enabled=True, distance_em=0.5, strength=0.9)
    _o0, v0, l0 = _paint(none, skip_blur=False)
    _o1, v1, l1 = _paint(off, skip_blur=False)
    _o2, v2, l2 = _paint(on, skip_blur=False)
    c0 = _full(v0, l0)
    c1 = _full(v1, l1)
    c2 = _full(v2, l2)
    assert c0.tobytes() == c1.tobytes()
    assert c2.tobytes() != c0.tobytes()

    panel = _panel()
    style = _new_shadow(distance_em=0.55, strength=0.8)
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    sw = getattr(panel, "sub_shadow_en", None)
    assert sw is not None
    sw.setChecked(True)
    got = panel.collect()
    assert (got["fx"]["shadow"]).get("enabled") is True
    assert float((got["fx"]["shadow"]).get("distance_em") or 0) > 0
    sw.setChecked(False)
    off_got = panel.collect()
    assert (off_got["fx"]["shadow"]).get("enabled") is False
    panel.close()


def test_t21_legacy_shadow_compat():
    """Old sigma_em/dx_em/dy_em projects stay byte-identical to the pre-Q21 raster."""
    _qapp()
    style = _base_fx({
        "sigma_em": 0.12,
        "dx_em": 0.08,
        "dy_em": 0.10,
        "color": "#141A26",
        "alpha": 0.55,
    })
    # Only the old keys — no light / angle / distance / size / falloff / strength.
    style["fx"]["shadow"] = {
        "sigma_em": 0.12,
        "dx_em": 0.08,
        "dy_em": 0.10,
        "color": "#141A26",
        "alpha": 0.55,
    }
    _owned, view, _lay = _paint(style, skip_blur=False)
    got = _md5(view)
    assert got == LEGACY_SHADOW_MD5, got


def test_t21_no_v1_shadow_keys():
    """collect() drops v1 shadow keys; dead widgets gone; 发光 / 投影 split."""
    from PySide6.QtWidgets import QLabel
    from cutora.ui.widgets.dock_panels import CollapsibleSection

    _qapp()
    panel = _panel()
    style = _new_shadow()
    style["shadow_blur"] = 8
    style["color_shadow"] = "#111111"
    style["opacity_shadow"] = 180
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    got = panel.collect()
    for key in V1_SHADOW_KEYS:
        assert key not in got, (key, got.keys())
        fx = got.get("fx") or {}
        assert key not in fx, (key, fx.keys())
        shadow = fx.get("shadow") or {}
        assert key not in shadow, (key, shadow)

    assert getattr(panel, "sub_blur", None) is None
    assert getattr(panel, "sub_c_shadow", None) is None
    assert getattr(panel, "sub_shadow_op", None) is None
    assert getattr(panel, "sub_blur_spin", None) is None
    assert getattr(panel, "fx_shadow_sigma_em", None) is None
    assert getattr(panel, "fx_shadow_dx_em", None) is None
    assert getattr(panel, "fx_shadow_dy_em", None) is None
    assert getattr(panel, "fx_shadow_alpha", None) is None
    assert getattr(panel, "fx_shadow_light", None) is not None
    assert getattr(panel, "fx_shadow_angle_deg", None) is not None
    assert getattr(panel, "fx_shadow_distance_em", None) is not None
    assert getattr(panel, "fx_shadow_size_em", None) is not None
    assert getattr(panel, "fx_shadow_falloff", None) is not None
    assert getattr(panel, "fx_shadow_color", None) is not None
    assert getattr(panel, "fx_shadow_strength", None) is not None
    assert getattr(panel, "sub_glow_en", None) is not None
    assert getattr(panel, "sub_shadow_en", None) is not None

    titles = [
        getattr(sec, "section_title", "")
        for sec in panel.findChildren(CollapsibleSection)
    ]
    assert "发光" in titles, titles
    assert "投影" in titles, titles
    assert "阴影 / 发光" not in titles, titles

    labels = []
    for sec in panel.findChildren(CollapsibleSection):
        if getattr(sec, "section_title", "") != "投影":
            continue
        for lab in sec.findChildren(QLabel):
            text = (lab.text() or "").strip()
            if text:
                labels.append(text)
    blob = " ".join(labels)
    assert "投影 σ" not in blob, labels
    assert "光源" in blob or "平行" in blob or "角度" in blob, labels
    panel.close()


def test_t21_shadow_section_live():
    """T18 pixel lock: every 投影 control must change export pixels."""
    from test_t18_no_dead_controls import (
        _base_style, _backdrop, _catalog, _merge_collect, _nudge, _panel,
        _qapp, _render, _install_color_mock,
    )
    _qapp()
    _install_color_mock()
    catalog = [row for row in _catalog() if row[1] == "投影"]
    assert catalog, "投影 section is empty"
    names = [row[0] for row in catalog]
    assert "sub_blur" not in names
    assert "sub_c_shadow" not in names
    assert "sub_shadow_op" not in names
    assert "sub_shadow_en" in names
    assert "fx_shadow_light" in names
    assert "fx_shadow_angle_deg" in names
    assert "fx_shadow_distance_em" in names
    assert "fx_shadow_size_em" in names
    assert "fx_shadow_falloff" in names
    assert "fx_shadow_color" in names
    assert "fx_shadow_strength" in names

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
    assert not dead, "投影 still dead: %s" % dead


def test_t21_glow_section_live():
    """Glow section stays live after the split; header toggle changes pixels."""
    from test_t18_no_dead_controls import (
        _base_style, _backdrop, _catalog, _merge_collect, _nudge, _panel,
        _qapp, _render, _install_color_mock,
    )
    _qapp()
    _install_color_mock()
    catalog = [row for row in _catalog() if row[1] == "发光"]
    assert catalog, "发光 section is empty"
    names = [row[0] for row in catalog]
    assert "sub_glow_en" in names
    assert "fx_glow_sigma_em" in names
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
    assert not dead, "发光 still dead: %s" % dead
