# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q170 · 专业的字体选择器：每个字体用它自己的字形显示、可搜索、有最近使用、底下有大样张 —— 而且不卡。

不卡的关键只有一条：**界面线程上永远不解字体、不排版、不画预览**。

* 列表里每一行「用自己字形写的家族名」是一张小图，由后台线程（``FontPreviewRenderer``）画好交回来；
  没画好之前这一行先用界面字体写名字，画好了再换上去 —— 滚动永远是即时的。
* 底部样张（当前选中文字用悬停的那个字体写出来）同样在后台画。
* 字体覆盖（这个字体缺不缺当前语种的字）查预先算好的 JSON，不解 cmap。
* 选中以后才把家族名写回 QComboBox（走原来的 currentTextChanged），字体选择器本身不碰工程数据。

弹窗是 ``Qt.Popup``：点外面就关；↑↓ 换行、Enter 确定、Esc 取消、直接打字就是搜索。
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QRect, QRectF, QSize, QThread, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QLineEdit, QListView, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.ui.i18n import tr

_LOG = get_logger("cutora.ui.font_picker")

ROW_H = 30
NAME_PX = 17               # 列表里家族名的字号
SAMPLE_PX = 28             # 底部样张字号
LIST_W = 360
MAX_RECENT = 8
DEFAULT_SAMPLE = "The quick brown fox · 片语 Studio 字幕 1234"
ROLE_FAMILY = Qt.ItemDataRole.UserRole + 1
ROLE_HEADER = Qt.ItemDataRole.UserRole + 2
ROLE_NOSCRIPT = Qt.ItemDataRole.UserRole + 3
ROLE_HEALTH = Qt.ItemDataRole.UserRole + 4     # 字体体检：blocked / checking / slow / ok / ""


def _health(family: str) -> str:
    from cutora.ui import font_probe
    return font_probe.health(family)


# ───────────────────────────── 后台预览渲染 ─────────────────────────────

class FontPreviewRenderer(QThread):
    """一条线程画所有预览图。请求放进集合，最新的先画（用户正在看的那几行最先出来）。"""

    ready = Signal(str, QImage)        # (key, image)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cv = threading.Condition()
        self._queue: "OrderedDict[str, tuple]" = OrderedDict()
        self._stop = False
        self.setObjectName("cutora-font-preview")

    def request(self, key: str, family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> None:
        with self._cv:
            self._queue.pop(key, None)
            self._queue[key] = (family, text, px, fg, dpr, max_w)
            self._cv.notify()

    def forget(self, prefix: str) -> None:
        with self._cv:
            for k in [k for k in self._queue if k.startswith(prefix)]:
                self._queue.pop(k, None)

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def run(self) -> None:  # noqa: D401
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                key, job = self._queue.popitem(last=True)      # 最新请求优先
            try:
                img = render_preview(*job)
            except Exception:
                _LOG.exception("字体预览渲染失败 %s", key)
                img = QImage()
            self.ready.emit(key, img)


def render_preview(family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> QImage:
    """在任意线程里画一张透明底的文字图（Qt 6 的光栅引擎支持线程里排版 / 画字）。"""
    from cutora.ui.panels.fonts import get_font_manager
    font = get_font_manager().get_font(family, int(px))
    fm = QFontMetricsF(font)
    text = str(text or "")
    if max_w > 0:
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, float(max_w))
    w = max(1, int(fm.horizontalAdvance(text) + 4))
    h = max(1, int(fm.height() + 2))
    if max_w > 0:
        w = min(w, int(max_w))
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(0)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font)
        p.setPen(QColor(fg))
        p.drawText(QRectF(2, 0, w - 2, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    finally:
        p.end()
    return img


class PreviewCache(QObject):
    """界面线程这边的缓存：key → QPixmap。没有就发请求，画好了 ``changed`` 通知刷新。"""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pending: set = set()
        self._cap = 900
        self._renderer = None

    def _thread(self) -> FontPreviewRenderer:
        if self._renderer is None:
            r = FontPreviewRenderer()
            r.ready.connect(self._on_ready)
            r.start(QThread.Priority.LowPriority)
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.shutdown)
            import atexit
            atexit.register(self.shutdown)      # 没走 aboutToQuit 的退出（测试 / 异常）也要先停线程，否则 Qt 直接 abort
            self._renderer = r
        return self._renderer

    def shutdown(self) -> None:
        r = self._renderer
        if r is not None:
            r.stop()
            r.wait(1500)
            self._renderer = None

    @staticmethod
    def key(family: str, text: str, px: int, fg: str, dpr: float, max_w: int) -> str:
        return "%s|%d|%s|%.2f|%d|%s" % (family, px, fg, dpr, max_w, text)

    def get(self, family: str, text: str, px: int, fg: str, dpr: float, max_w: int):
        k = self.key(family, text, px, fg, dpr, max_w)
        hit = self._pix.get(k)
        if hit is not None:
            self._pix.move_to_end(k)
            return hit
        if k not in self._pending:
            self._pending.add(k)
            self._thread().request(k, family, text, px, fg, dpr, max_w)
        return None

    def _on_ready(self, key: str, img: QImage) -> None:
        self._pending.discard(key)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(img.devicePixelRatio())
        while len(self._pix) >= self._cap:
            self._pix.popitem(last=False)
        self._pix[key] = pix
        self.changed.emit(key)

    def clear(self) -> None:
        """体检结果变了（某个字体从「体检中」变成可用 / 停用）→ 预览全部重画（只重画看得见的那几行）。"""
        self._pix.clear()


_CACHE = {"obj": None}


def preview_cache() -> PreviewCache:
    if _CACHE["obj"] is None:
        _CACHE["obj"] = PreviewCache()
    return _CACHE["obj"]


# ───────────────────────────── 最近使用 ─────────────────────────────

def _recent_path() -> str:
    from cutora.core.paths import settings_file
    return settings_file("font_recent.json")


def load_recent() -> list:
    try:
        with open(_recent_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return [str(x) for x in data if isinstance(x, str)][:MAX_RECENT]
    except Exception:
        return []


def push_recent(family: str) -> list:
    fam = str(family or "").strip()
    if not fam:
        return load_recent()
    rows = [r for r in load_recent() if r != fam]
    rows.insert(0, fam)
    rows = rows[:MAX_RECENT]
    try:
        path = _recent_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    except Exception:
        pass
    return rows


# ───────────────────────────── 模型 / 委托 ─────────────────────────────

class FontListModel(QAbstractListModel):
    def __init__(self, families, recent, no_script, parent=None):
        super().__init__(parent)
        self._all = list(families)
        self._recent = [r for r in recent if r in set(self._all)]
        self._no_script = set(no_script or ())
        self._rows: list = []
        self._filter = ""
        self.rebuild()

    def set_filter(self, text: str) -> None:
        self._filter = str(text or "").strip().lower()
        self.rebuild()

    def rebuild(self) -> None:
        self.beginResetModel()
        rows = []
        if self._filter:
            q = self._filter
            hits = [f for f in self._all if q in f.lower()]
            # 开头匹配的排前面
            hits.sort(key=lambda f: (0 if f.lower().startswith(q) else 1, f.lower()))
            rows.extend(("font", f) for f in hits)
        else:
            if self._recent:
                rows.append(("header", tr("最近使用")))
                rows.extend(("font", f) for f in self._recent)
                rows.append(("header", tr("全部字体（%d）") % len(self._all)))
            rows.extend(("font", f) for f in self._all)
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, val = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return val
        if role == ROLE_HEADER:
            return kind == "header"
        if role == ROLE_FAMILY:
            return val if kind == "font" else ""
        if role == ROLE_NOSCRIPT:
            return kind == "font" and val in self._no_script
        if role == ROLE_HEALTH:
            return _health(val) if kind == "font" else ""
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        kind, val = self._rows[index.row()]
        if kind == "header" or _health(val) == "blocked":
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def row_of(self, family: str, prefer_all: bool = True) -> int:
        found = [i for i, (k, v) in enumerate(self._rows) if k == "font" and v == family]
        if not found:
            return -1
        return found[-1] if prefer_all else found[0]

    def is_font_row(self, row: int) -> bool:
        """能选的字体行（不是分组标题、也不是体检停用的字体）。"""
        return (0 <= row < len(self._rows) and self._rows[row][0] == "font"
                and _health(self._rows[row][1]) != "blocked")


class FontRowDelegate(QStyledItemDelegate):
    def __init__(self, picker, parent=None):
        super().__init__(parent)
        self._picker = picker

    def sizeHint(self, option, index):  # noqa: N802
        if index.data(ROLE_HEADER):
            return QSize(LIST_W, 24)
        return QSize(LIST_W, ROW_H)

    def paint(self, painter, option, index):  # noqa: N802
        from cutora.ui.theme.tokens import current
        tok = current()
        r = option.rect
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if index.data(ROLE_HEADER):
                painter.fillRect(r, QColor(tok.bg1))
                f = painter.font()
                f.setPixelSize(11)
                f.setBold(True)
                painter.setFont(f)
                painter.setPen(QColor(tok.fg2))
                painter.drawText(r.adjusted(12, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
                return
            fam = str(index.data(ROLE_FAMILY) or "")
            selected = bool(option.state & option.state.__class__.State_Selected)
            hovered = (self._picker.hover_row == index.row())
            is_current = (fam == self._picker.current_family)
            if selected:
                bg = QColor(tok.accent)
                bg.setAlpha(60)
                painter.fillRect(r, bg)
            elif hovered:
                bg = QColor(tok.fg0)
                bg.setAlpha(14)
                painter.fillRect(r, bg)
            if is_current:
                painter.fillRect(QRect(r.left(), r.top() + 6, 3, r.height() - 12), QColor(tok.accent))
            no_script = bool(index.data(ROLE_NOSCRIPT))
            health = str(index.data(ROLE_HEALTH) or "")
            fg = tok.fg2 if (no_script or health in ("blocked", "checking")) else tok.fg0
            text_x = r.left() + 14
            tag_w = 0
            tag, tag_col = "", tok.fg2
            if health == "blocked":
                tag, tag_col = tr("会卡死·已停用"), tok.err
            elif health == "checking":
                tag = tr("体检中…")
            elif no_script:
                tag = tr("缺本语种字符")
            elif health == "slow":
                tag, tag_col = tr("较慢"), tok.warn
            if tag:
                f = painter.font()
                f.setPixelSize(11)
                painter.setFont(f)
                tw = painter.fontMetrics().horizontalAdvance(tag) + 10
                tag_w = tw + 8
                tag_rect = QRectF(r.right() - tw - 8, r.top() + (r.height() - 16) / 2, tw, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                c = QColor(tag_col)
                c.setAlpha(40)
                painter.setBrush(c)
                painter.drawRoundedRect(tag_rect, 8, 8)
                painter.setPen(QColor(tag_col))
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag)
            elif is_current:
                f = painter.font()
                f.setPixelSize(12)
                painter.setFont(f)
                painter.setPen(QColor(tok.accent))
                painter.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "✓")
                tag_w = 20
            max_w = max(40, r.width() - (text_x - r.left()) - tag_w - 6)
            # 停用 / 还在体检的字体不拿它自己的字形画（画了也是默认字体，而且停用的就是一画就卡）
            pix = (None if health in ("blocked", "checking")
                   else self._picker.cache.get(fam, fam, NAME_PX, fg, self._picker.dpr, max_w))
            if pix is not None and not pix.isNull():
                ph = pix.height() / max(1.0, pix.devicePixelRatio())
                painter.drawPixmap(QPoint(text_x, int(r.top() + (r.height() - ph) / 2)), pix)
            else:
                # 还没画好：先用界面字体写名字（不等、不卡），画好了会刷新这一行
                f = painter.font()
                f.setPixelSize(13)
                painter.setFont(f)
                painter.setPen(QColor(fg))
                painter.drawText(QRect(text_x, r.top(), int(max_w), r.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 painter.fontMetrics().elidedText(fam, Qt.TextElideMode.ElideRight, int(max_w)))
        finally:
            painter.restore()


class SamplePane(QWidget):
    """底部样张：悬停 / 选中的字体写当前文字。"""

    def __init__(self, picker, parent=None):
        super().__init__(parent)
        self._picker = picker
        self.family = ""
        self.setFixedHeight(SAMPLE_PX + 30)

    def set_family(self, fam: str) -> None:
        if fam != self.family:
            self.family = fam
            self.update()

    def paintEvent(self, ev):  # noqa: N802
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(tok.bg2))
            p.setPen(QPen(QColor(tok.line), 1))
            p.drawLine(0, 0, self.width(), 0)
            if not self.family:
                return
            f = p.font()
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(tok.fg2))
            p.drawText(QRect(12, 4, self.width() - 24, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self.family)
            health = _health(self.family)
            if health in ("blocked", "checking"):
                f.setPixelSize(13)
                p.setFont(f)
                p.setPen(QColor(tok.err if health == "blocked" else tok.fg2))
                msg = (tr("这个字体在这台电脑上一渲染就会卡死，已停用。老工程里用到它的地方暂用默认字体显示。")
                       if health == "blocked" else tr("正在后台体检这个字体（彩色 / 多轴可变字体），几秒后就能预览。"))
                p.drawText(QRect(12, 20, self.width() - 24, self.height() - 24),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, msg)
                return
            pix = self._picker.cache.get(self.family, self._picker.sample_text, SAMPLE_PX, tok.fg0,
                                         self._picker.dpr, self.width() - 24)
            if pix is not None and not pix.isNull():
                ph = pix.height() / max(1.0, pix.devicePixelRatio())
                p.drawPixmap(QPoint(12, int(18 + (self.height() - 18 - ph) / 2)), pix)
        finally:
            p.end()


# ───────────────────────────── 弹窗 ─────────────────────────────

class FontPickerPopup(QFrame):
    picked = Signal(str)

    def __init__(self, families, current_family: str, sample_text: str = "", no_script=(), parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("fontPicker")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.current_family = str(current_family or "")
        self.sample_text = (str(sample_text or "").replace("\n", " ").strip() or DEFAULT_SAMPLE)[:48]
        self.hover_row = -1
        self.cache = preview_cache()
        self.dpr = float(self.devicePixelRatioF() or 1.0)
        self._accepted = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(70)
        self._hover_timer.timeout.connect(self._apply_hover)
        self._hover_pending = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.search = QLineEdit(self)
        self.search.setObjectName("fontPickerSearch")
        self.search.setPlaceholderText(tr("搜索 %d 个字体…") % len(families))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_filter)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.model = FontListModel(families, load_recent(), no_script, self)
        self.view = QListView(self)
        self.view.setObjectName("fontPickerList")
        self.view.setModel(self.model)
        self.view.setItemDelegate(FontRowDelegate(self, self.view))
        self.view.setUniformItemSizes(False)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.clicked.connect(self._on_clicked)
        self.view.entered.connect(self._on_entered)
        self.view.viewport().installEventFilter(self)
        lay.addWidget(self.view, 1)
        self.sample = SamplePane(self, self)
        lay.addWidget(self.sample)
        self.cache.changed.connect(self._on_preview_ready)
        try:
            from cutora.ui import font_probe
            font_probe.qt_bridge().changed.connect(self._on_probe_changed)
        except Exception:
            pass
        self.setFixedWidth(LIST_W)
        self._select_family(self.current_family, scroll=True)
        self.sample.set_family(self.current_family)
        self.search.setFocus()

    # ---- 数据 ----
    def _select_family(self, fam: str, scroll: bool = False) -> None:
        row = self.model.row_of(fam, prefer_all=True) if fam else -1
        if row < 0:
            row = next((i for i in range(self.model.rowCount()) if self.model.is_font_row(i)), -1)
        if row >= 0:
            idx = self.model.index(row, 0)
            self.view.setCurrentIndex(idx)
            if scroll:
                self.view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self._select_family(self.current_family, scroll=False)
        self.view.scrollToTop()
        idx = self.view.currentIndex()
        if idx.isValid():
            self.sample.set_family(str(idx.data(ROLE_FAMILY) or ""))

    def _on_probe_changed(self) -> None:
        self.cache.clear()
        self.view.viewport().update()
        self.sample.update()

    def _on_preview_ready(self, key: str) -> None:
        self.view.viewport().update()
        self.sample.update()

    # ---- 交互 ----
    def _on_entered(self, index) -> None:
        row = index.row() if index.isValid() else -1
        if row != self.hover_row:
            self.hover_row = row
            self.view.viewport().update()
        fam = str(index.data(ROLE_FAMILY) or "") if index.isValid() else ""
        if fam:
            self._hover_pending = fam
            self._hover_timer.start()

    def _apply_hover(self) -> None:
        if self._hover_pending:
            self.sample.set_family(self._hover_pending)

    def _on_clicked(self, index) -> None:
        fam = str(index.data(ROLE_FAMILY) or "")
        if fam and _health(fam) != "blocked":
            self._accept(fam)

    def _accept(self, fam: str) -> None:
        self._accepted = True
        push_recent(fam)
        self.picked.emit(fam)
        self.close()

    def _move(self, delta: int) -> None:
        cur = self.view.currentIndex().row()
        n = self.model.rowCount()
        row = cur
        for _ in range(n):
            row += delta
            if row < 0 or row >= n:
                break
            if self.model.is_font_row(row):
                idx = self.model.index(row, 0)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                self.sample.set_family(str(idx.data(ROLE_FAMILY) or ""))
                return

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.search and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if k == Qt.Key.Key_Down else -1)
                return True
            if k in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._move(10 if k == Qt.Key.Key_PageDown else -10)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                idx = self.view.currentIndex()
                fam = str(idx.data(ROLE_FAMILY) or "") if idx.isValid() else ""
                if fam and _health(fam) != "blocked":
                    self._accept(fam)
                return True
            if k == Qt.Key.Key_Escape:
                self.close()
                return True
        if obj is self.view.viewport() and ev.type() == QEvent.Type.Leave:
            if self.hover_row != -1:
                self.hover_row = -1
                self.view.viewport().update()
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        # 焦点不在搜索框时打字也进搜索框
        self.search.setFocus()
        QApplication.sendEvent(self.search, ev)

    # ---- 定位 ----
    def show_below(self, anchor: QWidget) -> None:
        screen = anchor.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        top_left = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        h = min(560, int(avail.height() * 0.8))
        self.resize(LIST_W, h)
        x = min(max(avail.left(), top_left.x()), avail.right() - LIST_W)
        y = top_left.y()
        if y + h > avail.bottom():
            above = anchor.mapToGlobal(QPoint(0, 0)).y() - h - 2
            y = above if above >= avail.top() else max(avail.top(), avail.bottom() - h)
        self.move(x, y)
        self.show()
        self.search.setFocus()


def open_font_picker(anchor: QWidget, families, current_family: str, on_pick, sample_text: str = "",
                     no_script=()) -> FontPickerPopup:
    pop = FontPickerPopup(list(families), current_family, sample_text, no_script, parent=anchor.window())
    pop.picked.connect(on_pick)
    pop.show_below(anchor)
    return pop
