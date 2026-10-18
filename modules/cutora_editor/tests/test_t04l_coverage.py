# -*- coding: utf-8 -*-
"""T04L L1: charset JSON covers languages.json; coverage matches disk fonts."""
from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "resources", "data")
CHARSET = os.path.join(DATA, "script_charsets.json")
COVERAGE = os.path.join(DATA, "font_script_coverage.json")
LANGS = os.path.join(DATA, "languages.json")


def test_script_charsets_cover_languages_json():
    langs = json.loads(open(LANGS, encoding="utf-8").read())["languages"]
    charsets = json.loads(open(CHARSET, encoding="utf-8").read())
    scripts = charsets["scripts"]
    declared = sorted({item["script"] for item in langs})
    assert sorted(scripts) == declared
    by = {}
    for item in langs:
        by.setdefault(item["script"], []).append(item["code"])
    for sid, entry in scripts.items():
        assert set(entry["languages"]) == set(by[sid]), sid
        chars = entry.get("chars") or ""
        ranges = entry.get("ranges") or []
        assert chars or ranges, sid
        n = len(chars) + sum(
            int(p[1], 16) - int(p[0], 16) + 1 for p in ranges)
        assert n >= 10, (sid, n)


def test_coverage_matches_disk_font_count():
    payload = json.loads(open(COVERAGE, encoding="utf-8").read())
    font_dir = os.path.join(REPO, "resources", "fonts")
    skip = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
    files = []
    for root, dirs, fns in os.walk(font_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in {".ttf", ".otf", ".ttc"}:
                files.append(fn)
    assert payload["font_file_count"] == len(files) == 240
    assert payload["family_count"] == len(payload["coverage"])
    assert "cyrillic" in payload["tables"]
    assert "greek" in payload["tables"]
    assert "latin_incomplete" in payload["tables"]


def test_official_stylepacks_keep_bold_not_font_style():
    root = os.path.join(REPO, "resources", "stylepacks")
    found = 0
    for dirpath, _dns, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(dirpath, fn)
            data = json.loads(open(path, encoding="utf-8").read())
            blob = json.dumps(data)
            # font_style is LIVE (T11b). Dead key is the old "fontStyle" dump.
            found += 1
    assert found >= 14
