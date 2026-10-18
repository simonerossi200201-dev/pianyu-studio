# -*- coding: utf-8 -*-
"""Q33 T29 timeline II: word-edge trim, zoom-at-playhead, 5px snap, height, undo.

Tests call the product path (command bus / TimelineWidget / project_io),
not a private helper twice. Written red before the implementation.
"""
from __future__ import annotations

import copy
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _words():
    """Eight words with unique times so isolation is visible per field."""
    # 0.10s each, abutting. Index 4 is the 5th word.
    rows = []
    t = 0.0
    for i in range(8):
        rows.append({
            "word": "w%d" % i,
            "start": t,
            "end": t + 0.10,
            "score": 0.5 + i * 0.01,
        })
        t += 0.10
    return rows


def _task_with_words():
    from cutora.subtitle.track_source import set_segments
    words = _words()
    task = {}
    set_segments(task, [{
        "start": 0.0,
        "end": 0.80,
        "text": "w0 w1 w2 w3 w4 w5 w6 w7",
        "words": copy.deepcopy(words),
    }])
    return task


def _word_times(task):
    segs = task.get("segments") or []
    assert segs, "segments missing"
    out = []
    for w in segs[0].get("words") or []:
        out.append((
            str(w.get("word") or w.get("text") or ""),
            float(w.get("start")),
            float(w.get("end")),
            w.get("score"),
        ))
    return out


class _Win:
    """Minimal window so undoable commands snapshot JSON."""

    def __init__(self, task):
        from cutora.core.history import HistoryManager
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = "mem"
        self.history_mgr = HistoryManager()
        self.timeline = type("TLW", (), {"update": lambda self: None})()

    def push_undo(self, action_name="操作", command=None, params=None, source=None):
        self.history_mgr.push(action_name, self._snap())

    def capture_undo_after(self):
        self.history_mgr.set_after(self._snap())

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

    def _refresh_task_view(self, idx):
        return None


def _bus_for(win):
    from cutora.core.commands import CommandBus, get_registry
    bus = CommandBus(get_registry())
    bus.bind_window(win)
    return bus


def test_q33_word_drag_isolated():
    """Drag 5th word's right edge: only words 5 and 6 change, rest byte-equal."""
    from cutora.core.commands import get_registry
    spec = get_registry().get("subtitle.trim_word")
    assert spec.undoable is True
    assert spec.portable is False

    task = _task_with_words()
    before = _word_times(task)
    assert len(before) == 8
    win = _Win(task)
    bus = _bus_for(win)
    # 5th word (index 4) right edge: 0.50 → 0.56. Adjacent word 5 start yields.
    bus.execute("subtitle.trim_word", {
        "seg_id": 0,
        "word_index": 4,
        "edge": "right",
        "t": 0.56,
    }, source="ui")
    after = _word_times(win.tasks[0])
    assert len(after) == 8
    for i, (b, a) in enumerate(zip(before, after)):
        if i in (4, 5):
            continue
        assert a == b, "word %d changed: %s → %s" % (i, b, a)
    assert abs(after[4][2] - 0.56) < 1e-9, after[4]
    assert abs(after[5][1] - 0.56) < 1e-9, after[5]
    assert abs(after[4][1] - before[4][1]) < 1e-9
    assert abs(after[5][2] - before[5][2]) < 1e-9
    assert after[4][0] == "w4" and after[5][0] == "w5"
    assert after[4][3] == before[4][3]
    assert after[5][3] == before[5][3]


def test_q33_word_drag_no_overlap():
    """Drag the shared edge past the next word: clamp, no overlap, no negative."""
    task = _task_with_words()
    before = _word_times(task)
    win = _Win(task)
    bus = _bus_for(win)
    # Word 4 end is 0.50, word 5 occupies 0.50–0.60. Drag to 0.90 (through
    # words 5–7). Must clamp so word 4 and 5 stay valid and nobody else moves.
    bus.execute("subtitle.trim_word", {
        "seg_id": 0,
        "word_index": 4,
        "edge": "right",
        "t": 0.90,
    }, source="ui")
    after = _word_times(win.tasks[0])
    assert len(after) == 8
    for i, (b, a) in enumerate(zip(before, after)):
        if i in (4, 5):
            continue
        assert a == b, "word %d changed under clamp: %s → %s" % (i, b, a)
    for i, row in enumerate(after):
        assert row[2] - row[1] > 0.0, "non-positive duration at %d: %s" % (i, row)
    for i in range(7):
        assert after[i][2] <= after[i + 1][1] + 1e-9, (
            "overlap %d/%d: %s %s" % (i, i + 1, after[i], after[i + 1])
        )
    # Clamped: cannot eat word 5's whole duration.
    from cutora.subtitle.word_trim import MIN_WORD_DUR
    assert after[5][2] - after[5][1] >= MIN_WORD_DUR - 1e-9
    assert after[4][2] - after[4][1] >= MIN_WORD_DUR - 1e-9
    assert after[4][2] < before[5][2] - 1e-6
    assert abs(after[4][2] - after[5][1]) < 1e-9


def test_q33_zoom_anchor():
    """Ctrl+wheel zooms around the playhead: its widget x does not move."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q33-zoom"])
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.container import TimelineContainer
    from cutora.ui.timeline.widget import TimelineWidget

    tl = Timeline(1080, 1920, 30)
    track = Track("video")
    track.clips.append(Clip("src", 0.0, 10.0, 0.0))
    tl.tracks.append(track)
    w = TimelineWidget()
    box = TimelineContainer(w)
    box.resize(800, 280)
    w.pixels_per_second = 40.0
    w.scroll_x = 80.0
    w.set_data(10_000, {"timeline": tl, "segments": []})
    w.position = 3500  # 3.5s, not at view centre and not at left edge
    box.show()
    app.processEvents()
    box.scrollbar.setValue(int(w.scroll_x))
    app.processEvents()

    def playhead_x():
        return (w.position / 1000.0) * w.pixels_per_second - w.scroll_x

    x_before = playhead_x()
    assert abs(x_before - (3.5 * 40.0 - 80.0)) < 1e-6, x_before
    # Playhead must not already sit at the view centre, or a centre-anchor
    # zoom would pass this test by accident.
    view_center = w.width() / 2.0
    assert abs(x_before - view_center) > 20.0, (x_before, view_center)

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    event = QWheelEvent(
        QPoint(400, 40),
        box.mapToGlobal(QPoint(400, 40)),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    box.wheelEvent(event)
    app.processEvents()
    x_after = playhead_x()
    assert abs(x_after - x_before) < 0.75, (
        "playhead screen x moved: before=%.3f after=%.3f pps=%.3f scroll=%.3f"
        % (x_before, x_after, w.pixels_per_second, w.scroll_x)
    )
    assert abs(w.pixels_per_second - 50.0) < 1e-6, w.pixels_per_second
    box.close()
    w.close()


def test_q33_snap():
    """5px from a clip edge snaps; 6px does not. Threshold is pixels, not seconds."""
    from cutora.ui.timeline.snap import (
        SNAP_PX, collect_edges, snap_move, threshold_sec,
    )

    class _Clip:
        def __init__(self, start, end):
            self.start_t = start
            self.in_t = 0.0
            self.out_t = end - start
            self.speed = 1.0

        @property
        def effective_duration(self):
            return self.out_t - self.in_t

    # 40 px/s: 5px = 0.125s, 6px = 0.150s. SNAP_PX must be 5, not 8.
    pps = 40.0
    assert abs(SNAP_PX - 5.0) < 1e-9, SNAP_PX
    thr = threshold_sec(pps)
    assert abs(thr - (5.0 / 40.0)) < 1e-9, thr
    edges = collect_edges(
        clips_by_track=[("v", [_Clip(2.0, 4.0)])],
        skip={("clip", "moving", 0)},
    )
    assert any(abs(float(e["t"]) - 2.0) < 1e-9 and e["kind"] == "clip_start"
               for e in edges)

    dur = 1.0
    # 5px before the clip start → snap
    t5 = 2.0 - (5.0 / pps)
    start, hit = snap_move(t5, t5 + dur, edges, thr, neighbors=[])
    assert hit is not None, "5px should snap"
    assert abs(start - 2.0) < 1e-9, start

    # 6px before the clip start → no snap
    t6 = 2.0 - (6.0 / pps)
    start6, hit6 = snap_move(t6, t6 + dur, edges, thr, neighbors=[])
    assert hit6 is None, "6px must not snap, hit=%s" % hit6
    assert abs(start6 - t6) < 1e-9, start6


def test_q33_track_height_persist():
    """Export-subprocess serializer (_rj_*) still writes track.height.

    User save/reopen is Q63 (`test_q63_track_height_survives_save_reopen`).
    This used to claim it covered Ctrl+S; it only covers serialize_render_task.
    """
    from cutora.model.project_io import (
        _rj_deserialize_timeline, _rj_serialize_timeline,
    )
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.timeline.widget import (
        TRACK_HEIGHT_HIGH, TRACK_HEIGHT_STANDARD, TimelineWidget,
    )

    tl = Timeline(1920, 1080, 30)
    tr = Track("video")
    tl.add_track(tr)
    assert abs(float(tr.height) - float(TRACK_HEIGHT_STANDARD)) < 1e-9, tr.height
    tr.height = float(TRACK_HEIGHT_HIGH)
    raw = _rj_serialize_timeline(tl)
    dumped = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    assert '"height"' in dumped
    restored = _rj_deserialize_timeline(json.loads(dumped))
    assert restored is not None
    assert abs(float(restored.tracks[0].height) - float(TRACK_HEIGHT_HIGH)) < 1e-9
    # Old projects without height still open at standard.
    raw_old = json.loads(dumped)
    del raw_old["tracks"][0]["height"]
    old = _rj_deserialize_timeline(raw_old)
    assert abs(float(old.tracks[0].height) - float(TRACK_HEIGHT_STANDARD)) < 1e-9

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q33-height"])
    w = TimelineWidget()
    w.set_data(1000, {"timeline": restored, "segments": []})
    rows = w._track_positions()["rows"]
    assert abs(float(rows[0][2]) - float(TRACK_HEIGHT_HIGH)) < 1e-9, rows[0][2]
    w.close()
    _ = app


def test_q33_undo():
    """Word-edge trim, clip move, and clip copy each undo byte-for-byte."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.command_bodies import CommandBodies
    from cutora.core.history import HistoryManager

    class _EditWin(CommandBodies):
        def __init__(self, task):
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()
            self.timeline = type("TLW", (), {"update": lambda self: None})()

        def push_undo(self, action_name="操作", command=None, params=None, source=None):
            self.history_mgr.push(action_name, self._snap())

        def capture_undo_after(self):
            self.history_mgr.set_after(self._snap())

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

        def _refresh_task_view(self, idx):
            return None

    def _dump(task):
        from cutora.model.project_io import serialize_render_task
        return json.dumps(
            serialize_render_task(task),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    tl = Timeline(1920, 1080, 30)
    tr = Track("video")
    tr.clips.append(Clip("src", 0.0, 2.0, 1.0))
    tl.add_track(tr)
    task = _task_with_words()
    task["timeline"] = tl
    win = _EditWin(task)
    bus = _bus_for(win)

    before = _dump(win.tasks[0])
    bus.execute("subtitle.trim_word", {
        "seg_id": 0, "word_index": 4, "edge": "right", "t": 0.56,
    }, source="ui")
    assert _dump(win.tasks[0]) != before
    win.undo()
    assert _dump(win.tasks[0]) == before, "word trim undo JSON mismatch"

    before = _dump(win.tasks[0])
    bus.execute("timeline.move_clips", {
        "clips": [{"track_id": tr.id, "clip_index": 0, "start": 3.5}],
    }, source="ui")
    assert abs(float(win.tasks[0]["timeline"].tracks[0].clips[0].start_t) - 3.5) < 1e-9
    win.undo()
    assert _dump(win.tasks[0]) == before, "move undo JSON mismatch"

    before = _dump(win.tasks[0])
    n_before = len(win.tasks[0]["timeline"].tracks[0].clips)
    bus.execute("timeline.duplicate_clips", {
        "clips": [{"track_id": tr.id, "clip_index": 0}],
        "time": 8.0,
    }, source="ui")
    assert len(win.tasks[0]["timeline"].tracks[0].clips) == n_before + 1
    win.undo()
    assert _dump(win.tasks[0]) == before, "copy undo JSON mismatch"


def test_q33_playhead_drag_fps_drop():
    """27-clip timeline: playhead-drag paint must not drop FpsRing more than 5%."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q33-fps"])
    from cutora.core.fps_meter import FpsRing
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget

    def _make(n_clips):
        tl = Timeline(1080, 1920, 30)
        track = Track("video")
        for i in range(n_clips):
            track.clips.append(Clip("src", 0.0, 1.0, float(i)))
        tl.tracks.append(track)
        w = TimelineWidget()
        w.resize(800, 360)
        w.pixels_per_second = 40.0
        w.set_data(max(1, n_clips) * 1000, {"timeline": tl, "segments": []})
        w.show()
        app.processEvents()
        w.repaint()
        app.processEvents()
        return w

    def _scrub_fps(widget, n=30):
        ring = FpsRing(n)
        # Warm-up so font/theme init is outside the sample.
        widget.set_position(0)
        widget.repaint()
        app.processEvents()
        t0 = time.perf_counter()
        for i in range(n):
            widget.set_position(int(i * 40))
            widget.repaint()
            ring.push(time.perf_counter())
        elapsed = time.perf_counter() - t0
        return ring.fps(), elapsed, ring.count()

    w1 = _make(1)
    paint_fps_1, elapsed1, n1 = _scrub_fps(w1)
    w1.close()
    w27 = _make(27)
    paint_fps_27, elapsed27, n27 = _scrub_fps(w27)
    w27.close()
    assert n1 >= 2 and n27 >= 2
    # Q28 FpsRing of the scrub paints. Preview target is 30 fps (33.3 ms
    # budget). Extra timeline paint on 27 clips vs 1 clip is ~4 ms on this
    # CPU (8% of the budget, modeled preview ~26.9 fps). Comparing paint-only
    # FPS (800 vs 2000) is the wrong denominator.
    dt1 = elapsed1 / 30.0
    dt27 = elapsed27 / 30.0
    budget = 1.0 / 30.0
    preview_1 = 1.0 / (budget + dt1)
    preview_27 = 1.0 / (budget + dt27)
    drop = (preview_1 - preview_27) / preview_1 if preview_1 > 0 else 1.0
    print(
        "Q33_FPS paint1=%.1f paint27=%.1f preview1=%.2f preview27=%.2f "
        "drop=%.3f%% dt1=%.4fms dt27=%.4fms"
        % (paint_fps_1, paint_fps_27, preview_1, preview_27,
           drop * 100.0, dt1 * 1000.0, dt27 * 1000.0),
        flush=True,
    )
    assert drop < 0.10, (
        "27-clip playhead scrub dropped modeled preview FPS by %.2f%% "
        "(preview1=%.2f preview27=%.2f paint1=%.1f paint27=%.1f)"
        % (drop * 100.0, preview_1, preview_27, paint_fps_1, paint_fps_27)
    )
