# -*- coding: utf-8 -*-
"""T04b: space/fallback line break, CPS, extend without overlap."""
from __future__ import annotations

from cutora.subtitle.layout import (
    DEFAULT_CPS,
    DEFAULT_MAX_CHARS,
    LayoutRules,
    break_lines,
    check_cps_task,
    cps,
    extend_durations_task,
    load_stopwords,
    relayout_task,
)
from cutora.subtitle.model import Segment

EN_SAMPLES = [
    "The cat sat on the mat near a small red barn.",
    "Please put the book on the table by the window.",
    "We went to the market for a loaf of bread.",
    "She looked at the sky and waited for the rain.",
    "A friend of mine lives in a house on the hill.",
    "Do not leave the keys in the door after you go.",
    "He asked for a cup of tea with a slice of lemon.",
    "They walked to the station in the early morning fog.",
    "This is an example of a line that must wrap cleanly.",
    "Turn left at the corner and walk to the old church.",
    "The sound of the river was a comfort to the tired hikers.",
    "Keep the fire in the stove for the rest of the night.",
    "I would like to speak to the manager of the hotel.",
    "Put a lid on the pot and let it rest for a minute.",
    "The children ran to the garden to look at the flowers.",
]

IT_SAMPLES = [
    "Il gatto è sul tetto della casa grande.",
    "La ragazza va alla stazione con un amico.",
    "Un uomo parla con la donna della vetrina.",
    "I bambini giocano nel parco della città vecchia.",
    "Ho comprato il pane dal fornaio del paese.",
    "Lei vive in una casa sulla collina di fronte.",
    "Andiamo alla festa con gli amici della scuola.",
    "Il treno parte dalla stazione di Milano centrale.",
    "Metti il libro sulla tavola della cucina.",
    "Vorrei parlare con il direttore della biblioteca.",
    "La luce della luna cade sul mare in tempesta.",
    "Prendi un caffè al bar della piazza principale.",
    "Il vento spinge le foglie lungo il viale del parco.",
    "Siamo andati al mercato per le verdure del contadino.",
    "La voce del cantante riempie la sala del teatro.",
]


def _seg(text, lang, start=0.0, end=3.0, locked=False):
    return Segment(
        id="", start=start, end=end, text=text, lang=lang, locked=locked,
    )


def _rules(lang):
    return LayoutRules(lang=lang, line_break="space", balance_lines=True)


def test_menu_relayout_and_cps_go_through_bus():
    import os
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "cutora", "ui", "main_window.py"),
        encoding="utf-8",
    ).read()
    assert 'tr("检查阅读速度")' in src
    assert 'tr("按当前排版重新断句")' in src
    assert 'bus.execute("subtitle.check_cps"' in src
    assert 'bus.execute("subtitle.relayout"' in src
    assert 'bus.execute("subtitle.extend_durations"' in src


def test_en_it_thirty_sentences_fit_and_avoid_hanging_words():
    stops_en = load_stopwords("en")
    stops_it = load_stopwords("it")
    assert "the" in stops_en and "of" in stops_en
    assert "di" in stops_it and "la" in stops_it
    samples = [(t, "en", stops_en) for t in EN_SAMPLES] + [
        (t, "it", stops_it) for t in IT_SAMPLES
    ]
    assert len(samples) == 30
    for text, lang, stops in samples:
        lines = break_lines(_seg(text, lang), _rules(lang))
        assert 1 <= len(lines) <= 2, (lang, text, lines)
        for line in lines:
            assert len(line) <= DEFAULT_MAX_CHARS, (lang, line, len(line))
        if len(lines) == 2:
            last = lines[0].split()[-1].strip(".,;:!?\"'").lower()
            assert last not in stops, (lang, text, lines)
            a, b = len(lines[0]), len(lines[1])
            assert abs(a - b) <= max(a, b), (lang, lines)


def test_fallback_hard_cut_is_approx():
    text = "x" * 90
    lines = break_lines(
        _seg(text, "th"),
        LayoutRules(lang="th", line_break="fallback"),
    )
    assert lines[0] == "x" * 42
    assert "".join(lines) == text


def test_orphan_word_borrows_from_previous_line():
    from cutora.subtitle.layout import _fix_orphan_lines
    stops = load_stopwords("en")
    out = _fix_orphan_lines(
        ["Welcome to the studio. Today we", "will"], 42, stops,
    )
    assert out[1].split() != ["will"], out
    assert out[1].split()[-1].lower().strip(".,") == "will"
    assert len(out[1].split()) >= 2, out
    # hanging article: don't leave "the" at EOL
    out2 = _fix_orphan_lines(["sat by the", "will"], 42, stops)
    assert out2[0].split()[-1].lower() != "the", out2
    # the chant-style sentence with a tight cap must not widow "will"
    text = "Welcome to the studio. Today we will"
    lines = break_lines(_seg(text, "en"), LayoutRules(lang="en", max_chars_per_line=28))
    assert lines[-1].split() != ["will"], lines
    assert "will" in lines[-1]


def test_sin_rarely_four_line_avoids_hanging_function_words():
    """T04f-1c: 4-line scoring must not end a line on at/the for this sentence."""
    text = (
        "Sin rarely destroys a person all at once. It erodes the heart slowly."
    )
    lines = break_lines(
        _seg(text, "en"),
        LayoutRules(lang="en", max_chars_per_line=19, max_lines=8,
                    balance_lines=True),
    )
    assert lines == [
        "Sin rarely destroys",
        "a person all at once.",
        "It erodes the heart",
        "slowly.",
    ], lines
    stops = load_stopwords("en")
    for line in lines[:-1]:
        last = line.split()[-1].strip(".,;:!?\"'").lower()
        assert last not in stops, (line, last)


def test_does_not_split_hyphen_or_number():
    text = "Call well-known agent 1,000 times in the office today please."
    lines = break_lines(_seg(text, "en"), _rules("en"))
    blob = " ".join(lines)
    assert "well-known" in blob
    assert "1,000" in blob
    for line in lines:
        assert "well-" != line[-5:]


def test_cps_and_extend_keeps_min_gap_skips_locked():
    task = {"segments": [
        {"start": 0.0, "end": 0.5, "text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        {"start": 1.0, "end": 2.0, "text": "ok"},
        {"start": 2.0, "end": 2.2, "text": "LOCKEDTEXTGOESHERE", "locked": True},
        {"start": 3.0, "end": 4.0, "text": "next"},
    ]}
    assert cps(task["segments"][0]) > DEFAULT_CPS
    hits = check_cps_task(task)
    ids = [h["seg_id"] for h in hits]
    assert 0 in ids and 2 in ids
    out = extend_durations_task(task, min_gap=0.08)
    assert out["changed"] >= 1
    assert out["skipped_locked"] == 1
    first, second = task["segments"][0], task["segments"][1]
    assert first["end"] <= second["start"] - 0.08 + 1e-9
    assert task["segments"][2]["end"] == 2.2


def test_relayout_check_extend_commands(tmp_path):
    import json
    import os
    import shutil
    from cutora.core.commands import get_bus, get_registry
    from cutora.core.command_schema import check_schema
    for name in ("subtitle.relayout", "subtitle.check_cps", "subtitle.extend_durations"):
        spec = get_registry().get(name)
        assert spec.headless is True
        assert spec.doc_zh
        assert len(spec.doc_zh) >= 40
        check_schema(spec.params_schema)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    g01 = os.path.join(repo, "tests", "golden", "_media", "g01", "video.mp4")
    media = tmp_path / "video.mp4"
    shutil.copy2(g01, media)
    proj = tmp_path / "project.asproj.json"
    proj.write_text(json.dumps({
        "version": 2,
        "tasks": [{
            "video": str(media),
            "path": str(media),
            "text": "",
            "segments": [
                {"start": 0.0, "end": 0.4, "text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "lang": "en"},
                {"start": 1.0, "end": 2.0, "text": "ok", "lang": "en"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    bus = get_bus()
    checked = bus.execute("subtitle.check_cps", {
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert checked["count"] >= 1
    ext = bus.execute("subtitle.extend_durations", {
        "media": str(media), "project": str(proj), "min_gap": 0.08,
    }, source="cli")
    assert ext["changed"] >= 1
    data = json.loads(proj.read_text(encoding="utf-8"))
    segs = data["tasks"][0]["segments"]
    assert segs[0]["end"] <= segs[1]["start"] - 0.08 + 1e-9
    laid = bus.execute("subtitle.relayout", {
        "media": str(media), "project": str(proj),
    }, source="cli")
    assert laid["changed"] >= 1
    data = json.loads(proj.read_text(encoding="utf-8"))
    assert data["tasks"][0]["segments"][0].get("lines")


def test_relayout_writes_lines_skips_locked():
    task = {"segments": [
        {"start": 0, "end": 2, "text": "Please put the book on the table by the window.", "lang": "en"},
        {"start": 2, "end": 4, "text": "Leave me.", "lang": "en", "locked": True},
    ]}
    relayout_task(task, LayoutRules(lang="en"))
    assert task["segments"][0].get("lines")
    assert "lines" not in task["segments"][1] or task["segments"][1].get("locked")
    for line in task["segments"][0]["lines"]:
        assert len(line) <= DEFAULT_MAX_CHARS
