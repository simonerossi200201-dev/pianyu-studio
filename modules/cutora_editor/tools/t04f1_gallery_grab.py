# -*- coding: utf-8 -*-
"""Offscreen grab of the T04f-1 style gallery on real footage."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "CUTORA_USER_DATA" not in os.environ:
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f1_gal_")

from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory
from cutora.subtitle.io import canvas_size
from cutora.app import apply_ui_locale
from cutora.core.commands import get_bus
from cutora.ui.main_window import MainWindow
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.apply import apply_app_qss
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

G08 = os.path.join(
    REPO, "tests", "golden", "g08_stylepack_amber", "project.asproj.json")
VID = r"D:\1_PROJECT\2026-09-07\B\T0012-李良v03\clips\_stitched\T012.mp4"
OUT = os.path.join(REPO, "docs", "plan", "reports", "img")
T_MS = 178000


def _pump(app, ms=80):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _rewrite_paths(obj, video, width, height):
    if isinstance(obj, dict):
        if "width" in obj and "height" in obj and "fps" in obj:
            obj["width"] = width
            obj["height"] = height
        if "duration" in obj and obj.get("has_video"):
            obj["duration"] = 194.0
        for key, val in list(obj.items()):
            if isinstance(val, str) and val.replace("/", "\\").endswith(
                    r"g01\video.mp4"):
                obj[key] = video
            else:
                _rewrite_paths(val, video, width, height)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_paths(item, video, width, height)


def _write_proj(src, dest, video, width, height):
    data = json.loads(open(src, encoding="utf-8").read())
    _rewrite_paths(data, video, width, height)
    for task in data.get("tasks") or []:
        task["video"] = video
        task["path"] = video
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _force_canvas(win, width, height):
    tasks = getattr(win, "tasks", None) or []
    if not tasks:
        return
    task = tasks[0]
    task["dims"] = [width, height]
    tl = task.get("timeline")
    if isinstance(tl, dict):
        tl["width"] = width
        tl["height"] = height
    elif tl is not None:
        try:
            tl.width = width
            tl.height = height
        except Exception:
            pass
    print("canvas_size", canvas_size(task), flush=True)


def _shot(app, win, bus, proj, name, canvas_w, canvas_h):
    bus.execute("project.open", {"path": proj}, source="api")
    _pump(app, 350)
    _force_canvas(win, canvas_w, canvas_h)
    win._timeline_ms = T_MS
    gal = win.style_gallery
    gal.setParent(None)
    gal.setWindowFlags(Qt.WindowType.Window)
    gal.setMinimumSize(720, 920)
    gal.resize(720, 920)
    gal.show()
    _pump(app, 80)
    gal.reload()
    _pump(app, 600)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    gal.grab().save(path, "PNG")
    print("wrote", path, "cards", gal.list.count(),
          "icon", gal.list.iconSize().width(), gal.list.iconSize().height(),
          flush=True)
    return path


def main() -> int:
    if not os.path.isfile(VID):
        print("missing footage", VID, flush=True)
        return 1
    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t04f1-gallery"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    tmp = tempfile.mkdtemp(prefix="cutora_t04f1_proj_")
    v_proj = os.path.join(tmp, "vertical.asproj.json")
    h_proj = os.path.join(tmp, "horizontal.asproj.json")
    _write_proj(G08, v_proj, VID, 1080, 1920)
    _write_proj(G08, h_proj, VID, 1920, 1080)
    win = MainWindow()
    win.show()
    _pump(app, 200)
    win.show_editor()
    win.set_ui_mode("quick", persist=False)
    _pump(app, 200)
    bus = get_bus()
    bus.bind_window(win)
    _shot(app, win, bus, v_proj, "t04f1_fix_gallery_v.png", 1080, 1920)
    _shot(app, win, bus, h_proj, "t04f1_fix_gallery_h.png", 1920, 1080)
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
