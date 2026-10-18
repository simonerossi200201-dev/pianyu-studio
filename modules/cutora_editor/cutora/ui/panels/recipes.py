# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe recorder editor and apply-to-folder progress dialog."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from cutora.batch import recipe as recipe_mod
from cutora.core.commands import get_bus
from cutora.ui.i18n import tr


class RecipeEditorDialog(QDialog):
    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("保存配方"))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("名称")))
        self.ed_name = QLineEdit("未命名配方")
        layout.addWidget(self.ed_name)
        layout.addWidget(QLabel(tr("说明")))
        self.ed_desc = QTextEdit()
        self.ed_desc.setMaximumHeight(80)
        layout.addWidget(self.ed_desc)
        self.ed_steps = QTextEdit()
        import json
        self.ed_steps.setPlainText(json.dumps(steps, ensure_ascii=False, indent=2))
        layout.addWidget(self.ed_steps)
        row = QHBoxLayout()
        btn = QPushButton(tr("保存"))
        btn.clicked.connect(self._save)
        row.addWidget(btn)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)

    def _save(self):
        import json
        try:
            steps = json.loads(self.ed_steps.toPlainText() or "[]")
        except Exception as exc:
            QMessageBox.warning(self, tr("配方"), str(exc))
            return
        data = {
            "schema_version": 1,
            "name": self.ed_name.text().strip() or "未命名配方",
            "description": self.ed_desc.toPlainText().strip(),
            "inputs": {
                "media": {"description": "视频"},
                "out_dir": {"description": "输出目录"},
            },
            "steps": steps,
        }
        path, _ = QFileDialog.getSaveFileName(
            self, tr("保存配方"), "", "Recipe (*.cutorarecipe)")
        if not path:
            return
        if not path.endswith(".cutorarecipe"):
            path += ".cutorarecipe"
        try:
            recipe_mod.save_recipe(path, data)
        except recipe_mod.RecipeError as exc:
            QMessageBox.warning(self, tr("配方"), str(exc))
            return
        self.accept()


class RecipeApplyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("套用配方到文件夹"))
        self.resize(720, 420)
        self._rows = []
        layout = QVBoxLayout(self)
        self.ed_recipe = QLineEdit()
        self.ed_folder = QLineEdit()
        self.ed_out = QLineEdit()
        for label, edit, pick in (
            (tr("配方"), self.ed_recipe, self._pick_recipe),
            (tr("素材文件夹"), self.ed_folder, self._pick_folder),
            (tr("输出目录"), self.ed_out, self._pick_out),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(edit)
            btn = QPushButton(tr("浏览…"))
            btn.clicked.connect(pick)
            row.addWidget(btn)
            layout.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr("文件"), tr("状态"), tr("耗时"), tr("错误")])
        layout.addWidget(self.table)
        run = QPushButton(tr("开始"))
        run.clicked.connect(self._run)
        layout.addWidget(run)
        from cutora.api.events import HUB
        self._hub = HUB
        HUB.subscribe_sync(self._on_event)

    def closeEvent(self, event):
        try:
            self._hub.unsubscribe_sync(self._on_event)
        except Exception:
            pass
        super().closeEvent(event)

    def _pick_recipe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择配方"), "", "Recipe (*.cutorarecipe)")
        if path:
            self.ed_recipe.setText(path)

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("素材文件夹"))
        if path:
            self.ed_folder.setText(path)

    def _pick_out(self):
        path = QFileDialog.getExistingDirectory(self, tr("输出目录"))
        if path:
            self.ed_out.setText(path)

    def _on_event(self, payload):
        if payload.get("type") != "progress":
            return

    def _upsert(self, row_data):
        name = row_data.get("file") or ""
        for i, old in enumerate(self._rows):
            if old.get("file") == name:
                self._rows[i] = row_data
                self._fill()
                return
        self._rows.append(row_data)
        self._fill()

    def _fill(self):
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            vals = [
                row.get("file") or "",
                row.get("status") or "",
                "%.2fs" % float(row.get("elapsed_sec") or 0),
                (row.get("error") or "")[:80],
            ]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))

    def _run(self):
        recipe = self.ed_recipe.text().strip()
        folder = self.ed_folder.text().strip()
        out_dir = self.ed_out.text().strip()
        if not recipe or not folder or not out_dir:
            QMessageBox.warning(self, tr("配方"), tr("请填写配方、文件夹和输出目录"))
            return
        try:
            data = recipe_mod.load_recipe(recipe)
            report = recipe_mod.apply_to_folder(
                data, folder, out_dir, on_file=self._upsert)
        except Exception as exc:
            QMessageBox.warning(self, tr("配方"), str(exc))
            return
        QMessageBox.information(
            self, tr("配方"),
            tr("完成：成功 {ok} / 跳过 {skip} / 失败 {fail}").format(
                **report.get("counts") or {"ok": 0, "skip": 0, "fail": 0}))


def open_apply_dialog(parent=None):
    dlg = RecipeApplyDialog(parent)
    dlg.exec()


def start_recording(parent=None):
    get_bus().execute("recipe.record_start", {}, source="cli")
    QMessageBox.information(
        parent, tr("配方"),
        tr("已开始录制。可跨视频重放的操作会记入配方；与当前媒体绑定的操作会跳过。"),
    )


def stop_recording(parent=None):
    result = get_bus().execute("recipe.record_stop", {}, source="cli")
    steps = result.get("steps") or []
    dlg = RecipeEditorDialog(steps, parent)
    dlg.exec()
