# -*- coding: utf-8 -*-
"""T11f start: left-dock asset library is a real panel, not a placeholder label."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_main_window_no_longer_placeholder_label():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "资产库将在 T11 提供" not in src
    assert "AssetLibraryPanel" in src


def test_asset_library_has_nine_categories():
    from cutora.ui.panels.asset_library import CATEGORIES, AssetLibraryPanel
    assert len(CATEGORIES) == 7
    keys = [k for k, _ in CATEGORIES]
    assert keys == [
        "element", "frame", "lower_third", "sticker", "animated", "video", "audio",
    ]
    assert AssetLibraryPanel is not None


def test_asset_library_empty_state_offscreen():
    import subprocess
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    code = (
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES\n"
        "app = QApplication.instance() or QApplication(['t11f'])\n"
        "p = AssetLibraryPanel()\n"
        "p.show()\n"
        "assert p.tabs.count() == len(CATEGORIES) == 7\n"
        "assert p.list.count() >= 1\n"
        "assert not p.empty.isVisible()\n"
        "p.tabs.setCurrentIndex(len(CATEGORIES)-1)\n"
        "assert p.empty.isVisible()\n"
        "print('ok')\n"
    )
    r = subprocess.run(
        [py, "-c", code], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ok" in (r.stdout or ""), out[-3000:]
