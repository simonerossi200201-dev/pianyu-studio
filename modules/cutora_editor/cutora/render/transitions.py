# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-function video transitions: (A, B, t, w, h, **params) -> QImage.

t=0 is A, t=1 is B. Preview thumbs and export both call these through
``compose_standalone_transition``.
"""
from __future__ import annotations

import math


def _ease(t, name="ease_in_out"):
    from cutora.render.anim_engine import EASING_MAP, ease_in_out_cubic
    t = max(0.0, min(1.0, float(t)))
    fn = EASING_MAP.get(str(name or "ease_in_out"), ease_in_out_cubic)
    return fn(t)


_QCACHE = None


def _q():
    global _QCACHE
    if _QCACHE is None:
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform
        from PySide6.QtCore import QRectF, QPointF, Qt
        _QCACHE = (QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt)
    return _QCACHE


def _canvas(w, h, fill=0):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    if fill == 0:
        img.fill(0)
    elif fill is None:
        img.fill(0)
    else:
        img.fill(fill)
    return img


def _copy_fit(src, w, h):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    if src is None or src.isNull():
        return _canvas(w, h)
    if src.width() == int(w) and src.height() == int(h):
        return src.copy()
    out = _canvas(w, h)
    p = QPainter(out)
    try:
        p.drawImage(QRectF(0, 0, w, h), src, QRectF(0, 0, src.width(), src.height()))
    finally:
        p.end()
    return out


def _draw(painter, img, x, y, w, h):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    if img is None or img.isNull():
        return
    painter.drawImage(QRectF(x, y, w, h), img, QRectF(0, 0, img.width(), img.height()))


def _dir_vec(direction, w, h):
    d = str(direction or "left").lower()
    if d in ("right", "e"):
        return float(w), 0.0
    if d in ("up", "top", "n"):
        return 0.0, -float(h)
    if d in ("down", "bottom", "s"):
        return 0.0, float(h)
    return -float(w), 0.0


def _endpoints(a, b, t, w, h):
    if t <= 0.0:
        return _copy_fit(a, w, h)
    if t >= 1.0:
        return _copy_fit(b, w, h)
    return None


def _feather_px(feather, w, h):
    """Edge width in pixels. *feather* is a percent of the short side, clamped 0–50."""
    try:
        f = float(feather)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f) or f <= 0.0:
        return 0.0
    if f > 50.0:
        f = 50.0
    return (f / 100.0) * float(min(int(w), int(h)))


def _legacy_smooth(t):
    """smoothstep used by the pre-Q79 anim_engine wipes and pushes. Not the easing knob."""
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


# Presets whose picture has an A/B boundary. Dissolves stay off this set.
EDGED_TRANSITION_PRESETS = frozenset({
    "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    "wipe_angle", "wipe_circle", "wipe_clock", "wipe_blinds",
    "push_left", "push_right", "push_up", "push_down",
    "push_out", "push_out_left", "push_out_right", "push_out_up", "push_out_down",
    "push_split",
    "page_turn", "cube_spin",
    "scanlines", "zoom_out_trans",
})


def _cardinal_name(direction):
    d = str(direction or "left").lower()
    if d in ("right", "e"):
        return "right"
    if d in ("up", "top", "n"):
        return "up"
    if d in ("down", "bottom", "s"):
        return "down"
    return "left"


def _rotate_vec(nx, ny, degrees):
    th = math.radians(float(degrees or 0.0))
    c, s = math.cos(th), math.sin(th)
    return nx * c - ny * s, nx * s + ny * c


def _blend_masked(base, overlay, w, h, paint_mask):
    """Draw *overlay* onto *base*. *paint_mask* fills alpha via DestinationIn
    (255 keeps overlay, 0 drops it). One gradient fill, not a frame blur."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(base, w, h)
    layer = _copy_fit(overlay, w, h)
    mp = QPainter(layer)
    try:
        mp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        paint_mask(mp)
    finally:
        mp.end()
    p = QPainter(out)
    try:
        p.drawImage(0, 0, layer)
    finally:
        p.end()
    return out


def _paint_linear_mask(mp, nx, ny, threshold, feather_px, w, h):
    """Unit normal. Overlay stays where proj <= threshold and falls off across *feather_px*."""
    from PySide6.QtGui import QLinearGradient
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    half = max(0.75, float(feather_px) * 0.5)
    mx = float(nx) * float(threshold)
    my = float(ny) * float(threshold)
    p0 = QPointF(mx - float(nx) * half, my - float(ny) * half)
    p1 = QPointF(mx + float(nx) * half, my + float(ny) * half)
    grad = QLinearGradient(p0, p1)
    grad.setColorAt(0.0, QColor(0, 0, 0, 255))
    grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    mp.fillRect(QRectF(0, 0, float(w), float(h)), grad)


def _halfplane_hard(base, overlay, w, h, nx, ny, threshold):
    """Crisp half-plane: overlay where nx*x + ny*y < threshold. AA off so the edge stays a step."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(base, w, h)
    span = float(int(w) + int(h)) * 4.0 + 8.0
    nx, ny = float(nx), float(ny)
    if abs(nx) >= abs(ny) and abs(nx) > 1e-8:
        y_a, y_b = -span, span
        x_a = (float(threshold) - ny * y_a) / nx
        x_b = (float(threshold) - ny * y_b) / nx
    elif abs(ny) > 1e-8:
        x_a, x_b = -span, span
        y_a = (float(threshold) - nx * x_a) / ny
        y_b = (float(threshold) - nx * x_b) / ny
    else:
        return out
    path = QPainterPath()
    path.moveTo(QPointF(x_a, y_a))
    path.lineTo(QPointF(x_b, y_b))
    path.lineTo(QPointF(x_b - nx * span, y_b - ny * span))
    path.lineTo(QPointF(x_a - nx * span, y_a - ny * span))
    path.closeSubpath()
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    try:
        painter.setClipPath(path)
        _draw(painter, overlay, 0, 0, w, h)
    finally:
        painter.end()
    return out


def _legacy_axis_wipe(a, b, t, w, h, base):
    """Byte-identical to the pre-Q79 anim_engine wipe_* branch (smoothstep, hard clip)."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    w, h = int(w), int(h)
    p_smooth = _legacy_smooth(t)
    result = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        if a is not None and not a.isNull():
            painter.setOpacity(1.0)
            painter.drawImage(QRectF(0, 0, w, h), a, QRectF(0, 0, a.width(), a.height()))
        if b is not None and not b.isNull() and p_smooth > 0:
            painter.setOpacity(1.0)
            if str(base).endswith("right"):
                ww = int(round(w * p_smooth))
                painter.setClipRect(w - ww, 0, ww, h)
            elif str(base).endswith("up"):
                hh = int(round(h * p_smooth))
                painter.setClipRect(0, 0, w, hh)
            elif str(base).endswith("down"):
                hh = int(round(h * p_smooth))
                painter.setClipRect(0, h - hh, w, hh)
            else:
                ww = int(round(w * p_smooth))
                painter.setClipRect(0, 0, ww, h)
            painter.drawImage(QRectF(0, 0, w, h), b, QRectF(0, 0, b.width(), b.height()))
            painter.setClipping(False)
    finally:
        painter.end()
    return result


def _legacy_push(a, b, t, w, h, direction):
    """Byte-identical to the pre-Q79 anim_engine push_* branch."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    w, h = int(w), int(h)
    p_smooth = _legacy_smooth(t)
    name = "push_" + _cardinal_name(direction)
    dx = dy = 0.0
    in_dx = in_dy = 0.0
    if name.endswith("left"):
        dx = -w * p_smooth
        in_dx = w + dx
    elif name.endswith("right"):
        dx = w * p_smooth
        in_dx = -w + dx
    elif name.endswith("up"):
        dy = -h * p_smooth
        in_dy = h + dy
        in_dx = 0.0
    else:
        dy = h * p_smooth
        in_dy = -h + dy
        in_dx = 0.0
    if "up" not in name and "down" not in name:
        in_dy = 0.0
    result = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        if a is not None and not a.isNull():
            painter.setOpacity(1.0)
            painter.drawImage(QRectF(dx, dy, w, h), a, QRectF(0, 0, a.width(), a.height()))
        if b is not None and not b.isNull():
            painter.setOpacity(1.0)
            painter.drawImage(QRectF(in_dx, in_dy, w, h), b, QRectF(0, 0, b.width(), b.height()))
    finally:
        painter.end()
    return result


def _stamp_feather(painter, img, w, h, nx, ny, threshold, feather_px):
    """Draw *img* onto *painter*, opaque on the keep side of the line, fading across the band."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    layer = _copy_fit(img, w, h)
    mp = QPainter(layer)
    try:
        mp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        _paint_linear_mask(mp, nx, ny, threshold, feather_px, w, h)
    finally:
        mp.end()
    painter.drawImage(0, 0, layer)


def _mix_rect(img, src, x, y, bw, bh, w, h):
    """Flat 55% mix of *src* inside a rect. Cheaper than rescaling a full-width band."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = QPainter(img)
    try:
        p.setOpacity(0.55)
        p.setClipRect(QRectF(float(x), float(y), float(bw), float(bh)))
        _draw(p, src, 0, 0, w, h)
    finally:
        p.end()


def _soften_seam_strip(img, x0, y0, x1, y1):
    """Blur only the seam band. Not a full-frame gaussian."""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    w, h = img.width(), img.height()
    x0 = max(0, min(w - 1, int(math.floor(x0))))
    y0 = max(0, min(h - 1, int(math.floor(y0))))
    x1 = max(x0 + 1, min(w, int(math.ceil(x1))))
    y1 = max(y0 + 1, min(h, int(math.ceil(y1))))
    bw, bh = x1 - x0, y1 - y0
    if bw < 2 or bh < 2:
        return img
    strip = img.copy(x0, y0, bw, bh)
    if bw <= bh:
        tw, th = max(1, min(bw, max(1, bw // 8))), bh
    else:
        tw, th = bw, max(1, min(bh, max(1, bh // 8)))
    small = strip.scaled(
        tw, th,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)
    # Smooth upscale keeps the ramp from stair-stepping. Large bands use the
    # fast upscale: at 50% of 720p the band is most of the frame and the
    # smooth rescale misses the frame budget.
    up = (Qt.TransformationMode.FastTransformation if bw * bh > 200000
          else Qt.TransformationMode.SmoothTransformation)
    back = small.scaled(bw, bh, Qt.AspectRatioMode.IgnoreAspectRatio, up)
    p = QPainter(img)
    try:
        p.drawImage(x0, y0, back)
    finally:
        p.end()
    return img


def _frame_threshold(nx, ny, w, h, p):
    corners = (
        (0.0, 0.0), (float(w), 0.0), (0.0, float(h)), (float(w), float(h)),
    )
    vals = [x * nx + y * ny for x, y in corners]
    lo, hi = min(vals), max(vals)
    return lo + (hi - lo) * float(p)


def _wipe_angle_plane(w, h, ang_deg, p, intensity):
    """Moving edge of wipe_angle's rotated clip, as a unit-normal half-plane."""
    half = math.hypot(float(w), float(h)) * 0.5 * max(1.0, float(intensity))
    th = math.radians(float(ang_deg))
    c, s = math.cos(th), math.sin(th)
    nx, ny = c, s
    local_x = half * (2.0 * float(p) - 1.0)
    threshold = (float(w) / 2.0) * nx + (float(h) / 2.0) * ny + local_x
    return nx, ny, threshold


# ── 推移 ──────────────────────────────────────────────

def push_out(a, b, t, w, h, *, direction="left", easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    """A slides off; B is already full-frame underneath (A uncovers B)."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    dx, dy = _dir_vec(direction, w, h)
    # Travel always covers the full frame at t=1; intensity>1 overshoots.
    amp = max(1.0, min(2.0, float(intensity)))
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    try:
        _draw(painter, a, dx * p * amp, dy * p * amp, w, h)
    finally:
        painter.end()
    fe = _feather_px(feather, w, h)
    if fe > 0.0:
        name = _cardinal_name(direction)
        half = fe * 0.5
        ox, oy = dx * p * amp, dy * p * amp
        if name == "right":
            edge = ox
            _soften_seam_strip(out, edge - half, 0, edge + half, h)
        elif name == "up":
            edge = oy + float(h)
            _soften_seam_strip(out, 0, edge - half, w, edge + half)
        elif name == "down":
            edge = oy
            _soften_seam_strip(out, 0, edge - half, w, edge + half)
        else:
            edge = ox + float(w)
            _soften_seam_strip(out, edge - half, 0, edge + half, h)
    return out


def push_split(a, b, t, w, h, *, direction="horizontal", easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    """A splits apart left/right (or up/down); B sits in the middle."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    amp = max(1.0, min(2.0, float(intensity)))
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    try:
        horiz = str(direction or "horizontal").lower() not in ("vertical", "up", "down")
        if horiz:
            shift = w * 0.5 * p * amp
            remain = max(0.0, w * 0.5 - shift)
            if remain > 0:
                painter.setClipRect(0, 0, int(math.ceil(remain)), int(h))
                _draw(painter, a, -shift, 0, w, h)
                painter.setClipRect(int(w - remain), 0, int(math.ceil(remain)), int(h))
                _draw(painter, a, shift, 0, w, h)
        else:
            shift = h * 0.5 * p * amp
            remain = max(0.0, h * 0.5 - shift)
            if remain > 0:
                painter.setClipRect(0, 0, int(w), int(math.ceil(remain)))
                _draw(painter, a, 0, -shift, w, h)
                painter.setClipRect(0, int(h - remain), int(w), int(math.ceil(remain)))
                _draw(painter, a, 0, shift, w, h)
    finally:
        painter.end()
    fe = _feather_px(feather, w, h)
    if fe > 0.0:
        fp = QPainter(out)
        try:
            fp.setOpacity(0.55)
            if horiz:
                gap_l = w * 0.5 - shift
                gap_r = w * 0.5 + shift
                fp.setClipRect(QRectF(gap_l - fe * 0.5, 0, fe, float(h)))
                _draw(fp, b, 0, 0, w, h)
                fp.setClipRect(QRectF(gap_r - fe * 0.5, 0, fe, float(h)))
                _draw(fp, b, 0, 0, w, h)
            else:
                gap_t = h * 0.5 - shift
                gap_b = h * 0.5 + shift
                fp.setClipRect(QRectF(0, gap_t - fe * 0.5, float(w), fe))
                _draw(fp, b, 0, 0, w, h)
                fp.setClipRect(QRectF(0, gap_b - fe * 0.5, float(w), fe))
                _draw(fp, b, 0, 0, w, h)
        finally:
            fp.end()
    return out


# ── 缩放 ──────────────────────────────────────────────

def smash_zoom(a, b, t, w, h, *, intensity=1.0, easing="ease_in", motion_blur=0.4, **_):
    """突进: A lunges forward + fades; B slams in from overscale with ghosting."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w / 2.0, h / 2.0
        a_scale = 1.0 + 1.15 * k * p
        if a is not None and not a.isNull():
            painter.save()
            painter.setOpacity(1.0)
            painter.translate(cx, cy)
            painter.scale(a_scale, a_scale)
            painter.translate(-cx, -cy)
            _draw(painter, a, 0, 0, w, h)
            painter.restore()
        b_from = 1.0 + 0.85 * k
        b_scale = b_from + (1.0 - b_from) * p
        blur = max(0.0, min(1.0, float(motion_blur)))
        if b is not None and not b.isNull():
            if blur > 0.02 and p < 0.95:
                n = max(2, int(blur * 5))
                for i in range(n, 0, -1):
                    frac = i / float(n)
                    ghost = b_scale + (b_from - 1.0) * 0.18 * frac
                    painter.save()
                    painter.setOpacity(p * blur * 0.22 * (1.0 - frac * 0.4))
                    painter.translate(cx, cy)
                    painter.scale(ghost, ghost)
                    painter.translate(-cx, -cy)
                    _draw(painter, b, 0, 0, w, h)
                    painter.restore()
            painter.save()
            painter.setOpacity(p)
            painter.translate(cx, cy)
            painter.scale(b_scale, b_scale)
            painter.translate(-cx, -cy)
            _draw(painter, b, 0, 0, w, h)
            painter.restore()
    finally:
        painter.end()
    return out


def zoom_out_trans(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", feather=0.0, **_):
    """拉远: A shrinks away; B is revealed behind it."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        scale = max(0.02, 1.0 - 0.92 * k * p)
        op = max(0.0, 1.0 - p)
        cx, cy = w / 2.0, h / 2.0
        painter.setOpacity(op)
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.translate(-cx, -cy)
        _draw(painter, a, 0, 0, w, h)
    finally:
        painter.end()
    fe = _feather_px(feather, w, h)
    if fe > 0.0:
        rw, rh = float(w) * scale, float(h) * scale
        x0 = (float(w) - rw) / 2.0
        y0 = (float(h) - rh) / 2.0
        # One band around the whole shrinking rect. Interior of a flat frame
        # is unchanged by the rescale; the rect edge is what softens.
        _soften_seam_strip(out, x0 - fe * 0.15, y0 - fe * 0.15, x0 + rw + fe * 0.15, y0 + rh + fe * 0.15)
    return out


# ── 擦除 ──────────────────────────────────────────────

def wipe_cardinal(a, b, t, w, h, *, direction="left", easing="ease_in_out", intensity=1.0,
                  feather=0.0, angle=0.0, **kw):
    names = {"left": 0.0, "right": 180.0, "up": 270.0, "down": 90.0,
             "e": 180.0, "top": 270.0, "n": 270.0, "bottom": 90.0, "s": 90.0}
    d = direction
    try:
        ang = float(d)
    except (TypeError, ValueError):
        ang = names.get(str(d or "left").lower(), 0.0)
    try:
        ang = ang + float(angle or 0.0)
    except (TypeError, ValueError):
        pass
    return wipe_angle(
        a, b, t, w, h, direction=ang, easing=easing, intensity=intensity,
        feather=feather, **kw)


def wipe_screen(a, b, t, w, h, *, direction="left", angle=0.0, feather=0.0,
                easing="ease_in_out", intensity=1.0, **_):
    """「擦」. angle 0 + feather 0 is the old axis-aligned clip. Otherwise the edge tilts
    and/or fades. *angle* is degrees from the cardinal edge (0 = vertical for left/right)."""
    try:
        ang = float(angle)
    except (TypeError, ValueError):
        ang = 0.0
    if not math.isfinite(ang):
        ang = 0.0
    fe = _feather_px(feather, w, h)
    name = _cardinal_name(direction)
    base = "wipe_" + name
    if abs(ang) < 1e-3 and fe <= 0.0:
        return _legacy_axis_wipe(a, b, t, w, h, base)
    if float(t) <= 0.0:
        return _copy_fit(a, w, h)
    if float(t) >= 1.0:
        return _copy_fit(b, w, h)
    # Same progress as the legacy clip, so a soft band sits on the old edge.
    p = _legacy_smooth(t)
    base_n = {"left": (1.0, 0.0), "right": (-1.0, 0.0), "up": (0.0, 1.0), "down": (0.0, -1.0)}
    nx0, ny0 = base_n[name]
    nx, ny = _rotate_vec(nx0, ny0, ang)
    threshold = _frame_threshold(nx, ny, w, h, p)
    if fe <= 0.0:
        return _halfplane_hard(a, b, w, h, nx, ny, threshold)

    def paint(mp):
        _paint_linear_mask(mp, nx, ny, threshold, fe, w, h)
    return _blend_masked(a, b, w, h, paint)


def wipe_angle(a, b, t, w, h, *, direction=32.0, easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    """Linear wipe at *direction* degrees (0 = left→right). Default 32° so it is
    not the same picture as wipe_left."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    try:
        ang = float(direction)
    except (TypeError, ValueError):
        names = {"left": 0.0, "right": 180.0, "up": 270.0, "down": 90.0}
        ang = names.get(str(direction or "left").lower(), 0.0)
    fe = _feather_px(feather, w, h)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        # Half-diagonal: p=0 clip width 0, p=1 covers the rotated image bbox.
        half = math.hypot(w, h) * 0.5 * max(1.0, float(intensity))
        painter.save()
        painter.translate(w / 2.0, h / 2.0)
        painter.rotate(ang)
        painter.setClipRect(-half, -half, 2.0 * half * p, 2.0 * half)
        painter.rotate(-ang)
        painter.translate(-w / 2.0, -h / 2.0)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
    finally:
        painter.end()
    if fe > 0.0:
        nx, ny, threshold = _wipe_angle_plane(w, h, ang, p, intensity)
        band = fe * 0.5
        mx = nx * threshold
        my = ny * threshold
        tx, ty = -ny, nx
        span = math.hypot(float(w), float(h))
        quad = QPainterPath()
        quad.moveTo(QPointF(mx + tx * span - nx * band, my + ty * span - ny * band))
        quad.lineTo(QPointF(mx - tx * span - nx * band, my - ty * span - ny * band))
        quad.lineTo(QPointF(mx - tx * span + nx * band, my - ty * span + ny * band))
        quad.lineTo(QPointF(mx + tx * span + nx * band, my + ty * span + ny * band))
        quad.closeSubpath()
        fp = QPainter(out)
        try:
            fp.setClipPath(quad)
            fp.setOpacity(0.55)
            _draw(fp, a, 0, 0, w, h)
        finally:
            fp.end()
    return out


def wipe_circle(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", feather=0.0, **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    # Full diagonal cover at t=1 regardless of intensity; k only shapes the grow.
    k = max(0.2, min(2.0, float(intensity)))
    grow = p ** (1.0 / k) if abs(k - 1.0) > 1e-6 else p
    r = math.hypot(w, h) * 0.5 * grow
    fe = _feather_px(feather, w, h)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        path = QPainterPath()
        path.addEllipse(QRectF(w / 2.0 - r, h / 2.0 - r, r * 2, r * 2))
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    if fe > 0.0 and r > 0.5:
        outer = r + fe * 0.5
        inner = max(0.0, r - fe * 0.5)
        ring = QPainterPath()
        ring.setFillRule(Qt.FillRule.OddEvenFill)
        ring.addEllipse(QRectF(w / 2.0 - outer, h / 2.0 - outer, outer * 2, outer * 2))
        if inner > 1.0:
            ring.addEllipse(QRectF(w / 2.0 - inner, h / 2.0 - inner, inner * 2, inner * 2))
        fp = QPainter(out)
        try:
            fp.setClipPath(ring)
            fp.setOpacity(0.55)
            _draw(fp, a, 0, 0, w, h)
        finally:
            fp.end()
    return out


def wipe_clock(a, b, t, w, h, *, direction="cw", easing="linear", feather=0.0, **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    span = 360.0 * p
    ccw = str(direction or "cw").lower() in ("ccw", "left")
    if ccw:
        span = -span
    fe = _feather_px(feather, w, h)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        path = QPainterPath()
        cx, cy = w / 2.0, h / 2.0
        path.moveTo(cx, cy)
        path.arcTo(QRectF(cx - w, cy - h, w * 2, h * 2), 90.0, -span)
        path.closeSubpath()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    if fe > 0.0 and abs(span) > 0.5:
        # Two wedges straddling the sector rays. A clipped arc is cheap;
        # a full-frame conical gradient is not.
        radius = max(8.0, math.hypot(float(w), float(h)) * 0.5)
        ang_w = min(36.0, math.degrees(fe / radius))
        mag = abs(float(span))
        # Qt arc angles: 0 at 3 o'clock, CCW. Clockwise sweep from 12 o'clock
        # when span > 0 (cw). The rays sit at 90° and 90 - mag.
        if not ccw:
            rays = (90.0, 90.0 - mag)
        else:
            rays = (90.0, 90.0 + mag)
        wp = QPainter(out)
        try:
            for ray in rays:
                wedge = QPainterPath()
                wedge.moveTo(cx, cy)
                wedge.arcTo(QRectF(cx - w, cy - h, w * 2, h * 2), ray - ang_w * 0.5, ang_w)
                wedge.closeSubpath()
                wp.save()
                wp.setClipPath(wedge)
                wp.setOpacity(0.8)
                _draw(wp, a, 0, 0, w, h)
                wp.restore()
        finally:
            wp.end()
    return out


def wipe_blinds(a, b, t, w, h, *, direction="horizontal", intensity=1.0, easing="ease_in_out", feather=0.0, **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    n = max(4, int(round(8 * max(0.4, min(2.0, float(intensity))))))
    fe = _feather_px(feather, w, h)
    horiz = str(direction or "horizontal").lower() not in ("vertical", "up", "down")
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        if horiz:
            slat = h / float(n)
            for i in range(n):
                y0 = i * slat
                hh = slat * p
                painter.setClipRect(0, int(y0), int(w), max(1, int(math.ceil(hh))))
                _draw(painter, b, 0, 0, w, h)
        else:
            slat = w / float(n)
            for i in range(n):
                x0 = i * slat
                ww = slat * p
                painter.setClipRect(int(x0), 0, max(1, int(math.ceil(ww))), int(h))
                _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    if fe > 0.0:
        fp = QPainter(out)
        try:
            fp.setOpacity(0.55)
            if horiz:
                slat = float(h) / float(n)
                for i in range(n):
                    y0 = i * slat
                    edge = y0 + slat * p
                    fp.setClipRect(QRectF(0, edge - fe * 0.5, float(w), fe))
                    _draw(fp, a, 0, 0, w, h)
            else:
                slat = float(w) / float(n)
                for i in range(n):
                    x0 = i * slat
                    edge = x0 + slat * p
                    fp.setClipRect(QRectF(edge - fe * 0.5, 0, fe, float(h)))
                    _draw(fp, a, 0, 0, w, h)
        finally:
            fp.end()
    return out


# ── 变形 ──────────────────────────────────────────────

def rotate_dissolve(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", direction="cw", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    sign = -1.0 if str(direction or "cw").lower() in ("cw", "right") else 1.0
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w / 2.0, h / 2.0
        painter.save()
        painter.setOpacity(1.0)
        painter.translate(cx, cy)
        painter.rotate(sign * 18.0 * k * p)
        painter.scale(1.0 + 0.08 * k * p, 1.0 + 0.08 * k * p)
        painter.translate(-cx, -cy)
        _draw(painter, a, 0, 0, w, h)
        painter.restore()
        painter.save()
        painter.setOpacity(p)
        painter.translate(cx, cy)
        painter.rotate(sign * 18.0 * k * (p - 1.0))
        painter.scale(0.88 + 0.12 * p, 0.88 + 0.12 * p)
        painter.translate(-cx, -cy)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
    finally:
        painter.end()
    return out


def page_turn(a, b, t, w, h, *, direction="left", easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    """Peel A off as a curled flap (not a wipe with a 16px stripe)."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    p = _ease(t, easing)
    k = max(0.4, min(1.6, float(intensity)))
    fe = _feather_px(feather, w, h)
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        d = str(direction or "left").lower()
        horiz = d not in ("up", "down", "top", "bottom")
        from_start = d in ("left", "up", "top")
        # Remaining A is the un-peeled sheet; the flap is a perspective quad
        # of a darkened, mirrored strip sitting over B.
        if horiz:
            fold = w * (p if from_start else (1.0 - p))
            remain_a = (w - fold) if from_start else fold
            if remain_a > 0.5:
                if fe <= 0.0:
                    if from_start:
                        painter.setClipRect(int(fold), 0, int(math.ceil(remain_a)), int(h))
                    else:
                        painter.setClipRect(0, 0, int(math.ceil(remain_a)), int(h))
                    _draw(painter, a, 0, 0, w, h)
                    painter.setClipping(False)
                elif from_start:
                    # A stays to the right of the fold. Band sits on the sheet.
                    _stamp_feather(painter, a, w, h, -1.0, 0.0, -fold, fe * 2.0)
                else:
                    _stamp_feather(painter, a, w, h, 1.0, 0.0, fold, fe * 2.0)
            remain = fold if not from_start else (w - fold)
            flap = min(w * 0.42 * k, max(0.0, remain), w * 0.38)
            flap = flap * math.sin(p * math.pi)
            pinch = h * 0.12 * k * math.sin(p * math.pi)
            if flap >= 2.0:
                if from_start:
                    src = QPolygonF([QPointF(0, 0), QPointF(flap, 0), QPointF(flap, h), QPointF(0, h)])
                    dst = QPolygonF([
                        QPointF(fold, 0),
                        QPointF(fold - flap, pinch),
                        QPointF(fold - flap, h - pinch),
                        QPointF(fold, h),
                    ])
                    shade_x = int(fold - flap)
                    shade_w = int(flap + 10)
                else:
                    src = QPolygonF([QPointF(w - flap, 0), QPointF(w, 0), QPointF(w, h), QPointF(w - flap, h)])
                    dst = QPolygonF([
                        QPointF(fold + flap, pinch),
                        QPointF(fold, 0),
                        QPointF(fold, h),
                        QPointF(fold + flap, h - pinch),
                    ])
                    shade_x = int(fold)
                    shade_w = int(flap + 10)
                xf = QTransform()
                ok = QTransform.quadToQuad(src, dst, xf)
                back = a.flipped(Qt.Orientation.Horizontal) if a is not None and not a.isNull() else a
                if ok and back is not None and not back.isNull():
                    painter.fillRect(shade_x, 0, shade_w, int(h), QColor(0, 0, 0, int(90 * math.sin(p * math.pi))))
                    painter.save()
                    painter.setTransform(xf, True)
                    painter.setOpacity(0.82)
                    _draw(painter, back, 0, 0, w, h)
                    painter.restore()
                    painter.fillRect(int(fold) - 3, 0, 6, int(h), QColor(0, 0, 0, int(110 * math.sin(p * math.pi))))
        else:
            fold = h * (p if from_start else (1.0 - p))
            remain_a = (h - fold) if from_start else fold
            if remain_a > 0.5:
                if fe <= 0.0:
                    if from_start:
                        painter.setClipRect(0, int(fold), int(w), int(math.ceil(remain_a)))
                    else:
                        painter.setClipRect(0, 0, int(w), int(math.ceil(remain_a)))
                    _draw(painter, a, 0, 0, w, h)
                    painter.setClipping(False)
                elif from_start:
                    _stamp_feather(painter, a, w, h, 0.0, -1.0, -fold, fe * 2.0)
                else:
                    _stamp_feather(painter, a, w, h, 0.0, 1.0, fold, fe * 2.0)
            remain = fold if not from_start else (h - fold)
            flap = min(h * 0.42 * k, max(0.0, remain), h * 0.38)
            flap = flap * math.sin(p * math.pi)
            pinch = w * 0.12 * k * math.sin(p * math.pi)
            if flap >= 2.0:
                if from_start:
                    src = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, flap), QPointF(0, flap)])
                    dst = QPolygonF([
                        QPointF(0, fold),
                        QPointF(w, fold),
                        QPointF(w - pinch, fold - flap),
                        QPointF(pinch, fold - flap),
                    ])
                else:
                    src = QPolygonF([QPointF(0, h - flap), QPointF(w, h - flap), QPointF(w, h), QPointF(0, h)])
                    dst = QPolygonF([
                        QPointF(pinch, fold + flap),
                        QPointF(w - pinch, fold + flap),
                        QPointF(w, fold),
                        QPointF(0, fold),
                    ])
                xf = QTransform()
                ok = QTransform.quadToQuad(src, dst, xf)
                back = a.flipped(Qt.Orientation.Vertical) if a is not None and not a.isNull() else a
                if ok and back is not None and not back.isNull():
                    painter.fillRect(0, int(fold - flap if from_start else fold), int(w), int(flap + 10),
                                     QColor(0, 0, 0, int(90 * math.sin(p * math.pi))))
                    painter.save()
                    painter.setTransform(xf, True)
                    painter.setOpacity(0.82)
                    _draw(painter, back, 0, 0, w, h)
                    painter.restore()
                    painter.fillRect(0, int(fold) - 3, int(w), 6, QColor(0, 0, 0, int(110 * math.sin(p * math.pi))))
    finally:
        painter.end()
    return out


def cube_spin(a, b, t, w, h, *, direction="left", easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    p = _ease(t, easing)
    k = max(0.4, min(1.6, float(intensity)))
    fe = _feather_px(feather, w, h)
    out = _canvas(w, h, fill=QColor(8, 8, 10).rgb())
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        ang = p * 90.0
        d = str(direction or "left").lower()
        sign = 1.0 if d in ("left", "up", "top") else -1.0
        horiz = d not in ("up", "down", "top", "bottom")
        a_ang = sign * ang
        b_ang = sign * (ang - 90.0)

        def _face(img, deg, soften=False):
            if img is None or img.isNull():
                return
            rad = math.radians(deg)
            c = math.cos(rad)
            s = math.sin(rad)
            if abs(c) < 0.02:
                return
            f = max(w, h) * (1.55 - 0.45 * k)
            hw, hh = w / 2.0, h / 2.0
            src = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            pts = []
            for lx, ly in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                if horiz:
                    x2, y2, z2 = lx * c, ly, lx * s
                else:
                    x2, y2, z2 = lx, ly * c, ly * s
                denom = f + z2
                if abs(denom) < 1.0:
                    return
                pts.append(QPointF(hw + f * x2 / denom, hh + f * y2 / denom))
            xf = QTransform()
            ok = QTransform.quadToQuad(src, QPolygonF(pts), xf)
            if not ok:
                return
            if not soften:
                painter.save()
                painter.setTransform(xf, True)
                painter.setOpacity(max(0.35, abs(c)))
                _draw(painter, img, 0, 0, w, h)
                painter.restore()
                return
            layer = _canvas(w, h)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            try:
                lp.save()
                lp.setTransform(xf, True)
                lp.setOpacity(max(0.35, abs(c)))
                _draw(lp, img, 0, 0, w, h)
                lp.restore()
            finally:
                lp.end()
            if horiz:
                left_x = (pts[0].x() + pts[3].x()) * 0.5
                right_x = (pts[1].x() + pts[2].x()) * 0.5
                if abs(left_x - w / 2.0) <= abs(right_x - w / 2.0):
                    nx, ny, th = -1.0, 0.0, -left_x
                else:
                    nx, ny, th = 1.0, 0.0, right_x
            else:
                top_y = (pts[0].y() + pts[1].y()) * 0.5
                bot_y = (pts[2].y() + pts[3].y()) * 0.5
                if abs(top_y - h / 2.0) <= abs(bot_y - h / 2.0):
                    nx, ny, th = 0.0, -1.0, -top_y
                else:
                    nx, ny, th = 0.0, 1.0, bot_y
            mp = QPainter(layer)
            try:
                mp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                _paint_linear_mask(mp, nx, ny, th, fe * 2.0, w, h)
            finally:
                mp.end()
            painter.drawImage(0, 0, layer)

        soft = fe > 0.0
        if abs(math.cos(math.radians(a_ang))) >= abs(math.cos(math.radians(b_ang))):
            _face(b, b_ang, False)
            _face(a, a_ang, soft)
        else:
            _face(a, a_ang, False)
            _face(b, b_ang, soft)
    finally:
        painter.end()
    return out


# ── 光学 ──────────────────────────────────────────────

def flash_black(a, b, t, w, h, *, intensity=1.0, easing="linear", **_):
    """Short dip to black around the midpoint — not the same ramp as 黑场."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    k = max(0.2, min(2.0, float(intensity)))
    peak = math.exp(-((float(t) - 0.5) / 0.10) ** 2)
    src = a if t < 0.5 else b
    out = _copy_fit(src, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w / 2.0, h / 2.0
        sc = max(0.82, 1.0 - 0.12 * peak * k)
        painter.save()
        painter.translate(cx, cy)
        painter.scale(sc, sc)
        painter.translate(-cx, -cy)
        _draw(painter, src, 0, 0, w, h)
        painter.restore()
        painter.setOpacity(min(1.0, peak * k))
        painter.fillRect(0, 0, int(w), int(h), QColor(11, 11, 14))
    finally:
        painter.end()
    return out


def flash_white(a, b, t, w, h, *, intensity=1.0, easing="linear", **_):
    """Short white peak + overscale blowout — not the same ramp as 白场."""
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    k = max(0.2, min(2.0, float(intensity)))
    peak = math.exp(-((float(t) - 0.5) / 0.10) ** 2)
    src = a if t < 0.5 else b
    out = _copy_fit(src, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w / 2.0, h / 2.0
        sc = 1.0 + 0.14 * peak * k
        painter.save()
        painter.translate(cx, cy)
        painter.scale(sc, sc)
        painter.translate(-cx, -cy)
        _draw(painter, src, 0, 0, w, h)
        painter.restore()
        painter.setOpacity(min(1.0, peak * k))
        painter.fillRect(0, 0, int(w), int(h), QColor(255, 255, 255))
        if peak > 0.55:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.setOpacity(min(1.0, (peak - 0.55) * 1.2 * k))
            painter.fillRect(0, 0, int(w), int(h), QColor(255, 255, 255))
    finally:
        painter.end()
    return out


def exposure_dissolve(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        painter.setOpacity(1.0)
        _draw(painter, a, 0, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        blow = math.sin(p * math.pi) * 0.55 * k
        painter.setOpacity(blow)
        painter.fillRect(0, 0, int(w), int(h), QColor(255, 255, 255))
    finally:
        painter.end()
    return out


def bloom_dissolve(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    src = a if p < 0.5 else b
    out = _copy_fit(src, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        glow = math.sin(p * math.pi)
        painter.setOpacity(max(0.0, 1.0 - p) if p < 0.5 else p)
        # cheap bloom: draw scaled copies
        cx, cy = w / 2.0, h / 2.0
        for s, op in ((1.06, 0.18), (1.12, 0.12), (1.2, 0.08)):
            painter.save()
            painter.setOpacity(glow * op * k)
            painter.translate(cx, cy)
            painter.scale(s, s)
            painter.translate(-cx, -cy)
            _draw(painter, src, 0, 0, w, h)
            painter.restore()
        painter.setOpacity(max(0.0, 1.0 - p))
        _draw(painter, a, 0, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(glow * 0.28 * k)
        painter.fillRect(0, 0, int(w), int(h), QColor(255, 255, 230))
    finally:
        painter.end()
    return out


def rgb_split(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", direction="left", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.5, float(intensity)))
    # Offset starts at 0 with sin(pπ); spatial channel shift, no Plus overlay.
    sep = int(round(18 * k * math.sin(p * math.pi)))
    mixed = _copy_fit(a, w, h)
    mp = QPainter(mixed)
    try:
        mp.setOpacity(1.0)
        _draw(mp, a, 0, 0, w, h)
        mp.setOpacity(p)
        _draw(mp, b, 0, 0, w, h)
    finally:
        mp.end()
    if sep <= 0:
        return mixed
    dx, dy = _dir_vec(direction, sep, sep)
    return _channel_shift(mixed, int(round(dx)), int(round(dy * 0.3)))


# ── 模糊 ──────────────────────────────────────────────

def _channel_shift(img, dx, dy):
    """Shift R by +(dx,dy) and B by -(dx,dy). G stays. No additive mix."""
    if img is None or img.isNull() or (not dx and not dy):
        return img
    try:
        import numpy as np
    except Exception:
        return img
    conv = img.convertToFormat(img.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        return img
    arr = arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()
    out = arr.copy()
    if dx:
        out[:, :, 0] = np.roll(arr[:, :, 0], int(dx), axis=1)
        out[:, :, 2] = np.roll(arr[:, :, 2], -int(dx), axis=1)
    if dy:
        src_r = out[:, :, 0] if dx else arr[:, :, 0]
        src_b = out[:, :, 2] if dx else arr[:, :, 2]
        out[:, :, 0] = np.roll(src_r, int(dy), axis=0)
        out[:, :, 2] = np.roll(src_b, -int(dy), axis=0)
    shifted = type(img)(out.data, w, h, w * 4, conv.Format.Format_RGBA8888).copy()
    return shifted.convertToFormat(img.Format.Format_ARGB32_Premultiplied)


def _blur_qimage(img, radius):
    if img is None or img.isNull() or radius < 0.6:
        return img
    try:
        import cv2
        import numpy as np
    except Exception:
        return img
    conv = img.convertToFormat(img.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        return img
    arr = arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()
    k = int(max(1, radius)) * 2 + 1
    k = min(k, 31)
    blur = cv2.GaussianBlur(arr, (k, k), radius)
    out = type(img)(blur.data, w, h, w * 4, conv.Format.Format_RGBA8888).copy()
    return out.convertToFormat(img.Format.Format_ARGB32_Premultiplied)


def blur_directional(a, b, t, w, h, *, direction="left", intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    src = a if p < 0.5 else b
    streak = int(round(10 * k * math.sin(p * math.pi)))
    dx, dy = _dir_vec(direction, 1, 1)
    nx = 0 if abs(dx) < abs(dy) else (1 if dx > 0 else -1)
    ny = 0 if abs(dy) < abs(dx) else (1 if dy > 0 else -1)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        n = max(0, streak)
        if n >= 1:
            for i in range(n, 0, -1):
                painter.setOpacity((1.0 / n) * 0.85)
                _draw(painter, src, nx * i, ny * i, w, h)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    return out


def blur_radial(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    glow = math.sin(p * math.pi)
    src = a if p < 0.5 else b
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w / 2.0, h / 2.0
        n = max(2, int(4 * k))
        for i in range(n, 0, -1):
            s = 1.0 + 0.08 * k * glow * (i / float(n))
            painter.save()
            painter.setOpacity((1.0 / n) * 0.9)
            painter.translate(cx, cy)
            painter.scale(s, s)
            painter.translate(-cx, -cy)
            _draw(painter, src, 0, 0, w, h)
            painter.restore()
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    return out


def bokeh_dissolve(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    radius = 12.0 * k * math.sin(p * math.pi)
    a_b = _blur_qimage(_copy_fit(a, w, h), radius * (1.0 - p))
    b_b = _blur_qimage(_copy_fit(b, w, h), radius * p)
    out = _copy_fit(a_b, w, h)
    painter = QPainter(out)
    try:
        painter.setOpacity(1.0)
        _draw(painter, a_b, 0, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b_b, 0, 0, w, h)
    finally:
        painter.end()
    return out


# ── 数字感 ────────────────────────────────────────────

def glitch(a, b, t, w, h, *, intensity=1.0, easing="linear", direction="left", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.5, float(intensity)))
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        bands = max(6, int(10 * k))
        bh = max(1, int(h / bands))
        rng = _glitch_rng(p, w, h)
        src_mix = p
        for i in range(bands):
            y0 = i * bh
            hh = bh if i < bands - 1 else int(h) - y0
            jitter = int(((rng[i % len(rng)] / 255.0) - 0.5) * 40 * k * math.sin(p * math.pi))
            use_b = (i / float(bands)) < src_mix or (rng[(i + 3) % len(rng)] / 255.0) < p
            src = b if use_b else a
            painter.setClipRect(0, y0, int(w), hh)
            _draw(painter, src, jitter, 0, w, h)
        painter.setClipping(False)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(max(0.0, 1.0 - p) * 0.35)
        _draw(painter, a, 0, 0, w, h)
    finally:
        painter.end()
    return out


def _glitch_rng(p, w, h):
    # Deterministic, no import of random: hash of (p,w,h)
    seed = int(p * 1000) ^ int(w) ^ int(h)
    out = []
    x = seed * 1103515245 + 12345
    for _ in range(32):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append(x & 255)
    return out


def pixelate(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.5, float(intensity)))
    src = a if p < 0.5 else b
    # block size peaks at mid
    peak = math.sin(p * math.pi)
    block = max(1, int(round(1 + 31 * k * peak)))
    small_w = max(1, int(w) // block)
    small_h = max(1, int(h) // block)
    fitted = _copy_fit(src, w, h)
    tiny = fitted.scaled(small_w, small_h)
    pix = tiny.scaled(int(w), int(h))
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        painter.setOpacity(1.0)
        _draw(painter, pix, 0, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    return out


def scanlines(a, b, t, w, h, *, intensity=1.0, easing="ease_in_out", direction="down", feather=0.0, **_):
    hit = _endpoints(a, b, t, w, h)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    p = _ease(t, easing)
    k = max(0.2, min(2.0, float(intensity)))
    veil = math.sin(p * math.pi)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        d = str(direction or "down").lower()
        if d in ("up", "top"):
            y_cut = int(h * (1.0 - p))
            painter.setClipRect(0, 0, int(w), y_cut)
            _draw(painter, a, 0, 0, w, h)
            painter.setClipRect(0, y_cut, int(w), int(h) - y_cut)
            _draw(painter, b, 0, 0, w, h)
            scan_y = y_cut
        elif d in ("left",):
            x_cut = int(w * p)
            painter.setClipRect(0, 0, x_cut, int(h))
            _draw(painter, b, 0, 0, w, h)
            painter.setClipRect(x_cut, 0, int(w) - x_cut, int(h))
            _draw(painter, a, 0, 0, w, h)
            scan_y = None
            painter.setClipping(False)
            painter.fillRect(
                x_cut - 2, 0, 4, int(h),
                QColor(180, 220, 255, int(160 * k * veil)))
        else:
            y_cut = int(h * p)
            painter.setClipRect(0, 0, int(w), y_cut)
            _draw(painter, b, 0, 0, w, h)
            painter.setClipRect(0, y_cut, int(w), int(h) - y_cut)
            _draw(painter, a, 0, 0, w, h)
            scan_y = y_cut
        painter.setClipping(False)
        if scan_y is not None:
            painter.fillRect(
                0, scan_y - 2, int(w), 4,
                QColor(180, 220, 255, int(160 * k * veil)))
        painter.setOpacity(0.12 * k * veil)
        step = max(2, int(4 / max(0.5, k)))
        for y in range(0, int(h), step):
            painter.fillRect(0, y, int(w), 1, QColor(0, 0, 0, 80))
    finally:
        painter.end()
    fe = _feather_px(feather, w, h)
    if fe > 0.0 and 0.0 < float(t) < 1.0:
        d = str(direction or "down").lower()
        half = fe * 0.5
        if d in ("left",):
            cut = float(w) * p
            _soften_seam_strip(out, cut - half, 0, cut + half, h)
        elif d in ("up", "top"):
            cut = float(h) * (1.0 - p)
            _soften_seam_strip(out, 0, cut - half, w, cut + half)
        else:
            cut = float(h) * p
            _soften_seam_strip(out, 0, cut - half, w, cut + half)
    return out


# User-facing library entries (appended to STANDALONE_TRANSITION_LIBRARY).
EXTRA_TRANSITION_LIBRARY = [
    ("推出左", "push_out_left"),
    ("推出右", "push_out_right"),
    ("推出上", "push_out_up"),
    ("推出下", "push_out_down"),
    ("对推", "push_split"),
    ("突进", "smash_zoom"),
    ("拉远", "zoom_out_trans"),
    ("线性擦除", "wipe_angle"),
    ("圆形擦除", "wipe_circle"),
    ("时钟擦除", "wipe_clock"),
    ("百叶窗", "wipe_blinds"),
    ("旋转溶解", "rotate_dissolve"),
    ("翻页", "page_turn"),
    ("立方体旋转", "cube_spin"),
    ("黑闪", "flash_black"),
    ("曝光溶解", "exposure_dissolve"),
    ("光晕溢出", "bloom_dissolve"),
    ("色散", "rgb_split"),
    ("方向模糊", "blur_directional"),
    ("径向模糊", "blur_radial"),
    ("散景溶解", "bokeh_dissolve"),
    ("故障", "glitch"),
    ("像素化溶解", "pixelate"),
    ("扫描线", "scanlines"),
]


def _bind_dir(fn, direction):
    def inner(a, b, t, w, h, **p):
        p = dict(p)
        p["direction"] = direction
        return fn(a, b, t, w, h, **p)
    return inner


def push_slide(a, b, t, w, h, *, direction="left", easing="ease_in_out", intensity=1.0, feather=0.0, **_):
    """Legacy push when feather is 0; otherwise only the seam band is softened."""
    img = _legacy_push(a, b, t, w, h, direction)
    fe = _feather_px(feather, w, h)
    if fe <= 0.0 or not (0.0 < float(t) < 1.0):
        return img
    p = _legacy_smooth(t)
    name = _cardinal_name(direction)
    half = fe * 0.5
    if name == "left":
        seam = float(w) * (1.0 - p)
        _soften_seam_strip(img, seam - half, 0, seam + half, h)
    elif name == "right":
        seam = float(w) * p
        _soften_seam_strip(img, seam - half, 0, seam + half, h)
    elif name == "up":
        seam = float(h) * (1.0 - p)
        _mix_rect(img, b, 0, seam - half, w, fe, w, h)
    else:
        seam = float(h) * p
        _mix_rect(img, b, 0, seam - half, w, fe, w, h)
    return img


TRANSITION_FUNCS = {
    "push_left": _bind_dir(push_slide, "left"),
    "push_right": _bind_dir(push_slide, "right"),
    "push_up": _bind_dir(push_slide, "up"),
    "push_down": _bind_dir(push_slide, "down"),
    "push_out": push_out,
    "push_out_left": _bind_dir(push_out, "left"),
    "push_out_right": _bind_dir(push_out, "right"),
    "push_out_up": _bind_dir(push_out, "up"),
    "push_out_down": _bind_dir(push_out, "down"),
    "push_split": push_split,
    "smash_zoom": smash_zoom,
    "zoom_out_trans": zoom_out_trans,
    "wipe_angle": wipe_angle,
    "wipe_left": wipe_screen,
    "wipe_right": _bind_dir(wipe_cardinal, "right"),
    "wipe_up": _bind_dir(wipe_cardinal, "up"),
    "wipe_down": _bind_dir(wipe_cardinal, "down"),
    "wipe_circle": wipe_circle,
    "wipe_clock": wipe_clock,
    "wipe_blinds": wipe_blinds,
    "rotate_dissolve": rotate_dissolve,
    "page_turn": page_turn,
    "cube_spin": cube_spin,
    "flash_black": flash_black,
    "flash_white": flash_white,
    "exposure_dissolve": exposure_dissolve,
    "bloom_dissolve": bloom_dissolve,
    "rgb_split": rgb_split,
    "blur_directional": blur_directional,
    "blur_radial": blur_radial,
    "bokeh_dissolve": bokeh_dissolve,
    "glitch": glitch,
    "pixelate": pixelate,
    "scanlines": scanlines,
}


# Effects that are typically heavier than a 1080p 30fps slice (~33ms).
# Thumbs still run them (88×48); preview may skip the heavy path.
EXPORT_ONLY_PRESETS = set()


def apply_transition(preset, a, b, t, w, h, **params):
    fn = TRANSITION_FUNCS.get(str(preset or ""))
    if fn is None:
        return None
    params = {k: v for k, v in params.items() if v is not None}
    return fn(a, b, t, w, h, **params)
