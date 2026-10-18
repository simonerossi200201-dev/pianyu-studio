# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline selection: clips and segments in one view-state object.

Not persisted, not undoable, not recorded into recipes.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class TimelineSelection(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips: list[tuple[str, int]] = []
        self.segments: list[int] = []

    def as_tuple(self):
        return (tuple(self.clips), tuple(self.segments))

    def clear(self, emit: bool = True) -> bool:
        if not self.clips and not self.segments:
            return False
        self.clips = []
        self.segments = []
        if emit:
            self.changed.emit()
        return True

    def set_segments(self, ids, emit: bool = True) -> None:
        segs = sorted({int(i) for i in ids if int(i) >= 0})
        if segs == self.segments:
            return
        self.segments = segs
        if emit:
            self.changed.emit()

    def set_clips(self, clips, emit: bool = True) -> None:
        rows = []
        seen = set()
        for item in clips or []:
            tid, idx = str(item[0]), int(item[1])
            key = (tid, idx)
            if key in seen or idx < 0:
                continue
            seen.add(key)
            rows.append(key)
        if rows == self.clips:
            return
        self.clips = rows
        if emit:
            self.changed.emit()

    def replace(self, clips=None, segments=None, emit: bool = True) -> None:
        before = self.as_tuple()
        if clips is not None:
            self.set_clips(clips, emit=False)
        if segments is not None:
            self.set_segments(segments, emit=False)
        if emit and self.as_tuple() != before:
            self.changed.emit()

    def toggle_segment(self, idx: int) -> None:
        idx = int(idx)
        cur = set(self.segments)
        if idx in cur:
            cur.remove(idx)
        else:
            cur.add(idx)
        self.set_segments(cur)

    def toggle_clip(self, track_id: str, clip_index: int) -> None:
        key = (str(track_id), int(clip_index))
        cur = list(self.clips)
        if key in cur:
            cur = [c for c in cur if c != key]
        else:
            cur.append(key)
        self.set_clips(cur)

    def has_segment(self, idx: int) -> bool:
        return int(idx) in self.segments

    def has_clip(self, track_id: str, clip_index: int) -> bool:
        return (str(track_id), int(clip_index)) in self.clips
