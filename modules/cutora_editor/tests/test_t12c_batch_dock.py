# -*- coding: utf-8 -*-
"""T12c 提交 1：批量检测结果是左侧 dock 的 tab，不是独立窗口。"""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_batch_results_module_is_not_a_window():
    src = open(
        os.path.join(REPO, "cutora", "ui", "asr_batch_results.py"), encoding="utf-8"
    ).read()
    assert "WindowType.Window" not in src
    assert "setWindowFlag" not in src
    mw = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert 'addTab(\n            self._asr_batch_results' not in mw
    assert 'tr("批量结果")' not in mw


def test_batch_results_lives_in_left_dock():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
import tempfile, os
os.environ['CUTORA_USER_DATA'] = tempfile.mkdtemp(prefix='t12c_dock_')
from cutora.core.paths import refresh_cached_paths
refresh_cached_paths()
from cutora.app import apply_ui_locale
apply_ui_locale()
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyleFactory
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
from cutora.ui.main_window import MainWindow
from cutora.ui.asr_batch_results import AsrBatchResults
app = QApplication.instance() or QApplication(['t12c-dock'])
load_embedded_fonts()
register_ui_fonts()
app.setFont(ui_qfont())
app.setStyle(QStyleFactory.create('Fusion'))
apply_app_qss(app)
win = MainWindow()
win.show_editor()
win.set_ui_mode('pro', persist=False)
texts = [win.left_tabs.tabText(i) for i in range(win.left_tabs.count())]
assert '媒体库' in texts
assert '字幕文本' in texts
assert '资产库' in texts
assert '批量结果' not in texts
win.file_list.addItem('a.mp4', r'C:\a.mp4')
win._show_asr_batch_results([
    {'media_id': 0, 'path': r'C:\a.mp4', 'language': 'en', 'confidence': 0.9, 'warn': False},
])
assert 'en' in win.file_list.item(0).text(0)
assert '90%' in win.file_list.item(0).text(0)
print('ok', flush=True)
from cutora.core.runtime import shutdown_qapplication
shutdown_qapplication()
"""
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "ok" in (proc.stdout or ""), out
    ml = open(os.path.join(REPO, "cutora", "ui", "panels", "media_list.py"),
              encoding="utf-8").read()
    assert "重跑标黄的行" in ml
    assert "重跑纠正过的行" in ml
