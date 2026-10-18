# -*- coding: utf-8 -*-
"""BUG-27: long wav is VAD-chunked before whisperx.transcribe."""
from __future__ import annotations

import numpy as np

from cutora.asr.long_audio import WHISPER_CHUNK_S, transcribe_long_audio


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, batch_size=4, language="en"):
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


def test_long_wav_is_chunked_not_one_mel():
    sr = 16000
    wav = np.zeros(int(129 * sr), dtype=np.float32)
    model = _FakeModel()
    out = transcribe_long_audio(model, wav, "en")
    assert model.calls, "transcribe never called"
    max_s = max(n / sr for n in model.calls)
    assert max_s <= WHISPER_CHUNK_S + 0.05, max_s
    words = [w for s in out.get("segments") or [] for w in (s.get("words") or [])]
    assert len(words) > 300
