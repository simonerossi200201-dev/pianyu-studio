# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q209: Demucs isolate stays out of the GUI process. Off path is unchanged."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
WORKER = os.path.join(REPO, "cutora", "audio", "stem_worker.py")


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


def test_worker_source_keeps_the_model_out_of_the_gui():
    text = open(WORKER, encoding="utf-8").read()
    assert "htdemucs.th" not in text
    assert "import torch" not in text
    assert "from torch" not in text
    for line in text.splitlines():
        if line.startswith("import ") or line.startswith("from "):
            assert "demucs" not in line
            assert "torch" not in line
    menu = open(os.path.join(REPO, "cutora", "ui", "timeline", "widget.py"), encoding="utf-8").read()
    batch = open(os.path.join(REPO, "cutora", "ui", "batch_audio_tab.py"), encoding="utf-8").read()
    prop = open(os.path.join(REPO, "cutora", "ui", "panels", "property.py"), encoding="utf-8").read()
    assert "只留说话" in menu and "只留伴奏" in menu
    assert "只留说话" in batch and "只留伴奏" in batch
    assert "清除分离" in prop and "audio_stem_status" in prop


def test_missing_weights_and_export_do_not_write_a_path(tmp_path):
    from cutora.audio.stem_worker import main, plan_isolate
    empty = tmp_path / "empty"
    empty.mkdir()
    class _Clip:
        audio_proc = {}

    clip = _Clip()
    missing = plan_isolate(clip, exporting=False, weights_root=str(empty))
    assert missing["ok"] is False
    assert "没有找到人声分离模型" in missing["reason"]
    assert clip.audio_proc == {}
    packed = tmp_path / "pack"
    packed.mkdir()
    (packed / "pack.pt").write_bytes(b"not-a-model")
    busy = plan_isolate(clip, exporting=True, weights_root=str(packed))
    assert busy["ok"] is False
    assert "正在导出" in busy["reason"]
    assert clip.audio_proc == {}
    assert "path" not in str(busy)
    rc = main([
        "--src", "a.wav", "--out", str(tmp_path / "out"),
        "--weights", str(tmp_path / "missing.pt"), "--mode", "vocals",
    ])
    assert rc == 2
    assert "demucs" not in sys.modules


def test_sidecar_swaps_mix_path_and_empty_proc_matches(tmp_path):
    from cutora.asr.timeline_wav import mix_rows_for_task
    from cutora.audio.stem_worker import apply_sidecar, find_stem_file
    wav = str(tmp_path / "voice.wav")
    side = str(tmp_path / "vocals.wav")
    _wav(wav)
    _wav(side)
    bare, _c0, _s0 = _task(wav)
    off, cl_off, src_off = _task(wav)
    cl_off.audio_proc = {}
    fc_bare, _ = _filter(bare)
    fc_off, tracks = _filter(off)
    assert fc_off == fc_bare
    assert tracks[0]["path"] == wav
    assert src_off.path == wav
    task, cl, src = _task(wav)
    assert apply_sidecar(cl, "novocals", str(tmp_path / "missing.wav")) is False
    assert cl.audio_proc == {}
    assert apply_sidecar(cl, "vocals", side) is True
    assert cl.audio_proc["op"] == "stem_vocals"
    assert src.path == wav
    rows = mix_rows_for_task(task)
    assert rows and os.path.abspath(rows[0]["path"]) == os.path.abspath(side)
    assert src.path == wav
    nested = tmp_path / "work" / "htdemucs" / "voice"
    nested.mkdir(parents=True)
    (nested / "vocals.wav").write_bytes(b"RIFF")
    assert find_stem_file(str(tmp_path / "work"), "vocals").endswith("vocals.wav")


def test_jobs_do_not_touch_proc_and_command_refuses_early(tmp_path, monkeypatch):
    from cutora.audio.stem_worker import build_isolate_jobs
    from cutora.core.commands import CommandError
    from cutora.ui.command_bodies import CommandBodies
    wav = str(tmp_path / "voice.wav")
    _wav(wav)
    task, cl, _src = _task(wav)
    weight = tmp_path / "m.pt"
    weight.write_bytes(b"x")
    jobs = build_isolate_jobs(
        task, "novocals", str(weight), str(tmp_path / "cache"), sys.executable)
    assert len(jobs) == 1
    assert cl.audio_proc == {}
    argv = jobs[0]["argv"]
    assert "--two-stems" in argv and "vocals" in argv
    assert jobs[0]["dest"].endswith("no_vocals.wav")
    assert "htdemucs.th" not in " ".join(argv)

    class Win(CommandBodies):
        pass

    win = Win()
    win.tasks = [task]
    win.curr_idx = 0
    win.is_exporting = True
    with pytest.raises(CommandError) as exc:
        win.command_audio_isolate(mode="vocals", scope="current")
    assert "正在导出" in str(exc.value)
    assert cl.audio_proc == {}

    win.is_exporting = False
    monkeypatch.setattr("cutora.audio.stem_worker.find_weights", lambda root=None: "")
    with pytest.raises(CommandError) as missing:
        win.command_audio_isolate(mode="vocals", scope="current")
    assert "没有找到人声分离模型" in str(missing.value)
    assert cl.audio_proc == {}


def test_fake_no_gpu_exposes_the_notice_without_a_dialog(monkeypatch):
    monkeypatch.setenv("CUTORA_FAKE_NO_GPU", "1")
    from cutora.ui.gpu_notice import FEATURES, has_gpu, reset
    reset()
    assert has_gpu() is False
    assert "stem_split" in FEATURES
    assert "只留说话" in FEATURES["stem_split"][0]
    from cutora.core.cmds.specs_edit import build
    names = [item.name for item in build()]
    assert "audio.isolate" in names


def test_gui_modules_do_not_import_the_model():
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("CUTORA_FAKE_NO_GPU", None)
    script = (
        "import sys\n"
        "import cutora.audio.stem_worker\n"
        "import cutora.ui.gpu_notice\n"
        "import cutora.ui.batch_audio_tab\n"
        "import cutora.ui.command_bodies\n"
        "assert 'torch' not in sys.modules\n"
        "assert 'demucs' not in sys.modules\n"
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=REPO, env=env)
