# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thick-glass edge: SDF, refraction remap, bevel, inner step, sheen.

Geometry (d, n) is cached by (w, h, radius_q). Per-frame work is remap + composite.
Weak blur / remap run only on the four edge strips (width + 2 px).
"""
from __future__ import annotations

from collections import OrderedDict

import cv2
import numpy as np

from cutora.render.textfx.spec import rgb_tuple

_SDF_CACHE: OrderedDict = OrderedDict()
_SDF_MAX = 24
_MAP_CACHE: OrderedDict = OrderedDict()
_MAP_MAX = 16
_DARK = np.array([0x33, 0x1A, 0x0B], dtype=np.float32)  # BGR of #0B1A33
# Absolute rim blur. Not a fraction of glass_sigma_em (appendix E σ/3 was wrong).
# 0.04 em @ typical 1080p subtitle ≈ 2–4 px. Cap so a title-sized contact-sheet
# em (~130) does not smear the 12 px test stripes in the 0–0.6 w band.
GLASS_RIM_SIGMA_EM = 0.04
GLASS_RIM_SIGMA_PX_MAX = 1.2
# Backdrop-only half-res. Geometry shade maps always run at the caller's size.
GLASS_HALFRES_PIXELS = 400000


def rim_sigma_px(em: float) -> float:
    return float(max(0.6, min(GLASS_RIM_SIGMA_PX_MAX, GLASS_RIM_SIGMA_EM * float(em))))


def sdf_key(w: int, h: int, radius: float) -> tuple:
    return (int(w), int(h), int(round(float(radius) * 4.0)))


def sdf_from_mask(mask_u8: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """d = L2 distance to edge (inside). n = inward unit normal.

    Pad a 1 px zero border so straight edges of a full-bleed rounded rect
    still have exterior zeros. Without this, distanceTransform only sees
    the corner cutouts and the bevel collapses to a 1 px stroke.
    """
    h, w = mask_u8.shape[:2]
    pad = np.zeros((h + 2, w + 2), dtype=np.uint8)
    pad[1:-1, 1:-1] = mask_u8
    d_pad = cv2.distanceTransform(pad, cv2.DIST_L2, 5)
    d = d_pad[1:-1, 1:-1].astype(np.float32)
    gy, gx = np.gradient(d)
    mag = np.sqrt(gx * gx + gy * gy) + 1e-6
    return d, gx / mag, gy / mag


def cached_sdf(mask_u8: np.ndarray, key: tuple):
    hit = _SDF_CACHE.get(key)
    if hit is not None:
        _SDF_CACHE.move_to_end(key)
        return hit
    d, nx, ny = sdf_from_mask(mask_u8)
    h, w = d.shape
    xx = np.arange(w, dtype=np.float32)[None, :]
    yy = np.arange(h, dtype=np.float32)[:, None]
    packed = (d, nx, ny, xx, yy)
    _SDF_CACHE[key] = packed
    while len(_SDF_CACHE) > _SDF_MAX:
        _SDF_CACHE.popitem(last=False)
    return packed


def clear_sdf_cache() -> None:
    _SDF_CACHE.clear()
    _MAP_CACHE.clear()


def glass_maps_key(
    w: int, h: int, radius: float, width: float,
    bevel: float, sheen: float, light_deg: float, em: float,
) -> tuple:
    return (
        int(w), int(h),
        int(round(float(radius) * 4.0)),
        int(round(float(width) * 4.0)),
        int(round(float(bevel) * 200.0)),
        int(round(float(sheen) * 200.0)),
        int(round(float(light_deg))),
        int(round(float(em) * 4.0)),
    )


def cached_glass_maps(
    mask_u8: np.ndarray,
    em: float,
    thickness_em: float,
    bevel: float,
    sheen: float,
    light_deg: float,
    radius: float,
):
    """Bevel / step / sheen / k_refr depend only on geometry + light.

    Cached so play frames only remap the rim onto the live backdrop.
    """
    h, w = mask_u8.shape[:2]
    width = max(0.0, float(thickness_em) * float(em))
    key = glass_maps_key(w, h, radius, width, bevel, sheen, light_deg, em)
    hit = _MAP_CACHE.get(key)
    if hit is not None:
        _MAP_CACHE.move_to_end(key)
        return hit
    d, nx, ny, xx, yy = cached_sdf(mask_u8, sdf_key(w, h, radius))
    k = edge_weight(d, width)
    k_refr = np.clip(
        1.0 - np.maximum(d - 0.5 * width, 0.0) / max(width, 1e-4), 0.0, 1.0)
    rad = np.radians(float(light_deg))
    sdot = nx * float(np.cos(rad)) + ny * (-float(np.sin(rad)))
    bv = float(bevel)
    light_a = (np.clip(sdot, 0.0, 1.0) * k * bv * 0.55).astype(np.float32)
    dark_a = (np.clip(-sdot, 0.0, 1.0) * k * bv * 0.35).astype(np.float32)
    sigma = max(1.0, min(1.5, 0.012 * float(em)))
    step_k = np.exp(-((d - width) / sigma) ** 2).astype(np.float32)
    sa = np.zeros((h, w), dtype=np.float32)
    if sheen > 0:
        ang = np.radians(20.0)
        proj = xx * float(np.cos(ang)) + yy * float(np.sin(ang))
        pmin, pmax = float(proj.min()), float(proj.max())
        u = (proj - pmin) / (pmax - pmin + 1e-6)
        t = np.clip((u - 0.15) / 0.40, 0.0, 1.0)
        raw = np.clip((1.0 - t) * (float(sheen) * 0.10), 0.0, 1.0)
        sa = np.where((u >= 0.15) & (u <= 0.55), raw, 0.0).astype(np.float32)
    # Four strips must cover k_refr (holds to 0.5w, dies at 1.5w).
    band = int(np.ceil(width * 1.5 + 2.0))
    packed = (nx, ny, k_refr, light_a, dark_a, step_k, sa, band, width)
    _MAP_CACHE[key] = packed
    while len(_MAP_CACHE) > _MAP_MAX:
        _MAP_CACHE.popitem(last=False)
    return packed


def k_debug(mask_u8: np.ndarray, em: float, thickness_em: float, radius: float):
    """Return (k, width_px, k>0.1 uint8 mask, median band width)."""
    h, w = mask_u8.shape[:2]
    d, nx, ny, xx, yy = cached_sdf(mask_u8, sdf_key(w, h, radius))
    _ = nx, ny, xx, yy
    width = max(0.0, float(thickness_em) * float(em))
    k = edge_weight(d, width)
    ring = (k > 0.1).astype(np.uint8) * 255
    return k, width, ring, k_band_width_px(k)


def edge_weight(d: np.ndarray, width: float) -> np.ndarray:
    w = max(1e-4, float(width))
    t = np.clip(1.0 - d / w, 0.0, 1.0)
    return t * t


def k_band_width_px(k: np.ndarray, threshold: float = 0.1) -> float:
    """Median inward width (px) of the k>threshold ring along the four sides."""
    ring = k > float(threshold)
    h, w = ring.shape
    if h < 4 or w < 4:
        return 0.0
    widths = []
    mid_x = slice(w // 4, 3 * w // 4)
    mid_y = slice(h // 4, 3 * h // 4)
    for col in range(mid_x.start, mid_x.stop):
        col_top = ring[:, col]
        if col_top[0] or col_top.any():
            run = 0
            for v in col_top:
                if v:
                    run += 1
                else:
                    break
            if run:
                widths.append(run)
        col_bot = ring[::-1, col]
        run = 0
        for v in col_bot:
            if v:
                run += 1
            else:
                break
        if run:
            widths.append(run)
    for row in range(mid_y.start, mid_y.stop):
        for arr in (ring[row], ring[row, ::-1]):
            run = 0
            for v in arr:
                if v:
                    run += 1
                else:
                    break
            if run:
                widths.append(run)
    return float(np.median(widths)) if widths else 0.0


def _edge_regions(h: int, w: int, band: int) -> list[tuple[slice, slice]]:
    band = max(1, min(int(band), max(1, h // 2), max(1, w // 2)))
    return [
        (slice(0, band), slice(0, w)),
        (slice(h - band, h), slice(0, w)),
        (slice(0, h), slice(0, band)),
        (slice(0, h), slice(w - band, w)),
    ]


def blur_edge_bands(src_bgr: np.ndarray, sigma: float, band: int) -> np.ndarray:
    """Gaussian-blur only the four edge strips; interior is copied."""
    h, w = src_bgr.shape[:2]
    out = src_bgr.copy()
    sigma = max(0.3, float(sigma))
    pad = int(np.ceil(3.0 * sigma))
    for ys, xs in _edge_regions(h, w, band):
        y0, y1 = ys.start, ys.stop
        x0, x1 = xs.start, xs.stop
        cy0, cy1 = max(0, y0 - pad), min(h, y1 + pad)
        cx0, cx1 = max(0, x0 - pad), min(w, x1 + pad)
        crop = np.ascontiguousarray(src_bgr[cy0:cy1, cx0:cx1])
        blurred = cv2.GaussianBlur(crop, (0, 0), sigmaX=sigma)
        oy, ox = y0 - cy0, x0 - cx0
        out[y0:y1, x0:x1] = blurred[oy:oy + (y1 - y0), ox:ox + (x1 - x0)]
    return out


def refract_edge_bands(
    strong: np.ndarray,
    weak: np.ndarray,
    nx: np.ndarray,
    ny: np.ndarray,
    k: np.ndarray,
    disp: float,
    band: int,
) -> np.ndarray:
    """Remap only the four edge strips. Interior stays on *strong*."""
    h, w = strong.shape[:2]
    base = strong.astype(np.float32)
    if disp <= 0 or weak is None:
        return base
    for ys, xs in _edge_regions(h, w, band):
        y0, y1 = ys.start, ys.stop
        x0, x1 = xs.start, xs.stop
        grid_x, grid_y = np.meshgrid(
            np.arange(x0, x1, dtype=np.float32),
            np.arange(y0, y1, dtype=np.float32),
        )
        kk = k[ys, xs]
        map_x = (grid_x + nx[ys, xs] * kk * disp).astype(np.float32)
        map_y = (grid_y + ny[ys, xs] * kk * disp).astype(np.float32)
        refr = cv2.remap(
            weak, map_x, map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        a = kk[..., None].astype(np.float32)
        src = strong[ys, xs].astype(np.float32)
        base[ys, xs] = src * (1.0 - a) + refr.astype(np.float32) * a
    return base


def apply_tint(bgr: np.ndarray, color: str, alpha: float) -> np.ndarray:
    r, g, b = rgb_tuple(color)
    a = max(0.0, min(1.0, float(alpha)))
    src = bgr.astype(np.float32)
    tint = np.array([float(b), float(g), float(r)], dtype=np.float32)
    return src * (1.0 - a) + tint * a


def _apply_shade_maps(
    base: np.ndarray,
    light_a: np.ndarray,
    dark_a: np.ndarray,
    step_k: np.ndarray,
    sa: np.ndarray,
    band: int,
) -> None:
    """Bevel + inner-step on the four rim strips; sheen on its nonzero band."""
    h, w = base.shape[:2]
    for ys, xs in _edge_regions(h, w, band):
        la = light_a[ys, xs]
        da = dark_a[ys, xs]
        sl = np.clip(base[ys, xs] + la[..., None] * 230.0, 0, 255)
        sl *= (1.0 - da)[..., None]
        sl += _DARK * da[..., None]
        sl = np.clip(sl - step_k[ys, xs][..., None] * 12.0, 0, 255)
        base[ys, xs] = sl
    if sa is None or float(np.max(sa)) <= 0.0:
        return
    rows = np.any(sa > 1e-6, axis=1)
    cols = np.any(sa > 1e-6, axis=0)
    y0 = int(np.argmax(rows))
    y1 = h - int(np.argmax(rows[::-1]))
    x0 = int(np.argmax(cols))
    x1 = w - int(np.argmax(cols[::-1]))
    sa_r = sa[y0:y1, x0:x1]
    for c in range(3):
        ch = base[y0:y1, x0:x1, c]
        ch += sa_r * (255.0 - ch)


def _refract_tint(
    strong_bgr: np.ndarray,
    weak_bgr: np.ndarray | None,
    mask_u8: np.ndarray,
    em: float,
    thickness_em: float,
    refraction: float,
    bevel: float,
    sheen: float,
    light_deg: float,
    tint_color: str,
    tint_alpha: float,
    radius: float,
) -> np.ndarray:
    """Refract edge bands + tint. Returns float32, no shade maps."""
    nx, ny, k_refr, _la, _da, _sk, _sa, band, width = cached_glass_maps(
        mask_u8, em, thickness_em, bevel, sheen, light_deg, radius)
    if weak_bgr is None:
        base = strong_bgr.astype(np.float32, copy=True)
    else:
        disp = float(refraction) * width * 1.0
        base = refract_edge_bands(
            strong_bgr, weak_bgr, nx, ny, k_refr, disp, band)
    ta = max(0.0, min(1.0, float(tint_alpha)))
    r, g, b = rgb_tuple(tint_color)
    tint = np.array([float(b), float(g), float(r)], dtype=np.float32)
    ta_map = (ta * (1.0 - 0.75 * k_refr)).astype(np.float32)
    keep = (1.0 - ta_map)
    for c in range(3):
        ch = base[:, :, c]
        ch *= keep
        ch += tint[c] * ta_map
    return base


def compose_thick_base(
    strong_bgr: np.ndarray,
    weak_bgr: np.ndarray | None,
    mask_u8: np.ndarray,
    em: float,
    thickness_em: float,
    refraction: float,
    bevel: float,
    sheen: float,
    light_deg: float,
    tint_color: str,
    tint_alpha: float,
    radius: float,
) -> np.ndarray:
    """Backdrop path: refract + tint at *strong*'s res (half-res if huge).

    Geometry shade maps are applied later at target resolution.
    """
    h, w = strong_bgr.shape[:2]
    if h * w > GLASS_HALFRES_PIXELS and h >= 8 and w >= 8:
        ws, hs = max(1, w // 2), max(1, h // 2)
        strong_s = cv2.resize(strong_bgr, (ws, hs), interpolation=cv2.INTER_AREA)
        weak_s = (
            None if weak_bgr is None
            else cv2.resize(weak_bgr, (ws, hs), interpolation=cv2.INTER_AREA))
        mask_s = cv2.resize(mask_u8, (ws, hs), interpolation=cv2.INTER_NEAREST)
        small = _refract_tint(
            strong_s, weak_s, mask_s, float(em) * 0.5, thickness_em,
            refraction, bevel, sheen, light_deg, tint_color, tint_alpha,
            float(radius) * 0.5)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return _refract_tint(
        strong_bgr, weak_bgr, mask_u8, em, thickness_em, refraction,
        bevel, sheen, light_deg, tint_color, tint_alpha, radius)


def shade_thick_bgr(
    base: np.ndarray,
    mask_u8: np.ndarray,
    em: float,
    thickness_em: float,
    bevel: float,
    sheen: float,
    light_deg: float,
    radius: float,
) -> np.ndarray:
    """Apply cached bevel / inner-step / sheen at *base*'s resolution."""
    if base.dtype != np.float32 or not base.flags.writeable:
        out = np.array(base, dtype=np.float32, copy=True)
    else:
        out = base
    _nx, _ny, _k, light_a, dark_a, step_k, sa, band, _w = cached_glass_maps(
        mask_u8, em, thickness_em, bevel, sheen, light_deg, radius)
    _apply_shade_maps(out, light_a, dark_a, step_k, sa, band)
    return np.clip(out, 0, 255).astype(np.uint8)
