# -*- coding: utf-8 -*-
"""T12c 提交 2：对齐表许可证、回退近似词级、en/it 路径不动。"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_align_it_license_copied_from_model_card():
    from cutora.asr.models import get_model, reset_catalog
    reset_catalog()
    row = get_model("align-it")
    assert row is not None
    assert row["license"] == "CC-BY-NC-4.0"
    assert "voxpopuli" in (row.get("license_url") or "").lower()
    assert row.get("enabled") is True
    assert row.get("weight_file", "").endswith(".pt")


def test_en_it_align_load_call_unchanged():
    src = open(os.path.join(REPO, "cutora", "asr", "engine.py"), encoding="utf-8").read()
    assert '"en": "WAV2VEC2_ASR_LARGE_LV60K_960H"' in src
    assert '"it": None' in src
    assert "model_name=ALIGN_MODEL_BY_LANG.get(lang)" in src
    assert "model_dir=align_dir" in src


def test_fallback_languages_have_no_enabled_aligner():
    from cutora.asr.languages import load_catalog
    from cutora.asr.models import align_row_for_language, reset_catalog
    reset_catalog()
    for row in load_catalog():
        if row["line_break"] != "fallback":
            continue
        rec = align_row_for_language(row["code"])
        if rec is not None:
            assert rec.get("enabled") is False, row["code"]


def test_noncompliant_align_licenses_are_disabled():
    from cutora.asr.models import catalog, is_license_allowed, reset_catalog
    reset_catalog()
    found = {r["id"]: r for r in catalog() if r.get("kind") == "align"}
    assert "align-da" in found
    assert found["align-da"]["enabled"] is False
    assert found["align-sv"]["enabled"] is True
    assert found["align-vi"]["enabled"] is False
    assert found["align-hr"]["enabled"] is False
    assert found["align-gl"]["enabled"] is False
    for row in found.values():
        if row.get("gated"):
            assert row.get("enabled") is False, row["id"]


def test_space_language_aligners_enabled_when_license_ok():
    from cutora.asr.models import catalog, is_license_allowed, reset_catalog
    reset_catalog()
    enabled = [
        r for r in catalog()
        if r.get("kind") == "align" and r.get("enabled")
        and r.get("language") not in (None, "", "en", "it")
    ]
    assert any(r["language"] == "fr" for r in enabled)
    assert any(r["language"] == "ru" for r in enabled)
    assert any(r["language"] == "el" for r in enabled)
    for row in enabled:
        assert row.get("gated") in (None, False)


def test_word_timestamps_fallback_sets_score_none():
    from cutora.asr.align_worker import _faster_whisper_segments

    class _W:
        word = "ciao"
        start = 0.1
        end = 0.4

    class _S:
        start = 0.1
        end = 0.4
        text = "ciao"
        words = [_W()]

    class _FW:
        def transcribe(self, audio, **kwargs):
            assert kwargs.get("word_timestamps") is True
            assert kwargs.get("vad_filter") is True
            return [_S()], None

    segs = _faster_whisper_segments(_FW(), audio=None, lang="it")
    assert segs[0]["words"][0]["score"] is None
    assert segs[0]["words"][0]["word"] == "ciao"


def test_approximate_flag_on_worker_without_align_model():
    from cutora.asr.align_worker import AlignmentWorker
    w = AlignmentWorker.__new__(AlignmentWorker)
    w.align_note = None
    w.approximate_words = False
    w._is_running = True
    w.video_path = "x"
    calls = {}

    class _Pipe:
        model = None
        def transcribe(self, audio, **kwargs):
            calls["t"] = True
            return {"segments": [{"start": 0, "end": 1, "text": "x"}]}

    w.progress = type("P", (), {"emit": staticmethod(lambda *_a, **_k: None)})()
    w.smart_segmentation = lambda segs, dur: segs
    out = AlignmentWorker._transcribe_without_align(w, _Pipe(), [0], 1.0, "zh")
    assert w.approximate_words is True
    assert w.align_note == "近似词级"
    assert out
    assert calls.get("t") is True
