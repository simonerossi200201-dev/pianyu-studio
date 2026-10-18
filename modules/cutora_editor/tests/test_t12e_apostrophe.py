# -*- coding: utf-8 -*-
"""T12e 提交 3：匹配前统一撇号/连字符；原文一字不改。"""
from __future__ import annotations

from cutora.batch.script_stitcher import match_clip_global, tokenize_raw_pairs

STRAIGHT = "dell'anima"          # U+0027
CURLY = "dell\u2019anima"        # U+2019
HYPHEN_ASCII = "well-known"
HYPHEN_NB = "well\u2011known"    # U+2011 non-breaking hyphen


def test_curly_vs_straight_apostrophe_matches():
    clip = [(CURLY, 0.0, 0.5)]
    m = match_clip_global(clip, [STRAIGHT])
    assert m["hit"] >= 1
    assert m["matched"] is True


def test_hyphen_variants_match():
    clip = [(HYPHEN_NB, 0.0, 0.5)]
    m = match_clip_global(clip, [HYPHEN_ASCII])
    assert m["hit"] >= 1
    assert m["matched"] is True


def test_match_normalise_does_not_rewrite_source_text():
    clip = [(CURLY, 0.0, 0.5)]
    script = [STRAIGHT]
    match_clip_global(clip, script)
    assert clip[0][0] == CURLY
    assert script[0] == STRAIGHT
    text = "dell'anima — il cuore"
    _toks, raws = tokenize_raw_pairs(text)
    assert "dell'anima" in raws
    assert "—" in text
    assert text == "dell'anima — il cuore"
