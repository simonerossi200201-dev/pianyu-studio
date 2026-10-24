# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Welcome page shown when no project is open."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from cutora.ui.home_stitch import HomeStitchBand

from cutora.core.brand import APP_DISPLAY_NAME, TAGLINE_ZH
from cutora.core.history import _get_recent_projects
from cutora.core.paths import MARK_PNG
from cutora.model.canvas import CANVAS_PRESETS
from cutora.ui.i18n import tr
from cutora.ui.icons import bind_icon

# 左栏宽度：按钮不许拉满窗口宽度。最近项目吃掉剩下的所有空间。
ACTIONS_MIN_W = 320
ACTIONS_MAX_W = 380
H_PRIMARY = 46
H_SIZE = 34
H_OPEN = 38
H_LINK = 28


def _size_caption(w: int, h: int) -> str:
    """按宽高派生方向名，不写死 —— 预设改了这里跟着改。"""
    if h > w:
        facing = tr("竖屏")
    elif w > h:
        facing = tr("横屏")
    else:
        facing = tr("方形")
    return "%s %d×%d" % (facing, w, h)


class WelcomePage(QWidget):
    """Brand + new/open, recent list, 3-step cards along the bottom."""

    new_with_size = Signal(int, int)
    new_custom = Signal()
    open_project = Signal()
    open_recent = Signal(str)
    import_v6 = Signal()
    files_dropped = Signal(list)
    enter_editor = Signal()
    restore_backup = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WelcomePage")
        self.setAcceptDrops(True)
        self._size_buttons: dict[tuple[int, int], QPushButton] = {}
        self._split_ready = False
        self._build()
        self.reload_recent()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("welcomeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        host = QWidget()
        host.setObjectName("welcomeScrollHost")
        scroll.setWidget(host)
        viewport = scroll.viewport()
        viewport.setObjectName("welcomeViewport")
        outer.addWidget(scroll)
        self.scroll_area = scroll

        root = QVBoxLayout(host)
        root.setContentsMargins(40, 32, 40, 28)
        root.setSpacing(26)
        root.addLayout(self._brand_row())

        # Q105: 三栏并排 —— 左边操作、中间最近项目、右边拼接入口。
        # 原来拼接是通栏横条摆在最下面，右侧参数被压成窄条、状态文字和
        # 「详细日志」挤在一行，很难看。改成竖着占右半边之后，投放区
        # 能长高、参数能舒展开。
        recent = QWidget()
        recent.setObjectName("welcomeRecent")
        recent_lay = QVBoxLayout(recent)
        recent_lay.setContentsMargins(0, 0, 0, 0)
        recent_lay.setSpacing(12)
        recent_lay.addLayout(self._recent_col())

        self.home_stitch = HomeStitchBand()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("welcomeSplit")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        split.addWidget(self._actions_panel())
        split.addWidget(recent)
        split.addWidget(self.home_stitch)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 3)
        split.splitterMoved.connect(self._save_split)
        split.setSizes(self._restore_split())
        self._split = split
        root.addWidget(split, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_split_shown", False):
            return
        self._split_shown = True
        self._split_ready = False
        self._split.setSizes(self._restore_split())
        self._split_ready = True

    # 三栏宽度：操作栏不拉伸，剩下的宽度里拼接入口拿一半左右。
    _SPLIT_DEFAULT = [ACTIONS_MAX_W, 340, 780]

    def _restore_split(self):
        try:
            from cutora.core.history import _load_app_settings
            raw = (_load_app_settings() or {}).get("welcome_stitch_split")
            if isinstance(raw, (list, tuple)) and len(raw) == 3:
                cols = [int(v) for v in raw]
                if cols[0] >= ACTIONS_MIN_W and cols[1] >= 200 and cols[2] >= 380:
                    return cols
        except Exception:
            pass
        return list(self._SPLIT_DEFAULT)

    def _save_split(self, *_args) -> None:
        if not self._split_ready:
            return
        try:
            from cutora.core.history import _load_app_settings, _save_app_settings
            settings = _load_app_settings() or {}
            settings["welcome_stitch_split"] = [int(v) for v in self._split.sizes()]
            _save_app_settings(settings)
        except Exception:
            pass

    def _brand_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        mark = QLabel()
        mark.setObjectName("welcomeMark")
        pm = QPixmap(MARK_PNG)
        if not pm.isNull():
            mark.setPixmap(pm.scaledToHeight(
                48, Qt.TransformationMode.SmoothTransformation))
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr(APP_DISPLAY_NAME))
        title.setObjectName("pageTitle")
        sub = QLabel(tr(TAGLINE_ZH))
        sub.setObjectName("hint")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(title_col)
        row.addStretch(1)
        self.btn_enter_editor = QPushButton(tr("收起欢迎页 · 直接进入剪辑台  ✕"))
        self.btn_enter_editor.setObjectName("btnPrimary")
        self.btn_enter_editor.setFixedHeight(H_OPEN)
        self.btn_enter_editor.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_enter_editor.setToolTip(tr("关闭欢迎页并直接进入剪辑界面（快捷键 Esc，随时可从底部「欢迎页」图标重新打开）"))
        bind_icon(self.btn_enter_editor, "film", size=16)
        self.btn_enter_editor.clicked.connect(self.enter_editor.emit)
        row.addWidget(self.btn_enter_editor, 0, Qt.AlignmentFlag.AlignVCenter)
        self.mark_label = mark
        return row

    # ---------------- 左栏 ----------------

    def _make_size_button(self, w: int, h: int, primary: bool) -> QPushButton:
        if primary:
            btn = QPushButton(tr("新建项目") + " · " + _size_caption(w, h))
            btn.setObjectName("btnPrimary")
            btn.setFixedHeight(H_PRIMARY)
            bind_icon(btn, "file-plus", size=18)
        else:
            btn = QPushButton(_size_caption(w, h))
            btn.setObjectName("btnSecondary")
            btn.setFixedHeight(H_SIZE)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr("新建 {w}×{h} 工程", w=w, h=h))
        btn.clicked.connect(lambda _=False, _w=w, _h=h: self.new_with_size.emit(_w, _h))
        self._size_buttons[(w, h)] = btn
        return btn

    def _actions_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("welcomeActions")
        panel.setMinimumWidth(ACTIONS_MIN_W)
        panel.setMaximumWidth(ACTIONS_MAX_W)
        panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        left = QVBoxLayout(panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)

        head_new = QLabel(tr("新建项目"))
        head_new.setObjectName("sectionHeader")
        left.addWidget(head_new)

        presets = [(int(w), int(h)) for w, h, _lab in CANVAS_PRESETS]
        if not presets:
            presets = [(720, 1280)]
        first_w, first_h = presets[0]
        self.btn_portrait = self._make_size_button(first_w, first_h, primary=True)
        left.addWidget(self.btn_portrait)

        note = QLabel(tr("{c} 是最常用的短视频尺寸", c=_size_caption(first_w, first_h)))
        note.setObjectName("hint")
        left.addWidget(note)
        left.addSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(8)
        cells = []
        for w, h in presets[1:]:
            cells.append(self._make_size_button(w, h, primary=False))

        self.btn_custom = QPushButton(tr("自定义尺寸…"))
        self.btn_custom.setObjectName("btnSecondary")
        self.btn_custom.setFixedHeight(H_SIZE)
        self.btn_custom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_custom.clicked.connect(self.new_custom.emit)
        cells.append(self.btn_custom)

        for i, btn in enumerate(cells):
            grid.addWidget(btn, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        left.addLayout(grid)

        # 兼容旧引用：btn_landscape 指向第一个横向预设。
        landscape = [b for (w, h), b in self._size_buttons.items() if w > h]
        self.btn_landscape = landscape[0] if landscape else self.btn_portrait

        left.addSpacing(18)
        head_open = QLabel(tr("打开已有"))
        head_open.setObjectName("sectionHeader")
        left.addWidget(head_open)

        self.btn_open = QPushButton(tr("打开项目"))
        self.btn_open.setFixedHeight(H_OPEN)
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        bind_icon(self.btn_open, "folder-open", size=18)
        self.btn_open.clicked.connect(self.open_project.emit)
        left.addWidget(self.btn_open)

        self.btn_restore_backup = QPushButton(tr("恢复上次自动备份"))
        self.btn_restore_backup.setObjectName("btnSecondary")
        self.btn_restore_backup.setFixedHeight(H_OPEN)
        self.btn_restore_backup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restore_backup.setToolTip(tr("立即恢复并打开上次未保存或意外退出时的自动备份工程"))
        bind_icon(self.btn_restore_backup, "rotate-ccw", size=16)
        self.btn_restore_backup.clicked.connect(self.restore_backup.emit)
        left.addWidget(self.btn_restore_backup)

        # 次要入口：不跟「打开项目」等宽，贴着文字收窄，视觉上退一级。
        self.btn_v6 = QPushButton(tr("导入 AutoSub v6 设置"))
        self.btn_v6.setFixedHeight(H_LINK)
        self.btn_v6.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_v6.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.btn_v6.clicked.connect(self.import_v6.emit)
        v6_row = QHBoxLayout()
        v6_row.setContentsMargins(0, 0, 0, 0)
        v6_row.addWidget(self.btn_v6)
        v6_row.addStretch(1)
        left.addLayout(v6_row)

        left.addSpacing(14)
        drop = QFrame()
        drop.setObjectName("welcomeCard")
        drop.setMinimumHeight(120)
        drop.setMaximumHeight(240)
        drop_lay = QVBoxLayout(drop)
        drop_lay.setContentsMargins(12, 10, 12, 10)
        drop_hint = QLabel(tr("把视频拖到这里，将新建项目"))
        drop_hint.setObjectName("hint")
        drop_hint.setWordWrap(True)
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_lay.addStretch(1)
        drop_lay.addWidget(drop_hint)
        drop_lay.addStretch(1)
        # Q105: 底部 1/2/3 三张卡片去掉之后左栏会被拉得很长，
        # 拖拽框封个上限，下面留白，免得一个空框占掉半屏。
        left.addWidget(drop, 0)
        left.addStretch(1)
        self.actions_panel = panel
        return panel

    def _recent_col(self) -> QVBoxLayout:
        right = QVBoxLayout()
        right.setSpacing(8)
        recent_h = QLabel(tr("最近项目"))
        recent_h.setObjectName("sectionHeader")
        right.addWidget(recent_h)
        self.recent_list = QListWidget()
        self.recent_list.itemActivated.connect(self._on_recent)
        self.recent_list.itemDoubleClicked.connect(self._on_recent)
        right.addWidget(self.recent_list, 1)
        return right

    def reload_recent(self) -> None:
        try:
            from cutora.core.autosave import leftover_for, list_backups, untitled_leftover
            has_bak = bool(leftover_for(None) or untitled_leftover() or list_backups())
            if hasattr(self, "btn_restore_backup"):
                self.btn_restore_backup.setVisible(has_bak)
        except Exception:
            pass
        self.recent_list.clear()
        items = _get_recent_projects() or []
        if not items:
            empty = QListWidgetItem(tr("（没有最近项目）"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(empty)
            return
        for rec in items:
            path = rec.get("path") or ""
            ts = rec.get("timestamp") or 0
            try:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
            except Exception:
                when = ""
            label = "%s\n%s  %s" % (os.path.basename(path), when, path)
            item = QListWidgetItem(label)
            bind_icon(item, "clock", size=16, color_token="fg1")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.recent_list.addItem(item)

    def _on_recent(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not path:
            return
        now = time.monotonic()
        last = getattr(self, "_last_recent_emit", None)
        if last is not None and last[0] == str(path) and (now - last[1]) < 0.6:
            return
        self._last_recent_emit = (str(path), now)
        self.open_recent.emit(str(path))

    def dragEnterEvent(self, event) -> None:
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
            self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def keyPressEvent(self, event) -> None:
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.enter_editor.emit()
            event.accept()
            return
        super().keyPressEvent(event)
