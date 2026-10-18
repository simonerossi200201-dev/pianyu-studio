# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Allowed licenses for downloadable / bundled models.

Criterion: commercially usable and redistributable. This is the only
whitelist in the repo; add a model only if its card matches one of these.
"""
from __future__ import annotations

# Canonical ids. Aliases (apache-2.0, CC0, etc.) are folded in is_license_allowed.
ALLOWED_MODEL_LICENSES = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "CC0-1.0",
    "CC-BY-4.0",
    "Unlicense",
})

_ALIASES = {
    "APACHE-2.0": "Apache-2.0",
    "APACHE-2": "Apache-2.0",
    "APACHE2.0": "Apache-2.0",
    "BSD-2": "BSD-2-Clause",
    "BSD-3": "BSD-3-Clause",
    "CC0": "CC0-1.0",
    "CC-0": "CC0-1.0",
    "CC-BY": "CC-BY-4.0",
    "CC-BY-4": "CC-BY-4.0",
}


def _canonical(license_id: str | None) -> str:
    raw = (license_id or "").strip()
    if not raw:
        return ""
    if raw in ALLOWED_MODEL_LICENSES:
        return raw
    key = raw.upper().replace(" ", "-")
    if key in _ALIASES:
        return _ALIASES[key]
    for item in ALLOWED_MODEL_LICENSES:
        if item.upper() == key:
            return item
    return raw


def license_is_restricted(license_id: str | None) -> bool:
    """True for empty, other, or any license with NC / ND tokens."""
    raw = (license_id or "").strip()
    if not raw:
        return True
    parts = raw.upper().replace(" ", "-").replace("_", "-").split("-")
    if "OTHER" in parts and len(parts) == 1:
        return True
    if raw.strip().lower() == "other":
        return True
    return "NC" in parts or "ND" in parts


def is_license_allowed(license_id: str | None) -> bool:
    if license_is_restricted(license_id):
        return False
    return _canonical(license_id) in ALLOWED_MODEL_LICENSES
