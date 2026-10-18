# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q126 · 选中文本就浮出来的工具条（文本工具改造第三批）。

Canva 好用的一半原因是：选中文本，字体 / 字号 / 颜色 / 粗斜体 / 对齐就在手边，
不用把鼠标横跨整个屏幕去右侧面板里找。

这里的每一个控件都是**属性面板同名控件的代理**：改这里 = 改面板上那个控件 =
走面板自己的 on_change_ov 写进 overlay。没有第二份数据，没有第二条写入路径。
面板那边改了（或者换了选中对象），这里跟着刷新。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QSpinBox,
    QToolButton,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    INSPECTOR_FIELD_GAP,
    SPACE_1,
    SPACE_2,
    SPACE_4,
    SPACE_6,
)

_LOG = get_logger("cutora.ui.text_context_bar")

BAR_TOP_MARGIN = SPACE_2


class TextContextBar(QFrame):
    """浮在预览上方的文本工具条。``panel`` 是 PropertyPanel。"""

    more_requested = Signal()
    align_requested = Signal(str)        # Q128：对齐模式 left/hcenter/right/top/vcenter/bottom
    distribute_requested = Signal(str)   # Q128：h / v
    style_copy_requested = Signal()
    style_paste_requested = Signal()
    styles_requested = Signal(object)    # Q128：打开样式画廊，参数是锚点（全局坐标 QPoint）

    def __init__(self, panel, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("textContextBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel = panel
        self._host = parent
        self._syncing = False
        self._compact = 0
        self._build()
        self.hide()
        parent.installEventFilter(self)

    # ------------------------------------------------------------ 搭界面

    def _build(self):
        from cutora.ui.icons import icon as lucide_icon
        from cutora.ui.panels.fonts import register_font_combo
        from cutora.ui.widgets.inspector import ColorSwatch, SegmentedChoice

        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_2, SPACE_1, SPACE_2, SPACE_1)
        row.setSpacing(INSPECTOR_FIELD_GAP)

        # 属性名刻意避开 QWidget 自己的 font() / size() 这类方法，不然把 Qt 内部
        # 用的东西盖掉，样式都会乱。
        self.cb_font = QComboBox()
        self.cb_font.setObjectName("ctxFont")
        self.cb_font.setMinimumWidth(SPACE_6 * 4)
        self.cb_font.setMaximumWidth(SPACE_6 * 5 + SPACE_4)
        self.cb_font.setToolTip(tr("字体"))
        register_font_combo(self.cb_font)
        self.cb_font.currentIndexChanged.connect(self._push_font)
        row.addWidget(self.cb_font)

        self.sp_size = QSpinBox()
        self.sp_size.setObjectName("ctxSize")
        self.sp_size.setRange(8, 600)
        self.sp_size.setSuffix(" px")
        self.sp_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sp_size.setFixedWidth(SPACE_6 * 2 + SPACE_4)
        self.sp_size.setToolTip(tr("字号"))
        self.sp_size.valueChanged.connect(self._push_size)
        row.addWidget(self.sp_size)

        self.sw_color = ColorSwatch("#FFFFFF", chip=True)
        self.sw_color.setToolTip(tr("字色"))
        self.sw_color.changed.connect(self._push_color)
        row.addWidget(self.sw_color)

        row.addWidget(self._sep())

        self.bt_bold = QToolButton()
        self.bt_bold.setCheckable(True)
        self.bt_bold.setIcon(lucide_icon("bold"))
        self.bt_bold.setToolTip(tr("粗体"))
        self.bt_bold.toggled.connect(self._push_bold)
        row.addWidget(self.bt_bold)
        self.bt_italic = QToolButton()
        self.bt_italic.setCheckable(True)
        self.bt_italic.setIcon(lucide_icon("italic"))
        self.bt_italic.setToolTip(tr("斜体"))
        self.bt_italic.toggled.connect(self._push_italic)
        row.addWidget(self.bt_italic)

        row.addWidget(self._sep())

        self.sg_halign = SegmentedChoice((
            ("left", "align-left", tr("左对齐")),
            ("center", "align-center", tr("居中")),
            ("right", "align-right", tr("右对齐")),
        ))
        self.sg_halign.currentTextChanged.connect(self._push_halign)
        row.addWidget(self.sg_halign)
        self.sg_valign = SegmentedChoice((
            ("top", "align-vertical-justify-start", tr("顶对齐")),
            ("middle", "align-vertical-justify-center", tr("垂直居中")),
            ("bottom", "align-vertical-justify-end", tr("底对齐")),
        ))
        self.sg_valign.currentTextChanged.connect(self._push_valign)
        row.addWidget(self.sg_valign)

        row.addWidget(self._sep())

        # 行距 / 字距：名字做成前缀写在框里，不再是一个孤零零的标签 + 远处一个数
        self.sp_line = QSpinBox()
        self.sp_line.setRange(0, 200)
        self.sp_line.setPrefix(tr("行距") + " ")
        self.sp_line.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sp_line.setFixedWidth(SPACE_6 * 3)
        self.sp_line.setToolTip(tr("行距（像素）"))
        self.sp_line.valueChanged.connect(self._push_line_spacing)
        row.addWidget(self.sp_line)

        self.sp_tracking = QDoubleSpinBox()
        self.sp_tracking.setRange(-0.05, 0.5)
        self.sp_tracking.setSingleStep(0.01)
        self.sp_tracking.setDecimals(2)
        self.sp_tracking.setPrefix(tr("字距") + " ")
        self.sp_tracking.setSuffix(" em")
        self.sp_tracking.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sp_tracking.setFixedWidth(SPACE_6 * 4)
        self.sp_tracking.setToolTip(tr("字距（em）"))
        self.sp_tracking.valueChanged.connect(self._push_tracking)
        row.addWidget(self.sp_tracking)

        self._sep_spacing = self._sep()
        row.addWidget(self._sep_spacing)

        # Q128：排列（对齐 / 等距分布）和样式刷。一个下拉，不占地方。
        from PySide6.QtWidgets import QMenu
        from cutora.ui.align_ops import ALIGN_LABELS, ALIGN_MODES, DISTRIBUTE_AXES, DISTRIBUTE_LABELS
        self.bt_arrange = QToolButton()
        self.bt_arrange.setText(tr("排列"))
        self.bt_arrange.setToolTip(tr("对齐 / 等距分布（只选一条时相对画布；Shift+点多选后相对彼此）"))
        self.bt_arrange.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.bt_arrange)
        self._arrange_actions = {}
        for mode in ALIGN_MODES:
            act = menu.addAction(tr(ALIGN_LABELS[mode]))
            act.triggered.connect(lambda _c=False, m=mode: self.align_requested.emit(m))
            self._arrange_actions[mode] = act
            if mode == "right":
                menu.addSeparator()
        menu.addSeparator()
        self._distribute_actions = {}
        for axis in DISTRIBUTE_AXES:
            act = menu.addAction(tr(DISTRIBUTE_LABELS[axis]))
            act.triggered.connect(lambda _c=False, a=axis: self.distribute_requested.emit(a))
            self._distribute_actions[axis] = act
        menu.addSeparator()
        act_c = menu.addAction(tr("复制样式") + "\tCtrl+Alt+C")
        act_c.triggered.connect(self.style_copy_requested.emit)
        self._act_paste_style = menu.addAction(tr("粘贴样式") + "\tCtrl+Alt+V")
        self._act_paste_style.triggered.connect(self.style_paste_requested.emit)
        menu.aboutToShow.connect(self._sync_arrange_menu)
        self.bt_arrange.setMenu(menu)
        row.addWidget(self.bt_arrange)

        self.bt_styles = QToolButton()
        self.bt_styles.setText(tr("样式"))
        self.bt_styles.setToolTip(tr("文本样式画廊：一格一格看着挑，点一下刷到选中的文本上"))
        self.bt_styles.clicked.connect(self._emit_styles)
        row.addWidget(self.bt_styles)

        self.bt_more = QToolButton()
        self.bt_more.setText(tr("更多…"))
        self.bt_more.setToolTip(tr("描边、背景、发光、动画在右侧属性面板"))
        self.bt_more.clicked.connect(self.more_requested.emit)
        row.addWidget(self.bt_more)

    def _emit_styles(self):
        from PySide6.QtCore import QPoint
        anchor = self.bt_styles.mapToGlobal(QPoint(0, self.bt_styles.height() + 2))
        self.styles_requested.emit(anchor)

    def set_selection_count(self, n: int) -> None:
        """多选数量：≥3 才能等距分布；菜单打开时也会再对一次。"""
        self._sel_count = int(n or 0)

    def _sync_arrange_menu(self):
        from cutora.ui.text_style_clipboard import has_clipboard
        n = int(getattr(self, "_sel_count", 0) or 0)
        for act in self._distribute_actions.values():
            act.setEnabled(n >= 3)
        self._act_paste_style.setEnabled(has_clipboard())

    @staticmethod
    def _sep():
        s = QFrame()
        s.setObjectName("ctxSep")
        s.setFrameShape(QFrame.Shape.VLine)
        s.setFixedWidth(1)
        s.setFixedHeight(SPACE_4 + SPACE_1)
        return s

    # -------------------------------------------------------- 显示 / 定位

    def show_for(self, type_, data) -> bool:
        """选中的是文本 overlay 就显示并同步，否则藏起来。返回是否显示。"""
        is_text = (
            type_ == "overlay" and isinstance(data, dict)
            and str(data.get("type") or "text") == "text"
        )
        if not is_text:
            self.hide()
            return False
        self.refresh()
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        return True

    def _reposition(self):
        """居中放在预览顶上。预览太窄就先收起行距 / 字距那一段，再收字体框。"""
        host = self._host
        if host is None:
            return
        avail = max(0, host.width() - SPACE_2 * 2)
        level = 0
        self._set_compact(0)
        while self.sizeHint().width() > avail and level < self.COMPACT_MAX:
            level += 1
            self._set_compact(level)
        self.adjustSize()
        w = self.width()
        x = max(0, (host.width() - w) // 2)
        self.move(x, BAR_TOP_MARGIN)

    COMPACT_MAX = 4

    def _set_compact(self, level: int) -> None:
        """0 全显示；1 收起行距/字距；2 把字体框压到最窄；
        3 收起「排列」（右键菜单里还有）；4 再收起「样式」。"""
        show_spacing = level < 1
        for w in (self.sp_line, self.sp_tracking, self._sep_spacing):
            w.setVisible(show_spacing)
        if level >= 2:
            self.cb_font.setMinimumWidth(SPACE_6 * 3)
            self.cb_font.setMaximumWidth(SPACE_6 * 3 + SPACE_4)
        else:
            self.cb_font.setMinimumWidth(SPACE_6 * 4)
            self.cb_font.setMaximumWidth(SPACE_6 * 5 + SPACE_4)
        self.bt_arrange.setVisible(level < 3)
        self.bt_styles.setVisible(level < 4)
        self._compact = int(level)

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            if self.isVisible():
                self._reposition()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------ 从面板读过来

    def refresh(self):
        """把面板当前的值搬到这里。信号全堵住，不会反过来再写面板。"""
        p = self._panel
        if p is None:
            return
        self._syncing = True
        try:
            self._mirror_combo(self.cb_font, getattr(p, "ov_font", None))
            self._set(self.sp_size, int(getattr(p, "ov_size").value()))
            colors = getattr(p, "fx_fill_colors", None)
            if colors is not None and colors.colors():
                self.sw_color.set_hex(str(colors.colors()[0]), emit=False)
            self._set_checked(self.bt_bold, getattr(p, "ov_bold").isChecked())
            self._set_checked(self.bt_italic, getattr(p, "ov_italic").isChecked())
            ha = getattr(p, "ov_text_align").currentText() or "left"
            self._set_text(self.sg_halign, ha if ha in ("left", "center", "right") else "left")
            va = getattr(p, "ov_text_valign", None)
            if va is not None:
                self._set_text(self.sg_valign, va.currentText() or "top")
            self._set(self.sp_line, int(getattr(p, "ov_line_spacing").value()))
            tk = getattr(p, "fx_tracking_em", None)
            if tk is not None:
                self._set(self.sp_tracking, float(tk.value()))
        except Exception:
            _LOG.exception("文本工具条同步失败")
        finally:
            self._syncing = False

    @staticmethod
    def _mirror_combo(mine: QComboBox, theirs: QComboBox | None):
        if theirs is None:
            return
        mine.blockSignals(True)
        try:
            if mine.count() != theirs.count():
                mine.clear()
                for i in range(theirs.count()):
                    mine.addItem(theirs.itemText(i), theirs.itemData(i))
            mine.setCurrentIndex(theirs.currentIndex())
        finally:
            mine.blockSignals(False)

    @staticmethod
    def _set(w, value):
        w.blockSignals(True)
        try:
            w.setValue(value)
        finally:
            w.blockSignals(False)

    @staticmethod
    def _set_checked(w, on):
        w.blockSignals(True)
        try:
            w.setChecked(bool(on))
        finally:
            w.blockSignals(False)

    @staticmethod
    def _set_text(w, text):
        w.blockSignals(True)
        try:
            w.setCurrentText(str(text))
        finally:
            w.blockSignals(False)

    # ------------------------------------------------------- 写回到面板

    def _live(self) -> bool:
        p = self._panel
        return (not self._syncing) and p is not None and not getattr(p, "block_signals", False)

    def _push_font(self, idx):
        if self._live():
            self._panel.ov_font.setCurrentIndex(int(idx))

    def _push_size(self, v):
        if self._live():
            self._panel.ov_size.setValue(int(v))

    def _push_color(self):
        if not self._live():
            return
        colors = getattr(self._panel, "fx_fill_colors", None)
        if colors is None:
            return
        rest = list(colors.colors() or [])
        new = [self.sw_color.hex()] + rest[1:]
        colors.set_colors(new, emit=True)

    def _push_bold(self, on):
        if self._live():
            self._panel.ov_bold.setChecked(bool(on))

    def _push_italic(self, on):
        if self._live():
            self._panel.ov_italic.setChecked(bool(on))

    def _push_halign(self, text):
        if self._live():
            self._panel.ov_text_align.setCurrentText(str(text))

    def _push_valign(self, text):
        if self._live() and hasattr(self._panel, "ov_text_valign"):
            self._panel.ov_text_valign.setCurrentText(str(text))

    def _push_line_spacing(self, v):
        if self._live():
            self._panel.ov_line_spacing.setValue(int(v))

    def _push_tracking(self, v):
        if self._live() and hasattr(self._panel, "fx_tracking_em"):
            self._panel.fx_tracking_em.setValue(float(v))
