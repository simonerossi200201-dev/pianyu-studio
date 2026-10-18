# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Left-dock transition library: video (T20c) + audio tapes. Drag / double-click."""
from __future__ import annotations

import json
import math

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QDrag, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from cutora.render.anim_engine import (
    AUDIO_TRANSITION_LIBRARY,
    STANDALONE_TRANSITION_LIBRARY,
    audio_transition_duration,
    compose_standalone_transition,
)
from cutora.ui.i18n import tr

VIDEO_ITEMS = list(STANDALONE_TRANSITION_LIBRARY)
AUDIO_ITEMS = list(AUDIO_TRANSITION_LIBRARY)
TRANSITION_MIME = "application/x-cutora-transition"
THUMB_W = 88
THUMB_H = 48


def _solid(w, h, rgb):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rgb))
    return img


def _textured_thumb_pair(w, h):
    """Builtin A/B with edges and grain — solids make every thumb look like a dissolve."""
    from PySide6.QtGui import QBrush, QLinearGradient

    def _one(kind):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            g = QLinearGradient(0, 0, w, h)
            if kind == "a":
                g.setColorAt(0.0, QColor(36, 82, 140))
                g.setColorAt(1.0, QColor(18, 36, 64))
            else:
                g.setColorAt(0.0, QColor(196, 118, 42))
                g.setColorAt(1.0, QColor(92, 40, 18))
            p.fillRect(0, 0, w, h, QBrush(g))
            step = 6
            for y in range(0, int(h), step):
                for x in range(0, int(w), step):
                    if ((x // step) + (y // step) + (0 if kind == "a" else 1)) % 2 == 0:
                        p.fillRect(x, y, 3, 3, QColor(255, 255, 255, 38))
            p.setPen(QPen(QColor(240, 236, 210), 2))
            if kind == "a":
                p.drawLine(2, 2, w - 3, h - 3)
                p.setBrush(QColor(220, 80, 70, 210))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(6, 6, 16, 16)
                p.setBrush(QColor(230, 230, 240, 200))
                p.drawRect(w - 22, h - 16, 16, 10)
            else:
                p.drawLine(2, h - 3, w - 3, 2)
                p.setBrush(QColor(70, 170, 220, 210))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(8, h // 2 - 8, 22, 16)
                p.setBrush(QColor(250, 220, 80, 210))
                p.drawEllipse(w - 24, 4, 18, 18)
        finally:
            p.end()
        return img

    return _one("a"), _one("b")


_THUMB_A = None
_THUMB_B = None


def _thumb_sources():
    global _THUMB_A, _THUMB_B
    if _THUMB_A is None or _THUMB_A.isNull():
        _THUMB_A, _THUMB_B = _textured_thumb_pair(THUMB_W, THUMB_H)
    return _THUMB_A, _THUMB_B


class TransitionThumb(QWidget):
    def __init__(self, preset, domain="visual", parent=None):
        super().__init__(parent)
        self.preset = str(preset or "")
        self.domain = "audio" if domain == "audio" else "visual"
        self._prog = 0.45
        self._playing = False
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._out, self._inn = _thumb_sources()

    def set_playing(self, on):
        on = bool(on)
        if on == self._playing and (self._timer.isActive() == on):
            return
        self._playing = on
        if on:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._prog = 0.45
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        # Idle until hover/selected — 35 always-on 50ms timers used to pin the CPU.

    def hideEvent(self, event):
        self._timer.stop()
        self._playing = False
        super().hideEvent(event)

    def _tick(self):
        self._prog = (self._prog + 0.04) % 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#1a1d22"))
        if self.domain == "audio":
            self._paint_audio(p)
        else:
            self._paint_video(p)
        p.end()

    def _paint_video(self, painter):
        try:
            comp = compose_standalone_transition(
                self._prog, self.preset, self._out, self._inn, THUMB_W, THUMB_H)
        except Exception:
            comp = None
        if comp is not None and not comp.isNull():
            painter.drawImage(0, 0, comp)
        else:
            painter.fillRect(self.rect(), QColor("#2f3b4a"))

    def _paint_audio(self, painter):
        w, h = float(self.width()), float(self.height())
        mid = h * 0.5
        amp_out = 1.0 - self._prog
        amp_in = self._prog
        if self.preset == "audio_xfade_equal":
            amp_out = math.cos(self._prog * math.pi / 2.0)
            amp_in = math.sin(self._prog * math.pi / 2.0)
        elif self.preset == "audio_fade_in":
            amp_out = 0.0
            amp_in = self._prog
        elif self.preset == "audio_fade_out":
            amp_out = 1.0 - self._prog
            amp_in = 0.0
        elif self.preset == "audio_seam_fade":
            edge = 0.12
            if self._prog < edge:
                amp_out, amp_in = 1.0 - self._prog / edge, 0.0
            elif self._prog > 1.0 - edge:
                amp_out, amp_in = 0.0, (self._prog - (1.0 - edge)) / edge
            else:
                amp_out, amp_in = 0.0, 1.0
        painter.setPen(QPen(QColor("#6ab0d6"), 1.4))
        for i in range(int(w) - 1):
            t = i / max(1.0, w - 1)
            y = mid - math.sin(t * math.pi * 4) * (h * 0.32) * amp_out
            painter.drawLine(int(i), int(mid), int(i), int(y))
        painter.setPen(QPen(QColor("#e0a85c"), 1.4))
        for i in range(int(w) - 1):
            t = i / max(1.0, w - 1)
            y = mid - math.sin(t * math.pi * 4 + 0.6) * (h * 0.32) * amp_in
            painter.drawLine(int(i), int(mid), int(i), int(y))


class TransitionCard(QFrame):
    apply_requested = Signal(str, str, float)

    def __init__(self, label, preset, domain="visual", parent=None):
        super().__init__(parent)
        self.preset = str(preset)
        self.domain = "audio" if domain == "audio" else "visual"
        self.duration = (
            audio_transition_duration(self.preset) if self.domain == "audio" else 0.35
        )
        self.setObjectName("transitionCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(4)
        self.thumb = TransitionThumb(self.preset, self.domain)
        self._selected = False
        lay.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.caption = QLabel(tr(label))
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)

    def enterEvent(self, event):
        self.thumb.set_playing(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._selected:
            self.thumb.set_playing(False)
        super().leaveEvent(event)

    def set_selected(self, on):
        self._selected = bool(on)
        self.thumb.set_playing(self._selected or self.underMouse())

    def mouseDoubleClickEvent(self, event):
        self.apply_requested.emit(self.preset, self.domain, float(self.duration))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        drag = QDrag(self)
        mime = QMimeData()
        payload = json.dumps({
            "preset": self.preset,
            "domain": self.domain,
            "duration": float(self.duration),
        }, ensure_ascii=False)
        mime.setData(TRANSITION_MIME, payload.encode("utf-8"))
        mime.setText(self.preset)
        drag.setMimeData(mime)
        pix = QPixmap(self.thumb.size())
        self.thumb.render(pix)
        drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)


class TransitionPanel(QWidget):
    apply_requested = Signal(str, str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transitionPanel")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        self._video_cards = []
        self._audio_cards = []
        self._video_box = self._section(tr("视频转场"), VIDEO_ITEMS, "visual", self._video_cards)
        self._audio_box = self._section(tr("音频转场"), AUDIO_ITEMS, "audio", self._audio_cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.addWidget(self._video_box)
        inner_lay.addWidget(self._audio_box)
        inner_lay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

    def sizeHint(self):
        return QSize(280, 480)

    def minimumSizeHint(self):
        return QSize(200, 240)

    def video_count(self) -> int:
        return len(self._video_cards)

    def audio_count(self) -> int:
        return len(self._audio_cards)

    def set_active_preset(self, preset):
        want = str(preset or "")
        for card in self._video_cards + self._audio_cards:
            card.set_selected(card.preset == want)

    def _section(self, title, items, domain, bag):
        box = QWidget()
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(4)
        head = QLabel(title)
        head.setObjectName("transitionSection")
        box_lay.addWidget(head)
        for label, preset in items:
            card = TransitionCard(label, preset, domain=domain)
            card.apply_requested.connect(self.apply_requested)
            box_lay.addWidget(card)
            bag.append(card)
        return box
