# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Welcome-page band: drop clips or folders, paste scripts, start a stitch.

Imports of script_stitcher / stitch_session happen inside methods, after the
user drops something, picks files, or presses start. Building this widget
must not load numpy or whisperx.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QProgressBar, QPushButton, QSizePolicy, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    FONT_PAGE, RADIUS_CARD, SPACE_1, SPACE_2, SPACE_3, SPACE_4,
)

# Same cap as the welcome actions column. test_welcome_size_presets checks
# every QPushButton on the page is at most this wide.
_BTN_CAP = 380
_RIGHT_W = 320      # 留给老测试；新版参数栏跟着列宽走
_LABEL_W = 64       # 参数区标签列，两行对齐
_CTL_H = 32         # 参数控件统一高度
_START_H = 44       # 主按钮更高一点，是这一列的落点
_EM = "—"


def _cap(btn: QPushButton, width: int = _BTN_CAP) -> QPushButton:
    btn.setMaximumWidth(width)
    btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _place_key(groups, name, label, notes):
    """Same rename the multi-folder scan uses when a group name collides."""
    key = name
    if key not in groups:
        return key
    key = "%s(%s)" % (name, label)
    i = 2
    while key in groups:
        key = "%s(%s-%d)" % (name, label, i)
        i += 1
    notes.append(
        "组名 %s 在多个文件夹里都有, 表里改叫 %s "
        "(成品仍叫 %s.mp4, 写回它自己的文件夹)" % (name, key, name))
    return key


class _DropWell(QFrame):
    """Dashed target. Drops are handled by the band, which is the ancestor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homeStitchDrop")
        self.setMinimumHeight(SPACE_4 * 8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        super().paintEvent(event)
        from cutora.ui.theme.tokens import current
        tok = current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tok.line))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            self.rect().adjusted(1, 1, -2, -2), RADIUS_CARD, RADIUS_CARD)
        painter.end()


class _PasteDialog(QDialog):
    """One PasteTable, groups already filled. Ctrl+V lands the Sheet column."""

    def __init__(self, keys, scripts, parent=None):
        super().__init__(parent)
        from cutora.batch.script_stitcher import PasteTable

        self.setWindowTitle(tr("粘贴文案"))
        self.setMinimumSize(520, 360)
        self._keys = list(keys)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE_4, SPACE_3, SPACE_4, SPACE_3)
        lay.setSpacing(SPACE_2)
        hint = QLabel(tr(
            "在表格里点一下，Ctrl+V 把表格那一列贴进来。一格对应一组，多行会并成一条。"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = PasteTable(self._paste, 0, 2)
        self.table.setHorizontalHeaderLabels([tr("组"), tr("文案")])
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setRowCount(len(self._keys))
        for row, key in enumerate(self._keys):
            head = QTableWidgetItem(key)
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, head)
            body = QTableWidgetItem(str((scripts or {}).get(key) or ""))
            self.table.setItem(row, 1, body)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.setSpacing(SPACE_2)
        paste = _cap(QPushButton(tr("从剪贴板粘贴")))
        paste.setObjectName("btnSecondary")
        paste.clicked.connect(self._paste)
        ok = _cap(QPushButton(tr("用这些文案")))
        ok.setObjectName("btnPrimary")
        ok.clicked.connect(self.accept)
        cancel = _cap(QPushButton(tr("取消")))
        cancel.setObjectName("btnSecondary")
        cancel.clicked.connect(self.reject)
        row.addWidget(paste)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _paste(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from cutora.batch.script_stitcher import parse_sheet_clipboard

        if not self._keys:
            return
        cells = parse_sheet_clipboard(QApplication.clipboard().text())
        if not cells:
            QMessageBox.warning(self, tr("提示"), tr("剪贴板是空的。请先在表格里复制文案列"))
            return
        start = self.table.currentRow()
        if start < 0:
            start = 0
        for i, cell in enumerate(cells):
            row = start + i
            if row >= self.table.rowCount():
                break
            item = self.table.item(row, 1)
            if item is None:
                item = QTableWidgetItem("")
                self.table.setItem(row, 1, item)
            item.setText(cell)
            item.setToolTip(cell)

    def scripts(self) -> dict:
        out = {}
        for row, key in enumerate(self._keys):
            item = self.table.item(row, 1)
            out[key] = (item.text() if item else "").strip()
        return out


# Q131：拖总目录时往下找素材的层数。Luca：「不管目录有多深」—— 给个很深的上限
# 只为防循环链接 / 整盘拖进来时跑不完。
MATERIAL_SCAN_DEPTH = 64


class HomeStitchBand(QFrame):
    """Full-width stitch entry. Left is the drop list, right is the few knobs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("homeStitch")
        self.setMinimumWidth(380)
        self.setMinimumHeight(420)
        # 屏幕很宽时别让这一列无限长胖：再宽也只是空投放区变大，
        # 还不如把多出来的宽度还给最近项目。
        self.setMaximumWidth(880)
        self.setAcceptDrops(True)
        self._dropped: list[str] = []
        self._groups: dict = {}
        self._folder_of: dict = {}
        self._base_of: dict = {}
        self._notes: list[str] = []
        self._scripts: dict = {}
        self._pasted = False
        self._marks_loaded = False
        self._folder_mode = True
        self._session = None
        self._out_dir = ""
        self._out_dirs = {}
        self._missing_map = {}
        self._build()

    # ---------------------------------------------------------------- build
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_3, SPACE_3, SPACE_3, SPACE_3)
        root.setSpacing(SPACE_2)

        title = QLabel(tr("片段智能拼接"))
        title.setObjectName("sectionHeader")
        root.addWidget(title)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_work())
        self._stack.addWidget(self._build_result())
        root.addWidget(self._stack, 1)

    def _build_work(self) -> QWidget:
        # Q105: 这一块从「左右横排」改成「上下竖排」。它现在是首页右半边的
        # 一整列，投放区在上面长高，参数在下面一行一行铺开。
        page = QWidget()
        page.setObjectName("homeStitchWork")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_3)
        col.addWidget(self._build_left(), 1)
        col.addWidget(self._build_right(), 0)
        return page

    def _build_left(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)

        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(self._build_empty())
        self._left_stack.addWidget(self._build_table())
        lay.addWidget(self._left_stack, 1)
        return wrap

    def _build_empty(self) -> QWidget:
        well = _DropWell()
        lay = QVBoxLayout(well)
        lay.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        lay.setSpacing(SPACE_2)
        lay.addStretch(1)
        mark = QLabel()
        mark.setObjectName("homeStitchMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from PySide6.QtGui import QPixmap
            from cutora.core.paths import BRAND_DIR
            import os as _os
            _src = _os.path.join(BRAND_DIR, "icon_128.png")
            if _os.path.isfile(_src):
                pm = QPixmap(_src)
                if not pm.isNull():
                    mark.setPixmap(pm.scaled(
                        72, 72,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass
        lay.addWidget(mark)
        lay.addSpacing(SPACE_2)
        big = QLabel(tr("把要拼接的视频或文件夹拖到这里"))
        big.setObjectName("pageTitle")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setWordWrap(True)
        font = big.font()
        font.setPixelSize(FONT_PAGE)
        big.setFont(font)
        sub = QLabel(tr("支持多个文件夹；一个文件夹就是一组"))
        sub.setObjectName("hint")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(big)
        lay.addWidget(sub)
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_2)
        buttons.addStretch(1)
        self.btn_choose_folder = _cap(QPushButton(tr("选择文件夹…")), 180)
        self.btn_choose_folder.setObjectName("btnSecondary")
        self.btn_choose_folder.clicked.connect(self._choose_folder)
        self.btn_choose_videos = _cap(QPushButton(tr("选择视频…")), 180)
        self.btn_choose_videos.setObjectName("btnSecondary")
        self.btn_choose_videos.clicked.connect(self._choose_videos)
        buttons.addWidget(self.btn_choose_folder)
        buttons.addWidget(self.btn_choose_videos)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)
        return well

    def _build_table(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)
        head = QHBoxLayout()
        head.setSpacing(SPACE_2)
        self.lbl_count = QLabel(tr("共 0 组 · 0 条素材"))
        self.lbl_count.setObjectName("hint")
        head.addWidget(self.lbl_count, 1)
        self.btn_remove = _cap(QPushButton(tr("移除选中")), 120)
        self.btn_remove.setObjectName("btnSecondary")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear = _cap(QPushButton(tr("清空")), 80)
        self.btn_clear.setObjectName("btnSecondary")
        self.btn_clear.clicked.connect(self._clear)
        head.addWidget(self.btn_remove)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("homeStitchTable")
        self.table.setHorizontalHeaderLabels([tr("组"), tr("片段"), tr("文案"), tr("来源")])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 180)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemDoubleClicked.connect(self._reveal_row)
        lay.addWidget(self.table, 1)
        return wrap

    def _build_right(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("homeStitchParams")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)

        # 分组方式：两个按钮平分一行
        mode = QWidget()
        mode.setObjectName("homeStitchMode")
        mode_row = QHBoxLayout(mode)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(SPACE_1)
        lbl_mode = self._label(tr("分组方式"))
        lbl_mode.setFixedWidth(_LABEL_W)
        self.btn_mode_folder = QPushButton(tr("按文件夹"))
        self.btn_mode_prefix = QPushButton(tr("按文件名前缀"))
        self.btn_mode_folder.setObjectName("homeModeFolder")
        self.btn_mode_prefix.setObjectName("homeModePrefix")
        for btn in (self.btn_mode_folder, self.btn_mode_prefix):
            btn.setCheckable(True)
            btn.setMinimumHeight(_CTL_H)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
        self.btn_mode_folder.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.btn_mode_folder)
        self._mode_group.addButton(self.btn_mode_prefix)
        self.btn_mode_folder.toggled.connect(self._on_folder_toggled)
        self.btn_mode_prefix.toggled.connect(self._on_prefix_toggled)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.btn_mode_folder, 1)
        mode_row.addWidget(self.btn_mode_prefix, 1)
        lay.addWidget(mode)

        # 语言：标签和下拉同一行，标签列和上面对齐
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(SPACE_1)
        lbl_lang = self._label(tr("语言"))
        lbl_lang.setFixedWidth(_LABEL_W)
        self.cmb_lang = QComboBox()
        self.cmb_lang.setMinimumHeight(_CTL_H)
        self._fill_languages()
        self.cmb_lang.setMaximumWidth(220)
        lang_row.addWidget(lbl_lang)
        lang_row.addWidget(self.cmb_lang, 1)
        lang_row.addStretch(2)
        lay.addLayout(lang_row)

        # 粘贴文案 / 更多设置 并排
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(SPACE_2)
        self.btn_paste = QPushButton()
        self.btn_paste.setObjectName("btnSecondary")
        self.btn_paste.setMinimumHeight(_CTL_H)
        self.btn_paste.clicked.connect(self._open_paste)
        self.btn_more = QPushButton(tr("更多设置"))
        self.btn_more.setObjectName("btnSecondary")
        self.btn_more.setMinimumHeight(_CTL_H)
        self.btn_more.setCheckable(True)
        self.btn_more.toggled.connect(self._toggle_more)
        btn_row.addWidget(self.btn_paste, 2)
        btn_row.addWidget(self.btn_more, 1)
        lay.addLayout(btn_row)
        self._refresh_badge()
        lay.addWidget(self._build_more())

        self.btn_start = QPushButton(tr("开始拼接"))
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setMinimumHeight(_START_H)
        self.btn_start.clicked.connect(self._start)
        lay.addWidget(self.btn_start)

        self.bar = QProgressBar()
        self.bar.setObjectName("homeStitchProgress")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(SPACE_1)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        # 状态和「详细日志」各占一行的两端，不再互相挤
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(SPACE_2)
        self.lbl_status = QLabel(tr("把素材放进来，贴上文案，就可以开始"))
        self._on_lang_changed()
        self.lbl_status.setObjectName("homeStitchStatus")
        self.lbl_status.setWordWrap(False)
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Ignored,
                                      QSizePolicy.Policy.Preferred)
        self.btn_log = _cap(QPushButton(tr("详细日志")), 88)
        self.btn_log.setObjectName("homeStitchLink")
        self.btn_log.setFlat(True)
        self.btn_log.clicked.connect(self._open_console)
        status_row.addWidget(self.lbl_status, 1)
        status_row.addWidget(self.btn_log, 0)
        lay.addLayout(status_row)
        return panel

    def _build_more(self) -> QWidget:
        box = QWidget()
        box.setObjectName("homeStitchMore")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)
        pad_row = QHBoxLayout()
        pad_row.setSpacing(SPACE_2)
        _lbl_pad = self._label(tr("尾部留白"))
        _lbl_pad.setFixedWidth(_LABEL_W)
        pad_row.addWidget(_lbl_pad)
        self.spin_pad = QDoubleSpinBox()
        self.spin_pad.setRange(0.0, 2.0)
        self.spin_pad.setSingleStep(0.05)
        self.spin_pad.setValue(0.35)
        self.spin_pad.setSuffix(tr(" 秒"))
        # 数值框铺满整行既难看、右边的上下箭头也会被挤掉，给它一个体面的宽度
        self.spin_pad.setFixedWidth(140)
        self.spin_pad.setMinimumHeight(_CTL_H)
        pad_row.addWidget(self.spin_pad, 0)
        pad_row.addStretch(1)
        lay.addLayout(pad_row)
        self.chk_head = QCheckBox(tr("裁剪开头空白"))
        self.chk_head.setChecked(True)
        self.chk_edge = QCheckBox(tr("切口落在静音处"))
        self.chk_edge.setChecked(True)
        self.chk_edge.setToolTip(tr(
            "词尾之后一旦又有声音，就在它之前的静音里切断。"
            "余量够用时，切点和以前一样。"))
        self.chk_ignore_refs = QCheckBox(tr("括号里的经文出处不算缺失"))
        self.chk_ignore_refs.setChecked(True)
        self.chk_ignore_refs.setToolTip(tr(
            "括号里的经文出处（如 (John 8:34-35)）配音不会念，不列入缺失清单。"))
        for chk in (self.chk_head, self.chk_edge, self.chk_ignore_refs):
            lay.addWidget(chk)
        lay.addWidget(self._label(tr("分组前缀")))
        self.ed_group_mark = QLineEdit()
        self.ed_group_mark.setPlaceholderText(tr("留空=自动识别；例: Tik00x 或 Tik"))
        self.ed_group_mark.setEnabled(False)
        lay.addWidget(self.ed_group_mark)
        lay.addWidget(self._label(tr("序号前缀")))
        self.ed_order_mark = QLineEdit()
        self.ed_order_mark.setPlaceholderText(tr("留空=自动识别；例: v0x 或 v"))
        self.ed_order_mark.setEnabled(False)
        lay.addWidget(self.ed_order_mark)
        note = QLabel(tr("这两栏只在「按文件名前缀」时参与分组。"))
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        box.setVisible(False)
        self._more = box
        return box

    def _build_result(self) -> QWidget:
        page = QWidget()
        page.setObjectName("homeStitchResult")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)
        head = QLabel(tr("这些组的文案没拼全，时间线先不建"))
        head.setObjectName("sectionHeader")
        head.setWordWrap(True)
        lay.addWidget(head)
        hint = QLabel(tr("缺失清单写在素材旁边的 _stitched 里。补好片段或文案后再检查一次。"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.missing_list = QTableWidget(0, 2)
        self.missing_list.setObjectName("homeStitchMissing")
        self.missing_list.setHorizontalHeaderLabels([tr("组"), tr("缺了几处")])
        self.missing_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.missing_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.missing_list.verticalHeader().setVisible(False)
        self.missing_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.missing_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.missing_list, 1)

        refill_bar = QHBoxLayout()
        refill_bar.setSpacing(SPACE_2)
        refill_bar.addWidget(self._label(tr("缺片补生通道:")))
        self.cmb_missing_engine = QComboBox()
        self.cmb_missing_engine.addItem(tr("⚡ 优先使用付费购买账号补片 (Flow / Grok · 默认极速)"), "paid_flow_grok")
        self.cmb_missing_engine.addItem(tr("🖥️ 手动切换本地 AI 模型补片 (LTX-2.5 22B · 可选项)"), "local_ltx_flux")
        refill_bar.addWidget(self.cmb_missing_engine, 1)
        self.btn_refill_missing = _cap(QPushButton(tr("⚡ 一键提交缺片补生")))
        self.btn_refill_missing.setObjectName("btnPrimary")
        self.btn_refill_missing.clicked.connect(self._refill_missing_clips)
        refill_bar.addWidget(self.btn_refill_missing)
        lay.addLayout(refill_bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_2)
        self.btn_open_missing = _cap(QPushButton(tr("打开缺失清单所在文件夹")))
        self.btn_open_missing.setObjectName("btnSecondary")
        self.btn_open_missing.clicked.connect(self._open_missing_dir)
        self.btn_recheck = _cap(QPushButton(tr("补好了，重新检查")))
        self.btn_recheck.setObjectName("btnPrimary")
        self.btn_recheck.clicked.connect(self._recheck)
        buttons.addWidget(self.btn_open_missing)
        buttons.addWidget(self.btn_recheck)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        return page

    def _label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("hint")
        return lab

    def _fill_languages(self) -> None:
        from PySide6.QtGui import QStandardItemModel
        from cutora.asr.languages import (
            NONE_ALIGN_TOOLTIP, picker_entries,
        )
        from cutora.asr.models import language_capability
        self.cmb_lang.blockSignals(True)
        self.cmb_lang.clear()
        entries = picker_entries()
        for ent in entries:
            self.cmb_lang.addItem(ent["label"], ent["code"])
        model = self.cmb_lang.model()
        for i, ent in enumerate(entries):
            code = ent["code"]
            tip = ent.get("tooltip") or ""
            if code != "auto":
                cap = language_capability(code)
                if cap["align_tier"] == "none":
                    tip = tr(NONE_ALIGN_TOOLTIP)
                    if isinstance(model, QStandardItemModel):
                        item = model.item(i)
                        if item is not None:
                            item.setEnabled(False)
                            item.setToolTip(tip)
            if tip:
                self.cmb_lang.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        self.cmb_lang.setCurrentIndex(0)
        self.cmb_lang.blockSignals(False)
        if not getattr(self, "_lang_wired", False):
            self.cmb_lang.currentIndexChanged.connect(self._on_lang_changed)
            self._lang_wired = True
        self._on_lang_changed()

    def _selected_language(self) -> str:
        data = self.cmb_lang.currentData()
        if data:
            return str(data)
        return "auto"

    def _on_lang_changed(self, _idx=None) -> None:
        if not hasattr(self, "btn_start") or not hasattr(self, "lbl_status"):
            return
        from cutora.asr.models import language_capability
        code = self._selected_language()
        if code == "auto":
            self.btn_start.setEnabled(True)
            if getattr(self, "_lang_blocked", False):
                self._lang_blocked = False
                self.lbl_status.setText(tr("把素材放进来，贴上文案，就可以开始"))
            return
        cap = language_capability(code)
        if cap["align_tier"] == "none":
            self._lang_blocked = True
            self.btn_start.setEnabled(False)
            self.lbl_status.setText(tr(
                "{name} 能转写，但没有逐词时间对齐，拼不了。"
                "请改成「自动检测」，或选一种标着「逐词精确」的语言。",
                name=cap["name_zh"]))
            return
        self.btn_start.setEnabled(True)
        if getattr(self, "_lang_blocked", False):
            self._lang_blocked = False
            self.lbl_status.setText(tr("把素材放进来，贴上文案，就可以开始"))

    def _toggle_more(self, on: bool) -> None:
        self._more.setVisible(bool(on))
        if on:
            self._ensure_marks()

    # --------------------------------------------------------------- intake
    def _add_paths(self, paths) -> int:
        from cutora.batch.script_stitcher import VIDEO_EXTS

        have = {os.path.normcase(p) for p in self._dropped}
        added = 0
        for raw in paths or []:
            if not raw:
                continue
            path = os.path.normpath(str(raw))
            key = os.path.normcase(path)
            if key in have:
                continue
            if os.path.isdir(path):
                # Q131：拖进来的是总目录（B\T0081-…\clips\*.mp4 这种）时，不管哪种
                # 分组方式都自动往下找装着视频的文件夹；空镜头 / 成品 / _弃用 之类跳过。
                for sub in self._material_dirs(path):
                    sub_key = os.path.normcase(sub)
                    if sub_key in have:
                        continue
                    self._dropped.append(sub)
                    have.add(sub_key)
                    added += 1
                if key not in have and not self._material_dirs(path):
                    # 下面一个视频都没有：仍收下，好让状态栏说清楚「这里没有视频」
                    self._dropped.append(path)
                    have.add(key)
                    added += 1
            elif os.path.isfile(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                self._dropped.append(path)
                have.add(key)
                added += 1
        if added:
            self._stack.setCurrentIndex(0)
            self._rescan()
        elif paths:
            # 全是已经在表里的 / 不认识的文件：也要给个回应，别让人以为没点上
            self.lbl_status.setText(tr("这些已经在列表里了，或者不是视频"))
        return added

    @staticmethod
    def _material_dirs(root):
        """root 自己有视频就是它自己（老行为）；否则一直往下找装着视频的文件夹（不限层数，
        每个装视频的文件夹就是一组）。"""
        from cutora.batch.script_stitcher import VIDEO_EXTS, find_material_dirs
        try:
            names = os.listdir(root)
        except OSError:
            return []
        if any(os.path.splitext(n)[1].lower() in VIDEO_EXTS
               and os.path.isfile(os.path.join(root, n)) for n in names):
            return [os.path.normpath(root)]
        return [os.path.normpath(d) for d in find_material_dirs(root, max_depth=MATERIAL_SCAN_DEPTH)]

    def _inputs(self):
        folders, files = [], []
        folder_keys = set()
        for path in self._dropped:
            if os.path.isdir(path):
                folders.append(path)
                folder_keys.add(os.path.normcase(path))
        for path in self._dropped:
            if not os.path.isfile(path):
                continue
            parent = os.path.normcase(os.path.dirname(path))
            if parent in folder_keys:
                continue
            files.append(path)
        return folders, files

    def _marks(self):
        return (
            self.ed_group_mark.text().strip(),
            self.ed_order_mark.text().strip(),
        )

    def _ensure_marks(self) -> None:
        if self._marks_loaded:
            return
        from cutora.batch.script_stitcher import load_naming
        group_mark, order_mark = load_naming()
        self.ed_group_mark.setText(group_mark)
        self.ed_order_mark.setText(order_mark)
        self._marks_loaded = True

    def _rescan(self) -> None:
        from cutora.batch.script_stitcher import _gkey, _natkey, scan_groups_multi

        folders, files = self._inputs()
        if not self._folder_mode:
            self._ensure_marks()
        group_mark, order_mark = self._marks()
        groups, folder_of, base_of, notes = scan_groups_multi(
            folders, group_mark, order_mark, group_by_folder=self._folder_mode)
        if self._folder_mode:
            groups, folder_of, base_of, notes = self._relabel_generic(
                groups, folder_of, base_of, notes)
        if files:
            groups = dict(groups)
            folder_of = dict(folder_of)
            base_of = dict(base_of)
            notes = list(notes)
            self._merge_files(
                groups, folder_of, base_of, notes, files,
                group_mark, order_mark, _natkey)
            groups = {k: groups[k] for k in sorted(groups, key=_gkey)}
        self._groups = groups
        self._folder_of = folder_of
        self._base_of = base_of
        self._notes = list(notes)
        try:
            from cutora.batch.script_stitcher import StitcherDialog
            auto_filled = 0
            for k in self._groups:
                if not str(self._scripts.get(k) or "").strip():
                    ct = StitcherDialog._read_orante_copy_for_folder(self._folder_of.get(k, ""))
                    if ct:
                        self._scripts[k] = ct
                        auto_filled += 1
            if auto_filled or (self._groups and all(str(self._scripts.get(k) or "").strip() for k in self._groups)):
                self._pasted = True
        except Exception:
            pass
        self._refresh_table()

    @staticmethod
    def _relabel_generic(groups, folder_of, base_of, notes):
        """按文件夹分组时，叫 clips / 片段 的文件夹用上一级的名字当组名和成品名。

        自动往下找素材后，B 下面每个项目都有一个 clips，全叫「clips」、
        「clips(T0082…)」分不清。scan_groups_multi 本身不动（Q103 的约定），
        只在这里改显示和成品名；上级名重复时保持原样。
        """
        from cutora.batch.script_stitcher import _folder_label
        want = {}
        for key, folder in folder_of.items():
            label = _folder_label(folder)
            if label and label != os.path.basename(os.path.normpath(folder)):
                want[key] = label
        if not want:
            return groups, folder_of, base_of, notes
        taken = set(k for k in groups if k not in want)
        out_g, out_f, out_b = {}, {}, {}
        for key, paths in groups.items():
            new = want.get(key)
            if not new or new in taken:
                new = key
            taken.add(new)
            out_g[new] = paths
            out_f[new] = folder_of.get(key, "")
            out_b[new] = new if new != key else base_of.get(key, key)
        renamed = [k for k in groups if want.get(k) and want[k] in out_g]
        notes = [n for n in notes
                 if not any(("改叫 %s " % k) in n for k in renamed)]
        return out_g, out_f, out_b, notes

    def _merge_files(self, groups, folder_of, base_of, notes, files,
                     group_mark, order_mark, natkey):
        from cutora.batch.script_stitcher import scan_groups_multi

        if self._folder_mode:
            buckets = {}
            for path in files:
                buckets.setdefault(os.path.dirname(path), []).append(path)
            for parent, members in buckets.items():
                members.sort(key=lambda p: natkey(os.path.basename(p)))
                name = os.path.basename(parent) or parent
                label = os.path.basename(os.path.dirname(parent)) or parent
                key = _place_key(groups, name, label, notes)
                groups[key] = members
                folder_of[key] = parent
                base_of[key] = name
            return
        parents = []
        wanted = {os.path.normcase(os.path.normpath(p)) for p in files}
        for path in files:
            parent = os.path.dirname(path)
            if parent not in parents:
                parents.append(parent)
        extra, extra_folder, extra_base, extra_notes = scan_groups_multi(
            parents, group_mark, order_mark, group_by_folder=False)
        notes.extend(extra_notes)
        for key, paths in extra.items():
            kept = [p for p in paths if os.path.normcase(os.path.normpath(p)) in wanted]
            if not kept:
                continue
            label = os.path.basename(os.path.dirname(extra_folder.get(key, ""))) or key
            placed = key if key not in groups else _place_key(
                groups, extra_base.get(key, key), label, notes)
            groups[placed] = kept
            folder_of[placed] = extra_folder.get(key, os.path.dirname(kept[0]))
            base_of[placed] = extra_base.get(key, key)

    def _refresh_table(self) -> None:
        keys = list(self._groups)
        clips = sum(len(v) for v in self._groups.values())
        self.lbl_count.setText(tr("共 %d 组 · %d 条素材") % (len(keys), clips))
        self._refresh_badge()
        if not keys:
            self._left_stack.setCurrentIndex(0)
            if self._notes:
                self.lbl_status.setText(self._notes[0])
            elif self._dropped:
                self.lbl_status.setText(tr("这些里面没有可以拼接的视频"))
            return
        self._left_stack.setCurrentIndex(1)
        if self._notes:
            self.lbl_status.setText(self._notes[0])
        elif not self._pasted:
            self.lbl_status.setText(tr("贴上文案，就可以开始拼接"))
        self.table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            folder = self._folder_of.get(key, "")
            self._cell(row, 0, key, folder)
            self._cell(row, 1, str(len(self._groups[key])), folder)
            self._cell(row, 2, self._script_mark(key), folder)
            # Q105: 按文件夹分组时「组」本身就是文件夹名，这一列再写一遍
            # 等于白占地方。改成显示它的上级目录，一眼看出这组是哪来的。
            parent = os.path.dirname(folder.rstrip("\\/"))
            source = os.path.basename(parent) or parent or folder
            self._cell(row, 3, source, folder)
        if self._notes:
            self.lbl_status.setText(self._notes[0])

    def _cell(self, row, col, text, tip) -> None:
        item = QTableWidgetItem(text)
        if tip:
            item.setToolTip(tip)
        item.setData(Qt.ItemDataRole.UserRole, tip)
        self.table.setItem(row, col, item)

    def _script_mark(self, key: str) -> str:
        if not self._pasted:
            return _EM
        if str(self._scripts.get(key) or "").strip():
            return tr("✓ 已有")
        return tr("✗ 缺")

    def _refresh_badge(self) -> None:
        filled = 0
        if self._pasted:
            filled = sum(
                1 for key in self._groups
                if str(self._scripts.get(key) or "").strip())
        self.btn_paste.setText(tr("粘贴文案    已填 %d / 共 %d 组") % (
            filled, len(self._groups)))

    def _remove_selected(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        keys = list(self._groups)
        ban_files = set()
        selected = set()
        for row in rows:
            if 0 <= row < len(keys):
                selected.add(keys[row])
        by_folder = {}
        for key, folder in self._folder_of.items():
            by_folder.setdefault(os.path.normcase(folder), set()).add(key)
        ban_folders = set()
        for folder_key, group_keys in by_folder.items():
            if group_keys and group_keys <= selected:
                ban_folders.add(folder_key)
            else:
                for key in group_keys & selected:
                    for path in self._groups.get(key) or []:
                        ban_files.add(os.path.normcase(os.path.normpath(path)))
        kept = []
        for path in self._dropped:
            kind = os.path.normcase(os.path.normpath(path))
            if os.path.isdir(path):
                if kind in ban_folders:
                    continue
                if ban_files:
                    kept.extend(self._folder_files_except(path, ban_files))
                    continue
            if kind in ban_files or os.path.normcase(os.path.dirname(path)) in ban_folders:
                continue
            kept.append(path)
        self._dropped = self._dedupe(kept)
        self._rescan()

    def _folder_files_except(self, folder, ban_files):
        from cutora.batch.script_stitcher import VIDEO_EXTS, _natkey
        names = []
        try:
            entries = os.listdir(folder)
        except OSError:
            return []
        for name in entries:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                continue
            path = os.path.normpath(os.path.join(folder, name))
            if os.path.normcase(path) in ban_files:
                continue
            if os.path.isfile(path):
                names.append(name)
        names.sort(key=_natkey)
        return [os.path.normpath(os.path.join(folder, name)) for name in names]

    def _dedupe(self, paths):
        out, seen = [], set()
        for path in paths:
            path = os.path.normpath(path)
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        return out

    def _clear(self) -> None:
        self._dropped = []
        self._scripts = {}
        self._pasted = False
        self._groups = {}
        self._folder_of = {}
        self._base_of = {}
        self._notes = []
        self.lbl_status.setText(tr("把素材放进来，贴上文案，就可以开始"))
        self._refresh_table()

    def _reveal_row(self, item) -> None:
        import cutora.batch.script_stitcher as stitcher
        if item is None:
            return
        folder = item.data(Qt.ItemDataRole.UserRole)
        if folder:
            stitcher.reveal_in_explorer(folder)

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("选择要拼接的文件夹"))
        if path:
            self._add_paths([path])

    def _choose_videos(self) -> None:
        paths, _filt = QFileDialog.getOpenFileNames(
            self, tr("选择要拼接的视频"), "",
            tr("视频 (*.mp4 *.mov *.m4v *.mkv)"))
        if paths:
            self._add_paths(paths)

    def _on_folder_toggled(self, on: bool) -> None:
        if on:
            self._apply_mode(True)

    def _on_prefix_toggled(self, on: bool) -> None:
        if on:
            self._apply_mode(False)

    def _apply_mode(self, folder: bool) -> None:
        self.ed_group_mark.setEnabled(not folder)
        self.ed_order_mark.setEnabled(not folder)
        if folder == self._folder_mode:
            return
        self._folder_mode = folder
        if not folder:
            self._ensure_marks()
        if self._dropped:
            self._rescan()

    # ---------------------------------------------------------------- paste
    def make_paste_dialog(self) -> _PasteDialog:
        return _PasteDialog(list(self._groups), self._scripts, self)

    def apply_paste_table(self, table) -> None:
        mapping = {}
        for row, key in enumerate(self._groups):
            item = table.item(row, 1)
            mapping[key] = (item.text() if item else "").strip()
        self._commit_scripts(mapping)

    def _commit_scripts(self, mapping) -> None:
        self._scripts = {
            key: str((mapping or {}).get(key) or "").strip()
            for key in self._groups
        }
        self._pasted = True
        self._refresh_table()

    def _open_paste(self) -> None:
        if not self._groups:
            self.lbl_status.setText(tr("先把要拼接的视频或文件夹放进来"))
            return
        dialog = self.make_paste_dialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_paste_table(dialog.table)

    # ------------------------------------------------------------------ run
    def bind_session(self, session) -> None:
        self._session = session
        session.progress.connect(self._on_progress)
        session.log.connect(self._on_log)
        session.missing.connect(self._on_missing)
        session.preflight_ready.connect(self._on_preflight)
        session.analyzed.connect(self._on_analyzed)
        session.done.connect(self._on_done)
        session.failed.connect(self._on_failed)
        setter = getattr(session, "set_align_fetcher", None)
        if callable(setter):
            setter(self._fetch_align)

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        from cutora.asr.engine import get_align_model, get_whisperx_model
        from cutora.batch.stitch_session import StitchSession
        session = StitchSession(
            get_whisperx_model, get_align_model,
            parent=self, stop_on_missing=True)
        self.bind_session(session)
        return session

    def _params(self) -> dict:
        return {
            "language": self._selected_language(),
            "tail_pad": self.spin_pad.value(),
            "trim_head": self.chk_head.isChecked(),
            "edge_refine": self.chk_edge.isChecked(),
            "ignore_scripture_refs": self.chk_ignore_refs.isChecked(),
        }

    def _anchor_folders(self):
        folders = [p for p in self._dropped if os.path.isdir(p)]
        if folders:
            return folders
        files = [p for p in self._dropped if os.path.isfile(p)]
        if files:
            return [os.path.dirname(files[0])]
        return []

    def _start(self) -> None:
        session = self._session
        if session is not None and session.busy():
            session.stop()
            self.btn_start.setText(tr("正在停止..."))
            self._on_log(tr("正在停止..."))
            return
        from cutora.asr.models import language_capability
        code = self._selected_language()
        if code != "auto" and language_capability(code)["align_tier"] == "none":
            self._on_lang_changed()
            return
        if self._dropped:
            self._rescan()
        if not self._groups:
            self.lbl_status.setText(tr("还没有可以拼接的视频"))
            return
        if not self._pasted or any(
                not str(self._scripts.get(key) or "").strip() for key in self._groups):
            missing = [
                key for key in self._groups
                if not str(self._scripts.get(key) or "").strip()]
            if not self._pasted:
                self.lbl_status.setText(tr("先粘贴文案，每一组都要有"))
            else:
                self.lbl_status.setText(tr("还有 %d 组没有文案") % len(missing))
            return
        if not self._fetch_align(code):
            return
        from cutora.batch.stitch_session import assign_outputs
        scripts = {
            key: str(self._scripts.get(key) or "").strip() for key in self._groups}
        folders = self._anchor_folders()
        out_dirs, out_names, out_dir = assign_outputs(
            self._groups, self._folder_of, self._base_of, folders)
        self._out_dir = out_dir
        self._out_dirs = out_dirs
        session = self._ensure_session()
        session.set_job(
            self._groups, scripts, self._folder_of,
            out_dirs, out_names, out_dir, folders)
        self._stack.setCurrentIndex(0)
        self.btn_start.setEnabled(False)
        self.btn_start.setText(tr("体检中..."))
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self._on_log(
            "拼接前体检: 逐个验证 %d 个文件夹 / %d 组 / %d 个素材的文件头与可读性 ..."
            % (len(folders), len(self._groups),
               sum(len(v) for v in self._groups.values())))
        anchor = folders[0] if folders else ""
        self.pf_holder = session.start_preflight(
            anchor, self._groups, self._folder_of, quarantine_bad=True)

    def _fetch_align(self, code: str) -> bool:
        """Download this language's aligner when it is not on disk yet."""
        from cutora.ui.align_fetch_dialog import run_align_fetch
        ok, msg = run_align_fetch(self.window() or self, code)
        if msg:
            self._on_log(msg)
        return ok

    def _on_preflight(self, rep) -> None:
        from cutora.batch.stitch_session import preflight_continue
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.btn_start.setEnabled(True)
        self.btn_start.setText(tr("开始拼接"))
        self._on_log(rep.text())
        if not preflight_continue(self.window(), rep, self._on_log):
            self.bar.setVisible(False)
            return
        groups = self._session.groups
        self._on_log("阶段1/2 分析 %d 组素材 ..." % len(groups))
        self._session.launch_analyze(self._params())
        self.btn_start.setText(tr("停止"))

    def _on_progress(self, value: int) -> None:
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(int(value))

    def _on_log(self, text: str) -> None:
        line = str(text or "").strip()
        if not line:
            return
        self.lbl_status.setText(line.splitlines()[-1])
        try:
            from cutora.core.log import get_logger
            get_logger("cutora.batch.home_stitch").info("%s", line)
        except Exception:
            pass

    def _on_missing(self, missing_map) -> None:
        self._missing_map = dict(missing_map or {})
        self.bar.setVisible(False)
        self.btn_start.setEnabled(True)
        self.btn_start.setText(tr("开始拼接"))
        self.missing_list.setRowCount(len(self._missing_map))
        for row, (key, info) in enumerate(self._missing_map.items()):
            holes = (info or {}).get("missing") or []
            self.missing_list.setItem(row, 0, QTableWidgetItem(str(key)))
            self.missing_list.setItem(row, 1, QTableWidgetItem(str(len(holes))))
        self.lbl_status.setText(tr("有 %d 组文案没拼全，还不能做时间线") % len(self._missing_map))
        self._stack.setCurrentWidget(self._stack.widget(1))

    def _on_analyzed(self, cuts_map) -> None:
        missing = getattr(self._session, "missing_map", None) or {}
        if missing:
            self._on_missing(missing)
            return
        self.lbl_status.setText(tr("正在生成时间线…"))
        self._session.finish_timeline(cuts_map, self._canvas_wh())

    def _on_done(self, result) -> None:
        self.bar.setValue(100)
        self.btn_start.setEnabled(True)
        self.btn_start.setText(tr("开始拼接"))
        host = self.window()
        done = getattr(host, "_on_stitcher_done", None)
        if callable(done):
            done(result, True)
        show = getattr(host, "show_editor", None)
        if callable(show):
            show()

    def _on_failed(self, message) -> None:
        self.bar.setVisible(False)
        self.btn_start.setEnabled(True)
        self.btn_start.setText(tr("开始拼接"))
        self.lbl_status.setText(str(message or tr("拼接没有完成")))

    def _canvas_wh(self):
        host = self.window()
        ensure = getattr(host, "_ensure_timeline_template", None)
        if callable(ensure):
            try:
                ensure()
            except Exception:
                pass
        from cutora.batch.script_stitcher import StitcherDialog

        class _Host:
            def parent(_self):
                return host

        return StitcherDialog._project_canvas_wh(_Host())

    def _open_missing_dir(self) -> None:
        import cutora.batch.script_stitcher as stitcher
        folders = [p for p in self._dropped if os.path.isdir(p)]
        dirs = stitcher.missing_report_dirs(self._out_dir, self._out_dirs, folders)
        path = stitcher.find_missing_report(dirs)
        stitcher.reveal_in_explorer(path)

    def _recheck(self) -> None:
        self._stack.setCurrentIndex(0)
        self._start()

    def _refill_missing_clips(self) -> None:
        """一键将缺失片段提交补生：默认优先使用付费购买账号 (Flow / Grok)，也可手动切换本地 AI (LTX-2.5)。"""
        from PySide6.QtWidgets import QMessageBox
        from cutora.ui.ai_studio_room import _call_studio_gateway

        route = self.cmb_missing_engine.currentData() if hasattr(self, "cmb_missing_engine") else "paid_flow_grok"
        missing_keys = list((getattr(self, "_missing_map", None) or {}).keys())
        prompt_summary = " / ".join(str(k) for k in missing_keys[:6]) or "缺片镜头补生"
        res = _call_studio_gateway(
            "ltx25_generate",
            {
                "engine_route": route,
                "mode": "i2v",
                "prompt": f"补齐缺失片段: {prompt_summary}",
                "aspect": "16:9",
                "seconds": 5,
                "sync_audio": True,
            },
            self._on_log,
        )
        if res.get("ok"):
            QMessageBox.information(self, tr("缺片补生已提交"), str(res.get("message") or tr("已提交补生队列")))
        else:
            QMessageBox.warning(self, tr("提示"), str(res.get("error") or tr("补生提交未完成")))

    def _open_console(self) -> None:
        host = self.window()
        opener = getattr(host, "open_console", None)
        if callable(opener):
            opener()

    # ---------------------------------------------------------------- drops
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self._add_paths(paths)
        event.acceptProposedAction()

    # -------------------------------------------------------------- queries
    def group_count(self) -> int:
        return len(self._groups)

    def clip_count(self) -> int:
        return sum(len(v) for v in self._groups.values())

    def groups(self) -> dict:
        return dict(self._groups)
