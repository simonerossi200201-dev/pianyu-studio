# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Batch auto-subtitle detect results as a left-dock tab (not a window)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from cutora.asr.detect_plan import BATCH_RESULT_COLUMNS
from cutora.asr.languages import picker_entries
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import WARN

COLUMN_KEYS = BATCH_RESULT_COLUMNS
WARN_ROLE = Qt.ItemDataRole.UserRole + 1


class AsrBatchResults(QWidget):
    rerun_yellow = Signal(list)
    rerun_corrected = Signal(list)
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([
            tr("文件"), tr("语言"), tr("置信度"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        btn = QPushButton(tr("重跑标黄的行"))
        btn.clicked.connect(self._emit_yellow)
        row.addWidget(btn)
        btn_c = QPushButton(tr("重跑纠正过的行"))
        btn_c.clicked.connect(self._emit_corrected)
        row.addWidget(btn_c)
        row.addStretch(1)
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.close_requested.emit)
        row.addWidget(close_btn)
        lay.addLayout(row)

    def set_rows(self, rows: list[dict]) -> None:
        self._rows = list(rows or [])
        self.table.setRowCount(len(self._rows))
        entries = picker_entries()
        warn_bg = QBrush(QColor(WARN))
        for i, rec in enumerate(self._rows):
            name = rec.get("path") or ""
            self.table.setItem(i, 0, QTableWidgetItem(name))
            combo = QComboBox()
            for ent in entries:
                if ent["code"] == "auto":
                    continue
                combo.addItem(ent["label"], ent["code"])
            lang = rec.get("language")
            idx = combo.findData(lang)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setProperty("orig", lang if idx >= 0 else combo.currentData())
            self.table.setCellWidget(i, 1, combo)
            conf = rec.get("confidence")
            conf_txt = "" if conf is None else "%.0f%%" % (float(conf) * 100.0)
            conf_item = QTableWidgetItem(conf_txt)
            self.table.setItem(i, 2, conf_item)
            if rec.get("warn"):
                self.table.item(i, 0).setBackground(warn_bg)
                conf_item.setBackground(warn_bg)
                conf_item.setData(WARN_ROLE, True)

    def yellow_ids(self) -> list[int]:
        return [int(r["media_id"]) for r in self._rows if r.get("warn")]

    def language_for(self, media_id: int) -> str | None:
        for i, rec in enumerate(self._rows):
            if int(rec.get("media_id", -1)) != int(media_id):
                continue
            combo = self.table.cellWidget(i, 1)
            if combo is not None:
                return combo.currentData()
            return rec.get("language")
        return None

    def corrected_ids(self) -> list[int]:
        out = []
        for i, rec in enumerate(self._rows):
            combo = self.table.cellWidget(i, 1)
            if combo is None:
                continue
            now = combo.currentData()
            orig = combo.property("orig")
            if now != orig:
                rec["language"] = now
                combo.setProperty("orig", now)
                out.append(int(rec["media_id"]))
        return out

    def _emit_yellow(self) -> None:
        self.rerun_yellow.emit(self.yellow_ids())

    def _emit_corrected(self) -> None:
        self.rerun_corrected.emit(self.corrected_ids())
