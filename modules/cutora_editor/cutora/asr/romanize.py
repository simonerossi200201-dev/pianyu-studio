# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Romanize words for the MMS aligner only.

The strings returned here are fed to the aligner and then thrown away.
Subtitle text stays in the original script. ``Հայերեն`` must never be
replaced by ``Hayeren`` in a cue. Callers copy the aligner's timestamps
back onto the original tokens; they do not keep the romanized spellings.
"""
from __future__ import annotations

_ENGINE = None
_ISO_TO_6393: dict[str, str] | None = None


class RomanizeError(RuntimeError):
    """One original token did not romanize to exactly one aligner token."""


def uroman_available() -> bool:
    """True when ``import uroman`` succeeds. Does not build the data tables."""
    try:
        __import__("uroman")
    except Exception:
        return False
    return True


def _engine():
    """One ``Uroman()`` per process. Building the tables is the slow part."""
    global _ENGINE
    if _ENGINE is None:
        import uroman as ur
        _ENGINE = ur.Uroman()
    return _ENGINE


def _iso_to_6393() -> dict[str, str]:
    """Whisper's short code → the ISO-639-3 code uroman's tables use."""
    global _ISO_TO_6393
    if _ISO_TO_6393 is not None:
        return _ISO_TO_6393
    import os
    import uroman as ur
    path = os.path.join(os.path.dirname(ur.__file__), "data", "ISO-639-3-list.txt")
    mapping: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    for line in lines:
        if "::lang-code" not in line:
            continue
        fields: dict[str, str] = {}
        for part in line.split("::"):
            part = part.strip()
            if not part:
                continue
            bits = part.split(None, 1)
            fields[bits[0]] = bits[1].strip() if len(bits) > 1 else ""
        code = (fields.get("lang-code") or "").strip().lower()
        if not code:
            continue
        mapping[code] = code
        deprecated = fields.get("deprecated-lang-codes") or ""
        for piece in deprecated.replace(",", ";").split(";"):
            short = piece.strip().lower()
            if short:
                mapping[short] = code
    _ISO_TO_6393 = mapping
    return mapping


def uroman_lcode(lang: str) -> str | None:
    raw = (lang or "").strip().lower()
    if not raw or raw == "auto":
        return None
    return _iso_to_6393().get(raw, raw)


def _ascii_fold(text: str) -> str:
    """ô → o, ñ → n, ’ → '. Keeps ASCII letters/digits/apostrophe/hyphen only."""
    import unicodedata
    out = []
    for ch in unicodedata.normalize("NFKD", text or ""):
        if unicodedata.category(ch) in ("Mn", "Me", "Mc"):
            continue
        if ch in "’‘ʼ`´ʻ":
            ch = "'"
        if ch.isascii() and not ch.isspace():
            out.append(ch)
    return "".join(out)


# 一个 token 罗马化后什么字母都不剩（纯标点 / 符号）时给对齐器的占位：
# 不在 MMS 词表里 → whisperx 不给它分时间，之后由插值补上；token 数不变。
PUNCT_PLACEHOLDER = "-"


def _one(token: str, lang: str) -> str:
    rom = _engine().romanize_string(token, lcode=uroman_lcode(lang))
    if not isinstance(rom, str):
        raise RomanizeError(token)
    # uroman 偶尔吐空格（阿拉伯文连写的冠词、数字）或残留非 ASCII 字母：
    # 合成一个 token 并 ASCII 化，保证「一个原词 = 一个对齐 token」。
    rom = "".join(rom.split())
    if any(ch.isalpha() and not ch.isascii() for ch in rom):
        rom = _ascii_fold(rom)
    if not rom:
        rom = _ascii_fold(token)
    if not rom:
        return PUNCT_PLACEHOLDER
    return rom


def romanize_tokens(tokens: list[str], lang: str) -> list[str]:
    """Romanize each token. Pure ASCII tokens are returned as the same objects.

    Every token maps to exactly one aligner token (never zero, never several),
    so the caller can copy timestamps back by position. Latin-script tokens
    with diacritics (Malagasy ``ô``, Swahili loan ``ñ``) are ASCII-folded
    directly; uroman is only used for other scripts.
    """
    out: list[str] = []
    for token in tokens:
        if not isinstance(token, str):
            token = str(token)
        if token.isascii():
            out.append(token if token.strip() else PUNCT_PLACEHOLDER)
            continue
        if _is_latin_token(token):
            folded = _ascii_fold(token)
            out.append(folded or PUNCT_PLACEHOLDER)
            continue
        out.append(_one(token, lang))
    return out


def _is_latin_token(token: str) -> bool:
    import unicodedata
    for ch in token:
        if ch.isalpha():
            try:
                name = unicodedata.name(ch)
            except ValueError:
                return False
            if not name.startswith("LATIN"):
                return False
    return True


UNSPACED_SCRIPTS = frozenset({
    "han", "kana", "thai", "lao", "khmer", "myanmar", "tibetan",
})


def speech_tokens(text: str, lang: str) -> list[str]:
    """Tokens the aligner will time. Unspaced scripts are one character each."""
    from cutora.asr.languages import catalog_by_code
    raw = (lang or "").strip().lower()
    row = catalog_by_code().get(raw) or {}
    script = str(row.get("script") or "").strip().lower()
    if script in UNSPACED_SCRIPTS:
        return [ch for ch in (text or "") if not ch.isspace()]
    if row.get("line_break") == "fallback" and script not in ("arabic", "hebrew", "devanagari", "bengali", "gurmukhi", "gujarati", "tamil", "telugu", "kannada", "malayalam", "sinhala", "ethiopic", "hangul"):
        return [ch for ch in (text or "") if not ch.isspace()]
    return [part for part in (text or "").split() if part]
