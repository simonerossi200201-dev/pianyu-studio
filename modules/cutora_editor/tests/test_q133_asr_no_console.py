# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q133 · 自动字幕每条视频闪一下黑框：whisperx.load_audio 用裸 subprocess。"""
from __future__ import annotations

import os
import sys
import types

import numpy as np


def test_load_audio_matches_whisperx_semantics(monkeypatch):
    from cutora.asr import audio_io
    seen = {}
    pcm = np.array([0, 16384, -32768, 32767], dtype=np.int16).tobytes()

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return types.SimpleNamespace(stdout=pcm, returncode=0)

    import cutora.core.proc as proc
    monkeypatch.setattr(proc, "run", fake_run)
    out = audio_io.load_audio("a b.mp4")
    assert out.dtype == np.float32
    assert np.allclose(out, [0.0, 0.5, -1.0, 32767 / 32768.0])
    from cutora.core.config import FFMPEG_PATH
    assert seen["cmd"][0] == FFMPEG_PATH
    assert seen["cmd"][seen["cmd"].index("-f") + 1] == "s16le"
    assert seen["cmd"][seen["cmd"].index("-ar") + 1] == "16000"
    assert seen["kw"].get("check") is True and seen["kw"].get("capture_output") is True


def test_patch_whisperx_replaces_bound_names(monkeypatch):
    from cutora.asr import audio_io
    fakes = {}
    for name in ("whisperx.audio", "whisperx.asr", "whisperx.alignment"):
        m = types.ModuleType(name)
        m.load_audio = lambda *a, **k: "bare-subprocess"
        fakes[name] = m
        monkeypatch.setitem(sys.modules, name, m)
    assert audio_io.patch_whisperx() == 3
    for m in fakes.values():
        assert m.load_audio is audio_io.load_audio
    assert audio_io.patch_whisperx() == 0


def test_asr_paths_do_not_call_whisperx_load_audio():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("cutora/asr/align_worker.py", "cutora/asr/detect.py"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "whisperx.load_audio(" not in src, rel


def test_model_getters_patch_after_load():
    from cutora.asr import engine
    assert engine.get_whisperx_model.__wrapped__.__name__ == "get_whisperx_model"
    assert engine.get_align_model.__wrapped__.__name__ == "get_align_model"
