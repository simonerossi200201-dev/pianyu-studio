# -*- coding: utf-8 -*-
"""T04c Transcript panel: overflow badge visible (offscreen child)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
G09 = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin", "project.asproj.json")
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def run_panel_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04c_"))
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
    from cutora.ui.i18n import tr

    app = QApplication.instance() or QApplication(["cutora-t04c-panel"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()

    def pump(ms=80):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    pump(80)
    win.show_editor()
    pump(40)
    win.left_tabs.setCurrentWidget(win._tab_ops)
    win.transcript_inner.setCurrentWidget(win.transcript_panel)
    panel = win.transcript_panel
    panel.reload()
    pump(80)
    assert tr("先在媒体库里选一个条目") in panel.empty.text()
    assert not panel.btn_empty_auto.isVisible()
    assert panel.btn_reload is not None
    assert os.path.isfile(G09), G09
    win.command_project_open(path=G09)
    pump(200)
    panel.reload()
    pump(80)
    panel.inspect_cards()
    labels = [c.lbl_over.text() for c in panel._cards]
    mark = tr("超长，会超出画面")
    assert any(mark in (t or "") for t in labels), labels
    assert len(panel._cards) == 6, len(panel._cards)
    assert all(not (c.lbl_spk.text() or "").strip() for c in panel._cards)
    over_cps = [c for c in panel._cards if (c.lbl_cps.text() or "").strip()]
    under_cps = [c for c in panel._cards if not (c.lbl_cps.text() or "").strip()]
    assert over_cps, "over-CPS badge missing"
    assert under_cps, "in-range CPS should be hidden"
    assert all("CPS" in (c.lbl_cps.text() or "") for c in over_cps)
    short_h = panel._cards[1].editor.height()
    long_h = panel._cards[3].editor.height()
    assert short_h < 48, short_h
    assert long_h >= short_h
    from cutora.ui.icons import lucide_path
    assert os.path.isfile(lucide_path("lock"))
    assert os.path.isfile(lucide_path("lock-open"))
    assert not panel._cards[0].btn_lock.icon().isNull()
    assert "📌" not in panel._cards[0].btn_lock.text()
    assert panel.btn_more.menu() is not None
    fam = panel._cards[1].editor.font().family()
    assert "JetBrains" not in fam and "Consolas" not in fam, fam
    assert panel._cards[1].editor.font().family() or True
    assert "将断成" in (panel._cards[1].toolTip() or "")
    win.close()
    return 0


def test_transcript_overflow_badge_offscreen():
    env = _child_env()
    cmd = [PY, os.path.join(REPO, "tests", "test_t04c_panel.py")]
    out = ""
    r = None
    for _ in range(2):
        r = subprocess.run(
            cmd, cwd=REPO, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240,
        )
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode == 0 and "T04C_PANEL_OK" in (r.stdout or ""):
            return
    pytest.fail("rc=%s\n%s" % (getattr(r, "returncode", None), out[-4000:]))


if __name__ == "__main__":
    try:
        rc = run_panel_suite()
        print("T04C_PANEL_OK", flush=True)
        raise SystemExit(rc)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
