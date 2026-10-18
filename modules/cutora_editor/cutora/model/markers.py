# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Task markers: uuid id, absolute seconds. Missing field = empty list."""
from __future__ import annotations

import uuid

DEFAULT_COLOR = "#F5B700"


def markers_of(task: dict) -> list:
    if not isinstance(task, dict):
        return []
    raw = task.get("markers")
    if not isinstance(raw, list):
        task["markers"] = []
        return task["markers"]
    return raw


def _color(value) -> str:
    text = str(value or "").strip() or DEFAULT_COLOR
    if text.startswith("#") and len(text) == 7:
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            pass
    return DEFAULT_COLOR


def add_marker(task: dict, t: float, name: str = "", color: str | None = None) -> dict:
    rec = {
        "id": uuid.uuid4().hex,
        "t": float(t),
        "name": str(name or ""),
        "color": _color(color),
    }
    markers_of(task).append(rec)
    return rec


def find_marker(task: dict, mid: str) -> dict:
    want = str(mid or "")
    for rec in markers_of(task):
        if isinstance(rec, dict) and str(rec.get("id") or "") == want:
            return rec
    raise ValueError("找不到标记 %s" % want)


def remove_markers(task: dict, ids) -> list:
    want = {str(i) for i in (ids or []) if str(i)}
    if not want:
        raise ValueError("缺少 ids")
    kept, removed = [], []
    for rec in markers_of(task):
        if isinstance(rec, dict) and str(rec.get("id") or "") in want:
            removed.append(str(rec.get("id")))
        else:
            kept.append(rec)
    if len(removed) != len(want):
        missing = want - set(removed)
        raise ValueError("找不到标记 %s" % ", ".join(sorted(missing)))
    task["markers"] = kept
    return removed


def rename_marker(task: dict, mid: str, name: str) -> dict:
    rec = find_marker(task, mid)
    rec["name"] = str(name or "")
    return rec


def move_marker(task: dict, mid: str, t: float) -> dict:
    rec = find_marker(task, mid)
    rec["t"] = float(t)
    return rec


def set_marker_color(task: dict, mid: str, color: str) -> dict:
    rec = find_marker(task, mid)
    rec["color"] = _color(color)
    return rec


def sorted_markers(task: dict) -> list:
    rows = [m for m in markers_of(task) if isinstance(m, dict)]
    rows.sort(key=lambda m: float(m.get("t") or 0.0))
    return rows
