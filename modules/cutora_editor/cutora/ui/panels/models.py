# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings → 模型. UI calls asr.models.* commands."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from cutora.ui.i18n import tr
from cutora.ui.widgets.collapsible import CollapsibleSection

GROUP_ORDER = (("asr", "识别"), ("align", "对齐"), ("vad", "VAD"), ("matte", "AI 抠像"), ("track", "AI 跟踪"))
COLS = 6


class ModelsPage(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.tables = {}
        self.sections = {}
        self._active_kind = "asr"
        lay = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        for kind, title in GROUP_ORDER:
            sec = CollapsibleSection(title)
            table = self._make_table()
            table.itemSelectionChanged.connect(
                lambda k=kind: self._on_select(k))
            sec.addWidget(table)
            if kind == "vad":
                note = QLabel(tr("运行时 VAD 是 faster-whisper 内置 Silero（MIT）。"))
                note.setObjectName("hint")
                note.setWordWrap(True)
                sec.addWidget(note)
            elif kind == "matte":
                note = QLabel(tr("SAM 2.1（Meta，Apache 2.0）：提示式分割 + 视频传播，全程本地推理，不上传画面。"
                                 "Large 更准（建议 8 GB+ 显存），Base+ 更省。"))
                note.setObjectName("hint")
                note.setWordWrap(True)
                sec.addWidget(note)
            elif kind == "track":
                note = QLabel(tr("TAPNext++（Google DeepMind，Apache 2.0）：任意点跟踪，全程本地推理。"
                                 "512 档最准，256 档更快；两个文件都约 2.4 GB。"))
                note.setObjectName("hint")
                note.setWordWrap(True)
                sec.addWidget(note)
            inner_lay.addWidget(sec)
            self.tables[kind] = table
            self.sections[kind] = sec
        inner_lay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        self.table = self.tables["asr"]
        row = QHBoxLayout()
        self.btn_dl = QPushButton(tr("下载"))
        self.btn_rm = QPushButton(tr("删除"))
        self.btn_dl.clicked.connect(self._download)
        self.btn_rm.clicked.connect(self._delete)
        row.addWidget(self.btn_dl)
        row.addWidget(self.btn_rm)
        row.addStretch(1)
        lay.addLayout(row)
        self.reload()

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, COLS)
        table.setHorizontalHeaderLabels([
            tr("模型"), tr("状态"), tr("大小"), tr("许可证"), tr("启用"), tr("id"),
        ])
        table.setColumnHidden(5, True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def _bus(self):
        return getattr(self.host, "bus", None)

    def _on_select(self, kind: str) -> None:
        self._active_kind = kind
        self.table = self.tables[kind]

    def reload(self) -> None:
        bus = self._bus()
        if bus is None:
            from cutora.core.commands import get_bus
            bus = get_bus()
        out = bus.execute("asr.models.list", {}, source="ui")
        rec = out.get("recommend") or ""
        self.hint.setText(tr("按显存建议：{id}。下载只走 huggingface.co。", id=rec))
        grouped = {"asr": [], "align": [], "vad": []}
        for m in list(out.get("models") or []):
            kind = str(m.get("kind") or "asr")
            grouped.setdefault(kind, []).append(m)
        for kind, _title in GROUP_ORDER:
            rows = grouped.get(kind) or []
            self._fill_table(self.tables[kind], rows)
            if kind == "align":
                n_on = sum(1 for r in rows if r.get("enabled"))
                self.sections[kind].setTitle(
                    tr("对齐  已启用 {n} / 共 {m}", n=n_on, m=len(rows)))
                self.sections[kind].setExpanded(False)
            else:
                self.sections[kind].setExpanded(True)

    def _fill_table(self, table: QTableWidget, rows: list) -> None:
        rows = [m for m in rows if m.get("enabled") is not False]
        table.setRowCount(len(rows))
        for i, m in enumerate(rows):
            title = str(m.get("title_zh") or m.get("id"))
            if str(m.get("id") or "") == "asr-large-v3-turbo" and "实验" not in title:
                title = title + "（实验）"
            table.setItem(i, 0, QTableWidgetItem(title))
            table.setItem(i, 1, QTableWidgetItem(str(m.get("status") or "")))
            size = m.get("size_mb")
            size_txt = "" if size is None else "%s MB" % size
            table.setItem(i, 2, QTableWidgetItem(size_txt))
            table.setItem(i, 3, QTableWidgetItem(str(m.get("license") or "")))
            table.setItem(i, 4, QTableWidgetItem(
                tr("是") if m.get("enabled") else tr("否")))
            id_item = QTableWidgetItem(str(m.get("id") or ""))
            id_item.setData(Qt.ItemDataRole.UserRole, {
                "local_only": bool(m.get("local_only")),
                "bundled": bool(m.get("bundled")),
            })
            table.setItem(i, 5, id_item)

    def _selected_meta(self) -> tuple[str, dict]:
        table = self.tables.get(self._active_kind) or self.table
        r = table.currentRow()
        if r < 0:
            return "", {}
        item = table.item(r, 5)
        if item is None:
            return "", {}
        meta = item.data(Qt.ItemDataRole.UserRole) or {}
        if not isinstance(meta, dict):
            meta = {}
        return item.text(), meta

    def _download(self) -> None:
        model_id, meta = self._selected_meta()
        if not model_id or meta.get("local_only") or meta.get("bundled"):
            return
        # Q152-D：先过许可闸门（作者 / 许可 / sha256），再后台下载带进度和取消；不再在 GUI 线程里同步下
        try:
            from cutora.asr.models import get_model
            from cutora.ui.model_download_dialog import ModelDownloadDialog
            row = get_model(model_id)
        except Exception:
            row = None
        if row is not None:
            ModelDownloadDialog(row, self).exec()
        else:
            bus = self._bus() or __import__(
                "cutora.core.commands", fromlist=["get_bus"]).get_bus()
            bus.execute("asr.models.download", {"id": model_id}, source="ui")
        self.reload()

    def _delete(self) -> None:
        model_id, meta = self._selected_meta()
        if not model_id or meta.get("bundled"):
            return
        bus = self._bus() or __import__(
            "cutora.core.commands", fromlist=["get_bus"]).get_bus()
        bus.execute(
            "asr.models.delete", {"id": model_id, "confirm": True}, source="ui")
        self.reload()
