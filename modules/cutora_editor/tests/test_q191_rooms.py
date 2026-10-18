# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q191 · 主窗口房间 + 底部页面栏：批量加载 / 中央监视器不再是外置子窗口。

Luca：子窗口一开主窗口就钉在后面拖不动、看别的程序被挡住。现在只有一个窗口，切房间 = 换工作区。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_GPU_FX", "0")


def _pump(app, ms=60):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


@pytest.fixture(scope="module")
def win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q191_")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["cutora-q191"])
    from cutora.ui.main_window import MainWindow
    from cutora.model.timeline import Timeline
    w = MainWindow()
    w.resize(1400, 860)
    w.show()
    _pump(app)
    w.show_editor()
    _pump(app)
    d = tempfile.mkdtemp(prefix="q191v_")
    tasks = []
    for i in range(3):
        p = os.path.join(d, "v%02d.mp4" % i)
        open(p, "wb").write(b"x" * (10 + i))
        tasks.append({"video": p, "name": os.path.basename(p), "segments": [],
                      "timeline": Timeline(1080, 1920, 30.0), "text_overlays": [], "image_overlays": [],
                      "dims": [1080, 1920], "style": {}, "duration": 10.0, "text": ""})
    w.tasks = tasks
    w._q191_app = app
    yield w
    try:
        w.room_host.enter("edit")
    except Exception:
        pass


def _visible_docks(w):
    return sorted(d.objectName() for d in w.room_host._docks() if d.isVisible())


def test_page_bar_lists_rooms(win):
    rh = win.room_host
    assert list(rh.bar.buttons)[:3] == ["edit", "batch", "monitor"]
    assert rh.toolbar.isVisible() and rh.current == "edit"
    assert rh.bar.buttons["edit"].isChecked()


def test_batch_room_is_embedded_and_restores_editor(win):
    from PySide6.QtWidgets import QApplication
    rh = win.room_host
    before = _visible_docks(win)
    assert before
    win.open_script_insert_image()            # 菜单「批量 → 批量加载…」
    _pump(win._q191_app)
    dlg = rh.widget("batch")
    assert rh.current == "batch" and dlg is not None
    assert not dlg.isWindow(), "批量加载必须是主窗口里的控件，不是独立窗口"
    assert win.shell_stack.currentWidget().widget() is dlg      # Q193：外面套了一层滚动区
    assert _visible_docks(win) == []
    # 剪辑页快捷键让出来（空格 / Ctrl+Z 归房间里的表格），保存还在
    assert rh._muted
    assert not win.shortcut_space.isEnabled()
    keep = [a for a in win.findChildren(__import__("PySide6.QtGui", fromlist=["QAction"]).QAction)
            if any(s.toString() == "Ctrl+S" for s in a.shortcuts())]
    assert keep, "Ctrl+S 保存在房间里也要能用"
    # Esc 不会一下把房间（和粘了一半的表格）关掉
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    _pump(win._q191_app)
    assert rh.current == "batch" and rh.widget("batch") is dlg
    # 切回剪辑：面板原样回来，快捷键恢复，批量加载留着（页面栏上有小点）
    rh.enter("edit")
    _pump(win._q191_app)
    assert _visible_docks(win) == before
    assert win.shortcut_space.isEnabled() and not rh._muted
    assert rh.widget("batch") is dlg and rh.bar.buttons["batch"].badge
    # 再进来还是同一个（表格里填的东西不丢）
    rh.enter("batch")
    assert rh.widget("batch") is dlg
    # 「完成并退出」→ 拆掉，回剪辑
    dlg.reject()
    _pump(win._q191_app)
    assert rh.current == "edit" and rh.widget("batch") is None
    assert _visible_docks(win) == before
    assert QApplication.activeModalWidget() is None


def test_batch_room_rebuilds_when_tasks_change(win):
    rh = win.room_host
    rh.enter("batch")
    first = rh.widget("batch")
    rh.enter("edit")
    win.tasks = list(win.tasks[:2])
    rh.enter("batch")
    assert rh.widget("batch") is not first
    assert len(rh.widget("batch").picker._tasks) == 2
    rh.widget("batch").reject()
    _pump(win._q191_app)


def test_monitor_room_is_released_on_leave(win):
    rh = win.room_host
    win.open_central_monitor()
    _pump(win._q191_app)
    mon = rh.widget("monitor")
    assert rh.current == "monitor" and mon is not None and not mon.isWindow()
    rh.enter("edit")
    _pump(win._q191_app)
    assert rh.widget("monitor") is None, "监视器离开就拆（几十格缩略图 + 解码器不留着占内存）"
    assert getattr(mon, "_torn_down", False)


def test_batch_monitor_button_switches_rooms_and_comes_back(win):
    rh = win.room_host
    rh.enter("batch")
    dlg = rh.widget("batch")
    dlg.btn_monitor.click()
    _pump(win._q191_app)
    assert rh.current == "monitor"
    mon = rh.widget("monitor")
    assert getattr(mon, "_room_back", None) == "batch"
    rh.enter("batch")
    assert rh.widget("batch") is dlg and rh.widget("monitor") is None
    dlg.reject()
    _pump(win._q191_app)


def test_monitor_open_in_main_returns_to_edit(win):
    rh = win.room_host
    rh.enter("monitor")
    mon = rh.widget("monitor")
    mon._open_in_main(win.tasks[0])
    _pump(win._q191_app)
    assert rh.current == "edit"


def test_closing_inside_a_room_saves_editor_layout(win):
    rh = win.room_host
    rh.enter("batch")
    assert _visible_docks(win) == []
    rh.shutdown()
    assert rh.current == "edit" and rh.widget("batch") is None
    assert _visible_docks(win)
