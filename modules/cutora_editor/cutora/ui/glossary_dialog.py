# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project glossary editor. One term per line."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)

from cutora.asr.glossary import load_global_terms, parse_terms
from cutora.ui.i18n import localize_button_box, tr


class GlossaryDialog(QDialog):
    def __init__(self, host, parent=None):
        super().__init__(parent or host)
        self.host = host
        self.setWindowTitle(tr("术语表"))
        self.resize(440, 360)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("工程术语（一行一个：人名、地名、经文、专业词）")))
        self.edit = QPlainTextEdit()
        project = parse_terms(getattr(host, "project_glossary", None))
        self.edit.setPlainText("\n".join(project))
        lay.addWidget(self.edit, 1)
        glob = load_global_terms()
        hint = QLabel(
            tr("全局术语 {n} 条（USER_DATA/presets/glossary.txt）会一并送进识别。",
               n=len(glob))
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        localize_button_box(box)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _save(self) -> None:
        terms = parse_terms(self.edit.toPlainText())
        self.host.bus.execute(
            "glossary.set",
            {"scope": "project", "terms": terms},
            source="ui",
        )
        self.accept()
