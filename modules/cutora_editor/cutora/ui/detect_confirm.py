# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Low-confidence language confirm dialog (single-file auto only)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
)

from cutora.asr.languages import picker_entries
from cutora.ui.i18n import localize_button_box, tr


def ask_low_confidence(parent, detection: dict) -> str | None:
    """Return the language to use, or None if the user cancelled."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("确认识别语言"))
    lay = QVBoxLayout(dlg)
    lang = str(detection.get("language") or "")
    conf = detection.get("confidence")
    pct = ""
    try:
        pct = "（%.0f%%）" % (float(conf) * 100.0)
    except (TypeError, ValueError):
        pass
    name = detection.get("name_zh") or lang
    lay.addWidget(QLabel(tr("检测为 {name}{pct}，置信偏低，请确认。",
                            name=name, pct=pct)))
    combo = QComboBox()
    for ent in picker_entries():
        if ent["code"] == "auto":
            continue
        combo.addItem(ent["label"], ent["code"])
    idx = combo.findData(lang)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    lay.addWidget(combo)
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    localize_button_box(box)
    use_btn = box.button(QDialogButtonBox.StandardButton.Ok)
    if use_btn is not None:
        use_btn.setText(tr("仍然使用"))
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    data = combo.currentData()
    return str(data or lang)
