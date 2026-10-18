# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 10960-11004, 11006-11064

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QToolButton,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    Property,
    QPropertyAnimation,
    QEasingCurve,
)

from cutora.ui.theme.tokens import (
    DURATION_MS,
    FONT_BODY,
    INSPECTOR_FIELD_GAP,
    INSPECTOR_HEADER_H,
    INSPECTOR_PAD_X,
    INSPECTOR_RESET_PX,
    INSPECTOR_ROW_GAP,
    SPACE_2,
    SPACE_3,
    TOGGLE_H,
    TOGGLE_W,
)

# One pair for every collapsible inspector section. Do not introduce a second
# triangle; test_one_section_class greps for these two characters only.
ARROW_COLLAPSED = "▸"
ARROW_EXPANDED = "▾"

QWIDGETSIZE_MAX = 16777215
from PySide6.QtGui import QPainter, QColor, QFont
# ═══════════════════════════════════════════════════════════
#  可折叠区块 / 胶囊开关基础组件
# ═══════════════════════════════════════════════════════════

def labeled_toggle(label: str, parent=None, checked: bool = False):
    """ToggleSwitch with the label on the right (13 px). On = theme gold."""
    host = QWidget(parent)
    host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    host.setStyleSheet("background: transparent; border: none;")
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_2)
    sw = ToggleSwitch(checked, host)
    lab = QLabel(label, host)
    font = QFont(lab.font())
    font.setPixelSize(FONT_BODY)
    lab.setFont(font)
    lab.setStyleSheet("background: transparent; border: none; color: palette(window-text);")
    row.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(lab, 1, Qt.AlignmentFlag.AlignVCenter)
    host._switch = sw
    host._label = lab
    return host, sw


class ToggleSwitch(QWidget):
    """胶囊开关 36×20，圆钮 120 ms 滑动。开=金底白钮，关=灰底。"""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._hover = False
        self._knob = 1.0 if self._checked else 0.0
        self.setObjectName("sectionToggle")
        self.setFixedSize(TOGGLE_W, TOGGLE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        v = bool(v)
        if v == self._checked:
            return
        self._checked = v
        self._animate_knob()
        self.update()
        if not self.signalsBlocked():
            self.toggled.emit(self._checked)

    def _get_knob(self):
        return float(self._knob)

    def _set_knob(self, v):
        self._knob = float(v)
        self.update()

    knobPos = Property(float, _get_knob, _set_knob)

    def _animate_knob(self):
        self._anim.stop()
        self._anim.setStartValue(float(self._knob))
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()

    def mousePressEvent(self, e):
        if e is not None and e.button() != Qt.MouseButton.LeftButton:
            return
        self._checked = not self._checked
        self._animate_knob()
        self.update()
        if not self.signalsBlocked():
            self.toggled.emit(self._checked)
        if e is not None:
            e.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.update()
        super().focusOutEvent(e)

    def paintEvent(self, e):
        from cutora.ui.theme.states import DISABLED_OPACITY, mix_disabled_rgb
        from cutora.ui.theme.tokens import current, hex_rgb, lighten_hex
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            try:
                tok = current()
                on_c = QColor(tok.accent)
                off_c = QColor(tok.line)
                knob_c = QColor(tok.fg0)
                bg2 = hex_rgb(tok.bg2)
            except Exception:
                on_c = QColor("#C8A24A")
                off_c = QColor("#3A302A")
                knob_c = QColor("#EFE7DA")
                bg2 = (38, 32, 25)
            enabled = self.isEnabled()
            if enabled and self._hover:
                on_c = QColor(lighten_hex(on_c.name(), 0.08))
                off_c = QColor(lighten_hex(off_c.name(), 0.08))
            if not enabled:
                def _fade(c):
                    r, g, b = mix_disabled_rgb(
                        (c.red(), c.green(), c.blue()), bg2, DISABLED_OPACITY)
                    out = QColor(r, g, b)
                    out.setAlpha(c.alpha())
                    return out
                on_c = _fade(on_c)
                off_c = _fade(off_c)
                knob_c = _fade(knob_c)
            w, h = self.width(), self.height()
            r = h / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(on_c if self._checked else off_c)
            p.drawRoundedRect(0, 0, w, h, r, r)
            from cutora.ui.theme.tokens import SPACE_QUARTER
            margin = SPACE_QUARTER
            d = h - margin * 2
            x0 = margin
            x1 = w - margin - d
            cx = x0 + (x1 - x0) * max(0.0, min(1.0, float(self._knob)))
            p.setBrush(knob_c)
            p.drawEllipse(int(round(cx)), margin, int(d), int(d))
            if enabled and self.hasFocus():
                try:
                    ring = QColor(tok.accent)
                except Exception:
                    ring = QColor("#C8A24A")
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QColor(ring))
                p.drawRoundedRect(0, 0, w - 1, h - 1, r, r)
        finally:
            p.end()

class CollapsibleSection(QWidget):
    """Inspector section: header bar, optional enable capsule, collapsible body.

    One class for the whole app. Property panel / models page pass
    ``toggle_widget`` (right of the title, default expanded). Subtitle
    inspector passes ``toggle_checkbox`` (left of the title, often
    collapsed, optionally grays the body when off).
    """

    def __init__(self, title, parent=None, toggle_widget=None, *,
                 toggle_checkbox=None, reparent_toggle=True,
                 initially_collapsed=False, gray_content=False):
        super().__init__(parent)
        self._is_collapsed = bool(initially_collapsed)
        self._expanded = not self._is_collapsed
        self._gray_content = bool(gray_content)
        self.section_title = title
        toggle = toggle_checkbox if toggle_checkbox is not None else toggle_widget
        self._toggle_cb = toggle

        from cutora.ui.i18n import tr as _tr
        from cutora.ui.widgets.responsive import mark_explicit

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("dockSectionHeader")
        self._header.setFixedHeight(INSPECTOR_HEADER_H)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)

        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(INSPECTOR_PAD_X, 0, INSPECTOR_PAD_X, 0)
        h_layout.setSpacing(INSPECTOR_FIELD_GAP)

        self._arrow = QLabel(
            ARROW_EXPANDED if not initially_collapsed else ARROW_COLLAPSED)
        self._arrow.setObjectName("dockSectionArrow")
        self._arrow.setFixedWidth(SPACE_3)
        h_layout.addWidget(self._arrow)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("dockSectionTitle")
        h_layout.addWidget(self._title_label)
        h_layout.addStretch()

        if toggle is not None:
            if reparent_toggle:
                toggle.setParent(self._header)
                toggle.setVisible(True)
            h_layout.addWidget(toggle)
            if self._gray_content and hasattr(toggle, "toggled"):
                toggle.toggled.connect(self._on_enable_toggled)

        self._reset_btn = QToolButton(self._header)
        self._reset_btn.setObjectName("inspectorReset")
        self._reset_btn.setText("↺")
        self._reset_btn.setToolTip(_tr("重置本段"))
        self._reset_btn.setFixedSize(INSPECTOR_RESET_PX, INSPECTOR_RESET_PX)
        self._reset_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        mark_explicit(self._reset_btn, "maximumWidth")
        self._reset_btn.clicked.connect(self._reset_section)
        h_layout.addWidget(self._reset_btn)

        main_layout.addWidget(self._header)

        self._content = QWidget()
        self._content.setObjectName("dockSectionContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, INSPECTOR_ROW_GAP, 0, INSPECTOR_ROW_GAP)
        self._content_layout.setSpacing(INSPECTOR_ROW_GAP)
        self._content.setVisible(not initially_collapsed)
        main_layout.addWidget(self._content)
        self._height_anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._height_anim.setDuration(DURATION_MS)
        self._height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._height_anim.finished.connect(self._on_height_anim_finished)
        if initially_collapsed:
            self._content.setMaximumHeight(0)
        else:
            self._content.setMaximumHeight(QWIDGETSIZE_MAX)
        if (self._gray_content and toggle is not None
                and hasattr(toggle, "isChecked")):
            self.set_content_enabled(bool(toggle.isChecked()))

        self._header.mousePressEvent = self._on_header_clicked

    def set_content_enabled(self, on):
        on = bool(on)
        self._content.setEnabled(on)
        from PySide6.QtWidgets import QAbstractSpinBox, QGraphicsOpacityEffect, QLineEdit
        from cutora.ui.theme.states import DISABLED_OPACITY
        if not on:
            for edit in self._content.findChildren(QLineEdit):
                edit.deselect()
            for spin in self._content.findChildren(QAbstractSpinBox):
                inner = spin.lineEdit()
                if inner is not None:
                    inner.deselect()
        if on:
            self._content.setGraphicsEffect(None)
            return
        effect = QGraphicsOpacityEffect(self._content)
        effect.setOpacity(DISABLED_OPACITY)
        self._content.setGraphicsEffect(effect)

    def _on_enable_toggled(self, checked):
        if self._gray_content:
            self.set_content_enabled(checked)

    def _on_header_clicked(self, event):
        reset = getattr(self, "_reset_btn", None)
        if reset is not None and reset.geometry().contains(event.pos()):
            return
        toggle = self._toggle_cb
        if toggle is not None and toggle.geometry().contains(event.pos()):
            return
        self._is_collapsed = not self._is_collapsed
        self._expanded = not self._is_collapsed
        self._arrow.setText(
            ARROW_COLLAPSED if self._is_collapsed else ARROW_EXPANDED)
        self._animate_content(not self._is_collapsed)

    def contentLayout(self):
        return self._content_layout

    def addWidget(self, w):
        self._content_layout.addWidget(w)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def addLayout(self, lay):
        self._content_layout.addLayout(lay)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def _reset_section(self):
        begin = getattr(self, "begin_reset", None)
        end = getattr(self, "end_reset", None)
        if callable(begin):
            begin(self)
        try:
            self._reset_section_body()
        finally:
            if callable(end):
                end(self)

    def _reset_section_body(self):
        from cutora.core.log import get_logger
        from cutora.ui.widgets.inspector import InspectorRow
        toggle_reset = getattr(self, "reset_toggle", None)
        if callable(toggle_reset):
            toggle_reset(self)
        rows = self.findChildren(InspectorRow)
        extra = getattr(self, "extra_reset", None)
        if not rows and not callable(extra):
            get_logger("cutora.ui.widgets.collapsible").warning(
                "section reset: no InspectorRow in %r", self.section_title)
            return
        for row in rows:
            row.trigger_reset()
        if callable(extra):
            extra(self)

    def _toggle(self, event=None):
        self._on_header_clicked(event)

    def setExpanded(self, v):
        self.set_collapsed(not bool(v))

    def set_collapsed(self, collapsed):
        self._height_anim.stop()
        self._is_collapsed = bool(collapsed)
        self._expanded = not self._is_collapsed
        self._content.setVisible(not self._is_collapsed)
        self._content.setMaximumHeight(
            0 if self._is_collapsed else QWIDGETSIZE_MAX)
        self._arrow.setText(
            ARROW_COLLAPSED if self._is_collapsed else ARROW_EXPANDED)

    def _animate_content(self, expand):
        self._height_anim.stop()
        self._content.setVisible(True)
        hint = max(self._content.sizeHint().height(), 1)
        current = self._content.height()
        if expand:
            start = 0 if current <= 1 else current
            end = hint
        else:
            start = current if current > 0 else hint
            end = 0
        self._content.setMaximumHeight(int(start))
        self._height_anim.setStartValue(int(start))
        self._height_anim.setEndValue(int(end))
        self._height_anim.start()

    def _on_height_anim_finished(self):
        if self._is_collapsed:
            self._content.setVisible(False)
            self._content.setMaximumHeight(0)
        else:
            self._content.setMaximumHeight(QWIDGETSIZE_MAX)

    def isExpanded(self):
        return not bool(self._is_collapsed)

    def setTitle(self, title):
        self.section_title = title
        self._title_label.setText(title)
