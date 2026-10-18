# -*- coding: utf-8 -*-
"""BUG-19: line_bar only when follow_sentence; not in loop dropdown; packs off."""
from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_line_bar_not_in_loop_dropdown():
    from cutora.render.textfx.anim import phase_anim_items
    ids = [v for _lab, v in phase_anim_items("loop")]
    assert "line_bar" not in ids
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
        encoding="utf-8").read()
    assert "（整句底条）" in src


def test_from_style_strips_loop_line_bar_unless_follow():
    from cutora.render.textfx.spec import from_style
    from cutora.render.textfx.anim import _anim_ids
    spec = from_style({"fx": {"anim": {"loop": "line_bar"}}})
    assert "line_bar" not in _anim_ids(spec.anim.loop)
    spec2 = from_style({"fx": {"follow_sentence": True, "anim": {"loop": "none"}}})
    assert "line_bar" in _anim_ids(spec2.anim.loop)


def test_packs_and_defaults_follow_sentence_off():
    from cutora.subtitle.stylepack import builtin_dir, list_pack_dirs, load_pack
    from cutora.subtitle.stylepack import default_subtitle_style
    root = builtin_dir().replace("\\", "/")
    for path in list_pack_dirs():
        if not path.replace("\\", "/").startswith(root):
            continue
        pack = load_pack(path)
        st = dict(pack.get("style") or {})
        fx = st.get("fx") if isinstance(st.get("fx"), dict) else {}
        anim = fx.get("anim") if isinstance(fx.get("anim"), dict) else {}
        loop = str(anim.get("loop") or "")
        assert not fx.get("follow_sentence"), pack.get("id")
        assert "line_bar" not in loop.split("+"), pack.get("id")
    d = default_subtitle_style()
    fx = d.get("fx") if isinstance(d.get("fx"), dict) else {}
    assert not fx.get("follow_sentence")
    golden = os.path.join(REPO, "tests", "golden")
    for name in os.listdir(golden):
        proj = os.path.join(golden, name, "project.asproj.json")
        if not os.path.isfile(proj):
            continue
        blob = open(proj, encoding="utf-8").read()
        assert "line_bar" not in blob, name
        data = json.loads(blob)
        for task in data.get("tasks") or []:
            st = task.get("style") or {}
            fx = st.get("fx") if isinstance(st.get("fx"), dict) else {}
            assert not fx.get("follow_sentence"), name
