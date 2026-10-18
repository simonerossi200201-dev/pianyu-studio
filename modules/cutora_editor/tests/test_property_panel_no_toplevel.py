# -*- coding: utf-8 -*-
"""Property panel must not spawn a top-level QLabel ghost window (T13a-fix 2a)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def run_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_prop_ghost_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QLabel, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["cutora-prop-ghost"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    win.show_editor()
    win.add_text_ov()
    assert win.curr_idx >= 0
    ov = win.tasks[win.curr_idx]["text_overlays"][0]
    win.prop_panel.load_data("overlay", ov)
    app.processEvents()

    ghosts = []
    for w in QApplication.topLevelWidgets():
        if w is win:
            continue
        if isinstance(w, QLabel):
            ghosts.append((w.text(), w.objectName(), w.parent()))
    assert not ghosts, "top-level QLabel ghosts: %r" % (ghosts,)
    print("NO_GHOST_OK", flush=True)
    try:
        win.close()
    except Exception:
        pass
    os._exit(0)


def test_property_panel_no_toplevel_qlabel():
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_property_panel_no_toplevel.py")],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "NO_GHOST_OK" in (proc.stdout or ""), out


if __name__ == "__main__":
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        raise SystemExit(run_suite())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
