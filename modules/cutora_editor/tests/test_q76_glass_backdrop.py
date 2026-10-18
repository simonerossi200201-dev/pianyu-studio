# -*- coding: utf-8 -*-
"""Q76: export glass must sample the current composited video, not source A's last frame.

Two solid clips (red then green) make a frozen first-source frame obvious:
the glass body goes red while the picture on screen is already green.
A 0.5-scale clip parked in the top-left makes a raw source sample obvious:
the glass sits on empty canvas, which is black, while the source pixel
at that canvas position is still red.
"""
from __future__ import annotations

import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

W, H, FPS = 320, 180, 10


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _color_mp4(path, color, frames, size):
    # Temporal noise keeps a flat color from encoding under the 10 KB export
    # floor, without moving the mean off red or green.
    dur = max(int(frames), 1) / float(FPS)
    subprocess.check_call(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=%s:s=%s:r=%d:d=%.4f" % (color, size, FPS, dur),
            "-vf", "noise=alls=6:allf=t",
            "-frames:v", str(int(frames)),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _glass_fx(pad, sigma_em):
    from cutora.subtitle.stylepack import default_fx
    fx = default_fx()
    fx["stroke"]["width_em"] = 0
    fx["glow"]["sigma_em"] = 0
    fx["glow"]["strength"] = 0
    fx["shadow"]["sigma_em"] = 0
    fx["shadow"]["alpha"] = 0
    fx["shadow"]["dx_em"] = 0
    fx["shadow"]["dy_em"] = 0
    # sigma stays small on the transform case so the blur pad cannot reach
    # the scaled clip and paint red into a box that sits on black canvas.
    fx["bg"] = {
        "type": "glass",
        "enabled": True,
        "color": "#0B1A33",
        "alpha": 0.35,
        "radius_em": 0.25,
        "pad_em": list(pad),
        "glass_sigma_em": float(sigma_em),
        "glass_border": False,
        "glass_thickness_em": 0.14,
        "glass_refraction": 0.55,
        "glass_bevel": 0.7,
        "glass_sheen": 0.45,
    }
    return fx


def _style(pos, fontsize, pad, sigma_em):
    return {
        "font_family": "Arimo",
        "fontsize": int(fontsize),
        "bold": True,
        "pos_x": float(pos[0]),
        "pos_y": float(pos[1]),
        "fx": _glass_fx(pad, sigma_em),
    }


def _segment(text, end):
    return {
        "start": 0.0,
        "end": float(end),
        "text": text,
        "lines": [text],
        "words": [{"word": text, "start": 0.0, "end": float(end), "wid": 0}],
    }


def _track_with(clips_sources, w=W, h=H):
    """clips_sources: list of (sid, path, start, dur)."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(int(w), int(h), float(FPS))
    tr = Track("video")
    tr.id = "v0"
    for sid, path, start, dur in clips_sources:
        tl.sources[sid] = {
            "id": sid, "path": path, "duration": float(dur),
            "has_video": True, "has_audio": False,
        }
        clip = Clip(sid, 0.0, float(dur), float(start))
        clip.fit_mode = "stretch"
        tr.clips.append(clip)
    tl.add_track(tr)
    return tl, tr


def _task(tl, style, text, end, video_path):
    return {
        "video": video_path,
        "timeline": tl,
        "style": style,
        "segments": [_segment(text, end)],
        "text_overlays": [],
        "image_overlays": [],
    }


def _export(task, dest, workers):
    from cutora.render.render_worker import RenderWorker
    err = []
    worker = RenderWorker(
        task, dest, "quality", False, export_logs=False, compose_workers=workers)
    worker.error.connect(lambda m: err.append(str(m)))
    worker.run()
    assert os.path.isfile(dest) and os.path.getsize(dest) > 1000, (dest, err)
    return err


def _read_rgb(path, index):
    import cv2
    cap = cv2.VideoCapture(path)
    frame = None
    ok = False
    try:
        for _ in range(int(index) + 1):
            ok, frame = cap.read()
            if not ok:
                break
    finally:
        cap.release()
    assert ok and frame is not None, (path, index)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def _qrgb(img):
    import numpy as np
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, :w * 3]
    return arr.reshape((h, w, 3)).copy()


def _preview_pair(task, t):
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        video = eng.video_frame_at(task, float(t))
        full = eng.frame_at(task, float(t))
        return _qrgb(video), _qrgb(full)
    finally:
        eng.close()


def _glass_body(full, video, diff_min=12):
    import numpy as np
    delta = np.abs(full.astype(np.int16) - video.astype(np.int16)).max(axis=-1)
    white = full.min(axis=-1) > 185
    return (delta >= diff_min) & ~white


def _compose_ctx(task):
    from cutora.model.track_guard import video_tracks_for_render
    from cutora.render.compose_parallel import ExportComposeCtx
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_export_render_order
    from cutora.render.render_worker import RenderWorker, _ExportCapPool
    from cutora.render.textfx.spec import task_has_glass_bg

    rw = RenderWorker(
        task, os.devnull, "quality", False, export_logs=False, compose_workers=1)
    rw.project = task
    rw._geom_last_log_t = 0.0
    tl = task["timeline"]
    states = {}
    paths = {}
    last_end = {}
    tracks = []
    for tr in video_tracks_for_render(task):
        tracks.append(tr)
        for clip in tr.clips or []:
            sid = clip.source_id
            paths[sid] = tl.sources[sid]["path"]
            end = float(clip.start_t) + float(clip.effective_duration)
            last_end[sid] = max(last_end.get(sid, 0.0), end)
    caps = _ExportCapPool(paths, last_end, states)
    ctx = ExportComposeCtx(
        project=task,
        width=int(tl.width),
        height=int(tl.height),
        video_tracks=tracks,
        video_caps=caps,
        frame_states=states,
        fallback_cap=None,
        overlay_path=None,
        fallback_clip=None,
        render_order=compute_export_render_order(task),
        overlay_index=ExportRenderer.build_overlay_index(task),
        task_glass=task_has_glass_bg(task),
    )
    return rw, ctx, caps


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q76-glass"])


def test_q76_glass_follows_second_clip(tmp_path, qapp):
    """t=1.5 is the green clip. Glass body must be green for workers 1 and 2.

    A red-then-green join is the case the frozen first source fails: the
    picture's corner is already green, and the glass stays red.
    """
    import copy
    import numpy as np

    red = str(tmp_path / "red.mp4")
    green = str(tmp_path / "green.mp4")
    _color_mp4(red, "0xFF0000", 10, "%dx%d" % (W, H))
    _color_mp4(green, "0x00FF00", 10, "%dx%d" % (W, H))
    tl, _tr = _track_with([
        ("srcA", red, 0.0, 1.0),
        ("srcB", green, 1.0, 1.0),
    ])
    # Centered cue. Corners stay bare video so the live clip color is visible.
    task = _task(tl, _style((W / 2.0, H / 2.0), 28, (1.2, 0.7), 0.7), "Hi", 2.0, red)
    t = 1.5
    index = int(round(t * FPS))
    video, preview = _preview_pair(task, t)
    mask = _glass_body(preview, video)
    assert int(mask.sum()) > 80, "glass body missing in preview (%d px)" % int(mask.sum())
    prev = preview[mask].astype(np.float64)
    assert float(prev[:, 1].mean()) > float(prev[:, 0].mean()) + 40, (
        "preview glass is not green at t=1.5: mean RGB %s" % prev.mean(axis=0))
    corner = video[4:24, 4:24].astype(np.float64)
    assert float(corner[:, :, 1].mean()) > float(corner[:, :, 0].mean()) + 40, (
        "t=1.5 video layer is not the green clip: %s" % corner.mean(axis=(0, 1)))

    for workers in (1, 2):
        dest = str(tmp_path / ("out_w%d.mp4" % workers))
        err = _export(copy.deepcopy(task), dest, workers)
        mad_err = [e for e in err if "MAD" not in e]
        assert not mad_err, mad_err
        rgb = _read_rgb(dest, index)
        assert rgb.shape == preview.shape, (rgb.shape, preview.shape)
        live = rgb[4:24, 4:24].astype(np.float64)
        assert float(live[:, :, 1].mean()) > float(live[:, :, 0].mean()) + 40, (
            "workers=%d frame is not on the green clip: %s" % (
                workers, live.mean(axis=(0, 1))))
        body = rgb[mask].astype(np.float64)
        gmean, rmean = float(body[:, 1].mean()), float(body[:, 0].mean())
        assert gmean > rmean + 40 and gmean > 60, (
            "workers=%d glass at t=1.5 is not green (mean RGB %s). "
            "A frozen first-clip frame reads red."
            % (workers, body.mean(axis=0)))
        mad = float(np.mean(np.abs(
            preview[mask].astype(np.int16) - rgb[mask].astype(np.int16)))) / 255.0
        assert mad < 0.10, (workers, mad)


def test_q76_glass_backdrop_matches_video_layer(tmp_path, qapp):
    """After the red clip has played, backdrop at t=1.5 matches the video layer.

    The pool still holds source A's last_frame (red) and it was opened first.
    Returning that frame makes the center red while the video layer is green.
    """
    import numpy as np

    red = str(tmp_path / "red.mp4")
    green = str(tmp_path / "green.mp4")
    _color_mp4(red, "0xFF0000", 10, "%dx%d" % (W, H))
    _color_mp4(green, "0x00FF00", 10, "%dx%d" % (W, H))
    tl, _tr = _track_with([
        ("srcA", red, 0.0, 1.0),
        ("srcB", green, 1.0, 1.0),
    ])
    task = _task(tl, _style((W / 2.0, H / 2.0), 28, (1.2, 0.7), 0.7), "Hi", 2.0, red)
    rw, ctx, caps = _compose_ctx(task)
    try:
        video = backdrop = None
        for i in range(int(1.5 * FPS) + 1):
            video, backdrop = rw._compose_video_frame(i / float(FPS), ctx)
        states = list(ctx.frame_states.values())
        assert len(states) >= 2, [s.get("last_frame") is not None for s in states]
        first = states[0].get("last_frame")
        assert first is not None
        # BGR: a frozen red frame keeps a high red channel after the clip ends.
        red_mean = float(np.asarray(first).reshape(-1, first.shape[-1])[:, 2].mean())
        assert red_mean > 100, red_mean
        assert video is not None and backdrop is not None
        assert backdrop.width() == video.width() and backdrop.height() == video.height()
        cx, cy = video.width() // 2, video.height() // 2
        vp, bp = video.pixelColor(cx, cy), backdrop.pixelColor(cx, cy)
        assert vp.green() > vp.red() + 40, (vp.red(), vp.green(), vp.blue())
        assert abs(bp.red() - vp.red()) <= 1
        assert abs(bp.green() - vp.green()) <= 1
        assert abs(bp.blue() - vp.blue()) <= 1
    finally:
        caps.clear()


def test_q76_glass_respects_transform(tmp_path, qapp):
    """Scale 0.5, parked top-left. Glass on the empty canvas must not see the source.

    Source is solid red, so every source pixel is red. The canvas under the
    cue is black. Sampling the raw frame paints red glass; sampling the
    composited video paints a dark tint of black.
    """
    import numpy as np

    # 640×360, not 320×180: a 0.5 clip is mostly empty canvas, and that
    # frame encodes under the 10 KB floor at the smaller size.
    cw, ch = 640, 360
    red = str(tmp_path / "red.mp4")
    _color_mp4(red, "0xFF0000", 30, "%dx%d" % (cw, ch))
    tl, tr = _track_with([("src", red, 0.0, 3.0)], cw, ch)
    clip = tr.clips[0]
    clip.scale = 0.5
    # stretch + scale 0.5 is centered; shift by a quarter canvas to pin top-left.
    clip.x = -cw * 0.25
    clip.y = -ch * 0.25
    task = _task(
        tl, _style((520, 300), 18, (1.3, 0.7), 0.25), "I", 3.0, red)
    t = 0.2
    video, preview = _preview_pair(task, t)
    assert int(video[30, 30, 0]) > 140, video[30, 30].tolist()
    assert int(video[300, 560, 0]) < 30, video[300, 560].tolist()
    mask = _glass_body(preview, video, diff_min=8)
    assert int(mask.sum()) > 80, int(mask.sum())
    # Video quad is the top-left 320×180. Glass must sit entirely outside it.
    assert int(mask[:180, :320].sum()) == 0, "glass overlapped the scaled clip"
    src = _read_rgb(red, 0)
    ys, xs = np.where(mask)
    vis = video[ys, xs].astype(np.float64).mean(axis=0)
    raw = src[np.clip(ys, 0, src.shape[0] - 1), np.clip(xs, 0, src.shape[1] - 1)]
    raw = raw.astype(np.float64).mean(axis=0)
    prev = preview[ys, xs].astype(np.float64).mean(axis=0)

    def _l1(a, b):
        return float(np.abs(a - b).sum())

    assert _l1(prev, vis) + 20 < _l1(prev, raw), (prev, vis, raw)
    assert float(prev[0]) < 90, prev

    dest = str(tmp_path / "out.mp4")
    err = _export(task, dest, 1)
    mad_err = [e for e in err if "MAD" not in e]
    assert not mad_err, mad_err
    rgb = _read_rgb(dest, int(round(t * FPS)))
    assert int(rgb[30, 30, 0]) > 140, rgb[30, 30].tolist()
    body = rgb[ys, xs].astype(np.float64).mean(axis=0)
    assert float(body[0]) < 90, body
    assert _l1(body, vis) + 20 < _l1(body, raw), (body, vis, raw)
    mad = float(np.mean(np.abs(
        preview[ys, xs].astype(np.int16) - rgb[ys, xs].astype(np.int16)))) / 255.0
    assert mad < 0.10, mad
