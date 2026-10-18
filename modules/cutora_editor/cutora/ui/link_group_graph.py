# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 连线导入画布（第二代）：左边媒体库的视频，右边外部片段**一行一组**，中间拉线。

右边不再是一列，而是一行一行的「预合成组」：一行里的几段按先后拼成一段，连给左边一条媒体。
分组怎么来：工具条上「每组片段数」一改就按顺序重排；勾「按文件名前缀分组」就按前缀；
之后随便手调 —— 拖一段 / 框选几段，丢到同一行的别处（换位）、别的行（追加 / 插入）、
最下面的空白处（另起一组）。右键还有「合并成一组 / 各自单独成组」。

连线：从媒体卡片右边的端口拖到一行上（行的任何位置都行），或者从行左边的端口拖到媒体卡片上。
一条媒体可以连几组（几条新轨道）；一组也可以连给几条媒体。右键媒体 / 片段可以批量连。
按住 Ctrl 划一刀割线；双击一条线 / 选中按 Delete 删掉。

这个文件不认识业务：它只管「左边若干项、右边若干行、若干条线」。分组的纯逻辑在
`cutora/batch/link_groups.py`，导入在 `cutora/batch/link_import.py`。
颜色 / 间距一律取 `cutora.ui.theme.tokens`；每行的颜色和导入后时间线上那个预合成组同一个。
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QMenu, QScrollBar,
)

from cutora.batch import link_groups as LG
from cutora.model.clip_groups import GROUP_COLORS
from cutora.ui.link_graph import _c, material_tint
from cutora.ui.theme.tokens import (
    FONT_LABEL, FONT_MICRO, RADIUS_CARD, RADIUS_SM, SPACE_1, SPACE_1H, SPACE_2, SPACE_3, SPACE_4, SPACE_5, SPACE_6,
)

# 三档尺寸：片段缩略图宽 × 高（16:9），媒体卡片宽
SIZES = {
    "large": {"thumb": (SPACE_6 * 6, SPACE_6 * 6 * 9 // 16), "media_w": SPACE_6 * 8, "label": True},
    "medium": {"thumb": (SPACE_6 * 4, SPACE_6 * 4 * 9 // 16), "media_w": SPACE_6 * 7, "label": True},
    "small": {"thumb": (SPACE_6 * 5 // 2, SPACE_6 * 5 // 2 * 9 // 16), "media_w": SPACE_6 * 6, "label": False},
}
LABEL_H = SPACE_4 + SPACE_1            # 20  片段文件名那一行
CELL_PAD = SPACE_1                     # 片段四周留白
CELL_GAP = SPACE_2                     # 片段之间
ROW_HDR_H = SPACE_5                    # 24  行标题
ROW_PAD = SPACE_2
ROW_GAP = SPACE_3
MARGIN = SPACE_4
COL_GAP = SPACE_6 * 5                  # 160  左右两列之间给连线
PORT_R = SPACE_1H                      # 6
HIT_PAD = SPACE_2
DRAG_THRESHOLD = SPACE_1H


def _exec_menu(menu, pos):
    """弹菜单（测试里换掉，不阻塞）。"""
    return menu.exec(pos)


def row_color(index: int) -> QColor:
    return QColor(GROUP_COLORS[int(index) % len(GROUP_COLORS)])


def _fit_thumb(p: QPainter, pix: QPixmap | None, rect: QRectF, radius: float, tint: QColor):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    if pix is not None and not pix.isNull():
        scaled = pix.scaled(int(rect.width()), int(rect.height()), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(int(rect.x() + (rect.width() - scaled.width()) / 2),
                     int(rect.y() + (rect.height() - scaled.height()) / 2), scaled)
    else:
        p.fillRect(rect, _c("bg2"))
        t = QColor(tint)
        t.setAlphaF(0.25)
        p.fillRect(rect, t)
    p.restore()


# ───────────────────────────── 图元 ─────────────────────────────

class MediaCard(QGraphicsItem):
    """左边：媒体库里的一条视频。端口在右边中点。"""

    def __init__(self, index: int, row: dict, size: str):
        super().__init__()
        self.index = int(index)
        self.title = str(row.get("title") or "")
        self.subtitle = str(row.get("subtitle") or "")
        self.thumb = row.get("thumb")
        self.key_path = str(row.get("key_path") or "")
        self.tint = row.get("tint") or material_tint(index + 6)
        self.size = size
        self.selected_ = False
        self.magnet = False
        self.linked = 0
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setZValue(2)

    def width(self) -> float:
        return float(SIZES[self.size]["media_w"])

    def height(self) -> float:
        return float(SIZES[self.size]["thumb"][1] + 2 * CELL_PAD)

    def card_rect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def boundingRect(self) -> QRectF:
        return QRectF(-2.0, -2.0, self.width() + PORT_R + 4.0, self.height() + 4.0)

    def port_pos(self) -> QPointF:
        return self.mapToScene(QPointF(self.width(), self.height() / 2.0))

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def paint(self, p: QPainter, _o, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.card_rect()
        p.setPen(QPen(_c("accent") if self.selected_ else (_c("fg_muted") if self._hover else _c("border")),
                      2.0 if self.selected_ else 1.0))
        p.setBrush(QBrush(_c("bg1")))
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        th_w, th_h = SIZES[self.size]["thumb"]
        tw = th_h * 16 / 9.0
        p.fillRect(QRectF(r.left() + 1, r.top() + SPACE_1, 3, r.height() - 2 * SPACE_1), self.tint)
        trect = QRectF(CELL_PAD + 4, CELL_PAD, min(tw, r.width() * 0.5), th_h)
        _fit_thumb(p, self.thumb, trect, RADIUS_SM, self.tint)
        x = trect.right() + SPACE_2
        f = QFont(p.font())
        f.setPixelSize(FONT_LABEL)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_c("fg0"))
        fm = p.fontMetrics()
        avail = int(r.width() - x - SPACE_2)
        p.drawText(QRectF(x, CELL_PAD, avail, fm.height() + 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideMiddle, avail))
        f.setBold(False)
        f.setPixelSize(FONT_MICRO)
        p.setFont(f)
        p.setPen(_c("fg_muted"))
        sub = self.subtitle + ("   · 已连 %d 组" % self.linked if self.linked else "")
        p.drawText(QRectF(x, CELL_PAD + fm.height() + 2, avail, p.fontMetrics().height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, avail))
        # 端口
        c = QPointF(r.right(), r.center().y())
        p.setPen(QPen(_c("bg0"), 1.5))
        p.setBrush(_c("accent_hi") if self.magnet else self.tint)
        rr = PORT_R + (2 if (self.magnet or self._hover) else 0)
        p.drawEllipse(c, rr, rr)


class CellItem(QGraphicsItem):
    """右边：一段外部片段。父项是它所在的行。"""

    def __init__(self, src: int, row: dict, size: str):
        super().__init__()
        self.src = int(src)
        self.title = str(row.get("title") or "")
        self.thumb = row.get("thumb")
        self.key_path = str(row.get("key_path") or "")
        self.size = size
        self.color = QColor(GROUP_COLORS[0])
        self.selected_ = False
        self.ghost = False
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

    def width(self) -> float:
        return float(SIZES[self.size]["thumb"][0] + 2 * CELL_PAD)

    def height(self) -> float:
        h = SIZES[self.size]["thumb"][1] + 2 * CELL_PAD
        if SIZES[self.size]["label"]:
            h += LABEL_H
        return float(h)

    def boundingRect(self) -> QRectF:
        return QRectF(-1, -1, self.width() + 2, self.height() + 2)

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def paint(self, p: QPainter, _o, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0, 0, self.width(), self.height())
        if self.ghost:
            p.setOpacity(0.35)
        p.setPen(QPen(_c("accent") if self.selected_ else (_c("fg_muted") if self._hover else _c("line")),
                      2.0 if self.selected_ else 1.0))
        p.setBrush(QBrush(_c("bg2") if not self.selected_ else _c("bg3")))
        p.drawRoundedRect(r, RADIUS_SM, RADIUS_SM)
        tw, th = SIZES[self.size]["thumb"]
        trect = QRectF(CELL_PAD, CELL_PAD, tw, th)
        _fit_thumb(p, self.thumb, trect, RADIUS_SM - 1, self.color)
        # 左上角序号角标（组内第几段）
        order = getattr(self, "order", None)
        if order is not None:
            f = QFont(p.font())
            f.setPixelSize(FONT_MICRO)
            f.setBold(True)
            p.setFont(f)
            badge = QRectF(trect.left() + 3, trect.top() + 3, SPACE_4 + 2, SPACE_4 - 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color)
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(_c("bg0"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(int(order) + 1))
        if SIZES[self.size]["label"]:
            f = QFont(p.font())
            f.setPixelSize(FONT_MICRO)
            f.setBold(False)
            p.setFont(f)
            p.setPen(_c("fg1"))
            fm = p.fontMetrics()
            p.drawText(QRectF(CELL_PAD, trect.bottom() + 1, tw, LABEL_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.title, Qt.TextElideMode.ElideMiddle, int(tw)))
        if self.ghost:
            p.setOpacity(1.0)


class RowItem(QGraphicsItem):
    """右边的一行 = 一个预合成组：标题条 + 横着一排片段。端口在左边中点。"""

    def __init__(self, index: int, label: str, size: str):
        super().__init__()
        self.index = int(index)
        self.label = str(label or "")
        self.size = size
        self.color = row_color(index)
        self.cells: list[CellItem] = []
        self.magnet = False
        self.linked = 0
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setZValue(1)

    def add_cell(self, cell: CellItem):
        cell.setParentItem(self)
        cell.color = self.color
        cell.order = len(self.cells)
        self.cells.append(cell)
        self.relayout()

    def relayout(self):
        x = ROW_PAD
        for c in self.cells:
            c.setPos(x, ROW_HDR_H + ROW_PAD)
            x += c.width() + CELL_GAP
        self.prepareGeometryChange()

    def width(self) -> float:
        if not self.cells:
            return float(SIZES[self.size]["thumb"][0] + 2 * ROW_PAD + 2 * CELL_PAD)
        return float(ROW_PAD * 2 + sum(c.width() for c in self.cells) + CELL_GAP * (len(self.cells) - 1))

    def height(self) -> float:
        ch = self.cells[0].height() if self.cells else SIZES[self.size]["thumb"][1] + 2 * CELL_PAD
        return float(ROW_HDR_H + ROW_PAD * 2 + ch)

    def rect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def boundingRect(self) -> QRectF:
        return QRectF(-PORT_R - 2, -2, self.width() + PORT_R + 4, self.height() + 4)

    def port_pos(self) -> QPointF:
        return self.mapToScene(QPointF(0, self.height() / 2.0))

    def header_rect(self) -> QRectF:
        return QRectF(0, 0, self.width(), ROW_HDR_H)

    def insert_pos_at(self, scene_x: float) -> int:
        """鼠标在这一行里的 x → 插入到第几段之前（0 … len）。"""
        lx = self.mapFromScene(QPointF(scene_x, 0)).x()
        for i, c in enumerate(self.cells):
            if lx < c.pos().x() + c.width() / 2.0:
                return i
        return len(self.cells)

    def caret_x(self, pos: int) -> float:
        """第 pos 段之前的插入线（本地 x）。"""
        if not self.cells:
            return ROW_PAD
        if pos >= len(self.cells):
            c = self.cells[-1]
            return c.pos().x() + c.width() + CELL_GAP / 2.0
        return self.cells[pos].pos().x() - CELL_GAP / 2.0

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def paint(self, p: QPainter, _o, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        fill = QColor(self.color)
        fill.setAlphaF(0.10 if not self.magnet else 0.22)
        p.setPen(QPen(self.color if (self.magnet or self._hover) else _c("border"), 2.0 if self.magnet else 1.0))
        p.setBrush(QBrush(_c("bg1")))
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        # 标题条
        hdr = self.header_rect()
        hc = QColor(self.color)
        hc.setAlphaF(0.28)
        path = QPainterPath()
        path.addRoundedRect(hdr.adjusted(0, 0, 0, RADIUS_CARD * 2), RADIUS_CARD, RADIUS_CARD)
        box = QPainterPath()
        box.addRect(hdr)
        p.setBrush(hc)
        p.drawPath(path.intersected(box))
        p.fillRect(QRectF(hdr.left() + SPACE_1, hdr.top() + SPACE_1, 3, hdr.height() - 2 * SPACE_1), self.color)
        f = QFont(p.font())
        f.setPixelSize(FONT_LABEL)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_c("fg0"))
        text = "%s  ·  %d 段" % (self.label, len(self.cells))
        if self.linked:
            text += "  ·  已连 %d 条" % self.linked
        fm = p.fontMetrics()
        p.drawText(hdr.adjusted(SPACE_2 + 4, 0, -SPACE_2, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(hdr.width() - SPACE_4)))
        # 端口
        c = QPointF(0, r.center().y())
        p.setPen(QPen(_c("bg0"), 1.5))
        p.setBrush(_c("accent_hi") if self.magnet else self.color)
        rr = PORT_R + (2 if (self.magnet or self._hover) else 0)
        p.drawEllipse(c, rr, rr)


def _wire(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(float(SPACE_6), abs(b.x() - a.x()) * 0.5)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


class LinkItem(QGraphicsPathItem):
    def __init__(self, media: MediaCard, row: RowItem):
        super().__init__()
        self.media, self.row = media, row
        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._hover = False
        self.refresh()

    def key(self) -> tuple[int, int]:
        return (self.media.index, self.row.index)

    def refresh(self):
        self.setPath(_wire(self.media.port_pos(), self.row.port_pos()))

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(HIT_PAD + 2)
        return st.createStroke(self.path())

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def paint(self, p: QPainter, _o, _w=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(self.row.color)
        w = 3.0 if (self.isSelected() or self._hover) else 2.0
        glow = QColor(col)
        glow.setAlphaF(0.18)
        p.setPen(QPen(glow, w + 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())
        if self.isSelected():
            col = _c("accent_hi")
        p.setPen(QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())


# ───────────────────────────── 画布 ─────────────────────────────

class GroupLinkView(QGraphicsView):
    """业务只需要 set_items / set_rows / rows / links / set_links。其余是手势。"""

    linksChanged = Signal()
    rowsChanged = Signal()
    selectionChanged = Signal()
    cellDoubleClicked = Signal(int)

    MIN_Z, MAX_Z = 0.5, 1.6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("groupLinkGraph")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        bg = _c("bg0")
        self.setBackgroundBrush(QBrush(bg))
        self._scene.setBackgroundBrush(QBrush(bg))
        self._size = "medium"
        self._left_rows: list[dict] = []
        self._src_rows: list[dict] = []
        self._rows: list[list[int]] = []
        self._link_keys: list[tuple[int, int]] = []
        self._media: list[MediaCard] = []
        self._row_items: list[RowItem] = []
        self._cells: dict[int, CellItem] = {}
        self._links: list[LinkItem] = []
        self._sel_cells: list[int] = []
        self._sel_media: list[int] = []
        self._anchor_cell = None
        self._anchor_media = None
        # 手势
        self._press: QPointF | None = None
        self._press_cell: CellItem | None = None
        self._press_media: MediaCard | None = None
        self._press_ctrl = False
        self._wire_from = None          # ("media", MediaCard) / ("row", RowItem)
        self._wire_item: QGraphicsPathItem | None = None
        self._drag_cells: list[CellItem] = []
        self._drop = None               # (row_index | len(rows), pos)
        self._caret: QGraphicsRectItem | None = None
        self._band: QGraphicsRectItem | None = None
        self._band_additive = False
        self._knife: list = []
        self._knife_item: QGraphicsPathItem | None = None
        # 左右两列各自的纵向偏移（场景单位）：整页滚动照旧是视图自己的滚动条，
        # 这两根细滚动条 / Alt+滚轮 只挪那一列，方便左右对照
        self._off = {"left": 0.0, "right": 0.0}
        self._col_h = {"left": 0.0, "right": 0.0}
        self._sb = {"left": QScrollBar(Qt.Orientation.Vertical, self),
                    "right": QScrollBar(Qt.Orientation.Vertical, self)}
        for side, sb in self._sb.items():
            sb.setObjectName("groupLinkColScroll")
            sb.setToolTip("只滚%s这一列（鼠标放在这一列上 Alt + 滚轮也行）。整页滚动用右边的大滚动条 / 普通滚轮。"
                          % ("左边" if side == "left" else "右边"))
            sb.setVisible(False)
            sb.valueChanged.connect(lambda v, sd=side: self.set_column_offset(sd, float(v)))

    # ─────────── 单列滚动 ───────────
    def column_offset(self, side: str) -> float:
        return float(self._off.get(side, 0.0))

    def set_column_offset(self, side: str, value: float) -> None:
        """把左 / 右那一列单独往上挪 value 个场景单位（0 = 顶部对齐）。"""
        side = "left" if side == "left" else "right"
        value = max(0.0, min(float(value), self._col_max(side)))
        if abs(value - self._off[side]) < 0.5:
            return
        self._off[side] = value
        self._place_columns()
        sb = self._sb[side]
        if sb.value() != int(round(value)):
            sb.blockSignals(True)
            sb.setValue(int(round(value)))
            sb.blockSignals(False)
        self.viewport().update()

    def _col_max(self, side: str) -> float:
        """这一列最多能单独往上挪多少：列高减去视口高（场景单位）。"""
        vh = self.viewport().height() / max(0.3, self.transform().m11())
        return max(0.0, self._col_h.get(side, 0.0) + MARGIN - vh)

    def _place_columns(self) -> None:
        y = MARGIN - self._off["left"]
        for m in self._media:
            m.setPos(MARGIN, y)
            y += m.height() + SPACE_2
        y = MARGIN - self._off["right"]
        for r in self._row_items:
            r.setPos(r.pos().x(), y)
            y += r.height() + ROW_GAP
        for lk in self._links:
            lk.refresh()

    def _place_col_bars(self) -> None:
        """两根细滚动条贴在视口里：左边一根贴左缘，右边一根贴右缘（主滚动条的里侧）。"""
        vp = self.viewport()
        w = SPACE_2 + 2
        h = vp.height()
        for side, sb in self._sb.items():
            mx = self._col_max(side)
            if mx <= 1.0:
                sb.setVisible(False)
                if self._off[side]:
                    self._off[side] = 0.0
                    self._place_columns()
                continue
            sb.blockSignals(True)
            sb.setRange(0, int(math.ceil(mx)))
            sb.setPageStep(max(1, int(h / max(0.3, self.transform().m11()))))
            sb.setSingleStep(SPACE_6)
            sb.setValue(int(round(min(self._off[side], mx))))
            sb.blockSignals(False)
            x = 0 if side == "left" else vp.width() - w
            sb.setGeometry(x + vp.x(), vp.y(), w, h)
            sb.setVisible(True)
            sb.raise_()

    def _side_at_x(self, scene_x: float) -> str:
        left_w = max([m.width() for m in self._media] or [SIZES[self._size]["media_w"]])
        return "left" if scene_x < MARGIN + left_w + COL_GAP / 2.0 else "right"

    # ─────────── 数据 ───────────
    def set_items(self, left_rows, src_rows, rows=None) -> None:
        self._left_rows = list(left_rows or [])
        self._src_rows = list(src_rows or [])
        n = len(self._src_rows)
        self._rows = LG.normalize(rows if rows is not None else self._rows, n)
        self._link_keys = [(li, ri) for li, ri in self._link_keys
                           if 0 <= li < len(self._left_rows) and 0 <= ri < len(self._rows)]
        self._sel_cells = [i for i in self._sel_cells if 0 <= i < n]
        self._sel_media = [i for i in self._sel_media if 0 <= i < len(self._left_rows)]
        self._rebuild()

    def set_rows(self, rows, *, keep_links: bool = True) -> None:
        old = list(self._rows)
        self._rows = LG.normalize(rows, len(self._src_rows))
        if keep_links:
            self._link_keys = LG.remap_links(self._link_keys, old, self._rows)
        else:
            self._link_keys = []
        self._rebuild()
        self.rowsChanged.emit()
        self.linksChanged.emit()

    def rows(self) -> list:
        return [list(r) for r in self._rows]

    def links(self) -> list:
        return list(self._link_keys)

    def set_links(self, pairs) -> None:
        self._link_keys = []
        for li, ri in pairs or []:
            self._add_link(int(li), int(ri), quiet=True)
        self._rebuild_links()
        self.linksChanged.emit()

    def regroup_columns(self, cols: int) -> None:
        self._label_example = ""
        self.set_rows(LG.rows_by_columns(len(self._src_rows), cols))

    def regroup_prefix(self, example: str = "") -> None:
        """按文件名前缀分组。example = 用户给的例子（video01）；空 = 自动按最后一个分隔符。"""
        self._label_example = str(example or "")
        self.set_rows(LG.rows_by_pattern([r.get("key_path") or r.get("title") for r in self._src_rows], example))

    def node_size(self) -> str:
        return self._size

    def set_node_size(self, size: str) -> None:
        if size in SIZES and size != self._size:
            self._size = size
            self._rebuild()

    def set_thumb(self, key_path: str, pix) -> int:
        n = 0
        for m in self._media:
            if m.key_path == key_path:
                m.thumb = pix
                m.update()
                n += 1
        for c in self._cells.values():
            if c.key_path == key_path:
                c.thumb = pix
                c.update()
                n += 1
        for row in self._left_rows:
            if row.get("key_path") == key_path:
                row["thumb"] = pix
        for row in self._src_rows:
            if row.get("key_path") == key_path:
                row["thumb"] = pix
        return n

    def selected_cells(self) -> list:
        return list(self._sel_cells)

    def selected_media(self) -> list:
        return list(self._sel_media)

    def row_label(self, ri: int) -> str:
        paths = [r.get("key_path") or r.get("title") for r in self._src_rows]
        return LG.group_label(paths, self._rows[ri], ri, getattr(self, "_label_example", "")) if 0 <= ri < len(self._rows) else ""

    def row_colors(self) -> list:
        return [row_color(i).name() for i in range(len(self._rows))]

    # ─────────── 搭建 ───────────
    def _rebuild(self) -> None:
        self._scene.clear()
        self._media, self._row_items, self._cells, self._links = [], [], {}, []
        self._wire_item = self._caret = self._band = self._knife_item = None
        size = self._size
        y = MARGIN
        for i, row in enumerate(self._left_rows):
            m = MediaCard(i, row, size)
            m.setPos(MARGIN, y)
            m.selected_ = i in self._sel_media
            self._scene.addItem(m)
            self._media.append(m)
            y += m.height() + SPACE_2
        self._col_h["left"] = y
        left_w = max([m.width() for m in self._media] or [SIZES[size]["media_w"]])
        rx = MARGIN + left_w + COL_GAP
        y = MARGIN
        for ri, row in enumerate(self._rows):
            item = RowItem(ri, self.row_label(ri), size)
            item.setPos(rx, y)
            self._scene.addItem(item)
            for src in row:
                if 0 <= src < len(self._src_rows):
                    cell = CellItem(src, self._src_rows[src], size)
                    cell.selected_ = src in self._sel_cells
                    item.add_cell(cell)
                    self._cells[src] = cell
            self._row_items.append(item)
            y += item.height() + ROW_GAP
        # 最下面那块「另起一组」的空白也算进右列高度
        self._col_h["right"] = y + (self._row_items[0].height() if self._row_items else SPACE_6 * 3) + ROW_GAP
        for side in ("left", "right"):
            self._off[side] = min(self._off[side], self._col_max(side))
        self._rebuild_links()
        self._place_columns()
        self._fit_scene_rect()

    def _rebuild_links(self) -> None:
        for lk in self._links:
            if lk.scene() is self._scene:
                self._scene.removeItem(lk)
        self._links = []
        for m in self._media:
            m.linked = 0
        for r in self._row_items:
            r.linked = 0
        for li, ri in self._link_keys:
            if 0 <= li < len(self._media) and 0 <= ri < len(self._row_items):
                lk = LinkItem(self._media[li], self._row_items[ri])
                self._scene.addItem(lk)
                self._links.append(lk)
                self._media[li].linked += 1
                self._row_items[ri].linked += 1
        for it in list(self._media) + list(self._row_items):
            it.update()

    def _fit_scene_rect(self) -> None:
        # 场景高 = 两列里高的那一列（不随单列偏移变：某一列单独滚上去，整页范围不跟着缩）
        right_edge = max([r.pos().x() + r.width() for r in self._row_items]
                         + [m.pos().x() + m.width() + COL_GAP for m in self._media] + [400.0])
        h = max(self._col_h["left"], self._col_h["right"], SPACE_6 * 4) + MARGIN
        self._scene.setSceneRect(QRectF(0, 0, right_edge + MARGIN, h))
        self._place_col_bars()

    def new_row_zone(self) -> QRectF:
        """最后一行下面那块「丢这里另起一组」的区域（场景坐标）。"""
        if not self._row_items:
            left_w = max([m.width() for m in self._media] or [SIZES[self._size]["media_w"]])
            return QRectF(MARGIN + left_w + COL_GAP, MARGIN, SPACE_6 * 8, SPACE_6 * 3)
        last = self._row_items[-1]
        top = last.pos().y() + last.height() + ROW_GAP / 2.0
        return QRectF(last.pos().x(), top, max(last.width(), SPACE_6 * 8), last.height())

    # ─────────── 连线 ───────────
    def _add_link(self, li: int, ri: int, quiet: bool = False) -> bool:
        if not (0 <= li < len(self._left_rows)) or not (0 <= ri < len(self._rows)):
            return False
        if (li, ri) in self._link_keys:
            return False
        self._link_keys.append((li, ri))
        if not quiet:
            self._rebuild_links()
            self.linksChanged.emit()
        return True

    def add_link(self, li: int, ri: int) -> bool:
        return self._add_link(li, ri)

    def add_links(self, pairs) -> int:
        n = sum(1 for li, ri in pairs or [] if self._add_link(int(li), int(ri), quiet=True))
        if n:
            self._rebuild_links()
            self.linksChanged.emit()
        return n

    def remove_link(self, li: int, ri: int) -> bool:
        if (li, ri) not in self._link_keys:
            return False
        self._link_keys.remove((li, ri))
        self._rebuild_links()
        self.linksChanged.emit()
        return True

    def clear_links(self) -> None:
        if not self._link_keys:
            return
        self._link_keys = []
        self._rebuild_links()
        self.linksChanged.emit()

    def auto_link(self) -> int:
        pairs = LG.auto_links(len(self._left_rows), len(self._rows))
        if not pairs:
            return 0
        self._link_keys = []
        self.add_links(pairs)
        return len(pairs)

    def remove_selected(self) -> int:
        keys = [lk.key() for lk in self._links if lk.isSelected()]
        for k in keys:
            if k in self._link_keys:
                self._link_keys.remove(k)
        if keys:
            self._rebuild_links()
            self.linksChanged.emit()
        return len(keys)

    def connect_media_to_rows(self, li: int, row_indices) -> int:
        return self.add_links([(li, ri) for ri in row_indices])

    def connect_rows_to_media(self, row_indices, media_indices) -> int:
        return self.add_links([(li, ri) for li in media_indices for ri in row_indices])

    # ─────────── 分组编辑 ───────────
    def rows_of_cells(self, srcs) -> list:
        out = []
        for s in srcs:
            hit = LG.row_of(self._rows, s)
            if hit is not None and hit[0] not in out:
                out.append(hit[0])
        return out

    def move_cells(self, srcs, dest_row, dest_pos) -> None:
        srcs = [int(s) for s in srcs]
        if not srcs:
            return
        self.set_rows(LG.move(self._rows, srcs, dest_row, dest_pos))
        self._select_cells(srcs)

    def merge_selected(self) -> None:
        if len(self._sel_cells) >= 2:
            self.set_rows(LG.merge(self._rows, self._sel_cells))

    def split_selected(self) -> None:
        if self._sel_cells:
            self.set_rows(LG.split_out(self._rows, self._sel_cells))

    def new_row_from_selected(self) -> None:
        if self._sel_cells:
            self.move_cells(self._sel_cells, len(self._rows), 0)

    # ─────────── 选择 ───────────
    def _select_cells(self, srcs, *, emit: bool = True) -> None:
        self._sel_cells = [int(s) for s in srcs if int(s) in self._cells]
        for s, c in self._cells.items():
            c.selected_ = s in self._sel_cells
            c.update()
        if emit:
            self.selectionChanged.emit()

    def _select_media(self, idxs, *, emit: bool = True) -> None:
        self._sel_media = [int(i) for i in idxs if 0 <= int(i) < len(self._media)]
        for i, m in enumerate(self._media):
            m.selected_ = i in self._sel_media
            m.update()
        if emit:
            self.selectionChanged.emit()

    def select_cells(self, srcs) -> None:
        self._select_cells(srcs)

    def select_media(self, idxs) -> None:
        self._select_media(idxs)

    def select_row(self, ri: int, additive: bool = False) -> None:
        if not (0 <= ri < len(self._rows)):
            return
        want = list(self._sel_cells) if additive else []
        want += [s for s in self._rows[ri] if s not in want]
        self._select_cells(want)

    def clear_selection(self) -> None:
        self._select_cells([], emit=False)
        self._select_media([], emit=False)
        for lk in self._links:
            lk.setSelected(False)
        self.selectionChanged.emit()

    def _click_cell(self, cell: CellItem, ctrl: bool, shift: bool) -> None:
        src = cell.src
        if shift and self._anchor_cell is not None:
            a = LG.row_of(self._rows, self._anchor_cell)
            b = LG.row_of(self._rows, src)
            if a and b:
                order = [s for row in self._rows for s in row]
                i, j = order.index(self._anchor_cell), order.index(src)
                lo, hi = min(i, j), max(i, j)
                self._select_cells(order[lo:hi + 1])
                return
        if ctrl:
            sel = list(self._sel_cells)
            if src in sel:
                sel.remove(src)
            else:
                sel.append(src)
            self._select_cells(sel)
        elif src not in self._sel_cells:
            self._select_cells([src])
        self._anchor_cell = src
        if not ctrl:
            self._select_media([], emit=False)

    def _click_media(self, m: MediaCard, ctrl: bool, shift: bool) -> None:
        i = m.index
        if shift and self._anchor_media is not None:
            lo, hi = min(self._anchor_media, i), max(self._anchor_media, i)
            self._select_media(list(range(lo, hi + 1)))
            return
        if ctrl:
            sel = list(self._sel_media)
            sel.remove(i) if i in sel else sel.append(i)
            self._select_media(sel)
        else:
            self._select_media([i])
            self._select_cells([], emit=False)
        self._anchor_media = i

    # ─────────── 命中 ───────────
    def _hit(self, scene_pos: QPointF):
        """→ ("cell", CellItem) / ("row", RowItem) / ("media", MediaCard) / ("link", LinkItem) / None。"""
        for it in self._scene.items(scene_pos):
            if isinstance(it, CellItem):
                return "cell", it
            if isinstance(it, RowItem):
                return "row", it
            if isinstance(it, MediaCard):
                return "media", it
            if isinstance(it, LinkItem):
                return "link", it
        return None

    def _port_hit(self, scene_pos: QPointF, snap: float | None = None):
        snap = float(snap if snap is not None else PORT_R * 2.5)
        best, best_d = None, snap
        for m in self._media:
            d = (m.port_pos() - scene_pos).manhattanLength()
            if d <= best_d:
                best, best_d = ("media", m), d
        for r in self._row_items:
            d = (r.port_pos() - scene_pos).manhattanLength()
            if d <= best_d:
                best, best_d = ("row", r), d
        return best

    def _row_at(self, scene_pos: QPointF):
        for r in self._row_items:
            if r.sceneBoundingRect().adjusted(-PORT_R, -ROW_GAP / 2, 0, ROW_GAP / 2).contains(scene_pos):
                return r
        return None

    def _media_at(self, scene_pos: QPointF):
        for m in self._media:
            if m.sceneBoundingRect().contains(scene_pos):
                return m
        return None

    # ─────────── 鼠标 ───────────
    def mousePressEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        self._press = pos
        self._press_cell = self._press_media = None
        self._press_ctrl = ctrl or shift          # 加减选 / 连选：松手不收成单选
        port = self._port_hit(pos)
        if port is not None:
            self._wire_from = port
            kind, it = port
            self._wire_item = QGraphicsPathItem()
            col = it.tint if kind == "media" else it.color
            self._wire_item.setPen(QPen(col, 2.0, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
            self._wire_item.setZValue(5)
            self._scene.addItem(self._wire_item)
            self._update_wire(pos)
            e.accept()
            return
        hit = self._hit(pos)
        if hit is not None and hit[0] == "cell":
            self._click_cell(hit[1], ctrl, shift)
            self._press_cell = hit[1]
            e.accept()
            return
        if hit is not None and hit[0] == "row":
            self.select_row(hit[1].index, additive=ctrl)
            if not ctrl:
                self._select_media([], emit=False)
            self._press_cell = hit[1].cells[0] if hit[1].cells else None
            e.accept()
            return
        if hit is not None and hit[0] == "media":
            self._click_media(hit[1], ctrl, shift)
            self._press_media = hit[1]
            e.accept()
            return
        if hit is not None and hit[0] == "link":
            for lk in self._links:
                lk.setSelected(lk is hit[1] if not ctrl else (lk.isSelected() ^ (lk is hit[1])))
            e.accept()
            return
        if ctrl:
            self._knife = [pos]
            self._knife_item = QGraphicsPathItem()
            self._knife_item.setPen(QPen(_c("err_text"), 2.0, Qt.PenStyle.DashLine))
            self._knife_item.setZValue(6)
            self._scene.addItem(self._knife_item)
            e.accept()
            return
        # 空白：框选（Shift = 追加）
        self._band_additive = shift
        if not shift:
            self.clear_selection()
        self._band = QGraphicsRectItem(QRectF(pos, pos))
        self._band.setPen(QPen(_c("accent"), 1.0, Qt.PenStyle.DashLine))
        fill = _c("accent")
        fill.setAlphaF(0.12)
        self._band.setBrush(QBrush(fill))
        self._band.setZValue(6)
        self._scene.addItem(self._band)
        e.accept()

    def mouseMoveEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        if self._wire_from is not None:
            self._update_wire(pos)
            e.accept()
            return
        if self._knife_item is not None:
            self._knife.append(pos)
            path = QPainterPath(self._knife[0])
            for p in self._knife[1:]:
                path.lineTo(p)
            self._knife_item.setPath(path)
            e.accept()
            return
        if self._band is not None:
            self._band.setRect(QRectF(self._press, pos).normalized())
            e.accept()
            return
        if self._press_cell is not None and self._press is not None:
            if not self._drag_cells and (pos - self._press).manhattanLength() >= DRAG_THRESHOLD:
                srcs = self._sel_cells if self._press_cell.src in self._sel_cells else [self._press_cell.src]
                self._drag_cells = [self._cells[s] for s in srcs if s in self._cells]
                for c in self._drag_cells:
                    c.ghost = True
                    c.update()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._drag_cells:
                self._update_drop(pos)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        if self._wire_from is not None:
            self._end_wire(pos)
            e.accept()
            return
        if self._knife_item is not None:
            self._scene.removeItem(self._knife_item)
            self._knife_item = None
            self._cut_with_knife()
            self._knife = []
            e.accept()
            return
        if self._band is not None:
            rect = self._band.rect()
            self._scene.removeItem(self._band)
            self._band = None
            self._apply_band(rect)
            e.accept()
            return
        if self._drag_cells:
            drop = self._drop
            srcs = [c.src for c in self._drag_cells]
            for c in self._drag_cells:
                c.ghost = False
                c.update()
            self._drag_cells = []
            self._clear_caret()
            self.unsetCursor()
            if drop is not None:
                self.move_cells(srcs, drop[0], drop[1])
            e.accept()
            self._press_cell = None
            return
        if self._press_cell is not None and not self._press_ctrl and e.button() == Qt.MouseButton.LeftButton:
            # 点了多选里的一段没拖：收成单选（和时间线一样）
            if len(self._sel_cells) > 1 and self._press_cell.src in self._sel_cells and self._hit(pos) and self._hit(pos)[0] == "cell":
                self._select_cells([self._press_cell.src])
        self._press_cell = self._press_media = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        hit = self._hit(pos)
        if hit is not None and hit[0] == "link":
            self.remove_link(*hit[1].key())
            e.accept()
            return
        if hit is not None and hit[0] == "cell":
            self.cellDoubleClicked.emit(int(hit[1].src))
            e.accept()
            return
        if hit is not None and hit[0] == "row" and hit[1].cells:
            self.cellDoubleClicked.emit(int(hit[1].cells[0].src))
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                f = 1.1 if d > 0 else 1 / 1.1
                z = max(self.MIN_Z, min(self.MAX_Z, self.transform().m11() * f))
                cur = self.transform().m11()
                self.scale(z / cur, z / cur)
                self._place_col_bars()
            e.accept()
            return
        if e.modifiers() & Qt.KeyboardModifier.AltModifier:
            # Alt + 滚轮：只滚鼠标所在的那一列（Alt 按下时 Qt 把竖向滚轮报在 x 上）
            d = e.angleDelta().y() or e.angleDelta().x()
            if d:
                side = self._side_at_x(self.mapToScene(e.position().toPoint()).x())
                self.set_column_offset(side, self._off[side] - d / 120.0 * SPACE_6 * 2)
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.remove_selected():
                e.accept()
                return
        if e.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            e.accept()
            return
        if e.key() == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._select_cells(list(self._cells))
            e.accept()
            return
        super().keyPressEvent(e)

    # ─────────── 拖线 ───────────
    def _update_wire(self, pos: QPointF) -> None:
        kind, it = self._wire_from
        a = it.port_pos()
        target = self._port_hit(pos, snap=SPACE_6)
        for x in list(self._media) + list(self._row_items):
            x.magnet = False
        if kind == "media":
            row = self._row_at(pos)
            if row is not None:
                row.magnet = True
                pos = row.port_pos()
            elif target is not None and target[0] == "row":
                target[1].magnet = True
                pos = target[1].port_pos()
            self._wire_item.setPath(_wire(a, pos))
        else:
            m = self._media_at(pos)
            if m is not None:
                m.magnet = True
                pos = m.port_pos()
            elif target is not None and target[0] == "media":
                target[1].magnet = True
                pos = target[1].port_pos()
            self._wire_item.setPath(_wire(pos, a))
        for x in list(self._media) + list(self._row_items):
            x.update()

    def _end_wire(self, pos: QPointF) -> None:
        kind, it = self._wire_from
        self._wire_from = None
        if self._wire_item is not None:
            self._scene.removeItem(self._wire_item)
            self._wire_item = None
        for x in list(self._media) + list(self._row_items):
            x.magnet = False
            x.update()
        target = self._port_hit(pos, snap=SPACE_6)
        if kind == "media":
            row = self._row_at(pos) or (target[1] if target is not None and target[0] == "row" else None)
            if row is not None:
                self._add_link(it.index, row.index)
        else:
            m = self._media_at(pos) or (target[1] if target is not None and target[0] == "media" else None)
            if m is not None:
                self._add_link(m.index, it.index)

    def _cut_with_knife(self) -> int:
        if len(self._knife) < 2:
            return 0
        path = QPainterPath(self._knife[0])
        for p in self._knife[1:]:
            path.lineTo(p)
        st = QPainterPathStroker()
        st.setWidth(HIT_PAD)
        blade = st.createStroke(path)
        gone = [lk.key() for lk in self._links if lk.shape().intersects(blade)]
        for k in gone:
            if k in self._link_keys:
                self._link_keys.remove(k)
        if gone:
            self._rebuild_links()
            self.linksChanged.emit()
        return len(gone)

    # ─────────── 框选 ───────────
    def _apply_band(self, rect: QRectF) -> None:
        if rect.width() < 2 and rect.height() < 2:
            return
        cells = [s for s, c in self._cells.items() if c.sceneBoundingRect().intersects(rect)]
        medias = [m.index for m in self._media if m.sceneBoundingRect().intersects(rect)]
        if self._band_additive:
            cells = list(self._sel_cells) + [s for s in cells if s not in self._sel_cells]
            medias = list(self._sel_media) + [i for i in medias if i not in self._sel_media]
        self._select_cells(cells, emit=False)
        self._select_media(medias)

    # ─────────── 拖片段 ───────────
    def _update_drop(self, pos: QPointF) -> None:
        row = self._row_at(pos)
        if row is not None:
            p = row.insert_pos_at(pos.x())
            self._drop = (row.index, p)
            x = row.caret_x(p)
            top = ROW_HDR_H + ROW_PAD / 2.0
            r = QRectF(row.pos().x() + x - 1.5, row.pos().y() + top, 3.0, row.height() - top - ROW_PAD / 2.0)
            self._show_caret(r, row.color)
            return
        zone = self.new_row_zone()
        if zone.contains(pos) or pos.y() > zone.top():
            self._drop = (len(self._rows), 0)
            self._show_caret(zone, row_color(len(self._rows)), outline=True)
            return
        self._drop = None
        self._clear_caret()

    def _show_caret(self, rect: QRectF, color: QColor, outline: bool = False) -> None:
        if self._caret is None:
            self._caret = QGraphicsRectItem()
            self._caret.setZValue(7)
            self._scene.addItem(self._caret)
        self._caret.setRect(rect)
        if outline:
            self._caret.setPen(QPen(color, 2.0, Qt.PenStyle.DashLine))
            fill = QColor(color)
            fill.setAlphaF(0.10)
            self._caret.setBrush(QBrush(fill))
        else:
            self._caret.setPen(Qt.PenStyle.NoPen)
            self._caret.setBrush(QBrush(color))
        self._caret.show()

    def _clear_caret(self) -> None:
        if self._caret is not None:
            self._scene.removeItem(self._caret)
            self._caret = None
        self._drop = None

    # ─────────── 右键 ───────────
    def contextMenuEvent(self, e):
        pos = self.mapToScene(e.pos())
        hit = self._hit(pos)
        menu = QMenu(self)
        if hit is not None and hit[0] in ("cell", "row"):
            if hit[0] == "cell":
                if hit[1].src not in self._sel_cells:
                    self._select_cells([hit[1].src])
            elif hit[1].cells and not any(c.src in self._sel_cells for c in hit[1].cells):
                self.select_row(hit[1].index)
            rows = self.rows_of_cells(self._sel_cells)
            n = len(self._sel_cells)
            a = menu.addAction("合并成一组（选中的 %d 段）" % n)
            a.setEnabled(n >= 2)
            a.triggered.connect(self.merge_selected)
            a = menu.addAction("各自单独成组")
            a.setEnabled(n >= 1 and any(len(self._rows[r]) > 1 for r in rows))
            a.triggered.connect(self.split_selected)
            a = menu.addAction("另起一组（选中的这几段）")
            a.setEnabled(n >= 1 and (len(rows) > 1 or any(len(self._rows[r]) > n for r in rows)))
            a.triggered.connect(self.new_row_from_selected)
            menu.addSeparator()
            sub = menu.addMenu("把这 %d 组连到…" % len(rows))
            for m in self._media:
                act = sub.addAction(m.title)
                act.triggered.connect(lambda _c=False, li=m.index, rs=tuple(rows): self.connect_rows_to_media(rs, [li]))
            if self._sel_media:
                a = menu.addAction("连到左边选中的 %d 条媒体" % len(self._sel_media))
                a.triggered.connect(lambda: self.connect_rows_to_media(rows, self._sel_media))
            a = menu.addAction("断开这 %d 组的连线" % len(rows))
            a.setEnabled(any(ri in rows for _li, ri in self._link_keys))
            a.triggered.connect(lambda: self._drop_links(rows=rows))
        elif hit is not None and hit[0] == "media":
            if hit[1].index not in self._sel_media:
                self._select_media([hit[1].index])
            medias = list(self._sel_media)
            rows = self.rows_of_cells(self._sel_cells)
            if rows:
                a = menu.addAction("把右边选中的 %d 组连到这 %d 条" % (len(rows), len(medias)))
                a.triggered.connect(lambda: self.connect_rows_to_media(rows, medias))
            sub = menu.addMenu("连到组…")
            for ri in range(len(self._rows)):
                act = sub.addAction("%s · %d 段" % (self.row_label(ri), len(self._rows[ri])))
                act.triggered.connect(lambda _c=False, r=ri, ms=tuple(medias): self.connect_rows_to_media([r], ms))
            a = menu.addAction("按顺序连：这 %d 条 → 从第一组起各连一组" % len(medias))
            a.triggered.connect(lambda: self.add_links(list(zip(medias, range(len(self._rows))))))
            a = menu.addAction("断开这 %d 条的连线" % len(medias))
            a.setEnabled(any(li in medias for li, _ri in self._link_keys))
            a.triggered.connect(lambda: self._drop_links(medias=medias))
        elif hit is not None and hit[0] == "link":
            a = menu.addAction("删掉这条线")
            a.triggered.connect(lambda: self.remove_link(*hit[1].key()))
        else:
            a = menu.addAction("自动连线（按上下顺序一一对应）")
            a.triggered.connect(self.auto_link)
            a = menu.addAction("清空连线")
            a.triggered.connect(self.clear_links)
            menu.addSeparator()
            a = menu.addAction("全选片段\tCtrl+A")
            a.triggered.connect(lambda: self._select_cells(list(self._cells)))
        self._last_menu = menu
        _exec_menu(menu, e.globalPos())

    def _drop_links(self, rows=None, medias=None) -> None:
        before = len(self._link_keys)
        self._link_keys = [(li, ri) for li, ri in self._link_keys
                           if not ((rows is not None and ri in rows) or (medias is not None and li in medias))]
        if len(self._link_keys) != before:
            self._rebuild_links()
            self.linksChanged.emit()

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:
        """最后一行下面常驻一块虚线框：「拖到这里另起一组」。拖的时候由插入标记接管。"""
        super().drawForeground(p, rect)
        if not self._src_rows or (self._drag_cells and self._drop is not None and self._drop[0] == len(self._rows)):
            return
        zone = self.new_row_zone()
        if not zone.intersects(rect):
            return
        p.save()
        col = _c("fg_muted")
        col.setAlphaF(0.6)
        p.setPen(QPen(col, 1.0, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(zone, RADIUS_CARD, RADIUS_CARD)
        f = QFont(p.font())
        f.setPixelSize(FONT_LABEL)
        p.setFont(f)
        p.setPen(col)
        p.drawText(zone, Qt.AlignmentFlag.AlignCenter, "把片段拖到这里 = 另起一组")
        p.restore()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_scene_rect()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        for sb in self._sb.values():
            sb.raise_()
