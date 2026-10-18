# -*- coding: utf-8 -*-
"""T11c full-canvas gallery stills: non-hover, same theme, same zoom.

    runtime/venv/Scripts/python.exe tools/_diag/t11c_gallery_stills.py --stage before
    runtime/venv/Scripts/python.exe tools/_diag/t11c_gallery_stills.py --stage after
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t11c_"))

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
WIN_W, WIN_H = 1440, 900
THEME_ID = "roma-gold"


def _head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() or "UNKNOWN"
    except Exception as exc:
        return "UNKNOWN:%s" % exc


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pump(app, n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def _btn_report(gal) -> str:
    lines = []
    hover = getattr(gal, "_hover_item", None)
    lines.append("hover_item=%r" % (None if hover is None else type(hover).__name__))
    btns = list(getattr(gal, "_edit_btns", None) or [])
    single = getattr(gal, "_edit_btn", None)
    if single is not None and single not in btns:
        btns = [single] + btns
    rest = getattr(gal, "EDIT_BTN_REST_OPACITY", None)
    if rest is None:
        rest = getattr(sys.modules.get("cutora.ui.panels.style_gallery"),
                       "EDIT_BTN_REST_OPACITY", None)
    lines.append("EDIT_BTN_REST_OPACITY=%r" % rest)
    lines.append("btn_count=%d" % len(btns))
    for i, btn in enumerate(btns):
        fx = btn.graphicsEffect()
        op = fx.opacity() if fx is not None else None
        lines.append(
            "btn[%d] visible=%s pos=%s size=%s opacity=%s objectName=%s"
            % (i, btn.isVisible(), btn.pos(), btn.size(), op, btn.objectName()))
    return "\n".join(lines)


def _shot(win, path: str, head: str, captured_at: str, extra: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pix = win.grab()
    ok = pix.save(path, "PNG")
    if not ok or not os.path.isfile(path):
        raise RuntimeError("screenshot failed: %s" % path)
    digest = _sha256(path)
    ev_path = path[:-4] + "_evidence.txt"
    with open(ev_path, "w", encoding="utf-8") as fh:
        fh.write("HEAD=%s\n" % head)
        fh.write("captured_at=%s\n" % captured_at)
        fh.write("png=%s\n" % os.path.basename(path))
        fh.write("sha256=%s\n" % digest)
        fh.write("width=%s\n" % pix.width())
        fh.write("height=%s\n" % pix.height())
        fh.write("theme=%s\n" % THEME_ID)
        fh.write("window=%sx%s\n" % (WIN_W, WIN_H))
        fh.write("platform=%s\n" % os.environ.get("QT_QPA_PLATFORM", ""))
        fh.write("hover=0\n")
        fh.write("%s\n" % extra)
    print("SHOT", path, pix.width(), pix.height(), digest, flush=True)
    print("EVIDENCE", ev_path, flush=True)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=("before", "after", "pack_edit"), required=True)
    args = parser.parse_args()

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    from cutora.core.log import setup_logging
    from cutora.core.paths import ensure_user_layout
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss, apply_theme
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    ensure_user_layout()
    setup_logging()
    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t11c-gallery"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_theme(THEME_ID)
    apply_app_qss(app)

    head = _head()
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("HEAD", head, flush=True)
    print("STAGE", args.stage, flush=True)
    print("THEME", THEME_ID, "SIZE", WIN_W, WIN_H, flush=True)

    win = MainWindow()
    win.resize(WIN_W, WIN_H)
    win.show()
    _pump(app, 8)
    win.new_project()
    _pump(app, 12)
    gal = getattr(win, "style_gallery", None)
    if gal is None:
        print("T11C_FAIL no style_gallery", flush=True)
        return 2
    if hasattr(win, "tabs"):
        win.tabs.setCurrentWidget(gal)
    dock = getattr(win, "dock_right", None)
    if dock is not None:
        dock.show()
        dock.raise_()
    gal._hover_item = None
    if getattr(gal, "_hover_timer", None) is not None:
        gal._hover_timer.stop()
    gal.reload()
    _pump(app, 16)
    if hasattr(gal, "_sync_edit_btns"):
        gal._sync_edit_btns()
    elif gal.list.count() > 1:
        gal._place_edit_btn(gal.list.item(1))
    gal._hover_item = None
    _pump(app, 8)

    extra = _btn_report(gal)
    plus = gal.list.item(0) if gal.list.count() else None
    extra += "\nplus_text=%r" % (plus.text() if plus is not None else None)
    if args.stage == "pack_edit":
        class FakeBus:
            def execute(self, name, payload, source="ui"):
                return {}
        win.bus = FakeBus()
        builtin = next(p for p in gal._packs if p.get("builtin"))
        gal._start_edit(builtin)
        _pump(app, 16)
        panel = win.sub_panel
        extra += "\npack_edit_tab=%s" % (win.tabs.currentWidget() is panel)
        extra += "\npack_edit_bar=%s" % panel._pack_edit_bar.isVisible()
        extra += "\npack_edit_label=%r" % panel._pack_edit_label.text()
        extra += "\npack_edit_back=%s" % panel._pack_edit_back.isVisible()
        extra += "\npack_edit_save=%s" % panel._pack_edit_save.isVisible()
        extra += "\npack_edit_save_as=%s" % panel._pack_edit_save_as.isVisible()
        print(extra, flush=True)
        name = "T11c_pack_edit.png"
        path = os.path.join(IMG_DIR, name)
        _shot(win, path, head, captured_at, extra)
        win.close()
        app.quit()
        print("T11C_OK", args.stage, flush=True)
        return 0
    print(extra, flush=True)
    name = "T11c_gallery_%s.png" % args.stage
    path = os.path.join(IMG_DIR, name)
    _shot(win, path, head, captured_at, extra)
    win.close()
    app.quit()
    print("T11C_OK", args.stage, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
