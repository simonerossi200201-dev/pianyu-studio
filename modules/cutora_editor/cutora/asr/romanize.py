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


def _one(token: str, lang: str) -> str:
    rom = _engine().romanize_string(token, lcode=uroman_lcode(lang))
    if not isinstance(rom, str):
        raise RomanizeError(token)
    rom = rom.strip()
    if not rom or any(ch.isspace() for ch in rom):
        raise RomanizeError(token)
    if any(ch.isalpha() and not ch.isascii() for ch in rom):
        raise RomanizeError(token)
    return rom


def romanize_tokens(tokens: list[str], lang: str) -> list[str]:
    """Romanize each token. Pure ASCII tokens are returned as the same objects.

    A token that romanizes to zero or several words raises ``RomanizeError``.
    The aligner must not invent a mapping when the counts diverge.
    """
    out: list[str] = []
    for token in tokens:
        if not isinstance(token, str):
            token = str(token)
        if token.isascii():
            out.append(token)
            continue
        out.append(_one(token, lang))
    return out


def speech_tokens(text: str, lang: str) -> list[str]:
    """Tokens the aligner will time. Unspaced scripts are one character each."""
    from cutora.asr.languages import catalog_by_code
    raw = (lang or "").strip().lower()
    row = catalog_by_code().get(raw) or {}
    if row.get("line_break") == "fallback":
        return [ch for ch in (text or "") if not ch.isspace()]
    return [part for part in (text or "").split() if part]
