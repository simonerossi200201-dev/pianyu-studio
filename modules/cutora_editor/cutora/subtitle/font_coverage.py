# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""L3: bundled-font coverage and per-character script fallback. Never Qt system fonts."""
from __future__ import annotations

import json
import os
import unicodedata
from functools import lru_cache
from typing import Any

from cutora.core.paths import RESOURCES

FALLBACK_PATH = os.path.join(RESOURCES, "data", "script_fallback.json")
COVERAGE_PATH = os.path.join(RESOURCES, "data", "font_script_coverage.json")
CHARSET_PATH = os.path.join(RESOURCES, "data", "script_charsets.json")

EMOJI_FAMILY = "Noto Color Emoji"
PUNCT_FLOOR_FAMILY = "Noto Sans"

# ZWJ / variation selectors / keycap — no glyph of their own; stick to the previous run.
INVISIBLE_CPS = frozenset({0x200D, 0xFE0E, 0xFE0F, 0x20E3})

SHAPING_SCRIPTS = frozenset({
    "arabic", "hebrew",
    "devanagari", "bengali", "gurmukhi", "gujarati",
    "tamil", "telugu", "kannada", "malayalam", "sinhala",
    "thai", "lao", "khmer", "myanmar", "tibetan", "ethiopic",
})

# Emoji / pictograph blocks. Route these to Noto Color Emoji, not the display face.
_EMOJI_RANGES = (
    (0x2194, 0x2199),
    (0x21A9, 0x21AA),
    (0x231A, 0x231B),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x24C2, 0x24C2),
    (0x25AA, 0x25AB),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x25FB, 0x25FE),
    (0x2600, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x1F000, 0x1FAFF),
    (0x1F1E6, 0x1F1FF),
)


@lru_cache(maxsize=1)
def load_script_fallback() -> dict[str, str]:
    with open(FALLBACK_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    return {str(k): str(v) for k, v in (data or {}).items()}


@lru_cache(maxsize=1)
def load_coverage() -> dict[str, Any]:
    with open(COVERAGE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_charsets() -> dict[str, Any]:
    with open(CHARSET_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def reset_caches() -> None:
    load_script_fallback.cache_clear()
    load_coverage.cache_clear()
    load_charsets.cache_clear()
    _cmap_cps.cache_clear()


def _allowed() -> list[str]:
    from cutora.ui.panels.fonts import ALLOWED_FONTS, DEFAULT_FONT_FAMILY
    return list(ALLOWED_FONTS) or [DEFAULT_FONT_FAMILY]


def match_bundled_family(wanted: str) -> str:
    """Exact registered-family lookup. Never prefix.

    Prefix matching mapped ``SN Pro`` onto DirectWrite named instance
    ``SN Pro ExtraLight`` and collapsed v2 weight to hairline. Unknown
    names are returned unchanged (loud: missing cmap → tofu), not remapped.
    """
    want = (wanted or "").strip()
    if not want:
        names = list((load_coverage().get("coverage") or {}).keys()) or _allowed()
        if "Arimo" in names:
            return "Arimo"
        return names[0] if names else ""
    if want == EMOJI_FAMILY:
        return EMOJI_FAMILY
    names = list((load_coverage().get("coverage") or {}).keys())
    if not names:
        names = _allowed()
    if want in names:
        return want
    low = want.lower()
    for name in names:
        if name.lower() == low:
            return name
    if want in _allowed():
        return want
    try:
        from cutora.ui.panels.fonts import FONT_FILES
        if want in FONT_FILES:
            return want
    except Exception:
        pass
    return want


def fallback_family_for_script(script: str) -> str:
    table = load_script_fallback()
    raw = table.get((script or "").strip().lower()) or table.get("other") or PUNCT_FLOOR_FAMILY
    return match_bundled_family(raw)


def family_covers_script(family: str, script: str) -> bool:
    cov = (load_coverage().get("coverage") or {}).get(family) or {}
    if not cov:
        resolved = match_bundled_family(family)
        cov = (load_coverage().get("coverage") or {}).get(resolved) or {}
    key = (script or "").strip().lower()
    if key in cov:
        return bool(cov.get(key))
    if key == "latin":
        return bool(cov.get("latin_basic"))
    return False


def charset_for_language(lang_code: str) -> str:
    """Glyphs required by the current project language — not the script union.

    English → latin_basic (ASCII letters/digits/punct). Hausa Ɓ lives only in
    the latin union and must not grey display faces on an English project.
    """
    from cutora.asr.languages import get_language
    code = (lang_code or "en").strip().lower() or "en"
    if code == "auto":
        code = "en"
    row = get_language(code) or get_language("en") or {}
    script = str(row.get("script") or "latin").strip().lower()
    entry = (load_charsets().get("scripts") or {}).get(script) or {}
    chars = str(entry.get("chars") or "")
    if script != "latin":
        return chars
    subsets = entry.get("subsets") or {}
    basic = str(subsets.get("latin_basic") or "")
    western = str(subsets.get("latin_western") or "")
    native = str(row.get("name_native") or "")

    def _covered(pool: str) -> bool:
        if not pool:
            return False
        return all(ch in pool for ch in native if not ch.isspace())

    if _covered(basic):
        return basic
    if _covered(western):
        return western
    return chars or basic


def family_covers_language(family: str, lang_code: str) -> bool:
    """True if ``family`` can paint the current language's own charset.

    Q170：拉丁语种先查预先算好的 font_script_coverage.json（latin_basic / latin_western / latin），
    和 charset_for_language 挑的那一档一一对应。以前每个家族都用 fontTools 解一遍 cmap ——
    213 个字体冷启动要 4 秒，而且在界面线程上；只有 JSON 里没有的家族才回退到解 cmap。"""
    from cutora.asr.languages import get_language
    code = (lang_code or "en").strip().lower() or "en"
    if code == "auto":
        code = "en"
    row = get_language(code) or get_language("en") or {}
    script = str(row.get("script") or "latin").strip().lower()
    if script == "latin":
        pre = _latin_coverage_from_json(family, code)
        if pre is not None:
            return pre
        return not missing_chars(family, charset_for_language(code))
    return family_covers_script(family, script)


def _latin_coverage_from_json(family: str, code: str):
    """None = JSON 里没有这个家族（回退解 cmap）。"""
    covmap = load_coverage().get("coverage") or {}
    cov = covmap.get(family)
    if not cov:
        cov = covmap.get(match_bundled_family(family))
    if not cov:
        # 可变字体登记的家族名是「Noto Sans SC」，JSON 里记的是具名实例「Noto Sans SC Thin」—— 同一个文件
        fam = str(family or "").strip()
        if fam:
            for key, val in covmap.items():
                if key.startswith(fam + " ") and isinstance(val, dict):
                    cov = val
                    break
    if not isinstance(cov, dict):
        return None
    entry = (load_charsets().get("scripts") or {}).get("latin") or {}
    subsets = entry.get("subsets") or {}
    want = charset_for_language(code)
    if want and want == str(subsets.get("latin_basic") or ""):
        key = "latin_basic"
    elif want and want == str(subsets.get("latin_western") or ""):
        key = "latin_western"
    else:
        key = "latin"
    if key not in cov:
        return None
    return bool(cov.get(key))


def _emoji_font_path() -> str | None:
    try:
        from cutora.render.stickers import emoji_font_path
        path = emoji_font_path()
    except Exception:
        return None
    return path if path and os.path.isfile(path) else None


def _font_path(family: str) -> str | None:
    if family == EMOJI_FAMILY:
        return _emoji_font_path()
    cov = load_coverage().get("files_by_family") or {}
    rels = cov.get(family) or []
    if rels:
        return os.path.join(RESOURCES, "fonts", str(rels[0]).replace("/", os.sep))
    try:
        from cutora.ui.panels.fonts import FONT_FILES
        files = FONT_FILES.get(family) or []
        if files:
            return files[0]
    except Exception:
        pass
    resolved = match_bundled_family(family) if family else family
    if resolved and resolved != family:
        if resolved == EMOJI_FAMILY:
            return _emoji_font_path()
        rels = cov.get(resolved) or []
        if rels:
            return os.path.join(RESOURCES, "fonts", str(rels[0]).replace("/", os.sep))
        try:
            from cutora.ui.panels.fonts import FONT_FILES
            files = FONT_FILES.get(resolved) or []
            return files[0] if files else None
        except Exception:
            return None
    return None


class _CpSet:
    """一个字体的码点集合，存成位图（Q189）。以前是 frozenset[int]：一个 CJK 字体 3～5 万个码点就是
    3～5 MB 的 Python 对象，241 个字体启动时预热全解一遍，几十到上百 MB 常驻。位图最大 139 KB。
    支持 `cp in cps`、len()、迭代（按需）。"""

    __slots__ = ("_bits", "_n")

    def __init__(self, cps):
        cps = [int(c) for c in cps if int(c) >= 0]           # 可能是生成器：先落成列表
        hi = max(cps) if cps else 0
        bits = bytearray((hi >> 3) + 1)
        n = 0
        for cp in cps:
            cp = int(cp)
            if cp < 0:
                continue
            bits[cp >> 3] |= 1 << (cp & 7)
            n += 1
        self._bits = bytes(bits)
        self._n = n

    def __contains__(self, cp) -> bool:
        try:
            cp = int(cp)
        except (TypeError, ValueError):
            return False
        i = cp >> 3
        return 0 <= i < len(self._bits) and bool(self._bits[i] & (1 << (cp & 7)))

    def __len__(self) -> int:
        return self._n

    def __iter__(self):
        bits = self._bits
        for i, b in enumerate(bits):
            if b:
                base = i << 3
                for k in range(8):
                    if b & (1 << k):
                        yield base + k

    def __bool__(self) -> bool:
        return self._n > 0


_CMAP_CACHE_VER = b"CMAP1\n"


def _cmap_cache_path(path: str):
    try:
        from cutora.core.paths import user_cache_dir
        st = os.stat(path)
        import hashlib
        key = hashlib.sha1(("%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))).encode("utf-8")).hexdigest()
        d = os.path.join(user_cache_dir(), "font_probe", "cmap")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, key + ".bin")
    except Exception:
        return None


@lru_cache(maxsize=512)
def _cmap_cps(path: str) -> _CpSet:
    """一个字体的码点位图。Q189c：结果按（路径, 大小, 修改时间）缓存到磁盘 —— 以前每次启动都用 fontTools
    把 241 个字体（136 MB）解一遍，CJK 一个几百毫秒，启动头半分钟里 CPU 全在干这个。"""
    cpath = _cmap_cache_path(path)
    if cpath and os.path.isfile(cpath):
        try:
            with open(cpath, "rb") as fh:
                raw = fh.read()
            if raw.startswith(_CMAP_CACHE_VER):
                body = raw[len(_CMAP_CACHE_VER):]
                n = int.from_bytes(body[:4], "little")
                obj = _CpSet.__new__(_CpSet)
                obj._bits = bytes(body[4:])
                obj._n = n
                return obj
        except Exception:
            pass
    from fontTools.ttLib import TTFont
    font = TTFont(path, lazy=True, fontNumber=0)
    try:
        cmap = font.getBestCmap() or {}
        res = _CpSet(int(k) for k in cmap.keys())
    finally:
        font.close()
    if cpath:
        try:
            tmp = cpath + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(_CMAP_CACHE_VER + len(res).to_bytes(4, "little") + res._bits)
            os.replace(tmp, cpath)
        except Exception:
            pass
    return res


def _skip_coverage_char(ch: str) -> bool:
    if not ch or ch.isspace() or ch in "\n\r":
        return True
    if ord(ch) in INVISIBLE_CPS:
        return True
    if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return True
    return False


def missing_chars(family: str, text: str) -> list[str]:
    path = _font_path(family)
    if not path or not os.path.isfile(path):
        return [ch for ch in text if not _skip_coverage_char(ch)]
    cps = _cmap_cps(path)
    out = []
    for ch in text:
        if _skip_coverage_char(ch):
            continue
        if ord(ch) not in cps:
            out.append(ch)
    return out


def is_emoji_char(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    if cp in INVISIBLE_CPS:
        return True
    for lo, hi in _EMOJI_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _char_in_family(family: str, ch: str) -> bool:
    if _skip_coverage_char(ch):
        return True
    path = _font_path(family)
    if not path or not os.path.isfile(path):
        return False
    return ord(ch) in _cmap_cps(path)


def _joins_previous(ch: str) -> bool:
    if not ch or ch.isspace() or ch in "\n\r":
        return True
    if ord(ch) in INVISIBLE_CPS:
        return True
    if unicodedata.category(ch) in ("Mn", "Mc", "Me", "Cf"):
        return True
    return False


def _family_for_char(
    ch: str, requested: str, floor: str, script_fb: str,
) -> str:
    if is_emoji_char(ch):
        return EMOJI_FAMILY
    if _char_in_family(requested, ch):
        return requested
    if _char_in_family(floor, ch):
        return floor
    if script_fb not in (requested, floor) and _char_in_family(script_fb, ch):
        return script_fb
    return floor


def split_font_runs(
    family: str, text: str, script: str | None = None,
) -> list[tuple[str, str]]:
    """Split ``text`` into (piece, bundled_family) runs. Per-character.

    Characters the display face can paint stay on that face. Only a missing
    glyph (or an emoji codepoint) switches family. Never a system font.
    """
    if text == "":
        return []
    # Keep the caller's display-face name. Do not remap "SN Pro" to a
    # DirectWrite named instance via match_bundled_family.
    requested = (family or "").strip() or match_bundled_family(PUNCT_FLOOR_FAMILY)
    floor = match_bundled_family(PUNCT_FLOOR_FAMILY)
    script_fb = fallback_family_for_script(script or "other")
    runs: list[tuple[str, str]] = []
    buf: list[str] = []
    cur_fam: str | None = None
    prev_fam = requested
    for ch in text:
        if _joins_previous(ch):
            fam = cur_fam or prev_fam
        else:
            fam = _family_for_char(ch, requested, floor, script_fb)
        if cur_fam is None:
            cur_fam = fam
        elif fam != cur_fam:
            runs.append(("".join(buf), cur_fam))
            buf = []
            cur_fam = fam
        buf.append(ch)
        if not _joins_previous(ch):
            prev_fam = fam
    if buf:
        runs.append(("".join(buf), cur_fam or requested))
    return runs


def missing_chars_after_fallback(
    family: str, text: str, script: str | None = None,
) -> list[str]:
    holes: list[str] = []
    for piece, fam in split_font_runs(family, text, script):
        holes.extend(missing_chars(fam, piece))
    return holes


def resolve_run_font(family: str, text: str, script: str | None = None) -> dict[str, Any]:
    """Summarise per-character fallback for a token. Never a system font."""
    coverage = load_coverage().get("coverage") or {}
    requested = family if family in coverage else match_bundled_family(family)
    runs = split_font_runs(requested, text or "", script)
    if not runs:
        return {
            "requested": requested,
            "effective": requested,
            "substituted": False,
            "missing": [],
            "script": script or "",
            "runs": [],
        }
    fams = [fam for _piece, fam in runs]
    holes = missing_chars_after_fallback(requested, text or "", script)
    unique = []
    for fam in fams:
        if fam not in unique:
            unique.append(fam)
    substituted = any(fam != requested for fam in unique)
    if not substituted:
        effective = requested
    elif len(unique) == 1:
        effective = unique[0]
    else:
        effective = requested
    rec: dict[str, Any] = {
        "requested": requested,
        "effective": effective,
        "substituted": substituted,
        "missing": holes,
        "script": script or "",
        "runs": runs,
    }
    if substituted:
        others = [f for f in unique if f != requested]
        rec["note"] = "%s 缺少部分字形，已用 %s 代替" % (
            requested, " / ".join(others) if others else PUNCT_FLOOR_FAMILY)
    return rec


def coverage_note_for_text(family: str, text: str, script: str | None = None) -> str:
    rec = resolve_run_font(family, text, script)
    if rec.get("substituted"):
        return rec.get("note") or ""
    return ""


def apply_no_font_merging(font) -> None:
    """Stop Qt from painting system glyphs for missing cps."""
    try:
        from PySide6.QtGui import QFont
        font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    except Exception:
        pass
