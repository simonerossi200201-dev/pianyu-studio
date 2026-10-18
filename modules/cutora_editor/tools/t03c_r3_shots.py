# -*- coding: utf-8 -*-
"""Offscreen shots for T03c r3: editor with text overlay + separator idle/hover."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_r3_shots_"))

from cutora.core.runtime import init_torch_runtime

init_torch_runtime(render_child=False)

from PySide6.QtCore import QEventLoop, QTimer, QPoint, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory, QFileDialog
from PySide6.QtTest import QTest
from cutora.app import apply_ui_locale
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
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


def _timeline_sep_point(win):
    c = win.centralWidget()
    d = win.dock_timeline
    c_bl = c.mapTo(win, QPoint(0, c.height()))
    d_tl = d.mapTo(win, QPoint(0, 0))
    x = (max(c_bl.x(), d_tl.x()) + min(c.mapTo(win, QPoint(c.width(), 0)).x(),
                                       d.mapTo(win, QPoint(d.width(), 0)).x())) // 2
    gap_top = min(c_bl.y(), d_tl.y())
    y = int(gap_top) + 1 if abs(c_bl.y() - d_tl.y()) >= 2 else int(d_tl.y()) - 4
    return QPoint(x, y)


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

    assert os.path.isfile(G01), G01
    win._welcome_drop([G01])
    _pump(app, 400)
    win.set_ui_mode("pro", persist=False)
    leaves = win.file_list._get_all_leaves()
    if leaves:
        win.on_click(leaves[0])
        _pump(app, 200)
    win.add_text_ov()
    _pump(app, 300)
    _save(win, "T03c_r3_pro_text.png")

    # Idle separators: mouse away from docks.
    QTest.mouseMove(win, QPoint(800, 80))
    _pump(app, 150)
    _save(win, "T03c_r3_sep_idle.png")

    pt = _timeline_sep_point(win)
    QTest.mouseMove(win, pt)
    _pump(app, 200)
    _save(win, "T03c_r3_sep_hover.png")
    print("hover at", pt.x(), pt.y())

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
