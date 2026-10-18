# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview hit testing through transparent pixels. Does not touch the blit path."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

HIT_ALPHA_MIN = 8
MASK_DIV = 4


def downsample_alpha(img: QImage, div: int = MASK_DIV) -> tuple[np.ndarray, int, int]:
    """Return (uint8 mask h/div×w/div, src_w, src_h). Empty image → 1×1 zero."""
    if img is None or img.isNull() or img.width() < 1 or img.height() < 1:
        return np.zeros((1, 1), dtype=np.uint8), 1, 1
    src_w, src_h = int(img.width()), int(img.height())
    mw = max(1, src_w // max(1, int(div)))
    mh = max(1, src_h // max(1, int(div)))
    scaled = img.scaled(mw, mh)
    from cutora.render.textfx.raster import as_bgra
    owned, bgra = as_bgra(scaled)
    _ = owned
    return np.ascontiguousarray(bgra[..., 3].copy()), src_w, src_h


def video_to_local(vx: float, vy: float, rect: QRectF, rotation_deg: float = 0.0,
                   flip_x: bool = False, flip_y: bool = False) -> tuple[float, float]:
    """Inverse of painter translate-to-center / rotate / flip. Local origin = rect top-left."""
    cx = float(rect.center().x())
    cy = float(rect.center().y())
    dx = float(vx) - cx
    dy = float(vy) - cy
    rad = -math.radians(float(rotation_deg or 0.0))
    c, s = math.cos(rad), math.sin(rad)
    lx = c * dx - s * dy
    ly = s * dx + c * dy
    if flip_x:
        lx = -lx
    if flip_y:
        ly = -ly
    return lx + float(rect.width()) / 2.0, ly + float(rect.height()) / 2.0


def sample_alpha(mask: np.ndarray, src_w: int, src_h: int, lx: float, ly: float) -> int:
    if mask is None or src_w <= 0 or src_h <= 0:
        return 0
    if lx < 0 or ly < 0 or lx >= src_w or ly >= src_h:
        return 0
    mh, mw = int(mask.shape[0]), int(mask.shape[1])
    mx = min(mw - 1, max(0, int(lx * mw / float(src_w))))
    my = min(mh - 1, max(0, int(ly * mh / float(src_h))))
    return int(mask[my, mx])


def hits_record(record: dict, vx: float, vy: float, *, alt_bbox: bool = False) -> bool:
    rect = record.get("rect")
    if rect is None or not isinstance(rect, QRectF) or rect.isNull():
        return False
    if record.get("locked"):            # Q152-G：锁定的层在画布上点不中
        return False
    if alt_bbox:
        return bool(rect.contains(vx, vy))
    mask = record.get("alpha_mask")
    if mask is None:
        return bool(rect.contains(vx, vy))
    src_w, src_h = record.get("mask_src") or (int(rect.width()), int(rect.height()))
    lx, ly = video_to_local(
        vx, vy, rect,
        rotation_deg=float(record.get("rotation") or 0.0),
        flip_x=bool(record.get("flip_x")),
        flip_y=bool(record.get("flip_y")),
    )
    # video_to_local is in the drawn rect. The mask is the source image,
    # which is smaller whenever fit/scale enlarges the picture.
    rw = float(rect.width())
    rh = float(rect.height())
    if rw > 1e-6 and rh > 1e-6 and src_w and src_h:
        lx *= float(src_w) / rw
        ly *= float(src_h) / rh
    return sample_alpha(mask, int(src_w), int(src_h), lx, ly) > HIT_ALPHA_MIN


def pick_record(records, vx: float, vy: float, *, alt_bbox: bool = False):
    """Top-most record whose alpha at (vx,vy) exceeds the threshold. None = miss."""
    for rec in reversed(list(records or [])):
        if hits_record(rec, vx, vy, alt_bbox=alt_bbox):
            return rec
    return None


def attach_mask(record: dict, img: QImage, *, rotation=0.0, flip_x=False, flip_y=False,
                opacity: float = 1.0) -> dict:
    mask, sw, sh = downsample_alpha(img)
    op = max(0.0, min(1.0, float(opacity)))
    if op < 0.999:
        mask = np.clip(mask.astype(np.float32) * op, 0, 255).astype(np.uint8)
    record["alpha_mask"] = mask
    record["mask_src"] = (sw, sh)
    record["rotation"] = float(rotation or 0.0)
    record["flip_x"] = bool(flip_x)
    record["flip_y"] = bool(flip_y)
    return record
