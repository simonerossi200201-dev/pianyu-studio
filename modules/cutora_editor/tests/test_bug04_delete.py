# -*- coding: utf-8 -*-
"""BUG-04: Delete on the timeline removes clips, not tasks. Shift+Delete in the library."""
from __future__ import annotations

import copy

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, Track
from cutora.ui.main_window import MainWindow
from cutora.ui.timeline.widget import TimelineWidget


def _app():
    return QApplication.instance() or QApplication(["bug04"])


def _key(key, mods=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, mods)


def test_timeline_delete_keeps_task_count():
    _app()
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.core.history import HistoryManager
    from cutora.ui.command_bodies import CommandBodies

    tl = Timeline(1080, 1920, 30)
    vtrack = Track("video")
    tl.add_track(vtrack)
    clip = Clip("s", 0.0, 2.0, 0.0)
    vtrack.clips.append(clip)
    task = {"duration": 2.0, "segments": [{"start": 0, "end": 1, "text": "a"}], "timeline": tl}
    tasks = [task]
    w = TimelineWidget()
    w.set_data(2000, task)

    class _Host:
        def __init__(self):
            self._bodies = CommandBodies
            self.tasks = tasks
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()
            self.timeline = w

        def push_undo(self, action_name="操作", command=None, params=None, source=None):
            self.history_mgr.push(action_name, {"tasks": copy.deepcopy(self.tasks)})

        def capture_undo_after(self):
            self.history_mgr.set_after({"tasks": copy.deepcopy(self.tasks)})

        def __getattr__(self, name):
            fn = getattr(self._bodies, name, None)
            if callable(fn):
                return lambda *a, **k: fn(self, *a, **k)
            raise AttributeError(name)

    reset_bus_for_tests()
    get_bus().bind_window(_Host())
    try:
        w.selected_video_clip = (vtrack, clip)
        w.keyPressEvent(_key(Qt.Key.Key_Delete))
        assert len(tasks) == 1
        assert clip not in vtrack.clips
    finally:
        reset_bus_for_tests()


def test_media_list_delete_does_not_remove_task():
    host = type("H", (), {})()
    host.calls = []
    host.file_list = type("L", (), {
        "selectedItems": lambda self: [1],
        "currentRow": lambda self: 0,
    })()
    host._confirm_delete_tasks = lambda: host.calls.append("confirm")
    host.delete_selected_task = lambda: host.calls.append("delete")
    MainWindow.file_list_keypress(host, _key(Qt.Key.Key_Delete))
    assert host.calls == []
    MainWindow.file_list_keypress(
        host, _key(Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier))
    assert host.calls == ["confirm"]


def test_shift_delete_yes_deletes(monkeypatch):
    class Host:
        def __init__(self):
            self.deleted = False
            self.file_list = type("L", (), {
                "selectedItems": lambda self: [1],
                "currentRow": lambda self: 0,
            })()

        def delete_selected_task(self):
            self.deleted = True

    host = Host()
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    MainWindow._confirm_delete_tasks(host)
    assert host.deleted is True


def test_shift_delete_no_keeps_task(monkeypatch):
    class Host:
        def __init__(self):
            self.deleted = False
            self.file_list = type("L", (), {
                "selectedItems": lambda self: [1],
                "currentRow": lambda self: 0,
            })()

        def delete_selected_task(self):
            self.deleted = True

    host = Host()
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No)
    MainWindow._confirm_delete_tasks(host)
    assert host.deleted is False


def test_shortcut_delete_is_noop():
    class Host:
        def __init__(self):
            self.deleted = False

        def delete_selected_task(self):
            self.deleted = True

    host = Host()
    MainWindow.shortcut_delete(host)
    assert host.deleted is False
