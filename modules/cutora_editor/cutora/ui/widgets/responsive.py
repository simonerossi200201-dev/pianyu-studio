# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow-dock layout: vertical scroll only, wrap long form rows.

BUG-08: inspector pages must remain usable at any dock width ≥ 200 px.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QFormLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QWidget,
    QVBoxLayout,
)

_SKIP_BTN = {
    "swatchBtn",
    "dockToggleDot",
    "presetMenuBtn",
    "btn_play",
    "btn_skip_start",
    "btn_skip_end",
    "btn_exp",
    "btn_exp_stop",
    "btn_ai",
    "btn_empty_auto",
    "assetLibraryImport",
    "assetLibraryEmptyImport",
}

_EXPLICIT_PREFIX = "_cutora_explicit_"
QWIDGETSIZE_MAX = 16777215


def mark_explicit(widget: QWidget, attr: str) -> None:
    """Record that *attr* was a signed decision and must not be hardened over."""
    widget.setProperty(_EXPLICIT_PREFIX + attr, True)


def is_explicit(widget: QWidget, attr: str) -> bool:
    """True if *attr* was marked, or maximumWidth is already not the Qt default.

    minimumWidth is not inferred: BUG-08 still has to smash unsigned min-width
    traps. Callers who signed a min width must ``mark_explicit``.
    """
    if widget is None:
        return False
    try:
        flagged = widget.property(_EXPLICIT_PREFIX + attr)
        if flagged not in (None, False, 0, "false", "0", ""):
            return True
        if attr == "maximumWidth":
            return int(widget.maximumWidth()) != QWIDGETSIZE_MAX
    except Exception:
        from cutora.core.log import get_logger
        get_logger("cutora.ui.widgets.responsive").exception(
            "is_explicit attr=%s", attr)
        return False
    return False


class ViewportBound(QWidget):
    """Scroll inner whose sizeHint width never exceeds the viewport."""

    def __init__(self, child: QWidget, scroll: QScrollArea):
        super().__init__()
        self._scroll = scroll
        self._child = child
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(child)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if not is_explicit(child, "minimumWidth"):
            child.setMinimumWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._child is not None and not is_explicit(self._child, "maximumWidth"):
            self._child.setMaximumWidth(max(0, self.width()))

    def sizeHint(self) -> QSize:
        sh = self._child.sizeHint() if self._child is not None else super().sizeHint()
        vw = 0
        try:
            vw = int(self._scroll.viewport().width())
        except Exception:
            vw = 0
        width = min(int(sh.width()), vw) if vw > 0 else 0
        return QSize(max(0, width), max(0, int(sh.height())))

    def minimumSizeHint(self) -> QSize:
        if self._child is None:
            return QSize(0, 0)
        mh = self._child.minimumSizeHint()
        sh = self._child.sizeHint()
        return QSize(0, max(0, int(mh.height()), int(sh.height())))


class ElideFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and isinstance(obj, QPushButton):
            if is_explicit(obj, "text") or obj.property("noElide"):
                return False
            full = obj.property("fullText")
            if not full:
                full = obj.text()
                obj.setProperty("fullText", full)
            fm = obj.fontMetrics()
            pad = 16
            elided = fm.elidedText(
                str(full), Qt.TextElideMode.ElideRight, max(8, obj.width() - pad)
            )
            if elided != obj.text():
                obj.setText(elided)
            if not obj.toolTip():
                obj.setToolTip(str(full))
        return False


_ELIDE = ElideFilter()


def configure_form(form: QFormLayout) -> None:
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def configure_scroll(scroll: QScrollArea) -> None:
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    inner = scroll.widget()
    if inner is None:
        return
    if isinstance(inner, ViewportBound):
        return
    inner = scroll.takeWidget()
    if inner is None:
        return
    bound = ViewportBound(inner, scroll)
    scroll.setWidget(bound)


def harden_widget(root: QWidget) -> None:
    """Walk a page: no horizontal min-width traps, forms wrap, buttons elide."""
    if getattr(root, "_cutora_hardened", False):
        return
    root._cutora_hardened = True
    try:
        from cutora.ui.wheel_guard import install_wheel_guard_on_tree
        install_wheel_guard_on_tree(root)
    except Exception:
        pass
    for sa in root.findChildren(QScrollArea):
        configure_scroll(sa)
    for w in root.findChildren(QWidget):
        lay = QWidget.layout(w)
        if isinstance(lay, QFormLayout):
            configure_form(lay)
    for cb in root.findChildren(QComboBox):
        if not is_explicit(cb, "minimumWidth"):
            cb.setMinimumWidth(0)
        if not is_explicit(cb, "sizePolicy"):
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for sl in root.findChildren(QSlider):
        if not is_explicit(sl, "minimumWidth"):
            sl.setMinimumWidth(24)
        if not is_explicit(sl, "sizePolicy"):
            sl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for sp in root.findChildren(QAbstractSpinBox):
        if not is_explicit(sp, "minimumWidth"):
            sp.setMinimumWidth(
                min(sp.minimumWidth() or 48, 72) if sp.minimumWidth() else 48)
            if sp.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed:
                if sp.minimumWidth() < 48:
                    sp.setMinimumWidth(48)
    for btn in root.findChildren(QPushButton):
        name = btn.objectName() or ""
        if name in _SKIP_BTN or btn.property("noElide") or is_explicit(btn, "text"):
            continue
        if btn.maximumWidth() < 40:
            continue
        if not is_explicit(btn, "minimumWidth"):
            btn.setMinimumWidth(0)
        if not is_explicit(btn, "sizePolicy"):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if btn.property("fullText") is None:
            btn.setProperty("fullText", btn.text())
        btn.installEventFilter(_ELIDE)
        if not btn.toolTip() and btn.text():
            btn.setToolTip(btn.text())
    for tb in root.findChildren(QToolButton):
        if tb.objectName() in _SKIP_BTN:
            continue
        if tb.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly:
            continue
        if not is_explicit(tb, "minimumWidth"):
            tb.setMinimumWidth(0)


def harden_docks(win: QWidget) -> None:
    """BUG-08: apply to the inspector and every other dock page."""
    for name in (
        "tabs",
        "left_tabs",
        "sub_panel",
        "style_gallery",
        "prop_panel",
        "role_panel",
        "word_block_panel",
        "asset_library",
        "sticker_panel",
        "transition_panel",
        "transcript_panel",
    ):
        w = getattr(win, name, None)
        if w is not None:
            harden_widget(w)
    insp = getattr(win, "dock_right", None)
    if insp is not None:
        if not is_explicit(insp, "minimumWidth"):
            from cutora.ui.theme.tokens import DOCK_RIGHT_MAX, DOCK_RIGHT_MIN
            insp.setMinimumWidth(DOCK_RIGHT_MIN)
        if not is_explicit(insp, "maximumWidth"):
            from cutora.ui.theme.tokens import DOCK_RIGHT_MAX
            insp.setMaximumWidth(DOCK_RIGHT_MAX)
        inner = insp.widget()
        if inner is not None:
            if not is_explicit(inner, "minimumWidth"):
                inner.setMinimumWidth(0)
            harden_widget(inner)
