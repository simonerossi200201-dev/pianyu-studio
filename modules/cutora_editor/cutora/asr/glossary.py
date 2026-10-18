# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project + global glossary → initial_prompt / hotwords (first 200 tokens)."""
from __future__ import annotations

import os

from cutora.core.log import get_logger
from cutora.core.paths import user_presets_dir

logger = get_logger("cutora.asr.glossary")

MAX_TOKENS = 200
GLOBAL_NAME = "glossary.txt"


def parse_terms(raw) -> list[str]:
    """Split a string or sequence into non-empty unique-preserving lines."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        lines = [str(x) for x in raw]
    else:
        lines = str(raw).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    seen = set()
    for line in lines:
        term = " ".join(line.strip().split())
        if not term or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def _token_count(term: str) -> int:
    bits = term.split()
    return max(1, len(bits))


def clip_terms(terms: list[str], max_tokens: int = MAX_TOKENS) -> list[str]:
    kept = []
    n = 0
    for term in terms or []:
        cost = _token_count(term)
        if n + cost > max_tokens:
            break
        kept.append(term)
        n += cost
    return kept


def terms_to_prompt(terms: list[str], max_tokens: int = MAX_TOKENS) -> str:
    kept = clip_terms(terms, max_tokens=max_tokens)
    return ", ".join(kept)


def global_glossary_path() -> str:
    return os.path.join(user_presets_dir(), GLOBAL_NAME)


def load_global_terms() -> list[str]:
    path = global_glossary_path()
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return parse_terms(fh.read())


def save_global_terms(terms: list[str]) -> str:
    path = global_glossary_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = "\n".join(parse_terms(terms))
    if text:
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def combined_terms(project_terms=None, extra=None) -> list[str]:
    """Project first, then global, then extra; de-dupe; clip to 200 tokens."""
    merged = parse_terms(project_terms) + load_global_terms() + parse_terms(extra)
    return clip_terms(parse_terms(merged))


def prompt_and_hotwords(project_terms=None, extra=None) -> tuple[str, str]:
    text = terms_to_prompt(combined_terms(project_terms, extra))
    return text, text
