# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 6281-6470

import os

from PySide6.QtWidgets import (
    QWidget,
    QToolTip,
    QMenu,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QRect,
    QRectF,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QFont,
    QPen,
)
from cutora.ui.theme import tokens as theme_tokens
from cutora.render.track_transform import track_is_session_pinned
from cutora.model.track_guard import track_is_muted

def track_supports_batch(*a, **k):
    from cutora.ui.main_window import track_supports_batch as _impl
    return _impl(*a, **k)

# --- Track Header Panel (left side of timeline) ---
class TrackHeaderPanel(QWidget):
    """NLE Gen-2: 动态轨道头面板，从 timeline.tracks 遍历，每个 Track 一行。"""
    track_action = Signal(object, str)  # (Track 对象, action)

    PANEL_W = 214
    BTN_SIZE = 16
    BTN_GAP = 2
    # Q169：最左边一条轨道色的「把手」：拖它上下挪轨道（也是这条轨道的颜色标）
    GRIP_W = 14
    NAME_X = GRIP_W + 4
    NAME_W = 44
    BTN_X0 = NAME_X + NAME_W + 4
    DRAG_START_PX = 4
    BTN_DEFS = [
        ("pin", "pin", "图钉（批量编辑，会话，不存工程）"),
        ("arrow-left-right", "batch", "轨道共享（批量同步）"),
        ("eye", "eye", "显示 / 隐藏轨道"),
        ("M", "mute", "静音"),
        ("S", "solo", "独奏（会话，不存工程）"),
        ("lock", "lock", "锁定"),
        ("plus", "import", "导入素材到此轨"),
    ]

    def __init__(self, timeline_widget, parent=None):
        super().__init__(parent)
        self.timeline = timeline_widget
        self.setFixedWidth(self.PANEL_W)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)  # Q115：素材可以直接丢到轨道头上
        self._drop_row_id = None
        self._drag_track = None
        self._drag_press_y = 0.0
        self._drag_live = False          # 真的拖起来了（超过阈值），画插入线
        self._drag_target = None         # (Track, after: bool) 或 None
        self._drag_line_y = None
        self._hover_grip_id = None
        self._height_track = None
        self._height_start_y = 0.0
        self._height_start_h = 0.0
        self._height_orig = {}
        self._height_all = False
        self._scroll_y = 0
        self._btn_rects = []  # list of (QRectF, Track_obj, action, tooltip, icon)
        self._name_rects = []  # list of (QRectF, Track_obj)
        self._hover_name_id = None
        self._pressed_name_id = None
        self._icon_pm = {}

    @staticmethod
    def _tid(t_obj) -> str:
        return str(getattr(t_obj, "id", "") or "")

    def _icon_pixmap(self, name, color, size=12):
        key = (name, color, int(size))
        cached = self._icon_pm.get(key)
        if cached is not None:
            return cached
        from cutora.ui.icons import pixmap as lucide_pixmap
        pm = lucide_pixmap(name, color=color, size=int(size))
        self._icon_pm[key] = pm
        return pm

    def set_scroll_y(self, y):
        self._scroll_y = y
        self.update()

    def paintEvent(self, event):
        painter = None
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            t = theme_tokens.current()
            painter.fillRect(self.rect(), QColor(t.bg0))

            positions = self.timeline._track_positions()
            rows = positions.get("rows", [])
            self._btn_rects = []
            self._name_rects = []

            font_label = painter.font()
            font_label.setPixelSize(theme_tokens.FONT_MICRO)

            # 构建每类轨道的序号映射（Video 1, Video 2, ...）
            _type_counters = {}
            _track_labels = {}
            for (t_obj, _, _) in rows:
                t_type = getattr(t_obj, "type", "")
                _type_counters.setdefault(t_type, 0)
                _type_counters[t_type] += 1
                _track_labels[getattr(t_obj, "id", None)] = _type_counters[t_type]
            # 只有同类型 > 1 条时才显示序号
            _type_totals = dict(_type_counters)

            ruler_h = int(getattr(self.timeline, "RULER_H", 24))
            painter.save()
            painter.setClipRect(QRect(0, ruler_h, self.width(), max(0, self.height() - ruler_h)))
            try:
                for (t_obj, track_y, track_h) in rows:
                    if track_y + track_h <= ruler_h or track_y >= self.height():
                        continue
                    t_type = getattr(t_obj, "type", "")
                    disp = self.timeline._TRACK_DISPLAY.get(t_type, {})
                    base_label = disp.get("label", t_type.capitalize())
                    idx = _track_labels.get(getattr(t_obj, "id", None), 1)
                    custom_name = str(getattr(t_obj, "name", "") or "").strip()
                    label = custom_name or (
                        f"{base_label} {idx}" if _type_totals.get(t_type, 1) > 1 else base_label)
                    row_rect = QRectF(0, track_y, self.PANEL_W, track_h)

                    is_pinned = track_is_session_pinned(self.timeline, t_obj)
                    is_hidden = not getattr(t_obj, 'visible', True)
                    is_muted = getattr(t_obj, "mute", False)
                    is_locked = getattr(t_obj, "locked", False)
                    solo_ids = getattr(self.timeline, "_solo_ids", set()) or set()
                    tid = str(getattr(t_obj, "id", ""))
                    is_solo = tid in solo_ids
                    solo_silenced = (not is_muted) and track_is_muted(
                        t_obj, task=getattr(self.timeline, "task_data", None),
                        solo_ids=solo_ids)
                    if is_hidden or solo_silenced:
                        painter.setOpacity(0.35)

                    # Q169：轨道色（和时间线上的色块同一个色）
                    track_hex = str(disp.get("color").name() if disp.get("color") is not None else t.track_video)
                    is_dragging_this = (self._drag_live and self._drag_track is t_obj)
                    if is_dragging_this:
                        painter.setOpacity(0.45)

                    # Row background：面板底色里掺一点轨道色，一行一个色相，远看就分得开
                    inner = row_rect.adjusted(2, 1, -2, -1)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(theme_tokens.mix_hex(t.bg2, track_hex, 0.18)))
                    painter.drawRoundedRect(inner, theme_tokens.RADIUS_CTL, theme_tokens.RADIUS_CTL)
                    # 把手：最左边一条实色，圆的只有外侧两个角
                    grip_rect = QRectF(inner.left(), inner.top(), self.GRIP_W, inner.height())
                    grip_path = QPainterPath()
                    grip_path.addRoundedRect(inner, theme_tokens.RADIUS_CTL, theme_tokens.RADIUS_CTL)
                    painter.save()
                    painter.setClipPath(grip_path)
                    grip_hover = (self._tid(t_obj) == self._hover_grip_id) or is_dragging_this
                    painter.fillRect(grip_rect, QColor(theme_tokens.lighten_hex(track_hex, 0.14 if grip_hover else 0.0)))
                    painter.restore()
                    pm = self._icon_pixmap("grip-vertical", theme_tokens.track_ink(track_hex, t.accent)
                                           if t_type == "adjust" else t.fg0, 12)
                    if pm is not None and not pm.isNull():
                        painter.save()
                        painter.setOpacity(painter.opacity() * (0.95 if grip_hover else 0.7))
                        painter.drawPixmap(QRect(int(grip_rect.x() + (self.GRIP_W - 12) / 2),
                                                 int(grip_rect.y() + (grip_rect.height() - 12) / 2), 12, 12), pm)
                        painter.restore()

                    # Q115：素材正拖在这条轨道头上 —— 描一圈金边告诉用户会落这里
                    if (self._drop_row_id is not None
                            and getattr(t_obj, "id", None) == self._drop_row_id):
                        hl = QColor(t.accent)
                        hl.setAlpha(36)
                        painter.setBrush(hl)
                        painter.drawRoundedRect(
                            row_rect.adjusted(2, 1, -2, -1),
                            theme_tokens.RADIUS_CTL, theme_tokens.RADIUS_CTL)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.setPen(QPen(QColor(t.accent), 1))
                        painter.drawRoundedRect(
                            row_rect.adjusted(2, 1, -2, -1),
                            theme_tokens.RADIUS_CTL, theme_tokens.RADIUS_CTL)
                        painter.setPen(Qt.PenStyle.NoPen)

                    # Track name button（轨道色底 + 白字；悬停亮、按下沉、选中态主题金描边）
                    painter.setFont(font_label)
                    name_rect = QRectF(self.NAME_X, track_y + 2, self.NAME_W, max(12, track_h - 4))
                    self._name_rects.append((name_rect, t_obj))
                    tid = str(getattr(t_obj, "id", "") or "")
                    is_hover = bool(tid) and tid == self._hover_name_id
                    is_press = bool(tid) and tid == self._pressed_name_id
                    if is_press:
                        name_bg = QColor(theme_tokens.darken_hex(track_hex, 0.22))
                    elif is_hover:
                        name_bg = QColor(theme_tokens.lighten_hex(track_hex, 0.16))
                    else:
                        name_bg = QColor(theme_tokens.darken_hex(track_hex, 0.08))
                    painter.setPen(Qt.PenStyle.NoPen)
                    if t_type == "adjust":      # Q174：和时间线上的段一样的拉丝金属
                        from cutora.ui.timeline.widget import metal_gradient
                        painter.setBrush(QBrush(metal_gradient(name_rect, name_bg.name())))
                    else:
                        painter.setBrush(name_bg)
                    painter.drawRoundedRect(name_rect, theme_tokens.RADIUS_SM, theme_tokens.RADIUS_SM)
                    edge = QColor(t.accent) if (is_hover or is_press) else QColor(theme_tokens.lighten_hex(track_hex, 0.30))
                    if not (is_hover or is_press):
                        edge.setAlpha(110)
                    painter.setPen(QPen(edge, 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(name_rect, theme_tokens.RADIUS_SM, theme_tokens.RADIUS_SM)
                    if t_type == "adjust":      # Q174 银灰底：主题色字
                        painter.setPen(QColor(theme_tokens.track_ink(name_bg.name(), t.accent)))
                    else:
                        painter.setPen(QColor(t.fg0))
                    name_text = ("📌 " if is_pinned else "") + label
                    painter.drawText(
                        name_rect.adjusted(3, 0, -2, 0),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        name_text)

                    split_cell = getattr(t_obj, "split_cell", None)
                    if split_cell:
                        badge = QRectF(name_rect.right() - 13, name_rect.y() + 2, 11, max(10, name_rect.height() - 4))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QColor(t.accent))
                        painter.drawRoundedRect(badge, theme_tokens.RADIUS_SM, theme_tokens.RADIUS_SM)
                        painter.setPen(QColor(t.bg0))
                        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(split_cell)[:1])

                    # Buttons
                    btn_y = track_y + (track_h - self.BTN_SIZE) / 2
                    x = self.BTN_X0
                    for icon_name, action, tooltip in self.BTN_DEFS:
                        # subs 轨道不显示导入按钮（text 轨道保留，用于创建文本）
                        if action == "import" and t_type == "subs":
                            continue
                        if action == "batch" and not track_supports_batch(t_obj):
                            continue
                        r = QRectF(x, btn_y, self.BTN_SIZE, self.BTN_SIZE)
                        self._btn_rects.append((r, t_obj, action, tooltip, icon_name))
                        is_active = (
                            (action == "pin" and is_pinned) or
                            (action == "eye" and is_hidden) or
                            (action == "batch" and getattr(t_obj, 'enable_batch', False)) or
                            (action == "mute" and is_muted) or
                            (action == "solo" and is_solo) or
                            (action == "lock" and is_locked)
                        )
                        if action == "eye" and is_hidden:
                            tint = t.err
                            bg_color = QColor(t.err)
                            bg_color.setAlpha(40)
                            border_color = QColor(t.err)
                        elif action == "mute" and is_muted:
                            tint = t.err
                            bg_color = QColor(t.err)
                            bg_color.setAlpha(40)
                            border_color = QColor(t.err)
                        elif action == "solo" and is_solo:
                            tint = t.accent
                            bg_color = QColor(t.accent)
                            bg_color.setAlpha(50)
                            border_color = QColor(t.accent)
                        elif action == "lock" and is_locked:
                            tint = t.warn if hasattr(t, "warn") else t.accent
                            bg_color = QColor(tint)
                            bg_color.setAlpha(40)
                            border_color = QColor(tint)
                        elif is_active:
                            tint = t.accent
                            bg_color = QColor(t.accent)
                            bg_color.setAlpha(40)
                            border_color = QColor(t.accent)
                        else:
                            tint = t.fg1
                            bg_color = QColor(t.bg3)
                            border_color = QColor(t.line)
                        if action == "lock":
                            draw_name = "lock" if is_locked else "lock-open"
                        elif action == "eye" and is_hidden:
                            draw_name = "eye-off"
                        else:
                            draw_name = icon_name
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(bg_color)
                        painter.drawRoundedRect(r, theme_tokens.RADIUS_SM, theme_tokens.RADIUS_SM)
                        painter.setPen(QPen(border_color, 1))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRoundedRect(r, theme_tokens.RADIUS_SM, theme_tokens.RADIUS_SM)
                        if action in ("mute", "solo"):
                            letter_font = painter.font()
                            letter_font.setPixelSize(theme_tokens.FONT_MICRO)
                            letter_font.setWeight(QFont.Weight(
                                theme_tokens.W_SEMI if is_active else theme_tokens.W_REGULAR))
                            painter.setFont(letter_font)
                            painter.setPen(QColor(tint))
                            painter.drawText(
                                r, Qt.AlignmentFlag.AlignCenter,
                                "M" if action == "mute" else "S")
                            painter.setFont(font_label)
                        else:
                            icon_px = 12
                            pm = self._icon_pixmap(draw_name, tint, icon_px)
                            if pm is not None and not pm.isNull():
                                ix = int(r.x() + (r.width() - icon_px) / 2)
                                iy = int(r.y() + (r.height() - icon_px) / 2)
                                painter.drawPixmap(QRect(ix, iy, icon_px, icon_px), pm)
                        x += self.BTN_SIZE + self.BTN_GAP

                    if is_hidden or solo_silenced or is_dragging_this:
                        painter.setOpacity(1.0)

                # Q169：拖轨道时的插入线（金色，整条横过去）
                if self._drag_live and self._drag_line_y is not None:
                    ly = float(self._drag_line_y)
                    painter.setPen(QPen(QColor(t.accent_hi), 2))
                    painter.drawLine(2, int(ly), self.PANEL_W - 2, int(ly))
                    painter.setBrush(QColor(t.accent_hi))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(QRectF(1, ly - 3, 6, 6))
                    painter.drawEllipse(QRectF(self.PANEL_W - 7, ly - 3, 6, 6))

                # Domain separator
                sep_y = positions.get("domain_sep_y", 0)
                if sep_y > 0:
                    sep_mid = int(sep_y + self.timeline.DOMAIN_SEP // 2)
                    painter.setPen(QPen(QColor(t.line), 1))
                    painter.drawLine(4, sep_mid, self.PANEL_W - 4, sep_mid)
            finally:
                painter.restore()
            painter.fillRect(QRect(0, 0, self.width(), ruler_h), QColor(t.bg0))
            painter.setPen(QPen(QColor(t.line), 1))
            painter.drawLine(0, ruler_h - 1, self.width(), ruler_h - 1)
        except Exception as e:
            print("[TrackHeaderPanel PaintError]", e)
        finally:
            if painter is not None:
                painter.end()

    # ------------------------------------------------------------------
    # Q115：把素材直接丢到轨道头上 = 导进这条轨道（落在播放头位置）
    # ------------------------------------------------------------------
    def _track_at(self, pos_y):
        rows = (self.timeline._track_positions() or {}).get("rows", []) or []
        for t_obj, row_y, row_h in rows:
            if row_y <= pos_y <= row_y + row_h:
                return t_obj
        return None

    def _set_drop_row(self, t_obj):
        tid = getattr(t_obj, "id", None)
        if tid != self._drop_row_id:
            self._drop_row_id = tid
            self.update()

    def dragEnterEvent(self, event):
        from cutora.ui.timeline_drop import supported_exts
        md = event.mimeData()
        if not md.hasUrls():
            event.ignore()
            return
        exts = supported_exts()
        for url in md.urls():
            name = url.toLocalFile()
            if name and os.path.splitext(name)[1].lower() in exts:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        pos = event.position() if hasattr(event, "position") else event.posF()
        self._set_drop_row(self._track_at(pos.y()))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._set_drop_row(None)
        try:
            event.accept()
        except AttributeError:
            pass

    def dropEvent(self, event):
        from cutora.ui.timeline_drop import sort_for_drop, supported_exts
        self._set_drop_row(None)
        md = event.mimeData()
        if not md.hasUrls():
            event.ignore()
            return
        pos = event.position() if hasattr(event, "position") else event.posF()
        track = self._track_at(pos.y())
        exts = supported_exts()
        paths = [u.toLocalFile() for u in md.urls()]
        paths = [p for p in paths
                 if p and os.path.splitext(p)[1].lower() in exts]
        if not paths:
            event.ignore()
            return
        host = self.window()
        handler = getattr(host, "_handle_timeline_drop_batch", None)
        if handler is None:
            event.ignore()
            return
        handler({
            "paths": sort_for_drop(paths),
            "time": max(0.0, float(getattr(self.timeline, "position", 0) or 0) / 1000.0),
            "domain": "audio" if getattr(track, "type", "") == "audio" else "visual",
            "track": track,
            "new_track": track is None,
        })
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        pos = event.position()
        for r, t_obj, action, tooltip, glyph in self._btn_rects:
            if r.contains(pos):
                self.track_action.emit(t_obj, action)
                return
        for r, t_obj in self._name_rects:
            if r.contains(pos):
                self._pressed_name_id = str(getattr(t_obj, "id", "") or "")
                self.update()
                return
        rows = self.timeline._track_positions().get("rows", [])
        for t_obj, row_y, row_h in rows:
            if abs(pos.y() - (row_y + row_h)) <= 5:
                self._height_track = t_obj
                self._height_start_y = float(pos.y())
                self._height_start_h = float(row_h)
                self._height_all = bool(
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                orig = {}
                for t in self.timeline._get_timeline_tracks():
                    tid = str(getattr(t, "id", "") or "")
                    orig[tid] = float(getattr(t, "height", row_h) or row_h)
                self._height_orig = orig
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                return
            if row_y <= pos.y() <= row_y + row_h:
                # 把手或行身都能拖；真正开始（画插入线）要等鼠标移开几个像素
                self._drag_track = t_obj
                self._drag_press_y = float(pos.y())
                self._drag_live = False
                self._drag_target = None
                self._drag_line_y = None
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._height_track is not None:
            dy = float(event.position().y()) - self._height_start_y
            new_h = self._height_start_h + dy
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._height_all = True
            live = getattr(self.timeline, "apply_live_track_height", None)
            if callable(live):
                live(self._height_track, new_h, all_tracks=self._height_all)
            else:
                setter = getattr(self.timeline, "set_track_height", None)
                if callable(setter):
                    setter(self._height_track, new_h)
            self.update()
            return
        if self._drag_track is not None:
            self._drag_update(float(event.position().y()))
            return
        pos = event.position()
        rows = self.timeline._track_positions().get("rows", [])
        on_handle = False
        grip_id = None
        for t_obj, row_y, row_h in rows:
            if abs(pos.y() - (row_y + row_h)) <= 5:
                on_handle = True
                break
            if row_y <= pos.y() <= row_y + row_h and pos.x() <= self.GRIP_W + 2:
                grip_id = self._tid(t_obj)
        if grip_id != self._hover_grip_id:
            self._hover_grip_id = grip_id
            self.update()
        if on_handle:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif grip_id:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            QToolTip.showText(event.globalPosition().toPoint(), "拖动调整轨道上下顺序（越靠上 = 画面里越在底下）", self)
            return
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        hover_id = None
        for r, t_obj in self._name_rects:
            if r.contains(pos):
                hover_id = str(getattr(t_obj, "id", "") or "")
                QToolTip.showText(event.globalPosition().toPoint(), "轨道属性", self)
                break
        if hover_id != self._hover_name_id:
            self._hover_name_id = hover_id
            self.update()
        if hover_id:
            return
        for r, t_obj, action, tooltip, glyph in self._btn_rects:
            if r.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        pos = event.position()
        for r, _t_obj, _action, _tooltip, _glyph in self._btn_rects:
            if r.contains(pos):
                return
        for r, _t_obj in self._name_rects:
            if r.contains(pos):
                return
        rows = self.timeline._track_positions().get("rows", [])
        for t_obj, row_y, row_h in rows:
            if row_y <= pos.y() <= row_y + row_h:
                cycle = getattr(self.timeline, "cycle_track_height", None)
                if callable(cycle):
                    cycle(t_obj)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self._height_track is not None:
            track = self._height_track
            final_h = float(getattr(track, "height", self._height_start_h) or self._height_start_h)
            orig = dict(self._height_orig or {})
            all_mode = bool(self._height_all)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                all_mode = True
            for t in self.timeline._get_timeline_tracks():
                tid = str(getattr(t, "id", "") or "")
                if tid in orig:
                    t.height = orig[tid]
            self._height_track = None
            self._height_orig = {}
            self._height_all = False
            self.unsetCursor()
            tid = str(getattr(track, "id", "") or "")
            start_h = orig.get(tid, self._height_start_h)
            changed = abs(final_h - float(start_h)) >= 0.5
            if all_mode and not changed:
                for t in self.timeline._get_timeline_tracks():
                    other = orig.get(str(getattr(t, "id", "") or ""))
                    if other is not None and abs(final_h - float(other)) >= 0.5:
                        changed = True
                        break
            if changed:
                commit = getattr(self.timeline, "commit_track_height", None)
                if callable(commit):
                    commit(track, final_h, all_tracks=all_mode)
                else:
                    setter = getattr(self.timeline, "set_track_height", None)
                    if callable(setter):
                        setter(track, final_h)
            else:
                refresh = getattr(self.timeline, "_refresh_track_height_view", None)
                if callable(refresh):
                    refresh()
                else:
                    self.timeline.update()
            self.update()
            return
        pressed = self._pressed_name_id
        self._pressed_name_id = None
        if pressed:
            pos = event.position()
            for r, t_obj in self._name_rects:
                if r.contains(pos) and str(getattr(t_obj, "id", "") or "") == pressed:
                    self.track_action.emit(t_obj, "name")
                    self.update()
                    return
            self.update()
            return
        drag = self._drag_track
        if drag is None:
            return super().mouseReleaseEvent(event)
        self._drag_update(float(event.position().y()))
        target = self._drag_target if self._drag_live else None
        live = self._drag_live
        self._drag_track = None
        self._drag_live = False
        self._drag_target = None
        self._drag_line_y = None
        self._set_timeline_drag_line(None)
        self.unsetCursor()
        self.update()
        if not live or target is None:
            return
        dst, after = target
        if dst is drag:
            return
        win = self.window()
        if hasattr(win, "reorder_timeline_track"):
            try:
                win.reorder_timeline_track(drag, dst, after=after)
            except TypeError:
                win.reorder_timeline_track(drag, dst)
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Q169：拖把手挪轨道 —— 插入线跟着鼠标，松手落到线的位置
    # ------------------------------------------------------------------
    def _drag_update(self, y: float) -> None:
        drag = self._drag_track
        if drag is None:
            return
        if not self._drag_live:
            if abs(y - self._drag_press_y) < self.DRAG_START_PX:
                return
            self._drag_live = True
        rows = self.timeline._track_positions().get("rows", [])
        domain = getattr(drag, "domain", "visual")
        same = [(t, ry, rh) for t, ry, rh in rows if getattr(t, "domain", "visual") == domain]
        target = None
        line_y = None
        if same:
            # 落在哪条轨道的上半 / 下半 → 插到它前面 / 后面；超出这一域的范围就贴到两头
            if y < same[0][1]:
                t, ry, rh = same[0]
                target, line_y = (t, False), ry
            elif y > same[-1][1] + same[-1][2]:
                t, ry, rh = same[-1]
                target, line_y = (t, True), ry + rh
            else:
                for t, ry, rh in same:
                    if ry - self.timeline.TRACK_GAP <= y <= ry + rh:
                        after = y > ry + rh / 2.0
                        target = (t, after)
                        line_y = (ry + rh) if after else ry
                        break
        # 插回原位（自己的上沿 / 下沿）不算移动：不画线
        if target is not None:
            t, after = target
            idx = [x[0] for x in same].index(t) if t in [x[0] for x in same] else -1
            di = [x[0] for x in same].index(drag) if drag in [x[0] for x in same] else -1
            if t is drag or (idx >= 0 and di >= 0 and ((after and idx == di - 1) or ((not after) and idx == di + 1))):
                target, line_y = None, None
        if line_y is not None:
            line_y = line_y - (self.timeline.TRACK_GAP / 2.0 if (target and not target[1]) else -self.timeline.TRACK_GAP / 2.0)
        changed = (target != self._drag_target) or (line_y != self._drag_line_y)
        self._drag_target = target
        self._drag_line_y = line_y
        self.setCursor(Qt.CursorShape.ClosedHandCursor if target is not None or not self._drag_live
                       else Qt.CursorShape.ForbiddenCursor)
        if changed:
            self._set_timeline_drag_line(line_y)
            self.update()

    def _set_timeline_drag_line(self, y) -> None:
        tl = self.timeline
        if getattr(tl, "_track_drag_line_y", None) != y:
            tl._track_drag_line_y = y
            tl.update()

    def contextMenuEvent(self, event):
        """右键：添加/删除轨道 + 混合模式。"""
        pos = event.pos()
        target_track = None
        positions = self.timeline._track_positions()
        for (t_obj, row_y, row_h) in positions.get("rows", []):
            if row_y <= pos.y() <= row_y + row_h:
                target_track = t_obj
                break
        win = self.window()
        if hasattr(win, "popup_track_menu"):
            win.popup_track_menu(event.globalPos(), target_track)
            return
        if target_track is None:
            return
        menu = QMenu(self)
        blend_menu = menu.addMenu("混合模式")
        current_blend = getattr(target_track, "blend_mode", "normal")
        for mode_name in ["normal", "multiply", "screen", "overlay", "additive"]:
            action = blend_menu.addAction(mode_name)
            action.setCheckable(True)
            action.setChecked(mode_name == current_blend)
            action.setData(mode_name)
        result = menu.exec(event.globalPos())
        if result and result.data():
            target_track.blend_mode = result.data()
            self.timeline.update()
            self.update()
            w = self.window()
            if hasattr(w, 'video_canvas'):
                w.video_canvas.update()
            if hasattr(w, 'update_preview'):
                w.update_preview()
