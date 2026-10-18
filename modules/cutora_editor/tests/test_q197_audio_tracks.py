# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q197: role tracks, insert paths, serdes, audio-domain migrate."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from types import SimpleNamespace

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIND = os.path.join(REPO, "resources", "audio", "sfx", "wind_soft.wav")
BIRDS = os.path.join(REPO, "resources", "audio", "sfx", "birds_forest.wav")


def _audio_tracks(tl):
    return [
        tr for tr in (getattr(tl, "tracks", None) or [])
        if getattr(tr, "domain", None) == "audio"
        or getattr(tr, "type", None) in ("audio", "bgm", "sfx")
    ]


def _clip_fields(clip):
    keys = (
        "audio_role", "is_main", "fit_mode", "x", "y", "scale",
        "source_id", "in_t", "out_t", "start_t", "gain_db",
        "spatial_enabled", "spatial_amount",
    )
    if isinstance(clip, dict):
        return {k: clip.get(k) for k in keys}
    return {k: getattr(clip, k, None) for k in keys}


def test_ensure_role_track_never_returns_mixed():
    from cutora.audio.sfx_library import clip_audio_role, ensure_role_track
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(1080, 1920, 30)
    mixed = Track("audio")
    main_c = Clip("v", 0.0, 2.0, 0.0)
    main_c.is_main = True
    main_c.audio_role = "main"
    bgm_c = Clip("m", 0.0, 2.0, 0.0)
    bgm_c.audio_role = "bgm"
    mixed.clips.extend([main_c, bgm_c])
    tl.add_track(mixed)

    bgm_tr = ensure_role_track(tl, "bgm")
    assert bgm_tr is not mixed
    roles = {clip_audio_role(c) for c in (bgm_tr.clips or [])}
    assert "main" not in roles
    main_tr = ensure_role_track(tl, "main")
    assert main_tr is not mixed
    assert {clip_audio_role(c) for c in (main_tr.clips or [])} <= {"main"}


def test_drop_wav_leaves_main_clips():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.main_window import MainWindow

    tl = Timeline(1080, 1920, 30)
    voice = MediaSource.__new__(MediaSource)
    voice.id = "voice"
    voice.path = "voice.wav"
    voice.duration = 4.0
    voice.has_video = False
    voice.has_audio = True
    tl.sources[voice.id] = voice
    main_tr = Track("audio")
    main_tr.audio_role = "main"
    main_tr.name = "原声"
    main_clip = Clip(voice.id, 0.0, 4.0, 0.0)
    main_clip.is_main = True
    main_clip.audio_role = "main"
    main_tr.clips.append(main_clip)
    tl.add_track(main_tr)
    task = {"timeline": tl, "duration": 4.0, "audio_clips": []}
    before = list(main_tr.clips)

    class Host:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

        def _pending_drop_duration(self, _task, _start_t):
            return 2.0

        def _note_drop_slot(self, slot):
            return None

        def _queue_drop_probe(self, slot):
            return None

    MainWindow._drop_audio_file(Host(), task, tl, os.path.join(REPO, "bed.wav"), 0.0)
    assert list(main_tr.clips) == before
    audio = _audio_tracks(tl)
    assert any(getattr(tr, "audio_role", "") == "bgm" for tr in audio)
    bgm_tr = next(tr for tr in audio if getattr(tr, "audio_role", "") == "bgm")
    assert bgm_tr is not main_tr
    assert bgm_tr.clips and getattr(bgm_tr.clips[0], "audio_role", "") == "bgm"
    assert getattr(bgm_tr.clips[0], "is_main", True) is False


def test_alt_a_creates_bgm_role_track():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.main_window import MainWindow

    tl = Timeline(1080, 1920, 30)
    main_tr = Track("audio")
    main_tr.audio_role = "main"
    main_tr.name = "原声"
    tl.add_track(main_tr)
    task = {"timeline": tl}

    class Host:
        def _ensure_task_for_overlay(self):
            return task

        def push_undo(self, *a, **k):
            return None

        timeline = SimpleNamespace(update=lambda: None)

    added = MainWindow.add_timeline_track(Host(), "audio")
    assert added is not None
    assert added is not main_tr
    assert getattr(added, "audio_role", "") == "bgm"
    assert str(getattr(added, "name", "") or "").startswith("配乐")

    second = MainWindow.add_timeline_track(Host(), "audio")
    assert getattr(second, "audio_role", "") == "bgm"
    assert second.name != added.name


def test_golden_shaped_video_clip_not_moved():
    from cutora.model.project_io import _migrate_mixed_audio_tracks
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(1080, 1920, 30)
    vtr = Track("video")
    vtr.is_main_video = True
    vclip = Clip("vid", 0.0, 3.0, 0.0)
    vclip.is_main = False
    vclip.audio_role = ""
    vclip.fit_mode = "fill"
    vclip.x = 0.0
    vclip.y = 0.0
    vclip.scale = 1.0
    vtr.clips.append(vclip)
    atr = Track("audio")
    main_c = Clip("vid", 0.0, 3.0, 0.0)
    main_c.is_main = True
    main_c.audio_role = ""
    bgm_c = Clip("bed", 0.0, 3.0, 0.0)
    bgm_c.is_main = False
    bgm_c.audio_role = ""
    atr.clips.extend([main_c, bgm_c])
    tl.add_track(vtr)
    tl.add_track(atr)
    snap = _clip_fields(vclip)
    video_clips = list(vtr.clips)
    task = {"timeline": tl, "video": r"D:\media\talk.mp4"}
    _migrate_mixed_audio_tracks(tl, task)
    assert list(vtr.clips) == video_clips
    assert vclip is video_clips[0]
    assert _clip_fields(vclip) == snap
    assert getattr(vclip, "audio_role", None) == ""
    assert getattr(vclip, "is_main", None) is False
    assert getattr(vclip, "fit_mode", None) == "fill"


def test_track_audio_role_persist_roundtrip():
    from cutora.model.project_io import (
        deserialize_render_task, serialize_render_task, _rj_serialize_track,
    )
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(320, 180, 30.0)
    tr = Track("audio")
    tr.audio_role = "sfx"
    tr.name = "音效"
    tr.mute = True
    clip = Clip("s", 0.0, 1.0, 0.0)
    clip.audio_role = "sfx"
    tr.clips.append(clip)
    tl.add_track(tr)
    blob = serialize_render_task({"timeline": tl, "name": "q197"})
    dumped = _rj_serialize_track(tr)
    assert dumped.get("audio_role") == "sfx"
    assert "audio_role" in dumped
    back = deserialize_render_task(blob)
    tracks = list(back["timeline"].tracks)
    assert tracks and getattr(tracks[0], "audio_role", "") == "sfx"
    assert tracks[0].name == "音效"
    assert bool(tracks[0].mute) is True


def _make_wav(path, freq):
    from cutora.core.config import FFMPEG_PATH
    cmd = [
        FFMPEG_PATH, "-y", "-v", "error",
        "-f", "lavfi", "-i", "sine=frequency=%s:duration=1:sample_rate=48000" % freq,
        "-ac", "2", "-c:a", "pcm_s16le", path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 0 and os.path.isfile(path), (r.stderr or b"").decode(
        "utf-8", "replace")[-300:]


def _pcm_md5(task, dest):
    from cutora.core.config import FFMPEG_PATH
    from cutora.render.render_worker import RenderWorker

    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=False)
    assert tracks and fc, (fc, tracks)
    cmd = [FFMPEG_PATH, "-y", "-v", "error"]
    for t in tracks:
        cmd += ["-i", t["path"]]
    cmd += [
        "-filter_complex", fc, "-map", "[aout]",
        "-t", "1", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    assert r.returncode == 0 and os.path.isfile(dest), (r.stderr or b"").decode(
        "utf-8", "replace")[-400:]
    return hashlib.md5(open(dest, "rb").read()).hexdigest()


def test_migrate_mixed_track_pcm_md5_unchanged(tmp_path):
    from cutora.model.project_io import _migrate_mixed_audio_tracks
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    voice = str(tmp_path / "voice.wav")
    bed = str(tmp_path / "bed.wav")
    if os.path.isfile(WIND) and os.path.isfile(BIRDS):
        voice, bed = WIND, BIRDS
    else:
        _make_wav(voice, 440)
        _make_wav(bed, 880)

    tl = Timeline(1080, 1920, 30)
    vsrc = MediaSource.__new__(MediaSource)
    vsrc.id = "voice"
    vsrc.path = voice
    vsrc.duration = 1.0
    vsrc.has_video = False
    vsrc.has_audio = True
    bsrc = MediaSource.__new__(MediaSource)
    bsrc.id = "bed"
    bsrc.path = bed
    bsrc.duration = 1.0
    bsrc.has_video = False
    bsrc.has_audio = True
    tl.sources[vsrc.id] = vsrc
    tl.sources[bsrc.id] = bsrc

    vtr = Track("video")
    vclip = Clip(vsrc.id, 0.0, 1.0, 0.0)
    vclip.is_main = False
    vclip.audio_role = ""
    vclip.fit_mode = "fill"
    vclip.x = 0.0
    vclip.y = 0.0
    vclip.scale = 1.0
    vtr.clips.append(vclip)

    atr = Track("audio")
    main_c = Clip(vsrc.id, 0.0, 1.0, 0.0)
    main_c.is_main = True
    main_c.audio_role = "main"
    main_c.spatial_enabled = False
    main_c.gain_db = -1.0
    bgm_c = Clip(bsrc.id, 0.0, 1.0, 0.0)
    bgm_c.is_main = False
    bgm_c.audio_role = "bgm"
    bgm_c.spatial_enabled = False
    bgm_c.gain_db = -12.0
    atr.clips.extend([main_c, bgm_c])
    tl.add_track(vtr)
    tl.add_track(atr)
    task = {
        "timeline": tl,
        "video": voice,
        "src_volume": 1.0,
        "audio_fx": {"spatial_enabled": False, "spatial_wet": 0.0},
    }
    video_snap = _clip_fields(vclip)
    before = _pcm_md5(task, str(tmp_path / "before.wav"))
    _migrate_mixed_audio_tracks(tl, task)
    assert _clip_fields(vclip) == video_snap
    assert list(vtr.clips) == [vclip]
    audio = _audio_tracks(tl)
    assert len(audio) >= 2
    roles = {getattr(tr, "audio_role", "") for tr in audio}
    assert "main" in roles and "bgm" in roles
    after = _pcm_md5(task, str(tmp_path / "after.wav"))
    assert before == after, (before, after)
