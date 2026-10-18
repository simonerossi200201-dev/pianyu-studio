# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimized main window as a small always-on-top badge.

``Qt.Tool`` keeps it off the taskbar. A parent would hide it together with
the main window, so this widget is parentless on purpose.

Staying on top loses to another topmost window, exclusive fullscreen, and
UAC prompts. That is a Windows limit; the 5s timer only calls ``raise_()``.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from cutora.core.brand import APP_DISPLAY_NAME
from cutora.core.paths import RESOURCES
from cutora.ui.badge_prefs import (
    SIZE_DEFAULT,
    SIZE_MAX,
    SIZE_MED,
    SIZE_MIN,
    badge_edge_hide,
    badge_layout,
    save_badge_layout,
)
from cutora.ui.i18n import tr
from cutora.ui.theme.tokens import ACCENT, DUR_BASE, SPACE_1, SPACE_2, SPACE_3, SPACE_4

# Spec: click if the pointer moved less than 4px; edge if the centre is
# within 12px; tucked badge leaves 8px on screen; wheel step is 8px.
CLICK_SLOP = SPACE_1
EDGE_TRIGGER = SPACE_3
PEEK = SPACE_2
SIZE_STEP = SPACE_2
LEAVE_MS = 800
TOPMOST_MS = 5000
_EDGES = ("left", "right", "top", "bottom")
_APP_PNG = os.path.join(RESOURCES, "brand", "app.png")


def _clamp_side(side: int) -> int:
    try:
        side = int(side)
    except (TypeError, ValueError):
        side = SIZE_DEFAULT
    if side < SIZE_MIN:
        return SIZE_MIN
    if side > SIZE_MAX:
        return SIZE_MAX
    steps = (side - SIZE_MIN) // SIZE_STEP
    return SIZE_MIN + steps * SIZE_STEP


class FloatingBadge(QWidget):
    restore_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        # Ignore ``parent``. An owned Tool window disappears with its owner.
        del parent
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(APP_DISPLAY_NAME)

        self._pix = QPixmap(_APP_PNG) if os.path.isfile(_APP_PNG) else QPixmap()
        self._size = SIZE_DEFAULT
        self._dock = ""
        self._tucked = False
        self._full_pos = None  # fully visible; None until the user or a screen places it
        self._press_global = None
        self._press_pos = None
        self._moved = False
        self._placed = False

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(DUR_BASE)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(LEAVE_MS)
        self._leave_timer.timeout.connect(self._tuck_again)

        self._top_timer = QTimer(self)
        self._top_timer.setInterval(TOPMOST_MS)
        self._top_timer.timeout.connect(self._reassert_top)

        self._load_layout()
        self.setMouseTracking(True)

    def side(self) -> int:
        return int(self._size)

    def follows_dock(self) -> str:
        return self._dock

    def is_tucked(self) -> bool:
        return bool(self._tucked)

    def _load_layout(self) -> None:
        x, y, size, dock = badge_layout()
        self._size = _clamp_side(size)
        self._dock = dock if dock in _EDGES else ""
        self._full_pos = QPoint(x, y) if x is not None and y is not None else None
        self.setFixedSize(self._size, self._size)

    def _save(self) -> None:
        pos = self._full_pos if self._full_pos is not None else self.pos()
        save_badge_layout(pos.x(), pos.y(), self._size, self._dock)

    def _place_from_saved(self) -> None:
        self._load_layout()
        if self._dock and badge_edge_hide() and self._full_pos is not None:
            anchor = self._full_pos + QPoint(self._size // 2, self._size // 2)
            ag = self._work_area_at(anchor)
            if ag is not None:
                self._tucked = True
                self.move(self._peek_point(ag, self._dock, self._full_pos))
                return
            self._dock = ""
        self._tucked = False
        if self._full_pos is not None:
            self.move(self._full_pos)
        else:
            self._dock = ""
            self.move(self._default_point())
            self._full_pos = QPoint(self.pos())

    def present(self) -> None:
        """Show, clamped onto a screen. Safe to call more than once."""
        self._place_from_saved()
        self._placed = True
        self._ensure_on_screen()
        self.show()
        self.raise_()

    def reset_position(self) -> None:
        self._anim.stop()
        self._leave_timer.stop()
        self._dock = ""
        self._tucked = False
        self._full_pos = self._default_point()
        self.move(self._full_pos)
        self._ensure_on_screen()
        self._full_pos = QPoint(self.pos())
        self._save()

    def on_edge_setting_changed(self) -> None:
        """Feature off: slide is the wrong word �?jump fully back on screen now."""
        if badge_edge_hide():
            return
        self._leave_timer.stop()
        self._anim.stop()
        self._tucked = False
        self._dock = ""
        if self._full_pos is None:
            self._full_pos = self._default_point()
        self.move(self._full_pos)
        if not self._fully_inside():
            self._clamp_fully()
        self._full_pos = QPoint(self.pos())
        self._save()

    def set_side(self, side: int) -> None:
        side = _clamp_side(side)
        if side == self._size and self.width() == side:
            return
        anchor = self._full_pos if self._full_pos is not None else self.pos()
        self._size = side
        self.setFixedSize(side, side)
        if self._dock and badge_edge_hide():
            ag = self._work_area_at(anchor + QPoint(side // 2, side // 2))
            if ag is not None:
                self._full_pos = self._flush_point(ag, self._dock, anchor)
                if self._tucked:
                    self.move(self._peek_point(ag, self._dock, self._full_pos))
                else:
                    self.move(self._full_pos)
        self.update()
        self._save()

    def showEvent(self, event) -> None:
        if not self._placed:
            self._place_from_saved()
            self._placed = True
        self._ensure_on_screen()
        if not self._top_timer.isActive():
            self._top_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._top_timer.stop()
        self._leave_timer.stop()
        self._anim.stop()
        super().hideEvent(event)

    def _reassert_top(self) -> None:
        # raise_ only. activateWindow() would steal focus every tick.
        if self.isVisible():
            self.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self._pix.isNull():
            painter.drawPixmap(self.rect(), self._pix)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(self.rect())
        painter.end()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        steps = int(delta // 120)
        if steps == 0 and delta != 0:
            steps = 1 if delta > 0 else -1
        if steps:
            self.set_side(self._size + steps * SIZE_STEP)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._popup(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._anim.stop()
        self._leave_timer.stop()
        self._press_global = event.globalPosition().toPoint()
        self._press_pos = QPoint(self.pos())
        self._moved = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if delta.manhattanLength() >= CLICK_SLOP:
            self._moved = True
            self._tucked = False
            self.move(self._press_pos + delta)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        dist = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
        self._press_global = None
        if dist < CLICK_SLOP and not self._moved:
            self.restore_requested.emit()
            event.accept()
            return
        self._consider_dock()
        event.accept()

    def enterEvent(self, event) -> None:
        self._leave_timer.stop()
        if self._dock and self._tucked and badge_edge_hide():
            ag = self._work_area()
            if ag is not None and self._full_pos is not None:
                self._tucked = False
                self._slide_to(self._flush_point(ag, self._dock, self._full_pos))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if (
            self._dock
            and not self._tucked
            and badge_edge_hide()
            and self._press_global is None
        ):
            self._leave_timer.start()
        super().leaveEvent(event)

    def _tuck_again(self) -> None:
        if not self._dock or not badge_edge_hide() or self._tucked:
            return
        if self.underMouse():
            return
        ag = self._work_area()
        if ag is None or self._full_pos is None:
            return
        self._tucked = True
        self._slide_to(self._peek_point(ag, self._dock, self._full_pos))

    def _popup(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        small = menu.addAction(tr("小"))
        mid = menu.addAction(tr("中"))
        large = menu.addAction(tr("大"))
        menu.addSeparator()
        reset = menu.addAction(tr("重置位置"))
        menu.addSeparator()
        quit_act = menu.addAction(tr("退出%s" % APP_DISPLAY_NAME))
        chosen = menu.exec(global_pos)
        if chosen is small:
            self.set_side(SIZE_MIN)
        elif chosen is mid:
            self.set_side(SIZE_MED)
        elif chosen is large:
            self.set_side(SIZE_MAX)
        elif chosen is reset:
            self.reset_position()
        elif chosen is quit_act:
            self.quit_requested.emit()

    def _consider_dock(self) -> None:
        if not badge_edge_hide():
            self._dock = ""
            self._tucked = False
            self._full_pos = QPoint(self.pos())
            self._save()
            return
        ag = self._work_area()
        if ag is None:
            self._full_pos = QPoint(self.pos())
            self._save()
            return
        center = self.geometry().center()
        edges = (
            ("left", abs(center.x() - ag.left())),
            ("right", abs(center.x() - (ag.left() + ag.width()))),
            ("top", abs(center.y() - ag.top())),
            ("bottom", abs(center.y() - (ag.top() + ag.height()))),
        )
        name, dist = min(edges, key=lambda item: item[1])
        if dist < EDGE_TRIGGER:
            self._dock = name
            self._full_pos = self._flush_point(ag, name, self.pos())
            self._tucked = True
            self._slide_to(self._peek_point(ag, name, self._full_pos))
        else:
            self._dock = ""
            self._tucked = False
            self._full_pos = QPoint(self.pos())
        self._save()

    def _slide_to(self, point: QPoint) -> None:
        self._anim.stop()
        if self.pos() == point:
            self.move(point)
            return
        self._anim.setStartValue(QPoint(self.pos()))
        self._anim.setEndValue(QPoint(point))
        self._anim.start()

    def _flush_point(self, ag, edge: str, anchor: QPoint) -> QPoint:
        w, h = self.width(), self.height()
        x, y = anchor.x(), anchor.y()
        if edge in ("left", "right"):
            y = min(max(y, ag.top()), max(ag.top(), ag.top() + ag.height() - h))
            x = ag.left() if edge == "left" else ag.left() + ag.width() - w
        else:
            x = min(max(x, ag.left()), max(ag.left(), ag.left() + ag.width() - w))
            y = ag.top() if edge == "top" else ag.top() + ag.height() - h
        return QPoint(int(x), int(y))

    def _peek_point(self, ag, edge: str, full: QPoint) -> QPoint:
        w, h = self.width(), self.height()
        if edge == "left":
            return QPoint(ag.left() - w + PEEK, full.y())
        if edge == "right":
            return QPoint(ag.left() + ag.width() - PEEK, full.y())
        if edge == "top":
            return QPoint(full.x(), ag.top() - h + PEEK)
        return QPoint(full.x(), ag.top() + ag.height() - PEEK)

    def _screen_geoms(self):
        app = QApplication.instance()
        if app is None:
            return []
        out = []
        for screen in app.screens():
            if screen is not None:
                out.append(screen.availableGeometry())
        return out

    def _work_area(self):
        return self._work_area_at(self.geometry().center())

    def _work_area_at(self, center: QPoint):
        app = QApplication.instance()
        if app is None:
            return None
        screens = [s for s in app.screens() if s is not None]
        if not screens:
            return None
        for screen in screens:
            if screen.geometry().contains(center) or screen.availableGeometry().contains(center):
                return screen.availableGeometry()
        def _dist(screen):
            c = screen.availableGeometry().center()
            return abs(c.x() - center.x()) + abs(c.y() - center.y())
        return min(screens, key=_dist).availableGeometry()

    def _intersects_any(self) -> bool:
        rect = self.frameGeometry()
        for geom in self._screen_geoms():
            inter = geom.intersected(rect)
            if inter.width() > 0 and inter.height() > 0:
                return True
        return False

    def _fully_inside(self) -> bool:
        rect = self.frameGeometry()
        for geom in self._screen_geoms():
            if geom.contains(rect):
                return True
        return False

    def _ensure_on_screen(self) -> None:
        """A tucked sliver may sit mostly off-screen. A lost icon may not."""
        if self._intersects_any():
            return
        self._clamp_fully()
        if not self._tucked:
            self._full_pos = QPoint(self.pos())
            self._dock = ""
            self._save()

    def _clamp_fully(self) -> None:
        geoms = self._screen_geoms()
        if not geoms:
            return
        rect = self.frameGeometry()
        best = None
        best_area = -1
        for geom in geoms:
            inter = geom.intersected(rect)
            area = max(0, inter.width()) * max(0, inter.height())
            if area > best_area:
                best_area = area
                best = geom
        if best is None or best_area <= 0:
            primary = QApplication.primaryScreen()
            best = primary.availableGeometry() if primary is not None else geoms[0]
        self._move_inside(best)

    def _move_inside(self, ag) -> None:
        w, h = self.width(), self.height()
        if w >= ag.width():
            x = ag.left()
        else:
            x = min(max(self.x(), ag.left()), ag.left() + ag.width() - w)
        if h >= ag.height():
            y = ag.top()
        else:
            y = min(max(self.y(), ag.top()), ag.top() + ag.height() - h)
        self.move(int(x), int(y))

    def _default_point(self) -> QPoint:
        geoms = self._screen_geoms()
        if not geoms:
            return QPoint(SPACE_4, SPACE_4)
        primary = QApplication.primaryScreen()
        ag = primary.availableGeometry() if primary is not None else geoms[0]
        x = ag.left() + ag.width() - self.width() - SPACE_4
        y = ag.top() + ag.height() - self.height() - SPACE_4
        return QPoint(max(ag.left(), int(x)), max(ag.top(), int(y)))
