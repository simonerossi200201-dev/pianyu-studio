# -*- coding: utf-8 -*-
"""T07a: audio mute/solo uses the same track_outputs path as visual."""
from __future__ import annotations

import inspect

from cutora.audio.sfx_library import clip_audio_role
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, Track
from cutora.model.track_guard import (
    compute_audio_tracks,
    compute_export_audio_tracks,
)
from cutora.ui.command_bodies import CommandBodies


ROLES = ("main", "bgm", "sfx")


def _three_role_task():
    """One Track('audio') per role, clip.audio_role set. No type=='bgm'."""
    tl = Timeline(1920, 1080, 30)
    tracks = {}
    clips = {}
    for role in ROLES:
        tr = Track("audio")
        cl = Clip("src-%s" % role, 0.0, 2.0, 0.0)
        cl.audio_role = role
        if role == "main":
            cl.is_main = True
        tr.clips.append(cl)
        tl.tracks.append(tr)
        tracks[role] = tr
        clips[role] = cl
    return {"timeline": tl}, tracks, clips


def _roles(clips):
    return {clip_audio_role(c) for c in clips}


class _Out:
    def __init__(self):
        self.muted = False

    def setMuted(self, on):
        self.muted = bool(on)


class _PreviewWin(CommandBodies):
    def __init__(self, task, solo_ids=None):
        self.tasks = [task]
        self.curr_idx = 0
        self._jkl_rate = 0
        self._proxy_ready = False
        self.timeline = type("TL", (), {"_solo_ids": set(solo_ids or ())})()
        self.audio_output = _Out()
        self.bgm_audio_output = _Out()


def test_compute_export_audio_tracks_has_no_solo_ids():
    assert "solo_ids" not in inspect.signature(
        compute_export_audio_tracks).parameters
    try:
        compute_export_audio_tracks({}, solo_ids=set())
        raise AssertionError("solo_ids must not be accepted")
    except TypeError:
        pass


def test_unmuted_premix_keeps_all_three_roles():
    task, _tracks, clips = _three_role_task()
    export = compute_export_audio_tracks(task)
    preview = compute_audio_tracks(task)
    assert preview == export
    assert set(export) == set(clips.values())
    assert _roles(export) == set(ROLES)


def test_mute_main_drops_only_main():
    task, tracks, _clips = _three_role_task()
    tracks["main"].mute = True
    export = compute_export_audio_tracks(task)
    preview = compute_audio_tracks(task)
    assert preview == export
    assert _roles(export) == {"bgm", "sfx"}


def test_mute_bgm_drops_only_bgm():
    task, tracks, _clips = _three_role_task()
    tracks["bgm"].mute = True
    export = compute_export_audio_tracks(task)
    preview = compute_audio_tracks(task)
    assert preview == export
    assert _roles(export) == {"main", "sfx"}


def test_mute_sfx_drops_only_sfx():
    task, tracks, _clips = _three_role_task()
    tracks["sfx"].mute = True
    export = compute_export_audio_tracks(task)
    preview = compute_audio_tracks(task)
    assert preview == export
    assert _roles(export) == {"main", "bgm"}


def test_mute_preview_decision_equals_export_for_each_role():
    for role in ROLES:
        task, tracks, _clips = _three_role_task()
        tracks[role].mute = True
        preview = compute_audio_tracks(task, solo_ids=set())
        export = compute_export_audio_tracks(task)
        assert preview == export
        assert role not in _roles(export)
        assert _roles(export) == set(ROLES) - {role}


def test_solo_preview_applies_export_unaffected():
    task, tracks, clips = _three_role_task()
    unmuted = compute_export_audio_tracks(task)
    assert _roles(unmuted) == set(ROLES)
    for role in ROLES:
        solo = {str(tracks[role].id)}
        preview = compute_audio_tracks(task, solo_ids=solo)
        export = compute_export_audio_tracks(task)
        assert _roles(preview) == {role}
        assert preview == [clips[role]]
        assert export == unmuted
        assert _roles(export) == set(ROLES)


def test_preview_mute_bgm_does_not_mute_main_player():
    task, tracks, _clips = _three_role_task()
    tracks["bgm"].mute = True
    win = _PreviewWin(task)
    win._apply_preview_track_mute()
    assert win.audio_output.muted is False
    assert win.bgm_audio_output.muted is True


def test_preview_mute_main_leaves_bgm_player():
    task, tracks, _clips = _three_role_task()
    tracks["main"].mute = True
    win = _PreviewWin(task)
    win._apply_preview_track_mute()
    assert win.audio_output.muted is True
    assert win.bgm_audio_output.muted is False


def test_preview_mute_sfx_leaves_main_and_bgm_players():
    task, tracks, _clips = _three_role_task()
    tracks["sfx"].mute = True
    win = _PreviewWin(task)
    win._apply_preview_track_mute()
    assert win.audio_output.muted is False
    assert win.bgm_audio_output.muted is False
    assert "sfx" not in _roles(compute_audio_tracks(task))


def test_sfx_pool_feed_honors_mute_and_solo(tmp_path, monkeypatch):
    """The 4-slot SFX pool is fed by _get_current_sfx_clips (live_ids filter)."""
    from cutora.ui import main_window as mw
    wav = tmp_path / "tick.wav"
    wav.write_bytes(b"RIFF")
    task, tracks, clips = _three_role_task()
    row = {
        "audio_role": "sfx",
        "path": str(wav),
        "_clip_obj": clips["sfx"],
        "start": 0.0,
    }
    monkeypatch.setattr(mw, "_audio_clips_from_track", lambda _t: [row])
    stub = mw.MainWindow.__new__(mw.MainWindow)
    stub.curr_idx = 0
    stub.tasks = [task]
    stub.timeline = type("TL", (), {"_solo_ids": set()})()
    got = mw.MainWindow._get_current_sfx_clips(stub)
    assert len(got) == 1
    tracks["sfx"].mute = True
    assert mw.MainWindow._get_current_sfx_clips(stub) == []
    tracks["sfx"].mute = False
    stub.timeline._solo_ids = {str(tracks["main"].id)}
    assert mw.MainWindow._get_current_sfx_clips(stub) == []
    stub.timeline._solo_ids = {str(tracks["sfx"].id)}
    assert len(mw.MainWindow._get_current_sfx_clips(stub)) == 1


def test_preview_solo_bgm_silences_main_player_export_full():
    task, tracks, _clips = _three_role_task()
    win = _PreviewWin(task, solo_ids={str(tracks["bgm"].id)})
    win._apply_preview_track_mute()
    assert win.audio_output.muted is True
    assert win.bgm_audio_output.muted is False
    assert _roles(compute_export_audio_tracks(task)) == set(ROLES)


def test_proxy_ready_mute_main_does_not_mute_speaker():
    task, tracks, _clips = _three_role_task()
    tracks["main"].mute = True
    win = _PreviewWin(task)
    win._proxy_ready = True
    win._apply_preview_track_mute()
    assert win.audio_output.muted is False
    assert win.bgm_audio_output.muted is False


def test_proxy_ready_solo_bgm_does_not_mute_speaker():
    task, tracks, _clips = _three_role_task()
    win = _PreviewWin(task, solo_ids={str(tracks["bgm"].id)})
    win._proxy_ready = True
    win._apply_preview_track_mute()
    assert win.audio_output.muted is False
    from cutora.asr.timeline_wav import preview_audio_fingerprint
    k0 = preview_audio_fingerprint(task)
    k1 = preview_audio_fingerprint(task, solo_ids={str(tracks["bgm"].id)})
    assert k0 != k1


def test_sfx_pool_idle_when_proxy_ready():
    from cutora.ui import main_window as mw
    stub = mw.MainWindow.__new__(mw.MainWindow)
    stub._proxy_ready = True
    stub._sfx_preview_on = True
    stub._timeline_playing = True
    stub.curr_idx = 0
    stub.tasks = [{}]
    stopped = []
    stub._sfx_stop_all = lambda: stopped.append(True)
    mw.MainWindow._sync_sfx_preview(stub)
    assert stopped
