# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt-free color ramps for gradient / metal (float32, 1024 stops)."""
from __future__ import annotations

import numpy as np

from cutora.render.textfx.spec import is_pure_black, rgb_tuple, rgba_tuple

RAMP_N = 1024
METAL_ANGLE = 100.0
METAL_STOPS: dict[str, list[tuple[float, str]]] = {
    "gold": [
        (0.00, "#7A5A12"), (0.30, "#F5D77A"), (0.48, "#FFF2C2"),
        (0.55, "#D9B24C"), (0.80, "#8C6A1C"), (1.00, "#F0CC6A"),
    ],
    "silver": [
        (0.00, "#5C6470"), (0.30, "#E9EDF2"), (0.48, "#FFFFFF"),
        (0.55, "#C5CBD3"), (0.80, "#6E7681"), (1.00, "#DDE2E8"),
    ],
    "chrome": [
        (0.00, "#1E2A3A"), (0.35, "#C9D6E4"), (0.50, "#FFFFFF"),
        (0.52, "#3A4656"), (0.80, "#8FA0B4"), (1.00, "#E8EEF5"),
    ],
    "rose": [
        (0.00, "#7A3B3B"), (0.30, "#F4C2B8"), (0.48, "#FFF0EA"),
        (0.55, "#D89A8C"), (0.80, "#8C4E44"), (1.00, "#F1BCAF"),
    ],
}


def mix_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    ra, ga, ba = rgb_tuple(a)
    rb, gb, bb = rgb_tuple(b)
    r = int(round(ra * (1.0 - t) + rb * t))
    g = int(round(ga * (1.0 - t) + gb * t))
    bch = int(round(ba * (1.0 - t) + bb * t))
    return "#%02X%02X%02X" % (r, g, bch)


def sample_ramp(stops: list[tuple[float, str]], n: int = RAMP_N) -> np.ndarray:
    """Nx4 float32 straight RGBA in 0–1. Interpolation is premultiplied."""
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    rgba = np.array([rgba_tuple(s[1]) for s in stops], dtype=np.float32)
    rgb = rgba[:, :3] / 255.0
    alpha = rgba[:, 3]
    premul = rgb * alpha[:, None]
    xs = np.linspace(0.0, 1.0, n, dtype=np.float32)
    idx = np.searchsorted(pos, xs, side="right") - 1
    idx = np.clip(idx, 0, len(pos) - 2).astype(np.int32)
    span = (pos[idx + 1] - pos[idx]).astype(np.float32)
    span = np.where(span < 1e-8, 1.0, span)
    t = ((xs - pos[idx]) / span).astype(np.float32)
    t = np.clip(t, 0.0, 1.0)
    one = (1.0 - t)[:, None]
    tt = t[:, None]
    premul_l = premul[idx] * one + premul[idx + 1] * tt
    a_l = (alpha[idx] * (1.0 - t) + alpha[idx + 1] * t).astype(np.float32)
    a_safe = np.maximum(a_l, 1e-8).astype(np.float32)
    out = np.empty((n, 4), dtype=np.float32)
    out[:, :3] = (premul_l / a_safe[:, None]).astype(np.float32)
    out[:, 3] = a_l
    out[a_l <= 1e-8, :3] = 0.0
    return out


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    hi, lo = max(rf, gf, bf), min(rf, gf, bf)
    l = (hi + lo) / 2.0
    if hi - lo < 1e-8:
        return 0.0, 0.0, l
    s = (hi - lo) / (1.0 - abs(2.0 * l - 1.0))
    if hi == rf:
        h = (gf - bf) / (hi - lo) % 6.0
    elif hi == gf:
        h = (bf - rf) / (hi - lo) + 2.0
    else:
        h = (rf - gf) / (hi - lo) + 4.0
    return h * 60.0, s, l


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    hp = (h % 360.0) / 60.0
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    m = l - c / 2.0
    sext = {
        0: (c, x, 0.0), 1: (x, c, 0.0), 2: (0.0, c, x),
        3: (0.0, x, c), 4: (x, 0.0, c), 5: (c, 0.0, x),
    }[min(5, int(hp))]
    r, g, b = (int(round((v + m) * 255)) for v in sext)
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


def darken_for_extrude(hex_color: str) -> str:
    r, g, b = rgb_tuple(hex_color)
    h, s, l = _rgb_to_hsl(r, g, b)
    l = max(0.18, min(1.0, l * 0.55))
    s = min(1.0, s * 1.1)
    r, g, b = _hsl_to_rgb(h, s, l)
    out = "#%02X%02X%02X" % (r, g, b)
    if is_pure_black(out):
        return "#3A2A18"
    return out
