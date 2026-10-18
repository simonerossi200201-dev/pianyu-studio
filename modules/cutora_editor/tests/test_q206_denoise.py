# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q206: afftdn presets live on clip.audio_proc. Off stays byte-identical."""
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


def _task(path, *, role="main", seconds=2.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(1080, 1920, 30.0)
    src = MediaSource(path, duration=seconds, probe=False)
    tl.sources[src.id] = src
    tr = Track("audio")
    tr.audio_role = role
    cl = Clip(src.id, 0.0, seconds, 0.0)
    cl.audio_role = role
    cl.is_main = role == "main"
    cl.speed = 1.0
    tr.clips.append(cl)
    tl.add_track(tr)
    return {"timeline": tl, "src_volume": 1.0, "audio_fx": {}}, cl, src


def _filter(task):
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    return rw.export_audio_filter_complex(task, has_video=False)


def _wav(path, seconds=2.0):
    subprocess.check_call(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), path],
        cwd=REPO,
    )


def test_off_proc_matches_untouched_filter(tmp_path):
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    bare, _c1, _s1 = _task(wav)
    touched, cl, _s2 = _task(wav)
    cl.audio_proc = {}
    fc_bare, _ = _filter(bare)
    fc_off, tracks = _filter(touched)
    assert fc_off == fc_bare
    assert "afftdn" not in fc_off
    assert "atempo=" not in fc_off
    assert tracks[0]["denoise_chain"] == ""
    assert tracks[0]["path"] == wav


def test_denoise_preset_inserts_afftdn_without_sidecar(tmp_path):
    from cutora.audio.voice_presets import proc_for
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    task, cl, src = _task(wav)
    cl.audio_proc = proc_for("denoise", "mid")
    fc, tracks = _filter(task)
    assert "afftdn=nf=-25" in fc
    assert "atrim=start=0.0:duration=2.0" in fc
    assert tracks[0]["path"] == wav
    assert src.path == wav
    hall_task, hall, _ = _task(wav)
    hall.audio_proc = proc_for("大厅", "weak")
    fc_hall, _ = _filter(hall_task)
    assert "highpass=f=100,afftdn=nf=-30" in fc_hall
    assert "anlmdn" not in fc_hall


def test_sidecar_swaps_mix_path_and_skips_inline_chain(tmp_path):
    from cutora.asr.timeline_wav import mix_rows_for_task
    from cutora.audio.voice_presets import proc_for
    src_wav = str(tmp_path / "src.wav")
    side_wav = str(tmp_path / "side.wav")
    _wav(src_wav)
    _wav(side_wav)
    task, cl, src = _task(src_wav)
    proc = proc_for("podcast", "strong")
    proc["path"] = side_wav
    cl.audio_proc = proc
    fc, tracks = _filter(task)
    assert "afftdn" not in fc
    assert tracks[0]["path"] == side_wav
    assert src.path == src_wav
    rows = mix_rows_for_task(task)
    assert rows[0]["path"] == side_wav
    assert src.path == src_wav


def test_missing_sidecar_falls_back_and_keeps_chain(tmp_path):
    from cutora.asr.timeline_wav import mix_rows_for_task
    from cutora.audio.voice_presets import proc_for
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    task, cl, src = _task(wav)
    proc = proc_for("denoise", "mid")
    proc["path"] = str(tmp_path / "gone.wav")
    cl.audio_proc = proc
    fc, tracks = _filter(task)
    assert "afftdn=nf=-25" in fc
    assert tracks[0]["path"] == wav
    assert src.path == wav
    rows = mix_rows_for_task(task)
    assert rows[0]["path"] == wav


def test_empty_audio_proc_omitted_from_json():
    from cutora.model.project_io import _rj_serialize_clip
    from cutora.model.timeline_model import Clip
    from cutora.audio.voice_presets import proc_for
    cl = Clip("s", 0.0, 1.0, 0.0)
    assert cl.audio_proc == {}
    data = _rj_serialize_clip(cl)
    assert "audio_proc" not in data
    cl.audio_proc = proc_for("youtube", "weak")
    data = _rj_serialize_clip(cl)
    assert data["audio_proc"]["preset"] == "youtube"
    assert data["audio_proc"]["params"]["nf"] == -20


def test_fingerprint_off_path_ignores_empty_proc(tmp_path):
    from cutora.asr.timeline_wav import preview_audio_fingerprint
    from cutora.audio.voice_presets import proc_for
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    task, cl, _s = _task(wav)
    before = preview_audio_fingerprint(task)
    cl.audio_proc = {}
    assert preview_audio_fingerprint(task) == before
    cl.audio_proc = proc_for("denoise")
    assert preview_audio_fingerprint(task) != before


def test_assign_denoise_skips_bgm_and_source_path(tmp_path):
    from cutora.audio.voice_presets import assign_denoise
    from cutora.ui.command_bodies import CommandBodies
    wav = str(tmp_path / "a.wav")
    _wav(wav)
    task, voice, src = _task(wav)
    tl = task["timeline"]
    from cutora.model.timeline_model import Clip, Track
    btr = Track("audio")
    btr.audio_role = "bgm"
    bgm = Clip(voice.source_id, 0.0, 2.0, 0.0)
    bgm.audio_role = "bgm"
    btr.clips.append(bgm)
    tl.add_track(btr)

    class Win:
        tasks = [task]
        curr_idx = 0

    out = CommandBodies.command_audio_denoise(Win(), preset="denoise", strength="strong")
    assert out["updated"] == 1
    assert voice.audio_proc["preset"] == "denoise"
    assert voice.audio_proc["params"]["nf"] == -30
    assert bgm.audio_proc == {}
    assert src.path == wav
    n = assign_denoise(task, "off", track_id=tl.tracks[0].id, clip_index=0)
    assert n == 1
    assert voice.audio_proc == {}
    assert src.path == wav


def test_modules_do_not_import_torch_or_open_a_player():
    import cutora.audio.voice_presets as vp
    from cutora.ui.batch_audio_tab import BatchAudioTab
    from cutora.ui.timeline.widget import TimelineWidget
    body = inspect.getsource(vp)
    assert "import torch" not in body
    assert "from torch" not in body
    assert "QMediaPlayer" not in inspect.getsource(vp.audition_ffmpeg_argv)
    assert "QMediaPlayer" not in inspect.getsource(BatchAudioTab.audition_denoise_five)
    menu = inspect.getsource(TimelineWidget._popup_audio_clip_menu)
    assert "audio.denoise" in menu
    assert "降噪" in menu
    argv = vp.audition_ffmpeg_argv("ffmpeg", "a.wav", "afftdn=nf=-25", "out.wav")
    assert "-t" in argv and "5" in argv
    assert "QMediaPlayer" not in argv
