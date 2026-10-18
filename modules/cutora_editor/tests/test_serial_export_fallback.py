# -*- coding: utf-8 -*-
"""Serial fallback after parallel failed must actually export and verify."""
from __future__ import annotations

import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _tiny_mp4(path, duration=1.0, fps=25, size="320x180"):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "testsrc=size=%s:rate=%d:duration=%.2f" % (size, fps, duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode: %s" % (r.stderr or b"")[-200:])


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["serial-fallback"])


def test_serial_export_path_runs_end_to_end(tmp_path, qapp):
    """Force parallel failed=[0] → serial RenderWorker.run on a 1s clip."""
    src = str(tmp_path / "src.mp4")
    _tiny_mp4(src, duration=2.0, fps=25, size="320x180")
    out = str(tmp_path / "out.mp4")
    from cutora.model.timeline import Timeline
    task = {
        "video": src, "path": src, "duration": 2.0,
        "segments": [], "style": {},
        "timeline": Timeline(320, 180, 25.0),
    }

    from cutora.ui.main_window import MainWindow
    serial_calls = []

    def _serial():
        serial_calls.append("serial")
        from cutora.render.render_worker import RenderWorker
        err = []
        worker = RenderWorker(task, out, "quality", False, export_logs=False)
        worker.error.connect(lambda m: err.append(m))
        worker.run()
        assert os.path.isfile(out) and os.path.getsize(out) > 10 * 1024, out
        if err:
            assert all("MAD" in str(e) for e in err), err

    host = MainWindow.__new__(MainWindow)
    host._parallel_mgr = type("M", (), {"_degrade_reason": ""})()
    host._stop_ui_alive_probe = lambda: None
    host.is_exporting = True
    host._resume_preview_timers = lambda: None
    host.btn_exp = type("B", (), {"setEnabled": lambda *a, **k: None})()
    host.update_ui_state = lambda: None
    host.batch_export_running = True
    host._parallel_indices = [0]
    host.tasks = [task]
    host._batch_output_path = lambda t: out
    host.update_progress_value = lambda *a, **k: None
    host.process_next_export = _serial

    MainWindow._on_parallel_finished(host, [0])
    assert serial_calls == ["serial"]
    from cutora.render.export_verify import verify_export
    ok, why = verify_export(out, expected_duration=2.0, fps=25)
    assert ok is True, why
