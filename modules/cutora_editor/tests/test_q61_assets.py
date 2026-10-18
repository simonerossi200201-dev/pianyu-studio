# -*- coding: utf-8 -*-
"""Q61: asset library items actually appear on the picture; import does not freeze."""
from __future__ import annotations

import os
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from q14_media import ffmpeg_run, make_circle_png, make_prores_4444

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = H = 64
MAGENTA = (255, 0, 255)  # RGB
GREEN = (0, 200, 0)


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q61-assets"])


def _ud(tmp_path, monkeypatch):
    root = str(tmp_path / "ud")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setenv("CUTORA_USER_DATA", root)
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    return root


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    hh, ww = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(hh, bpl)[:, : ww * 4].reshape(hh, ww, 4).copy()


def _color_mp4(path, color, w, h, seconds, fps=30):
    ffmpeg_run([
        "-f", "lavfi",
        "-i", "color=c=%s:s=%dx%d:d=%s:r=%d" % (color, w, h, seconds, fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        path,
    ])
    return path


def _wait_until(pred, timeout=15.0, step=0.05):
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(step)
    QApplication.processEvents()
    return bool(pred())


def test_q61_asset_visible_on_screen(tmp_path, monkeypatch):
    """Transparent PNG from the library must show up in an ExportRenderer frame."""
    _app()
    _ud(tmp_path, monkeypatch)
    png = make_circle_png(
        str(tmp_path / "q61_badge.png"), w=W, h=H, radius=22, alpha=255)

    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline
    from cutora.render.export_renderer import ExportRenderer
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer

    rec = import_user_asset(png)
    tl = Timeline(W, H, 30.0)
    task = {
        "timeline": tl,
        "image_overlays": [],
        "text_overlays": [],
        "segments": [],
    }
    place_on_timeline(tl, rec, start_t=0.0, task=task)

    magenta = QColor(MAGENTA[0], MAGENTA[1], MAGENTA[2])

    def _bg(painter):
        painter.fillRect(0, 0, W, H, magenta)

    export = ExportRenderer.render_frame(
        task, W, H, 0.5, draw_background=_bg)
    preview = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    preview.fill(magenta)
    p = QPainter(preview)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(p, task, W, H, 0.5)
    finally:
        p.end()

    exp = _bgra(export)
    pre = _bgra(preview)
    cy, cx = H // 2, W // 2
    # Magenta underlay is B=255,G=0,R=255 in BGRA. White disc drives G up.
    center = exp[cy, cx]
    assert int(center[1]) > 80, (
        "export centre still looks like the magenta underlay (G=%d B=%d R=%d); "
        "the PNG never reached the picture" % (
            int(center[1]), int(center[0]), int(center[2])))
    corner = exp[1, 1]
    assert int(corner[0]) > 200 and int(corner[2]) > 200 and int(corner[1]) < 80, (
        "export corner lost the magenta underlay: BGR=%s" % (corner[:3].tolist(),))
    mad = float(np.abs(exp.astype(np.int16) - pre.astype(np.int16)).mean())
    assert mad < 8.0, "preview vs export MAD=%.2f" % mad


def test_q61_overlay_above_main_video_at_t1(tmp_path, monkeypatch):
    """Transparent ProRes at t=1s must sit above the main video, not under it."""
    _app()
    _ud(tmp_path, monkeypatch)
    png = make_circle_png(
        str(tmp_path / "q61_disc.png"), w=W, h=H, radius=18, alpha=255)
    mov = make_prores_4444(
        png, str(tmp_path / "q61_alpha.mov"), seconds=0.8, profile=4)
    main = _color_mp4(str(tmp_path / "q61_main.mp4"), "green", W, H, 3.0)

    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.preview.engine import PreviewEngine
    from cutora.render.render_worker import RenderWorker
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QImage, QPainter

    rec = import_user_asset(mov)
    tl = Timeline(W, H, 30.0)
    src = MediaSource(main)
    tl.sources[src.id] = src
    vtrack = Track("video")
    vtrack.is_main_video = True
    mclip = Clip(src.id, 0.0, float(src.duration or 3.0), 0.0)
    mclip.fit_mode = "stretch"
    vtrack.clips.append(mclip)
    tl.add_track(vtrack)
    task = {
        "timeline": tl,
        "image_overlays": [],
        "text_overlays": [],
        "segments": [],
        "video": main,
        "duration": 3.0,
    }
    oclip = place_on_timeline(tl, rec, start_t=1.0, task=task)
    assert oclip is not None
    oclip.fit_mode = "stretch"

    t = 1.05
    eng = PreviewEngine()
    try:
        preview = eng.video_frame_at(task, t)
    finally:
        eng.close()

    rw = RenderWorker.__new__(RenderWorker)
    QThread.__init__(rw)
    rw._geom_last_log_t = 0.0
    rw.project = task
    export = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    export.fill(0)
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.readers import _open_video_capture
    caps = {}
    states = {}
    try:
        for tr in video_tracks_for_render(task):
            for clip in getattr(tr, "clips", []) or []:
                sid = getattr(clip, "source_id", None)
                if sid in caps:
                    continue
                src_obj = tl.sources.get(sid)
                path = getattr(src_obj, "path", None) if src_obj is not None else None
                if not path:
                    continue
                cap = _open_video_capture(path, hw=False)
                if cap is not None and cap.isOpened():
                    caps[sid] = cap
                    states[sid] = {"last_time": -1.0, "last_frame": None}
        p = QPainter(export)
        try:
            from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL
            for tr in video_tracks_for_render(task):
                for clip in RenderWorker._find_active_clips_in_track_export(tr, t):
                    sid = getattr(clip, "source_id", None)
                    cap = caps.get(sid)
                    if cap is None:
                        continue
                    blend_name = getattr(clip, "blend_mode", None) or getattr(
                        tr, "blend_mode", "normal")
                    p.save()
                    p.setCompositionMode(BLEND_MODES.get(blend_name, _BLEND_NORMAL))
                    try:
                        rw._draw_video_overlay(
                            p, cap, clip, W, H, t, states.get(sid))
                    finally:
                        p.restore()
        finally:
            p.end()
    finally:
        for cap in caps.values():
            try:
                cap.release()
            except Exception:
                pass

    def _check(name, img):
        arr = _bgra(img)
        corner = arr[2, 2, :3].astype(np.int16)
        center = arr[H // 2, W // 2, :3].astype(np.int16)
        # BGRA: transparent overlay corner must show the green main video.
        assert int(corner[1]) >= 120, (
            "%s t=1s corner is not the main video (BGR=%s); overlay covered it "
            "or never composited" % (name, corner.tolist()))
        assert int(corner[2]) < 80 and int(corner[0]) < 80, (
            "%s t=1s corner not green: BGR=%s" % (name, corner.tolist()))
        # Disc centre must be the white overlay, not the green main plate.
        assert int(center[2]) >= 140 and int(center[1]) >= 140, (
            "%s t=1s centre is not the overlay (BGR=%s); main video is on top "
            "(this is the start_t sort bug if it only fails off t=0)" % (
                name, center.tolist()))

    _check("preview", preview)
    _check("export", export)
    mad = float(np.abs(
        _bgra(preview).astype(np.int16) - _bgra(export).astype(np.int16)).mean())
    assert mad < 12.0, "preview vs export MAD=%.2f" % mad


def test_q61_import_button_exists_and_works(tmp_path, monkeypatch):
    _app()
    _ud(tmp_path, monkeypatch)
    png = make_circle_png(
        str(tmp_path / "q61_import_me.png"), w=32, h=32, radius=10, alpha=255)

    from PySide6.QtWidgets import QFileDialog
    from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES

    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        lambda *a, **k: ([png], ""),
    )
    panel = AssetLibraryPanel()
    btn = getattr(panel, "btn_import", None)
    assert btn is not None, "panel has no 导入 button"
    btn.click()
    keys = [k for k, _ in CATEGORIES]
    sticker = keys.index("sticker")

    def _found():
        panel.tabs.setCurrentIndex(sticker)
        names = [panel.list.item(i).text() for i in range(panel.list.count())]
        return any("q61_import_me" in n for n in names)

    assert _wait_until(_found), (
        "clicked 导入 with a PNG selected but the asset never appeared; names=%s"
        % [panel.list.item(i).text() for i in range(panel.list.count())])


def test_q61_import_does_not_block_ui(tmp_path, monkeypatch):
    _app()
    _ud(tmp_path, monkeypatch)
    big = str(tmp_path / "q61_huge.mov")
    chunk = b"\x00" * (1024 * 1024)
    with open(big, "wb") as fh:
        for _ in range(500):
            fh.write(chunk)
    assert os.path.getsize(big) >= 500 * 1024 * 1024

    from PySide6.QtWidgets import QFileDialog
    from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES

    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        lambda *a, **k: ([big], ""),
    )
    panel = AssetLibraryPanel()
    t0 = time.perf_counter()
    panel.btn_import.click()
    blocked = time.perf_counter() - t0
    assert blocked < 0.2, (
        "importing a 500MB file blocked the UI thread for %.0f ms" % (blocked * 1000.0))

    keys = [k for k, _ in CATEGORIES]
    video = keys.index("video")

    def _found():
        panel.tabs.setCurrentIndex(video)
        if panel.list.count() < 1:
            return False
        rec = panel.list.item(0).data(0x0100) or {}  # Qt.UserRole
        return bool(rec)

    assert _wait_until(_found, timeout=20.0), "500MB import never showed a card"
    from PySide6.QtCore import Qt
    rec = panel.list.item(0).data(Qt.ItemDataRole.UserRole) or {}
    assert rec.get("referenced") is True, rec
    stored = rec.get("stored_path") or rec.get("source_path") or ""
    assert os.path.normcase(os.path.abspath(stored)) == os.path.normcase(
        os.path.abspath(big)), stored


def test_q61_video_thumbnail_not_empty(tmp_path, monkeypatch):
    _app()
    _ud(tmp_path, monkeypatch)
    mp4 = _color_mp4(str(tmp_path / "q61_thumb.mp4"), "red", 48, 48, 0.4)

    from cutora.assets.user import import_user_asset
    from cutora.ui.panels.asset_library import AssetLibraryPanel, CATEGORIES

    rec = import_user_asset(mp4)
    panel = AssetLibraryPanel()
    keys = [k for k, _ in CATEGORIES]
    panel.tabs.setCurrentIndex(keys.index("video"))
    assert panel.list.count() >= 1, "imported mp4 missing from 视频 tab"
    item = panel.list.item(0)

    def _has_red_frame():
        ic = item.icon()
        if ic.isNull():
            return False
        pm = ic.pixmap(72, 72)
        if pm is None or pm.isNull():
            return False
        arr = _bgra(pm.toImage()).astype(np.int16)
        # BGRA of the red mp4: R high, G/B low. Lucide placeholder is warm-grey.
        r, g = arr[:, :, 2], arr[:, :, 1]
        return int((r - g).max()) > 80 and int(r.mean()) > 50

    assert _wait_until(_has_red_frame, timeout=20.0), (
        "video card thumbnail is empty or a placeholder (QPixmap(path) on .mp4/.mov)")
    from PySide6.QtCore import Qt
    got = item.data(Qt.ItemDataRole.UserRole) or {}
    assert got.get("id") == rec.get("id") or "q61_thumb" in (item.text() or "")
