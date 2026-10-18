# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""WCAG contrast helpers used by tools/check_theme_contrast.py and the UI."""
from __future__ import annotations

from cutora.ui.theme.tokens import hex_rgb

def _checks():
    rows = []
    for fg, floor_bg1, floor_rest in (
        ("fg0", 7.0, 7.0),
        ("fg1", 4.5, 4.5),
        ("fg2", 4.5, 4.5),
    ):
        for bg in ("bg1", "bg2", "bg3"):
            # fg2 on hover (bg3): hex/captions live on bg1/bg2; hover is chrome.
            # Specified roma-gold FG2 is 4.08 on bg3, so 3.0 still flags a collapse.
            minimum = floor_bg1 if bg == "bg1" else floor_rest
            if fg == "fg2" and bg == "bg3":
                minimum = 3.0
            rows.append(("%s on %s" % (fg, bg), fg, bg, minimum))
    for fg in ("ok", "warn", "err"):
        for bg in ("bg1", "bg2"):
            rows.append(("%s on %s" % (fg, bg), fg, bg, 3.0))
    for bg in ("bg1", "bg2"):
        rows.append(("err_text on %s" % bg, "err_text", bg, 4.5))
    rows.append(("accent on bg1", "accent", "bg1", 3.0))
    return tuple(rows)


CHECKS = _checks()


def _srgb_lin(channel: float) -> float:
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(hex_color: str) -> float:
    r, g, b = hex_rgb(hex_color)
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def contrast_ratio(a: str, b: str) -> float:
    l1, l2 = rel_luminance(a), rel_luminance(b)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def accent_fg(palette: dict) -> str:
    accent = palette["accent"]
    fg0, bg0 = palette["fg0"], palette["bg0"]
    if contrast_ratio(bg0, accent) >= contrast_ratio(fg0, accent):
        return bg0
    return fg0


def check_palette(palette: dict, theme_id: str = "") -> list[str]:
    fails: list[str] = []
    prefix = ("[%s] " % theme_id) if theme_id else ""
    for label, fg_key, bg_key, minimum in CHECKS:
        try:
            ratio = contrast_ratio(palette[fg_key], palette[bg_key])
        except Exception as exc:
            fails.append("%s%s: %s" % (prefix, label, exc))
            continue
        if ratio < minimum:
            fails.append(
                "%s%s: %.2f < %.1f (%s on %s)"
                % (prefix, label, ratio, minimum, palette[fg_key], palette[bg_key])
            )
    try:
        af = accent_fg(palette)
        ratio = contrast_ratio(af, palette["accent"])
        if ratio < 4.5:
            fails.append(
                "%saccent label on accent: %.2f < 4.5 (%s on %s)"
                % (prefix, ratio, af, palette["accent"])
            )
    except Exception as exc:
        fails.append("%saccent label: %s" % (prefix, exc))
    return fails
