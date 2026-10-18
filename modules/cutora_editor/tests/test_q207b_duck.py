# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q207b: ducking is off unless task['audio_duck']['enabled'] is true."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys

import numpy as np

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")


def _pair(path):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource(path, duration=2.0, probe=False)
    tl.sources[src.id] = src
    main = Track("audio")
    main.audio_role = "main"
    bgm = Track("audio")
    bgm.audio_role = "bgm"
    mc = Clip(src.id, 0.0, 2.0, 0.0)
    mc.audio_role = "main"
    mc.is_main = True
    bc = Clip(src.id, 0.0, 2.0, 0.0)
    bc.audio_role = "bgm"
    main.clips.append(mc)
    bgm.clips.append(bc)
    tl.add_track(main)
    tl.add_track(bgm)
    return {"timeline": tl, "src_volume": 1.0, "audio_fx": {}}, mc


def _filter(task):
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, _tracks = rw.export_audio_filter_complex(task, has_video=False)
    return fc


def _wav(path):
    subprocess.check_call(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", path],
        cwd=REPO,
    )


def test_duck_off_matches_missing_key_and_on_adds_one_sidechain(tmp_path):
    from cutora.audio.ducking import sidechain_filter
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    bare, _mc = _pair(wav)
    off, _mc2 = _pair(wav)
    off["audio_duck"] = {"enabled": False, "depth_db": -12, "release_ms": 250}
    on, _mc3 = _pair(wav)
    on["audio_duck"] = {"enabled": True, "depth_db": -12, "release_ms": 250}
    fc_bare = _filter(bare)
    fc_off = _filter(off)
    fc_on = _filter(on)
    assert fc_off == fc_bare
    assert "duck_" not in fc_off
    assert "sidechaincompress" not in fc_off
    assert fc_on.count("sidechaincompress") == 1
    assert "[duck_bgm_in][duck_main]" + sidechain_filter(-12, 250) + "[duck_bgm]" in fc_on
    assert sidechain_filter(-12, 250) == (
        "sidechaincompress=threshold=-24dB:ratio=4:attack=5:release=250:level_sc=1"
    )
    voice_only, _ = _pair(wav)
    voice_only["timeline"].tracks.pop()
    voice_only["audio_duck"] = {"enabled": True, "depth_db": -12, "release_ms": 250}
    assert "duck_" not in _filter(voice_only)


def test_fingerprint_and_preview_do_not_touch_stem_bytes(tmp_path):
    from cutora.asr.timeline_wav import preview_audio_fingerprint
    from cutora.audio.ducking import apply_auto_fades, preview_bgm_gain
    from cutora.audio.stem_cache import mix_frames
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    task, voice = _pair(wav)
    before = preview_audio_fingerprint(task)
    task["audio_duck"] = {"enabled": False, "depth_db": -12, "release_ms": 250}
    assert preview_audio_fingerprint(task) == before
    task["audio_duck"]["enabled"] = True
    assert preview_audio_fingerprint(task) != before
    main = np.ones((64, 2), np.float32)
    bgm = np.ones((64, 2), np.float32) * 0.5
    saved = bgm.copy()
    mixed = mix_frames(
        {"main": main, "bgm": bgm}, 0, 64,
        {"main": 1.0, "bgm": 1.0, "sfx": 1.0}, {},
        duck={"enabled": True, "depth_db": -12, "release_ms": 250})
    assert np.array_equal(bgm, saved)
    assert abs(float(mixed[0, 0]) - (1.0 + 0.5 * preview_bgm_gain(main, -12))) < 1e-4
    quiet = np.zeros((64, 2), np.float32)
    assert preview_bgm_gain(quiet, -12) == 1.0
    assert voice.fade_in_dur == 0.0
    assert apply_auto_fades(task) == 1
    assert abs(voice.fade_in_dur - 0.03) < 1e-9
    assert abs(voice.fade_out_dur - 0.03) < 1e-9


def test_duck_sources_have_no_torch():
    import cutora.audio.ducking as ducking
    body = inspect.getsource(ducking)
    assert "import torch" not in body
    menu = inspect.getsource(
        __import__("cutora.ui.main_window", fromlist=["MainWindow"]).MainWindow.popup_track_menu
    )
    assert "人声闪避" in menu
    assert "audio.duck_set" in menu
