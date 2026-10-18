# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q173 · 主窗口底部的「正在打开…」进度条：打开批量加载 / 拼接 / 中央监视器这种要建一大堆界面的窗口时，
用户看得到它在动、走到哪一步了，不会以为软件卡死。

用法::

    busy = start_busy(win, "正在打开批量加载")
    report(0.3, "素材页")          # 库代码里随处可调；没有进行中的进度条就什么都不做
    ...
    busy.done()

每次 ``report`` 都让界面画一下这根条（只处理绘制类事件，不处理鼠标键盘，不会被重入）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QWidget

_STATE = {"bar": None}


def _tok():
    from cutora.ui.theme.tokens import current
    return current()


class BusyBar(QWidget):
    H = 26

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setObjectName("busyBar")
        self.title = str(title or "")
        self.text = ""
        self.frac = 0.0
        self._shown = 0.0
        self._t0 = time.monotonic()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(self.H)
        self._anim = QTimer(self)
        self._anim.setInterval(30)
        self._anim.timeout.connect(self._tick)
        self._anim.start()
        self._place()
        self.show()
        self.raise_()

    def _place(self):
        p = self.parentWidget()
        if p is None:
            return
        w = min(520, max(320, int(p.width() * 0.36)))
        sb = getattr(p, "statusBar", None)
        bottom = p.height()
        try:
            if callable(sb) and sb() is not None and sb().isVisible():
                bottom = sb().geometry().top()
        except Exception:
            pass
        self.setGeometry((p.width() - w) // 2, bottom - self.H - 8, w, self.H)

    def set_progress(self, frac: float, text: str = "") -> None:
        self.frac = max(self.frac, min(1.0, float(frac)))
        if text:
            self.text = str(text)
        self._place()
        self._shown = max(self._shown, self.frac * 0.9)
        self.repaint()

    def _tick(self):
        # 两步之间也慢慢往前爬一点（不超过下一步），一直在动 = 没卡
        target = min(0.98, self.frac + 0.08)
        if self._shown < target:
            self._shown = min(target, self._shown + 0.004)
            self.update()

    def paintEvent(self, ev):  # noqa: N802
        tok = _tok()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            bg = QColor(tok.bg1)
            bg.setAlpha(235)
            p.setPen(QColor(tok.line))
            p.setBrush(bg)
            from cutora.ui.theme.tokens import RADIUS_CARD
            p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
            track = QRectF(r.left() + 10, r.bottom() - 7, r.width() - 20, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tok.bg3))
            pill = track.height() / 2.0              # 细条两头全圆（半径 = 半高，不是固定数）
            p.drawRoundedRect(track, pill, pill)
            fill = QRectF(track.left(), track.top(), track.width() * max(0.02, self._shown), track.height())
            g = QLinearGradient(fill.left(), 0, fill.right(), 0)
            g.setColorAt(0.0, QColor(tok.accent_dim))
            g.setColorAt(1.0, QColor(tok.accent))
            p.setBrush(g)
            p.drawRoundedRect(fill, pill, pill)
            f = p.font()
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor(tok.fg0))
            label = self.title + ("  ·  " + self.text if self.text else "")
            p.drawText(QRectF(r.left() + 12, r.top() + 2, r.width() - 70, r.height() - 9),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(r.width() - 80)))
            p.setPen(QColor(tok.fg2))
            p.drawText(QRectF(r.right() - 60, r.top() + 2, 48, r.height() - 9),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       "%d%%" % round(self._shown * 100))
        finally:
            p.end()

    def done(self) -> None:
        self._anim.stop()
        if _STATE.get("bar") is self:
            _STATE["bar"] = None
        self.hide()
        self.deleteLater()


def start_busy(win, title: str) -> BusyBar:
    old = _STATE.get("bar")
    if old is not None:
        try:
            old.done()
        except RuntimeError:
            pass
    bar = BusyBar(win, title)
    _STATE["bar"] = bar
    report(0.03, "")
    return bar


def report(frac: float, text: str = "") -> None:
    """库代码里报一步。没有进行中的进度条：什么都不做（测试 / 命令行照常）。"""
    bar = _STATE.get("bar")
    if bar is None:
        return
    try:
        bar.set_progress(frac, text)
        app = QApplication.instance()
        if app is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 5)
    except RuntimeError:
        _STATE["bar"] = None


def finish() -> None:
    bar = _STATE.get("bar")
    if bar is not None:
        try:
            bar.done()
        except RuntimeError:
            _STATE["bar"] = None
