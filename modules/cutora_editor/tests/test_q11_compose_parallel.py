# -*- coding: utf-8 -*-
"""Q11: intra-video compose parallelism — byte-identical, split work, RSS cap.

Defects these parameters are meant to catch:
- workers=4 silently ignored (one worker paints every frame of a 64-frame clip)
- frames delivered out of order (testsrc frames are unique; md5 moves)
- preview path vs the workers=4 encoded file (not two calls to the same helper)
- parallel RSS blowing the T03 per-process budget
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "tests", "golden")

GOLDEN_TWELVE = (
    "g08_fx_static",
    "g10_fx_anim",
    "g11_vertical_basic_fx",
    "g12_vertical_fx_path",
    "g13_horizontal_it_fx",
    "g14_stitch_fx",
    "g15_scatter_insert_fx",
    "g16_parallel_fx",
    "g17_cli_fx",
    "g18_linebreak_latin_fx",
    "g19-2_stitch_timeline",
    "g19b-2_stitch_crossfade",
)


def _gc():
    path = os.path.join(REPO, "tools", "golden_check.py")
    spec = importlib.util.spec_from_file_location("cutora_golden_check_q11", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _tiny_mp4(path, duration, fps, size):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "testsrc=size=%s:rate=%d:duration=%.3f" % (size, fps, duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode: %s" % (r.stderr or b"")[-200:])


def _export(task, dest, workers):
    from cutora.render.render_worker import RenderWorker
    err = []
    worker = RenderWorker(
        task, dest, "quality", False, export_logs=False, compose_workers=workers)
    worker.error.connect(lambda m: err.append(m))
    worker.run()
    assert os.path.isfile(dest) and os.path.getsize(dest) > 1024, (dest, err)
    if err:
        assert all("MAD" in str(e) for e in err), err
    return worker


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q11-compose"])


@pytest.fixture(scope="module")
def golden_host(qapp):
    gc = _gc()
    host, app = gc.boot_host()
    return gc, host, app


def test_resolve_compose_workers_uses_cpu_and_env(monkeypatch):
    from cutora.render.compose_parallel import resolve_compose_workers
    monkeypatch.delenv("CUTORA_COMPOSE_WORKERS", raising=False)
    assert resolve_compose_workers(cpu_count=1) == 1
    assert resolve_compose_workers(cpu_count=4) == 4
    assert resolve_compose_workers(cpu_count=32) == 8
    assert resolve_compose_workers(requested=4, cpu_count=1) == 4
    monkeypatch.setenv("CUTORA_COMPOSE_WORKERS", "3")
    assert resolve_compose_workers(cpu_count=8) == 3
    monkeypatch.setenv("CUTORA_COMPOSE_WORKERS", "nope")
    assert resolve_compose_workers(cpu_count=4) == 4


def test_out_of_order_encode_changes_md5(tmp_path, qapp):
    """Unique testsrc frames: scramble order → mp4 md5 must move.

    If workers=4 wrote 0,2,1,3 into the pipe, the identity lock would be
    this difference. A solid-color clip would NOT expose that defect.
    """
    src = str(tmp_path / "src.mp4")
    _tiny_mp4(src, duration=1.6, fps=10, size="160x90")
    raw = str(tmp_path / "raw.bgra")
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-i", src, "-f", "rawvideo", "-pix_fmt", "bgra", raw,
        ],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0 and os.path.getsize(raw) > 0, r.stderr
    frame = 160 * 90 * 4
    data = open(raw, "rb").read()
    n = len(data) // frame
    assert n >= 12, n
    frames = [data[i * frame:(i + 1) * frame] for i in range(n)]
    scrambled = list(frames)
    scrambled[1], scrambled[2] = scrambled[2], scrambled[1]
    assert scrambled != frames

    def _encode(seq, dest):
        blob = b"".join(seq)
        p = subprocess.run(
            [
                _ffmpeg(), "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                "-s", "160x90", "-r", "10", "-i", "pipe:0",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", dest,
            ],
            input=blob, capture_output=True, timeout=30,
        )
        assert p.returncode == 0 and os.path.isfile(dest), p.stderr

    a = str(tmp_path / "ord.mp4")
    b = str(tmp_path / "scr.mp4")
    _encode(frames, a)
    _encode(scrambled, b)
    assert _md5(a) != _md5(b), (_md5(a), "scramble of unique frames hashed equal")


def test_workers_split_a_64_frame_clip(tmp_path, qapp):
    """64 frames / chunk=8 / workers=4. One worker doing all 64 = workers ignored."""
    from cutora.model.timeline import Timeline
    from cutora.render.compose_parallel import COMPOSE_CHUNK_FRAMES, LAST_RUN

    fps = 25
    n_frames = 64
    src = str(tmp_path / "src.mp4")
    _tiny_mp4(src, duration=n_frames / float(fps), fps=fps, size="320x180")
    out = str(tmp_path / "out.mp4")
    task = {
        "video": src, "path": src, "duration": n_frames / float(fps),
        "segments": [], "style": {},
        "timeline": Timeline(320, 180, float(fps)),
    }
    _export(task, out, workers=4)
    counts = dict(LAST_RUN["frames_by_worker"])
    total = sum(counts.values())
    assert LAST_RUN["workers"] == 4, LAST_RUN
    assert total == n_frames, (total, n_frames, counts)
    assert n_frames > COMPOSE_CHUNK_FRAMES * 4, "clip too short to expose a no-split bug"
    assert max(counts.values()) < n_frames, (
        "workers=4 left every frame on one thread: %s" % counts)
    assert len([c for c in counts.values() if c > 0]) >= 2, counts


@pytest.mark.parametrize("case", GOLDEN_TWELVE)
def test_workers_1_vs_4_md5_identical(golden_host, case, tmp_path):
    gc, host, _app = golden_host
    case_dir = os.path.join(GOLDEN, case)
    proj = os.path.join(case_dir, "project.asproj.json")
    assert os.path.isfile(proj), proj
    _data, tasks = gc.load_tasks(host, proj)
    assert tasks, case
    hashes_1 = []
    hashes_4 = []
    for i, task in enumerate(tasks):
        gc.reset_case_caches()
        p1 = str(tmp_path / ("%s_%02d_w1.mp4" % (case, i)))
        _export(task, p1, workers=1)
        hashes_1.append(_md5(p1))
        gc.reset_case_caches()
        p4 = str(tmp_path / ("%s_%02d_w4.mp4" % (case, i)))
        _export(task, p4, workers=4)
        hashes_4.append(_md5(p4))
    assert hashes_1 == hashes_4, (case, hashes_1, hashes_4)
    if case == "g08_fx_static":
        from cutora.render.compose_parallel import LAST_RUN
        from cutora.render.export_guard import mem_per_worker_gb
        budget = mem_per_worker_gb((1080, 1920))
        assert LAST_RUN["workers"] == 4, LAST_RUN
        assert LAST_RUN["peak_rss_gb"] > 0.2, LAST_RUN
        assert LAST_RUN["peak_rss_gb"] <= budget + 0.05, (
            "parallel compose RSS %.3f GB > T03 budget %.3f GB" % (
                LAST_RUN["peak_rss_gb"], budget))
        counts = LAST_RUN["frames_by_worker"]
        n = sum(counts.values())
        assert n > 8 * 4, (n, counts)
        assert max(counts.values()) < n, counts


def test_preview_mid_frame_vs_workers4_export(golden_host, tmp_path):
    """PreviewEngine.frame_at vs a frame decoded from the workers=4 mp4."""
    import numpy as np
    from cutora.preview.engine import PreviewEngine, timeline_frame_index
    from cutora.render.export_verify import MAD_LIMIT, _extract_rgb_frame, _qimage_to_rgb

    gc, host, _app = golden_host
    case = "g11_vertical_basic_fx"
    proj = os.path.join(GOLDEN, case, "project.asproj.json")
    _data, tasks = gc.load_tasks(host, proj)
    task = tasks[0]
    dest = str(tmp_path / "g11_w4.mp4")
    gc.reset_case_caches()
    _export(task, dest, workers=4)

    tl = task.get("timeline")
    fps = float(getattr(tl, "fps", 30.0) or 30.0)
    dur = float(task.get("duration") or 2.0)
    t = min(dur * 0.5, 1.0)
    n = timeline_frame_index(t, fps)
    t = n / fps

    eng = PreviewEngine()
    try:
        preview = eng.frame_at(task, t)
    finally:
        eng.close()
    prev_rgb = _qimage_to_rgb(preview)
    w, h = prev_rgb.shape[1], prev_rgb.shape[0]
    got = _extract_rgb_frame(dest, float(t), w, h)
    assert got is not None, t
    mad = float(np.mean(np.abs(
        got.astype(np.float32) - prev_rgb.astype(np.float32)))) / 255.0
    assert mad <= MAD_LIMIT, "preview vs workers=4 export MAD %.4f > %.2f at t=%.3f" % (
        mad, MAD_LIMIT, t)
