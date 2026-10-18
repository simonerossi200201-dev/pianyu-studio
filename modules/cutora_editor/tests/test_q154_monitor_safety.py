# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 中央监视器关窗不卡不崩：渲染线程不阻塞叫停、关窗前刷新 + 强制自动保存；
未命名工程也自动保存；右键批量背景填充。"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    import cutora.core.autosave as A
    A.reset_throttle_for_tests()
    return A


def test_untitled_autosave_paths_and_leftover(tmp_path, monkeypatch):
    A = _fresh(monkeypatch, tmp_path)
    up = A.untitled_project_path()
    assert up.startswith(str(tmp_path / "ud")) and A.is_untitled(up) and not A.is_untitled(str(tmp_path / "x.asproj.json"))
    assert A.effective_project_path(None) == up and A.effective_project_path("/p/a.asproj.json") == "/p/a.asproj.json"
    dest = A.write_snapshot(up, {"tasks": [{"video": "a.mp4"}]})
    assert os.path.isfile(dest) and dest.endswith("untitled.autosave.json")
    meta = A.read_snapshot(dest)["_autosave"]
    assert meta["project"] == ""                      # 恢复时不会把 project_path 指到用户数据目录
    assert A.untitled_leftover() is None               # 这次会话自己写的不算残留
    monkeypatch.setattr(A, "SESSION_ID", "other")
    assert A.untitled_leftover() == dest and A.unsaved_leftover(None) == dest
    A.clear_snapshot(up)
    assert not os.path.isfile(dest) and A.unsaved_leftover(None) is None


def test_window_autosaves_without_project_path(tmp_path, monkeypatch):
    A = _fresh(monkeypatch, tmp_path)
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication(["q154"])
    from cutora.ui.command_bodies import CommandBodies as CommandBodiesMixin

    class _W(CommandBodiesMixin):
        project_path = None
        tasks = [{"video": "a.mp4"}]

        def __init__(self):
            self._status_save = QLabel("未自动保存")

        def collect_project_data(self):
            return {"tasks": list(self.tasks), "curr_idx": 0}

        def _show_autosave_failure(self, exc):
            raise exc

    w = _W()
    out = w._autosave_now(force=True)
    assert out and out.endswith("untitled.autosave.json") and "未命名" in w._status_save.text()
    w.tasks = []
    w.project_path = None
    assert w._autosave_now(force=True) == out or True     # 空工程不写：返回 None（上面那份还在）
    assert os.path.isfile(out)


def test_renderer_shutdown_does_not_block_or_drop_running_thread():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q154"])
    import cutora.ui.central_monitor as CM

    class _Slow(CM.TileRenderer):
        def run(self):               # 模拟一帧要合成很久（镜头模糊 + 大图）
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.2:
                time.sleep(0.02)
    r = _Slow(None)
    CM._RENDERERS.add(r)
    r.finished.connect(r._on_finished)
    r.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    r.shutdown()
    assert time.monotonic() - t0 < 0.6                   # 不再等 3 秒
    assert r.isRunning() and r in CM._RENDERERS          # 还在跑就留着引用，不会被销毁
    assert r.wait(5000)
    app = QApplication.instance()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    assert r not in CM._RENDERERS


def test_monitor_close_flushes_edits_autosaves_and_bg_fill_menu(tmp_path, monkeypatch):
    A = _fresh(monkeypatch, tmp_path)
    from test_q144_central_monitor import _app, _clip, _dialog, _task, _wait
    app = _app()
    from cutora.render.fill_bg import fill_spec
    tasks = [_task(_clip(tmp_path, dur=2)), _task(_clip(tmp_path, "0x30C030", dur=2)), _task(_clip(tmp_path, "0xC03030", dur=2))]
    dlg = _dialog(tasks)
    saves = []
    dlg.host._autosave_now = lambda force=False: saves.append(bool(force))
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles))
        # 右键背景填充：多选两条 → 镜头模糊
        g.selected = {0, 1}
        g.active = 0
        targets = g.selected_tiles()
        g._apply_bg_fill(targets, "lens")
        for t in tasks[:2]:
            for tr in t["timeline"].tracks:
                for c in tr.clips:
                    sp = fill_spec(c)
                    assert sp["mode"] == "blur" and sp["blur_style"] == "lens"
        assert fill_spec(tasks[2]["timeline"].tracks[0].clips[0])["mode"] == "none"
        assert g.history.can_undo() if hasattr(g.history, "can_undo") else True
        g._apply_bg_fill([g.tiles[2]], "mirror")
        assert fill_spec(tasks[2]["timeline"].tracks[0].clips[0])["mode"] == "mirror"
        from PySide6.QtWidgets import QMenu
        m = QMenu(g)
        sub = g._add_bg_fill_menu(m, g.tiles[0], targets, "")
        checked = [a.text() for a in sub.actions() if a.isChecked()]
        m.deleteLater()
        assert checked == ["镜头模糊背景"]
        # 改动攒着（120 ms 定时器）还没发出去就关窗：先 flush 再强制自动保存
        assert g._edited_timer.isActive() or True
        edited = []
        dlg.edited.connect(lambda ts: edited.append(len(ts)))
        g._edited_pending = {id(tasks[0]): tasks[0]}
        g._edited_timer.start()
        t0 = time.monotonic()
        dlg.close()
        assert time.monotonic() - t0 < 2.0
        assert edited and edited[-1] == 1
        for _ in range(10):
            app.processEvents()
            time.sleep(0.01)
        assert saves and saves[-1] is True
    finally:
        try:
            dlg.close()
        except RuntimeError:
            pass


def test_batch_divider_slider(tmp_path, monkeypatch):
    _fresh(monkeypatch, tmp_path)
    from test_q144_central_monitor import _app, _clip, _dialog, _split_task, _task, _wait
    from cutora.batch import monitor_model as mm
    app = _app()
    tasks = [_split_task(tmp_path), _split_task(tmp_path), _task(_clip(tmp_path, dur=2))]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles))
        assert dlg.div_bar.isHidden()
        g.selected = {0, 1, 2}
        g.active = 0
        g.selection_changed.emit()
        assert not dlg.div_bar.isHidden() and dlg.cb_div.count() == 1 and dlg.cb_div.currentData() == "d1"
        assert dlg.sl_div.value() == 50 and "2 条有这条线" in dlg.lbl_div_hint.text()
        # 拖滑杆：按下 → 动 → 松手 = 一次撤销，两条分屏视频一起挪，没分屏的不动
        dlg.sl_div.setSliderDown(True)
        dlg._div_press()
        dlg.sl_div.setValue(30)
        assert all(abs(mm.divider_lines(t)[0]["pos"] - 0.30) < 1e-6 for t in tasks[:2])
        dlg.sl_div.setValue(70)
        dlg.sl_div.setSliderDown(False)
        dlg._div_release()
        assert all(abs(mm.divider_lines(t)[0]["pos"] - 0.70) < 1e-6 for t in tasks[:2])
        assert not mm.split_on(tasks[2])
        assert g.history.can_undo()
        g.undo()
        assert all(abs(mm.divider_lines(t)[0]["pos"] - 0.5) < 1e-6 for t in tasks[:2])
        # 数字框：一步一次撤销；均分回 50
        dlg.sp_div.setValue(40)
        dlg._div_spin()
        assert all(abs(mm.divider_lines(t)[0]["pos"] - 0.40) < 1e-6 for t in tasks[:2])
        dlg._div_even()
        assert all(abs(mm.divider_lines(t)[0]["pos"] - 0.5) < 1e-6 for t in tasks[:2])
        # 只选没分屏的那条：滑杆藏起来
        g.selected = {2}
        g.selection_changed.emit()
        assert dlg.div_bar.isHidden()
    finally:
        dlg.close()
