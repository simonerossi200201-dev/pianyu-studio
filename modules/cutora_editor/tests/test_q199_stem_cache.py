# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q199: stem cache, muted-proxy clock, live faders."""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

import numpy as np

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

from cutora.audio import stem_cache as SC
from cutora.audio.stem_cache import (
    StemCache,
    clock_index,
    get_stem_mixer,
    reset_stem_mixer,
    row_stem_role,
)
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, MediaSource, Track

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIND = os.path.join(REPO, "resources", "audio", "sfx", "wind_soft.wav")
BIRDS = os.path.join(REPO, "resources", "audio", "sfx", "birds_forest.wav")


def _role_task():
    tl = Timeline(1080, 1920, 30)
    clips = {}
    paths = {"main": WIND, "bgm": BIRDS, "sfx": WIND}
    for role in ("main", "bgm", "sfx"):
        src = MediaSource.__new__(MediaSource)
        src.id = role
        src.path = paths[role]
        src.duration = 2.0
        src.has_video = False
        src.has_audio = True
        tl.sources[src.id] = src
        tr = Track("audio")
        tr.audio_role = role
        cl = Clip(src.id, 0.0, 2.0, 0.0)
        cl.audio_role = role
        if role == "main":
            cl.is_main = True
            cl.spatial_enabled = False
            cl.spatial_amount = 0.0
        tr.clips.append(cl)
        tl.add_track(tr)
        clips[role] = cl
    task = {"timeline": tl, "audio_fx": {"spatial_preset": "stage", "spatial_width": 0.62}}
    return task, clips


def _fake_render(_task, rows, *, role="main", pad_to=0.0):
    n = max(4800, int(round(float(pad_to or 0.1) * 48000)))
    pcm = np.ones((n, 2), dtype=np.float32)
    if role == "bgm":
        pcm *= np.float32(0.5)
    if role == "sfx":
        pcm *= np.float32(0.25)
    return pcm


class _FakeSink:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.running = False

    def start(self, *a, **k):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


def test_builder_calls_mix_rows_for_task(monkeypatch):
    src = inspect.getsource(SC)
    assert "atrim=" not in src
    assert "afade=" not in src
    calls = []
    import cutora.asr.timeline_wav as tw
    orig = tw.mix_rows_for_task

    def spy(task, **kw):
        calls.append(dict(kw))
        return orig(task, **kw)

    monkeypatch.setattr(tw, "mix_rows_for_task", spy)
    monkeypatch.setattr(SC, "render_role_pcm", _fake_render)
    task, _clips = _role_task()
    cache = StemCache()
    cache.build(task, task_key="t0")
    assert calls, "builder must call mix_rows_for_task"
    assert cache.stems["main"] is not None
    assert cache.stems["bgm"] is not None
    assert cache.stems["sfx"] is not None


def test_intro_clips_fold_into_main():
    clip = SimpleNamespace(is_intro_video_leadin=True, audio_role="bgm")
    rec = {"audio_role": "bgm", "_clip": clip}
    assert row_stem_role(rec) == "main"


def test_spatial_only_dirties_main(monkeypatch):
    monkeypatch.setattr(SC, "render_role_pcm", _fake_render)
    task, clips = _role_task()
    cache = StemCache()
    cache.build(task, task_key="t0")
    cache.build(task, task_key="t0")
    assert cache.last_build_roles == []
    clips["main"].spatial_enabled = True
    clips["main"].spatial_amount = 0.4
    cache.build(task, task_key="t0")
    assert cache.last_build_roles == ["main"]


def test_kick_order_then_refuse_long_bgm_source():
    frame = np.ones((100, 2), dtype=np.float32)
    other = np.ones((200, 2), dtype=np.float32)
    dry = np.ones((200, 2), dtype=np.float32)
    sfx = np.ones((200, 2), dtype=np.float32)
    main = np.ones((50, 2), dtype=np.float32)
    budget = (
        other.nbytes + dry.nbytes + sfx.nbytes + main.nbytes + 64
    )
    cache = StemCache(budget_bytes=budget)
    cache.task_key = "curr"
    cache.stems["main"] = main
    cache.stems["sfx"] = sfx
    cache.dry_main = dry
    assert cache.cache_source_pcm("other.wav", other, task_key="other") is True
    huge = np.ones((20000, 2), dtype=np.float32)
    assert cache.cache_source_pcm(
        "bgm.wav", huge, task_key="curr", role="bgm") is False
    assert not any(rec.get("key") == "other.wav" for rec in cache._sources)
    assert cache.dry_main is None
    assert cache.stems["sfx"] is None
    assert cache.stems["main"] is not None
    del frame


def _sink_host(playing, mixer, sink):
    from cutora.ui.main_window import MainWindow

    class Host:
        _sync_stem_sink = MainWindow._sync_stem_sink
        _stop_stem_sink = MainWindow._stop_stem_sink

    host = Host()
    host._timeline_playing = playing
    host._jkl_rate = 0
    host._stem_mixer = mixer
    host._stem_sink = sink
    host._stem_pull_io = object()
    host._stem_sink_running = False
    host._muted = []
    host.audio_output = SimpleNamespace(
        setMuted=lambda on, h=host: h._muted.append(bool(on)))
    return host


def test_paused_no_mouse_does_not_start_sink():
    mixer = reset_stem_mixer()
    mixer.cache.stems["main"] = np.ones((480, 2), dtype=np.float32)
    sink = _FakeSink()
    host = _sink_host(False, mixer, sink)
    host._sync_stem_sink()
    assert sink.starts == 0
    assert sink.running is False


def test_playing_starts_sink():
    mixer = reset_stem_mixer()
    mixer.cache.stems["main"] = np.ones((480, 2), dtype=np.float32)
    sink = _FakeSink()
    host = _sink_host(True, mixer, sink)
    host._sync_stem_sink()
    assert sink.starts == 1
    assert host._stem_sink_running is True
    assert host._muted and host._muted[-1] is True
    host._timeline_playing = False
    host._sync_stem_sink()
    assert sink.stops >= 1
    assert host._stem_sink_running is False


def test_playback_rate_2_idx_tracks_clock_not_wall():
    mixer = reset_stem_mixer()
    mixer.cache.stems["main"] = np.ones((48000, 2), dtype=np.float32)
    wall_dt_ms = 20.0
    rate = 2.0
    clock_1x = wall_dt_ms
    clock_2x = wall_dt_ms * rate
    mixer.pull_frames(0.0, 48)
    mixer.pull_frames(clock_2x, 48)
    idx0, idx1 = mixer.last_idxs[-2], mixer.last_idxs[-1]
    assert idx0 == clock_index(0.0)
    assert idx1 == clock_index(clock_2x)
    delta = idx1 - idx0
    assert delta == int(round(40.0 * 48.0))
    assert delta != int(round(clock_1x * 48.0))
    mixer.pull_frames(clock_2x, 48)
    assert mixer.last_idxs[-1] == idx1


def test_live_fader_changes_rms_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(SC, "render_role_pcm", _fake_render)
    mixer = reset_stem_mixer()
    mixer.cache.stems["main"] = np.ones((4800, 2), dtype=np.float32)
    mixer.baked_gain["main"] = 1.0
    mixer.live_gain["main"] = 1.0
    a = mixer.pull_frames(0, 480)
    mixer.set_live_linear("main", 0.25)
    b = mixer.pull_frames(0, 480)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x))))
    assert rms(b) < rms(a) * 0.4
    from cutora.ui.main_window import MainWindow
    host = SimpleNamespace(
        _stem_mixer=mixer,
        spin_src_vol=SimpleNamespace(
            blockSignals=lambda *_a, **_k: None,
            setValue=lambda *_a, **_k: None,
        ),
        spin_bgm_vol=None,
        spin_sfx_vol=None,
    )
    mixer.baked_gain["main"] = 1.0
    MainWindow._on_role_fader_moved(host, "main", 50)
    c = mixer.pull_frames(0, 480)
    assert rms(c) < rms(a) * 0.7


def test_clock_index_is_round_ms_times_48():
    assert clock_index(0) == 0
    assert clock_index(1000) == 48000
    assert clock_index(20) == int(round(20 * 48.0))
