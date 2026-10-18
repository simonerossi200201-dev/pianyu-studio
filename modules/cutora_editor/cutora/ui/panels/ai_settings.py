# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""设置 → AI 提供商。密钥只进凭据库，界面只显示已保存/未配置。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cutora.ai import prefs as ai_prefs
from cutora.ai.providers import all_providers, get_provider
from cutora.core import secrets as secrets
from cutora.core.log import get_logger
from cutora.ui.i18n import localize_button_box, tr

logger = get_logger("cutora.ui.ai_settings")


class AIProvidersPage(QWidget):
    """Provider rows; used both as a dialog body and a settings tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = {}
        self._build()
        self._load()

    def _build(self):
        lay = QVBoxLayout(self)
        hint = QLabel(tr("密钥保存在 Windows 凭据管理器（服务名 CUTORA），不会写入 JSON。找不到凭据库时拒绝保存。"))
        hint.setWordWrap(True)
        lay.addWidget(hint)

        from cutora.ui.widgets.collapsible import labeled_toggle
        self.chk_net_host, self.chk_net = labeled_toggle(
            tr("允许 AI 功能联网（默认关闭；关闭时所有 AI 请求会被拒绝）"))
        lay.addWidget(self.chk_net_host)

        form = QFormLayout()
        self.cmb_default = QComboBox()
        for p in all_providers():
            self.cmb_default.addItem(p.name, p.id)
        form.addRow(tr("默认提供商:"), self.cmb_default)
        self.sp_ollama = QSpinBox()
        self.sp_ollama.setRange(1, 65535)
        self.sp_ollama.setValue(11434)
        form.addRow(tr("Ollama 端口（主机固定 127.0.0.1）:"), self.sp_ollama)
        lay.addLayout(form)

        for p in all_providers():
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            status = QLabel(tr("未配置"))
            ed = QLineEdit()
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setPlaceholderText(tr("输入后立即保存") if p.id != "ollama" else tr("本机，无需密钥"))
            ed.setEnabled(p.id != "ollama")
            model = QComboBox()
            model.setEditable(True)
            model.setMinimumWidth(180)
            btn = QPushButton(tr("测试连接"))
            h.addWidget(QLabel(p.name), 0)
            h.addWidget(status, 0)
            h.addWidget(ed, 1)
            h.addWidget(model, 0)
            h.addWidget(btn, 0)
            lay.addWidget(row)
            self._rows[p.id] = {
                "status": status,
                "edit": ed,
                "model": model,
                "test": btn,
                "provider": p,
            }
            ed.editingFinished.connect(lambda pid=p.id: self._save_key(pid))
            btn.clicked.connect(lambda _=False, pid=p.id: self._test(pid))

    def save_prefs(self) -> None:
        for pid in self._rows:
            self._save_key(pid)
        ai_prefs.save_ai_prefs(self._collect_prefs())

    def _load(self):
        prefs = ai_prefs.get_ai_prefs()
        self.chk_net.setChecked(bool(prefs.get("ai_network_enabled")))
        idx = self.cmb_default.findData(prefs.get("ai_default_provider") or "gemini")
        self.cmb_default.setCurrentIndex(max(0, idx))
        self.sp_ollama.setValue(int(prefs.get("ollama_port") or 11434))
        models = prefs.get("ai_models") or {}
        for pid, widgets in self._rows.items():
            secret = ai_prefs.secret_name_for(pid)
            stored = bool(secret and secrets.get(secret))
            if pid == "ollama":
                widgets["status"].setText(tr("本机"))
            elif stored:
                widgets["status"].setText(tr("••••已保存"))
                widgets["edit"].setPlaceholderText(tr("••••已保存（输入新值覆盖）"))
            else:
                widgets["status"].setText(tr("未配置"))
            combo = widgets["model"]
            combo.clear()
            fallback = list(widgets["provider"].list_models() or [])
            for m in fallback:
                combo.addItem(m)
            current = models.get(pid) or (fallback[0] if fallback else "")
            combo.setCurrentText(str(current))

    def _save_key(self, pid: str):
        widgets = self._rows[pid]
        secret = ai_prefs.secret_name_for(pid)
        if not secret:
            return
        text = widgets["edit"].text().strip()
        if not text:
            return
        try:
            secrets.set(secret, text)
        except secrets.SecretsError as exc:
            QMessageBox.warning(self, tr("无法保存密钥"), str(exc))
            return
        widgets["edit"].clear()
        widgets["edit"].setPlaceholderText(tr("••••已保存（输入新值覆盖）"))
        widgets["status"].setText(tr("••••已保存"))

    def _collect_prefs(self) -> dict:
        models = {}
        for pid, widgets in self._rows.items():
            models[pid] = widgets["model"].currentText().strip()
        return {
            "ai_network_enabled": self.chk_net.isChecked(),
            "ai_default_provider": self.cmb_default.currentData() or "gemini",
            "ai_models": models,
            "ollama_port": int(self.sp_ollama.value()),
        }

    def _test(self, pid: str):
        self._save_key(pid)
        ai_prefs.save_ai_prefs(self._collect_prefs())
        try:
            text = get_provider(pid).chat(
                [{"role": "user", "content": "回复 OK"}],
                timeout=30,
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("测试失败"), str(exc))
            return
        QMessageBox.information(self, tr("测试成功"), (text or "").strip()[:400] or tr("已连通"))


class AIProvidersDialog(QDialog):
    """One row per provider: status, key field, model, test button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("设置 — AI 提供商"))
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        self.page = AIProvidersPage(self)
        lay.addWidget(self.page)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        localize_button_box(buttons)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_ok(self):
        self.page.save_prefs()
        self.accept()
