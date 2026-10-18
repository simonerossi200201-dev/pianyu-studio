# -*- coding: utf-8 -*-
"""T28: timeline clock + frame_at uses export helpers."""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19 = os.path.join(REPO, "tests", "golden", "g19-2_stitch_timeline", "project.asproj.json")
G19_MP4 = os.path.join(REPO, "tests", "golden", "g19-2_stitch_timeline", "expected.mp4")
G11 = os.path.join(REPO, "tests", "golden", "g11_vertical_basic_fx", "project.asproj.json")
G11_MP4 = os.path.join(REPO, "tests", "golden", "g11_vertical_basic_fx", "expected.mp4")
PAIR_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t28-gates"])


def _load_task(path):
    from cutora.model.project_io import deserialize_render_task
    data = json.loads(open(path, encoding="utf-8").read())
    return deserialize_render_task(data["tasks"][0])


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _extract_frame(mp4, t, dest):
    from cutora.preview.engine import timeline_frame_index
    n = max(0, timeline_frame_index(float(t), 30.0))
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", mp4, "-vf", "select=eq(n\\,%d)" % n, "-vframes", "1", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def _qimage_rgb(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    ptr = conv.constBits()
    raw = bytes(ptr) if not isinstance(ptr, (bytes, bytearray, memoryview)) else bytes(ptr)
    import numpy as np
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def _mad(a, b):
    import numpy as np
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def _pair_png(preview_img, expected_path, dest):
    from PySide6.QtGui import QImage, QPainter
    exp = QImage(expected_path)
    w = preview_img.width() + (exp.width() if not exp.isNull() else 0)
    h = max(preview_img.height(), exp.height() if not exp.isNull() else 0)
    canvas = QImage(w, h, QImage.Format.Format_RGB32)
    canvas.fill(0)
    p = QPainter(canvas)
    p.drawImage(0, 0, preview_img)
    if not exp.isNull():
        p.drawImage(preview_img.width(), 0, exp)
    p.end()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    canvas.save(dest)


def test_t28_g19_duration_and_seek_bounds():
    from cutora.model.project_io import deserialize_render_task
    from cutora.preview.engine import timeline_duration_sec

    data = json.loads(open(G19, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    dur = timeline_duration_sec(task)
    assert abs(dur - 29.26) < 0.5, dur
    # seek 20s is inside, not clamped to first clip (~8s)
    assert 20.0 < dur


def test_t28_no_setsource_first_file():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    chunk = src[src.find("self._sfx_stop_all"):src.find("self.sub_panel.load_data")]
    assert "setSource(QUrl.fromLocalFile(task['video']))" not in chunk
    assert "_rebuild_preview_wav" in chunk
    assert "setVideoSink" not in src[src.find("self.media_player = QMediaPlayer"):src.find("self.intro_player")]


def test_t28_frame_at_uses_export_helpers():
    src = open(
        os.path.join(REPO, "cutora", "preview", "engine.py"), encoding="utf-8"
    ).read()
    assert "_find_active_clips_in_track_export" in src
    assert "_draw_video_overlay" in src
    assert "_paint_export_transition" in src
    assert "compose_standalone_transition" in src
    assert "CapturePool" in src


def test_t28_preview_wav_48k_stereo():
    src = open(
        os.path.join(REPO, "cutora", "asr", "timeline_wav.py"), encoding="utf-8"
    ).read()
    assert "rate=48000, channels=2" in src


@pytest.mark.skipif(not os.path.isfile(G19), reason="g19 missing")
def test_t28_frame_at_runs(qapp=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from cutora.model.project_io import deserialize_render_task
    from cutora.preview.engine import PreviewEngine

    QApplication.instance() or QApplication(["t28"])
    data = json.loads(open(G19, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    eng = PreviewEngine()
    try:
        img = eng.frame_at(task, 4.0)
        assert img is not None and not img.isNull()
        assert img.width() > 0 and img.height() > 0
    finally:
        eng.close()


def test_t28_preview_key_changes_with_gain():
    from cutora.preview.engine import _preview_key
    task = _load_task(G19)
    k0 = _preview_key(task)
    tl = task["timeline"]
    changed = False
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "domain", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            c.gain_db = float(getattr(c, "gain_db", 0.0) or 0.0) + 3.0
            changed = True
            break
        if changed:
            break
    assert changed
    k1 = _preview_key(task)
    assert k0 != k1


def test_t28_mix_honors_mute_gain_fade():
    """mix_rows_for_task drops muted tracks via compute_export_audio_tracks."""
    from cutora.asr.timeline_wav import mix_rows_for_task
    task = _load_task(G19)
    rows = mix_rows_for_task(task)
    assert rows
    tl = task["timeline"]
    muted = False
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "domain", None) == "audio":
            tr.mute = True
            muted = True
            break
    assert muted
    rows_m = mix_rows_for_task(task)
    assert len(rows_m) < len(rows)


def test_t28_play_to_end_wall_clock():
    from cutora.preview.engine import PreviewClock
    clk = PreviewClock()
    clk.end_ms = 29260
    clk.use_media = False
    now = 1000.0
    assert clk.play_toggle(now) is True
    last = -1
    for _ in range(2000):
        now += 0.033
        clk.tick(now)
        assert clk.ms >= last
        last = clk.ms
        if not clk.playing:
            break
    assert clk.ms == clk.end_ms
    assert clk.playing is False
    assert clk.play_toggle(now + 1) is True
    assert clk.ms == 0


def test_t28_seek_then_play_window():
    from cutora.preview.engine import PreviewClock
    clk = PreviewClock()
    clk.end_ms = 29260
    clk.seek(20000, now=0.0)
    clk.use_media = False
    now = 0.0
    clk.play_toggle(now)
    for _ in range(100):
        now += 0.033
        clk.tick(now)
    assert 20000 <= clk.ms <= 24000


def test_bug17_paused_seek_holds():
    from cutora.preview.engine import PreviewClock
    clk = PreviewClock()
    clk.end_ms = 29260
    clk.playing = False
    clk.use_media = False
    clk.seek(20000, now=1.0)
    for i in range(30):
        clk.tick(1.0 + i * 0.033, media_pos_ms=None)
        assert clk.ms == 20000, (i, clk.ms)


def test_bug17_playing_seek_ignores_stale_media():
    from cutora.preview.engine import PreviewClock
    clk = PreviewClock()
    clk.end_ms = 29260
    clk.playing = True
    clk.use_media = True
    clk.ms = 1000
    clk.seek(20000, now=10.0)
    for i in range(5):
        clk.tick(10.0 + i * 0.033, media_pos_ms=1000)
        assert clk.ms == 20000, (i, clk.ms)


@pytest.mark.skipif(not os.path.isfile(G19_MP4), reason="g19 expected.mp4 missing")
def test_t28_frame_at_vs_export_mad(qapp, tmp_path):
    from cutora.preview.engine import PreviewEngine
    cases = [("g19", G19, G19_MP4), ("g11", G11, G11_MP4)]
    times = (4, 12, 20, 28)
    eng = PreviewEngine()
    try:
        for tag, proj, mp4 in cases:
            if not os.path.isfile(mp4):
                continue
            task = _load_task(proj)
            from cutora.preview.engine import timeline_duration_sec
            dur = timeline_duration_sec(task)
            for t in times:
                if float(t) >= dur:
                    continue
                tt = min(float(t), max(0.0, dur - 1.0 / 30.0))
                prev = eng.frame_at(task, tt)
                shot = str(tmp_path / ("%s_%s.png" % (tag, t)))
                assert _extract_frame(mp4, tt, shot), (tag, t, tt)
                from PySide6.QtGui import QImage
                exp = QImage(shot)
                assert not exp.isNull()
                import numpy as np
                pa, ea = _qimage_rgb(prev), _qimage_rgb(exp)
                diff = np.abs(pa.astype(np.int16) - ea.astype(np.int16))
                mad = float(np.mean(diff)) / 255.0
                med = float(np.median(diff)) / 255.0
                pair = os.path.join(PAIR_DIR, "T28_pair_%s.png" % t) if tag == "g19" else os.path.join(
                    PAIR_DIR, "T28_pair_g11_%s.png" % t)
                _pair_png(prev, shot, pair)
                print("T28_MAD", tag, t, "mean", mad, "median", med, flush=True)
                if (tag, t) in (("g19", 28), ("g11", 28)):
                    heat = np.clip(diff.mean(axis=2) * 12.0, 0, 255).astype(np.uint8)
                    hh, ww = heat.shape
                    rgb = np.zeros((hh, ww, 3), dtype=np.uint8)
                    rgb[:, :, 0] = heat
                    from PySide6.QtGui import QImage
                    himg = QImage(rgb.data, ww, hh, ww * 3, QImage.Format.Format_RGB888).copy()
                    himg.save(os.path.join(PAIR_DIR, "T28_diff_%s_%s.png" % (tag, t)))
                # Encoded golden vs raw compositor: median must stay at encode floor.
                assert med <= 2.0 / 255.0 + 1e-6, (tag, t, med, mad)
                assert mad < 0.015, (tag, t, mad)
    finally:
        eng.close()


@pytest.mark.skipif(not os.path.isfile(G19_MP4), reason="g19 expected.mp4 missing")
def test_t28_off_by_one_three_mad(qapp, tmp_path):
    """frame_at(t=28) vs export t-1/30, t, t+1/30. Print three MADs."""
    from cutora.preview.engine import (
        PreviewEngine, timeline_duration_sec, timeline_frame_index,
    )
    cases = [("g19", G19, G19_MP4), ("g11", G11, G11_MP4)]
    t0 = 28.0
    fps = 30.0
    eng = PreviewEngine()
    try:
        for tag, proj, mp4 in cases:
            if not os.path.isfile(mp4):
                continue
            task = _load_task(proj)
            dur = timeline_duration_sec(task)
            t = min(float(t0), max(0.0, dur - 1.0 / fps))
            prev = eng.frame_at(task, t)
            pa = _qimage_rgb(prev)
            nums = {}
            for label, dt in (("t-1", -1.0 / fps), ("t", 0.0), ("t+1", 1.0 / fps)):
                shot = str(tmp_path / ("%s_%s.png" % (tag, label)))
                tt = min(max(0.0, t + dt), max(0.0, dur - 1.0 / fps))
                n = timeline_frame_index(tt, fps)
                from cutora.core.config import FFMPEG_PATH
                cmd = [
                    FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", mp4, "-vf", "select=eq(n\\,%d)" % n, "-vframes", "1",
                    shot,
                ]
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                if r.returncode != 0 or not os.path.isfile(shot) or os.path.getsize(shot) == 0:
                    nums[label] = None
                    continue
                from PySide6.QtGui import QImage
                exp = QImage(shot)
                if exp.isNull():
                    nums[label] = None
                    continue
                ea = _qimage_rgb(exp)
                nums[label] = _mad(pa, ea)
            print("T28_OFFBYONE", tag, "t-1", nums.get("t-1"), "t", nums.get("t"),
                  "t+1", nums.get("t+1"), "t_used", t, flush=True)
            present = {k: v for k, v in nums.items() if v is not None}
            assert present.get("t") is not None
            t_mad = present["t"]
            assert t_mad == min(present.values()), present
            if "t-1" in present:
                assert present["t-1"] > t_mad, present
            if "t+1" in present and abs(float(t) - float(t0)) < 1e-6:
                assert present["t+1"] > t_mad, present
    finally:
        eng.close()


@pytest.mark.skipif(not os.path.isfile(G19_MP4), reason="g19 expected.mp4 missing")
def test_t28_proxy_wav_xcorr_lag(tmp_path):
    import numpy as np
    from cutora.asr.timeline_wav import render_preview_wav
    task = _load_task(G19)
    proxy = str(tmp_path / "proxy.wav")
    render_preview_wav(task, proxy)
    ref = str(tmp_path / "ref.wav")
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", G19_MP4, "-ac", "1", "-ar", "16000", ref,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    prox16 = str(tmp_path / "proxy16.wav")
    subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", proxy, "-ac", "1", "-ar", "16000", prox16],
        check=True, capture_output=True, timeout=60)
    import wave
    def _pcm(path):
        with wave.open(path, "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
            return np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    a = _pcm(prox16)
    b = _pcm(ref)
    n = min(len(a), len(b), 16000 * 8)
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    corr = np.correlate(a, b, mode="full")
    lag = int(np.argmax(corr) - (n - 1))
    lag_s = abs(lag) / 16000.0
    assert lag_s < 0.020, lag_s


def test_t28_31clip_preview_fps(qapp):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.preview.engine import PreviewEngine
    task = _load_task(G19)
    src_path = None
    tl0 = task["timeline"]
    for s in (getattr(tl0, "sources", {}) or {}).values():
        p = getattr(s, "path", None) or (s.get("path") if isinstance(s, dict) else None)
        if p and os.path.isfile(p):
            src_path = p
            break
    if not src_path:
        pytest.skip("g19 source media missing")
    tl = Timeline(1080, 1920, 30)
    src = MediaSource(src_path)
    tl.sources[src.id] = src
    v, a = Track("video"), Track("audio")
    for i in range(31):
        vc = Clip(src.id, 0.0, 0.4, i * 0.4)
        ac = Clip(src.id, 0.0, 0.4, i * 0.4)
        ac.audio_role = "main"
        v.clips.append(vc)
        a.clips.append(ac)
    tl.add_track(v)
    tl.add_track(a)
    fake = {"timeline": tl, "duration": 12.4, "style": task.get("style") or {},
            "segments": [], "dims": (1080, 1920)}
    eng = PreviewEngine()
    n = 0
    t0 = time.perf_counter()
    t = 0.0
    try:
        while time.perf_counter() - t0 < 10.0:
            img = eng.frame_at(fake, t)
            assert img is not None and not img.isNull()
            n += 1
            t = (t + 1.0 / 24.0) % 12.0
    finally:
        eng.close()
    fps = n / 10.0
    print("T28_T023_FPS", fps, "frames", n, flush=True)
    assert fps >= 24.0, fps


def test_t28_27file_preview_fps(qapp):
    """27 distinct T0035 files in sequence — open+seek cost, not one capture."""
    import glob
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.preview.engine import PreviewEngine
    media = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
    files = sorted(glob.glob(os.path.join(media, "T0035-*.mp4")))
    files = [p for p in files if os.path.isfile(p)]
    assert len(files) >= 27, len(files)
    files = files[:27]
    tl = Timeline(1080, 1920, 30)
    v, a = Track("video"), Track("audio")
    t0 = 0.0
    dur_each = 0.4
    for path in files:
        src = MediaSource(path)
        tl.sources[src.id] = src
        vc = Clip(src.id, 0.0, dur_each, t0)
        ac = Clip(src.id, 0.0, dur_each, t0)
        ac.audio_role = "main"
        v.clips.append(vc)
        a.clips.append(ac)
        t0 += dur_each
    tl.add_track(v)
    tl.add_track(a)
    fake = {
        "timeline": tl, "duration": t0, "style": {},
        "segments": [], "dims": (1080, 1920),
    }
    eng = PreviewEngine()
    n = 0
    t = 0.0
    t_wall = time.perf_counter()
    try:
        while time.perf_counter() - t_wall < 10.0:
            img = eng.frame_at(fake, t)
            assert img is not None and not img.isNull()
            n += 1
            t = (t + 1.0 / 24.0) % max(0.4, t0)
    finally:
        eng.close()
    fps = n / 10.0
    print("T28_27FILE_FPS", fps, "frames", n, "files", len(files), flush=True)
    assert fps >= 24.0, fps


def test_t28_gain_db_minus20_rms(tmp_path):
    import numpy as np
    import wave
    from cutora.asr.timeline_wav import render_preview_wav
    task = _load_task(G19)
    tl = task["timeline"]
    clips = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "audio" or getattr(tr, "domain", None) == "audio":
            clips.extend(getattr(tr, "clips", None) or [])
    assert clips, "g19 has no audio clips"
    for c in clips:
        c.gain_db = 0.0
    p0 = str(tmp_path / "gain0.wav")
    render_preview_wav(task, p0)
    for c in clips:
        c.gain_db = -20.0
    p1 = str(tmp_path / "gain20.wav")
    render_preview_wav(task, p1)

    def _rms(path):
        with wave.open(path, "rb") as w:
            raw = w.readframes(w.getnframes())
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        return float(np.sqrt(np.mean(a * a)) + 1e-12)

    r0, r1 = _rms(p0), _rms(p1)
    db = 20.0 * np.log10(r1 / r0)
    print("T28_GAIN_RMS_DB", db, "r0", r0, "r1", r1, flush=True)
    assert abs(db - (-20.0)) <= 1.0, (db, r0, r1)

