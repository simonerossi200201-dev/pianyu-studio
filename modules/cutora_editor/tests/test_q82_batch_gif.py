# -*- coding: utf-8 -*-
"""Q82: a batch-loaded GIF is on a video track and shows up in the frame."""
from __future__ import annotations

import os
import struct
import subprocess
import zlib

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

W = H = 64


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q82-gif"])


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


def _solid(r, g, b):
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[:, :, 0] = r
    rgba[:, :, 1] = g
    rgba[:, :, 2] = b
    rgba[:, :, 3] = 255
    return rgba


def _make_green_gif(tmp_path):
    from cutora.core.config import FFMPEG_PATH
    png0 = str(tmp_path / "g0.png")
    png1 = str(tmp_path / "g1.png")
    _write_rgba_png(png0, _solid(0, 210, 0))
    _write_rgba_png(png1, _solid(0, 180, 20))
    pal = str(tmp_path / "pal.png")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", png0, "-vf", "palettegen=reserve_transparent=1", pal],
        check=True, timeout=30,
    )
    list_txt = str(tmp_path / "frames.txt")
    with open(list_txt, "w", encoding="utf-8") as fh:
        fh.write("file '%s'\nduration 0.1\nfile '%s'\nduration 0.1\n" % (
            png0.replace("\\", "/"), png1.replace("\\", "/")))
    gif = str(tmp_path / "batch.gif")
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", list_txt, "-i", pal,
         "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128", gif],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return gif


def _magenta_mp4(path):
    from cutora.core.config import FFMPEG_PATH
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=magenta:s=%dx%d:d=1:r=30" % (W, H),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return path


def _rgb(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    hh, ww = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(hh, bpl)[:, : ww * 3].reshape(hh, ww, 3).copy()


def _center_rgb(img):
    arr = _rgb(img)
    patch = arr[H // 2 - 4: H // 2 + 4, W // 2 - 4: W // 2 + 4]
    return patch.reshape(-1, 3).mean(axis=0)


def _assert_green(img, label):
    r, g, b = (float(x) for x in _center_rgb(img))
    assert g > 100 and g > r + 40 and g > b + 40, (
        "%s centre is not the GIF (RGB %.0f %.0f %.0f)" % (label, r, g, b)
    )


def _compose_export_frame(task, t):
    import threading
    from PySide6.QtCore import Qt, QThread
    from PySide6.QtGui import QImage
    from cutora.model.canvas import canvas_wh
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool
    from cutora.render.textfx.spec import task_has_glass_bg

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
            src_path = None
            if src is not None:
                src_path = getattr(src, "path", None)
                if src_path is None and isinstance(src, dict):
                    src_path = src.get("path")
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
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.black)
    try:
        return rw._compose_export_frame(float(t), canvas, ctx)
    finally:
        video_caps.clear()


def test_q82_batch_gif_visible(tmp_path):
    """Batch-load a two-frame GIF; preview and export both show it, not the plate."""
    _qapp()
    from cutora.batch.script_insert_image import insert_into_tasks
    from cutora.media.animated import classify_animated
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.preview.engine import PreviewEngine
    from cutora.render.split_screen import mean_abs_diff

    gif = _make_green_gif(tmp_path)
    assert classify_animated(gif) == "gif", classify_animated(gif)
    main = _magenta_mp4(str(tmp_path / "plate.mp4"))

    tl = Timeline(W, H, 30.0)
    src = MediaSource(main)
    tl.sources[src.id] = src
    video = Track("video")
    video.is_main_video = True
    plate = Clip(src.id, 0.0, float(src.duration or 1.0), 0.0)
    plate.fit_mode = "stretch"
    video.clips.append(plate)
    tl.add_track(video)
    task = {
        "video": main,
        "duration": 1.0,
        "dims": (W, H),
        "timeline": tl,
        "image_overlays": [],
        "text_overlays": [],
        "segments": [],
    }

    class _Host:
        def _tl_of(self, t):
            return t.get("timeline")

    insert_into_tasks(
        [task], gif, start_sec=0.0, end_sec=0.8, host=_Host(), fit="stretch",
    )

    t = 0.12
    eng = PreviewEngine()
    try:
        preview = eng.frame_at(task, t)
    finally:
        eng.close()
    export = _compose_export_frame(task, t)
    _assert_green(preview, "preview")
    _assert_green(export, "export")
    mad = mean_abs_diff(preview, export)
    assert mad < 0.05, mad
    holders = [
        tr for tr in tl.tracks
        if any(getattr(c, "source_id", None) != src.id for c in (tr.clips or []))
        and getattr(tr, "type", None) == "video"
        and not getattr(tr, "is_main_video", False)
    ]
    assert holders, "GIF clip is not on a video overlay track"
