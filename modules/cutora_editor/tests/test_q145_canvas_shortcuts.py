# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q145 · 主画布快捷操作（和中央监视器同一套）：焦点路由、微移、删除、复制粘贴、
Alt+拖复制、Ctrl+拖缩放、视图缩放平移、分界线吸附、双击重置、逐帧按帧率。"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

VW, VH = 1080, 1920
NONE = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier


def _app():
    app = QApplication.instance() or QApplication(["q145"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _mouse(typ, x, y, mods=NONE, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(typ, QPointF(float(x), float(y)), QPointF(float(x), float(y)),
                       button, buttons, mods)


def _key(c, key, mods=NONE, typ=QEvent.Type.KeyPress):
    ev = QKeyEvent(typ, key, mods)
    if typ == QEvent.Type.KeyPress:
        c.keyPressEvent(ev)
    else:
        c.keyReleaseEvent(ev)
    return ev


def _wheel(c, x, y, dy, mods=NONE):
    ev = QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0), QPoint(0, int(dy)),
                     Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
    c.wheelEvent(ev)
    return ev


def _canvas_texts(n=2):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(VW, VH, 30.0)
    tr = Track("text")
    tl.tracks.append(tr)
    ovs = []
    for i, (x, y) in enumerate(((60, 200), (300, 1100))[:n]):
        ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH, track_id=tr.id)
        ov.update({"content": "TEXT%d" % i, "x": x, "y": y, "w": 500, "h": 160, "fontsize": 90,
                   "fit_mode": "none", "start": 0.0, "end": 5.0})
        ovs.append(ov)
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": ovs,
            "image_overlays": [], "duration": 5.0, "dims": [VW, VH]}
    c = VideoCanvas()
    c.setFixedSize(540, 960)
    c.set_data(task, 0.5)
    c.show()
    QApplication.processEvents()
    c.repaint()
    return c, task, ovs


def _glyph_point(c, idx):
    """落在第 idx 条文字字形上的控件坐标（画布按 alpha 命中）。"""
    from cutora.render.hitmask import hits_record
    rec = next(r for r in c._hit_records if r.get("type") == "overlay" and r.get("idx") == idx)
    rect = rec["rect"]
    t = c._preview_target_rect()
    sx, sy = t.width() / VW, t.height() / VH
    for j in range(1, 40):
        for i in range(1, 80):
            vx = rect.x() + rect.width() * i / 80.0
            vy = rect.y() + rect.height() * j / 40.0
            if hits_record(rec, vx, vy):
                return t.x() + vx * sx, t.y() + vy * sy
    raise AssertionError("no glyph pixel")


def _click(c, x, y, mods=NONE):
    c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y, mods))
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y, mods, Qt.MouseButton.NoButton))


def _drag(c, x0, y0, x1, y1, mods=NONE, steps=6):
    c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x0, y0, mods))
    for k in range(1, steps + 1):
        x = x0 + (x1 - x0) * k / steps
        y = y0 + (y1 - y0) * k / steps
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x, y, mods))
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x1, y1, mods, Qt.MouseButton.NoButton))


def _counter(sig):
    box = []
    sig.connect(lambda *a: box.append(a))
    return box


# ─────────────────────────── 焦点路由 ───────────────────────────

def test_shortcut_override_only_with_selection():
    _app()
    c, _task, _ovs = _canvas_texts()
    try:
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Left, NONE)
        ev.ignore()
        c.event(ev)
        assert not ev.isAccepted()           # 没选中：方向键照旧逐帧
        _click(c, *_glyph_point(c, 0))
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Left, NONE)
        ev.ignore()
        assert c.event(ev) is True and ev.isAccepted()
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Space, NONE)
        ev.ignore()
        c.event(ev)
        assert not ev.isAccepted()           # 空格永远是播放
    finally:
        c.close()


# ─────────────────────────── 微移 / 删除 / 复制粘贴 ───────────────────────────

def test_arrow_nudge_is_one_undo_step():
    _app()
    c, _task, ovs = _canvas_texts()
    try:
        _click(c, *_glyph_point(c, 0))
        began = _counter(c.gesture_began)
        ended = _counter(c.gesture_ended)
        x0, y0 = ovs[0]["x"], ovs[0]["y"]
        for _ in range(3):
            _key(c, Qt.Key.Key_Left)
        _key(c, Qt.Key.Key_Down, SHIFT)
        assert (ovs[0]["x"], ovs[0]["y"]) == (x0 - 3, y0 + 10)
        assert len(began) == 1 and not ended
        c._ops_end_session()                  # 停手 700 ms 后
        assert len(ended) == 1
    finally:
        c.close()


def test_delete_copy_paste_duplicate_cut_and_cross_video_paste():
    _app()
    from cutora.batch import monitor_model as mm
    c, task, ovs = _canvas_texts()
    c2, task2, _ = _canvas_texts(n=1)
    try:
        structure = _counter(c.structure_changed)
        _click(c, *_glyph_point(c, 0))
        _key(c, Qt.Key.Key_C, CTRL)
        assert mm.get_clipboard()["kind"] == "overlays"
        _key(c, Qt.Key.Key_V, CTRL)
        assert len(task["text_overlays"]) == 3
        pasted = task["text_overlays"][-1]
        assert pasted["content"] == "TEXT0" and pasted["x"] == ovs[0]["x"] + 20
        assert pasted.get("id") != ovs[0].get("id")
        _key(c, Qt.Key.Key_D, CTRL)            # 选中的是刚粘贴的那条
        assert len(task["text_overlays"]) == 4
        assert task["text_overlays"][-1]["x"] == pasted["x"] + 20
        _key(c, Qt.Key.Key_Delete)
        assert len(task["text_overlays"]) == 3
        assert len(structure) >= 3
        # 剪切 → 另一条视频粘贴，原模原样同一位置
        c.repaint()
        _click(c, *_glyph_point(c, 1))
        _key(c, Qt.Key.Key_X, CTRL)
        assert [o["content"] for o in task["text_overlays"]].count("TEXT1") == 0
        c2.setFocus()
        _key(c2, Qt.Key.Key_V, CTRL)
        got = task2["text_overlays"][-1]
        assert got["content"] == "TEXT1" and (got["x"], got["y"]) == (300, 1100)
    finally:
        c.close()
        c2.close()


def test_escape_clears_selection_and_cancels_drag():
    _app()
    c, _task, ovs = _canvas_texts()
    try:
        cancelled = _counter(c.gesture_cancelled)
        x, y = _glyph_point(c, 0)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x + 40, y + 40))
        _key(c, Qt.Key.Key_Escape)
        assert cancelled and c._dragging_item is None and c._selected_item is None
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x + 40, y + 40, NONE,
                                   Qt.MouseButton.NoButton))
        _click(c, *_glyph_point(c, 1))
        assert c._selected_item is not None
        _key(c, Qt.Key.Key_Escape)
        assert c._selected_item is None
    finally:
        c.close()


# ─────────────────────────── Alt+拖复制 / Ctrl+拖缩放 ───────────────────────────

def test_alt_drag_duplicates_and_moves_the_copy():
    _app()
    c, task, ovs = _canvas_texts()
    try:
        structure = _counter(c.structure_changed)
        x, y = _glyph_point(c, 0)
        orig = (ovs[0]["x"], ovs[0]["y"])
        _drag(c, x, y, x + 60, y + 90, ALT)
        assert len(task["text_overlays"]) == 3
        assert (ovs[0]["x"], ovs[0]["y"]) == orig             # 原件不动
        copy = task["text_overlays"][-1]
        assert copy["content"] == "TEXT0"
        assert copy["y"] - orig[1] > 100                      # 副本跟着鼠标走
        assert structure
        # Alt 点一下不拖：不复制（仍是「穿透选择」）
        _click(c, *_glyph_point(c, 1), ALT)
        assert len(task["text_overlays"]) == 3
    finally:
        c.close()


def test_ctrl_drag_scales_text_about_its_center():
    _app()
    c, _task, ovs = _canvas_texts()
    try:
        ov = ovs[0]
        cx0 = ov["x"] + ov["w"] / 2
        cy0 = ov["y"] + ov["h"] / 2
        x, y = _glyph_point(c, 0)
        _drag(c, x, y, x + 60, y - 40, CTRL)
        assert ov["scale"] > 1.3
        assert abs(ov["x"] + ov["w"] / 2 - cx0) < 1e-6 and abs(ov["y"] + ov["h"] / 2 - cy0) < 1e-6
        assert (ov["w"], ov["h"]) == (500, 160)               # 无损：框不动、只改 scale
    finally:
        c.close()


def test_ctrl_wheel_scales_selected_else_zooms_view():
    _app()
    c, _task, ovs = _canvas_texts()
    try:
        x, y = _glyph_point(c, 0)
        base = c._preview_target_rect()
        _wheel(c, x, y, 120, CTRL)                              # 没选中 → 视图
        assert c._preview_target_rect().width() > base.width() * 1.1
        c.view_fit()
        assert c._preview_target_rect() == base
        c.repaint()
        x, y = _glyph_point(c, 0)
        _click(c, x, y)
        _wheel(c, x, y, 120, CTRL)                              # 选中了、在光标下 → 缩放对象
        assert ovs[0]["scale"] == pytest.approx(1.1)
        assert c._preview_target_rect() == base
    finally:
        c.close()


# ─────────────────────────── 视图 ───────────────────────────

def test_view_zoom_keeps_point_under_cursor_and_keys():
    _app()
    c, _task, _ovs = _canvas_texts()
    try:
        base = c._preview_target_rect()
        wx, wy = 300.0, 400.0
        before = c._video_xy(QPointF(wx, wy))
        c.view_zoom_at(3.0, QPointF(wx, wy))
        after = c._video_xy(QPointF(wx, wy))
        assert after[0] == pytest.approx(before[0], abs=0.5)
        assert after[1] == pytest.approx(before[1], abs=0.5)
        assert c._preview_target_rect().width() == pytest.approx(base.width() * 3.0)
        _key(c, Qt.Key.Key_Z, SHIFT)
        assert c._preview_target_rect() == base
        _key(c, Qt.Key.Key_0, CTRL)
        assert c._preview_target_rect().width() == pytest.approx(VW)
        # 中键拖平移
        r0 = c._preview_target_rect()
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 200, 200, NONE,
                                 Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton))
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 260, 230, NONE, Qt.MouseButton.MiddleButton,
                                Qt.MouseButton.MiddleButton))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 260, 230, NONE,
                                   Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton))
        r1 = c._preview_target_rect()
        assert (r1.x() - r0.x(), r1.y() - r0.y()) == pytest.approx((60, 30), abs=0.6)
        c.repaint()                                            # 放大状态下能正常画
        c.view_fit()
    finally:
        c.close()


# ─────────────────────────── 视频：分界线吸附 / Ctrl+拖 / 双击重置 ───────────────────────────

def _clip(tmp_path, color="0x3070C0"):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / ("c_%s.mp4" % color[2:]))
    if not os.path.isfile(out):
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=216x384:r=30:d=3", "-vf", "drawbox=c=%s@0.5:t=fill" % color,
                        "-pix_fmt", "yuv420p", out], check=True)
    return out


def _video_canvas(tmp_path, split=True):
    from cutora.batch.split_assign import assign_split
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(216, 384, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    tl.sources["m"] = MediaSource(_clip(tmp_path), duration=3.0, probe=False)
    tr.clips.append(Clip("m", 0.0, 3.0, 0.0))
    task = {"timeline": tl, "video": "", "dims": (216, 384), "image_overlays": [],
            "text_overlays": [], "segments": [], "style": {}, "duration": 3.0}
    if split:
        tr2 = Track("video")
        tl.add_track(tr2)
        tl.sources["m2"] = MediaSource(_clip(tmp_path, "0x30C030"), duration=3.0, probe=False)
        tr2.clips.append(Clip("m2", 0.0, 3.0, 0.0))
        assign_split(task, "top_bottom", [tr, tr2])
    c = VideoCanvas()
    c.setFixedSize(216, 384)
    c.set_data(task, 1.0)
    # 和程序里一样：视频层由预览线程合成好交给画布（画布据此收命中记录）
    from cutora.preview.engine import PreviewEngine
    c.set_program_frame(PreviewEngine().video_frame_at(task, 1.0))
    c.show()
    QApplication.processEvents()
    c.repaint()
    return c, task


def _w(c, vx, vy):
    t = c._preview_target_rect()
    return t.x() + vx * t.width() / c._video_dims[0], t.y() + vy * t.height() / c._video_dims[1]


def test_divider_drag_snaps_to_thirds_and_n_disables(tmp_path):
    _app()
    from cutora.batch import monitor_model as mm
    c, task = _video_canvas(tmp_path)
    try:
        committed = []
        c._commit_split_handle = lambda h, patch: committed.append(patch)
        hx, hy = _w(c, 108, 192)
        _drag(c, hx, hy, hx, _w(c, 0, 384 * (1 / 3 + 0.012))[1])
        assert committed[-1]["pos"] == pytest.approx(1 / 3)
        mm.set_divider_pos(task, "d1", 0.5)
        c.repaint()
        _key(c, Qt.Key.Key_N)                                  # 按住 N：不吸
        _drag(c, hx, hy, hx, _w(c, 0, 384 * (1 / 3 + 0.012))[1])
        _key(c, Qt.Key.Key_N, typ=QEvent.Type.KeyRelease)
        assert committed[-1]["pos"] != pytest.approx(1 / 3)
    finally:
        c.close()


def test_ctrl_drag_video_zooms_about_cursor_and_double_click_resets(tmp_path):
    _app()
    c, task = _video_canvas(tmp_path, split=False)
    try:
        clip = task["timeline"].tracks[0].clips[0]
        vx, vy = 150.0, 100.0
        x, y = _w(c, vx, vy)
        _drag(c, x, y, x + 50, y - 30, CTRL)
        s = clip.scale
        assert s > 1.3
        # 光标下那一点不动：片段中心 = 画布中心 + (x, y)，点 p 缩放后仍在 p
        cx, cy = 108 + clip.x, 192 + clip.y
        # 原来 p 相对中心 (vx-108, vy-192)；缩放后 (p - c') = s·(p - c0)
        assert (vx - cx) == pytest.approx(s * (vx - 108), abs=1e-6)
        assert (vy - cy) == pytest.approx(s * (vy - 192), abs=1e-6)
        c.repaint()
        c.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, x, y))
        assert (clip.x, clip.y, clip.scale) == (0.0, 0.0, 1.0)
    finally:
        c.close()


def test_video_arrow_nudge_moves_clip(tmp_path):
    _app()
    c, task = _video_canvas(tmp_path, split=False)
    try:
        clip = task["timeline"].tracks[0].clips[0]
        _click(c, *_w(c, 100, 100))
        assert c._selected_item and c._selected_item["type"] == "video_clip"
        _key(c, Qt.Key.Key_Right, SHIFT)
        _key(c, Qt.Key.Key_Up)
        assert (clip.x, clip.y) == (10.0, -1.0)
    finally:
        c.close()


# ─────────────────────────── 逐帧按帧率 ───────────────────────────

def test_frame_step_uses_project_fps():
    from cutora.model.timeline import Timeline
    from cutora.ui.main_window import MainWindow
    seeks = []
    fake = types.SimpleNamespace(tasks=[{"timeline": Timeline(1080, 1920, 60.0)}], curr_idx=0,
                                 _timeline_ms=1000, on_seek_request=seeks.append)
    fake._project_fps = types.MethodType(MainWindow._project_fps, fake)
    fake._step_frames = types.MethodType(MainWindow._step_frames, fake)
    MainWindow.shortcut_frame_next(fake)
    assert seeks[-1] == round(61 * 1000 / 60)
    fake._timeline_ms = seeks[-1]
    MainWindow.shortcut_frame_prev(fake)
    assert seeks[-1] == 1000
    fake.tasks = [{"timeline": Timeline(1080, 1920, 25.0)}]
    fake._timeline_ms = 0
    MainWindow.shortcut_frame_next(fake)
    assert seeks[-1] == 40


# ─────────────────────────── 中央监视器统一 ───────────────────────────

def test_monitor_swallows_global_keys_and_shares_clipboard():
    _app()
    from cutora.batch import monitor_model as mm
    from cutora.ui.central_monitor import MonitorGrid
    g = MonitorGrid()
    try:
        for key, mods, want in ((Qt.Key.Key_S, NONE, True), (Qt.Key.Key_Left, NONE, True),
                                (Qt.Key.Key_Space, NONE, True), (Qt.Key.Key_D, CTRL, True),
                                (Qt.Key.Key_M, CTRL | SHIFT, False), (Qt.Key.Key_S, CTRL, False)):
            ev = QKeyEvent(QEvent.Type.ShortcutOverride, key, mods)
            ev.ignore()
            g.event(ev)
            assert ev.isAccepted() == want, (key, mods)
        mm.set_clipboard({"kind": "overlays", "items": []})
        assert g._clipboard == {"kind": "overlays", "items": []}
        g._clipboard = None
        assert mm.get_clipboard() is None
    finally:
        g.shutdown()
