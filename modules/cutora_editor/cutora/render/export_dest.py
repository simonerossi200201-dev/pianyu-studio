# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T08e: optional clean export folder for mp4 only; sidecars stay at the source."""
from __future__ import annotations

import os

from cutora.core.history import _load_app_settings, _save_app_settings

SETTING_ASK = "export.ask_dest"
SETTING_LAST = "export.last_dir"


def export_ask_dest() -> bool:
    s = _load_app_settings() or {}
    return s.get(SETTING_ASK, True) is not False


def set_export_ask_dest(on: bool) -> None:
    s = _load_app_settings() or {}
    s[SETTING_ASK] = bool(on)
    _save_app_settings(s)


def last_export_dir() -> str:
    s = _load_app_settings() or {}
    return str(s.get(SETTING_LAST) or "")


def set_last_export_dir(path: str) -> None:
    s = _load_app_settings() or {}
    s[SETTING_LAST] = str(path or "")
    _save_app_settings(s)


def unique_mp4_path(folder: str, source_video: str, suffix: str = "_FINAL",
                    name: str | None = None) -> str:
    """file name = task name or source stem + suffix; collisions get _2, _3, …"""
    os.makedirs(folder, exist_ok=True)
    raw = str(name or "").strip()
    stem = raw or os.path.splitext(os.path.basename(source_video or "out.mp4"))[0]
    base = stem + suffix
    dest = os.path.join(folder, base + ".mp4")
    if not os.path.exists(dest):
        return dest
    n = 2
    while True:
        dest = os.path.join(folder, "%s_%d.mp4" % (base, n))
        if not os.path.exists(dest):
            return dest
        n += 1


def sidecar_base_path(task: dict, output_path: str) -> str:
    """Sidecars (SRT / export.json / cover) sit next to the source video."""
    src = (task or {}).get("video") or (task or {}).get("path") or ""
    if src:
        folder = os.path.dirname(os.path.abspath(src))
        stem = os.path.splitext(os.path.basename(output_path or src))[0]
        return os.path.join(folder, stem)
    return os.path.splitext(output_path)[0]


def prompt_export_folder(parent=None):
    """Yes → pick folder. No → None (in-place). Cancel → False."""
    from PySide6.QtWidgets import QMessageBox, QFileDialog

    if not export_ask_dest():
        return None
    box = QMessageBox(parent)
    box.setWindowTitle("指定导出文件夹？")
    box.setText("项目里全部视频都导出到这个文件夹。文件夹里只放导出的视频文件。")
    no_btn = box.addButton("否：就地", QMessageBox.ButtonRole.NoRole)
    yes_btn = box.addButton("是：选择…", QMessageBox.ButtonRole.YesRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(no_btn)
    box.exec()
    clicked = box.clickedButton()
    if clicked is no_btn:
        return None
    if clicked is not yes_btn:
        return False
    start = last_export_dir() or os.path.expanduser("~")
    folder = QFileDialog.getExistingDirectory(parent, "选择成品文件夹", start)
    if not folder:
        return False
    set_last_export_dir(folder)
    return folder
