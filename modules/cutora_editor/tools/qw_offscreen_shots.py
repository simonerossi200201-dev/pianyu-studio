# -*- coding: utf-8 -*-
"""QW before/after full-canvas + inspector stills. Same theme, same zoom.

    runtime/venv/Scripts/python.exe tools/qw_offscreen_shots.py --tag after
    runtime/venv/Scripts/python.exe tools/qw_offscreen_shots.py --tag before --repo D:\\0_Dev\\CUTORA-qw-before
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, choices=("before", "after"))
    parser.add_argument("--repo", default=REPO_DEFAULT)
    args = parser.parse_args()
    repo = os.path.abspath(args.repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()

    from PySide6.QtWidgets import QApplication, QStyleFactory
    from PySide6.QtCore import QEventLoop, QTimer
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow

    out = os.path.join(REPO_DEFAULT, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)

    def pump(app, ms=250):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    def save(widget, name):
        path = os.path.join(out, name)
        pix = widget.grab()
        if not pix.save(path, "PNG"):
            raise SystemExit("failed to save %s" % path)
        print("wrote", path, pix.width(), "x", pix.height())
        return path

    app = QApplication.instance() or QApplication(sys.argv)
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    win = MainWindow()
    win.show()
    pump(app, 400)
    if hasattr(win, "show_editor"):
        win.show_editor()
    win.resize(1700, 1000)
    pump(app, 300)
    if hasattr(win, "dock_right"):
        from PySide6.QtCore import Qt
        # After = QW 280px. Before = 450c8fe restore width 320.
        width = 280 if args.tag == "after" else 320
        try:
            win.resizeDocks([win.dock_right], [width], Qt.Orientation.Horizontal)
        except Exception:
            pass
        win.dock_right.show()
        win.dock_right.raise_()
    dummy = {
        "type": "text", "content": "Hello",
        "x": 120, "y": 240, "w": 480, "h": 90,
        "font_family": "Arimo", "fontsize": 48,
        "color": "#FFFFFF", "opacity": 255, "rotation": 0,
        "bold": False, "italic": False,
        "use_stroke": True, "stroke_width": 4, "stroke_color": "#000000",
        "bg_enabled": True, "bg_color": "#000000", "bg_opacity": 160,
        "bg_radius": 8, "bg_padding": 12,
        "shadow_enabled": True, "shadow_blur": 6, "shadow_opacity": 180,
        "shadow_offset_x": 4, "shadow_offset_y": 4, "shadow_color": "#000000",
    }
    prop = getattr(win, "prop_panel", None)
    if prop is not None and hasattr(prop, "load_data"):
        try:
            prop.load_data("overlay", dummy)
        except Exception as exc:
            print("prop load_data failed:", exc)
        for name in ("image_basic_group", "ov_text_grp"):
            sec = getattr(prop, name, None)
            if sec is not None and hasattr(sec, "setExpanded"):
                sec.setExpanded(True)
    pump(app, 200)

    save(win, "QW_%s_full.png" % args.tag)

    tabs = getattr(win, "tabs", None)
    names = {
        "字幕样式": "subtitle",
        "属性": "property",
        "角色映射": "roles",
        "字块样式": "wordblock",
    }
    if tabs is not None:
        for i in range(tabs.count()):
            label = tabs.tabText(i)
            slug = names.get(label)
            if not slug:
                continue
            tabs.setCurrentIndex(i)
            pump(app, 250)
            save(win, "QW_%s_%s_full.png" % (args.tag, slug))
            dock = getattr(win, "dock_right", None)
            if dock is not None:
                save(dock, "QW_%s_%s_dock.png" % (args.tag, slug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
