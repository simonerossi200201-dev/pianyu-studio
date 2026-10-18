# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""API bearer token in the OS credential store."""
from __future__ import annotations

import secrets as pysecrets

from cutora.api import TOKEN_NAME
from cutora.core.secrets import delete, get, set


def read_token() -> str | None:
    return get(TOKEN_NAME)


def ensure_token() -> str:
    tok = read_token()
    if tok:
        return tok
    return rotate_token()


def rotate_token() -> str:
    tok = pysecrets.token_urlsafe(32)
    set(TOKEN_NAME, tok)
    return tok


def clear_token() -> None:
    delete(TOKEN_NAME)


def token_matches(header: str | None) -> bool:
    expected = read_token()
    if not expected:
        return False
    raw = (header or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return False
    try:
        return pysecrets.compare_digest(raw, expected)
    except (ValueError, TypeError):
        return False
