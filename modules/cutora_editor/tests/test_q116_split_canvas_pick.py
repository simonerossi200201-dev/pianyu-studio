# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q116 · 分屏后在画布上点得中片段，拖得动，带参考线和方向锁。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QApplication

from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track


def _app():
    return QApplication.instance() or QApplication([])


def _split_task():
    from cutora.render.split_layout import apply_split_to_task, set_track_cell
    tl = Timeline(1080, 1920, 30.0)
    top, bottom = Track("video"), Track("video")
    tl.add_track(top)
    tl.add_track(bottom)
    task = {"timeline": tl, "dims": (1080, 1920)}
    apply_split_to_task(task, "top_bottom", True)
    set_track_cell(task, top.id, "A")
    set_track_cell(task, bottom.id, "B")
    return task, tl, top, bottom


# ------------------------------------------------------------- 格子几何

def test_cell_content_box_splits_the_canvas():
    """上下分屏：A 占上半，B 占下半，两块拼起来正好是整张画布。"""
    from cutora.render.split_screen import cell_content_box
    task, _tl, _a, _b = _split_task()
    sp = task.get("split")
    a = cell_content_box(sp, "A", 1080, 1920)
    b = cell_content_box(sp, "B", 1080, 1920)
    assert a is not None and b is not None
    assert a[0] == 0 and a[1] == 0
    assert a[2] == 1080
    assert b[0] == 0
    assert b[1] == pytest.approx(a[3], abs=2)
    assert a[3] + b[3] == pytest.approx(1920, abs=2)


def test_cell_content_box_unknown_cell_is_none():
    from cutora.render.split_screen import cell_content_box
    task, _tl, _a, _b = _split_task()
    assert cell_content_box(task.get("split"), "Z", 1080, 1920) is None


# --------------------------------------------------- 命中记录落在正确的格子

class _HitHost:
    """只提供 _remember_video_hit 用到的那几样东西。"""

    def __init__(self, rect):
        self._rect = rect
        self._hit_records = []
        self._task_data = None
        self.seen_dims = []

    def _clip_draw_params(self, clip, fw, fh, tw, th):
        self.seen_dims.append((tw, th))
        return {"rect": QRectF(self._rect), "opacity": 255,
                "rotation": 0.0, "flip_x": False, "flip_y": False}


def _fake_frame(w=640, h=360):
    from PySide6.QtGui import QImage
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0xFF204060)
    return img


def test_hit_rect_is_offset_into_its_cell(monkeypatch):
    """老 bug：分屏时根本不登记命中，点上下屏的画面什么都选不中。"""
    from cutora.ui import canvas as canvas_mod
    from cutora.ui.canvas import VideoCanvas
    _app()
    monkeypatch.setattr("cutora.render.hitmask.attach_mask",
                        lambda rec, frame, **kw: None)
    host = _HitHost(QRectF(0, 0, 1080, 960))
    clip = SimpleNamespace(alpha=False)
    VideoCanvas._remember_video_hit(
        host, clip, _fake_frame(), 1080, 1920, None,
        dest=(0, 960, 1080, 960))
    assert host.seen_dims == [(1080.0, 960.0)], "排版必须按格子尺寸算"
    rec = host._hit_records[0]
    assert rec["rect"].top() == pytest.approx(960.0)
    assert rec["rect"].height() == pytest.approx(960.0)
    assert rec["cell"] == (0, 960, 1080, 960)


def test_hit_rect_is_clipped_to_the_cell(monkeypatch):
    """超出格子的部分被 mask 挡着，点不中 —— 命中矩形也不能伸出去。"""
    from cutora.ui.canvas import VideoCanvas
    _app()
    monkeypatch.setattr("cutora.render.hitmask.attach_mask",
                        lambda rec, frame, **kw: None)
    host = _HitHost(QRectF(-200, -300, 1600, 1600))
    VideoCanvas._remember_video_hit(
        host, SimpleNamespace(alpha=False), _fake_frame(), 1080, 1920, None,
        dest=(0, 960, 1080, 960))
    rec = host._hit_records[0]
    assert rec["rect"].top() >= 960.0
    assert rec["rect"].bottom() <= 1920.0 + 1e-6
    assert rec["rect"].left() >= 0.0


def test_hit_without_a_cell_keeps_full_canvas(monkeypatch):
    from cutora.ui.canvas import VideoCanvas
    _app()
    monkeypatch.setattr("cutora.render.hitmask.attach_mask",
                        lambda rec, frame, **kw: None)
    host = _HitHost(QRectF(10, 20, 500, 400))
    VideoCanvas._remember_video_hit(
        host, SimpleNamespace(alpha=False), _fake_frame(), 1080, 1920, None)
    rec = host._hit_records[0]
    assert host.seen_dims == [(1080.0, 1920.0)]
    assert rec["rect"].topLeft().x() == pytest.approx(10.0)
    assert rec["cell"] is None


# --------------------------------------------------------- 吸附线跟着格子走

class _SnapHost:
    def __init__(self, cell=None):
        self._video_dims = (1080, 1920)
        self._snap_threshold = 15.0
        self._snap_hold_off = False
        self._snap_lines = []
        self._axis_lock = None
        self._dragging_item = {"type": "video_clip", "cell": cell} if cell else None
        self._selected_item = None

    def _drag_cell_box(self):
        from cutora.ui.canvas import VideoCanvas
        return VideoCanvas._drag_cell_box(self)

    def _other_snap_targets(self):
        return [], []          # Q125：这些用例只看格子/画布的线


def _calc(host, rect, dx, dy):
    from cutora.ui.canvas import VideoCanvas
    return VideoCanvas._calc_snap(host, rect, dx, dy)


def test_snap_targets_follow_the_cell():
    """改之前：分屏下拖 B 屏的画面，吸的还是整块画布的中线，对不上格子。"""
    from cutora.ui.canvas import VideoCanvas
    host = _SnapHost(cell=(0, 960, 1080, 960))
    rect = QRectF(0, 1400, 1080, 960)          # 顶边在 1400
    dx, dy = _calc(host, rect, 0.0, -432.0)    # 往上拖到 968，离格子顶 960 差 8
    assert dy == pytest.approx(-440.0)         # 被吸到 960
    assert {'axis': 'y', 'pos': 960.0} in host._snap_lines


def test_snap_without_split_still_uses_the_canvas():
    host = _SnapHost(cell=None)
    rect = QRectF(0, 8, 1080, 1920)
    dx, dy = _calc(host, rect, 0.0, -8.0)
    assert dy == pytest.approx(-8.0)
    assert {'axis': 'y', 'pos': 0.0} in host._snap_lines


def test_holding_n_turns_snapping_off():
    host = _SnapHost(cell=(0, 960, 1080, 960))
    host._snap_hold_off = True
    dx, dy = _calc(host, QRectF(0, 1400, 1080, 960), 3.0, -432.0)
    assert (dx, dy) == (3.0, -432.0)
    assert host._snap_lines == []


# ------------------------------------------------------------------ 方向锁

def _lock(host, dx, dy, mods):
    from cutora.ui.canvas import VideoCanvas
    return VideoCanvas._lock_axis(host, dx, dy, mods)


def test_shift_locks_to_the_dominant_axis():
    """Luca 要的「只上下移动，不会露出黑边」。"""
    host = _SnapHost()
    shift = Qt.KeyboardModifier.ShiftModifier
    assert _lock(host, 3.0, -40.0, shift) == (0.0, -40.0)
    assert host._axis_lock == "y"
    assert _lock(host, -40.0, 3.0, shift) == (-40.0, 0.0)
    assert host._axis_lock == "x"


def test_without_shift_nothing_is_locked():
    host = _SnapHost()
    assert _lock(host, 3.0, -40.0, Qt.KeyboardModifier.NoModifier) == (3.0, -40.0)
    assert host._axis_lock is None
    assert _lock(host, 3.0, -40.0, None) == (3.0, -40.0)


def test_locked_axis_draws_its_guide():
    host = _SnapHost(cell=(0, 960, 1080, 960))
    host._axis_lock = "y"
    _calc(host, QRectF(100, 1200, 400, 300), 0.0, -30.0)
    xs = [s for s in host._snap_lines if s['axis'] == 'x']
    assert xs and xs[-1]['pos'] == pytest.approx(300.0)   # 矩形中心 x


def test_drag_cell_box_falls_back_to_the_canvas():
    from cutora.ui.canvas import VideoCanvas
    host = _SnapHost()
    assert VideoCanvas._drag_cell_box(host) == (0.0, 0.0, 1080.0, 1920.0)
    host._dragging_item = {"type": "video_clip", "cell": (0, 960, 1080, 960)}
    assert VideoCanvas._drag_cell_box(host) == (0.0, 960.0, 1080.0, 960.0)


# --------------------------------------------------------- 选中就切属性页

def test_clicking_a_clip_opens_its_property_tab():
    """画布点中片段 → on_timeline_select 切到属性页并加载这个片段。"""
    from cutora.ui.main_window import MainWindow
    tl = Timeline(1080, 1920, 30.0)
    tr = Track("video")
    tl.add_track(tr)
    clip = SimpleNamespace(source_id="s1")
    tr.clips.append(clip)
    task = {"timeline": tl}
    loaded = []
    tabs = []

    class Host:
        curr_idx = 0
        tasks = [task]
        tabs = SimpleNamespace(setCurrentIndex=lambda i: tabs.append(i),
                               indexOf=lambda w: 2)
        prop_panel = SimpleNamespace(
            load_data=lambda kind, payload: loaded.append((kind, payload)))

        def _reset_sticker_ui_cache(self):
            return None

        def _reveal_video_clip(self, c):
            return None

    MainWindow.on_timeline_select(Host(), "video_clip", clip)
    assert tabs == [2]
    assert loaded == [("video_clip", (tr, clip))]
