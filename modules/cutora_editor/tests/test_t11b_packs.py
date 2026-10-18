# -*- coding: utf-8 -*-
"""T11b: LIVE is derived; 14 packs equal LIVE; leftover=0 and reset=0."""
from __future__ import annotations

import os

from cutora.subtitle.style_schema import (
    CANONICAL_STYLE_KEYS,
    DEPRECATED_UNPAINTED_KEYS,
    LIVE_CANONICAL_KEYS,
    PACK_STYLE_EXTRAS,
)
from cutora.subtitle.stylepack import (
    PACK_OFFSET_Y_PCT,
    apply_pack_to_task,
    builtin_dir,
    list_pack_dirs,
    load_pack,
)


def _builtin_paths():
    root = builtin_dir().replace("\\", "/")
    return [
        p for p in list_pack_dirs()
        if p.replace("\\", "/").startswith(root)
    ]


def test_live_canonical_is_canonical_minus_deprecated():
    assert set(LIVE_CANONICAL_KEYS) == (
        set(CANONICAL_STYLE_KEYS) - DEPRECATED_UNPAINTED_KEYS
    )
    assert LIVE_CANONICAL_KEYS == tuple(
        k for k in CANONICAL_STYLE_KEYS if k not in DEPRECATED_UNPAINTED_KEYS
    )


def test_deprecated_disjoint_from_canonical():
    assert set(DEPRECATED_UNPAINTED_KEYS) & set(CANONICAL_STYLE_KEYS) == set()


def test_deprecated_unpainted_not_canonical():
    root = os.path.join(os.path.dirname(__file__), "..", "cutora", "render", "textfx")
    blob = ""
    for name in ("raster.py", "word_fx.py", "pipeline.py", "spec.py"):
        blob += open(os.path.join(root, name), encoding="utf-8").read()
    for k in DEPRECATED_UNPAINTED_KEYS:
        assert k not in CANONICAL_STYLE_KEYS
        assert k not in LIVE_CANONICAL_KEYS
        assert k not in blob, k


def test_lyrics_seg_style_has_no_dead_v1_writes():
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "render", "frame_renderer.py"),
        encoding="utf-8",
    ).read()
    chunk = src.split("Build per-segment style for draw_subtitle", 1)[1][:800]
    assert "use_box" not in chunk
    assert "anim_in_type" not in chunk
    assert "box_padding_x" not in chunk


def test_pack_json_has_no_deprecated_keys():
    import json
    for path in _builtin_paths():
        body = json.loads(open(
            os.path.join(path, "style.json"), encoding="utf-8").read())
        for section in ("style", "animation"):
            blob = body.get(section) or {}
            if not isinstance(blob, dict):
                continue
            hits = sorted(k for k in blob if k in DEPRECATED_UNPAINTED_KEYS)
            assert hits == [], (os.path.basename(path), section, hits)


def test_default_style_has_no_deprecated_keys():
    from cutora.subtitle.stylepack import default_subtitle_style
    d = default_subtitle_style()
    hits = sorted(k for k in d if k in DEPRECATED_UNPAINTED_KEYS)
    assert hits == []


def test_pack_declares_every_live_canonical_key():
    live = set(LIVE_CANONICAL_KEYS)
    extra = set(PACK_STYLE_EXTRAS)
    for path in _builtin_paths():
        pack = load_pack(path)
        st = dict(pack.get("style") or {})
        st.update(pack.get("animation") or {})
        declared = set(st) - extra
        reset = sorted(live - set(st))
        extra_keys = sorted(declared - live)
        assert declared == live, (pack.get("id"), reset, extra_keys)
        assert reset == [], (pack.get("id"), reset)
        for dead in DEPRECATED_UNPAINTED_KEYS:
            assert dead not in st, (pack.get("id"), dead)


def test_apply_pack_canonical_and_zero_leftover():
    live = set(LIVE_CANONICAL_KEYS)
    extra = set(PACK_STYLE_EXTRAS)
    for path in _builtin_paths():
        pack = load_pack(path)
        task = {"style": {"legacy_junk": 1}, "dims": [1080, 1920], "segments": []}
        apply_pack_to_task(task, pack)
        style = task["style"]
        reset = sorted(live - set(style))
        leftover = sorted(set(style) - live - extra)
        assert set(style) - extra == live, (pack.get("id"), reset, leftover)
        assert reset == [], (pack.get("id"), reset)
        assert leftover == [], (pack.get("id"), leftover)
        assert "legacy_junk" not in style
        assert style.get("wrap_lines") is not None
        assert pack.get("style", {}).get("offset_y_pct") is not None


def test_new_pack_offset_default_is_constant():
    assert PACK_OFFSET_Y_PCT == -0.42
    for path in _builtin_paths():
        pack = load_pack(path)
        st = pack.get("style") or {}
        assert st.get("offset_y_pct") is not None, pack.get("id")
        assert st.get("wrap_lines") is not None, pack.get("id")
