# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview edge fade that only touches the ramp bands.

``apply_edge_fade`` multiplies every pixel, including the center where the
mask is exactly 1. On a 720×1280 overlay that is most of a preview frame.
Same smoothstep, same float32 multiply, same uint8 cast. Offsets and
overlapping bands fall back to ``apply_edge_fade`` so the pixels stay equal.
Export does not call this.
"""
from __future__ import annotations

import math

import numpy as np


def _nband(n_pix: int, frac: float) -> int:
    width = float(frac) * float(n_pix)
    if width <= 1e-8:
        return 0
    return int(math.ceil(width - 1e-9))


def _write(sub: np.ndarray, mask: np.ndarray) -> None:
    scaled = sub.astype(np.float32) * mask
    np.clip(scaled, 0, 255, out=scaled)
    sub[:] = scaled.astype(np.uint8)


def fade_qimage_fast(img, obj):
    """QImage in, QImage out. Pixel-identical to ``apply_edge_fade``."""
    from cutora.render.textfx.fade import (
        _ramp,
        apply_edge_fade,
        fade_offset_tuple,
        fade_tuple,
        offsets_active,
    )

    top, bottom, left, right = fade_tuple(obj)
    offs = fade_offset_tuple(obj)
    if max(top, bottom, left, right) <= 0.0 and not offsets_active(offs):
        return img
    if offsets_active(offs):
        return apply_edge_fade(img, top, bottom, left, right, offsets=offs)
    from PySide6.QtGui import QImage
    from cutora.render.textfx.raster import as_bgra

    fmt = QImage.Format.Format_ARGB32_Premultiplied
    owned = img.copy() if img.format() == fmt else img.convertToFormat(fmt)
    _kept, bgra = as_bgra(owned)
    h, w = bgra.shape[:2]
    if h < 1 or w < 1:
        return owned
    n_top = _nband(h, top)
    n_bottom = _nband(h, bottom)
    n_left = _nband(w, left)
    n_right = _nband(w, right)
    if n_top + n_bottom > h or n_left + n_right > w:
        return apply_edge_fade(img, top, bottom, left, right, offsets=offs)
    ramp_l = _ramp(w, left, 0.0) if n_left else None
    ramp_r = _ramp(w, right, 0.0)[::-1] if n_right else None
    ramp_t = _ramp(h, top, 0.0) if n_top else None
    ramp_b = _ramp(h, bottom, 0.0)[::-1] if n_bottom else None
    hm = np.ones(w, dtype=np.float32)
    if ramp_l is not None:
        hm[:n_left] *= ramp_l[:n_left]
    if ramp_r is not None:
        hm[w - n_right:] *= ramp_r[w - n_right:]
    y0 = n_top
    y1 = h - n_bottom
    if n_top:
        sub = bgra[:n_top]
        mask = hm[None, :] * ramp_t[:n_top, None]
        _write(sub, mask[:, :, None])
    if n_bottom:
        sub = bgra[h - n_bottom:]
        mask = hm[None, :] * ramp_b[h - n_bottom:, None]
        _write(sub, mask[:, :, None])
    if y1 > y0 and n_left:
        _write(bgra[y0:y1, :n_left], ramp_l[:n_left][None, :, None])
    if y1 > y0 and n_right:
        _write(bgra[y0:y1, w - n_right:], ramp_r[w - n_right:][None, :, None])
    return owned
