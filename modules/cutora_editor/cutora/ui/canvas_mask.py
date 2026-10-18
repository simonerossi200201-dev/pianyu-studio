# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-B/C · 画布上的遮罩手柄、多边形绘制、抠像吸管。

遮罩存在图层自己的 0–1 坐标里；画布上按这一帧图层画出来的框（命中记录的 rect + rotation +
scale）换算。所以图层怎么动，遮罩跟着动。

- 选中图层后，它的遮罩都画成细线；检查器里选中的那个画成金色 + 手柄：
  矩形 / 椭圆 = 四角 + 四边中点（拖角等比、拖边单向、Shift 拖角保持比例、按住中间拖整体）；
  多边形 = 顶点方块（拖）+ 边中点小圆（点一下插入一个点），Alt+点顶点删除。
- 「＋多边形」后进入绘制：点一下加一个点，双击 / Enter 闭合，Esc 取消，右键撤掉上一个点。
- Q167「＋贝塞尔」（钢笔）：点 = 尖角锚点；按住拖 = 拉出对称手柄（平滑点），拖的时候按住 Alt = 只动出手柄；
  点回第一个点 / 双击 / Enter 闭合。编辑：拖锚点、拖手柄（对称；Alt 拖 = 打断成尖角）、点曲线中点插点（形状不变）、
  Alt+点锚点删除、双击锚点 尖角 ↔ 平滑。
- 「吸管」后光标变十字，点画面取屏幕色（5×5 平均），Esc 取消。
拖动中直接改对象（画面跟着变），松手恢复原样再走一条 fx.mask_update —— 一次撤销。
"""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF

from cutora.core.log import get_logger
from cutora.fx import masks as M

_LOG = get_logger("cutora.ui.canvas_mask")

HANDLE_PX = 8.0
HIT_PX = 9.0
MID_PX = 5.0


# ───────────────────────────── 目标与坐标 ─────────────────────────────

def _panel(canvas):
    win = canvas.window()
    pp = getattr(win, "prop_panel", None)
    fn = getattr(pp, "fx_panel", None)
    return fn() if callable(fn) else None


def _host_and_rec(canvas, layer: bool = False):
    """选中的覆层 / 视频片段 → (host, 命中记录, kind)。

    Q152-F：这一层有节点图、检查器里选着一个节点时，host = 那个节点的 params（遮罩手柄 / 吸管 /
    AI 点选改的是节点）；记录复制一份并带上图层的整体缩放（_layer_scale），Frame 照旧算得对。
    layer=True：永远给图层本身（时间换算、跟踪用）。"""
    item = getattr(canvas, "_selected_item", None)
    if not isinstance(item, dict):
        return None, None, None
    live = canvas._live_record(item) if callable(getattr(canvas, "_live_record", None)) else item
    typ = item.get("type")
    if typ == "overlay":
        host = canvas._record_data(item)
        res = (host, live, "overlay") if isinstance(host, dict) else (None, None, None)
    elif typ == "video_clip":
        clip = item.get("clip")
        res = (clip, live, "clip") if clip is not None else (None, None, None)
    else:
        return None, None, None
    if layer or res[0] is None:
        return res
    return _node_override(canvas, *res)


def _node_override(canvas, host, rec, kind):
    from cutora.fx import graph as G
    g = G.graph_of(host)
    if g is None:
        return host, rec, kind
    panel = _panel(canvas)
    nid = str(getattr(panel, "node_id", "") or "")
    lt = getattr(panel, "_layer_target", None)
    if not nid or not callable(lt):
        return host, rec, kind
    try:
        if lt()[0] is not host:
            return host, rec, kind
    except Exception:
        return host, rec, kind
    n = G.node_by_id(g, nid)
    if n is None:
        return host, rec, kind
    rec2 = dict(rec) if isinstance(rec, dict) else {}
    sc = 1.0
    if isinstance(host, dict):
        try:
            sc = float(host.get("scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            sc = 1.0
    rec2["_layer_scale"] = sc
    return n["params"], rec2, kind


class Frame:
    """图层框 → 画布（视频坐标）的换算：中心 + 旋转 + 整体缩放。"""

    def __init__(self, rect: QRectF, rotation: float, scale: float = 1.0):
        self.rect = QRectF(rect)
        self.rot = math.radians(float(rotation or 0.0))
        self.scale = float(scale or 1.0) or 1.0

    @classmethod
    def of(cls, host, rec):
        rect = rec.get("rect") if isinstance(rec, dict) else None
        if rect is None or not isinstance(rect, QRectF) or rect.width() <= 0 or rect.height() <= 0:
            return None
        rot = float(rec.get("rotation") or 0.0) if isinstance(rec, dict) else 0.0
        sc = 1.0
        if isinstance(rec, dict) and "_layer_scale" in rec:
            sc = float(rec["_layer_scale"] or 1.0)
        elif isinstance(host, dict):
            try:
                sc = float(host.get("scale", 1.0) or 1.0)
            except (TypeError, ValueError):
                sc = 1.0
        return cls(rect, rot, sc)

    def to_canvas(self, nx: float, ny: float) -> QPointF:
        c = self.rect.center()
        lx = (nx - 0.5) * self.rect.width() * self.scale
        ly = (ny - 0.5) * self.rect.height() * self.scale
        cs, sn = math.cos(self.rot), math.sin(self.rot)
        return QPointF(c.x() + lx * cs - ly * sn, c.y() + lx * sn + ly * cs)

    def to_norm(self, vx: float, vy: float) -> tuple[float, float]:
        c = self.rect.center()
        dx, dy = vx - c.x(), vy - c.y()
        cs, sn = math.cos(-self.rot), math.sin(-self.rot)
        lx = dx * cs - dy * sn
        ly = dx * sn + dy * cs
        return (lx / (self.rect.width() * self.scale) + 0.5, ly / (self.rect.height() * self.scale) + 0.5)

    def px_per_norm(self) -> tuple[float, float]:
        return self.rect.width() * self.scale, self.rect.height() * self.scale


def _canvas_px(canvas) -> float:
    """一个屏幕像素对应几个视频像素（手柄尺寸用）。"""
    target = canvas._preview_target_rect()
    if target.width() <= 0:
        return 1.0
    return canvas._video_dims[0] / target.width()


# ───────────────────────────── 形状的手柄 ─────────────────────────────

def _rect_handles(mask, frame: Frame):
    """矩形 / 椭圆：{"nw","n","ne","e","se","s","sw","w"} → 画布点（考虑遮罩自身旋转）。"""
    nx, ny, nw, nh = mask["rect"]
    cx, cy = nx + nw / 2.0, ny + nh / 2.0
    rot = math.radians(float(mask.get("rotation") or 0.0))
    out = {}
    for name, (fx, fy) in (("nw", (-0.5, -0.5)), ("n", (0.0, -0.5)), ("ne", (0.5, -0.5)), ("e", (0.5, 0.0)),
                           ("se", (0.5, 0.5)), ("s", (0.0, 0.5)), ("sw", (-0.5, 0.5)), ("w", (-0.5, 0.0))):
        lx, ly = fx * nw, fy * nh
        pw, ph = frame.px_per_norm()
        # 旋转在图层像素空间里做（归一化空间不等比）
        px, py = lx * pw, ly * ph
        cs, sn = math.cos(rot), math.sin(rot)
        rx, ry = px * cs - py * sn, px * sn + py * cs
        out[name] = frame.to_canvas(cx + rx / pw, cy + ry / ph)
    return out


def _outline(mask, frame: Frame, w_px: int, h_px: int) -> QPolygonF:
    pts = M.shape_polygon(mask, w_px, h_px)
    poly = QPolygonF()
    for x, y in pts:
        poly.append(frame.to_canvas(x / w_px, y / h_px))
    return poly


def _poly_contains(poly: QPolygonF, vx, vy) -> bool:
    return poly.containsPoint(QPointF(vx, vy), Qt.FillRule.OddEvenFill)


# ───────────────────────────── 绘制 ─────────────────────────────

def draw(canvas, painter):
    host, rec, kind = _host_and_rec(canvas)
    panel = _panel(canvas)
    mode = getattr(canvas, "_mask_mode", None)
    canvas._mask_geom = None
    if mode and mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        _cmt.draw(canvas, painter, mode)
        return
    from cutora.ui import canvas_track as _ctk
    if mode and mode.get("kind") == "track":
        _ctk.draw(canvas, painter, mode)
        return
    _ctk.draw_passive(canvas, painter)
    if host is None or rec is None:
        return
    frame = Frame.of(host, rec)
    if frame is None:
        return
    raw = host.get("masks") if isinstance(host, dict) else getattr(host, "masks", None)
    masks = [M.normalize(m) for m in (raw or []) if isinstance(m, dict)]
    masks = [m for m in masks if m is not None]
    active_id = getattr(panel, "active_mask_id", "") if panel is not None else ""
    if not masks and not mode:
        return
    from cutora.ui.theme.tokens import current
    tok = current()
    accent = QColor(tok.accent)
    unit = _canvas_px(canvas)
    w_px, h_px = max(2, int(frame.rect.width())), max(2, int(frame.rect.height()))
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        geom = {"frame": frame, "handles": {}, "mids": [], "outline": None, "mask": None}
        for m in masks:
            active = m["id"] == active_id
            poly = _outline(m, frame, w_px, h_px)
            pen = QPen(accent if active else QColor(*_rgba(tok.fg1, 160)), 1.6 if active else 1.0)
            pen.setCosmetic(True)
            if not m.get("enabled", True):
                pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(poly)
            if not active:
                continue
            geom["mask"] = m
            geom["outline"] = poly
            half = HANDLE_PX / 2.0 * unit
            if m["type"] == "bezier":
                _bezier_handles(painter, m, frame, geom, accent, tok, unit)
            elif m["type"] == "polygon":
                pts = [frame.to_canvas(p[0], p[1]) for p in m["points"]]
                for i, p in enumerate(pts):
                    geom["handles"]["v%d" % i] = p
                    q = pts[(i + 1) % len(pts)]
                    geom["mids"].append(((i, QPointF((p.x() + q.x()) / 2, (p.y() + q.y()) / 2))))
                painter.setPen(QPen(accent, 1.0))
                painter.setBrush(QColor(tok.bg0))
                for _i, mid in geom["mids"]:
                    painter.drawEllipse(mid, MID_PX / 2.0 * unit, MID_PX / 2.0 * unit)
            else:
                geom["handles"] = _rect_handles(m, frame)
            drag = getattr(canvas, "_mask_drag", None)
            for name, p in geom["handles"].items():
                if name[0] in "io":
                    continue                     # 贝塞尔手柄已经画成小圆
                hot = drag is not None and drag.get("handle") == name
                painter.setPen(QPen(accent, 1.2))
                painter.setBrush(accent if hot else QColor(tok.bg0))
                painter.drawRect(QRectF(p.x() - half, p.y() - half, 2 * half, 2 * half))
        canvas._mask_geom = geom
        if mode and mode.get("kind") == "bezier":
            _draw_pen(painter, mode, frame, accent, tok, unit)
        if mode and mode.get("kind") == "poly":
            pts = [frame.to_canvas(p[0], p[1]) for p in mode.get("points") or []]
            pen = QPen(accent, 1.6)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            cur = mode.get("cursor")
            if pts:
                for a, b in zip(pts, pts[1:]):
                    painter.drawLine(a, b)
                if cur is not None:
                    dash = QPen(accent, 1.0, Qt.PenStyle.DashLine)
                    dash.setCosmetic(True)
                    painter.setPen(dash)
                    painter.drawLine(pts[-1], QPointF(*cur))
                    if len(pts) >= 2:
                        painter.drawLine(QPointF(*cur), pts[0])
                painter.setPen(QPen(accent, 1.2))
                painter.setBrush(QColor(tok.bg0))
                half = HANDLE_PX / 2.0 * unit
                for p in pts:
                    painter.drawRect(QRectF(p.x() - half, p.y() - half, 2 * half, 2 * half))
    finally:
        painter.restore()


def _bezier_handles(painter, m, frame, geom, accent, tok, unit):
    """选中的贝塞尔遮罩：锚点方块（geom["handles"] v%d）、手柄小圆（i%d / o%d）+ 连线、每段曲线中点（插点）。"""
    pts = [M._bz_pt(p) for p in m["points"]]
    r = MID_PX / 2.0 * unit
    for i, p in enumerate(pts):
        a = frame.to_canvas(p[0], p[1])
        geom["handles"]["v%d" % i] = a
        for tag, ox, oy in (("i", p[2], p[3]), ("o", p[4], p[5])):
            if abs(ox) < 1e-9 and abs(oy) < 1e-9:
                continue
            hp = frame.to_canvas(p[0] + ox, p[1] + oy)
            geom["handles"]["%s%d" % (tag, i)] = hp
            pen = QPen(accent, 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(a, hp)
            painter.setBrush(accent)
            painter.drawEllipse(hp, r, r)
    for i, (p0, p1, p2, p3) in enumerate(M.bezier_segments(pts)):
        mx, my = M._cubic(p0, p1, p2, p3, 0.5)
        geom["mids"].append((i, frame.to_canvas(mx, my)))
    painter.setPen(QPen(accent, 1.0))
    painter.setBrush(QColor(tok.bg0))
    for _i, mid in geom["mids"]:
        painter.drawEllipse(mid, r, r)


def _draw_pen(painter, mode, frame, accent, tok, unit):
    """钢笔绘制中：已有的锚点 + 曲线 + 到光标的橡皮筋。"""
    pts = [list(p) for p in mode.get("points") or []]
    if not pts:
        return
    cur = mode.get("cursor")
    path_pts = pts + ([[frame.to_norm(*cur)[0], frame.to_norm(*cur)[1], 0, 0, 0, 0]] if cur is not None and not mode.get("drag") else [])
    pen = QPen(accent, 1.6)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    segs = M.bezier_segments(path_pts)
    if len(path_pts) >= 2:
        segs = segs[:-1]                          # 还没闭合：不画最后一段回到起点
    for p0, p1, p2, p3 in segs:
        prev = None
        for k in range(M.BEZIER_STEPS + 1):
            x, y = M._cubic(p0, p1, p2, p3, k / float(M.BEZIER_STEPS))
            q = frame.to_canvas(x, y)
            if prev is not None:
                painter.drawLine(prev, q)
            prev = q
    half = HANDLE_PX / 2.0 * unit
    r = MID_PX / 2.0 * unit
    for p in pts:
        a = frame.to_canvas(p[0], p[1])
        for ox, oy in ((p[2], p[3]), (p[4], p[5])):
            if abs(ox) > 1e-9 or abs(oy) > 1e-9:
                hp = frame.to_canvas(p[0] + ox, p[1] + oy)
                painter.setPen(QPen(accent, 1.0))
                painter.drawLine(a, hp)
                painter.setBrush(accent)
                painter.drawEllipse(hp, r, r)
        painter.setPen(QPen(accent, 1.2))
        painter.setBrush(QColor(tok.bg0))
        painter.drawRect(QRectF(a.x() - half, a.y() - half, 2 * half, 2 * half))


def _rgba(hex_color: str, alpha: int):
    c = QColor(hex_color)
    return c.red(), c.green(), c.blue(), alpha


def hud_text(canvas) -> str | None:
    mode = getattr(canvas, "_mask_mode", None)
    if not mode:
        return None
    if mode.get("kind") == "poly":
        n = len(mode.get("points") or [])
        return "多边形遮罩：点一下加点（%d）· 双击 / Enter 闭合 · 右键退一步 · Esc 取消" % n
    if mode.get("kind") == "bezier":
        n = len(mode.get("points") or [])
        return ("贝塞尔遮罩：点 = 尖角 · 按住拖 = 平滑（Alt 只动一侧）· 点回起点 / 双击 / Enter 闭合（%d 个点）"
                " · 右键退一步 · Esc 取消" % n)
    if mode.get("kind") == "pick":
        return "吸管：点一下画面取屏幕色 · Esc 取消"
    if mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        return _cmt.hud_text(mode)
    if mode.get("kind") == "track":
        from cutora.ui import canvas_track as _ctk
        return _ctk.hud_text(mode)
    return None


# ───────────────────────────── 交互 ─────────────────────────────

def fx_canvas_action(canvas, action, arg, panel=None):
    if action == "draw_polygon":
        host, rec, _k = _host_and_rec(canvas)
        if host is None:
            win = canvas.window()
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                bar().showMessage("先在画布上选中一个视频、图片或文字，再画多边形遮罩", 3000)
            return
        canvas._mask_mode = {"kind": "poly", "points": [], "cursor": None, "panel": panel}
        canvas.setCursor(Qt.CursorShape.CrossCursor)
        canvas.setFocus()
    elif action == "draw_bezier":
        host, rec, _k = _host_and_rec(canvas)
        if host is None:
            win = canvas.window()
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                bar().showMessage("先在画布上选中一个视频、图片或文字，再画贝塞尔遮罩", 3000)
            return
        canvas._mask_mode = {"kind": "bezier", "points": [], "cursor": None, "panel": panel, "drag": None}
        canvas.setCursor(Qt.CursorShape.CrossCursor)
        canvas.setFocus()
    elif action == "pick_color":
        if arg:
            canvas._mask_mode = {"kind": "pick", "panel": panel}
            canvas.setCursor(Qt.CursorShape.CrossCursor)
            canvas.setFocus()
        elif getattr(canvas, "_mask_mode", None) and canvas._mask_mode.get("kind") == "pick":
            _end_mode(canvas)
    elif action == "select_mask":
        pass
    elif action == "matte_prompt":
        from cutora.ui import canvas_matte as _cmt
        if arg:
            _cmt.start(canvas, panel)
        elif getattr(canvas, "_mask_mode", None) and canvas._mask_mode.get("kind") == "matte":
            _cmt.end(canvas)
    elif action == "track_place":
        from cutora.ui import canvas_track as _ctk
        if arg:
            _ctk.start(canvas, panel)
        elif getattr(canvas, "_mask_mode", None) and canvas._mask_mode.get("kind") == "track":
            _ctk.end(canvas)
    canvas.update()


def _end_mode(canvas):
    mode = getattr(canvas, "_mask_mode", None)
    canvas._mask_mode = None
    canvas.unsetCursor()
    if mode and mode.get("kind") == "pick":
        p = mode.get("panel") or _panel(canvas)
        btn = getattr(p, "btn_pick", None)
        if btn is not None and btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
    canvas.update()


def _hit_handle(canvas, vx, vy):
    geom = getattr(canvas, "_mask_geom", None)
    if not geom or geom.get("mask") is None:
        return None
    tol = HIT_PX * _canvas_px(canvas)
    best = None
    for name, p in geom["handles"].items():
        d = max(abs(p.x() - vx), abs(p.y() - vy))
        if d <= tol and (best is None or d < best[0]):
            best = (d, name)
    if best is not None:
        return ("handle", best[1])
    for i, mid in geom.get("mids") or []:
        if max(abs(mid.x() - vx), abs(mid.y() - vy)) <= tol:
            return ("mid", i)
    poly = geom.get("outline")
    if poly is not None and _poly_contains(poly, vx, vy):
        return ("body", None)
    return None


def press(canvas, vx, vy, event) -> bool:
    mode = getattr(canvas, "_mask_mode", None)
    if mode and mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        return _cmt.press(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "track":
        from cutora.ui import canvas_track as _ctk
        return _ctk.press(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "pick":
        if event.button() == Qt.MouseButton.LeftButton:
            _pick(canvas, vx, vy, mode)
        return True
    if mode and mode.get("kind") == "bezier":
        return _pen_press(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "poly":
        if event.button() == Qt.MouseButton.RightButton:
            if mode["points"]:
                mode["points"].pop()
                canvas.update()
            else:
                _end_mode(canvas)
            return True
        if event.button() != Qt.MouseButton.LeftButton:
            return True
        host, rec, _k = _host_and_rec(canvas)
        frame = Frame.of(host, rec) if host is not None else None
        if frame is None:
            _end_mode(canvas)
            return True
        nx, ny = frame.to_norm(vx, vy)
        pts = mode["points"]
        # 点回第一个点 = 闭合
        if len(pts) >= 3:
            p0 = frame.to_canvas(pts[0][0], pts[0][1])
            if max(abs(p0.x() - vx), abs(p0.y() - vy)) <= HIT_PX * _canvas_px(canvas):
                finish_polygon(canvas)
                return True
        pts.append([nx, ny])
        canvas.update()
        return True
    if event.button() != Qt.MouseButton.LeftButton:
        return False
    hit = _hit_handle(canvas, vx, vy)
    if hit is None:
        return False
    geom = canvas._mask_geom
    mask = geom["mask"]
    host, rec, kind = _host_and_rec(canvas)
    frame = geom["frame"]
    panel = _panel(canvas)
    if hit[0] == "mid":
        i = hit[1]
        nx, ny = frame.to_norm(vx, vy)
        new = M.insert_point(mask, i, nx, ny)
        if panel is not None:
            panel.update_mask(mask["id"], {"points": new["points"]})
        canvas.update()
        return True
    if hit[0] == "handle" and hit[1][0] in "io":
        pass                                     # 贝塞尔手柄：下面照常开始拖
    elif hit[0] == "handle" and hit[1].startswith("v") and event.modifiers() & Qt.KeyboardModifier.AltModifier:
        new = M.remove_point(mask, int(hit[1][1:]))
        if new is not None and panel is not None:
            panel.update_mask(mask["id"], {"points": new["points"]})
        canvas.update()
        return True
    canvas._mask_drag = {
        "handle": hit[1] if hit[0] == "handle" else "body",
        "mask": copy.deepcopy(mask), "host": host, "frame": frame,
        "start": frame.to_norm(vx, vy), "moved": False, "panel": panel,
    }
    canvas.update()
    return True


def _write_mask(host, mask):
    raw = host.get("masks") if isinstance(host, dict) else getattr(host, "masks", None)
    masks = list(raw or [])
    for i, m in enumerate(masks):
        if isinstance(m, dict) and m.get("id") == mask["id"]:
            masks[i] = mask
    if isinstance(host, dict):
        host["masks"] = masks
    else:
        host.masks = masks


def move(canvas, vx, vy, event) -> bool:
    mode = getattr(canvas, "_mask_mode", None)
    if mode and mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        return _cmt.move(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "track":
        from cutora.ui import canvas_track as _ctk
        return _ctk.move(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "poly":
        mode["cursor"] = (vx, vy)
        canvas.update()
        return True
    if mode and mode.get("kind") == "bezier":
        return _pen_move(canvas, vx, vy, event, mode)
    if mode and mode.get("kind") == "pick":
        return True
    d = getattr(canvas, "_mask_drag", None)
    if d is None:
        return False
    frame: Frame = d["frame"]
    nx, ny = frame.to_norm(vx, vy)
    sx, sy = d["start"]
    dx, dy = nx - sx, ny - sy
    orig = d["mask"]
    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
    h = d["handle"]
    if h == "body":
        new = M.moved(orig, dx, dy)
    elif h.startswith("v"):
        new = M.with_point(orig, int(h[1:]), nx, ny)
    elif h[0] in "io" and orig["type"] == "bezier":
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        new = M.with_handle(orig, int(h[1:]), "in" if h[0] == "i" else "out", nx, ny, symmetric=not alt)
    else:
        x0, y0, w0, h0 = orig["rect"]
        rot = math.radians(float(orig.get("rotation") or 0.0))
        if abs(rot) > 1e-6:
            # 旋转过的框：把位移转到框自己的轴上
            pw, ph = frame.px_per_norm()
            px, py = dx * pw, dy * ph
            cs, sn = math.cos(-rot), math.sin(-rot)
            dx, dy = (px * cs - py * sn) / pw, (px * sn + py * cs) / ph
        x1, y1, x2, y2 = x0, y0, x0 + w0, y0 + h0
        if "w" in h:
            x1 += dx
        if "e" in h:
            x2 += dx
        if "n" in h:
            y1 += dy
        if "s" in h:
            y2 += dy
        if shift and h in ("nw", "ne", "sw", "se") and w0 > 1e-6 and h0 > 1e-6:
            ratio = h0 / w0
            nw_ = abs(x2 - x1)
            if "n" in h:
                y1 = y2 - nw_ * ratio
            else:
                y2 = y1 + nw_ * ratio
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        new = M.with_rect(orig, x1, y1, max(1e-3, x2 - x1), max(1e-3, y2 - y1))
    _write_mask(d["host"], new)
    d["new"] = new
    d["moved"] = True
    canvas.update()
    return True


def release(canvas) -> bool:
    mode = getattr(canvas, "_mask_mode", None)
    if mode and mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        return _cmt.release(canvas, mode)
    if mode and mode.get("kind") == "track":
        from cutora.ui import canvas_track as _ctk
        return _ctk.release(canvas, mode)
    if mode and mode.get("kind") == "bezier":
        mode["drag"] = None
        canvas.update()
        return True
    d = getattr(canvas, "_mask_drag", None)
    if d is None:
        return False
    canvas._mask_drag = None
    _write_mask(d["host"], d["mask"])          # 先恢复，命令里再改（撤销记的是拖之前）
    new = d.get("new")
    if d["moved"] and new is not None:
        panel = d.get("panel") or _panel(canvas)
        patch = {"points": new["points"]} if new["type"] in M.PATH_TYPES else {"rect": new["rect"]}
        if panel is not None:
            panel.update_mask(new["id"], patch)
        else:
            _write_mask(d["host"], new)
    canvas.update()
    return True


def double_click(canvas, vx, vy) -> bool:
    mode = getattr(canvas, "_mask_mode", None)
    if mode and mode.get("kind") == "poly":
        finish_polygon(canvas)
        return True
    if mode and mode.get("kind") == "bezier":
        # 双击的第一下已经在这里加了最后一个点 —— 直接闭合
        finish_bezier(canvas)
        return True
    if not mode:
        # 选中的贝塞尔遮罩：双击锚点 尖角 ↔ 平滑
        geom = getattr(canvas, "_mask_geom", None)
        m = (geom or {}).get("mask")
        if m is not None and m.get("type") == "bezier":
            hit = _hit_handle(canvas, vx, vy)
            if hit and hit[0] == "handle" and hit[1].startswith("v"):
                new = M.toggle_smooth(m, int(hit[1][1:]))
                panel = _panel(canvas)
                if panel is not None:
                    panel.update_mask(m["id"], {"points": new["points"]})
                canvas.update()
                return True
    return False


def key(canvas, event) -> bool:
    mode = getattr(canvas, "_mask_mode", None)
    if not mode:
        return False
    k = event.key()
    if mode.get("kind") == "matte":
        from cutora.ui import canvas_matte as _cmt
        return _cmt.key(canvas, event, mode)
    if mode.get("kind") == "track":
        from cutora.ui import canvas_track as _ctk
        return _ctk.key(canvas, event, mode)
    if k == Qt.Key.Key_Escape:
        _end_mode(canvas)
        return True
    if mode.get("kind") == "poly" and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        finish_polygon(canvas)
        return True
    if mode.get("kind") == "bezier" and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        finish_bezier(canvas)
        return True
    return True            # 绘制 / 吸管中吃掉别的键，免得触发全局快捷键


def finish_polygon(canvas):
    mode = getattr(canvas, "_mask_mode", None)
    if not mode or mode.get("kind") != "poly":
        return
    pts = [list(p) for p in mode.get("points") or []]
    panel = mode.get("panel") or _panel(canvas)
    _end_mode(canvas)
    if len(pts) < 3:
        win = canvas.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage("多边形至少要 3 个点，已取消", 2500)
        return
    m = M.default_mask("polygon")
    m["points"] = pts
    if panel is not None:
        panel.add_mask("polygon", m)
    canvas.update()


# ───────────────────────────── 钢笔（Q167）─────────────────────────────

PEN_DRAG_PX = 3.0


def _pen_press(canvas, vx, vy, event, mode) -> bool:
    if event.button() == Qt.MouseButton.RightButton:
        if mode["points"]:
            mode["points"].pop()
            canvas.update()
        else:
            _end_mode(canvas)
        return True
    if event.button() != Qt.MouseButton.LeftButton:
        return True
    host, rec, _k = _host_and_rec(canvas)
    frame = Frame.of(host, rec) if host is not None else None
    if frame is None:
        _end_mode(canvas)
        return True
    pts = mode["points"]
    if len(pts) >= 2:
        p0 = frame.to_canvas(pts[0][0], pts[0][1])
        if max(abs(p0.x() - vx), abs(p0.y() - vy)) <= HIT_PX * _canvas_px(canvas):
            finish_bezier(canvas)
            return True
    nx, ny = frame.to_norm(vx, vy)
    pts.append([nx, ny, 0.0, 0.0, 0.0, 0.0])
    mode["drag"] = {"i": len(pts) - 1, "start": (vx, vy), "frame": frame}
    canvas.update()
    return True


def _pen_move(canvas, vx, vy, event, mode) -> bool:
    mode["cursor"] = (vx, vy)
    d = mode.get("drag")
    if d is not None:
        sx, sy = d["start"]
        if max(abs(vx - sx), abs(vy - sy)) >= PEN_DRAG_PX * _canvas_px(canvas) or d.get("pulled"):
            d["pulled"] = True
            frame = d["frame"]
            p = mode["points"][d["i"]]
            nx, ny = frame.to_norm(vx, vy)
            ox, oy = nx - p[0], ny - p[1]
            p[4], p[5] = ox, oy
            if not (event.modifiers() & Qt.KeyboardModifier.AltModifier):
                p[2], p[3] = -ox, -oy
    canvas.update()
    return True


def finish_bezier(canvas):
    mode = getattr(canvas, "_mask_mode", None)
    if not mode or mode.get("kind") != "bezier":
        return
    pts = [list(p) for p in mode.get("points") or []]
    panel = mode.get("panel") or _panel(canvas)
    _end_mode(canvas)
    curved = any(any(abs(v) > 1e-9 for v in p[2:6]) for p in pts)
    if len(pts) < 3 and not (len(pts) == 2 and curved):
        win = canvas.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage("贝塞尔遮罩至少要 3 个点（或 2 个带手柄的点），已取消", 2500)
        return
    m = M.default_mask("bezier")
    m["points"] = pts
    if panel is not None:
        panel.add_mask("bezier", m)
    canvas.update()


def _pick(canvas, vx, vy, mode):
    """吸管：从选中图层的源像素取色（不是合成后的画面）。"""
    host, rec, kind = _host_and_rec(canvas)
    panel = mode.get("panel") or _panel(canvas)
    color = None
    try:
        frame = Frame.of(host, rec) if host is not None else None
        if frame is not None:
            nx, ny = frame.to_norm(vx, vy)
            img = None
            if kind == "clip":
                img = rec.get("frame")
                if img is not None and bool(rec.get("flip_x")):
                    nx = 1.0 - nx
                if img is not None and bool(rec.get("flip_y")):
                    ny = 1.0 - ny
            elif isinstance(host, dict) and host.get("type") == "image":
                from cutora.render.frame_renderer import get_cached_image
                img = get_cached_image(host.get("content") or host.get("path") or "")
            if img is not None and not img.isNull():
                from PySide6.QtGui import QImage
                import numpy as np
                from cutora.fx.keyer import sample_color
                rgb_img = img.convertToFormat(QImage.Format.Format_RGB888)
                w, h = rgb_img.width(), rgb_img.height()
                buf = np.frombuffer(bytes(rgb_img.constBits()), dtype=np.uint8).reshape((h, rgb_img.bytesPerLine()))
                arr = buf[:, :w * 3].reshape((h, w, 3))
                x = int(max(0, min(w - 1, round(nx * (w - 1)))))
                y = int(max(0, min(h - 1, round(ny * (h - 1)))))
                color = sample_color(arr, x, y, radius=2)
    except Exception:
        _LOG.exception("吸管取色失败")
    _end_mode(canvas)
    if color is None:
        win = canvas.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            bar().showMessage("这里取不到颜色：请点在选中的那条视频 / 图片的画面上", 3000)
        return
    if panel is not None:
        panel.set_picked_color(color)
    canvas.update()
