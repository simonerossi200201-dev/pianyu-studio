# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture and bubble backgrounds. Torn paper edge is seeded noise on the path."""
from __future__ import annotations

import hashlib
import math
import os
import random

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush, QImage, QPainter, QPainterPath, QTransform,
)

from cutora.core.paths import TEXTURE_DIR
from cutora.render.textfx.layout import bg_panel_rect
from cutora.render.textfx.paint_fill import qcolor_hex
from cutora.render.textfx.spec import FxSpec, rgb_tuple

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
