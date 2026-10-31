# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Left-dock transition library: video (T20c) + audio tapes. Drag / double-click."""
from __future__ import annotations

import json
import math

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QDrag, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
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

_CATEGORY_MAP = {
    # 基础溶解/渐变
    "cross_dissolve": "dissolve",
    "fade_dark": "dissolve",
    "fade_white": "dissolve",
    "zoom_dissolve": "dissolve",
    "blur_dissolve": "dissolve",
    "flash_white": "dissolve",
    "dip_color": "dissolve",
    "luma_fade": "dissolve",
    "luma_melt": "dissolve",
    # 推拉/甩镜/动感
    "push_left": "motion",
    "push_right": "motion",
    "push_up": "motion",
    "push_down": "motion",
    "slide_left": "motion",
    "slide_right": "motion",
    "slide_up": "motion",
    "slide_down": "motion",
    "cover_left": "motion",
    "cover_down": "motion",
    "zoom_in_cross": "motion",
    "zoom_out_cross": "motion",
    "cross_zoom_blur": "motion",
    "whip_pan_left": "motion",
    "whip_pan_up": "motion",
    "spin_in": "motion",
    "shake_impact": "motion",
    "cube_left": "motion",
    "cube_right": "motion",
    "flip_h": "motion",
    "flip_v": "motion",
    # 几何擦除/遮罩
    "wipe_left": "wipe",
    "wipe_right": "wipe",
    "wipe_up": "wipe",
    "wipe_down": "wipe",
    "wipe_circle": "wipe",
    "iris_close": "wipe",
    "iris_diamond": "wipe",
    "iris_rectangle": "wipe",
    "wipe_radial": "wipe",
    "barn_door_h": "wipe",
    "barn_door_v": "wipe",
    "split_interleave": "wipe",
    "checker_wipe": "wipe",
    "venetian_h": "wipe",
    "venetian_v": "wipe",
    "venetian_diag": "wipe",
    "curtain_drop": "wipe",
    "page_curl": "wipe",
    # 光影胶片/特效
    "light_leak": "fx",
    "film_burn": "fx",
    "wave_ripple": "fx",
    "glitch": "fx",
    "chromatic": "fx",
    "pixelate": "fx",
    "scanlines": "fx",
    "bokeh_blur": "fx",
}


def _preset_category(preset: str, domain: str) -> str:
    if domain == "audio":
        return "audio"
    return _CATEGORY_MAP.get(str(preset or ""), "fx")


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

    def hideEvent(self, event):
        self._timer.stop()
        self._playing = False
        super().hideEvent(event)

    def _tick(self):
        self._prog = (self._prog + 0.04) % 1.0
        self.update()

    def paintEvent(self, event):
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(tok.bg1))
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0.0, 0.0, float(self.width()), float(self.height())), 5.0, 5.0)
        p.save()
        p.setClipPath(clip_path)
        if self.domain == "audio":
            self._paint_audio(p)
        else:
            self._paint_video(p)
        p.restore()
        border_col = QColor(tok.accent_hi) if self._playing else QColor(255, 255, 255, 36)
        p.setPen(QPen(border_col, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0), 5.0, 5.0)
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
        self.label_text = str(label or "")
        self.preset = str(preset)
        self.domain = "audio" if domain == "audio" else "visual"
        self.category = _preset_category(self.preset, self.domain)
        self.duration = (
            audio_transition_duration(self.preset) if self.domain == "audio" else 0.35
        )
        self.setObjectName("transitionCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMinimumWidth(100)
        self.setMaximumWidth(148)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.thumb = TransitionThumb(self.preset, self.domain)
        self._selected = False
        self._hovered = False
        lay.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.caption = QLabel(tr(label))
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(
            "color: #e2e4ea; font-size: 11px; font-weight: 500; background: transparent; border: none;"
        )
        lay.addWidget(self.caption)
        self.setToolTip(f"{tr(label)} ({self.preset})\n双击或拖拽到视频接缝、视频片段、图片图层或文字图层")
        self._update_card_style()

    def _update_card_style(self):
        from cutora.ui.theme.tokens import current
        tok = current()
        if self._selected:
            self.setStyleSheet(
                f"QFrame#transitionCard {{"
                f"  background-color: {tok.bg3};"
                f"  border: 1px solid {tok.accent_hi};"
                f"  border-radius: 8px;"
                f"}}"
            )
        elif self._hovered:
            self.setStyleSheet(
                f"QFrame#transitionCard {{"
                f"  background-color: {tok.bg3};"
                f"  border: 1px solid {tok.accent};"
                f"  border-radius: 8px;"
                f"}}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#transitionCard {{"
                f"  background-color: {tok.bg2};"
                f"  border: 1px solid {tok.border};"
                f"  border-radius: 8px;"
                f"}}"
            )

    def enterEvent(self, event):
        self._hovered = True
        self.thumb.set_playing(True)
        self._update_card_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._selected:
            self.thumb.set_playing(False)
        self._update_card_style()
        super().leaveEvent(event)

    def set_selected(self, on):
        self._selected = bool(on)
        self.thumb.set_playing(self._selected or self.underMouse())
        self._update_card_style()

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
        lay.setSpacing(6)

        # 顶部搜索与分类筛选栏
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("🔍 搜索转场效果（如：甩镜、漏光、溶解、擦除）..."))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self._search_edit, 1)
        lay.addLayout(top_bar)

        cat_bar = QHBoxLayout()
        cat_bar.setContentsMargins(0, 0, 0, 0)
        cat_bar.setSpacing(4)
        self._active_category = "all"
        self._cat_buttons = {}
        categories = [
            ("all", tr("全部")),
            ("dissolve", tr("基础溶解")),
            ("motion", tr("推拉甩镜")),
            ("wipe", tr("几何遮罩")),
            ("fx", tr("光影特效")),
            ("audio", tr("音频淡化")),
        ]
        for cat_id, cat_label in categories:
            btn = QPushButton(cat_label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(cat_id == "all")
            btn.clicked.connect(lambda _checked=False, cid=cat_id: self._set_category(cid))
            self._cat_buttons[cat_id] = btn
            cat_bar.addWidget(btn)
        cat_bar.addStretch(1)
        self._style_cat_buttons()
        lay.addLayout(cat_bar)

        hint_lbl = QLabel(tr("💡 双击或拖拽卡片到「视频接缝 / 视频片段 / 图片图层 / 文字图层」即可应用"))
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet("color: #8b909a; font-size: 11px; padding: 0px 2px;")
        lay.addWidget(hint_lbl)

        self._video_cards = []
        self._audio_cards = []
        self._video_grid = None
        self._audio_grid = None
        self._last_cols = 0

        self._video_box, self._video_grid, self._video_head = self._section(
            tr("视频转场"), VIDEO_ITEMS, "visual", self._video_cards
        )
        self._audio_box, self._audio_grid, self._audio_head = self._section(
            tr("音频转场"), AUDIO_ITEMS, "audio", self._audio_cards
        )

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(12)
        inner_lay.addWidget(self._video_box)
        inner_lay.addWidget(self._audio_box)
        inner_lay.addStretch(1)
        self._scroll.setWidget(inner)
        lay.addWidget(self._scroll, 1)
        self._reflow_grids(force=True)

    def _style_cat_buttons(self):
        from cutora.ui.theme.tokens import current
        tok = current()
        for cid, btn in self._cat_buttons.items():
            active = (cid == self._active_category)
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background-color: {tok.bg3};"
                    f"  color: {tok.accent_hi};"
                    f"  border: 1px solid {tok.accent};"
                    f"  border-radius: 8px;"
                    f"  padding: 2px 8px;"
                    f"  font-size: 11px;"
                    f"  font-weight: 600;"
                    f"}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background-color: {tok.bg1};"
                    f"  color: {tok.fg1};"
                    f"  border: 1px solid {tok.border};"
                    f"  border-radius: 8px;"
                    f"  padding: 2px 8px;"
                    f"  font-size: 11px;"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  background-color: {tok.bg2};"
                    f"  color: {tok.fg0};"
                    f"}}"
                )

    def _set_category(self, cat_id: str):
        self._active_category = str(cat_id or "all")
        self._style_cat_buttons()
        self._reflow_grids(force=True)

    def _on_filter_changed(self, _text: str = ""):
        self._reflow_grids(force=True)

    def sizeHint(self):
        return QSize(340, 520)

    def minimumSizeHint(self):
        return QSize(220, 240)

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
        box_lay.setSpacing(6)
        head = QLabel(f"{title} ({len(items)})")
        head.setObjectName("transitionSection")
        head.setStyleSheet(
            "font-size: 12px; font-weight: 600; padding: 2px 2px;"
        )
        box_lay.addWidget(head)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        box_lay.addWidget(grid_host)

        for label, preset in items:
            card = TransitionCard(label, preset, domain=domain)
            card.apply_requested.connect(self.apply_requested)
            bag.append(card)
        return box, grid, head

    def _compute_cols(self) -> int:
        vp = self._scroll.viewport() if hasattr(self, "_scroll") and self._scroll is not None else self
        avail_w = max(int(vp.width() or 0), int(self.width() or 0) - 20)
        if avail_w <= 0:
            avail_w = 320
        return max(2, min(8, avail_w // 108))

    def _card_matches(self, card: TransitionCard, q: str, cat: str) -> bool:
        if cat != "all" and card.category != cat:
            return False
        if q:
            hay = f"{card.label_text} {card.caption.text()} {card.preset}".lower()
            if q not in hay:
                return False
        return True

    def _populate_grid(self, grid: QGridLayout, cards: list[TransitionCard], cols: int, q: str, cat: str) -> int:
        while grid.count():
            grid.takeAt(0)
        for c in range(max(cols, 8)):
            grid.setColumnStretch(c, 1 if c < cols else 0)
        vis_idx = 0
        for card in cards:
            ok = self._card_matches(card, q, cat)
            card.setVisible(ok)
            if ok:
                r = vis_idx // cols
                c = vis_idx % cols
                grid.addWidget(card, r, c)
                vis_idx += 1
        return vis_idx

    def _reflow_grids(self, force: bool = False):
        cols = self._compute_cols()
        if not force and cols == self._last_cols:
            return
        self._last_cols = cols
        q = (self._search_edit.text() or "").strip().lower()
        cat = self._active_category
        v_vis = self._populate_grid(self._video_grid, self._video_cards, cols, q, cat)
        a_vis = self._populate_grid(self._audio_grid, self._audio_cards, cols, q, cat)
        self._video_head.setText(f"{tr('视频转场')} ({v_vis}/{len(self._video_cards)})")
        self._audio_head.setText(f"{tr('音频转场')} ({a_vis}/{len(self._audio_cards)})")
        self._video_box.setVisible(v_vis > 0 or (cat == "all" and not q))
        self._audio_box.setVisible(a_vis > 0 or (cat == "all" and not q))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_grids(force=False)

    def showEvent(self, event):
        super().showEvent(event)
        self._reflow_grids(force=True)
