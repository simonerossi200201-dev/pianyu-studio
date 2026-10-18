# -*- coding: utf-8 -*-
"""Q88: GIF decode-once, preview off UI thread, persist, project assets, pack."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import time
import zlib

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

W, H = 160, 280
FPS = 30.0


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q88"])


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


def _solid(w, h, rgb, a=255):
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = rgb[0]
    rgba[:, :, 1] = rgb[1]
    rgba[:, :, 2] = rgb[2]
    rgba[:, :, 3] = a
    return rgba


def _make_gif(tmp_path, n=10, size=32):
    from cutora.core.config import FFMPEG_PATH
    pngs = []
    for i in range(n):
        g = 40 + i * 18
        p = str(tmp_path / ("f%02d.png" % i))
        _write_rgba_png(p, _solid(size, size, (20, g, 200)))
        pngs.append(p)
    pal = str(tmp_path / "pal.png")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", pngs[0], "-vf", "palettegen=reserve_transparent=1", pal],
        check=True, timeout=40,
    )
    lst = str(tmp_path / "frames.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in pngs:
            fh.write("file '%s'\nduration 0.1\n" % p.replace("\\", "/"))
        fh.write("file '%s'\n" % pngs[-1].replace("\\", "/"))
    gif = str(tmp_path / "loop.gif")
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", lst, "-i", pal,
         "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128", gif],
        capture_output=True, timeout=40,
    )
    assert r.returncode == 0, r.stderr
    return gif


def _plate(path, seconds=2.0):
    from cutora.core.config import FFMPEG_PATH
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi",
         "-i", "color=c=magenta:s=%dx%d:d=%.2f:r=30" % (W, H, seconds),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path],
        capture_output=True, timeout=40,
    )
    assert r.returncode == 0, r.stderr
    return path


def _task_with_gif(gif, plate, dur=3.0):
    from cutora.assets.user import place_on_timeline
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    tl = Timeline(W, H, FPS)
    src = MediaSource(plate)
    src.has_audio = False
    tl.sources[src.id] = src
    tr = Track("video")
    tr.is_main_video = True
    clip = Clip(src.id, 0.0, float(src.duration or dur), 0.0)
    clip.fit_mode = "stretch"
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {
        "timeline": tl, "video": plate, "path": plate,
        "segments": [], "text_overlays": [], "image_overlays": [],
        "duration": dur, "style": {},
    }
    rec = {"type": "animated", "stored_path": gif, "source_path": gif, "alpha": True}
    gclip = place_on_timeline(tl, rec, start_t=0.0, task=task)
    gclip.loop = True
    gclip.match_duration = True
    gclip._match_target_dur = dur
    gclip.size_mode = "source"
    return task, gclip


def _pct(xs, p):
    ys = sorted(float(x) for x in xs)
    if not ys:
        return 0.0
    k = (len(ys) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ys) - 1)
    t = k - lo
    return ys[lo] * (1.0 - t) + ys[hi] * t


def test_q88_gif_decoded_once(tmp_path, monkeypatch):
    """10-frame GIF looped 3 times: decode_clip_frame runs 10 times, not 30."""
    _qapp()
    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path, n=10, size=48)
    plate = _plate(str(tmp_path / "plate.mp4"), seconds=3.0)
    task, gclip = _task_with_gif(gif, plate, dur=3.0)
    from cutora.media.frame_cache import clear, reset_decode_stats
    from cutora.preview.engine import PreviewEngine
    clear()
    reset_decode_stats()
    eng = PreviewEngine()
    try:
        n_src = 10
        # 0.1 s per GIF frame. 3 loops of the 10 unique frames.
        for i in range(n_src * 3):
            eng.frame_at(task, i * 0.1)
    finally:
        eng.close()
    from cutora.media import frame_cache as fc
    unique = len(fc.DECODED_KEYS)
    assert fc.DECODE_CALLS == unique, (
        "decode_clip_frame ran %d times for %d unique frames (loop re-decoded)"
        % (fc.DECODE_CALLS, unique))
    assert unique <= n_src + 2, (
        "decoded %d unique frames, GIF has %d"
        % (unique, n_src))
    assert unique >= n_src - 2, "only decoded %d frames of a %d-frame GIF" % (unique, n_src)


def test_q88_preview_off_ui_thread(tmp_path, monkeypatch):
    """While the worker is composing, the GUI event loop stays under 16 ms p95."""
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer
    _qapp()
    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path, n=6, size=64)
    plate = _plate(str(tmp_path / "plate.mp4"), seconds=2.0)
    task, _clip = _task_with_gif(gif, plate, dur=2.0)

    from cutora.preview.worker import PreviewWorker
    delays = []
    stamps = []

    class _Probe(QObject):
        def event(self, ev):
            if ev.type() == QEvent.Type.User and stamps:
                delays.append((time.perf_counter() - stamps.pop(0)) * 1000.0)
                return True
            return super().event(ev)

    probe = _Probe()
    worker = PreviewWorker()
    worker.start()
    app = _qapp()
    for _ in range(80):
        if worker.engine is not None:
            break
        app.processEvents()
        time.sleep(0.02)
    assert worker.engine is not None, "preview worker never created its engine"
    real_at = worker.engine.frame_at

    def _slow(task2, t2, preview_scale=1.0, _real=real_at):
        time.sleep(0.05)
        return _real(task2, t2, preview_scale=preview_scale)

    worker.engine.frame_at = _slow
    worker.request(task, 0.0, scale=1.0)
    app.processEvents()
    time.sleep(0.35)
    app.processEvents()
    n = [0]

    def _tick():
        t = (n[0] % 20) / 30.0
        n[0] += 1
        stamps.append(time.perf_counter())
        QCoreApplication.postEvent(probe, QEvent(QEvent.Type.User))
        worker.request(task, t, scale=1.0)

    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(_tick)
    timer.start()
    loop = QEventLoop()
    QTimer.singleShot(800, loop.quit)
    loop.exec()
    timer.stop()
    app.processEvents()
    worker.shutdown(2000)
    assert len(delays) >= 20, "event loop did not tick enough: %d" % len(delays)
    p95 = _pct(delays, 95)
    assert p95 < 16.0, "UI event-loop delay p95=%.2f ms (want < 16)" % p95


def test_q88_gif_survives_cache_clear(tmp_path, monkeypatch):
    """Save a GIF clip, wipe the cache dir, reopen: preview and export still show it."""
    _qapp()
    ud = _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path, n=4, size=40)
    plate = _plate(str(tmp_path / "plate.mp4"), seconds=1.0)
    task, gclip = _task_with_gif(gif, plate, dur=1.0)
    from cutora.core.paths import user_cache_dir
    from cutora.model.project_io import serialize_render_task
    from cutora.preview.engine import PreviewEngine
    from cutora.render.split_screen import mean_abs_diff

    proj = str(tmp_path / "proj" / "job.cutora")
    os.makedirs(os.path.dirname(proj), exist_ok=True)
    data = {"version": 2, "tasks": [serialize_render_task(task)]}
    with open(proj, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.write("\n")

    cache = user_cache_dir()
    anim = os.path.join(cache, "anim")
    if os.path.isdir(anim):
        shutil.rmtree(anim, ignore_errors=True)
    # Also wipe anything under cache that looks like a transcode.
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            if "anim" in name.lower():
                shutil.rmtree(os.path.join(cache, name), ignore_errors=True)

    from cutora.model.project_io import deserialize_render_task
    from cutora.model.project_media import rebuild_animated_missing, resolve_relative_paths
    with open(proj, encoding="utf-8") as fh:
        loaded = json.load(fh)
    resolve_relative_paths(loaded["tasks"], proj)
    rebuild_animated_missing(loaded["tasks"])
    task2 = deserialize_render_task(loaded["tasks"][0])

    eng = PreviewEngine()
    try:
        prev = eng.frame_at(task2, 0.2)
    finally:
        eng.close()
    assert prev is not None and not prev.isNull()
    # Magenta plate is (255,0,255). The GIF is teal-ish; centre must not stay magenta.
    c = prev.pixelColor(W // 2, H // 2)
    assert not (c.red() > 200 and c.blue() > 200 and c.green() < 40), (
        "GIF vanished after cache clear: centre %d %d %d" % (c.red(), c.green(), c.blue()))

    from test_q82_batch_gif import _compose_export_frame
    exp = _compose_export_frame(task2, 0.2)
    assert mean_abs_diff(prev, exp) < 0.08


def test_q88_project_mode_asset_relative(tmp_path, monkeypatch):
    """Project-mode import lands in assets/ with a relative path; moving the folder still opens."""
    _qapp()
    _ud(tmp_path, monkeypatch)
    proj_dir = str(tmp_path / "job")
    os.makedirs(proj_dir, exist_ok=True)
    png = str(tmp_path / "sticker.png")
    _write_rgba_png(png, _solid(24, 24, (10, 200, 40)))
    from cutora.assets.user import import_user_asset, iter_user_assets
    rec = import_user_asset(
        png, storage_mode="project", project_dir=proj_dir)
    assert rec["storage_mode"] == "project"
    stored = rec["stored_path"]
    assert os.path.normcase(os.path.abspath(proj_dir)) in os.path.normcase(
        os.path.abspath(stored))
    assert os.path.isfile(stored)
    rel_assets = os.path.join(proj_dir, "assets")
    assert os.path.normcase(os.path.abspath(stored)).startswith(
        os.path.normcase(os.path.abspath(rel_assets)))

    listed = iter_user_assets("sticker", project_dir=proj_dir)
    assert any(it.get("id") == rec["id"] for it in listed)
    listed_lib = iter_user_assets("sticker", project_dir=None)
    assert not any(it.get("id") == rec["id"] for it in listed_lib)

    plate = _plate(str(tmp_path / "plate.mp4"), seconds=0.5)
    shutil.copy2(plate, os.path.join(proj_dir, "plate.mp4"))
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.model.project_io import serialize_render_task
    from cutora.model.project_media import relativize_under_project, resolve_relative_paths

    tl = Timeline(W, H, FPS)
    src = MediaSource(os.path.join(proj_dir, "plate.mp4"))
    tl.sources[src.id] = src
    tr = Track("video")
    tr.is_main_video = True
    tr.clips.append(Clip(src.id, 0.0, 0.5, 0.0))
    tl.add_track(tr)
    task = {
        "timeline": tl, "video": os.path.join(proj_dir, "plate.mp4"),
        "path": os.path.join(proj_dir, "plate.mp4"),
        "image_overlays": [{
            "type": "image", "path": stored,
            "start": 0.0, "end": 0.5, "x": 0, "y": 0, "w": W, "h": H,
            "elements": [{"kind": "image", "path": stored, "x": 10, "y": 10, "w": 24, "h": 24}],
        }],
        "duration": 0.5,
    }
    packed = {"version": 2, "tasks": [serialize_render_task(task)]}
    relativize_under_project(packed["tasks"], os.path.join(proj_dir, "job.cutora"))
    ov_path = packed["tasks"][0]["image_overlays"][0]["elements"][0]["path"]
    assert not os.path.isabs(ov_path), ov_path
    assert "assets" in ov_path.replace("\\", "/")

    dest = tmp_path / "moved"
    shutil.copytree(proj_dir, dest)
    moved_proj = str(dest / "job.cutora")
    with open(moved_proj, "w", encoding="utf-8") as fh:
        json.dump(packed, fh)
    with open(moved_proj, encoding="utf-8") as fh:
        loaded = json.load(fh)
    resolve_relative_paths(loaded["tasks"], moved_proj)
    el = loaded["tasks"][0]["image_overlays"][0]["elements"][0]["path"]
    assert os.path.isfile(el), el


def test_q88_pack_project(tmp_path, monkeypatch):
    """Packed folder is self-contained: delete the originals, still open and export."""
    _qapp()
    _ud(tmp_path, monkeypatch)
    gif = _make_gif(tmp_path, n=3, size=32)
    plate = _plate(str(tmp_path / "plate.mp4"), seconds=0.6)
    task, _c = _task_with_gif(gif, plate, dur=0.6)
    from cutora.model.project_io import serialize_render_task
    from cutora.model.project_pack import pack_tasks
    from cutora.model.project_media import resolve_relative_paths, rebuild_animated_missing
    from cutora.model.project_io import deserialize_render_task
    from cutora.preview.engine import PreviewEngine

    serial = serialize_render_task(task)
    bag = str(tmp_path / "bag")
    report = pack_tasks([serial], bag, mode="copy")
    assert report["files"] >= 1, report
    dest_proj = os.path.join(bag, "project.cutora")
    data = {"version": 2, "tasks": report["tasks"]}
    with open(dest_proj, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

    os.remove(plate)
    os.remove(gif)
    resolve_relative_paths(data["tasks"], dest_proj)
    rebuild_animated_missing(data["tasks"])
    task2 = deserialize_render_task(data["tasks"][0])
    eng = PreviewEngine()
    try:
        img = eng.frame_at(task2, 0.1)
    finally:
        eng.close()
    assert img is not None and not img.isNull()
    from test_q82_batch_gif import _compose_export_frame
    exp = _compose_export_frame(task2, 0.1)
    assert exp is not None and not exp.isNull()
