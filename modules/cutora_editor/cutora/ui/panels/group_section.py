# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 检查器里的「预合成组」一节：选中的片段在组里时才出现。

名字 / 颜色 / 锁定组内编辑 / 循环铺满到视频结尾 / 解除预合成。全部走命令总线（group.set /
group.ungroup），一次改动一次撤销。锁住时下面各节改的是「整组」——标题上写明白。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from cutora.core.log import get_logger
from cutora.model import clip_groups as CG
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import SPACE_1, SPACE_2
from cutora.ui.widgets.collapsible import CollapsibleSection
from cutora.ui.widgets.inspector import InspectForm, add_inspect_row

_LOG = get_logger("cutora.ui.panels.group_section")


class GroupSection(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._getter = None          # () -> (clip, cmd_params) 或 (None, None)
        self._loading = False
        self.gid = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sec = CollapsibleSection(tr("预合成组"))
        self.sec.setExpanded(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_1)
        self.info = QLabel("")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        form = InspectForm()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(tr("留空 = 自动编号"))
        self.ed_name.editingFinished.connect(lambda: self._set(name=self.ed_name.text().strip()))
        add_inspect_row(form, tr("组名"), self.ed_name)
        self.cb_color = QComboBox()
        for col in CG.GROUP_COLORS:
            pm = QPixmap(14, 14)
            pm.fill(QColor(col))
            self.cb_color.addItem(pm, col, col)
        self.cb_color.currentIndexChanged.connect(lambda _i: self._set(color=self.cb_color.currentData()))
        add_inspect_row(form, tr("颜色"), self.cb_color)
        self.chk_lock = QCheckBox(tr("锁定组内编辑（整组联动）"))
        self.chk_lock.setToolTip(tr("锁住：点一条选整组、拖一条整组走，下面改的每个参数整组一起变。\n解锁：可以单独改组里的某一条。"))
        self.chk_lock.toggled.connect(lambda on: self._set(locked=bool(on)))
        add_inspect_row(form, "", self.chk_lock)
        self.chk_loop = QCheckBox(tr("循环播放铺满到视频结尾"))
        self.chk_loop.setToolTip(tr("把整组当成一段视频，按组的总时长一遍一遍续接到视频结尾。\n续出来的那些带斜纹，组里一改它们自动重算。"))
        self.chk_loop.toggled.connect(lambda on: self._set(loop_fill=bool(on)))
        add_inspect_row(form, "", self.chk_loop)
        fw = QWidget()
        fw.setLayout(form)
        lay.addWidget(fw)
        row = QHBoxLayout()
        row.setSpacing(SPACE_2)
        self.btn_ungroup = QPushButton(tr("解除预合成"))
        self.btn_ungroup.setObjectName("btnSecondary")
        self.btn_ungroup.setToolTip(tr("片段留在原处，只是不再是一组（Ctrl+Shift+G）"))
        self.btn_ungroup.clicked.connect(self._ungroup)
        row.addWidget(self.btn_ungroup)
        row.addStretch(1)
        lay.addLayout(row)
        self.sec.addWidget(host)
        root.addWidget(self.sec)
        self.hide()

    def bind(self, getter):
        self._getter = getter

    def _target(self):
        if not callable(self._getter):
            return None, None
        try:
            return self._getter()
        except Exception:
            _LOG.exception("取预合成对象失败")
            return None, None

    def reload(self):
        clip, params = self._target()
        g = CG.group_of(clip) if clip is not None else None
        self.setVisible(bool(g and params))
        if not g:
            self.gid = ""
            return
        self.gid = g["id"]
        self._loading = True
        try:
            tl = self._tl()
            n = len(CG.members(tl, self.gid, repeats=False)) if tl is not None else 0
            n_rep = len(CG.members(tl, self.gid)) - n if tl is not None else 0
            lo, hi = CG.span(tl, self.gid) if tl is not None else (0.0, 0.0)
            idx = list(CG.groups(tl).keys()).index(self.gid) if tl is not None and self.gid in CG.groups(tl) else None
            self.sec.setTitle(tr("预合成组 · %s") % CG.display_name(g, idx))
            txt = tr("%d 条片段，组时长 %.2f 秒") % (n, max(0.0, hi - lo))
            if n_rep:
                txt += tr("，已循环续接 %d 份") % n_rep
            if g.get("repeat"):
                txt += tr("。这一条是循环复制出来的，改组里的真身它会跟着变。")
            elif g.get("locked", True):
                txt += tr("。锁住中：下面改的每个参数对整组生效。")
            else:
                txt += tr("。已解锁：下面改的只是这一条。")
            self.info.setText(txt)
            self.ed_name.setText(str(g.get("name") or ""))
            i = self.cb_color.findData(g.get("color"))
            self.cb_color.setCurrentIndex(max(0, i))
            self.chk_lock.setChecked(bool(g.get("locked", True)))
            self.chk_loop.setChecked(bool(g.get("loop_fill")))
        finally:
            self._loading = False

    def _tl(self):
        win = self.window()
        tasks = getattr(win, "tasks", None) or []
        ci = getattr(win, "curr_idx", -1)
        task = tasks[ci] if 0 <= ci < len(tasks) else None
        return task.get("timeline") if isinstance(task, dict) else None

    def _exec(self, name, extra) -> bool:
        clip, params = self._target()
        if clip is None or not params or not self.gid:
            return False
        payload = {k: v for k, v in dict(params).items() if k in ("media_id", "media", "project")}
        payload.update(extra)
        from cutora.core.commands import get_bus
        try:
            get_bus().execute(name, payload, source="ui")
        except Exception as exc:
            _LOG.exception("%s 失败", name)
            bar = getattr(self.window(), "statusBar", None)
            if callable(bar):
                try:
                    bar().showMessage("%s：%s" % (name, exc), 4000)
                except Exception:
                    pass
            return False
        win = self.window()
        for w in ("timeline", "video_canvas"):
            o = getattr(win, w, None)
            if o is not None and hasattr(o, "update"):
                o.update()
        self.changed.emit()
        return True

    def _set(self, **patch):
        if self._loading or not self.gid:
            return
        if self._exec("group.set", {"group": self.gid, "patch": patch}):
            self.reload()

    def _ungroup(self):
        if not self.gid:
            return
        if self._exec("group.ungroup", {"group": self.gid}):
            self.reload()
