# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q201: demote scatter into batch-load tab; drop page-bar key."""
from __future__ import annotations

import inspect
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


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q201"])


def _pump(app, ms=80):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def test_build_dialog_accepts_shared_picker(monkeypatch):
    from PySide6.QtWidgets import QDialog, QPushButton, QWidget
    from cutora.batch import script_broll_scatter as sc
    from cutora.batch.script_ui import TaskPicker

    _app()
    monkeypatch.setattr(sc, "load_last_sources", lambda: [])
    src = inspect.getsource(sc.build_dialog)
    assert "close_room" not in src
    assert "apply_plugin_style" not in src
    sig = inspect.signature(sc.build_dialog)
    assert "picker" in sig.parameters
    assert sig.parameters["picker"].default is None

    host = QWidget()
    picker = TaskPicker([])
    tab = sc.build_dialog(host, picker=picker)
    try:
        assert not isinstance(tab, QDialog)
        assert not tab.isWindow()
        assert tab.picker is picker
        assert tab.findChildren(TaskPicker) == []
        texts = [b.text() for b in tab.findChildren(QPushButton)]
        assert "完成" in texts
        assert "关闭" not in texts
        assert tab.btn_close.text() == "完成"
        assert tab.objectName() == "batchScatterTab"
    finally:
        tab.deleteLater()
        picker.deleteLater()
        host.close()


def test_build_dialog_none_still_owns_picker(monkeypatch):
    from PySide6.QtWidgets import QDialog, QPushButton
    from cutora.batch import script_broll_scatter as sc
    from cutora.batch.script_ui import TaskPicker

    _app()
    monkeypatch.setattr(sc, "load_last_sources", lambda: [])
    dlg = sc.build_dialog(None)
    try:
        assert isinstance(dlg, QDialog)
        assert dlg.picker is not None
        assert isinstance(dlg.picker, TaskPicker)
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        assert "完成" in texts
        assert "关闭" not in texts
    finally:
        dlg.deleteLater()


def test_rooms_install_does_not_register_scatter():
    from cutora.ui import rooms

    src = inspect.getsource(rooms.install)
    assert 'register("scatter"' not in src
    assert 'register("stitch"' in src
    assert not hasattr(rooms, "_make_scatter_room")
    assert not hasattr(rooms, "_enter_scatter_room")


@pytest.fixture(scope="module")
def win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q201_")
    app = _app()
    from cutora.ui.main_window import MainWindow
    from cutora.model.timeline import Timeline

    w = MainWindow()
    w.resize(1400, 860)
    w.show()
    _pump(app)
    w.show_editor()
    _pump(app)
    d = tempfile.mkdtemp(prefix="q201v_")
    tasks = []
    for i in range(3):
        p = os.path.join(d, "v%02d.mp4" % i)
        open(p, "wb").write(b"x" * (10 + i))
        tasks.append({
            "video": p, "name": os.path.basename(p), "segments": [],
            "timeline": Timeline(1080, 1920, 30.0), "text_overlays": [],
            "image_overlays": [], "dims": [1080, 1920], "style": {},
            "duration": 10.0, "text": "",
        })
    w.tasks = tasks
    w._q201_app = app
    yield w
    try:
        w.room_host.enter("edit")
    except Exception:
        pass


def test_scatter_not_in_page_bar(win):
    rh = win.room_host
    assert "scatter" not in rh.bar.buttons
    assert "scatter" not in rh.rooms
    assert list(rh.bar.buttons)[:3] == ["edit", "batch", "monitor"]
    assert list(rh.bar.buttons) == ["edit", "batch", "monitor", "stitch"]


def test_scatter_is_last_batch_tab(win):
    from cutora.batch.script_ui import TaskPicker

    rh = win.room_host
    rh.enter("batch")
    _pump(win._q201_app, 150)
    dlg = rh.widget("batch")
    names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert names[-1] == "片段散落"
    tab = dlg.scatter_tab
    assert tab is not None
    assert dlg.tabs.widget(dlg.tabs.count() - 1) is tab
    assert tab.picker is dlg.picker
    assert tab.findChildren(TaskPicker) == []
    assert tab.btn_close.text() == "完成"
    rh.enter("edit")


def test_menu_jumps_to_batch_scatter_tab(win):
    from PySide6.QtGui import QAction

    titles = [a.text() for a in win.findChildren(QAction)]
    assert "片段散落…" in titles

    rh = win.room_host
    win.open_script_broll_scatter()
    _pump(win._q201_app, 150)
    assert rh.current == "batch"
    dlg = rh.widget("batch")
    assert dlg is not None and not dlg.isWindow()
    assert dlg.tabs.currentWidget() is dlg.scatter_tab
    dlg.scatter_tab.btn_close.click()
    _pump(win._q201_app)
    assert rh.current == "batch" and rh.widget("batch") is dlg
    rh.enter("edit")
