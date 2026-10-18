# -*- coding: utf-8 -*-
"""Offscreen gallery + transcript font stills for T04e."""
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
    win.resize(1600, 1000)
    win.show()
    win.show_editor()
    win.set_ui_mode("quick")
    _pump(app, 250)
    if getattr(win, "style_gallery", None) is not None:
        win.tabs.setCurrentWidget(win.style_gallery)
        win.style_gallery.reload()
        _pump(app, 250)
        path = os.path.join(OUT, "T04e_gallery.png")
        pix = win.style_gallery.grab()
        if not pix.save(path, "PNG"):
            raise SystemExit("failed %s" % path)
        print("wrote", path, pix.width(), "x", pix.height())
        path2 = os.path.join(OUT, "T04e_quick.png")
        pix2 = win.grab()
        pix2.save(path2, "PNG")
        print("wrote", path2, pix2.width(), "x", pix2.height())
    win.command_project_open(path=G09)
    _pump(app, 400)
    win.left_tabs.setCurrentWidget(win._tab_ops)
    win.transcript_inner.setCurrentWidget(win.transcript_panel)
    win.transcript_panel.reload()
    _pump(app, 200)
    path3 = os.path.join(OUT, "T04e_transcript_font.png")
    pix3 = win.dock_left.widget().grab()
    pix3.save(path3, "PNG")
    fam = win.transcript_panel._cards[1].editor.font().family()
    print("wrote", path3, "editor family", fam)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
