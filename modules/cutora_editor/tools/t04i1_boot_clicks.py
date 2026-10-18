# -*- coding: utf-8 -*-
"""Pack gate: start MainWindow, new project, style panel, dock float, theme.

Each PNG is paired with *_evidence.txt (HEAD + timestamp + sha256) from this
same run. Pairs that must differ (theme before/after, float/docked) are
asserted different — identical bytes fail the gate.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
LOG = []
MUST_DIFFER = (
    ("T04i-1_boot_theme_before.png", "T04i-1_boot_theme_after.png"),
    ("T04i-1_boot_dock_float.png", "T04i-1_boot_dock_docked.png"),
)


def _log(msg: str) -> None:
    print(msg, flush=True)
    LOG.append(msg)


def _head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "UNKNOWN"
    except Exception as e:
        return "UNKNOWN:%s" % e


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _shot(win, name: str, head: str, captured_at: str) -> str:
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, name)
    pix = win.grab()
    ok = pix.save(path, "PNG")
    _log("SHOT %s %sx%s ok=%s visible=%s" % (
        path, pix.width(), pix.height(), ok, win.isVisible()))
    if not ok or not os.path.isfile(path):
        raise RuntimeError("screenshot failed: %s" % path)
    digest = _sha256(path)
    ev_path = path[:-4] + "_evidence.txt" if path.lower().endswith(".png") else path + "_evidence.txt"
    with open(ev_path, "w", encoding="utf-8") as fh:
        fh.write("HEAD=%s\n" % head)
        fh.write("captured_at=%s\n" % captured_at)
        fh.write("png=%s\n" % name)
        fh.write("sha256=%s\n" % digest)
        fh.write("width=%s\n" % pix.width())
        fh.write("height=%s\n" % pix.height())
    _log("EVIDENCE %s sha256=%s" % (ev_path, digest))
    return digest


def _assert_differ(a: str, b: str) -> None:
    pa = os.path.join(IMG_DIR, a)
    pb = os.path.join(IMG_DIR, b)
    da = _sha256(pa)
    db = _sha256(pb)
    if da == db:
        raise RuntimeError(
            "gate shots must differ but sha256 matched: %s == %s (%s)"
            % (a, b, da))
    _log("DIFF_OK %s != %s" % (a, b))


def _pump(app, n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def main() -> int:
    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_boot_clicks_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import (
        _enable_faulthandler, _log_startup_identity, apply_ui_locale,
        prepare_faulthandler_log,
    )
    from cutora.core.log import setup_logging
    from cutora.core.paths import ensure_user_layout, user_logs_dir
    from cutora.render import export_guard
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss, apply_theme
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.theme.tokens import current
    from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

    ensure_user_layout()
    setup_logging()
    prepare_faulthandler_log(os.path.join(user_logs_dir(), "faulthandler.log"))
    _enable_faulthandler()
    _log_startup_identity()
    apply_ui_locale()
    export_guard.disable_console_quickedit()
    app = QApplication.instance() or QApplication(["t04i1-boot"])
    plugin = app.platformName()
    _log("PLATFORM %s" % plugin)
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    head = _head()
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _log("HEAD %s" % head)
    _log("CAPTURED_AT %s" % captured_at)

    win = MainWindow()
    win.show()
    _pump(app)
    _log("WINDOW visible=%s title=%r size=%sx%s" % (
        win.isVisible(), str(win.windowTitle()), win.width(), win.height()))
    if not win.isVisible() or win.width() < 200 or win.height() < 200:
        _log("BOOT_FAIL window not really up")
        return 2
    _shot(win, "T04i-1_boot_main.png", head, captured_at)

    win.new_project()
    _pump(app)
    _log("NEW_PROJECT ok path=%r tasks=%s" % (win.project_path, len(win.tasks or [])))
    _shot(win, "T04i-1_boot_new_project.png", head, captured_at)

    win.dock_right.show()
    win.dock_right.raise_()
    if hasattr(win, "tabs") and hasattr(win, "sub_panel"):
        win.tabs.setCurrentWidget(win.sub_panel)
    _pump(app)
    panel = getattr(win, "sub_panel", None)
    vis = bool(panel is not None and panel.isVisible())
    _log("STYLE_PANEL class=%s visible=%s tab=%s" % (
        type(panel).__name__ if panel is not None else None,
        vis,
        win.tabs.tabText(win.tabs.currentIndex()) if hasattr(win, "tabs") else None))
    if panel is None:
        _log("BOOT_FAIL no sub_panel")
        return 2
    _shot(win, "T04i-1_boot_style_panel.png", head, captured_at)

    win.dock_right.setFloating(True)
    _pump(app)
    floating = win.dock_right.isFloating()
    _log("DOCK_FLOAT floating=%s" % floating)
    _shot(win, "T04i-1_boot_dock_float.png", head, captured_at)
    win.dock_right.setFloating(False)
    _pump(app)
    _log("DOCK_DOCKED floating=%s" % win.dock_right.isFloating())
    _shot(win, "T04i-1_boot_dock_docked.png", head, captured_at)

    before_id = current().theme_id
    _log("THEME_BEFORE %s" % before_id)
    _shot(win, "T04i-1_boot_theme_before.png", head, captured_at)
    apply_theme("midnight-blue")
    _pump(app, 12)
    for top in app.topLevelWidgets():
        top.update()
        for child in top.findChildren(QWidget):
            child.update()
    _pump(app, 12)
    after_id = current().theme_id
    _log("THEME_AFTER %s" % after_id)
    if after_id == before_id:
        raise RuntimeError(
            "theme did not change: before=%s after=%s" % (before_id, after_id))
    _shot(win, "T04i-1_boot_theme_after.png", head, captured_at)
    apply_theme("roma-gold")
    _pump(app)

    for a, b in MUST_DIFFER:
        _assert_differ(a, b)

    _log("BOOT_OK")
    win.close()
    app.quit()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
