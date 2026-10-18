# -*- coding: utf-8 -*-
"""Headless interactive smoke: click welcome buttons and trigger QActions.

Must run in a subprocess so ``init_torch_runtime()`` precedes PySide6 (MSVCP140).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
G01_MP4 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")

# Only skip actions that would exit the process or write a user-facing file
# even after modal stubs (empty path / Rejected). Reasons are required.
SKIP_ACTIONS = {
    # none today: 保存/导出 go through QFileDialog (stubbed to cancel);
    # there is no 退出 menu action. Settings writes stay in CUTORA_USER_DATA.
}


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("PYTHONUNBUFFERED", "1")
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _pump(app, ms: int = 40) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _install_modal_stubs() -> None:
    """Return defaults for every modal; do not skip the triggering action."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox,
    )

    QDialog.exec = lambda *a, **k: 0
    from PySide6.QtWidgets import QMenu
    def _no_menu_exec(*a, **k):
        return None
    QMenu.exec = _no_menu_exec
    _qmenu_init = QMenu.__init__
    def _qmenu_init_no_exec(self, *a, **k):
        _qmenu_init(self, *a, **k)
        self.exec = _no_menu_exec
    QMenu.__init__ = _qmenu_init_no_exec
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("1080x1920", True))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (2, False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    QColorDialog.getColor = staticmethod(lambda *a, **k: QColor())
    _ok = QMessageBox.StandardButton.Ok
    _no = QMessageBox.StandardButton.No
    QMessageBox.information = staticmethod(lambda *a, **k: _ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: _ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: _ok)
    QMessageBox.question = staticmethod(lambda *a, **k: _no)
    QMessageBox.about = staticmethod(lambda *a, **k: None)


def _watch_reentry(win) -> dict:
    stats = {}
    for name in ("set_ui_mode", "show_editor", "refresh_ui", "update_ui_state"):
        orig = getattr(win, name)
        box = {"n": 0, "depth": 0, "max": 0}

        def _wrap(fn=orig, s=box, label=name):
            def inner(*a, **k):
                s["n"] += 1
                s["depth"] += 1
                s["max"] = max(s["max"], s["depth"])
                if s["depth"] > 40:
                    raise RecursionError("%s depth=%d" % (label, s["depth"]))
                try:
                    return fn(*a, **k)
                finally:
                    s["depth"] -= 1
            return inner

        setattr(win, name, _wrap())
        stats[name] = box
    return stats


def _exercise_new_entries(win, app) -> None:
    """The four new-project paths that Luca can hit from the welcome page / menu."""
    win.show_welcome()
    _pump(app)
    win._welcome_new(1080, 1920)
    _pump(app)
    assert win.shell_stack.currentIndex() == 1, "portrait did not enter editor"
    win.show_welcome()
    _pump(app)
    win._welcome_new(1920, 1080)
    _pump(app)
    assert win.shell_stack.currentIndex() == 1, "landscape did not enter editor"
    win.show_welcome()
    _pump(app)
    win._welcome_new_custom()
    _pump(app)
    assert win.shell_stack.currentIndex() == 1, "custom did not enter editor"
    win.show_welcome()
    _pump(app)
    assert os.path.isfile(G01_MP4), G01_MP4
    win._welcome_drop([G01_MP4])
    _pump(app, 200)
    assert win.shell_stack.currentIndex() == 1, "drop did not enter editor"
    win.show_welcome()
    _pump(app)
    win.new_project()
    _pump(app)
    assert win.shell_stack.currentIndex() == 1, "menu new_project did not enter editor"


def _click_welcome_buttons(win, app) -> None:
    from PySide6.QtWidgets import QPushButton
    win.show_welcome()
    _pump(app)
    buttons = win.welcome_page.findChildren(QPushButton)
    assert buttons, "welcome page has no buttons"
    for btn in buttons:
        print("CLICK", btn.text(), flush=True)
        btn.click()
        _pump(app, 80)
        win.show_welcome()
        _pump(app)


def _trigger_actions(win, app) -> None:
    from PySide6.QtGui import QAction
    actions = [a for a in win.findChildren(QAction) if a is not None]
    assert actions, "MainWindow has no QActions"
    for act in actions:
        text = (act.text() or act.objectName() or "").strip()
        if text in SKIP_ACTIONS:
            print("SKIP", text, SKIP_ACTIONS[text], flush=True)
            continue
        if not act.isEnabled():
            print("DISABLED", text, flush=True)
            continue
        print("TRIGGER", text, flush=True)
        act.trigger()
        _pump(app, 40)


def run_smoke() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_ui_smoke_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    import faulthandler
    faulthandler.enable(all_threads=True)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow

    slot_errors = []

    def _hook(typ, val, tb):
        slot_errors.append((typ, val))
        traceback.print_exception(typ, val, tb)
    sys.excepthook = _hook

    app = QApplication.instance() or QApplication(["cutora-ui-smoke"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    _install_modal_stubs()
    win = MainWindow()
    win.show()
    _pump(app, 80)
    from PySide6.QtGui import QAction as _QAction
    have = {(a.text() or "").strip() for a in win.findChildren(_QAction)}
    for need in (
        "从选中视频拷贝字幕样式",
        "从选中视频共享轨道…",
        "添加文本",
        "添加图片",
        "添加配乐…",
        "片段散落…",
        "全屏预览",
    ):
        assert need in have, "missing action %r in %r" % (need, sorted(have))
    assert "批量配乐…" not in have
    assert "音量总控…" not in have
    assert "添加背景音乐" not in have
    bar_titles = {(a.text() or "").strip() for a in win.menuBar().actions()}
    assert "音频" not in bar_titles
    stats = _watch_reentry(win)
    _exercise_new_entries(win, app)
    _click_welcome_buttons(win, app)
    _trigger_actions(win, app)
    _pump(app, 80)
    from PySide6.QtCore import QPoint
    if win.file_list.count() == 0:
        win.file_list.addItem("T007.mp4")
        win.tasks.append({"video": "T007.mp4", "style": {}})
    win.file_list.show_context_menu(QPoint(8, 12))
    _pump(app, 40)
    for name, box in stats.items():
        print("REENTRY", name, "n=%d max_depth=%d" % (box["n"], box["max"]), flush=True)
        assert box["max"] <= 8, "%s reentered depth=%d" % (name, box["max"])
    if slot_errors:
        print("SMOKE_FAIL slot errors", len(slot_errors), flush=True)
        return 1
    win.show_editor()
    _pump(app, 80)
    for name in ("btn_ai", "btn_exp", "btnAddText", "btnAddImage", "btn_start"):
        btn = getattr(win, name)
        assert btn.isVisible(), "%s visible=%s parent=%s" % (
            name, btn.isVisible(), btn.parentWidget())
        assert not btn.icon().isNull(), "%s icon is null" % name
    assert getattr(win, "btnAddAudio", None) is None
    from PySide6.QtWidgets import QSlider
    for role in ("main", "bgm", "sfx"):
        sl = win.findChild(QSlider, "roleFader_%s" % role)
        assert sl is not None and sl.isVisible(), role
        cluster = sl.parent()
        assert cluster is not None
        assert cluster.maximumWidth() <= 90, (role, cluster.maximumWidth())
        assert sl.width() <= 90, (role, sl.width())
    assert not win.btn_exp_stop.icon().isNull()
    win.is_exporting = True
    win.update_ui_state()
    assert win.btn_exp_stop.isVisible()
    win.is_exporting = False
    win.update_ui_state()
    print("SMOKE_OK", flush=True)
    from cutora.core.runtime import shutdown_qapplication
    shutdown_qapplication()
    return 0


def test_ui_actions_smoke_offscreen():
    """Click every welcome button and trigger every QAction offscreen."""
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_ui_actions_smoke.py")],
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
    assert "SMOKE_OK" in (proc.stdout or ""), out
    assert "RecursionError" not in out


if __name__ == "__main__":
    try:
        raise SystemExit(run_smoke())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
