# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · 画布上的 AI 跟踪：放点（点 = 放 / 挪选中的点，Alt+点 = 去掉这一帧上最近的点）
+ 每个点前后各 TRAIL 帧的轨迹 + 当前位置（实心 = 看得见，空心红 = 被挡 / 跟丢）+ 本帧的放点标记（方框）。

模式存在 ``canvas._mask_mode = {"kind": "track", "panel": AiTrackPanel}``，由 canvas_mask 转过来。
没进放点模式时，只要选中的视频片段有跟踪结果，检查器里「AI 跟踪」展开着，也画轨迹（draw_passive）。
坐标链和 AI 抠像一样：画布 → 图层框归一化 → 解翻转 → 源帧归一化。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A
from cutora.fx import track_store as T
from cutora.fx import tracking as TR

_LOG = get_logger("cutora.ui.canvas_track")
TRAIL = 12
DOT_PX = 4.5
PICK_PX = 10.0


def _mask_mod():
    from cutora.ui import canvas_mask as ck
    return ck


def _resolve_panel(canvas, panel):
    if panel is not None and hasattr(panel, "place_from_canvas"):
        return panel
    inner = getattr(panel, "track", None)
    if inner is not None:
        return inner
    p = _mask_mod()._panel(canvas)
    return getattr(p, "track", None)


def _geom(rec) -> dict:
    return {"src_w": int(rec.get("frame_w") or 1), "src_h": int(rec.get("frame_h") or 1),
            "crop": (0, 0, 0, 0), "flip": (bool(rec.get("flip_x")), bool(rec.get("flip_y"))), "order": "crop_flip"}


def start(canvas, panel):
    ck = _mask_mod()
    panel = _resolve_panel(canvas, panel)
    host, _rec, kind = ck._host_and_rec(canvas, layer=True)
    if host is None or kind != "clip":
        bar = getattr(canvas.window(), "statusBar", None)
        if callable(bar):
            bar().showMessage("先在画布上选中一条视频片段，再放跟踪点", 3000)
        if panel is not None:
            panel.set_place_mode(False)
        return
    canvas._mask_mode = {"kind": "track", "panel": panel}
    canvas.setCursor(Qt.CursorShape.CrossCursor)
    canvas.setFocus()
    canvas.update()


def end(canvas):
    mode = getattr(canvas, "_mask_mode", None)
    canvas._mask_mode = None
    canvas.unsetCursor()
    p = (mode or {}).get("panel")
    if p is not None and hasattr(p, "set_place_mode"):
        p.set_place_mode(False)
    canvas.update()


def hud_text(_mode) -> str:
    return "AI 跟踪：点 = 放 / 挪选中的点 · Alt+点 = 去掉这帧上的点 · 右键 / Esc 退出"


def _frame_and_fps(panel):
    if panel is None:
        return None, None
    try:
        return panel.current_frame(), panel.fps()
    except Exception:
        return None, None


def press(canvas, vx, vy, event, mode) -> bool:
    if event.button() == Qt.MouseButton.RightButton:
        end(canvas)
        return True
    if event.button() != Qt.MouseButton.LeftButton:
        return True
    ck = _mask_mod()
    host, rec, kind = ck._host_and_rec(canvas, layer=True)
    frame = ck.Frame.of(host, rec) if host is not None and kind == "clip" else None
    panel = mode.get("panel")
    if frame is None or panel is None:
        end(canvas)
        return True
    geom = _geom(rec)
    sx, sy = A.layer_to_source_norm(*frame.to_norm(vx, vy), geom)
    if event.modifiers() & Qt.KeyboardModifier.AltModifier:
        # 去掉这一帧上离鼠标最近的点选
        fi, _fps = _frame_and_fps(panel)
        sp = TR.spec(host)
        best = None
        unit = ck._canvas_px(canvas)
        for q in TR.queries_at(sp, fi) if fi is not None else []:
            p = frame.to_canvas(*A.source_to_layer_norm(q["x"], q["y"], geom))
            d = max(abs(p.x() - vx), abs(p.y() - vy))
            if d <= PICK_PX * unit * 2 and (best is None or d < best[0]):
                best = (d, int(q["pt"]))
        if best is not None:
            panel.unplace_from_canvas(best[1])
        canvas.update()
        return True
    if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
        return True
    panel.place_from_canvas(sx, sy)
    canvas.update()
    return True


def move(canvas, vx, vy, event, mode) -> bool:
    return True


def release(canvas, mode) -> bool:
    return True


def key(canvas, event, mode) -> bool:
    if event.key() == Qt.Key.Key_Escape:
        end(canvas)
    return True


def _passive_panel(canvas):
    p = _mask_mod()._panel(canvas)
    tp = getattr(p, "track", None)
    if tp is None or not tp.isVisibleTo(p) or not tp.sec.isExpanded():
        return None
    return tp


def draw_passive(canvas, painter):
    """不在放点模式：AI 跟踪那节展开着就画轨迹，方便检查。"""
    tp = _passive_panel(canvas)
    if tp is None:
        return
    draw(canvas, painter, {"kind": "track", "panel": tp, "passive": True})


def draw(canvas, painter, mode):
    ck = _mask_mod()
    host, rec, kind = ck._host_and_rec(canvas, layer=True)
    if host is None or kind != "clip":
        return
    frame = ck.Frame.of(host, rec)
    if frame is None:
        return
    sp = TR.spec(host)
    panel = mode.get("panel")
    fi, _fps = _frame_and_fps(panel)
    if fi is None:
        return
    unit = ck._canvas_px(canvas)
    geom = _geom(rec)

    def to_c(x, y):
        return frame.to_canvas(*A.source_to_layer_norm(x, y, geom))

    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        sel = panel.current_point_id() if panel is not None and hasattr(panel, "current_point_id") else None
        for p in sp["points"]:
            if not p.get("enabled", True):
                continue
            col = QColor(p["color"])
            # 轨迹
            if sp["session"]:
                path = QPainterPath()
                started = False
                for f in range(fi - TRAIL, fi + TRAIL + 1):
                    v = T.points_at(sp["session"], f).get(p["id"])
                    if v is None:
                        started = False
                        continue
                    c = to_c(v[0], v[1])
                    if not started:
                        path.moveTo(c)
                        started = True
                    else:
                        path.lineTo(c)
                trail = QColor(col)
                trail.setAlpha(170)
                painter.setPen(QPen(trail, 1.2 * unit))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
                cur = T.points_at(sp["session"], fi).get(p["id"])
                if cur is not None:
                    c = to_c(cur[0], cur[1])
                    r = DOT_PX * unit * (1.3 if p["id"] == sel else 1.0)
                    if cur[2] >= T.VISIBLE:
                        painter.setPen(QPen(QColor(255, 255, 255, 230), 1.2 * unit))
                        painter.setBrush(col)
                    else:
                        painter.setPen(QPen(QColor("#E04848"), 1.6 * unit))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(c, r, r)
            # 这一帧上的放点：方框
            for q in TR.queries_at(sp, fi):
                if int(q["pt"]) != p["id"]:
                    continue
                c = to_c(q["x"], q["y"])
                h = (DOT_PX + 3.0) * unit
                painter.setPen(QPen(col, 1.6 * unit))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(c.x() - h, c.y() - h, 2 * h, 2 * h))
                if p["id"] == sel:
                    painter.drawLine(QPointF(c.x() - h * 1.6, c.y()), QPointF(c.x() - h, c.y()))
                    painter.drawLine(QPointF(c.x() + h, c.y()), QPointF(c.x() + h * 1.6, c.y()))
    finally:
        painter.restore()
