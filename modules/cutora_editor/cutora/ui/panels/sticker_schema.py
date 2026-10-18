# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sticker widget registry (no Qt). Missing attributes make the test red."""
from __future__ import annotations

STICKER_PANEL_WIDGETS: dict[str, str] = {
    "search": "sticker_search",
    "categories": "sticker_tabs",
    "grid": "sticker_grid",
}

STICKER_PROPERTY_WIDGETS: dict[str, str] = {
    "anim_in": "ov_sticker_anim_in",
    "anim_out": "ov_sticker_anim_out",
}

STICKER_ELEMENT_FIELDS = (
    "sticker_id", "anim_in", "anim_out", "anim_in_s", "anim_out_s",
)


def sticker_panel_widget_names() -> tuple[str, ...]:
    return tuple(STICKER_PANEL_WIDGETS.values())


def sticker_property_widget_names() -> tuple[str, ...]:
    return tuple(STICKER_PROPERTY_WIDGETS.values())
