# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Industry-standard parameter & mouse-wheel interaction (Resolve / Premiere / Figma style).

Core guarantees:
1. Scrolling the mouse wheel over ANY parameter widget (QSpinBox, QDoubleSpinBox,
   QComboBox, QSlider, QDial, ColorWheel) NEVER mutates the parameter by accident.
   Instead, the wheel event is cleanly forwarded to the enclosing QScrollArea so
   the inspector/panel scrolls smoothly from top to bottom.
2. Intentional wheel fine-tuning is available via ``Alt + MouseWheel`` (or
   ``Alt + Shift + MouseWheel`` for 10x step).
3. Scrubby number inputs (Premiere / DaVinci Resolve / Figma standard):
   - Press & drag horizontally (``↔``) on any QSpinBox / QDoubleSpinBox (or its
     InspectorRow label) to smoothly scrub the value in real time
     (``Shift`` = 10x fast, ``Ctrl``/``Alt`` = 0.1x fine).
   - Click without dragging to type an exact value directly.
   - Double-click an InspectorRow label to reset the parameter to default.
"""
from __future__ import annotations

import weakref
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QScrollBar,
    QSlider,
    QSpinBox,
    QWidget,
)

_SCRUB_HINT_ZH = "💡 左右拖拽 ↔ 平滑调节 · 单击输入数字 · Alt+滚轮微调"


def _enclosing_param_widget(obj: QObject) -> Optional[QWidget]:
    """Return the parameter widget if ``obj`` is one (or is an internal child of one)."""
    if not isinstance(obj, QWidget):
        return None
    if isinstance(obj, QScrollBar):
        return None
    cur: Optional[QWidget] = obj
    depth = 0
    while cur is not None and depth < 5:
        if isinstance(cur, QScrollBar):
            return None
        if isinstance(cur, (QAbstractSpinBox, QComboBox, QAbstractSlider)):
            return cur
        cls_name = type(cur).__name__
        obj_name = cur.objectName() or ""
        if cls_name == "ColorWheel" or obj_name == "inspectorColorWheel":
            return cur
        cur = cur.parentWidget()
        depth += 1
    return None


def _enclosing_spinbox(obj: QObject) -> Optional[QAbstractSpinBox]:
    if not isinstance(obj, QWidget):
        return None
    cur: Optional[QWidget] = obj
    depth = 0
    while cur is not None and depth < 4:
        if isinstance(cur, (QSpinBox, QDoubleSpinBox)):
            return cur
        cur = cur.parentWidget()
        depth += 1
    return None


def _find_parent_scroll_area(widget: QWidget) -> Optional[QAbstractScrollArea]:
    """Walk up parents to find the enclosing panel QScrollArea / QAbstractScrollArea."""
    cur = widget.parentWidget()
    while cur is not None:
        if isinstance(cur, QScrollArea):
            return cur
        if isinstance(cur, QAbstractScrollArea) and not isinstance(
            cur.parentWidget(), QComboBox
        ):
            return cur
        cur = cur.parentWidget()
    return None


def _scroll_enclosing_area(widget: QWidget, event: QWheelEvent) -> bool:
    """Scroll the enclosing QScrollArea using the wheel event's angle/pixel delta."""
    sa = _find_parent_scroll_area(widget)
    if sa is None:
        return False
    vbar = sa.verticalScrollBar()
    hbar = sa.horizontalScrollBar()
    pixel_delta = event.pixelDelta() if hasattr(event, "pixelDelta") else QPoint()
    angle_delta = event.angleDelta() if hasattr(event, "angleDelta") else QPoint()

    scrolled = False
    if vbar is not None and vbar.isVisible() and vbar.maximum() > vbar.minimum():
        dy = pixel_delta.y()
        if dy == 0 and angle_delta.y() != 0:
            # Standard 120 units per wheel notch -> ~3 single steps
            step = max(20, vbar.singleStep() or 20)
            dy = int(round((angle_delta.y() / 120.0) * step * 3))
        if dy != 0:
            vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), vbar.value() - dy)))
            scrolled = True

    if not scrolled and hbar is not None and hbar.isVisible() and hbar.maximum() > hbar.minimum():
        dx = pixel_delta.x() or pixel_delta.y()
        if dx == 0:
            raw_a = angle_delta.x() or angle_delta.y()
            if raw_a != 0:
                step = max(20, hbar.singleStep() or 20)
                dx = int(round((raw_a / 120.0) * step * 3))
        if dx != 0:
            hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), hbar.value() - dx)))
            scrolled = True

    return True


class _SpinScrubOverlay(QWidget):
    """Lightweight transparent overlay drawn inside a SpinBox to show:
    - A 2px bottom value-fill bar (when bounded)
    - Subtle hover scrub indicator ``‹ ›`` and gold scrub border
    """

    def __init__(self, spin: QAbstractSpinBox):
        super().__init__(spin)
        self._spin_ref = weakref.ref(spin)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False
        self._scrubbing = False
        self.setGeometry(spin.rect())
        self.show()

    def set_state(self, hover: bool, scrubbing: bool) -> None:
        if self._hover != hover or self._scrubbing != scrubbing:
            self._hover = hover
            self._scrubbing = scrubbing
            self.update()

    def paintEvent(self, _event) -> None:
        spin = self._spin_ref()
        if spin is None or not spin.isEnabled():
            return
        w, h = self.width(), self.height()
        if w < 24 or h < 12:
            return
        try:
            from cutora.ui.theme.tokens import current
            tok = current()
            accent_hex = tok.accent
            muted_hex = tok.fg_muted
        except Exception:
            accent_hex = "#D4AF37"
            muted_hex = "#8A8275"

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Bottom value ratio bar for reasonable bounded ranges
        try:
            lo = float(spin.minimum())
            hi = float(spin.maximum())
            val = float(spin.value())
            span = hi - lo
            if 0 < span <= 5000:
                ratio = max(0.0, min(1.0, (val - lo) / span))
                if ratio > 0.005 or self._hover or self._scrubbing:
                    bar_c = QColor(accent_hex)
                    bar_c.setAlphaF(0.85 if self._scrubbing else (0.55 if self._hover else 0.28))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(bar_c)
                    bar_w = max(2.0, (w - 4.0) * ratio)
                    p.drawRoundedRect(QRectF(2.0, h - 2.5, bar_w, 1.8), 1.0, 1.0)
        except Exception:
            pass

        # 2. Active scrub glow border
        if self._scrubbing:
            glow = QColor(accent_hex)
            glow.setAlphaF(0.9)
            p.setPen(QPen(glow, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5), 4.0, 4.0)
        elif self._hover and not spin.hasFocus():
            # Subtle right-side scrub arrows ``‹ ›`` so user sees it's draggable/steppable
            chev = QColor(muted_hex)
            chev.setAlphaF(0.65)
            p.setPen(chev)
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(
                QRectF(w - 18, 0, 16, h - 1),
                Qt.AlignmentFlag.AlignCenter,
                "↔",
            )
        p.end()


def _ensure_spin_overlay(spin: QAbstractSpinBox) -> Optional[_SpinScrubOverlay]:
    if not isinstance(spin, (QSpinBox, QDoubleSpinBox)):
        return None
    ov = getattr(spin, "_cutora_scrub_overlay", None)
    if ov is None:
        try:
            ov = _SpinScrubOverlay(spin)
            spin._cutora_scrub_overlay = ov
            spin.valueChanged.connect(lambda *_a, _o=ov: _o.update())
        except Exception:
            return None
    return ov


class WheelGuardAndScrubFilter(QObject):
    """Application-wide event filter enforcing:
    1) Unconditional scroll safety on parameter widgets (wheel scrolls the page, never mutates controls).
    2) Resolve/Premiere/Figma-style horizontal scrubbing on QSpinBox/QDoubleSpinBox and InspectorRow labels.
    """

    DRAG_THRESHOLD_PX = 3
    PIXELS_PER_STEP = 4.0

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._scrub_spin: Optional[QAbstractSpinBox] = None
        self._scrub_slider: Optional[QSlider] = None
        self._scrub_origin_x: int = 0
        self._scrub_last_x: int = 0
        self._scrub_start_val: float = 0.0
        self._scrub_accum_px: float = 0.0
        self._scrub_active: bool = False
        self._scrub_target_widget: Optional[QWidget] = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event is None:
            return False
        et = event.type()

        # ── 1. Wheel Event Guard (Industry Standard: Scroll Page, Never Mutate Unfocused/Hovered Params) ──
        if et == QEvent.Type.Wheel:
            param_w = _enclosing_param_widget(obj)
            if param_w is not None:
                # Never interfere with an open QComboBox popup list scrolling itself
                if isinstance(param_w, QComboBox):
                    view = param_w.view()
                    if view is not None and view.isVisible() and (obj is view or view.isAncestorOf(obj)):
                        return False
                mods = event.modifiers() if hasattr(event, "modifiers") else Qt.KeyboardModifier.NoModifier
                # Allow deliberate parameter wheel adjustment ONLY when holding Alt
                if mods & Qt.KeyboardModifier.AltModifier:
                    return False
                # Otherwise: block the parameter widget from changing its value and scroll the panel!
                _scroll_enclosing_area(param_w, event)
                event.accept()
                return True
            return False

        # ── 2. SpinBox Overlay Geometry & Hover Feedback ──
        if et == QEvent.Type.Resize and isinstance(obj, (QSpinBox, QDoubleSpinBox)):
            ov = getattr(obj, "_cutora_scrub_overlay", None)
            if ov is not None:
                ov.setGeometry(obj.rect())
            return False

        if et == QEvent.Type.Enter:
            spin = _enclosing_spinbox(obj)
            if spin is not None and spin.isEnabled() and not spin.isReadOnly():
                ov = _ensure_spin_overlay(spin)
                if ov is not None:
                    ov.setGeometry(spin.rect())
                    ov.set_state(True, self._scrub_active and self._scrub_spin is spin)
                self._ensure_scrub_tooltip(spin)
                if not spin.hasFocus():
                    le = spin.lineEdit()
                    if le is not None and le.cursor().shape() != Qt.CursorShape.SizeHorCursor:
                        le.setCursor(Qt.CursorShape.SizeHorCursor)
            elif isinstance(obj, QLabel) and (obj.objectName() or "") == "inspectorLabel":
                row = obj.parentWidget()
                if row is not None and self._row_scrub_target(row) is not None:
                    obj.setCursor(Qt.CursorShape.SizeHorCursor)
                    self._ensure_label_tooltip(obj)
            return False

        if et == QEvent.Type.Leave:
            spin = _enclosing_spinbox(obj)
            if spin is not None:
                ov = getattr(spin, "_cutora_scrub_overlay", None)
                if ov is not None and not (self._scrub_active and self._scrub_spin is spin):
                    ov.set_state(False, False)
            return False

        if et == QEvent.Type.FocusIn:
            spin = _enclosing_spinbox(obj)
            if spin is not None:
                le = spin.lineEdit()
                if le is not None:
                    le.setCursor(Qt.CursorShape.IBeamCursor)
            return False

        if et == QEvent.Type.FocusOut:
            spin = _enclosing_spinbox(obj)
            if spin is not None:
                le = spin.lineEdit()
                if le is not None:
                    le.setCursor(Qt.CursorShape.SizeHorCursor)
            return False

        # ── 3. Scrubby Number Input (SpinBox + InspectorRow Label) ──
        if et == QEvent.Type.MouseButtonDblClick:
            if isinstance(obj, QLabel) and (obj.objectName() or "") == "inspectorLabel":
                row = obj.parentWidget()
                if row is not None and hasattr(row, "trigger_reset"):
                    row.trigger_reset()
                    return True
            return False

        if et == QEvent.Type.MouseButtonPress:
            if not hasattr(event, "button") or event.button() != Qt.MouseButton.LeftButton:
                return False

            # Case A: Pressing on an InspectorRow label
            if isinstance(obj, QLabel) and (obj.objectName() or "") == "inspectorLabel":
                row = obj.parentWidget()
                target = self._row_scrub_target(row) if row is not None else None
                if target is not None:
                    gx = self._global_x(obj, event)
                    self._begin_scrub_candidate(obj, target, gx, immediate=True)
                    return True
                return False

            # Case B: Pressing on a QSpinBox / QDoubleSpinBox (or its QLineEdit)
            spin = _enclosing_spinbox(obj)
            if spin is not None and spin.isEnabled() and not spin.isReadOnly():
                # If spinbox already has keyboard edit focus, let user position caret/select text normally
                if spin.hasFocus() and getattr(spin, "_cutora_text_editing", False):
                    return False
                gx = self._global_x(obj, event)
                self._begin_scrub_candidate(obj, spin, gx, immediate=False)
                return True

        elif et == QEvent.Type.MouseMove:
            if self._scrub_target_widget is not None:
                gx = self._global_x(obj, event)
                dx_total = gx - self._scrub_origin_x
                if not self._scrub_active:
                    if abs(dx_total) >= self.DRAG_THRESHOLD_PX:
                        self._scrub_active = True
                        self._scrub_last_x = self._scrub_origin_x
                        self._scrub_accum_px = 0.0
                        QApplication.setOverrideCursor(Qt.CursorShape.SizeHorCursor)
                        if self._scrub_spin is not None:
                            ov = _ensure_spin_overlay(self._scrub_spin)
                            if ov is not None:
                                ov.set_state(True, True)
                    else:
                        return True
                dx = gx - self._scrub_last_x
                self._scrub_last_x = gx
                self._apply_scrub_delta(dx, event.modifiers() if hasattr(event, "modifiers") else Qt.KeyboardModifier.NoModifier)
                return True

        elif et == QEvent.Type.MouseButtonRelease:
            if self._scrub_target_widget is not None:
                was_scrubbing = self._scrub_active
                spin = self._scrub_spin
                self._end_scrub()
                if not was_scrubbing and spin is not None:
                    # Simple click without dragging -> enter direct keyboard text edit mode!
                    spin._cutora_text_editing = True
                    spin.setFocus(Qt.FocusReason.MouseFocusReason)
                    le = spin.lineEdit()
                    if le is not None:
                        le.setCursor(Qt.CursorShape.IBeamCursor)
                        QTimer.singleShot(0, le.selectAll)
                return True

        return False

    # ── Helpers ──

    @staticmethod
    def _global_x(obj: QObject, event) -> int:
        if hasattr(event, "position") and isinstance(obj, QWidget):
            try:
                pt = event.position().toPoint()
                return int(obj.mapToGlobal(pt).x())
            except Exception:
                return int(round(event.position().x()))
        if hasattr(event, "globalPosition"):
            return int(round(event.globalPosition().x()))
        if hasattr(event, "globalPos"):
            return int(event.globalPos().x())
        return QCursor.pos().x()

    @staticmethod
    def _row_scrub_target(row: QWidget):
        field = getattr(row, "field", None)
        if field is None or not field.isEnabled():
            return None
        if isinstance(field, (QSpinBox, QDoubleSpinBox)):
            return field
        spin = getattr(field, "spin", None)
        if isinstance(spin, (QSpinBox, QDoubleSpinBox)) and spin.isEnabled():
            return spin
        if isinstance(field, QSlider) and field.isEnabled():
            return field
        # Check first child spinbox inside paired_fields / with_trailing
        spins = field.findChildren(QAbstractSpinBox)
        for sp in spins:
            if isinstance(sp, (QSpinBox, QDoubleSpinBox)) and sp.isEnabled():
                return sp
        sliders = field.findChildren(QSlider)
        for sl in sliders:
            if sl.isEnabled():
                return sl
        return None

    def _begin_scrub_candidate(self, source_widget: QWidget, target, gx: int, immediate: bool) -> None:
        self._scrub_target_widget = source_widget
        self._scrub_origin_x = gx
        self._scrub_last_x = gx
        self._scrub_accum_px = 0.0
        self._scrub_active = bool(immediate)
        if isinstance(target, (QSpinBox, QDoubleSpinBox)):
            self._scrub_spin = target
            self._scrub_slider = None
            self._scrub_start_val = float(target.value())
            target._cutora_text_editing = False
            if immediate:
                QApplication.setOverrideCursor(Qt.CursorShape.SizeHorCursor)
                ov = _ensure_spin_overlay(target)
                if ov is not None:
                    ov.set_state(True, True)
        elif isinstance(target, QSlider):
            self._scrub_spin = None
            self._scrub_slider = target
            self._scrub_start_val = float(target.value())
            if immediate:
                QApplication.setOverrideCursor(Qt.CursorShape.SizeHorCursor)

    def _apply_scrub_delta(self, dx: int, mods) -> None:
        if dx == 0:
            return
        mult = 1.0
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mult = 10.0
        elif mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            mult = 0.1

        self._scrub_accum_px += float(dx)
        steps_f = self._scrub_accum_px / self.PIXELS_PER_STEP
        if abs(steps_f) < 0.25:
            return

        if self._scrub_spin is not None:
            sp = self._scrub_spin
            base_step = float(sp.singleStep() or 1.0)
            if isinstance(sp, QDoubleSpinBox):
                delta_val = steps_f * base_step * mult
                self._scrub_accum_px = 0.0
                new_val = max(float(sp.minimum()), min(float(sp.maximum()), float(sp.value()) + delta_val))
                sp.setValue(new_val)
            else:
                whole_steps = int(steps_f * mult) if mult >= 1.0 else int(steps_f)
                if whole_steps == 0:
                    return
                self._scrub_accum_px -= (whole_steps / max(mult, 0.1)) * self.PIXELS_PER_STEP
                step_i = max(1, int(round(base_step)))
                new_val_i = max(int(sp.minimum()), min(int(sp.maximum()), int(sp.value()) + whole_steps * step_i))
                sp.setValue(new_val_i)
            ov = getattr(sp, "_cutora_scrub_overlay", None)
            if ov is not None:
                ov.update()
        elif self._scrub_slider is not None:
            sl = self._scrub_slider
            whole_steps = int(steps_f * mult) if mult >= 1.0 else int(steps_f)
            if whole_steps == 0:
                return
            self._scrub_accum_px = 0.0
            step_i = max(1, int(sl.singleStep() or 1))
            new_val_i = max(int(sl.minimum()), min(int(sl.maximum()), int(sl.value()) + whole_steps * step_i))
            sl.setValue(new_val_i)

    def _end_scrub(self) -> None:
        if self._scrub_active:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
        if self._scrub_spin is not None:
            ov = getattr(self._scrub_spin, "_cutora_scrub_overlay", None)
            if ov is not None:
                ov.set_state(self._scrub_spin.underMouse(), False)
        self._scrub_spin = None
        self._scrub_slider = None
        self._scrub_target_widget = None
        self._scrub_active = False
        self._scrub_accum_px = 0.0

    @staticmethod
    def _ensure_scrub_tooltip(spin: QAbstractSpinBox) -> None:
        if getattr(spin, "_cutora_tip_enhanced", False):
            return
        spin._cutora_tip_enhanced = True
        base = (spin.toolTip() or "").strip()
        if _SCRUB_HINT_ZH not in base:
            tip = (base + "\n" + _SCRUB_HINT_ZH).strip() if base else _SCRUB_HINT_ZH
            spin.setToolTip(tip)
            le = spin.lineEdit()
            if le is not None:
                le.setToolTip(tip)

    @staticmethod
    def _ensure_label_tooltip(label: QLabel) -> None:
        if getattr(label, "_cutora_tip_enhanced", False):
            return
        label._cutora_tip_enhanced = True
        base = (label.toolTip() or label.text() or "").strip()
        hint = "💡 左右拖拽标签 ↔ 调节参数 · 双击标签重置默认值"
        if hint not in base:
            label.setToolTip((base + "\n" + hint).strip() if base else hint)


_INSTALLED_FILTER: Optional[WheelGuardAndScrubFilter] = None


def install_wheel_guard(target: Optional[QObject] = None) -> WheelGuardAndScrubFilter:
    """Install the global WheelGuardAndScrubFilter on QApplication (or ``target``)."""
    global _INSTALLED_FILTER
    app = target or QApplication.instance()
    if app is None:
        return WheelGuardAndScrubFilter()
    existing = getattr(app, "_cutora_wheel_guard", None)
    if existing is not None:
        return existing
    filt = WheelGuardAndScrubFilter(app)
    app.installEventFilter(filt)
    app._cutora_wheel_guard = filt
    _INSTALLED_FILTER = filt
    return filt
