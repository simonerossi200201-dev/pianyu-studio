# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q120 · 批量加载 → 文本分页。

一行一条视频，中间那格就是要加的文本。从 Google Sheets / Excel 里
Ctrl+C 一整列，点中某一格 Ctrl+V，从这一格往下顺着填 —— 可以分几次粘，
也可以一次全粘进去。

时长有两种来法：
- 自动定位：拿这格文字去这条视频的字幕里找，首句开头到末句结尾就是这条
  文本的时间。没跑过字幕的可以现场跑一遍**只要时间戳**的识别（不产生字幕、
  不写进 segments）。
- 手填：起点 + 时长两栏自己写。

真正的排期和落地逻辑在 cutora/batch/text_batch.py，这里只管界面。

Q121：点一行，右栏预览换成这一行的画面和文字。

Q136：这一页不再有自己的字号 / 颜色 / 描边。模板就是成品 —— 位置、框的
宽高（= 折行宽度）、字号、颜色、背景、缩放、旋转全部照模板加进去，和右侧
属性面板「存为文本层预设」时看到的一模一样。要改，就选中加进去的那条文本
在属性面板里改（变换那一栏就是位置 / 旋转 / 缩放），改好了再存一次预设。
没跑过字幕的，可以只转写用户圈定的开头几秒（默认 0–15 秒，最长 30 秒）
来定位标题。整段 3、4 分钟不会送进识别。
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cutora.batch.text_batch import DEFAULT_DURATION
from cutora.core.log import get_logger
from cutora.ui.sheet_table import SheetTable
from cutora.ui.theme.tokens import (
    RADIUS_CARD,
    RADIUS_SM,
    INSPECTOR_PAIR_GAP,
    INSPECTOR_ROW_GAP,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_6,
)

_LOG = get_logger("cutora.ui.batch_text_tab")

# 状态紧跟在文本后面：参数栏窄的时候它也得看得见（最右边会被滚出去）
# Q153：「套用模板」一列 —— 每一行可以单独指定一个文本层预设；空 = 跟随上方的模板
# Q190：「视频原话」一列 —— 识别到的那几句（带标点）；点格子右边的「用原话」把它填进文本内容
COL_NAME, COL_TEXT, COL_STATE, COL_SAID, COL_TPL, COL_START, COL_DUR = range(7)
_HEADERS = ("视频", "文本内容", "状态", "视频原话", "套用模板", "起点", "时长")
FOLLOW_LABEL = "↑ 跟随上方"
USE_SAID_LABEL = "用原话"
ROLE_ORIG = Qt.ItemDataRole.UserRole + 1      # 文本格：被原话替换前的标题
ROLE_PICK = Qt.ItemDataRole.UserRole + 2      # 状态格：用户在原话里划选的 (start, end)
ROLE_REASON = Qt.ItemDataRole.UserRole + 3    # 状态格：matched / partial / mismatch / …
AUTO_LOCATE_DELAY_MS = 600

# Q153：模板样式里要丢掉的键挪到 cutora/batch/text_batch.STYLE_DROP（批量落地和预览共用）

class TemplateDelegate(QStyledItemDelegate):
    """「套用模板」这一格：画一个 ▾，点 ▾ 或双击弹出预设下拉，选中即生效。"""

    def __init__(self, table):
        super().__init__(table)
        self.table = table

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        from PySide6.QtGui import QColor
        from cutora.ui.theme.tokens import current
        tok = current()
        r = option.rect
        painter.save()
        try:
            painter.setPen(QColor(tok.fg2))
            painter.drawText(r.adjusted(0, 0, -SPACE_1, 0),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "▾")
        finally:
            painter.restore()

    def createEditor(self, parent, option, index):
        from cutora.batch.text_batch import preset_names
        combo = QComboBox(parent)
        combo.addItem(FOLLOW_LABEL, "")
        for name in preset_names():
            combo.addItem(name, name)
        combo.setMaxVisibleItems(20)
        combo.activated.connect(lambda _i, c=combo: self._commit(c))
        QTimer.singleShot(0, combo.showPopup)
        return combo

    def _commit(self, combo):
        self.commitData.emit(combo)
        self.closeEditor.emit(combo, QStyledItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor, index):
        cur = str(index.data(Qt.ItemDataRole.UserRole) or "")
        i = editor.findData(cur)
        editor.setCurrentIndex(i if i >= 0 else 0)

    def setModelData(self, editor, model, index):
        val = str(editor.currentData() or "")
        self.table.apply_values({(index.row(), index.column()): val}, "套用模板")

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and event.position().x() >= option.rect.right() - SPACE_6):
            self.table.edit(index)
            return True
        return super().editorEvent(event, model, option, index)


class SaidDelegate(QStyledItemDelegate):
    """「视频原话」这一格：右边画一个「用原话」小按钮，点它把原话填进「文本内容」。"""

    def __init__(self, table, on_use):
        super().__init__(table)
        self.table = table
        self.on_use = on_use

    def _btn_w(self):
        return SPACE_6 * 2 + SPACE_1

    def paint(self, painter, option, index):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QStyle
        from cutora.ui.theme.tokens import current
        from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""                                  # 背景 / 选中框照常画，字我们自己画（留出按钮位置）
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        tok = current()
        r = option.rect
        bw = self._btn_w() if text else 0
        painter.save()
        try:
            fm = painter.fontMetrics()
            body = r.adjusted(SPACE_1, 0, -(bw + SPACE_1), 0)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.setPen(QColor(tok.fg0 if selected else tok.fg1))
            painter.drawText(body, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(text, Qt.TextElideMode.ElideRight, body.width()))
            if text:
                pill = r.adjusted(r.width() - bw, SPACE_1, -SPACE_1, -SPACE_1)
                painter.setPen(Qt.PenStyle.NoPen)
                c = QColor(tok.accent)
                c.setAlpha(60)
                painter.setBrush(c)
                painter.drawRoundedRect(pill, RADIUS_SM, RADIUS_SM)
                painter.setPen(QColor(tok.fg0))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, USE_SAID_LABEL)
        finally:
            painter.restore()

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if (text and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and event.position().x() >= option.rect.right() - self._btn_w()):
            if callable(self.on_use):
                self.on_use(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class TranscriptStrip(QWidget):
    """Q190 · 点词定位：一行原话按词排开，按住鼠标拖过几个词 = 标题就是这几个词。

    按住拖过的词就是这一条的标题。选中的词高亮，``picked(start, end)`` 发出去；
    点「清除」回到自动定位。只画当前那一行的词（几十个），不占资源。
    """

    def __init__(self, parent=None):
        from PySide6.QtCore import Signal  # noqa: F401  (类属性在下面)
        super().__init__(parent)
        self.setObjectName("transcriptStrip")
        self.setMouseTracking(False)
        self.words: list[dict] = []
        self.sel: tuple[int, int] | None = None
        self.auto: tuple[int, int] | None = None
        self._rects: list = []
        self._drag_from = None
        self.playing_at = None       # 试听时当前的秒数：那个词描一圈
        self.on_pick = None          # callable(start, end) / callable(None) 清除
        self.setMinimumHeight(SPACE_6 + SPACE_2)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_words(self, words, auto_span=None, picked=None) -> None:
        self.words = [w for w in (words or []) if str(w.get("text") or "").strip()]
        self.auto = self._span_to_idx(auto_span)
        self.sel = self._span_to_idx(picked)
        self._layout()
        self.update()

    def _span_to_idx(self, span):
        if not span or not self.words:
            return None
        try:
            a, b = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            return None
        idx = [k for k, w in enumerate(self.words)
               if float(w["end"]) > a + 0.01 and float(w["start"]) < b - 0.01]
        return (idx[0], idx[-1]) if idx else None

    def _layout(self):
        from PySide6.QtCore import QRectF
        fm = self.fontMetrics()
        x = y = float(SPACE_1)
        h = float(fm.height() + SPACE_1)
        self._rects = []
        width = max(SPACE_6 * 4, self.width() - SPACE_2)
        for w in self.words:
            tw = fm.horizontalAdvance(str(w["text"])) + SPACE_2
            if x + tw > width and x > SPACE_1:
                x = float(SPACE_1)
                y += h + 2
            self._rects.append(QRectF(x, y, tw, h))
            x += tw + 2
        total = int(y + h + SPACE_1) if self._rects else SPACE_6
        if self.height() != total:
            self.setFixedHeight(min(total, SPACE_6 * 4))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout()

    def _hit(self, pos):
        for k, r in enumerate(self._rects):
            if r.adjusted(-1, -1, 1, 1).contains(pos):
                return k
        best, dist = None, None
        for k, r in enumerate(self._rects):
            if r.top() - 2 <= pos.y() <= r.bottom() + 2:
                d = min(abs(pos.x() - r.left()), abs(pos.x() - r.right()))
                if dist is None or d < dist:
                    best, dist = k, d
        return best

    def mousePressEvent(self, ev):
        k = self._hit(ev.position())
        if k is None:
            return
        self._drag_from = k
        self.sel = (k, k)
        self.update()

    def mouseMoveEvent(self, ev):
        if self._drag_from is None:
            return
        k = self._hit(ev.position())
        if k is None:
            return
        a, b = sorted((self._drag_from, k))
        self.sel = (a, b)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag_from is None:
            return
        self._drag_from = None
        if self.sel and callable(self.on_pick):
            a, b = self.sel
            self.on_pick(float(self.words[a]["start"]), float(self.words[b]["end"]))

    def clear_pick(self):
        self.sel = None
        self.update()
        if callable(self.on_pick):
            self.on_pick(None, None)

    def paintEvent(self, ev):
        from PySide6.QtGui import QColor, QPainter
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            p.setPen(Qt.PenStyle.NoPen)
            bg = QColor(tok.bg2)
            p.setBrush(bg)
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), RADIUS_CARD, RADIUS_CARD)
            span = self.sel or self.auto
            for k, (w, r) in enumerate(zip(self.words, self._rects)):
                inside = span is not None and span[0] <= k <= span[1]
                if inside:
                    c = QColor(tok.accent)
                    c.setAlpha(150 if self.sel else 70)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(c)
                    p.drawRoundedRect(r, RADIUS_SM, RADIUS_SM)
                t = self.playing_at
                if t is not None and float(w["start"]) <= t < float(w["end"]) + 0.08:
                    from PySide6.QtGui import QPen
                    p.setPen(QPen(QColor(tok.fg0), 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS_SM, RADIUS_SM)
                p.setPen(QColor(tok.fg0 if inside else tok.fg2))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(w["text"]))
        finally:
            p.end()


class TextPasteTable(SheetTable):
    """Q120 起：一行一条视频。Q153 起当电子表格用：复制粘贴任意格、拖右下角填充柄批量填、
    「套用模板」一格一个下拉、Ctrl+Z 撤销。一个单元格就是一条文本，里面有换行也还是一条。"""

    def __init__(self, parent=None):
        super().__init__(0, len(_HEADERS), parent)
        self.setObjectName("batchTextTable")
        self.setHorizontalHeaderLabels(list(_HEADERS))
        tip = self.horizontalHeaderItem(COL_TPL)
        if tip is not None:
            tip.setToolTip("每一行单独套用哪个文本预设。空着（↑ 跟随上方）= 用上面「模板」选的那个。\n"
                           "点格子右边的 ▾ 选；选好一格后 Ctrl+C，框选别的格 Ctrl+V 批量贴；\n"
                           "或者拖选区右下角的小方块往下拉，像 Google 表格那样一路填下去。")
        self.editable_cols = {COL_TEXT, COL_TPL, COL_START, COL_DUR}
        self.verticalHeader().setVisible(False)
        # Q191：一行微亮一行微暗、行高留白、去掉网格竖线的密集感（圆角边框在主题 QSS 里）
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setDefaultSectionSize(SPACE_6 + SPACE_1)
        self.verticalHeader().setMinimumSectionSize(SPACE_6)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.setWordWrap(False)
        self.setItemDelegateForColumn(COL_TPL, TemplateDelegate(self))
        self.use_said = None                         # build_text_tab 装：row -> None
        self.setItemDelegateForColumn(COL_SAID, SaidDelegate(self, lambda r: self.use_said(r) if callable(self.use_said) else None))
        head = self.horizontalHeader()
        head.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        head.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        for c in (COL_START, COL_DUR, COL_STATE, COL_TPL, COL_SAID):
            head.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        head.setMinimumSectionSize(SPACE_6 * 2)
        for c, w in ((COL_NAME, SPACE_6 * 3 + SPACE_3), (COL_STATE, SPACE_6 * 6), (COL_SAID, SPACE_6 * 6),
                     (COL_TPL, SPACE_6 * 3 + SPACE_3), (COL_START, SPACE_6 * 2), (COL_DUR, SPACE_6 * 2)):
            self.setColumnWidth(c, w)
        # Q123：栏不宽的时候让表格自己横向滚，不许把整个对话框撑破
        self.setMinimumWidth(SPACE_6 * 6)
        self.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSizeAdjustPolicy(
            QAbstractItemView.SizeAdjustPolicy.AdjustIgnored)

    # ── 每列的值 ──
    def cell_value(self, r, c):
        it = self.item(r, c)
        if it is None:
            return ""
        if c == COL_TPL:
            return str(it.data(Qt.ItemDataRole.UserRole) or "")
        return it.text()

    def set_cell_value(self, r, c, value) -> None:
        it = self.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.setItem(r, c, it)
        text = "" if value is None else str(value)
        if c == COL_TPL:
            it.setData(Qt.ItemDataRole.UserRole, text)
            _paint_template_item(it, text)
            return
        it.setText(text)
        if c == COL_TEXT:
            # 一行放不下就截断显示，鼠标停上去看全文
            it.setToolTip(text)

    def normalize_value(self, c, raw):
        raw = "" if raw is None else str(raw)
        if c == COL_TPL:
            from cutora.batch.text_batch import match_preset_name
            name = match_preset_name(raw)
            return (name is not None), (name or "")
        if c in (COL_START, COL_DUR):
            txt = raw.strip()
            if not txt:
                return True, ""
            try:
                v = float(txt.replace("s", "").replace("秒", ""))
            except ValueError:
                return False, ""
            return True, ("%g" % v)
        return True, raw.strip("\n")

    def display_value(self, c, value) -> str:
        return "" if value is None else str(value)

    def paste_target_col(self, c) -> int:
        return COL_TEXT if c in (COL_NAME, COL_STATE, COL_SAID) else c

    def paste_grid(self, grid, *, from_inside=False) -> int:
        """外部表格多列时，按「能改的列」依次对上：文本 → 套用模板 → 起点 → 时长。"""
        box = self.selection_box()
        if box is None or not grid:
            return 0
        r0, r1, c0, c1 = box
        single = len(grid) == 1 and len(grid[0]) == 1
        if single or from_inside:
            return super().paste_grid(grid, from_inside=from_inside)
        start = self.paste_target_col(c0)
        order = [c for c in (COL_TEXT, COL_TPL, COL_START, COL_DUR) if c >= start] or [COL_TEXT]
        changes, bad = {}, 0
        for i, row in enumerate(grid):
            r = r0 + i
            if r >= self.rowCount():
                break
            for j, raw in enumerate(row[:len(order)]):
                c = order[j]
                ok, v = self.normalize_value(c, raw)
                if ok:
                    changes[(r, c)] = v
                else:
                    bad += 1
        n = self.apply_values(changes, "粘贴")
        msg = "粘贴了 %d 格" % n
        if bad:
            msg += "，%d 格对不上（模板名不存在 / 不是数字），已跳过" % bad
        self.status.emit(msg)
        return n

    # ── 老接口（Q120）──
    def paste_from_clipboard(self) -> int:
        return self.paste_clipboard()

    def fill_from(self, row: int, cells) -> int:
        """从第 row 行开始往下填文本，填到表格末尾为止。返回填了几格。"""
        changes = {}
        n = 0
        for i, cell in enumerate(cells or []):
            r = row + i
            if r >= self.rowCount():
                break
            changes[(r, COL_TEXT)] = str(cell or "")
            n += 1
        self.apply_values(changes, "粘贴")
        return n


def _paint_template_item(item, name: str) -> None:
    from PySide6.QtGui import QColor, QFont
    from cutora.ui.theme.tokens import current
    tok = current()
    f = QFont(item.font())
    if name:
        item.setText(name)
        f.setItalic(False)
        item.setForeground(QColor(tok.fg0))
        item.setToolTip("这一行套用「%s」。点 ▾ 换，空着就跟随上方的模板" % name)
    else:
        item.setText(FOLLOW_LABEL)
        f.setItalic(True)
        item.setForeground(QColor(tok.fg2))
        item.setToolTip("跟随上方的模板。点 ▾ 给这一行单独选一个")
    item.setFont(f)


class TableZoomDialog(QDialog):
    """「放大」：把同一张表搬进一个大窗口里批量操作，关掉再搬回去（数据、选择、撤销都还在）。"""

    def __init__(self, table, home_layout, home_index, parent=None, apply_template=None):
        super().__init__(parent)
        self.setWindowTitle("批量加载 · 文本表格")
        self.setObjectName("batchTextZoom")
        self.table = table
        self._home = (home_layout, home_index)
        self._apply_template = apply_template
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_3, SPACE_3, SPACE_3, SPACE_3)
        lay.setSpacing(SPACE_2)
        bar = QHBoxLayout()
        bar.setSpacing(SPACE_2)
        hint = QLabel(
            "点 ▾ 选模板 · Ctrl+C / Ctrl+V 复制粘贴（一格贴到一片也行）· "
            "拖选区右下角的小方块往下拉 = 批量填 · 双击小方块 = 填到底 · Ctrl+D 向下填 · Ctrl+Z 撤销")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        bar.addWidget(hint, 1)
        self.cb_tpl = QComboBox()
        self.cb_tpl.setMinimumWidth(SPACE_6 * 6)
        self.btn_tpl = QPushButton("套用到选中行")
        self.btn_tpl.setToolTip("把左边选的模板填进选中的那些行的「套用模板」格")
        self.btn_tpl.clicked.connect(self._apply_to_selection)
        bar.addWidget(QLabel("模板"))
        bar.addWidget(self.cb_tpl)
        bar.addWidget(self.btn_tpl)
        btn_close = QPushButton("收起")
        btn_close.setToolTip("放回原来的位置（Esc）")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        lay.addLayout(bar)
        self._fill_templates()
        lay.addWidget(table, 1)
        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        lay.addWidget(self.msg)
        table.status.connect(self.msg.setText)
        table.show()
        screen = (parent.screen() if parent is not None else None) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else None
        if geo is not None:
            self.resize(int(geo.width() * 0.82), int(geo.height() * 0.8))
        table.setColumnWidth(COL_NAME, SPACE_6 * 8)
        table.setColumnWidth(COL_STATE, SPACE_6 * 10)
        table.setColumnWidth(COL_TPL, SPACE_6 * 8)

    def _fill_templates(self):
        from cutora.batch.text_batch import preset_names
        self.cb_tpl.clear()
        self.cb_tpl.addItem(FOLLOW_LABEL, "")
        for name in preset_names():
            self.cb_tpl.addItem(name, name)

    def _apply_to_selection(self):
        box = self.table.selection_box()
        if box is None:
            self.msg.setText("先在表格里选中要套用的行")
            return
        r0, r1, _c0, _c1 = box
        val = str(self.cb_tpl.currentData() or "")
        n = self.table.apply_values({(r, COL_TPL): val for r in range(r0, r1 + 1)}, "套用模板")
        self.msg.setText("%d 行套用了「%s」" % (n, val or "跟随上方"))

    def done(self, code):
        lay, idx = self._home
        try:
            self.table.status.disconnect(self.msg.setText)
        except Exception:
            pass
        lay.insertWidget(idx, self.table, 1)
        self.table.setColumnWidth(COL_NAME, SPACE_6 * 4)
        self.table.setColumnWidth(COL_STATE, SPACE_6 * 7)
        self.table.setColumnWidth(COL_TPL, SPACE_6 * 5)
        self.table.show()
        super().done(code)


def build_text_tab(dlg, host, picker, preset_combo=None):
    """返回 (页面 widget, 刷新函数, 应用函数)。

    host  : MainWindow，用来拿 tasks 和刷新界面
    picker: 左边那个勾选列表，表格跟着它走
    """
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, SPACE_2, 0, 0)
    lay.setSpacing(INSPECTOR_ROW_GAP)

    # Q123：工具栏分两行。参数栏本来就不宽，一行塞五个控件右边会被切出画面外。
    bar = QHBoxLayout()
    bar.setSpacing(INSPECTOR_PAIR_GAP)
    cb_preset = QComboBox()
    cb_preset.setFixedWidth(SPACE_6 * 5 + SPACE_2)
    lbl_preset = QLabel("模板")
    lbl_preset.setFixedWidth(SPACE_6 * 2)   # 别被挤没：上一版这两个字看不见
    bar.addWidget(lbl_preset)
    bar.addWidget(cb_preset)
    ck_auto = QCheckBox("自动定位到字幕时间")
    ck_auto.setChecked(True)
    ck_auto.setToolTip("拿这格文字去字幕里找：首句开头到末句结尾就是这条文本的时长")
    bar.addWidget(ck_auto)

    # Q136：模板即成品，没有第二行样式控件。
    # Q191：说明文字收进悬停提示，表格多留出高度
    cb_preset.setToolTip(
        "模板里存的位置、框宽（折行）、字号、颜色、背景、缩放会原样加进去。\n"
        "要改样式：选中加进去的文本，在右侧属性面板改好后「存为文本层预设」。")
    bar2 = bar
    sp_dur = QDoubleSpinBox()
    sp_dur.setRange(0.2, 600.0)
    sp_dur.setDecimals(1)
    sp_dur.setSingleStep(0.5)
    sp_dur.setPrefix("默认时长  ")
    sp_dur.setSuffix(" s")
    sp_dur.setFixedWidth(SPACE_6 * 5)
    sp_dur.setValue(DEFAULT_DURATION)
    sp_dur.setToolTip("没定位到时用这个时长，照样加进去并在状态里标出来")
    bar2.addWidget(sp_dur)
    bar2.addStretch(1)
    btn_paste = QPushButton("粘贴")      # Q193：粘贴 / 清空 / 放大挪到第三行（1366 宽的小屏一行放不下）
    btn_paste.setToolTip("等同在表格里按 Ctrl+V：从选中那一格往下填")
    btn_clear = QPushButton("清空")
    btn_zoom = QPushButton("⤢ 放大")
    btn_zoom.setToolTip("在大窗口里编辑这张表：批量选模板、复制粘贴、拖右下角小方块批量填")
    lay.addLayout(bar)

    bar3 = QHBoxLayout()
    bar3.setSpacing(INSPECTOR_PAIR_GAP)
    btn_asr = QPushButton("识别开头时间戳")
    btn_asr.setToolTip(
        "粘上标题会自动识别并定位：数字 / 序数 / 缩写 / 标点 / 大小写都不影响；对不上的会写明原因。\n"
        "只转写右边圈定的这一段（或整条视频），一条接一条跑，转完一条就记在本机；\n"
        "显存不够会停下，已经记下的不会丢。软件卡了存盘重开，时间戳还在。")
    sp_win0 = QDoubleSpinBox()
    sp_win0.setRange(0.0, 30.0)
    sp_win0.setDecimals(1)
    sp_win0.setSingleStep(1.0)
    sp_win0.setPrefix("从 ")
    sp_win0.setSuffix(" 秒")
    sp_win0.setFixedWidth(SPACE_6 * 4)
    sp_win0.setValue(0.0)
    sp_win1 = QDoubleSpinBox()
    sp_win1.setRange(0.5, 30.0)
    sp_win1.setDecimals(1)
    sp_win1.setSingleStep(1.0)
    sp_win1.setPrefix("到 ")
    sp_win1.setSuffix(" 秒")
    sp_win1.setFixedWidth(SPACE_6 * 4)
    sp_win1.setValue(15.0)
    ck_force = QCheckBox("重新识别")
    ck_force.setToolTip("同一段已经记过时间戳时，勾上才会再跑一遍。默认直接用记下的。")
    ck_full = QCheckBox("整条视频")
    ck_full.setToolTip("识别整条视频（30 秒一段串行跑，显存和短窗口一样）。\n"
                       "默认只识别开头 0–15 秒；标题说到一半识别范围到头时，软件会自动对那一条整条识别。")
    bar3.addWidget(btn_asr)
    bar3.addWidget(sp_win0)
    bar3.addWidget(sp_win1)
    bar3.addWidget(ck_full)
    bar3.addWidget(ck_force)
    bar3.addStretch(1)
    lay.addLayout(bar3)

    # Q190：原话工具栏 —— 只看有问题的行、用原话替换标题（单条在「视频原话」格里点，这里是批量）
    bar4 = QHBoxLayout()
    bar4.setSpacing(INSPECTOR_PAIR_GAP)
    btn_filter = QPushButton("只看有问题的")
    btn_filter.setCheckable(True)
    btn_filter.setToolTip("只显示没定位到 / 内容不符 / 基本吻合但有词不同的行。\n"
                          "↑ ↓ 逐行看，右边预览跟着跳；按空格试听这一行标题的那一段。")
    btn_said_bad = QPushButton("只替换不符的")
    btn_said_bad.setToolTip("「视频内容与标题文字不符」的行：把识别到的原话填进文本内容（句数、大小写跟原标题）。Ctrl+Z 可撤销。")
    btn_said_all = QPushButton("全部用原话")
    btn_said_all.setToolTip("所有识别到原话的行都换成原话。Ctrl+Z 可撤销。")
    lbl_said = QLabel("")
    lbl_said.setObjectName("hint")
    lbl_said.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    bar4.addWidget(btn_filter)
    bar4.addWidget(btn_paste)
    bar4.addWidget(btn_clear)
    bar4.addWidget(btn_zoom)
    bar4.addStretch(1)
    bar4.addWidget(lbl_said, 1)
    bar4.addWidget(btn_said_bad)
    bar4.addWidget(btn_said_all)
    lay.addLayout(bar4)

    # 识别进度：第几条 / 一共几条、现在在转哪条、失败的原因。以前只有按钮上的
    # 「剩余 N」，出错写在按钮的悬停提示里，根本看不出到底识别了没有。
    asr_prog = QProgressBar()
    asr_prog.setObjectName("locateProgress")
    asr_prog.setTextVisible(True)
    asr_prog.setFormat("%v / %m")
    asr_prog.hide()
    lay.addWidget(asr_prog)
    asr_msg = QLabel("")
    asr_msg.setObjectName("hint")
    asr_msg.setWordWrap(True)
    asr_msg.hide()
    lay.addWidget(asr_msg)

    table = TextPasteTable()
    lay.addWidget(table, 1)
    table_home_index = lay.indexOf(table)

    # Q190：点词定位 —— 当前行识别到的原话，按住拖选几个词，起止就是那几个词的时间
    strip_box = QWidget()
    strip_box.setObjectName("transcriptBox")
    sb = QVBoxLayout(strip_box)
    sb.setContentsMargins(0, 0, 0, 0)
    sb.setSpacing(SPACE_1)
    sb_bar = QHBoxLayout()
    sb_bar.setSpacing(INSPECTOR_PAIR_GAP)
    lbl_strip = QLabel("")
    lbl_strip.setObjectName("hint")
    btn_play = QPushButton("▶ 试听这一段")
    btn_play.setToolTip("只播这一行标题的那一段声音，词会跟着亮（快捷键：空格）")
    btn_unpick = QPushButton("清除手选")
    btn_unpick.setToolTip("回到自动定位")
    sb_bar.addWidget(lbl_strip, 1)
    sb_bar.addWidget(btn_play)
    sb_bar.addWidget(btn_unpick)
    sb.addLayout(sb_bar)
    strip = TranscriptStrip()
    sb.addWidget(strip)
    strip_box.hide()
    lay.addWidget(strip_box)

    tip = QLabel("")
    tip.setObjectName("hint")
    tip.setWordWrap(False)
    tip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lay.addWidget(tip)
    table.setToolTip(
        "从表格里复制一整列 → 点中要开始的那一格 → Ctrl+V 顺着往下填。\n"
        "「套用模板」点 ▾ 选；选好一格 Ctrl+C，框选别的格 Ctrl+V，或拖右下角小方块往下拉批量填。\n"
        "↑ ↓ 换行，右边预览跟着跳；空格试听这一行标题那一段。")

    state = {"tasks": [], "segs": {}, "pending": 0,
             "locate": {}, "total": 0, "ok": 0, "fail": [],
             "extend_done": set(), "extend_wait": set(), "auto_seen": set(),
             "upgrade_seen": set()}

    def _task_name(task):
        import os
        name = str((task or {}).get("name") or "").strip()
        if name:
            return name
        path = (task or {}).get("video") or (task or {}).get("path") or ""
        return os.path.basename(str(path).replace("\\", "/")) or "（未命名）"

    def refresh():
        """跟着左边的勾选重建行。已经填过的文字按视频记着，不会丢。"""
        page._refreshing = True
        table._batch += 1
        try:
            keep = {}
            keep_tpl = {}
            for r in range(table.rowCount()):
                it = table.item(r, COL_TEXT)
                key = table.item(r, COL_NAME)
                if it is not None and key is not None:
                    keep[key.data(Qt.ItemDataRole.UserRole)] = it.text()
                    keep_tpl[key.data(Qt.ItemDataRole.UserRole)] = table.cell_value(r, COL_TPL)
            tasks = picker.selected_tasks() if picker is not None else []
            state["tasks"] = list(tasks)
            table.setRowCount(len(tasks))
            for r, task in enumerate(tasks):
                key = id(task)
                name = QTableWidgetItem(_task_name(task))
                name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                name.setData(Qt.ItemDataRole.UserRole, key)
                table.setItem(r, COL_NAME, name)
                txt = QTableWidgetItem(keep.get(key, ""))
                table.setItem(r, COL_TEXT, txt)
                table.set_cell_value(r, COL_TPL, keep_tpl.get(key, ""))
                for col, val in ((COL_START, ""), (COL_DUR, "")):
                    cell = table.item(r, col)
                    if cell is None:
                        table.setItem(r, col, QTableWidgetItem(val))
                st = table.item(r, COL_STATE)
                if st is None:
                    st = QTableWidgetItem("")
                    st.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    table.setItem(r, COL_STATE, st)
                if table.item(r, COL_SAID) is None:
                    _set_said(r, "")
            table.resizeRowsToContents()
        finally:
            table._batch -= 1
            page._refreshing = False
        table.reset_history()
        preview_plans()
        row = getattr(page, "_preview_row", None)
        if isinstance(row, int) and row >= table.rowCount():
            page._preview_row = None
        elif isinstance(row, int) and row >= 0:
            _push_preview()

    def _rows():
        out = []
        tasks = list(getattr(host, "tasks", None) or [])
        for r in range(table.rowCount()):
            if r >= len(state["tasks"]):
                break
            task = state["tasks"][r]
            try:
                idx = tasks.index(task)
            except ValueError:
                idx = -1
            def _num(col):
                cell = table.item(r, col)
                raw = (cell.text() if cell is not None else "").strip()
                if not raw:
                    return None
                try:
                    return float(raw)
                except ValueError:
                    return None
            txt = table.item(r, COL_TEXT)
            out.append({
                "task_idx": idx,
                "text": txt.text() if txt is not None else "",
                "start": _num(COL_START),
                "dur": _num(COL_DUR),
                "tpl": table.cell_value(r, COL_TPL),
                "pick": _pick_of(r),
            })
        return out

    def _pick_of(r):
        st = table.item(r, COL_STATE)
        v = st.data(ROLE_PICK) if st is not None else None
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return [float(v[0]), float(v[1])]
        return None

    def _task_path(task):
        return str((task or {}).get("video") or (task or {}).get("path") or "")

    def _win_end():
        from cutora.batch.text_locate_asr import FULL_VIDEO
        return FULL_VIDEO if ck_full.isChecked() else float(sp_win1.value())

    def _located(task):
        """(词列表, 带标点的原句, 来源) —— 工程字幕 > 整条识别 > 当前窗口 > 任意窗口 > 本次内存。"""
        segs = (task or {}).get("segments") or []
        if segs:
            return segs, [], "subs"
        path = _task_path(task)
        if not path:
            return [], [], ""
        try:
            from cutora.batch.text_locate_asr import lookup_best
            words, sents, label = lookup_best(path, float(sp_win0.value()), _win_end())
            if words is not None:
                return words, sents or [], label
        except Exception:
            _LOG.exception("读取已保存的时间戳失败")
        loc = (task or {}).get("_title_locate") or {}
        if isinstance(loc, dict) and loc.get("segments"):
            return list(loc["segments"]), [], "project"
        return state["segs"].get(path) or [], [], "mem"

    def _segments_of(task):
        return _located(task)[0]

    _sent_cache = {}

    def _sentences_of(task):
        """带时间的原句（有标点就按标点切，没有就按停顿切）。按 (路径, 词数) 缓存。"""
        words, sents, label = _located(task)
        if not words:
            return []
        key = (_task_path(task), label, len(words), len(sents))
        got = _sent_cache.get(key)
        if got is None:
            from cutora.batch.title_align import sentences_with_times
            try:
                got = sentences_with_times(words, sents)
            except Exception:
                _LOG.exception("切原句失败")
                got = []
            _sent_cache.clear()      # 只留最近一批，别无限长
            _sent_cache[key] = got
        return got

    def _set_state(row, text, warn=False, reason="", bad=False):
        from PySide6.QtGui import QBrush, QColor
        from cutora.ui.theme.tokens import current
        cell = table.item(row, COL_STATE)
        if cell is None:
            return
        cell.setText(text)
        cell.setData(ROLE_REASON, reason)
        t = current()
        if bad:
            color = getattr(t, "err_text", None) or "#C2736B"
        elif warn:
            color = getattr(t, "accent", "#E0A030")
        else:
            color = getattr(t, "fg2", "#999999")
        cell.setForeground(QBrush(QColor(color)))

    def _set_said(row, text, tip=""):
        cell = table.item(row, COL_SAID)
        if cell is None:
            cell = QTableWidgetItem("")
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, COL_SAID, cell)
        cell.setText(text or "")
        cell.setToolTip(tip or text or "")

    def _transcript_tip(segs, task=None):
        sents = _sentences_of(task) if task is not None else []
        if not sents and segs:
            from cutora.batch.title_align import spoken_sentences
            try:
                sents = spoken_sentences(segs)
            except Exception:
                sents = []
        if sents:
            text = " ".join(str(x.get("text") or "").strip() for x in sents if str(x.get("text") or "").strip())
        else:
            from cutora.batch.title_align import ensure_sentence_punctuation
            words = [str((s or {}).get("text") or "").strip() for s in segs or []]
            text = ensure_sentence_punctuation(" ".join(w for w in words if w), segs)
        if not text:
            return ""
        try:
            a = float(segs[0].get("start") or 0.0)
            b = float(segs[-1].get("end") or 0.0)
            head = "识别到的原话（%.1f–%.1f 秒）：\n" % (a, b)
        except Exception:
            head = "识别到的原话：\n"
        return head + (text if len(text) <= 600 else text[:600] + "…")

    def _locate_label(task):
        """这一行的识别状态（和有没有粘标题无关）。"""
        path = _task_path(task)
        rec = state["locate"].get(path) or {}
        st = rec.get("state")
        if st == "queued":
            return "排队识别…", False
        if st == "running":
            return "识别中…", False
        if st == "error":
            return "识别失败：%s" % (rec.get("err") or "未知原因"), True
        return None, False

    _LABEL = {"matched": "已定位", "partial": "基本吻合", "picked": "手选词",
              "manual": "手填", "fallback": "未定位，用默认时长"}

    def _said_text_for(plan, task):
        """这一行「视频原话」该显示什么：对上了就是标题对应的那几句，没对上就是开头的几句（必带准确标点与句号）。"""
        from cutora.batch.title_align import ensure_sentence_punctuation, sentences_in_span
        sents = _sentences_of(task)
        if not sents:
            said = ensure_sentence_punctuation(str(plan.get("said") or ""))
            return said, said
        reason = str(plan.get("reason") or "")
        picked = []
        if reason in ("matched", "partial") and plan.get("start") is not None:
            picked = sentences_in_span(sents, plan.get("start"), plan.get("end"))
        if not picked:
            from cutora.batch.text_batch import split_sentences
            k = max(1, len(split_sentences(str(plan.get("text") or ""))))
            picked = sents[:k]
        text = " ".join(ensure_sentence_punctuation(str(x["text"])) for x in picked).strip()
        full = "\n".join("%.1f–%.1fs  %s" % (x["start"], x["end"], ensure_sentence_punctuation(str(x["text"]))) for x in sents[:12])
        return text, full

    def _replacement_for(row):
        """这一行用原话替换后的标题；没有原话返回 ""。"""
        if row >= len(state["tasks"]):
            return ""
        task = state["tasks"][row]
        sents = _sentences_of(task)
        if not sents:
            return ""
        title = table.cell_value(row, COL_TEXT)
        plans = page._last_plans or []
        plan = plans[row] if row < len(plans) else {}
        from cutora.batch.title_align import replacement_title, sentences_in_span
        reason = str(plan.get("reason") or "")
        picked = []
        if reason in ("matched", "partial") and plan.get("start") is not None:
            picked = sentences_in_span(sents, plan.get("start"), plan.get("end"))
        return replacement_title(title, picked or sents)

    def _is_problem(plan):
        if not plan.get("ok"):
            return bool(str(plan.get("text") or "").strip())
        how = str(plan.get("how") or "")
        return how in ("partial", "fallback") or (how == "matched" and plan.get("diff"))

    def _apply_filter():
        plans = page._last_plans or []
        on = btn_filter.isChecked()
        for r in range(table.rowCount()):
            plan = plans[r] if r < len(plans) else {}
            table.setRowHidden(r, bool(on and not _is_problem(plan)))

    def preview_plans():
        """重算每一行的定位并刷新「状态 / 视频原话」两列。这两列不进撤销，也不再触发自己。"""
        if getattr(page, "_in_plans", False):
            return page._last_plans
        page._in_plans = True
        try:
            with table.quiet():
                return _preview_plans()
        finally:
            page._in_plans = False

    def _preview_plans():
        from cutora.batch.text_batch import plan_rows
        from cutora.batch.title_align import diff_note
        tasks = list(getattr(host, "tasks", None) or [])
        plans = plan_rows(
            tasks, _rows(),
            auto_locate=bool(ck_auto.isChecked()),
            default_dur=float(sp_dur.value()),
            segments_of=_segments_of)
        page._last_plans = plans
        n_said = 0
        n_bad = 0
        extend = []
        for r, plan in enumerate(plans):
            if r >= table.rowCount():
                break
            task = state["tasks"][r] if r < len(state["tasks"]) else None
            segs = _segments_of(task) if task is not None else []
            cell = table.item(r, COL_STATE)
            if cell is not None:
                cell.setToolTip(_transcript_tip(segs, task=task))
            busy, busy_warn = _locate_label(task)
            text = str(plan.get("text") or "").strip()
            said, said_tip = _said_text_for(plan, task) if (task is not None and segs) else ("", "")
            _set_said(r, said, said_tip)
            if said:
                n_said += 1
            if not plan.get("ok"):
                if not text:
                    # 没粘标题：状态栏说清楚这条视频识别了没有
                    if busy:
                        _set_state(r, busy, warn=busy_warn, reason="busy")
                    elif segs:
                        _set_state(r, "已识别 %d 个词 · 粘上标题就自动定位" % len(segs), reason="ready")
                    else:
                        _set_state(r, "还没识别 · 空文本，跳过", warn=True, reason="empty")
                    continue
                _set_state(r, plan.get("warn") or "跳过", warn=True, reason="skip")
                continue
            how = plan.get("how") or ""
            reason = str(plan.get("reason") or how)
            if busy and how == "fallback":
                _set_state(r, busy, warn=busy_warn, reason="busy")
                continue
            span = "%.1f–%.1fs" % (plan.get("start") or 0.0, plan.get("end") or 0.0)
            tpl = str(plan.get("tpl") or "")
            tpl_bad = bool(tpl and tpl not in _known_templates())
            if tpl_bad:
                plan["tpl"] = ""
            tail = " · 模板「%s」不存在，用上方模板" % tpl if tpl_bad else ""
            diff = plan.get("diff") or []
            score = int(round(float(plan.get("score") or 0.0) * 100))
            if how == "matched":
                if diff:
                    label = "已定位 %d%%，%d 处词不同  %s" % (score, len(diff), span)
                    _set_state(r, label + tail, warn=True, reason="matched")
                    if cell is not None:
                        cell.setToolTip("标题 → 口播：\n" + diff_note(diff) + "\n\n" + _transcript_tip(segs, task=task))
                else:
                    _set_state(r, "已定位  %s%s" % (span, tail), warn=tpl_bad, reason="matched")
            elif how == "partial":
                _set_state(r, "基本吻合 %d%%，%d 处词不同  %s%s" % (score, len(diff), span, tail),
                           warn=True, reason="partial")
                if cell is not None:
                    cell.setToolTip("标题 → 口播：\n" + diff_note(diff) + "\n\n" + _transcript_tip(segs, task=task))
            elif how == "picked":
                _set_state(r, "手选词  %s%s" % (span, tail), reason="picked")
            elif how == "manual":
                _set_state(r, "手填  %s%s" % (span, tail), reason="manual")
            else:
                # fallback：说清楚为什么
                if reason == "mismatch":
                    n_bad += 1
                    _set_state(r, "视频内容与标题文字不符 · 用默认时长 %s%s" % (span, tail),
                               bad=True, reason="mismatch")
                elif reason == "window_short":
                    path = _task_path(task)
                    if path and path in state["extend_done"]:
                        _set_state(r, "整条视频里也只对上了开头 · 用默认时长 %s%s" % (span, tail),
                                   bad=True, reason="mismatch")
                    else:
                        _set_state(r, "标题超出识别范围，正在整条识别…  %s%s" % (span, tail),
                                   warn=True, reason="window_short")
                        if path:
                            extend.append(path)
                elif reason == "no_speech":
                    _set_state(r, "识别范围里没听到说话 · 用默认时长 %s%s" % (span, tail),
                               bad=True, reason="no_speech")
                elif reason == "no_subs" and busy:
                    _set_state(r, busy, warn=busy_warn, reason="busy")
                elif reason == "no_subs":
                    _set_state(r, "还没识别 · 用默认时长 %s%s" % (span, tail), warn=True, reason="no_subs")
                else:
                    _set_state(r, "%s · 用默认时长 %s%s" % (plan.get("warn") or "未定位", span, tail),
                               warn=True, reason=reason or "fallback")
        for r in range(min(table.rowCount(), len(plans))):
            cell = table.item(r, COL_TEXT)
            orig = cell.data(ROLE_ORIG) if cell is not None else None
            if orig not in (None, "") and cell.text() != orig:
                st = table.item(r, COL_STATE)
                if st is not None and "已换成原话" not in st.text():
                    st.setText(st.text() + " · 已换成原话")
                    st.setToolTip("原标题：\n%s\n\n%s" % (orig, st.toolTip() or ""))
        lbl_said.setText("%d 行有原话，%d 行不符" % (n_said, n_bad) if n_said else "")
        btn_said_all.setEnabled(n_said > 0)
        btn_said_bad.setEnabled(n_bad > 0)
        _apply_filter()
        if extend:
            _schedule_extend(extend)
        _sync_strip()
        return plans

    def _schedule_extend(paths):
        """标题超出识别范围的那几条：自动改成整条视频再识别一次（只做一次）。"""
        from cutora.batch.text_locate_asr import FULL_VIDEO, queue_locate_batch
        todo = [p for p in dict.fromkeys(paths) if p not in state["extend_done"] and p not in state["extend_wait"]]
        if not todo:
            return
        for p in todo:
            state["extend_wait"].add(p)

        def _done(path, segs, err):
            state["extend_wait"].discard(path)
            state["extend_done"].add(path)
            if err:
                _LOG.warning("整条识别失败 %s: %s", path, err)
                state["locate"][path] = {"state": "error", "err": err}
            else:
                state["segs"][path] = list(segs or [])
                state["locate"][path] = {"state": "done", "n": len(segs or [])}
                _LOG.info("[Locate] 自动整条识别 %s：%d 个词", path, len(segs or []))
            preview_plans()

        n = queue_locate_batch(todo, _done, start=0.0, end=FULL_VIDEO)
        if not n:
            for p in todo:
                state["extend_wait"].discard(p)
            page._extend_retry = list(todo)      # 正在跑别的批：跑完再来

    def _known_templates():
        from cutora.batch.text_batch import preset_names
        names = page.__dict__.get("_tpl_names")
        if names is None:
            names = set(preset_names())
            page._tpl_names = names
        return names

    def _show_msg(text, warn=False):
        # 不用 setStyleSheet（全局样式表数量有上限，见 test_t03_theme）：改调色板
        from PySide6.QtGui import QColor, QPalette
        from cutora.ui.theme.tokens import current
        t = current()
        color = getattr(t, "accent", "#E0A030") if warn else getattr(t, "fg2", "#999999")
        pal = asr_msg.palette()
        pal.setColor(QPalette.ColorRole.WindowText, QColor(color))
        asr_msg.setPalette(pal)
        asr_msg.setText(text)
        asr_msg.setVisible(bool(text))

    def _name_of_path(path):
        for task in state["tasks"]:
            if _task_path(task) == path:
                return _task_name(task)
        import os
        return os.path.basename(str(path).replace("\\", "/"))

    def _stamp_locate(path, segs):
        window = [float(sp_win0.value()), float(_win_end())]
        for task in state["tasks"]:
            if _task_path(task) != path:
                continue
            if (task or {}).get("segments"):
                continue
            task["_title_locate"] = {"window": window, "segments": list(segs or [])}

    def run_locate_asr(on_all_done=None):
        """只转写用户圈的那几秒。一条接一条，转完就写入缓存和工程。"""
        from cutora.batch.text_locate_asr import (
            clamp_window, queue_locate_batch, remember_window,
        )
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(page, "locate"):            # Q193：没有 N 卡先说清楚
            if callable(on_all_done):
                on_all_done()
            return 0
        start, end = clamp_window(sp_win0.value(), _win_end())
        if not ck_full.isChecked():
            sp_win0.setValue(start)
            sp_win1.setValue(end)
            remember_window(start, end)
        seen = set()
        todo = []
        for task in state["tasks"]:
            if (task or {}).get("segments"):
                continue
            path = _task_path(task)
            if path and path not in seen:
                seen.add(path)
                todo.append(path)
        if not todo:
            _show_msg("勾选的视频都已经有字幕，直接用字幕里的时间定位，不用再识别。")
            if callable(on_all_done):
                on_all_done()
            return 0
        state["pending"] = len(todo)
        state["total"] = len(todo)
        state["ok"] = 0
        state["fail"] = []
        state["order"] = list(todo)
        for i, path in enumerate(todo):
            state["locate"][path] = {"state": "running" if i == 0 else "queued"}
        btn_asr.setEnabled(False)
        btn_asr.setText("识别中…")
        asr_prog.setRange(0, len(todo))
        asr_prog.setValue(0)
        asr_prog.show()
        _show_msg("正在识别第 1 / %d 条：%s（第一次要加载模型，稍等十几秒）"
                  % (len(todo), _name_of_path(todo[0])))
        preview_plans()

        def _done(path, segs, err):
            state["segs"][path] = list(segs or [])
            if segs and not err:
                _stamp_locate(path, segs)
            state["pending"] = max(0, state["pending"] - 1)
            left = state["pending"]
            total = int(state.get("total") or 0)
            finished = total - left
            if err:
                _LOG.warning("定位识别失败 %s: %s", path, err)
                state["locate"][path] = {"state": "error", "err": err}
                state["fail"].append((path, err))
            else:
                state["locate"][path] = {"state": "done", "n": len(segs or [])}
                if not segs:
                    state["fail"].append((path, "这一段里没听到说话"))
                else:
                    state["ok"] += 1
            order = state.get("order") or []
            nxt = next((p for p in order if (state["locate"].get(p) or {}).get("state") == "queued"), None)
            if nxt is not None:
                state["locate"][nxt] = {"state": "running"}
            asr_prog.setValue(finished)
            btn_asr.setText("识别中… %d / %d" % (finished, total) if left else "识别开头时间戳")
            if left and nxt is not None:
                _show_msg("正在识别第 %d / %d 条：%s" % (finished + 1, total, _name_of_path(nxt)))
            preview_plans()
            if left == 0:
                btn_asr.setEnabled(True)
                btn_asr.setText("识别开头时间戳")
                asr_prog.hide()
                fails = state["fail"]
                msg = "识别完成：%d 条成功" % state["ok"]
                if fails:
                    names = "；".join("%s（%s）" % (_name_of_path(p), e) for p, e in fails[:6])
                    more = " 等 %d 条" % len(fails) if len(fails) > 6 else ""
                    msg += "，%d 条没识别出来：%s%s" % (len(fails), names, more)
                msg += "。「视频原话」一列是识别到的句子，点「用原话」可以替换标题；对不上的行会写明原因。"
                _show_msg(msg, warn=bool(fails))
                retry = list(getattr(page, "_extend_retry", None) or [])
                page._extend_retry = []
                if retry:
                    QTimer.singleShot(0, lambda: _schedule_extend(retry))
                if callable(on_all_done):
                    on_all_done()

        n = queue_locate_batch(
            todo, _done, start=start, end=end, force=bool(ck_force.isChecked()))
        if not n:
            state["pending"] = 0
            for path in todo:
                state["locate"].pop(path, None)
            btn_asr.setEnabled(True)
            btn_asr.setText("识别开头时间戳")
            asr_prog.hide()
            _show_msg("上一批识别还没结束，等它跑完再点。", warn=True)
            preview_plans()
            if callable(on_all_done):
                on_all_done()
            return 0
        return n

    def _dims():
        from cutora.model.canvas import canvas_wh
        task = None
        row = getattr(page, "_preview_row", None)
        if isinstance(row, int) and 0 <= row < len(state["tasks"]):
            task = state["tasks"][row]
        elif state["tasks"]:
            task = state["tasks"][0]
        try:
            return canvas_wh(task)
        except Exception:
            _LOG.exception("读取画布尺寸失败")
            return 1080, 1920

    def _adopt_preset(ref):
        """把下拉里选中的模板装进 page.style。比例框另存在 page.layout，按每条视频的画布再换算。"""
        from cutora.batch.text_batch import template_for_preset
        cw, ch = _dims()
        tpl = template_for_preset(ref, cw, ch)
        page.layout = tpl["layout"]
        page.preset_id = tpl["preset_id"]
        page.style.clear()
        page.style.update(tpl["style"])

    def _row_template(name):
        """这一行单独套用的预设（按名字缓存；预设列表刷新时清掉）。"""
        from cutora.batch.text_batch import template_for_preset
        cache = page._tpl_cache
        tpl = cache.get(name)
        if tpl is None:
            cw, ch = _dims()
            tpl = template_for_preset(name, cw, ch)
            cache[name] = tpl
        return tpl

    def _sync_controls():
        return None   # Q136：没有样式控件可同步

    def current_preview():
        """当前点中的那一行，预览该画成什么样。不改任何 task。"""
        row = getattr(page, "_preview_row", None)
        if not isinstance(row, int) or row < 0 or row >= table.rowCount():
            return None
        if row >= len(state["tasks"]):
            return None
        task = state["tasks"][row]
        rows = _rows()
        if row >= len(rows):
            return None
        from cutora.batch.text_batch import build_overlay, plan_rows
        plans = plan_rows(
            list(getattr(host, "tasks", None) or []), rows,
            auto_locate=bool(ck_auto.isChecked()),
            default_dur=float(sp_dur.value()),
            segments_of=_segments_of)
        plan = plans[row] if row < len(plans) else {"ok": False, "text": ""}
        path = str((task or {}).get("video") or (task or {}).get("path") or "")
        if plan.get("ok"):
            try:
                t = float(plan.get("start") if plan.get("start") is not None else 0.0)
            except (TypeError, ValueError):
                t = 0.0
        else:
            raw = rows[row].get("start")
            try:
                t = float(raw) if raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                t = 0.0
        text = str(plan.get("text") or "")
        overlay = None
        if plan.get("ok") and text.strip():
            try:
                end = float(plan.get("end") if plan.get("end") is not None else t + 0.2)
            except (TypeError, ValueError):
                end = t + 0.2
            if end < t:
                end = t
            cw, ch = _dims()
            row_tpl = str(plan.get("tpl") or "")
            if row_tpl:
                tpl = _row_template(row_tpl)
                style, lay_, pid, pname = tpl["style"], tpl["layout"], tpl["preset_id"], row_tpl
            else:
                style, lay_, pid = page.style, page.layout, page.preset_id
                pname = (cb_preset.currentData() or "") or None
            overlay = build_overlay(
                text, t, end, width=int(cw), height=int(ch),
                template=copy.deepcopy(style),
                preset_name=pname,
                layout=copy.deepcopy(lay_) if isinstance(lay_, dict) else None,
                preset_id=pid or None)
            # 正文以表格这一格为准，样式里即使混进了示例字也不许盖掉
            overlay["content"] = text
        return {"path": path, "time": t, "task": task, "overlay": overlay, "plan": plan}

    def _push_preview():
        fn = getattr(dlg, "show_text_preview", None) if dlg is not None else None
        if not callable(fn):
            return
        fn()

    def _refill_presets(select_name):
        from cutora.subtitle import text_preset as TP
        page._tpl_cache = {}
        page._tpl_names = None
        cb_preset.blockSignals(True)
        try:
            cb_preset.clear()
            cb_preset.addItem("默认文本样式", "")
            try:
                for row in TP.list_presets() or []:
                    name = str((row or {}).get("name") or "")
                    if name:
                        cb_preset.addItem(name, name)
            except Exception:
                _LOG.exception("刷新文本模板列表失败")
            if select_name and cb_preset.findData(select_name) < 0:
                cb_preset.addItem(select_name, select_name)
            idx = cb_preset.findData(select_name) if select_name else 0
            if idx < 0:
                idx = 0
            cb_preset.setCurrentIndex(idx)
        finally:
            cb_preset.blockSignals(False)

    def _preset_by_name(name):
        from cutora.subtitle import text_preset as TP
        want = str(name or "").strip()
        if not want:
            return None
        try:
            rows = TP.list_presets() or []
        except Exception:
            _LOG.exception("查找文本模板失败")
            return None
        for row in rows:
            if str((row or {}).get("name") or "") == want:
                return row
        return None

    def _on_preset(_i=0):
        _adopt_preset(cb_preset.currentData() or "")
        _sync_controls()
        _push_preview()

    def _on_cell(row, *_rest):
        if page._refreshing:
            return
        try:
            row = int(row)
        except (TypeError, ValueError):
            return
        if row < 0 or row >= table.rowCount():
            return
        if page._preview_row != row and player["row"] is not None:
            _stop_play()
        page._preview_row = row
        _sync_strip()
        _push_preview()

    def apply():
        """把表格里的文本批量加到各自视频的时间线上。样式用 page.style。"""
        from cutora.batch.text_batch import apply_rows
        plans = preview_plans()
        _push_preview()
        stats = apply_rows(
            plans, list(getattr(host, "tasks", None) or []),
            template=page.style,
            preset_name=(cb_preset.currentData() or "") or None,
            new_track=True, host=host,
            layout=page.layout, preset_id=page.preset_id or None,
            row_templates={})
        return stats

    page.style = {}
    page._last_plans = []
    page._in_plans = False
    page.layout = None
    page.preset_id = ""
    page._tpl_cache = {}
    page._tpl_names = None
    page._preview_row = None
    page._refreshing = False
    _refill_presets("")
    _adopt_preset(cb_preset.currentData() or "")
    _sync_controls()

    def text_for_task(task):
        want = id(task)
        for r in range(table.rowCount()):
            key = table.item(r, COL_NAME)
            cell = table.item(r, COL_TEXT)
            if key is None or cell is None:
                continue
            if key.data(Qt.ItemDataRole.UserRole) == want:
                return cell.text()
        return ""

    def segments_for_task(task):
        return _segments_of(task)

    try:
        from cutora.batch.text_locate_asr import saved_window
        w0, w1 = saved_window()
        sp_win0.setValue(w0)
        sp_win1.setValue(w1)
    except Exception:
        _LOG.exception("读取上次的时间窗口失败")

    btn_asr.clicked.connect(lambda: run_locate_asr())

    def _on_full(on):
        sp_win0.setEnabled(not on)
        sp_win1.setEnabled(not on)
        preview_plans()

    ck_full.toggled.connect(_on_full)
    btn_paste.clicked.connect(lambda: (table.paste_from_clipboard(), preview_plans()))
    btn_clear.clicked.connect(
        lambda: (table.apply_values({(r, COL_TEXT): "" for r in range(table.rowCount())}, "清空"),
                 preview_plans()))

    def _zoom():
        dlg_z = TableZoomDialog(table, lay, table_home_index, parent=page.window())
        page.zoom_dialog = dlg_z
        dlg_z.exec()
        page.zoom_dialog = None
        preview_plans()

    btn_zoom.clicked.connect(_zoom)

    def _show_table_msg(text):
        tip.setText(text or "")

    table.status.connect(_show_table_msg)

    def _on_cells_changed():
        preview_plans()
        if page._preview_row is not None:
            _push_preview()

    table.cells_changed.connect(_on_cells_changed)

    def _on_item_changed(item):
        if item is not None and item.column() == COL_TEXT:
            item.setToolTip(item.text())
        if table._batch:
            return          # 批量改（粘贴 / 填充 / 撤销）结束时 cells_changed 统一刷一次
        preview_plans()
        if page._refreshing:
            return
        if item is not None and page._preview_row == item.row():
            _push_preview()

    table.itemChanged.connect(_on_item_changed)
    table.cellClicked.connect(_on_cell)
    table.currentCellChanged.connect(lambda row, _c, _pr, _pc: _on_cell(row))
    def _on_auto(on):
        # Q191：自动定位时「起点 / 时长」两列用不上，收起来给文本内容腾地方；关掉自动定位才露出来手填
        table.setColumnHidden(COL_START, bool(on))
        table.setColumnHidden(COL_DUR, bool(on))
        preview_plans()

    ck_auto.toggled.connect(_on_auto)
    table.setColumnHidden(COL_START, bool(ck_auto.isChecked()))
    table.setColumnHidden(COL_DUR, bool(ck_auto.isChecked()))
    sp_dur.valueChanged.connect(lambda _v: preview_plans())
    cb_preset.currentIndexChanged.connect(_on_preset)


    # ───────────────────────── Q190：原话替换 / 点词定位 / 试听 / 筛选 / 粘贴即识别 ─────────────────────────

    def _mark_replaced(row, orig):
        cell = table.item(row, COL_TEXT)
        if cell is None:
            return
        if cell.data(ROLE_ORIG) in (None, ""):
            cell.setData(ROLE_ORIG, orig)

    def use_said(row, *, refresh_after=True):
        new = _replacement_for(row)
        if not new:
            return False
        old = table.cell_value(row, COL_TEXT)
        if new == old:
            return False
        _mark_replaced(row, old)
        st = table.item(row, COL_STATE)
        if st is not None:
            st.setData(ROLE_PICK, None)
        table.apply_values({(row, COL_TEXT): new}, "用原话")
        if refresh_after:
            preview_plans()
        _LOG.info("[TextBatch] 第 %d 行用原话：%r → %r", row + 1, old[:80], new[:80])
        return True

    table.use_said = use_said

    def use_said_rows(only_bad):
        changes = {}
        plans = page._last_plans or []
        for r in range(table.rowCount()):
            plan = plans[r] if r < len(plans) else {}
            if only_bad and str(plan.get("reason") or "") != "mismatch":
                continue
            new = _replacement_for(r)
            old = table.cell_value(r, COL_TEXT)
            if new and new != old:
                _mark_replaced(r, old)
                st = table.item(r, COL_STATE)
                if st is not None:
                    st.setData(ROLE_PICK, None)
                changes[(r, COL_TEXT)] = new
        n = table.apply_values(changes, "全部用原话" if not only_bad else "替换不符的") if changes else 0
        _show_table_msg("换成原话 %d 行（Ctrl+Z 可撤销）" % n if n else "没有可替换的行")
        preview_plans()
        return n

    btn_said_all.clicked.connect(lambda: use_said_rows(False))
    btn_said_bad.clicked.connect(lambda: use_said_rows(True))
    btn_filter.toggled.connect(lambda _on: _apply_filter())

    def _cur_row():
        row = getattr(page, "_preview_row", None)
        if isinstance(row, int) and 0 <= row < table.rowCount() and row < len(state["tasks"]):
            return row
        return None

    def _sync_strip():
        row = _cur_row()
        if row is None:
            strip_box.hide()
            return
        task = state["tasks"][row]
        words = _segments_of(task)
        if not words or (task or {}).get("segments"):
            # 工程字幕是整句一条，没法按词选；只对识别出来的词开放
            strip_box.setVisible(bool(words) and not (task or {}).get("segments"))
            if not strip_box.isVisible():
                return
        plans = page._last_plans or []
        plan = plans[row] if row < len(plans) else {}
        auto = None
        if plan.get("start") is not None and str(plan.get("how") or "") in ("matched", "partial", "picked"):
            auto = (plan.get("start"), plan.get("end"))
        pick = _pick_of(row)
        from cutora.batch.title_align import punctuate_words_for_display
        disp_words = punctuate_words_for_display(words, _sentences_of(task))
        strip.set_words(disp_words, auto_span=auto, picked=pick)
        lbl_strip.setText(
            ("第 %d 行 · 手选了 %.1f–%.1fs，" % (row + 1, pick[0], pick[1]) if pick else
             "第 %d 行 · " % (row + 1)) + "按住鼠标拖过标题对应的那几个词，起止就定在这几个词上")
        btn_unpick.setEnabled(bool(pick))
        strip_box.show()

    def _on_pick(a, b):
        row = _cur_row()
        if row is None:
            return
        st = table.item(row, COL_STATE)
        if st is None:
            return
        st.setData(ROLE_PICK, None if a is None else [float(a), float(b)])
        if a is not None:
            _LOG.info("[TextBatch] 第 %d 行手选词 %.2f–%.2fs", row + 1, a, b)
        preview_plans()
        _push_preview()

    strip.on_pick = _on_pick
    btn_unpick.clicked.connect(strip.clear_pick)

    # 试听：只放声音（QMediaPlayer 只开音频，不解视频），词跟着亮；放完或换行就停并释放
    player = {"p": None, "a": None, "end": 0.0, "row": None}

    def _stop_play():
        mp = player["p"]
        if mp is not None:
            try:
                mp.stop()
                from PySide6.QtCore import QUrl
                mp.setSource(QUrl())
            except Exception:
                pass
        player["row"] = None
        btn_play.setText("▶ 试听这一段")
        strip.playing_at = None
        strip.update()

    def _on_pos(ms):
        t = ms / 1000.0
        if player["row"] is None:
            return
        strip.playing_at = t
        strip.update()
        if t >= player["end"]:
            _stop_play()

    def play_span(row=None):
        row = _cur_row() if row is None else row
        if row is None:
            return False
        if player["row"] is not None:
            _stop_play()
            return False
        plans = page._last_plans or []
        plan = plans[row] if row < len(plans) else {}
        if plan.get("start") is None:
            return False
        path = _task_path(state["tasks"][row])
        if not path:
            return False
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if player["p"] is None:
                mp = QMediaPlayer(page)
                ao = QAudioOutput(page)
                mp.setAudioOutput(ao)
                mp.positionChanged.connect(_on_pos)
                mp.setVideoOutput(None)
                player["p"], player["a"] = mp, ao
            mp = player["p"]
            start = max(0.0, float(plan["start"]) - 0.15)
            player["end"] = float(plan.get("end") or start) + 0.25
            player["row"] = row
            mp.setSource(QUrl.fromLocalFile(path))
            mp.setPosition(int(start * 1000))
            mp.play()
            btn_play.setText("■ 停止")
            _LOG.info("[TextBatch] 试听第 %d 行 %.2f–%.2fs", row + 1, start, player["end"])
            return True
        except Exception:
            _LOG.exception("试听失败")
            _stop_play()
            return False

    btn_play.clicked.connect(lambda: play_span())

    from PySide6.QtCore import QObject, QEvent

    class _Keys(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Space \
                    and table.state() != QAbstractItemView.State.EditingState \
                    and not ev.modifiers():
                play_span()
                return True
            return False

    page._keys = _Keys(page)
    table.installEventFilter(page._keys)

    # 粘贴 / 改了标题 → 还没识别的视频自动排队识别（停手 0.6 s 再跑，连续粘几次只跑一批）
    auto_timer = QTimer(page)
    auto_timer.setSingleShot(True)
    auto_timer.setInterval(AUTO_LOCATE_DELAY_MS)

    def _auto_locate():
        if not ck_auto.isChecked() or not btn_asr.isEnabled():
            return
        from cutora.ui.gpu_notice import declined
        if declined("locate"):
            return                      # 没有 N 卡、这次已经点过「取消」：粘贴时不再自动跑
        need = []
        for r, task in enumerate(state["tasks"]):
            if not table.cell_value(r, COL_TEXT).strip():
                continue
            if (task or {}).get("segments"):
                continue
            path = _task_path(task)
            if not path or path in state["auto_seen"]:
                continue
            words, _s, _l = _located(task)
            if words:
                continue
            need.append(path)
        if not need:
            _upgrade_old_results()
            return
        state["auto_seen"].update(need)
        _LOG.info("[TextBatch] 粘贴后自动识别 %d 条", len(need))
        run_locate_asr(on_all_done=_upgrade_old_results)

    def _upgrade_old_results():
        """旧版识别结果只有词、没有标点 → 「视频原话」是一串小写词。后台把这几条重跑一次拿带标点的原句。"""
        from cutora.batch.text_locate_asr import queue_locate_batch
        todo = []
        for r, task in enumerate(state["tasks"]):
            if (task or {}).get("segments"):
                continue
            path = _task_path(task)
            if not path or path in state["upgrade_seen"]:
                continue
            words, sents, label = _located(task)
            if words and not sents and label not in ("subs", "project", "mem"):
                todo.append(path)
        if not todo:
            return
        state["upgrade_seen"].update(todo)
        start, end = float(sp_win0.value()), _win_end()

        def _done(path, segs, err):
            if err:
                _LOG.warning("补全原话标点失败 %s: %s", path, err)
            preview_plans()

        n = queue_locate_batch(todo, _done, start=start, end=end, force=True)
        if n:
            _LOG.info("[TextBatch] 旧识别结果没有标点，后台重识别 %d 条拿原句", n)
            _show_msg("正在后台补全 %d 条视频原话的标点（旧的识别结果没有标点），不影响定位。" % n)
        else:
            for p in todo:
                state["upgrade_seen"].discard(p)

    auto_timer.timeout.connect(_auto_locate)

    def _kick_auto():
        if ck_auto.isChecked():
            auto_timer.start()

    table.cells_changed.connect(_kick_auto)
    page.kick_auto_locate = _kick_auto
    page.use_said = use_said
    page.use_said_rows = use_said_rows
    page.play_span = play_span
    page.stop_play = _stop_play
    page.strip = strip
    page.strip_box = strip_box
    page.btn_filter = btn_filter
    page.btn_said_all = btn_said_all
    page.btn_said_bad = btn_said_bad
    page.ck_full = ck_full
    page.sync_strip = _sync_strip
    page._last_plans = []

    page.table = table
    page.btn_zoom = btn_zoom
    page.zoom_dialog = None
    page.zoom = _zoom
    page.refresh = refresh
    page.apply = apply
    page.preview_plans = preview_plans
    page.run_locate_asr = run_locate_asr
    page.text_for_task = text_for_task
    page.segments_for_task = segments_for_task
    page.btn_asr = btn_asr
    page.asr_prog = asr_prog
    page.asr_msg = asr_msg
    page.sp_win0 = sp_win0
    page.sp_win1 = sp_win1
    page.current_preview = current_preview
    page.push_preview = _push_preview
    page.cb_preset = cb_preset
    page.ck_auto = ck_auto
    page.sp_dur = sp_dur
    def _refill_and_adopt(select_name=""):
        _refill_presets(select_name)
        _on_preset()

    page.refill_presets = _refill_and_adopt
    return page
