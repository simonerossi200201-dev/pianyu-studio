# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""悬浮图标的开关、位置和边长。只改自己的键。"""
from __future__ import annotations

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.ui.theme.tokens import SPACE_4, SPACE_6

KEY_ENABLED = "ui.badge.enabled"
KEY_EDGE = "ui.badge.edge_hide"
KEY_X = "ui.badge.x"
KEY_Y = "ui.badge.y"
KEY_SIZE = "ui.badge.size"
KEY_DOCK = "ui.badge.dock"

# 48 / 96 / 128 / 256，都落在 8px 的缩放步长上。
SIZE_MIN = SPACE_6 + SPACE_4
SIZE_DEFAULT = SPACE_6 * 3
SIZE_MED = SPACE_6 * 4
SIZE_MAX = SPACE_6 * 8

_OWN_KEYS = (KEY_ENABLED, KEY_EDGE, KEY_X, KEY_Y, KEY_SIZE, KEY_DOCK)


def _load() -> dict:
    data = _load_app_settings()
    return data if isinstance(data, dict) else {}


def _write_keys(updates: dict) -> None:
    """Load, replace only the keys we own, save. Other settings stay put."""
    data = _load()
    for key, value in updates.items():
        if key not in _OWN_KEYS:
            continue
        data[key] = value
    _save_app_settings(data)


def badge_enabled() -> bool:
    """Missing key means on. A fresh install must show the badge."""
    data = _load()
    if KEY_ENABLED not in data:
        return True
    return bool(data.get(KEY_ENABLED))


def set_badge_enabled(on: bool) -> None:
    _write_keys({KEY_ENABLED: bool(on)})


def badge_edge_hide() -> bool:
    """Missing key means off."""
    data = _load()
    return bool(data.get(KEY_EDGE, False))


def set_badge_edge_hide(on: bool) -> None:
    _write_keys({KEY_EDGE: bool(on)})


def badge_layout() -> tuple:
    """``(x, y, size, dock)``. ``x`` / ``y`` are None when the user has not placed it."""
    data = _load()
    x = data.get(KEY_X)
    y = data.get(KEY_Y)
    try:
        size = int(data.get(KEY_SIZE, SIZE_DEFAULT))
    except (TypeError, ValueError):
        size = SIZE_DEFAULT
    dock = str(data.get(KEY_DOCK) or "")
    if x is not None:
        try:
            x = int(x)
        except (TypeError, ValueError):
            x = None
    if y is not None:
        try:
            y = int(y)
        except (TypeError, ValueError):
            y = None
    return x, y, size, dock


def save_badge_layout(x: int, y: int, size: int, dock: str) -> None:
    _write_keys({
        KEY_X: int(x),
        KEY_Y: int(y),
        KEY_SIZE: int(size),
        KEY_DOCK: str(dock or ""),
    })


def clear_badge_position() -> None:
    """Drop the saved spot so the next show uses the default corner.

    Does not touch the on/off switches.
    """
    data = _load()
    for key in (KEY_X, KEY_Y, KEY_DOCK):
        data.pop(key, None)
    _save_app_settings(data)
