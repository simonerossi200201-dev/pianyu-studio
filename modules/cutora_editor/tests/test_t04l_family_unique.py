# -*- coding: utf-8 -*-
"""T04L 3c: registered subtitle fonts must not collide on family+style."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(REPO, "resources", "fonts")
SKIP = {"_quarantine", "_extra", "fonts_pending", "Noto_Color_Emoji"}
EXTS = {".ttf", ".otf", ".ttc"}


def _iter_font_files():
    files = []
    for root, dirs, names in os.walk(FONT_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fn in names:
            if os.path.splitext(fn)[1].lower() in EXTS:
                files.append(os.path.join(root, fn))
    return files


def _name_id(font, nid: int) -> str:
    table = font["name"]
    for rec in table.names:
        if rec.nameID != nid:
            continue
        if rec.platformID not in (1, 3):
            continue
        try:
            text = rec.toUnicode().strip()
        except Exception:
            continue
        if text:
            return text
    return ""


def test_registered_fonts_family_style_unique():
    from fontTools.ttLib import TTFont
    files = _iter_font_files()
    assert len(files) == 240
    seen: dict[tuple[str, str], str] = {}
    dups = []
    for path in files:
        font = TTFont(path, lazy=True, fontNumber=0)
        try:
            family = _name_id(font, 1) or os.path.basename(path)
            style = _name_id(font, 2) or "Regular"
        finally:
            font.close()
        key = (family, style)
        rel = os.path.relpath(path, FONT_DIR).replace("\\", "/")
        if key in seen:
            dups.append((family, style, seen[key], rel))
        else:
            seen[key] = rel
    assert not dups, "same family+style in two files: %s" % dups[:8]
