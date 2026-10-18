# -*- coding: utf-8 -*-
"""Q107 step 0: can the local MMS aligner take hy / vi / ja text as-is?

Loads MahmoudAshraf/mms-300m-1130-forced-aligner from runtime/models/align_mms
via whisperx.load_align_model, then aligns one espeak-ng utterance per language.
Prints the raw outcome. Does not install anything and does not change product code.
"""
from __future__ import annotations

import os
import sys
import traceback
import wave

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALIGN_DIR = os.path.join(REPO, "runtime", "models", "align_mms")
ESPEAK = os.path.join(REPO, "build", "espeak-ng", "eSpeak NG", "espeak-ng.exe")
ESPEAK_DATA = os.path.join(REPO, "build", "espeak-ng", "eSpeak NG", "espeak-ng-data")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
OUT_DIR = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or ".", "q107_mms_probe")

# Short real sentences. hy / ja are non-Latin; vi is Latin plus diacritics.
SAMPLES = (
    ("hy", "hy", "Բարև ձեզ, ինչպես եք այսօր։"),
    ("vi", "vi", "Xin chào, hôm nay thời tiết rất đẹp."),
    ("ja", "ja", "こんにちは、今日はいい天気です。"),
)


def _say(text=""):
    print(text, flush=True)


def _dict_coverage(text, dictionary, language):
    """Mirror whisperx.align's character lookup. '*' means not in the vocab."""
    no_space = language in ("ja", "zh")
    cleaned = []
    hit = 0
    considered = 0
    missing = []
    for ch in text:
        if ch.isspace():
            cleaned.append("|" if not no_space else ch)
            continue
        considered += 1
        key = ch.lower()
        if key in dictionary:
            hit += 1
            cleaned.append(key)
        else:
            missing.append(ch)
            cleaned.append("*")
    return hit, considered, "".join(cleaned), missing


def _speak(voice, text, wav_path):
    import subprocess
    env = dict(os.environ)
    env["ESPEAK_DATA_PATH"] = ESPEAK_DATA
    # Windows argv is not a safe place for Armenian / Japanese. espeak -f
    # reads a UTF-8 file (it accepts a BOM).
    txt_path = wav_path + ".txt"
    with open(txt_path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)
    proc = subprocess.run(
        [ESPEAK, "-v", voice, "-w", wav_path, "-f", txt_path],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
        cwd=os.path.dirname(ESPEAK),
    )
    _say("espeak rc=%s stderr=%r" % (proc.returncode, (proc.stderr or "")[:400]))
    if proc.returncode != 0 or not os.path.isfile(wav_path):
        raise RuntimeError("espeak failed for %s" % voice)
    with wave.open(wav_path, "rb") as w:
        dur = w.getnframes() / float(w.getframerate() or 1)
        _say("wav frames=%s rate=%s dur=%.3f" % (w.getnframes(), w.getframerate(), dur))
    return dur


def _check_words(words, duration):
    rows = []
    ok_mono = True
    ok_bounds = True
    prev = -1.0
    for w in words or []:
        start = w.get("start")
        end = w.get("end")
        rows.append((w.get("word"), start, end, w.get("score")))
        if start is None or end is None:
            ok_mono = False
            ok_bounds = False
            continue
        start = float(start)
        end = float(end)
        if start < prev - 1e-3 or end + 1e-3 < start:
            ok_mono = False
        if start < -0.05 or end > duration + 0.05:
            ok_bounds = False
        prev = max(prev, start)
    return rows, ok_mono, ok_bounds


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    os.makedirs(OUT_DIR, exist_ok=True)

    _say("align_dir=%s exists=%s" % (ALIGN_DIR, os.path.isdir(ALIGN_DIR)))
    _say("files=%s" % sorted(os.listdir(ALIGN_DIR)))
    import json
    with open(os.path.join(ALIGN_DIR, "vocab.json"), encoding="utf-8") as fh:
        vocab = json.load(fh)
    _say("vocab_size=%s" % len(vocab))
    _say("vocab_keys=%s" % sorted(vocab, key=lambda k: vocab[k]))

    import torch
    import whisperx
    _say("whisperx_file=%s" % getattr(whisperx, "__file__", "?"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    _say("device=%s" % device)

    loaded_any = False
    align_usable = {}
    load_errors = {}

    for code, voice, text in SAMPLES:
        _say("")
        _say("======== LOAD %s ========" % code)
        _say("text=%s" % text)
        model = None
        metadata = None
        try:
            model, metadata = whisperx.load_align_model(
                language_code=code,
                model_name=ALIGN_DIR,
                model_dir=ALIGN_DIR,
                device=device,
            )
            loaded_any = True
            dictionary = metadata.get("dictionary") or {}
            _say("LOAD_OK type=%s language=%s dict_size=%s" % (
                metadata.get("type"), metadata.get("language"), len(dictionary)))
            hit, considered, cleaned, missing = _dict_coverage(text, dictionary, code)
            _say("dict_hit=%s/%s" % (hit, considered))
            _say("cleaned=%s" % cleaned)
            _say("missing=%s" % "".join(missing))
        except Exception:
            load_errors[code] = traceback.format_exc()
            _say("LOAD_FAIL")
            _say(load_errors[code])
            continue

        _say("======== ALIGN %s ========" % code)
        wav = os.path.join(OUT_DIR, "%s.wav" % code)
        try:
            _speak(voice, text, wav)
            audio = whisperx.load_audio(wav)
            duration = float(len(audio)) / 16000.0
            _say("audio_16k_samples=%s duration=%.3f" % (len(audio), duration))
            segments = [{"text": text, "start": 0.0, "end": duration}]
            result = whisperx.align(
                segments, model, metadata, audio, device,
                return_char_alignments=False,
            )
            words = list(result.get("word_segments") or [])
            _say("n_segments=%s n_words=%s" % (
                len(result.get("segments") or []), len(words)))
            rows, mono, bounds = _check_words(words, duration)
            for word, start, end, score in rows:
                _say("  word=%r start=%s end=%s score=%s" % (word, start, end, score))
            _say("monotonic=%s within_duration=%s" % (mono, bounds))
            hit, considered, _cleaned, _missing = _dict_coverage(
                text, metadata.get("dictionary") or {}, code)
            # A real alignment needs the script's letters in the vocab.
            # Wildcard-only timestamps (every letter replaced) are not usable.
            usable = bool(words) and mono and bounds and considered > 0 and hit == considered
            align_usable[code] = usable
            _say("letters_all_in_vocab=%s usable_without_romanization=%s" % (
                hit == considered and considered > 0, usable))
        except Exception:
            align_usable[code] = False
            _say("ALIGN_FAIL")
            _say(traceback.format_exc())
        finally:
            del model
            del metadata
            import gc
            gc.collect()
            if device == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

    _say("")
    _say("======== CONCLUSION ========")
    _say("load_errors=%s" % sorted(load_errors))
    _say("align_usable=%s" % align_usable)
    if load_errors and not loaded_any:
        _say("VERDICT=C")
    elif any(not align_usable.get(code, False) for code, _v, _t in SAMPLES):
        # Non-latin (and diacritics) are not in this roman vocab.
        _say("VERDICT=B")
    else:
        _say("VERDICT=A")


if __name__ == "__main__":
    main()
