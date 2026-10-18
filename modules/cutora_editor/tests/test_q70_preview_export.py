# -*- coding: utf-8 -*-
"""Q70: real preview≠export locks — split MAD, spatial-off filter, shared audio graph."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# g08 with spatial off. Character-identical to export_audio_filter_complex
# after every clip.spatial_enabled is forced False.
G08_SPATIAL_OFF_FILTER = (
    "[1:a]atrim=start=0.0:duration=20.0,asetpts=N/SR/TB,aresample=48000[a0];"
    "[a0]anull,atrim=end=20.0,afade=t=out:st=19.5:d=0.5,asetpts=N/SR/TB[aout]"
)

G08_FX = {
    "spatial_enabled": True,
    "spatial_preset": "stage",
    "spatial_wet": 0.1,
    "spatial_width": 0.6,
    "mic_texture": True,
    "mic_amount": 0.2,
}


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q70"])


def force_software_decode(monkeypatch):
    """Preview CapturePool defaults hw=True; export is software. Same decoder."""
    import cutora.preview.engine as eng_mod
    orig = eng_mod._open_video_capture
    monkeypatch.setattr(
        eng_mod, "_open_video_capture",
        lambda path, hw=True, alpha_mode=None, threads=None: orig(
            path, hw=False, alpha_mode=alpha_mode, threads=threads),
    )


def compose_export_frame(task, t):
    """One frame through RenderWorker._compose_export_frame (product export)."""
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
            src_path = getattr(src, "path", None) if src else None
            if src_path and os.path.exists(src_path):
                src_paths[sid] = src_path
            try:
                cs = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                if eff <= 0:
                    eff = max(
                        0.0,
                        float(getattr(clip_obj, "out_t", 0.0))
                        - float(getattr(clip_obj, "in_t", 0.0)),
                    )
                ce = cs + eff
            except Exception:
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


def _make_testsrc_mp4(path, which="testsrc", duration=0.6, fps=15, size="96x192"):
    from cutora.core.config import FFMPEG_PATH
    src = "testsrc2" if which == "testsrc2" else "testsrc"
    r = subprocess.run(
        [
            FFMPEG_PATH, "-y", "-f", "lavfi",
            "-i", "%s=size=%s:rate=%d:duration=%.2f" % (src, size, fps, duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 400, (
        r.stderr or "")[-400:]
    return path


def _split_task_two_videos(a_path, b_path, w=96, h=192, fps=15, duration=0.6):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render.split_layout import apply_split_to_task, set_track_cell

    tl = Timeline(w, h, fps)
    src_a = MediaSource(a_path)
    src_b = MediaSource(b_path)
    tl.sources[src_a.id] = src_a
    tl.sources[src_b.id] = src_b
    ta, tb = Track("video"), Track("video")
    ta.clips.append(Clip(src_a.id, 0.0, duration, 0.0))
    tb.clips.append(Clip(src_b.id, 0.0, duration, 0.0))
    tl.add_track(ta)
    tl.add_track(tb)
    task = {
        "timeline": tl, "video": a_path, "segments": [],
        "text_overlays": [], "image_overlays": [],
        "duration": duration, "trim_end_sec": duration,
    }
    apply_split_to_task(task, layout="top_bottom", enabled=True, border={
        "style": "glass", "width_px": 20, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": "straight", "shadow_px": 12, "glass_sigma": 16,
    })
    set_track_cell(task, ta.id, "A")
    set_track_cell(task, tb.id, "B")
    return task


def test_q70_split_preview_vs_real_export(tmp_path, monkeypatch):
    """PreviewEngine vs RenderWorker._compose_export_frame on textured split+glass.

    Turning off the preview-side glass band (see .ok) must make this red.
    """
    _qapp()
    from cutora.preview.engine import PreviewEngine
    from cutora.render.split_screen import mean_abs_diff

    force_software_decode(monkeypatch)
    a = _make_testsrc_mp4(str(tmp_path / "tex_a.mp4"), "testsrc")
    b = _make_testsrc_mp4(str(tmp_path / "tex_b.mp4"), "testsrc2")
    task = _split_task_two_videos(a, b)
    t = 0.2
    eng = PreviewEngine()
    try:
        preview = eng.frame_at(task, t)
        t_snap = eng._snap_t(task, t)
        export = compose_export_frame(task, t_snap)
        mad = mean_abs_diff(preview, export)
        assert mad <= 0.015, mad
    finally:
        eng.close()


def _g08_spatial_off_task():
    from cutora.model.project_io import _normalize_project_data, deserialize_render_task

    path = os.path.join(REPO, "tests", "golden", "g08_fx_static", "project.asproj.json")
    data = json.loads(open(path, encoding="utf-8").read())
    data["tasks"][0]["audio_fx"] = dict(G08_FX)
    _normalize_project_data(data)
    task = deserialize_render_task(data["tasks"][0])
    task["audio_fx"] = dict(G08_FX)
    for tr in task["timeline"].tracks:
        for c in getattr(tr, "clips", []) or []:
            c.spatial_enabled = False
            c.spatial_amount = 0.0
    return task


def test_q70_spatial_off_filter_pinned():
    """g08 with spatial off: export_audio_filter_complex is the dry chain, byte for byte."""
    from cutora.render.render_worker import RenderWorker

    task = _g08_spatial_off_task()
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=True)
    assert tracks, "g08 produced no audio tracks"
    assert fc == G08_SPATIAL_OFF_FILTER, (fc, G08_SPATIAL_OFF_FILTER)
    assert "afir" not in fc
    assert "amovie=" not in fc
    assert "aformat=" not in fc
    assert "volume=" not in fc


def _pcm_md5(inputs, filter_complex, dest):
    from cutora.core.config import FFMPEG_PATH
    cmd = [FFMPEG_PATH, "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += [
        "-filter_complex", filter_complex, "-map", "[aout]",
        "-t", "3", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    assert r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 44, (
        r.stderr or b"").decode("utf-8", "replace")[-400:]
    return hashlib.md5(open(dest, "rb").read()).hexdigest()


def test_q70_spatial_off_audio_md5(tmp_path):
    """Live spatial-off mix vs independently written dry chain: first 3s PCM md5."""
    from cutora.render.render_worker import RenderWorker

    task = _g08_spatial_off_task()
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=True)
    assert tracks
    src = tracks[0]["path"]
    assert os.path.isfile(src), src
    live = _pcm_md5([src, src], fc, str(tmp_path / "live.wav"))
    dry = _pcm_md5([src], G08_SPATIAL_OFF_FILTER.replace("[1:a]", "[0:a]"),
                   str(tmp_path / "dry.wav"))
    assert live == dry, (live, dry)


def test_q70_preview_audio_uses_export_filter():
    """timeline_wav.render_timeline_wav must call export_audio_filter_complex,
    not assemble atrim/afade/atempo itself."""
    path = os.path.join(REPO, "cutora", "asr", "timeline_wav.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename="timeline_wav.py")
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "render_timeline_wav":
            fn = node
            break
    assert fn is not None, "render_timeline_wav missing"
    called = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Name):
            called.add(func.id)
    assert "export_audio_filter_complex" in called, sorted(called)
    literals = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    literals.append(v.value)
    blob = " ".join(literals)
    for needle in ("atrim=", "afade=", "atempo=", "adelay=", "asetrate=", "amix="):
        assert needle not in blob, (needle, blob)
