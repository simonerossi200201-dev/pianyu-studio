# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""节点图的图元：NodeItem（卡片 + 端口）、EdgeItem（贝塞尔连线）、WireItem（拖线中的临时线）。

尺寸 / 颜色一律取 theme tokens。端口颜色：画面 = 金色（accent），遮罩 = 蓝（info）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem

from cutora.fx import graph as G
from cutora.ui.theme.tokens import (
    FONT_LABEL, FONT_MICRO, RADIUS_CARD, SPACE_1, SPACE_1H, SPACE_2, SPACE_5, SPACE_6, current,
)

NODE_W = SPACE_6 * 5            # 160
HEADER_H = SPACE_5 + SPACE_1    # 28
ROW_H = SPACE_5                 # 24
PORT_R = SPACE_1H               # 6
GRID = SPACE_2                  # 8：拖完吸到网格

CATEGORY_TINT = {"抠像": "accent", "遮罩": "info", "画面": "ok", "io": "fg2"}


def tok(name: str, alpha: float = 1.0) -> QColor:
    c = QColor(getattr(current(), name, "#888888"))
    if alpha < 1.0:
        c.setAlphaF(alpha)
    return c


def port_color(kind) -> QColor:
    return tok("accent") if kind == G.IMAGE else tok("info")


class NodeItem(QGraphicsItem):
    """一个节点（含隐式的 src / out）。端口：左边输入一行一个，右边一个输出（在标题行）。"""

    def __init__(self, nid: str, ntype: str, title: str, *, label: str = "", color: str = "",
                 enabled: bool = True, viewer: bool = False):
        super().__init__()
        self.nid = nid
        self.ntype = ntype
        self.title = title
        self.label = label
        self.color = color
        self.enabled = enabled
        self.viewer = viewer
        self.inputs = [(p[0], p[1], p[3]) for p in G.inputs_of(ntype)]
        self.out_kind = G.output_of(ntype)
        self.hover_port = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.edges = []

    # ---- 几何 ----
    def height(self) -> float:
        return HEADER_H + max(1, len(self.inputs)) * ROW_H + SPACE_1

    def card(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self.height())

    def boundingRect(self) -> QRectF:
        return self.card().adjusted(-PORT_R - 2, -2, PORT_R + 2, 2)

    def in_port_pos(self, port: str) -> QPointF:
        for i, (name, _k, _l) in enumerate(self.inputs):
            if name == port:
                return self.mapToScene(QPointF(0, HEADER_H + i * ROW_H + ROW_H / 2.0))
        return self.mapToScene(QPointF(0, HEADER_H / 2.0))

    def out_port_pos(self) -> QPointF:
        return self.mapToScene(QPointF(NODE_W, HEADER_H / 2.0))

    def port_at(self, scene_pt: QPointF, slop: float = PORT_R * 2.2):
        """(\"in\", 端口名, 类型) / (\"out\", None, 类型) / None。"""
        if self.out_kind is not None:
            p = self.out_port_pos()
            if (p - scene_pt).manhattanLength() <= slop * 1.4:
                return ("out", None, self.out_kind)
        for name, kind, _l in self.inputs:
            p = self.in_port_pos(name)
            if (p - scene_pt).manhattanLength() <= slop * 1.4:
                return ("in", name, kind)
        return None

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for e in self.edges:
                e.refresh()
        return super().itemChange(change, value)

    def hoverMoveEvent(self, event):
        hp = self.port_at(event.scenePos())
        if hp != self.hover_port:
            self.hover_port = hp
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.hover_port = None
        self.update()
        super().hoverLeaveEvent(event)

    # ---- 画 ----
    def _tint(self) -> QColor:
        if self.color:
            return QColor(self.color)
        if self.ntype in (G.SRC, G.OUT):
            return tok("fg2")
        cat = (G.node_type(self.ntype) or {}).get("category", "")
        return tok(CATEGORY_TINT.get(cat, "fg2"))

    def paint(self, p: QPainter, _option, _widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.card()
        sel = self.isSelected()
        body = tok("bg2")
        p.setPen(QPen(tok("accent") if sel else tok("border"), 2.0 if sel else 1.0))
        p.setBrush(QBrush(body))
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        # 标题条
        head = QRectF(r.left(), r.top(), r.width(), HEADER_H)
        tint = self._tint()
        hc = QColor(tint)
        hc.setAlphaF(0.30 if self.enabled else 0.12)
        # 上面两个角圆、下边平：圆角矩形往下多画一个圆角高度，再裁到标题条
        path = QPainterPath()
        path.addRoundedRect(head.adjusted(0, 0, 0, RADIUS_CARD * 2), RADIUS_CARD, RADIUS_CARD)
        box = QPainterPath()
        box.addRect(head)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hc)
        p.drawPath(path.intersected(box))
        p.fillRect(QRectF(head.left() + 1, head.bottom() - 1, head.width() - 2, 1), tok("line"))
        # 色条
        p.fillRect(QRectF(head.left() + SPACE_1, head.top() + SPACE_1H, 3, HEADER_H - 2 * SPACE_1H), tint)
        f = QFont(p.font())
        f.setPixelSize(FONT_LABEL)
        f.setBold(True)
        p.setFont(f)
        p.setPen(tok("fg0") if self.enabled else tok("fg_muted"))
        text = self.label or self.title
        fm = p.fontMetrics()
        p.drawText(QRectF(head.left() + SPACE_2 + 2, head.top(), head.width() - SPACE_6, head.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(head.width() - SPACE_6)))
        # 查看器眼睛 / 旁路标记
        if self.viewer:                       # 查看器：标题条右侧一个「眼睛」（环 + 瞳孔）
            c = QPointF(head.right() - SPACE_5, head.center().y())
            p.setPen(QPen(tok("accent_hi"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 6, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tok("accent_hi"))
            p.drawEllipse(c, 2, 2)
        if not self.enabled:
            p.setPen(QPen(tok("warn"), 1.5))
            p.drawLine(QPointF(r.left() + SPACE_2, r.bottom() - SPACE_2), QPointF(r.right() - SPACE_2, r.top() + HEADER_H + SPACE_1))
        # 端口
        f2 = QFont(p.font())
        f2.setPixelSize(FONT_MICRO)
        f2.setBold(False)
        p.setFont(f2)
        for i, (name, kind, lbl) in enumerate(self.inputs):
            y = HEADER_H + i * ROW_H + ROW_H / 2.0
            hov = self.hover_port is not None and self.hover_port[0] == "in" and self.hover_port[1] == name
            self._port(p, QPointF(0, y), port_color(kind), hov)
            p.setPen(tok("fg1"))
            p.drawText(QRectF(PORT_R + SPACE_1H, y - ROW_H / 2.0, NODE_W - SPACE_5, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, lbl)
        if not self.inputs and self.ntype != G.SRC:
            p.setPen(tok("fg_muted"))
            p.drawText(QRectF(SPACE_2, HEADER_H, NODE_W - SPACE_5, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       "输出遮罩" if self.out_kind == G.MATTE else "")
        if self.ntype == G.SRC:
            p.setPen(tok("fg_muted"))
            p.drawText(QRectF(SPACE_2, HEADER_H, NODE_W - SPACE_5, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "这一层的画面")
        if self.out_kind is not None:
            hov = self.hover_port is not None and self.hover_port[0] == "out"
            self._port(p, QPointF(NODE_W, HEADER_H / 2.0), port_color(self.out_kind), hov)

    @staticmethod
    def _port(p: QPainter, c: QPointF, col: QColor, hover: bool):
        p.setPen(QPen(tok("bg0"), 1.5))
        p.setBrush(col)
        rr = PORT_R + (2 if hover else 0)
        p.drawEllipse(c, rr, rr)


def wire_path(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(SPACE_6 * 1.5, abs(b.x() - a.x()) * 0.5)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


class EdgeItem(QGraphicsPathItem):
    """上游节点输出 → 下游节点某个输入。"""

    def __init__(self, src: NodeItem, dst: NodeItem, port: str):
        super().__init__()
        self.src, self.dst, self.port = src, dst, port
        kind = dict((n, k) for n, k, _l in dst.inputs).get(port, G.IMAGE)
        self.kind = kind
        self.setZValue(1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._hover = False
        src.edges.append(self)
        dst.edges.append(self)
        self.refresh()

    def key(self):
        return (self.src.nid, self.dst.nid, self.port)

    def refresh(self):
        self.setPath(wire_path(self.src.out_port_pos(), self.dst.in_port_pos(self.port)))

    def shape(self):
        from PySide6.QtGui import QPainterPathStroker
        st = QPainterPathStroker()
        st.setWidth(SPACE_2 + 2)
        return st.createStroke(self.path())

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def paint(self, p: QPainter, _o, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = port_color(self.kind)
        if not self.src.enabled:
            col.setAlphaF(0.5)
        width = 3.0 if (self.isSelected() or self._hover) else 2.0
        glow = QColor(col)
        glow.setAlphaF(0.18)
        p.setPen(QPen(glow, width + 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())
        p.setPen(QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())


class WireItem(QGraphicsPathItem):
    """拖线时跟着鼠标的线。"""

    def __init__(self, anchor: QPointF, kind, from_output: bool):
        super().__init__()
        self.anchor = QPointF(anchor)
        self.from_output = from_output
        col = port_color(kind)
        self.setPen(QPen(col, 2.0, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
        self.setZValue(3)
        self.update_to(anchor)

    def update_to(self, pt: QPointF):
        a, b = (self.anchor, pt) if self.from_output else (pt, self.anchor)
        self.setPath(wire_path(a, b))
