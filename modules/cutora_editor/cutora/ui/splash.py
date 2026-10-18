# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Startup splash screen.

Background is ``splash_bg.png`` (name / mark / tagline / version already baked).
Runtime draws only the progress caption and the progress bar (brand_final §四).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import QApplication, QDialog, QSplashScreen

from cutora.core.paths import APP_ICON, APP_ICON_PNG, SPLASH_PNG
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import BG0, RADIUS_SM

# Spec §四, 1280×720. Caption top-left; slot is 7px tall with 4px corners.
MSG_POS = (132, 632)
MSG_SIZE_PX = 18
MSG_COLOR = QColor("#B0A392")
SLOT = QRectF(132, 668, 1148 - 132, 675 - 668)
SLOT_RADIUS = float(RADIUS_SM)
SLOT_COLOR = QColor("#302921")
FILL_COLOR = QColor("#CCA650")

_STAGE_PROGRESS = {
    "加载字体…": 0.34,
    "检测显卡…": 0.67,
    "加载界面…": 1.0,
}


def _caption_font() -> QFont:
    """System CJK font: first splash message is drawn before embedded fonts."""
    font = QFont()
    font.setPixelSize(MSG_SIZE_PX)
    families = set(QFontDatabase.families())
    for name in (
        "Microsoft YaHei", "Microsoft YaHei UI", "Noto Sans SC", "SimHei",
    ):
        if name in families:
            font.setFamily(name)
            return font
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for fname in ("msyh.ttc", "msyh.ttf", "msyhbd.ttc"):
        path = os.path.join(windir, "Fonts", fname)
        if not os.path.isfile(path):
            continue
        fid = QFontDatabase.addApplicationFont(path)
        loaded = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if loaded:
            font.setFamily(loaded[0])
            return font
    return font


def _paint_slot(painter: QPainter, progress: float) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(SLOT_COLOR)
    painter.drawRoundedRect(SLOT, SLOT_RADIUS, SLOT_RADIUS)
    if progress <= 0:
        return
    path = QPainterPath()
    path.addRoundedRect(SLOT, SLOT_RADIUS, SLOT_RADIUS)
    painter.save()
    painter.setClipPath(path)
    painter.setBrush(FILL_COLOR)
    painter.drawRect(QRectF(
        SLOT.x(), SLOT.y(), SLOT.width() * progress, SLOT.height()))
    painter.restore()


class _ModalGuard(QObject):
    """Q131 兜底：闪屏置顶期间一旦有对话框要显示，就先把闪屏收起来。

    闪屏带 WindowStaysOnTopHint，启动路径上任何一个模态框（单实例锁提示、
    迁移、报错……）都会被压在它下面：看不见、点不到，模态框又堵着事件循环，
    整个程序就「卡在加载界面」。主路径已经改成等主窗口出来再弹，这里保证
    以后谁再在启动期间弹框，也不会再被闪屏盖住。
    """

    def __init__(self, splash: "BrandSplash") -> None:
        super().__init__(splash)
        self._splash = splash

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        try:
            if (event.type() == QEvent.Type.Show and isinstance(obj, QDialog)
                    and obj.isWindow() and self._splash.isVisible()):
                self._splash.hide()
                obj.raise_()
                obj.activateWindow()
        except RuntimeError:
            pass
        return False


class BrandSplash(QSplashScreen):
    """Paints progress caption + bar; everything else is in the background."""

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__(pixmap)
        self._progress = 0.0
        self._modal_guard = _ModalGuard(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._modal_guard)

    def _drop_modal_guard(self) -> None:
        guard = getattr(self, "_modal_guard", None)
        app = QApplication.instance()
        if guard is not None and app is not None:
            app.removeEventFilter(guard)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._drop_modal_guard()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is not None and getattr(self, "_modal_guard", None) is not None:
            app.removeEventFilter(self._modal_guard)
            app.installEventFilter(self._modal_guard)
        super().showEvent(event)

    def set_progress(self, fraction: float) -> None:
        self._progress = max(0.0, min(1.0, float(fraction)))
        self.update()

    def drawContents(self, painter: QPainter) -> None:
        _paint_slot(painter, self._progress)
        text = self.message() or ""
        if not text:
            return
        painter.setFont(_caption_font())
        painter.setPen(MSG_COLOR)
        painter.drawText(
            QRect(MSG_POS[0], MSG_POS[1], int(SLOT.width()), 28),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            text,
        )


def load_app_icon() -> QIcon:
    if os.path.isfile(APP_ICON):
        return QIcon(APP_ICON)
    if os.path.isfile(APP_ICON_PNG):
        return QIcon(APP_ICON_PNG)
    return QIcon()


def make_splash() -> QSplashScreen:
    if os.path.isfile(SPLASH_PNG):
        pix = QPixmap(SPLASH_PNG)
    else:
        pix = QPixmap(1280, 720)
        pix.fill(QColor(BG0))
    splash = BrandSplash(pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return splash


def splash_message(splash: QSplashScreen, text: str,
                   progress: float | None = None) -> None:
    if isinstance(splash, BrandSplash):
        frac = _STAGE_PROGRESS.get(text) if progress is None else progress
        if frac is not None:
            splash.set_progress(frac)
    splash.showMessage(tr(text), Qt.AlignmentFlag.AlignLeft, MSG_COLOR)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
