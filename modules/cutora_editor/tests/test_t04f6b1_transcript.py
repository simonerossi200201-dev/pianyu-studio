# -*- coding: utf-8 -*-
"""T04f-6b-1 A: sentence cards fill after ASR without applying a pack."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

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


def run_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04f6b1_a_"))
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
    from cutora.ui.panels.transcript import TranscriptPanel

    app = QApplication.instance() or QApplication(["cutora-t04f6b1-a"])
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

    n_reload = {"n": 0}
    orig = TranscriptPanel.reload

    def wrap(self, *args, **kwargs):
        n_reload["n"] += 1
        return orig(self, *args, **kwargs)

    TranscriptPanel.reload = wrap

    pump(80)
    win.show_editor()
    pump(40)
    win.transcript_inner.setCurrentWidget(win.transcript_panel)
    panel = win.transcript_panel
    win.command_project_open(path=G09)
    pump(250)
    segs = list((win.tasks[win.curr_idx].get("segments") or []))
    panel.inspect_cards()
    n_cards = len(panel._cards)
    print("OPEN segs=%d cards=%d reloads=%d" % (len(segs), n_cards, n_reload["n"]), flush=True)
    assert n_cards > 0, n_cards
    assert n_cards == len(segs), (n_cards, len(segs))

    class _Fake:
        align_note = None
        approximate_words = False

    win.worker_ai = _Fake()
    before = n_reload["n"]
    win.on_align_done(list(segs), False)
    pump(120)
    after = n_reload["n"]
    panel.inspect_cards()
    print("ALIGN cards=%d reloads=%d (+%d)" % (
        len(panel._cards), after, after - before), flush=True)
    assert len(panel._cards) == len(win.tasks[win.curr_idx].get("segments") or [])
    assert after - before <= 1, after - before

    # ≥200 segment on_click cost
    fat = []
    for i in range(220):
        fat.append({
            "start": float(i), "end": float(i) + 0.8,
            "text": "sentence number %d with some words" % i,
        })
    from cutora.subtitle.track_source import set_segments
    set_segments(win.tasks[win.curr_idx], fat)
    t0 = time.perf_counter()
    win._refresh_task_view(win.curr_idx)
    pump(20)
    ms = (time.perf_counter() - t0) * 1000.0
    print("REFRESH_220_MS %.1f cards=%d" % (ms, panel.model.rowCount()), flush=True)
    assert panel.model.rowCount() == 220
    if ms > 300:
        print("REFRESH_SLOW", ms, flush=True)
    win.close()
    return 0


def test_asr_fills_transcript_without_stylepack():
    env = _child_env()
    cmd = [PY, os.path.join(REPO, "tests", "test_t04f6b1_transcript.py")]
    r = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    if r.returncode != 0 or "T04F6B1_A_OK" not in (r.stdout or ""):
        pytest.fail("rc=%s\n%s" % (r.returncode, out[-4000:]))
    # 220-card reload cost is reported in stdout (REFRESH_220_MS / REFRESH_SLOW).


if __name__ == "__main__":
    try:
        rc = run_suite()
        print("T04F6B1_A_OK", flush=True)
        raise SystemExit(rc)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
