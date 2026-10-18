# -*- coding: utf-8 -*-
"""Q87: a library GIF on the canvas drags, scales and rotates like a picture.

Animated assets become an alpha video clip on the overlay track. The preview
paints one program frame, so the clip used to have no hit target: a press in
the middle did not move it, and a press on the clear margin still could not
fall through to the picture underneath.
"""
from __future__ import annotations

import os
import struct
import subprocess
import zlib

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

TW, TH = 320, 180
T = 0.2


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q87"])


def _ud(tmp_path, monkeypatch):
    root = str(tmp_path / "ud")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setenv("CUTORA_USER_DATA", root)
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    return root


def _write_rgba_png(path, rgba):
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def _badge(w, h, green=220, margin=40):
    """Opaque green centre, clear margin. Same aspect as the 320x180 canvas."""
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[margin:h - margin, margin:w - margin, 1] = green
    rgba[margin:h - margin, margin:w - margin, 3] = 255
    return rgba


def _make_gif(tmp_path):
    from cutora.core.config import FFMPEG_PATH
    png0 = str(tmp_path / "f0.png")
    png1 = str(tmp_path / "f1.png")
    _write_rgba_png(png0, _badge(160, 90, 220))
    _write_rgba_png(png1, _badge(160, 90, 180))
    pal = str(tmp_path / "pal.png")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", png0, "-vf", "palettegen=reserve_transparent=1", pal],
        check=True, timeout=40,
    )
    list_txt = str(tmp_path / "frames.txt")
    with open(list_txt, "w", encoding="utf-8") as fh:
        fh.write("file '%s'\nduration 0.4\nfile '%s'\nduration 0.4\n" % (
            png0.replace("\\", "/"), png1.replace("\\", "/")))
    gif = str(tmp_path / "badge.gif")
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", list_txt, "-i", pal,
         "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128", gif],
        capture_output=True, timeout=40,
    )
    assert r.returncode == 0, r.stderr
    return gif


def _plate(path):
    from cutora.core.config import FFMPEG_PATH
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=magenta:s=%dx%d:d=1:r=30" % (TW, TH),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path],
        capture_output=True, timeout=40,
    )
    assert r.returncode == 0, r.stderr
    return path


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None


def _mouse(typ, x, y, mods=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    if mods is None:
        mods = Qt.KeyboardModifier.NoModifier
    return QMouseEvent(
        typ, QPointF(float(x), float(y)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, mods,
    )


def _task_with_gif(gif, plate=None):
    from cutora.assets.user import import_user_asset, place_on_timeline
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    tl = Timeline(TW, TH, 30.0)
    main_clip = None
    if plate:
        src = MediaSource(plate)
        tl.sources[src.id] = src
        video = Track("video")
        video.is_main_video = True
        main_clip = Clip(src.id, 0.0, float(src.duration or 1.0), 0.0)
        main_clip.fit_mode = "stretch"
        video.clips.append(main_clip)
        tl.add_track(video)
    task = {
        "timeline": tl,
        "segments": [],
        "style": {},
        "text": "",
        "text_overlays": [],
        "image_overlays": [],
        "duration": 1.0,
        "dims": [TW, TH],
        "video": plate or "",
    }
    rec = import_user_asset(gif)
    assert rec["type"] == "animated", rec.get("type")
    clip = place_on_timeline(tl, rec, start_t=0.0, task=task)
    return task, clip, main_clip


def _arm(task):
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow
    load_embedded_fonts()
    win = MainWindow()
    _silence(win)
    win.tasks = [task]
    win.curr_idx = 0
    win._task_data = task
    win.file_list.addItem("gif", "")
    win.file_list.setCurrentRow(0)
    win.timeline.set_data(1000, task)
    return win


def _show(win, task, t=T):
    """Program frame, the way playback presents the picture, then one paint."""
    from PySide6.QtWidgets import QApplication
    from cutora.preview.engine import PreviewEngine

    canvas = win.video_canvas
    canvas.setParent(None)
    canvas.setFixedSize(TW, TH)
    eng = PreviewEngine()
    try:
        frame = eng.video_frame_at(task, t)
    finally:
        eng.close()
    canvas.set_data(task, t)
    canvas.set_program_frame(frame)
    canvas.show()
    QApplication.processEvents()
    canvas.repaint()
    return canvas


def _compose_export(task, t):
    import threading
    from PySide6.QtCore import Qt, QThread
    from PySide6.QtGui import QImage
    from cutora.model.canvas import canvas_wh
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.preview.engine import PreviewEngine
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool
    from cutora.render.textfx.spec import task_has_glass_bg

    eng = PreviewEngine()
    try:
        t = eng._snap_t(task, t)
    finally:
        eng.close()
    w, h = canvas_wh(task)
    tl = task.get("timeline")
    rw = RenderWorker.__new__(RenderWorker)
    QThread.__init__(rw)
    rw._geom_last_log_t = 0.0
    rw.project = task
    rw._pipe_stop = threading.Event()
    rw._is_running = True
    video_tracks = video_tracks_for_render(task)
    src_paths = {}
    src_last_end = {}
    frame_states = {}
    for tr in video_tracks:
        for clip_obj in getattr(tr, "clips", []) or []:
            sid = getattr(clip_obj, "source_id", None)
            if not sid:
                continue
            src = tl.sources.get(sid) if tl else None
            src_path = getattr(src, "path", None) if src is not None else None
            if src_path and os.path.exists(src_path):
                src_paths[sid] = src_path
            try:
                cs = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                if eff <= 0:
                    eff = max(0.0, float(getattr(clip_obj, "out_t", 0.0))
                              - float(getattr(clip_obj, "in_t", 0.0)))
                ce = cs + eff
            except Exception:
                ce = float("inf")
            if bool(getattr(clip_obj, "loop", False)):
                ce = float("inf")
            src_last_end[sid] = max(src_last_end.get(sid, 0.0), ce)
    video_caps = _ExportCapPool(src_paths, src_last_end, frame_states)
    ctx = ExportComposeCtx(
        project=task, width=w, height=h,
        video_tracks=video_tracks, video_caps=video_caps,
        frame_states=frame_states, fallback_cap=None,
        overlay_path=None, fallback_clip=None,
        render_order=compute_export_render_order(task),
        overlay_index=ExportRenderer.build_overlay_index(task),
        task_glass=task_has_glass_bg(task),
    )
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black)
    try:
        return rw._compose_export_frame(float(t), image, ctx), float(t)
    finally:
        video_caps.clear()


def _preview_at(task, t):
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        return eng.frame_at(task, t)
    finally:
        eng.close()


def _gif_of(task, clip):
    """The same logical clip after undo replaced the timeline objects."""
    tl = task.get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            if getattr(c, "source_id", None) == getattr(clip, "source_id", None):
                if not getattr(tr, "is_main_video", False):
                    return c
    return None


def test_q87_gif_drag_moves_clip(tmp_path, monkeypatch):
    """Press the GIF centre, drag (120, 80): clip.x/y and the export move."""
    _app()
    from PySide6.QtCore import QEvent
    from cutora.render.keyframe import set_point, value_at
    from cutora.render.split_screen import mean_abs_diff

    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path)
    plate = _plate(str(tmp_path / "plate.mp4"))
    task, clip, _main = _task_with_gif(gif, plate)
    x0, y0 = float(clip.x), float(clip.y)
    before, t_snap = _compose_export(task, T)
    cx, cy = TW / 2.0, TH / 2.0

    win = _arm(task)
    try:
        canvas = _show(win, task, t_snap)
        n0 = len(win.history_mgr.history)
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx + 40, cy + 30))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx + 120, cy + 80))
        canvas.mouseReleaseEvent(
            _mouse(QEvent.Type.MouseButtonRelease, cx + 120, cy + 80))
        panel = win.prop_panel
        assert panel.current_type == "video_clip", panel.current_type
        assert panel.stack.currentIndex() == 3
        held = getattr(panel, "_video_clip", None)
        assert held is clip, "overlay GIF did not open the video clip page"
        track = getattr(panel, "_video_track", None)
        assert track is not None and not getattr(track, "is_main_video", True)
        assert abs(float(panel.vid_x.value()) - float(clip.x)) < 2.0
        assert abs(float(panel.vid_y.value()) - float(clip.y)) < 2.0
        sel = canvas._selected_item
        assert sel is not None and sel.get("clip") is clip
    finally:
        win.close()

    dx = float(clip.x) - x0
    dy = float(clip.y) - y0
    # Snap can pull an edge onto the canvas centre (15px). The drag still moved.
    assert abs(dx - 120.0) <= 15.0 and abs(dy - 80.0) <= 15.0, (dx, dy, x0, y0)
    assert abs(dx) > 50.0 and abs(dy) > 40.0
    after, _t2 = _compose_export(task, t_snap)
    moved = mean_abs_diff(before, after)
    assert moved > 0.02, "export frame did not move with the GIF (mad=%.5f)" % moved
    preview = _preview_at(task, t_snap)
    agree = mean_abs_diff(preview, after)
    assert agree < 0.08, "preview and export disagree after the drag (mad=%.5f)" % agree
    added = len(win.history_mgr.history) - n0
    assert added == 1, "one drag must be one undo step, got %s" % added

    win.undo()
    restored = _gif_of(win.tasks[0], clip)
    assert restored is not None
    assert abs(float(restored.x) - x0) < 2.0, restored.x
    assert abs(float(restored.y) - y0) < 2.0, restored.y

    # Keyed x: writing only the static field leaves value_at and the export put.
    task2, clip2, _m2 = _task_with_gif(gif, plate)
    x2 = float(clip2.x)
    set_point(clip2, "x", 0.0, x2)
    set_point(clip2, "x", 1.0, x2)
    v0 = float(value_at(clip2, "x", t_snap, -999.0))
    before2, t2 = _compose_export(task2, T)
    win2 = _arm(task2)
    try:
        canvas = _show(win2, task2, t2)
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx + 120, cy))
        canvas.mouseReleaseEvent(
            _mouse(QEvent.Type.MouseButtonRelease, cx + 120, cy))
    finally:
        win2.close()
    v1 = float(value_at(clip2, "x", t2, -999.0))
    assert abs(v1 - v0) > 50.0, (
        "keyed drag wrote static x=%s but value_at stayed %s" % (clip2.x, v1)
    )
    moved2 = mean_abs_diff(before2, _compose_export(task2, t2)[0])
    assert moved2 > 0.02, "keyed GIF bounced back in the export (mad=%.5f)" % moved2


def test_q87_gif_corner_scale_and_rotate(tmp_path, monkeypatch):
    """Corner drag keeps aspect. The rotate handle changes rotation. Undo each."""
    _app()
    from PySide6.QtCore import QEvent

    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path)
    task, clip, _main = _task_with_gif(gif, None)
    s0 = float(clip.scale)
    sy0 = float(getattr(clip, "scale_y", 1.0) or 1.0)
    r0 = float(clip.rotation)

    win = _arm(task)
    try:
        canvas = _show(win, task, T)
        cx, cy = TW / 2.0, TH / 2.0
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cx, cy))
        assert canvas._selected_item is not None
        rect = canvas._selected_rect
        handles = canvas._resize_handle_rects(rect, with_rotate=True)
        se = handles["se"]
        hx, hy = float(se.center().x()), float(se.center().y())
        n0 = len(win.history_mgr.history)
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, hx, hy))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, hx + 80, hy + 10))
        canvas.mouseReleaseEvent(
            _mouse(QEvent.Type.MouseButtonRelease, hx + 80, hy + 10))
        assert len(win.history_mgr.history) - n0 == 1
        assert abs(float(clip.scale) - s0) > 0.05, (clip.scale, s0)
        sy1 = float(getattr(clip, "scale_y", 1.0) or 1.0)
        assert abs(sy1 / sy0 - 1.0) < 0.02, (sy0, sy1, clip.scale)
        win.undo()
        clip = _gif_of(win.tasks[0], clip)
        task = win.tasks[0]
        assert abs(float(clip.scale) - s0) < 0.02, clip.scale

        canvas = _show(win, task, T)
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cx, cy))
        rect = canvas._selected_rect
        handles = canvas._resize_handle_rects(rect, with_rotate=True)
        assert "rot" in handles, "video clip has no rotate handle"
        rot = handles["rot"].center()
        rx, ry = float(rot.x()), float(rot.y())
        # After undo, history keeps the previous entry and the index steps back,
        # so the next push replaces that tail. Count the index, not the length.
        n1 = win.history_mgr.current_index
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, rx, ry))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, rx + 90, ry + 40))
        canvas.mouseReleaseEvent(
            _mouse(QEvent.Type.MouseButtonRelease, rx + 90, ry + 40))
        assert win.history_mgr.current_index == n1 + 1
        assert abs(float(clip.rotation) - r0) > 8.0, clip.rotation
        win.undo()
        clip = _gif_of(win.tasks[0], clip)
        assert abs(float(clip.rotation) - r0) < 1.0, clip.rotation
    finally:
        win.close()


def test_q87_click_through_empty_area(tmp_path, monkeypatch):
    """The clear margin of the GIF does not select it. The green centre does."""
    _app()
    from PySide6.QtCore import QEvent

    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path)
    plate = _plate(str(tmp_path / "plate.mp4"))
    task, clip, main = _task_with_gif(gif, plate)
    win = _arm(task)
    try:
        canvas = _show(win, task, T)
        recs = list(canvas._hit_records or [])
        kinds = [r.get("type") for r in recs]
        assert any(r.get("clip") is clip for r in recs), kinds
        assert any(r.get("clip") is main for r in recs), kinds

        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 8, 8))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 8, 8))
        sel = canvas._selected_item
        assert sel is None or sel.get("clip") is not clip, (
            "click on the clear margin selected the GIF"
        )
        assert sel is not None and sel.get("clip") is main, sel
        assert win.prop_panel.current_type == "video_clip"
        assert win.prop_panel._video_clip is main

        cx, cy = TW / 2.0, TH / 2.0
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cx, cy))
        sel = canvas._selected_item
        assert sel is not None and sel.get("clip") is clip, sel
        assert win.prop_panel._video_clip is clip
    finally:
        win.close()
