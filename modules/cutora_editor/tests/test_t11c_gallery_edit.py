# -*- coding: utf-8 -*-
"""T11c: rest-visible stylepack pencil + pack edit bar landing."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
if not os.path.isfile(VENV_PY):
    VENV_PY = os.path.join(
        "D:\\0_Dev\\CUTORA", "runtime", "venv", "Scripts", "python.exe")


def _qt_child(code: str, timeout: int = 240) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [VENV_PY, "-c", code],
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


def test_rest_pencil_on_every_pack_and_builtin_edit_bar():
    _qt_child(
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication, QStyleFactory\n"
        "from cutora.app import apply_ui_locale\n"
        "from cutora.core.log import setup_logging\n"
        "from cutora.core.paths import ensure_user_layout\n"
        "from cutora.ui.main_window import MainWindow\n"
        "from cutora.ui.panels.fonts import load_embedded_fonts\n"
        "from cutora.ui.panels.style_gallery import (\n"
        "    EDIT_BTN_HOVER_OPACITY, EDIT_BTN_REST_OPACITY)\n"
        "from cutora.ui.theme.apply import apply_app_qss\n"
        "from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont\n"
        "ensure_user_layout()\n"
        "setup_logging()\n"
        "apply_ui_locale()\n"
        "assert EDIT_BTN_REST_OPACITY >= 0.7\n"
        "assert EDIT_BTN_REST_OPACITY < EDIT_BTN_HOVER_OPACITY\n"
        "assert abs(EDIT_BTN_HOVER_OPACITY - 1.0) < 1e-6\n"
        "app = QApplication.instance() or QApplication(['t11c'])\n"
        "load_embedded_fonts()\n"
        "register_ui_fonts()\n"
        "app.setFont(ui_qfont())\n"
        "app.setStyle(QStyleFactory.create('Fusion'))\n"
        "apply_app_qss(app)\n"
        "win = MainWindow()\n"
        "win.resize(1440, 900)\n"
        "win.show()\n"
        "for _ in range(8):\n"
        "    app.processEvents()\n"
        "win.new_project()\n"
        "for _ in range(12):\n"
        "    app.processEvents()\n"
        "gal = win.style_gallery\n"
        "win.tabs.setCurrentWidget(gal)\n"
        "gal._hover_item = None\n"
        "gal.reload()\n"
        "for _ in range(12):\n"
        "    app.processEvents()\n"
        "pack_items = gal._pack_list_items()\n"
        "visible = [b for b in gal._edit_btns if b.isVisible()]\n"
        "assert len(pack_items) >= 14, len(pack_items)\n"
        "assert len(visible) == len(pack_items), (len(visible), len(pack_items))\n"
        "for btn in visible:\n"
        "    assert btn.objectName() == 'stylepackEditBtn'\n"
        "    fx = btn.graphicsEffect()\n"
        "    assert fx is not None\n"
        "    assert abs(fx.opacity() - EDIT_BTN_REST_OPACITY) < 1e-6, fx.opacity()\n"
        "class FakeBus:\n"
        "    def execute(self, name, payload, source='ui'):\n"
        "        return {}\n"
        "win.bus = FakeBus()\n"
        "builtin = next(p for p in gal._packs if p.get('builtin'))\n"
        "gal._start_edit(builtin)\n"
        "for _ in range(12):\n"
        "    app.processEvents()\n"
        "panel = win.sub_panel\n"
        "assert win.tabs.currentWidget() is panel\n"
        "assert panel._pack_edit_bar.isVisible()\n"
        "assert not panel._pack_edit_save.isVisible()\n"
        "assert panel._pack_edit_save_as.isVisible()\n"
        "assert panel._pack_edit_discard.isVisible()\n"
        "assert panel._pack_edit_back.isVisible()\n"
        "st = panel.pack_edit_state()\n"
        "assert st and st.get('builtin') is True\n"
        "assert st.get('name') and st.get('name') in panel._pack_edit_label.text()\n"
        "assert '正在编辑风格包' in panel._pack_edit_label.text()\n"
        "plus = gal.list.item(0)\n"
        "assert plus is not None\n"
        "assert plus.text() != '＋'\n"
        "assert plus.text() != '+'\n"
        "panel._on_pack_edit_back()\n"
        "for _ in range(6):\n"
        "    app.processEvents()\n"
        "assert win.tabs.currentWidget() is gal\n"
        "win.close()\n"
        "print('ok')\n"
    )
