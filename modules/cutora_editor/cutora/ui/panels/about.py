# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""帮助 → 关于 片语 Studio / 字体许可。"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cutora.core.brand import (
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_VERSION,
    BUILD_DATE,
    PREDECESSOR,
    TAGLINE_EN,
    TAGLINE_ZH,
)
from cutora.core.paths import FONT_DIR, MARK_PNG, RESOURCES
from cutora.core.proc import run
from cutora.ui.i18n import localize_button_box, tr
from cutora.ui.theme.tokens import ACCENT, INFO

LICENSES_MD = os.path.join(RESOURCES, "THIRD_PARTY_LICENSES.md")
FONTS_MANIFEST = os.path.join(FONT_DIR, "FONTS_MANIFEST.json")


def _ffmpeg_version() -> str:
    from cutora.core.paths import FFMPEG
    try:
        proc = run(
            [FFMPEG, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return str(exc)
    line = (proc.stdout or proc.stderr or "").splitlines()
    return line[0] if line else "unknown"


def _gpu_reason() -> str:
    try:
        from cutora.core.config import detect_gpu_config
        _dev, _ct, reason = detect_gpu_config(verbose=False)
        return reason or "-"
    except Exception as exc:
        return str(exc)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("关于 %s" % APP_DISPLAY_NAME))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        head = QHBoxLayout()
        self._logo = QLabel(self)
        if os.path.isfile(MARK_PNG):
            pix = QPixmap(MARK_PNG)
            if not pix.isNull():
                self._logo.setPixmap(pix.scaled(
                    128, 96,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        head.addWidget(self._logo)
        en = (" · %s" % TAGLINE_EN) if TAGLINE_EN else ""
        self._names = QLabel(
            "<b style='font-size:17px'>%s</b><br/>"
            "<span style='color:%s;letter-spacing:0.15em'>%s</span><br/>"
            "v%s · %s<br/>"
            "%s<br/>"
            "<span style='color:%s'>%s%s</span>" % (
                APP_DISPLAY_NAME, ACCENT, APP_NAME, APP_VERSION, BUILD_DATE,
                PREDECESSOR, INFO, TAGLINE_ZH, en,
            ),
            self,
        )
        self._names.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self._names, 1)
        lay.addLayout(head)

        self._info = QLabel(
            "GPU：%s\nFFmpeg：%s\nPython：%s" % (
                _gpu_reason(),
                _ffmpeg_version(),
                sys.version.split()[0],
            ),
            self,
        )
        self._info.setWordWrap(True)
        lay.addWidget(self._info)
        self._emoji_font = QLabel(
            tr("贴纸：Noto Color Emoji（Google LLC，SIL OFL 1.1）。成片无署名义务。"),
            self,
        )
        self._emoji_font.setWordWrap(True)
        lay.addWidget(self._emoji_font)

        self.btn_third_party = QPushButton(tr("第三方授权"))
        self.btn_third_party.clicked.connect(self._open_licenses)
        lay.addWidget(self.btn_third_party)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        localize_button_box(box)
        box.accepted.connect(self.accept)
        lay.addWidget(box)

    def _open_licenses(self):
        dlg = ThirdPartyLicensesDialog(self)
        dlg.exec()


class ThirdPartyLicensesDialog(QDialog):
    """In-app viewer for the shipped third-party license index."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("第三方授权"))
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        lay.addWidget(self.view, 1)
        if not os.path.isfile(LICENSES_MD):
            self.view.setPlainText(tr("缺少 THIRD_PARTY_LICENSES.md"))
        else:
            text = open(LICENSES_MD, "r", encoding="utf-8").read()
            self.view.setPlainText(text)
            self._scroll_to("## Stickers")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        localize_button_box(box)
        box.accepted.connect(self.accept)
        lay.addWidget(box)

    def _scroll_to(self, needle: str) -> None:
        if not needle or needle not in self.view.toPlainText():
            return
        cursor = self.view.document().find(needle)
        if cursor.isNull():
            return
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()


class FontLicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("帮助 — 字体许可"))
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("内置字体仅允许 OFL-1.1 / Apache-2.0 / UFL-1.0 / Bitstream-Vera。")))
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels([tr("家族"), tr("许可"), tr("版本"), tr("文件")])
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(True)
        lay.addWidget(table, 1)
        try:
            from cutora.core import safe_json
            data = safe_json.load(FONTS_MANIFEST) if os.path.isfile(FONTS_MANIFEST) else {}
        except Exception:
            data = {}
        fonts = data.get("fonts") if isinstance(data, dict) else None
        rows = fonts if isinstance(fonts, list) else []
        table.setRowCount(len(rows))
        for i, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            table.setItem(i, 0, QTableWidgetItem(str(item.get("family") or "")))
            table.setItem(i, 1, QTableWidgetItem(str(item.get("license") or "")))
            table.setItem(i, 2, QTableWidgetItem(str(item.get("version") or "")))
            table.setItem(i, 3, QTableWidgetItem(str(item.get("file") or "")))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        localize_button_box(box)
        box.accepted.connect(self.accept)
        lay.addWidget(box)
