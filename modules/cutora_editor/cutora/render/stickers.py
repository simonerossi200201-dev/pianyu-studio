# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Noto Color Emoji (COLRv1, SIL OFL 1.1) sticker catalog + font raster cache.

Stickers are image elements (kind=image + sticker_id). sticker_id is a
Unicode codepoint sequence in lowercase hex, hyphen-separated (e.g. 1f525,
1f468-200d-1f469). Animation reuses ANIMS (image-meaningful subset).
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

from cutora.core.log import get_logger
from cutora.core.paths import FONT_DIR, STICKER_DIR

logger = get_logger("cutora.render.stickers")

# Image-meaningful slice of ANIMS (no word/char/karaoke). New stickers may
# pick any of these; old fade / scale_in clips keep the same pixels.
ALLOWED_ANIMS = (
    "none", "fade", "scale_in",
    "float_in_up", "float_in_down", "drop_in",
    "pop_in", "impact_pop", "stomp", "slide",
    "reveal_zoom_blur",
    "zoom_slow", "float_loop",
)
DEFAULT_SIZE_FRAC = 0.12
CATALOG_NAME = "catalog.json"
EMOJI_FONT_REL = os.path.join("Noto_Color_Emoji", "NotoColorEmoji-COLRv1.ttf")
EMOJI_FAMILY = "Noto Color Emoji"
_CATALOG: dict | None = None
_ITEMS_BY_ID: dict | None = None
_RASTER: OrderedDict = OrderedDict()
_RASTER_MAX = 64
_FONT_ID = -1


def catalog_path() -> str:
    return os.path.join(STICKER_DIR, CATALOG_NAME)


def emoji_font_path() -> str:
    return os.path.join(FONT_DIR, EMOJI_FONT_REL)


def sticker_text(sticker_id: str) -> str:
    """Decode hyphenated hex codepoints to a Unicode string."""
    sid = str(sticker_id or "").strip().lower()
    if not sid:
        return ""
    chars = []
    for part in sid.replace("_", "-").split("-"):
        if not part:
            continue
        try:
            chars.append(chr(int(part, 16)))
        except ValueError:
            return ""
    return "".join(chars)


def load_catalog() -> dict:
    global _CATALOG, _ITEMS_BY_ID
    if _CATALOG is not None:
        return _CATALOG
    path = catalog_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = [it for it in (data.get("items") or []) if isinstance(it, dict) and it.get("id")]
    data = dict(data)
    data["items"] = items
    by_id = {}
    for it in items:
        by_id[str(it.get("id") or "").lower()] = it
    _ITEMS_BY_ID = by_id
    _CATALOG = data
    return data


def catalog_items() -> list[dict]:
    return list(load_catalog().get("items") or [])


def get_item(sticker_id: str) -> dict | None:
    load_catalog()
    sid = str(sticker_id or "").strip().lower()
    return (_ITEMS_BY_ID or {}).get(sid)


def known_id(sticker_id: str) -> bool:
    return get_item(sticker_id) is not None


def _ensure_font() -> str:
    global _FONT_ID
    from PySide6.QtGui import QFontDatabase
    fams = QFontDatabase.families()
    if EMOJI_FAMILY in fams:
        return EMOJI_FAMILY
    path = emoji_font_path()
    if not os.path.isfile(path):
        logger.warning("emoji font missing: %s", path)
        return ""
    _FONT_ID = QFontDatabase.addApplicationFont(path)
    got = QFontDatabase.applicationFontFamilies(_FONT_ID) if _FONT_ID >= 0 else []
    return got[0] if got else ""


def raster_sticker(sticker_id: str, px: int):
    """Rasterize emoji glyph at integer pixel size. Cache key = id + px."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QImage, QPainter

    sid = str(sticker_id or "").strip().lower()
    size = max(1, int(px))
    key = (sid, size)
    hit = _RASTER.get(key)
    if hit is not None:
        _RASTER.move_to_end(key)
        return hit
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    text = sticker_text(sid)
    family = _ensure_font()
    if not text or not family:
        _RASTER[key] = img
        return img
    font = QFont(family)
    font.setPixelSize(size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font)
        p.drawText(img.rect(), int(Qt.AlignmentFlag.AlignCenter), text)
    finally:
        p.end()
    _RASTER[key] = img
    while len(_RASTER) > _RASTER_MAX:
        _RASTER.popitem(last=False)
    return img


def clear_raster_cache() -> None:
    _RASTER.clear()


def anim_is_allowed(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return False
    if n in ALLOWED_ANIMS:
        return True
    return n.split("+", 1)[0] in ALLOWED_ANIMS


def _clamp_anim(name: str) -> str:
    n = str(name or "none").strip()
    return n if anim_is_allowed(n) else "none"


def sticker_anim_items(phase: str) -> list[tuple[str, str]]:
    """Dropdown (label, value) for sticker in/out. Subset of ANIMS."""
    from cutora.render.textfx.anim import ANIMS, SLIDE_DIRS

    items = []
    seen = set()
    for aid in ALLOWED_ANIMS:
        if aid in seen:
            continue
        seen.add(aid)
        if aid == "none":
            items.append(("无", "none"))
            continue
        defn = ANIMS.get(aid)
        if defn is None or phase not in defn.phases:
            continue
        if aid == "slide":
            for key, lab in SLIDE_DIRS:
                items.append((lab, "slide+" + key))
        else:
            items.append((defn.label_zh, aid))
    return items


def _anim_ctx(name: str) -> dict:
    from cutora.render.textfx.anim import _anim_param

    ctx = {}
    param = _anim_param(name)
    if param:
        ctx["dir"] = param
    return ctx


def sticker_transform(el: dict, ov: dict, t: float | None):
    """Reuse ANIMS. Identity when t is None or anim is none."""
    from cutora.render.textfx.anim import ANIMS, OUT_MIRROR, Transform, _anim_id

    if t is None or not isinstance(el, dict):
        return Transform()
    in_name = _clamp_anim(el.get("anim_in") or "none")
    out_name = _clamp_anim(el.get("anim_out") or "none")
    try:
        in_s = max(0.0, float(el.get("anim_in_s") or 0.25))
    except (TypeError, ValueError):
        in_s = 0.25
    try:
        out_s = max(0.0, float(el.get("anim_out_s") or 0.25))
    except (TypeError, ValueError):
        out_s = 0.25
    start = float(ov.get("start") or 0.0)
    end = float(ov.get("end") or (start + 1.0))
    tt = float(t)
    if in_name != "none" and in_s > 1e-6 and tt < start + in_s:
        u = max(0.0, min(1.0, (tt - start) / in_s))
        return ANIMS[_anim_id(in_name)].fn(u, _anim_ctx(in_name))
    if out_name != "none" and out_s > 1e-6 and tt >= end - out_s:
        u = max(0.0, min(1.0, (tt - (end - out_s)) / out_s))
        aid = _anim_id(out_name)
        raw_u = (1.0 - u) if aid in OUT_MIRROR else u
        return ANIMS[aid].fn(raw_u, _anim_ctx(out_name))
    return Transform()


def default_size_px(canvas_w: int, canvas_h: int) -> float:
    return DEFAULT_SIZE_FRAC * float(min(max(1, canvas_w), max(1, canvas_h)))
