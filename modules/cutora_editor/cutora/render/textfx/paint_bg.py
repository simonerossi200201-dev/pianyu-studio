# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture and bubble backgrounds. Torn paper edge is seeded noise on the path."""
from __future__ import annotations

import hashlib
import math
import os
import random

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen, QPolygonF, QTransform,
)

from cutora.core.paths import TEXTURE_DIR
from cutora.render.textfx.layout import bg_panel_rect
from cutora.render.textfx.paint_fill import qcolor_hex
from cutora.render.textfx.spec import DEFAULT_FILL, FxSpec, rgb_tuple

_TEX: dict[str, QImage] = {}


def load_texture(name: str) -> QImage | None:
    hit = _TEX.get(name)
    if hit is not None:
        return hit
    path = os.path.join(TEXTURE_DIR, "%s.png" % name)
    if not os.path.isfile(path):
        return None
    img = QImage(path)
    if img.isNull():
        return None
    _TEX[name] = img
    return img


def tint_texture(src: QImage, tint: str, alpha: float) -> QImage:
    from cutora.render.textfx.raster import as_bgra
    owned, bgra = as_bgra(src)
    out = QImage(owned.size(), QImage.Format.Format_ARGB32_Premultiplied)
    dest, dview = as_bgra(out)
    r, g, b = rgb_tuple(tint)
    a = max(0.0, min(1.0, float(alpha)))
    src_f = bgra.astype("float32")
    gray = src_f[..., 0] * (1.0 / 255.0)
    dview[..., 0] = (b * gray).astype("uint8")
    dview[..., 1] = (g * gray).astype("uint8")
    dview[..., 2] = (r * gray).astype("uint8")
    dview[..., 3] = (bgra[..., 3].astype("float32") * a).astype("uint8")
    _ = dest
    return out


def _round_rect_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    rad = max(0.0, min(radius, min(rect.width(), rect.height()) / 2.0))
    path.addRoundedRect(rect, rad, rad)
    return path


def _seed_for(layout, spec: FxSpec) -> int:
    raw = hashlib.blake2s(str(layout.seg_id).encode("utf-8"), digest_size=4)
    return int.from_bytes(raw.digest(), "little") ^ int(spec.anim.seed)


def _periodic_1d(n: int, wavelength_samples: float, rng: random.Random) -> list[float]:
    """Closed-loop cosine-interpolated 1D value noise, n samples."""
    n_ctrl = max(4, int(round(n / max(wavelength_samples, 1.0))))
    ctrl = [rng.uniform(-1.0, 1.0) for _ in range(n_ctrl)]
    out = []
    for i in range(n):
        t = (i / float(n)) * n_ctrl
        i0 = int(math.floor(t)) % n_ctrl
        i1 = (i0 + 1) % n_ctrl
        f = t - math.floor(t)
        f = 0.5 - 0.5 * math.cos(math.pi * f)
        out.append(ctrl[i0] * (1.0 - f) + ctrl[i1] * f)
    return out


def torn_offset_series(n: int, spacing: float, em: float, seed: int) -> list[float]:
    """Multi-frequency outward displacement in px. Coarse 1.5 em / 0.08 em + fine 0.15 em / 0.02 em."""
    rng = random.Random(seed)
    coarse_wl = max(1.0, 1.5 * em / max(spacing, 0.5))
    fine_wl = max(1.0, 0.15 * em / max(spacing, 0.5))
    coarse = _periodic_1d(n, coarse_wl, rng)
    fine = _periodic_1d(n, fine_wl, rng)
    return [c * 0.08 * em + f * 0.02 * em for c, f in zip(coarse, fine)]


def torn_rect_path(rect: QRectF, radius: float, em: float, seed: int) -> QPainterPath:
    """Offset the rounded-rect outline by two-scale seeded noise (not a sawtooth)."""
    rad = max(0.0, min(radius, min(rect.width(), rect.height()) / 2.0))
    spacing = max(1.25, 0.04 * em)
    pts = _round_rect_samples(rect, rad, spacing)
    offsets = torn_offset_series(len(pts), spacing, em, seed)
    path = QPainterPath()
    for i, (x, y, nx, ny) in enumerate(pts):
        mag = offsets[i]
        px, py = x + nx * mag, y + ny * mag
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.closeSubpath()
    return path


def _round_rect_samples(rect: QRectF, rad: float, spacing: float):
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    rad = min(rad, w / 2.0, h / 2.0)
    segs = [
        ("h", x + rad, y, w - 2 * rad, 0.0, 0.0, -1.0),
        ("c", x + w - rad, y + rad, 0.0, 270.0, 0.0, 0.0),
        ("v", x + w, y + rad, h - 2 * rad, 0.0, 1.0, 0.0),
        ("c", x + w - rad, y + h - rad, 0.0, 0.0, 0.0, 0.0),
        ("h", x + w - rad, y + h, -(w - 2 * rad), 0.0, 0.0, 1.0),
        ("c", x + rad, y + h - rad, 0.0, 90.0, 0.0, 0.0),
        ("v", x, y + h - rad, -(h - 2 * rad), 0.0, -1.0, 0.0),
        ("c", x + rad, y + rad, 0.0, 180.0, 0.0, 0.0),
    ]
    out = []
    for kind, x0, y0, length, start_deg, nx, ny in segs:
        if kind == "h" or kind == "v":
            dist = abs(length)
            n = max(1, int(dist / spacing))
            for i in range(n):
                t = i / float(n)
                out.append((x0 + (length if kind == "h" else 0.0) * t,
                            y0 + (length if kind == "v" else 0.0) * t,
                            nx, ny))
        else:
            n = max(4, int((math.pi * rad / 2.0) / spacing))
            cx, cy = x0, y0
            for i in range(n):
                ang = math.radians(start_deg + 90.0 * i / float(n))
                px = cx + rad * math.cos(ang)
                py = cy + rad * math.sin(ang)
                out.append((px, py, math.cos(ang), math.sin(ang)))
    return out


def _fill_path_texture(painter: QPainter, path: QPainterPath, spec: FxSpec) -> None:
    tex = load_texture(spec.bg.texture)
    if tex is None or tex.isNull():
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor_hex(spec.bg.texture_tint, spec.bg.alpha))
        painter.drawPath(path)
        return
    tinted = tint_texture(tex, spec.bg.texture_tint, spec.bg.alpha)
    brush = QBrush(tinted)
    brush.setTransform(QTransform())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    painter.drawPath(path)


def paint_texture_bg(painter: QPainter, layout, spec: FxSpec) -> None:
    rect = bg_panel_rect(
        layout.text_x, layout.text_y, layout.text_w, layout.text_h,
        spec, layout.em)
    rect.translate(-layout.origin[0], -layout.origin[1])
    radius = spec.bg.radius_em * layout.em
    if spec.bg.texture == "paper":
        path = torn_rect_path(rect, radius, layout.em, _seed_for(layout, spec))
    else:
        path = _round_rect_path(rect, radius)
    _fill_path_texture(painter, path, spec)


def paint_bubble_bg(painter: QPainter, layout, spec: FxSpec) -> None:
    radius = spec.bg.radius_em * layout.em
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor_hex(spec.bg.color, spec.bg.alpha))
    for line in layout.lines:
        rect = bg_panel_rect(line.x, line.y, line.w, line.h, spec, layout.em)
        rect.translate(-layout.origin[0], -layout.origin[1])
        painter.drawPath(_round_rect_path(rect, radius))


def _ornament_color(spec: FxSpec, alpha: float = 1.0) -> QColor:
    raw = str(getattr(spec.bg, "ornament_color", "") or "").strip()
    if not raw:
        cols = spec.fill.colors if spec.fill and spec.fill.colors else []
        raw = cols[0] if cols else DEFAULT_FILL
    return qcolor_hex(raw, alpha)


def _draw_diamond(painter: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
    poly = QPolygonF([
        QPointF(cx, cy - r),
        QPointF(cx + r, cy),
        QPointF(cx, cy + r),
        QPointF(cx - r, cy),
    ])
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(poly)
    painter.restore()


def paint_bg_ornament(painter: QPainter, layout, spec: FxSpec) -> None:
    """Paint high-taste decorative line/frame elements onto the background panel."""
    orn = str(getattr(spec.bg, "ornament", "none") or "none")
    if orn == "none" or not orn:
        return
    em = max(8.0, float(layout.em))
    rect = bg_panel_rect(
        layout.text_x, layout.text_y, layout.text_w, layout.text_h,
        spec, em)
    rect.translate(-layout.origin[0], -layout.origin[1])
    w, h = rect.width(), rect.height()
    if w < 4.0 or h < 4.0:
        return
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    cx, cy = rect.center().x(), rect.center().y()
    rad = spec.bg.radius_em * em

    thin = max(1.0, 0.032 * em)
    med = max(1.4, 0.055 * em)
    thick = max(2.2, 0.11 * em)

    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if orn == "editorial_lines":
            # Top & bottom main rules + subtle inner hairlines + center diamond
            pen_main = QPen(_ornament_color(spec, 0.86), med)
            pen_main.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_main)
            painter.drawLine(QLineF(x0, y0 + med * 0.5, x1, y0 + med * 0.5))
            painter.drawLine(QLineF(x0, y1 - med * 0.5, x1, y1 - med * 0.5))

            inset = max(2.5, 0.095 * em)
            pen_sub = QPen(_ornament_color(spec, 0.36), thin)
            painter.setPen(pen_sub)
            painter.drawLine(QLineF(x0 + inset * 1.5, y0 + inset, x1 - inset * 1.5, y0 + inset))
            painter.drawLine(QLineF(x0 + inset * 1.5, y1 - inset, x1 - inset * 1.5, y1 - inset))

            dr = max(2.2, 0.085 * em)
            _draw_diamond(painter, x0 + dr, y0 + med * 0.5, dr * 0.75, _ornament_color(spec, 0.90))
            _draw_diamond(painter, x1 - dr, y0 + med * 0.5, dr * 0.75, _ornament_color(spec, 0.90))
            _draw_diamond(painter, cx, y1 - med * 0.5, dr * 0.85, _ornament_color(spec, 0.92))

        elif orn == "accent_left":
            # Sleek vertical pill bar on left + tapered horizontal bottom guide line
            bar_w = max(2.8, 0.115 * em)
            pad_v = max(2.0, h * 0.14)
            bar_rect = QRectF(x0 + bar_w * 0.45, y0 + pad_v, bar_w, max(4.0, h - 2.0 * pad_v))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ornament_color(spec, 0.94))
            painter.drawRoundedRect(bar_rect, bar_w * 0.5, bar_w * 0.5)

            grad = QLinearGradient(QPointF(bar_rect.right() + 0.08 * em, y1), QPointF(x1, y1))
            grad.setColorAt(0.0, _ornament_color(spec, 0.55))
            grad.setColorAt(0.65, _ornament_color(spec, 0.22))
            grad.setColorAt(1.0, _ornament_color(spec, 0.0))
            pen_line = QPen(QBrush(grad), thin)
            painter.setPen(pen_line)
            painter.drawLine(QLineF(bar_rect.right() + 0.08 * em, y1 - thin, x1 - 0.1 * em, y1 - thin))

        elif orn == "accent_both":
            # Symmetrical left & right vertical accent bars + fine outer frame
            bar_w = max(2.4, 0.095 * em)
            pad_v = max(2.0, h * 0.16)
            bh = max(4.0, h - 2.0 * pad_v)
            painter.setPen(QPen(_ornament_color(spec, 0.26), thin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(_round_rect_path(rect.adjusted(thin, thin, -thin, -thin), rad))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ornament_color(spec, 0.90))
            painter.drawRoundedRect(QRectF(x0 + bar_w * 0.5, y0 + pad_v, bar_w, bh), bar_w * 0.5, bar_w * 0.5)
            painter.drawRoundedRect(QRectF(x1 - bar_w * 1.5, y0 + pad_v, bar_w, bh), bar_w * 0.5, bar_w * 0.5)

        elif orn == "corner_brackets":
            # Architectural L-corner brackets + whisper-fine inner border
            inset = max(1.5, 0.05 * em)
            painter.setPen(QPen(_ornament_color(spec, 0.20), thin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))

            arm_x = min(0.42 * em, w * 0.22)
            arm_y = min(0.42 * em, h * 0.34)
            pen_b = QPen(_ornament_color(spec, 0.90), med)
            pen_b.setCapStyle(Qt.PenCapStyle.SquareCap)
            pen_b.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen_b)
            # Top-Left
            painter.drawLine(QLineF(x0, y0, x0 + arm_x, y0))
            painter.drawLine(QLineF(x0, y0, x0, y0 + arm_y))
            # Top-Right
            painter.drawLine(QLineF(x1 - arm_x, y0, x1, y0))
            painter.drawLine(QLineF(x1, y0, x1, y0 + arm_y))
            # Bottom-Left
            painter.drawLine(QLineF(x0, y1 - arm_y, x0, y1))
            painter.drawLine(QLineF(x0, y1, x0 + arm_x, y1))
            # Bottom-Right
            painter.drawLine(QLineF(x1 - arm_x, y1, x1, y1))
            painter.drawLine(QLineF(x1, y1 - arm_y, x1, y1))

        elif orn == "jp_brackets":
            # Double-line East-Asian editorial quote brackets 「 」
            arm_x = min(0.56 * em, w * 0.26)
            arm_y = min(0.48 * em, h * 0.42)
            step = max(2.2, 0.095 * em)
            pen_o = QPen(_ornament_color(spec, 0.92), med)
            pen_o.setCapStyle(Qt.PenCapStyle.SquareCap)
            painter.setPen(pen_o)
            # Top-left outer 「
            painter.drawLine(QLineF(x0, y0, x0 + arm_x, y0))
            painter.drawLine(QLineF(x0, y0, x0, y0 + arm_y))
            # Bottom-right outer 」
            painter.drawLine(QLineF(x1 - arm_x, y1, x1, y1))
            painter.drawLine(QLineF(x1, y1 - arm_y, x1, y1))

            pen_i = QPen(_ornament_color(spec, 0.48), thin)
            pen_i.setCapStyle(Qt.PenCapStyle.SquareCap)
            painter.setPen(pen_i)
            # Top-left inner
            painter.drawLine(QLineF(x0 + step, y0 + step, x0 + arm_x * 0.72, y0 + step))
            painter.drawLine(QLineF(x0 + step, y0 + step, x0 + step, y0 + arm_y * 0.72))
            # Bottom-right inner
            painter.drawLine(QLineF(x1 - arm_x * 0.72, y1 - step, x1 - step, y1 - step))
            painter.drawLine(QLineF(x1 - step, y1 - arm_y * 0.72, x1 - step, y1 - step))

        elif orn == "double_frame":
            # Classical French double-line border with side diamond emblems
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_ornament_color(spec, 0.72), thin * 1.15))
            r_out = rect.adjusted(thin, thin, -thin, -thin)
            painter.drawPath(_round_rect_path(r_out, rad))

            gap = max(2.5, 0.105 * em)
            r_in = r_out.adjusted(gap, gap, -gap, -gap)
            if r_in.width() > 4 and r_in.height() > 4:
                painter.setPen(QPen(_ornament_color(spec, 0.36), thin))
                painter.drawPath(_round_rect_path(r_in, max(0.0, rad - gap)))

            dr = max(2.4, 0.09 * em)
            _draw_diamond(painter, x0 + gap * 0.6, cy, dr, _ornament_color(spec, 0.90))
            _draw_diamond(painter, x1 - gap * 0.6, cy, dr, _ornament_color(spec, 0.90))

        elif orn == "capsule_ring":
            # Pill double ring + side rivet dots
            cap_rad = min(w, h) * 0.5
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r_out = rect.adjusted(med * 0.5, med * 0.5, -med * 0.5, -med * 0.5)
            painter.setPen(QPen(_ornament_color(spec, 0.76), med * 0.9))
            painter.drawPath(_round_rect_path(r_out, cap_rad))

            gap = max(2.2, 0.09 * em)
            r_in = r_out.adjusted(gap, gap, -gap, -gap)
            if r_in.width() > 4 and r_in.height() > 4:
                painter.setPen(QPen(_ornament_color(spec, 0.30), thin))
                painter.drawPath(_round_rect_path(r_in, max(0.0, cap_rad - gap)))

            dot_r = max(1.8, 0.06 * em)
            dot_off = max(dot_r * 2.5, 0.24 * em)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ornament_color(spec, 0.88))
            painter.drawEllipse(QPointF(x0 + dot_off, cy), dot_r, dot_r)
            painter.drawEllipse(QPointF(x1 - dot_off, cy), dot_r, dot_r)

        elif orn == "bottom_rule":
            # Tapered horizontal rule at bottom + fine upper echo + center diamond
            y_bar = y1 - max(2.0, 0.07 * em)
            grad = QLinearGradient(QPointF(x0, y_bar), QPointF(x1, y_bar))
            grad.setColorAt(0.0, _ornament_color(spec, 0.04))
            grad.setColorAt(0.20, _ornament_color(spec, 0.85))
            grad.setColorAt(0.50, _ornament_color(spec, 0.96))
            grad.setColorAt(0.80, _ornament_color(spec, 0.85))
            grad.setColorAt(1.0, _ornament_color(spec, 0.04))
            painter.setPen(QPen(QBrush(grad), med * 1.15))
            painter.drawLine(QLineF(x0, y_bar, x1, y_bar))

            y_sub = y_bar - max(2.2, 0.085 * em)
            painter.setPen(QPen(_ornament_color(spec, 0.32), thin))
            painter.drawLine(QLineF(x0 + w * 0.14, y_sub, x1 - w * 0.14, y_sub))

            dr = max(2.4, 0.095 * em)
            _draw_diamond(painter, cx, y_bar, dr, _ornament_color(spec, 0.98))

        elif orn == "top_tag_line":
            # Editorial top overline with bold left header bar & scale ticks
            y_top = y0 + med * 0.6
            painter.setPen(QPen(_ornament_color(spec, 0.42), thin))
            painter.drawLine(QLineF(x0, y_top, x1, y_top))
            painter.drawLine(QLineF(x0, y1 - thin, x1, y1 - thin))

            pen_hi = QPen(_ornament_color(spec, 0.95), thick * 0.85)
            pen_hi.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen_hi)
            painter.drawLine(QLineF(x0, y_top, x0 + min(w * 0.30, 1.8 * em), y_top))

            # Subtle scale ticks on top-right
            tick_h = max(2.5, 0.09 * em)
            tick_step = max(3.5, 0.14 * em)
            painter.setPen(QPen(_ornament_color(spec, 0.65), thin))
            for i in range(4):
                tx = x1 - i * tick_step - thin
                if tx > x0 + w * 0.45:
                    painter.drawLine(QLineF(tx, y_top, tx, y_top + tick_h))

        elif orn == "quote_watermark":
            # Subtle border + geometric double quote marks in top-left corner
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_ornament_color(spec, 0.36), thin))
            painter.drawPath(_round_rect_path(rect.adjusted(thin, thin, -thin, -thin), rad))

            # Stylized quote marks (two comma-like geometric blocks)
            q_s = max(4.0, 0.22 * em)
            qx = x0 + max(4.0, 0.22 * em)
            qy = y0 + max(4.0, 0.18 * em)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ornament_color(spec, 0.34))
            for k in range(2):
                bx = qx + k * (q_s * 1.25)
                painter.drawRoundedRect(QRectF(bx, qy, q_s * 0.85, q_s * 0.85), q_s * 0.2, q_s * 0.2)
                tail = QPolygonF([
                    QPointF(bx + q_s * 0.35, qy + q_s * 0.80),
                    QPointF(bx + q_s * 0.85, qy + q_s * 0.80),
                    QPointF(bx + q_s * 0.45, qy + q_s * 1.45),
                    QPointF(bx + q_s * 0.15, qy + q_s * 1.45),
                ])
                painter.drawPolygon(tail)

        elif orn == "crosshair_hud":
            # Minimalist viewfinder crosshairs (+) at 4 corners + fine connecting lines
            arm = max(2.5, 0.11 * em)
            gap = arm * 1.45
            painter.setPen(QPen(_ornament_color(spec, 0.24), thin))
            if w > gap * 2.5:
                painter.drawLine(QLineF(x0 + gap, y0, x1 - gap, y0))
                painter.drawLine(QLineF(x0 + gap, y1, x1 - gap, y1))
            if h > gap * 2.5:
                painter.drawLine(QLineF(x0, y0 + gap, x0, y1 - gap))
                painter.drawLine(QLineF(x1, y0 + gap, x1, y1 - gap))

            pen_c = QPen(_ornament_color(spec, 0.88), thin * 1.25)
            painter.setPen(pen_c)
            for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                painter.drawLine(QLineF(px - arm, py, px + arm, py))
                painter.drawLine(QLineF(px, py - arm, px, py + arm))

        elif orn == "art_deco":
            # Stepped Art-Deco geometric corners + outer frame
            inset = max(2.0, 0.08 * em)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_ornament_color(spec, 0.42), thin))
            painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))

            step = min(0.32 * em, w * 0.16, h * 0.28)
            pen_d = QPen(_ornament_color(spec, 0.88), med * 0.9)
            painter.setPen(pen_d)
            for sx, sy, dx, dy in (
                (x0, y0, 1, 1), (x1, y0, -1, 1),
                (x0, y1, 1, -1), (x1, y1, -1, -1),
            ):
                p = QPainterPath()
                p.moveTo(sx, sy + dy * step)
                p.lineTo(sx + dx * step * 0.45, sy + dy * step)
                p.lineTo(sx + dx * step * 0.45, sy + dy * step * 0.45)
                p.lineTo(sx + dx * step, sy + dy * step * 0.45)
                p.lineTo(sx + dx * step, sy)
                painter.drawPath(p)

        elif orn == "wave_underline":
            # Smooth sinusoidal wave underline + subtle baseline
            y_wave = y1 - max(2.5, 0.10 * em)
            wl = max(8.0, 0.52 * em)
            amp = max(1.2, 0.042 * em)
            path = QPainterPath()
            n_steps = max(16, int((x1 - x0) / 2.5))
            for i in range(n_steps + 1):
                u = i / float(n_steps)
                px = x0 + u * (x1 - x0)
                # Taper amplitude at ends
                env = math.sin(math.pi * u) ** 0.6
                py = y_wave + math.sin((px - x0) * (2.0 * math.pi / wl)) * amp * env
                if i == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            pen_w = QPen(_ornament_color(spec, 0.88), med)
            pen_w.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen_w)
            painter.drawPath(path)

        elif orn == "side_wings":
            # Cinema emblem frame with horizontal wing lines & diamonds on left/right
            wing = min(0.45 * em, w * 0.14)
            inner = rect.adjusted(wing, thin, -wing, -thin)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_ornament_color(spec, 0.42), thin))
            painter.drawPath(_round_rect_path(inner, rad))

            pen_w = QPen(_ornament_color(spec, 0.88), med)
            pen_w.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_w)
            painter.drawLine(QLineF(x0, cy, inner.left() + wing * 0.35, cy))
            painter.drawLine(QLineF(inner.right() - wing * 0.35, cy, x1, cy))
            dr = max(2.2, 0.085 * em)
            _draw_diamond(painter, x0 + dr, cy, dr * 0.85, _ornament_color(spec, 0.92))
            _draw_diamond(painter, x1 - dr, cy, dr * 0.85, _ornament_color(spec, 0.92))
    finally:
        painter.restore()

