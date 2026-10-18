# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q145 第二期 · 时间线快捷操作：V/C/R、Delete / Shift+Delete（波纹）、Q/W、↑/↓、
, / .（Shift ×10）、Ctrl+C/X/V/D、Ctrl+= / −、Ctrl+滚轮以光标为中心、擦洗半分辨率、
多选整组拖（含文字 / 图片多选）、裁剪手柄撤销；以及画布按显示像素合成视频层。

全部走真的 MainWindow + 命令总线：每个操作只记一条撤销，撤销后数据回到原样。"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q145b_"))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

NONE = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
PPS = 80.0
FPS = 25.0


def _app():
    return QApplication.instance() or QApplication(["q145b"])


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None


_WIN = None


def _window():
    global _WIN
    if _WIN is None:
        _app()
        from cutora.ui.main_window import MainWindow
        win = MainWindow()
        _silence(win)
        win.resize(1400, 900)
        win.show()
        if callable(getattr(win, "show_editor", None)):
            win.show_editor()
        QApplication.processEvents()
        _WIN = win
    return _WIN


def _task():
    """主视频两段 [0,4) [4,8)、一条文字 [1,3)、一条图片 [5,7)、两条字幕 [0.5,1.5) [6,7)。"""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, FPS)
    video = Track("video")
    video.is_main_video = True
    video.clips.append(Clip("src-a", 0.0, 4.0, 0.0))
    video.clips.append(Clip("src-b", 10.0, 14.0, 4.0))
    text_tr = Track("text")
    image_tr = Track("image")
    subs_tr = Track("subs")
    tl.tracks.extend([video, text_tr, image_tr, subs_tr])
    text = {"type": "text", "id": 101, "content": "你好", "start": 1.0, "end": 3.0,
            "x": 20, "y": 20, "w": 200, "h": 60, "fontsize": 40, "font_family": "Arimo",
            "color": "#FFFFFF", "opacity": 255, "rotation": 0, "_track_id": text_tr.id}
    image = {"type": "image", "id": 202, "start": 5.0, "end": 7.0, "x": 0, "y": 0, "w": 320, "h": 180,
             "opacity": 255, "elements": [], "_track_id": image_tr.id}
    segs = [{"start": 0.5, "end": 1.5, "text": "一", "words": []},
            {"start": 6.0, "end": 7.0, "text": "二", "words": []}]
    task = {"video": "q145b.mp4", "text": "", "segments": segs, "overlays": [], "text_overlays": [text],
            "image_overlays": [image], "audio_clips": [], "dims": [320, 180], "style": {},
            "src_volume": 1.0, "intro_media": None, "timeline": tl, "video_overlay": {},
            "audio_tracks": [], "duration": 8.0, "guard_error": None}
    from cutora.subtitle.stylepack import ensure_default_pack
    ensure_default_pack(task)
    return task


def _load(task=None):
    from cutora.core.commands import get_bus
    win = _window()
    task = task or _task()
    win.tasks = [task]
    win.curr_idx = 0
    win.project_path = None
    win.file_list.blockSignals(True)
    win.file_list.clear()
    win.file_list.addItem("q145b.mp4")
    win.file_list.blockSignals(False)
    get_bus().bind_window(win)
    _settle()
    win.history_mgr.history = []           # 每个用例从干净的撤销栈开始
    win.history_mgr.current_index = -1
    tl = win.timeline
    tl.pixels_per_second = PPS
    tl.scroll_x = 0
    tl.scroll_y = 0
    tl.set_data(8000, task)
    tl._reset_selection()
    tl._ov_multi = []
    tl.set_tool("select")
    tl.position = 0
    QApplication.processEvents()
    return win, tl, task


def _cur(win):
    return win.tasks[win.curr_idx]


def _clips(task, kind="video"):
    tr = next(t for t in task["timeline"].tracks if t.type == kind)
    return tr, list(tr.clips)


def _select_mixed(tl, task, clip_idx=None, texts=(), images=(), segs=()):
    """框选能得到的混合选择：片段 + 文字 / 图片 + 字幕。"""
    tl._reset_selection()
    vtr = next(t for t in task["timeline"].tracks if t.type == "video")
    tl._clip_sel = [(str(vtr.id), i) for i in ([] if clip_idx is None else [clip_idx])]
    tl._ov_multi = [task["text_overlays"][i] for i in texts] + [task["image_overlays"][i] for i in images]
    tl.selected_indices = list(segs)
    QApplication.processEvents()


def _key(tl, key, mods=NONE):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, mods)
    tl.keyPressEvent(ev)
    QApplication.processEvents()
    return ev


def _settle(ms=150):
    """等主窗口 50 ms 的预览合并定时器跑完（它以前会在编辑后再补一条「操作」撤销）。"""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    QApplication.processEvents()


def _hist(win):
    _settle()
    return len(win.history_mgr.history)


def _row_y(tl, kind):
    for track, y, h in tl._track_positions()["rows"]:
        if getattr(track, "type", None) == kind:
            return int(y + h / 2)
    raise AssertionError(kind)


def _x(tl, t):
    return int(t * tl.pixels_per_second - tl.scroll_x)


def _click(tl, t, kind, mods=NONE):
    from PySide6.QtTest import QTest
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, mods, QPoint(_x(tl, t), _row_y(tl, kind)))
    QApplication.processEvents()


def _drag(tl, t0, t1, kind, mods=NONE, steps=6):
    y = _row_y(tl, kind)
    x0, x1 = _x(tl, t0), _x(tl, t1)

    def ev(typ, x, buttons, button=Qt.MouseButton.LeftButton):
        return QMouseEvent(typ, QPointF(x, y), QPointF(x, y), button, buttons, mods)

    tl.mousePressEvent(ev(QEvent.Type.MouseButtonPress, x0, Qt.MouseButton.LeftButton))
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        tl.mouseMoveEvent(ev(QEvent.Type.MouseMove, x, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    tl.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, x1, Qt.MouseButton.NoButton))
    QApplication.processEvents()


# ───────────────────────────── 工具切换 ─────────────────────────────

def test_vcr_switch_tool_and_toolbar_follows():
    win, tl, _task_ = _load()
    cont = win.timeline_container if hasattr(win, "timeline_container") else tl.parent()
    while cont is not None and not hasattr(cont, "btn_tool_razor"):
        cont = cont.parent()
    assert cont is not None, "找不到时间线工具栏"
    _key(tl, Qt.Key.Key_C)
    assert tl.tool == "razor" and cont.btn_tool_razor.isChecked()
    _key(tl, Qt.Key.Key_R)
    assert tl.tool == "speed" and cont.btn_tool_speed.isChecked()
    _key(tl, Qt.Key.Key_V)
    assert tl.tool == "select" and cont.btn_tool_select.isChecked()


# ───────────────────────────── 删除 / 波纹删除 ─────────────────────────────

def test_delete_clip_and_overlay_one_undo():
    win, tl, task = _load()
    _select_mixed(tl, task, clip_idx=0, texts=[0])
    assert len(tl._sel_clips()) == 1 and len(tl._sel_overlays()) == 1
    n = _hist(win)
    _key(tl, Qt.Key.Key_Backspace)          # Q157：Backspace = 普通删除；Delete = 波纹删除（达芬奇）
    task = _cur(win)
    _tr, clips = _clips(task)
    assert len(clips) == 1 and abs(clips[0].start_t - 4.0) < 1e-6   # 普通删除不前移
    assert task["text_overlays"] == []
    assert _hist(win) == n + 1
    win.undo()
    task = _cur(win)
    assert len(_clips(task)[1]) == 2 and len(task["text_overlays"]) == 1


def test_shift_delete_ripples_later_clip():
    win, tl, _t = _load()
    _click(tl, 1.5, "video")
    n = _hist(win)
    _key(tl, Qt.Key.Key_Delete)            # Q157：Delete 本身就是波纹删除（Shift+Delete 也一样）
    task = _cur(win)
    _tr, clips = _clips(task)
    assert len(clips) == 1
    assert abs(clips[0].start_t - 0.0) < 1e-6, clips[0].start_t   # 后面那段前移补位
    assert abs(task["segments"][-1]["start"] - 2.0) < 1e-6          # 字幕跟着前移
    assert _hist(win) == n + 1
    win.undo()
    _tr, clips = _clips(_cur(win))
    assert [round(c.start_t, 3) for c in clips] == [0.0, 4.0]


# ───────────────────────────── Q / W 波纹修剪 ─────────────────────────────

def test_q_w_ripple_trim_to_playhead():
    win, tl, _t = _load()
    tl.position = 1000                      # 第一段中间 1.0 s
    seeks = []
    tl.seek_request.connect(seeks.append)
    try:
        _key(tl, Qt.Key.Key_Q)
    finally:
        tl.seek_request.disconnect(seeks.append)
    _tr, clips = _clips(_cur(win))
    first = min(clips, key=lambda c: c.start_t)
    assert abs(first.start_t) < 1e-6 and abs(first.in_t - 1.0) < 1e-6 and abs(first.out_t - 4.0) < 1e-6
    second = max(clips, key=lambda c: c.start_t)
    assert abs(second.start_t - 3.0) < 1e-6
    assert seeks and seeks[-1] == 0
    win.undo()
    tl = win.timeline
    tl.position = 5000                      # 第二段 [4,8) 中间 5.0 s
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)    # Q157：Shift+] = W（达芬奇）
    _tr, clips = _clips(_cur(win))
    second = max(clips, key=lambda c: c.start_t)
    assert abs(second.start_t - 4.0) < 1e-6 and abs(second.out_t - 11.0) < 1e-6


# ───────────────────────────── ↑ / ↓ 剪辑点 ─────────────────────────────

def test_up_down_jump_edit_points():
    _win, tl, _t = _load()
    seeks = []
    tl.seek_request.connect(seeks.append)
    try:
        tl.position = 1000
        _key(tl, Qt.Key.Key_Down)
        assert seeks[-1] == 4000
        tl.position = 4000
        _key(tl, Qt.Key.Key_Down)
        assert seeks[-1] == 8000
        tl.position = 4000
        _key(tl, Qt.Key.Key_Up)
        assert seeks[-1] == 0
    finally:
        tl.seek_request.disconnect(seeks.append)


# ───────────────────────────── , / . 微移 ─────────────────────────────

def test_comma_period_nudge_clip_overlay_and_segment():
    win, tl, task = _load()
    _select_mixed(tl, task, clip_idx=1, images=[0], segs=[1])   # 第二段 + 图片 + 字幕「二」
    n = _hist(win)
    _key(tl, Qt.Key.Key_Period)
    task = _cur(win)
    _tr, clips = _clips(task)
    assert abs(max(c.start_t for c in clips) - (4.0 + 1 / FPS)) < 1e-6
    assert abs(task["image_overlays"][0]["start"] - (5.0 + 1 / FPS)) < 1e-6
    assert abs(task["segments"][1]["start"] - (6.0 + 1 / FPS)) < 1e-6
    assert _hist(win) == n + 1
    _key(tl, Qt.Key.Key_Comma, SHIFT)      # ×10 往回
    task = _cur(win)
    assert abs(task["image_overlays"][0]["start"] - (5.0 - 9 / FPS)) < 1e-6
    win.undo()
    win.undo()
    task = _cur(win)
    assert abs(task["image_overlays"][0]["start"] - 5.0) < 1e-6
    assert abs(max(c.start_t for c in _clips(task)[1]) - 4.0) < 1e-6


# ───────────────────────────── 复制 / 剪切 / 粘贴 / 原地复制 ─────────────────────────────

def test_copy_paste_at_playhead_and_duplicate():
    win, tl, task = _load()
    _select_mixed(tl, task, clip_idx=0, texts=[0])
    _key(tl, Qt.Key.Key_C, CTRL)
    tl.position = 8000
    n = _hist(win)
    _key(tl, Qt.Key.Key_V, CTRL)
    task = _cur(win)
    _tr, clips = _clips(task)
    assert len(clips) == 3
    assert any(abs(c.start_t - 8.0) < 1e-6 and c.source_id == "src-a" for c in clips)
    texts = task["text_overlays"]
    assert len(texts) == 2
    pasted = [o for o in texts if abs(o["start"] - 9.0) < 1e-6]
    assert pasted and pasted[0]["id"] != 101           # 相对位置保留（文字晚 1 s），新 id
    assert _hist(win) == n + 1
    win.undo()
    assert len(_clips(_cur(win))[1]) == 2 and len(_cur(win)["text_overlays"]) == 1

    win2, tl2, _t2 = _load()
    _click(tl2, 6.0, "image")
    _key(tl2, Qt.Key.Key_D, CTRL)
    imgs = _cur(win2)["image_overlays"]
    assert len(imgs) == 2 and abs(imgs[1]["start"] - 7.0) < 1e-6    # 接在后面


def test_cut_removes_and_paste_restores():
    win, tl, _t = _load()
    _click(tl, 2.0, "text")
    _key(tl, Qt.Key.Key_X, CTRL)
    assert _cur(win)["text_overlays"] == []
    tl.position = 4000
    _key(tl, Qt.Key.Key_V, CTRL)
    texts = _cur(win)["text_overlays"]
    assert len(texts) == 1 and abs(texts[0]["start"] - 4.0) < 1e-6 and abs(texts[0]["end"] - 6.0) < 1e-6


def test_locked_track_is_not_deleted():
    win, tl, task = _load()
    vtr, _c = _clips(task)
    vtr.locked = True
    _click(tl, 1.5, "video")
    tl.selected_video_clip = (vtr, vtr.clips[0])
    n = _hist(win)
    _key(tl, Qt.Key.Key_Delete)
    assert len(_clips(_cur(win))[1]) == 2
    assert _hist(win) == n                     # 什么也没改，不留空撤销


# ───────────────────────────── 缩放 / 擦洗 ─────────────────────────────

def test_ctrl_wheel_zooms_about_cursor_and_ctrl_equal():
    _win, tl, _t = _load()
    tl.scroll_x = 40
    x = 300.0
    t_before = (x + tl.scroll_x) / tl.pixels_per_second
    ev = QWheelEvent(QPointF(x, 30), QPointF(x, 30), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, CTRL, Qt.ScrollPhase.NoScrollPhase, False)
    tl.wheelEvent(ev)
    assert tl.pixels_per_second > PPS
    t_after = (x + tl.scroll_x) / tl.pixels_per_second
    assert abs(t_after - t_before) < 1e-6          # 光标下的时刻不动
    pps = tl.pixels_per_second
    _key(tl, Qt.Key.Key_Minus, CTRL)
    assert tl.pixels_per_second < pps
    pps = tl.pixels_per_second
    _key(tl, Qt.Key.Key_Equal, CTRL)
    assert tl.pixels_per_second > pps


def test_scrub_uses_half_res_then_full():
    win, tl, _t = _load()
    tl._scrub_to(200.0)
    assert tl._scrubbing and win._preview_interaction_scale() <= 0.5
    tl._scrub_end(240.0)
    assert not tl._scrubbing


# ───────────────────────────── 多选整组拖 ─────────────────────────────

def test_group_drag_moves_all_selected_clips_one_undo():
    from cutora.model.timeline_model import Clip
    task = _task()
    vtr, _c = _clips(task)
    vtr.clips[1].start_t = 5.0                 # 留空隙好拖
    vtr.clips.append(Clip("src-c", 0.0, 1.0, 10.0))
    win, tl, task = _load(task)
    tl.snap_enabled = False
    _click(tl, 1.0, "video")
    _click(tl, 6.0, "video", CTRL)
    assert len(tl._sel_clips()) == 2
    n = _hist(win)
    _drag(tl, 6.0, 6.5, "video")
    starts = sorted(round(c.start_t, 3) for c in _clips(_cur(win))[1])
    assert starts == [0.5, 5.5, 10.0], starts
    assert _hist(win) == n + 1
    win.undo()
    starts = sorted(round(c.start_t, 3) for c in _clips(_cur(win))[1])
    assert starts == [0.0, 5.0, 10.0]


def test_overlay_multiselect_group_drag_one_undo():
    win, tl, task = _load()
    tl.snap_enabled = False
    _click(tl, 2.0, "text")
    _click(tl, 6.0, "image", CTRL)
    assert len(tl._sel_overlays()) == 2
    assert tl.ov_is_selected("image_overlays", 0, task["image_overlays"][0])
    n = _hist(win)
    _drag(tl, 2.0, 3.0, "text")
    task = _cur(win)
    assert abs(task["text_overlays"][0]["start"] - 2.0) < 1e-6
    assert abs(task["image_overlays"][0]["start"] - 6.0) < 1e-6
    assert _hist(win) == n + 1
    win.undo()
    task = _cur(win)
    assert abs(task["text_overlays"][0]["start"] - 1.0) < 1e-6
    assert abs(task["image_overlays"][0]["start"] - 5.0) < 1e-6


def test_plain_click_drops_overlay_multiselect():
    _win, tl, _task_ = _load()
    _click(tl, 2.0, "text")
    _click(tl, 6.0, "image", CTRL)
    assert len(tl._sel_overlays()) == 2
    _click(tl, 2.0, "text")
    assert len(tl._sel_overlays()) == 1


# ───────────────────────────── 裁剪手柄撤销 ─────────────────────────────

def test_trim_handle_drag_is_one_undo():
    win, tl, _t = _load()
    tl._trim_undo_pushed = False
    tl._dragging_trim = True
    tl._trim_drag_start_x = 0.0
    tl._trim_drag_start_sec = 0.0
    n = _hist(win)
    for x in (20.0, 40.0, 80.0):
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, 10), QPointF(x, 10),
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, NONE)
        tl.mouseMoveEvent(ev)
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(80, 10), QPointF(80, 10),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, NONE)
    tl.mouseReleaseEvent(ev)
    assert abs(float(_cur(win).get("trim_start_sec") or 0) - 1.0) < 1e-6
    assert _hist(win) == n + 1
    win.undo()
    assert float(_cur(win).get("trim_start_sec") or 0) == 0.0


# ───────────────────────────── 画布按显示像素合成 ─────────────────────────────

def test_display_scale_step():
    from cutora.preview.engine import display_scale_step
    assert display_scale_step(0.5) == 0.5
    assert display_scale_step(0.51) == 0.625
    assert display_scale_step(0.1) == 0.25
    assert display_scale_step(0.95) == 1.0
    assert display_scale_step(3.0) == 1.0
    assert display_scale_step(float("nan")) == 1.0
    assert display_scale_step(0) == 1.0


def test_canvas_preview_scale_follows_display_size():
    win, _tl, task = _load()
    canvas = win.video_canvas
    if not canvas.isVisible() or canvas.width() < 10:
        pytest.skip("画布没有显示")
    from cutora.model.canvas import canvas_wh
    w, h = canvas_wh(task)
    fit = min(canvas.width() / w, canvas.height() / h) * float(canvas.devicePixelRatioF() or 1.0)
    assert win._preview_interaction_scale() == 1.0     # 暂停：满分辨率，画面和以前一模一样
    win._timeline_playing = True
    try:
        s = win._preview_interaction_scale()
        assert s >= min(1.0, fit) - 1e-9      # 播放：永远不低于屏幕上实际显示的像素
        canvas._view_zoom = 8.0               # 放大查看 → 满分辨率
        try:
            assert win._preview_interaction_scale() == 1.0
        finally:
            canvas._view_zoom = 1.0
    finally:
        win._timeline_playing = False


def test_small_display_uses_reduced_frame_and_export_frame_stays_full():
    """1080×1920 的工程放在小画布里：视频层按显示像素合成；「导出当前帧」仍是满分辨率。"""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.canvas import VideoCanvas
    from PySide6.QtGui import QImage
    tl = Timeline(1080, 1920, 30.0)
    tr = Track("video")
    tr.is_main_video = True
    tl.tracks.append(tr)
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [],
            "image_overlays": [], "duration": 2.0, "dims": [1080, 1920]}
    win = _window()
    win.tasks = [task]
    win.curr_idx = 0
    canvas = win.video_canvas
    canvas.set_data(task, 0.0)
    QApplication.processEvents()
    if not canvas.isVisible() or canvas.width() < 10:
        pytest.skip("画布没有显示")
    assert win._preview_interaction_scale() == 1.0
    win._timeline_playing = True
    try:
        s = win._preview_interaction_scale()
    finally:
        win._timeline_playing = False
    fit = min(canvas.width() / 1080.0, canvas.height() / 1920.0) * float(canvas.devicePixelRatioF() or 1.0)
    if fit < 0.85:
        assert s < 1.0
    small = QImage(max(1, int(1080 * s)), max(1, int(1920 * s)), QImage.Format.Format_ARGB32_Premultiplied)
    small.fill(0)
    canvas.set_program_frame(small)
    saved = {}

    def fake_dialog(*_a, **_k):
        return (os.path.join(tempfile.mkdtemp(prefix="q145b_"), "f.png"), "")

    import cutora.ui.canvas as cmod
    from PySide6.QtWidgets import QMessageBox
    old = cmod.QFileDialog.getSaveFileName
    cmod.QFileDialog.getSaveFileName = staticmethod(fake_dialog)
    QMessageBox.information = staticmethod(lambda *a, **k: saved.setdefault("ok", True))
    try:
        from cutora.preview import engine as eng_mod
        seen = []
        orig = eng_mod.PreviewEngine.video_frame_at

        def spy(self_, task_, t, size=None, scale=1.0):
            seen.append(scale)
            return orig(self_, task_, t, size=size, scale=scale)

        eng_mod.PreviewEngine.video_frame_at = spy
        try:
            canvas.export_current_frame()
        finally:
            eng_mod.PreviewEngine.video_frame_at = orig
    finally:
        cmod.QFileDialog.getSaveFileName = old
    if small.width() != 1080:
        assert seen and seen[-1] == 1.0          # 导出当前帧重合成了一张满分辨率
    assert canvas._program_frame is small          # 画布上的帧放回原样
    assert isinstance(VideoCanvas, type)


def test_paused_canvas_upgrades_small_playback_frame():
    """播放时拿到的是显示像素的小图；停下来画布要自己补一张满分辨率。"""
    from PySide6.QtGui import QImage
    win, _tl, task = _load()
    canvas = win.video_canvas
    canvas.set_data(task, 0.0)
    small = QImage(160, 90, QImage.Format.Format_ARGB32_Premultiplied)
    small.fill(0)
    canvas.set_program_frame(small)
    calls = []
    old = win._present_preview_frame
    win._present_preview_frame = lambda: calls.append(1)
    try:
        win._timeline_playing = True
        canvas._request_sharper_frame()
        playing_calls = len(calls)
        win._timeline_playing = False
        canvas._request_sharper_frame()
    finally:
        win._present_preview_frame = old
        win._timeline_playing = False
    if canvas.isVisible() and canvas.width() > 10:
        assert len(calls) == playing_calls + 1


# ───────────────────────────── Q157 达芬奇手感 ─────────────────────────────

def test_delete_key_ripples_and_shift_bracket_trims_start():
    win, tl, _t = _load()
    _click(tl, 1.5, "video")
    _key(tl, Qt.Key.Key_Delete)
    _tr, clips = _clips(_cur(win))
    assert len(clips) == 1 and abs(clips[0].start_t) < 1e-6          # Delete = 连空隙一起删
    win.undo()
    tl = win.timeline
    tl.position = 1000
    _key(tl, Qt.Key.Key_BracketLeft, SHIFT)                           # Shift+[ = 删掉播放头左边
    _tr, clips = _clips(_cur(win))
    first = min(clips, key=lambda c: c.start_t)
    assert abs(first.in_t - 1.0) < 1e-6 and abs(first.start_t) < 1e-6


def test_grab_playhead_head_and_drag_scrubs():
    win, tl, _t = _load()
    tl.position = 2000
    seeks = []
    tl.seek_request.connect(seeks.append)
    y = 10                                   # 标尺里、播放头头部
    x0 = int(tl.playhead_screen_x())

    def ev(typ, x, buttons, button=Qt.MouseButton.LeftButton):
        return QMouseEvent(typ, QPointF(x, y), QPointF(x, y), button, buttons, NONE)
    try:
        tl.mousePressEvent(ev(QEvent.Type.MouseButtonPress, x0 + 3, Qt.MouseButton.LeftButton))
        assert tl.mode == "seek" and tl._scrubbing
        for t in (2.5, 3.0, 3.5):
            tl.mouseMoveEvent(ev(QEvent.Type.MouseMove, _x(tl, t), Qt.MouseButton.LeftButton,
                                 Qt.MouseButton.NoButton))
        tl.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, _x(tl, 3.5), Qt.MouseButton.NoButton))
    finally:
        tl.seek_request.disconnect(seeks.append)
    assert seeks[-1] == 3500 and len(seeks) >= 4 and not tl._scrubbing


def test_playback_end_follows_clips_and_render_end():
    from cutora.preview.engine import playback_end_sec, timeline_duration_sec
    win, tl, task = _load()
    assert abs(timeline_duration_sec(task) - 8.0) < 1e-6
    task["duration"] = 4.0                   # 原视频只有 4 s：后面接的片段照样算进去
    assert abs(playback_end_sec(task) - 8.0) < 1e-6
    task["trim_end_sec"] = 12.0              # 渲染范围拉到 12 s → 总时长 12 s
    assert abs(playback_end_sec(task) - 12.0) < 1e-6
    assert win._timeline_end_ms() == 12000
