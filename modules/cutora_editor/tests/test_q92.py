# -*- coding: utf-8 -*-
"""Q92: filter_complex_script roundtrip, unicode paths, GIF cache, sticky skip."""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

from test_q88 import FPS, H, W, _make_gif, _plate, _qapp, _task_with_gif, _ud


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _sine_wav(path, seconds=0.2):
    r = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=%.2f" % seconds,
         "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", path],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0 and os.path.isfile(path), r.stderr


def _pcm_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _long_graph(base_head, base_tail, min_chars=32768):
    pad = ",anull"
    n = max(1, (int(min_chars) - len(base_head) - len(base_tail)) // len(pad) + 1)
    graph = base_head + pad * n + base_tail
    assert len(graph) > 32767, len(graph)
    return graph


class _FakeCap:
    def __init__(self, frames, w, h):
        self._frames = int(frames)
        self._w = int(w)
        self._h = int(h)

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FPS:
            return 30.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._frames)
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        return 0.0

    def set(self, *_a, **_k):
        return True

    def release(self):
        return None


def test_q92_filter_script_roundtrip(tmp_path, monkeypatch):
    """A graph over 32767 chars runs via run_filter_script; bytes match -filter_complex."""
    from cutora.core import proc
    from cutora.core.proc import run_filter_script

    wav = str(tmp_path / "in.wav")
    _sine_wav(wav)
    head = "[0:a]aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono"
    tail = "[aout]"
    short = head + tail
    long_graph = _long_graph(head, tail)

    scripts = []
    real_mk = proc.tempfile.mkstemp

    def _mk(*a, **k):
        fd, p = real_mk(*a, **k)
        scripts.append(p)
        return fd, p

    monkeypatch.setattr(proc.tempfile, "mkstemp", _mk)

    ff = _ffmpeg()
    before = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", wav]
    after_short = ["-map", "[aout]", "-t", "0.2", "-c:a", "pcm_s16le",
                   str(tmp_path / "script_short.wav")]
    after_long = ["-map", "[aout]", "-t", "0.2", "-c:a", "pcm_s16le",
                  str(tmp_path / "script_long.wav")]

    captured = {}
    real_run = subprocess.run

    def _run(cmd, **kw):
        captured.setdefault("cmds", []).append(list(cmd))
        captured.setdefault("kwargs", []).append(dict(kw))
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", _run)

    r_long = run_filter_script(before, long_graph, after_long, capture_output=True)
    assert r_long.returncode == 0, (r_long.stderr or b"")[-400:]
    long_out = str(tmp_path / "script_long.wav")
    assert os.path.isfile(long_out) and os.path.getsize(long_out) > 44

    r_short = run_filter_script(before, short, after_short, capture_output=True)
    assert r_short.returncode == 0, (r_short.stderr or b"")[-400:]

    cli_short = str(tmp_path / "cli_short.wav")
    cmd_cli = before + ["-filter_complex", short, "-map", "[aout]",
                        "-t", "0.2", "-c:a", "pcm_s16le", cli_short]
    r_cli = real_run(cmd_cli, capture_output=True, timeout=30)
    assert r_cli.returncode == 0, (r_cli.stderr or b"")[-400:]
    assert _pcm_bytes(long_out) == _pcm_bytes(cli_short)
    assert _pcm_bytes(str(tmp_path / "script_short.wav")) == _pcm_bytes(cli_short)

    for p in scripts:
        assert not os.path.isfile(p), p

    for cmd in captured.get("cmds") or []:
        assert "-filter_complex" not in cmd
        assert "-filter_complex_script" in cmd
        assert len(subprocess.list2cmdline(cmd)) < 32767

    cmd_long_cli = before + ["-filter_complex", long_graph, "-map", "[aout]",
                             "-t", "0.2", "-c:a", "pcm_s16le",
                             str(tmp_path / "cli_long.wav")]
    assert len(subprocess.list2cmdline(cmd_long_cli)) > 32767
    try:
        real_run(cmd_long_cli, capture_output=True, timeout=30)
        cli_long_failed = False
    except OSError as exc:
        cli_long_failed = True
        assert getattr(exc, "winerror", None) == 206 or "206" in str(exc)

    if os.name == "nt":
        assert cli_long_failed, "Windows CreateProcess should reject the long -filter_complex"
        nflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert captured["kwargs"], "subprocess.run was not used"
        flags = int(captured["kwargs"][0].get("creationflags") or 0)
        assert flags & nflags, captured["kwargs"][0]

    def _one(i):
        dest = str(tmp_path / ("conc_%d.wav" % i))
        r = run_filter_script(
            before, short,
            ["-map", "[aout]", "-t", "0.2", "-c:a", "pcm_s16le", dest],
            capture_output=True)
        assert r.returncode == 0, (r.stderr or b"")[-200:]
        return dest

    with ThreadPoolExecutor(2) as pool:
        dests = list(pool.map(_one, (0, 1)))
    assert dests[0] != dests[1]
    assert _pcm_bytes(dests[0]) == _pcm_bytes(dests[1])
    names = [os.path.basename(p) for p in scripts]
    assert len(set(names)) == len(names), names
    for p in scripts:
        assert not os.path.isfile(p), p


def test_q92_filter_script_unicode_path(tmp_path, monkeypatch):
    """Input, output, and the temp script live under a Chinese+spaces path."""
    from cutora.core import proc
    from cutora.core.proc import run_filter_script

    root = tmp_path / "中文 目录"
    root.mkdir()
    script_dir = root / "临时 脚本"
    script_dir.mkdir()
    monkeypatch.setattr(proc.tempfile, "tempdir", str(script_dir))

    wav = str(root / "音 频.wav")
    out = str(root / "输出 mix.wav")
    _sine_wav(wav)
    graph = "[0:a]aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono[aout]"
    scripts = []
    real_mk = proc.tempfile.mkstemp

    def _mk(*a, **k):
        fd, p = real_mk(*a, **k)
        scripts.append(p)
        return fd, p

    monkeypatch.setattr(proc.tempfile, "mkstemp", _mk)
    r = run_filter_script(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", wav],
        graph,
        ["-map", "[aout]", "-t", "0.2", "-c:a", "pcm_s16le", out],
        capture_output=True)
    assert r.returncode == 0, (r.stderr or b"").decode("utf-8", "replace")[-400:]
    assert os.path.isfile(out) and os.path.getsize(out) > 44
    assert scripts, "temp script was never created"
    for p in scripts:
        assert "中文" in p or " " in os.path.dirname(p)
        assert not os.path.isfile(p), p


def test_q92_cache_still_caches_gif(tmp_path, monkeypatch):
    """GIF / _cutora_anim still decode-once, even when the clip window is > 30 s."""
    _qapp()
    _ud(tmp_path, monkeypatch)
    from cutora.media.frame_cache import (
        MAX_DURATION_S, should_cache_clip, clear, reset_decode_stats,
    )
    from cutora.preview.engine import PreviewEngine
    from cutora.media import frame_cache as fc

    long_anim = type("C", (), {
        "loop": True, "in_t": 0.0, "out_t": MAX_DURATION_S + 10.0,
    })()
    assert should_cache_clip(
        long_anim, r"D:\fake\sticker._cutora_anim.gif.abc.mov") is True
    gif_src = type("S", (), {"kind": "gif"})()
    assert should_cache_clip(
        long_anim, r"D:\fake\loop.gif", src=gif_src) is True

    gif = _make_gif(tmp_path, n=10, size=48)
    plate = _plate(str(tmp_path / "plate.mp4"), seconds=3.0)
    task, gclip = _task_with_gif(gif, plate, dur=3.0)
    tl = task["timeline"]
    src = tl.sources.get(gclip.source_id)
    anim_path = getattr(src, "path", None) or getattr(src, "preview_path", None)
    assert anim_path, src
    long_on_real = type("C", (), {
        "loop": True, "in_t": 0.0, "out_t": MAX_DURATION_S + 10.0,
    })()
    assert should_cache_clip(long_on_real, anim_path, src=src) is True

    clear()
    reset_decode_stats()
    eng = PreviewEngine()
    try:
        n_src = 10
        for i in range(n_src * 3):
            eng.frame_at(task, i * 0.1)
    finally:
        eng.close()
    unique = len(fc.DECODED_KEYS)
    assert fc.DECODE_CALLS == unique, (
        "decode_clip_frame ran %d times for %d unique frames (loop re-decoded)"
        % (fc.DECODE_CALLS, unique))
    assert unique <= n_src + 2, "decoded %d unique frames, GIF has %d" % (unique, n_src)
    assert unique >= n_src - 2, "only decoded %d frames of a %d-frame GIF" % (unique, n_src)


def test_q92_cache_skip_is_sticky(monkeypatch):
    """A clip that cannot fit is probed once across 30 preview frames."""
    _qapp()
    import cutora.media.frame_cache as fc

    probes = {"n": 0}

    def _probe(_path):
        probes["n"] += 1
        return 500

    monkeypatch.setattr("cutora.media.animated._nb_frames", _probe)
    cache = fc.PremulFrameCache(budget_bytes=16 * 1024 * 1024)
    orig_load = cache._load
    loads = {"n": 0}

    def _load(path, cap=None):
        loads["n"] += 1
        return orig_load(path, cap=cap)

    cache._load = _load
    cap = _FakeCap(frames=500, w=64, h=64)
    path = r"D:\fake\q92-skip-plate.mp4"
    for i in range(30):
        img = cache.qimage_at(path, i / 30.0, cap=cap)
        assert img is None
    assert loads["n"] == 1, "re-probed every frame: loads=%s probes=%s" % (loads, probes)
    assert probes["n"] == 1, probes


def test_q92_long_loop_clip_not_cached(tmp_path, monkeypatch):
    """loop on a >30 s window stays off the cache; preview still paints."""
    _qapp()
    _ud(tmp_path, monkeypatch)
    from cutora.media.frame_cache import MAX_DURATION_S, should_cache_clip, clear
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.preview.engine import PreviewEngine

    plate = _plate(str(tmp_path / "plate.mp4"), seconds=2.0)
    long_clip = type("C", (), {
        "loop": True, "in_t": 0.0, "out_t": MAX_DURATION_S + 10.0,
    })()
    short_clip = type("C", (), {"loop": True, "in_t": 0.0, "out_t": 2.0})()
    assert should_cache_clip(long_clip, plate) is False
    assert should_cache_clip(short_clip, plate) is True

    tl = Timeline(W, H, FPS)
    src = MediaSource(plate)
    src.has_audio = False
    tl.sources[src.id] = src
    tr = Track("video")
    tr.is_main_video = True
    clip = Clip(src.id, 0.0, MAX_DURATION_S + 10.0, 0.0)
    clip.loop = True
    clip.fit_mode = "stretch"
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {
        "timeline": tl, "video": plate, "path": plate,
        "segments": [], "text_overlays": [], "image_overlays": [],
        "duration": 2.0, "style": {},
    }
    assert should_cache_clip(clip, plate) is False
    clear()
    eng = PreviewEngine()
    try:
        img = eng.frame_at(task, 0.1)
    finally:
        eng.close()
    assert img is not None and not img.isNull()
    c = img.pixelColor(W // 2, H // 2)
    assert c.red() > 200 and c.blue() > 200 and c.green() < 40, (
        "preview blank/wrong: %d %d %d" % (c.red(), c.green(), c.blue()))
