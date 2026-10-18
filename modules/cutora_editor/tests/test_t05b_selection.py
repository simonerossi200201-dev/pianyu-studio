# -*- coding: utf-8 -*-
"""T05b: selection is view state; duplicate/split commands exist."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.subtitle.edit import duplicate_segments_task


def test_selection_does_not_touch_history():
    from PySide6.QtWidgets import QApplication
    from cutora.core.history import HistoryManager
    from cutora.ui.timeline.selection import TimelineSelection

    app = QApplication.instance() or QApplication(["t05b-sel"])
    _ = app
    mgr = HistoryManager()
    sel = TimelineSelection()
    n = 0

    def bump():
        nonlocal n
        n += 1

    sel.changed.connect(bump)
    for i in range(10):
        sel.set_segments([i])
        sel.toggle_clip("track-a", i % 3)
    assert n >= 10
    assert mgr.history == []
    assert mgr.current_index == -1


def test_duplicate_segments_shifts_words():
    task = {
        "segments": [
            {
                "start": 1.0, "end": 2.0, "text": "a",
                "words": [{"word": "a", "start": 1.0, "end": 2.0}],
            },
            {
                "start": 3.0, "end": 4.0, "text": "b",
                "words": [{"word": "b", "start": 3.0, "end": 4.0}],
            },
        ]
    }
    out = duplicate_segments_task(task, seg_ids=[0, 1], time=10.0)
    assert out["count"] == 4
    added = [task["segments"][i] for i in out["added"]]
    assert abs(added[0]["start"] - 10.0) < 1e-9
    assert abs(added[1]["start"] - 12.0) < 1e-9
    assert abs(added[0]["words"][0]["start"] - 10.0) < 1e-9


def test_portable_false_on_index_commands():
    from cutora.core.commands import get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    reg = get_registry()
    for name in (
        "subtitle.trim_segment", "subtitle.delete", "subtitle.set_text",
        "timeline.trim_clip", "timeline.duplicate_clips",
        "subtitle.duplicate_segments",
    ):
        assert reg.get(name).portable is False, name
    for name in (
        "subtitle.auto", "stylepack.apply", "text_preset.apply",
        "media.import", "export.run",
    ):
        assert reg.get(name).portable is True, name
    reset_bus_for_tests()
