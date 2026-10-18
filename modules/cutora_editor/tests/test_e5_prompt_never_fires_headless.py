# -*- coding: utf-8 -*-
"""E5 canvas-mismatch QMessageBox must never fire on unattended paths.

Exemption of _prompt_canvas_if_sources_mismatch in
test_command_run_functions_have_no_dialogs is only valid if this holds.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
SRC_W, SRC_H = 80, 160
CANVAS_W, CANVAS_H = 1080, 1920


def _tiny_project(tmpdir):
    video = os.path.join(tmpdir, "tiny.mp4")
    cmd = [
        FFMPEG, "-y", "-f", "lavfi",
        "-i", "color=c=red:s=%dx%d:d=1" % (SRC_W, SRC_H),
        "-pix_fmt", "yuv420p", video,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    sid = "src_mismatch"
    proj = os.path.join(tmpdir, "mismatch.asproj.json")
    data = {
        "version": 2,
        "tasks": [{
            "video": video,
            "path": video,
            "text": "x",
            "segments": [],
            "timeline": {
                "width": CANVAS_W,
                "height": CANVAS_H,
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
        }],
    }
    with open(proj, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return proj


def _canvas_wh(win):
    tpl = getattr(win, "_timeline_template", None) or {}
    return int(tpl.get("width") or 0), int(tpl.get("height") or 0)


def test_e5_prompt_never_fires_headless(monkeypatch, caplog):
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
    app = QApplication.instance() or QApplication(["e5-headless"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    boxes = []

    def _boom(*a, **k):
        boxes.append((a, k))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", _boom)
    monkeypatch.setattr("cutora.ui.main_window.QMessageBox.question", _boom)

    tmp = tempfile.mkdtemp(prefix="cutora_e5_")
    proj = _tiny_project(tmp)
    win = MainWindow()
    bus = get_bus()
    bus.bind_window(win)

    cases = (
        ("api", "windows"),
        ("cli", "windows"),
        ("ui", "offscreen"),
    )
    caplog.set_level(logging.INFO)
    for source, platform in cases:
        boxes.clear()
        win._canvas_mismatch_asked = False
        monkeypatch.setenv("QT_QPA_PLATFORM", platform)
        bus.execute("project.open", {"path": proj}, source=source)
        assert boxes == [], "QMessageBox fired on source=%s platform=%s: %s" % (
            source, platform, boxes)
        cw, ch = _canvas_wh(win)
        assert (cw, ch) == (CANVAS_W, CANVAS_H), (
            "headless default is keep canvas, got %sx%s after source=%s"
            % (cw, ch, source)
        )
        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "canvas mismatch" in blob.lower() or "画布" in blob, (
            "headless mismatch must log; source=%s log=%r" % (source, blob[-400:])
        )
        caplog.clear()
