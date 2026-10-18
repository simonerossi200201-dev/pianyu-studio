# -*- coding: utf-8 -*-
"""Offscreen shots for T03c r4: transport bar + preset ellipsis menu."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_r4_shots_"))

from cutora.core.runtime import init_torch_runtime

init_torch_runtime(render_child=False)

from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory
from cutora.app import apply_ui_locale
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss, apply_theme
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
from cutora.ui.main_window import MainWindow

OUT = os.path.join(REPO, "docs", "plan", "reports", "img")
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def _pump(app, ms=200):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _save(widget, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    pix = widget.grab()
    if not pix.save(path, "PNG"):
        raise SystemExit("failed to save %s" % path)
    print("wrote", path, pix.width(), "x", pix.height())
    return path


def _load_g01(win, app):
    assert os.path.isfile(G01), G01
    win._welcome_drop([G01])
    _pump(app, 400)
    leaves = win.file_list._get_all_leaves()
    if leaves:
        win.on_click(leaves[0])
        _pump(app, 200)


def main() -> int:
    apply_ui_locale()
    app = QApplication.instance() or QApplication(sys.argv)
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    win = MainWindow()
    win.show()
    win.resize(1600, 900)
    win.show_editor()
    win.set_ui_mode("pro", persist=False)
    _pump(app, 200)
    _load_g01(win, app)
    win.set_ui_mode("pro", persist=False)
    _pump(app, 200)
    _save(win, "T03c_r4_pro.png")

    wrap_h = win.video_wrap.height()
    bar_h = win.preview_tools.height()
    print("pro video_wrap_h", wrap_h, "transport_h", bar_h)

    win.set_ui_mode("quick", persist=False)
    _pump(app, 200)
    _save(win, "T03c_r4_quick.png")
    print("quick video_wrap_h", win.video_wrap.height(),
          "transport_h", win.preview_tools.height())

    win.set_ui_mode("pro", persist=False)
    win.dock_right.show()
    win.dock_right.raise_()
    for i in range(win.tabs.count()):
        if win.tabs.tabText(i) == "字幕样式":
            win.tabs.setCurrentIndex(i)
            break
    _pump(app, 200)
    _save(win.dock_right, "T03c_r4_preset_row.png")

    btn = win.sub_panel._menu_btn
    menu = win.sub_panel._style_menu
    gp = btn.mapToGlobal(QPoint(0, btn.height()))
    menu.popup(gp)
    _pump(app, 150)
    _save(menu, "T03c_r4_preset_menu.png")
    menu.hide()

    apply_theme("amethyst")
    _pump(app, 200)
    _save(win, "T03c_r4_theme_amethyst.png")
    apply_theme("rose")
    _pump(app, 200)
    _save(win, "T03c_r4_theme_rose.png")
    apply_theme("roma-gold")
    _pump(app, 100)

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
