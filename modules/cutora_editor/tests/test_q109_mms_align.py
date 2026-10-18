# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q109: MMS fallback aligns real audio, and subtitles stay in the original script.

Qt and script_stitcher imports stay inside the tests. This module is listed
in qt_isolate.
"""
from __future__ import annotations

import json
import os
import subprocess
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG = os.path.join(REPO, "runtime", "ffmpeg")
os.environ["PATH"] = _FFMPEG + os.pathsep + os.environ.get("PATH", "")

ESPEAK = os.path.join(REPO, "build", "espeak-ng", "eSpeak NG", "espeak-ng.exe")
ESPEAK_DATA = os.path.join(REPO, "build", "espeak-ng", "eSpeak NG", "espeak-ng-data")

# Same sentences Q107 measured. The last token there swallowed the tail.
SAMPLES = (
    ("hy", "hy", "Բարև ձեզ, ինչպես եք այսօր։"),
    ("vi", "vi", "Xin chào, hôm nay thời tiết rất đẹp."),
    ("ja", "ja", "こんにちは、今日はいい天気です。"),
)


def _speak(voice, text, wav_path):
    env = dict(os.environ)
    env["ESPEAK_DATA_PATH"] = ESPEAK_DATA
    txt = wav_path + ".txt"
    with open(txt, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)
    proc = subprocess.run(
        [ESPEAK, "-v", voice, "-w", wav_path, "-f", txt],
        env=env, capture_output=True, timeout=60,
        cwd=os.path.dirname(ESPEAK),
    )
    assert proc.returncode == 0, proc.stderr
    assert os.path.isfile(wav_path)
    with wave.open(wav_path, "rb") as handle:
        assert handle.getnframes() > 0


def _words_of(result):
    rows = []
    for seg in result.get("segments") or []:
        for word in seg.get("words") or []:
            token = str(word.get("word") or "")
            if not token.strip():
                continue
            rows.append((token, word.get("start"), word.get("end")))
    return rows


def _assert_real_alignment(lang, voice, text, tmp_path):
    import whisperx
    from cutora.asr.engine import DEVICE, align_transcript, get_align_model
    wav = os.path.join(str(tmp_path), "%s.wav" % lang)
    _speak(voice, text, wav)
    audio = whisperx.load_audio(wav)
    duration = float(len(audio)) / 16000.0
    assert duration > 0.5
    model, metadata = get_align_model(lang)
    assert metadata.get("cutora_mms") is True
    result = align_transcript(
        [{"text": text, "start": 0.0, "end": duration}],
        model, metadata, audio, DEVICE, return_char_alignments=False,
    )
    rows = _words_of(result)
    print("Q109 %s n=%s dur=%.3f" % (lang, len(rows), duration))
    assert rows, lang
    prev = -1.0
    max_ratio = 0.0
    for token, start, end in rows:
        print("  %r %s %s" % (token, start, end))
        assert start is not None and end is not None, token
        start = float(start)
        end = float(end)
        assert start < end, (token, start, end)
        assert start + 1e-6 >= prev, (token, start, prev)
        max_ratio = max(max_ratio, (end - start) / duration)
        prev = start
    cover = (float(rows[-1][2]) - float(rows[0][1])) / duration
    print("  max_ratio=%.3f cover=%.3f" % (max_ratio, cover))
    assert max_ratio <= 0.40, (lang, max_ratio)
    assert cover >= 0.70, (lang, cover)
    joined = "".join(token for token, _s, _e in rows)
    assert "hayeren" not in joined.lower()
    return rows


def test_armenian_alignment_does_not_swallow_the_tail(tmp_path):
    lang, voice, text = SAMPLES[0]
    _assert_real_alignment(lang, voice, text, tmp_path)


def test_vietnamese_alignment_does_not_swallow_the_tail(tmp_path):
    lang, voice, text = SAMPLES[1]
    _assert_real_alignment(lang, voice, text, tmp_path)


def test_japanese_alignment_does_not_swallow_the_tail(tmp_path):
    lang, voice, text = SAMPLES[2]
    _assert_real_alignment(lang, voice, text, tmp_path)


def test_english_frozen_timestamps_match_the_historical_call(tmp_path):
    """The en load arguments are the pre-change call, byte for byte.

    Two forwards of this model on the same clip matched before this change.
    A fallback-chain edit that touches the frozen branch makes the blobs differ.
    """
    import whisperx
    from cutora.asr.engine import (
        ALIGN_MODEL_BY_LANG, DEVICE, align_transcript, get_align_model,
    )
    from cutora.core.paths import get_model_paths, models_root
    text = "This is a short English sentence for the frozen aligner."
    wav = os.path.join(str(tmp_path), "en.wav")
    _speak("en", text, wav)
    audio = whisperx.load_audio(wav)
    duration = float(len(audio)) / 16000.0
    segment = [{"text": text, "start": 0.0, "end": duration}]
    paths = get_model_paths()
    align_dir = paths.get("align_path")
    if not align_dir or not os.path.exists(align_dir):
        align_dir = os.path.join(models_root(), "align_wav2vec2")
    historical_model, historical_meta = whisperx.load_align_model(
        language_code="en",
        device=DEVICE,
        model_name=ALIGN_MODEL_BY_LANG.get("en"),
        model_dir=align_dir,
    )
    historical = whisperx.align(
        segment, historical_model, historical_meta, audio, DEVICE,
        return_char_alignments=False,
    )
    product_model, product_meta = get_align_model("en")
    assert not product_meta.get("cutora_mms")
    product = align_transcript(
        segment, product_model, product_meta, audio, DEVICE,
        return_char_alignments=False,
    )
    assert _words_of(historical) == _words_of(product)
    assert repr(_words_of(historical)) == repr(_words_of(product))


def test_ascii_tokens_are_the_same_objects():
    from cutora.asr.romanize import romanize_tokens
    tokens = ["Hello,", "world", "it's"]
    out = romanize_tokens(tokens, "en")
    assert out[0] is tokens[0]
    assert out[1] is tokens[1]
    assert out[2] is tokens[2]


def test_subtitle_text_stays_in_the_original_script(tmp_path, monkeypatch):
    """Fake ASR, real MMS align, then the stitch project the user opens."""
    import whisperx
    from cutora.asr import engine
    from cutora.asr.engine import DEVICE, get_align_model
    text = "Հայերեն բարև"
    wav = os.path.join(str(tmp_path), "clip.wav")
    _speak("hy", text, wav)
    audio = whisperx.load_audio(wav)
    duration = float(len(audio)) / 16000.0
    seen = []
    real_align = engine.whisperx.align

    def _spy(segments, *args, **kwargs):
        seen.append(" ".join(str(seg.get("text") or "") for seg in segments))
        return real_align(segments, *args, **kwargs)

    monkeypatch.setattr(engine.whisperx, "align", _spy)

    def _fake_asr(_model, _audio, _language, batch_size=4):
        return {"segments": [{
            "start": 0.0, "end": duration, "text": text,
        }]}

    monkeypatch.setattr(
        "cutora.asr.long_audio.transcribe_long_audio", _fake_asr)
    from cutora.batch.script_stitcher import (
        build_timeline_result, clip_fingerprint, save_asr_cache, transcribe_clip,
    )
    align_model, metadata = get_align_model("hy")
    words = transcribe_clip(
        object(), align_model, metadata, audio, duration, "hy", DEVICE,
        log=lambda _msg: None,
    )
    assert words
    joined = " ".join(str(item[0]) for item in words)
    assert "hayeren" not in joined.lower()
    assert "այերեն" in joined
    assert any("hayeren" in blob.lower() for blob in seen)

    out = os.path.join(str(tmp_path), "stitch")
    os.makedirs(out, exist_ok=True)
    key = clip_fingerprint(wav, "hy")
    save_asr_cache(out, {
        key: {
            "words": [[item[0], float(item[1]), float(item[2])] for item in words],
            "dur": round(duration, 3),
            "fps": 30.0,
        },
    })
    cuts = {"1": [{
        "path": wav, "keep": True, "name": "clip.wav",
        "in_t": 0.0, "out_t": duration,
        "segs": [[0.0, duration]],
    }]}
    _payload, _n, asproj, _canvas = build_timeline_result(
        cuts, out, {"1": text}, (1080, 1920))
    project = json.loads(open(asproj, encoding="utf-8").read())
    task = project["tasks"][0]
    segment_blob = json.dumps(task["segments"], ensure_ascii=False)
    assert task["text"] == text
    assert "Հայերեն" in task["text"]
    assert "hayeren" not in segment_blob.lower()
    assert "այերեն" in segment_blob


def test_uroman_import_failure_reports_none(monkeypatch):
    import builtins
    from cutora.asr import engine
    engine._ALIGN_CACHE.clear()
    real = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "uroman" or str(name).startswith("uroman."):
            raise ImportError("uroman missing")
        return real(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    from cutora.asr.models import align_tier
    from cutora.asr.stitch_language import StitchLanguageError
    assert align_tier("hy") == "none"
    assert align_tier("ja") == "none"
    assert align_tier("vi") == "none"
    assert align_tier("en") == "exact"
    try:
        engine.get_align_model("hy")
        raised = None
    except StitchLanguageError as exc:
        raised = exc
    assert raised is not None
    message = str(raised)
    assert "亚美尼亚" in message
    assert "拼接" in message


def test_romanize_count_mismatch_does_not_call_the_aligner(monkeypatch):
    from cutora.asr import engine
    from cutora.asr.stitch_language import StitchLanguageError
    called = []

    def _boom(*_args, **_kwargs):
        called.append(1)
        raise AssertionError("aligner must not run")

    monkeypatch.setattr(engine.whisperx, "align", _boom)
    monkeypatch.setattr(
        "cutora.asr.romanize.romanize_tokens",
        lambda tokens, lang: list(tokens) + ["extra"],
    )
    try:
        engine.align_transcript(
            [{"text": "Հայերեն", "start": 0.0, "end": 1.0}],
            object(),
            {"cutora_mms": True, "cutora_source_lang": "hy"},
            None,
            "cpu",
        )
        raised = False
    except StitchLanguageError:
        raised = True
    assert raised
    assert called == []


def test_fallback_is_not_described_as_exact():
    from cutora.asr.languages import (
        align_phrase, capability_summary_line, capability_table_cells, load_catalog,
    )
    from cutora.asr.models import align_tier, language_capability
    counts = {"exact": 0, "fallback": 0, "none": 0}
    for row in load_catalog():
        counts[align_tier(row["code"])] += 1
    assert counts == {"exact": 26, "fallback": 74, "none": 0}
    line = capability_summary_line()
    assert line == "可转写 100 种；26 种逐词精确，74 种可用"
    en_cell = capability_table_cells(language_capability("en"))[3]
    hy_cell = capability_table_cells(language_capability("hy"))[3]
    vi_cell = capability_table_cells(language_capability("vi"))[3]
    assert en_cell == "精确（专用模型）"
    assert hy_cell == "可用（通用模型，精度略低）"
    assert vi_cell == "可用（通用模型，精度略低）"
    assert align_phrase("fallback") == "可用（精度略低）"
    assert "精确" not in hy_cell


def test_language_table_shows_the_lower_precision_label():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q109"])
    from cutora.asr.languages import capability_summary_line
    from cutora.ui.language_support import LanguageSupportDialog
    dialog = LanguageSupportDialog()
    assert dialog.summary.text() == capability_summary_line()
    assert "74 种可用" in dialog.summary.text()
    found = False
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text() == "hy":
            found = True
            assert dialog.table.item(row, 3).text() == "可用（通用模型，精度略低）"
    assert found
    dialog.close()
