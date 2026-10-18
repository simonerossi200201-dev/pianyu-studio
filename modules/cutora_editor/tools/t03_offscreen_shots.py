# -*- coding: utf-8 -*-
"""Grab T03 shell screenshots on the offscreen Qt platform.

    runtime/venv/Scripts/python.exe tools/t03_offscreen_shots.py
"""
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

from PySide6.QtWidgets import QApplication, QStyleFactory
from PySide6.QtCore import QEventLoop, QTimer
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
from cutora.ui.main_window import MainWindow
from cutora.ui.settings_dialog import SettingsDialog

OUT = os.path.join(REPO, "docs", "plan", "reports", "img")


def _pump(app, ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _save(widget, name: str) -> str:
    path = os.path.join(OUT, name)
    pix = widget.grab()
    ok = pix.save(path, "PNG")
    if not ok:
        raise SystemExit("failed to save %s" % path)
    print("wrote", path, pix.width(), "x", pix.height())
    return path


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    win = MainWindow()
    win.show()
    _pump(app, 400)

    win.show_welcome()
    win.resize(1366, 768)
    _pump(app, 250)
    _save(win, "T03_welcome.png")

    win.show_editor()
    win.set_ui_mode("quick", persist=False)
    win.resize(1366, 768)
    _pump(app, 250)
    _save(win, "T03_quick.png")

    win.set_ui_mode("pro", persist=False)
    win.resize(1700, 1000)
    _pump(app, 250)
    _save(win, "T03_pro.png")

    dlg = SettingsDialog(win, initial_tab=0, parent=win)
    dlg.show()
    _pump(app, 200)
    _save(dlg, "T03_settings.png")
    dlg.close()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
