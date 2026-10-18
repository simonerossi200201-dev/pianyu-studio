# -*- coding: utf-8 -*-
"""Startup order: migration dialog before splash; no modal during splash."""
from __future__ import annotations

import os
import sys

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(["cutora-test"])
    return app


def _dummy_win_cls():
    from PySide6.QtWidgets import QWidget

    class DummyWin(QWidget):
        def resume_project_queue(self):
            return None

    return DummyWin


def _stub_gui(monkeypatch, app, prompt, make_splash, fonts):
    from PySide6.QtGui import QFont
    monkeypatch.setattr(
        "cutora.core.migrate_user_data.prompt_and_import", prompt)
    monkeypatch.setattr("cutora.ui.splash.make_splash", make_splash)
    monkeypatch.setattr(
        "cutora.ui.panels.fonts.load_embedded_fonts", fonts)

    class _Mgr:
        def get_font(self, *a, **k):
            return QFont()

    monkeypatch.setattr(
        "cutora.ui.panels.fonts.get_font_manager", lambda: _Mgr())
    monkeypatch.setattr(
        "cutora.core.config.detect_gpu_config",
        lambda verbose=False: (None, None, "test"))
    monkeypatch.setattr(app, "exec", lambda: 0)
    monkeypatch.setattr(app, "setStyle", lambda *a, **k: None)
    monkeypatch.setattr(app, "setFont", lambda *a, **k: None)


def test_splash_first_then_prompt_and_import(monkeypatch):
    # Q106: the splash is the first thing on screen; the user-data import
    # prompt comes after it (and before fonts / the main window).
    from PySide6.QtGui import QColor, QPixmap
    from cutora.ui.splash import BrandSplash

    order = []
    app = _qapp()

    def fake_prompt(parent=None):
        order.append("prompt_and_import")
        return None

    def fake_make_splash():
        order.append("make_splash")
        pix = QPixmap(64, 36)
        pix.fill(QColor("#141210"))
        return BrandSplash(pix)

    def fake_fonts():
        order.append("load_embedded_fonts")
        return True

    _stub_gui(monkeypatch, app, fake_prompt, fake_make_splash, fake_fonts)
    from cutora.app import run_gui_startup
    run_gui_startup(app, window_factory=_dummy_win_cls())
    assert order[:2] == ["make_splash", "prompt_and_import"]
    assert order.index("prompt_and_import") < order.index("load_embedded_fonts")


def test_no_dialog_exec_during_splash(monkeypatch):
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QDialog, QMessageBox
    from cutora.ui.splash import BrandSplash

    app = _qapp()
    splash_on = {"v": False}
    execs = []

    def fake_make_splash():
        splash_on["v"] = True
        pix = QPixmap(64, 36)
        pix.fill(QColor("#141210"))
        return BrandSplash(pix)

    orig_dialog, orig_box = QDialog.exec, QMessageBox.exec

    def wrap(orig, name):
        def _exec(self, *a, **k):
            if splash_on["v"]:
                execs.append(name)
            return orig(self, *a, **k)
        return _exec

    monkeypatch.setattr(QDialog, "exec", wrap(orig_dialog, "QDialog"))
    monkeypatch.setattr(QMessageBox, "exec", wrap(orig_box, "QMessageBox"))
    _stub_gui(monkeypatch, app, lambda parent=None: None, fake_make_splash,
              lambda: True)
    from cutora.app import run_gui_startup
    run_gui_startup(app, window_factory=_dummy_win_cls())
    assert execs == []


def test_load_embedded_fonts_missing_dir_logs_not_dialog(monkeypatch, tmp_path):
    from cutora.ui.panels import fonts as fonts_mod
    monkeypatch.setattr(fonts_mod, "FONT_DIR", str(tmp_path / "missing"))
    called = []
    monkeypatch.setattr(
        fonts_mod.QMessageBox, "critical",
        lambda *a, **k: called.append(True),
    )
    assert fonts_mod.load_embedded_fonts() is False
    assert called == []


def test_prepare_import_dialog_stays_on_top():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from cutora.core.migrate_user_data import prepare_import_dialog
    _qapp()
    box = QMessageBox()
    prepare_import_dialog(box)
    assert box.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    box.close()


def test_splash_runtime_paints_slot_not_chrome():
    """drawContents paints the spec slot + fill; chrome is not in the pixmap."""
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from cutora.ui.splash import FILL_COLOR, SLOT, SLOT_COLOR, BrandSplash

    canvas = QPixmap(1280, 720)
    canvas.fill(QColor("#141210"))
    splash = BrandSplash(canvas)
    splash.set_progress(0.34)
    splash.showMessage("load")
    out = QPixmap(canvas)
    painter = QPainter(out)
    splash.drawContents(painter)
    painter.end()
    img = out.toImage()
    mid_y = int(SLOT.y() + SLOT.height() / 2)
    fill_x = int(SLOT.x() + SLOT.width() * 0.15)
    empty_x = int(SLOT.x() + SLOT.width() * 0.80)
    fill = img.pixelColor(fill_x, mid_y)
    slot = img.pixelColor(empty_x, mid_y)
    assert (fill.red(), fill.green(), fill.blue()) == (
        FILL_COLOR.red(), FILL_COLOR.green(), FILL_COLOR.blue())
    assert (slot.red(), slot.green(), slot.blue()) == (
        SLOT_COLOR.red(), SLOT_COLOR.green(), SLOT_COLOR.blue())


def test_about_dialog_mark_and_tagline():
    from PySide6.QtWidgets import QLabel
    from cutora.core.brand import APP_DISPLAY_NAME, TAGLINE_EN, TAGLINE_ZH
    from cutora.ui.panels import about as about_mod
    about_mod._ffmpeg_version = lambda: "ffmpeg test"
    about_mod._gpu_reason = lambda: "test gpu"
    _qapp()
    dlg = about_mod.AboutDialog()
    try:
        joined = "\n".join(lab.text() for lab in dlg.findChildren(QLabel))
        assert TAGLINE_ZH in joined
        assert TAGLINE_EN in joined
        assert APP_DISPLAY_NAME in joined
        pix = dlg.findChildren(QLabel)[0].pixmap()
        assert pix is not None and not pix.isNull()
        assert getattr(dlg, "btn_third_party", None) is not None
        assert "第三方授权" in dlg.btn_third_party.text()
    finally:
        try:
            dlg.close()
        except RuntimeError:
            pass


def test_welcome_page_does_not_import_stitcher():
    """Building the welcome page must not pull the stitcher or whisperx."""
    import subprocess
    import sys
    code = (
        "import os, sys\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication.instance() or QApplication(['q104-startup'])\n"
        "from cutora.ui.welcome import WelcomePage\n"
        "page = WelcomePage()\n"
        "page.show()\n"
        "app.processEvents()\n"
        "assert 'cutora.batch.script_stitcher' not in sys.modules\n"
        "assert 'whisperx' not in sys.modules\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert "ok" in (proc.stdout or "")


# ───────────── Q131：启动期间的模态框不许被置顶闪屏压住 ─────────────

def test_q131_pending_startup_warning_shown_after_splash(monkeypatch):
    """主窗口构造时攒下的提示，要等闪屏关掉、窗口显示之后才弹。"""
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QWidget
    from cutora.ui.splash import BrandSplash

    app = _qapp()
    splashes = []
    shown = []

    def fake_make_splash():
        pix = QPixmap(64, 36)
        pix.fill(QColor("#141210"))
        s = BrandSplash(pix)
        splashes.append(s)
        return s

    class Win(QWidget):
        def resume_project_queue(self):
            return None

        def show_pending_startup_warnings(self):
            shown.append((splashes[0].isVisible(), self.isVisible()))

    _stub_gui(monkeypatch, app, lambda parent=None: None, fake_make_splash,
              lambda: True)
    from cutora.app import run_gui_startup
    run_gui_startup(app, window_factory=Win)
    assert shown == []          # 不在 run_gui_startup 里同步弹
    for _ in range(5):
        app.processEvents()
    assert shown == [(False, True)], shown


def test_q131_splash_hides_when_a_dialog_shows():
    """兜底：闪屏开着的时候任何对话框一显示，闪屏就让开。"""
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QDialog
    from cutora.ui.splash import BrandSplash

    app = _qapp()
    pix = QPixmap(64, 36)
    pix.fill(QColor("#141210"))
    splash = BrandSplash(pix)
    splash.show()
    app.processEvents()
    assert splash.isVisible()
    dlg = QDialog()
    dlg.show()
    app.processEvents()
    try:
        assert not splash.isVisible()
    finally:
        dlg.close()
        splash.close()
    # 闪屏收起后不再拦截：再开一个对话框不会出错
    dlg2 = QDialog()
    dlg2.show()
    app.processEvents()
    dlg2.close()


def test_q131_main_window_lock_warning_is_deferred():
    """MainWindow.__init__ 的单实例锁提示不再直接 QMessageBox.warning。"""
    import inspect
    from cutora.ui import main_window as mw
    src = inspect.getsource(mw.MainWindow.__init__)
    block = src[src.index("gui_try_acquire"):]
    block = block[:block.index("except Exception as _lk_err")]
    assert "QMessageBox" not in block
    assert "_pending_cli_lock_warning" in block
    assert hasattr(mw.MainWindow, "show_pending_startup_warnings")
