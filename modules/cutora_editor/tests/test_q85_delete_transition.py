# -*- coding: utf-8 -*-
"""Q85: Delete on a selected timeline transition removes it and can undo.

The click is a real mouse press on the transition tape, not an assigned
selection. Delete goes through the timeline key handler and the command bus.
"""
from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q85-delete-transition"])


def _key(key):
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _mouse_press(w, x, y):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    pos = QPointF(float(x), float(y))
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos, pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mousePressEvent(ev)


class _Panel:
    def __init__(self):
        self.current_type = None
        self.current_data = None
        self.loads = []

    def load_data(self, type_, data):
        self.current_type = type_
        self.current_data = data
        self.loads.append((type_, data))


class _Host:
    """Bus window: command bodies plus the same undo snapshot MainWindow uses."""

    def __init__(self, task, timeline):
        from cutora.core.history import HistoryManager
        from cutora.ui.command_bodies import CommandBodies
        self._bodies = CommandBodies
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = "mem"
        self.history_mgr = HistoryManager()
        self.timeline = timeline
        self.prop_panel = _Panel()
        self._task_data = task
        self._selected_sticker = None
        self._selected_image_overlay = None
        self.file_list = type("L", (), {
            "selectedItems": lambda self: [1],
            "currentRow": lambda self: 0,
        })()
        self._stolen = []

    def _confirm_delete_tasks(self):
        self._stolen.append("confirm")

    def delete_selected_task(self):
        self._stolen.append("delete")

    def push_undo(self, action_name="操作", command=None, params=None, source=None):
        extra = None
        if command:
            extra = {"command": command, "params": params, "source": source}
        self.history_mgr.push(action_name, self._snap(), extra=extra)

    def capture_undo_after(self):
        self.history_mgr.set_after(self._snap())

    def _snap(self):
        return {"tasks": copy.deepcopy(self.tasks), "curr_idx": self.curr_idx}

    def undo(self):
        state = self.history_mgr.undo()
        if not state:
            return
        self.tasks = copy.deepcopy(state["tasks"])
        self.curr_idx = state["curr_idx"]
        task = self.tasks[self.curr_idx]
        self._task_data = task
        dur = int(self.timeline.duration or 4000)
        self.timeline.set_data(dur, task)

    def __getattr__(self, name):
        fn = getattr(self._bodies, name, None)
        if callable(fn):
            return lambda *a, **k: fn(self, *a, **k)
        raise AttributeError(name)


def _bind(host):
    from cutora.core.commands import get_bus, reset_bus_for_tests
    reset_bus_for_tests()
    bus = get_bus()
    bus.bind_window(host)
    return bus


def _video_track(task):
    tl = task.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            return tr
    raise AssertionError("video track missing")


def _clip_sig(clip):
    return (
        str(getattr(clip, "source_id", "")),
        round(float(clip.in_t), 6),
        round(float(clip.out_t), 6),
        round(float(clip.start_t), 6),
        round(float(clip.x), 6),
        round(float(clip.rotation), 6),
        round(float(clip.speed), 6),
    )


def _trans_sig(trans):
    return {
        "id": str(getattr(trans, "id", "")),
        "start_t": round(float(trans.start_t), 6),
        "duration": round(float(trans.duration), 6),
        "preset": str(trans.preset),
        "direction": trans.direction,
        "feather": round(float(trans.feather), 6),
        "angle": round(float(trans.angle), 6),
        "intensity": round(float(trans.intensity), 6),
        "easing": str(trans.easing),
        "zoom_scale_start": round(float(trans.zoom_scale_start), 6),
        "motion_blur": round(float(trans.motion_blur), 6),
        "domain": str(getattr(trans, "domain", "visual")),
    }


def _scene():
    """Two video clips with one non-default transition straddling the seam."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.timeline.widget import TimelineWidget

    _app()
    tl = Timeline(64, 64, 30)
    track = Track("video")
    track.id = "q85-video"
    left = Clip("src-left", 0.0, 2.0, 0.0)
    left.x = 11.5
    left.rotation = 7.0
    left.speed = 1.0
    right = Clip("src-right", 0.25, 2.25, 2.0)
    right.x = -4.0
    right.rotation = 3.5
    right.speed = 1.0
    track.clips.extend([left, right])
    trans = TransitionItem(
        start_t=1.76, duration=0.48, preset="wipe",
        zoom_scale_start=0.51, motion_blur=0.15,
        intensity=0.62, easing="ease_out", direction="down",
        angle=48.0, feather=0.37,
    )
    track.transitions.append(trans)
    tl.tracks.append(track)
    task = {"timeline": tl, "segments": [], "duration": 4.0}
    w = TimelineWidget()
    w.pixels_per_second = 100.0
    w.scroll_x = 0
    w.scroll_y = 0
    w.resize(800, 240)
    w.set_data(4000, task)
    host = _Host(task, w)
    _bind(host)

    def _on_select(type_, data):
        if type_ == "transition":
            host.prop_panel.current_type = "transition"
            host.prop_panel.current_data = data
            return
        if not type_:
            from cutora.ui.main_window import MainWindow
            MainWindow.on_timeline_select(host, type_, data)

    w.item_selected.connect(_on_select)
    return host, w, track, left, right, trans


def _transition_click(w, trans):
    x = (float(trans.start_t) + float(trans.duration) / 2.0) * w.pixels_per_second
    y = w.RULER_H + w.TOP_PAD + 8
    hit = w._hit_transition_tape(x + w.scroll_x, y)
    assert hit is not None and hit[1] is trans, "click would miss the transition tape"
    _mouse_press(w, x, y)
    assert w.selected_transition is not None
    assert w.selected_transition[1] is trans
    assert w.selected_video_clip is None


def _live_trans(w):
    tr = _video_track(w.task_data)
    return tr, list(getattr(tr, "transitions", []) or [])


def test_q85_delete_selected_transition():
    from cutora.ui.main_window import MainWindow
    host, w, track, left, right, trans = _scene()
    try:
        before_clips = [_clip_sig(left), _clip_sig(right)]
        _transition_click(w, trans)
        assert host.prop_panel.current_type == "transition"
        MainWindow.file_list_keypress(host, _key(Qt.Key.Key_Delete))
        assert host._stolen == []
        _tr, still = _live_trans(w)
        assert len(still) == 1 and still[0] is trans
        MainWindow.delete_timeline_selection(host)
        _tr, gone = _live_trans(w)
        assert gone == [], "selected transition survived Delete"
        assert [_clip_sig(c) for c in _tr.clips] == before_clips
        assert w.selected_transition is None
        assert host.prop_panel.loads == [(None, None)]
        assert host.prop_panel.current_type is None
    finally:
        from cutora.core.commands import reset_bus_for_tests
        reset_bus_for_tests()
        w.close()


def test_q85_undo_restores_transition():
    host, w, track, left, right, trans = _scene()
    try:
        before = _trans_sig(trans)
        before_clips = [_clip_sig(left), _clip_sig(right)]
        assert before["preset"] == "wipe"
        assert before["direction"] == "down"
        assert before["feather"] == 0.37
        assert before["duration"] != 0.35
        _transition_click(w, trans)
        w.keyPressEvent(_key(Qt.Key.Key_Backspace))
        _tr, gone = _live_trans(w)
        assert gone == []
        assert len(host.history_mgr.history) >= 1
        host.undo()
        tr, back = _live_trans(w)
        assert len(back) == 1, "undo did not bring the transition back"
        assert _trans_sig(back[0]) == before
        assert [_clip_sig(c) for c in tr.clips] == before_clips
    finally:
        from cutora.core.commands import reset_bus_for_tests
        reset_bus_for_tests()
        w.close()


def test_q85_context_menu_deletes_transition(monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu

    host, w, track, left, right, trans = _scene()
    try:
        before_clips = [_clip_sig(left), _clip_sig(right)]
        orig_init = QMenu.__init__

        def _init(menu, *args, **kwargs):
            orig_init(menu, *args, **kwargs)

            def _exec(*_a, **_k):
                labels = [act.text() for act in menu.actions()]
                for act in menu.actions():
                    if act.text() == "删除转场" and act.isEnabled():
                        return act
                raise AssertionError("菜单里没有可点的删除转场: %s" % labels)

            menu.exec = _exec

        monkeypatch.setattr(QMenu, "__init__", _init)
        x = (float(trans.start_t) + float(trans.duration) / 2.0) * w.pixels_per_second
        y = w.RULER_H + w.TOP_PAD + 8
        ev = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(int(x), int(y)),
            QPoint(int(x), int(y)),
        )
        w.contextMenuEvent(ev)
        tr, gone = _live_trans(w)
        assert gone == [], "context menu did not delete the transition"
        assert [_clip_sig(c) for c in tr.clips] == before_clips
        assert w.selected_transition is None
        assert host.prop_panel.loads == [(None, None)]
    finally:
        from cutora.core.commands import reset_bus_for_tests
        reset_bus_for_tests()
        w.close()


def test_q85_delete_video_clip_undoable():
    host, w, track, left, right, trans = _scene()
    try:
        before_left = _clip_sig(left)
        before_right = _clip_sig(right)
        before_trans = _trans_sig(trans)
        x = 1.0 * w.pixels_per_second
        y = w.RULER_H + w.TOP_PAD + 8
        _mouse_press(w, x, y)
        assert w.selected_video_clip is not None
        assert w.selected_video_clip[1] is left
        assert w.selected_transition is None
        # Q157：Backspace = 普通删除（留空隙，右边那段不动）；Delete = 波纹删除（达芬奇习惯）
        w.keyPressEvent(_key(Qt.Key.Key_Backspace))
        tr = _video_track(w.task_data)
        assert [_clip_sig(c) for c in tr.clips] == [before_right]
        assert [_trans_sig(t) for t in tr.transitions] == [before_trans]
        assert len(host.history_mgr.history) >= 1
        host.undo()
        tr = _video_track(w.task_data)
        assert [_clip_sig(c) for c in tr.clips] == [before_left, before_right]
        assert [_trans_sig(t) for t in tr.transitions] == [before_trans]
        # Delete：删掉并前移
        _mouse_press(w, x, y)
        assert w.selected_video_clip is not None and w.selected_video_clip[1] is _video_track(w.task_data).clips[0]
        w.keyPressEvent(_key(Qt.Key.Key_Delete))
        tr = _video_track(w.task_data)
        assert len(tr.clips) == 1 and float(getattr(tr.clips[0], "start_t", 0.0)) < before_right[3] - 1e-6
        host.undo()
        tr = _video_track(w.task_data)
        assert [_clip_sig(c) for c in tr.clips] == [before_left, before_right]
    finally:
        from cutora.core.commands import reset_bus_for_tests
        reset_bus_for_tests()
        w.close()
