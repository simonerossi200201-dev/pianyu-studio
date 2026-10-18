# -*- coding: utf-8 -*-
"""Q34-1: Shift+S toggles the safe-zone overlay; SHORTCUTS is the source."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_q34_safe_zone_shortcut():
    from cutora.ui.shortcuts import SHORTCUTS

    rows = [r for r in SHORTCUTS if r[0] == "safe_zones"]
    assert len(rows) == 1, "SHORTCUTS must have a safe_zones row"
    sid, key, label, slot = rows[0]
    assert key == "Shift+S", key
    assert "安全区" in label, label
    assert slot == "toggle_safe_zones", slot
    keys = [r[1] for r in SHORTCUTS]
    assert keys.count("Shift+S") == 1, keys

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        [VENV_PY, "-u", os.path.abspath(__file__), "--run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "Q34_SAFE_ZONE_OK" in (proc.stdout or ""), out


def run() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMainWindow
    from cutora.core.history import _load_app_settings, _save_app_settings
    from cutora.render.safe_zones import SETTINGS_MASTER
    from cutora.ui.shortcuts import shortcut_key

    app = QApplication.instance() or QApplication(["q34-safe-zone"])
    key = shortcut_key("safe_zones")
    assert key == "Shift+S", key

    prev = _load_app_settings() or {}
    try:
        start = dict(prev)
        start[SETTINGS_MASTER] = False
        _save_app_settings(start)

        win = QMainWindow()
        act = QAction("安全区参考层", win, checkable=True)
        act.setShortcut(QKeySequence(key))
        act.setChecked(False)

        def _on(on):
            s = _load_app_settings() or {}
            s[SETTINGS_MASTER] = bool(on)
            _save_app_settings(s)

        act.toggled.connect(_on)
        win.addAction(act)
        win.show()
        app.processEvents()
        win.activateWindow()
        win.setFocus()
        QApplication.setActiveWindow(win)
        app.processEvents()

        QTest.keyClick(win, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        s1 = _load_app_settings() or {}
        assert bool(s1.get(SETTINGS_MASTER)) is True, s1.get(SETTINGS_MASTER)

        QTest.keyClick(win, Qt.Key.Key_S, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        s2 = _load_app_settings() or {}
        assert bool(s2.get(SETTINGS_MASTER)) is False, s2.get(SETTINGS_MASTER)
        print("Q34_SAFE_ZONE_OK", flush=True)
        return 0
    finally:
        _save_app_settings(prev)


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        raise SystemExit(run())
    raise SystemExit("use pytest")
