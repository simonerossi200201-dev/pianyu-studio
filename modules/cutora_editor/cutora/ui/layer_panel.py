# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-G · 中央监视器右侧的图层面板（专业版）。

对标 Photoshop 图层 / Resolve 图层面板：
- 每行：色标条、缩略图、类型图标、名字（双击 / F2 改）、时段；右侧 👁 显隐、🔒 锁定、S 独奏。
- 上面的层在上面；文本 / 图片层可以拖拽排 z 序（同一种之间）。
- 搜索框按名字过滤，类型下拉只看某一类；颜色标签 8 色。
- 右键：显隐 / 锁定 / 独奏 / 颜色 / 重命名 / 删除 / 复制到其他视频 / 粘贴到选中或全部视频。
- 按住眼睛往下拖，拖过的行一起隐藏或一起显示。再拖一次反向。
- 键盘：↑↓ 选，Space 显隐，L 锁，S 独奏，F2 改名，Delete 删，Ctrl+F 搜索，Esc 清搜索。
- 和监视器画面双向同步选择；所有改动进监视器的撤销历史。

数据和操作在 cutora/batch/layer_model.py；这里只画、只转发。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractItemModel, QEvent, QItemSelection, QItemSelectionModel, QMimeData, QModelIndex,
    QPointF, QRect, QRectF, QSize, Qt, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMenu,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QTreeView,
    QVBoxLayout, QWidget,
)

from cutora.batch import layer_model as lm
from cutora.batch import layer_transfer as lt
from cutora.batch import monitor_model as mm
from cutora.core.log import get_logger
from cutora.ui.icons import icon as lucide_icon, pixmap as lucide_pixmap
from cutora.ui.theme.tokens import (
    FONT_BODY, FONT_MICRO, ICON_MD, ICON_SM, RADIUS_SM, SPACE_1, SPACE_1H, SPACE_2,
    SPACE_6, current, hex_rgba,
)

_LOG = get_logger("cutora.ui.layer_panel")

ROW_H = 40
THUMB_W, THUMB_H = 40, 24
STRIP_W = 3
BTN_W = 22          # 右侧每个小按钮占的宽度（含间距）
KIND_ICON = {"text": "type", "image": "image", "video": "film", "adjust": "layers",
             "audio": "headphones", "bgm": "music", "sfx": "headphones"}
KIND_FILTERS = (("all", "全部"), ("adjust", "调整"), ("text", "文本"), ("image", "图片"), ("video", "视频"),
                ("audio", "音频"))
_MIME = "application/x-cutora-layer-rows"

ROLE_ROW = Qt.ItemDataRole.UserRole + 1


# ───────────────────────────── 缩略图 ─────────────────────────────

class _ThumbCache:
    """按（种类, 内容签名）缓存 40×24 的小图；视频用监视器已经解出来的那一帧。"""

    def __init__(self):
        self._cache: dict = {}

    def clear(self):
        self._cache.clear()

    def get(self, row: dict, task, tile) -> QPixmap | None:
        kind = row.get("kind")
        key = self._key(row, task, tile)
        if key is None:
            return None
        pm = self._cache.get(key)
        if pm is None:
            try:
                pm = self._make(kind, row, task, tile)
            except Exception:
                _LOG.exception("图层缩略图失败")
                pm = None
            if pm is None:
                pm = QPixmap()
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = pm
        return pm if not pm.isNull() else None

    def _key(self, row, task, tile):
        kind = row.get("kind")
        obj = lm.resolve(task, row.get("locator"))
        if obj is None:
            return None
        if kind == "image":
            return ("image", lt._image_path(obj))
        if kind == "text":
            return ("text", str(obj.get("content") or "")[:12], str(obj.get("font_family") or ""),
                    str(obj.get("color") or obj.get("fill_color") or ""), bool(obj.get("bold")))
        if kind == "adjust":
            from cutora.render import adjust_layer as AL
            sol, eff = AL.solid_of(obj), AL.effect_of(obj)
            return ("adjust", sol["on"], sol["color"], AL.effect_active(eff), tuple(eff["kinds"]))
        if kind == "video":
            vid = getattr(tile, "video", None) if tile is not None else None
            gen = getattr(tile, "gen", 0) if tile is not None else 0
            return ("video", id(task), row.get("locator", {}).get("track_id"), gen if vid is not None else -1)
        return (kind,)

    def _make(self, kind, row, task, tile):
        t = current()
        pm = QPixmap(THUMB_W, THUMB_H)
        pm.fill(QColor(t.bg0))
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            if kind == "image":
                path = lt._image_path(lm.resolve(task, row["locator"]))
                img = QImage(path) if path else QImage()
                if not img.isNull():
                    img = img.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                    p.drawImage((THUMB_W - img.width()) // 2, (THUMB_H - img.height()) // 2, img)
                else:
                    p.drawPixmap(12, 4, lucide_pixmap("image", t.fg2, ICON_MD))
            elif kind == "text":
                ov = lm.resolve(task, row["locator"])
                text = str(ov.get("content") or "T").strip().replace("\n", " ")[:3] or "T"
                color = str(ov.get("color") or ov.get("fill_color") or t.track_text)
                from cutora.ui.panels.fonts import render_font_family
                f = QFont(render_font_family(str(ov.get("font_family") or "")))
                f.setPixelSize(15)
                f.setBold(bool(ov.get("bold")))
                p.setFont(f)
                p.setPen(QColor(color) if QColor(color).isValid() else QColor(t.track_text))
                p.drawText(QRect(0, 0, THUMB_W, THUMB_H), Qt.AlignmentFlag.AlignCenter, text)
            elif kind == "video":
                vid = getattr(tile, "video", None) if tile is not None else None
                if isinstance(vid, QImage) and not vid.isNull():
                    img = vid.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
                    p.drawImage((THUMB_W - img.width()) // 2, (THUMB_H - img.height()) // 2, img)
                else:
                    p.drawPixmap(12, 4, lucide_pixmap("film", t.fg2, ICON_MD))
            elif kind == "adjust":
                # Q174：纯色 = 色块；效果 = 虚线框（和时间线上的小图标一个意思）
                from cutora.render import adjust_layer as AL
                ov = lm.resolve(task, row["locator"])
                sol, eff = AL.solid_of(ov), AL.effect_of(ov)
                r = QRect(8, 4, THUMB_W - 16, THUMB_H - 8)
                if sol["on"]:
                    p.fillRect(r, QColor(sol["color"]))
                pen = QPen(QColor(t.fg1), 1.2)
                if AL.effect_active(eff):
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawRect(r)
            else:
                p.fillRect(0, 0, THUMB_W, THUMB_H, QColor(t.track_audio).darker(160))
                p.drawPixmap(12, 4, lucide_pixmap("headphones", t.fg1, ICON_MD))
        finally:
            p.end()
        return pm


# ───────────────────────────── 模型 ─────────────────────────────

class LayerListModel(QAbstractItemModel):
    """平的一层（以后遮罩 / 抠像作为子项挂在下面，所以用 QAbstractItemModel）。"""

    reorder_requested = Signal(list, object)   # (rows to move, drop-before row dict or None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self):
        return self._rows

    def row_at(self, index) -> dict | None:
        if not index.isValid():
            return None
        r = index.row()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def index_of(self, locator) -> QModelIndex:
        for i, r in enumerate(self._rows):
            if lt._loc_key(r.get("locator")) == lt._loc_key(locator):
                return self.index(i, 0)
        return QModelIndex()

    # Qt 接口
    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid() or column != 0 or not (0 <= row < len(self._rows)):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, _index):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, _parent=QModelIndex()):
        return 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.row_at(index)
        if row is None:
            return None
        if role == ROLE_ROW:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return row.get("title") or ""
        if role == Qt.ItemDataRole.ToolTipRole:
            return lm.describe(row)
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        row = self.row_at(index)
        if row is not None and lm.can_reorder(row):
            base |= Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        return base

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        return [_MIME]

    def mimeData(self, indexes):
        md = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        md.setData(_MIME, ",".join(str(r) for r in rows).encode("ascii"))
        return md

    def canDropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat(_MIME):
            return False
        src = self._decode(data)
        if not src:
            return False
        # Q177：画面图层都能拖（拖的是它所在的时间线轨道）；声音不参与叠压
        if not all(lm.can_reorder(self._rows[i]) for i in src):
            return False
        target = self._drop_target(row, parent)
        if target is not None and not lm.can_reorder(target):
            return False
        return True

    def dropMimeData(self, data, action, row, column, parent):
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        src = [self._rows[i] for i in self._decode(data)]
        self.reorder_requested.emit(src, self._drop_target(row, parent))
        return True

    def _drop_target(self, row, parent):
        """放在哪一行「上面」。放到最后 → None（最底）。"""
        if parent.isValid():
            return self.row_at(parent)
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _decode(self, data) -> list[int]:
        try:
            raw = bytes(data.data(_MIME)).decode("ascii")
        except Exception:
            return []
        out = []
        for part in raw.split(","):
            if part.strip().isdigit():
                i = int(part)
                if 0 <= i < len(self._rows):
                    out.append(i)
        return out


def _paint_eye(painter: QPainter, rect: QRect, open_: bool, color) -> None:
    """矢量眼睛。打开是瞳孔，关上加一道斜线。抗锯齿，不走位图。"""
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(1.35)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = float(rect.center().x())
        cy = float(rect.center().y())
        hw, hh = 6.5, 4.2
        path = QPainterPath()
        path.moveTo(cx - hw, cy)
        path.quadTo(cx, cy - hh * 1.7, cx + hw, cy)
        path.quadTo(cx, cy + hh * 1.7, cx - hw, cy)
        painter.drawPath(path)
        if open_:
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(cx - 1.7, cy - 1.7, 3.4, 3.4))
        else:
            painter.drawLine(
                QPointF(cx - hw + 0.6, cy + hh * 0.85),
                QPointF(cx + hw - 0.6, cy - hh * 0.85),
            )
    finally:
        painter.restore()


# ───────────────────────────── 画一行 ─────────────────────────────

class LayerDelegate(QStyledItemDelegate):
    """一行：色标条 | 缩略图 | 类型图标 名字 / 时段 | 👁 🔒 S。"""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.hover_row = -1
        self.hover_btn = ""

    # 右侧三个按钮的矩形
    @staticmethod
    def button_rects(rect: QRect) -> dict:
        right = rect.right() - SPACE_1H
        out = {}
        for name in ("solo", "lock", "eye"):
            x0 = right - BTN_W + 1
            out[name] = QRect(x0, rect.top() + (rect.height() - BTN_W) // 2, BTN_W, BTN_W)
            right = x0 - 1
        return out

    @staticmethod
    def name_rect(rect: QRect) -> QRect:
        left = rect.left() + STRIP_W + SPACE_2 + THUMB_W + SPACE_2 + ICON_SM + SPACE_1H
        right = rect.right() - SPACE_1H - BTN_W * 3 - SPACE_1
        return QRect(left, rect.top() + SPACE_1, max(10, right - left), rect.height() // 2)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        row = index.data(ROLE_ROW) or {}
        t = current()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.hover_row
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 底色
            if selected:
                painter.fillRect(rect, QColor(*hex_rgba(t.accent, 56)))
            elif hovered:
                painter.fillRect(rect, QColor(t.bg3))
            # 分隔线
            painter.setPen(QPen(QColor(t.line), 1))
            painter.drawLine(rect.left() + STRIP_W + SPACE_2, rect.bottom(), rect.right(), rect.bottom())
            # 色标条
            raw_col = str(row.get("color") or "")
            color = lm.LABEL_HEX.get(raw_col) or (raw_col if raw_col.startswith("#") else "")   # Q154：组色是 #RRGGBB
            if color:
                painter.fillRect(QRect(rect.left(), rect.top(), STRIP_W, rect.height()), QColor(color))
            dim = bool(row.get("hidden"))
            # 缩略图
            tx = rect.left() + STRIP_W + SPACE_2
            ty = rect.top() + (rect.height() - THUMB_H) // 2
            pm = self.panel.thumbs.get(row, self.panel.task(), self.panel.tile())
            painter.setOpacity(0.45 if dim else 1.0)
            if pm is not None:
                painter.drawPixmap(tx, ty, pm)
            else:
                painter.fillRect(QRect(tx, ty, THUMB_W, THUMB_H), QColor(t.bg0))
            painter.setOpacity(1.0)
            painter.setPen(QPen(QColor(t.border), 1))
            painter.drawRoundedRect(QRectF(tx + 0.5, ty + 0.5, THUMB_W - 1, THUMB_H - 1), RADIUS_SM, RADIUS_SM)
            # 类型图标
            kind = row.get("kind")
            kcolor = {"text": t.track_text, "image": t.track_image, "video": t.track_video}.get(kind, t.track_audio)
            ix = tx + THUMB_W + SPACE_2
            painter.drawPixmap(ix, rect.top() + SPACE_1 + 1, lucide_pixmap(KIND_ICON.get(kind, "square"),
                                                                            t.fg2 if dim else kcolor, ICON_SM))
            # 名字 + 时段
            nrect = self.name_rect(rect)
            f = QFont(option.font)
            f.setPixelSize(FONT_BODY)
            painter.setFont(f)
            painter.setPen(QColor(t.fg2 if dim else t.fg0))
            title = QFontMetrics(f).elidedText(row.get("title") or "", Qt.TextElideMode.ElideRight, nrect.width())
            painter.drawText(nrect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
            f2 = QFont(option.font)
            f2.setPixelSize(FONT_MICRO)
            painter.setFont(f2)
            painter.setPen(QColor(t.fg2))
            sub = "%s · %.1f–%.1fs" % (lt.KIND_WORD.get(kind, kind), float(row.get("start") or 0.0),
                                       float(row.get("end") or 0.0))
            extra = []
            if row.get("blend") and row["blend"] != "normal":
                extra.append(row["blend"])
            if float(row.get("opacity", 255)) < 255:
                extra.append("%d%%" % int(round(float(row["opacity"]) / 2.55)))
            if extra:
                sub += " · " + " ".join(extra)
            srect = QRect(nrect.left(), rect.top() + rect.height() // 2, nrect.width(), rect.height() // 2 - SPACE_1)
            painter.drawText(srect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, srect.width()))
            # 右侧按钮：👁 永远显示；🔒 / S 锁着 / 独奏中 / 悬停 / 选中时显示
            btns = self.button_rects(rect)
            show_side = hovered or selected or row.get("locked") or self.panel.is_solo(row)
            for name, r in btns.items():
                on = {"eye": not dim, "lock": bool(row.get("locked")), "solo": self.panel.is_solo(row)}[name]
                if name != "eye" and not show_side and not on:
                    continue
                hot = hovered and self.hover_btn == name
                if hot:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(*hex_rgba(t.fg0, 28)))
                    painter.drawRoundedRect(QRectF(r), RADIUS_SM, RADIUS_SM)
                if name == "solo":
                    f3 = QFont(option.font)
                    f3.setPixelSize(FONT_MICRO)
                    f3.setBold(True)
                    painter.setFont(f3)
                    painter.setPen(QColor(t.accent if on else t.fg2))
                    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "S")
                elif name == "eye":
                    _paint_eye(painter, r, open_=bool(on), color=t.fg1 if on else t.fg2)
                else:
                    stem = "lock" if on else "lock-open"
                    col = t.accent if on else t.fg2
                    pmi = lucide_pixmap(stem, col, ICON_MD)
                    painter.drawPixmap(r.left() + (r.width() - ICON_MD) // 2,
                                       r.top() + (r.height() - ICON_MD) // 2, pmi)
        finally:
            painter.restore()

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(self.name_rect(option.rect).adjusted(-2, -1, 0, 3))

    def setModelData(self, editor, model, index):
        row = index.data(ROLE_ROW) or {}
        self.panel.rename_layer(row, editor.text())

    def editorEvent(self, event, model, option, index):
        row = index.data(ROLE_ROW)
        if row is None:
            return False
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            name = self._button_at(option.rect, event.position().toPoint())
            if name:
                alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                self.panel.click_button(row, name, alt=alt)
                return True
        if event.type() == QEvent.Type.MouseButtonDblClick:
            if self._button_at(option.rect, event.position().toPoint()):
                return True
        return super().editorEvent(event, model, option, index)

    def _button_at(self, rect, pos) -> str:
        for name, r in self.button_rects(rect).items():
            if r.contains(pos):
                return name
        return ""


class LayerView(QTreeView):
    """带悬停高亮和「count()」兼容的树视图。"""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setMouseTracking(True)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QTreeView.EditTrigger.EditKeyPressed | QTreeView.EditTrigger.DoubleClicked)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setObjectName("layerView")
        self._eye_drag = False

    def count(self) -> int:
        return self.model().rowCount() if self.model() is not None else 0

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(ev.position().toPoint())
            if idx.isValid():
                name = self.itemDelegate()._button_at(
                    self.visualRect(idx), ev.position().toPoint())
                if name == "eye":
                    row = self.model().row_at(idx)
                    if row is not None:
                        self._eye_drag = True
                        self.panel._eye_begin(not bool(row.get("hidden")))
                        self.panel._eye_touch(row)
                        ev.accept()
                        return
        self._eye_drag = False
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        idx = self.indexAt(ev.position().toPoint())
        d = self.itemDelegate()
        row = idx.row() if idx.isValid() else -1
        btn = d._button_at(self.visualRect(idx), ev.position().toPoint()) if idx.isValid() else ""
        if (row, btn) != (d.hover_row, d.hover_btn):
            d.hover_row, d.hover_btn = row, btn
            self.viewport().update()
        if self._eye_drag and (ev.buttons() & Qt.MouseButton.LeftButton):
            if idx.isValid():
                got = self.model().row_at(idx)
                if got is not None:
                    self.panel._eye_touch(got)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._eye_drag and ev.button() == Qt.MouseButton.LeftButton:
            self._eye_drag = False
            self.panel._eye_finish()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev):
        d = self.itemDelegate()
        if d.hover_row != -1:
            d.hover_row, d.hover_btn = -1, ""
            self.viewport().update()
        super().leaveEvent(ev)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            return
        super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            return
        super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        if ev.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
            if paths and self.panel.drop_external(paths):
                ev.acceptProposedAction()
                return
        super().dropEvent(ev)

    def keyPressEvent(self, ev):
        if self.panel.handle_key(ev):
            ev.accept()
            return
        super().keyPressEvent(ev)


# ───────────────────────────── 面板 ─────────────────────────────

class LayerPanel(QWidget):
    def __init__(self, grid, parent=None):
        super().__init__(parent)
        self.grid = grid
        self._tile = None
        self._layers: list = []          # 当前显示的行（过滤后）
        self._all_rows: list = []
        self._sig = None
        self._syncing = False
        self.thumbs = _ThumbCache()
        self.setMinimumWidth(SPACE_6 * 9)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        root.setSpacing(SPACE_2)

        head = QHBoxLayout()
        head.setSpacing(SPACE_1)
        self.title = QLabel("图层")
        self.title.setObjectName("sectionHeader")
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("hint")
        head.addWidget(self.title)
        head.addWidget(self.count_lbl)
        head.addStretch(1)
        self.btn_filter = QToolButton()
        self.btn_filter.setIcon(lucide_icon("funnel", None, ICON_MD))
        self.btn_filter.setToolTip("只看某一类图层")
        self.btn_filter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_filter.setAutoRaise(True)
        self._filter_kind = "all"
        fm = QMenu(self.btn_filter)
        self._filter_actions = {}
        for key, label in KIND_FILTERS:
            act = fm.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == "all")
            act.triggered.connect(lambda _c=False, k=key: self.set_filter(k))
            self._filter_actions[key] = act
        self.btn_filter.setMenu(fm)
        head.addWidget(self.btn_filter)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索图层（Ctrl+F）")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(lucide_icon("search", None, ICON_SM), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self._refill())
        root.addWidget(self.search)

        self.hint = QLabel("点一条视频，这里列出它时间线上的文本、图片、视频和音频。")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("hint")
        root.addWidget(self.hint)

        self.model = LayerListModel(self)
        self.model.reorder_requested.connect(self._reorder)
        self.lst = LayerView(self)
        self.lst.setModel(self.model)
        self.lst.setItemDelegate(LayerDelegate(self))
        self.lst.setToolTip("Ctrl / Shift 多选。双击名字重命名；拖动文本 / 图片层改上下顺序。\n"
                            "👁 显隐 · 🔒 锁定 · S 独奏（Alt+点 S 只独奏这一层）")
        self.lst.customContextMenuRequested.connect(self._menu)
        self.lst.selectionModel().selectionChanged.connect(self._on_view_selection)
        root.addWidget(self.lst, 1)

    # ───── 基础 ─────

    def task(self):
        return self._tile.task if self._tile is not None else None

    def tile(self):
        return self._tile

    def set_filter(self, kind: str):
        self._filter_kind = kind if kind in dict(KIND_FILTERS) else "all"
        for key, act in self._filter_actions.items():
            act.setChecked(key == self._filter_kind)
        self._refill()

    # ───── 同步 ─────

    def sync(self):
        tile = self._current_tile()
        task = tile.task if tile is not None else None
        rows = lm.rows(task) if task is not None else []
        sig = None if tile is None else (
            id(task),
            tuple((r["kind"], r["index"], r["title"], round(r["start"], 3), round(r["end"], 3),
                   r["hidden"], r["locked"], r["color"], r["blend"], int(r["opacity"]))
                  for r in rows),
            self._filter_kind, self.search.text(),
        )
        if tile is not self._tile:
            self.thumbs.clear()
        self._tile = tile
        self._all_rows = rows
        if sig != self._sig:
            self._sig = sig
            self._refill(keep_sig=True)
        else:
            self.lst.viewport().update()
        self._sync_selection_from_grid()
        self._retitle()

    def _refill(self, keep_sig=False):
        if not keep_sig:
            self._sig = None
        self._layers = lm.filter_rows(self._all_rows, self.search.text(), self._filter_kind)
        self._syncing = True
        try:
            self.model.set_rows(self._layers)
        finally:
            self._syncing = False
        self._sync_selection_from_grid()
        self._retitle()

    def _retitle(self):
        tile = self._tile
        if tile is None:
            self.title.setText("图层")
            self.count_lbl.setText("")
            self.hint.setText("点一条视频，这里列出它时间线上的文本、图片、视频和音频。")
            self.hint.setVisible(True)
            return
        self.title.setText("图层 · %s" % tile.label)
        total = len(self._all_rows)
        shown = len(self._layers)
        self.count_lbl.setText("%d" % total if shown == total else "%d / %d" % (shown, total))
        self.hint.setVisible(total == 0)
        self.hint.setText("这条视频上还没有图层。" if total == 0 else "")

    def _current_tile(self):
        grid = self.grid
        if grid.active is not None and 0 <= grid.active < len(grid.tiles):
            return grid.tiles[grid.active]
        picked = grid.selected_tiles()
        return picked[-1] if picked else None

    def _sync_selection_from_grid(self):
        """监视器里点中的覆层 → 面板里高亮那一行。"""
        if self._tile is None or self._syncing:
            return
        want = {id(ov) for i, ov in getattr(self.grid, "ov_sel", []) if i == self._tile.idx}
        want_tr = {str(tid) for i, tid in getattr(self.grid, "video_sel", []) if i == self._tile.idx and tid}
        sel = self.lst.selectionModel()
        self._syncing = True
        try:
            new = QItemSelection()
            task = self._tile.task
            for i, row in enumerate(self._layers):
                loc = row.get("locator") or {}
                if loc.get("where") == "clip" and str(loc.get("track_id") or "") in want_tr:
                    idx = self.model.index(i, 0)
                    new.select(idx, idx)
                    continue
                obj = lm.resolve(task, loc)
                if obj is not None and id(obj) in want:
                    idx = self.model.index(i, 0)
                    new.select(idx, idx)
            if want or want_tr:
                sel.select(new, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = False

    def _on_view_selection(self, *_a):
        """面板里选了层 → 监视器画面上高亮。视频片段和覆层都有选中态，右键参数跟这一层。"""
        if self._syncing or self._tile is None:
            return
        rows = self.chosen_layers(strict=True)
        task = self._tile.task
        picked = []
        vids = []
        for row in rows:
            loc = row.get("locator") or {}
            if loc.get("where") == "overlay":
                ov = lm.resolve(task, loc)
                if ov is not None:
                    picked.append((self._tile.idx, ov))
            elif loc.get("where") == "clip":
                tid = str(loc.get("track_id") or "")
                if tid:
                    vids.append((self._tile.idx, tid))
        self._syncing = True
        try:
            self.grid.ov_sel = picked
            self.grid.video_sel = [v for v in getattr(self.grid, "video_sel", []) if v[0] != self._tile.idx] + vids
            self.grid.selected.add(self._tile.idx)
            self.grid.update()
            if rows:
                self.grid.status.emit(lm.describe(rows[-1]))
        finally:
            self._syncing = False

    def chosen_layers(self, strict: bool = False):
        """选中的行；没选中时（strict=False）= 全部，和 Q149 的复制语义一致。"""
        idxs = sorted({i.row() for i in self.lst.selectionModel().selectedIndexes()})
        rows = [self._layers[i] for i in idxs if 0 <= i < len(self._layers)]
        if not rows and not strict:
            return list(self._layers)
        return rows

    def is_solo(self, row: dict) -> bool:
        others = [r for r in self._all_rows if lt._loc_key(r["locator"]) != lt._loc_key(row["locator"])]
        return bool(others) and (not row.get("hidden")) and all(r.get("hidden") for r in others)

    # ───── 改动（都进监视器的撤销）─────

    def _apply(self, label: str, fn) -> bool:
        tile = self._tile
        if tile is None:
            self.grid.status.emit("先点一条视频")
            return False
        tok = self.grid.history.begin([tile.task], snap=mm.snapshot_layers)
        try:
            changed = bool(fn(tile.task))
        except Exception:
            _LOG.exception("图层操作失败：%s", label)
            self.grid.history.cancel(tok)
            self.grid.status.emit("操作失败：%s" % label)
            return False
        if not changed:
            self.grid.history.cancel(tok)
            return False
        self.grid._changed([tile], prio=0)
        self.grid._commit(tok, label, [tile])
        self.sync()
        return True

    def drop_external(self, paths) -> bool:
        """把素材放进当前视频的图层。已经选中的其他视频各放一份。"""
        tile = self._tile
        if tile is None or not paths:
            return False
        targets = list(self.grid.selected_tiles() or [])
        if tile not in targets:
            targets = [tile] + targets
        tok = self.grid.history.begin([t.task for t in targets], snap=mm.snapshot_layers)
        msgs = []
        try:
            for t in targets:
                for path in paths:
                    msgs.append(mm.drop_as_layer(t.task, path))
        except Exception as exc:
            _LOG.exception("拖进图层失败")
            self.grid.history.cancel(tok)
            self.grid.status.emit("放不进去：%s" % exc)
            return False
        self.grid._changed(targets, prio=0)
        self.grid._commit(tok, "放入图层", targets)
        self.sync()
        self.grid.status.emit(msgs[-1] if len(targets) == 1 else "已放到 %d 条视频" % len(targets))
        return True

    def click_button(self, row: dict, name: str, alt: bool = False):
        loc = row["locator"]
        if (loc or {}).get("where") == "folder" and name == "solo":
            from cutora.batch.layer_folders import toggle_collapsed
            self._apply("折叠分组", lambda task, fid=loc.get("id"): toggle_collapsed(task, fid))
            return
        if name == "eye":
            on = not row.get("hidden")
            word = ("静音" if on else "取消静音") if row.get("is_audio") else ("隐藏" if on else "显示")
            self._apply("%s图层" % word, lambda task: lm.set_flag(task, loc, "hidden", on))
        elif name == "lock":
            on = not row.get("locked")
            self._apply("锁定图层" if on else "解锁图层", lambda task: lm.set_flag(task, loc, "locked", on))
        elif name == "solo":
            self._apply("独奏图层", lambda task: lm.solo(task, loc, lm.rows(task)))

    def set_hidden_span(self, rows, hidden: bool) -> bool:
        """把若干行收成同一种显隐。一次撤销。"""
        rows = [r for r in (rows or []) if r]
        if not rows:
            return False
        hidden = bool(hidden)
        label = "隐藏图层" if hidden else "显示图层"

        def _do(task):
            n = 0
            for r in rows:
                n += int(lm.set_flag(task, r["locator"], "hidden", hidden))
            return n

        return self._apply(label, _do)

    def _eye_begin(self, hidden: bool) -> None:
        self._eye_hidden = bool(hidden)
        self._eye_keys = set()
        self._eye_dirty = False
        self._eye_tok = None
        tile = self._tile
        if tile is None:
            return
        self._eye_tok = self.grid.history.begin([tile.task], snap=mm.snapshot_layers)

    def _eye_touch(self, row) -> None:
        if self._eye_tok is None or self._tile is None or not row:
            return
        loc = row.get("locator")
        key = lt._loc_key(loc)
        if key in self._eye_keys:
            return
        self._eye_keys.add(key)
        if bool(row.get("hidden")) == self._eye_hidden:
            return
        if lm.set_flag(self._tile.task, loc, "hidden", self._eye_hidden):
            self._eye_dirty = True
            self.grid._changed([self._tile], prio=0)
            self.sync()

    def _eye_finish(self) -> None:
        tok = getattr(self, "_eye_tok", None)
        self._eye_tok = None
        if tok is None:
            return
        if not getattr(self, "_eye_dirty", False):
            self.grid.history.cancel(tok)
            return
        label = "隐藏图层" if self._eye_hidden else "显示图层"
        self.grid._commit(tok, label, [self._tile])
        self.sync()

    def toggle_selected(self, key: str):
        rows = self.chosen_layers(strict=True)
        if not rows:
            return
        on = not all(r.get(key) for r in rows)
        label = {"hidden": "隐藏图层" if on else "显示图层",
                 "locked": "锁定图层" if on else "解锁图层"}[key]

        def _do(task):
            n = 0
            for r in rows:
                n += int(lm.set_flag(task, r["locator"], key, on))
            return n
        self._apply(label, _do)

    def rename_layer(self, row: dict, name: str):
        self._apply("重命名图层", lambda task: lm.rename(task, row["locator"], name))

    def set_color_selected(self, color: str):
        rows = self.chosen_layers(strict=True)
        if not rows:
            return
        self._apply("图层颜色标签", lambda task: sum(int(lm.set_color(task, r["locator"], color)) for r in rows))

    def delete_selected(self):
        rows = self.chosen_layers(strict=True)
        if not rows:
            return
        n = len(rows)
        if self._apply("删除 %d 个图层" % n, lambda task: lm.delete(task, [r["locator"] for r in rows])):
            self.grid.ov_sel = []
            tile = self._tile
            if tile is not None:
                self.grid.video_sel = [v for v in getattr(self.grid, "video_sel", []) if v[0] != tile.idx]
            self.grid.status.emit("已删除 %d 个图层（Ctrl+Z 撤销）" % n)

    def _reorder(self, rows: list, before: dict | None):
        """拖到 before 那一行上面（None = 最底）。Q177：面板顺序就是时间线的轨道叠压顺序 ——
        拖到别的轨道的图层上面 = 把它所在的轨道挪到那条轨道上面；同一条轨道里只换先后。"""
        if not rows or self._tile is None:
            return
        if before is not None and any(lt._loc_key(r["locator"]) == lt._loc_key(before["locator"]) for r in rows):
            return
        if self._apply("调整叠放顺序", lambda task: lm.reorder_rows(task, rows, before)):
            self.grid.status.emit("叠放顺序已改（时间线上的轨道顺序一起变了，Ctrl+Z 撤销）")

    def restack_selected(self, how: str, label: str):
        """面板右键 / 快捷键：选中这一层所在的轨道上移 / 下移 / 置顶 / 置底（同一批的选中视频一起）。"""
        rows = self.chosen_layers(strict=True)
        tile = self._tile
        if len(rows) != 1 or tile is None:
            return 0
        ref = self.grid.layer_ref_from_row(tile, rows[0])
        if ref is None:
            return 0
        targets = self.grid.selected_tiles() or [tile]
        n = self.grid.layer_restack_tracks(self.grid.sibling_layers(ref, targets), how, label)
        self.sync()
        return n

    # ───── 右键 / 键盘 ─────

    def _menu(self, pos):
        idx = self.lst.indexAt(pos)
        if idx.isValid() and not self.lst.selectionModel().isSelected(idx):
            self.lst.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        rows = self.chosen_layers(strict=True)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if rows:
            any_hidden = any(r.get("hidden") for r in rows)
            any_audio = any(r.get("is_audio") for r in rows)
            menu.addAction(("显示" if any_hidden else "隐藏") + ("（静音）" if any_audio else "") + "\tSpace",
                           lambda: self.toggle_selected("hidden"))
            menu.addAction(("解锁" if all(r.get("locked") for r in rows) else "锁定") + "\tL",
                           lambda: self.toggle_selected("locked"))
            if len(rows) == 1:
                menu.addAction(("取消独奏" if self.is_solo(rows[0]) else "独奏") + "\tS",
                               lambda r=rows[0]: self.click_button(r, "solo"))
            menu.addSeparator()
            cm = menu.addMenu("颜色标签")
            for key, label in lm.COLOR_LABELS:
                act = cm.addAction(label)
                if key:
                    pm = QPixmap(ICON_SM, ICON_SM)
                    pm.fill(Qt.GlobalColor.transparent)
                    p = QPainter(pm)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setBrush(QColor(lm.LABEL_HEX[key]))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(2, 2, ICON_SM - 4, ICON_SM - 4)
                    p.end()
                    act.setIcon(pm)
                act.triggered.connect(lambda _c=False, k=key: self.set_color_selected(k))
            if len(rows) == 1:
                menu.addAction("重命名\tF2", lambda: self.lst.edit(self.model.index_of(rows[0]["locator"])))
            menu.addSeparator()
            self._add_folder_menu(menu, rows)
            self._add_video_layer_ops(menu, rows)
            self._add_transfer_menu(menu)
            menu.addSeparator()
            menu.addAction("删除\tDelete", self.delete_selected)
        else:
            menu.addAction("全选\tCtrl+A", self.lst.selectAll)
            self._add_transfer_menu(menu)
        if menu.actions():
            menu.exec(self.lst.viewport().mapToGlobal(pos))

    def _add_folder_menu(self, menu, rows):
        """编组。文件夹默认不改画面；眼睛 / 不透明度 / 混合离开默认才影响里面的层。"""
        from cutora.batch.layer_folders import set_blend, set_opacity, ungroup
        from cutora.ui.widgets.inspector import BLEND_ITEMS
        held = getattr(menu, "_transfer_subs", None)
        if held is None:
            held = []
            menu._transfer_subs = held
        plain = [r for r in rows if (r.get("locator") or {}).get("where") != "folder"]
        if len(plain) >= 2:
            menu.addAction("编组", self._group_selected)
        folders = [r for r in rows if (r.get("locator") or {}).get("where") == "folder"]
        if len(folders) != 1:
            return
        fid = (folders[0].get("locator") or {}).get("id")
        word = "展开分组" if folders[0].get("collapsed") else "折叠分组"
        menu.addAction(word, lambda i=fid: self._apply(
            "折叠分组", lambda task, x=i: __import__(
                "cutora.batch.layer_folders", fromlist=["toggle_collapsed"]).toggle_collapsed(task, x)))
        menu.addAction("取消编组", lambda i=fid: self._apply(
            "取消编组", lambda task, x=i: ungroup(task, x)))
        op = QMenu("组不透明度", menu)
        menu.addMenu(op)
        held.append(op)
        for pct in (100, 75, 50, 25, 0):
            op.addAction("%d%%" % pct, lambda _c=False, p=pct, i=fid: self._apply(
                "组不透明度", lambda task, x=i, v=p: set_opacity(task, x, int(round(v * 255 / 100.0)))))
        bm = QMenu("组混合", menu)
        menu.addMenu(bm)
        held.append(bm)
        for key, label in BLEND_ITEMS:
            bm.addAction(label, lambda _c=False, k=key, i=fid: self._apply(
                "组混合", lambda task, x=i, name=k: set_blend(task, x, name)))

    def _group_selected(self):
        rows = [r for r in self.chosen_layers(strict=True)
                if (r.get("locator") or {}).get("where") != "folder"]
        if len(rows) < 2:
            self.grid.status.emit("先选中两层以上再编组")
            return
        locs = [r["locator"] for r in rows]
        if self._apply("编组", lambda task: lm.group_layers(task, locs)):
            self.grid.status.emit("已编组。默认不改变画面；在组上改不透明度或混合才影响里面的层")

    def _add_video_layer_ops(self, menu, rows):
        """Q173：选中的是一条视频图层 → 翻转 / 混合 / 调色 / 叠放顺序 / 属性，作用于选中的每条视频里同一批的那一层。
        Q177：文字 / 图片 / 调整图层 → 它自己的属性 + 叠放顺序（时间线轨道）+ RMBG-2.0 一键抠图。"""
        from cutora.ui.ai_studio_room import has_rmbg_original, restore_rmbg_original_ui, run_rmbg_cutout_ui
        tile = self._current_tile() if hasattr(self, "_current_tile") else None
        if tile is not None:
            img_rows = [r for r in rows if r.get("kind") == "image"]
            if img_rows:
                targets = self.grid.selected_tiles() or [tile]
                if tile not in targets:
                    targets = [tile] + list(targets)
                ovs_to_cut = []
                for r in img_rows:
                    ref_i = self.grid.layer_ref_from_row(tile, r)
                    if ref_i is not None:
                        for _t, o, _tr in self.grid.sibling_layers(ref_i, targets):
                            if isinstance(o, dict) and o not in ovs_to_cut:
                                ovs_to_cut.append(o)
                if ovs_to_cut:
                    menu.addAction(
                        "✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级)",
                        lambda ovs=list(ovs_to_cut), tg=list(targets): run_rmbg_cutout_ui(
                            overlays=ovs, monitor_grid=self.grid, monitor_tiles=tg, parent=self
                        ),
                    )
                    if any(has_rmbg_original(o) for o in ovs_to_cut):
                        menu.addAction(
                            "↩️ 还原抠图前原图",
                            lambda ovs=list(ovs_to_cut), tg=list(targets): restore_rmbg_original_ui(
                                overlays=ovs, monitor_grid=self.grid, monitor_tiles=tg
                            ),
                        )
                    menu.addSeparator()
        if len(rows) != 1:
            return
        loc = rows[0].get("locator") or {}
        if tile is not None and loc.get("where") == "overlay":
            ref = self.grid.layer_ref_from_row(tile, rows[0])
            if ref is not None:
                targets = self.grid.selected_tiles() or [tile]
                if tile not in targets:
                    targets = [tile] + list(targets)
                self.grid.add_layer_props_menu(menu, ref, targets)
                menu.addSeparator()
            return
        if loc.get("where") != "clip" or tile is None:
            return
        tl = mm.timeline_of(tile.task)
        track = next((t for t in getattr(tl, "tracks", None) or []
                      if str(getattr(t, "id", "")) == str(loc.get("track_id"))), None)
        if track is None or getattr(track, "type", "") != "video":
            return
        cid = getattr(track, "split_cell", None) if mm.split_on(tile.task) else None
        targets = self.grid.selected_tiles() or [tile]
        if tile not in targets:
            targets = [tile] + list(targets)
        self.grid.add_layer_ops_menu(menu, tile, track, cid, targets)
        menu.addAction(
            "✂️ AI 抠图当前画面帧 (RMBG-2.0 发丝级)",
            lambda t=tile: run_rmbg_cutout_ui(
                task=t.task, frame_sec=float(getattr(t, "t", 0.0) or 0.0),
                monitor_grid=self.grid, monitor_tiles=[t], parent=self
            ),
        )
        menu.addSeparator()

    def handle_key(self, ev) -> bool:
        key = ev.key()
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Space:
            self.toggle_selected("hidden")
            return True
        if key == Qt.Key.Key_L and not ctrl:
            self.toggle_selected("locked")
            return True
        if key == Qt.Key.Key_S and not ctrl:
            rows = self.chosen_layers(strict=True)
            if len(rows) == 1:
                self.click_button(rows[0], "solo")
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
        if key == Qt.Key.Key_F and ctrl:
            self.search.setFocus()
            self.search.selectAll()
            return True
        if key == Qt.Key.Key_Escape:
            if self.search.text():
                self.search.clear()
            else:
                self.lst.clearSelection()
            return True
        if key == Qt.Key.Key_Z and ctrl:
            (self.grid.redo if mods & Qt.KeyboardModifier.ShiftModifier else self.grid.undo)()
            return True
        if key == Qt.Key.Key_Y and ctrl:
            self.grid.redo()
            return True
        if key == Qt.Key.Key_A and ctrl:
            self.lst.selectAll()
            return True
        if ctrl and key in (Qt.Key.Key_BracketRight, Qt.Key.Key_BracketLeft):
            # Q177：Photoshop 的习惯 —— Ctrl+] 上移一层、Ctrl+[ 下移一层，加 Shift = 置顶 / 置底
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            up = key == Qt.Key.Key_BracketRight
            how = ("top" if up else "bottom") if shift else ("up" if up else "down")
            self.restack_selected(how, {"up": "上移一层", "down": "下移一层", "top": "置顶", "bottom": "置底"}[how])
            return True
        if key == Qt.Key.Key_C and ctrl:
            self._copy("all")
            return True
        if key == Qt.Key.Key_V and ctrl:
            self._paste(None)
            return True
        return False

    def event(self, ev):
        if ev.type() == QEvent.Type.ShortcutOverride and ev.key() == Qt.Key.Key_F \
                and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            ev.accept()
            self.search.setFocus()
            self.search.selectAll()
            return True
        return super().event(ev)

    # ───── 跨视频复制（Q149）─────

    def _add_transfer_menu(self, menu):
        """复制 / 粘贴收到右键里。粘贴分「选中的视频」和「这一批全部」。"""
        # 子菜单挂在父菜单上。局部变量被回收时，PySide 会把 C++ 对象一起删掉。
        held = getattr(menu, "_transfer_subs", None)
        if held is None:
            held = []
            menu._transfer_subs = held
        tips = {
            "material": "按原来的起止时间复制。对方这个时间已有内容就新开一条轨道",
            "style": "只复制外观和声音属性，贴到对方同一种、同一序号的那一层",
            "all": "连文字、时间和素材来源一起复制，贴到对应的那一层",
        }
        # addMenu(标题) 返回的包装，过一会儿 QAction.menu() 会说对象已删。
        # 先建好、交给这个菜单当父对象，再 addMenu(菜单)，包装才是同一个。
        copy = QMenu("复制到其他视频", menu)
        menu.addMenu(copy)
        held.append(copy)
        for text, mode in (("复制素材", "material"), ("复制属性", "style"), ("复制全部", "all")):
            act = copy.addAction(text, lambda _c=False, m=mode: self._copy(m))
            act.setToolTip(tips[mode])
        for title, all_tiles in (("粘贴到选中的视频", False), ("粘贴到全部视频", True)):
            sub = QMenu(title, menu)
            menu.addMenu(sub)
            held.append(sub)
            for text, mode in (("粘贴素材", "material"), ("粘贴属性", "style"), ("粘贴全部", "all")):
                tip = tips[mode]
                if all_tiles:
                    tip = "这一批视频全部都贴。" + tip
                act = sub.addAction(
                    text, lambda _c=False, m=mode, a=all_tiles: self._paste(m, all_tiles=a))
                act.setToolTip(tip)

    def _copy(self, mode):
        tile = self._tile
        if tile is None:
            self.grid.status.emit("先点一条视频")
            return
        layers = [r for r in self.chosen_layers()
                  if (r.get("locator") or {}).get("where") != "folder"]
        self.grid.copy_layers(tile, layers, mode)

    def _paste(self, mode, all_tiles=False):
        self.grid.paste_layers(mode, all_tiles=bool(all_tiles))

