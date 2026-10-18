# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q196 · 图片节点连线铺满主窗口，并跟着窗口变大变小（与 Q194 视频连线导入同一套）。"""
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
    return QApplication.instance() or QApplication(["cutora-q196"])


def _pump(app, ms=30):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _almost(card, win):
    return (abs(card.width() - (win.width() - 16)) <= 2
            and abs(card.height() - (win.height() - 16)) <= 2)


def test_link_image_dialog_asks_to_fill():
    from cutora.ui.inwindow import placement_for
    from cutora.ui.link_image_dialog import LinkImageDialog

    _app()
    dlg = LinkImageDialog([])
    assert dlg.property("cutora_sheet") == "fill"
    assert placement_for(dlg) == "fill"
    assert dlg.minimumWidth() == 0
    assert dlg.minimumHeight() == 0
    dlg.close()


def test_link_image_sheet_matches_window_and_follows_resize():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget
    from cutora.ui.inwindow import OverlayHost
    from cutora.ui.link_image_dialog import LinkImageDialog

    app = _app()
    host = QWidget()
    host.resize(1600, 900)
    host.show()
    _pump(app)
    overlay = OverlayHost(host)

    dlg = LinkImageDialog([])
    dlg.setWindowFlags(Qt.WindowType.Widget)
    layer = overlay.push(dlg, "fill")
    _pump(app)

    assert _almost(layer.card.geometry(), host)
    host.resize(1100, 700)
    _pump(app)
    assert _almost(layer.card.geometry(), host)
    host.resize(1366, 768)
    _pump(app)
    assert _almost(layer.card.geometry(), host)
    host.close()
    dlg.close()
