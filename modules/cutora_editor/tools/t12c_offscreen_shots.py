# -*- coding: utf-8 -*-
"""Offscreen screenshots for T12c: batch-results dock tab, approximate words."""
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
    win.show_editor()
    win.set_ui_mode("pro", persist=False)
    win.resize(1700, 1000)
    _pump(app, 300)

    win._show_asr_batch_results([
        {"media_id": 0, "path": r"C:\media\sermon_a.mp4",
         "language": "en", "confidence": 0.94, "warn": False},
        {"media_id": 1, "path": r"C:\media\sermon_b.mp4",
         "language": "it", "confidence": 0.41, "warn": True},
        {"media_id": 2, "path": r"C:\media\zh_clip.mp4",
         "language": "zh", "confidence": 0.88, "warn": False},
    ])
    _pump(app, 250)
    _save(win, "T12c_batch_results_dock.png")

    if hasattr(win, "lbl_approx_words"):
        from cutora.ui.i18n import tr
        win.lbl_approx_words.setText(tr("近似词级"))
        win.lbl_approx_words.show()
        idx = None
        for i in range(win.left_tabs.count()):
            if win.left_tabs.tabText(i) == "字幕文本":
                idx = i
                break
        if idx is not None:
            win.left_tabs.setCurrentIndex(idx)
        _pump(app, 200)
        _save(win, "T12c_approx_words.png")

    dlg = SettingsDialog(win, initial_tab=5, parent=win)
    dlg.show()
    _pump(app, 200)
    _save(dlg, "T12c_models_dir.png")
    dlg.close()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
