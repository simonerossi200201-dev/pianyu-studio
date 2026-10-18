# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""File-menu dialogs: split a project by video count, and the project export queue."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout,
)

from cutora.core.brand import PROJECT_FILTER

try:
    from cutora.batch import cli as batch_cli
except Exception:
    batch_cli = None



def _plan_text(n, chunk):
    if not batch_cli:
        return ""
    try:
        plan = batch_cli.split_plan(n, chunk)
    except Exception as e:
        return "无法预览：%s" % e
    if not plan:
        return "当前没有视频。"
    sizes = " / ".join(str(p["count"]) for p in plan)
    return "将拆成 %d 个工程（%s）" % (len(plan), sizes)


def _fill_credit_preset_combo(cmb, chk):
    cmb.clear()
    try:
        from cutora.subtitle.text_preset import list_presets
        rows = list_presets()
    except Exception:
        rows = []
    if not rows:
        cmb.addItem("（没有文本预设）", "")
        if chk is not None:
            chk.setEnabled(False)
        return
    if chk is not None:
        chk.setEnabled(True)
    for p in rows:
        label = str(p.get("name") or p.get("id"))
        if p.get("builtin"):
            label = "%s（内置）" % label
        cmb.addItem(label, p.get("id"))
    idx = cmb.findData("credit.default")
    if idx >= 0:
        cmb.setCurrentIndex(idx)


class SplitProjectDialog(QDialog):
    def __init__(self, parent, n_videos, default_chunk=40, default_outdir="", stem="split"):
        super().__init__(parent)
        self.setWindowTitle("自动拆分工程")
        self.setObjectName("SplitProjectDialog")
        self.setMinimumWidth(520)
        self._stem = stem or "split"
        self._n = int(n_videos or 0)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("当前工程有 %d 条视频。按列表顺序切成多个较小的工程文件，不改原工程。" % self._n))

        row = QHBoxLayout()
        row.addWidget(QLabel("每个工程条数"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 9999)
        self.spin.setValue(int(default_chunk or 40))
        self.spin.setMinimumWidth(90)
        row.addWidget(self.spin)
        row.addStretch(1)
        layout.addLayout(row)

        self.preview = QLabel(_plan_text(self._n, self.spin.value()))
        self.preview.setObjectName("hint")
        layout.addWidget(self.preview)
        self.spin.valueChanged.connect(self._refresh_preview)

        layout.addWidget(QLabel("输出目录"))
        dir_row = QHBoxLayout()
        self.outdir = QLineEdit(default_outdir or "")
        dir_row.addWidget(self.outdir, 1)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse)
        dir_row.addWidget(btn_browse)
        layout.addLayout(dir_row)

        hint = QLabel("文件名示例：%s_1-%d.asproj.json" % (self._stem, min(self._n, self.spin.value()) if self._n else 40))
        hint.setObjectName("hint")
        self._name_hint = hint
        layout.addWidget(hint)
        self.spin.valueChanged.connect(self._refresh_preview)

        self.chk_queue = QCheckBox("拆分后把这些工程加入工程队列")
        self.chk_auto_ai = QCheckBox("队列导出前先执行「一键全自动」加字幕并保存（拆分后还没有字幕）")
        self.chk_auto_ai.setChecked(True)
        self.chk_open = QCheckBox("拆分后打开第一个工程")
        layout.addWidget(self.chk_queue)
        layout.addWidget(self.chk_auto_ai)
        layout.addWidget(self.chk_open)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("拆分")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("btnPrimary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_preview(self, *_):
        self.preview.setText(_plan_text(self._n, self.spin.value()))
        n = min(self._n, self.spin.value()) if self._n else self.spin.value()
        self._name_hint.setText("文件名示例：%s_1-%d.asproj.json" % (self._stem, n))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.outdir.text() or "")
        if path:
            self.outdir.setText(path)

    def _accept(self):
        outdir = (self.outdir.text() or "").strip()
        if not outdir:
            QMessageBox.warning(self, "自动拆分工程", "请选择输出目录。")
            return
        if self._n <= 0:
            QMessageBox.warning(self, "自动拆分工程", "当前工程没有视频。")
            return
        self.accept()

    def values(self):
        return {
            "chunk": int(self.spin.value()),
            "outdir": os.path.abspath(self.outdir.text().strip()),
            "add_to_queue": bool(self.chk_queue.isChecked()),
            "auto_subtitle": bool(self.chk_auto_ai.isChecked()),
            "open_first": bool(self.chk_open.isChecked()),
            "stem": self._stem,
        }


class ProjectQueueDialog(QDialog):
    def __init__(self, parent=None, initial_paths=None, auto_subtitle=None,
                 auto_credit=None, credit_preset_id=None, highlight_credit=False):
        super().__init__(parent)
        self.setWindowTitle("自动署名" if highlight_credit else "工程队列")
        self.setMinimumSize(620, 460)
        self._want_start = False
        self._auto_subtitle_override = auto_subtitle
        self._auto_credit_override = auto_credit
        self._credit_preset_override = credit_preset_id

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel(
            "按顺序处理多个工程。每个工程可先「一键全自动」加字幕并保存，再导出；"
            "一份结束后软件会自动重启、打开下一个。"
        ))
        if highlight_credit:
            banner = QLabel(
                "勾选「自动署名」并选择文本预设。导出时会套到队列里每一条视频。"
                "默认关闭。同一预设重复跑不叠加。"
            )
            banner.setObjectName("hint")
            layout.addWidget(banner)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加工程…")
        btn_add.clicked.connect(self._add_files)
        btn_folder = QPushButton("添加文件夹…")
        btn_folder.clicked.connect(self._add_folder)
        btn_cur = QPushButton("加入当前工程")
        btn_cur.clicked.connect(self._add_current)
        btn_del = QPushButton("移除")
        btn_del.clicked.connect(self._remove)
        btn_up = QPushButton("上移")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("下移")
        btn_down.clicked.connect(lambda: self._move(1))
        for b in (btn_add, btn_folder, btn_cur, btn_del, btn_up, btn_down):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.chk_auto_ai = QCheckBox("导出前先执行「一键全自动」加字幕并保存")
        self.chk_auto_ai.setChecked(True)
        layout.addWidget(self.chk_auto_ai)
        credit_row = QHBoxLayout()
        self.chk_auto_credit = QCheckBox("自动署名：")
        self.chk_auto_credit.setChecked(False)
        self.cmb_credit = QComboBox()
        self._fill_credit_presets()
        credit_row.addWidget(self.chk_auto_credit)
        credit_row.addWidget(self.cmb_credit, 1)
        layout.addLayout(credit_row)
        credit_hint = QLabel("导出时套到队列里每一条视频。默认关闭。")
        credit_hint.setObjectName("hint")
        layout.addWidget(credit_hint)
        self.chk_restart = QCheckBox("每个工程结束后重启软件再继续（推荐，用来释放显存）")
        self.chk_restart.setChecked(True)
        layout.addWidget(self.chk_restart)

        action_row = QHBoxLayout()
        self.btn_stop = QPushButton("停止队列")
        self.btn_stop.clicked.connect(self._stop_queue)
        self.btn_start = QPushButton("开始队列")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.clicked.connect(self._start)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        action_row.addWidget(self.btn_stop)
        action_row.addStretch(1)
        action_row.addWidget(btn_close)
        action_row.addWidget(self.btn_start)
        layout.addLayout(action_row)

        if initial_paths:
            for p in initial_paths:
                self._add_path(p)
        else:
            self._load_from_saved_queue()
        if self._auto_subtitle_override is not None:
            self.chk_auto_ai.setChecked(bool(self._auto_subtitle_override))
        cid = str(self._credit_preset_override or "").strip()
        if cid:
            idx = self.cmb_credit.findData(cid)
            if idx >= 0:
                self.cmb_credit.setCurrentIndex(idx)
        if self._auto_credit_override is not None:
            self.chk_auto_credit.setChecked(
                bool(self._auto_credit_override) and bool(self.credit_preset_id()))
        self._refresh_status()

    def _fill_credit_presets(self):
        _fill_credit_preset_combo(self.cmb_credit, self.chk_auto_credit)

    def _add_path(self, path):
        if not path:
            return
        ap = os.path.abspath(path)
        if not os.path.isfile(ap):
            return
        key = os.path.normcase(ap)
        for i in range(self.list.count()):
            if os.path.normcase(self.list.item(i).data(Qt.ItemDataRole.UserRole) or "") == key:
                return
        item = QListWidgetItem(os.path.basename(ap))
        item.setToolTip(ap)
        item.setData(Qt.ItemDataRole.UserRole, ap)
        self.list.addItem(item)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择工程文件", "",
            PROJECT_FILTER)
        for p in paths or []:
            self._add_path(p)
        self._refresh_status()

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择含有工程文件的文件夹", "")
        if not folder or not batch_cli:
            return
        try:
            paths = batch_cli.collect_asproj_paths(folder, sort="name")
        except Exception as e:
            QMessageBox.warning(self, "工程队列", str(e))
            return
        for p in paths:
            self._add_path(p)
        self._refresh_status()

    def _add_current(self):
        parent = self.parent()
        path = getattr(parent, "project_path", None) if parent is not None else None
        if not path:
            QMessageBox.information(self, "工程队列", "当前工程还没有保存。请先保存项目，或直接添加已有的 .asproj.json。")
            return
        self._add_path(path)
        self._refresh_status()

    def _remove(self):
        for item in self.list.selectedItems():
            row = self.list.row(item)
            self.list.takeItem(row)
        self._refresh_status()

    def _move(self, delta):
        row = self.list.currentRow()
        if row < 0:
            return
        dest = row + int(delta)
        if dest < 0 or dest >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(dest, item)
        self.list.setCurrentRow(dest)

    def _load_from_saved_queue(self):
        if not batch_cli:
            return
        q = batch_cli.load_queue()
        if not q:
            return
        for it in q.get("items") or []:
            self._add_path(it.get("project"))
        opts = q.get("options") or {}
        if "restart_between" in opts:
            self.chk_restart.setChecked(bool(opts.get("restart_between")))
        if self._auto_subtitle_override is None and "auto_subtitle" in opts:
            self.chk_auto_ai.setChecked(bool(opts.get("auto_subtitle")))
        if opts.get("auto_credit"):
            self.chk_auto_credit.setChecked(True)
        cid = str(opts.get("credit_preset_id") or "")
        if cid:
            idx = self.cmb_credit.findData(cid)
            if idx >= 0:
                self.cmb_credit.setCurrentIndex(idx)

    def _refresh_status(self):
        n = self.list.count()
        extra = ""
        if batch_cli:
            s = batch_cli.queue_summary()
            if s.get("present") and s.get("total"):
                extra = "　磁盘队列：%d/%d" % (s.get("current") or 0, s.get("total") or 0)
                if s.get("active"):
                    extra += "　进行中" if not s.get("paused") else "　已暂停"
                counts = s.get("counts") or {}
                if counts.get("done"):
                    extra += "　已完成 %d" % counts["done"]
        self.status_label.setText("列表里有 %d 个工程。%s" % (n, extra))
        self.btn_start.setEnabled(n > 0)
        active = False
        if batch_cli:
            q = batch_cli.load_queue()
            active = bool(q and q.get("active") and not q.get("paused"))
        self.btn_stop.setEnabled(active)

    def _stop_queue(self):
        if batch_cli:
            batch_cli.pause_queue("user_stop")
        self._refresh_status()
        QMessageBox.information(self, "工程队列", "已停止。当前正在导出的工程仍需在主界面点「停止批量导出」。")

    def _start(self):
        if self.list.count() <= 0:
            QMessageBox.warning(self, "工程队列", "请先添加至少一个工程。")
            return
        missing = []
        for p in self.projects():
            if not os.path.isfile(p):
                missing.append(p)
        if missing:
            QMessageBox.warning(self, "工程队列", "这些工程文件不存在：\n" + "\n".join(missing[:8]))
            return
        n = self.list.count()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("开始工程队列")
        restart = self.chk_restart.isChecked()
        auto_ai = self.chk_auto_ai.isChecked()
        steps = "一键全自动加字幕 → 保存 → 导出" if auto_ai else "直接导出"
        extra = "每个工程结束后，软件会自动重启再打开下一个。" if restart else "将在同一进程内连续打开下一个工程（不重启）。"
        box.setText("将按顺序处理 %d 个工程。\n每个工程：%s。\n%s\n\n确定开始？" % (n, steps, extra))
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        from cutora.ui.i18n import localize_message_box
        localize_message_box(box)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        self._want_start = True
        self.accept()

    def want_start(self):
        return bool(self._want_start)

    def restart_between(self):
        return bool(self.chk_restart.isChecked())

    def auto_subtitle(self):
        return bool(self.chk_auto_ai.isChecked())

    def auto_credit(self):
        return bool(self.chk_auto_credit.isChecked()) and bool(self.credit_preset_id())

    def credit_preset_id(self):
        return str(self.cmb_credit.currentData() or "").strip()

    def projects(self):
        out = []
        for i in range(self.list.count()):
            p = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if p:
                out.append(p)
        return out


class AutoCreditDialog(QDialog):
    """批量 → 自动署名. Default OFF. Opens the project queue with the chosen preset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自动署名")
        self.setObjectName("AutoCreditDialog")
        self.setMinimumWidth(480)
        self._want_queue = False

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel(
            "选择文本预设。勾选后，工程队列导出时会套到每一条视频"
            "（每个视频一层，同一预设重复跑不叠加）。默认关闭。"
        ))

        row = QHBoxLayout()
        self.chk_auto_credit = QCheckBox("自动署名：")
        self.chk_auto_credit.setChecked(False)
        self.cmb_credit = QComboBox()
        _fill_credit_preset_combo(self.cmb_credit, self.chk_auto_credit)
        row.addWidget(self.chk_auto_credit)
        row.addWidget(self.cmb_credit, 1)
        layout.addLayout(row)

        hint = QLabel("只改队列选项，不会立刻改当前工程。接着打开工程队列即可开始。")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        buttons = QDialogButtonBox()
        self.btn_queue = buttons.addButton(
            "打开工程队列…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_queue.setObjectName("btnPrimary")
        buttons.accepted.connect(self._open_queue)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_queue(self):
        self._want_queue = True
        self.accept()

    def want_queue(self):
        return bool(self._want_queue)

    def auto_credit(self):
        return bool(self.chk_auto_credit.isChecked()) and bool(self.credit_preset_id())

    def credit_preset_id(self):
        return str(self.cmb_credit.currentData() or "").strip()
