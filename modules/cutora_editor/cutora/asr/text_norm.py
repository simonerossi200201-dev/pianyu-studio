# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chinese / Arabic text folding shared by alignment, stitching and title locate.

Two families of helpers live here:

* ``fold_*_for_match`` — aggressive, symmetric normalisation used to compare a
  script against an ASR transcript (both sides go through the same function).
* ``zh_align_units`` / ``ar_align_units`` — split display text into the units
  the dedicated wav2vec2 aligners time (one per Chinese character / Latin run,
  one per Arabic word) and give each unit the string the aligner should see.
  The display text is never replaced by these strings.

Pure Python, no torch / Qt import.
"""
from __future__ import annotations

import re
import unicodedata

# ----------------------------------------------------------------- Chinese

CJK_CHAR_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ebef]")
# Punctuation that may never start a line / is glued to the previous unit.
ZH_CLOSING = frozenset("，。！？、；：）」』】》〉”’…‥·,.!?;:)]}%‰℃°～~")
# Punctuation that may never end a line / is glued to the next unit.
ZH_OPENING = frozenset("（「『【《〈“‘([{")
_ZH_DIGIT = {
    "零": 0, "〇": 0, "○": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "貳": 2,
    "两": 2, "兩": 2, "三": 3, "叁": 3, "參": 3, "四": 4, "肆": 4, "五": 5,
    "伍": 5, "六": 6, "陆": 6, "陸": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_ZH_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_ZH_BIG = {"万": 10 ** 4, "萬": 10 ** 4, "亿": 10 ** 8, "億": 10 ** 8}
_ZH_NUM_RUN = re.compile(
    "[%s]+" % re.escape("".join(list(_ZH_DIGIT) + list(_ZH_UNIT) + list(_ZH_BIG))))
_ZH_PERCENT = re.compile(r"百分之([%s]+)" % re.escape("".join(
    list(_ZH_DIGIT) + list(_ZH_UNIT) + list(_ZH_BIG))))
_T2S: dict[str, str] | None = None


def _t2s_table() -> dict[str, str]:
    global _T2S
    if _T2S is None:
        from cutora.asr.zh_t2s_data import SIMP, TRAD
        _T2S = dict(zip(TRAD, SIMP))
    return _T2S


def t2s(text: str) -> str:
    """Traditional → Simplified, character by character (OpenCC table)."""
    table = _t2s_table()
    return "".join(table.get(ch, ch) for ch in (text or ""))


def is_cjk(ch: str) -> bool:
    return bool(ch) and bool(CJK_CHAR_RE.match(ch))


def _zh_section(run: str) -> int | None:
    """Value of a run without 万/亿 (e.g. 三千零五 → 3005)."""
    total = 0
    digit = None
    for ch in run:
        if ch in _ZH_DIGIT:
            digit = _ZH_DIGIT[ch]
        elif ch in _ZH_UNIT:
            total += (1 if digit is None else digit) * _ZH_UNIT[ch]
            digit = None
        else:
            return None
    if digit is not None:
        total += digit
    return total


def zh_numeral_value(run: str) -> int | None:
    """Parse a Chinese numeral run. ``二零二四`` → 2024, ``两千零二十四`` → 2024."""
    run = run or ""
    if not run:
        return None
    if all(ch in _ZH_DIGIT for ch in run):
        if len(run) == 1:
            return _ZH_DIGIT[run]
        return int("".join(str(_ZH_DIGIT[ch]) for ch in run))
    total = 0
    rest = run
    for big_ch, big in sorted(_ZH_BIG.items(), key=lambda kv: -kv[1]):
        if big_ch in rest:
            head, rest = rest.split(big_ch, 1)
            val = _zh_section(head) if head else 1
            if val is None:
                return None
            total += val * big
    if rest:
        val = _zh_section(rest)
        if val is None:
            return None
        total += val
    return total


def fold_zh_numerals(text: str) -> str:
    """Replace Chinese numeral runs with ASCII digits (百分之五十 → 50%)."""
    def _pct(m):
        val = zh_numeral_value(m.group(1))
        return m.group(0) if val is None else "%d%%" % val

    def _num(m):
        val = zh_numeral_value(m.group(0))
        return m.group(0) if val is None else " %d " % val

    text = _ZH_PERCENT.sub(_pct, text or "")
    return _ZH_NUM_RUN.sub(_num, text)


def fold_zh_for_match(text: str) -> str:
    """Width-fold, Traditional→Simplified, Chinese numerals → digits."""
    raw = unicodedata.normalize("NFKC", text or "")
    return fold_zh_numerals(t2s(raw))


_CN_DIGITS = "零一二三四五六七八九"


def _cn_under_10k(n: int) -> str:
    out = ""
    zero = False
    for unit, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d = n // unit
        n %= unit
        if d:
            if zero and out:
                out += "零"
            out += _CN_DIGITS[d] + name
            zero = False
        elif out:
            zero = True
    return out


def zh_number_reading(digits: str) -> str:
    """How a digit string is usually read aloud in Mandarin (for alignment only).

    Years / codes (leading zero, or 4 digits next to 年) are read digit by
    digit by callers; this returns the cardinal reading for up to 10^12.
    """
    s = (digits or "").strip()
    if not s.isdigit():
        return ""
    if len(s) > 1 and s.startswith("0") or len(s) > 12:
        return "".join(_CN_DIGITS[int(c)] for c in s)
    n = int(s)
    if n == 0:
        return "零"
    parts = []
    for big, name in ((10 ** 8, "亿"), (10 ** 4, "万"), (1, "")):
        chunk = n // big
        n %= big
        if chunk:
            if parts and chunk < 1000:
                parts.append("零")
            parts.append(_cn_under_10k(chunk) + name)
        elif parts and n:
            parts.append("")
    text = "".join(parts)
    if text.startswith("一十"):
        text = text[1:]
    return text


_LATIN_RUN = re.compile(r"[A-Za-z0-9\u00c0-\u024f'’%.\-+&@#]+")
WILDCARD = "\u2047"  # never in any aligner vocab → whisperx placeholder


def zh_display_units(text: str) -> list[str]:
    """Chinese display units: each CJK char; Latin/digit runs whole.

    Closing punctuation sticks to the previous unit and opening punctuation
    to the next one, so a unit never starts with ``，`` or ends with ``（``.
    """
    body = unicodedata.normalize("NFC", text or "")
    units: list[str] = []
    pending = ""
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch.isspace():
            i += 1
            continue
        m = _LATIN_RUN.match(body, i)
        if m and m.end() > i and not is_cjk(ch):
            tok = m.group(0)
            core = tok.rstrip(".,'’")
            if not core:
                core = tok
            units.append(pending + core)
            pending = ""
            i += len(core)
            continue
        if ch in ZH_OPENING:
            pending += ch
            i += 1
            continue
        if is_cjk(ch) or unicodedata.category(ch)[0] in "LN":
            units.append(pending + ch)
            pending = ""
            i += 1
            continue
        # punctuation / symbol: glue to previous unit
        if units:
            units[-1] += ch
        else:
            pending += ch
        i += 1
    if pending:
        if units:
            units[-1] += pending
        else:
            units.append(pending)
    return units


def zh_align_form(unit: str, vocab: set | frozenset, next_unit: str = "") -> str:
    """The characters the zh aligner should see for one display unit.

    CJK chars are mapped Traditional→Simplified; anything the model vocab
    lacks becomes a wildcard so the unit still receives a time. Digits are
    spelled the way they are read. Punctuation contributes nothing.
    """
    out = []
    for piece in re.findall(r"\d+|[A-Za-z\u00c0-\u024f]+|.", unit or ""):
        if piece.isdigit():
            year = len(piece) == 4 and next_unit.startswith(("年", "年"))
            reading = ("".join(_CN_DIGITS[int(c)] for c in piece)
                       if year else zh_number_reading(piece))
            out.extend(c if c in vocab else WILDCARD for c in reading)
            continue
        if piece[0].isalpha() and not is_cjk(piece[0]):
            folded = unicodedata.normalize("NFKD", piece)
            for c in folded:
                if unicodedata.category(c) == "Mn":
                    continue
                up = c.upper()
                out.append(up if (up in vocab or c in vocab) else WILDCARD)
            continue
        ch = piece
        if is_cjk(ch):
            simp = t2s(ch)
            out.append(simp if simp in vocab else (ch if ch in vocab else WILDCARD))
            continue
        cat = unicodedata.category(ch)
        if cat[0] in "LN":
            out.append(ch if ch in vocab else WILDCARD)
    return "".join(out)


# ------------------------------------------------------------------ Arabic

AR_TASHKEEL_RE = re.compile(
    "[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed\u08d3-\u08ff]")
AR_TATWEEL = "\u0640"
_AR_DIGITS = str.maketrans({
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
})
_AR_MATCH_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ٲ": "ا", "ٳ": "ا",
    "ى": "ي", "ی": "ي", "ې": "ي", "ئ": "ي",
    "ة": "ه", "ۀ": "ه", "ە": "ه",
    "ؤ": "و", "ک": "ك", "ڪ": "ك",
    "،": ",", "؛": ";", "؟": "?", "٪": "%", "٫": ".", "٬": ",",
})
# Variants the ar aligner vocab lacks → closest vocab letter.
_AR_ALIGN_MAP = str.maketrans({
    "ٱ": "ا", "ٲ": "ا", "ٳ": "ا", "ی": "ي", "ې": "ي", "ۍ": "ي",
    "ک": "ك", "ڪ": "ك", "ۀ": "ه", "ە": "ه", "ھ": "ه", "ۃ": "ة",
    "ٹ": "ت", "پ": "ب", "چ": "ج", "ژ": "ز", "ڤ": "ف", "گ": "ك",
})
AR_PUNCT = frozenset("،؛؟.,!?:;\"'«»()[]{}…-–—")


def strip_tashkeel(text: str) -> str:
    """Remove harakat / Quranic marks and tatweel; keep letters and hamza forms."""
    return AR_TASHKEEL_RE.sub("", (text or "").replace(AR_TATWEEL, ""))


def ar_digits_to_ascii(text: str) -> str:
    return (text or "").translate(_AR_DIGITS)


def fold_ar_for_match(text: str) -> str:
    """Strip tashkeel/tatweel, unify alef / ya / ta-marbuta / hamza seats, digits → ASCII."""
    raw = unicodedata.normalize("NFKC", text or "")
    raw = strip_tashkeel(raw)
    return ar_digits_to_ascii(raw.translate(_AR_MATCH_MAP))


def ar_display_units(text: str) -> list[str]:
    """Arabic display words (split on whitespace); lone punctuation is glued on."""
    units: list[str] = []
    for tok in (text or "").split():
        letters = [c for c in tok if unicodedata.category(c)[0] in "LN"]
        if not letters and units:
            units[-1] += tok
            continue
        units.append(tok)
    return units


def ar_align_form(unit: str, vocab: set | frozenset) -> str:
    """Characters the ar aligner should see for one display word.

    Tashkeel and tatweel are dropped, letter variants mapped onto the vocab,
    digits and Latin letters become wildcards (one per character, three per
    digit — a spoken Arabic number is much longer than its digits).
    """
    raw = unicodedata.normalize("NFKC", unit or "")
    raw = strip_tashkeel(raw).translate(_AR_ALIGN_MAP)
    raw = ar_digits_to_ascii(raw)
    out = []
    for ch in raw:
        if ch in vocab and ch not in ("|", "-"):
            out.append(ch)
        elif ch.isdigit():
            out.append(WILDCARD * 3)
        elif unicodedata.category(ch)[0] in "LN":
            out.append(WILDCARD)
    return "".join(out)


def detect_dominant_script(text: str) -> str:
    """``han`` / ``arabic`` / ``other`` by letter count (for language-less callers)."""
    han = ar = other = 0
    for ch in text or "":
        if is_cjk(ch):
            han += 1
        elif "\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f":
            ar += 1
        elif ch.isalpha():
            other += 1
    if han and han >= max(ar, other):
        return "han"
    if ar and ar >= other:
        return "arabic"
    return "other"
