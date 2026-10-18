# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q205: constant audio speed lives in the filter graph, not the clock."""
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


def _task(path, speed, *, role="main", seconds=2.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource(path, duration=seconds, probe=False)
    tl.sources[src.id] = src
    tr = Track("audio")
    tr.audio_role = role
    cl = Clip(src.id, 0.0, seconds, 0.0)
    cl.speed = speed
    cl.audio_role = role
    cl.is_main = role == "main"
    tr.clips.append(cl)
    tl.add_track(tr)
    return {"timeline": tl, "src_volume": 1.0, "audio_fx": {}}, cl


def _filter(task):
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=False)
    return fc, tracks


def _wav(path, seconds=2.0):
    subprocess.check_call(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), path],
        cwd=REPO,
    )


def test_speed_one_filter_has_no_speed_atempo(tmp_path):
    wav = str(tmp_path / "a.wav")
    _wav(wav, 2.0)
    task, _cl = _task(wav, 1.0)
    fc, tracks = _filter(task)
    assert tracks
    assert "atrim=start=0.0:duration=2.0" in fc
    assert "atempo=" not in fc
    assert abs(float(tracks[0]["duration"]) - 2.0) < 1e-9
    assert abs(float(tracks[0]["src_span"]) - 2.0) < 1e-9


def test_speed_two_atrim_keeps_source_span_and_pcm_halves(tmp_path):
    wav = str(tmp_path / "a.wav")
    _wav(wav, 2.0)
    task, _cl = _task(wav, 2.0)
    fc, tracks = _filter(task)
    assert "atrim=start=0.0:duration=2.0" in fc
    assert "atempo=2" in fc
    assert abs(float(tracks[0]["src_span"]) - 2.0) < 1e-9
    assert abs(float(tracks[0]["duration"]) - 1.0) < 1e-6
    cmd = [
        FFMPEG, "-y", "-v", "error", "-i", wav,
        "-filter_complex", fc, "-map", "[aout]",
        "-f", "f32le", "-ac", "2", "-ar", "48000", "pipe:1",
    ]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True)
    assert r.returncode == 0, r.stderr[-300:]
    pcm = np.frombuffer(r.stdout, dtype=np.float32)
    seconds = (pcm.size / 2) / 48000.0
    assert abs(seconds - 1.0) < 0.05, seconds


def test_split_atempo_below_half():
    from cutora.render.render_worker import split_atempo
    stages = split_atempo(0.25)
    assert stages[0] == 0.5
    prod = 1.0
    for s in stages:
        assert 0.5 <= s <= 100.0
        prod *= s
    assert abs(prod - 0.25) < 1e-6
    assert split_atempo(1.0) == []


def test_bar_width_uses_effective_duration():
    from cutora.model.timeline_model import Clip
    cl = Clip("s", 0.0, 2.0, 0.0)
    cl.speed = 2.0
    assert abs(cl.effective_duration - 1.0) < 1e-9
    src = inspect.getsource(
        __import__("cutora.ui.timeline.widget", fromlist=["TimelineWidget"]).TimelineWidget._paint_audio_track
    )
    assert "effective_duration" in src


def test_linked_voice_copies_speed_bgm_does_not():
    from cutora.model.av_link import sync_linked_constant_speed
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(1080, 1920, 30.0)
    vtr = Track("video")
    atr = Track("audio")
    atr.audio_role = "main"
    btr = Track("audio")
    btr.audio_role = "bgm"
    vc = Clip("v", 0.0, 4.0, 0.0)
    ac = Clip("a", 0.0, 4.0, 0.0)
    ac.audio_role = "main"
    bg = Clip("b", 0.0, 4.0, 0.0)
    bg.audio_role = "bgm"
    vc.link_group_id = ac.link_group_id = "gid"
    bg.link_group_id = "gid"
    vtr.clips.append(vc)
    atr.clips.append(ac)
    btr.clips.append(bg)
    tl.add_track(vtr)
    tl.add_track(atr)
    tl.add_track(btr)
    vc.speed = 2.0
    changed = sync_linked_constant_speed(tl, vc)
    assert ac in changed
    assert ac.speed == 2.0
    assert bg.speed == 1.0
    assert bg not in changed


def test_preview_clock_stays_one_when_stems_ready():
    from cutora.audio.stem_cache import preview_clock_rate
    assert preview_clock_rate(stems_ready=True, jkl_rate=0, clip_speed=2.0) == 1.0
    assert preview_clock_rate(stems_ready=False, jkl_rate=0, clip_speed=2.0) == 2.0
    assert preview_clock_rate(stems_ready=True, jkl_rate=2, clip_speed=2.0) == 2.0
    src = inspect.getsource(
        __import__("cutora.ui.main_window", fromlist=["MainWindow"]).MainWindow.sync_ui
    )
    assert "preview_clock_rate" in src


def test_second_drag_is_not_baked_times_live():
    from cutora.audio.stem_cache import speed_drag_live, speed_drag_src
    sr = 48000
    src_span = 2.0
    stem = np.ones((sr, 2), np.float32)
    drag = speed_drag_src(stem, 0.0, src_span, 2.0)
    assert len(drag) == int(src_span * sr)
    live = speed_drag_live(drag, src_span, 4.0)
    assert len(live) == int(src_span / 4.0 * sr)
    assert len(live) != int(src_span / 8.0 * sr)
    body = inspect.getsource(speed_drag_live) + inspect.getsource(speed_drag_src)
    assert "subprocess" not in body
    assert "FFMPEG" not in body
    assert "run_filter" not in body


def test_audio_resize_uses_trim_clip_edge():
    src = inspect.getsource(
        __import__("cutora.ui.timeline.widget", fromlist=["TimelineWidget"]).TimelineWidget.mouseMoveEvent
    )
    assert "trim_clip_edge" in src
    assert "TOOL_SPEED" in src
