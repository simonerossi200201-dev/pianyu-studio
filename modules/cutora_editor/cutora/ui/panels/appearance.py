# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings → 外观: six theme cards + restore default + optional accent."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import DEFAULT_THEME_ID, RADIUS_CARD, RADIUS_SM, current, list_themes


class ThemeCard(QWidget):
    """Mini chrome: a button, a line of type, one track. Click to apply."""

    picked = Signal(str)

    def __init__(self, theme_id: str, palette: dict, parent=None):
        super().__init__(parent)
        self.theme_id = theme_id
        self.palette = palette
        self.setFixedSize(148, 92)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("%s / %s" % (palette.get("name_zh") or theme_id,
                                     palette.get("name_en") or theme_id))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.theme_id)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = self.palette
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qp.setPen(QPen(QColor(p["line"]), 1))
        qp.setBrush(QColor(p["bg1"]))
        qp.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, RADIUS_CARD, RADIUS_CARD)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(p["accent"]))
        qp.drawRoundedRect(12, 14, 48, 16, RADIUS_SM, RADIUS_SM)
        qp.setPen(QColor(p["fg0"]))
        qp.drawText(68, 26, p.get("name_zh") or self.theme_id)
        qp.setPen(QColor(p["fg1"]))
        qp.drawText(12, 48, p.get("name_en") or "")
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(p["track_video"]))
        qp.drawRoundedRect(12, 58, self.width() - 24, 18, RADIUS_SM, RADIUS_SM)
        if current().theme_id == self.theme_id:
            qp.setPen(QPen(QColor(p["accent"]), 2))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawRoundedRect(2, 2, self.width() - 4, self.height() - 4, RADIUS_CARD, RADIUS_CARD)
        qp.end()


class AppearancePage(QWidget):
    """First settings tab. apply_theme is the only switcher."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        title = QLabel(tr("配色方案"))
        title.setObjectName("sectionHeader")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setSpacing(10)
        self._cards = []
        for i, (tid, pal) in enumerate(list_themes()):
            card = ThemeCard(tid, pal, self)
            card.picked.connect(self._on_pick)
            self._cards.append(card)
            grid.addWidget(card, i // 3, i % 3)
        lay.addLayout(grid)
        row = QHBoxLayout()
        self.btn_default = QPushButton(tr("恢复默认（罗马金）"))
        self.btn_default.clicked.connect(lambda: self._on_pick(DEFAULT_THEME_ID))
        self.btn_accent = QPushButton(tr("自定义强调色…"))
        self.btn_accent.clicked.connect(self._on_custom_accent)
        row.addWidget(self.btn_default)
        row.addWidget(self.btn_accent)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel(tr("应用图标与关于框标志保持金色，不随主题改变。"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)

    def _on_pick(self, theme_id: str) -> None:
        from cutora.ui.theme.apply import apply_theme
        apply_theme(theme_id)
        for card in self._cards:
            card.update()

    def _on_custom_accent(self) -> None:
        from cutora.ui.theme.apply import apply_theme
        from cutora.ui.theme.tokens import current, load_palette_data
        from cutora.ui.theme.contrast import check_palette

        t = current()
        col = QColorDialog.getColor(QColor(t.accent), self, tr("自定义强调色"))
        if not col.isValid():
            return
        hex_color = col.name().upper()
        data = dict(load_palette_data(t.theme_id))
        data["accent"] = hex_color
        from cutora.ui.theme.tokens import darken_hex, lighten_hex
        data["accent_hi"] = lighten_hex(hex_color, 0.18)
        data["accent_dim"] = darken_hex(hex_color, 0.30)
        fails = check_palette(data, t.theme_id)
        if fails:
            QMessageBox.warning(
                self, tr("对比度不足"),
                tr("这个强调色对比度不够，未应用。\n") + "\n".join(fails[:4]),
            )
            return
        apply_theme(t.theme_id, accent_override=hex_color)
        for card in self._cards:
            card.update()
