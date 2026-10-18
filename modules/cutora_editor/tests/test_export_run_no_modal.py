# -*- coding: utf-8 -*-
"""API export.run must not pop a modal dialog (T13 收尾 item 3). Write first, expect red."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
G01_PROJ = os.path.join(REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")


def test_export_run_command_never_prompts():
    import inspect
    import cutora.ui.command_bodies as bodies
    src = inspect.getsource(bodies)
    # command_export_run lives in this mixin; UI export_video may prompt,
    # but the command path used by CLI/recipe/MCP must not.
    start = src.find("def command_export_run")
    end = src.find("\n    def command_export_batch")
    chunk = src[start:end] if start >= 0 else ""
    assert "def command_export_run" in chunk
    assert "prompt_export_folder" not in chunk
    assert "指定导出文件夹" not in chunk


def test_export_run_api_source_has_no_modal():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    proc = subprocess.run(
        [VENV_PY, os.path.abspath(__file__), "--run"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_export_modal_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStyleFactory
    from cutora.app import apply_ui_locale
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import get_bus
    from PySide6.QtCore import QEventLoop, QTimer
    import time

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t13-no-modal"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()

    def pump(ms=50):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    pump(80)
    win.show_editor()
    if hasattr(win, "chk_gpu"):
        win.chk_gpu.setChecked(False)
    pump(40)
    bus = get_bus()
    bus.bind_window(win)
    bus.execute("project.open", {"path": G01_PROJ}, source="api")
    pump(80)
    prompted = []
    import cutora.render.export_dest as dest
    orig = dest.prompt_export_folder
    dest.prompt_export_folder = lambda *a, **k: prompted.append("hit") or None
    out = os.path.join(tempfile.mkdtemp(prefix="cutora_exp_"), "api.mp4")
    t0 = time.time()
    bus.execute("export.run", {"out": out, "confirm": True}, source="api")
    while getattr(win, "is_exporting", False) and time.time() - t0 < 150:
        pump(100)
    elapsed = time.time() - t0
    assert elapsed < 150, "export.run source=api hung (likely a modal dialog)"
    modals = []
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMessageBox):
            modals.append(type(w).__name__)
        elif isinstance(w, QDialog) and w.isModal() and w is not win:
            modals.append(type(w).__name__)
    assert modals == [], modals
    assert prompted == [], prompted
    dest.prompt_export_folder = orig
    sys.stdout.write("ok\n")
    sys.stdout.flush()
    from cutora.core.runtime import shutdown_qapplication
    shutdown_qapplication()
    return 0


if __name__ == "__main__":
    if "--run" in sys.argv:
        raise SystemExit(run())
