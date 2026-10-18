# -*- coding: utf-8 -*-
"""T20d: split / trim / ripple on g19-2, then three undos restore JSON."""
from __future__ import annotations

import json
import os

from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
from cutora.core.history import HistoryManager
from cutora.model.project_io import (
    _normalize_project_data,
    deserialize_render_task,
    serialize_render_task,
)
from cutora.ui.command_bodies import CommandBodies
from cutora.ui.timeline.snap import clip_span

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19_2 = os.path.join(REPO, "tests", "golden", "g19-2_stitch_timeline", "project.asproj.json")


def _dump(task):
    return json.dumps(
        serialize_render_task(task),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _video_track(task):
    tl = task["timeline"]
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video" and getattr(tr, "clips", None):
            return tr
    raise AssertionError("g19-2 has no video clips")


class _DummyTimelineWidget:
    def update(self):
        pass


class _Win(CommandBodies):
    def __init__(self, task):
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = G19_2
        self.history_mgr = HistoryManager()
        self.timeline = _DummyTimelineWidget()

    def _snap(self):
        import copy
        return {"tasks": copy.deepcopy(self.tasks), "curr_idx": self.curr_idx}

    def push_undo(self, action_name="操作", command=None, params=None, source=None):
        extra = None
        if command:
            extra = {"command": command, "params": params, "source": source}
        self.history_mgr.push(action_name, self._snap(), extra=extra)

    def capture_undo_after(self):
        self.history_mgr.set_after(self._snap())

    def undo(self):
        state = self.history_mgr.undo()
        if state:
            self.tasks[:] = state["tasks"]
            self.curr_idx = state["curr_idx"]


def test_t20d_split_trim_ripple_undo_restores_g19_2_json():
    reset_bus_for_tests()
    try:
        raw = json.loads(open(G19_2, encoding="utf-8").read())
        _normalize_project_data(raw)
        task = deserialize_render_task(raw["tasks"][0])
        win = _Win(task)
        before = _dump(win.tasks[0])
        vtr = _video_track(win.tasks[0])
        assert len(vtr.clips) >= 2
        clip1 = vtr.clips[1]
        start, end = clip_span(clip1)
        split_t = (start + end) / 2.0
        n_before = len(vtr.clips)
        place_before = [(c.start_t, c.in_t, c.out_t) for c in vtr.clips]

        bus = CommandBus(get_registry())
        bus.bind_window(win)
        bus.execute("timeline.split_at", {"time": split_t}, source="ui")
        vtr = _video_track(win.tasks[0])
        assert len(vtr.clips) == n_before + 1
        left = vtr.clips[1]
        left_start, left_end = clip_span(left)
        assert abs(left_start - start) < 1e-6
        assert abs(left_end - split_t) < 1e-6
        place_split = [(c.start_t, c.in_t, c.out_t) for c in vtr.clips]
        assert place_split != place_before

        bus.execute("timeline.trim_clip", {
            "track_id": vtr.id,
            "clip_index": 1,
            "in_t": float(left.in_t) + 0.15,
            "out_t": float(left.out_t) - 0.15,
            "start_t": float(left.start_t) + 0.15,
        }, source="ui")
        vtr = _video_track(win.tasks[0])
        trimmed = vtr.clips[1]
        assert abs(trimmed.start_t - (start + 0.15)) < 1e-6
        place_trim = [(c.start_t, c.in_t, c.out_t) for c in vtr.clips]
        assert place_trim != place_split

        bus.execute("timeline.ripple_delete", {
            "targets": [{"kind": "clip", "track_id": vtr.id, "clip_index": 1}],
        }, source="ui")
        vtr = _video_track(win.tasks[0])
        place_del = [(c.start_t, c.in_t, c.out_t) for c in vtr.clips]
        assert place_del != place_trim
        assert len(vtr.clips) < n_before + 1
        mid = _dump(win.tasks[0])
        assert mid != before

        win.undo()
        win.undo()
        win.undo()
        restored = _dump(win.tasks[0])
        assert restored == before
    finally:
        reset_bus_for_tests()
