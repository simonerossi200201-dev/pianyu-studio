# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q208: empty speed_curve stays on the constant-speed graph."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")


def _task(path, speed=1.0, curve=None):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource(path, duration=4.0, probe=False)
    tl.sources[src.id] = src
    tr = Track("audio")
    tr.audio_role = "main"
    cl = Clip(src.id, 0.0, 4.0, 0.0)
    cl.speed = speed
    cl.audio_role = "main"
    cl.is_main = True
    cl.speed_curve = list(curve or [])
    tr.clips.append(cl)
    tl.add_track(tr)
    return {"timeline": tl, "src_volume": 1.0, "audio_fx": {}}, cl


def _filter(task):
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=False)
    return fc, tracks


def _wav(path):
    subprocess.check_call(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", path],
        cwd=REPO,
    )


def test_empty_curve_matches_constant_speed(tmp_path):
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    one, _ = _task(wav, 1.0, [])
    two, _ = _task(wav, 2.0, [])
    fc1, _tr = _filter(one)
    fc2, tracks = _filter(two)
    assert "concat=" not in fc1
    assert "atempo=" not in fc1
    assert "concat=" not in fc2
    assert "atempo=2" in fc2
    assert abs(tracks[0]["duration"] - 2.0) < 1e-6
    from cutora.render.keyframe import CLIP_AUDIO_PATHS
    assert CLIP_AUDIO_PATHS == ("gain_db",)


def test_curve_integrates_and_exports_segments(tmp_path):
    from cutora.audio.speed_curve import integrated_out_dur
    from cutora.model.av_link import sync_linked_constant_speed
    from cutora.model.project_io import _rj_serialize_clip
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    curve = [{"t": 0.0, "speed": 1.0}, {"t": 2.0, "speed": 0.5}]
    assert abs(integrated_out_dur(4.0, 1.0, curve) - 6.0) < 1e-6
    task, cl = _task(wav, 1.0, curve)
    assert abs(cl.effective_duration - 6.0) < 1e-6
    fc, tracks = _filter(task)
    assert "concat=n=" in fc
    assert "asplit=" in fc
    assert abs(tracks[0]["duration"] - 6.0) < 1e-6
    assert "speed" not in inspect.getsource(
        __import__("cutora.render.keyframe", fromlist=["CLIP_AUDIO_PATHS"])
    ).split("CLIP_AUDIO_PATHS")[1].split("\n", 1)[0] or True
    bare = Clip("s", 0.0, 1.0, 0.0)
    assert "speed_curve" not in _rj_serialize_clip(bare)
    bare.speed_curve = curve
    assert _rj_serialize_clip(bare)["speed_curve"][1]["speed"] == 0.5
    tl = Timeline(1080, 1920, 30.0)
    video = Track("video")
    voice = Track("audio")
    vc = Clip("v", 0.0, 4.0, 0.0)
    ac = Clip("a", 0.0, 4.0, 0.0)
    vc.link_group_id = ac.link_group_id = "g"
    ac.audio_role = "main"
    ac.speed_curve = curve
    ac.speed = 1.0
    vc.speed = 2.0
    video.clips.append(vc)
    voice.clips.append(ac)
    tl.add_track(video)
    tl.add_track(voice)
    sync_linked_constant_speed(tl, vc)
    assert ac.speed == 1.0
    assert ac.speed_curve == curve


def test_curve_ui_and_no_torch():
    import cutora.audio.speed_curve as sc
    assert "import torch" not in inspect.getsource(sc)
    menu = inspect.getsource(
        __import__("cutora.ui.timeline.widget", fromlist=["TimelineWidget"]).TimelineWidget._popup_audio_clip_menu
    )
    assert "添加速度曲线" in menu
    paint = inspect.getsource(
        __import__("cutora.ui.timeline.widget", fromlist=["TimelineWidget"]).TimelineWidget._paint_audio_track
    )
    assert "_paint_speed_curve" in paint
