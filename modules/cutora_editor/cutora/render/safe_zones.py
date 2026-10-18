# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform safe-zone reference data. Preview chrome only — never export.

All insets come from resources/safe_zones.json. No platform magic numbers here.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from cutora.core.paths import SAFE_ZONES_JSON

_CACHE: dict | None = None

SETTINGS_MASTER = "safe_zones.enabled"
SETTINGS_PRESETS = "safe_zones.presets"


@dataclass(frozen=True)
class Band:
    preset_id: str
    preset_name: str
    side: str
    label: str
    color: str
    x: float
    y: float
    w: float
    h: float


def load_safe_zones(path: str | None = None) -> dict:
    global _CACHE
    src = path or SAFE_ZONES_JSON
    if _CACHE is not None and path is None:
        return copy.deepcopy(_CACHE)
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("presets"), list):
        raise ValueError("safe_zones.json 格式无效")
    if path is None:
        _CACHE = data
        return copy.deepcopy(data)
    return data


def disclaimer(data: dict | None = None) -> str:
    body = data or load_safe_zones()
    return str(body.get("disclaimer") or "")


def presets(data: dict | None = None) -> list[dict]:
    body = data or load_safe_zones()
    return [p for p in (body.get("presets") or []) if isinstance(p, dict) and p.get("id")]


def preset_by_id(pid: str, data: dict | None = None) -> dict | None:
    for p in presets(data):
        if str(p.get("id")) == str(pid):
            return p
    return None


def read_state(settings: dict | None) -> tuple[bool, dict]:
    """Missing keys → master off, every preset off (old projects)."""
    s = settings if isinstance(settings, dict) else {}
    enabled = bool(s.get(SETTINGS_MASTER, False))
    raw = s.get(SETTINGS_PRESETS) or {}
    flags = {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    return enabled, flags


def enabled_presets(settings: dict | None, data: dict | None = None) -> list[dict]:
    on, flags = read_state(settings)
    if not on:
        return []
    out = []
    for p in presets(data):
        pid = str(p.get("id"))
        if flags.get(pid, False):
            out.append(p)
    return out


def _unsafe(preset: dict) -> dict:
    raw = preset.get("unsafe") if isinstance(preset.get("unsafe"), dict) else {}
    out = {}
    for side in ("top", "bottom", "left", "right"):
        try:
            out[side] = max(0.0, min(0.49, float(raw.get(side) or 0.0)))
        except (TypeError, ValueError):
            out[side] = 0.0
    return out


def aspect_key(canvas_w: int, canvas_h: int) -> str:
    w = max(1, int(canvas_w))
    h = max(1, int(canvas_h))
    r = w / float(h)
    if r >= 1.4:
        return "16:9"
    if r <= 0.75:
        return "9:16"
    return "1:1"


def union_unsafe(canvas_w: int, canvas_h: int, data: dict | None = None) -> dict[str, float]:
    """Max unsafe inset among presets matching this canvas aspect."""
    want = aspect_key(canvas_w, canvas_h)
    u = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    hit = False
    for p in presets(data):
        if str(p.get("aspect") or "") != want:
            continue
        pu = _unsafe(p)
        hit = True
        for k in u:
            u[k] = max(u[k], pu[k])
    if not hit:
        u = {"top": 0.08, "bottom": 0.08, "left": 0.08, "right": 0.08}
    return u


def usable_rect(canvas_w: int, canvas_h: int, data: dict | None = None) -> tuple[float, float, float, float]:
    """Inner safe rectangle (x, y, w, h) in pixels."""
    w = float(max(1, int(canvas_w)))
    h = float(max(1, int(canvas_h)))
    u = union_unsafe(int(w), int(h), data)
    x = w * u["left"]
    y = h * u["top"]
    return x, y, w * (1.0 - u["left"] - u["right"]), h * (1.0 - u["top"] - u["bottom"])


def usable_line_width(
    canvas_w: int, canvas_h: int, *, center_x: float | None = None,
    stroke: float = 0.0, wrap_max: float | None = None,
    data: dict | None = None,
) -> float:
    """Width a centered line may occupy without leaving the safe rect."""
    sx, _sy, sw, _sh = usable_rect(canvas_w, canvas_h, data)
    right = sx + sw
    cx = float(center_x) if center_x is not None else float(canvas_w) * 0.5
    room = 2.0 * min(max(0.0, cx - sx), max(0.0, right - cx))
    cap = min(float(sw), room)
    if wrap_max is not None:
        cap = min(cap, float(wrap_max))
    cap = cap - 2.0 * max(0.0, float(stroke))
    return max(50.0, cap)


def bands_for_preset(preset: dict, canvas_w: int, canvas_h: int) -> list[Band]:
    w = max(1, int(canvas_w))
    h = max(1, int(canvas_h))
    u = _unsafe(preset)
    labels = preset.get("labels") if isinstance(preset.get("labels"), dict) else {}
    color = str(preset.get("color") or "#22D3EE")
    pid = str(preset.get("id") or "")
    name = str(preset.get("name") or pid)
    out: list[Band] = []
    if u["top"] > 0:
        out.append(Band(pid, name, "top", str(labels.get("top") or ""), color,
                        0.0, 0.0, float(w), h * u["top"]))
    if u["bottom"] > 0:
        bh = h * u["bottom"]
        out.append(Band(pid, name, "bottom", str(labels.get("bottom") or ""), color,
                        0.0, float(h) - bh, float(w), bh))
    if u["left"] > 0:
        out.append(Band(pid, name, "left", str(labels.get("left") or ""), color,
                        0.0, 0.0, w * u["left"], float(h)))
    if u["right"] > 0:
        rw = w * u["right"]
        out.append(Band(pid, name, "right", str(labels.get("right") or ""), color,
                        float(w) - rw, 0.0, rw, float(h)))
    return out


def active_bands(settings: dict | None, canvas_w: int, canvas_h: int,
                 data: dict | None = None) -> list[Band]:
    out: list[Band] = []
    for p in enabled_presets(settings, data):
        out.extend(bands_for_preset(p, canvas_w, canvas_h))
    return out


def box_hits(box, bands: list[Band]) -> list[Band]:
    x, y, w, h = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    hits = []
    for b in bands:
        if x + w <= b.x or b.x + b.w <= x or y + h <= b.y or b.y + b.h <= y:
            continue
        hits.append(b)
    return hits


def nudge_center_y(box, canvas_h: int, bands: list[Band]) -> float | None:
    """New box-center Y that clears enabled top/bottom bands. None if already clear."""
    hits = [b for b in box_hits(box, bands) if b.side in ("top", "bottom")]
    if not hits:
        return None
    _x, y, _w, h = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    ch = float(max(1, int(canvas_h)))
    top_lim = 0.0
    bot_lim = ch
    for b in bands:
        if b.side == "top":
            top_lim = max(top_lim, b.y + b.h)
        elif b.side == "bottom":
            bot_lim = min(bot_lim, b.y)
    if bot_lim - top_lim < h:
        return (top_lim + bot_lim) * 0.5
    cy = y + h * 0.5
    if y < top_lim:
        cy = top_lim + h * 0.5
    elif y + h > bot_lim:
        cy = bot_lim - h * 0.5
    return max(h * 0.5, min(ch - h * 0.5, cy))
