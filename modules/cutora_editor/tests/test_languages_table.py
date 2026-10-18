# -*- coding: utf-8 -*-
"""T12b 提交 1：languages.json 的 script 必须说真话。先写、先红。"""
from __future__ import annotations

import json
import os

from cutora.asr.languages import (
    LINE_BREAKS,
    SCRIPT_ENUM,
    catalog_by_code,
    common_codes,
    languages_json_path,
    load_catalog,
)

RTL_OR_ARABIC = ("ar", "he", "fa", "ur", "ps", "sd")


def test_script_enum_and_line_break_values():
    rows = load_catalog()
    assert rows, "languages.json 为空"
    for row in rows:
        assert row["script"] in SCRIPT_ENUM, (row["code"], row["script"])
        assert row["line_break"] in LINE_BREAKS, (row["code"], row["line_break"])
        if row["line_break"] == "space":
            assert row["experimental"] is False, row["code"]
        else:
            assert row["experimental"] is True, row["code"]


def test_arabic_hebrew_persian_not_latin():
    by_code = catalog_by_code()
    for code in RTL_OR_ARABIC:
        script = by_code[code]["script"]
        assert script in ("arabic", "hebrew"), (code, script)
        assert script != "latin", code
        assert by_code[code]["line_break"] == "fallback"
        assert by_code[code]["experimental"] is True


def test_cjk_scripts_are_han_kana_hangul():
    by_code = catalog_by_code()
    assert by_code["zh"]["script"] == "han"
    assert by_code["ja"]["script"] == "kana"
    assert by_code["ko"]["script"] == "hangul"
    for code in ("zh", "ja", "ko"):
        assert by_code[code]["line_break"] == "fallback"
        assert by_code[code]["experimental"] is True


def test_greek_armenian_georgian_are_supported():
    by_code = catalog_by_code()
    assert by_code["el"]["script"] == "greek"
    assert by_code["hy"]["script"] == "armenian"
    assert by_code["ka"]["script"] == "georgian"
    for code in ("el", "hy", "ka"):
        assert by_code[code]["line_break"] == "space"
        assert by_code[code]["experimental"] is False


def test_common_codes_exist_and_are_supported():
    by_code = catalog_by_code()
    common = common_codes()
    assert common == ["en", "it", "es", "fr", "de", "pt", "ru", "nl", "pl"]
    for code in common:
        assert code in by_code, code
        assert by_code[code]["experimental"] is False, code
        assert by_code[code]["line_break"] == "space", code


def test_raw_json_has_line_break_on_every_row():
    path = languages_json_path()
    assert os.path.isfile(path), path
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["common"] == ["en", "it", "es", "fr", "de", "pt", "ru", "nl", "pl"]
    for row in data["languages"]:
        assert "line_break" in row, row.get("code")
        assert row["script"] in SCRIPT_ENUM, (row.get("code"), row.get("script"))
