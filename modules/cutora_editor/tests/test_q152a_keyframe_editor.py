# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-A · 关键帧面板 + 运动曲线编辑器。

kf_edit：纯函数（平移 / 缩放时距 / 删除 / 反转 / 等距 / 缓动 / 平滑 / 手柄 / 过冲）。
keyframe：老工程求值字节不变；bez 的 y 超出 0–1 = 过冲。
keyframe.replace：一个手势一次撤销。
编辑器：跟着检查器对象走；拖动预览 → 松手一条命令；键盘、菜单动作。
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _pts():
    return [{"t": 0.0, "v": 0.0, "ease": "linear"},
            {"t": 1.0, "v": 100.0, "ease": "linear"},
            {"t": 2.0, "v": 50.0, "ease": "linear"}]


def _val(pts, t, path="x"):
    from cutora.render.keyframe import value_at
    return float(value_at({"keyframes": {path: pts}}, path, t, 0.0))


# ───────────────────────────── 求值兼容 ─────────────────────────────

def test_legacy_bezier_values_unchanged():
    """老工程的 bez 都在 0–1：Q152 前后求值一致（逐点比对 Q62 时代的公式）。"""
    from cutora.render.keyframe import _cubic, value_at
    pts = [{"t": 0.0, "v": 10.0, "ease": "bezier", "bez": [0.32, 0.0, 0.67, 1.0]},
           {"t": 2.0, "v": 90.0, "ease": "linear"}]
    host = {"keyframes": {"x": copy.deepcopy(pts)}}

    def old_y(u, x1, y1, x2, y2):          # Q62 原实现（含末尾夹 0–1）
        if u <= 0.0:
            return 0.0
        if u >= 1.0:
            return 1.0
        lo, hi, t = 0.0, 1.0, u
        for _ in range(60):
            x = _cubic(t, x1, x2)
            if x < u:
                lo = t
            else:
                hi = t
            t = 0.5 * (lo + hi)
        return max(0.0, min(1.0, _cubic(t, y1, y2)))

    for k in range(21):
        t = k / 10.0
        want = 10.0 + 80.0 * old_y(t / 2.0, 0.32, 0.0, 0.67, 1.0)
        assert abs(value_at(host, "x", t, 0.0) - want) < 1e-3


def test_bezier_overshoot_goes_past_target():
    from cutora.render import kf_edit as ke
    pts = ke.drag_handle(_pts()[:2], 0, "out", 0.3, 150.0, linked=False)
    pts = ke.drag_handle(pts, 1, "in", 0.7, 140.0, linked=False)
    assert pts[0]["bez"][1] == pytest.approx(1.5) and pts[0]["bez"][3] == pytest.approx(1.4)
    peak = max(_val(pts, k / 20.0) for k in range(21))
    assert peak > 100.0                       # 冲过 100 再回来
    assert _val(pts, 1.0) == pytest.approx(100.0)


def test_color_paths_never_overshoot():
    from cutora.render.keyframe import interpolate
    assert interpolate("#000000", "#FFFFFF", 1.4) == "#FFFFFF"


# ───────────────────────────── kf_edit ─────────────────────────────

def test_move_clamps_at_zero_and_replaces_collisions():
    from cutora.render import kf_edit as ke
    out = ke.move(_pts(), [1], -5.0)
    assert [p["t"] for p in out] == [0.0, 1.0] or out[0]["t"] == 0.0
    # 被挪到 t=0 上的那个点让位：只剩一个 t=0
    assert sum(1 for p in out if abs(p["t"]) < 1e-9) == 1
    assert out[0]["v"] == 100.0
    out = ke.move(_pts(), [1, 2], 0.5)
    assert [p["t"] for p in out] == [0.0, 1.5, 2.5]


def test_scale_time_and_distribute_and_delete():
    from cutora.render import kf_edit as ke
    out = ke.scale_time(_pts(), [0, 1, 2], 0.0, 0.5)
    assert [p["t"] for p in out] == [0.0, 0.5, 1.0]
    uneven = _pts()
    uneven[1]["t"] = 0.2
    out = ke.distribute(uneven, [0, 1, 2])
    assert [p["t"] for p in out] == [0.0, 1.0, 2.0]
    out = ke.delete(_pts(), [1])
    assert [p["v"] for p in out] == [0.0, 50.0]


def test_reverse_flips_values_and_eases():
    from cutora.render import kf_edit as ke
    pts = _pts()
    pts[0]["ease"] = "ease_in"
    out = ke.reverse(pts, [0, 1, 2])
    assert [p["v"] for p in out] == [50.0, 100.0, 0.0]
    assert [p["t"] for p in out] == [0.0, 1.0, 2.0]
    assert out[1]["ease"] == "ease_out"       # 原来 0→1 缓入，倒过来是 1→0 缓出
    # 曲线是镜像：倒过来的 f(t) = 原 f(2 - t)
    for k in range(21):
        t = k / 10.0
        assert _val(out, t) == pytest.approx(_val(pts, 2.0 - t), abs=1e-6)


def test_easy_ease_f9_sets_zero_speed_handles():
    from cutora.render import kf_edit as ke
    out = ke.set_ease(_pts(), [1], "easy")
    assert out[0]["ease"] == "bezier" and out[0]["bez"][2:] == [pytest.approx(2 / 3), 1.0]
    assert out[1]["ease"] == "bezier" and out[1]["bez"][:2] == [pytest.approx(1 / 3), 0.0]
    # 关键帧两侧速度为 0：t=1 附近值几乎不变
    assert abs(_val(out, 0.98) - 100.0) < 0.5 and abs(_val(out, 1.02) - 100.0) < 0.5
    only_in = ke.set_ease(_pts(), [1], "easy_in")
    assert only_in[0]["ease"] == "bezier" and only_in[1]["ease"] == "linear"
    only_out = ke.set_ease(_pts(), [1], "easy_out")
    assert only_out[0]["ease"] == "linear" and only_out[1]["ease"] == "bezier"
    held = ke.set_ease(_pts(), [0], "hold")
    assert held[0]["ease"] == "hold" and _val(held, 0.9) == 0.0


def test_smooth_passes_through_without_stopping():
    from cutora.render import kf_edit as ke
    pts = [{"t": 0.0, "v": 0.0}, {"t": 1.0, "v": 50.0}, {"t": 2.0, "v": 100.0}]
    out = ke.smooth(pts)
    # 中间点上斜率连续（左右差分接近）
    left = (_val(out, 1.0) - _val(out, 0.99)) / 0.01
    right = (_val(out, 1.01) - _val(out, 1.0)) / 0.01
    assert left == pytest.approx(right, rel=0.05)
    assert left == pytest.approx(50.0, rel=0.1)
    # 峰值处不冲出去
    peak = ke.smooth(_pts())
    assert max(_val(peak, k / 50.0) for k in range(101)) <= 100.0 + 1e-6


def test_linked_handle_keeps_tangent_collinear():
    from cutora.render import kf_edit as ke
    pts = ke.set_ease(_pts(), [1], "easy")
    out = ke.drag_handle(pts, 1, "out", 1.3, 80.0, linked=True)
    hp = ke.handle_points(out, 1)
    (ti, vi), (to, vo) = hp["in"], hp["out"]
    s_in = (100.0 - vi) / (1.0 - ti)
    s_out = (vo - 100.0) / (to - 1.0)
    assert s_in == pytest.approx(s_out, rel=1e-6)
    broken = ke.drag_handle(pts, 1, "out", 1.3, 80.0, linked=False)
    assert ke.handle_points(broken, 1)["in"] == pytest.approx(ke.handle_points(pts, 1)["in"])


def test_sanitize():
    from cutora.render import kf_edit as ke
    out = ke.sanitize([{"t": "1", "v": 3}, {"t": -2, "v": 1, "ease": "螺旋"}, {"t": 1.0, "v": 9},
                       {"v": 1}, "x", {"t": 0.5, "v": 2, "ease": "bezier", "bez": [2, 9, -1, -9]}])
    assert [p["t"] for p in out] == [0.0, 0.5, 1.0]
    assert out[0]["ease"] == "linear" and out[2]["v"] == 9.0
    assert out[1]["bez"] == [1.0, 3.0, 0.0, -2.0]


# ───────────────────────────── 命令 ─────────────────────────────

def _clip_task():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.keyframe import set_point
    tl = Timeline(160, 90, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 3.0, 1.0)
    set_point(clip, "x", 0.0, 0.0)
    set_point(clip, "x", 1.0, 80.0)
    set_point(clip, "opacity", 0.0, 255.0)
    set_point(clip, "opacity", 2.0, 0.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    return {"timeline": tl, "duration": 4.0, "text_overlays": [], "image_overlays": []}, tr, clip


def _bus_window(task):
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    return win, get_bus()


def test_keyframe_replace_is_one_undo_and_can_clear_a_path():
    from test_q31_keyframes import _qapp
    _qapp()
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, bus = _bus_window(task)
    before = copy.deepcopy(keyframes_of(clip))
    new_x = [{"t": 0.0, "v": 0.0, "ease": "bezier", "bez": [0.3, 1.4, 0.7, 1.0]},
             {"t": 1.5, "v": 80.0, "ease": "linear"}]
    out = bus.execute("keyframe.replace", {
        "target": "clip", "track_id": "v0", "clip_index": 0,
        "paths": {"x": new_x, "opacity": None}}, source="ui")
    assert sorted(out["changed"]) == ["opacity", "x"]
    kfs = keyframes_of(clip)
    assert "opacity" not in kfs and kfs["x"][1]["t"] == 1.5
    assert kfs["x"][0]["bez"][1] == pytest.approx(1.4)
    assert len(win.history_mgr.history) == 1
    win.undo()
    clip2 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert keyframes_of(clip2) == before
    with pytest.raises(Exception):
        bus.execute("keyframe.replace", {"target": "clip", "track_id": "v0", "clip_index": 0,
                                         "paths": {"x": "nope"}}, source="ui")


# ───────────────────────────── 编辑器 ─────────────────────────────

class _Panel:
    def __init__(self, host, params):
        self.host, self.params = host, params
        self.current_type = "video_clip"

    def _kf_host(self):
        return self.host

    def _kf_cmd_params(self, _host):
        return dict(self.params)


def _editor(task, clip):
    from test_q31_keyframes import _qapp
    _qapp()
    from PySide6.QtWidgets import QApplication
    from cutora.ui.keyframe_editor import KeyframeEditor
    from cutora.ui.timeline.widget import TimelineWidget
    win, bus = _bus_window(task)
    win.prop_panel = _Panel(clip, {"target": "clip", "track_id": "v0", "clip_index": 0})
    tlw = TimelineWidget()
    tlw.set_data(4000, task)
    win.timeline = tlw
    ed = KeyframeEditor(win)
    ed.resize(900, 260)
    ed.show()
    QApplication.processEvents()
    ed.refresh()
    ed.fit_view()
    return win, ed


def _mouse(view, typ, x, y, mods=None, button=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    btn = button or Qt.MouseButton.LeftButton
    ev = QMouseEvent(typ, QPointF(float(x), float(y)), btn,
                     btn if typ != QMouseEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                     mods or Qt.KeyboardModifier.NoModifier)
    return ev


def test_editor_follows_target_and_lists_rows():
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        assert ed.target is not None and ed.target.host is clip
        assert ed.target.start == 1.0 and ed.target.dur == pytest.approx(3.0)
        assert [p for p, _ in ed.rows_points()] == ["x", "opacity"]
        assert [r[1] for r in ed.sheet.rows()] == ["全部", "位置 X", "不透明度"]
        assert ed.summary_times() == [0.0, 1.0, 2.0]
        assert "视频" in ed.title.text()
        win.prop_panel.host = None
        ed.refresh()
        assert ed.target is None and ed.rows_points() == []
    finally:
        ed.close()


def test_dope_sheet_drag_moves_keys_as_one_undo():
    from PySide6.QtCore import QEvent
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        sheet = ed.sheet
        y = sheet.row_y(1) + 12                       # 「位置 X」这一行
        x1 = ed.axis.x(1.0)
        sheet.mousePressEvent(_mouse(sheet, QEvent.Type.MouseButtonPress, x1, y))
        assert ed.selection == {("x", 1)}
        x_to = ed.axis.x(1.5)
        sheet.mouseMoveEvent(_mouse(sheet, QEvent.Type.MouseMove, (x1 + x_to) / 2, y))
        sheet.mouseMoveEvent(_mouse(sheet, QEvent.Type.MouseMove, x_to, y))
        # 拖动中：对象已经变了（画面跟着动），但还没进撤销
        assert any(abs(p["t"] - 1.5) < 0.02 for p in keyframes_of(clip)["x"])
        assert len(win.history_mgr.history) == 0
        sheet.mouseReleaseEvent(_mouse(sheet, QEvent.Type.MouseButtonRelease, x_to, y))
        xs = [p["t"] for p in keyframes_of(clip)["x"]]
        assert xs[0] == 0.0 and abs(xs[1] - 1.5) < 0.02
        assert len(win.history_mgr.history) == 1
        assert ed.selection == {("x", 1)}
        # 吸附到帧：1/30 的整数倍
        assert abs(xs[1] * 30 - round(xs[1] * 30)) < 1e-6
    finally:
        ed.close()


def test_band_select_and_summary_row_and_alt_scale():
    from PySide6.QtCore import QEvent, Qt
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        sheet = ed.sheet
        # 框住 t=0.9..2.1 两行 → x@1 与 opacity@2
        sheet.mousePressEvent(_mouse(sheet, QEvent.Type.MouseButtonPress, ed.axis.x(0.9), sheet.row_y(1) + 2))
        sheet.mouseMoveEvent(_mouse(sheet, QEvent.Type.MouseMove, ed.axis.x(2.1), sheet.row_y(2) + 22))
        sheet.mouseReleaseEvent(_mouse(sheet, QEvent.Type.MouseButtonRelease, ed.axis.x(2.1), sheet.row_y(2) + 22))
        assert ed.selection == {("x", 1), ("opacity", 1)}
        # 点「全部」行 t=0 的菱形 → 两行 t=0 都选中
        sheet.mousePressEvent(_mouse(sheet, QEvent.Type.MouseButtonPress, ed.axis.x(0.0), sheet.row_y(0) + 12))
        sheet.mouseReleaseEvent(_mouse(sheet, QEvent.Type.MouseButtonRelease, ed.axis.x(0.0), sheet.row_y(0) + 12))
        assert ed.selection == {("x", 0), ("opacity", 0)}
        # 全选后 Alt+拖最后一个（t=2）到 t=1 → 以 t=0 为中心缩成一半
        ed.select_all()
        y = sheet.row_y(2) + 12
        sheet.mousePressEvent(_mouse(sheet, QEvent.Type.MouseButtonPress, ed.axis.x(2.0), y,
                                     mods=Qt.KeyboardModifier.AltModifier))
        sheet.mouseMoveEvent(_mouse(sheet, QEvent.Type.MouseMove, ed.axis.x(1.5), y,
                                    mods=Qt.KeyboardModifier.AltModifier))
        sheet.mouseMoveEvent(_mouse(sheet, QEvent.Type.MouseMove, ed.axis.x(1.0), y,
                                    mods=Qt.KeyboardModifier.AltModifier))
        sheet.mouseReleaseEvent(_mouse(sheet, QEvent.Type.MouseButtonRelease, ed.axis.x(1.0), y))
        kfs = keyframes_of(clip)
        assert [p["t"] for p in kfs["opacity"]] == pytest.approx([0.0, 1.0], abs=0.02)
        assert [p["t"] for p in kfs["x"]] == pytest.approx([0.0, 0.5], abs=0.02)
        assert len(win.history_mgr.history) == 1
    finally:
        ed.close()


def test_graph_drag_handle_and_key_value():
    from PySide6.QtCore import QEvent
    from cutora.render import kf_edit as ke
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        ed.set_mode("graph")
        g = ed.graph
        g.hidden_paths = {"opacity"}
        g.fit_values()
        assert not g.is_normalized()
        # 选中 x@0，拖它的出手柄往上 → 变成贝塞尔且值增大
        ed.select([("x", 0)])
        hp = ke.handle_points(ed.points("x"), 0)["out"]
        hx, hy = ed.axis.x(hp[0]), g.y("x", hp[1])
        g.mousePressEvent(_mouse(g, QEvent.Type.MouseButtonPress, hx, hy))
        g.mouseMoveEvent(_mouse(g, QEvent.Type.MouseMove, hx + 4, hy - 20))
        g.mouseMoveEvent(_mouse(g, QEvent.Type.MouseMove, hx + 4, hy - 40))
        g.mouseReleaseEvent(_mouse(g, QEvent.Type.MouseButtonRelease, hx + 4, hy - 40))
        x0 = keyframes_of(clip)["x"][0]
        assert x0["ease"] == "bezier" and x0["bez"][1] > 1.0 / 3.0
        assert len(win.history_mgr.history) == 1
        # 拖 x@1 的值往下 20 px
        ed.select([("x", 1)])
        kx, ky = ed.axis.x(1.0), g.y("x", 80.0)
        g.mousePressEvent(_mouse(g, QEvent.Type.MouseButtonPress, kx, ky))
        g.mouseMoveEvent(_mouse(g, QEvent.Type.MouseMove, kx, ky + 10))
        g.mouseMoveEvent(_mouse(g, QEvent.Type.MouseMove, kx, ky + 20))
        g.mouseReleaseEvent(_mouse(g, QEvent.Type.MouseButtonRelease, kx, ky + 20))
        p1 = keyframes_of(clip)["x"][1]
        assert p1["v"] < 80.0 and p1["t"] == pytest.approx(1.0)
        assert len(win.history_mgr.history) == 2
    finally:
        ed.close()


def test_keys_menu_actions_and_jump():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        ed.select([("x", 0), ("x", 1)])
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.NoModifier)
        assert ed.wants_key(ev) and ed.handle_key(ev)
        assert keyframes_of(clip)["x"][0]["ease"] == "bezier"
        # J / K 跳关键帧（局部 0 / 1 / 2 → 时间线 1 / 2 / 3 秒）
        win.timeline.set_position(1500)
        ed.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier))
        assert win.timeline.position == 2000
        ed.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_J, Qt.KeyboardModifier.NoModifier))
        assert win.timeline.position == 1000
        # 复制 → 播放头 → 粘贴
        ed.select([("x", 1)])
        ed.copy_selected()
        win.timeline.set_position(3500)       # 局部 2.5
        ed.paste_at_playhead()
        assert any(abs(p["t"] - 2.5) < 1e-6 and p["v"] == 80.0 for p in keyframes_of(clip)["x"])
        # 在播放头打关键帧（选中 opacity 行）
        ed.select([("opacity", 0)])
        win.timeline.set_position(2000)       # 局部 1.0，opacity 线性 255→0 → 127.5
        ed.add_key_at_playhead()
        op = keyframes_of(clip)["opacity"]
        mid = [p for p in op if abs(p["t"] - 1.0) < 1e-6]
        assert mid and mid[0]["v"] == pytest.approx(127.5)
        # 删除
        ed.select([("opacity", 1)])
        ed.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
        assert len(keyframes_of(clip)["opacity"]) == 2
        # 反转 x
        ed.select([("x", i) for i in range(len(keyframes_of(clip)["x"]))])
        vals = [p["v"] for p in keyframes_of(clip)["x"]]
        ed.reverse_selected()
        assert [p["v"] for p in keyframes_of(clip)["x"]] == list(reversed(vals))
        # Shift+F3 切换视图
        ed.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier))
        assert ed.mode() == "graph"
    finally:
        ed.close()


def test_double_click_adds_key_with_curve_value():
    from PySide6.QtCore import QEvent
    from cutora.render.keyframe import keyframes_of
    task, _tr, clip = _clip_task()
    win, ed = _editor(task, clip)
    try:
        sheet = ed.sheet
        y = sheet.row_y(1) + 12
        sheet.mouseDoubleClickEvent(_mouse(sheet, QEvent.Type.MouseButtonDblClick, ed.axis.x(0.5), y))
        xs = keyframes_of(clip)["x"]
        mid = [p for p in xs if abs(p["t"] - 0.5) < 0.02]
        assert mid and mid[0]["v"] == pytest.approx(40.0, abs=1.5)
    finally:
        ed.close()


def test_shortcut_registered_and_help_rows():
    from cutora.ui.shortcuts import GESTURE_ROWS, shortcut_key
    assert shortcut_key("keyframe_editor") == "Alt+K"
    assert any(area == "关键帧编辑器" and "F9" in key for area, key, _ in GESTURE_ROWS)


def test_main_window_dock_and_menu_toggle():
    from test_q145b_timeline_shortcuts import _window
    from PySide6.QtWidgets import QApplication
    win = _window()
    assert win.dock_keyframes.objectName() == "dockKeyframes"
    assert not win.dock_keyframes.isVisible()
    assert win.act_keyframes.shortcut().toString() == "Alt+K"
    win.act_keyframes.trigger()
    QApplication.processEvents()
    assert win.dock_keyframes.isVisible() and win.act_keyframes.isChecked()
    # 在时间线下面（同一个底部区域）
    from PySide6.QtCore import Qt
    assert win.dockWidgetArea(win.dock_keyframes) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert win.dock_keyframes.geometry().top() >= win.dock_timeline.geometry().top()
    win.toggle_keyframe_editor()
    QApplication.processEvents()
    assert not win.dock_keyframes.isVisible() and not win.act_keyframes.isChecked()
