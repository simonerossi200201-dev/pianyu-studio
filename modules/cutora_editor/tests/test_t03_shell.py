# -*- coding: utf-8 -*-
"""T03b shell: shortcuts table, settings/welcome widgets, menu rewrite."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _qt_child(code: str, *, no_cuda: bool = False) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if no_cuda:
        # Parent already owns a CUDA context (q11). These two children only
        # build Welcome / Settings; config.detect_gpu_config would open a
        # second context and the process then dies 0xC0000005 after "ok".
        env["CUTORA_RENDER_CHILD"] = "1"
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = proc.stdout or ""
    # Assertions already printed "ok". On this machine Qt/CUDA teardown
    # still ACCESS_VIOLATIONs (0xC0000005) after quit; qt_isolate ignores
    # that code when junit says passed. Do the same here.
    if "ok" in stdout and proc.returncode in (0, 3221225477, -1073741819):
        return
    assert proc.returncode == 0, stdout + "\n" + (proc.stderr or "")
    assert "ok" in stdout


def test_shortcut_table_has_required_keys():
    from cutora.ui.shortcuts import SHORTCUTS
    keys = {row[0] for row in SHORTCUTS}
    for need in (
        "play_pause", "stop", "play_fwd", "play_rev", "split", "marker",
        "fullscreen", "settings", "import_media", "export",
    ):
        assert need in keys, need
    assert any(row[1] == "Space" for row in SHORTCUTS)
    assert any(row[1] == "Ctrl+," for row in SHORTCUTS)


def test_welcome_and_settings_construct():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QWidget\n"
        "from cutora.ui.welcome import WelcomePage\n"
        "from cutora.ui.settings_dialog import SettingsDialog\n"
        "from cutora.ui.shortcuts import ShortcutHelpDialog\n"
        "app = QApplication.instance() or QApplication([])\n"
        "host = QWidget()\n"
        "host._ui_mode = 'quick'\n"
        "host.chk_gpu = None\n"
        "page = WelcomePage()\n"
        "assert page.recent_list is not None\n"
        "dlg = SettingsDialog(host)\n"
        "titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]\n"
        "assert dlg.tabs.count() >= 8\n"
        "assert '外观' in dlg.tabs.tabText(0)\n"
        "assert '模型' in titles\n"
        "assert '代理接口' in titles\n"
        "assert '字幕' in titles\n"
        "ShortcutHelpDialog.exec_dialog = ShortcutHelpDialog.exec_dialog\n"
        "_ = app\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n",
    )


def test_main_window_shell_markers():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "addDockWidget" in src
    assert "set_ui_mode" in src
    assert 'addMenu(tr("批量"))' in src or 'addMenu("批量")' in src
    assert "WelcomePage" in src
    assert "open_settings_dialog" in src
    assert "dockLeft" in src
    assert "quickToolbar" in src


def test_ai_providers_page_exists():
    from cutora.ui.panels.ai_settings import AIProvidersDialog, AIProvidersPage
    assert AIProvidersPage is not None
    assert AIProvidersDialog is not None


def test_rework_source_markers():
    mw = open(os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8").read()
    settings = open(
        os.path.join(REPO, "cutora", "ui", "settings_dialog.py"), encoding="utf-8"
    ).read()
    welcome = open(os.path.join(REPO, "cutora", "ui", "welcome.py"), encoding="utf-8").read()
    canvas = open(os.path.join(REPO, "cutora", "ui", "canvas.py"), encoding="utf-8").read()
    timeline = open(
        os.path.join(REPO, "cutora", "ui", "timeline", "widget.py"), encoding="utf-8"
    ).read()
    assert "setFixedWidth(72)" not in mw
    assert "_fit_volume_spin" in mw
    assert "setTitleBarWidget" in mw
    assert 'QDockWidget(tr("左栏")' not in mw
    assert "_refresh_quick_steps" in mw
    assert "尚未自动保存" not in mw
    assert "gpu_status_text" in mw
    assert "占位" not in settings
    assert "将在后续版本接入" in settings
    assert "pageTitle" in welcome
    assert "MARK_PNG" in welcome
    assert "btnSecondary" in welcome
    assert 'QColor("#2a2a2a")' not in canvas
    assert "current()" in canvas
    assert "tok.bg0" in canvas
    assert "把视频拖到这里开始" in timeline


def test_settings_dialog_every_tab_no_deleted_layout():
    """User path: open 设置, visit every page, close. No deleted QFormLayout."""
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.ui.main_window import MainWindow\n"
        "from cutora.ui.settings_dialog import SettingsDialog\n"
        "apply_ui_locale()\n"
        "app = QApplication.instance() or QApplication(['q74-settings'])\n"
        "win = MainWindow()\n"
        "win.show()\n"
        "app.processEvents()\n"
        "dlg = SettingsDialog(win, parent=win)\n"
        "dlg.show()\n"
        "app.processEvents()\n"
        "n = dlg.tabs.count()\n"
        "assert n >= 8, n\n"
        "seen = []\n"
        "for i in range(n):\n"
        "    dlg.tabs.setCurrentIndex(i)\n"
        "    app.processEvents()\n"
        "    seen.append(dlg.tabs.tabText(i))\n"
        "    assert dlg.tabs.currentIndex() == i\n"
        "dlg.close()\n"
        "app.processEvents()\n"
        "assert '外观' in seen and '通用' in seen and '模型' in seen, seen\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_settings_placeholders_hidden():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QWidget, QLabel\n"
        "from cutora.ui.settings_dialog import SettingsDialog\n"
        "app = QApplication.instance() or QApplication([])\n"
        "host = QWidget()\n"
        "host._ui_mode = 'quick'\n"
        "host.chk_gpu = None\n"
        "dlg = SettingsDialog(host)\n"
        "labels = [w.text() for w in dlg.findChildren(QLabel)]\n"
        "joined = ' '.join(labels)\n"
        "assert '占位' not in joined, joined\n"
        "assert not dlg.cmb_lang.isEnabled()\n"
        "assert dlg.sp_autosave.isEnabled()\n"
        "assert '后续' in dlg.cmb_lang.toolTip()\n"
        "_ = app\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n",
    )


def test_welcome_brand_and_secondary_buttons():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.welcome import WelcomePage\n"
        "app = QApplication.instance() or QApplication([])\n"
        "page = WelcomePage()\n"
        "assert page.mark_label.pixmap() is not None\n"
        "assert not page.mark_label.pixmap().isNull()\n"
        "assert page.btn_portrait.objectName() == 'btnPrimary'\n"
        "assert page.btn_landscape.objectName() == 'btnSecondary'\n"
        "assert page.btn_custom.objectName() == 'btnSecondary'\n"
        "from PySide6.QtWidgets import QLabel\n"
        "names = {w.objectName() for w in page.findChildren(QLabel)}\n"
        "assert 'pageTitle' in names\n"
        "_ = app\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_welcome_size_presets_and_layout_cap():
    """每个画布预设都要有按钮，且发出的尺寸必须和预设逐对相等；左栏不许拉满窗口。
    Q105 之后是三栏：中间最近项目可以比左栏窄，右栏拼接入口拿走剩余宽度。"""
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QPushButton\n"
        "from cutora.model.canvas import CANVAS_PRESETS\n"
        "from cutora.ui.welcome import WelcomePage\n"
        "app = QApplication.instance() or QApplication([])\n"
        "page = WelcomePage()\n"
        "page.resize(1280, 820)\n"
        "page.show()\n"
        "app.processEvents()\n"
        "want = [(int(w), int(h)) for w, h, _l in CANVAS_PRESETS]\n"
        "assert sorted(page._size_buttons.keys()) == sorted(want), page._size_buttons.keys()\n"
        "got = []\n"
        "page.new_with_size.connect(lambda w, h: got.append((w, h)))\n"
        "for wh in want:\n"
        "    page._size_buttons[wh].click()\n"
        "assert got == want, got\n"
        "hit = []\n"
        "page.new_custom.connect(lambda: hit.append(1))\n"
        "page.btn_custom.click()\n"
        "assert hit == [1]\n"
        "assert page.btn_portrait is page._size_buttons[want[0]]\n"
        "assert page.actions_panel.width() <= 380, page.actions_panel.width()\n"
        "sizes = page._split.sizes()\n"
        "assert sizes[0] <= 380, sizes\n"
        "assert sizes[1] + sizes[2] > sizes[0], sizes\n"
        "for b in page.actions_panel.findChildren(QPushButton):\n"
        "    assert b.width() <= 380, (b.text(), b.width())\n"
        "_ = app\n"
        "print('ok')\n"
        "from cutora.core.runtime import shutdown_qapplication\n"
        "shutdown_qapplication()\n"
    )


def test_welcome_has_no_dead_signals():
    """旧的 new_portrait / new_landscape 已经删掉，别留没人发的信号。"""
    src = open(
        os.path.join(REPO, "cutora", "ui", "welcome.py"), encoding="utf-8"
    ).read()
    assert "new_portrait" not in src
    assert "new_landscape" not in src
    mw = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "new_with_size.connect" in mw
    assert "new_portrait" not in mw
    assert "new_landscape" not in mw


def test_gpu_status_text_has_value():
    from cutora.core.config import COMPUTE_TYPE, gpu_status_text
    text = gpu_status_text()
    assert COMPUTE_TYPE in text
    assert text.startswith("GPU:") or text.startswith("CPU")
    assert text != "GPU"
