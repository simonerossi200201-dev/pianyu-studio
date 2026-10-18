# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Language detection on a QThread so the GUI stays responsive."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class DetectWorker(QThread):
    """Run ``detect_fn(path)`` for each ``(media_id, path)`` off the GUI thread."""

    one_done = Signal(int, object)
    all_done = Signal(object)
    failed = Signal(str)

    def __init__(self, jobs, detect_fn, parent=None):
        super().__init__(parent)
        self.jobs = list(jobs or [])
        self.detect_fn = detect_fn
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return bool(self._cancelled)

    def run(self) -> None:
        out = []
        try:
            for media_id, path in self.jobs:
                if self._cancelled:
                    return
                det = self.detect_fn(path)
                if self._cancelled:
                    return
                pair = (int(media_id), det)
                out.append(pair)
                self.one_done.emit(int(media_id), det)
            if not self._cancelled:
                self.all_done.emit(out)
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
