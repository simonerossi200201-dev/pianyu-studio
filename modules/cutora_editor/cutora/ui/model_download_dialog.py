# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · 模型下载前的许可闸门 + 下载进度（任何模型都能用，先给 AI 抠像用）。

下载前把 作者 / 许可 / 来源仓库 / 钉死的 revision / 大小 / 权重 sha256 摆出来，用户点「同意并下载」才下；
下载在后台线程（MatteWorker kind=download），有进度和取消；取消保留 .part，下次续传。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_2, SPACE_3


def model_summary_rows(row: dict) -> list:
    """(标签, 值) 列表，给对话框和测试用。"""
    files = list(row.get("files") or [])
    total = 0
    for f in files:
        try:
            total += int(f.get("size") or 0)
        except (TypeError, ValueError):
            pass
    size_mb = total / (1024.0 * 1024.0) if total else float(row.get("size_mb") or 0.0)
    out = [
        (tr("模型"), str(row.get("title_zh") or row.get("id") or "")),
        (tr("作者"), str(row.get("author") or "—")),
        (tr("许可"), str(row.get("license") or "—")),
        (tr("来源"), "huggingface.co/%s" % row.get("repo", "")),
        (tr("版本"), str(row.get("revision") or "")[:12]),
        (tr("大小"), "%.0f MB（%d 个文件）" % (size_mb, len(files) or 1)),
        (tr("权重 SHA256"), str(row.get("sha256") or "")),
    ]
    if row.get("note"):
        out.append((tr("说明"), str(row.get("note"))))
    return out


class ModelDownloadDialog(QDialog):
    """关闭时 Accepted = 下载完成并校验通过。"""

    def __init__(self, row: dict, parent=None, *, worker_factory=None):
        super().__init__(parent)
        self.row = dict(row or {})
        self.model_id = str(self.row.get("id") or "")
        self.worker = None
        self._worker_factory = worker_factory
        self.setWindowTitle(tr("下载模型"))
        self.setObjectName("modelDownloadDialog")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(SPACE_3)
        lay.setContentsMargins(SPACE_3 * 2, SPACE_3 * 2, SPACE_3 * 2, SPACE_3 * 2)
        head = QLabel(tr("这个模型只在本机运行，不会上传任何画面。下载只从 huggingface.co 取，下载后按 SHA256 校验。"))
        head.setWordWrap(True)
        lay.addWidget(head)
        for label, value in model_summary_rows(self.row):
            r = QHBoxLayout()
            r.setSpacing(SPACE_2)
            k = QLabel(label)
            k.setObjectName("fieldLabel")
            k.setFixedWidth(96)
            k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            r.addWidget(k)
            r.addWidget(v, 1)
            lay.addLayout(r)
        lic_url = str(self.row.get("license_url") or "")
        if lic_url:
            link = QLabel('<a href="%s">%s</a>' % (lic_url, tr("查看许可全文（浏览器打开）")))
            link.setOpenExternalLinks(True)
            lay.addWidget(link)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setValue(0)
        self.prog.setTextVisible(True)
        self.prog.hide()
        lay.addWidget(self.prog)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row_btn = QHBoxLayout()
        row_btn.addStretch(1)
        self.btn_cancel = QPushButton(tr("取消"))
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_ok = QPushButton(tr("同意并下载"))
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._start)
        row_btn.addWidget(self.btn_cancel)
        row_btn.addWidget(self.btn_ok)
        lay.addLayout(row_btn)
        self._buttons = QDialogButtonBox()      # 占位：保持和其他对话框一致的 Tab 顺序
        self._buttons.hide()

    # ---- 下载 ----
    def _start(self):
        if self.worker is not None:
            return
        self.btn_ok.setEnabled(False)
        self.prog.show()
        self.status.setText(tr("正在下载…"))
        if self._worker_factory is not None:
            w = self._worker_factory({"model": self.model_id})
        else:
            from cutora.ui.matte_worker import MatteWorker
            w = MatteWorker("download", {"model": self.model_id}, parent=self)
        self.worker = w
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.start()

    def _on_progress(self, done_kb, total_kb, _f, _s):
        total_kb = max(1, int(total_kb))
        self.prog.setValue(int(1000 * min(1.0, done_kb / float(total_kb))))
        self.status.setText(tr("已下载 %.0f / %.0f MB") % (done_kb / 1024.0, total_kb / 1024.0))

    def _on_done(self, _result):
        self.prog.setValue(1000)
        self.status.setText(tr("下载完成，校验通过。"))
        self.worker = None
        self.accept()

    def _on_failed(self, msg):
        self.worker = None
        self.btn_ok.setEnabled(True)
        self.btn_ok.setText(tr("重试"))
        if "取消" in str(msg):
            self.status.setText(tr("已取消；已下载的部分保留，下次继续。"))
        else:
            self.status.setText(tr("下载失败：%s") % msg)

    def _on_cancel(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.status.setText(tr("正在停止…"))
            return
        self.reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(event)
