# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""English function-word list for emphasis.auto.

Auto rules must never highlight these (emphasis.words may still).
English only — no CJK tokenizer, no download, no extra dependency.
"""
from __future__ import annotations

import unicodedata

# Articles, prepositions, conjunctions, copulas, auxiliaries, pronouns.
_STOP = frozenset({
    "a", "an", "the",
    "in", "on", "at", "to", "of", "for", "from", "with", "by",
    "into", "onto", "upon", "over", "under", "about", "as",
    "and", "or", "but", "so", "nor", "yet",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing",
    "will", "would", "can", "could", "should", "may", "might", "must",
    "shall", "have", "has", "had",
    "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those",
    "who", "whom", "which", "what",
    "not", "no", "nor",
})


def fold_word(s: str) -> str:
    text = unicodedata.normalize("NFKC", s or "")
    for ch in "’‘`´ʼ":
        text = text.replace(ch, "'")
    for ch in "‐‑‒–—―":
        text = text.replace(ch, "-")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")
    return text.casefold()


def strip_punct(token: str) -> str:
    return (token or "").strip().strip("\"'`.,;:!?…—()[]{}")


def is_stopword(token: str) -> bool:
    core = fold_word(strip_punct(token))
    if not core:
        return False
    return core in _STOP


def stopwords() -> frozenset[str]:
    return _STOP
