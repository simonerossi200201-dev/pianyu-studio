# -*- coding: utf-8 -*-
"""T04L L4: subtitle_tier + shaping scripts cannot use char/word anim."""
from __future__ import annotations

from cutora.render.textfx.spec import from_style
from cutora.subtitle.font_coverage import SHAPING_SCRIPTS


def test_languages_have_subtitle_tier():
    from cutora.asr.languages import load_catalog, picker_label
    rows = load_catalog()
    assert len(rows) >= 90
    tiers = {r["subtitle_tier"] for r in rows}
    assert tiers <= {"complete", "font_only"}
    assert "complete" in tiers and "font_only" in tiers
    fr = next(r for r in rows if r["code"] == "fr")
    assert fr["subtitle_tier"] == "complete"
    assert "完善" in picker_label(fr)
    ar = next(r for r in rows if r["code"] == "ar")
    assert ar["subtitle_tier"] == "font_only"
    assert "仅字形保证" in picker_label(ar)


def test_shaping_script_coerces_char_and_word_units():
    assert "arabic" in SHAPING_SCRIPTS
    assert "devanagari" in SHAPING_SCRIPTS
    style = {
        "font_family": "Noto Sans Arabic",
        "script": "arabic",
        "fx": {"anim": {"in": "flip_clock", "unit": "char"}},
    }
    spec = from_style(style)
    assert spec.anim.unit not in ("char", "word")
    style2 = {
        "font_family": "Noto Sans Devanagari",
        "script": "devanagari",
        "fx": {"anim": {"in": "stomp", "unit": "word"}},
    }
    spec2 = from_style(style2)
    assert spec2.anim.unit not in ("char", "word")
    latin = from_style({
        "font_family": "Anton",
        "script": "latin",
        "fx": {"anim": {"in": "stomp", "unit": "word"}},
    })
    assert latin.anim.unit == "word"
