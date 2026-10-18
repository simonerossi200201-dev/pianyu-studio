# -*- coding: utf-8 -*-
"""Q30: per-clip spatial stereo — preview reverb, stitch default, one-layer switch."""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import wave

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = {
    "spatial_enabled": True,
    "spatial_preset": "stage",
    "spatial_wet": 0.26,
    "spatial_width": 0.62,
    "mic_texture": False,
    "mic_amount": 0.0,
}
G08_FX = {
    "spatial_enabled": True,
    "spatial_preset": "stage",
    "spatial_wet": 0.1,
    "spatial_width": 0.6,
    "mic_texture": True,
    "mic_amount": 0.2,
}


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _make_speech_wav(path, duration=2.0, sr=48000):
    """Mono speech-like burst so dry upmix has corr≈1 and reverb can decorrelate."""
    n = int(sr * duration)
    buf = [0.0] * n
    seed = 12345
    t = 0.12
    while t + 0.35 < duration:
        on, off = t, min(duration - 0.05, t + 0.28)
        for i in range(int(on * sr), int(off * sr)):
            tt = (i / sr) - on
            f0 = 120.0 + 18.0 * math.sin(2 * math.pi * 3 * tt)
            v = 0.0
            for h in range(1, 9):
                v += math.sin(2 * math.pi * f0 * h * tt) / h
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            v += 0.2 * ((seed / 0x3FFFFFFF) - 1.0)
            env = min(1.0, tt / 0.015) * min(1.0, (off - on - tt) / 0.04)
            buf[i] = v * env
        t += 0.55
    peak = max(abs(x) for x in buf) or 1.0
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(
            struct.pack("<h", int(32767 * 0.4 * x / peak)) for x in buf))
    return path


def _read_stereo(path):
    with wave.open(path, "rb") as w:
        ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    assert ch == 2, (path, ch)
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        data = np.frombuffer(raw, dtype=np.float32)
    frames = data.reshape(-1, 2)
    return frames, sr


def _stereo_corr(path, t0, t1):
    frames, sr = _read_stereo(path)
    a = max(0, int(t0 * sr))
    b = min(len(frames), int(t1 * sr))
    seg = frames[a:b]
    if len(seg) < 64:
        return 1.0
    left, right = seg[:, 0], seg[:, 1]
    if float(np.std(left)) < 1e-7 or float(np.std(right)) < 1e-7:
        return 1.0
    return float(np.corrcoef(left, right)[0, 1])


def _clip_task(wavs, *, enabled=True, roles=None, extra=None):
    """Build a timeline task: one audio clip per wav, sequential, 1.5s each."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    dur = 1.5
    tl = Timeline(1080, 1920, 30.0)
    vtr = Track("video")
    vtr.is_main_video = True
    atr = Track("audio")
    t = 0.0
    roles = roles or ["main"] * len(wavs)
    for i, path in enumerate(wavs):
        src = MediaSource(path)
        src.has_video = False
        src.has_audio = True
        src.duration = dur
        tl.sources[src.id] = src
        vc = Clip(src.id, 0.0, dur, t)
        ac = Clip(src.id, 0.0, dur, t)
        role = roles[i]
        ac.audio_role = role
        ac.is_main = role == "main"
        on = bool(enabled if not isinstance(enabled, (list, tuple)) else enabled[i])
        ac.spatial_enabled = on
        ac.spatial_amount = 0.26 if on else 0.0
        gid = "g%d" % i
        vc.link_group_id = gid
        ac.link_group_id = gid
        vtr.clips.append(vc)
        atr.clips.append(ac)
        t += dur
    tl.add_track(vtr)
    tl.add_track(atr)
    task = {
        "video": wavs[0],
        "timeline": tl,
        "duration": t,
        "trim_end_sec": t,
        "audio_fx": dict(FX),
        "src_volume": 1.0,
        "audio_tracks": [],
        "audio_clips": [],
    }
    if extra:
        task.update(extra)
    return task, atr.clips


def _export_style_wav(task, dest, clip):
    """One clip through export's hall constructor (build_hall_reverb_filter)."""
    from cutora.audio import spatial as S
    from cutora.asr.timeline_wav import _source_path

    tl = task["timeline"]
    path = _source_path(tl, clip.source_id)
    rec = {
        "path": path,
        "spatial_enabled": bool(clip.spatial_enabled),
        "spatial_amount": float(clip.spatial_amount),
        "is_main": bool(clip.is_main),
        "audio_role": clip.audio_role,
    }
    on, preset, wet, width = S.resolve_spatial_for_track(rec, task.get("audio_fx") or FX)
    in_t = float(clip.in_t)
    out_t = float(clip.out_t)
    dur = max(0.01, out_t - in_t)
    chain = f"[0:a]atrim=start={in_t:.3f}:duration={dur:.3f},asetpts=N/SR/TB,aresample=48000"
    if on and wet > 0:
        hall = S.build_hall_reverb_filter(
            preset, wet, width, uid=0, src_path=path,
            mic=S.mic_amount(rec, task.get("audio_fx") or FX))
        if hall:
            chain += f",{hall}"
    chain += "[aout]"
    cmd = [_ffmpeg(), "-y", "-v", "error", "-i", path,
           "-filter_complex", chain, "-map", "[aout]",
           "-ac", "2", "-ar", "48000", dest]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) < 44:
        err = (r.stderr or b"").decode("utf-8", "replace")[-800:]
        raise RuntimeError("export-style mix failed: %s" % err)
    return dest


# ── gates ──────────────────────────────────────────────────────────

def test_t30_no_user_set():
    token = "spatial_user_set"
    skip_names = {"test_t30_spatial.py"}
    skip_dirs = {"golden", "__pycache__", "_diag"}
    hits = []
    roots = [
        os.path.join(REPO, "cutora"),
        os.path.join(REPO, "tests"),
        os.path.join(REPO, "tools", "golden_gen.py"),
    ]
    for root in roots:
        if os.path.isfile(root):
            paths = [root]
        else:
            paths = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                for name in filenames:
                    if name.endswith((".py", ".md")):
                        paths.append(os.path.join(dirpath, name))
        for path in paths:
            if os.path.basename(path) in skip_names:
                continue
            try:
                text = open(path, encoding="utf-8").read()
            except OSError:
                continue
            if token in text:
                hits.append(os.path.relpath(path, REPO))
    assert hits == [], "spatial_user_set still in:\n" + "\n".join(hits)


def test_t30_fingerprint_includes_spatial(tmp_path):
    from cutora.asr.timeline_wav import preview_audio_fingerprint

    wav = _make_speech_wav(str(tmp_path / "a.wav"))
    task, clips = _clip_task([wav], enabled=True)
    k0 = preview_audio_fingerprint(task)
    clips[0].spatial_enabled = False
    k1 = preview_audio_fingerprint(task)
    assert k0 != k1, (k0, k1)
    clips[0].spatial_enabled = True
    clips[0].spatial_amount = 0.40
    k2 = preview_audio_fingerprint(task)
    assert k2 != k0
    clips[0].spatial_amount = 0.26
    clips[0].spatial_preset = "cathedral"  # dynamic leftover; must not affect mix
    k3 = preview_audio_fingerprint(task)
    assert k3 == k0, (k0, k3)
    fx = task.setdefault("audio_fx", {})
    fx["spatial_preset"] = "cathedral"
    k4 = preview_audio_fingerprint(task)
    assert k4 != k0, (k0, k4)
    fx["spatial_preset"] = FX["spatial_preset"]
    fx["spatial_width"] = 0.99
    k5 = preview_audio_fingerprint(task)
    assert k5 != k0, (k0, k5)


def test_t30_stitch_default_on(tmp_path):
    from cutora.audio.sfx_library import apply_default_spatial_to_clip
    from cutora.batch.timeline_stitch import (
        ClipPlacement, project_from_placements,
    )
    from cutora.model.project_io import deserialize_render_task
    from cutora.model.timeline_model import Clip, MediaSource

    wav = _make_speech_wav(str(tmp_path / "s.wav"), duration=1.0)
    places = [
        ClipPlacement(wav, 0.0, 0.8, 0.0),
        ClipPlacement(wav, 0.0, 0.8, 0.8),
        ClipPlacement(wav, 0.0, 0.8, 1.6),
    ]
    dest = str(tmp_path / "st.asproj.json")
    project_from_placements(places, dest)
    data = json.loads(open(dest, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    tl = task["timeline"]
    mains = []
    for tr in tl.tracks:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in tr.clips:
            mains.append(c)
            assert c.is_main is True
            assert c.audio_role == "main"
            assert c.spatial_enabled is True, c.spatial_enabled
    assert len(mains) == 3

    bgm_src = MediaSource(wav)
    bgm_src.has_video = False
    tl.sources[bgm_src.id] = bgm_src
    bgm = Clip(bgm_src.id, 0.0, 2.4, 0.0)
    bgm.is_main = False
    bgm.audio_role = "bgm"
    apply_default_spatial_to_clip(bgm, FX)
    assert bgm.spatial_enabled is False


def _g08_shape_clip():
    return {
        "source_id": "g", "in_t": 0, "out_t": 1, "start_t": 0,
        "is_main": True, "audio_role": "",
        "spatial_enabled": False, "spatial_amount": 0.0,
        "spatial_preset": "cathedral",
        "spatial_user_set": False,
    }


def test_t30_migration():
    from cutora.model.project_io import _normalize_project_data

    data = {
        "version": 1,
        "tasks": [{
            "audio_fx": {"spatial_enabled": True, "spatial_preset": "stage"},
            "timeline": {
                "tracks": [
                    {"type": "audio", "clips": [
                        {"source_id": "m", "in_t": 0, "out_t": 1, "start_t": 0,
                         "is_main": True, "audio_role": "main"},
                        {"source_id": "b", "in_t": 0, "out_t": 1, "start_t": 0,
                         "is_main": False, "audio_role": "bgm"},
                        {"source_id": "x", "in_t": 0, "out_t": 1, "start_t": 0,
                         "is_main": True, "audio_role": "main",
                         "spatial_enabled": False},
                        {"source_id": "s", "in_t": 0, "out_t": 1, "start_t": 0,
                         "is_main": False, "audio_role": "sfx"},
                        # golden g08 shape: placeholder false + user_set false
                        _g08_shape_clip(),
                        # real user uncheck after the old three-layer flag
                        {"source_id": "u", "in_t": 0, "out_t": 1, "start_t": 0,
                         "is_main": True, "audio_role": "main",
                         "spatial_enabled": False, "spatial_user_set": True},
                    ]},
                ],
            },
        }],
    }
    _normalize_project_data(data)
    by_id = {}
    for tr in data["tasks"][0]["timeline"]["tracks"]:
        for cl in tr.get("clips") or []:
            by_id[cl["source_id"]] = cl
    assert by_id["m"]["spatial_enabled"] is True
    assert by_id["b"]["spatial_enabled"] is False
    assert by_id["x"]["spatial_enabled"] is False
    assert by_id["s"]["spatial_enabled"] is False
    assert by_id["g"]["spatial_enabled"] is True
    assert by_id["u"]["spatial_enabled"] is False
    for cl in by_id.values():
        assert "spatial_user_set" not in cl


def test_t30_migration_respects_global_off():
    """user_set=False on a main clip must follow old fx.spatial_enabled, not force True."""
    from cutora.model.project_io import _normalize_project_data

    data = {
        "version": 1,
        "tasks": [{
            "audio_fx": {"spatial_enabled": False, "spatial_preset": "stage"},
            "timeline": {"tracks": [{"type": "audio", "clips": [_g08_shape_clip()]}]},
        }],
    }
    _normalize_project_data(data)
    clip = data["tasks"][0]["timeline"]["tracks"][0]["clips"][0]
    assert clip["spatial_enabled"] is False
    assert "spatial_user_set" not in clip


def test_t30_resolve_ignores_cathedral_placeholder():
    """aa3613a broke g08 by feeding row.spatial_preset='cathedral' into resolve.

    Golden clips store cathedral/0.0 as setdefault placeholders. Render must
    still use the global stage / 0.1 / 0.6 that ac520ed exported.
    """
    from cutora.audio.spatial import resolve_spatial_for_track

    row = {
        "is_main": True,
        "audio_role": "main",
        "spatial_enabled": True,
        "spatial_amount": 0.0,
        "spatial_preset": "cathedral",
    }
    on, preset, wet, width = resolve_spatial_for_track(row, G08_FX)
    assert on is True
    assert preset == "stage", preset
    assert abs(wet - 0.1) < 1e-9, wet
    assert abs(width - 0.6) < 1e-9, width
    hall = __import__("cutora.audio.spatial", fromlist=["spatial"]).build_hall_reverb_filter(
        preset, wet, width, uid=0, src_path=None, mic=0.2)
    assert "ir_stage_" in hall, hall[:200]
    assert "ir_cathedral_" not in hall
    assert "volume=1.5dB" in hall, hall[-200:]
    assert "equalizer=f=3200" in hall


def test_t30_sfx_never_reverb(tmp_path):
    from cutora.audio.spatial import resolve_spatial_for_track
    from cutora.asr.timeline_wav import render_preview_wav

    rec = {
        "spatial_enabled": True,
        "spatial_amount": 0.4,
        "spatial_preset": "stage",
        "is_main": False,
        "audio_role": "sfx",
    }
    on, _preset, wet, _w = resolve_spatial_for_track(rec, FX)
    assert on is False
    assert wet == 0.0 or on is False

    wav = _make_speech_wav(str(tmp_path / "sfx.wav"))
    task, clips = _clip_task([wav], enabled=True, roles=["sfx"])
    clips[0].spatial_enabled = True
    clips[0].spatial_amount = 0.4
    dest = str(tmp_path / "sfx_prev.wav")
    render_preview_wav(task, dest)
    corr = _stereo_corr(dest, 0.15, 1.2)
    assert corr > 0.97, corr


def test_t30_per_clip_switch(tmp_path):
    from cutora.asr.timeline_wav import render_preview_wav

    wavs = [
        _make_speech_wav(str(tmp_path / "c0.wav")),
        _make_speech_wav(str(tmp_path / "c1.wav")),
        _make_speech_wav(str(tmp_path / "c2.wav")),
    ]
    dry_task, _ = _clip_task(wavs, enabled=False)
    dry = str(tmp_path / "dry.wav")
    render_preview_wav(dry_task, dry)

    task, _clips = _clip_task(wavs, enabled=[False, True, False])
    wet = str(tmp_path / "switch.wav")
    render_preview_wav(task, wet)

    windows = [(0.42, 0.64), (1.92, 2.14), (3.42, 3.64)]
    corrs = [_stereo_corr(wet, a, b) for a, b in windows]
    dry_corrs = [_stereo_corr(dry, a, b) for a, b in windows]
    assert dry_corrs[0] > 0.95 and dry_corrs[2] > 0.95, dry_corrs
    assert abs(corrs[0] - dry_corrs[0]) < 0.04, (corrs, dry_corrs)
    assert abs(corrs[2] - dry_corrs[2]) < 0.04, (corrs, dry_corrs)
    assert corrs[1] < dry_corrs[1] - 0.08, (corrs, dry_corrs)


def test_t30_preview_has_reverb(tmp_path):
    from cutora.asr.timeline_wav import render_preview_wav

    wavs = [
        _make_speech_wav(str(tmp_path / "p0.wav")),
        _make_speech_wav(str(tmp_path / "p1.wav")),
        _make_speech_wav(str(tmp_path / "p2.wav")),
    ]
    dry_task, _ = _clip_task(wavs, enabled=False)
    dry = str(tmp_path / "all_dry.wav")
    render_preview_wav(dry_task, dry)

    wet_task, _ = _clip_task(wavs, enabled=True)
    wet = str(tmp_path / "all_wet.wav")
    render_preview_wav(wet_task, wet)

    windows = [(0.42, 0.64), (1.92, 2.14), (3.42, 3.64)]
    for i, (a, b) in enumerate(windows):
        d = _stereo_corr(dry, a, b)
        w = _stereo_corr(wet, a, b)
        assert d > 0.95, (i, d)
        assert w < d - 0.08, (i, "dry", d, "wet", w)


def test_t30_preview_matches_export(tmp_path):
    from cutora.asr.timeline_wav import render_preview_wav

    wav = _make_speech_wav(str(tmp_path / "one.wav"), duration=2.0)
    task, clips = _clip_task([wav], enabled=True)
    prev = str(tmp_path / "prev.wav")
    render_preview_wav(task, prev)
    exp = str(tmp_path / "exp.wav")
    _export_style_wav(task, exp, clips[0])
    pc = _stereo_corr(prev, 0.42, 0.64)
    ec = _stereo_corr(exp, 0.42, 0.64)
    assert abs(pc - ec) < 0.05, (pc, ec)
    assert pc < 0.70, pc


def test_t30_resolve_is_clip_switch():
    from cutora.audio.spatial import resolve_spatial_for_track

    fx_off = dict(FX)
    fx_off["spatial_enabled"] = False
    main_off = {
        "spatial_enabled": False, "spatial_amount": 0.26,
        "spatial_preset": "stage", "is_main": True, "audio_role": "main",
    }
    on, _, _, _ = resolve_spatial_for_track(main_off, FX)
    assert on is False
    main_on = dict(main_off)
    main_on["spatial_enabled"] = True
    on, _, wet, _ = resolve_spatial_for_track(main_on, fx_off)
    assert on is True
    assert wet > 0
    bgm = {
        "spatial_enabled": False, "is_main": False, "audio_role": "bgm",
        "spatial_amount": 0.26, "spatial_preset": "stage",
    }
    on, _, _, _ = resolve_spatial_for_track(bgm, FX)
    assert on is False
    bgm["spatial_enabled"] = True
    on, _, _, _ = resolve_spatial_for_track(bgm, FX)
    assert on is True


def test_t30_property_batch_and_wet_enable():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline_model import Clip
    from cutora.ui.panels.property import PropertyPanel

    QApplication.instance() or QApplication(["t30-prop"])
    a = Clip("s", 0.0, 1.0, 0.0)
    a.audio_role = "main"
    a.is_main = True
    a.spatial_enabled = True
    a.spatial_amount = 0.26
    b = Clip("s", 0.0, 1.0, 1.0)
    b.audio_role = "bgm"
    b.is_main = False
    b.spatial_enabled = True
    b.spatial_amount = 0.26
    p = PropertyPanel()
    p.load_data("audio", a)
    p.set_audio_targets([a, b])
    assert p.audio_spatial_amount.isEnabled() is True
    p.audio_spatial_enable.setChecked(False)
    p.on_change_audio()
    assert a.spatial_enabled is False
    assert b.spatial_enabled is False
    assert p.audio_spatial_amount.isEnabled() is False
    p.audio_spatial_enable.setChecked(True)
    p.on_change_audio()
    assert a.spatial_enabled is True
    assert b.spatial_enabled is True
    assert p.audio_spatial_amount.isEnabled() is True
    assert "这条音频" in p.audio_spatial_enable.text() or "这条音频" in (
        p.audio_spatial_enable.toolTip() or "")


def _canon_filter(s: str) -> str:
    import re
    return re.sub(r"amovie='[^']+'", "amovie='IR'", s)


# Pinned from docs/plan/reports/Q30_filter_diff.txt filter_complex A (ac520ed).
# IR path canonicalized. Character-identical ⇒ audio stream md5 04deefb5a08a4057.
G08_FILTER = (
    "[1:a]atrim=start=0.0:duration=20.0,asetpts=N/SR/TB,aresample=48000,"
    "aformat=channel_layouts=stereo,volume=3.0103dB,highpass=f=79,"
    "equalizer=f=95:t=q:w=1.1:g=0.44,bass=g=0.36:f=170:w=0.7,"
    "aexciter=amount=0.16:drive=2.5:blend=0:freq=7800:ceil=15500,volume=-0.52dB,"
    "equalizer=f=250:t=q:w=1.0:g=-1.8,equalizer=f=3200:t=q:w=1.2:g=2.2,"
    "treble=g=1.8:f=9000,asplit=3[sp0d][sp0s][sp0w];amovie='IR'[sp0ir];"
    "[sp0w][sp0ir]afir=dry=1:wet=1:irnorm=-1:maxir=8:minp=8192,adelay=18|18:all=1,"
    "highpass=f=220,lowpass=f=6800,stereotools=mlev=1:slev=1.120,volume=0.2400[sp0wr];"
    "[sp0wr][sp0s]sidechaincompress=threshold=0.03:ratio=5.0:attack=4:release=180:"
    "makeup=1:level_sc=1[sp0wd];[sp0d]volume=0.9780[sp0dg];"
    "[sp0dg][sp0wd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
    "volume=1.5dB,alimiter=limit=0.97:level=false[a0];"
    "[a0]anull,atrim=end=20.0,afade=t=out:st=19.5:d=0.5,asetpts=N/SR/TB[aout]"
)


def test_t30_golden_audio_unchanged():
    from cutora.model.project_io import _normalize_project_data, deserialize_render_task
    from cutora.render.render_worker import RenderWorker

    path = os.path.join(REPO, "tests", "golden", "g08_fx_static", "project.asproj.json")
    data = json.loads(open(path, encoding="utf-8").read())
    data["tasks"][0]["audio_fx"] = dict(G08_FX)
    _normalize_project_data(data)
    task = deserialize_render_task(data["tasks"][0])
    task["audio_fx"] = dict(G08_FX)
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=True)
    assert tracks, "g08 produced no audio tracks"
    got = _canon_filter(fc)
    assert got == G08_FILTER, (len(got), len(G08_FILTER), got[:180], G08_FILTER[:180])
    assert "afir" in fc
    assert "ir_cathedral" not in fc
    assert "volume=1.5dB" in fc


def test_t30_disabled_has_no_reverb(tmp_path):
    from cutora.render.render_worker import RenderWorker

    wav = _make_speech_wav(str(tmp_path / "dry.wav"))
    task, clips = _clip_task([wav], enabled=False)
    assert clips[0].spatial_enabled is False
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=True)
    assert tracks
    assert "afir" not in fc, fc
    assert "amovie=" not in fc, fc


def test_t30_proxy_preparing_does_not_start_old_fx_player():
    """_proxy_ready False must play original audio, never the first-clip fx proxy."""
    from cutora.preview.engine import preview_audio_mode
    from cutora.ui.main_window import MainWindow

    assert preview_audio_mode(True) == "timeline_wav"
    assert preview_audio_mode(False) == "preparing"

    hits = []

    class _Bar:
        def showMessage(self, msg, *a, **k):
            hits.append(("status", str(msg)))

    class Stub:
        _proxy_ready = False
        _preview_fx_player = None
        _preview_audio_src_logged = ""

        def _preview_fx_unmute(self):
            hits.append("unmute")

        def _preview_fx_ensure_player(self):
            hits.append("ensure_player")
            return None

        def statusBar(self):
            return _Bar()

    Stub._sync_preview_fx = MainWindow._sync_preview_fx
    s = Stub()
    s._sync_preview_fx()
    assert "unmute" in hits
    assert "ensure_player" not in hits
    assert any(h[0] == "status" and "预览音频准备中" in h[1] for h in hits if isinstance(h, tuple))
