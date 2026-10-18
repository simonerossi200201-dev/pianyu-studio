# -*- coding: utf-8 -*-
"""Q28: preview + export FPS capsule. Tests written red before the widget."""
from __future__ import annotations

import json
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t28-fps-meter"])


def test_t28_fps_from_ring_buffer():
    from cutora.core.fps_meter import FpsRing

    ring = FpsRing(30)
    t0 = 1000.0
    for i in range(30):
        ring.push(t0 + i * 0.0333)
    fps = ring.fps()
    assert 29.9 <= fps <= 30.1, fps


def test_t28_fps_color_thresholds():
    from cutora.core.fps_meter import color_token
    from cutora.ui.theme.tokens import current

    assert color_token(29.0, 30.0) == "ok"
    assert color_token(23.0, 30.0) == "warn"
    assert color_token(12.0, 30.0) == "err"

    _qapp()
    from cutora.ui.theme.fonts_ui import register_ui_fonts
    from cutora.ui.widgets.fps_meter import FpsMeter

    register_ui_fonts()
    tok = current()
    meter = FpsMeter()
    meter.set_preview(29.0, target=30.0, n=30, force=True)
    assert meter.tone() == "ok"
    assert meter.value_hex().lower() == tok.ok.lower()
    meter.set_preview(23.0, target=30.0, n=30, force=True)
    assert meter.tone() == "warn"
    assert meter.value_hex().lower() == tok.warn.lower()
    meter.set_preview(12.0, target=30.0, n=30, force=True)
    assert meter.tone() == "err"
    assert meter.value_hex().lower() == tok.err.lower()
    src = open(
        os.path.join(REPO, "cutora", "ui", "widgets", "fps_meter.py"),
        encoding="utf-8",
    ).read()
    assert "#7D9B6A" not in src
    assert "#C8913A" not in src
    assert "#B4544A" not in src


def test_t28_fps_throttle():
    _qapp()
    from cutora.ui.widgets.fps_meter import FpsMeter

    clock = [0.0]
    meter = FpsMeter(now=lambda: clock[0])
    for i in range(60):
        clock[0] = i / 59.0
        meter.set_preview(30.0, target=30.0, n=30)
    assert meter.visual_updates <= 3, meter.visual_updates
    assert meter.visual_updates >= 1


def test_t28_fps_meter_hidden_when_idle():
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

    app = _qapp()
    from cutora.ui.widgets.fps_meter import FpsMeter

    win = QMainWindow()
    bar = win.statusBar()
    meter = FpsMeter()
    neighbor = QLabel("status-neighbor")
    bar.addWidget(meter)
    bar.addWidget(neighbor, 1)
    win.resize(640, 80)
    win.show()
    app.processEvents()
    assert meter.isVisible() is False
    x_idle = neighbor.x()
    w_idle = bar.width()
    app.processEvents()
    assert meter.isVisible() is False
    assert neighbor.x() == x_idle
    assert bar.width() == w_idle

    meter.set_preview(29.9, target=30.0, n=30, force=True)
    app.processEvents()
    assert meter.isVisible() is True
    x_shown = neighbor.x()
    assert x_shown >= x_idle

    meter.clear()
    app.processEvents()
    assert meter.isVisible() is False
    assert neighbor.x() == x_idle
    assert bar.width() == w_idle


def test_t28_export_fps_in_progress_file(tmp_path):
    from cutora.core.fps_meter import export_fps
    from cutora.render.parallel import write_worker_progress

    fps = export_fps(90, 2.0)
    assert fps > 0
    path = tmp_path / "w0.progress"
    write_worker_progress(
        str(path),
        done=0,
        cur=40,
        total=1,
        rss_gb=2.71,
        pid=4242,
        fps=fps,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "fps" in data, data
    assert float(data["fps"]) > 0, data

    src = open(
        os.path.join(REPO, "cutora", "render", "parallel.py"), encoding="utf-8"
    ).read()
    body = src[src.index("def _write_progress"): src.index("def _write_progress") + 900]
    assert '"fps"' in body, body

    from cutora.render.parallel import ParallelExportManager

    mgr = ParallelExportManager.__new__(ParallelExportManager)
    mgr._lock = threading.Lock()
    mgr._running = {}
    mgr._finished_infos = []
    mgr._snapshot = {}
    mgr._hb = None
    mgr._done_items = 0
    mgr._failed = []
    mgr._launch_done = False
    mgr._degrade_reason = ""
    mgr._canvas_wh = (720, 1280)
    mgr._ui_owner = None

    class _Proc:
        pid = 999999

        def poll(self):
            return None

    info = {
        "proc": _Proc(),
        "progress_path": str(path),
        "indices": [0],
        "partial": 0,
        "peak_rss": 0.0,
    }
    mgr._running[0] = info
    mgr._sample()
    assert float(info.get("fps") or 0) > 0, info
    assert float(mgr._snapshot.get("fps") or 0) > 0
