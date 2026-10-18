# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q153 · 像电子表格一样用的 QTableWidget（对标 Google Sheets / Excel 的手感）。

- 选区外框 + 右下角「填充柄」小方块：按住往下（或往上）拖 = 把选中的那几格按顺序
  重复填过去；双击填充柄 = 一直填到最后一行。
- Ctrl+C 复制选区（制表符分隔，贴到 Sheets / Excel 也对得上）；Ctrl+V：
  · 剪贴板只有一格、选了一片 → 这一片全部填成它；
  · 剪贴板是一块 → 从当前格往右下贴，超出表格的丢掉。
- Delete / Backspace 清空选中的可编辑格。
- Ctrl+Z / Ctrl+Y（Ctrl+Shift+Z）撤销重做：粘贴、拖填充柄、清空、改一格，各算一步。

哪些列能改、每列的值怎么存怎么显示，由子类决定（editable_cols / cell_value /
set_cell_value / normalize_value）。基类只管表格手感。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QTableWidgetItem

from cutora.ui.theme.tokens import current

HANDLE = 7          # 填充柄边长（像素）
HANDLE_HIT = 6      # 命中半径


def parse_grid(text: str) -> list[list[str]]:
    """剪贴板 TSV → 二维表。单元格里的换行（被双引号包着）照样是一格。"""
    import csv
    import io
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if raw.endswith("\n"):
        raw = raw[:-1]
    if raw == "":
        return []
    rows = list(csv.reader(io.StringIO(raw), delimiter="\t"))
    return [[str(c) for c in row] if row else [""] for row in rows]


def to_tsv(grid) -> str:
    out = []
    for row in grid:
        cells = []
        for c in row:
            s = str(c if c is not None else "")
            if any(ch in s for ch in ('\t', '\n', '"')):
                s = '"' + s.replace('"', '""') + '"'
            cells.append(s)
        out.append("\t".join(cells))
    return "\n".join(out)


class SheetTable(QTableWidget):
    cells_changed = Signal()          # 一次批量改动（粘贴 / 填充 / 清空 / 撤销）结束
    status = Signal(str)              # 给界面一句话（比如「粘贴了 12 格，3 格模板名不认识」）

    def __init__(self, rows, cols, parent=None):
        super().__init__(rows, cols, parent)
        self.editable_cols: set = set()
        self._undo: list = []
        self._redo: list = []
        self._shadow: dict = {}
        self._batch = 0
        self._fill = None             # 拖填充柄：{"r0","r1","c0","c1","to"}
        self._clip = None             # (tsv 文本, 二维值) —— 表内复制保留原值（比如模板名）
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.itemChanged.connect(self._on_item_changed)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(40)
        self._scroll_timer.timeout.connect(self._auto_scroll)
        self._scroll_dir = 0

    # ── 子类可覆盖 ──
    def cell_value(self, r, c):
        it = self.item(r, c)
        return it.text() if it is not None else ""

    def set_cell_value(self, r, c, value) -> None:
        it = self.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.setItem(r, c, it)
        it.setText("" if value is None else str(value))

    def normalize_value(self, c, raw):
        """外部粘进来的文字 → (能不能用, 值)。默认原样。"""
        return True, raw

    def display_value(self, c, value) -> str:
        """复制到剪贴板时的文字。"""
        return "" if value is None else str(value)

    # ── 历史 ──
    def quiet(self):
        """with table.quiet(): 里面的改动不进撤销（整表重建时用）。"""
        table = self

        class _Q:
            def __enter__(self_inner):
                table._batch += 1

            def __exit__(self_inner, *exc):
                table._batch -= 1
                return False
        return _Q()

    def reset_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._shadow = {(r, c): self.cell_value(r, c)
                        for r in range(self.rowCount()) for c in self.editable_cols}

    def _on_item_changed(self, item) -> None:
        if self._batch or item is None:
            return
        key = (item.row(), item.column())
        if key[1] not in self.editable_cols:
            return
        new = self.cell_value(*key)
        old = self._shadow.get(key, "")
        if old != new:
            self._undo.append({key: (old, new)})
            self._redo.clear()
            self._shadow[key] = new

    def apply_values(self, changes: dict, label: str = "") -> int:
        """{(r, c): 值} 一次写进去，一步撤销。返回真正变了的格数。"""
        rec = {}
        self._batch += 1
        try:
            for (r, c), v in changes.items():
                if not (0 <= r < self.rowCount()) or c not in self.editable_cols:
                    continue
                old = self.cell_value(r, c)
                if old == v:
                    continue
                self.set_cell_value(r, c, v)
                rec[(r, c)] = (old, self.cell_value(r, c))
                self._shadow[(r, c)] = self.cell_value(r, c)
        finally:
            self._batch -= 1
        if rec:
            self._undo.append(rec)
            self._redo.clear()
            self.cells_changed.emit()
        return len(rec)

    def undo(self) -> bool:
        return self._swap(self._undo, self._redo, 0)

    def redo(self) -> bool:
        return self._swap(self._redo, self._undo, 1)

    def _swap(self, src, dst, which) -> bool:
        if not src:
            return False
        rec = src.pop()
        self._batch += 1
        try:
            for (r, c), pair in rec.items():
                if 0 <= r < self.rowCount():
                    self.set_cell_value(r, c, pair[which])
                    self._shadow[(r, c)] = self.cell_value(r, c)
        finally:
            self._batch -= 1
        dst.append(rec)
        self.cells_changed.emit()
        return True

    # ── 选区 ──
    def selection_box(self):
        """(r0, r1, c0, c1)；没选 → 当前格；什么都没有 → None。"""
        ranges = self.selectedRanges()
        if ranges:
            r0 = min(x.topRow() for x in ranges)
            r1 = max(x.bottomRow() for x in ranges)
            c0 = min(x.leftColumn() for x in ranges)
            c1 = max(x.rightColumn() for x in ranges)
            return r0, r1, c0, c1
        r, c = self.currentRow(), self.currentColumn()
        if r < 0 or c < 0:
            return None
        return r, r, c, c

    def select_box(self, r0, r1, c0, c1) -> None:
        from PySide6.QtWidgets import QTableWidgetSelectionRange
        self.clearSelection()
        self.setRangeSelected(QTableWidgetSelectionRange(r0, c0, r1, c1), True)

    # ── 复制 / 粘贴 / 清空 ──
    def copy_selection(self) -> int:
        box = self.selection_box()
        if box is None:
            return 0
        r0, r1, c0, c1 = box
        values = [[self.cell_value(r, c) for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
        shown = [[self.display_value(c0 + j, v) for j, v in enumerate(row)] for row in values]
        text = to_tsv(shown)
        self._clip = (text, values, c0)
        QApplication.clipboard().setText(text)
        n = (r1 - r0 + 1) * (c1 - c0 + 1)
        self.status.emit("已复制 %d 格" % n)
        return n

    def paste_target_col(self, c) -> int:
        """从哪一列开始贴（子类可以把不能改的列改到能改的列）。"""
        return c

    def paste_grid(self, grid, *, from_inside=False) -> int:
        box = self.selection_box()
        if box is None or not grid:
            return 0
        r0, r1, c0, c1 = box
        c_start = self.paste_target_col(c0)
        changes = {}
        bad = 0
        single = len(grid) == 1 and len(grid[0]) == 1
        if single and (r1 > r0 or c1 > c0):
            # 一格 → 选中的一片都填
            raw = grid[0][0]
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    cc = self.paste_target_col(c) if c1 == c0 else c
                    if cc not in self.editable_cols:
                        continue
                    ok, v = (True, raw) if from_inside else self.normalize_value(cc, raw)
                    if ok:
                        changes[(r, cc)] = v
                    else:
                        bad += 1
        else:
            for i, row in enumerate(grid):
                r = r0 + i
                if r >= self.rowCount():
                    break
                for j, raw in enumerate(row):
                    c = c_start + j
                    if c >= self.columnCount() or c not in self.editable_cols:
                        continue
                    ok, v = (True, raw) if from_inside else self.normalize_value(c, raw)
                    if ok:
                        changes[(r, c)] = v
                    else:
                        bad += 1
        n = self.apply_values(changes, "粘贴")
        msg = "粘贴了 %d 格" % n
        if bad:
            msg += "，%d 格内容这一列用不了（已跳过）" % bad
        self.status.emit(msg)
        return n

    def paste_clipboard(self) -> int:
        text = QApplication.clipboard().text()
        if self._clip is not None and self._clip[0] == text:
            return self.paste_grid(self._clip[1], from_inside=True)
        return self.paste_grid(parse_grid(text))

    def clear_selection_values(self) -> int:
        box = self.selection_box()
        if box is None:
            return 0
        r0, r1, c0, c1 = box
        changes = {(r, c): "" for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)
                   if c in self.editable_cols}
        n = self.apply_values(changes, "清空")
        if n:
            self.status.emit("清空了 %d 格（Ctrl+Z 撤销）" % n)
        return n

    # ── 填充柄 ──
    def handle_rect(self) -> QRect | None:
        box = self.selection_box()
        if box is None:
            return None
        r0, r1, c0, c1 = box
        if not any(c in self.editable_cols for c in range(c0, c1 + 1)):
            return None
        rect = self.visualRect(self.model().index(r1, c1))
        if not rect.isValid():
            return None
        return QRect(rect.right() - HANDLE // 2, rect.bottom() - HANDLE // 2, HANDLE, HANDLE)

    def _near_handle(self, pos) -> bool:
        h = self.handle_rect()
        return h is not None and h.adjusted(-HANDLE_HIT, -HANDLE_HIT, HANDLE_HIT, HANDLE_HIT).contains(pos)

    def fill_to(self, target_row: int) -> int:
        """把选区按顺序重复填到 target_row（向下或向上）。"""
        box = self.selection_box()
        if box is None:
            return 0
        r0, r1, c0, c1 = box
        target_row = max(0, min(self.rowCount() - 1, int(target_row)))
        h = r1 - r0 + 1
        cols = [c for c in range(c0, c1 + 1) if c in self.editable_cols]
        changes = {}
        if target_row > r1:
            rows = range(r1 + 1, target_row + 1)
            for t in rows:
                src = r0 + (t - r1 - 1) % h
                for c in cols:
                    changes[(t, c)] = self.cell_value(src, c)
            new_box = (r0, target_row, c0, c1)
        elif target_row < r0:
            rows = range(target_row, r0)
            for t in rows:
                src = r1 - ((r0 - 1 - t) % h)
                for c in cols:
                    changes[(t, c)] = self.cell_value(src, c)
            new_box = (target_row, r1, c0, c1)
        else:
            return 0
        n = self.apply_values(changes, "填充")
        self.select_box(*new_box)
        self.status.emit("填充了 %d 格（Ctrl+Z 撤销）" % n)
        return n

    def fill_down_to_end(self) -> int:
        return self.fill_to(self.rowCount() - 1)

    # ── 事件 ──
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._near_handle(ev.position().toPoint()):
            box = self.selection_box()
            self._fill = {"box": box, "to": box[1]}
            self.viewport().update()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        if self._fill is not None:
            r = self.rowAt(pos.y())
            if r < 0:
                r = self.rowCount() - 1 if pos.y() > 0 else 0
            self._fill["to"] = r
            h = self.viewport().height()
            self._scroll_dir = 1 if pos.y() > h - 12 else (-1 if pos.y() < 12 else 0)
            if self._scroll_dir and not self._scroll_timer.isActive():
                self._scroll_timer.start()
            self.viewport().update()
            ev.accept()
            return
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if self._near_handle(pos)
                                  else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._fill is not None:
            fill = self._fill
            self._fill = None
            self._scroll_timer.stop()
            r0, r1, _c0, _c1 = fill["box"]
            to = fill["to"]
            if to > r1 or to < r0:
                self.fill_to(to)
            self.viewport().update()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._near_handle(ev.position().toPoint()):
            self.fill_down_to_end()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def _auto_scroll(self):
        if self._fill is None or not self._scroll_dir:
            self._scroll_timer.stop()
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self._scroll_dir * max(1, bar.singleStep()))
        if self._scroll_dir > 0:
            self._fill["to"] = min(self.rowCount() - 1, self._fill["to"] + 1)
        else:
            self._fill["to"] = max(0, self._fill["to"] - 1)
        self.viewport().update()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_clipboard()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            if not self.undo():
                self.status.emit("没有可以撤销的了")
            return
        if event.matches(QKeySequence.StandardKey.Redo) or (
                event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if not self.redo():
                self.status.emit("没有可以重做的了")
            return
        if event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            box = self.selection_box()     # Ctrl+D：用选区第一行往下填（Sheets / Excel 同款）
            if box is not None and box[1] > box[0]:
                r0, r1, c0, c1 = box
                self.select_box(r0, r0, c0, c1)
                self.fill_to(r1)
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.state() != \
                QAbstractItemView.State.EditingState:
            self.clear_selection_values()
            return
        super().keyPressEvent(event)

    def event(self, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.ShortcutOverride:
            seq_hit = any(ev.matches(k) for k in (
                QKeySequence.StandardKey.Copy, QKeySequence.StandardKey.Paste,
                QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo))
            if seq_hit or ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) or (
                    ev.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and ev.key() in (Qt.Key.Key_Y, Qt.Key.Key_D)):
                ev.accept()
                return True
        return super().event(ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        box = self.selection_box() if self.selectedRanges() else None
        if box is None:
            return
        r0, r1, c0, c1 = box
        tl = self.visualRect(self.model().index(r0, c0))
        br = self.visualRect(self.model().index(r1, c1))
        if not tl.isValid() or not br.isValid():
            return
        tok = current()
        p = QPainter(self.viewport())
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            outer = QRect(tl.topLeft(), br.bottomRight())
            p.setPen(QPen(QColor(tok.accent), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(outer.adjusted(0, 0, -1, -1))
            if self._fill is not None:
                to = self._fill["to"]
                if to > r1:
                    a = self.visualRect(self.model().index(r1 + 1, c0))
                    b = self.visualRect(self.model().index(to, c1))
                elif to < r0:
                    a = self.visualRect(self.model().index(to, c0))
                    b = self.visualRect(self.model().index(r0 - 1, c1))
                else:
                    a = b = None
                if a is not None and a.isValid() and b.isValid():
                    pen = QPen(QColor(tok.accent), 1, Qt.PenStyle.DashLine)
                    p.setPen(pen)
                    p.drawRect(QRect(a.topLeft(), b.bottomRight()).adjusted(0, 0, -1, -1))
            h = self.handle_rect()
            if h is not None:
                p.setPen(QPen(QColor(tok.bg0), 1))
                p.setBrush(QColor(tok.accent))
                p.drawRect(h)
        finally:
            p.end()

    def visual_point_of(self, r, c, where="center") -> QPoint:
        rect = self.visualRect(self.model().index(r, c))
        return rect.center() if where == "center" else rect.bottomRight()
