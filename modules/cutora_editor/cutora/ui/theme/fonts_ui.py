# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Register Inter / JetBrains Mono / Noto Sans SC for UI chrome only.

Inter / JetBrains Mono live in ``resources/fonts_pending/`` (UI only).
Noto Sans SC is the subtitle-pool copy under ``resources/fonts/``.
Call this from the GUI path after ``load_embedded_fonts()``.
"""
from __future__ import annotations

import os

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES
from cutora.ui.theme.tokens import (
    FONT_MONO, FONT_SIZE_BODY, FONT_UI, FONT_UI_CJK,
)

_LOG = get_logger("cutora.ui.theme.fonts_ui")

# (family, path relative to resources/)
UI_FONT_FILES = (
    (FONT_UI, os.path.join("fonts_pending", "Inter", "Interopsz_wght.ttf")),
    (FONT_MONO, os.path.join("fonts_pending", "JetBrains_Mono", "JetBrainsMonowght.ttf")),
    (FONT_UI_CJK, os.path.join("fonts", "Noto_Sans_SC", "NotoSansSCwght.ttf")),
)

_registered: list[str] = []


def ui_font_abs(rel: str) -> str:
    return os.path.join(RESOURCES, rel.replace("/", os.sep))


def register_ui_fonts() -> list[str]:
    """Load UI fonts into ``QFontDatabase``. Returns loaded family names.

    Safe to call more than once. Failures are logged; they do not abort
    startup (system CJK fallback still works).
    """
    global _registered
    if _registered:
        return list(_registered)
    from PySide6.QtGui import QFontDatabase

    loaded: list[str] = []
    for _expected, rel in UI_FONT_FILES:
        path = ui_font_abs(rel)
        if not os.path.isfile(path):
            _LOG.error("UI font missing: %s", path)
            continue
        fid = QFontDatabase.addApplicationFont(path)
        if fid < 0:
            _LOG.error("UI font failed to register: %s", path)
            continue
        families = list(QFontDatabase.applicationFontFamilies(fid) or [])
        if families:
            loaded.extend(families)
            _LOG.info("UI font registered: %s (%s)", families[0], rel)
        else:
            _LOG.error("UI font registered with no family: %s", path)
    _registered = loaded
    return list(loaded)


def ui_qfont(pixel_size: int = FONT_SIZE_BODY, bold: bool = False):
    """Application UI font: Inter, then Noto Sans SC, then system YaHei."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies([FONT_UI, FONT_UI_CJK, "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPixelSize(int(pixel_size))
    font.setBold(bool(bold))
    return font


def mono_qfont(pixel_size: int = FONT_SIZE_BODY):
    """Timecode / monospace UI font."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies([FONT_MONO, "Consolas", "Cascadia Mono"])
    font.setPixelSize(int(pixel_size))
    return font
