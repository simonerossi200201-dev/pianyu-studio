# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Builtin pack items + user assets, filtered by category / search."""
from __future__ import annotations

from cutora.assets.pack import discover_packs
from cutora.assets.user import USER_CATEGORY_KEYS, iter_user_assets


def items_for_category(category: str, query: str = "",
                       project_dir: str | None = None) -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    if category in USER_CATEGORY_KEYS or category:
        for pack in discover_packs():
            for it in pack.get("items") or []:
                if it.get("type") != category:
                    continue
                it = dict(it)
                it["stored_path"] = _stored(it)
                it["_origin"] = "builtin"
                out.append(it)
        for it in iter_user_assets(category, project_dir=project_dir):
            it = dict(it)
            it["_origin"] = "user"
            out.append(it)
    if not q:
        return out
    filtered = []
    for it in out:
        hay = " ".join([
            str(it.get("name") or ""),
            str(it.get("id") or ""),
            " ".join(it.get("tags") or []),
        ]).lower()
        if q in hay:
            filtered.append(it)
    return filtered


def _stored(it: dict) -> str:
    import os
    d = it.get("_dir") or ""
    f = it.get("file") or ""
    return os.path.join(d, f) if d else f
