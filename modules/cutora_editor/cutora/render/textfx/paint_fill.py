# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gradient / metal fills: 1024-stop float32 ramps, then QLinearGradient."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath

from cutora.render.textfx.ramps import (
    METAL_ANGLE, METAL_STOPS, mix_hex, sample_ramp,
)
from cutora.render.textfx.spec import (
    DEFAULT_EMPH, DEFAULT_FILL, FxSpec,
    emphasis_colors, emphasis_is_gradient, hex_with_alpha, rgba_tuple,
)


def qcolor_hex(hex_color: str, alpha: float | None = None) -> QColor:
    r, g, b, a = rgba_tuple(hex_color)
    if alpha is not None:
        a *= float(alpha)
    c = QColor(r, g, b)
    c.setAlpha(max(0, min(255, int(round(a * 255)))))
    return c


def gradient_ends(rect: QRectF, angle_deg: float) -> tuple[QPointF, QPointF]:
    """0° = left→right, 90° = top→bottom. 0° up would be metal 135=右下."""
    rad = math.radians(float(angle_deg))
    dx, dy = math.cos(rad), math.sin(rad)
    cx, cy = rect.center().x(), rect.center().y()
    corners = (
        (rect.left(), rect.top()), (rect.right(), rect.top()),
        (rect.left(), rect.bottom()), (rect.right(), rect.bottom()),
    )
    dots = [(x - cx) * dx + (y - cy) * dy for x, y in corners]
    t0, t1 = min(dots), max(dots)
    return (QPointF(cx + dx * t0, cy + dy * t0),
            QPointF(cx + dx * t1, cy + dy * t1))


def ramp_brush(rect: QRectF, angle: float, stops: list[tuple[float, str]]) -> QBrush:
    p0, p1 = gradient_ends(rect, angle)
    grad = QLinearGradient(p0, p1)
    ramp = sample_ramp(stops)
    last = len(ramp) - 1
    for i, row in enumerate(ramp):
        color = QColor.fromRgbF(
            float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        grad.setColorAt(i / last, color)
    return QBrush(grad)


def _metal_angle(spec: FxSpec) -> float:
    if abs(float(spec.fill.angle) - 90.0) < 0.01:
        return METAL_ANGLE
    return float(spec.fill.angle)


def emphasis_brush(spec: FxSpec, rect: QRectF, *, alpha: float = 1.0,
                   solid: str | None = None) -> QBrush:
    """Current-word / keyword emphasis fill. Gradient when type=gradient."""
    if emphasis_is_gradient(spec):
        colors = emphasis_colors(spec)
        n = min(5, len(colors))
        stops = [
            (i / float(n - 1), hex_with_alpha(colors[i], alpha))
            for i in range(n)
        ]
        return ramp_brush(rect, float(spec.emphasis.angle), stops)
    hex_c = solid or spec.emphasis.color or DEFAULT_EMPH
    return QBrush(qcolor_hex(hex_c, alpha))


def fill_brush(spec: FxSpec, rect: QRectF, solid: str | None = None) -> QBrush:
    if solid:
        return QBrush(qcolor_hex(solid))
    if spec.fill.type == "metal":
        stops = METAL_STOPS.get(spec.fill.metal) or METAL_STOPS["gold"]
        return ramp_brush(rect, _metal_angle(spec), stops)
    if spec.fill.type == "gradient":
        colors = [c for c in (spec.fill.colors or []) if c]
        if not colors:
            colors = [DEFAULT_FILL]
        if len(colors) < 2:
            return QBrush(qcolor_hex(colors[0]))
        n = min(5, len(colors))
        stops = [(i / float(n - 1), colors[i]) for i in range(n)]
        return ramp_brush(rect, float(spec.fill.angle), stops)
    return QBrush(qcolor_hex((spec.fill.colors or [DEFAULT_FILL])[0]))


def paint_metal_highlight(painter: QPainter, path: QPainterPath, rect: QRectF,
                          spec: FxSpec) -> None:
    p0, p1 = gradient_ends(rect, _metal_angle(spec))
    grad = QLinearGradient(p0, p1)
    clear = QColor(255, 255, 255, 0)
    hi = QColor(255, 255, 255, int(0.35 * 255))
    grad.setColorAt(0.00, clear)
    grad.setColorAt(0.40, clear)
    grad.setColorAt(0.44, hi)
    grad.setColorAt(0.48, clear)
    grad.setColorAt(1.00, clear)
    painter.fillPath(path, QBrush(grad))


def neon_face_hex(spec: FxSpec) -> str | None:
    if spec.glow.mode != "neon" or not spec.glow.live():
        return None
    if spec.fill.type != "solid":
        return None
    given = (spec.fill.colors or [DEFAULT_FILL])[0].upper()
    if given != DEFAULT_FILL.upper():
        return given
    return mix_hex(spec.glow.color, "#FFFFFF", 0.8)


def face_fill_hex(spec: FxSpec) -> str:
    neon = neon_face_hex(spec)
    if neon:
        return neon
    if spec.fill.type == "metal":
        stops = METAL_STOPS.get(spec.fill.metal) or METAL_STOPS["gold"]
        return stops[2][1]
    return (spec.fill.colors or [DEFAULT_FILL])[0]
