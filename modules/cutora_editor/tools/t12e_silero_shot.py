# -*- coding: utf-8 -*-
"""Offscreen screenshot: Settings → 模型 VAD group with bundled Silero."""
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
from cutora.ui.settings_dialog import SettingsDialog

OUT = os.path.join(REPO, "docs", "plan", "reports", "img")


def _pump(app, ms: int = 200) -> None:
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
    win.show()
    dlg = SettingsDialog(win, initial_tab=4, parent=win)
    dlg.resize(760, 640)
    dlg.show()
    page = dlg.models_page
    page.sections["asr"].setExpanded(False)
    page.sections["align"].setExpanded(False)
    page.sections["vad"].setExpanded(True)
    page.tables["vad"].setColumnWidth(0, 220)
    _pump(app, 250)
    path = os.path.join(OUT, "T12e_models_silero_vad.png")
    pix = dlg.grab()
    if not pix.save(path, "PNG"):
        raise SystemExit("failed to save %s" % path)
    print("wrote", path, pix.width(), "x", pix.height())
    dlg.close()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
