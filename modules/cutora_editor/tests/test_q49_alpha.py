# -*- coding: utf-8 -*-
"""Q49: recover Q14 — transparent GIF/MOV roundtrip, large-file memory, Q15 gaps."""
from __future__ import annotations

import gc
import os
from types import SimpleNamespace

import numpy as np

from q14_media import (
    make_circle_png,
    make_disposal_gif,
    make_large_prores_4444,
    make_png_sequence,
    make_prores_4444,
    make_static_gif,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GREEN = (0, 200, 0)  # RGB
RSS_LIMIT = 500 * 1024 * 1024  # 500 MB


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q49"])


def _working_set():
    """Current process working-set bytes (not lifetime peak)."""
    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, os.getpid())
    assert handle, "OpenProcess failed"
    try:
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        assert ok, "GetProcessMemoryInfo failed"
        return int(pmc.WorkingSetSize)
    finally:
        k32.CloseHandle(handle)


def _qimage_bgra(img):
    h, w = img.height(), img.width()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8).reshape((h, img.bytesPerLine()))[:, : w * 4]
    return arr.reshape((h, w, 4)).copy()


def _composite_preview(frame, bg_rgb=GREEN):
    """Preview helper path: readers._cv2_frame_to_qimage + QPainter."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.readers import _cv2_frame_to_qimage
    qimg = _cv2_frame_to_qimage(frame)
    assert qimg is not None
    h, w = frame.shape[:2]
    bg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bg.fill(QColor(bg_rgb[0], bg_rgb[1], bg_rgb[2]))
    p = QPainter(bg)
    p.drawImage(0, 0, qimg)
    p.end()
    return _qimage_bgra(bg)


def _composite_export(path, clip, bg_rgb=GREEN, w=None, h=None):
    """Export/preview draw path: RenderWorker._draw_video_overlay."""
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker

    cap = _open_video_capture(path, hw=False)
    assert cap is not None and cap.isOpened(), "export reader failed: %s" % path
    try:
        ok, frame = cap.read()
        assert ok and frame is not None
        if w is None or h is None:
            h, w = frame.shape[:2]
        rw = RenderWorker.__new__(RenderWorker)
        QThread.__init__(rw)
        rw._geom_last_log_t = 0.0
        canvas = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor(bg_rgb[0], bg_rgb[1], bg_rgb[2]))
        p = QPainter(canvas)
        st = {"last_time": -1.0, "last_frame": None}
        try:
            rw._draw_video_overlay(p, cap, clip, int(w), int(h), 0.0, st)
        finally:
            p.end()
        return _qimage_bgra(canvas), frame
    finally:
        cap.release()


def _assert_clear_shows_green(bgra, src_alpha, name):
    """Transparent overlay pixels must show the green underlay, not black/white."""
    clear = src_alpha < 20
    assert int(clear.sum()) > 4, "%s: no transparent pixels to sample" % name
    sample = bgra[clear][:, :3].astype(np.int16)
    # BGRA: B, G, R. Green underlay → G high, R and B low.
    g = sample[:, 1]
    r = sample[:, 2]
    b = sample[:, 0]
    assert int(g.min()) >= 150, "%s transparent region not green (G min=%d); black/white matte?" % (
        name, int(g.min()))
    assert int(r.max()) < 80, "%s transparent region R max=%d (not green)" % (name, int(r.max()))
    assert int(b.max()) < 80, "%s transparent region B max=%d (not green)" % (name, int(b.max()))
    # Explicitly not a black hole or white plate.
    luma = 0.114 * sample[:, 0] + 0.587 * sample[:, 1] + 0.299 * sample[:, 2]
    assert float(luma.min()) > 40.0, "%s looks black in a clear pixel (luma=%.1f)" % (
        name, float(luma.min()))
    assert float(luma.max()) < 240.0, "%s looks white in a clear pixel (luma=%.1f)" % (
        name, float(luma.max()))


def _drop_host(fps=24.0):
    from cutora.model.timeline import Timeline
    tl = Timeline(720, 1280, fps)
    task = {"timeline": tl, "image_overlays": []}

    class Host:
        curr_idx = 0
        tasks = [task]
        _timeline_template = {"width": 720, "height": 1280, "fps": fps}
        timeline = SimpleNamespace(
            _last_drop_target_track=None, position=0, duration=5000,
            set_data=lambda *a, **k: None, update=lambda: None,
        )
        video_canvas = SimpleNamespace(update=lambda: None)
        dropped_images = []
        dropped_videos = []

        def push_undo(self):
            return None

        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

        def _drop_image_file(self, task, tl, path, start_t, target_track=None):
            self.dropped_images.append(path)

        def _drop_video_file(self, task, tl, path, start_t, target_track=None, loop=False):
            self.dropped_videos.append((path, loop))

        def _drop_audio_file(self, *a, **k):
            return None

    return Host()


def test_q49_gif_alpha_roundtrip(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    gif = make_disposal_gif(str(tmp_path / "logo.gif"), size=32)
    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline
    from cutora.render.readers import _open_video_capture

    rec = import_user_asset(gif)
    assert rec["type"] == "animated"
    tl = Timeline(32, 32, 30.0)
    clip = place_on_timeline(tl, rec, start_t=0.0)
    assert clip is not None
    assert getattr(clip, "alpha", False) is True
    src = tl.sources[clip.source_id]
    path = getattr(src, "path", None) or src.get("path")
    clip.fit_mode = "stretch"

    export_bgra, frame = _composite_export(path, clip, w=32, h=32)
    preview_bgra = _composite_preview(frame)
    src_a = frame[..., 3]
    _assert_clear_shows_green(export_bgra, src_a, "export")
    _assert_clear_shows_green(preview_bgra, src_a, "preview")
    mad = float(np.abs(export_bgra.astype(np.int16) - preview_bgra.astype(np.int16)).mean())
    assert mad < 8.0, "preview vs export MAD=%.2f" % mad

    cap = _open_video_capture(path, hw=False)
    try:
        ok, fr = cap.read()
    finally:
        cap.release()
    assert ok and fr.shape[2] == 4
    assert float(np.var(fr[..., 3].astype(np.float64))) > 0


def test_q49_prores4444_alpha(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    png = make_circle_png(str(tmp_path / "disc.png"), w=64, h=64, radius=18, alpha=160)
    mov = make_prores_4444(png, str(tmp_path / "alpha.mov"), seconds=0.2, profile=4)
    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline

    rec = import_user_asset(mov)
    assert rec["type"] == "video"
    assert rec.get("alpha") is True
    tl = Timeline(64, 64, 30.0)
    clip = place_on_timeline(tl, rec, start_t=0.0)
    assert getattr(clip, "alpha", False) is True
    src = tl.sources[clip.source_id]
    path = getattr(src, "path", None) or src.get("path")
    clip.fit_mode = "stretch"

    export_bgra, frame = _composite_export(path, clip, w=64, h=64)
    preview_bgra = _composite_preview(frame)
    src_a = frame[..., 3]
    _assert_clear_shows_green(export_bgra, src_a, "export")
    _assert_clear_shows_green(preview_bgra, src_a, "preview")
    mad = float(np.abs(export_bgra.astype(np.int16) - preview_bgra.astype(np.int16)).mean())
    assert mad < 8.0, "preview vs export MAD=%.2f" % mad


def test_q49_large_file_memory(tmp_path, monkeypatch):
    """1920×1080 30s ProRes 4444 (~1–2 GB) must stream; RSS delta < 500 MB."""
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    cached = os.path.join(REPO, "build", "_q49_large_prores4444.mov")
    if os.path.isfile(cached) and os.path.getsize(cached) >= 700 * 1024 * 1024:
        mov = cached
    else:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        mov = make_large_prores_4444(cached, seconds=30.0, fps=30, timeout=600)
    size = os.path.getsize(mov)
    assert size >= 700 * 1024 * 1024, "fixture too small to prove streaming: %d" % size
    assert size < 3 * 1024 * 1024 * 1024, size

    # Do not encode a 30s VP9 proxy during the RSS window.
    monkeypatch.setattr("cutora.media.alpha_proxy._build_proxy", lambda src: None)

    gc.collect()
    before = _working_set()
    from cutora.assets.user import import_user_asset
    rec = import_user_asset(mov)
    from cutora.render.readers import _open_video_capture
    cap = _open_video_capture(rec["stored_path"], hw=False)
    assert cap is not None and cap.isOpened()
    try:
        got = 0
        for _ in range(8):
            ok, fr = cap.read()
            if not ok or fr is None:
                break
            got += 1
            assert fr.shape[2] == 4
        assert got >= 4, "streamed fewer than 4 frames"
    finally:
        cap.release()
    gc.collect()
    after = _working_set()
    delta = after - before
    assert delta < RSS_LIMIT, (
        "RSS rose by %.1f MB importing a %.1f MB ProRes 4444 (limit 500 MB) — "
        "the file was probably slurped into memory"
        % (delta / (1024 * 1024), size / (1024 * 1024))
    )


def test_q49_static_gif_imports(tmp_path, monkeypatch):
    _app()
    gif = make_static_gif(str(tmp_path / "still.gif"))
    from cutora.media.animated import classify_animated, try_import_animated
    from cutora.ui.main_window import IMG_EXT, MainWindow
    assert classify_animated(gif) is None
    info, err = try_import_animated(gif)
    assert info is None and err is None
    assert ".gif" in IMG_EXT
    host = _drop_host()
    MainWindow._handle_timeline_drop(host, gif, 0.0, "visual")
    assert host.dropped_images == [gif], (
        "static gif was not imported as an image (videos=%s images=%s)"
        % (host.dropped_videos, host.dropped_images)
    )
    assert host.dropped_videos == []


def test_q49_import_error_reported_once(tmp_path, monkeypatch):
    _app()
    gif = make_disposal_gif(str(tmp_path / "boom.gif"), size=16)
    boxes = []

    def _boom(*_a, **_k):
        raise RuntimeError("decode exploded")

    def _warn(parent, title, text, *a, **k):
        boxes.append((title, str(text)))
        return 0

    monkeypatch.setattr("cutora.media.animated.decode_animated", _boom)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", _warn)
    from PySide6.QtWidgets import QWidget
    from cutora.ui.main_window import MainWindow
    host = _drop_host()
    # Product only pops a dialog on a real QWidget. Mix in the drop stub.
    class WidgetHost(QWidget):
        pass
    wh = WidgetHost()
    for name in (
        "curr_idx", "tasks", "_timeline_template", "timeline", "video_canvas",
        "dropped_images", "dropped_videos", "push_undo", "statusBar",
        "_drop_image_file", "_drop_video_file", "_drop_audio_file",
    ):
        setattr(wh, name, getattr(host, name))
    MainWindow._handle_timeline_drop(wh, gif, 0.0, "visual")
    from PySide6.QtWidgets import QApplication
    import time as _time
    deadline = _time.time() + 15.0
    while _time.time() < deadline and len(boxes) == 0:
        QApplication.processEvents()
        _time.sleep(0.02)
    QApplication.processEvents()
    assert len(boxes) == 1, "expected one dialog, got %d: %s" % (len(boxes), boxes)
    assert "decode exploded" in boxes[0][1]
    assert host.dropped_images == [] and host.dropped_videos == []


def test_q49_sequence_fps(tmp_path, monkeypatch):
    from cutora.core.config import FFMPEG_PATH, FFPROBE_PATH
    from cutora.ui.main_window import MainWindow
    pattern, _paths = make_png_sequence(str(tmp_path / "seq"), n=2, w=16, h=16)

    recorded_drop = []
    import cutora.media.animated as _anim
    _real_try = _anim.try_import_animated

    def spy_try(src, ffmpeg=None, fps=None):
        recorded_drop.append(fps)
        return _real_try(src, ffmpeg=ffmpeg, fps=fps)

    monkeypatch.setattr(_anim, "try_import_animated", spy_try)
    host = _drop_host(fps=24.0)
    # Path must exist for _handle_timeline_drop; a real sequence pattern does not.
    dummy = str(tmp_path / "dummy.png")
    make_circle_png(dummy, w=8, h=8, radius=3, alpha=200)
    MainWindow._handle_timeline_drop(host, dummy, 0.0, "visual")
    assert recorded_drop == [24.0], recorded_drop

    from cutora.assets.user import place_on_timeline
    from cutora.model.timeline import Timeline
    tl = Timeline(16, 16, 12.0)
    item = {
        "type": "animated",
        "stored_path": _paths[0],
        "source_path": pattern,
        "alpha": True,
        "name": "seq",
    }
    clip = place_on_timeline(tl, item, start_t=0.0)
    src = tl.sources[clip.source_id]
    path = getattr(src, "path", None) or src.get("path")
    import subprocess
    r = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=20,
    )
    dur = float((r.stdout or "").strip())
    # 2 frames @ 12 fps ≈ 0.166 s; ffmpeg image2 default 25 fps → 0.08 s
    assert abs(dur - (2.0 / 12.0)) < 0.08, dur
    assert abs(dur - (2.0 / 25.0)) > 0.02, dur
    assert abs(dur - (2.0 / 30.0)) > 0.02, dur
    _ = FFMPEG_PATH
