# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""节点连线画布：左右两列卡片，中间拉贝塞尔曲线把它们连起来。

用途见 `cutora/ui/link_import_dialog.py` —— 左列是媒体库里的视频，
右列是要导入的外部视频，一条连线就是「把右边这条加到左边那条的时间线上」。

这个文件**不认识任何业务概念**：它只知道「左边若干项、右边若干项、若干条
连线」。业务放在 `cutora/batch/link_import.py`，这样连线画布可以单独测。

`set_poster(True)` 换成海报卡片：上面整张缩略图、下面一行文件名。
默认仍是视频连线导入那套紧凑卡片，两种高度互不影响。

颜色和间距一律取 `cutora.ui.theme.tokens`，不写裸像素、不写裸色值。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QScrollBar,
)

from cutora.ui.theme.tokens import (
    FONT_LABEL, FONT_SECTION, RADIUS_CARD, RADIUS_SM, SPACE_1, SPACE_1H,
    SPACE_2, SPACE_4, SPACE_5, SPACE_6, current,
)

# 画布尺寸：都由既有 SPACE 拼出来，不引入新的裸数字。
# 卡片宽度**按文字实测**，只在 MIN/MAX 之间取 —— 名字短就窄，不硬撑成一大块。
NODE_MIN_W = SPACE_6 * 5      # 160 再短的名字也不低于这个，端口才好点
NODE_MAX_W = SPACE_6 * 10     # 320 再长的名字就省略号，不让卡片吃掉连线区
NODE_W = NODE_MIN_W           # 兼容旧名字：默认宽度
NODE_H_CARD = SPACE_6 * 2     # 64  大图模式行高（缩略图 16:9 正好放得下）
NODE_H_LIST = SPACE_5 + SPACE_2   # 32  列表模式行高
NODE_GAP = SPACE_2            # 卡片间距
COL_GAP = SPACE_6 * 6         # 192 两列之间留给连线的最小宽度
MARGIN = SPACE_4
PORT_R = SPACE_1H            # 6 端口半径
THUMB_W = SPACE_6 * 3         # 96  16:9 在 56px 高度下的宽
HIT_PAD = SPACE_2             # 连线的点击容差
# 吸附半径的上限。实际半径按行距再收一收，免得列表模式下吸到隔壁那张。
PORT_SNAP_MAX = float(SPACE_6 + SPACE_4)   # 48
PORT_SNAP_MIN = float(SPACE_4)             # 16
# 海报模式：缩略图占满卡片上半，文件名单独一行。视频连线导入不走这条，
# 所以那边的行高、卡片宽度都不变。
NODE_H_POSTER = SPACE_6 * 5   # 160
POSTER_LABEL_H = SPACE_5      # 24  文件名这一行
POSTER_W = SPACE_6 * 12       # 384  短文件名也不缩成一条，图才看得清
POSTER_THUMB_W = POSTER_W
POSTER_THUMB_H = SPACE_6 * 4  # 128  解码尺寸，画上去再适配


def _c(name: str, alpha: float = 1.0) -> QColor:
    tok = current()
    col = QColor(getattr(tok, name, "#888888"))
    if alpha < 1.0:
        col.setAlphaF(alpha)
    return col


def material_tint(index: int) -> QColor:
    """第 index 个素材的识别色。从主题强调色转开，相邻两张不会撞色。"""
    base = _c("accent")
    hue, sat, val, _alpha = base.getHsv()
    if hue < 0:
        hue = 32
    hue = (int(hue) + (int(index) % 12) * 30) % 360
    sat = 200 if sat < 80 else min(255, max(160, int(sat)))
    val = 230 if val < 100 else min(250, max(180, int(val)))
    return QColor.fromHsv(hue, sat, val, 255)


class NodeItem(QGraphicsItem):
    """一张卡片。左列端口在右边，右列端口在左边。"""

    def __init__(self, side: str, index: int, title: str,
                 subtitle: str = "", thumb: QPixmap | None = None,
                 key_path: str = ""):
        super().__init__()
        self.side = side
        self.key_path = str(key_path or "")
        self.index = int(index)
        self.title = str(title or "")
        self.subtitle = str(subtitle or "")
        self.thumb = thumb
        self.compact = False
        self.poster = False     # 大缩略图 + 文件名一行
        self.chosen = False     # 这一列里当前点中的那张
        self.selected = False   # 多选 / 框选里的一张
        self.tint = None        # 这张素材自己的颜色；没有就跟主题走
        self.width = float(NODE_W)   # 由 view 按可用宽度分配
        self.linked = 0          # 连了几条
        self.magnet = False     # 拖线时，松手会吸到这张
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setZValue(2)

    # ---- 几何 ----
    def height(self) -> float:
        if self.compact:
            return float(NODE_H_LIST)
        if self.poster:
            return float(NODE_H_POSTER)
        return float(NODE_H_CARD)

    def card_rect(self) -> QRectF:
        """卡片本体。端口画在边缘上，一半伸到卡片外面，不记在这里。"""
        return QRectF(0, 0, self.width, self.height())

    def boundingRect(self) -> QRectF:
        # 端口圆心贴着边缘，圆有一半在卡片外。不把这半圆算进范围的话，
        # 点可见的端口点不中，抗锯齿也会被裁掉一条白边。
        pad = float(PORT_R) + 2.0
        if self.side == "left":
            return QRectF(-2.0, -2.0, self.width + pad + 2.0, self.height() + 4.0)
        return QRectF(-pad, -2.0, self.width + pad + 2.0, self.height() + 4.0)

    def port_pos(self) -> QPointF:
        y = self.height() / 2.0
        x = self.width if self.side == "left" else 0.0
        return self.mapToScene(QPointF(x, y))

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    # ---- 画 ----
    def paint(self, p: QPainter, _option, _widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.card_rect().adjusted(0.5, 0.5, -0.5, -0.5)
        if self.selected or self.chosen:
            border, width = _c("accent_hi"), 2.0
        elif self.tint is not None:
            border, width = self.tint, 2.0
        elif self.linked:
            border, width = _c("accent", 0.85), 2.0
        elif self._hover:
            border, width = _c("fg0", 0.35), 1.0
        else:
            border, width = _c("fg0", 0.16), 1.0
        p.setBrush(QBrush(_c("bg2")))
        p.setPen(QPen(border, width))
        p.drawRoundedRect(r, RADIUS_CARD, RADIUS_CARD)
        if self.tint is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self.tint))
            if self.side == "right":
                bar = QRectF(r.right() - SPACE_1H, r.top() + SPACE_1,
                             SPACE_1H, max(1.0, r.height() - SPACE_2))
            else:
                bar = QRectF(r.left(), r.top() + SPACE_1,
                             SPACE_1H, max(1.0, r.height() - SPACE_2))
            p.drawRoundedRect(bar, RADIUS_SM, RADIUS_SM)

        inner = r.adjusted(SPACE_2, SPACE_1, -SPACE_2, -SPACE_1)
        font = p.font()
        font.setPixelSize(FONT_LABEL)
        p.setFont(font)

        if self.poster and not self.compact:
            self._paint_poster(p, inner)
        elif self.compact or self.thumb is None or self.thumb.isNull():
            p.setPen(QPen(_c("fg0")))
            text_rect = inner
            if not self.compact:
                text_rect = QRectF(inner.left(), inner.top(),
                                   inner.width(), inner.height())
            p.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._elided(p, self.title, text_rect.width()))
        else:
            th = QRectF(inner.left(), inner.top(), THUMB_W, inner.height())
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(th, RADIUS_SM, RADIUS_SM)
            p.setClipPath(clip)
            scaled = self.thumb.scaled(
                int(th.width()), int(th.height()),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(
                int(th.left() - max(0, (scaled.width() - th.width()) / 2)),
                int(th.top() - max(0, (scaled.height() - th.height()) / 2)),
                scaled)
            p.restore()
            tx = QRectF(th.right() + SPACE_2, inner.top(),
                        inner.right() - th.right() - SPACE_2, inner.height())
            p.setPen(QPen(_c("fg0")))
            p.drawText(
                QRectF(tx.left(), tx.top(), tx.width(), tx.height() / 2),
                int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft),
                self._elided(p, self.title, tx.width()))
            if self.subtitle:
                p.setPen(QPen(_c("fg2")))
                p.drawText(
                    QRectF(tx.left(), tx.center().y(), tx.width(), tx.height() / 2),
                    int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
                    self._elided(p, self.subtitle, tx.width()))

        # 端口
        pos = QPointF(self.width if self.side == "left" else 0.0,
                      self.height() / 2.0)
        p.setPen(QPen(_c("bg0"), 2.0))
        if self.magnet:
            port = self.tint if self.tint is not None else _c("accent_hi")
            radius = float(PORT_R) + float(SPACE_1)
        elif self.tint is not None:
            port = self.tint
            radius = float(PORT_R)
        elif self.linked:
            port = _c("accent")
            radius = float(PORT_R)
        else:
            port = _c("fg0", 0.35)
            radius = float(PORT_R)
        p.setBrush(QBrush(port))
        p.drawEllipse(pos, radius, radius)

    def _paint_poster(self, p: QPainter, inner: QRectF) -> None:
        """缩略图整张放进上半（不裁切），文件名独占下面一行。"""
        label = QRectF(inner.left(), inner.bottom() - POSTER_LABEL_H,
                       inner.width(), POSTER_LABEL_H)
        well_bottom = label.top() - SPACE_1
        well = QRectF(inner.left(), inner.top(), inner.width(),
                      max(float(SPACE_6), well_bottom - inner.top()))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c("bg0")))
        p.drawRoundedRect(well, RADIUS_SM, RADIUS_SM)
        if self.thumb is not None and not self.thumb.isNull():
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(well, RADIUS_SM, RADIUS_SM)
            p.setClipPath(clip)
            scaled = self.thumb.scaled(
                int(well.width()), int(max(1, well.height())),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(
                int(well.center().x() - scaled.width() / 2),
                int(well.center().y() - scaled.height() / 2),
                scaled)
            p.restore()
        font = p.font()
        font.setPixelSize(FONT_SECTION)
        p.setFont(font)
        p.setPen(QPen(_c("fg0")))
        p.drawText(
            label,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._elided(p, self.title, label.width()))

    def _elided(self, p: QPainter, text: str, width: float) -> str:
        fm = p.fontMetrics()
        return fm.elidedText(text, Qt.TextElideMode.ElideMiddle, int(width))


class LinkItem(QGraphicsPathItem):
    """一条连线。左端口 → 右端口的三次贝塞尔。"""

    def __init__(self, left: NodeItem, right: NodeItem):
        super().__init__()
        self.left = left
        self.right = right
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.refresh()

    def key(self) -> tuple[int, int]:
        return (self.left.index, self.right.index)

    def refresh(self) -> None:
        a = self.left.port_pos()
        b = self.right.port_pos()
        dx = max(SPACE_6, abs(b.x() - a.x()) * 0.45)
        path = QPainterPath(a)
        path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
        self.setPath(path)

    def shape(self):
        # 曲线本身只有两像素宽，不加宽的话根本点不中
        from PySide6.QtGui import QPainterPathStroker
        st = QPainterPathStroker()
        st.setWidth(HIT_PAD * 2)
        return st.createStroke(self.path())

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def color(self) -> QColor:
        """连线跟右边那张素材走。素材没有自己的颜色时，仍用主题强调色。"""
        tint = self.right.tint or self.left.tint
        if tint is None:
            return _c("accent")
        return QColor(tint)

    def paint(self, p: QPainter, _option, _widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        a = self.path().pointAtPercent(0.0)
        b = self.path().pointAtPercent(1.0)
        tint = self.right.tint or self.left.tint
        grad = QLinearGradient(a, b)
        if tint is None:
            if self.isSelected():
                grad.setColorAt(0.0, _c("accent_hi"))
                grad.setColorAt(1.0, _c("accent_hi"))
                width = 3.0
            elif self._hover:
                grad.setColorAt(0.0, _c("accent"))
                grad.setColorAt(1.0, _c("accent_hi"))
                width = 3.0
            else:
                grad.setColorAt(0.0, _c("accent", 0.75))
                grad.setColorAt(1.0, _c("accent_dim", 0.75))
                width = 2.0
        else:
            col = QColor(tint)
            if self.isSelected() or self._hover:
                col = col.lighter(125)
                width = 3.0
            else:
                col.setAlphaF(0.92)
                width = 2.5
            far = QColor(col)
            far.setAlphaF(0.55)
            grad.setColorAt(0.0, far)
            grad.setColorAt(1.0, col)
        pen = QPen(QBrush(grad), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self.path())


class LinkGraphView(QGraphicsView):
    """两列卡片 + 连线。两列各自上下滚动。业务只需要 `set_items` / `links`。"""

    linksChanged = Signal()
    nodeRightClicked = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("linkGraph")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        # 拖贝塞尔时若只重画包围盒，抗锯齿边缘会留下一条条白线，
        # 滚一下才消失 —— 那是视口默认的白底露出来了。底色跟场景对齐。
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, False)
        self._paint_backdrop()
        # 内容比视口矮时顶到上面，别悬在正中间
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._left: list[NodeItem] = []
        self._right: list[NodeItem] = []
        self._links: list[LinkItem] = []
        self._compact = False
        self._poster = False
        self._chosen = {"left": None, "right": None}
        self._anchor = {"left": None, "right": None}
        self._selected = {"left": set(), "right": set()}
        self._scroll = {"left": 0.0, "right": 0.0}
        self._sb_left = QScrollBar(Qt.Orientation.Vertical, self)
        self._sb_right = QScrollBar(Qt.Orientation.Vertical, self)
        self._sb_left.setVisible(False)
        self._sb_right.setVisible(False)
        self._sb_left.valueChanged.connect(lambda v: self._on_scroll("left", v))
        self._sb_right.valueChanged.connect(lambda v: self._on_scroll("right", v))
        self._drag_from: NodeItem | None = None
        self._drag_item: QGraphicsPathItem | None = None
        self._knife: list = []
        self._knife_item: QGraphicsPathItem | None = None
        self._knife_press_node: NodeItem | None = None
        self._band_item: QGraphicsRectItem | None = None
        self._band_origin: QPointF | None = None
        self._band_side = "left"
        self._band_additive = False
        self._wire_drag = False

    def _paint_backdrop(self) -> None:
        """视口底色用主题的暖黑。漏画的一条不会再是纯白。"""
        try:
            bg = _c("bg0")
        except Exception:
            return
        self.setBackgroundBrush(QBrush(bg))
        self._scene.setBackgroundBrush(QBrush(bg))
        pal = self.viewport().palette()
        pal.setColor(QPalette.ColorRole.Window, bg)
        self.viewport().setPalette(pal)
        self.viewport().setAutoFillBackground(True)

    def _arm_wire_repaint(self) -> None:
        """拖线期间整块重画。松手再回到只更新包围盒，平时滚动不加重。"""
        if self._wire_drag:
            return
        self._wire_drag = True
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def _end_wire_repaint(self) -> None:
        self._wire_drag = False
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.viewport().update()

    # ---------------------------------------------------------- 数据
    def set_items(self, left_rows: list[dict], right_rows: list[dict]) -> None:
        """每一项是 {"title":…, "subtitle":…, "thumb": QPixmap|None}。
        换数据会清掉所有连线 —— 连线是按下标存的，换了内容就没有意义了。"""
        self.clear_links()
        self._chosen = {"left": None, "right": None}
        self._anchor = {"left": None, "right": None}
        self._selected = {"left": set(), "right": set()}
        self._scroll = {"left": 0.0, "right": 0.0}
        for node in self._left + self._right:
            self._scene.removeItem(node)
        self._left, self._right = [], []
        for i, row in enumerate(left_rows or []):
            self._left.append(self._make_node("left", i, row))
        for i, row in enumerate(right_rows or []):
            self._right.append(self._make_node("right", i, row))
        self._relayout()

    def _make_node(self, side: str, index: int, row: dict) -> NodeItem:
        node = NodeItem(
            side, index,
            str(row.get("title") or ""),
            str(row.get("subtitle") or ""),
            row.get("thumb"),
            str(row.get("key_path") or ""))
        tint = row.get("tint")
        if tint is not None:
            node.tint = tint
        node.compact = self._compact
        node.poster = self._poster
        tip = str(row.get("tooltip") or row.get("key_path") or "")
        if tip:
            node.setToolTip(tip)
        self._scene.addItem(node)
        return node

    def set_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if compact == self._compact and not (compact and self._poster):
            return
        self._compact = compact
        if compact:
            self._poster = False
        for node in self._left + self._right:
            node.prepareGeometryChange()
            node.compact = compact
            node.poster = self._poster
        self._relayout()

    def node_size(self) -> str:
        """small 列表 / medium 卡片 / large 海报。"""
        if self._compact:
            return "small"
        if self._poster:
            return "large"
        return "medium"

    def set_node_size(self, size: str) -> None:
        """三档高度来回切。两列一起变，连线和选中都留着。"""
        size = str(size or "")
        if size not in ("small", "medium", "large"):
            size = "medium"
        compact = size == "small"
        poster = size == "large"
        if compact == self._compact and poster == self._poster:
            return
        self._compact = compact
        self._poster = poster
        for node in self._left + self._right:
            node.prepareGeometryChange()
            node.compact = compact
            node.poster = poster
        self._relayout()

    def set_poster(self, poster: bool) -> None:
        """大图：每个节点上面是整张缩略图，下面一行文件名。"""
        poster = bool(poster)
        if poster == self._poster and not (poster and self._compact):
            return
        self._poster = poster
        if poster:
            self._compact = False
        for node in self._left + self._right:
            node.prepareGeometryChange()
            node.poster = self._poster
            node.compact = self._compact
        self._relayout()

    def is_poster(self) -> bool:
        return self._poster

    def choose(self, side: str, index: int) -> None:
        """记住这一列当前点中的节点。点另一列不会把这一列的选择清掉。"""
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        index = int(index)
        if not (0 <= index < len(col)):
            return
        self._chosen[side] = index
        self._anchor[side] = index
        self._selected[side] = {index}
        self._sync_node_flags()

    def selected_indices(self, side: str) -> list[int]:
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        return sorted(i for i in self._selected[side] if 0 <= i < len(col))

    def select_range(self, side: str, index: int) -> None:
        """从上次点中的那张一直选到这一张。锚点不动，所以可以来回改终点。"""
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        index = int(index)
        if not (0 <= index < len(col)):
            return
        anchor = self._anchor.get(side)
        if anchor is None or not (0 <= int(anchor) < len(col)):
            self.choose(side, index)
            return
        lo, hi = sorted((int(anchor), index))
        self._selected[side] = set(range(lo, hi + 1))
        self._chosen[side] = index
        self._sync_node_flags()

    def toggle_in_selection(self, side: str, index: int) -> None:
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        index = int(index)
        if not (0 <= index < len(col)):
            return
        if index in self._selected[side]:
            self._selected[side].discard(index)
            if self._chosen.get(side) == index:
                self._chosen[side] = max(self._selected[side]) if self._selected[side] else None
        else:
            self._selected[side].add(index)
            self._chosen[side] = index
            self._anchor[side] = index
        self._sync_node_flags()

    def select_band(self, side: str, rect: QRectF, *, additive: bool = False) -> list[int]:
        """框里的这一列节点。另一列不动。"""
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        hits = [n.index for n in col if rect.intersects(n.sceneBoundingRect())]
        if additive:
            self._selected[side].update(hits)
        else:
            self._selected[side] = set(hits)
        if hits:
            self._chosen[side] = hits[-1]
            if not additive:
                self._anchor[side] = hits[0]
        elif not additive:
            self._chosen[side] = None
        self._sync_node_flags()
        return hits

    def _sync_node_flags(self) -> None:
        for side, col in (("left", self._left), ("right", self._right)):
            sel = self._selected[side]
            chosen = self._chosen.get(side)
            for node in col:
                on = node.index in sel
                ch = chosen == node.index
                if node.selected != on or node.chosen != ch:
                    node.selected = on
                    node.chosen = ch
                    node.update()

    def chosen(self, side: str):
        side = "right" if side == "right" else "left"
        col = self._right if side == "right" else self._left
        index = self._chosen.get(side)
        if index is None or not (0 <= int(index) < len(col)):
            return None
        return int(index)

    def is_compact(self) -> bool:
        return self._compact

    def set_thumb(self, key_path: str, pixmap) -> int:
        """缩略图后台算好之后就地补上。不重建场景，连线和选中都不受影响。"""
        had = self._has_any_thumb()
        n = 0
        for node in self._left + self._right:
            if node.key_path and node.key_path == key_path:
                node.thumb = pixmap
                node.update()
                n += 1
        # 第一张缩略图到货时卡片要变宽给它腾地方，之后就不用再排了
        if n and not had:
            self._relayout()
        return n

    def _column_extent(self, count: int) -> float:
        """这一列从顶边距到最后一张卡片底边。空列是 0。"""
        n = int(count)
        if n <= 0:
            return 0.0
        return MARGIN * 2 + n * (self._row_height() + NODE_GAP) - NODE_GAP

    def _layout_ready(self) -> tuple[bool, float, float]:
        vp = self.viewport()
        view_w = float(vp.width())
        view_h = float(vp.height())
        # 视口还没撑开时沿用整列高度，离屏测试不会被一条假滚动条挤窄。
        ready = (view_h >= (MARGIN * 2 + self._row_height())
                 and view_w >= NODE_MIN_W)
        return ready, view_w, view_h

    def scroll_column(self, side: str, value: float) -> None:
        """只滚动这一列。另一列的位置不动。"""
        side = "right" if side == "right" else "left"
        self._scroll[side] = max(0.0, float(value))
        self._relayout()

    def _on_scroll(self, side: str, value: int) -> None:
        side = "right" if side == "right" else "left"
        if abs(float(value) - float(self._scroll[side])) < 0.5:
            return
        self.scroll_column(side, float(value))

    def _sync_bars(self, view_h: float) -> tuple[float, float]:
        """哪一列比视口高，哪一侧才出现滚动条，滚动量也夹在可滚范围内。"""
        sb_w = max(float(SPACE_2 * 2), float(self._sb_left.sizeHint().width()))
        insets = {}
        step = max(1, int(self._row_height() + NODE_GAP))
        for side, bar, count in (
                ("left", self._sb_left, len(self._left)),
                ("right", self._sb_right, len(self._right))):
            extent = self._column_extent(count)
            max_scroll = max(0.0, extent - view_h)
            self._scroll[side] = min(max(0.0, self._scroll[side]), max_scroll)
            overflow = extent > view_h + 1.0
            bar.blockSignals(True)
            bar.setRange(0, int(round(max_scroll)))
            bar.setPageStep(max(1, int(view_h)))
            bar.setSingleStep(step)
            bar.setValue(int(round(self._scroll[side])))
            bar.setVisible(overflow)
            bar.blockSignals(False)
            insets[side] = sb_w if overflow else 0.0
        return insets["left"], insets["right"]

    def _place_bars(self, inset_l: float, inset_r: float) -> None:
        vp = self.viewport()
        x, y = vp.x(), vp.y()
        w = vp.width()
        h = max(1, vp.height())
        if inset_l > 0:
            self._sb_left.setGeometry(x, y, int(inset_l), h)
            self._sb_left.raise_()
        if inset_r > 0:
            bw = int(inset_r)
            self._sb_right.setGeometry(x + max(0, w - bw), y, bw, h)
            self._sb_right.raise_()

    def _relayout(self) -> None:
        """左列贴左、右列贴右。两列各自用自己的滚动量，互不影响。

        视图自己的滚动条是关掉的：开着的话一滚就是两列一起动。
        某一列比视口高，才在那一侧放滚动条，卡片给它让出宽度。
        """
        ready, view_w, view_h = self._layout_ready()
        inset_l = inset_r = 0.0
        if ready:
            inset_l, inset_r = self._sync_bars(view_h)
        else:
            for bar in (self._sb_left, self._sb_right):
                bar.blockSignals(True)
                bar.setVisible(False)
                bar.blockSignals(False)
        avail = max(float(SPACE_6 * 12), view_w - MARGIN * 2 - inset_l - inset_r)
        node_w = min(self._content_width(), (avail - COL_GAP) / 2.0)
        node_w = max(float(NODE_MIN_W), node_w)
        gap = max(float(COL_GAP), avail - node_w * 2)
        total_w = node_w * 2 + gap
        left_x = MARGIN + inset_l
        right_x = MARGIN + inset_l + total_w - node_w
        for side, column, x in (
                ("left", self._left, left_x),
                ("right", self._right, right_x)):
            y = MARGIN - (self._scroll[side] if ready else 0.0)
            for node in column:
                node.prepareGeometryChange()
                node.width = node_w
                node.setPos(x, y)
                y += node.height() + NODE_GAP
        for link in self._links:
            link.refresh()
        if ready:
            scene_w = max(view_w, MARGIN * 2 + total_w + inset_l + inset_r)
            scene_h = max(view_h, 1.0)
        else:
            rows = max(len(self._left), len(self._right), 1)
            scene_w = MARGIN * 2 + total_w
            scene_h = MARGIN * 2 + rows * (self._row_height() + NODE_GAP)
        self._scene.setSceneRect(0, 0, scene_w, scene_h)
        if ready:
            self._place_bars(inset_l, inset_r)

    def _row_height(self) -> float:
        if self._compact:
            return float(NODE_H_LIST)
        if self._poster:
            return float(NODE_H_POSTER)
        return float(NODE_H_CARD)

    def _content_width(self) -> float:
        """按最长的那条文字算卡片该多宽，夹在 MIN/MAX 之间。

        Luca：「只有一条连线的话，不用这么宽」。所以宽度跟内容走，
        不跟窗口走 —— 窗口多出来的宽度全给中间的连线区。
        海报模式例外：卡片固定到 POSTER_W，缩略图才够大。
        """
        if self._poster and not self._compact:
            return float(POSTER_W)
        from PySide6.QtGui import QFont, QFontMetricsF
        font = QFont(self.font())
        font.setPixelSize(FONT_LABEL)
        fm = QFontMetricsF(font)
        widest = 0.0
        for node in self._left + self._right:
            widest = max(widest, fm.horizontalAdvance(node.title))
            if not self._compact and node.subtitle:
                widest = max(widest, fm.horizontalAdvance(node.subtitle))
        pad = SPACE_2 * 2 + PORT_R * 2
        if not self._compact and self._has_any_thumb():
            pad += THUMB_W + SPACE_2
        return max(NODE_MIN_W, min(NODE_MAX_W, widest + pad))

    def _has_any_thumb(self) -> bool:
        for node in self._left + self._right:
            if node.thumb is not None and not node.thumb.isNull():
                return True
        return False

    def column_centers(self) -> tuple[float, float]:
        """两列在视口坐标里的中心 x，给外面的表头对齐用。"""
        if not (self._left or self._right):
            return (0.0, 0.0)
        node = (self._left or self._right)[0]
        w = node.width
        left_c = MARGIN + w / 2.0
        right_c = self._scene.sceneRect().width() - MARGIN - w / 2.0
        return (left_c, right_c)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    # ---------------------------------------------------------- 连线
    def links(self) -> list[tuple[int, int]]:
        """[(左下标, 右下标), …]，按左、再按右排序，结果稳定。"""
        return sorted(link.key() for link in self._links)

    def node_key(self, side: str, index: int) -> str:
        col = self._right if side == "right" else self._left
        index = int(index)
        if 0 <= index < len(col):
            return col[index].key_path
        return ""

    def link_key_paths(self) -> list[tuple[int, str]]:
        """[(左下标, 右节点的 key_path), …]。右边换序之后还能按路径把线接回去。"""
        out = []
        for li, ri in self.links():
            if 0 <= ri < len(self._right):
                out.append((li, self._right[ri].key_path))
        return out

    def set_links(self, pairs) -> None:
        self.clear_links(quiet=True)
        for li, ri in pairs or []:
            self._add_link(int(li), int(ri), quiet=True)
        self.linksChanged.emit()

    def _add_link(self, li: int, ri: int, quiet: bool = False) -> bool:
        if not (0 <= li < len(self._left) and 0 <= ri < len(self._right)):
            return False
        if (li, ri) in {lk.key() for lk in self._links}:
            return False
        left, right = self._left[li], self._right[ri]
        link = LinkItem(left, right)
        self._scene.addItem(link)
        self._links.append(link)
        left.linked += 1
        right.linked += 1
        left.update()
        right.update()
        if not quiet:
            self.linksChanged.emit()
        return True

    def add_links(self, pairs) -> int:
        """加上这些连线，已有的留下。返回新加上的条数。"""
        added = 0
        for li, ri in pairs or []:
            if self._add_link(int(li), int(ri), quiet=True):
                added += 1
        if added:
            self.linksChanged.emit()
        return added

    def _remove_link(self, link: LinkItem, quiet: bool = False) -> None:
        if link not in self._links:
            return
        self._links.remove(link)
        link.left.linked = max(0, link.left.linked - 1)
        link.right.linked = max(0, link.right.linked - 1)
        link.left.update()
        link.right.update()
        self._scene.removeItem(link)
        if not quiet:
            self.linksChanged.emit()

    def clear_links(self, quiet: bool = False) -> None:
        for link in list(self._links):
            self._remove_link(link, quiet=True)
        if not quiet:
            self.linksChanged.emit()

    def auto_link(self) -> int:
        """从上到下一一对应。多出来的那一边留空，不硬凑。"""
        self.clear_links(quiet=True)
        n = min(len(self._left), len(self._right))
        for i in range(n):
            self._add_link(i, i, quiet=True)
        self.linksChanged.emit()
        return n

    def assign_right_to_lefts(self, ri: int, left_indices) -> int:
        """这些左边的节点改成只连这一张右边的。没点名的左边保持原样。

        返回现在连着这张右边的左边条数。拖线仍然可以给同一条左边再加一张。
        """
        ri = int(ri)
        if not (0 <= ri < len(self._right)):
            return 0
        want = set()
        for raw in left_indices or []:
            li = int(raw)
            if 0 <= li < len(self._left):
                want.add(li)
        for link in list(self._links):
            li, rj = link.key()
            if li in want and rj != ri:
                self._remove_link(link, quiet=True)
        for li in sorted(want):
            self._add_link(li, ri, quiet=True)
        self.linksChanged.emit()
        return sum(1 for li, rj in self.links() if rj == ri and li in want)

    def _cut_with_knife(self) -> int:
        """刀划过的每一条连线都断掉。轨迹太短（等于误点）就不算。"""
        if len(self._knife) < 2:
            return 0
        from PySide6.QtGui import QPainterPathStroker
        span = self._knife[-1] - self._knife[0]
        if (span.x() * span.x() + span.y() * span.y()) < (SPACE_2 ** 2):
            return 0
        blade = QPainterPath(self._knife[0])
        for pt in self._knife[1:]:
            blade.lineTo(pt)
        st = QPainterPathStroker()
        st.setWidth(HIT_PAD)
        blade = st.createStroke(blade)
        gone = 0
        for link in list(self._links):
            if link.path().intersects(blade):
                self._remove_link(link, quiet=True)
                gone += 1
        return gone

    def remove_selected(self) -> int:
        gone = 0
        for link in list(self._links):
            if link.isSelected():
                self._remove_link(link, quiet=True)
                gone += 1
        if gone:
            self.linksChanged.emit()
        return gone

    # ---------------------------------------------------------- 交互
    def _snap_radius(self) -> float:
        """离端口这么远就算吸上。不超过半行距，列表模式不会吸到隔壁。"""
        pitch = float(self._row_height() + NODE_GAP)
        return max(PORT_SNAP_MIN, min(PORT_SNAP_MAX, pitch * 0.45))

    @staticmethod
    def _on_port(node: NodeItem, scene_pos: QPointF) -> bool:
        """按在端口那个小圆上（给一点余量，不然太难点）。"""
        d = node.port_pos() - scene_pos
        return (d.x() * d.x() + d.y() * d.y()) <= (PORT_R * 2.5) ** 2

    def _nearest_port(self, scene_pos: QPointF, side: str | None = None,
                      radius: float | None = None) -> NodeItem | None:
        """离这个点最近、并且还在吸附半径里的端口。隔着一条小缝也算。"""
        limit = float(self._snap_radius() if radius is None else radius)
        best, best_d = None, limit * limit
        columns = []
        if side in (None, "left"):
            columns.append(self._left)
        if side in (None, "right"):
            columns.append(self._right)
        for col in columns:
            for node in col:
                d = node.port_pos() - scene_pos
                dd = d.x() * d.x() + d.y() * d.y()
                if dd <= best_d:
                    best, best_d = node, dd
        return best

    def _set_magnet(self, node: NodeItem | None) -> None:
        for item in self._left + self._right:
            on = item is node
            if item.magnet != on:
                item.magnet = on
                item.update()

    def _link_at_node(self, node: NodeItem, scene_pos: QPointF):
        """这个节点上离按下点最近的那条连线。"""
        best, best_d = None, None
        for link in self._links:
            if link.left is not node and link.right is not node:
                continue
            other = link.right if link.left is node else link.left
            d = (other.port_pos() - scene_pos)
            dd = d.x() * d.x() + d.y() * d.y()
            if best_d is None or dd < best_d:
                best, best_d = link, dd
        return best

    def _node_at(self, scene_pos: QPointF) -> NodeItem | None:
        for item in self._scene.items(scene_pos):
            if isinstance(item, NodeItem):
                return item
        return None

    def _side_at(self, scene_pos: QPointF) -> str:
        """光标落在哪一列。两列中间的空档，左半算左边。"""
        if self._left and self._right:
            left_edge = self._left[0].pos().x() + self._left[0].width
            right_edge = self._right[0].pos().x()
            mid = (left_edge + right_edge) / 2.0
            return "left" if scene_pos.x() <= mid else "right"
        if self._right and not self._left:
            return "right"
        return "left"

    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            super().wheelEvent(event)
            return
        pos = self.mapToScene(event.position().toPoint())
        side = self._side_at(pos)
        step = self._row_height() + NODE_GAP
        self.scroll_column(side, self._scroll[side] - (float(dy) / 120.0) * step)
        event.accept()

    def _start_knife(self, pos: QPointF) -> None:
        self._knife = [pos]
        self._knife_item = QGraphicsPathItem()
        pen = QPen(_c("err_text", 0.95), 2.0, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._knife_item.setPen(pen)
        self._knife_item.setZValue(4)
        self._scene.addItem(self._knife_item)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _start_drag(self, start: NodeItem) -> None:
        self._drag_from = start
        self._drag_item = QGraphicsPathItem()
        ink = QColor(start.tint) if start.tint is not None else _c("accent", 0.9)
        pen = QPen(ink, 2.0, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._drag_item.setPen(pen)
        self._drag_item.setZValue(3)
        self._drag_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self._scene.addItem(self._drag_item)
        self._arm_wire_repaint()

    def _start_band(self, pos: QPointF, side: str, additive: bool) -> None:
        self._band_side = side
        self._band_additive = additive
        self._band_origin = pos
        self._band_item = QGraphicsRectItem()
        pen = QPen(_c("accent", 0.9), 1.0, Qt.PenStyle.DashLine)
        self._band_item.setPen(pen)
        self._band_item.setBrush(QBrush(_c("accent", 0.12)))
        self._band_item.setZValue(5)
        self._band_item.setRect(QRectF(pos, pos))
        self._scene.addItem(self._band_item)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = self.mapToScene(event.position().toPoint())
        mods = event.modifiers()
        # 按住 Ctrl 拖 = 刀：划过哪条线就割断哪条。没划开就算点选切换。
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._knife_press_node = self._node_at(pos)
            self._start_knife(pos)
            event.accept()
            return
        node = self._node_at(pos)
        if node is None:
            # 端口有一半画在卡片外面，再往缝里偏几个像素也该能把线拉出来。
            near = self._nearest_port(pos)
            if near is not None:
                self.choose(near.side, near.index)
                self._start_drag(near)
                event.accept()
                return
        if node is not None and (mods & Qt.KeyboardModifier.ShiftModifier):
            self.select_range(node.side, node.index)
            event.accept()
            return
        if node is not None:
            self.choose(node.side, node.index)
            start = node
            # 按在已经连了线的端口上 = 把那条线摘下来接着拖：
            # 丢在空白处就是断开，丢到对面别的卡片上就是改接
            if self._on_port(node, pos) and node.linked:
                link = self._link_at_node(node, pos)
                if link is not None:
                    start = link.right if link.left is node else link.left
                    self._remove_link(link)
            self._start_drag(start)
            event.accept()
            return
        self._start_band(
            pos, self._side_at(pos),
            bool(mods & Qt.KeyboardModifier.ShiftModifier))
        event.accept()

    def mouseMoveEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        if self._knife_item is not None:
            self._knife.append(pos)
            path = QPainterPath(self._knife[0])
            for pt in self._knife[1:]:
                path.lineTo(pt)
            self._knife_item.setPath(path)
            event.accept()
            return
        if self._drag_item is not None and self._drag_from is not None:
            a = self._drag_from.port_pos()
            b = pos
            dx = max(SPACE_6, abs(b.x() - a.x()) * 0.45)
            sign = 1.0 if self._drag_from.side == "left" else -1.0
            path = QPainterPath(a)
            path.cubicTo(QPointF(a.x() + dx * sign, a.y()),
                         QPointF(b.x() - dx * sign, b.y()), b)
            self._drag_item.setPath(path)
            opposite = "right" if self._drag_from.side == "left" else "left"
            self._set_magnet(self._nearest_port(pos, side=opposite))
            self.viewport().update()
            event.accept()
            return
        if self._band_item is not None and self._band_origin is not None:
            self._band_item.setRect(QRectF(self._band_origin, pos).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._knife_item is not None:
            press_node = self._knife_press_node
            short = True
            if len(self._knife) >= 2:
                delta = self._knife[-1] - self._knife[0]
                short = (delta.x() * delta.x() + delta.y() * delta.y()) < float(SPACE_2 ** 2)
            cut = self._cut_with_knife()
            self._scene.removeItem(self._knife_item)
            self._knife_item = None
            self._knife = []
            self._knife_press_node = None
            self.viewport().unsetCursor()
            if cut:
                self.linksChanged.emit()
            elif short and press_node is not None:
                self.toggle_in_selection(press_node.side, press_node.index)
            event.accept()
            return
        if self._drag_item is not None:
            pos = self.mapToScene(event.position().toPoint())
            target = self._node_at(pos)
            src = self._drag_from
            if src is not None and (target is None or target.side == src.side):
                opposite = "right" if src.side == "left" else "left"
                near = self._nearest_port(pos, side=opposite)
                if near is not None:
                    target = near
            self._set_magnet(None)
            self._scene.removeItem(self._drag_item)
            self._drag_item = None
            self._drag_from = None
            self._end_wire_repaint()
            if (target is not None and src is not None
                    and target.side != src.side):
                left, right = (src, target) if src.side == "left" else (target, src)
                self._add_link(left.index, right.index)
            event.accept()
            return
        if self._band_item is not None:
            rect = self._band_item.rect()
            side = self._band_side
            additive = self._band_additive
            self._scene.removeItem(self._band_item)
            self._band_item = None
            self._band_origin = None
            if rect.width() < SPACE_2 and rect.height() < SPACE_2:
                if not additive:
                    self._selected[side] = set()
                    self._chosen[side] = None
                    self._sync_node_flags()
            else:
                self.select_band(side, rect, additive=additive)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # 双击连线直接删掉，比先选中再按 Delete 快
        for item in self._scene.items(self.mapToScene(event.position().toPoint())):
            if isinstance(item, LinkItem):
                self._remove_link(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.remove_selected():
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        node = self._node_at(self.mapToScene(event.pos()))
        if node is not None:
            self.nodeRightClicked.emit(node.side, node.index)
            event.accept()
            return
        super().contextMenuEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, _c("bg0"))
        # 稀疏网点，和 Blender 的节点区一个意思：给眼睛一个参照，但不抢戏
        step = SPACE_5
        painter.setPen(QPen(_c("fg0", 0.06), 1.0))
        x = int(rect.left()) - int(rect.left()) % step
        while x < rect.right():
            y = int(rect.top()) - int(rect.top()) % step
            while y < rect.bottom():
                painter.drawPoint(x, y)
                y += step
            x += step
