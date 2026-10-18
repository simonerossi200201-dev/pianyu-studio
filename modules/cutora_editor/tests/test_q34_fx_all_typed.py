# -*- coding: utf-8 -*-
"""Q34-2: every FX_KEYS name is a typed FxSpec field; unknown subkeys warn once."""
from __future__ import annotations

from dataclasses import fields

from cutora.render.textfx import spec as specmod
from cutora.render.textfx.spec import FX_KEYS, FxSpec, from_style


def test_q34_fx_all_typed(caplog):
    names = {f.name for f in fields(FxSpec)}
    missing = sorted(k for k in FX_KEYS if k not in names)
    assert missing == [], "FX_KEYS missing on FxSpec: %s" % missing

    spec = from_style({
        "fx": {
            "follow_word": True,
            "follow_sentence": True,
            "box_highlight": {
                "color": "#22D3EE",
                "alpha": 0.7,
                "pad_em": 0.12,
                "offset_y_em": 0.0,
                "radius_em": 0.2,
                "stroke_width": 0.0,
                "glow_radius": 0.0,
                "overshoot": 1.3,
                "duration": 0.15,
            },
        }
    })
    assert spec.follow_word
    assert spec.follow_word.tint.enabled
    assert spec.follow_word.box.enabled
    assert spec.follow_sentence is True
    bh = spec.box_highlight
    assert bh.color == "#22D3EE"
    assert abs(bh.alpha - 0.7) < 1e-6
    assert abs(bh.pad_em - 0.12) < 1e-6
    assert abs(bh.radius_em - 0.2) < 1e-6
    assert abs(bh.overshoot - 1.3) < 1e-6
    assert abs(bh.duration - 0.15) < 1e-6
    assert "box_highlight" in spec.anim.loop
    assert "line_bar" in spec.anim.loop

    specmod._WARNED.clear()
    caplog.clear()
    from_style({"fx": {"box_highlight": {"nope": 1, "color": "#22D3EE"}}})
    assert "unknown fx key" in caplog.text
    assert "box_highlight.nope" in caplog.text
    caplog.clear()
    from_style({"fx": {"box_highlight": {"nope": 1, "color": "#22D3EE"}}})
    assert "unknown fx key" not in caplog.text
