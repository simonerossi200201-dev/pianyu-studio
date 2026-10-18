# -*- coding: utf-8 -*-
"""T04i-1 signed schema: fade cap 1.0, multiply overlap, per-box offset, extrema."""
from __future__ import annotations

import os

import numpy as np

from cutora.render.textfx.fade import (
    FADE_CAP,
    OFFSET_CAP,
    apply_fade_bgra,
    edge_fade_mask,
    fade_offset_tuple,
    fade_tuple,
    write_fade,
)
from cutora.render.textfx.spec import from_style

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_three_fade_caps_are_1():
    assert FADE_CAP == 1.0
    assert OFFSET_CAP == 1.0
    spec = from_style({"fx": {"bg": {"fade": {
        "top": 1.0, "bottom": 1.0, "left": 1.0, "right": 2.0,
    }}}})
    assert spec.bg.fade.top == 1.0
    assert spec.bg.fade.bottom == 1.0
    assert spec.bg.fade.left == 1.0
    assert spec.bg.fade.right == 1.0
    obj = {}
    write_fade(obj, 1.0, 1.0, 1.0, 1.0)
    assert obj["fade_top"] == obj["fade_right"] == 1.0
    src = open(os.path.join(REPO, "cutora", "ui", "panels", "fx_schema.py"),
               encoding="utf-8").read()
    assert "def pct_to_fade" in src and "FADE_CAP" in src
    src_p = open(os.path.join(REPO, "cutora", "ui", "panels", "property.py"),
                 encoding="utf-8").read()
    assert "FADE_CAP" in src_p and "ov_fade_top" in src_p and "vid_fade_top" in src_p


def test_overlap_multiply_center_is_00625():
    # Odd size so the center pixel is the same sample on the reversed ramp.
    n = 101
    mid = n // 2
    mask = edge_fade_mask(n, n, 1.0, 1.0, 1.0, 1.0)
    t = float(mid) / float(n)
    s = t * t * (3.0 - 2.0 * t)
    center = float(mask[mid, mid])
    assert abs(center - s ** 4) < 1e-5, (center, s ** 4)
    # Continuous midpoint: smoothstep(0.5)^4 = 0.0625
    assert abs(s ** 4 - 0.0625) < 0.01
    assert np.isfinite(mask).all()
    assert float(mask.min()) >= 0.0
    assert float(mask.max()) <= 1.0


def test_offset_zero_matches_today():
    a = edge_fade_mask(40, 100, 0, 0, 0.3, 0)
    b = edge_fade_mask(40, 100, 0, 0, 0.3, 0, left_offset=0.0)
    assert a.tobytes() == b.tobytes()


def test_offset_is_translation_vacated_is_zero():
    # left=0.3, offset=+0.2, w=100 → ramp covers [20, 50]. [0, 20) stays 0.
    mask = edge_fade_mask(40, 100, 0, 0, 0.3, 0, left_offset=0.2)
    assert float(mask[20, 0]) < 0.05
    assert float(mask[20, 19]) < 0.05
    assert abs(float(mask[20, 35]) - 0.5) < 0.02
    assert float(mask[20, 50]) > 0.99


def test_range_zero_offset_zero_stays_opaque():
    """Fade width 0 and offset 0 is still a no-op. Every default element stays whole."""
    plain = edge_fade_mask(40, 100, 0, 0, 0, 0)
    plus = edge_fade_mask(
        40, 100, 0, 0, 0, 0,
        top_offset=0.0, bottom_offset=0.0, left_offset=0.0, right_offset=0.0)
    assert plain.tobytes() == plus.tobytes()
    assert float(plain.min()) == 1.0
    img = np.full((10, 40, 4), 255, dtype=np.uint8)
    apply_fade_bgra(img, 0, 0, 0, 0, offsets=(0.0, 0.0, 0.0, 0.0))
    assert int(img[4, 0, 3]) == 255


def test_zero_fade_nonzero_offset_hard_crops():
    """Fade width 0 still crops at the offset. That is an edge cut, not a ramp."""
    from cutora.render.textfx.fade import fade_active

    mask = edge_fade_mask(40, 100, 0, 0, 0, 0, left_offset=0.2)
    assert float(mask[10, 0]) == 0.0
    assert float(mask[10, 19]) == 0.0
    assert float(mask[10, 20]) == 1.0
    assert float(mask[10, 99]) == 1.0
    right = edge_fade_mask(20, 100, 0, 0, 0, 0, right_offset=0.25)
    assert float(right[5, 99]) == 0.0
    assert float(right[5, 75]) == 0.0
    assert float(right[5, 74]) == 1.0
    img = np.full((8, 100, 4), 255, dtype=np.uint8)
    apply_fade_bgra(img, 0, 0, 0, 0, offsets=(0.0, 0.0, 0.2, 0.0))
    assert int(img[3, 0, 3]) == 0
    assert int(img[3, 19, 3]) == 0
    assert int(img[3, 20, 3]) == 255
    assert fade_active({"fade_left_offset": 0.2})
    assert not fade_active({})
    assert not fade_active({"fade_left": 0.0, "fade_left_offset": 0.0})


def test_extrema_combinations_stay_finite():
    cases = (
        (1.0, 1.0), (1.0, -1.0), (0.0, 1.0), (0.0, -1.0),
    )
    for frac, off in cases:
        mask = edge_fade_mask(
            32, 48, frac, frac, frac, frac,
            top_offset=off, bottom_offset=off,
            left_offset=off, right_offset=off)
        assert mask.shape == (32, 48)
        assert np.isfinite(mask).all()
        assert not np.isnan(mask).any()
        assert float(mask.min()) >= 0.0
        assert float(mask.max()) <= 1.0


def test_bubble_offset_is_per_box_not_roi():
    img = np.full((80, 120, 4), 255, dtype=np.uint8)
    # Two bubble rows of different widths. left=0.5, offset=0.
    boxes = [(0, 0, 40, 20), (0, 40, 80, 20)]
    apply_fade_bgra(img, 0, 0, 0.5, 0, boxes=boxes)
    # First box w=40 → ramp 20 px, x=20 is end of ramp (alpha ~1).
    # Second box w=80 → ramp 40 px, x=20 is midpoint (alpha ~0.5).
    a1 = img[10, 20, 3] / 255.0
    a2 = img[50, 20, 3] / 255.0
    assert a1 > 0.95, a1
    assert abs(a2 - 0.5) < 0.05, a2
    img2 = np.full((80, 120, 4), 255, dtype=np.uint8)
    apply_fade_bgra(
        img2, 0, 0, 0.5, 0, boxes=boxes, offsets=(0.0, 0.0, 0.25, 0.0))
    # offset 0.25 of box width: first start=10, second start=20.
    assert img2[10, 5, 3] < 20
    assert img2[50, 10, 3] < 20
    assert img2[50, 5, 3] < 20


def test_missing_offset_reads_as_zero():
    assert fade_offset_tuple({}) == (0.0, 0.0, 0.0, 0.0)
    assert fade_offset_tuple(None) == (0.0, 0.0, 0.0, 0.0)
    assert fade_tuple({}) == (0.0, 0.0, 0.0, 0.0)
    obj = {}
    write_fade(obj, 0.2, 0, 0, 0, offsets=(0.1, -0.2, 0.0, 0.3))
    assert abs(obj["fade_top_offset"] - 0.1) < 1e-9
    assert abs(obj["fade_bottom_offset"] + 0.2) < 1e-9


