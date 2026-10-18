# -*- coding: utf-8 -*-
"""Q41: optical shadows — contact hardening, penumbra vs distance/size, point light."""
from __future__ import annotations

import copy
import hashlib
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
# Frozen from tools/_diag/t21_roi_before.py; _legacy path must not move.
LEGACY_SHADOW_MD5 = "45d67f11f5bb117e80dd663f4feba505"
GRID_PATH = os.path.join(REPO, "docs", "plan", "reports", "Q41_shadow_grid.png")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q41-shadow"])


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


def _shadow_alpha(on_style, off_style, text="I", skip_blur=False):
    _o_on, v_on, lay_on = _paint(on_style, text=text, skip_blur=skip_blur)
    _o_off, v_off, lay_off = _paint(off_style, text=text, skip_blur=skip_blur)
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
        "enabled": True,
        "light": "parallel",
        "angle_deg": 0.0,
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


def _never_shadow():
    style = _base_fx()
    style["fx"]["shadow"] = {"sigma_em": 0, "color": "#141A26", "alpha": 0.6}
    return style


def _col_edge_grad(alpha, x0, x1, y0, y1):
    """Max |d alpha / dy| over columns [x0, x1) inside the y band."""
    h, w = alpha.shape
    x0 = max(0, min(w - 1, int(x0)))
    x1 = max(x0 + 1, min(w, int(x1)))
    y0 = max(1, min(h - 2, int(y0)))
    y1 = max(y0 + 4, min(h - 1, int(y1)))
    band = alpha[y0:y1, x0:x1].astype(np.float32)
    assert float(band.max()) > 8, float(band.max())
    d = np.abs(np.diff(band, axis=0))
    return float(d.max())


def _row_edge_grad(alpha, y0, y1, x0, x1):
    h, w = alpha.shape
    y0 = max(0, min(h - 1, int(y0)))
    y1 = max(y0 + 1, min(h, int(y1)))
    x0 = max(1, min(w - 2, int(x0)))
    x1 = max(x0 + 4, min(w - 1, int(x1)))
    band = alpha[y0:y1, x0:x1].astype(np.float32)
    assert float(band.max()) > 8, float(band.max())
    d = np.abs(np.diff(band, axis=1))
    return float(d.max())


def _glyph_extent_x(glyph_a, gy, thresh=40):
    row = np.where(glyph_a[int(round(gy)), :] >= thresh)[0]
    assert row.size > 4, int(row.size)
    return int(row.min()), int(row.max())


def _shadow_span_x(alpha, thresh=12):
    ys, xs = np.where(alpha >= thresh)
    assert xs.size > 30, int(xs.size)
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


def test_q41_knobs_live_from_default():
    """Section toggle on writes visible defaults; size_em alone must change pixels."""
    _qapp()
    panel = _panel()
    style = _never_shadow()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    sw = getattr(panel, "sub_shadow_en", None)
    assert sw is not None
    sw.setChecked(True)
    got = panel.collect()
    sh = got["fx"]["shadow"]
    dist = float(sh.get("distance_em") or 0)
    size = float(sh.get("size_em") or 0)
    strength = float(sh.get("strength") or 0)
    assert abs(dist - 0.18) < 0.02, dist
    assert abs(size - 0.10) < 0.02, size
    assert abs(strength - 0.6) < 0.05, strength
    _o0, v0, l0 = _paint(got, text="I", skip_blur=False)
    a0 = _full(v0, l0)
    size_spin = getattr(panel, "fx_shadow_size_em", None)
    assert size_spin is not None
    size_spin.setValue(0.40)
    got2 = panel.collect()
    sh2 = got2["fx"]["shadow"]
    assert abs(float(sh2.get("distance_em") or 0) - dist) < 0.02, sh2
    assert abs(float(sh2.get("size_em") or 0) - 0.40) < 0.02, sh2
    _o1, v1, l1 = _paint(got2, text="I", skip_blur=False)
    a1 = _full(v1, l1)
    panel.close()
    assert a0.tobytes() != a1.tobytes()


def test_q41_contact_hardening():
    """Near-glyph shadow edge is ≥ 1.8× sharper than the tip (contact hardening)."""
    _qapp()
    off = _off_shadow()
    on = _new_shadow(
        angle_deg=0.0, distance_em=1.1, size_em=0.28, falloff=0.0, strength=0.95,
    )
    alpha, glyph_a, lay, _v = _shadow_alpha(on, off, text="I")
    gx, gy = _centroid(glyph_a >= 40)
    _g0, g1 = _glyph_extent_x(glyph_a, gy)
    s0, s1, y0, y1 = _shadow_span_x(alpha)
    # Rightward light: measure top/bottom silhouette sharpness near the glyph vs at the tip.
    root = g1
    tip = s1
    length = max(8.0, float(tip - root))
    near0 = root + 0.12 * length
    near1 = root + 0.32 * length
    far0 = root + 0.72 * length
    far1 = root + 0.94 * length
    near = _col_edge_grad(alpha, near0, near1, y0, y1)
    far = _col_edge_grad(alpha, far0, far1, y0, y1)
    assert near > 4, near
    assert near >= far * 1.8, (near, far, length, root, tip)


def test_q41_penumbra_scales_with_distance():
    """Fixed size_em, distance 0.15 → 0.6: tip edge gradient drops > 35%."""
    _qapp()
    off = _off_shadow()
    close = _new_shadow(
        angle_deg=0.0, distance_em=0.15, size_em=0.18, falloff=0, strength=0.95,
    )
    far = _new_shadow(
        angle_deg=0.0, distance_em=0.6, size_em=0.18, falloff=0, strength=0.95,
    )
    a_close, g_close, lay_c, _v = _shadow_alpha(close, off, text="I")
    a_far, g_far, lay_f, _v2 = _shadow_alpha(far, off, text="I")

    def _tip_grad(alpha, glyph):
        gx, gy = _centroid(glyph >= 40)
        _g0, g1 = _glyph_extent_x(glyph, gy)
        s0, s1, y0, y1 = _shadow_span_x(alpha)
        length = max(8.0, float(s1 - g1))
        far0 = g1 + 0.72 * length
        far1 = g1 + 0.94 * length
        return _col_edge_grad(alpha, far0, far1, y0, y1)

    g_short = _tip_grad(a_close, g_close)
    g_long = _tip_grad(a_far, g_far)
    assert g_short > 4, g_short
    assert g_long < g_short * 0.65, (g_long, g_short)


def test_q41_penumbra_scales_with_size():
    """Fixed distance_em, size 0.05 → 0.4: tip edge gradient drops > 35%."""
    _qapp()
    off = _off_shadow()
    hard = _new_shadow(
        angle_deg=0.0, distance_em=0.7, size_em=0.05, falloff=0, strength=0.95,
    )
    soft = _new_shadow(
        angle_deg=0.0, distance_em=0.7, size_em=0.4, falloff=0, strength=0.95,
    )
    a_hard, g_hard, lay, _v = _shadow_alpha(hard, off, text="I")
    a_soft, g_soft, _l2, _v2 = _shadow_alpha(soft, off, text="I")

    def _tip_grad(alpha, glyph):
        gx, gy = _centroid(glyph >= 40)
        _g0, g1 = _glyph_extent_x(glyph, gy)
        s0, s1, y0, y1 = _shadow_span_x(alpha)
        length = max(8.0, float(s1 - g1))
        far0 = g1 + 0.72 * length
        far1 = g1 + 0.94 * length
        return _col_edge_grad(alpha, far0, far1, y0, y1)

    d_hard = _tip_grad(a_hard, g_hard)
    d_soft = _tip_grad(a_soft, g_soft)
    assert d_hard > 4, d_hard
    assert d_soft < d_hard * 0.65, (d_soft, d_hard)


def test_q41_point_diverges():
    """Point light: bbox grows as light moves closer; tip wider than root."""
    _qapp()
    off = _off_shadow()
    far = _new_shadow(
        light="point", angle_deg=0.0, distance_em=2.0, size_em=0.08,
        falloff=0, strength=0.95,
    )
    near = _new_shadow(
        light="point", angle_deg=0.0, distance_em=0.5, size_em=0.08,
        falloff=0, strength=0.95,
    )
    a_far, _g, _l, _v = _shadow_alpha(far, off, text="I")
    a_near, glyph, lay, _v2 = _shadow_alpha(near, off, text="I")

    def _area(a):
        ys, xs = np.where(a >= 16)
        assert xs.size > 30, int(xs.size)
        return int(xs.max() - xs.min() + 1) * int(ys.max() - ys.min() + 1)

    assert _area(a_near) > _area(a_far), (_area(a_near), _area(a_far))

    gx, gy = _centroid(glyph >= 40)
    _g0, g1 = _glyph_extent_x(glyph, gy)
    s0, s1, y0, y1 = _shadow_span_x(a_near, thresh=16)
    length = max(8.0, float(s1 - g1))
    root_x = int(round(g1 + 0.18 * length))
    tip_x = int(round(g1 + 0.88 * length))
    root_x = max(0, min(CANVAS_W - 1, root_x))
    tip_x = max(0, min(CANVAS_W - 1, tip_x))

    def _height(col):
        ys = np.where(a_near[:, col] >= 16)[0]
        assert ys.size > 4, (col, int(ys.size))
        return int(ys.max() - ys.min() + 1)

    h_root = _height(root_x)
    h_tip = _height(tip_x)
    assert h_tip > h_root, (h_tip, h_root, root_x, tip_x, length)


def test_q41_point_intensity():
    """Point light distance 0.6 → 0.2: peak shadow alpha rises > 20%."""
    _qapp()
    off = _off_shadow()
    far = _new_shadow(
        light="point", angle_deg=0.0, distance_em=0.6, size_em=0.10,
        falloff=0, strength=0.85,
    )
    near = _new_shadow(
        light="point", angle_deg=0.0, distance_em=0.2, size_em=0.10,
        falloff=0, strength=0.85,
    )
    a_far, _g, _l, _v = _shadow_alpha(far, off, text="I")
    a_near, _g2, _l2, _v2 = _shadow_alpha(near, off, text="I")
    p_far = float(a_far.max())
    p_near = float(a_near.max())
    assert p_far > 8, p_far
    assert p_near > p_far * 1.20, (p_near, p_far)


def test_q41_angle():
    """0/90/180/270: shadow centroid falls in that quadrant relative to the glyph."""
    _qapp()
    off = _off_shadow()
    glyph_ref = None
    cents = {}
    for ang in (0.0, 90.0, 180.0, 270.0):
        a, glyph, _l, _v = _shadow_alpha(
            _new_shadow(
                angle_deg=ang, distance_em=0.7, size_em=0.08, falloff=0,
            ),
            off,
            text="O",
        )
        if glyph_ref is None:
            glyph_ref = glyph
        cents[ang] = _centroid(a >= 20)
    gx, gy = _centroid(glyph_ref >= 40)
    x0, y0 = cents[0.0]
    x90, y90 = cents[90.0]
    x180, y180 = cents[180.0]
    x270, y270 = cents[270.0]
    assert x0 > gx, (x0, gx)
    assert abs(y0 - gy) < abs(x0 - gx), (x0, y0, gx, gy)
    assert y90 > gy, (y90, gy)
    assert abs(x90 - gx) < abs(y90 - gy), (x90, y90, gx, gy)
    assert x180 < gx, (x180, gx)
    assert abs(y180 - gy) < abs(gx - x180), (x180, y180, gx, gy)
    assert y270 < gy, (y270, gy)
    assert abs(x270 - gx) < abs(gy - y270), (x270, y270, gx, gy)


def test_q41_toggle_off_is_noop():
    """Section header off matches a style that never had projection, byte-for-byte."""
    _qapp()
    never = _never_shadow()
    panel = _panel()
    panel.load_data(copy.deepcopy(never))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(never)
    sw = getattr(panel, "sub_shadow_en", None)
    assert sw is not None
    sw.setChecked(True)
    on_got = panel.collect()
    sw.setChecked(False)
    off_got = panel.collect()
    panel.close()
    _o0, v0, l0 = _paint(never, text="HELLO", skip_blur=False)
    _o1, v1, l1 = _paint(off_got, text="HELLO", skip_blur=False)
    _o2, v2, l2 = _paint(on_got, text="HELLO", skip_blur=False)
    c0 = _full(v0, l0)
    c1 = _full(v1, l1)
    c2 = _full(v2, l2)
    assert c0.tobytes() == c1.tobytes()
    assert c2.tobytes() != c0.tobytes()


def test_q41_legacy_byte_identical():
    """Old sigma_em/dx_em/dy_em projects stay byte-identical (do not touch _legacy)."""
    _qapp()
    style = _base_fx()
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


def test_q41_shadow_grid_png():
    """5×5 size_em × distance_em contact sheet for Luca."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    _qapp()
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()

    sizes = (0.04, 0.10, 0.18, 0.30, 0.50)
    dists = (0.08, 0.18, 0.35, 0.60, 1.00)
    cell_w, cell_h = 160, 200
    label_h, label_w = 28, 36
    cols, rows = len(sizes), len(dists)
    img_w = label_w + cols * cell_w
    img_h = label_h + rows * cell_h
    sheet = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#EDE6D6"))
    painter = QPainter(sheet)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Arimo", 10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#3A3228")))
        for c, size in enumerate(sizes):
            painter.drawText(
                QRectF(label_w + c * cell_w, 0, cell_w, label_h),
                int(Qt.AlignmentFlag.AlignCenter),
                "size %.2f" % size,
            )
        for r, dist in enumerate(dists):
            painter.drawText(
                QRectF(0, label_h + r * cell_h, label_w, cell_h),
                int(Qt.AlignmentFlag.AlignCenter),
                "d %.2f" % dist,
            )
        cream = np.array([214, 230, 237, 255], dtype=np.uint8)  # BGRA of #EDE6D6
        for r, dist in enumerate(dists):
            for c, size in enumerate(sizes):
                style = _new_shadow(
                    angle_deg=90.0, distance_em=dist, size_em=size,
                    falloff=0.15, strength=0.85,
                )
                style["fontsize"] = 56
                style["pos_x"] = cell_w // 2
                style["pos_y"] = 70
                _owned, view, lay = _paint(style, text="H", skip_blur=False)
                cell = np.zeros((cell_h, cell_w, 4), dtype=np.uint8)
                cell[:] = cream
                ox = int(lay.origin[0])
                oy = int(lay.origin[1])
                vh, vw = view.shape[:2]
                x0 = max(0, -ox)
                y0 = max(0, -oy)
                dx = max(0, ox)
                dy = max(0, oy)
                ww = min(cell_w - dx, vw - x0)
                hh = min(cell_h - dy, vh - y0)
                if ww > 0 and hh > 0:
                    src = view[y0:y0 + hh, x0:x0 + ww].astype(np.float32)
                    dst = cell[dy:dy + hh, dx:dx + ww].astype(np.float32)
                    a = src[..., 3:4] / 255.0
                    # src is Format_ARGB32_Premultiplied: RGB already * alpha.
                    out = src + dst * (1.0 - a)
                    cell[dy:dy + hh, dx:dx + ww] = np.clip(out, 0, 255).astype(
                        np.uint8)
                qcell = QImage(
                    cell.data, cell_w, cell_h, cell.strides[0],
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                painter.drawImage(label_w + c * cell_w, label_h + r * cell_h, qcell)
    finally:
        painter.end()
    os.makedirs(os.path.dirname(GRID_PATH), exist_ok=True)
    assert sheet.save(GRID_PATH), GRID_PATH
    assert os.path.isfile(GRID_PATH), GRID_PATH
    assert os.path.getsize(GRID_PATH) > 8000


def _outer_ring_mean(shadow_a, glyph_a, ksize=11):
    import cv2

    glyph_bin = (glyph_a >= 40).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    dil = cv2.dilate(glyph_bin, k)
    ring = (dil > 0) & (glyph_bin == 0)
    assert int(ring.sum()) > 20, int(ring.sum())
    return float(shadow_a[ring].mean())


def test_q41_contact_shadow_at_zero_distance():
    """distance_em=0: size_em 0.05→0.4 changes pixels; a ring around the glyph is visible.

    Export (skip_blur=False) and degraded preview (skip_blur=True) both live.
    """
    _qapp()
    off = _off_shadow()
    small = _new_shadow(
        angle_deg=0.0, distance_em=0.0, size_em=0.05, falloff=0.0, strength=0.95,
    )
    large = _new_shadow(
        angle_deg=0.0, distance_em=0.0, size_em=0.40, falloff=0.0, strength=0.95,
    )
    for skip in (False, True):
        a_small, glyph, _l, _v = _shadow_alpha(
            small, off, text="I", skip_blur=skip)
        a_large, _g2, _l2, _v2 = _shadow_alpha(
            large, off, text="I", skip_blur=skip)
        mad = float(np.abs(
            a_small.astype(np.int16) - a_large.astype(np.int16)).mean())
        assert a_small.tobytes() != a_large.tobytes(), skip
        assert mad > 0.4, (skip, mad)
        ring = _outer_ring_mean(a_large, glyph, ksize=11)
        assert ring > 12.0, (skip, ring)


def test_q41_size_live_when_degraded():
    """skip_blur=True (degraded preview): size_em alone must change pixels."""
    _qapp()
    off = _off_shadow()
    hard = _new_shadow(
        angle_deg=0.0, distance_em=0.18, size_em=0.05, falloff=0.0, strength=0.95,
    )
    soft = _new_shadow(
        angle_deg=0.0, distance_em=0.18, size_em=0.40, falloff=0.0, strength=0.95,
    )
    a_hard, _g, _l, _v = _shadow_alpha(hard, off, text="I", skip_blur=True)
    a_soft, _g2, _l2, _v2 = _shadow_alpha(soft, off, text="I", skip_blur=True)
    delta = np.abs(a_hard.astype(np.int16) - a_soft.astype(np.int16))
    changed = int((delta > 8).sum())
    assert a_hard.tobytes() != a_soft.tobytes()
    assert changed > 200, (changed, float(delta.mean()), float(delta.max()))
    assert float(delta.max()) > 20, float(delta.max())
    assert float(a_hard.max()) > 8, float(a_hard.max())
    assert float(a_soft.max()) > 8, float(a_soft.max())


def test_q41_falloff_is_gradient():
    """distance_em=0.18 (section default, not 1.1): falloff is a real along-ray gradient.

    Wide glyph so half-width > dist_px; a centroid-origin span=dist_px
    puts the whole downwind umbra at t=1 and this test goes red.
    """
    _qapp()
    off = _off_shadow()
    on = _new_shadow(
        angle_deg=0.0, distance_em=0.18, size_em=0.08, falloff=1.0, strength=0.95,
    )
    for skip in (False, True):
        alpha, glyph, _l, _v = _shadow_alpha(on, off, text="H", skip_blur=skip)
        gx, gy = _centroid(glyph >= 40)
        _g0, g1 = _glyph_extent_x(glyph, gy)
        s0, s1, y0, y1 = _shadow_span_x(alpha, thresh=8)
        assert s1 > g1 + 4, (skip, g1, s1, s0, y0, y1)
        length = max(8.0, float(s1 - g1))
        bands = []
        for lo_f, hi_f in ((0.06, 0.28), (0.40, 0.62), (0.74, 0.96)):
            x0 = int(round(g1 + lo_f * length))
            x1 = int(round(g1 + hi_f * length))
            x0 = max(0, min(CANVAS_W - 2, x0))
            x1 = max(x0 + 1, min(CANVAS_W, x1))
            band = alpha[max(0, y0):min(CANVAS_H, y1 + 1), x0:x1]
            live = band[band >= 4]
            assert live.size > 8, (skip, lo_f, hi_f, int(live.size), length)
            bands.append(float(live.mean()))
        m0, m1, m2 = bands
        assert m0 >= m1 >= m2 or (m0 > m2 and m1 <= m0), (skip, bands, length)
        assert m0 - m2 > 18.0, (skip, bands, length)


def test_q41_point_no_saturation():
    """Point light at distance_em=0.2 must not clip the penumbra to a solid block."""
    _qapp()
    off = _off_shadow()
    on = _new_shadow(
        light="point", angle_deg=0.0, distance_em=0.2, size_em=0.10,
        falloff=0.0, strength=0.85,
    )
    for skip in (False, True):
        alpha, _g, _l, _v = _shadow_alpha(on, off, text="I", skip_blur=skip)
        vals = alpha[alpha >= 8]
        assert vals.size > 80, (skip, int(vals.size))
        sat = float((vals >= 254).sum()) / float(vals.size)
        assert sat < 0.20, (skip, sat, float(vals.max()), int(vals.size))
        mid = int(((vals >= 40) & (vals <= 210)).sum())
        assert mid > 80, (skip, mid, sat)


def test_q41_point_roi():
    """Point light distance_em=0.2: 1px ROI border is fully transparent."""
    _qapp()
    style = _new_shadow(
        light="point", angle_deg=0.0, distance_em=0.2, size_em=0.10,
        falloff=0.0, strength=0.90,
    )
    style["fontsize"] = 48
    for skip in (False, True):
        owned, view, _lay = _paint(style, text="I", skip_blur=skip)
        a = view[..., 3]
        assert int(a.max()) > 40, (skip, int(a.max()), view.shape)
        border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
        assert int(border.max()) == 0, (skip, int(border.max()), view.shape)


def test_q41_legacy_roundtrip_byte_identical():
    """Old sigma/dx/dy project: direct paint stays on _legacy; panel collect promotes."""
    _qapp()
    style = _base_fx()
    style["fx"]["shadow"] = {
        "sigma_em": 0.12,
        "dx_em": 0.08,
        "dy_em": 0.10,
        "color": "#141A26",
        "alpha": 0.55,
    }
    _o0, v0, l0 = _paint(style, skip_blur=False)
    panel = _panel()
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    got = panel.collect()
    panel.close()
    sh = (got.get("fx") or {}).get("shadow") or {}
    # Q52: collect drops retired keys so the new model drives subsequent paint.
    for retired in ("sigma_em", "dx_em", "dy_em", "alpha"):
        assert retired not in sh, (retired, sh)
    assert "size_em" in sh and "distance_em" in sh, sh
    _o1, v1, l1 = _paint(got, skip_blur=False)
    assert int(v1[..., 3].max()) > 40, int(v1[..., 3].max())
    assert _md5(v0) == LEGACY_SHADOW_MD5


def test_q41_degrade_threshold_above_point_light():
    """20.4 ms/frame (measured point-light export) must not trip preview degrade."""
    from cutora.render.textfx import pipeline as pipe

    pipe.reset_degrade()
    try:
        for _ in range(10):
            pipe._note_slow(20.4, True)
        assert pipe._degraded is False
        pipe.reset_degrade()
        for _ in range(10):
            pipe._note_slow(30.0, True)
        assert pipe._degraded is True
    finally:
        pipe.reset_degrade()

