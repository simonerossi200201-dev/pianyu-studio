# -*- coding: utf-8 -*-
"""Offscreen screenshot of the Transcript panel with g09 overflow badge."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.core.runtime import init_torch_runtime

init_torch_runtime(render_child=False)

from cutora.app import apply_ui_locale

apply_ui_locale()

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QStyleFactory
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
from cutora.ui.main_window import MainWindow

G09 = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin", "project.asproj.json")
OUT = os.path.join(REPO, "docs", "plan", "reports", "img")


def _pump(app, ms=200):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    win.show_editor()
    _pump(app, 200)
    win.command_project_open(path=G09)
    _pump(app, 400)
    win.left_tabs.setCurrentWidget(win._tab_ops)
    win.transcript_inner.setCurrentWidget(win.transcript_panel)
    win.transcript_panel.reload()
    _pump(app, 200)
    path = os.path.join(OUT, "T04c_transcript.png")
    pix = win.dock_left.widget().grab()
    if not pix.save(path, "PNG"):
        raise SystemExit("failed %s" % path)
    print("wrote", path, pix.width(), "x", pix.height())
    cards = win.transcript_panel._cards
    if len(cards) >= 4:
        path2 = os.path.join(OUT, "T04c_overlong_badge.png")
        pix2 = cards[3].grab()
        pix2.save(path2, "PNG")
        print("wrote", path2, pix2.width(), "x", pix2.height(), cards[3].lbl_over.text())
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
