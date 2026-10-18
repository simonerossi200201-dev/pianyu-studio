# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q194 · 连线导入铺满主窗口，并跟着窗口变大变小。普通对话框仍是中间的小卡片。"""
from __future__ import annotations

import os
import sys

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_GPU_FX", "0")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q194"])


def _pump(app, ms=30):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def test_fill_sheet_matches_window_and_follows_resize():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget
    from cutora.ui.inwindow import OverlayHost, placement_for

    app = _app()
    host = QWidget()
    host.resize(1600, 900)
    host.show()
    _pump(app)
    overlay = OverlayHost(host)

    dlg = QDialog()
    dlg.setProperty("cutora_sheet", "fill")
    dlg.setWindowTitle("连线导入")
    QVBoxLayout(dlg).addWidget(QLabel("画布"))
    assert placement_for(dlg) == "fill"
    dlg.setWindowFlags(Qt.WindowType.Widget)
    layer = overlay.push(dlg, "fill")
    _pump(app)

    def _almost(card, win):
        return (abs(card.width() - (win.width() - 16)) <= 2
                and abs(card.height() - (win.height() - 16)) <= 2)

    assert _almost(layer.card.geometry(), host)
    host.resize(1100, 700)
    _pump(app)
    assert _almost(layer.card.geometry(), host)
    host.close()


def test_center_sheet_stays_a_small_card():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget
    from cutora.ui.inwindow import OverlayHost

    app = _app()
    host = QWidget()
    host.resize(1600, 900)
    host.show()
    _pump(app)
    overlay = OverlayHost(host)
    dlg = QDialog()
    dlg.setWindowTitle("小提示")
    QVBoxLayout(dlg).addWidget(QLabel("一行"))
    dlg.setWindowFlags(Qt.WindowType.Widget)
    layer = overlay.push(dlg, "center")
    _pump(app)
    g = layer.card.geometry()
    assert g.width() < host.width() * 0.6
    assert g.height() < host.height() * 0.6
    host.close()


def test_link_import_dialog_asks_to_fill():
    from cutora.ui.link_import_dialog import LinkImportDialog

    _app()
    dlg = LinkImportDialog([], None)
    assert dlg.property("cutora_sheet") == "fill"
    dlg.close()
