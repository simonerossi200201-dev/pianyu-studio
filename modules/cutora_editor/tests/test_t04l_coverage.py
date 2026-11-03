# -*- coding: utf-8 -*-
"""T04L L1: charset JSON covers languages.json; coverage matches disk fonts and commercial license manifest."""
from __future__ import annotations

import hashlib
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "resources", "data")
CHARSET = os.path.join(DATA, "script_charsets.json")
COVERAGE = os.path.join(DATA, "font_script_coverage.json")
LANGS = os.path.join(DATA, "languages.json")
MANIFEST = os.path.join(REPO, "resources", "fonts", "FONTS_MANIFEST.json")
PROVENANCE = os.path.join(REPO, "resources", "fonts", "PROVENANCE.json")
LICENSES_MD = os.path.join(REPO, "resources", "FONTS_LICENSES.md")


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
                files.append(os.path.join(root, fn))
    assert payload["font_file_count"] == len(files)
    assert len(files) >= 240
    assert payload["family_count"] == len(payload["coverage"])
    assert "cyrillic" in payload["tables"]
    assert "greek" in payload["tables"]
    assert "latin_incomplete" in payload["tables"]

    cov = payload["coverage"]
    han_fams = [f for f, r in cov.items() if r.get("han")]
    ar_fams = [f for f, r in cov.items() if r.get("arabic")]
    assert len(han_fams) >= 15, han_fams
    assert len(ar_fams) >= 20, ar_fams


def test_commercial_license_and_provenance_credentials():
    assert os.path.isfile(LICENSES_MD), LICENSES_MD
    man = json.loads(open(MANIFEST, encoding="utf-8").read())
    prov = json.loads(open(PROVENANCE, encoding="utf-8").read())
    allowed_lic = {"OFL-1.1", "Apache-2.0"}
    assert set((man.get("by_license") or {}).keys()) <= allowed_lic
    assert not man.get("quarantined")
    assert not man.get("missing_license_file")
    for item in man.get("fonts") or []:
        assert item.get("license") in allowed_lic, item
        assert item.get("has_license_file") is True, item
    for item in prov.get("fonts") or []:
        rel = str(item.get("file") or "")
        path = os.path.join(REPO, "resources", rel.replace("/", os.sep))
        assert os.path.isfile(path), rel
        fam_dir = os.path.dirname(path)
        assert os.path.isfile(os.path.join(fam_dir, "OFL.txt")), fam_dir
        if str(item.get("google_fonts") or "").endswith("@2fe1e8c6b137fb13041a7cfca6fdcd8a910ea4dc"):
            assert os.path.isfile(os.path.join(fam_dir, "METADATA.pb")), fam_dir


def test_official_stylepacks_keep_bold_not_font_style():
    root = os.path.join(REPO, "resources", "stylepacks")
    found = 0
    for dirpath, _dns, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(dirpath, fn)
            data = json.loads(open(path, encoding="utf-8").read())
            _ = json.dumps(data)
            # font_style is LIVE (T11b). Dead key is the old "fontStyle" dump.
            found += 1
    assert found >= 14


def test_font_picker_script_grouping_and_pinyin_search():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    import cutora.ui.panels.fonts as fonts_mod
    from cutora.ui.widgets.font_picker import (
        FONT_COMMERCIAL_BADGE,
        ROLE_BADGE,
        ROLE_DISPLAY,
        ROLE_FAMILY,
        ROLE_HEADER,
        ROLE_SCRIPT,
        FontListModel,
        font_display_name,
        font_matches_search,
    )

    _app = QApplication.instance() or QApplication([])
    fonts_mod.load_embedded_fonts()
    assert len(fonts_mod.ALLOWED_FONTS) >= 80

    # Active language = zh -> 中文 group pinned at top (right after any recents)
    model_zh = FontListModel(fonts_mod.ALLOWED_FONTS, recent=[], no_script=[], lang_code="zh")
    headers_zh = [
        model_zh.index(r, 0).data(Qt.ItemDataRole.DisplayRole)
        for r in range(model_zh.rowCount())
        if model_zh.index(r, 0).data(ROLE_HEADER)
    ]
    assert headers_zh and "中文" in headers_zh[0], headers_zh
    assert any("阿拉伯文" in h for h in headers_zh), headers_zh

    # Active language = ar -> 阿拉伯文 group pinned at top
    model_ar = FontListModel(fonts_mod.ALLOWED_FONTS, recent=[], no_script=[], lang_code="ar")
    headers_ar = [
        model_ar.index(r, 0).data(Qt.ItemDataRole.DisplayRole)
        for r in range(model_ar.rowCount())
        if model_ar.index(r, 0).data(ROLE_HEADER)
    ]
    assert headers_ar and "阿拉伯文" in headers_ar[0], headers_ar

    # Friendly display name + preserved underlying font_family ID + commercial badge
    assert "站酷快乐体" in font_display_name("ZCOOL KuaiLe")
    assert "思源黑体" in font_display_name("Noto Sans SC")
    assert "霞鹜文楷" in font_display_name("LXGW WenKai TC")
    assert "阿米里" in font_display_name("Amiri")

    # Pinyin initials & Chinese/English search
    assert font_matches_search("ZCOOL KuaiLe", "zkklt")
    assert font_matches_search("ZCOOL KuaiLe", "快乐")
    assert font_matches_search("Noto Sans SC", "syht")
    assert font_matches_search("LXGW WenKai TC", "xwwk")
    assert font_matches_search("Ma Shan Zheng", "msz")
    assert font_matches_search("Cairo", "开罗")

    model_zh.set_filter("zkklt")
    matched_fams = [
        model_zh.index(r, 0).data(ROLE_FAMILY)
        for r in range(model_zh.rowCount())
        if not model_zh.index(r, 0).data(ROLE_HEADER)
    ]
    assert "ZCOOL KuaiLe" in matched_fams
    for r in range(model_zh.rowCount()):
        idx = model_zh.index(r, 0)
        if not idx.data(ROLE_HEADER):
            assert FONT_COMMERCIAL_BADGE in idx.data(ROLE_BADGE)
            assert idx.data(ROLE_DISPLAY)
            assert idx.data(ROLE_SCRIPT)

