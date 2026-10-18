# -*- coding: utf-8 -*-
"""T03c r3: F / Esc / close all leave fullscreen preview and restore the canvas.

Must run in a subprocess so init_torch_runtime() precedes PySide6.
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _qt_child(timeout: int = 180) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, os.path.abspath(__file__), "--run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
    return proc.stdout or ""


def test_fullscreen_preview_f_esc_close():
    _qt_child()


def _pump(app, ms: int = 50) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _enter(win, app):
    win.toggle_preview_fullscreen()
    _pump(app, 80)
    canvas = win.video_canvas
    assert canvas.isFullScreen(), "expected canvas.isFullScreen() after enter"
    assert canvas._preview_fullscreen


def _assert_restored(win, canvas):
    assert canvas.parent() is win.video_wrap, canvas.parent()
    assert canvas.isVisible()
    assert not canvas.isFullScreen()
    assert not canvas._preview_fullscreen


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from cutora.app import apply_ui_locale
    from cutora.ui.main_window import MainWindow

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t03c-fs"])
    win = MainWindow()
    win.show()
    win.show_editor()
    _pump(app, 80)
    canvas = win.video_canvas

    _enter(win, app)
    canvas.activateWindow()
    canvas.setFocus()
    QApplication.setActiveWindow(canvas)
    _pump(app, 40)
    QTest.keyClick(canvas, Qt.Key.Key_F)
    _pump(app, 80)
    _assert_restored(win, canvas)
    print("pass F", flush=True)

    _enter(win, app)
    canvas.activateWindow()
    canvas.setFocus()
    QApplication.setActiveWindow(canvas)
    _pump(app, 40)
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    _pump(app, 80)
    _assert_restored(win, canvas)
    print("pass Esc", flush=True)

    _enter(win, app)
    canvas.close()
    _pump(app, 80)
    _assert_restored(win, canvas)
    print("pass close", flush=True)

    print("ok", flush=True)
    from cutora.core.runtime import shutdown_qapplication
    shutdown_qapplication()
    return 0


if __name__ == "__main__":
    if "--run" in sys.argv:
        raise SystemExit(run())
    raise SystemExit(0)
