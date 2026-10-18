# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q38 measurements: turbo hallucination counts + Italian align vs MMS.

Writes JSON under docs/plan/reports/. Does not change the product default.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "plan", "reports")
SR = 16000
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
TURBO = os.path.join(REPO, "runtime", "models", "asr_large-v3-turbo")
ALIGN_IT_DIR = os.path.join(REPO, "runtime", "models", "align_wav2vec2")
ALIGN_MMS = os.path.join(REPO, "runtime", "models", "align_mms")
ALIGN_XLSR = os.path.join(REPO, "runtime", "models", "align_it")
SPEECH_WAV = os.path.join(REPO, "tests", "golden", "_media", "g04_stitch", "_01_0.wav")
IT_MP4 = os.path.join(REPO, "tests", "data", "multilang", "it.mp4")
IT_WAV = os.path.join(REPO, "tests", "data", "multilang", "it.wav")
G03_MP4 = os.path.join(REPO, "tests", "golden", "_media", "g03", "video.mp4")
G03_PROJ = os.path.join(REPO, "tests", "golden", "g03_horizontal_it", "project.asproj.json")
T0035_WAV = os.path.join(OUT, "model_compare_work", "t0035.wav")
WORK = os.path.join(OUT, "Q38_work")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _dir_bytes(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _file_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def load_wav(path: str) -> np.ndarray:
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
        raise RuntimeError("wav width %s" % sw)
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    if int(rate) != SR:
        n = int(round(len(x) * SR / float(rate)))
        x = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(x)), x).astype(np.float32)
    return np.asarray(x, dtype=np.float32).reshape(-1)


def extract_wav(src: str, dest: str) -> str:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
        return dest
    import subprocess
    cmd = [FFMPEG if os.path.isfile(FFMPEG) else "ffmpeg",
           "-y", "-i", src, "-ac", "1", "-ar", str(SR), dest]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError("ffmpeg: %s" % (r.stderr[-400:] if r.stderr else ""))
    return dest


def save_wav(path: str, wav: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pcm = np.clip(wav, -1.0, 1.0)
    data = (pcm * 32767.0).astype(np.int16).tobytes()
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(data)


def synth_music(seconds: float) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n, dtype=np.float32) / float(SR)
    # C-major arpeggio + slow tremolo, no speech formants.
    freqs = (261.63, 329.63, 392.00, 523.25)
    y = np.zeros(n, dtype=np.float32)
    for i, f in enumerate(freqs):
        y += 0.18 * np.sin(2 * np.pi * f * t + 0.2 * i)
    y *= (0.55 + 0.45 * np.sin(2 * np.pi * 1.5 * t)).astype(np.float32)
    return y.astype(np.float32)


def build_probe() -> tuple[np.ndarray, dict]:
    speech = load_wav(SPEECH_WAV)
    head_n = min(len(speech), int(1.5 * SR))
    tail_n = min(len(speech), int(1.5 * SR))
    head = speech[:head_n]
    tail = speech[-tail_n:]
    gap = np.zeros(int(4.0 * SR), dtype=np.float32)
    music = synth_music(6.0)
    wav = np.concatenate([head, gap, music, tail])
    marks = {
        "speech_a": [0.0, head_n / float(SR)],
        "silence": [head_n / float(SR), (head_n + gap.size) / float(SR)],
        "music": [
            (head_n + gap.size) / float(SR),
            (head_n + gap.size + music.size) / float(SR),
        ],
        "speech_b": [
            (head_n + gap.size + music.size) / float(SR),
            len(wav) / float(SR),
        ],
        "duration_s": round(len(wav) / float(SR), 3),
    }
    return wav, marks


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w]


def repeat_ratio(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(max(len(sa), len(sb)))


def vad_windows(wav: np.ndarray) -> list[tuple[float, float]]:
    from cutora.asr.vad_silero import speech_segments_from_audio
    segs = speech_segments_from_audio({"waveform": wav, "sample_rate": SR})
    return [(float(s.start), float(s.end)) for s in segs]


def silence_gaps(windows: list[tuple[float, float]], dur: float, min_s: float = 3.0):
    gaps = []
    t = 0.0
    for a, b in windows:
        if a - t > min_s:
            gaps.append((t, a))
        t = max(t, b)
    if dur - t > min_s:
        gaps.append((t, dur))
    return gaps


def overlap_ratio(start: float, end: float, windows: list[tuple[float, float]]) -> float:
    dur = max(0.0, end - start)
    if dur <= 1e-6:
        return 0.0
    ov = 0.0
    for a, b in windows:
        ov += max(0.0, min(end, b) - max(start, a))
    return ov / dur


def count_hallucinations(segments, speech_win, known_silence=None):
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    n_repeat = 0
    pairs = []
    for i in range(1, len(segs)):
        r = repeat_ratio(str(segs[i - 1].get("text") or ""), str(segs[i].get("text") or ""))
        if r > 0.8:
            n_repeat += 1
            pairs.append({
                "i": i,
                "ratio": round(r, 3),
                "a": (segs[i - 1].get("text") or "")[:80],
                "b": (segs[i].get("text") or "")[:80],
            })
    in_vad_silence = []
    for s in segs:
        st = float(s.get("start") or 0.0)
        en = float(s.get("end") or 0.0)
        if overlap_ratio(st, en, speech_win) < 0.25:
            in_vad_silence.append({
                "start": round(st, 3), "end": round(en, 3),
                "text": (s.get("text") or "")[:80],
            })
    in_known = []
    if known_silence:
        lo, hi = known_silence
        for s in segs:
            st = float(s.get("start") or 0.0)
            en = float(s.get("end") or 0.0)
            if st >= lo - 1e-3 and en <= hi + 1e-3:
                in_known.append({
                    "start": round(st, 3), "end": round(en, 3),
                    "text": (s.get("text") or "")[:80],
                })
    return {
        "n_segments": len(segs),
        "n_repeat_adj": n_repeat,
        "repeat_pairs": pairs,
        "n_in_vad_silence": len(in_vad_silence),
        "in_vad_silence": in_vad_silence,
        "n_in_known_silence": len(in_known),
        "in_known_silence": in_known,
        "texts": [(s.get("text") or "")[:120] for s in segs],
    }


def drop_silence(segments, speech_win):
    from cutora.asr.long_audio import drop_silence_segments
    return drop_silence_segments(segments, speech_win)


def transcribe_fw(model, wav, **opts):
    segs, info = model.transcribe(
        wav, language="en", word_timestamps=False, vad_filter=False, **opts)
    out = []
    for s in segs:
        out.append({
            "start": float(getattr(s, "start", 0.0) or 0.0),
            "end": float(getattr(s, "end", 0.0) or 0.0),
            "text": getattr(s, "text", "") or "",
        })
    lang = getattr(info, "language", None)
    return out, lang


def measure_turbo():
    os.makedirs(WORK, exist_ok=True)
    from faster_whisper import WhisperModel
    import torch

    device, compute = ("cuda", "float16") if torch.cuda.is_available() else ("cpu", "int8")
    _log("load turbo %s %s" % (device, compute))
    t0 = time.time()
    model = WhisperModel(TURBO, device=device, compute_type=compute)
    load_s = time.time() - t0

    probe, marks = build_probe()
    probe_path = os.path.join(WORK, "probe_silence_music.wav")
    save_wav(probe_path, probe)
    speech_win = vad_windows(probe)
    known_sil = tuple(marks["silence"])
    gaps = silence_gaps(speech_win, marks["duration_s"], 3.0)

    param_sets = [
        ("A_baseline_condition_true",
         dict(condition_on_previous_text=True,
              compression_ratio_threshold=2.4, no_speech_threshold=0.6),
         False),
        ("B_condition_false",
         dict(condition_on_previous_text=False,
              compression_ratio_threshold=2.4, no_speech_threshold=0.6),
         False),
        ("C_condition_false_plus_vad_drop",
         dict(condition_on_previous_text=False,
              compression_ratio_threshold=2.4, no_speech_threshold=0.6),
         True),
        ("D_compression_2.2",
         dict(condition_on_previous_text=False,
              compression_ratio_threshold=2.2, no_speech_threshold=0.6),
         True),
        ("E_no_speech_0.5",
         dict(condition_on_previous_text=False,
              compression_ratio_threshold=2.4, no_speech_threshold=0.5),
         True),
    ]

    rows = []
    for name, opts, do_drop in param_sets:
        _log("transcribe probe %s" % name)
        t1 = time.time()
        segs, lang = transcribe_fw(model, probe, **opts)
        infer_s = time.time() - t1
        if do_drop:
            segs = drop_silence(segs, speech_win)
        stats = count_hallucinations(segs, speech_win, known_sil)
        rows.append({
            "id": name,
            "opts": opts,
            "vad_drop": do_drop,
            "infer_s": round(infer_s, 3),
            "language": lang,
            **stats,
        })

    sermon = None
    if os.path.isfile(T0035_WAV):
        raw = load_wav(T0035_WAV)
        clip = raw[: int(45 * SR)]
        win = vad_windows(clip)
        _log("transcribe t0035 45s C")
        t1 = time.time()
        segs, lang = transcribe_fw(
            model, clip,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
            no_speech_threshold=0.6,
        )
        infer_s = time.time() - t1
        before = count_hallucinations(segs, win)
        after_segs = drop_silence(segs, win)
        after = count_hallucinations(after_segs, win)
        sermon = {
            "audio": "t0035.wav[:45s]",
            "duration_s": round(len(clip) / float(SR), 3),
            "n_vad_windows": len(win),
            "n_silence_gaps_gt3s": len(silence_gaps(win, len(clip) / float(SR), 3.0)),
            "infer_s": round(infer_s, 3),
            "before_vad_drop": before,
            "after_vad_drop": after,
            "opts": {
                "condition_on_previous_text": False,
                "compression_ratio_threshold": 2.4,
                "no_speech_threshold": 0.6,
            },
        }

    payload = {
        "device": device,
        "compute": compute,
        "load_s": round(load_s, 3),
        "probe": marks,
        "probe_vad_windows": [(round(a, 3), round(b, 3)) for a, b in speech_win],
        "probe_silence_gaps_gt3s": [(round(a, 3), round(b, 3)) for a, b in gaps],
        "steps": rows,
        "sermon_45s": sermon,
        "product": {
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "no_speech_threshold": 0.6,
            "vad_drop": True,
            "model_unchanged": "asr-large-v3-turbo",
        },
    }
    path = os.path.join(OUT, "Q38_turbo_measure.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _log("wrote %s" % path)
    return payload


def envelope_onset(wav: np.ndarray, t_guess: float, window=0.35) -> tuple[float | None, float | None]:
    """First RMS rise near t_guess. Returns (onset, abs_error)."""
    lo = max(0, int((t_guess - window) * SR))
    hi = min(len(wav), int((t_guess + window) * SR))
    if hi - lo < int(0.04 * SR):
        return None, None
    sl = wav[lo:hi]
    frame = int(0.01 * SR)
    hop = int(0.005 * SR)
    rms = []
    times = []
    for i in range(0, len(sl) - frame, hop):
        rms.append(float(np.sqrt(np.mean(sl[i:i + frame] ** 2))))
        times.append((lo + i) / float(SR))
    if not rms:
        return None, None
    arr = np.asarray(rms, dtype=np.float32)
    peak = float(np.percentile(arr, 85))
    floor = float(np.percentile(arr, 20))
    thresh = floor + 0.35 * (peak - floor)
    for t, e in zip(times, arr):
        if e >= thresh:
            return float(t), abs(float(t) - t_guess)
    return None, None


def flatten_words(segments):
    words = []
    for seg in segments or []:
        for w in seg.get("words") or []:
            tok = str(w.get("word") or "").strip()
            if not tok:
                continue
            st, en = w.get("start"), w.get("end")
            if not isinstance(st, (int, float)) or not isinstance(en, (int, float)):
                continue
            words.append({"word": tok, "start": float(st), "end": float(en)})
    return words


def score_words(words, wav):
    errs = []
    missing = 0
    for w in words:
        onset, err = envelope_onset(wav, w["start"])
        if err is None:
            missing += 1
            continue
        errs.append(err)
    if not errs:
        return {
            "n_words": len(words),
            "n_scored": 0,
            "n_missing_onset": missing,
            "mae_s": None,
            "p50_s": None,
            "p90_s": None,
        }
    arr = np.asarray(errs, dtype=np.float64)
    return {
        "n_words": len(words),
        "n_scored": int(arr.size),
        "n_missing_onset": missing,
        "mae_s": round(float(arr.mean()), 4),
        "p50_s": round(float(np.percentile(arr, 50)), 4),
        "p90_s": round(float(np.percentile(arr, 90)), 4),
    }


def _it_text_from_json(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if "it" in data:
        return " ".join(data["it"])
    tasks = data.get("tasks") or []
    if tasks:
        return str(tasks[0].get("text") or "")
    return ""


def align_one(name, model, meta, audio, text, device):
    import whisperx
    segs = [{"start": 0.0, "end": float(len(audio)) / float(SR), "text": text}]
    t0 = time.time()
    aligned = whisperx.align(
        segs, model, meta, audio, device, return_char_alignments=False)
    wall = time.time() - t0
    words = flatten_words(aligned.get("segments") or [])
    return {
        "id": name,
        "wall_s": round(wall, 3),
        "n_words": len(words),
        "words": words,
        **score_words(words, audio),
    }


def load_mms(device: str):
    """Try the product-shaped whisperx loader, then a measurement-only fallback."""
    import whisperx
    t0 = time.time()
    try:
        model, meta = whisperx.load_align_model(
            language_code="it", device=device,
            model_name=ALIGN_MMS, model_dir=ALIGN_MMS)
        return model, meta, time.time() - t0, None
    except Exception as exc:
        first = str(exc)[:400]
        _log("mms whisperx load failed: %s" % first)
    try:
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        proc = Wav2Vec2Processor.from_pretrained(ALIGN_MMS)
        try:
            model = Wav2Vec2ForCTC.from_pretrained(ALIGN_MMS)
        except Exception as exc2:
            _log("mms strict load failed: %s" % str(exc2)[:300])
            model = Wav2Vec2ForCTC.from_pretrained(
                ALIGN_MMS, ignore_mismatched_sizes=True)
        model = model.to(device)
        labels = proc.tokenizer.get_vocab()
        align_dictionary = {char.lower(): code for char, code in labels.items()}
        meta = {"language": "it", "dictionary": align_dictionary, "type": "huggingface"}
        return model, meta, time.time() - t0, first
    except Exception as exc:
        return None, None, time.time() - t0, "%s | fallback: %s" % (first, str(exc)[:400])


def measure_italian():
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    import torch
    import whisperx
    from cutora.asr.engine import get_align_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    os.makedirs(WORK, exist_ok=True)

    clips = []
    if os.path.isfile(IT_WAV) or os.path.isfile(IT_MP4):
        it_wav = IT_WAV if os.path.isfile(IT_WAV) else extract_wav(
            IT_MP4, os.path.join(WORK, "it.wav"))
        it_text = _it_text_from_json(
            os.path.join(REPO, "tests", "data", "multilang", "sentences.json"))
        clips.append(("it.mp4", load_wav(it_wav) if it_wav.endswith(".wav") and os.path.isfile(IT_WAV)
                      else load_wav(it_wav), it_text))
    if os.path.isfile(G03_MP4):
        g03_wav = extract_wav(G03_MP4, os.path.join(WORK, "g03.wav"))
        g03_text = _it_text_from_json(G03_PROJ) if os.path.isfile(G03_PROJ) else ""
        clips.append(("g03_horizontal_it", load_wav(g03_wav), g03_text))

    _log("load current it align (VoxPopuli)")
    t0 = time.time()
    cur_model, cur_meta = get_align_model("it")
    cur_load = time.time() - t0
    _log("load mms")
    mms_model, mms_meta, mms_load, mms_err = load_mms(device)

    xlsr_model = xlsr_meta = None
    xlsr_load = None
    xlsr_err = None
    if os.path.isdir(ALIGN_XLSR):
        _log("load xlsr (extra, not product)")
        t1 = time.time()
        try:
            xlsr_model, xlsr_meta = whisperx.load_align_model(
                language_code="it", device=device,
                model_name=ALIGN_XLSR, model_dir=ALIGN_XLSR)
            xlsr_load = time.time() - t1
        except Exception as exc:
            xlsr_err = str(exc)[:400]
            xlsr_load = time.time() - t1

    results = []
    for clip_id, audio, text in clips:
        _log("align %s current" % clip_id)
        row = {
            "clip": clip_id,
            "duration_s": round(len(audio) / float(SR), 3),
            "n_ref_tokens": len(tokens(text)),
            "text": text,
            "current": align_one("align-it-voxpopuli", cur_model, cur_meta, audio, text, device),
        }
        row["current"]["load_s"] = round(cur_load, 3)
        row["current"]["license"] = "CC-BY-NC-4.0"
        row["current"]["bytes"] = _file_bytes(
            os.path.join(ALIGN_IT_DIR, "wav2vec2_voxpopuli_base_10k_asr_it.pt"))
        row["current"]["path"] = "runtime/models/align_wav2vec2 (torchaudio VOXPOPULI_ASR_BASE_10K_IT)"
        if mms_model is not None:
            _log("align %s mms" % clip_id)
            try:
                hit = align_one("mms-300m-forced-aligner", mms_model, mms_meta, audio, text, device)
                hit["load_s"] = round(mms_load, 3)
                hit["license"] = "CC-BY-NC-4.0"
                hit["bytes"] = _dir_bytes(ALIGN_MMS)
                hit["path"] = "runtime/models/align_mms (MahmoudAshraf/mms-300m-1130-forced-aligner)"
                if mms_err:
                    hit["load_warning"] = mms_err
                row["mms"] = hit
            except Exception as exc:
                row["mms"] = {"id": "mms-300m-forced-aligner", "error": str(exc)[:400]}
        else:
            row["mms"] = {
                "id": "mms-300m-forced-aligner",
                "error": mms_err or "load failed",
                "load_s": round(mms_load, 3),
                "bytes": _dir_bytes(ALIGN_MMS),
                "license": "CC-BY-NC-4.0",
            }
        if xlsr_model is not None:
            _log("align %s xlsr" % clip_id)
            try:
                hit = align_one("align-it-xlsr", xlsr_model, xlsr_meta, audio, text, device)
                hit["load_s"] = round(xlsr_load or 0.0, 3)
                hit["license"] = "Apache-2.0"
                hit["bytes"] = _dir_bytes(ALIGN_XLSR)
                hit["path"] = "runtime/models/align_it (jonatasgrosman/wav2vec2-large-xlsr-53-italian)"
                row["xlsr"] = hit
            except Exception as exc:
                row["xlsr"] = {"id": "align-it-xlsr", "error": str(exc)[:400]}
        elif xlsr_err:
            row["xlsr"] = {"id": "align-it-xlsr", "error": xlsr_err}
        results.append(row)

    payload = {
        "current_path": {
            "engine": 'ALIGN_MODEL_BY_LANG["it"] = None → whisperX default VOXPOPULI_ASR_BASE_10K_IT',
            "loader": "torchaudio pipeline, model_dir=runtime/models/align_wav2vec2",
            "not": "not MMS, not XLSR, not English LV60K",
            "license": "CC-BY-NC-4.0",
        },
        "device": device,
        "clips": results,
        "sizes_bytes": {
            "align-it-voxpopuli_pt": _file_bytes(
                os.path.join(ALIGN_IT_DIR, "wav2vec2_voxpopuli_base_10k_asr_it.pt")),
            "align_mms_dir": _dir_bytes(ALIGN_MMS),
            "align_it_xlsr_dir": _dir_bytes(ALIGN_XLSR),
        },
        "product_default_unchanged": True,
    }
    path = os.path.join(OUT, "Q38_italian_measure.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _log("wrote %s" % path)
    return payload


def main():
    sys.path.insert(0, REPO)
    os.chdir(REPO)
    what = sys.argv[1:] or ["turbo", "italian"]
    if "turbo" in what:
        measure_turbo()
    if "italian" in what:
        measure_italian()


if __name__ == "__main__":
    main()
