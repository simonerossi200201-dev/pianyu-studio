# -*- coding: utf-8 -*-
"""T20-f: left-dock transition panel lists video + audio libraries."""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_transition_panel_lists_video_and_audio():
    from cutora.render.anim_engine import (
        AUDIO_TRANSITION_LIBRARY,
        STANDALONE_TRANSITION_LIBRARY,
    )

    assert len(STANDALONE_TRANSITION_LIBRARY) >= 11
    assert len(AUDIO_TRANSITION_LIBRARY) == 5
    audio_presets = [p for _lab, p in AUDIO_TRANSITION_LIBRARY]
    assert audio_presets == [
        "audio_xfade_equal",
        "audio_xfade_linear",
        "audio_fade_in",
        "audio_fade_out",
        "audio_seam_fade",
    ]
    audio_labels = [lab for lab, _p in AUDIO_TRANSITION_LIBRARY]
    assert "等功率交叉淡化" in audio_labels
    assert "线性交叉淡化" in audio_labels
    assert "淡入" in audio_labels
    assert "淡出" in audio_labels
    assert "接缝淡化" in audio_labels

    code = r"""
from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)
from PySide6.QtWidgets import QApplication, QLabel
from cutora.ui.panels.transition_panel import TransitionPanel, AUDIO_ITEMS, VIDEO_ITEMS
from cutora.render.anim_engine import (
    AUDIO_TRANSITION_LIBRARY, STANDALONE_TRANSITION_LIBRARY,
)
app = QApplication.instance() or QApplication(['t20f'])
p = TransitionPanel()
p.show()
assert p.objectName() == 'transitionPanel'
assert VIDEO_ITEMS == list(STANDALONE_TRANSITION_LIBRARY)
assert AUDIO_ITEMS == list(AUDIO_TRANSITION_LIBRARY)
labels = [w.text() for w in p.findChildren(QLabel) if w.text()]
blob = ' '.join(labels)
assert '视频转场' in blob, blob
assert '音频转场' in blob, blob
assert p.video_count() == len(STANDALONE_TRANSITION_LIBRARY)
assert p.video_count() >= 11
assert p.audio_count() == len(AUDIO_TRANSITION_LIBRARY) == 5
print('ok')
"""
    r = subprocess.run(
        [VENV_PY if os.path.isfile(VENV_PY) else sys.executable, "-c", code],
        cwd=REPO, env=_child_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "ok" in (r.stdout or ""), out[-4000:]


def test_audio_transition_fades_export_records():
    from cutora.model.seams import apply_audio_transition_fades
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem

    tl = Timeline(1080, 1920, 30)
    tr = Track("audio")
    left = Clip("src-a", 0.0, 2.0, 0.0)
    right = Clip("src-b", 0.0, 2.0, 2.0)
    left.is_main = right.is_main = True
    left.audio_role = right.audio_role = "main"
    tr.clips.extend([left, right])
    tr.transitions.append(TransitionItem(
        start_t=1.825, duration=0.35, preset="audio_xfade_equal",
        domain="audio",
    ))
    tl.tracks.append(tr)
    recs = apply_audio_transition_fades({"timeline": tl})
    assert len(recs) == 2
    assert recs[0]["fade_out_dur"] >= 0.35 - 1e-9
    assert recs[1]["fade_in_dur"] >= 0.35 - 1e-9
    assert recs[0]["fade_curve"] == "esin"
    assert recs[1]["fade_curve"] == "esin"
    assert recs[0]["clip"] is left
    assert recs[1]["clip"] is right


def _audio_xfade_task(wav_a, wav_b):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem

    tl = Timeline(1080, 1920, 30)
    src_a = MediaSource(wav_a)
    src_b = MediaSource(wav_b)
    src_a.has_video = src_b.has_video = False
    src_a.has_audio = src_b.has_audio = True
    src_a.duration = src_b.duration = 2.0
    tl.sources[src_a.id] = src_a
    tl.sources[src_b.id] = src_b
    tr = Track("audio")
    left = Clip(src_a.id, 0.0, 2.0, 0.0)
    right = Clip(src_b.id, 0.0, 2.0, 2.0)
    left.is_main = right.is_main = True
    left.audio_role = right.audio_role = "main"
    tr.clips.extend([left, right])
    tr.transitions.append(TransitionItem(
        start_t=1.825, duration=0.35, preset="audio_xfade_equal",
        domain="audio",
    ))
    tl.tracks.append(tr)
    return {"timeline": tl, "audio_fx": {"spatial_enabled": False}}, left, right


def test_preview_mix_applies_audio_transition_fades(tmp_path):
    """mix_rows_for_task must pick up TransitionItem fades + esin, matching export."""
    import wave
    import struct
    from cutora.asr.timeline_wav import mix_rows_for_task
    from cutora.model.seams import apply_audio_transition_fades
    from cutora.render.render_worker import RenderWorker

    def _tone(path):
        n = 48000 * 2
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<h", 8000) for _ in range(n)))
        return path

    wav_a = _tone(str(tmp_path / "a.wav"))
    wav_b = _tone(str(tmp_path / "b.wav"))
    task, left, right = _audio_xfade_task(wav_a, wav_b)
    recs = apply_audio_transition_fades(task)
    rows = mix_rows_for_task(task)
    assert len(rows) == 2
    assert rows[0]["fade_out_dur"] >= 0.35 - 1e-9
    assert rows[1]["fade_in_dur"] >= 0.35 - 1e-9
    assert rows[0]["fade_curve"] == "esin"
    assert rows[1]["fade_curve"] == "esin"
    assert recs[0]["fade_curve"] == rows[0]["fade_curve"]
    assert abs(recs[0]["fade_out_dur"] - rows[0]["fade_out_dur"]) < 1e-9
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    tracks = rw._build_audio_tracks(task)
    assert tracks[0]["fade_curve"] == "esin"
    assert abs(tracks[0]["fade_out_dur"] - rows[0]["fade_out_dur"]) < 1e-9


def test_preview_wav_filter_uses_esin_curve(tmp_path, monkeypatch):
    import struct
    import subprocess
    import wave
    from cutora.asr.timeline_wav import render_preview_wav

    def _tone(path):
        n = 48000 * 2
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"".join(struct.pack("<h", 8000) for _ in range(n)))
        return path

    wav_a = _tone(str(tmp_path / "a.wav"))
    wav_b = _tone(str(tmp_path / "b.wav"))
    task, _l, _r = _audio_xfade_task(wav_a, wav_b)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        script = cmd[cmd.index("-filter_complex_script") + 1]
        captured["fc"] = open(script, encoding="utf-8").read()
        dest = cmd[-1]
        n = 48000
        with wave.open(dest, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x00" * n * 2)
        class R:
            returncode = 0
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    render_preview_wav(task, str(tmp_path / "out.wav"))
    cmd = captured["cmd"]
    fc = captured["fc"]
    assert "-filter_complex" not in cmd
    assert "curve=esin" in fc, fc
    assert "afade=t=in" in fc, fc
    assert "afade=t=out" in fc, fc
