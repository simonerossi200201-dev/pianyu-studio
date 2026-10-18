# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chunk long waveforms so whisperx.transcribe never sees >30s of mel."""
from __future__ import annotations

from cutora.core.log import get_logger

logger = get_logger("cutora.asr.long_audio")

WHISPER_CHUNK_S = 28.0
# Drop a whole sentence when less than this fraction of its span overlaps
# Silero speech. 0.25 still keeps a word that slightly overruns a VAD edge.
MIN_SPEECH_OVERLAP = 0.25


def _model_transcribe(model, audio, language, batch_size=4):
    from cutora.asr.decode_opts import asr_decode_options
    opts = asr_decode_options()
    try:
        return model.transcribe(
            audio, batch_size=batch_size, language=language,
            verbose=False, print_progress=False, **opts)
    except TypeError:
        try:
            return model.transcribe(
                audio, batch_size=batch_size, language=language, **opts)
        except TypeError:
            return model.transcribe(
                audio, batch_size=batch_size, language=language)


def _speech_windows(wav, sr):
    """Silero speech intervals in seconds. Empty list if VAD cannot run."""
    try:
        from cutora.asr.vad_silero import speech_segments_from_audio
        vad = speech_segments_from_audio({"waveform": wav, "sample_rate": sr})
        return [(float(s.start), float(s.end)) for s in vad]
    except Exception:
        logger.exception("Silero VAD failed; falling back to fixed windows")
        return []


def _speech_overlap_ratio(start, end, windows):
    dur = max(0.0, float(end) - float(start))
    if dur <= 1e-6:
        return 0.0
    ov = 0.0
    for a, b in windows:
        ov += max(0.0, min(end, b) - max(start, a))
    return ov / dur


def drop_silence_segments(segments, speech_windows, min_overlap=MIN_SPEECH_OVERLAP):
    """Drop whole sentences that sit in VAD-judged silence.

    If VAD produced no windows we cannot judge — keep the segments and log.
    """
    segs = list(segments or [])
    if not speech_windows:
        logger.warning(
            "no VAD speech windows; keeping all %d segments", len(segs))
        return segs
    kept = []
    dropped = 0
    for seg in segs:
        if not isinstance(seg, dict):
            kept.append(seg)
            continue
        s = float(seg.get("start") or 0.0)
        e = float(seg.get("end") or 0.0)
        ratio = _speech_overlap_ratio(s, e, speech_windows)
        if ratio < float(min_overlap):
            dropped += 1
            logger.info(
                "drop silence-region sentence overlap=%.2f start=%.2f end=%.2f text=%s",
                ratio, s, e, (seg.get("text") or "")[:80],
            )
            continue
        kept.append(seg)
    if dropped:
        logger.info("dropped %d silence-region sentence(s)", dropped)
    return kept


def transcribe_long_audio(model, audio, language, batch_size=4):
    """Silero-VAD chunk then whisperx.transcribe. Avoids 30s mel blow-up.

    Always runs VAD (even on clips shorter than WHISPER_CHUNK_S) so
    sentences that land in silence can be dropped after decode.
    """
    import numpy as np
    wav = np.asarray(audio, dtype=np.float32).reshape(-1)
    sr = 16000
    dur = float(len(wav)) / float(sr)
    vad_windows = _speech_windows(wav, sr)
    if dur <= WHISPER_CHUNK_S:
        result = _model_transcribe(model, wav, language, batch_size=batch_size)
        segs = drop_silence_segments(result.get("segments") or [], vad_windows)
        out = dict(result)
        out["segments"] = segs
        return out
    windows = list(vad_windows)
    if not windows:
        t = 0.0
        while t < dur:
            windows.append((t, min(dur, t + WHISPER_CHUNK_S)))
            t += WHISPER_CHUNK_S - 1.0
    chunks = []
    for a, b in windows:
        t = a
        while t < b - 0.05:
            chunks.append((t, min(b, t + WHISPER_CHUNK_S)))
            t += WHISPER_CHUNK_S
    all_segs = []
    for a, b in chunks:
        i0 = max(0, int(a * sr))
        i1 = min(len(wav), int(b * sr))
        piece = wav[i0:i1]
        if piece.size < int(sr * 0.2):
            continue
        result = _model_transcribe(model, piece, language, batch_size=batch_size)
        for seg in result.get("segments") or []:
            rec = dict(seg)
            rec["start"] = float(rec.get("start") or 0.0) + a
            rec["end"] = float(rec.get("end") or 0.0) + a
            words = []
            for w in rec.get("words") or []:
                if not isinstance(w, dict):
                    continue
                ww = dict(w)
                if ww.get("start") is not None:
                    ww["start"] = float(ww["start"]) + a
                if ww.get("end") is not None:
                    ww["end"] = float(ww["end"]) + a
                words.append(ww)
            rec["words"] = words
            all_segs.append(rec)
    return {"segments": drop_silence_segments(all_segs, vad_windows)}
