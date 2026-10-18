# -*- coding: utf-8 -*-
"""T12c 提交 1：识别路径 VAD 必须是 faster-whisper 内置 Silero，不得默认加载 pyannote。"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_whisperx_vendor_defaults_to_pyannote_so_we_must_override():
    asr = os.path.join(REPO, "vendor", "whisperX", "whisperx", "asr.py")
    src = open(asr, encoding="utf-8").read()
    assert 'vad_method: Optional[str] = "pyannote"' in src


def test_engine_passes_faster_whisper_vad_into_load_model():
    src = open(
        os.path.join(REPO, "cutora", "asr", "engine.py"), encoding="utf-8"
    ).read()
    assert "whisperx_vad_model" in src
    assert "vad_model=" in src
    assert 'vad_method="pyannote"' not in src
    assert "vad_model=whisperx_vad_model()" in src


def test_silero_adapter_calls_faster_whisper(monkeypatch):
    import numpy as np
    from cutora.asr.vad_silero import speech_segments_from_audio

    called = []

    def fake_get(audio, vad_options=None, sampling_rate=16000, **kwargs):
        called.append(int(sampling_rate))
        return [{"start": 0, "end": int(sampling_rate)}]

    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", fake_get)
    segs = speech_segments_from_audio({
        "waveform": np.zeros(16000, dtype=np.float32),
        "sample_rate": 16000,
    })
    assert called == [16000]
    assert len(segs) == 1
    assert segs[0].start == 0
    assert segs[0].end == 1.0


def test_vad_factory_is_whisperx_vad_subclass():
    import subprocess
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    py = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
    code = (
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime(render_child=False)\n"
        "from whisperx.vads.vad import Vad\n"
        "from cutora.asr.vad_silero import make_faster_whisper_vad\n"
        "vad = make_faster_whisper_vad()\n"
        "assert isinstance(vad, Vad)\n"
        "assert vad.backend == 'faster-whisper-silero'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [py, "-c", code], cwd=REPO, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "ok" in (proc.stdout or "")


def test_runtime_model_table_has_no_gated_or_other_defaults():
    from cutora.asr.engine import runtime_model_table
    from cutora.asr.vad_silero import speech_segments_from_audio
    assert callable(speech_segments_from_audio)
    rows = runtime_model_table()
    assert rows
    loaded = [r for r in rows if r.get("loaded_by_default")]
    assert loaded
    for row in loaded:
        lic = str(row.get("license") or "")
        assert lic.lower() not in ("other", "")
        assert not row.get("gated")
        repo = (row.get("repo") or "") + (row.get("source") or "")
        assert "pyannote" not in repo.lower()
    py = [r for r in rows if "pyannote" in (r.get("repo") or "").lower()]
    assert py and all(not r.get("loaded_by_default") for r in py)
