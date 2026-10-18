# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Status-bar FPS capsule. Hidden with zero layout width until it has a reading."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

from cutora.core.fps_meter import (
    THROTTLE_S,
    color_token,
    format_export_tooltip,
    format_preview_tooltip,
)
from cutora.ui.theme.fonts_ui import mono_qfont
from cutora.ui.theme.tokens import (
    FONT_SIZE_SM,
    RADIUS_CTL,
    SPACE_1,
    SPACE_2,
    current,
)

PREVIEW_GLYPH = "⏵"
EXPORT_GLYPH = "⏺"
_VALUE_PROBE = "88.8"


class FpsMeter(QFrame):
    """Left-edge status capsule: glyph + monospace fps. Idle = not visible."""

    def __init__(self, parent=None, now=None) -> None:
        super().__init__(parent)
        self.setObjectName("FpsMeter")
        self._now = now or time.perf_counter
        self._has_data = False
        self._mode = ""
        self._tone = "ok"
        self._value_hex = ""
        self._fps = 0.0
        self._last_paint_t: float | None = None
        self.visual_updates = 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SPACE_1, 0, SPACE_2, 0)
        lay.setSpacing(SPACE_1)
        self._glyph = QLabel()
        self._glyph.setObjectName("FpsMeterGlyph")
        self._value = QLabel()
        self._value.setObjectName("FpsMeterValue")
        font = mono_qfont(FONT_SIZE_SM)
        self._value.setFont(font)
        self._glyph.setFont(font)
        advance = QFontMetrics(font).horizontalAdvance(_VALUE_PROBE)
        self._value.setMinimumWidth(max(1, advance))
        self._value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        lay.addWidget(self._glyph, 0)
        lay.addWidget(self._value, 0)
        self._apply_chrome()
        super().setVisible(False)

    def tone(self) -> str:
        return self._tone

    def value_hex(self) -> str:
        return self._value_hex

    def setVisible(self, visible: bool) -> None:
        if visible and not self._has_data:
            visible = False
        super().setVisible(visible)

    def sizeHint(self) -> QSize:
        if not self._has_data:
            return QSize(0, 0)
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        if not self._has_data:
            return QSize(0, 0)
        return super().minimumSizeHint()

    def clear(self) -> None:
        self._has_data = False
        self._mode = ""
        self._fps = 0.0
        self._last_paint_t = None
        self.setToolTip("")
        super().setVisible(False)

    def set_preview(
        self,
        fps: float,
        target: float = 30.0,
        n: int = 30,
        force: bool = False,
    ) -> None:
        self._fps = float(fps)
        self._mode = "preview"
        self.setToolTip(format_preview_tooltip(self._fps, target, n))
        tone = color_token(self._fps, target)
        self._commit_visual(PREVIEW_GLYPH, tone, force=force)

    def set_export(
        self,
        fps: float,
        done: int = 0,
        total: int = 0,
        force: bool = False,
    ) -> None:
        self._fps = float(fps)
        self._mode = "export"
        remain = None
        if fps > 0 and total > done >= 0:
            remain = (int(total) - int(done)) / float(fps)
        self.setToolTip(
            format_export_tooltip(self._fps, done, total, remain_s=remain)
        )
        tone = color_token(self._fps, 30.0) if fps > 0 else "ok"
        self._commit_visual(EXPORT_GLYPH, tone, force=force)

    def _commit_visual(self, glyph: str, tone: str, force: bool = False) -> None:
        now = float(self._now())
        if (
            not force
            and self._last_paint_t is not None
            and (now - self._last_paint_t) < THROTTLE_S
        ):
            return
        self._last_paint_t = now
        self._tone = tone
        self._has_data = True
        self.visual_updates += 1
        tok = current()
        color = getattr(tok, tone, tok.ok)
        self._value_hex = str(color)
        self._glyph.setText(glyph)
        self._value.setText("%.1f" % self._fps)
        self._apply_chrome(color)
        super().setVisible(True)
        self.update()

    def _apply_chrome(self, fg: str | None = None) -> None:
        tok = current()
        color = fg or tok.fg1
        self.setStyleSheet(
            "QFrame#FpsMeter {"
            " background: %s;"
            " border: 1px solid %s;"
            " border-radius: %dpx;"
            " padding: 0px;"
            "}"
            " QLabel#FpsMeterGlyph, QLabel#FpsMeterValue {"
            " color: %s;"
            " font-size: %dpx;"
            " background: transparent;"
            " border: none;"
            " padding: 0px;"
            "}"
            % (tok.bg2, tok.line, RADIUS_CTL, color, FONT_SIZE_SM)
        )
