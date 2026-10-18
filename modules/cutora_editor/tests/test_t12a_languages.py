# -*- coding: utf-8 -*-
"""T12a: languages.json catalog, auto-detect, asr.detect_language."""
from __future__ import annotations

import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_languages_json_covers_whisper_codes():
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    from cutora.asr.languages import (
        SCRIPT_ENUM,
        catalog_by_code,
        load_catalog,
        languages_json_path,
    )

    assert os.path.isfile(languages_json_path()), languages_json_path()
    rows = load_catalog()
    by_code = catalog_by_code()
    whisper = set(_LANGUAGE_CODES)
    ours = {r["code"] for r in rows}
    assert ours == whisper, {
        "missing": sorted(whisper - ours),
        "extra": sorted(ours - whisper),
    }
    for row in rows:
        assert row["script"] in SCRIPT_ENUM, row
        for key in ("name_en", "name_native", "name_zh"):
            assert row[key], (key, row["code"])
    assert by_code["en"]["script"] == "latin"
    assert by_code["en"]["experimental"] is False
    assert by_code["it"]["script"] == "latin"
    assert by_code["ru"]["script"] == "cyrillic"
    assert by_code["zh"]["experimental"] is True
    assert by_code["ja"]["experimental"] is True
    assert by_code["ar"]["experimental"] is True


def test_picker_entries_auto_common_all():
    from cutora.asr.languages import common_codes, picker_entries

    entries = picker_entries()
    assert entries[0]["code"] == "auto"
    assert entries[0]["section"] == "auto"
    assert "自动" in entries[0]["label"]
    common = [e for e in entries if e["section"] == "common"]
    all_rest = [e for e in entries if e["section"] == "all"]
    assert [e["code"] for e in common] == common_codes()
    for need in ("en", "it", "es", "fr", "de", "ru"):
        assert need in {e["code"] for e in common}, need
    from cutora.asr.languages import catalog_by_code
    rest_native = [
        catalog_by_code()[e["code"]]["name_native"] for e in all_rest
    ]
    assert rest_native == sorted(rest_native)
    ja = next(e for e in entries if e["code"] == "ja")
    assert ja["experimental"] is True
    assert ja["label"] == "日语 · 可用（精度略低） · 仅字形保证"
    codes = [e["code"] for e in entries]
    assert len(codes) == len(set(codes))


def test_whisper_language_arg_and_default_config():
    from cutora.asr.languages import set_asr_language, whisper_language_arg
    from cutora.core import config as cfg
    from cutora.core.commands import CommandError

    assert cfg.LANGUAGE == "en"
    assert whisper_language_arg("en") == "en"
    assert whisper_language_arg("IT") == "it"
    assert whisper_language_arg("auto") is None
    assert whisper_language_arg("") is None
    assert whisper_language_arg(None) is None
    prev = cfg.LANGUAGE
    try:
        assert set_asr_language("auto") == "auto"
        assert cfg.LANGUAGE == "auto"
        assert set_asr_language("ru") == "ru"
        assert cfg.LANGUAGE == "ru"
        with pytest.raises(CommandError):
            set_asr_language("xx-not-a-lang")
    finally:
        cfg.LANGUAGE = prev


def test_low_confidence_threshold_and_prompt():
    from cutora.asr.detect import LOW_CONFIDENCE_THRESHOLD, build_detection_result

    assert LOW_CONFIDENCE_THRESHOLD == 0.6
    low = build_detection_result("it", 0.59)
    assert low["low_confidence"] is True
    assert low["language"] == "it"
    assert "低置信" in low["prompt_zh"]
    assert "意大利" in low["prompt_zh"]
    ok = build_detection_result("en", 0.6)
    assert ok["low_confidence"] is False
    assert ok["prompt_zh"] == ""
    zh = build_detection_result("zh", 0.99)
    assert zh["experimental"] is True
    ru = build_detection_result("ru", 0.88)
    assert ru["script"] == "cyrillic"
    assert ru["experimental"] is False


def test_detect_from_array_uses_first_30s_and_model():
    from cutora.asr.detect import DETECT_SECONDS, SAMPLE_RATE, detect_from_array

    seen = {}

    class _Fw:
        def detect_language(self, audio):
            seen["n"] = len(audio)
            return "es", 0.81, [("es", 0.81)]

    class _Pipe:
        model = _Fw()

    audio = list(range(DETECT_SECONDS * SAMPLE_RATE + 1234))
    result = detect_from_array(audio, model=_Pipe())
    assert seen["n"] == DETECT_SECONDS * SAMPLE_RATE
    assert result["language"] == "es"
    assert result["confidence"] == 0.81
    assert result["low_confidence"] is False


def test_asr_detect_language_command_schema_and_handler(tmp_path, monkeypatch):
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import CommandError, get_bus, get_registry

    spec = get_registry().get("asr.detect_language")
    assert spec.headless is True
    assert spec.async_job is True
    assert spec.dangerous is False
    assert spec.requires_project is False
    assert len(spec.doc_zh) >= 40
    check_schema(spec.params_schema)
    check_schema(spec.returns_schema)

    bus = get_bus()
    with pytest.raises(CommandError):
        bus.execute("asr.detect_language", {}, source="cli")

    media = tmp_path / "clip.wav"
    media.write_bytes(b"RIFF")

    def fake_detect(path, model=None):
        assert os.path.isfile(path)
        return {
            "language": "en",
            "confidence": 0.42,
            "low_confidence": True,
            "experimental": False,
            "script": "latin",
            "name_zh": "英语",
            "name_en": "English",
            "name_native": "English",
            "prompt_zh": "检测为 英语（低置信），请确认",
        }

    monkeypatch.setattr("cutora.asr.detect.detect_for_media", fake_detect)
    result = bus.execute(
        "asr.detect_language", {"media": str(media)}, source="cli")
    assert result["language"] == "en"
    assert result["low_confidence"] is True
    assert "低置信" in result["prompt_zh"]


def test_main_window_language_menu_uses_catalog():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "picker_entries" in src
    assert '("English (大模型)", "en")' not in src
    assert "_start_detect_worker" in src
    assert "DetectWorker" in src
    engine = open(
        os.path.join(REPO, "cutora", "asr", "engine.py"), encoding="utf-8"
    ).read()
    assert "whisper_language_arg" in engine
    worker = open(
        os.path.join(REPO, "cutora", "asr", "align_worker.py"), encoding="utf-8"
    ).read()
    assert "detect_from_array" in worker
    assert "has_local_align_model" in worker


def test_ensure_segment_words_fills_missing():
    from cutora.asr.align_worker import _ensure_segment_words

    segs = _ensure_segment_words([
        {"start": 0.0, "end": 1.2, "text": "hello there"},
        {"start": 1.2, "end": 2.0, "text": "ok", "words": [
            {"word": "ok", "start": 1.2, "end": 2.0},
        ]},
    ])
    assert segs[0]["words"][0]["word"] == "hello there"
    assert segs[1]["words"][0]["word"] == "ok"
