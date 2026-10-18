# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve-style inspector widgets. Pixel sizes come from theme tokens."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import (
    QWIDGETSIZE_MAX,
    INSPECTOR_OPTION_GAP,
    INSPECTOR_PAIR_GAP,
    INSPECTOR_ICON_PX,
    INSPECTOR_VALUE_W,
    INSPECTOR_ROW_H,
    CONTROL_H,
    FONT_MONO,
    INSPECTOR_CHECKER_PX,
    INSPECTOR_COLOR_MAX_W,
    INSPECTOR_COMBO_MAX_W,
    INSPECTOR_FIELD_GAP,
    INSPECTOR_GRAD_BAR_H,
    INSPECTOR_HEADER_H,
    INSPECTOR_LABEL_FONT,
    INSPECTOR_LABEL_MAX,
    INSPECTOR_LABEL_MIN,
    INSPECTOR_RESET_PX,
    INSPECTOR_SLIDER_MIN,
    INSPECTOR_SPIN_WIDTH,
    INSPECTOR_SWATCH_PX,
    INSPECTOR_TITLE_FONT,
    INSPECTOR_WHEEL_PX,
    RADIUS_CTL,
    ROW_H,
    current,
)
from cutora.ui.widgets.responsive import mark_explicit

_LOG = get_logger("cutora.ui.widgets.inspector")


def inspector_row_of(widget):
    """Walk parents until an InspectorRow, or None."""
    cur = widget
    while cur is not None:
        if isinstance(cur, InspectorRow):
            return cur
        cur = cur.parentWidget()
    return None


def _cap_combo(widget) -> None:
    # Expanding up to the cap: every dropdown shares one right edge instead
    # of hugging its current text (which made widths jump row to row).
    widget.setMaximumWidth(INSPECTOR_COMBO_MAX_W)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    mark_explicit(widget, "maximumWidth")
    mark_explicit(widget, "minimumWidth")
    mark_explicit(widget, "sizePolicy")


def _cap_spin(widget) -> None:
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    widget.setMaximumWidth(INSPECTOR_SPIN_WIDTH)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    mark_explicit(widget, "maximumWidth")
    mark_explicit(widget, "minimumWidth")
    mark_explicit(widget, "sizePolicy")


class InspectorLimitFilter(QObject):
    """Re-apply max-width caps after Qt lays the panel out."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def eventFilter(self, obj, event):
        if self._busy or event is None:
            return False
        if event.type() not in (QEvent.Type.Show, QEvent.Type.Resize):
            return False
        if not isinstance(obj, QWidget):
            return False
        self._busy = True
        try:
            apply_inspector_limits(obj)
        finally:
            self._busy = False
        return False


def install_inspector_limits(root: QWidget) -> None:
    apply_inspector_limits(root)
    filt = getattr(root, "_inspector_limit_filter", None)
    if filt is None:
        filt = InspectorLimitFilter(root)
        root.installEventFilter(filt)
        root._inspector_limit_filter = filt


def apply_inspector_limits(root: QWidget) -> None:
    """Cap leftover combos / spins / color chips so a 480px panel cannot stretch them."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QPushButton

    for combo in root.findChildren(QComboBox):
        if combo.maximumWidth() > INSPECTOR_COMBO_MAX_W:
            _cap_combo(combo)
    for spin in root.findChildren(QAbstractSpinBox):
        parent = spin.parentWidget()
        if isinstance(parent, SliderSpin) or (
                parent is not None and parent.objectName() in (
                    "inspectorSliderSpin", "inspectorPair", "inspectorWithTrailing")):
            continue
        _cap_spin(spin)
    for btn in root.findChildren(QPushButton):
        if (btn.objectName() or "") != "swatchBtn":
            continue
        if btn.maximumWidth() > INSPECTOR_COLOR_MAX_W:
            btn.setMaximumWidth(INSPECTOR_COLOR_MAX_W)
            mark_explicit(btn, "maximumWidth")


def add_inspect_row(layout, label, widget, on_reset=None, span=False, reset_key=None):
    """Drop-in for QFormLayout.addRow / QVBoxLayout.addWidget.

    ``reset_key`` is resolved by ``layout.reset_resolver`` (one panel
    function, not a hand-copied lambda per row).
    """
    if on_reset is None and reset_key is not None:
        resolver = getattr(layout, "reset_resolver", None)
        if callable(resolver):
            on_reset = lambda k=reset_key, fn=resolver: fn(k)
    row = InspectorRow(label, widget, on_reset=on_reset, span=span)
    row.reset_key = reset_key
    if isinstance(layout, QFormLayout):
        layout.addRow(row)
    else:
        layout.addWidget(row)
    return row


def _as_widget(item):
    if item is None:
        return QWidget()
    if isinstance(item, QWidget):
        return item
    host = QWidget()
    host.setLayout(item)
    return host


def bind_icon_safe(widget, name: str, color_token: str = "fg1", size=None) -> None:
    """Theme-bound icon; falls back to nothing if the icon file is missing."""
    try:
        from cutora.ui.icons import bind_icon
        bind_icon(widget, name, size=int(size or INSPECTOR_ICON_PX), color_token=color_token)
    except Exception as exc:  # noqa: BLE001 - icon is cosmetic
        _LOG.debug("icon %s unavailable: %s", name, exc)


def inspector_icon_button(name: str, tip: str = "", checkable: bool = False,
                          object_name: str = "inspectorIconBtn") -> QToolButton:
    """Square 28px icon button used in inspector rows and toolbars."""
    btn = QToolButton()
    btn.setObjectName(object_name)
    btn.setCheckable(bool(checkable))
    btn.setToolTip(tip)
    btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    bind_icon_safe(btn, name)
    return btn


def paired_fields(*fields, trailing=None) -> QWidget:
    """Two (or more) compact fields sharing one row, equal width."""
    host = QWidget()
    host.setObjectName("inspectorPair")
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(INSPECTOR_PAIR_GAP)
    for f in fields:
        f.setParent(host)
        _pair_width(f)
        lay.addWidget(f, 1)
    if trailing is not None:
        trailing.setParent(host)
        lay.addWidget(trailing, 0)
    # Zero-stretch spacer: boxes grow to their cap first, then the spacer
    # takes the rest, so pairs stay left-packed on wide docks and shrink
    # together (down to INSPECTOR_VALUE_W) on narrow ones.
    lay.addStretch(0)
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return host


def _pair_width(field) -> None:
    """Paired number boxes are exactly one spin width, left-packed."""
    if isinstance(field, QAbstractSpinBox):
        field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        field.setMinimumWidth(INSPECTOR_VALUE_W)
        field.setMaximumWidth(INSPECTOR_SPIN_WIDTH)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mark_explicit(field, "minimumWidth")
        mark_explicit(field, "maximumWidth")


def with_trailing(field, trailing) -> QWidget:
    """A field with a small widget glued to its right (keyframe watch, unit)."""
    host = QWidget()
    host.setObjectName("inspectorWithTrailing")
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(INSPECTOR_FIELD_GAP)
    field.setParent(host)
    trailing.setParent(host)
    _pair_width(field)
    lay.addWidget(field, 1)
    lay.addWidget(trailing, 0)
    lay.addStretch(0)
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return host


def slider_with_spin(slider, spin) -> QWidget:
    """Existing slider + spin pair laid out exactly like SliderSpin."""
    host = QWidget()
    host.setObjectName("inspectorSliderSpin")
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(INSPECTOR_PAIR_GAP)
    slider.setParent(host)
    spin.setParent(host)
    slider.setMinimumWidth(INSPECTOR_SLIDER_MIN)
    slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    _cap_spin(spin)
    spin.setFixedWidth(INSPECTOR_VALUE_W)
    lay.addWidget(slider, 1)
    lay.addWidget(spin, 0)
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return host


class ValueCombo(QComboBox):
    """Dropdown that shows Chinese labels but speaks stored values.

    currentText / setCurrentText / findText use the VALUE (e.g. "multiply"),
    so load/save code written against the old English-text combos keeps
    working while the user sees 正片叠底. Connect to ``valueChanged``."""

    valueChanged = Signal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        for value, label in items:
            self.addItem(label, value)
        self.currentIndexChanged.connect(
            lambda _i: self.valueChanged.emit(self.currentText()))

    def currentText(self) -> str:  # noqa: N802 - Qt API name
        data = self.currentData()
        return "" if data is None else str(data)

    def setCurrentText(self, value) -> None:  # noqa: N802
        idx = self.findData(str(value))
        if idx >= 0:
            self.setCurrentIndex(idx)

    def set_items(self, items) -> None:
        """换一组选项，尽量留在原来的值上。不发 valueChanged。"""
        cur = self.currentText()
        self.blockSignals(True)
        try:
            self.clear()
            for value, label in items:
                self.addItem(label, value)
            self.setCurrentText(cur)
        finally:
            self.blockSignals(False)

    def findText(self, value, *_args) -> int:  # noqa: N802
        return self.findData(str(value))


BLEND_ITEMS = (
    ("normal", "正常"), ("multiply", "正片叠底"), ("screen", "滤色"),
    ("overlay", "叠加"), ("additive", "相加"),
)

# 只给视频片段 / 视频轨。文字和图片仍用上面五档，避免选了蒙版却按普通画面画出来。
VIDEO_BLEND_ITEMS = BLEND_ITEMS + (
    ("luma_matte", "作为蒙版"),
    ("luma_matte_inv", "蒙版反相"),
)

LUMA_MATTE_TIP = (
    "不画出这一层的颜色。白保留下面已经画好的画面，黑让下面变透明。"
    "蒙版反相反过来。空白边缘不挖空画面。"
    "位置、旋转、缩放、不透明度、关键帧和调色仍作用在这一层上。"
)


class SegmentedChoice(QWidget):
    """Icon segmented control with a QComboBox-like text API
    (currentText / setCurrentText / currentTextChanged)."""

    currentTextChanged = Signal(str)

    def __init__(self, items, parent=None):
        from PySide6.QtWidgets import QButtonGroup

        super().__init__(parent)
        self.setObjectName("segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._values = []
        for value, icon_name, tip in items:
            btn = QToolButton(self)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            bind_icon_safe(btn, icon_name)
            self._group.addButton(btn, len(self._values))
            self._values.append(str(value))
            lay.addWidget(btn, 1)
        if self._group.buttons():
            self._group.buttons()[0].setChecked(True)
        self._group.idToggled.connect(self._on_toggled)
        self.setMaximumWidth(INSPECTOR_COMBO_MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _on_toggled(self, idx, on) -> None:
        if on and not self.signalsBlocked():
            self.currentTextChanged.emit(self._values[idx])

    def currentText(self) -> str:
        idx = self._group.checkedId()
        return self._values[idx] if 0 <= idx < len(self._values) else ""

    def setCurrentText(self, value) -> None:
        value = str(value)
        if value in self._values:
            btn = self._group.button(self._values.index(value))
            if btn is not None and not btn.isChecked():
                blocked = self.signalsBlocked()
                btn.setChecked(True)
                if blocked:
                    return


class InspectForm(QVBoxLayout):
    """QFormLayout.addRow-compatible stack of InspectorRow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reset_resolver = None

    def set_reset_resolver(self, fn) -> None:
        self.reset_resolver = fn

    def addSpanRow(self, widget, on_reset=None, reset_key=None):
        """Full-width row for a composite field with its own labels."""
        return add_inspect_row(
            self, "", _as_widget(widget), on_reset=on_reset, span=True, reset_key=reset_key)

    def addRow(self, a, b=None, on_reset=None, reset_key=None):
        if b is None:
            return add_inspect_row(
                self, "", _as_widget(a), on_reset=on_reset, reset_key=reset_key)
        label = a
        if isinstance(label, QLabel):
            text = label.text()
            label.hide()
        else:
            text = "" if label is None else str(label)
        return add_inspect_row(
            self, text, _as_widget(b), on_reset=on_reset, reset_key=reset_key)

    def setLabelAlignment(self, *args) -> None:
        return None

    def setFormAlignment(self, *args) -> None:
        return None

    def setHorizontalSpacing(self, *args) -> None:
        return None

    def setVerticalSpacing(self, value) -> None:
        self.setSpacing(value)

    def setFieldGrowthPolicy(self, *args) -> None:
        return None

    def rowCount(self) -> int:
        return self.count()

    def setRowVisible(self, index, on) -> None:
        item = self.itemAt(index)
        if item is None:
            return
        w = item.widget()
        if w is not None:
            w.setVisible(bool(on))


class EnumCombo(QComboBox):
    """Dropdown that refuses to grow past INSPECTOR_COMBO_MAX_W."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorCombo")
        _cap_combo(self)


class SliderSpin(QWidget):
    """Slider + fixed-width spin. One widget, one value."""

    valueChanged = Signal(float)

    def __init__(self, lo, hi, step, decimals, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorSliderSpin")
        self._decimals = int(decimals)
        self._step = float(step)
        scale = 10 ** max(0, self._decimals)
        self._scale = float(scale)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_FIELD_GAP)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setMinimumWidth(INSPECTOR_SLIDER_MIN)
        mark_explicit(self.slider, "minimumWidth")
        self.slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mark_explicit(self.slider, "sizePolicy")
        lo_i = int(round(float(lo) * self._scale))
        hi_i = int(round(float(hi) * self._scale))
        if hi_i <= lo_i:
            hi_i = lo_i + 1
        self.slider.setRange(lo_i, hi_i)
        step_i = max(1, int(round(float(step) * self._scale)))
        self.slider.setSingleStep(step_i)
        if self._decimals:
            self.spin = QDoubleSpinBox(self)
            self.spin.setDecimals(self._decimals)
            self.spin.setSingleStep(float(step))
            self.spin.setRange(float(lo), float(hi))
        else:
            self.spin = QSpinBox(self)
            self.spin.setSingleStep(max(1, int(round(float(step)))))
            self.spin.setRange(int(lo), int(hi))
        self.spin.setObjectName("inspectorSpin")
        _cap_spin(self.spin)
        # One value column for every slider row in the panel.
        self.spin.setFixedWidth(INSPECTOR_VALUE_W)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin, 0)
        # Tracking stays on: each tick emits so the preview updates while dragging.
        self.slider.setTracking(True)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        h = max(self.spin.sizeHint().height(), self.slider.sizeHint().height())
        return QSize(INSPECTOR_SLIDER_MIN + INSPECTOR_FIELD_GAP + INSPECTOR_LABEL_MIN, h)

    def minimumSizeHint(self) -> QSize:
        h = max(
            self.spin.minimumSizeHint().height(),
            self.slider.minimumSizeHint().height(),
        )
        return QSize(INSPECTOR_SLIDER_MIN + INSPECTOR_FIELD_GAP, h)

    def _from_slider(self, raw: int) -> None:
        val = float(raw) / self._scale
        self.spin.blockSignals(True)
        self.spin.setValue(val if self._decimals else int(round(val)))
        self.spin.blockSignals(False)
        if not self.signalsBlocked():
            self.valueChanged.emit(float(self.spin.value()))

    def _from_spin(self, val) -> None:
        raw = int(round(float(val) * self._scale))
        self.slider.blockSignals(True)
        self.slider.setValue(raw)
        self.slider.blockSignals(False)
        if not self.signalsBlocked():
            self.valueChanged.emit(float(val))

    def value(self):
        v = self.spin.value()
        return float(v) if self._decimals else int(v)

    def setValue(self, value) -> None:
        if value is None:
            value = 0
        if self._decimals:
            self.spin.setValue(float(value))
        else:
            self.spin.setValue(int(round(float(value))))
        raw = int(round(float(self.spin.value()) * self._scale))
        prev = self.slider.blockSignals(True)
        try:
            self.slider.setValue(raw)
        finally:
            self.slider.blockSignals(prev)

    def minimum(self):
        return float(self.spin.minimum()) if self._decimals else int(self.spin.minimum())

    def maximum(self):
        return float(self.spin.maximum()) if self._decimals else int(self.spin.maximum())

    def setRange(self, lo, hi) -> None:
        self.spin.setRange(lo, hi)
        self.slider.setRange(
            int(round(float(lo) * self._scale)),
            int(round(float(hi) * self._scale)),
        )

    def setSingleStep(self, step) -> None:
        self._step = float(step)
        if self._decimals:
            self.spin.setSingleStep(float(step))
        else:
            self.spin.setSingleStep(max(1, int(round(float(step)))))
        self.slider.setSingleStep(max(1, int(round(float(step) * self._scale))))

    def setDecimals(self, decimals) -> None:
        if hasattr(self.spin, "setDecimals"):
            self.spin.setDecimals(int(decimals))

    def setSuffix(self, text) -> None:
        if hasattr(self.spin, "setSuffix"):
            self.spin.setSuffix(text)

    def setToolTip(self, text) -> None:
        super().setToolTip(text)
        self.slider.setToolTip(text)
        self.spin.setToolTip(text)

    def setEnabled(self, on) -> None:
        super().setEnabled(on)
        self.slider.setEnabled(on)
        self.spin.setEnabled(on)

    def blockSignals(self, b: bool) -> bool:
        prev = super().blockSignals(b)
        self.slider.blockSignals(b)
        self.spin.blockSignals(b)
        return prev


class ColorSwatch(QWidget):
    """Token-sized square whose fill *is* the color. Hex caption lives in InspectorRow."""

    changed = Signal()
    clicked = Signal()
    CHIP_PX = INSPECTOR_SWATCH_PX

    def __init__(self, hex_color="#FFFFFF", parent=None, chip=False):
        super().__init__(parent)
        self.setObjectName("swatchBtn")
        self._chip = bool(chip)
        self.setFixedSize(self.CHIP_PX, self.CHIP_PX)
        mark_explicit(self, "maximumWidth")
        mark_explicit(self, "minimumWidth")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._hex = ""
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_hex(hex_color, emit=False)

    def sizeHint(self) -> QSize:
        return QSize(self.CHIP_PX, self.CHIP_PX)

    def hex(self) -> str:
        return self._hex

    def text(self) -> str:
        """QPushButton-compat: the chip has no caption."""
        return ""

    def set_hex(self, value: str, emit: bool = True) -> None:
        from cutora.render.textfx.spec import parse_color

        raw = (value or "").strip()
        if not raw:
            self._hex = ""
        else:
            self._hex = parse_color(raw, self._hex or "#FFFFFF")
        self.update()
        if emit:
            self.changed.emit()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pick()
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def click(self) -> None:
        self._pick()
        self.clicked.emit()

    def _qcolor(self) -> QColor:
        from cutora.render.textfx.spec import rgba_tuple

        r, g, b, a = rgba_tuple(self._hex or "#FFFFFF")
        c = QColor(int(r), int(g), int(b))
        c.setAlpha(max(0, min(255, int(round(float(a) * 255)))))
        return c

    def _pick(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        opts = QColorDialog.ColorDialogOption.ShowAlphaChannel
        picked = QColorDialog.getColor(self._qcolor(), self, "", opts)
        if not picked.isValid():
            return
        r, g, b, a = picked.red(), picked.green(), picked.blue(), picked.alpha()
        if a >= 255:
            self.set_hex("#%02X%02X%02X" % (r, g, b))
        else:
            self.set_hex("#%02X%02X%02X%02X" % (r, g, b, a))

    def paintEvent(self, event) -> None:
        from cutora.render.textfx.spec import rgba_tuple
        from cutora.ui.theme.states import DISABLED_OPACITY, mix_disabled_rgb
        from cutora.ui.theme.tokens import hex_rgb

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            rect = self.rect()
            try:
                r, g, b, a = rgba_tuple(self._hex or "#FFFFFF")
            except Exception:
                _LOG.exception("ColorSwatch paint parse %r", self._hex)
                r, g, b, a = 244, 235, 216, 1.0
            tok = current()
            enabled = self.isEnabled()
            if not enabled:
                br, bg, bb = hex_rgb(tok.bg2)
                r, g, b = mix_disabled_rgb((r, g, b), (br, bg, bb), DISABLED_OPACITY)
            if float(a) < 0.999:
                self._paint_checker(p, rect)
            fill = QColor(int(r), int(g), int(b))
            fill.setAlpha(max(0, min(255, int(round(float(a) * 255)))))
            p.fillRect(rect, fill)
            if not enabled:
                border = QColor(tok.fg2)
            elif self.hasFocus() or self._hover:
                border = QColor(tok.accent)
            else:
                border = QColor(tok.line)
            p.setPen(QPen(border, 1))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            hi = QColor(tok.fg0)
            hi.setAlpha(16 if not enabled else 40)
            p.setPen(QPen(hi, 1))
            p.drawLine(rect.left() + 1, rect.top() + 1, rect.right() - 1, rect.top() + 1)
        finally:
            p.end()

    def _paint_checker(self, painter: QPainter, rect) -> None:
        tok = current()
        a = QColor(tok.bg2)
        b = QColor(tok.fg2)
        cs = INSPECTOR_CHECKER_PX
        y = rect.top()
        while y < rect.bottom():
            x = rect.left()
            row = ((y - rect.top()) // cs) % 2
            while x < rect.right():
                col = ((x - rect.left()) // cs) % 2
                painter.fillRect(x, y, cs, cs, b if (row + col) % 2 else a)
                x += cs
            y += cs


class _GradBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = ["#F4EBD8"]
        self.setFixedHeight(INSPECTOR_GRAD_BAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_colors(self, colors) -> None:
        self._colors = list(colors or ["#F4EBD8"])
        self.update()

    def paintEvent(self, event) -> None:
        from cutora.render.textfx.spec import rgba_tuple
        from cutora.ui.theme.states import DISABLED_OPACITY, mix_disabled_rgb
        from cutora.ui.theme.tokens import hex_rgb

        p = QPainter(self)
        try:
            rect = self.rect()
            cols = self._colors or ["#F4EBD8"]
            tok = current()
            enabled = self.isEnabled()
            bg = hex_rgb(tok.bg2)

            def _qcol(hx):
                r, g, b, a = rgba_tuple(hx)
                if not enabled:
                    r, g, b = mix_disabled_rgb((r, g, b), bg, DISABLED_OPACITY)
                c = QColor(int(r), int(g), int(b))
                c.setAlpha(max(0, min(255, int(round(float(a) * 255)))))
                return c

            if len(cols) == 1:
                p.fillRect(rect, _qcol(cols[0]))
            else:
                grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
                n = max(1, len(cols) - 1)
                for i, hx in enumerate(cols):
                    grad.setColorAt(float(i) / n, _qcol(hx))
                p.fillRect(rect, grad)
            p.setPen(QPen(QColor(tok.fg2 if not enabled else tok.line), 1))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
        finally:
            p.end()


class ColorStops(QWidget):
    """Gradient stops: square chips, preview bar, add/remove."""

    changed = Signal()

    def __init__(self, nmin=1, nmax=3, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorColorStops")
        self._nmin, self._nmax = int(nmin), int(nmax)
        self._swatches = []
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(INSPECTOR_FIELD_GAP)
        self._bar = _GradBar(self)
        col.addWidget(self._bar)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(INSPECTOR_FIELD_GAP)
        self._count = QSpinBox(self)
        self._count.setRange(self._nmin if self._nmin else 0, max(self._nmin, self._nmax))
        self._count.valueChanged.connect(self._on_count)
        self._count.hide()
        for _ in range(max(self._nmax, 1)):
            chip = ColorSwatch("#F4EBD8", parent=self, chip=True)
            chip.changed.connect(self._on_chip)
            row.addWidget(chip)
            self._swatches.append(chip)
        self._btn_add = QPushButton("+", self)
        self._btn_add.setFixedSize(INSPECTOR_SWATCH_PX, INSPECTOR_SWATCH_PX)
        mark_explicit(self._btn_add, "maximumWidth")
        self._btn_add.setToolTip(tr("添加色标"))
        self._btn_add.clicked.connect(self._add)
        row.addWidget(self._btn_add)
        self._btn_del = QPushButton("−", self)
        self._btn_del.setFixedSize(INSPECTOR_SWATCH_PX, INSPECTOR_SWATCH_PX)
        mark_explicit(self._btn_del, "maximumWidth")
        self._btn_del.setToolTip(tr("删除色标"))
        self._btn_del.clicked.connect(self._del)
        row.addWidget(self._btn_del)
        self._hex = QLabel(self)
        self._hex.setObjectName("inspectorHex")
        hx_font = QFont(FONT_MONO)
        hx_font.setPixelSize(INSPECTOR_LABEL_FONT)
        self._hex.setFont(hx_font)
        row.addWidget(self._hex)
        row.addStretch(1)
        col.addLayout(row)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_colors(["#F4EBD8"], emit=False)

    def set_limits(self, nmin, nmax) -> None:
        self._nmin, self._nmax = int(nmin), int(nmax)
        lo = self._nmin if self._nmin else 0
        self._count.setRange(lo, max(lo, self._nmax))
        n = self._count.value()
        target = n
        if n < lo:
            target = lo
        elif self._nmax and n > self._nmax:
            target = self._nmax
        self._count.blockSignals(True)
        self._count.setValue(target)
        self._count.blockSignals(False)
        for i, chip in enumerate(self._swatches):
            chip.setVisible(i < target)
        self._refresh_chrome()
        self._bar.set_colors(self.colors())

    def colors(self) -> list:
        n = self._count.value()
        return [s.hex() for s in self._swatches[:n]]

    def set_colors(self, values, emit: bool = True) -> None:
        from cutora.render.textfx.spec import parse_color

        cap = max(1, len(self._swatches))
        raw = [parse_color(v, "#F4EBD8") for v in (values or [])][:cap]
        if self._nmin and len(raw) < self._nmin:
            raw.extend(["#F4EBD8"] * (self._nmin - len(raw)))
        if self._nmax:
            raw = raw[: self._nmax]
        self._count.blockSignals(True)
        self._count.setValue(max(self._nmin, len(raw)) if raw else self._nmin)
        self._count.blockSignals(False)
        for i, chip in enumerate(self._swatches):
            if i < len(raw):
                chip.set_hex(raw[i], emit=False)
            chip.setVisible(i < self._count.value())
        self._refresh_chrome()
        self._bar.set_colors(self.colors())
        if emit:
            self.changed.emit()

    def _on_chip(self) -> None:
        self._bar.set_colors(self.colors())
        self._refresh_chrome()
        self.changed.emit()

    def _on_count(self, n: int) -> None:
        for i, chip in enumerate(self._swatches):
            chip.setVisible(i < n)
        self._refresh_chrome()
        self._bar.set_colors(self.colors())
        self.changed.emit()

    def _add(self) -> None:
        self._count.setValue(min(self._nmax, self._count.value() + 1))

    def _del(self) -> None:
        self._count.setValue(max(self._nmin, self._count.value() - 1))

    def _refresh_chrome(self) -> None:
        n = self._count.value()
        show_edit = self._nmax > self._nmin
        self._btn_add.setVisible(show_edit and n < self._nmax)
        self._btn_del.setVisible(show_edit and n > self._nmin)
        # One stop is a flat colour: show its hex, not a 1-colour gradient bar.
        self._bar.setVisible(n > 1)
        cols = self.colors()
        self._hex.setVisible(n == 1)
        self._hex.setText(cols[0].upper() if n == 1 and cols else "")

    def blockSignals(self, b: bool) -> None:
        super().blockSignals(b)
        self._count.blockSignals(b)
        self._btn_add.blockSignals(b)
        self._btn_del.blockSignals(b)
        for chip in self._swatches:
            chip.blockSignals(b)


def _section_of(widget):
    """Nearest collapsible inspector section, or None."""
    from cutora.ui.widgets.collapsible import CollapsibleSection

    cur = widget
    while cur is not None:
        if isinstance(cur, CollapsibleSection):
            return cur
        cur = cur.parentWidget()
    return None


@dataclass(frozen=True)
class Row:
    """One inspector row. ``attr`` is a panel attribute name."""

    label: str
    attr: str
    extra: tuple = ()
    reset: str | None = None


@dataclass(frozen=True)
class Section:
    """One collapsible inspector section. ``toggle`` is a panel attr or None."""

    title: str
    toggle: str | None = None
    gray_when_off: bool = False
    collapsed: bool = True
    rows: tuple = field(default_factory=tuple)


def layout_attr_names(layout) -> list:
    """Every widget attribute referenced by an INSPECTOR_LAYOUT table."""
    names = []
    for sec in layout:
        if getattr(sec, "toggle", None):
            names.append(sec.toggle)
        for row in getattr(sec, "rows", ()) or ():
            if getattr(row, "attr", None):
                names.append(row.attr)
            for extra in getattr(row, "extra", ()) or ():
                names.append(extra)
    return names


def _wrap_row_widgets(panel, row):
    """Return the field widget for ``row``, combining ``extra`` on one line."""
    primary = getattr(panel, row.attr, None)
    extras = [
        getattr(panel, name, None)
        for name in (row.extra or ())
        if getattr(panel, name, None) is not None
    ]
    if primary is None and not extras:
        return None
    if not extras:
        return primary
    from PySide6.QtWidgets import QCheckBox

    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(INSPECTOR_FIELD_GAP)
    parts = ([primary] if primary is not None else []) + extras
    if all(isinstance(p, QCheckBox) for p in parts):
        # A group of checkboxes reads as one line of options, left-packed.
        lay.setSpacing(INSPECTOR_OPTION_GAP)
        for p in parts:
            lay.addWidget(p, 0)
        lay.addStretch(1)
        return host
    if primary is not None:
        lay.addWidget(primary, 1)
    for extra in extras:
        lay.addWidget(extra, 0)
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return host


def _is_hint_widget(widget) -> bool:
    from PySide6.QtWidgets import QLabel

    if widget is None:
        return True
    name = (widget.objectName() or "")
    if name in ("hint", "errHint"):
        return True
    return isinstance(widget, QLabel)


def _is_hint_row(panel, row) -> bool:
    attr = row.attr or ""
    if attr.endswith("_hint") or attr.endswith("_disclaimer"):
        return True
    return _is_hint_widget(getattr(panel, attr, None))


def _fx_widget_names() -> set:
    from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS
    return set(FX_FIELD_WIDGETS.values())


def _snapshot_widget(w):
    """Factory value of a resettable field, or None if the row is not a value."""
    from PySide6.QtWidgets import (
        QAbstractSpinBox, QCheckBox, QComboBox, QPlainTextEdit, QPushButton,
        QSlider,
    )
    from cutora.ui.widgets.collapsible import ToggleSwitch

    if w is None or _is_hint_widget(w):
        return None
    if isinstance(w, QPushButton) and w.objectName() != "swatchBtn":
        return None
    if isinstance(w, SliderSpin):
        return ("value", w.value())
    if isinstance(w, ColorSwatch):
        return ("hex", w.hex())
    if isinstance(w, QComboBox):
        return ("combo", w.currentIndex(), w.currentText(), w.currentData())
    if isinstance(w, (QCheckBox, ToggleSwitch)):
        return ("checked", w.isChecked())
    if isinstance(w, (QAbstractSpinBox, QSlider)):
        return ("value", w.value())
    if isinstance(w, QPlainTextEdit):
        return ("plain", w.toPlainText())
    if hasattr(w, "flags") and callable(w.flags) and hasattr(w, "set_flags"):
        return ("flags", list(w.flags()))
    if hasattr(w, "colors") and callable(w.colors) and hasattr(w, "set_colors"):
        return ("colors", list(w.colors() or []))
    if hasattr(w, "pair") and callable(w.pair) and hasattr(w, "set_pair"):
        return ("pair", list(w.pair()))
    return None


def _restore_widget(w, snap) -> None:
    if w is None or snap is None:
        return
    kind = snap[0]
    w.blockSignals(True)
    try:
        if kind == "value":
            w.setValue(snap[1])
        elif kind == "hex":
            w.set_hex(snap[1], emit=False)
        elif kind == "combo":
            idx, text, data = snap[1], snap[2], snap[3]
            if data is not None:
                found = w.findData(data)
                if found >= 0:
                    w.setCurrentIndex(found)
                else:
                    w.setCurrentIndex(idx)
            elif text:
                w.setCurrentText(text)
            else:
                w.setCurrentIndex(idx)
        elif kind == "checked":
            w.setChecked(bool(snap[1]))
        elif kind == "plain":
            w.setPlainText(snap[1])
        elif kind == "flags":
            w.set_flags(snap[1], emit=False)
        elif kind == "colors":
            w.set_colors(snap[1], emit=False)
        elif kind == "pair":
            w.set_pair(snap[1], emit=False)
    finally:
        w.blockSignals(False)


def _row_reset(panel, row):
    attr = row.attr
    kind = row.reset
    if kind == "preset":
        fn = getattr(panel, "_reset_preset_row", None)
        return fn if callable(fn) else None
    if _is_hint_row(panel, row):
        return None
    fx_names = _fx_widget_names()
    if kind == "fx" or (isinstance(attr, str) and attr in fx_names):
        if attr not in fx_names:
            return None

        def _go_fx(p=panel, a=attr):
            from cutora.ui.panels.fx_fields import reset_fx_attr
            reset_fx_attr(p, a)
        return _go_fx
    attrs = [attr] + list(row.extra or ())
    snaps = []
    for name in attrs:
        snap = _snapshot_widget(getattr(panel, name, None))
        if snap is not None:
            snaps.append((name, snap))
    if not snaps:
        return None

    def _go_sub(p=panel, pairs=tuple(snaps)):
        for name, snap in pairs:
            _restore_widget(getattr(p, name, None), snap)
        on_change = getattr(p, "on_change", None)
        if callable(on_change):
            on_change()
    return _go_sub


def build_inspector(panel, layout, scroll=None):
    """Render ``layout`` (list of Section) into ``panel``'s scroll area.

    Widgets must already exist on ``panel`` as attributes. This function
    only parents them into CollapsibleSection + InspectorRow. A missing
    attribute raises; optional rows (value is None, e.g. chk_detached)
    are skipped.
    """
    from PySide6.QtWidgets import QScrollArea, QSizePolicy, QVBoxLayout
    from cutora.ui.theme.tokens import (
        INSPECTOR_PAD_X,
        INSPECTOR_PAD_Y,
        INSPECTOR_ROW_GAP,
        INSPECTOR_SECTION_GAP,
    )
    from cutora.ui.widgets.collapsible import CollapsibleSection
    from cutora.ui.widgets.responsive import configure_scroll, harden_widget

    if scroll is None:
        for i in range(panel.layout.count()):
            item = panel.layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, QScrollArea):
                scroll = w
                break
    if scroll is None:
        _LOG.error("build_inspector: panel has no QScrollArea")
        return

    new_widget = QWidget()
    new_widget.setObjectName("dockSubContent")
    vbox = QVBoxLayout(new_widget)
    vbox.setContentsMargins(0, INSPECTOR_ROW_GAP, 0, 0)
    vbox.setSpacing(INSPECTOR_SECTION_GAP)

    for spec in layout:
        toggle = getattr(panel, spec.toggle, None) if spec.toggle else None
        sec = CollapsibleSection(
            spec.title,
            toggle_checkbox=toggle,
            initially_collapsed=bool(spec.collapsed),
            gray_content=bool(spec.gray_when_off),
        )
        body = QWidget()
        form = QVBoxLayout(body)
        form.setSpacing(INSPECTOR_ROW_GAP)
        form.setContentsMargins(
            INSPECTOR_PAD_X, INSPECTOR_PAD_Y, INSPECTOR_PAD_X, INSPECTOR_PAD_Y)
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for row in spec.rows or ():
            if not hasattr(panel, row.attr):
                raise AttributeError(
                    "build_inspector: panel has no attribute %r (section %r)"
                    % (row.attr, spec.title))
            for extra in row.extra or ():
                if not hasattr(panel, extra):
                    raise AttributeError(
                        "build_inspector: panel has no attribute %r "
                        "(section %r extra)" % (extra, spec.title))
            field = _wrap_row_widgets(panel, row)
            if field is None:
                continue
            add_inspect_row(form, row.label, field, on_reset=_row_reset(panel, row))
        sec.add_widget(body)
        live = any(r.reset_btn.isVisible() for r in body.findChildren(InspectorRow))
        reset_btn = getattr(sec, "_reset_btn", None)
        if reset_btn is not None:
            reset_btn.setVisible(live)
        vbox.addWidget(sec)
        apply_section_label_widths(sec)

    vbox.addStretch()
    new_widget.setMinimumWidth(0)
    scroll.setWidget(new_widget)
    configure_scroll(scroll)
    harden_widget(scroll)
    install_inspector_limits(panel)
    from cutora.ui.panels.fx_fields import _sync_fill_mode
    _sync_fill_mode(panel)


def _label_advance(label, text: str) -> int:
    fm = QFontMetrics(label.font())
    adv = int(fm.horizontalAdvance(text))
    box = fm.boundingRect(text)
    return max(adv, int(box.width()), 0)


def _label_root(widget):
    """The scroll-content widget (or top panel) that owns ``widget``'s rows.
    Every label in one root shares one column width, so fields line up
    across sections instead of jumping at each section boundary."""
    cur = widget
    while cur is not None:
        par = cur.parentWidget()
        if par is None:
            return cur
        if (par.objectName() or "") == "qt_scrollarea_viewport":
            return cur
        cur = par
    return widget


def _root_label_width(root, sample_row) -> int:
    rows = [
        r for r in root.findChildren(InspectorRow)
        if not r._span and (r.full_label_text() or "").strip()
    ]
    if not rows:
        return 0
    total = max(0, sample_row.width())
    reset_w = INSPECTOR_RESET_PX
    available = total - reset_w - INSPECTOR_FIELD_GAP * 2 - INSPECTOR_SLIDER_MIN
    cap = max(INSPECTOR_LABEL_MIN, min(INSPECTOR_LABEL_MAX, available))
    needed = 0
    for row in rows:
        needed = max(needed, _label_advance(row.label, row.full_label_text()))
    return max(INSPECTOR_LABEL_MIN, min(cap, needed))


def apply_section_label_widths(section) -> None:
    """Size the label column of every row under ``section``'s root panel to
    the longest label that fits. Cached per (root, row width, row count) so
    a resize costs one pass, not one pass per row."""
    root = _label_root(section)
    if getattr(root, "_label_sync_busy", False):
        return
    rows = root.findChildren(InspectorRow)
    if not rows:
        return
    sample = next((r for r in rows if r.width() > 0 and not r._span), None)
    if sample is None:
        return
    width = _root_label_width(root, sample)
    root._label_sync_busy = True
    try:
        for row in rows:
            if not row._span:
                row.apply_label_width(width)
        root._label_cache = (sample.width(), len(rows), width)
    finally:
        root._label_sync_busy = False


def _schedule_label_sync(root, row) -> None:
    pending = getattr(root, "_label_sync_pending", None)
    if pending is not None and pending():
        return
    from PySide6.QtCore import QTimer
    import weakref
    ref = weakref.ref(row)
    root._label_sync_pending = lambda: True

    def _run():
        try:
            root._label_sync_pending = None
        except RuntimeError:
            return
        r = ref()
        if r is None:
            return
        try:
            apply_section_label_widths(r)
        except RuntimeError:
            pass
    QTimer.singleShot(0, _run)


class KeyframeWatch(QToolButton):
    """Stopwatch: dim = off, gold = path enabled, filled diamond = on a key."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("keyframeWatch")
        self.setFixedSize(INSPECTOR_RESET_PX, INSPECTOR_RESET_PX)
        mark_explicit(self, "maximumWidth")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tr("为此参数打关键帧"))
        self.setCheckable(True)
        self._path = ""
        self._enabled = False
        self._on_key = False

    def set_path(self, path: str) -> None:
        self._path = str(path or "")

    def path(self) -> str:
        return self._path

    def set_state(self, enabled, on_key) -> None:
        self._enabled = bool(enabled)
        self._on_key = bool(on_key)
        prev = self.blockSignals(True)
        try:
            self.setChecked(self._enabled)
        finally:
            self.blockSignals(prev)
        self.update()

    def paintEvent(self, event) -> None:
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 2.0
        if self._enabled:
            ring = QColor(tok.accent)
            fill = QColor(tok.accent)
        else:
            ring = QColor(tok.fg_muted)
            fill = QColor(tok.fg_muted)
        p.setPen(QPen(ring, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy + 1.0), r - 1.0, r - 1.0)
        # Crown
        p.drawLine(QPointF(cx, cy - r + 1.0), QPointF(cx, cy - r - 1.5))
        p.drawLine(QPointF(cx - 2.0, cy - r - 1.5), QPointF(cx + 2.0, cy - r - 1.5))
        # Hands
        p.drawLine(QPointF(cx, cy + 1.0), QPointF(cx, cy - r + 4.0))
        p.drawLine(QPointF(cx, cy + 1.0), QPointF(cx + r - 4.0, cy + 1.0))
        # Diamond in the well
        d = 3.5 if self._on_key else 3.0
        dia = QPolygonF([
            QPointF(cx, cy + 1.0 - d),
            QPointF(cx + d, cy + 1.0),
            QPointF(cx, cy + 1.0 + d),
            QPointF(cx - d, cy + 1.0),
        ])
        if self._on_key and self._enabled:
            p.setBrush(QBrush(fill))
            p.setPen(Qt.PenStyle.NoPen)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1.0))
        p.drawPolygon(dia)
        p.end()


class InspectorRow(QWidget):
    """Two-column inspector row: right-aligned label, field, per-row reset."""

    def __init__(self, label, widget, on_reset=None, parent=None, span=False):
        super().__init__(parent)
        self.setObjectName("inspectorRow")
        self._on_reset = on_reset
        self.field = widget
        # span=True: a composite field (transform grid, text editor) that
        # carries its own labels and takes the full row width.
        self._span = bool(span)
        text = "" if label is None else str(label)
        self._label_text = text.strip().rstrip(":：").strip()
        self._label_w = INSPECTOR_LABEL_MAX
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(INSPECTOR_FIELD_GAP)
        self.label = QLabel(self._label_text)
        self.label.setObjectName("inspectorLabel")
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setWordWrap(False)
        self.label.setToolTip(self._label_text)
        font = QFont(self.label.font())
        font.setPixelSize(INSPECTOR_LABEL_FONT)
        self.label.setFont(font)
        self.label.setFixedWidth(self._label_w)
        lay.addWidget(self.label, 0)
        if self._span:
            self.label.hide()
        self._field_host = QWidget(self)
        field_lay = QHBoxLayout(self._field_host)
        field_lay.setContentsMargins(0, 0, 0, 0)
        field_lay.setSpacing(INSPECTOR_FIELD_GAP)
        if widget is not None:
            if widget.parent() is not self._field_host:
                widget.setParent(self._field_host)
            self._apply_field_limits(widget)
            hpol = widget.sizePolicy().horizontalPolicy()
            capped = widget.maximumWidth() < QWIDGETSIZE_MAX
            expanding = self._span or isinstance(widget, SliderSpin) or (
                hpol in (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
                and not capped)
            grows = hpol in (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            stretch = 1 if (expanding or grows) else 0
            field_lay.addWidget(widget, stretch)
            if isinstance(widget, ColorSwatch):
                self._hex = QLabel(widget.hex() or "")
                self._hex.setObjectName("inspectorHex")
                hx_font = QFont(FONT_MONO)
                hx_font.setPixelSize(INSPECTOR_LABEL_FONT)
                self._hex.setFont(hx_font)
                field_lay.addWidget(self._hex, 0)
                widget.changed.connect(self._sync_hex)
            if not expanding:
                # Capped-but-growing fields fill to their cap first; the
                # zero-stretch spacer only takes what is left after that.
                field_lay.addStretch(0 if grows else 1)
        lay.addWidget(self._field_host, 1)
        self.reset_btn = QToolButton(self)
        self.reset_btn.setObjectName("inspectorReset")
        self.reset_btn.setText("↺")
        self.reset_btn.setToolTip(tr("重置此参数"))
        self.reset_btn.setFixedSize(INSPECTOR_RESET_PX, INSPECTOR_RESET_PX)
        self.reset_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        mark_explicit(self.reset_btn, "maximumWidth")
        self.reset_btn.clicked.connect(self.trigger_reset)
        if callable(on_reset):
            lay.addWidget(self.reset_btn, 0)
        else:
            self.reset_btn.hide()
            if not self._span:
                # Keep the reset column even when empty so every field in the
                # panel ends on the same right edge.
                lay.addSpacing(INSPECTOR_RESET_PX)
        self.kf_watch = None
        self._lock_row_height()
        self._watched = []
        if widget is not None:
            # Rows follow their field: code that hides a field (panel-type
            # switches) no longer leaves an orphan label + reset button.
            # A plain host widget (field + extras) counts as hidden when every
            # part inside it is hidden.
            self._watched = [widget]
            if type(widget) is QWidget:
                self._watched += [
                    c for c in widget.children()
                    if isinstance(c, QWidget) and not c.isWindow()]
            for w in self._watched:
                w.installEventFilter(self)
            if not self._field_shown():
                self.setVisible(False)

    def _field_shown(self) -> bool:
        field = self.field
        if field is None:
            return True
        if field.isHidden():
            return False
        parts = self._watched[1:]
        if parts and all(p.isHidden() for p in parts):
            return False
        return True

    def eventFilter(self, obj, event):
        if event is not None and obj in self._watched:
            et = event.type()
            if et in (QEvent.Type.HideToParent, QEvent.Type.ShowToParent):
                self.setVisible(self._field_shown())
        return False

    def _lock_row_height(self) -> None:
        """Rows size to their content: single-line fields get exactly INSPECTOR_ROW_H,
        tall fields (geometry grid, text editor, colour stops) get their own
        height. Never pin a row below its field — that clipped every control
        into a 24px strip. Height is recomputed live via sizeHint() / minimumSizeHint()."""
        self.setMaximumHeight(QWIDGETSIZE_MAX)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.updateGeometry()

    def _content_h(self) -> int:
        lay = self.layout()
        h = int(lay.sizeHint().height()) if lay is not None else 0
        if self.field is not None and not self.field.isHidden():
            f = self.field
            want = max(int(f.sizeHint().height()),
                       int(f.minimumSizeHint().height()),
                       int(f.minimumHeight() or 0))
            # A field with a fixed/max height (list, text box) must not
            # inflate the row past what it can actually fill.
            h = max(h, min(want, int(f.maximumHeight())))
        return max(INSPECTOR_ROW_H, h)

    def hasHeightForWidth(self) -> bool:
        lay = self.layout()
        return bool(lay is not None and lay.hasHeightForWidth())

    def heightForWidth(self, width: int) -> int:
        # Word-wrapped hints grow the row instead of spilling over the next.
        lay = self.layout()
        h = lay.totalHeightForWidth(width) if lay is not None else -1
        return max(INSPECTOR_ROW_H, int(h), self._content_h() if h < 0 else 0)

    def _wrapped_h(self) -> int:
        # Rows are vertically Fixed (so they never stretch), and Qt caps a
        # Fixed item at its sizeHint even when heightForWidth is larger. So
        # fold the wrapped height for the current width into the hint.
        h = self._content_h()
        if self.width() > 0 and self.hasHeightForWidth():
            lay = self.layout()
            h = max(h, int(lay.totalHeightForWidth(self.width())))
        return h

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(base.width(), self._wrapped_h())

    def minimumSizeHint(self) -> QSize:
        base = super().minimumSizeHint()
        return QSize(base.width(), self._wrapped_h())

    def attach_keyframe(self, path: str) -> KeyframeWatch:
        """Put a stopwatch on the right of this row. Hidden until a clip is selected."""
        if self.kf_watch is None:
            self.kf_watch = KeyframeWatch(self)
            lay = self.layout()
            # Sit just left of the reset button when it is in the layout.
            idx = lay.indexOf(self.reset_btn)
            if idx < 0:
                lay.addWidget(self.kf_watch, 0)
            else:
                lay.insertWidget(idx, self.kf_watch, 0)
        self.kf_watch.set_path(path)
        self.kf_watch.show()
        return self.kf_watch

    def _sync_hex(self) -> None:
        hx = getattr(self, "_hex", None)
        if hx is not None and isinstance(self.field, ColorSwatch):
            hx.setText(self.field.hex() or "")

    def _apply_field_limits(self, widget) -> None:
        """Width caps only. Heights come from QSS (CONTROL_INNER_H content +
        1px border = CONTROL_H); a Python setFixedHeight here fought the QSS
        min-height and clipped text."""
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QPushButton

        if isinstance(widget, QComboBox):
            _cap_combo(widget)
        elif isinstance(widget, (SliderSpin, ColorSwatch)):
            return
        elif isinstance(widget, QAbstractSpinBox):
            _cap_spin(widget)
        elif isinstance(widget, QPushButton):
            if widget.objectName() == "swatchBtn":
                widget.setMaximumWidth(INSPECTOR_COLOR_MAX_W)
                mark_explicit(widget, "maximumWidth")

    def trigger_reset(self) -> None:
        if callable(self._on_reset):
            self._on_reset()
            return
        if self.reset_btn.isVisible():
            _LOG.warning("inspector reset with no handler label=%r", self.label.text())

    def _reset_reserve(self) -> int:
        if not self.reset_btn.isVisible():
            return 0
        return INSPECTOR_RESET_PX

    def full_label_text(self) -> str:
        return self._label_text

    def apply_label_width(self, width: int) -> None:
        width = max(0, int(width))
        if width != self._label_w:
            self._label_w = width
            self.label.setFixedWidth(width)
        self.label.show()
        if not self._label_text.strip():
            self.label.setText("")
            self.label.setToolTip("")
            return
        if width <= 0:
            self.label.setText(self._label_text)
            self.label.setToolTip(self._label_text)
            return
        fm = QFontMetrics(self.label.font())
        elided = fm.elidedText(
            self._label_text, Qt.TextElideMode.ElideRight, width)
        self.label.setText(elided)
        self.label.setToolTip(self._label_text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (event is not None and event.oldSize().width() != self.width()
                and self.hasHeightForWidth()):
            self.updateGeometry()
        if self._span:
            return
        root = _label_root(self)
        cache = getattr(root, "_label_cache", None)
        if cache is not None and cache[0] == self.width():
            if cache[2] != self._label_w:
                self.apply_label_width(cache[2])
            return
        # Q161：一次换面板会连着来几十个 resizeEvent（每个都整棵树扫一遍 → 点一下片段卡 200 ms）。
        # 先按上次的宽度摆好，整棵树的重算攒到事件循环空下来做一次。
        if cache is not None and cache[2] != self._label_w:
            self.apply_label_width(cache[2])
        _schedule_label_sync(root, self)


class ColorWheel(QWidget):
    """Circular lift/gamma/gain control. Drag the disc for colour bias;
    mouse wheel on the outer ring changes luminance. Not three sliders."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorColorWheel")
        self.setFixedSize(INSPECTOR_WHEEL_PX, INSPECTOR_WHEEL_PX)
        mark_explicit(self, "maximumWidth")
        mark_explicit(self, "minimumWidth")
        mark_explicit(self, "maximumHeight")
        mark_explicit(self, "minimumHeight")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._r = 0.0
        self._g = 0.0
        self._y = 0.0
        self._drag = False

    def value(self):
        return (float(self._r), float(self._g), float(self._y))

    def set_value(self, r, g, y) -> None:
        self._r = float(max(-1.0, min(1.0, r)))
        self._g = float(max(-1.0, min(1.0, g)))
        self._y = float(max(-1.0, min(1.0, y)))
        self.update()
        if not self.signalsBlocked():
            self.changed.emit()

    def reset(self) -> None:
        self.set_value(0.0, 0.0, 0.0)

    def sizeHint(self) -> QSize:
        return QSize(INSPECTOR_WHEEL_PX, INSPECTOR_WHEEL_PX)

    def minimumSizeHint(self) -> QSize:
        return QSize(INSPECTOR_WHEEL_PX, INSPECTOR_WHEEL_PX)

    def _center_radius(self):
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        r = min(self.width(), self.height()) / 2.0 - 1.0
        ring = max(float(INSPECTOR_FIELD_GAP) * 2.0, 1.0)
        return c, r, ring

    def _from_pos(self, pos):
        c, rmax, ring = self._center_radius()
        dx = float(pos.x()) - c.x()
        dy = float(pos.y()) - c.y()
        inner = max(1.0, rmax - ring)
        self._r = max(-1.0, min(1.0, dx / inner))
        self._g = max(-1.0, min(1.0, -dy / inner))
        mag = (self._r * self._r + self._g * self._g) ** 0.5
        if mag > 1.0:
            self._r /= mag
            self._g /= mag

    def mousePressEvent(self, event):
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self.reset()
                event.accept()
                return
            self._drag = True
            self._from_pos(event.position() if hasattr(event, "position") else event.pos())
            self.update()
            if not self.signalsBlocked():
                self.changed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.reset()
        if event is not None:
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag and event is not None:
            self._from_pos(event.position() if hasattr(event, "position") else event.pos())
            self.update()
            if not self.signalsBlocked():
                self.changed.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event is None:
            return
        delta = 0
        if hasattr(event, "angleDelta"):
            delta = int(event.angleDelta().y())
        step = 0.05 if delta > 0 else -0.05
        if delta == 0:
            return
        self._y = max(-1.0, min(1.0, self._y + step))
        self.update()
        if not self.signalsBlocked():
            self.changed.emit()
        event.accept()

    def paintEvent(self, event) -> None:
        tok = current()
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            c, rmax, ring = self._center_radius()
            inner = max(1.0, rmax - ring)
            cone = QConicalGradient(c, 0)
            for deg, hx in (
                (0, "#E07070"),
                (60, "#E0C050"),
                (120, "#70C070"),
                (180, "#50C0C8"),
                (240, "#6080D0"),
                (300, "#C070C0"),
                (360, "#E07070"),
            ):
                cone.setColorAt((deg / 360.0) % 1.0, QColor(hx))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(cone))
            p.drawEllipse(c, rmax, rmax)
            p.setBrush(QColor(tok.bg1))
            p.drawEllipse(c, inner, inner)
            rad = QRadialGradient(c, inner)
            rad.setColorAt(0.0, QColor(tok.fg0))
            rad.setColorAt(0.12, QColor(tok.fg0))
            rad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(rad))
            p.drawEllipse(c, inner, inner)
            ycol = QColor(tok.accent)
            ycol.setAlpha(40 + int(80 * abs(self._y)))
            p.setPen(QPen(ycol, ring * 0.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rmax - ring * 0.5, rmax - ring * 0.5)
            px = c.x() + self._r * inner
            py = c.y() - self._g * inner
            p.setBrush(QColor(tok.fg0))
            p.setPen(QPen(QColor(tok.bg0), 1))
            p.drawEllipse(QPointF(px, py), float(INSPECTOR_FIELD_GAP), float(INSPECTOR_FIELD_GAP))
        finally:
            p.end()


# Back-compat alias used by T18 / T19 tests.
ColorsList = ColorStops
