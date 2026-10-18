# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings tab: 代理接口 (local API)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from cutora.api import DEFAULT_PORT
from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.ui.i18n import tr


class ApiSettingsPage(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        s = _load_app_settings() or {}
        lay = QVBoxLayout(self)
        from cutora.ui.widgets.collapsible import labeled_toggle
        self.chk_on_host, self.chk_on = labeled_toggle(tr("启用本地 API"))
        self.chk_on.setChecked(bool(s.get("api_enabled")))
        self.chk_on.toggled.connect(self._on_toggle)
        lay.addWidget(self.chk_on_host)

        form = QFormLayout()
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(int(s.get("api_port") or DEFAULT_PORT))
        form.addRow(tr("端口:"), self.sp_port)

        tok_row = QHBoxLayout()
        self.ed_token = QLineEdit()
        self.ed_token.setReadOnly(True)
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        tok_row.addWidget(self.ed_token, 1)
        btn_copy = QPushButton(tr("复制"))
        btn_copy.clicked.connect(self._copy_token)
        btn_rot = QPushButton(tr("重新生成"))
        btn_rot.clicked.connect(self._rotate)
        tok_row.addWidget(btn_copy)
        tok_row.addWidget(btn_rot)
        form.addRow(tr("Token:"), tok_row)
        lay.addLayout(form)

        lay.addWidget(QLabel(tr("已连接的客户端")))
        self.lst = QListWidget()
        lay.addWidget(self.lst, 1)
        btn_drop = QPushButton(tr("一键断开"))
        btn_drop.clicked.connect(self._disconnect_all)
        lay.addWidget(btn_drop)
        hint = QLabel(tr("只监听 127.0.0.1。浏览器页面调用会被拒绝。"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._reload_token()
        self._refresh_clients()

    def _reload_token(self) -> None:
        try:
            from cutora.api.token import ensure_token
            self.ed_token.setText(ensure_token())
        except Exception:
            self.ed_token.setText("")

    def _copy_token(self) -> None:
        QApplication.clipboard().setText(self.ed_token.text() or "")

    def _rotate(self) -> None:
        from cutora.api.token import rotate_token
        self.ed_token.setText(rotate_token())

    def _on_toggle(self, on: bool) -> None:
        s = _load_app_settings() or {}
        s["api_enabled"] = bool(on)
        s["api_port"] = int(self.sp_port.value())
        _save_app_settings(s)
        from cutora.api.lifecycle import start_api, stop_api
        if on:
            start_api(int(self.sp_port.value()))
        else:
            stop_api()
        if hasattr(self.host, "_refresh_api_led"):
            self.host._refresh_api_led()
        self._refresh_clients()

    def _refresh_clients(self) -> None:
        self.lst.clear()
        try:
            from cutora.api.lifecycle import get_server
            srv = get_server()
            peers = sorted(srv.clients) if srv is not None else []
        except Exception:
            peers = []
        for p in peers:
            self.lst.addItem(str(p))

    def _disconnect_all(self) -> None:
        from cutora.api.lifecycle import get_server, stop_api, start_api
        srv = get_server()
        port = int(self.sp_port.value())
        if srv is not None and srv.running:
            stop_api()
            if self.chk_on.isChecked():
                start_api(port)
        self._refresh_clients()
        if hasattr(self.host, "_refresh_api_led"):
            self.host._refresh_api_led()

    def save_prefs(self) -> None:
        s = _load_app_settings() or {}
        s["api_enabled"] = bool(self.chk_on.isChecked())
        s["api_port"] = int(self.sp_port.value())
        _save_app_settings(s)
