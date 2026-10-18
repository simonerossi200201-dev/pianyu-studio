# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe-zone widget registry (no Qt). Missing attributes make the test red."""
from __future__ import annotations

from cutora.render.safe_zones import SETTINGS_MASTER, SETTINGS_PRESETS, presets

SAFE_ZONE_WINDOW_WIDGETS: dict[str, str] = {
    SETTINGS_MASTER: "act_safe_zones",
    SETTINGS_PRESETS: "safe_zone_preset_actions",
}

SAFE_ZONE_CAPTION_WIDGETS: dict[str, str] = {
    "hint": "safe_zone_hint",
    "move_btn": "btn_move_to_safe_zone",
    "disclaimer": "safe_zone_disclaimer",
}


def safe_zone_window_widget_names() -> tuple[str, ...]:
    return tuple(SAFE_ZONE_WINDOW_WIDGETS.values())


def safe_zone_caption_widget_names() -> tuple[str, ...]:
    return tuple(SAFE_ZONE_CAPTION_WIDGETS.values())


def required_preset_ids() -> tuple[str, ...]:
    return tuple(str(p.get("id")) for p in presets())
