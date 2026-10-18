# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q198: volume via bus; mute not whole mix; spatial fingerprint."""
from __future__ import annotations

import inspect
import os
import sys
from types import SimpleNamespace

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

from cutora.audio.sfx_library import clip_audio_role
from cutora.asr.timeline_wav import mix_rows_for_task, preview_audio_fingerprint
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, MediaSource, Track
from cutora.ui.command_bodies import CommandBodies

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIND = os.path.join(REPO, "resources", "audio", "sfx", "wind_soft.wav")
BIRDS = os.path.join(REPO, "resources", "audio", "sfx", "birds_forest.wav")


class _Out:
    def __init__(self):
        self.muted = False
        self.vol = 1.0
        self.vols = []

    def setMuted(self, on):
        self.muted = bool(on)

    def setVolume(self, v):
        self.vol = float(v)
        self.vols.append(float(v))

    def volume(self):
        return self.vol


class _PreviewWin(CommandBodies):
    def __init__(self, task, solo_ids=None):
        self.tasks = [task]
        self.curr_idx = 0
        self._jkl_rate = 0
        self._proxy_ready = False
        self.timeline = type("TL", (), {"_solo_ids": set(solo_ids or ()),
                                        "update": lambda *a, **k: None})()
        self.audio_output = _Out()
        self.bgm_audio_output = _Out()


def _role_task(with_files=False):
    tl = Timeline(1080, 1920, 30)
    tracks = {}
    clips = {}
    paths = {"main": WIND, "bgm": BIRDS, "sfx": WIND}
    for role in ("main", "bgm", "sfx"):
        src = MediaSource.__new__(MediaSource)
        src.id = role
        src.path = paths[role] if with_files else ("src-%s" % role)
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
        tr.clips.append(cl)
        tl.add_track(tr)
        tracks[role] = tr
        clips[role] = cl
    task = {
        "timeline": tl,
        "audio_fx": {
            "spatial_preset": "stage",
            "spatial_width": 0.62,
            "spatial_wet": 0.26,
        },
    }
    return task, tracks, clips


def test_volume_master_dialog_removed_command_stays():
    from cutora.ui.main_window import MainWindow
    assert not hasattr(MainWindow, "open_volume_master_dialog")
    mw = open(os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8").read()
    assert "open_volume_master_dialog" not in mw
    src = inspect.getsource(CommandBodies.command_volume_master)
    assert "batch_apply_volume" in src


def test_volume_master_batch_tab_uses_bus():
    from cutora.ui.batch_audio_tab import BatchAudioTab
    src = inspect.getsource(BatchAudioTab.apply_volume)
    assert "audio.volume_master" in src
    assert "execute" in src
    assert "self.batch_apply_volume" not in src


def test_intro_ducking_skipped_when_proxy_ready():
    from PySide6.QtMultimedia import QMediaPlayer
    from cutora.ui.main_window import MainWindow

    class Intro:
        def __init__(self):
            self._state = QMediaPlayer.PlaybackState.PlayingState

        def playbackState(self):
            return self._state

        def pause(self):
            self._state = QMediaPlayer.PlaybackState.PausedState

        def play(self):
            self._state = QMediaPlayer.PlaybackState.PlayingState

    host = SimpleNamespace(
        curr_idx=0,
        tasks=[{
            "_intro_video_state": {
                "intro_path": os.path.join(REPO, "x.mp4"),
                "effective_duration": 3.0,
                "transition_duration": 0.4,
                "intro_in_t": 0.0,
            },
        }],
        _timeline_ms=0,
        _timeline_playing=True,
        _proxy_ready=True,
        audio_output=_Out(),
        intro_audio_output=_Out(),
        intro_player=Intro(),
    )
    host.audio_output.vol = 0.85
    MainWindow._sync_intro_preview_audio(host, offset_ms=3000)
    assert 0.0 not in host.audio_output.vols
    assert host.audio_output.volume() == 0.85
    assert host.intro_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState


def test_intro_ducking_applies_when_preparing():
    from cutora.ui.main_window import MainWindow

    host = SimpleNamespace(
        curr_idx=0,
        tasks=[{
            "_intro_video_state": {
                "intro_path": os.path.join(REPO, "x.mp4"),
                "effective_duration": 3.0,
                "transition_duration": 0.4,
                "intro_in_t": 0.0,
            },
        }],
        _timeline_ms=0,
        _timeline_playing=True,
        _proxy_ready=False,
        audio_output=_Out(),
        intro_audio_output=_Out(),
    )
    MainWindow._sync_intro_preview_audio(host, offset_ms=3000)
    assert host.audio_output.volume() == 0.0


def test_proxy_ready_bgm_player_does_not_play():
    from PySide6.QtMultimedia import QMediaPlayer
    from cutora.ui.main_window import MainWindow

    class Bgm:
        def __init__(self):
            self.played = False
            self.paused = False

        def pause(self):
            self.paused = True

        def play(self):
            self.played = True

    host = SimpleNamespace(_proxy_ready=True, bgm_player=Bgm())
    MainWindow._sync_bgm_state(host, QMediaPlayer.PlaybackState.PlayingState)
    assert host.bgm_player.played is False
    assert host.bgm_player.paused is True


def test_proxy_ready_sfx_pool_idle():
    from cutora.ui.main_window import MainWindow

    stopped = []
    host = SimpleNamespace(
        _proxy_ready=True,
        _sfx_preview_on=True,
        _timeline_playing=True,
        curr_idx=0,
        tasks=[{}],
        _sfx_stop_all=lambda: stopped.append(1),
    )
    MainWindow._sync_sfx_preview(host)
    assert stopped


def test_proxy_ready_mute_main_does_not_mute_speaker():
    task, tracks, _clips = _role_task()
    tracks["main"].mute = True
    win = _PreviewWin(task)
    win._proxy_ready = True
    win._apply_preview_track_mute()
    assert win.audio_output.muted is False
    assert win.bgm_audio_output.muted is False


def test_proxy_ready_jkl_reverse_still_mutes_speaker():
    task, _tracks, _clips = _role_task()
    win = _PreviewWin(task)
    win._proxy_ready = True
    win._jkl_rate = -1
    win._apply_preview_track_mute()
    assert win.audio_output.muted is True


def test_solo_change_rebuilds_preview_wav():
    from cutora.ui.main_window import MainWindow

    rebuilds = []
    task, tracks, _clips = _role_task()

    class Host(CommandBodies):
        def __init__(self):
            self.tasks = [task]
            self.curr_idx = 0
            self._jkl_rate = 0
            self._proxy_ready = True
            self.timeline = type("TL", (), {
                "_solo_ids": set(),
                "update": lambda *a, **k: None,
            })()
            self.audio_output = _Out()
            self.bgm_audio_output = _Out()

        def _rebuild_preview_wav(self, immediate=False):
            rebuilds.append(immediate)

        def _schedule_autosave(self):
            return None

    host = Host()
    host.command_set_track_solo(tracks["bgm"].id, True)
    MainWindow._on_command_executed(host, {"name": "timeline.track.solo"})
    assert rebuilds
    assert str(tracks["bgm"].id) in host.timeline._solo_ids


def test_solo_header_goes_through_bus():
    from cutora.ui.timeline.container import TimelineContainer
    src = inspect.getsource(TimelineContainer._on_track_action)
    assert "timeline.track.solo" in src
    assert "_rebuild_preview_wav" not in src or "timeline.track.solo" in src


def test_fingerprint_includes_solo_and_global_spatial():
    task, tracks, clips = _role_task(with_files=True)
    k0 = preview_audio_fingerprint(task)
    k_solo = preview_audio_fingerprint(
        task, solo_ids={str(tracks["bgm"].id)})
    assert k0 != k_solo
    task["audio_fx"]["spatial_preset"] = "cathedral"
    k_preset = preview_audio_fingerprint(task)
    assert k_preset != k0
    clips["main"].spatial_preset = "hall"
    k_leftover = preview_audio_fingerprint(task)
    assert k_leftover == k_preset
    task["audio_fx"]["spatial_preset"] = "stage"
    task["audio_fx"]["spatial_width"] = 0.99
    k_width = preview_audio_fingerprint(task)
    assert k_width != k0


def test_preview_sfx_toggle_busts_fingerprint_and_omits_rows():
    task, _tracks, _clips = _role_task(with_files=True)
    k_on = preview_audio_fingerprint(task, preview_sfx=True)
    k_off = preview_audio_fingerprint(task, preview_sfx=False)
    assert k_on != k_off
    rows_on = mix_rows_for_task(task, preview_sfx=True)
    rows_off = mix_rows_for_task(task, preview_sfx=False)
    assert "sfx" in {clip_audio_role(r.get("_clip") or r) for r in rows_on}
    assert "sfx" not in {clip_audio_role(r.get("_clip") or r) for r in rows_off}
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    _fc, tracks = rw.export_audio_filter_complex(task, has_video=False)
    assert "sfx" in {str(t.get("audio_role") or "") for t in tracks}
    _fc2, tracks_off = rw.export_audio_filter_complex(
        task, has_video=False, preview_sfx=False)
    assert "sfx" not in {str(t.get("audio_role") or "") for t in tracks_off}


def test_command_spatial_set_writes_global_preset_width(monkeypatch):
    saved = {}

    def _load():
        return {
            "spatial_preset": "stage",
            "spatial_width": 0.62,
            "spatial_wet": 0.26,
            "sfx_volume": 0.18,
        }

    def _save(settings):
        saved.clear()
        saved.update(settings or {})

    monkeypatch.setattr(
        "cutora.audio.sfx_library.load_fx_settings", _load)
    monkeypatch.setattr(
        "cutora.audio.sfx_library.save_fx_settings", _save)
    task, _tracks, clips = _role_task()
    clips["main"].spatial_amount = 0.26
    win = _PreviewWin(task)
    win.command_spatial_set(preset="cathedral", width=0.8, wet=0.4)
    assert saved.get("spatial_preset") == "cathedral"
    assert saved.get("spatial_width") == 0.8
    assert task["audio_fx"]["spatial_preset"] == "cathedral"
    assert task["audio_fx"]["spatial_width"] == 0.8
    assert clips["main"].spatial_amount == 0.4
    assert clips["main"].spatial_enabled is True
    assert getattr(clips["main"], "spatial_preset", None) not in (
        "cathedral",)
    assert float(getattr(clips["bgm"], "spatial_amount", 0.0) or 0.0) != 0.4
