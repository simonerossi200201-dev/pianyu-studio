# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q125 · 文本框手柄按排版软件的约定：角缩字号、边改折行宽、Shift 反转。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication


def _app():
    return QApplication.instance() or QApplication(["q125"])


class _Host:
    """只带 _apply_text_resize / _calc_snap 用得到的东西。"""

    TEXT_FONT_MIN = 8.0
    TEXT_FONT_MAX = 600.0

    def __init__(self, ov, rect):
        self._task_data = {"text_overlays": [ov]}
        self._selected_rect = QRectF(rect)
        self._video_dims = (1080, 1920)
        self._dragging_item = {"type": "overlay", "list": "text_overlays", "idx": 0}
        self._selected_item = self._dragging_item
        self._hit_records = []
        self._drag_hud = None
        self._snap_threshold = 15.0
        self._snap_hold_off = False
        self._snap_lines = []
        self._axis_lock = None

    def _playhead_t(self):
        return 0.0

    def _commit_host_param(self, host, path, value, static_assign=None):
        if callable(static_assign):
            static_assign(value)

    # 把真正的实现绑过来
    def _apply_text_resize(self, *a, **k):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._apply_text_resize(self, *a, **k)

    def _width_pct(self, w):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._width_pct(self, w)

    def _other_snap_targets(self):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._other_snap_targets(self)

    def _same_item(self, a, b):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._same_item(a, b)

    def _drag_cell_box(self):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._drag_cell_box(self)


def _ov(**kw):
    ov = {"type": "text", "content": "Hi", "x": 100.0, "y": 200.0,
          "w": 400.0, "h": 100.0, "fontsize": 80.0, "fit_mode": "auto_height"}
    ov.update(kw)
    return ov


def _rect(ov):
    return QRectF(ov["x"], ov["y"], ov["w"], ov["h"])


SHIFT = Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier


# ------------------------------------------------------------------ 角柄

def test_corner_scales_the_font_and_pins_the_opposite_corner():
    """老毛病：八个手柄都只改框，字号一动不动。"""
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 200.0, 50.0, "se", NONE)   # 右下角往外拖
    assert ov["fontsize"] == pytest.approx(120.0)                       # 400→600 = 1.5×
    assert ov["w"] == pytest.approx(600.0)
    assert (ov["x"], ov["y"]) == (100.0, 200.0), "左上角要钉住"
    assert host._drag_hud[0] == "字号 120 px"


def test_nw_corner_pins_the_bottom_right():
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 200.0, 50.0, "nw", NONE)   # 左上角往里拖 → 缩小
    assert ov["fontsize"] < 80.0
    assert ov["x"] + ov["w"] == pytest.approx(500.0)                    # 右边不动
    assert ov["y"] + ov["h"] == pytest.approx(300.0)                    # 底边不动


def test_corner_with_shift_only_resizes_the_box():
    """Shift 反转：角柄变成只改框、字号不动（想手动定框的人用）。"""
    _app()
    ov = _ov(fit_mode="none")
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 200.0, 50.0, "se", SHIFT)
    assert ov["fontsize"] == 80.0
    assert ov["w"] == pytest.approx(600.0)
    assert ov["h"] == pytest.approx(150.0)


def test_font_size_is_clamped():
    _app()
    ov = _ov(fontsize=590.0)
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 4000.0, 4000.0, "se", NONE)
    assert ov["fontsize"] == 600.0
    ov = _ov(fontsize=10.0)
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), -390.0, -90.0, "se", NONE)
    assert ov["fontsize"] >= 8.0


# ------------------------------------------------------------------ 边柄

def test_side_handle_only_changes_the_width():
    """拖右边：字号不变，只有框宽变（文字会重新折行），高度不跟着缩。"""
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), -150.0, 999.0, "e", NONE)
    assert ov["fontsize"] == 80.0
    assert ov["w"] == pytest.approx(250.0)
    assert ov["h"] == pytest.approx(100.0), "以前默认锁比例，拖宽高度会跟着变"
    assert ov["x"] == 100.0
    assert host._drag_hud == ("框宽 23%", "250 px")


def test_left_handle_moves_x_and_keeps_the_right_edge():
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 60.0, 0.0, "w", NONE)
    assert ov["x"] == pytest.approx(160.0)
    assert ov["x"] + ov["w"] == pytest.approx(500.0)


def test_width_cannot_collapse():
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), -1000.0, 0.0, "e", NONE)
    assert ov["w"] >= 24.0


def test_top_bottom_handles_do_nothing_on_auto_height():
    """高度由文字决定，上下边柄没东西可拖。"""
    _app()
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 0.0, 80.0, "s", NONE)
    assert (ov["y"], ov["h"]) == (200.0, 100.0)
    assert host._drag_hud[0] == "高度跟着文字走"


def test_top_bottom_handles_work_on_a_fixed_box():
    _app()
    ov = _ov(fit_mode="none")
    host = _Host(ov, _rect(ov))
    host._apply_text_resize(ov, _rect(ov), 0.0, 80.0, "s", NONE)
    assert ov["h"] == pytest.approx(180.0)
    host._apply_text_resize(ov, _rect(ov), 0.0, -30.0, "n", NONE)
    assert ov["y"] == pytest.approx(170.0)


# ------------------------------------------------------------- 互相吸附

def test_other_boxes_become_snap_targets():
    """两条文本要能吸到同一条中线上。"""
    _app()
    from cutora.ui.canvas import VideoCanvas
    ov = _ov()
    host = _Host(ov, _rect(ov))
    host._hit_records = [
        {"type": "overlay", "list": "text_overlays", "idx": 0,
         "rect": QRectF(100, 200, 400, 100)},                       # 自己，跳过
        {"type": "overlay", "list": "text_overlays", "idx": 1,
         "rect": QRectF(300, 900, 200, 100)},                       # 中心 x=400
        {"type": "sub", "idx": 0, "rect": QRectF(0, 0, 50, 50)},    # 字幕不算
    ]
    xs, ys = host._other_snap_targets()
    assert 400.0 in xs and 300.0 in xs and 500.0 in xs
    assert 950.0 in ys
    assert 25.0 not in xs
    # 拖到离 400 中线 6px 的地方 → 吸上去
    rect = QRectF(194, 200, 400, 100)      # 中心 x = 394
    dx, dy = VideoCanvas._calc_snap(host, rect, 0.0, 0.0)
    assert dx == pytest.approx(6.0)
    assert {"axis": "x", "pos": 400.0} in host._snap_lines


def test_same_item_matching():
    from cutora.ui.canvas import VideoCanvas
    a = {"type": "overlay", "list": "text_overlays", "idx": 2}
    assert VideoCanvas._same_item(a, dict(a))
    assert not VideoCanvas._same_item(a, {"type": "overlay", "list": "text_overlays", "idx": 3})
    clip = object()
    assert VideoCanvas._same_item({"type": "video_clip", "clip": clip},
                                  {"type": "video_clip", "clip": clip})
    assert not VideoCanvas._same_item({"type": "video_clip", "clip": clip},
                                      {"type": "video_clip", "clip": object()})
