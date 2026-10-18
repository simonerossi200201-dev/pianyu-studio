# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""节点画布：点状网格背景、滚轮缩放（以光标为中心）、中键 / 空格拖平移、左键空白处框选。

手势（拖线、加节点、删除…）由 NodeEditor 处理；这里只管看。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from cutora.ui.nodes.items import GRID, tok
from cutora.ui.theme.tokens import SPACE_5


class NodeScene(QGraphicsScene):
    def drawBackground(self, p: QPainter, rect: QRectF):
        p.fillRect(rect, tok("bg0"))
        step = GRID * 3
        left = int(rect.left()) - (int(rect.left()) % step)
        top = int(rect.top()) - (int(rect.top()) % step)
        p.setPen(QPen(tok("line"), 1.0))
        pts = []
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                pts.append(QPointF(x, y))
                y += step
            x += step
            if len(pts) > 20000:
                break
        p.drawPoints(pts)


class NodeView(QGraphicsView):
    zoom_changed = Signal(float)

    MIN_Z, MAX_Z = 0.3, 2.5

    def __init__(self, scene: NodeScene, parent=None):
        super().__init__(scene, parent)
        self.setObjectName("nodeView")
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSceneRect(QRectF(-20000, -20000, 40000, 40000))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pan = None
        self._space = False

    def zoom(self) -> float:
        return float(self.transform().m11())

    def set_zoom(self, z: float):
        z = max(self.MIN_Z, min(self.MAX_Z, float(z)))
        cur = self.zoom()
        if cur > 0:
            self.scale(z / cur, z / cur)
        self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if not d:
            return
        f = 1.15 if d > 0 else 1 / 1.15
        self.set_zoom(self.zoom() * f)

    def fit_items(self, rect: QRectF | None = None):
        r = rect or self.scene().itemsBoundingRect()
        if r.isEmpty():
            return
        r = r.adjusted(-SPACE_5 * 2, -SPACE_5 * 2, SPACE_5 * 2, SPACE_5 * 2)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom() > 1.25:
            self.set_zoom(1.0)
        elif self.zoom() < self.MIN_Z:
            self.set_zoom(self.MIN_Z)
        self.centerOn(r.center())
        self.zoom_changed.emit(self.zoom())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton or (self._space and e.button() == Qt.MouseButton.LeftButton):
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat() and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.unsetCursor()
        super().keyReleaseEvent(e)
