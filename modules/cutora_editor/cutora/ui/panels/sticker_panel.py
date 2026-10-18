# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sticker picker: category tabs + searchable grid. Noto Color Emoji COLRv1."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QGridLayout, QLineEdit, QScrollArea, QSizePolicy, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

from cutora.render.stickers import catalog_items, raster_sticker
from cutora.ui.i18n import tr

CAT_LABELS = {
    "smileys": "表情",
    "people": "人物",
    "gestures": "手势",
    "nature": "自然",
    "food": "食物",
    "travel": "出行",
    "activities": "活动",
    "objects": "物件",
    "symbols": "符号",
    "flags": "旗帜",
}
THUMB = 44
COLS = 5


class StickerPanel(QWidget):
    insert_requested = Signal(str)

    def __init__(self, parent=None, populate=True):
        super().__init__(parent)
        self.setObjectName("stickerPanel")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._ready = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.sticker_search = QLineEdit()
        self.sticker_search.setPlaceholderText(tr("搜索贴纸（英文名）"))
        self.sticker_search.textChanged.connect(self._on_filter)
        lay.addWidget(self.sticker_search)
        self.sticker_tabs = QTabBar()
        self.sticker_tabs.setExpanding(False)
        self.sticker_tabs.setDrawBase(False)
        self.sticker_tabs.setUsesScrollButtons(True)
        self.sticker_tabs.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        cats = []
        for it in catalog_items():
            c = str(it.get("category") or "")
            if c and c not in cats:
                cats.append(c)
        self._cat_order = [""] + cats
        self.sticker_tabs.addTab(tr("全部"))
        for c in cats:
            self.sticker_tabs.addTab(tr(CAT_LABELS.get(c, c)))
        if cats:
            self.sticker_tabs.setCurrentIndex(1)
        lay.addWidget(self.sticker_tabs)
        self.sticker_grid = QWidget()
        self._grid = QGridLayout(self.sticker_grid)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.sticker_grid)
        lay.addWidget(scroll, 1)
        self._buttons: list[QToolButton] = []
        self._pending_icons: list[tuple] = []
        self._icon_timer = None
        self._ready = True
        self._want_populate = bool(populate)
        self.sticker_tabs.currentChanged.connect(self._on_filter)

    def sizeHint(self):
        return QSize(280, 480)

    def minimumSizeHint(self):
        return QSize(200, 240)

    def showEvent(self, event):
        super().showEvent(event)
        if self._want_populate and not self._buttons:
            self._rebuild()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._buttons:
            return
        if getattr(self, "_resize_timer", None) is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._rebuild)
        self._resize_timer.start(50)

    def _on_filter(self, *_args) -> None:
        if not getattr(self, "_ready", False):
            return
        self._rebuild()

    def _filtered(self) -> list[dict]:
        q = (self.sticker_search.text() or "").strip().casefold()
        idx = self.sticker_tabs.currentIndex()
        cat = self._cat_order[idx] if 0 <= idx < len(self._cat_order) else ""
        out = []
        for it in catalog_items():
            if cat and str(it.get("category") or "") != cat:
                continue
            if q:
                blob = " ".join([
                    str(it.get("id") or ""),
                    str(it.get("name") or ""),
                    " ".join(it.get("keywords") or []),
                ]).casefold()
                if q not in blob:
                    continue
            out.append(it)
        return out

    def _rebuild(self) -> None:
        if getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            self._rebuild_inner()
        finally:
            self._rebuilding = False

    def _rebuild_inner(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._buttons = []
        self._pending_icons = []
        items = self._filtered()
        cell = THUMB + 8 + self._grid.spacing()
        avail = max(cell, int(self.sticker_grid.width() or self.width() or cell))
        cols = max(1, avail // cell)
        self._cols = cols
        for i, it in enumerate(items):
            sid = str(it.get("id") or "")
            btn = QToolButton()
            btn.setAutoRaise(True)
            btn.setToolTip("%s (%s)" % (it.get("name") or sid, sid))
            btn.setFixedSize(THUMB + 8, THUMB + 8)
            btn.setIconSize(QSize(THUMB, THUMB))
            btn.clicked.connect(lambda _=False, s=sid: self.insert_requested.emit(s))
            self._grid.addWidget(btn, i // cols, i % cols)
            self._buttons.append(btn)
            self._pending_icons.append((btn, sid))
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        if self._icon_timer is not None:
            self._icon_timer.stop()
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(0)
        self._icon_timer.timeout.connect(self._fill_icon_batch)
        self._icon_timer.start()

    def _fill_icon_batch(self) -> None:
        n = 0
        while self._pending_icons and n < 16:
            btn, sid = self._pending_icons.pop(0)
            n += 1
            if btn is None:
                continue
            img = raster_sticker(sid, THUMB)
            pm = QPixmap.fromImage(img)
            if not pm.isNull():
                btn.setIcon(QIcon(pm))
        if not self._pending_icons and self._icon_timer is not None:
            self._icon_timer.stop()
