# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lucide SVG icons, tinted with Roma tokens and cached as QIcon."""
from __future__ import annotations

import os

from weakref import WeakKeyDictionary

from cutora.core.log import get_logger
from cutora.core.paths import ICON_DIR

_LOG = get_logger("cutora.ui.icons")

LUCIDE_DIR = os.path.join(ICON_DIR, "lucide")

# Old CUTORA-original filenames → Lucide stem.
LEGACY_ALIAS = {
    "tool_text": "type",
    "tool_text.svg": "type",
    "tool_image": "image",
    "tool_image.svg": "image",
    "tool_audio": "music",
    "tool_audio.svg": "music",
    "btn_match": "captions",
    "btn_match.svg": "captions",
    "btn_export": "download",
    "btn_export.svg": "download",
    "btn_stop": "square",
    "btn_stop.svg": "square",
    "Track_Sharing": "copy",
    "Track_Sharing.svg": "copy",
    "ShareT": "share-2",
    "ShareT.svg": "share-2",
    "Batch_Add_Music": "list-plus",
    "Batch_Add_Music.svg": "list-plus",
}

_CACHE: dict[tuple, object] = {}
_BINDINGS: WeakKeyDictionary = WeakKeyDictionary()


def lucide_path(name: str) -> str:
    """Resolve a Lucide stem or a legacy filename to an SVG path."""
    stem = LEGACY_ALIAS.get(name) or LEGACY_ALIAS.get(os.path.basename(name)) or name
    if stem.lower().endswith(".svg"):
        stem = stem[:-4]
    return os.path.join(LUCIDE_DIR, stem + ".svg")


def icon(name: str, color: str | None = None, size: int = 20):
    """Tinted Lucide ``QIcon``. ``name`` is a Lucide stem or a legacy alias."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    if color:
        hex_color = color.lower()
    else:
        from cutora.ui.theme.tokens import current
        hex_color = current().fg0.lower()
    key = (name, hex_color, int(size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    path = lucide_path(name)
    if not os.path.isfile(path):
        _LOG.error("Lucide icon missing: %s (%s)", name, path)
        empty = QIcon()
        _CACHE[key] = empty
        return empty
    px = max(8, int(size))
    renderer = QSvgRenderer(path)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pm.rect(), QColor(hex_color))
    painter.end()
    out = QIcon()
    out.addPixmap(pm, QIcon.Mode.Normal)
    out.addPixmap(pm, QIcon.Mode.Active)
    out.addPixmap(pm, QIcon.Mode.Selected)
    out.addPixmap(pm, QIcon.Mode.Disabled)
    _CACHE[key] = out
    return out


def pixmap(name: str, color: str | None = None, size: int = 20):
    """Tinted Lucide ``QPixmap`` (for QPainter, not QIcon)."""
    return icon(name, color=color, size=size).pixmap(int(size), int(size))


def clear_icon_cache() -> None:
    _CACHE.clear()


def bind_icon(widget, name: str, size: int = 20, color_token: str | None = None):
    """setIcon + register so apply_theme can recolor without findChildren."""
    from cutora.ui.theme.tokens import current

    hex_color = None if color_token is None else getattr(current(), color_token)
    ic = icon(name, color=hex_color, size=size)
    widget.setIcon(ic)
    try:
        _BINDINGS[widget] = (name, int(size), color_token)
    except TypeError:
        pass
    return ic


def recolor_bound_icons() -> None:
    """Re-setIcon on every live bound widget using the current palette."""
    from cutora.ui.theme.tokens import current

    t = current()
    for widget, (name, size, color_token) in list(_BINDINGS.items()):
        try:
            hex_color = None if color_token is None else getattr(t, color_token)
            widget.setIcon(icon(name, color=hex_color, size=size))
        except RuntimeError:
            continue


def populate_icon_menu(menu, entries, size: int = 16, color_token: str = "fg1"):
    """Fill ``menu`` with Lucide-icon actions. ``None`` entries are separators."""
    actions = []
    for entry in entries:
        if entry is None:
            menu.addSeparator()
            continue
        text, name, slot = entry
        act = menu.addAction(text)
        act.setIconVisibleInMenu(True)
        bind_icon(act, name, size=size, color_token=color_token)
        if slot is not None:
            act.triggered.connect(slot)
        actions.append(act)
    return actions
