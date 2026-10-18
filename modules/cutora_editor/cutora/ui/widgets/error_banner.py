# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dismissable error strip. Not a modal — status-bar companion."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from cutora.ui.i18n import tr


class ErrorBanner(QFrame):
    """Thin bar above the status bar. Hidden until ``show_error``."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorBanner")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(8)
        self._label = QLabel()
        self._label.setObjectName("ErrorBannerLabel")
        self._label.setWordWrap(True)
        close = QPushButton(tr("×"))
        close.setObjectName("ErrorBannerClose")
        close.setFlat(True)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(tr("关闭"))
        close.clicked.connect(self.hide)
        layout.addWidget(self._label, 1)
        layout.addWidget(close, 0)

    def show_error(self, text: str) -> None:
        self._label.setText(text)
        self.setVisible(True)
        self.raise_()
