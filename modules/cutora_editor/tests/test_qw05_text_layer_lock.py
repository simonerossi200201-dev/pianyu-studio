# -*- coding: utf-8 -*-
"""QW-05: text-layer preset.style keys equal TEXT_LAYER_CANONICAL_KEYS."""
from __future__ import annotations

from cutora.subtitle.style_schema import (
    TEXT_LAYER_CANONICAL_KEYS,
    TEXT_LAYER_DEFAULTS,
    TEXT_LAYER_LAYOUT_KEYS,
)
from cutora.subtitle.text_preset import (
    fill_text_layer_style,
    overlay_from_preset,
    preset_from_overlay,
    save_preset,
)


def test_canonical_keys_match_defaults():
    assert set(TEXT_LAYER_CANONICAL_KEYS) == set(TEXT_LAYER_DEFAULTS)
    assert set(TEXT_LAYER_LAYOUT_KEYS) == {
        "anchor", "margin_x", "margin_y", "size_pct", "z",
        "box", "scale",     # Q136：整个框 + 整体缩放也进预设
    }


def test_fill_zero_leftover_zero_reset():
    filled = fill_text_layer_style({"color": "#E81CFF", "junk": 1})
    live = set(TEXT_LAYER_CANONICAL_KEYS)
    leftover = sorted(set(filled) - live)
    reset = sorted(live - set(filled))
    assert leftover == []
    assert reset == []
    assert filled["color"] == "#E81CFF"
    assert "junk" not in filled


def test_preset_from_overlay_equality_lock(tmp_path, monkeypatch):
    from cutora.subtitle import text_preset as TP
    monkeypatch.setattr(TP, "_user_path", lambda: str(tmp_path / "text_presets.json"))
    monkeypatch.setattr(TP, "_legacy_path", lambda: str(tmp_path / "legacy.json"))
    TP.reset_runtime_state_for_tests()

    ov = {
        "type": "text",
        "content": "Hi",
        "x": 80, "y": 120, "w": 480, "h": 80,
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "italic": False,
        "color": "#E81CFF",
        "use_stroke": True,
        "stroke_width": 10,
        "stroke_color": "#00E5FF",
        "bg_enabled": True,
        "bg_color": "#FF00AA",
        "bg_opacity": 200,
        "bg_radius": 6,
        "opacity": 240,
        "rotation": 12,
        "shadow_enabled": True,
        "shadow_blur": 8,
        "anim_in_type": "fade",
        "anim_in_duration": 0.4,
    }
    row = preset_from_overlay(ov, 1080, 1920, name="锁测试")
    live = set(TEXT_LAYER_CANONICAL_KEYS)
    leftover = sorted(set(row["style"]) - live)
    reset = sorted(live - set(row["style"]))
    assert leftover == [], leftover
    assert reset == [], reset
    assert set(row["style"]) == live
    assert set(row["layout"]) == set(TEXT_LAYER_LAYOUT_KEYS)
    assert row["style"]["color"] == "#E81CFF"
    assert row["style"]["font_family"] == "Arimo"
    assert row["style"]["fontsize"] == 72
    assert row["style"]["stroke_width"] == 10
    assert row["style"]["use_stroke"] is True
    assert row["style"]["rotation"] == 12
    assert row["style"]["opacity"] == 240

    saved = save_preset(row)
    assert set(saved["style"]) == live
    assert set(saved["layout"]) == set(TEXT_LAYER_LAYOUT_KEYS)

    back = overlay_from_preset(saved, 1080, 1920)
    assert back["color"] == "#E81CFF"
    assert back["font_family"] == "Arimo"
    assert back["stroke_width"] == 10
    assert back["use_stroke"] is True
    assert back["rotation"] == 12
    assert back["opacity"] == 240


def test_builtin_credit_fills_canonical():
    from cutora.subtitle.text_preset import get_preset
    row = get_preset("credit.default")
    assert row is not None
    assert set(row["style"]) == set(TEXT_LAYER_CANONICAL_KEYS)
    assert set(row["layout"]) == set(TEXT_LAYER_LAYOUT_KEYS)
    assert row["style"]["font_family"]
    assert row["style"]["color"]
