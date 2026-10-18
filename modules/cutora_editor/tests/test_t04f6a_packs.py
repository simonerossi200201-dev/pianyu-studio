# -*- coding: utf-8 -*-
"""T04f-6a: 14 packs, no karaoke gold on 极简白/字幕条, chant no glow, feather migrate."""
from __future__ import annotations

import copy
import json
import os

from cutora.core.migrate_user_data import migrate_feather_to_fade
from cutora.model.project_io import _normalize_project_data
from cutora.render.textfx.spec import collect_pure_black, is_pure_black
from cutora.subtitle.stylepack import PACK_OFFSET_Y_PCT, list_pack_dirs, load_pack

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(REPO, "resources", "stylepacks")

EXPECTED_IDS = {
    "glass-white", "minimal-white", "bar-black", "amber-karaoke", "chant",
    "podcast-card", "pop-word", "duo-pop", "handwrite-duo", "neon-cyan",
    "chunky-3d", "chat-bubbles", "paper-torn", "lyrics-scroll",
}
NO_KARAOKE_GOLD = ("minimal-white", "bar-black")


def _builtin_ids():
    return {
        os.path.basename(p)
        for p in list_pack_dirs()
        if "resources" in p.replace("\\", "/")
    }


def test_fourteen_builtin_packs():
    ids = _builtin_ids()
    assert ids == EXPECTED_IDS, (sorted(ids), sorted(EXPECTED_IDS))
    for name in sorted(ids):
        pack = load_pack(os.path.join(PACK_DIR, name))
        assert pack["version"] >= 2, name
        assert pack["preview"] and os.path.isfile(pack["preview"]), name
        body = json.loads(open(
            os.path.join(PACK_DIR, name, "style.json"), encoding="utf-8").read())
        st = body.get("style") or {}
        assert st.get("offset_y_pct") is not None, name
        assert float(st.get("fontsize_pct") or 0) >= 0.05, name
        for field in ("color_text", "color_outline", "color_bg"):
            assert not is_pure_black(st.get(field)), (name, field, st.get(field))
        hits = collect_pure_black(body)
        assert hits == [], (name, hits)


def test_minimal_and_bar_have_no_karaoke_gold():
    for name in NO_KARAOKE_GOLD:
        pack = load_pack(os.path.join(PACK_DIR, name))
        st = pack.get("style") or {}
        assert st.get("color_high") == st.get("color_text"), name


def test_chant_still_v2_without_glow():
    pack = load_pack(os.path.join(PACK_DIR, "chant"))
    body = json.loads(open(
        os.path.join(PACK_DIR, "chant", "style.json"), encoding="utf-8").read())
    assert int(body.get("version") or 1) >= 2
    fx = (pack.get("style") or {}).get("fx") or {}
    assert float((fx.get("glow") or {}).get("sigma_em") or 0) == 0


def test_feather_migrate_writes_legacy_and_fade():
    data = {
        "tasks": [{
            "timeline": {"width": 1080, "height": 1920, "tracks": [{
                "clips": [{"feather": 54, "w": 1080, "h": 1920}],
            }]},
            "image_overlays": [{
                "elements": [{
                    "kind": "image", "w": 400, "h": 200, "feather": 40,
                }],
            }],
        }],
    }
    migrate_feather_to_fade(data)
    el = data["tasks"][0]["image_overlays"][0]["elements"][0]
    assert el["feather_legacy"] == 40
    assert el["feather"] == 40
    assert abs(el["fade_top"] - 40 / 200.0) < 1e-9
    assert abs(el["fade_left"] - 40 / 200.0) < 1e-9
    clip = data["tasks"][0]["timeline"]["tracks"][0]["clips"][0]
    assert clip["feather_legacy"] == 54
    assert clip["feather"] == 54
    again = copy.deepcopy(data)
    migrate_feather_to_fade(again)
    assert again["tasks"][0]["image_overlays"][0]["elements"][0]["fade_top"] == el["fade_top"]


def test_feather_absent_project_bytes_unchanged():
    data = {
        "version": 2,
        "tasks": [{
            "timeline": {
                "width": 1080, "height": 1920, "fps": 30,
                "sources": {}, "tracks": [],
            },
            "segments": [],
            "image_overlays": [{"elements": [{"kind": "image", "w": 100, "h": 80}]}],
        }],
    }
    before = json.dumps(data, sort_keys=True)
    migrate_feather_to_fade(data)
    after = json.dumps(data, sort_keys=True)
    assert before == after


def test_normalize_calls_feather_migrate():
    data = {
        "tasks": [{
            "timeline": {"width": 1080, "height": 1920, "tracks": []},
            "image_overlays": [{
                "elements": [{"kind": "image", "w": 300, "h": 300, "feather": 30}],
            }],
        }],
    }
    _normalize_project_data(data)
    el = data["tasks"][0]["image_overlays"][0]["elements"][0]
    assert el.get("feather_legacy") == 30
    assert el.get("fade_top", 0) > 0
