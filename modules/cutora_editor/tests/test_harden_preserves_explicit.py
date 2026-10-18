# -*- coding: utf-8 -*-
"""BUG-28: harden_docks / harden_widget must not overwrite signed attributes."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CTA = "用这段文案做自动字幕"


def test_harden_preserves_explicit():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QDockWidget,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )
    from cutora.ui.widgets.responsive import harden_docks, harden_widget

    app = QApplication.instance() or QApplication(["t07-harden"])

    win = QMainWindow()
    dock = QDockWidget("检查器", win)
    dock.setObjectName("dockRight")
    inner = QWidget()
    dock.setWidget(inner)
    win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    win.dock_right = dock
    dock.setMaximumWidth(420)
    harden_docks(win)
    assert dock.maximumWidth() == 420, dock.maximumWidth()

    page_root = QWidget()
    page_lay = QVBoxLayout(page_root)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    page = QWidget()
    page.setMaximumWidth(420)
    scroll.setWidget(page)
    page_lay.addWidget(scroll)
    page_root.resize(200, 200)
    page_root.show()
    app.processEvents()
    harden_widget(page_root)
    page_root.resize(160, 200)
    app.processEvents()
    assert page.maximumWidth() == 420, page.maximumWidth()

    cta_root = QWidget()
    cta_lay = QVBoxLayout(cta_root)
    cta = QPushButton(CTA)
    cta.setProperty("_cutora_explicit_text", True)
    cta_lay.addWidget(cta)
    cta_root.resize(400, 80)
    cta_root.show()
    app.processEvents()
    harden_widget(cta_root)
    cta_root.resize(72, 80)
    cta.resize(56, 28)
    app.processEvents()
    assert cta.text() == CTA, cta.text()
