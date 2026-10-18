# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q164 · 下拉菜单一展开就看到全部选项（检查器 / 停靠面板里以前少算十几像素，最后几项被挡住）。"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_combo_popups_show_every_item():
    from PySide6.QtWidgets import QApplication, QComboBox, QStyleFactory, QVBoxLayout, QWidget
    app = QApplication.instance() or QApplication(["q164"])
    app.setStyle(QStyleFactory.create("Fusion"))
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.combo_popup import MAX_VISIBLE, install
    apply_app_qss(app)
    install(app)
    install(app)                                   # 装两次也只装一个
    for name, n in (("PropertyPanel", 6), ("dockSubContent", 4), ("plain", 3), ("PropertyPanel", 20)):
        host = QWidget()
        host.setObjectName(name)
        lay = QVBoxLayout(host)
        cb = QComboBox()
        cb.addItems(["选项 %d" % i for i in range(n)])
        lay.addWidget(cb)
        host.resize(300, 200)
        host.show()
        app.processEvents()
        assert cb.maxVisibleItems() == MAX_VISIBLE
        cb.showPopup()
        for _ in range(3):
            app.processEvents()
        v = cb.view()
        rh = v.sizeHintForRow(0)
        assert v.viewport().height() >= rh * n - 1, (name, n, v.viewport().height(), rh)
        cb.hidePopup()
        host.close()
