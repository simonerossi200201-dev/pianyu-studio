# -*- coding: utf-8 -*-
"""T04f-6b-1 D: alpha hit testing (threshold, inverse transform, Alt bbox, miss)."""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from cutora.render.hitmask import (
    HIT_ALPHA_MIN,
    attach_mask,
    hits_record,
    pick_record,
    sample_alpha,
    video_to_local,
)


def _glyph_img(w=200, h=80):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        p.setPen(QColor(255, 255, 255, 255))
        p.setBrush(QColor(255, 255, 255, 255))
        p.drawRect(20, 20, 40, 40)
    finally:
        p.end()
    return img


def test_transparent_region_misses_solid_hits():
    img = _glyph_img()
    rec = attach_mask({"type": "overlay", "rect": QRectF(0, 0, 200, 80)}, img)
    assert hits_record(rec, 40, 40) is True
    assert hits_record(rec, 150, 40) is False
    assert hits_record(rec, 150, 40, alt_bbox=True) is True


def test_half_transparent_fade_still_hits():
    img = QImage(100, 40, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        p.fillRect(0, 0, 100, 40, QColor(255, 255, 255, 60))
    finally:
        p.end()
    rec = attach_mask({"rect": QRectF(0, 0, 100, 40)}, img)
    assert 60 > HIT_ALPHA_MIN
    assert hits_record(rec, 50, 20) is True


def test_zero_opacity_misses():
    img = _glyph_img()
    rec = attach_mask({"rect": QRectF(0, 0, 200, 80)}, img, opacity=0.0)
    assert hits_record(rec, 40, 40) is False
    assert hits_record(rec, 40, 40, alt_bbox=True) is True


def test_rotation_samples_visible_shape_not_aabb_corner():
    img = QImage(80, 20, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        p.fillRect(0, 0, 80, 20, QColor(255, 255, 255, 255))
    finally:
        p.end()
    rect = QRectF(100, 100, 80, 20)
    rec = attach_mask({"rect": rect}, img, rotation=90.0)
    cx, cy = rect.center().x(), rect.center().y()
    assert hits_record(rec, cx, cy - 30) is True
    assert hits_record(rec, cx + 30, cy) is False
    assert hits_record(rec, cx + 30, cy, alt_bbox=True) is True


def test_pick_topmost_opaque_then_miss_clears():
    full = QImage(200, 80, QImage.Format.Format_ARGB32_Premultiplied)
    full.fill(QColor(255, 255, 255, 255))
    bottom = attach_mask({"type": "sub", "rect": QRectF(0, 0, 200, 80)}, full)
    top = attach_mask(
        {"type": "overlay", "rect": QRectF(0, 0, 200, 80)}, _glyph_img())
    assert pick_record([bottom, top], 40, 40)["type"] == "overlay"
    assert pick_record([bottom, top], 150, 40)["type"] == "sub"
    assert pick_record([bottom, top], 250, 40) is None


def test_pick_microbench():
    recs = []
    img = _glyph_img()
    for i in range(8):
        recs.append(attach_mask(
            {"type": "overlay", "idx": i, "rect": QRectF(i * 10, 0, 200, 80)}, img))
    t0 = time.perf_counter()
    for _ in range(2000):
        pick_record(recs, 40, 40)
        pick_record(recs, 900, 900)
    dt_ms = (time.perf_counter() - t0) * 1000.0 / 2000.0
    assert dt_ms < 2.0, dt_ms
