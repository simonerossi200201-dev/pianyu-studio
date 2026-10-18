# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q166 · AI 抠像：相邻对象合成一整块（去接缝）/ 点完一批一次算 / 彩色区域显示开关。"""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_q152d_ai_matte import _app, _mouse, _square_video, _wait_worker  # noqa: E402,F401
from test_q152d_ai_matte import _fake_backend_and_cache  # noqa: E402,F401  (autouse fixture)


def _two_halves(h=120, w=200, gap=2, soft=True):
    """左右两个对象，中间一条 gap 像素的缝，边缘各自半透明 —— 老的 max 合成会留一条缝。"""
    a = np.zeros((h, w), np.float32)
    b = np.zeros((h, w), np.float32)
    mid = w // 2
    a[20:100, 30:mid - gap // 2] = 1.0
    b[20:100, mid + (gap - gap // 2):170] = 1.0
    if soft:
        a[20:100, mid - gap // 2 - 1] = 0.5
        b[20:100, mid + (gap - gap // 2)] = 0.5
    return a, b


def test_merge_masks_fills_seam_keeps_outline():
    from cutora.fx import ai_matte as A
    a, b = _two_halves()
    plain = np.maximum(a, b)
    assert plain[60, 100] == 0.0 and plain[60, 98] == 0.5          # 老做法：缝
    m = A.merge_masks([a, b])
    assert m[60, 99] > 0.99 and m[60, 100] > 0.99 and m[60, 98] > 0.99
    # 外轮廓不动：背景还是 0，两个对象外边还是原样
    assert m[5, 100] == 0.0 and m[60, 10] == 0.0 and m[60, 190] == 0.0
    assert m[60, 30] == 1.0 and m[60, 29] == 0.0 and m[60, 169] == 1.0 and m[60, 170] == 0.0
    # 离得远的两个对象：中间不会被连起来
    far_a = np.zeros((120, 200), np.float32)
    far_b = np.zeros((120, 200), np.float32)
    far_a[20:100, 10:60] = 1.0
    far_b[20:100, 140:190] = 1.0
    mf = A.merge_masks([far_a, far_b])
    assert np.array_equal(mf, np.maximum(far_a, far_b))
    assert A.merge_masks([]) is None


def test_combined_raw_merge_option(tmp_path):
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    a, b = _two_halves()
    sid = S.new_session_id()
    with S.Session(sid, fps=30.0, src_w=200, src_h=120) as sess:
        sess.write(0, 1, (a * 255).astype(np.uint8), 1.0)
        sess.write(0, 2, (b * 255).astype(np.uint8), 1.0)
    objs = [{"id": 1}, {"id": 2}]
    sp = A.normalize({"session": sid, "objects": objs})
    assert sp["merge"] is True
    m = A.combined_raw(sp, 0)
    assert m[60, 100] > 0.99
    sp_off = A.normalize({"session": sid, "objects": objs, "merge": False})
    assert A.combined_raw(sp_off, 0)[60, 100] == 0.0
    assert A.signature(sp) != A.signature(sp_off)
    # 挖掉对象照样从合成后的整块里减
    sp_sub = A.normalize({"session": sid, "objects": [{"id": 1}, {"id": 2, "mode": "subtract"}]})
    ms = A.combined_raw(sp_sub, 0)
    assert ms[60, 50] == 1.0 and ms[60, 150] == 0.0


def _wait_all(ai, timeout=30.0):
    """等到连排队的那次也算完（done 信号里会马上开下一次）。"""
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        QApplication.processEvents()
        if not ai._running() and ai._pending_frame is None and ai._worker is None:
            break
        time.sleep(0.02)
    assert not ai._running() and ai._pending_frame is None


def _env(tmp_path):
    _app()
    from test_q145_canvas_shortcuts import _w
    from test_q31_keyframes import _task_with_clip
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.panels.property import PropertyPanel
    media = _square_video(str(tmp_path / "sq.mp4"), frames=6)
    task, tr, clip = _task_with_clip(media, start_t=0.0, duration=6 / 30.0, canvas=(320, 180))
    task["duration"] = 6 / 30.0
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    win._timeline_ms = 0
    c = VideoCanvas()
    c.setParent(win)
    c.setFixedSize(640, 360)
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.video_canvas = c
    win.show()
    c.set_data(task, 0.0)
    from cutora.preview.engine import PreviewEngine
    c.set_program_frame(PreviewEngine().video_frame_at(task, 0.0))
    c.repaint()
    QApplication.processEvents()
    c._selected_item = None
    from test_q145_canvas_shortcuts import _click
    _click(c, *_w(c, 160, 90))
    panel.load_data("video_clip", (tr, clip))
    return win, c, panel, clip, _w


def test_batch_points_compute_once_and_tint_toggle(tmp_path):
    from cutora.fx import matte_store as S
    win, c, panel, clip, _w = _env(tmp_path)
    try:
        ai = panel.vid_fx.ai
        assert not ai.chk_auto.isChecked()                   # 默认：先点完再算
        ai.btn_prompt.setChecked(True)
        sid = clip.ai_matte["session"]
        for sx in (40, 60, 70):                              # 连点三下，一个都不算
            x, y = _w(c, sx, 90)
            c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
            c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
            assert not ai._running()
        assert len(clip.ai_matte["prompts"]["0"]) == 3
        assert ai.frame_dirty(0) and "3 个点" in ai.btn_compute.text()
        assert S.read_alpha(sid, 0, [1])[0] is None
        # 画布上 Enter = 一次算完
        c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        assert ai._running()
        _wait_worker(ai)
        a, used = S.read_alpha(sid, 0, [1])
        assert used == 0 and a[90, 50] == 1.0 and not ai.frame_dirty(0)
        # 在算的时候又点：收下，算完自动再算一次（自动模式）
        ai.chk_auto.setChecked(True)
        x, y = _w(c, 45, 70)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
        assert ai._running()
        x, y = _w(c, 55, 100)
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))
        assert len(clip.ai_matte["prompts"]["0"]) == 5 and ai._pending_frame == 0
        _wait_all(ai)
        assert ai._pending_frame is None and not ai._running()
        # 彩色区域：H 切换 / 复选框同步；关掉以后画布不画叠加
        import cutora.ui.canvas_matte as CM
        calls = []
        orig = CM._overlay_image
        CM._overlay_image = lambda *a, **k: calls.append(1) or orig(*a, **k)
        try:
            c.repaint()
            QApplication.processEvents()
            assert calls
            calls.clear()
            c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_H, Qt.KeyboardModifier.NoModifier))
            assert ai.show_tint is False and not ai.chk_tint.isChecked()
            c.repaint()
            QApplication.processEvents()
            assert not calls
            ai.chk_tint.setChecked(True)
            assert ai.show_tint is True
        finally:
            CM._overlay_image = orig
        # 去接缝选项进工程
        ai.chk_merge.setChecked(False)
        assert clip.ai_matte["merge"] is False
        ai.chk_merge.setChecked(True)
        assert clip.ai_matte["merge"] is True
    finally:
        win.close()
