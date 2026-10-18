# -*- coding: utf-8 -*-
"""Q80: export does not create 成品/_verify unless the contact-sheet setting is on.

Integrity checks (duration, frame count, decode, MAD vs preview) still run.
"""
from __future__ import annotations

import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

from cutora.core.config import FFMPEG_PATH


def _ffmpeg():
    return FFMPEG_PATH


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))


def _make_mp4(path, duration=1.0, fps=25, size="64x64", src="color=c=red"):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "%s:s=%s:d=%.2f:r=%d" % (src, size, duration, fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode test clip: %s" % (r.stderr or b"")[-200:])
    assert os.path.isfile(path) and os.path.getsize(path) > 400


def _cells(png_path):
    import cv2
    img = cv2.imread(png_path, cv2.IMREAD_COLOR)
    assert img is not None, png_path
    h, w = img.shape[:2]
    assert w % 3 == 0 and h % 3 == 0, (w, h)
    cw, ch = w // 3, h // 3
    out = []
    for row in range(3):
        for col in range(3):
            out.append(img[row * ch:(row + 1) * ch, col * cw:(col + 1) * cw])
    return out


def test_q80_no_verify_folder_by_default(tmp_path, monkeypatch):
    """A real export into 成品/ must not create _verify when the setting is off."""
    _isolate(monkeypatch, tmp_path)
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.render.export_verify import contact_sheet_enabled, verify_export
    from cutora.render.render_worker import RenderWorker

    _ = QApplication.instance() or QApplication(["q80-no-verify"])
    assert contact_sheet_enabled() is False

    src = str(tmp_path / "src.mp4")
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "testsrc=size=320x180:rate=25:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            src,
        ],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode testsrc: %s" % (r.stderr or b"")[-200:])
    assert os.path.isfile(src) and os.path.getsize(src) > 400

    dest_dir = tmp_path / "成品"
    dest_dir.mkdir()
    out = str(dest_dir / "clip.mp4")
    task = {
        "video": src, "path": src, "duration": 2.0,
        "segments": [], "style": {},
        "timeline": Timeline(320, 180, 25.0),
    }
    calls = []
    real = verify_export

    def _wrap(*args, **kwargs):
        calls.append(os.path.abspath(args[0] if args else kwargs.get("path", "")))
        return real(*args, **kwargs)

    monkeypatch.setattr("cutora.render.export_verify.verify_export", _wrap)
    err = []
    worker = RenderWorker(task, out, "quality", False, export_logs=False)
    worker.error.connect(lambda m: err.append(m))
    worker.run()
    assert os.path.isfile(out) and os.path.getsize(out) > 10 * 1024, (out, err)
    if err:
        text = " | ".join(str(e) for e in err)
        assert "MAD" in text, text
    assert calls, "RenderWorker finished without calling verify_export: %s" % err
    verify_dir = dest_dir / "_verify"
    assert not verify_dir.exists(), os.listdir(dest_dir)


def test_q80_contact_sheet_when_enabled(tmp_path, monkeypatch):
    """Settings checkbox on → verify_export writes a 3×3 non-black sheet."""
    _isolate(monkeypatch, tmp_path)
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
    from cutora.render.export_verify import (
        contact_sheet_enabled, contact_sheet_path, verify_export,
    )
    from cutora.ui.settings_dialog import SettingsDialog
    import cutora.render.parallel as par

    _ = QApplication.instance() or QApplication(["q80-sheet-on"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    host = QWidget()
    host._ui_mode = "quick"
    host.chk_gpu = None
    dlg = SettingsDialog(host)
    assert dlg.chk_contact_sheet.text() == "导出后生成校验缩略图（排查问题用）"
    assert dlg.chk_contact_sheet.isChecked() is False
    dlg.chk_contact_sheet.setChecked(True)
    saved = (par.PARALLEL_EXPORT_ENABLED, par.PARALLEL_EXPORT_WORKERS)
    try:
        dlg._on_ok()
    finally:
        par.PARALLEL_EXPORT_ENABLED, par.PARALLEL_EXPORT_WORKERS = saved
        par._save_parallel_settings(*saved)
    assert contact_sheet_enabled() is True

    src = str(tmp_path / "good.mp4")
    _make_mp4(src, duration=1.0, fps=25, src="color=c=red")
    ok, why = verify_export(src, expected_duration=1.0, fps=25)
    assert ok is True, why
    png = contact_sheet_path(src)
    assert os.path.isfile(png), png
    assert os.path.basename(os.path.dirname(png)) == "_verify"
    cells = _cells(png)
    assert len(cells) == 9
    for i, cell in enumerate(cells):
        mean = float(cell.mean())
        assert mean > 8.0, "cell %d is all-black mean=%.3f" % (i, mean)


def test_q80_integrity_check_still_runs(tmp_path, monkeypatch):
    """An all-black finished file still fails verify_export against a bright preview."""
    _isolate(monkeypatch, tmp_path)
    from cutora.render.export_verify import (
        _extract_rgb_frame, probe_duration_and_frames, verify_export,
    )

    black = str(tmp_path / "black.mp4")
    _make_mp4(black, duration=1.0, fps=25, size="64x64", src="color=c=black")
    dur, frames = probe_duration_and_frames(black)
    assert dur is not None and abs(float(dur) - 1.0) <= 0.5, dur
    assert frames is not None and int(frames) >= 20, frames
    shot = _extract_rgb_frame(black, 0.4, 64, 64)
    assert shot is not None, "could not decode the black fixture"
    assert float(shot.mean()) < 8.0, "fixture is not black, mean=%.3f" % float(shot.mean())

    import numpy as np
    bright = np.full((64, 64, 3), 210, dtype=np.uint8)
    ok, why = verify_export(
        black,
        expected_duration=float(dur),
        expected_frames=int(frames),
        fps=25,
        preview_frames=[(0.4, bright)],
        width=64,
        height=64,
    )
    assert ok is False, why
    assert any("MAD" in s for s in why), why
