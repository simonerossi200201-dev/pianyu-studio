# -*- coding: utf-8 -*-
"""T04c: LCS remap, overflow flag, case/split/glossary (no Qt)."""
from __future__ import annotations

from cutora.subtitle.edit import (
    apply_set_text,
    apply_pairs,
    find_in_text,
    lcs_old_index,
    merge_pair,
    remap_words,
    replace_in_text,
    split_segment,
    strip_end_punct,
    transform_case,
)
from cutora.subtitle.layout import DEFAULT_MAX_CHARS, DEFAULT_MAX_LINES, is_overlong


def test_lcs_keeps_timing_for_typo_fix():
    old = [
        {"word": "Hello", "start": 0.0, "end": 0.4, "wid": 0},
        {"word": "frends.", "start": 0.5, "end": 1.0, "wid": 1},
    ]
    new = remap_words(old, "Hello friends.", 0.0, 1.0)
    assert new[0]["word"] == "Hello"
    assert new[0]["start"] == 0.0
    assert new[0]["end"] == 0.4
    assert new[0]["wid"] == 0
    assert new[1]["word"] == "friends."
    assert new[1]["start"] >= 0.4
    assert new[1]["end"] <= 1.0 + 1e-6


def test_lcs_inserts_interpolated_word():
    old = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "town.", "start": 0.8, "end": 1.0},
    ]
    new = remap_words(old, "Hello new town.", 0.0, 1.0)
    words = [w["word"] for w in new]
    assert words == ["Hello", "new", "town."]
    assert new[0]["start"] == 0.0
    mapped = lcs_old_index(["Hello", "town."], ["Hello", "new", "town."])
    assert mapped[0] == 0
    assert mapped[1] is None
    assert mapped[2] == 1


def test_set_text_locks_and_drops_lines():
    seg = {
        "start": 0.0, "end": 1.0, "text": "Hello friends.",
        "words": [{"word": "Hello", "start": 0.0, "end": 0.4}],
        "lines": ["Hello friends."],
        "locked": False,
    }
    apply_set_text(seg, "Hello friend.")
    assert seg["locked"] is True
    assert "lines" not in seg
    assert seg["text"] == "Hello friend."


def test_overlong_uses_max_chars_times_max_lines():
    short = {"text": "x" * 84}
    long = {"text": "x" * 85}
    assert is_overlong(short, 42, 2) is False
    assert is_overlong(long, 42, 2) is True
    assert DEFAULT_MAX_CHARS * DEFAULT_MAX_LINES == 84


def test_case_and_end_punct_and_replace():
    assert transform_case("Hello there.", "upper") == "HELLO THERE."
    assert transform_case("HELLO THERE.", "lower") == "hello there."
    assert transform_case("hello there.", "title") == "Hello There."
    assert strip_end_punct("Hello there...") == "Hello there"
    assert replace_in_text("The cat sat", "cat", "dog") == "The dog sat"
    spans = find_in_text("Ab ab", "ab", regex=False, case=False)
    assert len(spans) == 2


def test_split_at_char_uses_ratio():
    seg = {
        "start": 0.0, "end": 10.0,
        "text": "Hello world today",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 3.0},
            {"word": "world", "start": 3.0, "end": 6.0},
            {"word": "today", "start": 6.0, "end": 10.0},
        ],
    }
    left, right = split_segment(seg, at_char=6)
    assert left["text"] == "Hello"
    assert right["text"].startswith("world")
    assert left["end"] == right["start"]
    assert 0.0 < left["end"] < 10.0
    assert left["locked"] and right["locked"]


def test_merge_pair_concatenates():
    a = {"start": 0.0, "end": 1.0, "text": "Hello", "words": [{"word": "Hello", "start": 0.0, "end": 1.0}]}
    b = {"start": 1.0, "end": 2.0, "text": "there.", "words": [{"word": "there.", "start": 1.0, "end": 2.0}]}
    m = merge_pair(a, b)
    assert m["text"] == "Hello there."
    assert m["start"] == 0.0 and m["end"] == 2.0
    assert len(m["words"]) == 2
    assert m["locked"] is True


def test_glossary_longest_first():
    text = apply_pairs("New York City", [("New York City", "NYC"), ("York", "YRK")])
    assert text == "NYC"
