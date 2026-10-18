# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""帮助 → 控制台. A non-modal view of the in-memory log ring.

Closing the window does not stop collection. Opening it again replays the
ring. Auto-scroll stays on until the user moves off the bottom, and comes
back when they return there.
"""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from cutora.core.log import privacy_note, ring_buffer
from cutora.core.paths import user_logs_dir
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    ACCENT,
    BG0,
    ERR,
    FG0,
    FG_MUTED,
    FONT_BODY,
    FONT_LABEL,
    FONT_MONO,
    SPACE_2,
    SPACE_3,
    SPACE_6,
    WARN,
)


def _color_for(level: int) -> str:
    if level >= logging.ERROR:
        return ERR
    if level >= logging.WARNING:
        return WARN
    if level >= logging.INFO:
        return FG0
    return FG_MUTED


def format_record(snap) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(getattr(snap, "created", 0) or 0))
    return "%s %s %s: %s" % (ts, snap.levelname, snap.name, snap.getMessage())


class ConsoleView(QDialog):
    """Non-modal log window. The ring keeps filling while this is closed."""

    _arrived = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("控制台"))
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(SPACE_6 * 22, SPACE_6 * 14)
        self._min_level = logging.DEBUG
        self._follow = True
        self._adjusting = False
        self._hooked = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_3, SPACE_2, SPACE_3, SPACE_2)
        lay.setSpacing(SPACE_2)

        self.note = QLabel(privacy_note(), self)
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        note_font = QFont(FONT_MONO)
        note_font.setPixelSize(FONT_LABEL)
        self.note.setFont(note_font)
        lay.addWidget(self.note)

        tools = QHBoxLayout()
        tools.setSpacing(SPACE_2)
        self.level_combo = QComboBox(self)
        self.level_combo.addItem("DEBUG", logging.DEBUG)
        self.level_combo.addItem("INFO", logging.INFO)
        self.level_combo.addItem("WARNING", logging.WARNING)
        self.level_combo.addItem("ERROR", logging.ERROR)
        self.level_combo.setToolTip(tr("只显示这个级别和更高的"))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(tr("搜索"))
        self.search.setClearButtonEnabled(True)
        tools.addWidget(self.level_combo)
        tools.addWidget(self.search, 1)
        lay.addLayout(tools)

        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        self.editor.setObjectName("consoleLog")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont(FONT_MONO)
        font.setPixelSize(FONT_BODY)
        self.editor.setFont(font)
        pal = self.editor.palette()
        pal.setColor(self.editor.backgroundRole(), QColor(BG0))
        pal.setColor(self.editor.foregroundRole(), QColor(FG0))
        self.editor.setPalette(pal)
        lay.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.setSpacing(SPACE_2)
        self.btn_copy = QPushButton(tr("复制全部"), self)
        self.btn_clear = QPushButton(tr("清空"), self)
        self.btn_folder = QPushButton(tr("打开日志文件夹"), self)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_clear)
        row.addWidget(self.btn_folder)
        row.addStretch(1)
        lay.addLayout(row)

        self.level_combo.currentIndexChanged.connect(self._on_level)
        self.search.textChanged.connect(lambda _t: self._highlight())
        self.btn_copy.clicked.connect(self._copy_all)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_folder.clicked.connect(self._open_folder)
        self.editor.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._arrived.connect(self._append_snap, Qt.ConnectionType.QueuedConnection)

    def follows_tail(self) -> bool:
        return bool(self._follow)

    def shown_line_count(self) -> int:
        text = self.editor.toPlainText()
        if not text.strip():
            return 0
        return len(text.splitlines())

    def showEvent(self, event) -> None:
        self.note.setText(privacy_note())
        self._attach()
        self._rebuild(ring_buffer().records())
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self._detach()
        event.accept()

    def _attach(self) -> None:
        if self._hooked:
            return
        ring_buffer().add_callback(self._enqueue)
        self._hooked = True

    def _detach(self) -> None:
        if not self._hooked:
            return
        try:
            ring_buffer().remove_callback(self._enqueue)
        except Exception:
            pass
        self._hooked = False

    def _enqueue(self, snap) -> None:
        self._arrived.emit(snap)

    def _passes(self, snap) -> bool:
        return int(getattr(snap, "levelno", 0)) >= int(self._min_level)

    def _on_level(self, _index: int) -> None:
        data = self.level_combo.currentData()
        self._min_level = int(data if data is not None else logging.DEBUG)
        self._rebuild(ring_buffer().records())

    def _on_scroll(self, value: int) -> None:
        if self._adjusting:
            return
        bar = self.editor.verticalScrollBar()
        self._follow = value >= bar.maximum()

    def _rebuild(self, snaps) -> None:
        self._adjusting = True
        try:
            self.editor.clear()
            for snap in snaps:
                if self._passes(snap):
                    self._insert(snap)
            bar = self.editor.verticalScrollBar()
            if self._follow:
                bar.setValue(bar.maximum())
        finally:
            self._adjusting = False
        self._highlight()

    def _append_snap(self, snap) -> None:
        if not self._passes(snap):
            return
        bar = self.editor.verticalScrollBar()
        old = bar.value()
        self._adjusting = True
        try:
            self._insert(snap)
            if self._follow:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(min(old, bar.maximum()))
        finally:
            self._adjusting = False
        self._highlight()

    def _insert(self, snap) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_color_for(int(snap.levelno))))
        cursor.setCharFormat(fmt)
        cursor.insertText(format_record(snap) + "\n")
        self.editor.setTextCursor(cursor)

    def _highlight(self) -> None:
        term = self.search.text()
        selections = []
        if term:
            doc = self.editor.document()
            cursor = QTextCursor(doc)
            found = doc.find(term, cursor)
            color = QColor(ACCENT)
            color.setAlpha(140)
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            fmt.setForeground(QColor(BG0))
            guard = 0
            while not found.isNull() and guard < 2000:
                sel = QTextEdit.ExtraSelection()
                sel.cursor = found
                sel.format = fmt
                selections.append(sel)
                found = doc.find(term, found)
                guard += 1
        self.editor.setExtraSelections(selections)

    def _copy_all(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.clipboard().setText(self.editor.toPlainText())

    def _clear(self) -> None:
        ring_buffer().clear()
        self._rebuild([])

    def _open_folder(self) -> None:
        folder = user_logs_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
