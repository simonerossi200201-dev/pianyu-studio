# -*- coding: utf-8 -*-
"""BUG-06 ASR tail self-check · BUG-07 scripture/number fold."""
from __future__ import annotations

import numpy as np

from cutora.batch import script_stitcher as ss


def _env(segments, dur, floor=-50.0, hop=ss.ENV_HOP):
    n = int(round(dur / hop))
    db = np.full(n, floor, dtype=np.float32)
    for a, b, lv in segments:
        db[int(round(a / hop)):int(round(b / hop))] = lv
    return {"db": db, "hop": hop, "floor": floor}


def test_bug06_tail_energy_flags_incomplete():
    # T0040-s002-p09: last word 2.90s, dur 8s, tail still speaking.
    words = [("many", 0.0, 0.74), ("others", 2.42, 2.90)]
    env = _env([(0.0, 2.9, -20.0), (3.4, 8.0, -20.0)], 8.0)
    assert ss.transcript_incomplete(words, 8.0, env) is True


def test_bug06_quiet_tail_is_complete():
    words = [("many", 0.0, 0.74), ("others", 2.42, 2.90)]
    env = _env([(0.0, 2.9, -20.0)], 8.0, floor=-50.0)
    assert ss.transcript_incomplete(words, 8.0, env) is False


def test_bug06_last_word_near_end_is_complete():
    words = [("amen", 6.8, 7.4)]
    env = _env([(0.0, 7.4, -20.0)], 8.0)
    assert ss.transcript_incomplete(words, 8.0, env) is False


def test_bug06_fill_reruns_whole_clip_no_vad(monkeypatch):
    words = [("many", 0.0, 0.74), ("others", 2.42, 2.90)]
    sr = 16000
    audio = np.zeros(sr * 8, dtype=np.float32)
    audio[int(3.4 * sr):] = 0.05

    def fake_fw(model, audio_all, language, dur):
        assert audio_all is audio
        return [{
            "start": 0.0, "end": 7.8,
            "text": "many people grace mercy freedom",
            "words": [
                {"word": "many", "start": 0.0, "end": 0.5},
                {"word": "people", "start": 0.5, "end": 1.0},
                {"word": "grace", "start": 3.5, "end": 4.0},
                {"word": "mercy", "start": 4.2, "end": 4.8},
                {"word": "freedom", "start": 7.0, "end": 7.8},
            ],
        }]

    monkeypatch.setattr(ss, "_fw_transcribe_words", fake_fw)
    logs = []
    out, still = ss.fill_incomplete_transcript(
        object(), audio, words, 8.0, "en", log=logs.append)
    names = [w[0] for w in out]
    assert "grace" in names and "mercy" in names and "freedom" in names
    assert ss.last_word_end(out) >= 7.5
    assert any("整段" in x and "VAD" in x for x in logs)


def test_bug06_even_split_detected():
    even = [(str(i), i * 0.53, (i + 1) * 0.53) for i in range(15)]
    assert ss.is_even_split(even)
    assert ss.word_dur_std(even) <= ss.EVEN_SPLIT_STD
    real = [
        ("many", 0.00, 0.74), ("people", 0.74, 1.10), ("use", 1.10, 1.38),
        ("scripture", 1.38, 1.80), ("to", 1.80, 2.10), ("condemn", 2.10, 2.42),
        ("others", 2.42, 2.90), ("but", 3.00, 3.25), ("forget", 3.25, 3.70),
        ("that", 3.70, 3.90), ("jesus", 3.90, 4.40), ("also", 4.50, 4.80),
        ("brought", 4.90, 5.40), ("grace", 5.50, 6.00), ("mercy", 6.20, 6.80),
    ]
    assert ss.word_dur_std(real) > 0.08
    assert not ss.is_even_split(real)


def test_bug06_rerun_has_real_word_timing(monkeypatch):
    even = [
        (w, i * 0.53, (i + 1) * 0.53)
        for i, w in enumerate(
            "many people use scripture to condemn others but forget "
            "that jesus also brought grace mercy".split())
    ]
    real = [
        ("many", 0.00, 0.74), ("people", 0.74, 1.10), ("use", 1.10, 1.38),
        ("scripture", 1.38, 1.80), ("to", 1.80, 2.10), ("condemn", 2.10, 2.42),
        ("others", 2.42, 2.90), ("but", 3.00, 3.25), ("forget", 3.25, 3.70),
        ("that", 3.70, 3.90), ("jesus", 3.90, 4.40), ("also", 4.50, 4.80),
        ("brought", 4.90, 5.40), ("grace", 5.50, 6.00), ("mercy", 6.20, 6.80),
    ]

    def fake_align(segs, model, meta, audio, device, **kwargs):
        return {"segments": [{
            "start": 0.0, "end": 8.0, "text": "x",
            "words": [{"word": t, "start": a, "end": b} for t, a, b in real],
        }]}

    monkeypatch.setattr(ss.whisperx, "align", fake_align)
    audio = np.zeros(16000, dtype=np.float32)
    out, tier = ss.stamp_word_timings(
        None, object(), {}, audio, even, 8.0, "en", "cpu")
    assert tier <= 2
    assert ss.word_dur_std(out) > 0.08
    cache = {
        "words": [[w[0], round(float(w[1]), 3), round(float(w[2]), 3)] for w in out],
        "timing_tier": tier,
    }
    assert cache["timing_tier"] <= 2
    durs = [c[2] - c[1] for c in cache["words"]]
    assert float(np.std(durs)) > 0.08


def test_bug06_p09_evidence_json_has_real_timing():
    import json
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "plan", "reports", "bug0607_evidence", "p09_words.json")
    data = json.loads(open(path, encoding="utf-8").read())
    assert int(data.get("timing_tier") or 99) <= 2
    durs = [float(w[2]) - float(w[1]) for w in data["words"]]
    assert float(np.std(durs)) > 0.08
    assert float(data["last_end"]) >= 7.5


def test_bug06_still_short_marks_incomplete_not_missing():
    items = [{"name": "p09.mp4", "keep": True, "s_runs": [[0, 4]]}]
    missing = [{"s": 4, "e": 12, "text": "but forget that Jesus also brought grace"}]
    clips = [{"name": "p09.mp4", "incomplete": True}]
    tagged = ss.tag_incomplete_missing(items, missing, clips)
    assert tagged[0]["kind"] == "incomplete"
    footer = ss.format_missing_footer(incomplete_count=1)
    assert ss.INCOMPLETE_MARK in "\n".join(footer)


def test_bug07_john_1613_matches_copy():
    script = ss.tokenize("John 16:13 says when he the spirit of truth is come")
    asr = [("john", 0, 0.2), ("1613", 0.2, 0.5), ("says", 0.5, 0.7),
           ("when", 0.7, 0.9), ("he", 0.9, 1.0), ("the", 1.0, 1.1),
           ("spirit", 1.1, 1.4), ("of", 1.4, 1.5), ("truth", 1.5, 1.8),
           ("is", 1.8, 1.9), ("come", 1.9, 2.2)]
    m = ss.match_clip_global(asr, script)
    assert m["matched"] or m["hit"] >= 8
    assert "16" in script and "13" in script
    assert ss.tokenize("john 1613") == ss.tokenize("John 16:13")


def test_bug07_scripture_equivalence_class():
    want = ss.tokenize("John 16:13")
    variants = [
        "John 16:13",
        "john 16 13",
        "john 1613",
        "john sixteen thirteen",
        "John chapter 16 verse 13",
    ]
    for text in variants:
        got = ss.tokenize(text)
        assert got == want, (text, got, want)


def test_bug07_luke_john_matthew_and_numbers():
    assert ss.tokenize("Luke 13:3") == ss.tokenize("luke 13 3")
    assert ss.tokenize("(Isaiah 44:6)")[-2:] == ["44", "6"]
    assert ss.tokenize("(John 1:14)")[-2:] == ["1", "14"]
    assert ss.tokenize("John 8") == ss.tokenize("john eight")
    assert ss.tokenize("Matthew 5") == ss.tokenize("matthew five")
    assert ss.tokenize("KJV") == ss.tokenize("K J V")
    assert ss.tokenize("KJV") == ["kjv"]


def test_bug07_copy_side_refs_match_asr_side():
    # The two Luca copies' inline refs (parenthetical ones are ignored by SCRIPTURE_REF_RE).
    pairs = [
        ("John 16:13", [("john", 0, 0), ("1613", 0, 0)]),
        ("Luke 13:3", [("luke", 0, 0), ("thirteen", 0, 0), ("three", 0, 0)]),
        ("John 8", [("john", 0, 0), ("eight", 0, 0)]),
        ("Matthew 5", [("matthew", 0, 0), ("five", 0, 0)]),
    ]
    for copy, words in pairs:
        script = ss.tokenize(copy)
        m = ss.match_clip_global(words, script)
        assert m["hit"] >= 2, (copy, script, m)


def test_internal_use_file_and_no_gpl_license():
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    use = os.path.join(repo, "INTERNAL_USE.txt")
    assert os.path.isfile(use)
    assert "内部使用，不对外分发" in open(use, encoding="utf-8").read()
    assert not os.path.isfile(os.path.join(repo, "LICENSE"))
    from cutora.ui.about import about_text
    text = about_text()
    assert "内部使用，不对外分发" in text
    assert "TODO" not in text
    assert "源码：" not in text


def test_bug07_t0035_t0040_all_scripture_refs():
    """Every inline scripture ref in Luca's two copies matches ASR-like forms."""
    import os
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "asr_coverage")
    cases = {
        "t0035_copy.txt": [
            "Luke 13:3", "luke 133", "luke thirteen three",
            "John 8", "john eight",
            "John 16:13", "john 1613", "john sixteen thirteen",
            "KJV", "K J V",
        ],
        "t0040_copy.txt": [
            "Matthew 5", "matthew five",
        ],
    }
    for fname, variants in cases.items():
        text = open(os.path.join(root, fname), encoding="utf-8").read()
        script = ss.tokenize(text)
        for form in variants:
            words = [(t, 0.0, 0.0) for t in ss.tokenize(form)]
            m = ss.match_clip_global(words, script)
            assert m["hit"] >= max(1, len(words) - 1), (fname, form, script, m)


def test_align_it_sha256_matches_build():
    import hashlib
    import os
    from cutora.asr.models import get_model, reset_catalog, weight_path
    reset_catalog()
    row = get_model("align-it")
    assert row["sha256"].startswith("620bad57")
    path = weight_path(row)
    if os.path.isfile(path):
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert h == row["sha256"]
        assert os.path.getsize(path) == 377689881
