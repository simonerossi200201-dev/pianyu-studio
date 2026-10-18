# -*- coding: utf-8 -*-
"""T12c 提交 1：语言检测必须离开 GUI 主线程，可取消。"""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_run_align_does_not_call_detect_on_gui_thread():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "DetectWorker" in src
    assert "正在检测语言" in src
    assert "btn_prog_cancel" in src


def test_detect_sleep_keeps_main_window_responsive_and_cancel_works():
    env = _child_env()
    proc = subprocess.run(
        [VENV_PY, os.path.abspath(__file__), "--run"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out


def run() -> int:
    import tempfile
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="t12c_detect_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.asr.languages import set_asr_language

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t12c-detect"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    win.show_editor()
    win.set_ui_mode("pro", persist=False)

    def pump(ms=50):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    pump(80)
    set_asr_language("auto")
    video = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
    win.tasks = [{"video": video, "text": "hello", "segments": []}]
    win.curr_idx = 0
    win.text_input.setPlainText("hello")
    win._ensure_asr_model = lambda: True
    started = []
    win._begin_alignment = lambda lang, is_batch=False: started.append(lang)

    def slow(_path):
        time.sleep(3.0)
        return {
            "language": "en", "confidence": 0.99, "low_confidence": False,
            "name_zh": "英语",
        }

    win._detect_fn = slow
    ticked = {"n": 0}

    def tick():
        ticked["n"] += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(50)
    win.run_align(is_batch=False)
    app.processEvents()
    assert win.btn_prog_cancel.isVisible()
    assert "检测" in (win.prog_label.text() or "")
    deadline = time.time() + 1.2
    while time.time() < deadline:
        pump(40)
    assert ticked["n"] >= 5, "检测期间主窗口必须能处理事件，ticked=%s" % ticked["n"]
    win.btn_prog_cancel.click()
    app.processEvents()
    end = time.time() + 4.0
    while time.time() < end:
        pump(50)
        if not getattr(win, "_detect_worker", None) or not win._detect_worker.isRunning():
            break
    timer.stop()
    assert started == [], "取消后不得启动识别: %s" % started
    print("ok", flush=True)
    from cutora.core.runtime import shutdown_qapplication
    shutdown_qapplication()
    return 0


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        raise SystemExit(run())
