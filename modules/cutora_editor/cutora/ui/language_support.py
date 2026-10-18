# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""帮助 → 语言支持情况. Every row comes from language_capability()."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from cutora.asr.languages import (
    capability_summary_line, capability_table_cells, load_catalog,
)
from cutora.asr.models import language_capability
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_2, SPACE_3, SPACE_4

_HEADERS = ("语言", "代码", "转写", "逐词对齐", "字幕排版", "字体")
_FOOTER = (
    "逐词对齐就是给每个词标上它在声音里的起止时间。"
    "拼接靠这个时间把视频切到文案对应的那一句。"
    "没有逐词对齐，就只能得到整段文字，对不上该从哪切，所以拼接用不了。"
    "「可用（通用模型，精度略低）」表示时间对得上，用的是一套通用模型，没有专用模型那么准。"
)


class LanguageSupportDialog(QDialog):
    """Non-modal table of the 100 transcribe languages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("语言支持情况"))
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(720, 560)
        self._rows: list[tuple] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_4, SPACE_3, SPACE_4, SPACE_3)
        lay.setSpacing(SPACE_2)
        self.summary = QLabel(tr(capability_summary_line()))
        self.summary.setWordWrap(True)
        self.summary.setObjectName("sectionHeader")
        lay.addWidget(self.summary)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(tr("搜索")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("语言或代码"))
        self.search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search, 1)
        lay.addLayout(search_row)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels([tr(h) for h in _HEADERS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setFont(self.font())
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        foot = QLabel(tr(_FOOTER))
        foot.setWordWrap(True)
        foot.setObjectName("hint")
        lay.addWidget(foot)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(0)
        self._rows = []
        for row in load_catalog():
            cap = language_capability(row["code"])
            cells = capability_table_cells(cap)
            self._rows.append(cells)
            r = self.table.rowCount()
            self.table.insertRow(r)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for r, cells in enumerate(self._rows):
            blob = " ".join(cells).lower()
            self.table.setRowHidden(r, bool(needle) and needle not in blob)


def open_language_support(parent=None) -> LanguageSupportDialog:
    dlg = LanguageSupportDialog(parent)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
