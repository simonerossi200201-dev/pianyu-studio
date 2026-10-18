# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""whisperX VAD adapter around faster-whisper's bundled Silero (MIT)."""
from __future__ import annotations

import numpy as np

from cutora.core.log import get_logger

logger = get_logger("cutora.asr.vad_silero")


class SpeechSeg:
    """Minimal segment: start/end in seconds. Avoids importing whisperx.diarize."""

    def __init__(self, start, end, speaker="UNKNOWN"):
        self.start = start
        self.end = end
        self.speaker = speaker


def speech_segments_from_audio(audio, vad_onset: float = 0.5):
    """Run faster-whisper's bundled Silero VAD. ``audio`` is a waveform dict."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    waveform = audio["waveform"]
    sample_rate = int(audio.get("sample_rate") or 16000)
    if hasattr(waveform, "detach"):
        waveform = waveform.detach().cpu().numpy()
    waveform = np.asarray(waveform, dtype=np.float32)
    if waveform.ndim > 1:
        waveform = np.reshape(waveform, -1)
    opts = VadOptions(threshold=float(vad_onset))
    chunks = get_speech_timestamps(
        waveform, vad_options=opts, sampling_rate=sample_rate)
    return [
        SpeechSeg(c["start"] / sample_rate, c["end"] / sample_rate, "UNKNOWN")
        for c in chunks
    ]


def make_faster_whisper_vad(vad_onset: float = 0.5):
    """Build a whisperX-compatible Vad that uses faster-whisper Silero.

    whisperX ``Vad`` is subclassed here (not at import) so importing this
    module does not pull pyannote.audio / onnxruntime.
    """
    from whisperx.vads.vad import Vad

    class FasterWhisperSileroVad(Vad):
        backend = "faster-whisper-silero"
        license = "MIT"

        def __init__(self):
            super().__init__(vad_onset)
            self.vad_onset = float(vad_onset)
            logger.info("VAD backend=faster-whisper-silero license=MIT")

        def __call__(self, audio, **kwargs):
            return speech_segments_from_audio(audio, self.vad_onset)

        @staticmethod
        def preprocess_audio(audio):
            return audio

    return FasterWhisperSileroVad()
