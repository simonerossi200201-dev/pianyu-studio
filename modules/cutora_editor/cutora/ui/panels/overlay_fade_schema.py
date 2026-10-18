# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Overlay / clip four-edge fade widget registry (no Qt).

Adding a fade field without a PropertyPanel attribute makes the registry test red.
"""
from __future__ import annotations

FADE_FIELDS = ("fade_top", "fade_bottom", "fade_left", "fade_right")

# JSON field → PropertyPanel attribute (overlay + sticker share these).
OVERLAY_FADE_WIDGETS: dict[str, str] = {
    "fade_top": "ov_fade_top",
    "fade_bottom": "ov_fade_bottom",
    "fade_left": "ov_fade_left",
    "fade_right": "ov_fade_right",
    "fade_top_offset": "ov_fade_top_offset",
    "fade_bottom_offset": "ov_fade_bottom_offset",
    "fade_left_offset": "ov_fade_left_offset",
    "fade_right_offset": "ov_fade_right_offset",
    "fade_sync": "ov_fade_sync",
}

VIDEO_FADE_WIDGETS: dict[str, str] = {
    "fade_top": "vid_fade_top",
    "fade_bottom": "vid_fade_bottom",
    "fade_left": "vid_fade_left",
    "fade_right": "vid_fade_right",
    "fade_top_offset": "vid_fade_top_offset",
    "fade_bottom_offset": "vid_fade_bottom_offset",
    "fade_left_offset": "vid_fade_left_offset",
    "fade_right_offset": "vid_fade_right_offset",
    "fade_sync": "vid_fade_sync",
}


def overlay_fade_widget_names() -> tuple[str, ...]:
    return tuple(OVERLAY_FADE_WIDGETS.values()) + tuple(VIDEO_FADE_WIDGETS.values())
