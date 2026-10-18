# -*- coding: utf-8 -*-
"""BUG-31: transcribe_long_audio is wired into transcribe_clip (not just defined)."""
from __future__ import annotations

import os

import numpy as np

from cutora.asr.long_audio import WHISPER_CHUNK_S, transcribe_long_audio
from cutora.batch.script_stitcher import transcribe_clip

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, batch_size=4, language="en", **kwargs):
        n = int(np.asarray(audio).reshape(-1).size)
        self.calls.append(n)
        dur = n / 16000.0
        n_words = max(1, int(dur * 3))
        words = [{"word": "w%d" % i, "start": i * 0.3, "end": i * 0.3 + 0.2}
                 for i in range(n_words)]
        return {"segments": [{
            "start": 0.0, "end": dur, "text": " ".join(w["word"] for w in words),
            "words": words,
        }]}


def test_bug27_long_audio_wired(monkeypatch):
    src = open(
        os.path.join(REPO, "cutora", "batch", "script_stitcher.py"),
        encoding="utf-8",
    ).read()
    fn = src.split("def transcribe_clip", 1)[1].split("\ndef ", 1)[0]
    assert "transcribe_long_audio" in fn
    assert "model.transcribe(audio" not in fn

    sr = 16000
    wav = np.zeros(int(129 * sr), dtype=np.float32)
    model = _FakeModel()

    import cutora.batch.script_stitcher as ss

    monkeypatch.setattr(
        ss.whisperx, "align",
        lambda segs, *a, **k: {"segments": segs})
    monkeypatch.setattr(
        ss, "fill_incomplete_transcript",
        lambda model, audio, words, dur, language, log=None: (words, False))
    monkeypatch.setattr(
        ss, "stamp_word_timings",
        lambda model, align_model, metadata, audio, words, dur, language, device, log=None: (words, 1))

    transcribe_clip(model, None, None, wav, 129.0, "en", "cpu")
    assert len(model.calls) > 1, model.calls
    assert max(model.calls) <= int(WHISPER_CHUNK_S * sr) + sr


def test_transcribe_long_audio_chunks_129s():
    sr = 16000
    wav = np.zeros(int(129 * sr), dtype=np.float32)
    model = _FakeModel()
    transcribe_long_audio(model, wav, "en")
    assert len(model.calls) > 1
    assert max(model.calls) <= int(WHISPER_CHUNK_S * sr) + sr
