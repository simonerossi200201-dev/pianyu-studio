# -*- coding: utf-8 -*-
"""E5: open a same-source-size ≠ canvas project → prompt once, never auto-apply.

Must go through project.open (not the helper alone). Parameters are a real
mismatch (80×160 sources vs 1080×1920 canvas) with two tasks, so a per-task
dialog would fire twice.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
SRC_W, SRC_H = 80, 160
OTHER_W, OTHER_H = 96, 176
CANVAS_W, CANVAS_H = 1080, 1920


def _make_video(path, w, h):
    cmd = [
        FFMPEG, "-y", "-f", "lavfi",
        "-i", "color=c=red:s=%dx%d:d=1" % (w, h),
        "-pix_fmt", "yuv420p", path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _task(video, canvas_wh, sid):
    cw, ch = canvas_wh
    return {
        "video": video,
        "path": video,
        "text": "x",
        "segments": [],
        "timeline": {
            "width": cw,
            "height": ch,
            "fps": 30.0,
            "sources": {
                sid: {
                    "id": sid,
                    "path": video,
                    "duration": 1.0,
                    "has_video": True,
                    "has_audio": False,
                }
            },
            "tracks": [{
                "type": "video",
                "is_main_video": True,
                "clips": [{
                    "source_id": sid,
                    "in_t": 0.0,
                    "out_t": 1.0,
                    "start_t": 0.0,
                }],
            }],
        },
    }


def _write_project(tmpdir, name, tasks):
    proj = os.path.join(tmpdir, name)
    data = {"version": 2, "tasks": tasks}
    with open(proj, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return proj


def _canvas_wh(win):
    tpl = getattr(win, "_timeline_template", None) or {}
    return int(tpl.get("width") or 0), int(tpl.get("height") or 0)


def _dialog_text(call):
    args, kwargs = call
    if len(args) >= 3:
        return str(args[2])
    return str(kwargs.get("text") or kwargs.get("informativeText") or "")


def test_canvas_mismatch_prompts_once(monkeypatch):
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication, QStyleFactory, QMessageBox
    from cutora.app import apply_ui_locale
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import get_bus

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["e5-prompt-once"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    boxes = []
    answer = {"btn": QMessageBox.StandardButton.No}

    def _ask(*a, **k):
        boxes.append((a, k))
        return answer["btn"]

    monkeypatch.setattr(QMessageBox, "question", _ask)
    monkeypatch.setattr("cutora.ui.main_window.QMessageBox.question", _ask)

    tmp = tempfile.mkdtemp(prefix="cutora_e5_once_")
    v_a = _make_video(os.path.join(tmp, "a.mp4"), SRC_W, SRC_H)
    v_b = _make_video(os.path.join(tmp, "b.mp4"), SRC_W, SRC_H)
    v_other = _make_video(os.path.join(tmp, "other.mp4"), OTHER_W, OTHER_H)

    two_task_mismatch = _write_project(tmp, "two_mismatch.asproj.json", [
        _task(v_a, (CANVAS_W, CANVAS_H), "src_a"),
        _task(v_b, (CANVAS_W, CANVAS_H), "src_b"),
    ])
    mixed = _write_project(tmp, "mixed.asproj.json", [
        _task(v_a, (CANVAS_W, CANVAS_H), "src_a"),
        _task(v_other, (CANVAS_W, CANVAS_H), "src_o"),
    ])
    matching = _write_project(tmp, "matching.asproj.json", [
        _task(v_a, (SRC_W, SRC_H), "src_a"),
        _task(v_b, (SRC_W, SRC_H), "src_b"),
    ])

    win = MainWindow()
    bus = get_bus()
    bus.bind_window(win)
    win._offer_autosave_restore = lambda *a, **k: None

    def _open(path, platform, source="ui"):
        monkeypatch.setenv("QT_QPA_PLATFORM", platform)
        bus.execute("project.open", {"path": path}, source=source)

    # Two tasks, same 80×160 sources, 1080×1920 canvas, UI, not offscreen.
    # User says No → one dialog (not two), canvas stays.
    boxes.clear()
    win._canvas_mismatch_asked = False
    answer["btn"] = QMessageBox.StandardButton.No
    _open(two_task_mismatch, "windows", "ui")
    assert len(boxes) == 1, "mismatch must prompt once, got %d: %s" % (
        len(boxes), boxes)
    text = _dialog_text(boxes[0])
    assert "%dx%d" % (SRC_W, SRC_H) in text, text
    assert "%dx%d" % (CANVAS_W, CANVAS_H) in text, text
    assert _canvas_wh(win) == (CANVAS_W, CANVAS_H), (
        "No must keep canvas, got %s" % (_canvas_wh(win),)
    )

    # Same session, open again: still one dialog total (the 一次).
    _open(two_task_mismatch, "windows", "ui")
    assert len(boxes) == 1, "must not prompt a second time, got %d" % len(boxes)
    assert _canvas_wh(win) == (CANVAS_W, CANVAS_H)

    # Fresh ask, user Yes → canvas becomes source size. Not auto: only after Yes.
    boxes.clear()
    win._canvas_mismatch_asked = False
    answer["btn"] = QMessageBox.StandardButton.Yes
    _open(two_task_mismatch, "windows", "ui")
    assert len(boxes) == 1, "Yes path must still prompt, got %d" % len(boxes)
    assert _canvas_wh(win) == (SRC_W, SRC_H), (
        "Yes must apply source size, got %s" % (_canvas_wh(win),)
    )

    # offscreen: never pop, never auto-apply even if the mock would return Yes.
    boxes.clear()
    win._canvas_mismatch_asked = False
    answer["btn"] = QMessageBox.StandardButton.Yes
    _open(two_task_mismatch, "offscreen", "ui")
    assert boxes == [], "offscreen must not pop QMessageBox, got %s" % (boxes,)
    assert _canvas_wh(win) == (CANVAS_W, CANVAS_H), (
        "offscreen must keep canvas, got %s" % (_canvas_wh(win),)
    )

    # Mixed source sizes: do not prompt (condition is 所有源同尺寸).
    boxes.clear()
    win._canvas_mismatch_asked = False
    answer["btn"] = QMessageBox.StandardButton.Yes
    _open(mixed, "windows", "ui")
    assert boxes == [], "mixed source sizes must not prompt, got %s" % (boxes,)
    assert _canvas_wh(win) == (CANVAS_W, CANVAS_H)

    # Sources already match canvas: do not prompt.
    boxes.clear()
    win._canvas_mismatch_asked = False
    _open(matching, "windows", "ui")
    assert boxes == [], "matching canvas must not prompt, got %s" % (boxes,)
    assert _canvas_wh(win) == (SRC_W, SRC_H)
