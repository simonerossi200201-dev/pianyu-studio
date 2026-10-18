# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download one language's aligner, then hand control back to stitch.

The dialog opens already downloading. It says which language, tells the
user not to close the app, and shows live bytes. On success it says that
language is ready and closes so the caller can start stitch.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QRectF, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_4, SPACE_5


def _mb(num: float) -> str:
    mb = float(num) / (1024.0 * 1024.0)
    if mb >= 100:
        return "%.0f MB" % mb
    if mb >= 10:
        return "%.1f MB" % mb
    return "%.2f MB" % mb


class _GoldBar(QWidget):
    """Track plus a gold fill. The theme's QProgressBar is only a few pixels tall."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ratio = 0.0
        self.setMinimumHeight(SPACE_3)

    def set_ratio(self, ratio: float) -> None:
        self.ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def paintEvent(self, _event):
        from cutora.ui.theme.tokens import current
        tok = current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect().adjusted(0, 1, -1, -2))
        if rect.height() < 2 or rect.width() < 2:
            painter.end()
            return
        radius = rect.height() / 2.0
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        painter.fillPath(track, QColor(tok.line))
        fill_w = max(0.0, rect.width() * self.ratio)
        if fill_w >= 2:
            fill = QRectF(rect.x(), rect.y(), fill_w, rect.height())
            path = QPainterPath()
            path.addRoundedRect(fill, radius, radius)
            painter.fillPath(path, QColor(tok.accent))
        painter.end()


class _FetchThread(QThread):
    """Bytes, not kilobytes, so the dialog can show the file name."""

    progress = Signal(int, int, str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, model_id: str):
        super().__init__()
        self.model_id = str(model_id or "")
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        from cutora.asr.download import download_model
        from cutora.core.net import NetCancelled
        try:
            download_model(
                self.model_id,
                progress_cb=lambda done, total, name: self.progress.emit(
                    int(done), int(total), str(name or "")),
                cancel_event=self.cancel_event)
        except NetCancelled:
            self.failed.emit("已取消")
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.succeeded.emit()


class AlignFetchDialog(QDialog):
    """Closes with Accepted only after the weight checks out."""

    def __init__(self, plan: dict, parent=None, *, worker_factory=None, hold_ms: int = 900):
        super().__init__(parent)
        self.plan = dict(plan or {})
        self.model_id = str(self.plan.get("id") or "")
        self.name_zh = str(self.plan.get("name_zh") or self.plan.get("language") or "")
        self.worker = None
        self._worker_factory = worker_factory
        self.hold_ms = int(hold_ms)
        self._started = False
        self._succeeded = False
        self._failed = False
        self._t0 = 0.0
        self.setObjectName("alignFetchDialog")
        self.setWindowTitle(tr("正在下载{name}对齐模型", name=self.name_zh))
        self.setMinimumWidth(520)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSpacing(SPACE_3)
        lay.setContentsMargins(SPACE_5, SPACE_4, SPACE_5, SPACE_4)

        self.title = QLabel(tr("正在下载{name}的对齐模型", name=self.name_zh))
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        size = _mb(float(self.plan.get("size_bytes") or 0))
        self.lead = QLabel(tr(
            "只下载这一种语言（约 {size}）。请不要关闭软件。"
            "下载完成后会自动开始拼接。",
            size=size))
        self.lead.setObjectName("hint")
        self.lead.setWordWrap(True)
        lay.addWidget(self.lead)

        repo = str(self.plan.get("repo") or "")
        lic = str(self.plan.get("license") or "")
        self.source = QLabel(tr(
            "来源 huggingface.co/{repo} · {license}", repo=repo, license=lic or "—"))
        self.source.setObjectName("hint")
        self.source.setWordWrap(True)
        lay.addWidget(self.source)

        self.percent = QLabel("0%")
        self.percent.setObjectName("pageTitle")
        self.percent.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.percent)

        self.prog = _GoldBar()
        lay.addWidget(self.prog)

        self.status = QLabel(tr("正在连接…"))
        self.status.setObjectName("progLabel")
        self.status.setProperty("mode", "bgm")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.file_line = QLabel("")
        self.file_line.setObjectName("hint")
        self.file_line.setWordWrap(True)
        lay.addWidget(self.file_line)

        row = QHBoxLayout()
        row.setSpacing(SPACE_2)
        row.addStretch(1)
        self.btn_cancel = QPushButton(tr("取消"))
        self.btn_cancel.setObjectName("btnSecondary")
        self.btn_cancel.clicked.connect(self._on_cancel)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start)

    def _start(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._failed = False
        self._succeeded = False
        self.btn_cancel.setText(tr("取消"))
        self.btn_cancel.setEnabled(True)
        self._t0 = time.perf_counter()
        if self._worker_factory is not None:
            worker = self._worker_factory(self.plan)
        else:
            worker = _FetchThread(self.model_id)
        self.worker = worker
        worker.progress.connect(self._on_progress)
        done = getattr(worker, "succeeded", None) or getattr(worker, "done", None)
        done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.start()

    def _on_progress(self, done, total, name) -> None:
        done = max(0, int(done))
        total = max(1, int(total))
        ratio = min(1.0, done / float(total))
        self.prog.set_ratio(ratio)
        self.percent.setText("%d%%" % int(round(100 * ratio)))
        elapsed = max(0.001, time.perf_counter() - self._t0)
        speed = done / elapsed
        self.status.setText(tr(
            "已下载 {done} / {total} · {speed}/秒",
            done=_mb(done), total=_mb(total), speed=_mb(speed)))
        label = str(name or "").strip()
        if label:
            self.file_line.setText(tr("正在下载 {name}", name=label))

    def _on_done(self, *_args) -> None:
        self.worker = None
        self._succeeded = True
        self._failed = False
        self.prog.set_ratio(1.0)
        self.percent.setText("100%")
        self.title.setText(tr("{name}的对齐模型已经下载完毕", name=self.name_zh))
        self.lead.setText(tr("即将自动开始拼接。"))
        self.status.setText(tr("{name}的对齐模型已经下载完毕。", name=self.name_zh))
        self.file_line.setText("")
        self.btn_cancel.hide()
        QTimer.singleShot(max(0, self.hold_ms), self.accept)

    def _on_failed(self, msg) -> None:
        self.worker = None
        self._failed = True
        text = str(msg or "")
        if "取消" in text:
            self.status.setText(tr("已取消。已下载的部分保留，下次会接着下。"))
            self.reject()
            return
        self.title.setText(tr("{name}的对齐模型没有下载完", name=self.name_zh))
        self.status.setText(tr("下载失败：{msg}", msg=text))
        self.btn_cancel.setText(tr("重试"))
        self.btn_cancel.setEnabled(True)

    def _on_cancel(self) -> None:
        if self._failed and (self.worker is None or not self.worker.isRunning()):
            self.title.setText(tr("正在下载{name}的对齐模型", name=self.name_zh))
            self.status.setText(tr("正在连接…"))
            self._start()
            return
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.status.setText(tr("正在停止…"))
            return
        self.reject()

    def closeEvent(self, event):
        if self._succeeded:
            event.accept()
            return
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(event)


def run_align_fetch(parent, code: str) -> tuple[bool, str]:
    """Download this language if its aligner is missing.

    ``(True, "")`` when nothing was needed. On success the message says
    the model is ready. On cancel or failure the message says stitch
    will not start.
    """
    from cutora.asr.align_fetch import fetch_plan
    plan = fetch_plan(code)
    if plan is None:
        return True, ""
    name = str(plan.get("name_zh") or code or "")
    dlg = AlignFetchDialog(plan, parent)
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    if ok:
        return True, tr("{name}的对齐模型已经下载完毕，开始拼接。", name=name)
    return False, tr("{name}的对齐模型没有下载完成，拼接没有开始。", name=name)
