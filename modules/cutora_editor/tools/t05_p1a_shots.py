# -*- coding: utf-8 -*-
"""P1-A evidence: gallery pencil + builtin edit bar (save hidden)."""
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
    ("T05_P1A_gallery.png", "T05_P1A_pencil.png"),
    ("T05_P1A_pencil.png", "T05_P1A_builtin_editbar.png"),
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
    _log("SHOT %s %sx%s ok=%s" % (path, pix.width(), pix.height(), ok))
    if not ok or not os.path.isfile(path):
        raise RuntimeError("screenshot failed: %s" % path)
    digest = _sha256(path)
    ev_path = path[:-4] + "_evidence.txt"
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
    da, db = _sha256(pa), _sha256(pb)
    if da == db:
        raise RuntimeError("P1-A shots must differ: %s == %s (%s)" % (a, b, da))
    _log("DIFF_OK %s != %s" % (a, b))


def _pump(app, n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def main() -> int:
    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_p1a_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    from cutora.core.log import setup_logging
    from cutora.core.paths import ensure_user_layout
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    ensure_user_layout()
    setup_logging()
    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t05-p1a"])
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
    win.new_project()
    _pump(app, 12)
    gal = getattr(win, "style_gallery", None)
    if gal is None:
        _log("P1A_FAIL no style_gallery")
        return 2
    win.tabs.setCurrentWidget(gal)
    _pump(app, 12)
    _shot(win, "T05_P1A_gallery.png", head, captured_at)

    packs = list(getattr(gal, "_packs", None) or [])
    builtin = next((p for p in packs if p.get("builtin")), None)
    if builtin is None:
        _log("P1A_FAIL no builtin pack")
        return 2
    from PySide6.QtCore import Qt
    item = None
    path = builtin.get("path")
    for i in range(gal.list.count()):
        it = gal.list.item(i)
        if it is not None and it.data(Qt.ItemDataRole.UserRole) == path:
            item = it
            break
    if item is None:
        item = gal.list.item(0)
    gal.list.scrollToItem(item)
    gal._place_edit_btn(item)
    _pump(app, 8)
    if not gal._edit_btn.isVisible():
        _log("P1A_FAIL pencil not visible")
        return 2
    _shot(win, "T05_P1A_pencil.png", head, captured_at)

    win.dock_right.show()
    win.dock_right.raise_()
    panel = getattr(win, "sub_panel", None)
    if panel is not None and hasattr(win, "tabs"):
        win.tabs.setCurrentWidget(panel)
    if panel is not None and hasattr(panel, "enter_pack_edit"):
        panel.enter_pack_edit(builtin, {})
    _pump(app, 12)
    panel = getattr(win, "sub_panel", None)
    if panel is None or not getattr(panel, "_pack_edit_bar", None):
        _log("P1A_FAIL no edit bar")
        return 2
    if not panel._pack_edit_bar.isVisible():
        _log("P1A_FAIL edit bar hidden")
        return 2
    if panel._pack_edit_save.isVisible():
        _log("P1A_FAIL builtin save button visible")
        return 2
    if not panel._pack_edit_save_as.isVisible():
        _log("P1A_FAIL save-as missing")
        return 2
    _log("BUILTIN_EDIT save_hidden=1 save_as=1 label=%r" % (
        panel._pack_edit_label.text(),))
    _shot(win, "T05_P1A_builtin_editbar.png", head, captured_at)

    for a, b in MUST_DIFFER:
        _assert_differ(a, b)
    _log("P1A_OK")
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
