# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load generated QSS and the few remaining dynamic inline styles."""
from __future__ import annotations

import os

from cutora.core.log import get_logger
from cutora.core.paths import THEME_QSS
from cutora.ui.theme.tokens import RADIUS_CTL, current, hex_rgb, rgba

_LOG = get_logger("cutora.ui.theme.apply")
_LAST_QSS = ""


def load_theme_qss() -> str:
    """Read the generated dark theme. Empty string if the file is missing."""
    if not os.path.isfile(THEME_QSS):
        _LOG.error("theme QSS missing: %s", THEME_QSS)
        return ""
    with open(THEME_QSS, "r", encoding="utf-8") as fh:
        return fh.read()


def apply_app_qss(target) -> None:
    """Apply the current in-memory QSS, falling back to the committed file."""
    text = _LAST_QSS or load_theme_qss()
    if not text:
        return
    target.setStyleSheet(text)


def apply_theme(theme_id: str, accent_override: str | None = None) -> None:
    """Switch the process palette, rebuild QSS in memory, restyle the app."""
    global _LAST_QSS
    from PySide6.QtWidgets import QApplication, QWidget

    from cutora.core.history import _load_app_settings, _save_app_settings
    from cutora.core.paths import user_cache_dir
    from cutora.ui.icons import clear_icon_cache, recolor_bound_icons
    from cutora.ui.theme.qss import render, write_qss_icons
    from cutora.ui.theme.tokens import load_theme, qss_context

    tset = load_theme(theme_id, accent_override=accent_override)
    ctx = qss_context()
    cache = os.path.join(user_cache_dir(), "theme_qss")
    write_qss_icons(ctx, dest_dir=cache)
    slash = cache.replace("\\", "/")
    ctx["qss_check"] = slash + "/qss_check.svg"
    ctx["qss_check_disabled"] = slash + "/qss_check_disabled.svg"
    ctx["qss_chevron_up"] = slash + "/qss_chevron_up.svg"
    ctx["qss_chevron_down"] = slash + "/qss_chevron_down.svg"
    qss = render(ctx)
    _LAST_QSS = qss
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss)
        clear_icon_cache()
        recolor_bound_icons()
        for top in app.topLevelWidgets():
            top.update()
            for child in top.findChildren(QWidget):
                child.update()
    s = _load_app_settings()
    s["theme_id"] = tset.theme_id
    if accent_override:
        s["theme_accent"] = accent_override
    elif "theme_accent" in s:
        s.pop("theme_accent", None)
    _save_app_settings(s)


def swatch_fg(hex_color: str) -> str:
    """Label color that stays readable on a swatch fill."""
    t = current()
    try:
        r, g, b = hex_rgb(hex_color)
    except Exception:
        return t.fg0
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return t.bg0 if luma > 0.62 else t.fg0


def apply_swatch(widget, color: str) -> None:
    """Color chip whose fill is a user-chosen hex (cannot live in QSS)."""
    t = current()
    hex_color = color or t.line
    try:
        r, g, b = hex_rgb(hex_color)
        h = (hex_color or "").lstrip("#")
        alpha = int(h[6:8], 16) if len(h) >= 8 else 255
        fill = "rgba(%d, %d, %d, %d)" % (r, g, b, alpha)
    except Exception:
        fill = hex_color
    widget.setStyleSheet(
        "background-color: %s; color: %s; border: 1px solid %s; "
        "border-radius: %dpx; padding: 2px 8px;"
        % (fill, swatch_fg(hex_color), t.line, RADIUS_CTL)
    )


def apply_inline_editor(widget, color: str | None = None) -> None:
    """On-canvas text editor: overlay chrome, not product chrome."""
    t = current()
    fg = color or t.fg0
    widget.setStyleSheet(
        "QTextEdit { color: %s; background: %s; border: 1px dashed %s;"
        " selection-background-color: %s; }"
        % (fg, rgba(t.bg0, 0.62), rgba(t.fg0, 0.40), rgba(t.fg0, 0.25))
    )


def restyle(widget) -> None:
    """Re-apply QSS after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
