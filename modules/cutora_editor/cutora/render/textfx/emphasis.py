# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Emphasis detection. Pure: same text+spec → same hits. No hash(), no RNG."""
from __future__ import annotations

import re
from dataclasses import dataclass

from cutora.subtitle.stopwords import fold_word, is_stopword, strip_punct
from cutora.render.textfx.spec import EMPH_AUTO, FxSpec

# 50% / $20 / 3.5x / 12:30 / 1,200 / 2026 — keep symbols glued to the digits.
_NUM_RE = re.compile(
    r"^[\(\[\$€£¥₩₹#]*"
    r"\d{1,3}(?:,\d{3})*(?:\.\d+)?"
    r"(?:[:./-]\d+)*"
    r"[xX%]?"
    r"[\)\]]*"
    r"[,.;:!?]*$"
)
_LIST_MARK_RE = re.compile(r"^\d+[\.\)]?$")
_ORDINALS = frozenset({
    "first", "second", "third", "fourth", "fifth",
    "sixth", "seventh", "eighth", "ninth", "tenth",
    "finally", "lastly",
})
RULE_RANK = {
    "words": 0,
    "numbers": 1,
    "glossary": 2,
    "list": 3,
    "caps": 4,
    "longest": 5,
}


@dataclass
class EmphasisHit:
    index: int
    token: str
    rule: str
    color: str = ""


def tokenize(text: str) -> list[str]:
    return [t for t in str(text or "").split() if t]


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def is_number_token(token: str) -> bool:
    s = token.strip().strip("\"'`")
    if not s or not _has_digit(s):
        return False
    if _NUM_RE.match(s):
        return True
    return _has_digit(s)


def is_caps_token(token: str) -> bool:
    core = strip_punct(token)
    if len(core) < 2:
        return False
    letters = [ch for ch in core if ch.isalpha()]
    if len(letters) < 2:
        return False
    return all(ch.isupper() for ch in letters)


def is_list_mark(token: str) -> bool:
    core = strip_punct(token)
    folded = fold_word(core)
    if folded in _ORDINALS:
        return True
    return bool(_LIST_MARK_RE.match(core) or _LIST_MARK_RE.match(token.strip()))


def _line_start_indices(lines: list[str], tokens: list[str]) -> set[int]:
    """Token indices that sit at the start of a visual line."""
    starts: set[int] = set()
    if not tokens:
        return starts
    starts.add(0)
    if not lines:
        return starts
    cursor = 0
    for line in lines:
        line_toks = tokenize(line)
        if not line_toks:
            continue
        # Align to the global token stream.
        while cursor < len(tokens) and (
                fold_word(strip_punct(tokens[cursor]))
                != fold_word(strip_punct(line_toks[0]))):
            cursor += 1
        if cursor < len(tokens):
            starts.add(cursor)
            cursor += len(line_toks)
    return starts


def _glossary_indices(tokens: list[str], terms: list[str]) -> list[int]:
    folded_terms = []
    for term in terms or []:
        bits = tokenize(term)
        if bits:
            folded_terms.append([fold_word(strip_punct(b)) for b in bits])
    hits = []
    n = len(tokens)
    folded = [fold_word(strip_punct(t)) for t in tokens]
    for i in range(n):
        for parts in folded_terms:
            m = len(parts)
            if m == 0 or i + m > n:
                continue
            if folded[i:i + m] == parts:
                for k in range(m):
                    hits.append(i + k)
                break
    return hits


def detect_emphasis(
    text: str,
    spec: FxSpec,
    *,
    glossary: list[str] | None = None,
    lines: list[str] | None = None,
) -> list[EmphasisHit]:
    """Pick emphasized tokens. Caps uses *text* as-is (before letter_case)."""
    tokens = tokenize(text)
    if not tokens:
        return []
    explicit = {
        fold_word(strip_punct(w))
        for w in (spec.emphasis.words or [])
        if str(w).strip()
    }
    auto = [r for r in (spec.emphasis.auto or []) if r in EMPH_AUTO]
    line_starts = _line_start_indices(list(lines or []), tokens)

    chosen: dict[int, str] = {}

    def take(idx: int, rule: str) -> None:
        if idx in chosen:
            return
        chosen[idx] = rule

    for i, tok in enumerate(tokens):
        key = fold_word(strip_punct(tok))
        if key and key in explicit:
            take(i, "words")

    def allowed(idx: int) -> bool:
        return not is_stopword(tokens[idx])

    if "numbers" in auto:
        for i, tok in enumerate(tokens):
            if allowed(i) and is_number_token(tok):
                take(i, "numbers")
    if "glossary" in auto:
        for i in _glossary_indices(tokens, list(glossary or [])):
            if allowed(i):
                take(i, "glossary")
    if "list" in auto:
        for i, tok in enumerate(tokens):
            if i in line_starts and allowed(i) and is_list_mark(tok):
                take(i, "list")
    if "caps" in auto:
        for i, tok in enumerate(tokens):
            if allowed(i) and is_caps_token(tok):
                take(i, "caps")
    if "longest" in auto:
        best_i = None
        best_len = -1
        for i, tok in enumerate(tokens):
            if not allowed(i):
                continue
            n = len(strip_punct(tok))
            if n > best_len:
                best_len = n
                best_i = i
        if best_i is not None and best_len > 0:
            take(best_i, "longest")

    words_hits = [
        EmphasisHit(i, tokens[i], "words")
        for i, rule in chosen.items() if rule == "words"
    ]
    auto_hits = [
        EmphasisHit(i, tokens[i], rule)
        for i, rule in chosen.items() if rule != "words"
    ]
    auto_hits.sort(key=lambda h: (RULE_RANK.get(h.rule, 9), h.index))
    cap = int(spec.emphasis.max_per_cue)
    if cap > 0:
        auto_hits = auto_hits[:cap]
    hits = words_hits + auto_hits
    hits.sort(key=lambda h: h.index)
    palette = list(spec.emphasis.palette or [])
    color = spec.emphasis.color
    for n, hit in enumerate(hits):
        hit.color = palette[n % len(palette)] if palette else color
    return hits


def hit_map(hits: list[EmphasisHit]) -> dict[int, EmphasisHit]:
    return {h.index: h for h in hits}
