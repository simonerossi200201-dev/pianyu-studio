# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""3D extrude: 1 px offset layers, sides 35–50% darker, never pure black."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath

from cutora.render.textfx.paint_fill import qcolor_hex
from cutora.render.textfx.ramps import darken_for_extrude, mix_hex
from cutora.render.textfx.spec import FxSpec, is_pure_black


def _lift(hex_color: str) -> str:
    if not hex_color or is_pure_black(hex_color):
        return "#3A2A18"
    return hex_color


def paint_extrude(
    painter: QPainter,
    paths: list[QPainterPath],
    layout,
    spec: FxSpec,
    face_hex: str,
) -> None:
    depth = spec.extrude.depth_em * layout.em
    if depth < 0.5 or not paths:
        return
    n = max(1, int(round(depth)))
    rad = math.radians(float(spec.extrude.angle))
    # 0° = up, 90° = right, 135° = down-right (附录 A).
    dx = math.sin(rad)
    dy = -math.cos(rad)
    color = _lift(spec.extrude.color or darken_for_extrude(face_hex))
    far = _lift(mix_hex(color, "#141A26", 0.15))
    painter.save()
    try:
        painter.translate(-layout.origin[0], -layout.origin[1])
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(n, 0, -1):
            t = i / float(n)
            shade = mix_hex(color, far, t)
            brush = QBrush(qcolor_hex(_lift(shade), 1.0))
            ox, oy = dx * i, dy * i
            for path in paths:
                painter.fillPath(path.translated(QPointF(ox, oy)), brush)
    finally:
        painter.restore()
