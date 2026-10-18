# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q171b · 抠像阈值「色阶」：一条横条两个把手（左 = 透明阈值、右 = 不透明阈值），上面是 Matte 直方图。

和调色里的色阶一样读：直方图是这条视频抠出来的 Matte 里每种透明度有多少像素 ——
左边一堆 = 背景，右边一堆 = 人；中间那些就是「半透明的灰」。把左把手拖过背景那堆、右把手拖过人那堆，
中间的灰就全部变成纯透明 / 纯不透明。

``LevelsSlider``：纯绘制 + 拖动（没有业务逻辑），信号 ``moved(black, white)`` / ``released(black, white)``。
``KeyLevelsPanel``：放进右键菜单（QWidgetAction）的一块：标题、色阶条、「一起挪 / 统一成这个值」、收紧 / 放松 / 复原。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget

HIST_BINS = 64
MIN_GAP = 0.02


def _tok():
    from cutora.ui.theme.tokens import current
    return current()


class LevelsSlider(QWidget):
    moved = Signal(float, float)
    released = Signal(float, float)

    PAD = 10
    HIST_H = 46
    BAR_H = 8
    HANDLE = 9

    def __init__(self, black: float = 0.0, white: float = 1.0, parent=None):
        super().__init__(parent)
        self.black = float(black)
        self.white = float(white)
        self.hist: np.ndarray | None = None
        self.hist_note = ""
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(QSize(300, self.HIST_H + self.BAR_H + self.HANDLE + 22))
        self.setToolTip("左把手 = 透明阈值（比它更透明的全部变透明）\n右把手 = 不透明阈值（比它更实的全部变实）\n"
                        "两个往中间收 = 半透明的灰消失。←/→ 微调，Shift 加大步长")

    # ---- 数据 ----
    def set_values(self, black: float, white: float) -> None:
        self.black, self.white = self._clamp(black, white)
        self.update()

    def set_histogram(self, counts, note: str = "") -> None:
        self.hist = None if counts is None else np.asarray(counts, dtype=np.float64)
        self.hist_note = note
        self.update()

    @staticmethod
    def _clamp(b, w):
        b = max(0.0, min(1.0 - MIN_GAP, float(b)))
        w = max(b + MIN_GAP, min(1.0, float(w)))
        return round(b, 3), round(w, 3)

    # ---- 几何 ----
    def _x_of(self, v: float) -> float:
        return self.PAD + v * (self.width() - 2 * self.PAD)

    def _v_of(self, x: float) -> float:
        return (x - self.PAD) / max(1.0, self.width() - 2 * self.PAD)

    def _bar_rect(self) -> QRectF:
        return QRectF(self.PAD, self.HIST_H + 4, self.width() - 2 * self.PAD, self.BAR_H)

    # ---- 绘制 ----
    def paintEvent(self, ev):  # noqa: N802
        tok = _tok()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            hist_r = QRectF(self.PAD, 2, self.width() - 2 * self.PAD, self.HIST_H)
            p.fillRect(hist_r, QColor(tok.bg2))
            if self.hist is not None and self.hist.size and float(self.hist.max()) > 0:
                h = np.log1p(self.hist)
                h = h / max(1e-9, float(h.max()))
                n = len(h)
                path = QPainterPath(QPointF(hist_r.left(), hist_r.bottom()))
                for i, v in enumerate(h):
                    x0 = hist_r.left() + hist_r.width() * i / n
                    x1 = hist_r.left() + hist_r.width() * (i + 1) / n
                    y = hist_r.bottom() - v * (hist_r.height() - 3)
                    path.lineTo(x0, y)
                    path.lineTo(x1, y)
                path.lineTo(hist_r.right(), hist_r.bottom())
                path.closeSubpath()
                fill = QColor(tok.fg1)
                fill.setAlpha(150)
                p.fillPath(path, fill)
            else:
                p.setPen(QColor(tok.fg2))
                f = p.font()
                f.setPixelSize(11)
                p.setFont(f)
                p.drawText(hist_r, Qt.AlignmentFlag.AlignCenter, self.hist_note or "正在读这条的 Matte…")
            # 被裁掉的两边压暗（和色阶工具一样一眼看出裁掉了哪一段）
            shade = QColor(0, 0, 0, 120)
            xb, xw = self._x_of(self.black), self._x_of(self.white)
            p.fillRect(QRectF(hist_r.left(), hist_r.top(), xb - hist_r.left(), hist_r.height()), shade)
            p.fillRect(QRectF(xw, hist_r.top(), hist_r.right() - xw, hist_r.height()), shade)
            # 黑 → 白渐变条：只在两个把手之间过渡
            bar = self._bar_rect()
            grad = QLinearGradient(bar.left(), 0, bar.right(), 0)
            span = max(1e-3, bar.width())
            grad.setColorAt(0.0, QColor(0, 0, 0))
            grad.setColorAt(max(0.0, min(1.0, (xb - bar.left()) / span)), QColor(0, 0, 0))
            grad.setColorAt(max(0.0, min(1.0, (xw - bar.left()) / span)), QColor(255, 255, 255))
            grad.setColorAt(1.0, QColor(255, 255, 255))
            p.setPen(QPen(QColor(tok.line), 1))
            p.setBrush(grad)
            p.drawRoundedRect(bar, 3, 3)
            # 把手：▲ 左黑右白，描主题高亮色边
            for v, fill_c, key in ((self.black, QColor(20, 20, 20), "b"), (self.white, QColor(245, 245, 245), "w")):
                x = self._x_of(v)
                top = bar.bottom() + 2
                tri = QPainterPath(QPointF(x, top))
                tri.lineTo(x - self.HANDLE * 0.8, top + self.HANDLE)
                tri.lineTo(x + self.HANDLE * 0.8, top + self.HANDLE)
                tri.closeSubpath()
                pen = QPen(QColor(tok.accent if self._drag == key else tok.fg1), 1.4)
                p.setPen(pen)
                p.setBrush(fill_c)
                p.drawPath(tri)
                p.setPen(QPen(QColor(tok.accent), 1))
                p.drawLine(QPointF(x, hist_r.top()), QPointF(x, bar.bottom()))
            f = p.font()
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(tok.fg1))
            ty = bar.bottom() + self.HANDLE + 4
            p.drawText(QRectF(self.PAD, ty, 140, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "透明 %.2f" % self.black)
            p.drawText(QRectF(self.width() - self.PAD - 140, ty, 140, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "不透明 %.2f" % self.white)
        finally:
            p.end()

    # ---- 交互 ----
    def _pick(self, x: float) -> str:
        db, dw = abs(x - self._x_of(self.black)), abs(x - self._x_of(self.white))
        if abs(db - dw) < 1e-6:
            return "b" if x < self._x_of(self.black) else "w"
        return "b" if db < dw else "w"

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(ev)
        x = ev.position().x()
        self._drag = self._pick(x)
        self._drag_to(x)
        ev.accept()

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._drag:
            self._drag_to(ev.position().x())
            ev.accept()
        else:
            near = min(abs(ev.position().x() - self._x_of(self.black)), abs(ev.position().x() - self._x_of(self.white)))
            self.setCursor(Qt.CursorShape.SizeHorCursor if near < 10 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._drag and ev.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.update()
            self.released.emit(self.black, self.white)
            ev.accept()

    def _drag_to(self, x: float) -> None:
        v = self._v_of(x)
        if self._drag == "b":
            b, w = self._clamp(v, self.white)
        else:
            b, w = self._clamp(self.black, max(v, self.black + MIN_GAP))
        if (b, w) != (self.black, self.white):
            self.black, self.white = b, w
            self.update()
            self.moved.emit(b, w)

    def keyPressEvent(self, ev):  # noqa: N802
        k = ev.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 0.05 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
            sign = -1 if k == Qt.Key.Key_Left else 1
            if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.black, self.white = self._clamp(self.black, self.white + sign * step)
            else:
                self.black, self.white = self._clamp(self.black + sign * step, self.white)
            self.update()
            self.released.emit(self.black, self.white)
            ev.accept()
            return
        super().keyPressEvent(ev)


class KeyLevelsPanel(QWidget):
    """右键菜单里的一块：色阶条 + 作用方式 + 快捷按钮。apply_cb(black, white, mode) 由监视器提供。"""

    def __init__(self, black: float, white: float, n_targets: int, apply_cb, preview_cb=None, parent=None):
        super().__init__(parent)
        self.setObjectName("keyLevelsPanel")
        self._apply = apply_cb
        self._preview = preview_cb
        self._b0, self._w0 = float(black), float(white)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        title = QLabel("阈值（色阶）· 作用于选中的 %d 条" % n_targets if n_targets > 1 else "阈值（色阶）")
        title.setObjectName("keyLevelsTitle")
        lay.addWidget(title)
        self.slider = LevelsSlider(black, white, self)
        lay.addWidget(self.slider)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.rel = QRadioButton("一起挪（各自保留精调差异）")
        self.abs = QRadioButton("统一成这个值")
        self.rel.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rel)
        grp.addButton(self.abs)
        self.rel.setToolTip("每条按自己现在的阈值，挪同样的幅度（自动精调给每条算的值不会被抹平）")
        self.abs.setToolTip("选中的每条都设成色阶条上的这两个数")
        row.addWidget(self.rel)
        row.addWidget(self.abs)
        row.addStretch(1)
        lay.addLayout(row)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        for text, db, dw, tip in (("收紧", 0.05, -0.05, "两个把手各往中间收 0.05：灰更少、边更硬"),
                                  ("大幅收紧", 0.15, -0.15, "各往中间收 0.15"),
                                  ("放松", -0.05, 0.05, "各往外放 0.05：边更柔"),
                                  ("复原", None, None, "透明 0、不透明 1（不裁）")):
            b = QPushButton(text, self)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, d1=db, d2=dw: self._quick(d1, d2))
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.slider.released.connect(self._commit)
        self.slider.moved.connect(self._moved)

    def mode(self) -> str:
        return "abs" if self.abs.isChecked() else "rel"

    def _moved(self, b, w):
        if callable(self._preview):
            self._preview(b, w)

    def _commit(self, b, w):
        if callable(self._apply):
            self._apply(b, w, self.mode(), self._b0, self._w0)
        self._b0, self._w0 = b, w

    def _quick(self, db, dw):
        if db is None:
            b, w = 0.0, 1.0
            mode = "abs"
        else:
            b, w = LevelsSlider._clamp(self.slider.black + db, self.slider.white + dw)
            mode = self.mode()
        self.slider.set_values(b, w)
        if callable(self._apply):
            self._apply(self.slider.black, self.slider.white, mode, self._b0, self._w0)
        self._b0, self._w0 = self.slider.black, self.slider.white


def alpha_histogram(alpha: np.ndarray, bins: int = HIST_BINS) -> np.ndarray:
    a = np.asarray(alpha, dtype=np.float32).ravel()
    counts, _e = np.histogram(a, bins=bins, range=(0.0, 1.0))
    return counts
