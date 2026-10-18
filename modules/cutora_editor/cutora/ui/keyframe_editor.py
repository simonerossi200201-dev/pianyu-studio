# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-A · 关键帧面板（Dope Sheet）+ 运动曲线编辑器（Graph Editor）。

时间线下方一个独立 dock（视图 → 关键帧编辑器，Alt+K）。跟着检查器里选中的对象走：
视频片段、音频片段、文本 / 图片层。

两种视图共用同一条时间轴和同一份选择：
- 关键帧：每个有动画的参数一行，最上面一行「全部」汇总。菱形形状 = 往后那一段的插值
  （◆ 线性、● 缓动 / 贝塞尔、■ 定格）。
- 曲线：参数的值随时间的曲线，选中关键帧露出贝塞尔手柄，拖手柄改快慢（y 可以拉出
  0–1 之外 = 过冲）。多条曲线时默认各自归一化，一条时显示真实数值。

操作（对标 After Effects / Resolve / Blender）：
- 点选，Shift 加选，Ctrl 反选；空白处拖框选；拖动整体平移（吸附到帧、播放头、其它关键帧，
  按住 Ctrl 临时不吸附）；Alt+拖首 / 尾一个 = 以另一端为中心缩放时距。
- 曲线里拖关键帧同时改时间和值（Shift 只改一个方向）；拖手柄默认两侧联动，Alt 断开。
- 双击空白处在那个时刻加一个关键帧（值取曲线上当时的值）；双击关键帧 = 播放头跳过去。
- F9 缓动、Shift+F9 缓入、Ctrl+Shift+F9 缓出；J / K 上 / 下一个关键帧；Delete 删除；
  Ctrl+C / Ctrl+V 复制粘贴到播放头；Ctrl+A 全选；Shift+F3 切换关键帧 / 曲线；Shift+Z 适应。
- Ctrl+滚轮缩放时间，滚轮平移；曲线里 Ctrl+Shift+滚轮缩放数值。

拖动时直接改对象（画面跟着动），松手时恢复原样再走一条 ``keyframe.replace`` 命令：
一个手势一次撤销，MCP / 命令行看得到同一条记录。
"""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.render import kf_edit as ke
from cutora.render.keyframe import (
    HIT_EPS, keyframes_of, path_label, path_unit, replace_paths, value_at,
)
from cutora.ui.theme.tokens import (
    FONT_LABEL, FONT_MICRO, SPACE_1, SPACE_2, current, hex_rgba,
)

_LOG = get_logger("cutora.ui.keyframe_editor")

GUTTER_W = 132          # 左侧参数名一栏
RULER_H = 22
ROW_H = 24
DIAMOND = 5.0           # 菱形半径
KEY_HALF = 4.0          # 曲线里关键帧方块的一半
HANDLE_R = 3.5
SNAP_PX = 6.0
CURVE_COLORS = ("#E07A5F", "#81B29A", "#7FA7D9", "#F2CC8F", "#B892D6", "#5FC8C0", "#D98BA3", "#A3B86C")
SUMMARY = "__all__"


# ───────────────────────────── 目标 ─────────────────────────────

class KfTarget:
    """编辑器正在看的那个对象。"""

    def __init__(self, host, params, start, dur, fps, title, task=None):
        self.host = host
        self.params = dict(params or {})
        self.start = float(start or 0.0)
        self.dur = max(0.0, float(dur or 0.0))
        self.fps = float(fps or 30.0) or 30.0
        self.title = str(title or "")
        self.task = task

    def same(self, other) -> bool:
        return other is not None and other.host is self.host


def resolve_target(window) -> KfTarget | None:
    """从检查器取当前对象。没有可打关键帧的对象 → None。"""
    panel = getattr(window, "prop_panel", None)
    if panel is None:
        return None
    try:
        host = panel._kf_host()
        if host is None:
            return None
        params = panel._kf_cmd_params(host)
    except Exception:
        _LOG.exception("取关键帧对象失败")
        return None
    if not params:
        return None
    task = None
    tasks = getattr(window, "tasks", None) or []
    ci = getattr(window, "curr_idx", -1)
    if 0 <= ci < len(tasks):
        task = tasks[ci]
    tl = task.get("timeline") if isinstance(task, dict) else None
    fps = float(getattr(tl, "fps", 30.0) or 30.0)
    if isinstance(host, dict):
        start = float(host.get("start") or 0.0)
        dur = float(host.get("end") or start) - start
        if host.get("type") == "text" or "content" in host:
            title = "文本 · %s" % (str(host.get("content") or "").replace("\n", " ").strip()[:16] or "文本")
        else:
            import os
            title = "图片 · %s" % (os.path.basename(str(host.get("path") or "")) or "图片")
    else:
        start = float(getattr(host, "start_t", 0.0) or 0.0)
        try:
            dur = float(host.effective_duration)
        except Exception:
            dur = float(getattr(host, "out_t", 0.0) or 0.0) - float(getattr(host, "in_t", 0.0) or 0.0)
        src = None
        sources = getattr(tl, "sources", None) or {}
        src = sources.get(getattr(host, "source_id", None))
        import os
        name = os.path.basename(str(getattr(src, "path", "") or "")) if src is not None else ""
        kind = "音频" if getattr(panel, "current_type", "") == "audio" else "视频"
        title = "%s · %s" % (kind, name or kind)
    return KfTarget(host, params, start, dur, fps, title, task=task)


# ───────────────────────────── 时间轴 ─────────────────────────────

class TimeAxis:
    """本地秒 ↔ 像素。两种视图共用一个。"""

    def __init__(self):
        self.t0 = -0.25         # 视口左边对应的本地秒
        self.pps = 120.0        # 每秒多少像素

    def x(self, t: float) -> float:
        return GUTTER_W + (float(t) - self.t0) * self.pps

    def t(self, x: float) -> float:
        return self.t0 + (float(x) - GUTTER_W) / self.pps

    def fit(self, dur: float, width: int) -> None:
        span = max(0.5, float(dur))
        usable = max(80.0, float(width) - GUTTER_W - 24.0)
        self.pps = max(4.0, min(4000.0, usable / span))
        self.t0 = -12.0 / self.pps

    def zoom(self, factor: float, about_x: float) -> None:
        t = self.t(about_x)
        self.pps = max(4.0, min(4000.0, self.pps * float(factor)))
        self.t0 = t - (about_x - GUTTER_W) / self.pps


def _tick_step(pps: float, fps: float) -> tuple[float, int]:
    """刻度间隔（秒）和每几个刻度标一次字。都取「好看」的间隔：1/2/5/10/15 帧、1/2/5/10/30/60 秒。"""
    frame = 1.0 / max(1.0, fps)
    nice = [frame * k for k in (1, 2, 5, 10)] + [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
    step = next((s for s in nice if s * pps >= 9.0), 60.0)
    for lab in nice:
        if lab < step - 1e-9 or lab * pps < 60.0:
            continue
        ratio = lab / step
        if abs(ratio - round(ratio)) < 1e-6:
            return step, max(1, int(round(ratio)))
    return step, max(1, int(round(60.0 / step)))


def fmt_time(t: float, fps: float) -> str:
    """整秒写「2s」，否则「秒:帧」（AE 的 0:00:01:05 去掉小时分钟）。"""
    t = max(0.0, float(t))
    if abs(t - round(t)) < 1e-6:
        return "%ds" % int(round(t))
    sec = int(math.floor(t + 1e-9))
    frame = int(round((t - sec) * fps))
    if frame >= int(round(fps)):
        sec, frame = sec + 1, 0
    return "%d:%02d" % (sec, frame)


# ───────────────────────────── 视图基类 ─────────────────────────────

class _TimeView(QWidget):
    """尺子 + 左栏 + 播放头；子类画内容、处理拖动。"""

    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(RULER_H + ROW_H * 3)
        self._drag = None

    @property
    def ax(self) -> TimeAxis:
        return self.ed.axis

    def body_rect(self) -> QRectF:
        return QRectF(GUTTER_W, RULER_H, max(1, self.width() - GUTTER_W), max(1, self.height() - RULER_H))

    def snap_t(self, t: float, *, free: bool = False, exclude=()) -> float:
        """吸附：先对齐到帧；不按 Ctrl 时再吸附到播放头 / 其它关键帧（6 px 内）。"""
        fps = self.ed.fps()
        t = round(float(t) * fps) / fps
        if free:
            return t
        cands = [self.ed.playhead_local()]
        for path, pts in self.ed.rows_points():
            for i, p in enumerate(pts):
                if (path, i) not in exclude:
                    cands.append(float(p["t"]))
        best, dist = t, SNAP_PX / self.ax.pps
        for c in cands:
            if abs(c - t) < dist:
                best, dist = c, abs(c - t)
        return best

    # ── 画 ──
    def paint_frame(self, p: QPainter) -> None:
        tok = current()
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(tok.bg0))
        tgt = self.ed.target
        # 片段时长以外的区域压暗
        if tgt is not None:
            x0, x1 = self.ax.x(0.0), self.ax.x(tgt.dur)
            p.fillRect(QRectF(GUTTER_W, RULER_H, max(0.0, x0 - GUTTER_W), h - RULER_H),
                       QColor(*hex_rgba(tok.bg1, 200)))
            if x1 < w:
                p.fillRect(QRectF(x1, RULER_H, w - x1, h - RULER_H), QColor(*hex_rgba(tok.bg1, 200)))
        # 尺子
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor(tok.bg1))
        p.setPen(QPen(QColor(tok.line), 1))
        p.drawLine(0, RULER_H, w, RULER_H)
        fps = self.ed.fps()
        step, label_every = _tick_step(self.ax.pps, fps)
        f = QFont(self.font())
        f.setPixelSize(FONT_MICRO)
        p.setFont(f)
        k0 = int(math.floor(self.ax.t(GUTTER_W) / step))
        k1 = int(math.ceil(self.ax.t(w) / step))
        for k in range(k0, k1 + 1):
            t = k * step
            x = self.ax.x(t)
            if x < GUTTER_W:
                continue
            major = (k % label_every) == 0
            p.setPen(QPen(QColor(tok.fg2 if major else tok.line), 1))
            p.drawLine(QPointF(x, RULER_H - (8 if major else 4)), QPointF(x, RULER_H))
            if major and t >= -1e-9:
                p.setPen(QColor(tok.fg2))
                p.drawText(QPointF(x + 3, RULER_H - 9), fmt_time(t, fps))
            if major:
                p.setPen(QPen(QColor(*hex_rgba(tok.line, 90)), 1))
                p.drawLine(QPointF(x, RULER_H), QPointF(x, h))

    def paint_playhead(self, p: QPainter) -> None:
        tok = current()
        x = self.ax.x(self.ed.playhead_local())
        if x < GUTTER_W:
            return
        p.setPen(QPen(QColor(tok.accent), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        tri = QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)])
        p.setBrush(QColor(tok.accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)

    def paint_gutter_bg(self, p: QPainter) -> None:
        tok = current()
        p.fillRect(QRectF(0, RULER_H, GUTTER_W, self.height() - RULER_H), QColor(tok.bg1))
        p.setPen(QPen(QColor(tok.line), 1))
        p.drawLine(GUTTER_W, 0, GUTTER_W, self.height())

    def paint_band(self, p: QPainter) -> None:
        d = self._drag
        if not d or d.get("kind") != "band" or not d.get("rect"):
            return
        tok = current()
        p.setPen(QPen(QColor(tok.accent), 1, Qt.PenStyle.DashLine))
        p.setBrush(QColor(*hex_rgba(tok.accent, 30)))
        p.drawRect(d["rect"])

    # ── 通用输入 ──
    def wheelEvent(self, ev):
        mods = ev.modifiers()
        dy = ev.angleDelta().y() or ev.angleDelta().x()
        if mods & Qt.KeyboardModifier.ControlModifier and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.ax.zoom(1.15 if dy > 0 else 1 / 1.15, ev.position().x())
        elif self.wheel_extra(ev, dy):
            return
        else:
            self.ax.t0 -= (dy / 120.0) * 60.0 / self.ax.pps
        self.ed.views_changed()
        ev.accept()

    def wheel_extra(self, ev, dy) -> bool:
        return False

    def ruler_press(self, ev) -> bool:
        if ev.position().y() <= RULER_H and ev.position().x() > GUTTER_W:
            self._drag = {"kind": "scrub"}
            self.ed.seek_local(self.snap_t(self.ax.t(ev.position().x()), free=True))
            return True
        return False

    def keyPressEvent(self, ev):
        if self.ed.handle_key(ev):
            ev.accept()
            return
        super().keyPressEvent(ev)

    def event(self, ev):
        # 焦点在编辑器里时，J / K / F9 / Delete 等归编辑器（全局 JKL 播放让路）
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.ShortcutOverride and self.ed.wants_key(ev):
            ev.accept()
            return True
        return super().event(ev)


# ───────────────────────────── 关键帧面板 ─────────────────────────────

class DopeSheet(_TimeView):
    def row_y(self, r: int) -> float:
        return RULER_H + r * ROW_H

    def rows(self):
        """[(path or SUMMARY, label)]，第 0 行是「全部」。"""
        out = [(SUMMARY, "全部")]
        for path, _pts in self.ed.rows_points():
            out.append((path, path_label(path)))
        return out

    def key_at(self, pos):
        """返回 (path, index) 或 (SUMMARY, t)。"""
        x, y = pos.x(), pos.y()
        if y <= RULER_H or x <= GUTTER_W:
            return None
        r = int((y - RULER_H) // ROW_H)
        rows = self.rows()
        if not (0 <= r < len(rows)):
            return None
        path = rows[r][0]
        if path == SUMMARY:
            for t in self.ed.summary_times():
                if abs(self.ax.x(t) - x) <= DIAMOND + 2:
                    return (SUMMARY, t)
            return None
        pts = self.ed.points(path)
        for i in range(len(pts) - 1, -1, -1):
            if abs(self.ax.x(float(pts[i]["t"])) - x) <= DIAMOND + 2:
                return (path, i)
        return None

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tok = current()
        self.paint_frame(p)
        self.paint_gutter_bg(p)
        f = QFont(self.font())
        f.setPixelSize(FONT_LABEL)
        rows = self.rows()
        sel = self.ed.selection
        for r, (path, label) in enumerate(rows):
            y = self.row_y(r)
            if r % 2 == 1:
                p.fillRect(QRectF(GUTTER_W, y, self.width() - GUTTER_W, ROW_H), QColor(*hex_rgba(tok.bg1, 90)))
            p.setPen(QPen(QColor(*hex_rgba(tok.line, 120)), 1))
            p.drawLine(QPointF(0, y + ROW_H), QPointF(self.width(), y + ROW_H))
            p.setFont(f)
            focus = path != SUMMARY and any(k[0] == path for k in sel)
            p.setPen(QColor(tok.fg0 if (path == SUMMARY or focus) else tok.fg1))
            if path != SUMMARY:
                color = self.ed.curve_color(path)
                p.setBrush(QColor(color))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(SPACE_2 + 4, y + ROW_H / 2), 3.5, 3.5)
                p.setPen(QColor(tok.fg0 if focus else tok.fg1))
            unit = path_unit(path) if path != SUMMARY else ""
            text = label + (("  " + unit) if unit else "")
            p.drawText(QRectF(SPACE_2 + 14, y, GUTTER_W - SPACE_2 - 18, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, GUTTER_W - 24))
            cy = y + ROW_H / 2
            p.save()
            p.setClipRect(QRectF(GUTTER_W, RULER_H, self.width() - GUTTER_W, self.height() - RULER_H))
            if path == SUMMARY:
                all_sel = self.ed.summary_selected()
                for t in self.ed.summary_times():
                    self._diamond(p, self.ax.x(t), cy, "summary", t in all_sel)
            else:
                pts = self.ed.points(path)
                # 段之间连一条细线，看得出哪里在动
                for i in range(len(pts) - 1):
                    a, b = float(pts[i]["t"]), float(pts[i + 1]["t"])
                    hold = str(pts[i].get("ease") or "") == "hold"
                    same = pts[i].get("v") == pts[i + 1].get("v")
                    if same:
                        continue
                    p.setPen(QPen(QColor(*hex_rgba(self.ed.curve_color(path), 90)), 2,
                                  Qt.PenStyle.DotLine if hold else Qt.PenStyle.SolidLine))
                    p.drawLine(QPointF(self.ax.x(a), cy), QPointF(self.ax.x(b), cy))
                for i, pt in enumerate(pts):
                    self._diamond(p, self.ax.x(float(pt["t"])), cy,
                                  str(pt.get("ease") or "linear"), (path, i) in sel)
            p.restore()
        self.paint_playhead(p)
        self.paint_band(p)
        if self.ed.target is None:
            self._empty(p)
        p.end()

    def _empty(self, p):
        tok = current()
        p.setPen(QColor(tok.fg2))
        p.drawText(self.body_rect(), Qt.AlignmentFlag.AlignCenter,
                   "选中一个视频、音频、文本或图片，在检查器里点参数旁的 ⏱ 打关键帧")

    def _diamond(self, p, x, y, ease, selected):
        tok = current()
        r = DIAMOND + (1.0 if selected else 0.0)
        fill = QColor(tok.accent) if selected else QColor(tok.fg1)
        p.setPen(QPen(QColor(tok.bg0), 1.2))
        p.setBrush(fill)
        if ease == "hold":
            p.drawRect(QRectF(x - r + 0.8, y - r + 0.8, 2 * r - 1.6, 2 * r - 1.6))
        elif ease in ("bezier", "ease_in", "ease_out", "ease_in_out"):
            p.drawEllipse(QPointF(x, y), r - 0.5, r - 0.5)
        elif ease == "summary":
            p.setBrush(QColor(tok.accent) if selected else QColor(tok.fg2))
            p.drawPolygon(QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)]))
        else:
            p.drawPolygon(QPolygonF([QPointF(x, y - r), QPointF(x + r, y), QPointF(x, y + r), QPointF(x - r, y)]))

    # ── 输入 ──
    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == Qt.MouseButton.RightButton:
            hit = self.key_at(ev.position())
            if hit is not None and not self.ed.is_selected(hit):
                self.ed.select([hit], mode="replace")
            self.ed.context_menu(self.mapToGlobal(ev.position().toPoint()))
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.ruler_press(ev):
            return
        mods = ev.modifiers()
        hit = self.key_at(ev.position())
        if ev.position().x() <= GUTTER_W:
            r = int((ev.position().y() - RULER_H) // ROW_H)
            rows = self.rows()
            if 0 <= r < len(rows):
                path = rows[r][0]
                keys = ([(pp, i) for pp, pts in self.ed.rows_points() for i in range(len(pts))]
                        if path == SUMMARY else [(path, i) for i in range(len(self.ed.points(path)))])
                self.ed.select(keys, mode="add" if mods & Qt.KeyboardModifier.ShiftModifier else "replace")
            return
        if hit is None:
            if not (mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
                self.ed.select([], mode="replace")
            self._drag = {"kind": "band", "origin": ev.position(), "rect": None,
                          "base": set(self.ed.selection)}
            return
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.ed.select([hit], mode="toggle")
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self.ed.select([hit], mode="add")
        elif not self.ed.is_selected(hit):
            self.ed.select([hit], mode="replace")
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        self._drag = {"kind": "scale" if alt else "move", "origin": ev.position(),
                      "t_grab": self._hit_time(hit), "started": False}

    def _hit_time(self, hit):
        if hit[0] == SUMMARY:
            return float(hit[1])
        return float(self.ed.points(hit[0])[hit[1]]["t"])

    def mouseMoveEvent(self, ev):
        d = self._drag
        if not d:
            return
        if d["kind"] == "scrub":
            self.ed.seek_local(self.snap_t(self.ax.t(ev.position().x()), free=True))
            return
        if d["kind"] == "band":
            o = d["origin"]
            d["rect"] = QRectF(o, ev.position()).normalized()
            keys = self._keys_in(d["rect"])
            self.ed.select(list(d["base"]) + keys, mode="replace", quiet=True)
            self.update()
            return
        if not d["started"]:
            if (ev.position() - d["origin"]).manhattanLength() < 3:
                return
            d["started"] = True
            self.ed.begin_gesture()
        free = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        t_now = self.ax.t(ev.position().x())
        if d["kind"] == "move":
            target = self.snap_t(d["t_grab"] + (t_now - self.ax.t(d["origin"].x())), free=free,
                                 exclude=self.ed.selection)
            self.ed.preview_move(target - d["t_grab"])
        else:
            self.ed.preview_scale(d["t_grab"], self.snap_t(t_now, free=free, exclude=self.ed.selection))

    def mouseReleaseEvent(self, ev):
        d = self._drag
        self._drag = None
        if d and d.get("kind") in ("move", "scale") and d.get("started"):
            self.ed.end_gesture("移动关键帧" if d["kind"] == "move" else "缩放关键帧时距")
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.position().y() <= RULER_H or ev.position().x() <= GUTTER_W:
            return
        hit = self.key_at(ev.position())
        if hit is not None:
            self.ed.seek_local(self._hit_time(hit))
            return
        r = int((ev.position().y() - RULER_H) // ROW_H)
        rows = self.rows()
        if 1 <= r < len(rows):
            self.ed.add_key(rows[r][0], self.snap_t(self.ax.t(ev.position().x())))

    def _keys_in(self, rect: QRectF):
        out = []
        for r, (path, _label) in enumerate(self.rows()):
            cy = self.row_y(r) + ROW_H / 2
            if not (rect.top() - DIAMOND <= cy <= rect.bottom() + DIAMOND):
                continue
            if path == SUMMARY:
                for t in self.ed.summary_times():
                    if rect.left() <= self.ax.x(t) <= rect.right():
                        out.extend(self.ed.keys_at_time(t))
                continue
            for i, pt in enumerate(self.ed.points(path)):
                if rect.left() <= self.ax.x(float(pt["t"])) <= rect.right():
                    out.append((path, i))
        return out

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(600, RULER_H + ROW_H * max(3, len(self.rows()) + 1))


# ───────────────────────────── 曲线编辑器 ─────────────────────────────

class GraphView(_TimeView):
    PAD = 18.0

    def __init__(self, editor):
        super().__init__(editor)
        self.v_lo = 0.0
        self.v_hi = 1.0
        self.normalize = None        # None = 自动（多条时归一化）
        self.hidden_paths: set = set()
        self._ranges: dict = {}

    def numeric_paths(self):
        return [path for path, pts in self.ed.rows_points() if ke.is_numeric_path(pts) and pts]

    def shown_paths(self):
        return [p for p in self.numeric_paths() if p not in self.hidden_paths]

    def is_normalized(self) -> bool:
        if self.normalize is not None:
            return bool(self.normalize)
        return len(self.shown_paths()) > 1

    def fit_values(self):
        self._ranges = {path: ke.value_range(self.ed.points(path)) for path in self.numeric_paths()}
        if self.is_normalized():
            self.v_lo, self.v_hi = -0.08, 1.08
            return
        shown = self.shown_paths()
        if not shown:
            self.v_lo, self.v_hi = 0.0, 1.0
            return
        lo = min(self._ranges[p][0] for p in shown)
        hi = max(self._ranges[p][1] for p in shown)
        pad = (hi - lo) * 0.08 or 1.0
        self.v_lo, self.v_hi = lo - pad, hi + pad

    # 值 ↔ 屏幕
    def _norm(self, path, v):
        if not self.is_normalized():
            return float(v)
        lo, hi = self._ranges.get(path) or ke.value_range(self.ed.points(path))
        return (float(v) - lo) / (hi - lo) if hi > lo else 0.5

    def _denorm(self, path, n):
        if not self.is_normalized():
            return float(n)
        lo, hi = self._ranges.get(path) or ke.value_range(self.ed.points(path))
        return lo + float(n) * (hi - lo)

    def y(self, path, v) -> float:
        top, bot = RULER_H + self.PAD, self.height() - self.PAD
        n = self._norm(path, v)
        span = (self.v_hi - self.v_lo) or 1.0
        return bot - (n - self.v_lo) / span * (bot - top)

    def v(self, path, y) -> float:
        top, bot = RULER_H + self.PAD, self.height() - self.PAD
        span = (self.v_hi - self.v_lo) or 1.0
        n = self.v_lo + (bot - float(y)) / max(1.0, bot - top) * span
        return self._denorm(path, n)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tok = current()
        if not self._ranges or set(self._ranges) != set(self.numeric_paths()):
            self.fit_values()
        self.paint_frame(p)
        body = self.body_rect()
        p.save()
        p.setClipRect(body)
        # 水平网格 + 数值
        f = QFont(self.font())
        f.setPixelSize(FONT_MICRO)
        p.setFont(f)
        shown = self.shown_paths()
        ref = shown[0] if shown else None
        if ref is not None:
            for k in range(6):
                n = self.v_lo + (self.v_hi - self.v_lo) * k / 5.0
                yy = self.y(ref, self._denorm(ref, n)) if self.is_normalized() else self.y(ref, n)
                p.setPen(QPen(QColor(*hex_rgba(tok.line, 70)), 1))
                p.drawLine(QPointF(GUTTER_W, yy), QPointF(self.width(), yy))
                if not self.is_normalized():
                    p.setPen(QColor(tok.fg2))
                    p.drawText(QPointF(GUTTER_W + 4, yy - 2), _fmt_v(n))
        sel = self.ed.selection
        for path in shown:
            color = QColor(self.ed.curve_color(path))
            pts = self.ed.points(path)
            focus = any(k[0] == path for k in sel)
            # 曲线：每 2 px 取一个值
            curve = QPainterPath()
            host = {"keyframes": {path: pts}}
            x_from = max(GUTTER_W, self.ax.x(min(0.0, float(pts[0]["t"]))))
            x_to = min(self.width(), self.ax.x(max(self.ed.target.dur if self.ed.target else 0, float(pts[-1]["t"]))))
            first = True
            x = x_from
            while x <= x_to + 1:
                val = value_at(host, path, self.ax.t(x), float(pts[0]["v"]))
                yy = self.y(path, val)
                if first:
                    curve.moveTo(x, yy)
                    first = False
                else:
                    curve.lineTo(x, yy)
                x += 2.0
            c = QColor(color)
            if not focus and sel:
                c.setAlpha(140)
            p.setPen(QPen(c, 2.0 if focus else 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(curve)
            # 关键帧 + 手柄
            for i, pt in enumerate(pts):
                kx, ky = self.ax.x(float(pt["t"])), self.y(path, pt["v"])
                is_sel = (path, i) in sel
                if is_sel:
                    for side, (ht, hv) in ke.handle_points(pts, i).items():
                        seg_ease = str(pts[i if side == "out" else i - 1].get("ease") or "linear")
                        if seg_ease == "hold":
                            continue
                        hx, hy = self.ax.x(ht), self.y(path, hv)
                        p.setPen(QPen(QColor(tok.fg1), 1))
                        p.drawLine(QPointF(kx, ky), QPointF(hx, hy))
                        p.setBrush(QColor(tok.bg0))
                        p.setPen(QPen(QColor(tok.accent), 1.5))
                        p.drawEllipse(QPointF(hx, hy), HANDLE_R, HANDLE_R)
                p.setPen(QPen(QColor(tok.bg0), 1))
                p.setBrush(QColor(tok.accent) if is_sel else color)
                r = KEY_HALF + (1 if is_sel else 0)
                p.drawRect(QRectF(kx - r, ky - r, 2 * r, 2 * r))
        p.restore()
        self._paint_legend(p)
        self.paint_playhead(p)
        self.paint_band(p)
        if self.ed.target is None or not self.numeric_paths():
            p.setPen(QColor(tok.fg2))
            msg = ("选中一个视频、音频、文本或图片，在检查器里点参数旁的 ⏱ 打关键帧"
                   if self.ed.target is None else "这个对象还没有数值关键帧")
            p.drawText(body, Qt.AlignmentFlag.AlignCenter, msg)
        p.end()

    def _paint_legend(self, p):
        tok = current()
        self.paint_gutter_bg(p)
        f = QFont(self.font())
        f.setPixelSize(FONT_LABEL)
        p.setFont(f)
        for r, path in enumerate(self.numeric_paths()):
            y = RULER_H + SPACE_1 + r * ROW_H
            on = path not in self.hidden_paths
            color = QColor(self.ed.curve_color(path))
            if not on:
                color.setAlpha(70)
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(SPACE_2, y + ROW_H / 2), QPointF(SPACE_2 + 12, y + ROW_H / 2))
            p.setPen(QColor(tok.fg0 if on else tok.fg2))
            p.drawText(QRectF(SPACE_2 + 18, y, GUTTER_W - SPACE_2 - 22, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter, path_label(path))
        if self.numeric_paths():
            yb = self.height() - ROW_H - SPACE_1
            p.setPen(QColor(tok.fg2))
            f2 = QFont(f)
            f2.setPixelSize(FONT_MICRO)
            p.setFont(f2)
            p.drawText(QRectF(SPACE_2, yb, GUTTER_W - SPACE_2 * 2, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter,
                       "归一化显示" if self.is_normalized() else "真实数值")

    def hit(self, pos):
        """('handle', path, i, side) / ('key', path, i) / None。手柄优先（只有选中的才有）。"""
        x, y = pos.x(), pos.y()
        sel = self.ed.selection
        for path in reversed(self.shown_paths()):
            pts = self.ed.points(path)
            for i in range(len(pts)):
                if (path, i) not in sel:
                    continue
                for side, (ht, hv) in ke.handle_points(pts, i).items():
                    if math.hypot(self.ax.x(ht) - x, self.y(path, hv) - y) <= HANDLE_R + 3:
                        return ("handle", path, i, side)
        for path in reversed(self.shown_paths()):
            pts = self.ed.points(path)
            for i in range(len(pts) - 1, -1, -1):
                kx, ky = self.ax.x(float(pts[i]["t"])), self.y(path, pts[i]["v"])
                if abs(kx - x) <= KEY_HALF + 3 and abs(ky - y) <= KEY_HALF + 3:
                    return ("key", path, i)
        return None

    def mousePressEvent(self, ev):
        self.setFocus()
        pos = ev.position()
        if ev.button() == Qt.MouseButton.RightButton:
            h = self.hit(pos)
            if h and h[0] == "key" and not self.ed.is_selected((h[1], h[2])):
                self.ed.select([(h[1], h[2])], mode="replace")
            self.ed.context_menu(self.mapToGlobal(pos.toPoint()))
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.ruler_press(ev):
            return
        if pos.x() <= GUTTER_W:
            r = int((pos.y() - RULER_H - SPACE_1) // ROW_H)
            paths = self.numeric_paths()
            if 0 <= r < len(paths):
                path = paths[r]
                if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
                    self.hidden_paths = set(paths) - {path}          # Alt+点 = 只看这一条
                elif path in self.hidden_paths:
                    self.hidden_paths.discard(path)
                else:
                    self.hidden_paths.add(path)
                self.fit_values()
                self.update()
            elif pos.y() >= self.height() - ROW_H - SPACE_1:
                self.normalize = not self.is_normalized()
                self.fit_values()
                self.update()
            return
        mods = ev.modifiers()
        h = self.hit(pos)
        if h is None:
            if not (mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
                self.ed.select([], mode="replace")
            self._drag = {"kind": "band", "origin": pos, "rect": None, "base": set(self.ed.selection)}
            return
        if h[0] == "handle":
            self._drag = {"kind": "handle", "path": h[1], "i": h[2], "side": h[3],
                          "origin": pos, "started": False}
            return
        key = (h[1], h[2])
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.ed.select([key], mode="toggle")
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self.ed.select([key], mode="add")
        elif not self.ed.is_selected(key):
            self.ed.select([key], mode="replace")
        pt = self.ed.points(h[1])[h[2]]
        self._drag = {"kind": "key", "path": h[1], "i": h[2], "origin": pos, "started": False,
                      "t_grab": float(pt["t"]), "v_grab": float(pt["v"])}

    def mouseMoveEvent(self, ev):
        d = self._drag
        if not d:
            return
        pos = ev.position()
        if d["kind"] == "scrub":
            self.ed.seek_local(self.snap_t(self.ax.t(pos.x()), free=True))
            return
        if d["kind"] == "band":
            d["rect"] = QRectF(d["origin"], pos).normalized()
            keys = []
            for path in self.shown_paths():
                for i, pt in enumerate(self.ed.points(path)):
                    if d["rect"].contains(QPointF(self.ax.x(float(pt["t"])), self.y(path, pt["v"]))):
                        keys.append((path, i))
            self.ed.select(list(d["base"]) + keys, mode="replace", quiet=True)
            self.update()
            return
        if not d["started"]:
            if (pos - d["origin"]).manhattanLength() < 3:
                return
            d["started"] = True
            self.ed.begin_gesture()
        mods = ev.modifiers()
        if d["kind"] == "handle":
            path = d["path"]
            self.ed.preview_handle(path, d["i"], d["side"], self.ax.t(pos.x()), self.v(path, pos.y()),
                                   linked=not (mods & Qt.KeyboardModifier.AltModifier))
            return
        # 拖关键帧：时间 + 值；Shift 只改主方向
        path = d["path"]
        dx = pos.x() - d["origin"].x()
        dy = pos.y() - d["origin"].y()
        lock_t = lock_v = False
        if mods & Qt.KeyboardModifier.ShiftModifier:
            if abs(dx) >= abs(dy):
                lock_v = True
            else:
                lock_t = True
        free = bool(mods & Qt.KeyboardModifier.ControlModifier)
        dt = 0.0 if lock_t else (self.snap_t(d["t_grab"] + dx / self.ax.pps, free=free,
                                             exclude=self.ed.selection) - d["t_grab"])
        v_new = d["v_grab"] if lock_v else self.v(path, self.y(path, d["v_grab"]) + dy)
        self.ed.preview_key_drag(dt, path, v_new - d["v_grab"])

    def mouseReleaseEvent(self, ev):
        d = self._drag
        self._drag = None
        if d and d.get("kind") in ("key", "handle") and d.get("started"):
            self.ed.end_gesture("调整曲线手柄" if d["kind"] == "handle" else "移动关键帧")
            self.fit_values()
        self.update()

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position()
        if pos.y() <= RULER_H or pos.x() <= GUTTER_W:
            return
        h = self.hit(pos)
        if h and h[0] == "key":
            self.ed.seek_local(float(self.ed.points(h[1])[h[2]]["t"]))
            return
        shown = self.shown_paths()
        if len(shown) == 1:
            self.ed.add_key(shown[0], self.snap_t(self.ax.t(pos.x())))

    def wheel_extra(self, ev, dy) -> bool:
        mods = ev.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier:
            mid = (self.v_lo + self.v_hi) / 2.0
            half = (self.v_hi - self.v_lo) / 2.0 * (1 / 1.15 if dy > 0 else 1.15)
            self.v_lo, self.v_hi = mid - half, mid + half
            self.update()
            return True
        return False


def _fmt_v(v: float) -> str:
    av = abs(v)
    if av >= 100:
        return "%.0f" % v
    if av >= 10:
        return "%.1f" % v
    return "%.2f" % v


# ───────────────────────────── 编辑器 ─────────────────────────────

class KeyframeEditor(QWidget):
    """dock 里的整块：工具条 + （关键帧 | 曲线）。"""

    mode_changed = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.setObjectName("keyframeEditor")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.win = window
        self.target: KfTarget | None = None
        self.axis = TimeAxis()
        self.selection: set = set()             # {(path, index)}
        self._live: dict | None = None          # 拖动中：{path: points}
        self._orig: dict | None = None          # 拖动开始时的点表
        self._sig = None
        self._clip: dict | None = None          # 关键帧剪贴板：{path: [(dt, point)]}
        self._fitted_for = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(SPACE_2, SPACE_1, SPACE_2, SPACE_1)
        bar.setSpacing(SPACE_1)
        self.title = QLabel("关键帧")
        self.title.setObjectName("sectionHeader")
        bar.addWidget(self.title)
        self.btn_sheet = self._tool("关键帧", "关键帧面板：每个参数一行（Shift+F3 切换）", checkable=True)
        self.btn_graph = self._tool("曲线", "运动曲线编辑器：拖关键帧和贝塞尔手柄（Shift+F3 切换）", checkable=True)
        self.btn_sheet.setChecked(True)
        self.btn_sheet.clicked.connect(lambda: self.set_mode("sheet"))
        self.btn_graph.clicked.connect(lambda: self.set_mode("graph"))
        bar.addSpacing(SPACE_2)
        bar.addWidget(self.btn_sheet)
        bar.addWidget(self.btn_graph)
        bar.addSpacing(SPACE_2)
        self.ease_buttons = {}
        for key, text, tip in (
            ("linear", "线性", "选中关键帧往后那一段：匀速"),
            ("easy", "缓动", "两头都缓（F9）"),
            ("easy_in", "缓入", "进这个关键帧时慢下来（Shift+F9）"),
            ("easy_out", "缓出", "离开这个关键帧时慢慢起步（Ctrl+Shift+F9）"),
            ("smooth", "平滑", "按前后关键帧自动算切线，经过时不停顿"),
            ("hold", "定格", "保持这个值，到下一个关键帧突变"),
        ):
            b = self._tool(text, tip)
            b.clicked.connect(lambda _c=False, k=key: self.apply_ease(k))
            bar.addWidget(b)
            self.ease_buttons[key] = b
        bar.addStretch(1)
        self.btn_prev = self._tool("◀◆", "上一个关键帧（J）")
        self.btn_next = self._tool("◆▶", "下一个关键帧（K）")
        self.btn_add = self._tool("＋◆", "在播放头给选中参数打关键帧（没选就是全部有动画的参数）")
        self.btn_fit = self._tool("适应", "整个片段放进视野（Shift+Z）")
        self.btn_prev.clicked.connect(lambda: self.jump(-1))
        self.btn_next.clicked.connect(lambda: self.jump(1))
        self.btn_add.clicked.connect(self.add_key_at_playhead)
        self.btn_fit.clicked.connect(self.fit_view)
        for b in (self.btn_prev, self.btn_next, self.btn_add, self.btn_fit):
            bar.addWidget(b)
        root.addLayout(bar)
        self.stack = QStackedWidget()
        self.sheet = DopeSheet(self)
        self.graph = GraphView(self)
        self.stack.addWidget(self.sheet)
        self.stack.addWidget(self.graph)
        root.addWidget(self.stack, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self.refresh)
        self._hook_timeline()
        self.refresh(force=True)

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()

    def hideEvent(self, e):
        self._poll.stop()
        super().hideEvent(e)

    # ── 小工具 ──
    def _tool(self, text, tip, checkable=False):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _hook_timeline(self):
        tlw = getattr(self.win, "timeline", None)
        if tlw is not None and hasattr(tlw, "position_changed"):
            tlw.position_changed.connect(lambda _ms: self.views_changed())

    def mode(self) -> str:
        return "graph" if self.stack.currentWidget() is self.graph else "sheet"

    def set_mode(self, mode: str):
        graph = mode == "graph"
        self.stack.setCurrentWidget(self.graph if graph else self.sheet)
        self.btn_graph.setChecked(graph)
        self.btn_sheet.setChecked(not graph)
        if graph:
            self.graph.fit_values()
        self.stack.currentWidget().setFocus()
        self.mode_changed.emit(self.mode())
        self.views_changed()

    def fps(self) -> float:
        return self.target.fps if self.target is not None else 30.0

    def curve_color(self, path) -> str:
        paths = [p for p, _pts in self.rows_points()]
        i = paths.index(path) if path in paths else 0
        return CURVE_COLORS[i % len(CURVE_COLORS)]

    # ── 数据 ──
    def rows_points(self):
        """[(path, points)]：当前对象有关键帧的参数。拖动中返回预览值。"""
        if self.target is None:
            return []
        src = self._live if self._live is not None else keyframes_of(self.target.host)
        order = list(keyframes_of(self.target.host).keys())
        for k in (self._live or {}):
            if k not in order:
                order.append(k)
        out = []
        for path in order:
            pts = src.get(path) if isinstance(src, dict) else None
            if pts:
                out.append((path, ke.clone(pts)))
        return out

    def points(self, path):
        for p, pts in self.rows_points():
            if p == path:
                return pts
        return []

    def summary_times(self):
        times = []
        for _path, pts in self.rows_points():
            for pt in pts:
                t = float(pt["t"])
                if all(abs(t - x) > HIT_EPS for x in times):
                    times.append(t)
        return sorted(times)

    def keys_at_time(self, t):
        return [(path, i) for path, pts in self.rows_points()
                for i, pt in enumerate(pts) if abs(float(pt["t"]) - float(t)) <= HIT_EPS]

    def summary_selected(self):
        out = set()
        for t in self.summary_times():
            keys = self.keys_at_time(t)
            if keys and all(k in self.selection for k in keys):
                out.add(t)
        return out

    def is_selected(self, key) -> bool:
        if key[0] == SUMMARY:
            return float(key[1]) in self.summary_selected()
        return key in self.selection

    def select(self, keys, mode="replace", quiet=False):
        expanded = set()
        for k in keys:
            if k[0] == SUMMARY:
                expanded.update(self.keys_at_time(k[1]))
            else:
                expanded.add((k[0], int(k[1])))
        if mode == "replace":
            self.selection = expanded
        elif mode == "add":
            self.selection |= expanded
        elif mode == "toggle":
            if expanded and expanded <= self.selection:
                self.selection -= expanded
            else:
                self.selection |= expanded
        if not quiet:
            self.views_changed()

    def playhead_local(self) -> float:
        tlw = getattr(self.win, "timeline", None)
        pos = float(getattr(tlw, "position", 0) or 0) / 1000.0 if tlw is not None else 0.0
        return pos - (self.target.start if self.target is not None else 0.0)

    def seek_local(self, t_local: float):
        if self.target is None:
            return
        tlw = getattr(self.win, "timeline", None)
        if tlw is None:
            return
        ms = int(round((self.target.start + max(0.0, float(t_local))) * 1000.0))
        try:
            tlw.set_position(ms)
        except Exception:
            _LOG.exception("关键帧编辑器 seek 失败")
        self.views_changed()

    # ── 跟着检查器走 ──
    def refresh(self, force=False):
        if self._live is not None:
            return
        if not force and not self.isVisible():
            return
        tgt = resolve_target(self.win)
        sig = None
        if tgt is not None:
            kfs = keyframes_of(tgt.host)
            sig = (id(tgt.host), repr(sorted((k, repr(v)) for k, v in kfs.items())), round(tgt.start, 4),
                   round(tgt.dur, 4))
        if sig == self._sig and (tgt is None) == (self.target is None):
            return
        changed_host = tgt is None or self.target is None or tgt.host is not self.target.host
        self.target = tgt
        self._sig = sig
        if changed_host:
            self.selection = set()
        else:
            self.selection = {(p, i) for p, i in self.selection if i < len(self.points(p))}
        self.title.setText("关键帧 · %s" % tgt.title if tgt is not None else "关键帧")
        if tgt is not None and self._fitted_for is not tgt.host:
            self._fitted_for = tgt.host
            self.fit_view()
        self.graph.fit_values()
        self.views_changed()

    def views_changed(self):
        self.sheet.update()
        self.graph.update()
        has = self.target is not None
        for b in list(self.ease_buttons.values()) + [self.btn_prev, self.btn_next, self.btn_add, self.btn_fit]:
            b.setEnabled(has)

    def fit_view(self):
        if self.target is None:
            return
        self.axis.fit(self.target.dur, max(self.sheet.width(), self.graph.width(), 400))
        self.graph.fit_values()
        self.views_changed()

    # ── 手势：预览 → 提交 ──
    def begin_gesture(self):
        if self.target is None:
            return
        self._orig = {p: ke.clone(pts) for p, pts in keyframes_of(self.target.host).items()}
        self._live = {p: ke.clone(pts) for p, pts in self._orig.items()}
        self._sel0 = set(self.selection)

    def _by_path(self, keys):
        out: dict = {}
        for path, i in keys:
            out.setdefault(path, []).append(i)
        return out

    def _push_live(self, new: dict, new_sel: set):
        """把预览写进对象（画面跟着动）。"""
        self._live = new
        self.selection = new_sel
        try:
            replace_paths(self.target.host, {p: new.get(p) for p in set(new) | set(self._orig or {})})
        except Exception:
            _LOG.exception("关键帧预览写入失败")
        canvas = getattr(self.win, "video_canvas", None)
        if canvas is not None:
            canvas.update()
        tlw = getattr(self.win, "timeline", None)
        if tlw is not None:
            tlw.update()
        self.views_changed()

    def _remap(self, before: list, after: list, idxs, t_of):
        """拖动后按「预期新时间」把选中的点找回来。"""
        out = set()
        for i in idxs:
            want = t_of(float(before[i]["t"]))
            for j, p in enumerate(after):
                if abs(float(p["t"]) - want) <= 1e-6:
                    out.add(j)
                    break
        return out

    def preview_move(self, dt: float):
        if self._orig is None:
            return
        new = {p: ke.clone(pts) for p, pts in self._orig.items()}
        sel = set()
        lo = 0.0
        for path, idxs in self._by_path(self._sel0).items():
            base = self._orig.get(path) or []
            new[path] = ke.move(base, idxs, dt, lo=lo)
            d_eff = dt
            times = [float(base[i]["t"]) for i in idxs if i < len(base)]
            if times:
                d_eff = max(dt, lo - min(times))
            sel |= {(path, j) for j in self._remap(base, new[path], idxs, lambda t, d=d_eff: max(0.0, t + d))}
        self._push_live(new, sel)

    def preview_scale(self, t_grab: float, t_now: float):
        if self._orig is None:
            return
        times = [float(self._orig[p][i]["t"]) for p, i in self._sel0 if p in self._orig and i < len(self._orig[p])]
        if len(times) < 2:
            self.preview_move(t_now - t_grab)
            return
        lo_t, hi_t = min(times), max(times)
        anchor = hi_t if abs(t_grab - lo_t) <= abs(t_grab - hi_t) else lo_t
        span = t_grab - anchor
        if abs(span) < 1e-9:
            return
        factor = max(0.0, (t_now - anchor) / span)
        new = {p: ke.clone(pts) for p, pts in self._orig.items()}
        sel = set()
        for path, idxs in self._by_path(self._sel0).items():
            base = self._orig.get(path) or []
            new[path] = ke.scale_time(base, idxs, anchor, factor)
            sel |= {(path, j) for j in self._remap(base, new[path], idxs,
                                                    lambda t: max(0.0, anchor + (t - anchor) * factor))}
        self._push_live(new, sel)

    def preview_key_drag(self, dt: float, path_grab: str, dv: float):
        """曲线里拖关键帧：时间整体平移 dt；值——被抓的那条曲线上选中的点加 dv（按各自值域比例换算到别的曲线）。"""
        if self._orig is None:
            return
        new = {p: ke.clone(pts) for p, pts in self._orig.items()}
        sel = set()
        lo_g, hi_g = ke.value_range(self._orig.get(path_grab) or [])
        span_g = (hi_g - lo_g) or 1.0
        for path, idxs in self._by_path(self._sel0).items():
            base = self._orig.get(path) or []
            if not ke.is_numeric_path(base):
                continue
            lo, hi = ke.value_range(base)
            scale = ((hi - lo) or 1.0) / span_g if path != path_grab else 1.0
            moved = ke.set_values(base, {i: float(base[i]["v"]) + dv * scale for i in idxs if i < len(base)})
            new[path] = ke.move(moved, idxs, dt)
            sel |= {(path, j) for j in self._remap(moved, new[path], idxs, lambda t, d=dt: max(0.0, t + d))}
        self._push_live(new, sel)

    def preview_handle(self, path, i, side, t, v, linked=True):
        if self._orig is None:
            return
        new = {p: ke.clone(pts) for p, pts in self._orig.items()}
        new[path] = ke.drag_handle(self._orig.get(path) or [], i, side, t, v, linked=linked)
        self._push_live(new, set(self._sel0))

    def end_gesture(self, label: str):
        if self._orig is None or self.target is None:
            self._live = self._orig = None
            return
        new = self._live or {}
        orig = self._orig
        self._live = self._orig = None
        # 先恢复原样（撤销快照记的是拖之前），再走命令
        replace_paths(self.target.host, {p: orig.get(p) for p in set(new) | set(orig)})
        changed = {p: new.get(p) for p in set(new) | set(orig) if new.get(p) != orig.get(p)}
        sel = set(self.selection)
        if changed:
            self.commit(changed, label)
        self.selection = sel
        self.views_changed()

    def commit(self, paths: dict, label: str = "改写关键帧") -> bool:
        if self.target is None or not paths:
            return False
        from cutora.core.commands import get_bus
        payload = dict(self.target.params)
        payload["paths"] = {p: (pts if pts else None) for p, pts in paths.items()}
        try:
            get_bus().execute("keyframe.replace", payload, source="ui")
        except Exception as exc:
            _LOG.exception("keyframe.replace 失败")
            bar = getattr(self.win, "statusBar", None)
            if callable(bar):
                bar().showMessage("关键帧没改成：%s" % exc, 4000)
            return False
        self._sig = None
        panel = getattr(self.win, "prop_panel", None)
        refresh = getattr(panel, "_refresh_kf_watches", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
        self.refresh()
        return True

    # ── 一步操作 ──
    def _apply_per_path(self, fn, label, *, need_sel=True, keep_sel=True):
        if self.target is None:
            return False
        groups = self._by_path(self.selection)
        if need_sel and not groups:
            self._toast("先选中关键帧")
            return False
        changes = {}
        for path, idxs in groups.items():
            base = self.points(path)
            out = fn(path, base, sorted(idxs))
            if out != base:
                changes[path] = out
        if not changes:
            return False
        sel = set(self.selection) if keep_sel else set()
        ok = self.commit(changes, label)
        self.selection = {(p, i) for p, i in sel if i < len(self.points(p))}
        self.views_changed()
        return ok

    def apply_ease(self, ease: str):
        names = {"linear": "线性", "easy": "缓动", "easy_in": "缓入", "easy_out": "缓出",
                 "smooth": "平滑", "hold": "定格", "ease_in": "缓入", "ease_out": "缓出",
                 "ease_in_out": "缓入缓出"}

        def fn(_path, base, idxs):
            if ease != "hold" and not ke.is_numeric_path(base) and ease in ("easy", "easy_in", "easy_out", "smooth"):
                return ke.set_ease(base, idxs, "ease_in_out")
            return ke.set_ease(base, idxs, ease)
        return self._apply_per_path(fn, "关键帧插值：%s" % names.get(ease, ease))

    def delete_selected(self):
        return self._apply_per_path(lambda _p, base, idxs: ke.delete(base, idxs), "删除关键帧", keep_sel=False)

    def reverse_selected(self):
        return self._apply_per_path(lambda _p, base, idxs: ke.reverse(base, idxs), "反转关键帧")

    def distribute_selected(self):
        return self._apply_per_path(lambda _p, base, idxs: ke.distribute(base, idxs), "关键帧等距分布")

    def select_all(self):
        self.select([(p, i) for p, pts in self.rows_points() for i in range(len(pts))])

    def copy_selected(self):
        groups = self._by_path(self.selection)
        if not groups:
            self._toast("先选中关键帧")
            return
        t0 = min(float(self.points(p)[i]["t"]) for p, idxs in groups.items() for i in idxs)
        self._clip = {p: [(float(self.points(p)[i]["t"]) - t0, copy.deepcopy(self.points(p)[i]))
                          for i in sorted(idxs)] for p, idxs in groups.items()}
        n = sum(len(v) for v in self._clip.values())
        self._toast("已复制 %d 个关键帧" % n)

    def paste_at_playhead(self):
        if not self._clip or self.target is None:
            self._toast("关键帧剪贴板是空的")
            return
        t = max(0.0, self.playhead_local())
        changes = {}
        for path, items in self._clip.items():
            base = self.points(path)
            for dt, pt in items:
                rec = copy.deepcopy(pt)
                rec["t"] = t + dt
                base = [q for q in base if abs(float(q["t"]) - rec["t"]) > HIT_EPS] + [rec]
            changes[path] = ke.clone(base)
        self.commit(changes, "粘贴关键帧")

    def add_key(self, path, t_local):
        if self.target is None or path in (None, SUMMARY):
            return
        base = self.points(path)
        if not base:
            return
        fb = base[0]["v"]
        v = value_at({"keyframes": {path: base}}, path, t_local, fb)
        ease = "linear"
        for pt in base:
            if float(pt["t"]) <= t_local:
                ease = str(pt.get("ease") or "linear")
        rec = {"t": max(0.0, float(t_local)), "v": v, "ease": "linear" if ease == "bezier" else ease}
        new = [q for q in base if abs(float(q["t"]) - rec["t"]) > HIT_EPS] + [rec]
        if self.commit({path: ke.clone(new)}, "添加关键帧"):
            pts = self.points(path)
            self.selection = {(path, j) for j, q in enumerate(pts) if abs(float(q["t"]) - rec["t"]) <= 1e-6}
            self.views_changed()

    def add_key_at_playhead(self):
        if self.target is None:
            return
        paths = sorted({p for p, _i in self.selection}) or [p for p, _pts in self.rows_points()]
        if not paths:
            self._toast("先在检查器里点参数旁的 ⏱ 打开关键帧")
            return
        t = max(0.0, self.playhead_local())
        changes = {}
        for path in paths:
            base = self.points(path)
            if not base or any(abs(float(q["t"]) - t) <= HIT_EPS for q in base):
                continue
            v = value_at({"keyframes": {path: base}}, path, t, base[0]["v"])
            changes[path] = ke.clone(base + [{"t": t, "v": v, "ease": "linear"}])
        if changes:
            self.commit(changes, "在播放头打关键帧")

    def jump(self, direction: int):
        if self.target is None:
            return
        now = self.playhead_local()
        if self.selection and self.mode() == "graph":
            times = sorted({float(self.points(p)[i]["t"]) for p, i in self.selection
                            if i < len(self.points(p))})
        else:
            times = self.summary_times()
        if not times:
            return
        if direction < 0:
            cand = [t for t in times if t < now - HIT_EPS]
            t = cand[-1] if cand else None
        else:
            cand = [t for t in times if t > now + HIT_EPS]
            t = cand[0] if cand else None
        if t is not None:
            self.seek_local(t)

    # ── 键盘 / 菜单 ──
    def wants_key(self, ev) -> bool:
        key = ev.key()
        mods = ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key in (Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_Delete, Qt.Key.Key_Backspace,
                   Qt.Key.Key_F9, Qt.Key.Key_Escape):
            return True
        if key == Qt.Key.Key_F3 and mods & Qt.KeyboardModifier.ShiftModifier:
            return True
        if key == Qt.Key.Key_Z and mods == Qt.KeyboardModifier.ShiftModifier:
            return True
        if ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_V, Qt.Key.Key_A):
            return True
        return False

    def handle_key(self, ev) -> bool:
        key = ev.key()
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_J:
            self.jump(-1)
        elif key == Qt.Key.Key_K:
            self.jump(1)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key.Key_F9:
            self.apply_ease("easy_out" if (ctrl and shift) else "easy_in" if shift else "easy")
        elif key == Qt.Key.Key_F3 and shift:
            self.set_mode("sheet" if self.mode() == "graph" else "graph")
        elif key == Qt.Key.Key_Z and shift and not ctrl:
            self.fit_view()
        elif ctrl and key == Qt.Key.Key_C:
            self.copy_selected()
        elif ctrl and key == Qt.Key.Key_V:
            self.paste_at_playhead()
        elif ctrl and key == Qt.Key.Key_A:
            self.select_all()
        elif key == Qt.Key.Key_Escape:
            self.select([])
        else:
            return False
        return True

    def context_menu(self, global_pos):
        menu = QMenu(self)
        has_sel = bool(self.selection)
        interp = menu.addMenu("插值")
        for key, text in (("linear", "线性"), ("easy", "缓动\tF9"), ("easy_in", "缓入\tShift+F9"),
                          ("easy_out", "缓出\tCtrl+Shift+F9"), ("smooth", "平滑（自动贝塞尔）"),
                          ("hold", "定格")):
            act = interp.addAction(text, lambda k=key: self.apply_ease(k))
            act.setEnabled(has_sel)
        interp.setEnabled(has_sel)
        menu.addSeparator()
        menu.addAction("复制\tCtrl+C", self.copy_selected).setEnabled(has_sel)
        menu.addAction("粘贴到播放头\tCtrl+V", self.paste_at_playhead).setEnabled(bool(self._clip))
        menu.addSeparator()
        menu.addAction("反转", self.reverse_selected).setEnabled(len(self.selection) >= 2)
        menu.addAction("等距分布", self.distribute_selected).setEnabled(len(self.selection) >= 3)
        menu.addAction("全选\tCtrl+A", self.select_all)
        menu.addSeparator()
        menu.addAction("删除\tDelete", self.delete_selected).setEnabled(has_sel)
        menu.exec(global_pos)

    def _toast(self, msg: str):
        bar = getattr(self.win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(msg, 2500)
            except Exception:
                pass
