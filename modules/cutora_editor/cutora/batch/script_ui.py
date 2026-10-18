# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""外挂脚本共用暗色弹窗样式 + 工程视频勾选器。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton,
)


def apply_plugin_style(widget):
    """No-op: chrome comes from the application QSS (T03a)."""
    return widget


def task_caption(task, index):
    path = (task or {}).get("video") or (task or {}).get("path") or ""
    name = os.path.basename(path) or ("视频 %d" % (index + 1))
    dur = 0.0
    try:
        dur = float((task or {}).get("duration") or (task or {}).get("trim_end_sec") or 0)
    except Exception:
        dur = 0.0
    if dur > 0:
        m, s = divmod(int(round(dur)), 60)
        return "%02d  %s    %d:%02d" % (index + 1, name, m, s)
    return "%02d  %s" % (index + 1, name)


def expand_indices(spec, n):
    """1-based: 1-3,5 或 [1,2,5]。返回 0-based 去重排序列表。"""
    if spec is None or spec == "" or n <= 0:
        return list(range(n))
    raw = []
    if isinstance(spec, (list, tuple)):
        raw = list(spec)
    else:
        s = str(spec).replace("，", ",").replace(" ", "")
        for part in s.split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    lo, hi = int(a), int(b)
                except ValueError:
                    continue
                if lo > hi:
                    lo, hi = hi, lo
                raw.extend(range(lo, hi + 1))
            else:
                try:
                    raw.append(int(part))
                except ValueError:
                    continue
    out = []
    seen = set()
    zero_based = any(int(x) == 0 for x in raw if str(x).lstrip("-").isdigit())
    for x in raw:
        try:
            i = int(x)
        except Exception:
            continue
        if not zero_based:
            i -= 1
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def select_tasks(tasks, indices=None, videos=None):
    """按序号（1-based）或文件名筛任务。都不给则全选。"""
    tasks = list(tasks or [])
    n = len(tasks)
    if (indices is None or indices == "") and not videos:
        return tasks
    chosen = set()
    if videos:
        names = {os.path.normcase(os.path.basename(str(v))) for v in videos}
        for i, t in enumerate(tasks):
            bn = os.path.normcase(os.path.basename(
                (t or {}).get("video") or (t or {}).get("path") or ""))
            if bn in names:
                chosen.add(i)
    if indices is not None and indices != "":
        for i in expand_indices(indices, n):
            chosen.add(i)
    if not chosen:
        return []
    return [tasks[i] for i in sorted(chosen)]


class _PickDelegate:
    """Q191：勾选行自己画 —— 圆角勾选框（选中填主题色 + 白勾）、编号 / 文件名 / 时长三段、
    奇偶行微亮微暗、悬停一层浅亮。原来是系统那个小方框 + 一行挤在一起的字。"""

    ROW_H = 30

    @staticmethod
    def make(list_widget):
        from PySide6.QtCore import QRectF, QSize
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
        from PySide6.QtWidgets import QStyle, QStyledItemDelegate
        from cutora.ui.theme.tokens import RADIUS_SM, current

        class _D(QStyledItemDelegate):
            def sizeHint(self, option, index):
                return QSize(option.rect.width(), _PickDelegate.ROW_H)

            def paint(self, p: QPainter, option, index):
                tok = current()
                r = QRectF(option.rect)
                p.save()
                try:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    if index.row() % 2 == 1:
                        c = QColor(tok.fg0)
                        c.setAlphaF(0.025)
                        p.fillRect(r, c)
                    if option.state & QStyle.StateFlag.State_MouseOver:
                        c = QColor(tok.fg0)
                        c.setAlphaF(0.07)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(c)
                        p.drawRoundedRect(r.adjusted(2, 1, -2, -1), RADIUS_SM, RADIUS_SM)
                    on = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
                    box = QRectF(r.left() + 10, r.center().y() - 8, 16, 16)
                    if on:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(tok.accent))
                        p.drawRoundedRect(box, RADIUS_SM, RADIUS_SM)
                        path = QPainterPath()
                        path.moveTo(box.left() + 4, box.center().y() + 0.5)
                        path.lineTo(box.left() + 7, box.bottom() - 4.5)
                        path.lineTo(box.right() - 3.5, box.top() + 4.5)
                        pen = QPen(QColor(tok.bg0), 2.0)
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                        p.setPen(pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawPath(path)
                    else:
                        c = QColor(tok.fg2)
                        p.setPen(QPen(c, 1.2))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_SM, RADIUS_SM)
                    parts = index.data(Qt.ItemDataRole.UserRole + 1)
                    if not isinstance(parts, (tuple, list)) or len(parts) != 3:
                        parts = ("", str(index.data(Qt.ItemDataRole.DisplayRole) or ""), "")
                    num, name, dur = parts
                    fm = p.fontMetrics()
                    x = box.right() + 10
                    p.setPen(QColor(tok.fg2))
                    nw = fm.horizontalAdvance("00") + 8
                    p.drawText(QRectF(x, r.top(), nw, r.height()),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, num)
                    dw = fm.horizontalAdvance(dur) + 12 if dur else 0
                    if dur:
                        p.drawText(QRectF(r.right() - dw - 8, r.top(), dw, r.height()),
                                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur)
                    p.setPen(QColor(tok.fg0 if on else tok.fg1))
                    nr = QRectF(x + nw, r.top(), max(0.0, r.right() - dw - 12 - (x + nw)), r.height())
                    p.drawText(nr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                               fm.elidedText(name, Qt.TextElideMode.ElideMiddle, int(nr.width())))
                finally:
                    p.restore()

        d = _D(list_widget)
        list_widget.setItemDelegate(d)
        list_widget.setMouseTracking(True)
        list_widget.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        return d


def caption_parts(task, index):
    """(编号, 文件名, 时长) —— 勾选列表三段式显示用。"""
    path = (task or {}).get("video") or (task or {}).get("path") or ""
    name = os.path.basename(path) or ("视频 %d" % (index + 1))
    try:
        dur = float((task or {}).get("duration") or (task or {}).get("trim_end_sec") or 0)
    except Exception:
        dur = 0.0
    if dur > 0:
        m, s = divmod(int(round(dur)), 60)
        return "%02d" % (index + 1), name, "%d:%02d" % (m, s)
    return "%02d" % (index + 1), name, ""


class TaskPicker(QWidget):
    """工程视频勾选列表。默认全选，整行点一下就是勾 / 取消勾。

    Q96：原来是 ExtendedSelection + 复选框两套状态并存，行被选中(蓝条)
    却没打勾，必须精确点中那个几像素的小方框才算数 —— 这就是「勾选
    某个视频的时候不太好用」。现在去掉选中态，整行就是复选框本身：
    点哪都切换，Shift 点两下切一整段，上面还有一条过滤框。
    """

    def __init__(self, tasks, parent=None):
        super().__init__(parent)
        self._tasks = list(tasks or [])
        self._last_row = -1
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("作用视频")
        title.setObjectName("section")
        title.setToolTip("默认全选。点整行勾选 / 取消；按住鼠标上下拖过多行 = 一起勾上（或一起取消）；\n"
                         "Shift + 点 = 连续一段")
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("hint")
        head.addWidget(self.lbl_count)
        v.addLayout(head)
        row = QHBoxLayout()
        self.btn_all = QPushButton("全选")
        self.btn_none = QPushButton("取消全选")
        self.btn_inv = QPushButton("反选")
        for b in (self.btn_all, self.btn_none, self.btn_inv):
            b.setObjectName("btn_ghost")
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addWidget(self.btn_inv)
        row.addStretch(1)
        v.addLayout(row)
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("过滤文件名…（全选 / 反选只作用于筛出来的行）")
        self.ed_filter.setClearButtonEnabled(True)
        v.addWidget(self.ed_filter)
        self.list = QListWidget()
        # 没有选中态：行本身就是复选框，不再有「选中但没勾」的第二种状态。
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        v.addWidget(self.list, 1)
        for i, t in enumerate(self._tasks):
            item = QListWidgetItem(task_caption(t, i))
            # 故意不给 ItemIsUserCheckable：勾选一律走 _on_click，
            # 否则点在小方框上会被 Qt 和我们各切换一次，净效果是不动。
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setData(Qt.ItemDataRole.CheckStateRole, Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setData(Qt.ItemDataRole.UserRole + 1, caption_parts(t, i))
            self.list.addItem(item)
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_inv.clicked.connect(self._invert)
        self.ed_filter.textChanged.connect(self._apply_filter)
        # Q144：按住左键往下拖 = 一路刷成同一个状态（按下那行勾 → 拖过的都勾；
        # 按下那行不勾 → 拖过的都不勾），和 Excel / 邮箱里刷选一样。
        # 点击、Shift 连选也在这里处理，不再走 itemClicked（它在松开时才来，
        # 拖选会被它再切一次）。
        self._drag_state = None
        self._drag_rows = set()
        self.list.viewport().installEventFilter(self)
        self.list.setObjectName("taskPickerList")
        self._delegate = _PickDelegate.make(self.list)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # 文件名自己会省略
        self._refresh_count()

    # ---- 拖选 ----------------------------------------------------------
    def eventFilter(self, obj, event):  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is not self.list.viewport():
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            item = self.list.itemAt(event.position().toPoint())
            if item is None:
                return False
            mods = event.modifiers()
            row = self.list.row(item)
            want = not self._is_checked(item)
            self.list.blockSignals(True)
            if (mods & Qt.KeyboardModifier.ShiftModifier) and self._last_row >= 0:
                lo, hi = sorted((self._last_row, row))
                for i in range(lo, hi + 1):
                    it = self.list.item(i)
                    if it is not None and not it.isHidden():
                        self._set_checked(it, want)
            else:
                self._set_checked(item, want)
            self._drag_state = want
            self._drag_rows = {row}
            self._last_row = row
            self._refresh_count()
            self.list.viewport().update()
            return True
        if et == QEvent.Type.MouseMove and self._drag_state is not None:
            pos = event.position().toPoint()
            item = self.list.itemAt(pos)
            if item is None:
                # 拖出列表上下边缘时自动滚动
                vp = self.list.viewport()
                if pos.y() < 0:
                    self.list.verticalScrollBar().setValue(self.list.verticalScrollBar().value() - 1)
                elif pos.y() > vp.height():
                    self.list.verticalScrollBar().setValue(self.list.verticalScrollBar().value() + 1)
                return True
            row = self.list.row(item)
            # 拖得快会跳行：把上一行到这一行之间的全刷上
            lo, hi = sorted((self._last_row, row)) if self._last_row >= 0 else (row, row)
            for i in range(lo, hi + 1):
                it = self.list.item(i)
                if it is not None and not it.isHidden() and i not in self._drag_rows:
                    self._set_checked(it, self._drag_state)
                    self._drag_rows.add(i)
            self._last_row = row
            self._refresh_count()
            self.list.viewport().update()
            return True
        if et == QEvent.Type.MouseButtonRelease and self._drag_state is not None:
            self._drag_state = None
            self._drag_rows = set()
            self.list.blockSignals(False)
            it = self.list.item(self._last_row) if self._last_row >= 0 else None
            if it is not None:
                # 整个拖选只通知一次（预览只重算一遍）
                self.list.itemChanged.emit(it)
                self.list.currentItemChanged.emit(it, None)
            return True
        return False

    # ---- 行为 ----------------------------------------------------------
    def _visible_items(self):
        return [self.list.item(i) for i in range(self.list.count())
                if not self.list.item(i).isHidden()]

    def _is_checked(self, item):
        return item.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    def _set_checked(self, item, on):
        item.setData(Qt.ItemDataRole.CheckStateRole,
                     Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _on_click(self, item):
        row = self.list.row(item)
        from PySide6.QtWidgets import QApplication
        mods = QApplication.keyboardModifiers()
        want = not self._is_checked(item)
        if (mods & Qt.KeyboardModifier.ShiftModifier) and self._last_row >= 0:
            lo, hi = sorted((self._last_row, row))
            for i in range(lo, hi + 1):
                it = self.list.item(i)
                if it is not None and not it.isHidden():
                    self._set_checked(it, want)
        else:
            self._set_checked(item, want)
        self._last_row = row
        self._refresh_count()

    def _apply_filter(self, text=""):
        key = (text or "").strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(key) and key not in it.text().lower())
        self._refresh_count()

    def _set_all(self, checked):
        for it in self._visible_items():
            self._set_checked(it, checked)
        self._refresh_count()

    def _invert(self):
        for it in self._visible_items():
            self._set_checked(it, not self._is_checked(it))
        self._refresh_count()

    def _refresh_count(self):
        n = self.selected_count()
        total = len(self._tasks)
        shown = len(self._visible_items())
        if shown != total:
            self.lbl_count.setText("已选 %d / %d（筛出 %d）" % (n, total, shown))
        else:
            self.lbl_count.setText("已选 %d / %d" % (n, total))

    def selected_count(self):
        return len(self.selected_indices())

    def selected_indices(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if self._is_checked(it):
                out.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return out

    def selected_tasks(self):
        idx = set(self.selected_indices())
        return [t for i, t in enumerate(self._tasks) if i in idx]

    def focused_task(self):
        if 0 <= self._last_row < self.list.count():
            it = self.list.item(self._last_row)
            i = int(it.data(Qt.ItemDataRole.UserRole))
            if 0 <= i < len(self._tasks):
                return self._tasks[i]
        sel = self.selected_tasks()
        if sel:
            return sel[0]
        return self._tasks[0] if self._tasks else None
