# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-visible string wrapper. Phase 1 is identity (Chinese as-is)."""
from __future__ import annotations

# QDialogButtonBox / QMessageBox standard roles → Chinese (via tr()).
_BUTTON_LABELS = {
    "Ok": "确定",
    "Cancel": "取消",
    "Save": "保存",
    "Open": "打开",
    "Close": "关闭",
    "Apply": "应用",
    "Yes": "是",
    "No": "否",
    "Abort": "中止",
    "Retry": "重试",
    "Ignore": "忽略",
    "Discard": "丢弃",
    "Help": "帮助",
    "Reset": "重置",
}


def tr(text: str, **kwargs) -> str:
    """Return ``text``, optionally formatted with ``kwargs``.

    English UI is a later phase; call sites wrap strings now so they can
    be collected without a second pass.
    """
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def format_ui_number(value, digits: int = 1) -> str:
    """Format a number for on-screen labels (C locale, fixed decimals).

    Internal values stay as stored; this is display only.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    places = max(0, int(digits))
    if places == 0:
        return str(int(round(number)))
    return ("%." + str(places) + "f") % number


def localize_button_box(box) -> None:
    """Replace standard QDialogButtonBox labels with Chinese via ``tr()``."""
    from PySide6.QtWidgets import QDialogButtonBox

    roles = (
        (QDialogButtonBox.StandardButton.Ok, "Ok"),
        (QDialogButtonBox.StandardButton.Cancel, "Cancel"),
        (QDialogButtonBox.StandardButton.Save, "Save"),
        (QDialogButtonBox.StandardButton.Open, "Open"),
        (QDialogButtonBox.StandardButton.Close, "Close"),
        (QDialogButtonBox.StandardButton.Apply, "Apply"),
        (QDialogButtonBox.StandardButton.Yes, "Yes"),
        (QDialogButtonBox.StandardButton.No, "No"),
        (QDialogButtonBox.StandardButton.Abort, "Abort"),
        (QDialogButtonBox.StandardButton.Retry, "Retry"),
        (QDialogButtonBox.StandardButton.Ignore, "Ignore"),
        (QDialogButtonBox.StandardButton.Discard, "Discard"),
        (QDialogButtonBox.StandardButton.Help, "Help"),
        (QDialogButtonBox.StandardButton.Reset, "Reset"),
    )
    for role, key in roles:
        btn = box.button(role)
        if btn is not None:
            btn.setText(tr(_BUTTON_LABELS[key]))


def localize_message_box(box) -> None:
    """Replace standard QMessageBox button labels with Chinese via ``tr()``."""
    from PySide6.QtWidgets import QMessageBox

    roles = (
        (QMessageBox.StandardButton.Ok, "Ok"),
        (QMessageBox.StandardButton.Cancel, "Cancel"),
        (QMessageBox.StandardButton.Save, "Save"),
        (QMessageBox.StandardButton.Open, "Open"),
        (QMessageBox.StandardButton.Close, "Close"),
        (QMessageBox.StandardButton.Apply, "Apply"),
        (QMessageBox.StandardButton.Yes, "Yes"),
        (QMessageBox.StandardButton.No, "No"),
        (QMessageBox.StandardButton.Abort, "Abort"),
        (QMessageBox.StandardButton.Retry, "Retry"),
        (QMessageBox.StandardButton.Ignore, "Ignore"),
        (QMessageBox.StandardButton.Discard, "Discard"),
        (QMessageBox.StandardButton.Help, "Help"),
        (QMessageBox.StandardButton.Reset, "Reset"),
    )
    for role, key in roles:
        btn = box.button(role)
        if btn is not None:
            btn.setText(tr(_BUTTON_LABELS[key]))
