# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q108 language tiers, auto-detect, home picker, support table.

Qt imports stay inside the tests. This module is listed in qt_isolate.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERAGE = os.path.join(REPO, "resources", "data", "font_script_coverage.json")

# Official full-block scan still marks these uncovered. Dedicated Noto faces
# are in the file, but each misses code points, so the boolean stays false.
STILL_ZERO_SCRIPTS = (
    "bengali", "ethiopic", "gujarati", "gurmukhi", "kannada", "khmer",
    "lao", "malayalam", "sinhala", "tamil", "telugu", "thai", "tibetan",
)


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q108"])


def _script_cover_counts() -> dict:
    data = json.load(open(COVERAGE, encoding="utf-8"))
    cov = data.get("coverage") or {}
    out = {}
    for script in STILL_ZERO_SCRIPTS:
        out[script] = sum(
            1 for row in cov.values()
            if isinstance(row, dict) and row.get(script)
        )
    return out


def test_align_tier_exact_none_unknown():
    """Unknown codes return ``none``. They are not raised.

    en and de have a dedicated aligner. hy has no dedicated aligner.
    Q109 turns the generic aligner on when MMS and uroman are both present,
    so hy is ``fallback`` in that case and ``none`` when they are not.
    """
    from cutora.asr.models import align_tier, generic_aligner_available
    assert align_tier("en") == "exact"
    assert align_tier("de") == "exact"
    if generic_aligner_available():
        assert align_tier("hy") == "fallback"
    else:
        assert align_tier("hy") == "none"
    assert align_tier("not-a-language") == "none"
    assert align_tier("") == "none"


def test_language_capability_is_the_catalog():
    from cutora.asr.languages import load_catalog
    from cutora.asr.models import language_capability
    from cutora.subtitle.font_coverage import load_coverage, reset_caches
    reset_caches()
    rows = load_catalog()
    assert len(rows) == 100
    cov = load_coverage().get("coverage") or {}
    fields = {"code", "name_zh", "transcribe", "align_tier", "subtitle_tier", "font_ok"}
    for row in rows:
        cap = language_capability(row["code"])
        assert set(cap) == fields
        assert cap["transcribe"] is True
        assert cap["code"] == row["code"]
        assert cap["name_zh"] == row["name_zh"]
        assert cap["subtitle_tier"] == row["subtitle_tier"]
        script = row["script"]
        expect = any(
            isinstance(fam, dict) and bool(fam.get(script))
            for fam in cov.values()
        )
        assert cap["font_ok"] is expect


def test_rebuilt_coverage_names_scripts_still_at_zero():
    """These 13 scripts are still uncovered after the coverage rebuild.

    A looser ``count > 0`` assertion is red: each count is 0. The test
    names that set exactly.
    """
    counts = _script_cover_counts()
    zeros = sorted(script for script, n in counts.items() if n == 0)
    assert zeros == sorted(STILL_ZERO_SCRIPTS)
    for script in STILL_ZERO_SCRIPTS:
        assert counts[script] == 0


class _Info:
    def __init__(self, language, language_probability):
        self.language = language
        self.language_probability = language_probability


class _FW:
    def __init__(self, info):
        self.info = info
        self.languages = []

    def transcribe(self, audio, language=None, **kwargs):
        self.languages.append(language)
        return [], self.info


def _worker(tmp_path, info, get_align, confirm):
    from cutora.batch.script_stitcher import AnalyzeWorker
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    fw = _FW(info)

    def get_model():
        pipe = type("Pipe", (), {})()
        pipe.model = fw
        return pipe

    worker = AnalyzeWorker(
        {"1": [str(a), str(b)]},
        {"1": "hello world"},
        get_model,
        get_align,
        language="auto",
        edge_refine=False,
        out_dir="",
    )
    return worker, fw


def _patch_media(monkeypatch):
    import numpy as np
    from cutora.batch import script_stitcher as ss

    def probe(_path):
        return {
            "duration": 1.5, "width": 16, "height": 16,
            "fps": 30.0, "has_audio": True,
        }

    monkeypatch.setattr(ss, "probe_media", probe)
    monkeypatch.setattr(
        ss, "load_audio_16k",
        lambda _path: np.zeros(1600, dtype=np.float32))
    calls = []

    def transcribe(*_a, **_k):
        calls.append(1)
        return [("hello", 0.0, 0.2), ("world", 0.2, 0.5)]

    monkeypatch.setattr(ss, "transcribe_clip", transcribe)
    return calls


def test_auto_high_confidence_loads_align_once(monkeypatch, tmp_path):
    _app()
    monkeypatch.setenv("CUTORA_ASR_INPROC", "1")
    transcribed = _patch_media(monkeypatch)
    confirms = []
    monkeypatch.setattr(
        "cutora.ui.detect_confirm.ask_low_confidence",
        lambda *_a, **_k: confirms.append(1) or "en")
    loaded = []

    def get_align(lang):
        loaded.append(lang)
        return ("align", {"lang": lang})

    worker, fw = _worker(tmp_path, _Info("de", 0.95), get_align, None)
    worker.run()
    assert fw.languages == [None]
    assert confirms == []
    assert loaded == ["de"]
    assert len(transcribed) == 2


def test_auto_low_confidence_confirms_then_loads_chosen(monkeypatch, tmp_path):
    _app()
    monkeypatch.setenv("CUTORA_ASR_INPROC", "1")
    _patch_media(monkeypatch)
    seen = []

    def confirm(_parent, detection):
        seen.append(dict(detection))
        return "en"

    monkeypatch.setattr("cutora.ui.detect_confirm.ask_low_confidence", confirm)
    loaded = []

    def get_align(lang):
        loaded.append(lang)
        return ("align", {"lang": lang})

    worker, _fw = _worker(tmp_path, _Info("de", 0.4), get_align, None)
    worker.run()
    assert len(seen) == 1
    assert seen[0]["language"] == "de"
    assert abs(float(seen[0]["confidence"]) - 0.4) < 1e-6
    assert loaded == ["en"]


def test_none_tier_stops_before_ffmpeg(monkeypatch, tmp_path):
    """The pre-cut stop still fires when the generic aligner is unavailable.

    Q109 makes hy ``fallback`` once MMS and uroman are present, so this
    test turns that predicate off and checks the guard with hy again.
    """
    _app()
    monkeypatch.setenv("CUTORA_ASR_INPROC", "1")
    monkeypatch.setattr(
        "cutora.asr.models.generic_aligner_available", lambda: False)
    from cutora.batch import script_stitcher as ss
    media = []

    def _run(cmd, timeout=None):
        media.append(list(cmd))
        raise AssertionError("media tool called: %s" % cmd)

    monkeypatch.setattr(ss, "_run", _run)
    loaded = []

    def get_align(lang):
        loaded.append(lang)
        return ("align", {})

    worker, _fw = _worker(tmp_path, _Info("hy", 0.95), get_align, None)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert media == []
    assert loaded == []
    assert errors
    text = errors[0]
    assert "亚美尼亚" in text
    assert "拼接" in text
    assert "逐词" in text


def test_home_picker_auto_first_and_none_disabled():
    _app()
    from PySide6.QtGui import QStandardItemModel
    from cutora.asr.languages import picker_entries
    from cutora.asr.models import language_capability
    from cutora.ui.home_stitch import HomeStitchBand
    band = HomeStitchBand()
    combo = band.cmb_lang
    entries = picker_entries()
    assert combo.itemText(0) == "自动检测"
    assert combo.currentIndex() == 0
    assert combo.currentData() == "auto"
    assert combo.count() == len(entries)
    model = combo.model()
    assert isinstance(model, QStandardItemModel)
    for i, ent in enumerate(entries):
        item = model.item(i)
        if ent["code"] == "auto":
            assert item.isEnabled()
            continue
        cap = language_capability(ent["code"])
        if cap["align_tier"] == "none":
            assert item.isEnabled() is False
        else:
            assert item.isEnabled() is True
    en = next(i for i, ent in enumerate(entries) if ent["code"] == "en")
    assert "逐词精确" in combo.itemText(en)
    assert "排版完善" in combo.itemText(en)
    hy = next(i for i, ent in enumerate(entries) if ent["code"] == "hy")
    from cutora.asr.languages import align_phrase
    hy_tier = language_capability("hy")["align_tier"]
    assert align_phrase(hy_tier) in combo.itemText(hy)
    if hy_tier == "none":
        assert "暂不支持拼接" in combo.itemText(hy)
    else:
        assert "可用（精度略低）" in combo.itemText(hy)
        assert "暂不支持拼接" not in combo.itemText(hy)


def test_language_support_table_matches_capability():
    _app()
    from cutora.asr.languages import (
        capability_summary_line, capability_table_cells, load_catalog,
    )
    from cutora.asr.models import language_capability
    from cutora.ui.language_support import LanguageSupportDialog
    dlg = LanguageSupportDialog()
    assert dlg.table.rowCount() == 100
    assert dlg.summary.text() == capability_summary_line()
    assert "可转写 100 种" in dlg.summary.text()
    by_code = {}
    for r in range(dlg.table.rowCount()):
        cells = tuple(dlg.table.item(r, c).text() for c in range(6))
        by_code[cells[1]] = cells
    for code in ("en", "hy", "de"):
        cap = language_capability(code)
        assert by_code[code] == capability_table_cells(cap)
        assert by_code[code][0] == cap["name_zh"]
        assert by_code[code][1] == cap["code"]
    assert len(load_catalog()) == dlg.table.rowCount()
    dlg.search.setText("亚美尼亚")
    visible = [
        r for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r)
    ]
    assert visible
    assert all("亚美尼亚" in dlg.table.item(r, 0).text() for r in visible)
    dlg.close()
