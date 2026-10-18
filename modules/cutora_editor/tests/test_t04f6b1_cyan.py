# -*- coding: utf-8 -*-
"""T04f-6b-1 C: no saturated cyan blow-up on feathered paper / premul edges."""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

from cutora.render.textfx.raster import as_bgra, _feather_alpha


def _cyan_mask(bgra: np.ndarray) -> np.ndarray:
    b = bgra[..., 0].astype(np.int16)
    g = bgra[..., 1].astype(np.int16)
    r = bgra[..., 2].astype(np.int16)
    a = bgra[..., 3]
    return (a > 8) & (r < 130) & (g > 170) & (b > 170) & ((g + b) / 2 - r > 80)


def test_feather_alpha_does_not_blow_up_low_alpha_cyan():
    img = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    owned, bgra = as_bgra(img)
    bgra[16:48, 16:48, 0] = 210
    bgra[16:48, 16:48, 1] = 220
    bgra[16:48, 16:48, 2] = 230
    bgra[16:48, 16:48, 3] = 255
    bgra[20:24, 20:24, 0] = 3
    bgra[20:24, 20:24, 1] = 3
    bgra[20:24, 20:24, 2] = 0
    bgra[20:24, 20:24, 3] = 1
    out = _feather_alpha(owned, 0.6)
    _, ob = as_bgra(out)
    assert int(_cyan_mask(ob).sum()) == 0


def test_feather_alpha_zeros_tiny_alpha_instead_of_scaling():
    img = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    owned, bgra = as_bgra(img)
    bgra[8:24, 8:24, :] = (200, 210, 220, 255)
    bgra[10, 10] = (14, 219, 223, 1)
    out = _feather_alpha(owned, 0.6)
    _, ob = as_bgra(out)
    pix = ob[10, 10]
    assert int(pix[1]) < 40 and int(pix[0]) < 40
    assert int(_cyan_mask(ob).sum()) == 0
