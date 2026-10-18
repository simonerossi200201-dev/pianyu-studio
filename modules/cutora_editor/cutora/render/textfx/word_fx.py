# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-word colour overlays and per-unit warps. Glyph raster stays static."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF,
)

from cutora.render.anim_engine import ease_out_cubic, spring_interp
from cutora.render.textfx.spec import (
    DEFAULT_EMPH,
    emphasis_colors, emphasis_is_gradient,
    follow_box_color, follow_tint_angle, follow_tint_colors,
    follow_tint_is_gradient, rgba_tuple, rgb_tuple,
)

WORD_COLOR = frozenset({
    "karaoke", "highlight", "box_highlight",
    "colour_highlight", "rotate_highlight",
})
UNIT_WARP = frozenset({
    "flip_clock", "rotate_flip", "stack",
    "colour_highlight", "rotate_highlight",
    "bullet_reveal",
})
HIGHLIGHT_FADE_S = 0.080
BOX_MOVE_S = 0.25
COLOUR_BOUNCE_S = 0.200
COLOUR_PEAK = 1.15
ROT_HL_S = 0.120
ROT_HL_DEG = 6.0


def _runs(layout):
    out = []
    for line in layout.lines:
        for run in line.runs:
            if str(run.text or "").strip():
                out.append(run)
    return out


def current_run(layout, t: float):
    runs = _runs(layout)
    if not runs:
        return None, 0.0, -1
    for i, run in enumerate(runs):
        dur = float(run.t_end) - float(run.t_start)
        if dur > 1e-6 and run.t_start <= t < run.t_end:
            return run, (t - run.t_start) / dur, i
    past = [i for i, r in enumerate(runs) if t >= r.t_start]
    if past:
        i = past[-1]
        return runs[i], 1.0, i
    return runs[0], 0.0, 0


def recolor_premul(img: QImage, hex_color: str, amount: float = 1.0) -> QImage:
    from cutora.render.textfx.raster import as_bgra
    amt = max(0.0, min(1.0, float(amount)))
    if amt <= 1e-6:
        return img
    owned, bgra = as_bgra(img)
    r, g, b = rgb_tuple(hex_color)
    a = bgra[..., 3].astype("float32") * (1.0 / 255.0)
    inv = 1.0 - amt
    bgra[..., 0] = (bgra[..., 0].astype("float32") * inv + b * a * 255.0 * amt).clip(0, 255).astype("uint8")
    bgra[..., 1] = (bgra[..., 1].astype("float32") * inv + g * a * 255.0 * amt).clip(0, 255).astype("uint8")
    bgra[..., 2] = (bgra[..., 2].astype("float32") * inv + r * a * 255.0 * amt).clip(0, 255).astype("uint8")
    return owned


def _run_rect(layout, run, pad: float = 1.0) -> tuple[int, int, int, int]:
    ox, oy = layout.origin
    x0 = int(math_floor(run.x - ox - pad))
    y0 = int(math_floor(run.y - oy - pad))
    x1 = int(math_ceil(run.x + run.w - ox + pad))
    y1 = int(math_ceil(run.y + run.h - oy + pad))
    return x0, y0, x1, y1


def math_floor(v):
    import math
    return math.floor(v)


def math_ceil(v):
    import math
    return math.ceil(v)


def tint_run_palette(img: QImage, layout, run, colors, amount: float = 1.0) -> QImage:
    """LTR 2–3 stop recolor of one run. Post-cache; does not rebuild glyphs."""
    cols = [str(c) for c in (colors or []) if c]
    if len(cols) <= 1:
        return tint_run_ltr(
            img, layout, run, cols[0] if cols else "#22D3EE", 1.0, amount)
    from cutora.render.textfx.raster import as_bgra
    amt = max(0.0, min(1.0, float(amount)))
    if amt <= 1e-6:
        return img
    x0, y0, x1, y1 = _run_rect(layout, run, 2.0)
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return owned
    sl = bgra[y0:y1, x0:x1]
    rgb = [rgb_tuple(c) for c in cols[:3]]
    n = len(rgb)
    width = sl.shape[1]
    xs = (np.arange(width, dtype="float32") + 0.5) / float(max(1, width))
    stops = [i / float(n - 1) for i in range(n)]
    mix = np.zeros((width, 3), dtype="float32")
    for ch in range(3):
        mix[:, ch] = np.interp(xs, stops, [c[ch] for c in rgb])
    a = sl[..., 3].astype("float32") * (1.0 / 255.0)
    inv = 1.0 - amt
    # mix is 0–255 RGB; premul target is mix * alpha, not mix * alpha * 255.
    sl[..., 0] = (sl[..., 0].astype("float32") * inv + mix[None, :, 2] * a * amt).clip(0, 255).astype("uint8")
    sl[..., 1] = (sl[..., 1].astype("float32") * inv + mix[None, :, 1] * a * amt).clip(0, 255).astype("uint8")
    sl[..., 2] = (sl[..., 2].astype("float32") * inv + mix[None, :, 0] * a * amt).clip(0, 255).astype("uint8")
    return owned


def _blend_premul_rgb(sl, r: int, g: int, b: int, amount: float) -> None:
    """Replace premul RGB with (b,g,r)*alpha. *r/g/b* are 0–255.

    Unique definition: never multiply by 255 a second time (that clips
    every channel ≥1 to 255). karaoke and highlight share this.
    """
    amt = max(0.0, min(1.0, float(amount)))
    a = sl[..., 3].astype("float32") * (1.0 / 255.0)
    inv = 1.0 - amt
    sl[..., 0] = (sl[..., 0].astype("float32") * inv + float(b) * a * amt).clip(0, 255).astype("uint8")
    sl[..., 1] = (sl[..., 1].astype("float32") * inv + float(g) * a * amt).clip(0, 255).astype("uint8")
    sl[..., 2] = (sl[..., 2].astype("float32") * inv + float(r) * a * amt).clip(0, 255).astype("uint8")


def tint_run_ltr(img: QImage, layout, run, color: str, frac: float, amount: float = 1.0,
                 colors=None, angle: float = 0.0) -> QImage:
    from cutora.render.textfx.raster import as_bgra
    frac = max(0.0, min(1.0, float(frac)))
    if frac <= 1e-6 or amount <= 1e-6:
        return img
    x0, y0, x1, y1 = _run_rect(layout, run, 2.0)
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return owned
    cut = x0 + int(round((x1 - x0) * frac))
    cols = [str(c) for c in (colors or []) if c]
    if len(cols) >= 2:
        sl_full = bgra[y0:y1, x0:x1]
        if sl_full.size == 0:
            return owned
        rgb = [rgb_tuple(c) for c in cols[:5]]
        n = len(rgb)
        hh, ww = sl_full.shape[:2]
        rad = math.radians(float(angle))
        dx, dy = math.cos(rad), math.sin(rad)
        ys = np.arange(hh, dtype="float32")[:, None] + 0.5
        xs = np.arange(ww, dtype="float32")[None, :] + 0.5
        dots = xs * dx + ys * dy
        t0 = float(dots.min())
        t1 = float(dots.max())
        span = max(t1 - t0, 1e-6)
        u = ((dots - t0) / span).reshape(-1)
        stops = [i / float(n - 1) for i in range(n)]
        mix = np.empty((hh, ww, 3), dtype="float32")
        for ch in range(3):
            mix[..., ch] = np.interp(u, stops, [c[ch] for c in rgb]).reshape(hh, ww)
        cut_i = max(0, min(ww, cut - x0))
        if cut_i <= 0:
            return owned
        sl = sl_full[:, :cut_i]
        mix_sl = mix[:, :cut_i]
        amt = max(0.0, min(1.0, float(amount)))
        a = sl[..., 3].astype("float32") * (1.0 / 255.0)
        inv = 1.0 - amt
        sl[..., 0] = (
            sl[..., 0].astype("float32") * inv + mix_sl[..., 2] * a * amt
        ).clip(0, 255).astype("uint8")
        sl[..., 1] = (
            sl[..., 1].astype("float32") * inv + mix_sl[..., 1] * a * amt
        ).clip(0, 255).astype("uint8")
        sl[..., 2] = (
            sl[..., 2].astype("float32") * inv + mix_sl[..., 0] * a * amt
        ).clip(0, 255).astype("uint8")
        return owned
    sl = bgra[y0:y1, x0:cut]
    if sl.size == 0:
        return owned
    r, g, b = rgb_tuple(color)
    _blend_premul_rgb(sl, r, g, b, amount)
    return owned


def tint_run_solid(img: QImage, layout, run, color: str, amount: float = 1.0) -> QImage:
    """Whole-run premul recolor. Used by the current-word highlight loop."""
    from cutora.render.textfx.raster import as_bgra
    amt = max(0.0, min(1.0, float(amount)))
    if amt <= 1e-6:
        return img
    x0, y0, x1, y1 = _run_rect(layout, run, 2.0)
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return owned
    sl = bgra[y0:y1, x0:x1]
    if sl.size == 0:
        return owned
    r, g, b = rgb_tuple(color)
    _blend_premul_rgb(sl, r, g, b, amt)
    return owned


def tint_run_gradient(img: QImage, layout, run, colors, angle: float = 0.0,
                      amount: float = 1.0) -> QImage:
    """Premul recolor of one run along *angle* (0° left→right, 90° top→bottom)."""
    cols = [str(c) for c in (colors or []) if c]
    if len(cols) <= 1:
        return tint_run_solid(
            img, layout, run, cols[0] if cols else "#22D3EE", amount)
    from cutora.render.textfx.raster import as_bgra
    amt = max(0.0, min(1.0, float(amount)))
    if amt <= 1e-6:
        return img
    x0, y0, x1, y1 = _run_rect(layout, run, 2.0)
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return owned
    sl = bgra[y0:y1, x0:x1]
    if sl.size == 0:
        return owned
    rgb = [rgb_tuple(c) for c in cols[:5]]
    n = len(rgb)
    hh, ww = sl.shape[:2]
    rad = math.radians(float(angle))
    dx, dy = math.cos(rad), math.sin(rad)
    ys = np.arange(hh, dtype="float32")[:, None] + 0.5
    xs = np.arange(ww, dtype="float32")[None, :] + 0.5
    dots = xs * dx + ys * dy
    t0 = float(dots.min())
    t1 = float(dots.max())
    span = max(t1 - t0, 1e-6)
    u = ((dots - t0) / span).reshape(-1)
    stops = [i / float(n - 1) for i in range(n)]
    mix = np.empty((hh, ww, 3), dtype="float32")
    for ch in range(3):
        mix[..., ch] = np.interp(u, stops, [c[ch] for c in rgb]).reshape(hh, ww)
    a = sl[..., 3].astype("float32") * (1.0 / 255.0)
    inv = 1.0 - amt
    sl[..., 0] = (
        sl[..., 0].astype("float32") * inv + mix[..., 2] * a * amt
    ).clip(0, 255).astype("uint8")
    sl[..., 1] = (
        sl[..., 1].astype("float32") * inv + mix[..., 1] * a * amt
    ).clip(0, 255).astype("uint8")
    sl[..., 2] = (
        sl[..., 2].astype("float32") * inv + mix[..., 0] * a * amt
    ).clip(0, 255).astype("uint8")
    return owned


def tint_run_emphasis(img: QImage, layout, run, spec, amount: float = 1.0) -> QImage:
    """Solid or gradient tint from spec.emphasis for the current word."""
    if emphasis_is_gradient(spec):
        return tint_run_gradient(
            img, layout, run, emphasis_colors(spec),
            float(spec.emphasis.angle), amount)
    color = spec.emphasis.color or "#22D3EE"
    return tint_run_solid(img, layout, run, color, amount)


def _tint_run_follow(img, layout, run, spec, amount: float):
    """Current-word glyph color: follow_word.tint → product default."""
    if follow_tint_is_gradient(spec):
        return tint_run_gradient(
            img, layout, run, follow_tint_colors(spec),
            follow_tint_angle(spec), amount)
    cols = follow_tint_colors(spec)
    color = cols[0] if cols else DEFAULT_EMPH
    return tint_run_solid(img, layout, run, color, amount)


def apply_word_color(img: QImage | None, layout, spec, t: float, event: dict):
    if img is None:
        return None
    from cutora.render.textfx.anim import _anim_id, _anim_ids
    ids = _anim_ids(spec.anim.loop)
    aid = next((i for i in ids if i in WORD_COLOR and i != "box_highlight"), "")
    if not aid and "box_highlight" in ids:
        # Old follow_word only appended box_highlight; still tint unless
        # the user turned 换字色 off.
        fw = getattr(spec, "follow_word", None)
        tint = getattr(fw, "tint", None) if fw is not None else None
        if getattr(tint, "enabled", getattr(spec, "follow_word_tint", True)):
            aid = "box_highlight"
    if not aid and _anim_id(spec.anim.in_name) in WORD_COLOR:
        aid = _anim_id(spec.anim.in_name)
    if not aid:
        return img
    run, local_u, idx = current_run(layout, t)
    if run is None:
        return img
    fw = getattr(spec, "follow_word", None)
    tint_on = bool(getattr(getattr(fw, "tint", None), "enabled", False))
    # Keyword fill_override stays when 当前词字色 is off (g10 karaoke+list).
    # When 当前词字色 is on, the current word wins even if it is a keyword.
    if bool(run.emphasized) and not tint_on:
        return img
    img = img.copy()
    if aid == "karaoke":
        cols = follow_tint_colors(spec)
        color = cols[0] if cols else DEFAULT_EMPH
        if follow_tint_is_gradient(spec):
            return tint_run_ltr(
                img, layout, run, color, local_u, 1.0,
                colors=cols, angle=follow_tint_angle(spec))
        return tint_run_ltr(img, layout, run, color, local_u, 1.0)
    if aid in ("highlight", "box_highlight"):
        # Current word wins over keyword emphasis: recolor even if
        # layout already painted fill_override for an emphasized run.
        fade = min(1.0, max(0.0, (t - float(run.t_start)) / HIGHLIGHT_FADE_S))
        return _tint_run_follow(img, layout, run, spec, fade)
    if aid == "colour_highlight":
        pal = spec.emphasis.palette or []
        if pal:
            return tint_run_palette(img, layout, run, pal, 1.0)
        return tint_run_emphasis(img, layout, run, spec, 1.0)
    if aid == "rotate_highlight":
        fade = min(1.0, max(0.0, (t - float(run.t_start)) / ROT_HL_S))
        return tint_run_emphasis(img, layout, run, spec, fade)
    return img


def draw_box_highlight(bg: QImage | None, layout, spec, t: float, event: dict,
                       style=None) -> QImage | None:
    """Rounded box under the current word. Reuses bg radius + apply_edge_fade."""
    from cutora.render.textfx.anim import _anim_id, _anim_ids
    from cutora.render.textfx.fade import apply_edge_fade
    from cutora.render.textfx.raster import _qcolor, _round_rect_path, as_bgra
    loop_ids = _anim_ids(spec.anim.loop)
    if "box_highlight" not in loop_ids:
        return bg
    runs = _runs(layout)
    run, local_u, idx = current_run(layout, t)
    if run is None:
        return bg
    prev = runs[idx - 1] if idx > 0 else run
    dt = max(0.0, t - float(run.t_start))
    p = spring_interp(min(1.0, dt / BOX_MOVE_S))
    ox, oy = layout.origin
    pad = 0.12 * layout.em
    def _r(rn):
        return QRectF(rn.x - ox - pad, rn.y - oy - pad, rn.w + 2 * pad, rn.h + 2 * pad)
    a, b = _r(prev), _r(run)
    rect = QRectF(
        a.x() + (b.x() - a.x()) * p,
        a.y() + (b.y() - a.y()) * p,
        a.width() + (b.width() - a.width()) * p,
        a.height() + (b.height() - a.height()) * p,
    )
    if bg is None:
        bg = QImage(layout.roi_w, layout.roi_h, QImage.Format.Format_ARGB32_Premultiplied)
        bg.fill(0)
    else:
        bg = bg.copy()
    fw_box = getattr(getattr(spec, "follow_word", None), "box", None)
    bh = fw_box if fw_box is not None else getattr(spec, "box_highlight", None)
    color = follow_box_color(spec)
    if fw_box is not None:
        alpha = float(fw_box.alpha)
        radius = float(fw_box.radius_em) * layout.em
        pad = float(fw_box.pad_em) * layout.em
        a, b = _r(prev), _r(run)
        rect = QRectF(
            a.x() + (b.x() - a.x()) * p,
            a.y() + (b.y() - a.y()) * p,
            a.width() + (b.width() - a.width()) * p,
            a.height() + (b.height() - a.height()) * p,
        )
    else:
        alpha = 0.55
        radius = spec.bg.radius_em * layout.em
        if bh is not None and bh.alpha is not None:
            alpha = float(bh.alpha)
        if bh is not None and bh.radius_em is not None:
            radius = float(bh.radius_em) * layout.em
        if bh is not None and bh.pad_em is not None:
            pad = float(bh.pad_em) * layout.em
            a, b = _r(prev), _r(run)
            rect = QRectF(
                a.x() + (b.x() - a.x()) * p,
                a.y() + (b.y() - a.y()) * p,
                a.width() + (b.width() - a.width()) * p,
                a.height() + (b.height() - a.height()) * p,
            )
    stroke_w = 0.0
    glow_r = 0.0
    if bh is not None:
        stroke_w = float(bh.stroke_width or 0.0)
        glow_r = float(bh.glow_radius or 0.0)
        overshoot = float(bh.overshoot or 0.0)
        bounce_dur = float(bh.duration or 0.0)
        if overshoot > 1.0 and bounce_dur > 0:
            from cutora.render.anim_engine import compute_dyn_box_scale
            sc = compute_dyn_box_scale(t, float(run.t_start), float(run.t_end),
                                       overshoot=overshoot, anim_duration=bounce_dur)
            if sc > 0:
                cx, cy = rect.center().x(), rect.center().y()
                rect = QRectF(
                    cx - rect.width() * sc / 2.0,
                    cy - rect.height() * sc / 2.0,
                    rect.width() * sc,
                    rect.height() * sc,
                )
    if bh is not None:
        off_y = getattr(bh, "offset_y_em", None)
        if off_y is not None:
            rect.translate(0.0, float(off_y) * layout.em)
    def _bh_brush(rrect, a):
        _ = rrect
        return QBrush(_qcolor(color, a))

    pnt = QPainter(bg)
    try:
        pnt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if glow_r > 0:
            glow_rect = rect.adjusted(-glow_r, -glow_r, glow_r, glow_r)
            pnt.setPen(Qt.PenStyle.NoPen)
            pnt.setBrush(_bh_brush(glow_rect, max(0.08, min(0.35, alpha * 0.5))))
            pnt.drawPath(_round_rect_path(glow_rect, radius + glow_r))
        if stroke_w > 0:
            pen = QPen(_bh_brush(rect, min(1.0, alpha + 0.2)), max(1.0, stroke_w))
            pnt.setPen(pen)
            pnt.setBrush(Qt.BrushStyle.NoBrush)
        else:
            pnt.setPen(Qt.PenStyle.NoPen)
            pnt.setBrush(_bh_brush(rect, alpha))
        pnt.drawPath(_round_rect_path(rect, radius))
        if stroke_w <= 0:
            pass
        else:
            pnt.setPen(Qt.PenStyle.NoPen)
            pnt.setBrush(_bh_brush(rect, max(0.0, alpha * 0.15)))
            pnt.drawPath(_round_rect_path(rect, radius))
    finally:
        pnt.end()
    fade = spec.bg.fade
    _fade_offs = (
        fade.top_offset, fade.bottom_offset,
        fade.left_offset, fade.right_offset,
    )
    from cutora.render.textfx.fade import offsets_active
    if max(fade.top, fade.bottom, fade.left, fade.right) > 0 or offsets_active(_fade_offs):
        box = (
            int(rect.x()), int(rect.y()),
            max(1, int(rect.width())), max(1, int(rect.height())),
        )
        bg = apply_edge_fade(
            bg, fade.top, fade.bottom, fade.left, fade.right, boxes=[box],
            offsets=_fade_offs)
    _ = as_bgra
    _ = QColor
    _ = QPainterPath
    return bg


def _line_bar_progress(line, t: float) -> float:
    """0–1 width fraction: line start → current-word right edge, eased."""
    runs = [r for r in (line.runs or []) if str(r.text or "").strip()]
    if not runs:
        return 1.0
    t0 = float(runs[0].t_start)
    t1 = float(runs[-1].t_end)
    if t >= t1 - 1e-9:
        return 1.0
    if t <= t0:
        return 0.0
    line_x = float(line.x)
    line_w = max(float(line.w), 1e-6)
    for i, run in enumerate(runs):
        rs = float(run.t_start)
        re = float(run.t_end)
        nxt = float(runs[i + 1].t_start) if i + 1 < len(runs) else t1
        if t < rs:
            continue
        if t <= re or t < nxt:
            dur = max(re - rs, 1e-6)
            u = max(0.0, min(1.0, (t - rs) / dur))
            x = float(run.x) + float(run.w) * ease_out_cubic(u)
            return max(0.0, min(1.0, (x - line_x) / line_w))
    return 1.0


def _line_bar_qcolor(color: str, alpha: float) -> QColor:
    r, g, b, a_hex = rgba_tuple(color)
    a = a_hex if len(str(color).lstrip("#")) >= 8 else float(alpha)
    c = QColor(r, g, b)
    c.setAlpha(max(0, min(255, int(round(a * 255)))))
    return c


def _line_bar_wave_path(x0: float, x1: float, cy: float, amp: float) -> QPainterPath:
    path = QPainterPath()
    width = max(1.0, x1 - x0)
    n = max(24, int(width))
    for i in range(n + 1):
        s = i / n
        x = x0 + width * s
        y = cy + amp * math.sin(2.0 * math.pi * 2.0 * s)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def _line_bar_brush_poly(x0: float, x1: float, cy: float, height: float) -> QPolygonF:
    width = max(1.0, x1 - x0)
    n = max(12, int(width / 4.0))
    top, bot = [], []
    for i in range(n + 1):
        s = i / n
        x = x0 + width * s
        half = height * (0.12 + 0.88 * math.sin(math.pi * s)) * 0.5
        top.append(QPointF(x, cy - half))
        bot.append(QPointF(x, cy + half))
    pts = top + list(reversed(bot))
    return QPolygonF(pts)


def _composite_glow(tile: QImage, glow_px: float, color: QColor) -> QImage:
    from cutora.render.textfx.raster import as_bgra, gaussian_channel

    if glow_px <= 0.05:
        return tile
    owned, bgra = as_bgra(tile)
    alpha = bgra[:, :, 3].astype(np.float32)
    blur = gaussian_channel(alpha, glow_px)
    glow_a = np.clip(blur * 0.85, 0, 255)
    glow = np.zeros_like(bgra, dtype=np.float32)
    scale = glow_a / 255.0
    glow[:, :, 0] = color.blue() * scale
    glow[:, :, 1] = color.green() * scale
    glow[:, :, 2] = color.red() * scale
    glow[:, :, 3] = glow_a
    src = bgra.astype(np.float32)
    sa = src[:, :, 3:4] / 255.0
    out = src + glow * (1.0 - sa)
    bgra[:] = np.clip(out, 0, 255).astype(np.uint8)
    return owned


def _paint_line_bar_shape(pnt: QPainter, rect: QRectF, radius: float,
                          kind: str, color: QColor) -> None:
    pnt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if kind == "solid":
        from cutora.render.textfx.raster import _round_rect_path
        pnt.setPen(Qt.PenStyle.NoPen)
        pnt.setBrush(color)
        pnt.drawPath(_round_rect_path(rect, radius))
        return
    x0, x1 = rect.left(), rect.right()
    cy = rect.center().y()
    thick = max(1.0, rect.height())
    if kind == "brush":
        pnt.setPen(Qt.PenStyle.NoPen)
        pnt.setBrush(color)
        pnt.drawPolygon(_line_bar_brush_poly(x0, x1, cy, thick))
        return
    pen = QPen(color, thick)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if kind == "dashed":
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4.0, 3.0])
    elif kind == "dotted":
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([1.0, 2.2])
    else:
        pen.setStyle(Qt.PenStyle.SolidLine)
    pnt.setBrush(Qt.BrushStyle.NoBrush)
    pnt.setPen(pen)
    if kind == "wave":
        pnt.drawPath(_line_bar_wave_path(x0, x1, cy, thick * 0.45))
    else:
        pnt.drawLine(QPointF(x0, cy), QPointF(x1, cy))


def draw_line_bar(bg: QImage | None, layout, spec, t: float, event: dict,
                  style=None) -> QImage | None:
    """Bar under the current sentence/line. Preview and export share this."""
    from cutora.render.textfx.anim import _anim_ids
    from cutora.render.textfx.raster import _qcolor, _round_rect_path, as_bgra
    loop_ids = _anim_ids(spec.anim.loop)
    if not isinstance(style, dict):
        style = (event.get("_style") if isinstance(event, dict) else None) or {}
    fx = style.get("fx") if isinstance(style, dict) else None
    follow = False
    if isinstance(fx, dict) and fx.get("follow_sentence"):
        follow = True
    if "line_bar" not in loop_ids and not follow:
        return bg
    line = None
    for ln in layout.lines or []:
        if not ln.runs:
            continue
        t0 = float(ln.runs[0].t_start)
        t1 = float(ln.runs[-1].t_end)
        if t0 <= t <= t1 + 1e-6:
            line = ln
            break
    if line is None and layout.lines:
        line = layout.lines[0]
    if line is None:
        return bg
    ox, oy = layout.origin
    lb = getattr(spec, "line_bar", None)
    pad = float(getattr(lb, "pad_em", 0.18)) * layout.em
    bar_h = max(3.0, float(getattr(lb, "height_em", 0.22)) * layout.em)
    off_y = float(getattr(lb, "offset_y_em", 0.06)) * layout.em
    if lb is not None and not getattr(lb, "_use_bg_radius", True):
        radius = float(lb.radius_em) * layout.em
    else:
        radius = float(spec.bg.radius_em) * layout.em
    color = ""
    alpha = 0.7
    kind = "solid"
    anim = "none"
    glow_em = 0.0
    if lb is not None:
        color = str(lb.color or "")
        alpha = float(getattr(lb, "_alpha", 0.7))
        kind = str(lb.style or "solid")
        anim = str(lb.anim or "none")
        glow_em = float(lb.glow_em or 0)
    if not color:
        color = str(spec.emphasis.color or spec.bg.color or "#F2C14E")
    full_w = line.w + 2 * pad
    progress = 1.0 if anim != "draw" else _line_bar_progress(line, t)
    if progress <= 1e-6:
        return bg
    rect = QRectF(
        line.x - ox - pad,
        line.y - oy + line.h + off_y,
        full_w * progress,
        bar_h,
    )
    legacy = (
        anim != "draw" and kind == "solid" and glow_em <= 1e-9
    )
    if legacy:
        if bg is None:
            bg = QImage(
                layout.roi_w, layout.roi_h,
                QImage.Format.Format_ARGB32_Premultiplied)
            bg.fill(0)
        else:
            bg = bg.copy()
        pnt = QPainter(bg)
        try:
            pnt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pnt.setPen(Qt.PenStyle.NoPen)
            pnt.setBrush(_qcolor(color, alpha))
            pnt.drawPath(_round_rect_path(rect, radius))
        finally:
            pnt.end()
        _ = as_bgra
        return bg
    glow_px = glow_em * layout.em
    margin = int(math.ceil(max(4.0, glow_px * 3.0 + 4.0)))
    dx = int(math.floor(rect.x())) - margin
    dy = int(math.floor(rect.y())) - margin
    tw = int(math.ceil(rect.width())) + 2 * margin + 2
    th = int(math.ceil(rect.height() + 2.0 * max(0.0, glow_px))) + 2 * margin + 2
    if tw < 1:
        tw = 1
    if th < 1:
        th = 1
    tile = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
    tile.fill(0)
    local = QRectF(rect.x() - dx, rect.y() - dy, rect.width(), rect.height())
    qc = _line_bar_qcolor(color, alpha)
    tp = QPainter(tile)
    try:
        _paint_line_bar_shape(tp, local, radius, kind, qc)
    finally:
        tp.end()
    if glow_px > 0.05:
        tile = _composite_glow(tile, glow_px, qc)
    if bg is None:
        out = QImage(
            layout.roi_w, layout.roi_h,
            QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(0)
    else:
        out = bg.copy()
    pnt = QPainter(out)
    try:
        pnt.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        pnt.drawImage(dx, dy, tile)
    finally:
        pnt.end()
    return out


def _unit_list(layout, aid: str):
    if aid in ("stack", "bullet_reveal"):
        return [
            {"x": ln.x, "y": ln.y, "w": ln.w, "h": ln.h,
             "cx": ln.x + ln.w * 0.5, "cy": ln.baseline,
             "word": {"start": (ln.runs[0].t_start if ln.runs else 0.0),
                      "end": (ln.runs[-1].t_end if ln.runs else 0.0)}}
            for ln in layout.lines if ln.w > 0
        ]
    if aid == "flip_clock":
        from cutora.render.textfx.layout import _qfont
        from PySide6.QtGui import QFontMetricsF
        units = []
        for line in layout.lines:
            for run in line.runs:
                if not str(run.text or "").strip():
                    continue
                if run.qfont is not None:
                    font = QFont(run.qfont)
                else:
                    font = _qfont(
                        run.font_family, run.fontsize_px, True, False,
                        run.tracking_em)
                fm = QFontMetricsF(font)
                x = run.x + float(run.pad_px)
                for ch in run.text:
                    adv = float(fm.horizontalAdvance(ch))
                    if ch.strip():
                        units.append({
                            "x": x, "y": run.y, "w": adv, "h": run.h,
                            "cx": x + adv * 0.5, "cy": run.baseline,
                            "word": {"start": run.t_start, "end": run.t_end},
                        })
                    x += adv
        return units
    return [
        {"x": r.x, "y": r.y, "w": r.w, "h": r.h,
         "cx": r.x + r.w * 0.5, "cy": r.baseline,
         "word": {"start": r.t_start, "end": r.t_end}, "run": r}
        for r in _runs(layout)
    ]


def _alpha_only(xf) -> bool:
    """True when the warp is a uniform premul fade (bullet_reveal / fade)."""
    clip_dir = str(getattr(xf, "clip_dir", None) or "ltr")
    return (
        abs(float(xf.alpha) - 1.0) >= 1e-4
        and abs(float(xf.scale_x) - 1.0) < 1e-4
        and abs(float(xf.scale_y) - 1.0) < 1e-4
        and abs(float(xf.dx_em)) < 1e-6
        and abs(float(xf.dy_em)) < 1e-6
        and abs(float(xf.rot_deg)) < 1e-3
        and float(xf.clip) >= 0.999
        and clip_dir not in ("box",)
        and float(xf.blur_sigma_em) <= 1e-6
        and not xf.extra_color
    )


def _mul_premul_alpha(img: QImage, alpha: float) -> QImage:
    """Integer premul scale. QPainter.setOpacity on UNIT_WARP crops is not
    bit-stable after a long in-process paint sequence (g11 pass A vs B)."""
    from cutora.render.textfx.raster import as_bgra
    a = max(0.0, min(1.0, float(alpha)))
    if a >= 1.0 - 1e-4:
        return img
    out = img.copy()
    if a <= 1e-6:
        out.fill(0)
        return out
    owned, bgra = as_bgra(out)
    q = int(round(a * 255.0))
    bgra[:] = ((bgra.astype(np.uint16) * q + 127) // 255).astype(np.uint8)
    return owned


def _blit_over(dst_bgra: np.ndarray, src: QImage, x: int, y: int) -> None:
    """Integer src-over into premul BGRA. Avoids QPainter.drawImage on *out*."""
    from cutora.render.textfx.raster import as_bgra
    _owned, s = as_bgra(src)
    sh, sw = s.shape[:2]
    dh, dw = dst_bgra.shape[:2]
    x, y = int(x), int(y)
    sx0 = 0 if x >= 0 else -x
    sy0 = 0 if y >= 0 else -y
    dx0 = max(0, x)
    dy0 = max(0, y)
    w = min(sw - sx0, dw - dx0)
    h = min(sh - sy0, dh - dy0)
    if w <= 0 or h <= 0:
        return
    sl_d = dst_bgra[dy0:dy0 + h, dx0:dx0 + w]
    sl_s = s[sy0:sy0 + h, sx0:sx0 + w]
    sa = sl_s[..., 3].astype(np.uint16)
    inv = (255 - sa)[..., None]
    acc = sl_s.astype(np.uint16) + sl_d.astype(np.uint16) * inv // 255
    sl_d[:] = np.clip(acc, 0, 255).astype(np.uint8)


def apply_geom(img, layout, spec, t, event, xf_whole, video_w=0, video_h=0):
    """Unit warps for flip/stack/word-scale; else one ROI warp."""
    from dataclasses import replace
    from cutora.render.textfx.anim import (
        _anim_id, transform_for, unit_progress, xf_is_identity,
    )
    from cutora.render.textfx.raster import warp_layer_xf
    if img is None:
        return None
    phase, _u = unit_progress(t, 0, 1, spec.anim, event)
    if phase == "in":
        aid = _anim_id(spec.anim.in_name)
    elif phase == "out":
        aid = _anim_id(spec.anim.out_name)
    else:
        aid = _anim_id(spec.anim.loop)
    # k=0 may already be hold while later staggered units are still in.
    if aid not in UNIT_WARP:
        for name in (spec.anim.in_name, spec.anim.loop, spec.anim.out_name):
            cand = _anim_id(name)
            if cand in UNIT_WARP:
                aid = cand
                break
    if aid not in UNIT_WARP:
        if xf_whole is not None and not xf_is_identity(xf_whole):
            return warp_layer_xf(img, layout, xf_whole, video_w, video_h)
        return img
    units = _unit_list(layout, aid)
    n = max(1, len(units))
    # bullet_reveal is alpha-only per line. QPainter.setOpacity + drawImage
    # on those crops is not bit-stable across two in-process exports.
    numpy_comp = aid == "bullet_reveal"
    fmt = (
        QImage.Format.Format_ARGB32_Premultiplied if numpy_comp else img.format()
    )
    out = QImage(img.size(), fmt)
    out.fill(0)
    ox, oy = layout.origin
    dst_bgra = None
    if numpy_comp:
        from cutora.render.textfx.raster import as_bgra
        out, dst_bgra = as_bgra(out)
    for k, unit in enumerate(units):
        word = unit.get("word")
        phase_k, u_k = unit_progress(t, k, n, spec.anim, event, word=word)
        from cutora.render.textfx.anim import _compressed_timing
        start = float(event.get("start") or 0.0)
        end = float(event.get("end") or (start + 1.0))
        _in_s, _out_s, stagger = _compressed_timing(n, spec.anim, start, end)
        k_start = start + k * stagger
        if phase_k == "in" and t + 1e-9 < k_start:
            continue
        ctx = {
            "k": k, "n": n, "seed": spec.anim.seed,
            "layout": layout, "em": layout.em,
            "stroke_em": spec.stroke.width_em if spec.stroke.live() else 0.0,
            "line_h_em": (unit["h"] / layout.em) if layout.em else 1.2,
            "emph_color": spec.emphasis.color,
        }
        if aid == "stack":
            started = 0
            for j in range(k + 1, n):
                ph_j, u_j = unit_progress(t, j, n, spec.anim, event)
                if ph_j == "in" and u_j > 0.0:
                    started += 1
            ctx["started_after"] = started
        xf = transform_for(phase_k, u_k, spec.anim, ctx)
        if aid == "colour_highlight":
            from cutora.render.textfx.anim import Transform, _colour_scale
            run = unit.get("run")
            cur, local_u, idx = current_run(layout, t)
            if run is None or cur is None or run is not cur:
                xf = Transform()
            else:
                s = _colour_scale(local_u)
                xf = Transform(scale_x=s, scale_y=s)
        if aid == "rotate_highlight":
            from cutora.render.textfx.anim import Transform
            run = unit.get("run")
            cur, local_u, idx = current_run(layout, t)
            if run is None or cur is None or run is not cur:
                xf = Transform()
            else:
                sign = -1.0 if idx % 2 else 1.0
                fade = min(1.0, max(0.0, (t - float(cur.t_start)) / ROT_HL_S))
                xf = Transform(rot_deg=sign * ROT_HL_DEG * (1.0 - fade))
        # Source crop stays on this unit. dx/dy is a blit offset so a
        # neighbor line is not pulled into the crop (stack ghost).
        pad = 4.0
        extra = max(abs(xf.scale_x), abs(xf.scale_y), 1.0) - 1.0
        pad += extra * max(unit["w"], unit["h"]) * 0.5 + abs(xf.rot_deg) * 0.4
        x0 = int(math_floor(unit["x"] - ox - pad))
        y0 = int(math_floor(unit["y"] - oy - pad))
        x1 = int(math_ceil(unit["x"] + unit["w"] - ox + pad))
        y1 = int(math_ceil(unit["y"] + unit["h"] - oy + pad))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.width(), x1), min(img.height(), y1)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = img.copy(x0, y0, x1 - x0, y1 - y0)
        class _Lay:
            pass
        lay = _Lay()
        lay.em = layout.em
        lay.origin = (0, 0)
        lay.cx = unit["cx"] - ox - x0
        lay.cy = unit["cy"] - oy - y0
        lay.text_x, lay.text_y = 0.0, 0.0
        lay.text_w, lay.text_h = float(x1 - x0), float(y1 - y0)
        xf_geo = replace(xf, dx_em=0.0, dy_em=0.0)
        if _alpha_only(xf_geo):
            warped = _mul_premul_alpha(crop, xf_geo.alpha)
        elif not xf_is_identity(xf_geo):
            warped = warp_layer_xf(crop, lay, xf_geo)
        else:
            warped = crop
        ox_draw = int(round(x0 + xf.dx_em * layout.em))
        oy_draw = int(round(y0 + xf.dy_em * layout.em))
        if numpy_comp:
            _blit_over(dst_bgra, warped, ox_draw, oy_draw)
        else:
            p = QPainter(out)
            p.drawImage(ox_draw, oy_draw, warped)
            p.end()
    return out
