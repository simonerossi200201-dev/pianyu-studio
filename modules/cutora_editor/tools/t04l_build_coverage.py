# -*- coding: utf-8 -*-
"""T04L L1: write script_charsets.json (if building) and font_script_coverage.json.

Charset JSON is the source of truth. Coverage is cmap-scanned from the same
215 files load_embedded_fonts walks (skip _quarantine / _extra / fonts_pending
/ Noto_Color_Emoji).
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "resources", "data")
FONT_DIR = os.path.join(REPO, "resources", "fonts")
CHARSET_PATH = os.path.join(DATA, "script_charsets.json")
COVERAGE_PATH = os.path.join(DATA, "font_script_coverage.json")
LANG_PATH = os.path.join(DATA, "languages.json")
SKIP_DIRS = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}


def _cp(s: str) -> set[str]:
    return set(s)


def _range(a: str, b: str) -> set[str]:
    return {chr(i) for i in range(int(a, 16), int(b, 16) + 1)}


def _ascii_letters() -> str:
    return "".join(chr(i) for i in list(range(0x41, 0x5B)) + list(range(0x61, 0x7B)))


def _digits_punct() -> str:
    return "0123456789.,!?'\"-:;()[] "


def build_charsets(languages: list[dict]) -> dict:
    by = defaultdict(list)
    for item in languages:
        by[item["script"]].append(item["code"])

    latin_basic = _ascii_letters() + _digits_punct()
    # Diagnostic subset: Latin Extended-A block (not the completeness bar).
    latin_ext_a = "".join(chr(i) for i in range(0x0100, 0x0180))
    # Union of alphabets for the 56 latin languages in languages.json.
    # Not the whole Ext-A block — ƀƁ etc. are not required by any declared lang.
    latin_lang = (
        "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
        "ŒœŸĂÂÊÔƠƯăâêôơư"
        "ÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ"
        "áàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ"
        "ẤẦẨẪẬẮẰẲẴẶẾỀỂỄỆỐỒỔỖỘỚỜỞỠỢỨỪỬỮỰ"
        "ấầẩẫậắằẳẵặếềểễệốồổỗộớờởỡợứừửữự"
        "ĐđĂăĄąĆćČčĎďĚěĘęĞğĢģĦħİıĮįĶķĹĺĻļŁłŃńŇňŅņŐőŔŕŘřŚśŠšŞşȘșŢţŤťŲųŮůŰűŹźŻżŽž"
        "ƏəƓɓɗƙƴƁƊƘƳʻʼ"
        "ĀāĒēĪīŌōŪūŴŵŶŷĊċĠġ"
        "ŐőŰűǺǻǾǿ"
        "¿¡«»·"
    )
    latin_full = latin_basic + latin_lang

    cyr = (
        "".join(chr(i) for i in range(0x0400, 0x0450))
        + "ЀЁЂЃЄЅІЇЈЉЊЋЌЍЎЏАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
        "абвгдежзийклмнопрстуфхцчшщъыьэюяѐёђѓєѕіїјљњћќѝўџ"
        "ҐґҒғҚқҢңҮүҰұҺһӘәӨөІіЎўҖҗҘҙҪҫҠҡӢӣӮӯҲҳҶҷЃѓЌќЏџЈјЅѕ"
        "ЌќЃѓ"
    )
    greek = (
        "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρσςτυφχψω"
        "ΆΈΉΊΌΎΏΐΪΫάέήίΰϊϋόύώ"
    )
    arabic = (
        "ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوىي"
        "پچژگکێڵڕۆەھۍېی"
        "ٹپچڈڑژکگںھہےی"
        "،؛؟ـًٌٍَُِّْٰ"
        "٠١٢٣٤٥٦٧٨٩"
    )
    hebrew = "אבגדהוזחטיךכלםמןנסעףפץצקרשתװױײ׳״"
    armenian = "".join(chr(i) for i in range(0x0531, 0x0557)) + "".join(
        chr(i) for i in range(0x0561, 0x0588))
    georgian = "".join(chr(i) for i in range(0x10D0, 0x10FB))
    devanagari = "".join(chr(i) for i in range(0x0900, 0x097F))
    bengali = "".join(chr(i) for i in range(0x0980, 0x09FF))
    gurmukhi = "".join(chr(i) for i in range(0x0A00, 0x0A7F))
    gujarati = "".join(chr(i) for i in range(0x0A80, 0x0AFF))
    tamil = "".join(chr(i) for i in range(0x0B80, 0x0BFF))
    telugu = "".join(chr(i) for i in range(0x0C00, 0x0C7F))
    kannada = "".join(chr(i) for i in range(0x0C80, 0x0CFF))
    malayalam = "".join(chr(i) for i in range(0x0D00, 0x0D7F))
    sinhala = "".join(chr(i) for i in range(0x0D80, 0x0DFF))
    thai = "".join(chr(i) for i in range(0x0E01, 0x0E5B))
    lao = "".join(chr(i) for i in range(0x0E81, 0x0EDF))
    tibetan = "".join(chr(i) for i in range(0x0F00, 0x0FDA))
    myanmar = "".join(chr(i) for i in range(0x1000, 0x109F))
    ethiopic = "".join(chr(i) for i in range(0x1200, 0x137D))
    khmer = "".join(chr(i) for i in range(0x1780, 0x17F9))
    kana = (
        "".join(chr(i) for i in range(0x3041, 0x3097))
        + "".join(chr(i) for i in range(0x30A1, 0x30FB))
        + "ー、。"
    )
    # Hangul syllables: store as range, expanded at scan time.
    hangul_ranges = [["AC00", "D7A3"]]
    hangul_chars = "한글자막。、"
    # Han: common subtitle punctuation + a 常用字 core (must be in-repo).
    han_punct = "，。、；：？！「」『』（）【】《》—…·"
    han_core = (
        "的一是不了人我在有他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生"
        "自会那后能对着事其里所去行过家十用发天如然作方成者多日都三小军二无同么经法当起与"
        "好看学进种将还分此心前面又定见只主没公从想如实意力理点文月事长机"
        "中國人說話時候看見東西沒有現在已經因為所以"
    )

    packs = {
        "latin": {
            "languages": by["latin"],
            "chars": latin_full,
            "subsets": {
                "latin_basic": latin_basic,
                "latin_ext_a": latin_ext_a,
                "latin_western": (
                    latin_basic
                    + "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜÝàáâãäåæçèéêëìíîïñòóôõöøùúûüýÿ"
                    + "ŒœŸßÁÉÍÓÚÜÑ¿¡ĂÂÊÔăâêô"
                    + "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
                    + "çğıöşüÇĞİÖŞÜß"
                ),
            },
        },
        "cyrillic": {"languages": by["cyrillic"], "chars": cyr},
        "greek": {"languages": by["greek"], "chars": greek},
        "arabic": {"languages": by["arabic"], "chars": arabic},
        "hebrew": {"languages": by["hebrew"], "chars": hebrew},
        "armenian": {"languages": by["armenian"], "chars": armenian},
        "georgian": {"languages": by["georgian"], "chars": georgian},
        "devanagari": {"languages": by["devanagari"], "chars": devanagari},
        "bengali": {"languages": by["bengali"], "chars": bengali},
        "gurmukhi": {"languages": by["gurmukhi"], "chars": gurmukhi},
        "gujarati": {"languages": by["gujarati"], "chars": gujarati},
        "tamil": {"languages": by["tamil"], "chars": tamil},
        "telugu": {"languages": by["telugu"], "chars": telugu},
        "kannada": {"languages": by["kannada"], "chars": kannada},
        "malayalam": {"languages": by["malayalam"], "chars": malayalam},
        "sinhala": {"languages": by["sinhala"], "chars": sinhala},
        "thai": {"languages": by["thai"], "chars": thai},
        "lao": {"languages": by["lao"], "chars": lao},
        "tibetan": {"languages": by["tibetan"], "chars": tibetan},
        "myanmar": {"languages": by["myanmar"], "chars": myanmar},
        "ethiopic": {"languages": by["ethiopic"], "chars": ethiopic},
        "khmer": {"languages": by["khmer"], "chars": khmer},
        "kana": {"languages": by["kana"], "chars": kana},
        "hangul": {
            "languages": by["hangul"],
            "chars": hangul_chars,
            "ranges": hangul_ranges,
        },
        "han": {"languages": by["han"], "chars": han_punct + han_core},
    }
    missing = set(by) - set(packs)
    if missing:
        raise SystemExit("charset missing scripts: %s" % sorted(missing))
    return {
        "version": 1,
        "note": (
            "Required glyphs per languages.json script. "
            "latin is the union of all 56 latin-language alphabets. "
            "han is a subtitle core (punctuation + 常用字), not GB2312-all."
        ),
        "scripts": packs,
    }


def charset_cps(entry: dict) -> set[int]:
    cps: set[int] = set()
    for ch in entry.get("chars") or "":
        cps.add(ord(ch))
    for pair in entry.get("ranges") or []:
        lo = int(pair[0], 16)
        hi = int(pair[1], 16)
        cps.update(range(lo, hi + 1))
    cps.discard(0x20)  # space is in almost every font; don't fail on it
    return cps


def iter_font_files() -> list[str]:
    out = []
    if not os.path.isdir(FONT_DIR):
        return out
    for root, dirs, files in os.walk(FONT_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if os.path.splitext(fn)[1].lower() in FONT_EXTS:
                out.append(os.path.join(root, fn))
    out.sort()
    return out


def font_family_and_cmap(path: str) -> tuple[str, set[int]]:
    from fontTools.ttLib import TTFont
    font = TTFont(path, lazy=True, fontNumber=0)
    try:
        family = path
        name = font["name"]
        for rec in name.names:
            if rec.nameID == 1 and rec.platformID in (1, 3):
                try:
                    family = rec.toUnicode()
                    break
                except Exception:
                    continue
        cps: set[int] = set()
        cmap = font.getBestCmap() or {}
        cps.update(int(k) for k in cmap.keys())
        return family, cps
    finally:
        font.close()


def scan(charsets: dict) -> dict:
    scripts = charsets["scripts"]
    needed = {sid: charset_cps(entry) for sid, entry in scripts.items()}
    subsets = {}
    latin = scripts.get("latin") or {}
    for name, chars in (latin.get("subsets") or {}).items():
        subsets[name] = {ord(c) for c in chars if c != " "}

    by_family: dict[str, set[int]] = defaultdict(set)
    files_by_family: dict[str, list[str]] = defaultdict(list)
    paths = iter_font_files()
    for path in paths:
        family, cps = font_family_and_cmap(path)
        by_family[family] |= cps
        rel = os.path.relpath(path, FONT_DIR).replace("\\", "/")
        files_by_family[family].append(rel)

    coverage = {}
    for family in sorted(by_family):
        have = by_family[family]
        row = {}
        for sid, need in needed.items():
            if not need:
                row[sid] = False
                continue
            missing = need - have
            row[sid] = len(missing) == 0
            row[sid + "_missing"] = len(missing)
            row[sid + "_need"] = len(need)
        for name, need in subsets.items():
            missing = need - have
            row[name] = len(missing) == 0
            row[name + "_missing"] = len(missing)
            row[name + "_need"] = len(need)
        coverage[family] = row

    return {
        "font_file_count": len(paths),
        "family_count": len(by_family),
        "files_by_family": {k: files_by_family[k] for k in sorted(files_by_family)},
        "coverage": coverage,
    }


def tables(payload: dict) -> dict:
    cov = payload["coverage"]
    cyr = sorted(f for f, row in cov.items() if row.get("cyrillic"))
    gre = sorted(f for f, row in cov.items() if row.get("greek"))
    latin_bad = sorted(
        f for f, row in cov.items()
        if not row.get("latin")
    )
    latin_west_bad = sorted(
        f for f, row in cov.items()
        if not row.get("latin_western")
    )
    return {
        "cyrillic": cyr,
        "greek": gre,
        "latin_incomplete": latin_bad,
        "latin_western_incomplete": latin_west_bad,
    }


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    langs = json.load(open(LANG_PATH, encoding="utf-8"))["languages"]
    charsets = build_charsets(langs)
    with open(CHARSET_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(charsets, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", CHARSET_PATH)
    payload = scan(charsets)
    tabs = tables(payload)
    payload["tables"] = {
        "cyrillic_count": len(tabs["cyrillic"]),
        "greek_count": len(tabs["greek"]),
        "latin_incomplete_count": len(tabs["latin_incomplete"]),
        "latin_western_incomplete_count": len(tabs["latin_western_incomplete"]),
        "cyrillic": tabs["cyrillic"],
        "greek": tabs["greek"],
        "latin_incomplete": tabs["latin_incomplete"],
        "latin_western_incomplete": tabs["latin_western_incomplete"],
    }
    with open(COVERAGE_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", COVERAGE_PATH)
    print("files", payload["font_file_count"], "families", payload["family_count"])
    print("cyrillic", len(tabs["cyrillic"]))
    print("greek", len(tabs["greek"]))
    print("latin_incomplete", len(tabs["latin_incomplete"]))
    print("latin_western_incomplete", len(tabs["latin_western_incomplete"]))
    print("WESTERN_GAP", ", ".join(tabs["latin_western_incomplete"]))
    for name in tabs["latin_incomplete"]:
        row = payload["coverage"][name]
        print("  LATIN_GAP", name, "missing", row.get("latin_missing"),
              "/", row.get("latin_need"),
              "basic", row.get("latin_basic"),
              "ext_a", row.get("latin_ext_a"))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, REPO)
    raise SystemExit(main())
