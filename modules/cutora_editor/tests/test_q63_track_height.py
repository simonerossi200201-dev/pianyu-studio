# -*- coding: utf-8 -*-
"""Q63: track height must survive the real save/open path, and be undoable.

User save is collect_project_data → _serialize_track, not serialize_render_task.
"""
from __future__ import annotations

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Custom drag height — not compact/standard/high, so a default cannot hide a miss.
VIDEO_H = 88
AUDIO_H = 24


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q63-track-height"])


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None
    win._begin_open_progress = lambda *a, **k: None
    win._tick_open_progress = lambda *a, **k: None
    win._end_open_progress = lambda *a, **k: None
    win._ui_warning = lambda *a, **k: None


def _task_with_heights(video_h=VIDEO_H, audio_h=AUDIO_H):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(1920, 1080, 30)
    src_id = "q63src"
    tl.sources[src_id] = {
        "id": src_id, "path": "", "duration": 2.0,
        "has_video": True, "has_audio": True,
    }
    video = Track("video")
    video.is_main_video = True
    video.height = float(video_h)
    video.clips.append(Clip(src_id, 0.0, 2.0, 0.0))
    audio = Track("audio")
    audio.height = float(audio_h)
    audio.clips.append(Clip(src_id, 0.0, 2.0, 0.0))
    tl.add_track(video)
    tl.add_track(audio)
    return {
        "name": "q63",
        "video": "",
        "path": "",
        "text": "",
        "segments": [],
        "style": {},
        "duration": 2.0,
        "text_overlays": [],
        "image_overlays": [],
        "timeline": tl,
    }, video, audio


def _heights_by_type(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    out = {}
    for tr in list(getattr(tl, "tracks", []) or []):
        ttype = getattr(tr, "type", None)
        if ttype:
            out[str(ttype)] = float(getattr(tr, "height", 0) or 0)
    return out


class _UndoWin:
    """Minimal window so undoable commands snapshot JSON. Real command body."""

    def __init__(self, task):
        from cutora.core.history import HistoryManager
        from cutora.ui.command_bodies import CommandBodies
        self._bodies = CommandBodies
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = "mem"
        self.history_mgr = HistoryManager()
        self.timeline = type("TLW", (), {"update": lambda self: None})()
        self._project_dirty = False

    def push_undo(self, action_name="操作", command=None, params=None, source=None):
        extra = None
        if command:
            extra = {"command": command, "params": params, "source": source}
        self.history_mgr.push(action_name, self._snap(), extra=extra)

    def capture_undo_after(self):
        self.history_mgr.set_after(self._snap())

    def mark_dirty(self):
        self._project_dirty = True

    def is_dirty(self):
        return bool(self._project_dirty)

    def _snap(self):
        return {
            "tasks": copy.deepcopy(self.tasks),
            "curr_idx": self.curr_idx,
        }

    def undo(self):
        state = self.history_mgr.undo()
        if state:
            self.tasks[:] = state["tasks"]
            self.curr_idx = state["curr_idx"]

    def command_set_track_height(self, track_id, height, all_tracks=False):
        from cutora.ui.command_bodies import CommandBodies
        return CommandBodies.command_set_track_height(
            self, track_id, height, all_tracks=all_tracks)


def test_q63_track_height_survives_save_reopen(tmp_path):
    """Ctrl+S path (project.save_as) then project.open must keep per-track height.

    Must not go through serialize_render_task — that is the export subprocess
    serializer Q33 tested by mistake.
    """
    _qapp()
    from cutora.core.commands import reset_bus_for_tests
    from cutora.ui.main_window import MainWindow

    reset_bus_for_tests()
    task, video, audio = _task_with_heights()
    want_v, want_a = float(video.height), float(audio.height)
    vid = str(video.id)
    dest = tmp_path / "q63-height.asproj"

    win = MainWindow()
    try:
        _silence(win)
        win.tasks = [task]
        win.curr_idx = 0
        win.project_path = None
        win.bus.execute("project.save_as", {"path": str(dest)}, source="ui")
        assert dest.is_file(), dest
        raw = json.loads(dest.read_text(encoding="utf-8"))
        assert raw.get("app") == "CUTORA"
        assert "tasks" in raw and raw["tasks"], list(raw.keys())
        saved_tracks = (raw["tasks"][0].get("timeline") or {}).get("tracks") or []
        by_type = {t.get("type"): t for t in saved_tracks if isinstance(t, dict)}
        assert "video" in by_type, saved_tracks
        assert by_type["video"].get("height") == int(want_v), by_type["video"]
        assert by_type["audio"].get("height") == int(want_a), by_type["audio"]
        # Canvas height is 1080 — must not have been written as the track height.
        assert by_type["video"].get("height") != 1080

        win.bus.execute("project.open", {"path": str(dest)}, source="ui")
        tasks = list(getattr(win, "tasks", None) or [])
        assert tasks, "open left no tasks"
        got = _heights_by_type(tasks[0])
        assert abs(got.get("video", 0) - want_v) < 1e-6, got
        assert abs(got.get("audio", 0) - want_a) < 1e-6, got
        # Same track id still has the custom height (not a type-default).
        tl = tasks[0].get("timeline")
        match = [t for t in tl.tracks if str(getattr(t, "id", "")) == vid]
        assert match, [getattr(t, "id", None) for t in tl.tracks]
        assert abs(float(match[0].height) - want_v) < 1e-6, match[0].height
    finally:
        win.close()
        reset_bus_for_tests()


def test_q63_height_change_undoable():
    """One height command = one undo; marks dirty; all=true is still one undo."""
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
    from cutora.model.timeline_model import TRACK_HEIGHT_STANDARD

    reset_bus_for_tests()
    spec = get_registry().get("timeline.set_track_height")
    assert spec.undoable is True
    assert spec.portable is False

    task, video, audio = _task_with_heights(
        video_h=TRACK_HEIGHT_STANDARD, audio_h=TRACK_HEIGHT_STANDARD)
    before_v = float(video.height)
    before_a = float(audio.height)
    win = _UndoWin(task)
    bus = CommandBus(get_registry())
    bus.bind_window(win)

    hist_before = len(win.history_mgr.history)
    bus.execute("timeline.set_track_height", {
        "track_id": str(video.id),
        "height": VIDEO_H,
    }, source="ui")
    assert len(win.history_mgr.history) == hist_before + 1
    assert abs(float(video.height) - float(VIDEO_H)) < 1e-6, video.height
    assert abs(float(audio.height) - before_a) < 1e-6, audio.height
    assert win.is_dirty() is True

    win.undo()
    got = _heights_by_type(win.tasks[0])
    assert abs(got["video"] - before_v) < 1e-6, got
    assert abs(got["audio"] - before_a) < 1e-6, got

    # Shift+drag / 所有轨道: one command writes every track, one undo restores all.
    video2 = win.tasks[0]["timeline"].tracks[0]
    audio2 = win.tasks[0]["timeline"].tracks[1]
    bus.execute("timeline.set_track_height", {
        "track_id": str(video2.id),
        "height": VIDEO_H,
        "all": True,
    }, source="ui")
    got = _heights_by_type(win.tasks[0])
    assert abs(got["video"] - float(VIDEO_H)) < 1e-6, got
    assert abs(got["audio"] - float(VIDEO_H)) < 1e-6, got
    # undo() rewound the pointer; a new command truncates the undone branch,
    # so the stack has this one entry, not two.
    assert len(win.history_mgr.history) == hist_before + 1
    win.undo()
    got = _heights_by_type(win.tasks[0])
    assert abs(got["video"] - before_v) < 1e-6, got
    assert abs(got["audio"] - before_a) < 1e-6, got
    reset_bus_for_tests()


def test_q63_height_drag_updates_scroll_range():
    """Live height change must refresh the vertical scroll range."""
    from PySide6.QtWidgets import QWidget
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track, TRACK_HEIGHT_STANDARD
    from cutora.ui.timeline.widget import TimelineWidget

    _qapp()

    class _Host(QWidget):
        def __init__(self):
            super().__init__()
            self.n = 0
            self.scrollbar = None
            self.v_scrollbar = None

        def update_scroll_range(self):
            self.n += 1

    host = _Host()
    w = TimelineWidget()
    w.setParent(host)
    tl = Timeline(1920, 1080, 30)
    tr = Track("video")
    tl.add_track(tr)
    w.set_data(1000, {"timeline": tl, "segments": []})
    before = host.n
    w.set_track_height(tr, VIDEO_H)
    assert host.n > before, "set_track_height did not call update_scroll_range"
    assert abs(float(tr.height) - float(VIDEO_H)) < 1e-6, tr.height
    w.set_track_height(tr, TRACK_HEIGHT_STANDARD)
    w.close()
    host.close()
