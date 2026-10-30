# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Four-edge alpha fade. Same function for subtitle bg and overlays.

Cap raise and center-offset live only in ``edge_fade_mask``. The three
schemas (bg.fade / overlay fade_* / video fade_*) just change slider and
clamp ceilings.
"""
from __future__ import annotations

import numpy as np

FADE_KEYS = ("fade_top", "fade_bottom", "fade_left", "fade_right")
FADE_OFFSET_KEYS = (
    "fade_top_offset", "fade_bottom_offset",
    "fade_left_offset", "fade_right_offset",
)
FADE_CAP = 1.0
OFFSET_CAP = 1.0
# Four edges at 1.0 multiply to 0.5**4 at center. Useful range is ~0.5–0.8.
FADE_HINT = "实用区间约 0.5–0.8；四边都到 1.0 时中心接近全透明。"


def _smoothstep(t: np.ndarray) -> np.ndarray:
    return t * t * (3.0 - 2.0 * t)


def _ramp(n_pix: int, frac: float, offset: float = 0.0) -> np.ndarray:
    """Translate a fixed-width smoothstep band.

    Width stays ``frac * n_pix``. Positive *offset* moves the band inward
    (center shifts by ``offset × n_pix`` along the inward normal). The
    vacated outer strip keeps ramp-start alpha 0. Negative offset moves
    the band outward; the part past the box is clipped.

    A zero width is a hard crop at the offset: pixels outside the cut are
    0, pixels inside stay 1. Width 0 and offset 0 is a no-op (all 1).
    """
    start = float(offset) * float(n_pix)
    width = float(frac) * float(n_pix)
    idx = np.arange(n_pix, dtype=np.float32)
    if width <= 1e-8:
        return (idx + 1e-3 >= start).astype(np.float32)
    n = max(1.0, width)
    t = np.clip((idx - start) / n, 0.0, 1.0)
    return _smoothstep(t)


def offsets_active(offsets) -> bool:
    """True when any edge offset is far enough from 0 to crop."""
    if not offsets:
        return False
    for raw in offsets:
        try:
            if abs(float(raw)) > 1e-8:
                return True
        except (TypeError, ValueError):
            continue
    return False


def edge_fade_mask(
    h: int, w: int,
    top: float, bottom: float, left: float, right: float,
    top_offset: float = 0.0,
    bottom_offset: float = 0.0,
    left_offset: float = 0.0,
    right_offset: float = 0.0,
) -> np.ndarray:
    """HxW float32. Overlap is multiply (``f *= _ramp``), the running kernel.

    left=0.3 → left 30% of width ramps alpha 0→1 (smoothstep) when offset=0.
    """
    f = np.ones((h, w), dtype=np.float32)
    if w < 1 or h < 1:
        return f
    left = max(0.0, min(1.0, float(left)))
    right = max(0.0, min(1.0, float(right)))
    top = max(0.0, min(1.0, float(top)))
    bottom = max(0.0, min(1.0, float(bottom)))
    left_offset = max(-OFFSET_CAP, min(OFFSET_CAP, float(left_offset)))
    right_offset = max(-OFFSET_CAP, min(OFFSET_CAP, float(right_offset)))
    top_offset = max(-OFFSET_CAP, min(OFFSET_CAP, float(top_offset)))
    bottom_offset = max(-OFFSET_CAP, min(OFFSET_CAP, float(bottom_offset)))
    if left > 1e-8 or abs(left_offset) > 1e-8:
        f *= _ramp(w, left, left_offset)[None, :]
    if right > 1e-8 or abs(right_offset) > 1e-8:
        f *= _ramp(w, right, right_offset)[None, ::-1]
    if top > 1e-8 or abs(top_offset) > 1e-8:
        f *= _ramp(h, top, top_offset)[:, None]
    if bottom > 1e-8 or abs(bottom_offset) > 1e-8:
        f *= _ramp(h, bottom, bottom_offset)[::-1, None]
    return f


def _read_fade(obj, key: str, cap: float) -> float:
    if obj is None:
        return 0.0
    if isinstance(obj, dict):
        raw = obj.get(key, 0.0)
    else:
        raw = getattr(obj, key, 0.0)
    if raw is None:
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(float(cap), val))


def _read_offset(obj, key: str) -> float:
    if obj is None:
        return 0.0
    if isinstance(obj, dict):
        raw = obj.get(key, 0.0)
    else:
        raw = getattr(obj, key, 0.0)
    if raw is None:
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(-OFFSET_CAP, min(OFFSET_CAP, val))


def fade_tuple(obj, cap: float = FADE_CAP) -> tuple[float, float, float, float]:
    """Missing field == 0.0. Cap is 1.0 for overlays and subtitle bg.fade."""
    return (
        _read_fade(obj, "fade_top", cap),
        _read_fade(obj, "fade_bottom", cap),
        _read_fade(obj, "fade_left", cap),
        _read_fade(obj, "fade_right", cap),
    )


def fade_offset_tuple(obj) -> tuple[float, float, float, float]:
    """Missing field == 0.0. Range −1.0…1.0. 0 is today's ramp (rule A)."""
    return (
        _read_offset(obj, "fade_top_offset"),
        _read_offset(obj, "fade_bottom_offset"),
        _read_offset(obj, "fade_left_offset"),
        _read_offset(obj, "fade_right_offset"),
    )


def fade_active(obj, cap: float = FADE_CAP) -> bool:
    """A zero fade width still counts when an edge offset would hard-crop."""
    if max(fade_tuple(obj, cap=cap)) > 0.0:
        return True
    return offsets_active(fade_offset_tuple(obj))


def write_fade(obj, top: float, bottom: float, left: float, right: float,
               cap: float = FADE_CAP,
               offsets: tuple[float, float, float, float] | None = None,
               ) -> tuple[float, float, float, float]:
    vals = (
        max(0.0, min(cap, float(top))),
        max(0.0, min(cap, float(bottom))),
        max(0.0, min(cap, float(left))),
        max(0.0, min(cap, float(right))),
    )
    for key, val in zip(FADE_KEYS, vals):
        if isinstance(obj, dict):
            obj[key] = val
        else:
            setattr(obj, key, val)
    if offsets is not None:
        off = tuple(
            max(-OFFSET_CAP, min(OFFSET_CAP, float(v))) for v in offsets)
        for key, val in zip(FADE_OFFSET_KEYS, off):
            if isinstance(obj, dict):
                obj[key] = val
            else:
                setattr(obj, key, val)
    return vals


def _clamp_fade(v: float) -> float:
    return max(0.0, min(FADE_CAP, float(v)))


def _clamp_offset(v: float) -> float:
    return max(-OFFSET_CAP, min(OFFSET_CAP, float(v)))


def _runs(idx: np.ndarray):
    """[3,4,5,9,10] -> [(3,6),(9,11)]：连续下标合并成半开区间。"""
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) != 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks] + 1, [idx[-1] + 1]))
    return list(zip(starts.tolist(), ends.tolist()))


def _mul_region(bgra: np.ndarray, mask: np.ndarray) -> None:
    # mask ∈ [0, 1]、像素 ∈ [0, 255] → 乘积不会越界，原来的 np.clip 是空操作；截断取整方式不变
    scaled = bgra.astype(np.float32) * mask[..., None]
    bgra[:] = scaled.astype(np.uint8)


def _apply_mask(bgra: np.ndarray, mask: np.ndarray, plan=None) -> None:
    """逐像素与原实现完全一致，但只处理 mask < 1 的行 / 列（mask == 1 的像素乘 1.0 截断后不变）。

    导出时上边缘 21% 渐隐的整幅覆盖层，原来每帧对 1080×1920×4 全部做浮点乘法（约 14 ms/帧），
    现在只算渐隐带。``plan`` = (行区间, 列区间)，由缓存提供，省掉每帧再扫一遍 mask。
    """
    if plan is None:
        plan = _mask_plan(mask)
    row_runs, col_runs = plan
    h = mask.shape[0]
    covered = np.zeros(h, dtype=bool)
    for r0, r1 in row_runs:
        _mul_region(bgra[r0:r1], mask[r0:r1])
        covered[r0:r1] = True
    if col_runs:
        rest = np.flatnonzero(~covered)
        for r0, r1 in _runs(rest):
            for c0, c1 in col_runs:
                _mul_region(bgra[r0:r1, c0:c1], mask[r0:r1, c0:c1])


def _mask_plan(mask: np.ndarray, rfac: np.ndarray | None = None, cfac: np.ndarray | None = None):
    """要处理的行区间 + 列区间。mask = 行因子 × 列因子：行因子 < 1 的行整行处理；
    其余行只处理列因子 < 1 的列。两个因子都是 1 的像素 mask 恰好是 1.0，乘了也不变。"""
    if rfac is None or cfac is None:
        rows = np.flatnonzero(mask.min(axis=1) < 1.0)
        cols = np.flatnonzero(mask.min(axis=0) < 1.0) if rows.size == 0 else np.arange(mask.shape[1])
        return _runs(rows), _runs(cols)
    return _runs(np.flatnonzero(rfac < 1.0)), _runs(np.flatnonzero(cfac < 1.0))


_MASK_CACHE: dict = {}
_MASK_CACHE_MAX = 32


def _cached_mask(h, w, top, bottom, left, right, toff, boff, loff, roff):
    key = (h, w, top, bottom, left, right, toff, boff, loff, roff)
    hit = _MASK_CACHE.get(key)
    if hit is None:
        m = edge_fade_mask(h, w, top, bottom, left, right,
                           top_offset=toff, bottom_offset=boff,
                           left_offset=loff, right_offset=roff)
        m.setflags(write=False)
        rfac = edge_fade_mask(h, 1, top, bottom, 0.0, 0.0, top_offset=toff, bottom_offset=boff)[:, 0]
        cfac = edge_fade_mask(1, w, 0.0, 0.0, left, right, left_offset=loff, right_offset=roff)[0]
        hit = (m, _mask_plan(m, rfac, cfac))
        if len(_MASK_CACHE) >= _MASK_CACHE_MAX:
            _MASK_CACHE.pop(next(iter(_MASK_CACHE)))
        _MASK_CACHE[key] = hit
    return hit


def apply_fade_bgra(
    bgra: np.ndarray,
    top: float, bottom: float, left: float, right: float,
    boxes=None,
    offsets: tuple[float, float, float, float] | None = None,
) -> None:
    """In-place premul BGRA. Multiplies all four channels by the ramp.

    *boxes* is an optional iterable of (x, y, w, h) in image pixels. Each
    box gets its own ramp from that box's size (offset is per-box, not ROI).
    None / empty = whole image.
    """
    top, bottom, left, right = (
        _clamp_fade(top), _clamp_fade(bottom),
        _clamp_fade(left), _clamp_fade(right),
    )
    if offsets is None:
        toff = boff = loff = roff = 0.0
    else:
        toff, boff, loff, roff = (_clamp_offset(v) for v in offsets)
    if max(top, bottom, left, right) <= 0 and not offsets_active(
            (toff, boff, loff, roff)):
        return
    h, w = bgra.shape[:2]
    if not boxes:
        m, plan = _cached_mask(h, w, top, bottom, left, right, toff, boff, loff, roff)
        _apply_mask(bgra, m, plan)
        return
    for x0, y0, bw, bh in boxes:
        x0 = int(x0)
        y0 = int(y0)
        x1 = min(w, x0 + int(bw))
        y1 = min(h, y0 + int(bh))
        x0 = max(0, x0)
        y0 = max(0, y0)
        if x1 <= x0 or y1 <= y0:
            continue
        sub = bgra[y0:y1, x0:x1]
        m, plan = _cached_mask(sub.shape[0], sub.shape[1], top, bottom, left, right,
                               toff, boff, loff, roff)
        _apply_mask(sub, m, plan)


def apply_edge_fade(
    img, top: float, bottom: float, left: float, right: float,
    boxes=None,
    offsets: tuple[float, float, float, float] | None = None,
):
    """QImage in, QImage out. Never mutates *img*.

    No-op only when every ramp width is 0 and every offset is 0.
    A zero width with a nonzero offset hard-crops that edge.
    """
    offs = (0.0, 0.0, 0.0, 0.0) if offsets is None else offsets
    if max(top, bottom, left, right) <= 0 and not offsets_active(offs):
        return img
    from PySide6.QtGui import QImage
    from cutora.render.textfx.raster import as_bgra
    fmt = QImage.Format.Format_ARGB32_Premultiplied
    owned = img.copy() if img.format() == fmt else img.convertToFormat(fmt)
    _kept, bgra = as_bgra(owned)
    apply_fade_bgra(
        bgra, top, bottom, left, right, boxes=boxes, offsets=offsets)
    return owned
