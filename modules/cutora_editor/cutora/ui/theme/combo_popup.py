# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q164 · 下拉菜单一展开就看得到全部选项。

Fusion + 我们的 QSS 下，检查器 / 停靠面板里的下拉框弹出列表算高度时少算了十几像素：
最后一两项被挡住、底下冒出一条滚动箭头，只有一个选项时看起来像「只有一个固定值」。
这里在应用级装一个事件过滤器：

* 每个 QComboBox 出来时把一次最多显示的行数提到 30（Qt 默认 10）；
* 弹出列表显示时按真实行高量一遍，不够就把弹窗拉高到能放下全部（最多 30 行、不超出屏幕），
  并收起多余的滚动箭头。

全程序的下拉框都走这一个地方，不用每个控件改。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox

MAX_VISIBLE = 30
_INSTALLED = {"filter": None}


def _fit_popup(container) -> None:
    view = container.findChild(QAbstractItemView)
    if view is None:
        return
    model = view.model()
    if model is None:
        return
    root = view.rootIndex()
    rows = model.rowCount(root)
    if rows <= 0:
        return
    combo = container.parentWidget()
    limit = MAX_VISIBLE
    if isinstance(combo, QComboBox):
        limit = max(1, min(MAX_VISIBLE, int(combo.maxVisibleItems() or MAX_VISIBLE)))
    shown = [r for r in range(rows) if not view.isRowHidden(r)] if hasattr(view, "isRowHidden") else list(range(rows))
    need_rows = shown[:limit]
    content = sum(max(1, view.sizeHintForRow(r)) for r in need_rows)
    fw = view.frameWidth() * 2
    want_view_h = content + fw
    have = view.height()
    if have >= want_view_h and len(shown) <= limit:
        return
    extra = max(0, want_view_h - have)
    geo = container.geometry()
    new_h = geo.height() + extra
    screen = container.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry() if screen is not None else None
    top = geo.top()
    if avail is not None:
        new_h = min(new_h, avail.height())
        if top + new_h > avail.bottom():
            top = max(avail.top(), avail.bottom() - new_h)
    container.setGeometry(geo.left(), top, geo.width(), new_h)
    # 放得下全部了：滚动箭头不要
    if len(shown) <= limit:
        for ch in container.children():
            try:
                if ch.metaObject().className() == "QComboBoxPrivateScroller":
                    ch.hide()
            except Exception:
                pass
        view.setGeometry(0, 0, geo.width(), new_h)
        try:
            view.scrollToTop()
        except Exception:
            pass


class _ComboPopupFilter(QObject):
    def eventFilter(self, obj, ev):  # noqa: N802
        et = ev.type()
        if et == QEvent.Type.Polish and isinstance(obj, QComboBox):
            if obj.maxVisibleItems() < MAX_VISIBLE:
                obj.setMaxVisibleItems(MAX_VISIBLE)
        elif et == QEvent.Type.Show:
            try:
                if obj.metaObject().className() == "QComboBoxPrivateContainer":
                    parent = obj.parentWidget()
                    if parent is not None and parent.property("cutoraFontCombo"):
                        return False            # Q170：字体框走自己的选择器
                    _fit_popup(obj)
                    QTimer.singleShot(0, lambda o=obj: _safe_fit(o))
            except RuntimeError:
                pass
        return False


def _safe_fit(obj):
    try:
        if obj.isVisible():
            _fit_popup(obj)
    except RuntimeError:
        pass


def install(app=None) -> None:
    app = app or QApplication.instance()
    if app is None or _INSTALLED["filter"] is not None:
        return
    f = _ComboPopupFilter(app)
    app.installEventFilter(f)
    _INSTALLED["filter"] = f
