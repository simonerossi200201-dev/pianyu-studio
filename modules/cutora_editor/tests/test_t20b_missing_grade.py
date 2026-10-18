# -*- coding: utf-8 -*-
"""T20b: ≥3 missing / 1–2 suspected with context."""
from __future__ import annotations

from cutora.batch.script_stitcher import MISSING_HOLE, find_missing


def _items(covered):
    return [{"keep": True, "s_runs": covered}]


def test_three_word_hole_is_missing():
    toks = ["a", "b", "c", "d", "e", "f"]
    raws = list(toks)
    # cover 0-1 and 5, hole 2-4 = 3 tokens
    got = find_missing(_items([(0, 2), (5, 6)]), toks, raws,
                       ignore_scripture_refs=False)
    assert len(got) == 1
    assert got[0]["grade"] == "missing"
    assert got[0]["text"] == "c d e"


def test_two_word_hole_is_suspected_not_missing():
    toks = ["grace", "mercy", "and", "freedom", "so"]
    raws = list(toks)
    suspected = []
    # cover 0-2 and 4, hole "and freedom" = 2
    got = find_missing(_items([(0, 2), (4, 5)]), toks, raws,
                       ignore_scripture_refs=False, suspected=suspected)
    assert got == []
    assert len(suspected) == 1
    assert suspected[0]["grade"] == "suspected"
    assert suspected[0]["text"] == "and freedom"
    assert "mercy" in suspected[0]["context_before"]
    assert "so" in suspected[0]["context_after"]


def test_threshold_constant_is_three():
    assert MISSING_HOLE == 3
