# -*- coding: utf-8 -*-
"""T04e §1 diagnosis: apply 圣咏 offscreen and dump the three checkpoints.

Run:
  CUTORA_DEBUG_STYLEPACK=1 CUTORA_PERF=1 python tools/t04e_diagnose_apply.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G08 = os.path.join(REPO, "tests", "golden", "g08_stylepack_amber", "project.asproj.json")
CHANT = os.path.join(REPO, "resources", "stylepacks", "chant")
KEYS = (
    "font_family", "fontsize", "color_text", "border_width",
    "color_outline", "opacity_bg", "use_box", "use_stroke", "anim_in_type",
)


def _pump(app, ms=40):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _snap(style, layout=None):
    s = style if isinstance(style, dict) else {}
    out = {k: s.get(k) for k in KEYS}
    out["obj_id"] = id(style) if style is not None else 0
    if layout is not None:
        out["layout_rules"] = dict(layout) if isinstance(layout, dict) else layout
    return out


def _dump(tag, win):
    tasks = getattr(win, "tasks", None) or []
    try:
        idx = int(getattr(win, "curr_idx", -1))
    except (TypeError, ValueError):
        idx = -1
    task = tasks[idx] if 0 <= idx < len(tasks) else None
    style = (task or {}).get("style") if isinstance(task, dict) else None
    layout = (task or {}).get("layout_rules") if isinstance(task, dict) else None
    panel = getattr(win, "sub_panel", None)
    pdata = getattr(panel, "current_data", None) if panel is not None else None
    canvas = getattr(win, "video_canvas", None)
    ctask = getattr(canvas, "_task_data", None) if canvas is not None else None
    cstyle = (ctask or {}).get("style") if isinstance(ctask, dict) else None
    row = {
        "tag": tag,
        "curr_idx": idx,
        "task_id": id(task) if task is not None else 0,
        "task_is_list_item": task is tasks[idx] if 0 <= idx < len(tasks) else False,
        "panel_is_task_style": pdata is style,
        "canvas_is_task": ctask is task,
        "task_style": _snap(style, layout),
        "panel_style": _snap(pdata),
        "canvas_style": _snap(cstyle),
        "panel_font_widget": panel.sub_font.currentText() if panel is not None else None,
        "panel_size_widget": panel.sub_size.value() if panel is not None else None,
    }
    print("T04e-diag DUMP " + json.dumps(row, ensure_ascii=False, default=str), flush=True)
    return row


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["CUTORA_DEBUG_STYLEPACK"] = "1"
    os.environ["CUTORA_PERF"] = "1"
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04e_diag_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import get_bus
    from cutora.core.command_audit import audit_path

    app = QApplication.instance() or QApplication(["t04e-diag"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    _pump(app, 80)
    win.show_editor()
    _pump(app, 40)
    assert os.path.isfile(G08), G08
    win.command_project_open(path=G08)
    _pump(app, 200)
    assert win.tasks, "g08 did not load"
    win.curr_idx = 0
    if win.file_list.item(0) is not None:
        win.on_click(win.file_list.item(0))
    _pump(app, 80)

    bus = get_bus()
    bus.bind_window(win)
    print("T04e-diag STEP1_BEFORE", flush=True)
    before = _dump("before", win)

    params = {"path": CHANT, "apply_animation": True, "media_id": 0}
    print("T04e-diag STEP1_EXECUTE " + json.dumps(params, ensure_ascii=False), flush=True)
    result = bus.execute("stylepack.apply", params, source="ui")
    print("T04e-diag STEP1_RESULT " + json.dumps(result, ensure_ascii=False, default=str), flush=True)
    audit = audit_path()
    last = ""
    if os.path.isfile(audit):
        with open(audit, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        last = next((ln for ln in reversed(lines) if '"stylepack.apply"' in ln), "")
    print("T04e-diag STEP1_AUDIT " + last, flush=True)

    print("T04e-diag STEP2_AFTER_CMD", flush=True)
    after = _dump("after_cmd", win)

    task = win.tasks[win.curr_idx]
    pos = float(getattr(win, "_timeline_ms", 0) or 0) / 1000.0
    win.video_canvas.set_data(task, pos)
    win.video_canvas.update()
    win.timeline.update()
    app.processEvents()
    print("T04e-diag STEP3_AFTER_SET_DATA", flush=True)
    mid = _dump("after_set_data", win)

    for _ in range(20):
        win.sync_ui()
        app.processEvents()
    print("T04e-diag STEP3_AFTER_HEARTBEAT", flush=True)
    later = _dump("after_heartbeat", win)

    def _font(row, which):
        return ((row.get(which) or {}) or {}).get("font_family")

    print("T04e-diag VERDICT_INPUTS " + json.dumps({
        "before_font": _font(before, "task_style"),
        "after_font": _font(after, "task_style"),
        "after_set_data_font": _font(mid, "task_style"),
        "after_heartbeat_font": _font(later, "task_style"),
        "panel_is_task_style_after": after.get("panel_is_task_style"),
        "panel_widget_font_after": after.get("panel_font_widget"),
        "canvas_font_after": _font(after, "canvas_style"),
        "canvas_font_heartbeat": _font(later, "canvas_style"),
        "style_obj_changed": (
            (before["task_style"] or {}).get("obj_id")
            != (after["task_style"] or {}).get("obj_id")
        ),
        "after_size": (after["task_style"] or {}).get("fontsize"),
        "heartbeat_size": (later["task_style"] or {}).get("fontsize"),
        "panel_size_widget_after": after.get("panel_size_widget"),
        "panel_size_widget_heartbeat": later.get("panel_size_widget"),
    }, ensure_ascii=False), flush=True)
    try:
        win.media_player.stop()
        win.close()
    except Exception:
        pass
    print("T04e-diag DONE", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
