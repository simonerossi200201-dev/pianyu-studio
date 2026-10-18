# -*- coding: utf-8 -*-
"""Q38: turbo anti-hallucination. Product path, not source grep.

test_q38_turbo_params intercepts whisperx.load_model on get_whisperx_model
(the turbo load path) and requires condition_on_previous_text=False in the
asr_options that actually get passed.

test_q38_no_hallucination_in_silence feeds transcribe_long_audio a clip with
a >3 s silence gap and a fake decoder that plants a sentence in that gap.
The product must drop it. Speech-region sentences must remain — dropping
everything would make the silence assertion pass for the wrong reason.
"""
from __future__ import annotations

import os
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEECH_WAV = os.path.join(REPO, "tests", "golden", "_media", "g04_stitch", "_01_0.wav")
TURBO_DIR = os.path.join(REPO, "runtime", "models", "asr_large-v3-turbo")
SR = 16000


def _load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as fh:
        nch = fh.getnchannels()
        sw = fh.getsampwidth()
        rate = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise AssertionError("unsupported wav width %s" % sw)
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    if int(rate) != SR:
        n = int(round(len(x) * SR / float(rate)))
        x = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(x)), x).astype(np.float32)
    return np.asarray(x, dtype=np.float32).reshape(-1)


def _speech_silence_speech(silence_s: float = 4.0) -> tuple[np.ndarray, float, float]:
    """Real speech | >3 s zeros | real speech. Returns wav, silence_lo, silence_hi."""
    assert os.path.isfile(SPEECH_WAV), SPEECH_WAV
    speech = _load_wav(SPEECH_WAV)
    head_n = min(len(speech), int(1.2 * SR))
    tail_n = min(len(speech), int(1.2 * SR))
    head = speech[:head_n]
    tail = speech[-tail_n:] if tail_n else speech[:head_n]
    gap = np.zeros(int(silence_s * SR), dtype=np.float32)
    wav = np.concatenate([head, gap, tail])
    lo = head_n / float(SR)
    hi = (head_n + gap.size) / float(SR)
    assert hi - lo > 3.0
    return wav, lo, hi


class _HallucinatingModel:
    """Decoder that plants one sentence inside the known silence gap."""

    def __init__(self, silence_lo: float, silence_hi: float):
        mid = 0.5 * (silence_lo + silence_hi)
        self.planted = {
            "start": mid - 0.6,
            "end": mid + 0.6,
            "text": "thank you for watching please subscribe",
            "words": [
                {"word": "thank", "start": mid - 0.6, "end": mid - 0.3},
                {"word": "you", "start": mid - 0.3, "end": mid},
                {"word": "for", "start": mid, "end": mid + 0.3},
                {"word": "watching", "start": mid + 0.3, "end": mid + 0.6},
            ],
        }
        self.real_head = {
            "start": 0.05,
            "end": max(0.4, silence_lo - 0.15),
            "text": "hello friends",
            "words": [
                {"word": "hello", "start": 0.05, "end": 0.25},
                {"word": "friends", "start": 0.25, "end": max(0.4, silence_lo - 0.15)},
            ],
        }
        self.real_tail = {
            "start": silence_hi + 0.1,
            "end": silence_hi + 0.9,
            "text": "peace be with you",
            "words": [
                {"word": "peace", "start": silence_hi + 0.1, "end": silence_hi + 0.4},
                {"word": "be", "start": silence_hi + 0.4, "end": silence_hi + 0.55},
                {"word": "with", "start": silence_hi + 0.55, "end": silence_hi + 0.7},
                {"word": "you", "start": silence_hi + 0.7, "end": silence_hi + 0.9},
            ],
        }
        self.calls = []

    def transcribe(self, audio, batch_size=4, language="en"):
        n = int(np.asarray(audio).reshape(-1).size)
        self.calls.append(n)
        return {
            "segments": [
                dict(self.real_head),
                dict(self.planted),
                dict(self.real_tail),
            ]
        }


def _in_interval(seg, lo, hi) -> bool:
    s = float(seg.get("start") or 0.0)
    e = float(seg.get("end") or 0.0)
    return s >= lo - 1e-3 and e <= hi + 1e-3


def test_q38_no_hallucination_in_silence():
    from cutora.asr.long_audio import transcribe_long_audio

    wav, lo, hi = _speech_silence_speech(4.0)
    assert (hi - lo) > 3.0
    model = _HallucinatingModel(lo, hi)
    # The planted sentence sits entirely in the silence gap — that is the
    # defect the product must catch. Passing a clip with no silence, or a
    # decoder that never hallucinates, would not expose the missing filter.
    assert _in_interval(model.planted, lo, hi)
    out = transcribe_long_audio(model, wav, "en")
    segs = list(out.get("segments") or [])
    in_silence = [s for s in segs if _in_interval(s, lo, hi)]
    assert in_silence == [], (
        "silence (%.2f–%.2f s) still has %d subtitle sentence(s): %s"
        % (lo, hi, len(in_silence), [s.get("text") for s in in_silence])
    )
    speech_texts = [str(s.get("text") or "") for s in segs]
    assert any("hello" in t.lower() for t in speech_texts), speech_texts
    assert any("peace" in t.lower() for t in speech_texts), speech_texts
    assert model.calls, "transcribe never called — test did not exercise the product path"


def test_q38_turbo_params(monkeypatch):
    """Turbo load path must pass condition_on_previous_text=False into whisperx."""
    import cutora.asr.engine as eng

    captured = {}

    class _DummyModel:
        pass

    def fake_load_model(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = dict(kwargs)
        captured["asr_options"] = kwargs.get("asr_options")
        return _DummyModel()

    monkeypatch.setattr(eng.whisperx, "load_model", fake_load_model)
    monkeypatch.setattr(eng, "whisperx_vad_model", lambda: object())
    monkeypatch.setenv("CUTORA_ASR_TIER", "T")

    from cutora.asr.tier import HardwareProbe

    def fake_probe():
        return HardwareProbe(
            cuda_count=1,
            vram_total_gb=16.0,
            vram_free_gb=12.0,
            ram_total_gb=32.0,
            ram_free_gb=16.0,
            cpu_count=8,
            models_on_disk=frozenset({"asr-large-v3-turbo", "asr_large-v3-turbo"}),
        )

    monkeypatch.setattr("cutora.asr.tier.probe_hardware", fake_probe)
    monkeypatch.setattr("cutora.core.config.DEVICE", "cuda")
    eng.DEVICE = "cuda"
    eng._WHISPERX_MODEL = None
    eng._WHISPERX_DIR = None
    eng._WHISPERX_DEVICE = None

    assert os.path.isdir(TURBO_DIR), TURBO_DIR
    model = eng.get_whisperx_model(asr_dir=TURBO_DIR, log=lambda _m: None)
    assert model is not None
    assert "kwargs" in captured, "get_whisperx_model never called whisperx.load_model"
    opts = captured.get("asr_options")
    assert isinstance(opts, dict), (
        "turbo load_model was not given asr_options (got %r); "
        "whisperX defaults are not a product guarantee"
        % (opts,)
    )
    assert opts.get("condition_on_previous_text") is False, opts
