# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background worker for the subtitle-track inspector「⚡ 自动转录」.

Why this exists: the inspector used to call ``transcribe_track_to_segments`` directly in the
button slot. That spawned the ASR worker process, waited up to 180 s for the model to load and
up to 120 s per 30 s audio window — all on the GUI thread (the single ``processEvents()``
before it only painted the "⏳ 正在转录…" label). Windows marked the app "Not responding",
queued clicks re-entered the slot and started *another* ASR process (second model copy →
CUDA OOM / crash), and one worker process was spawned per clip of the source track.

Contract:
* The timeline is read on the GUI thread (spans resolved before ``start()``) and written on
  the GUI thread (``done`` handler). ``run()`` only does audio + ASR + segmentation.
* ``cancel()`` is non-blocking; it kills the ASR worker process so a blocking roundtrip returns.
* GPU access is queued through ``cutora.core.gpu_gate`` (same queue as AI 字幕 / 抠像 / 跟踪).
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.panels.sub_track_worker")


class SubTrackTranscribeWorker(QThread):
    progress = Signal(int, str)        # percent 0..100, status text
    done = Signal(object)              # (segments, timeline_words, detected_language)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, spans, *, language="auto", sub_track_id="", title_locate=None,
                 parent=None):
        super().__init__(parent)
        self.spans = list(spans or [])
        self.language = language or "auto"
        self.sub_track_id = str(sub_track_id or "")
        self.title_locate = title_locate
        self._stop = threading.Event()
        self._runner = None

    # GUI thread
    def cancel(self) -> None:
        self._stop.set()
        runner = self._runner
        if runner is not None and hasattr(runner, "cancel"):
            try:
                runner.cancel()
            except Exception:
                _LOG.exception("cancel runner failed")

    def is_cancelled(self) -> bool:
        return self._stop.is_set()

    # worker thread
    def run(self) -> None:  # noqa: D401
        from cutora.core import gpu_gate
        from cutora.subtitle import track_transcribe as TT
        ticket = 0
        try:
            self.progress.emit(0, "排队等待显卡…" if gpu_gate.busy() else "准备转录…")
            ticket = gpu_gate.acquire(
                "字幕轨道转录",
                should_stop=self._stop.is_set,
                on_wait=lambda who: self.progress.emit(0, "显卡正被「%s」占用，排队中…" % (who or "其它任务")),
            )
            if not ticket or self._stop.is_set():
                self.cancelled.emit()
                return
            total = TT.spans_total_sec(self.spans)
            runner = TT.make_default_runner(
                self.language,
                should_stop=self._stop.is_set,
                on_progress=lambda pct, msg: self.progress.emit(int(pct), str(msg)),
                total_sec=total,
            )
            self._runner = runner
            if self._stop.is_set():
                self.cancelled.emit()
                return
            try:
                segs, words = TT.compute_track_transcript(
                    self.spans,
                    language=self.language,
                    transcribe_fn=runner,
                    sub_track_id=self.sub_track_id,
                    title_locate=self.title_locate,
                    should_stop=self._stop.is_set,
                )
            finally:
                close = getattr(runner, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            if self._stop.is_set():
                self.cancelled.emit()
                return
            self.progress.emit(100, "转录完成")
            self.done.emit((segs, words, getattr(runner, "detected", None)))
        except TT.TranscribeCancelled:
            self.cancelled.emit()
        except Exception as exc:  # never let an exception escape QThread.run (abort on Qt6)
            if self._stop.is_set():
                self.cancelled.emit()
                return
            _LOG.exception("subtitle-track transcription failed")
            msg = str(exc) or exc.__class__.__name__
            if "out of memory" in msg.lower() or "oom" in msg.lower():
                msg += "\n显存不足：已自动降档仍失败，可关闭其它 AI 任务后重试。"
            self.failed.emit(msg)
        finally:
            self._runner = None
            if ticket:
                try:
                    gpu_gate.release(ticket)
                except Exception:
                    pass
