# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · 画布上的 AI 抠像交互：点选对象（正点 / 负点 / 框）+ 当前帧的彩色结果叠加 + 提示点标记。

模式存在 ``canvas._mask_mode = {"kind": "matte", "panel": AiMattePanel, "drag": …}``，
由 canvas_mask 在 press / move / release / key / draw / hud_text 里转过来。
坐标：画布 → 图层框归一化（Frame.to_norm）→ 解翻转 → 源帧归一化（ai_matte.layer_to_source_norm），
所以提示点和裁切 / 缩放 / 位置无关。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen

from cutora.core.log import get_logger
from cutora.fx import ai_matte as A

_LOG = get_logger("cutora.ui.canvas_matte")
DOT_PX = 5.0
BOX_MIN_PX = 6.0


def _mask_mod():
    from cutora.ui import canvas_mask as ck
    return ck


def _geom(rec) -> dict:
    """命中记录 → 源帧几何（命中框是按原始帧尺寸排的，只有翻转，没有裁切）。"""
    return {"src_w": int(rec.get("frame_w") or 1), "src_h": int(rec.get("frame_h") or 1),
            "crop": (0, 0, 0, 0), "flip": (bool(rec.get("flip_x")), bool(rec.get("flip_y"))),
            "order": "crop_flip"}


def _source_sec(canvas, host) -> float:
    task = getattr(canvas, "_task_data", None)
    tl = None
    if task is not None:
        from cutora.ui.canvas import _tl_of
        tl = _tl_of(task)
    fn = getattr(canvas, "_clip_source_sec", None)
    try:
        return float(fn(host, tl)) if callable(fn) else 0.0
    except Exception:
        return 0.0


def _resolve_panel(canvas, panel):
    """信号链上 sender 是 LayerFxPanel；真正要回调的是它里面的 AiMattePanel。"""
    if panel is not None and hasattr(panel, "add_prompt_from_canvas"):
        return panel
    inner = getattr(panel, "ai", None)
    if inner is not None and hasattr(inner, "add_prompt_from_canvas"):
        return inner
    p = _mask_mod()._panel(canvas)
    inner = getattr(p, "ai", None)
    return inner if inner is not None else p


def start(canvas, panel):
    ck = _mask_mod()
    panel = _resolve_panel(canvas, panel)
    host, rec, kind = ck._host_and_rec(canvas)
    if host is None or kind != "clip":
        bar = getattr(canvas.window(), "statusBar", None)
        if callable(bar):
            bar().showMessage("先在画布上选中一条视频片段，再点选对象", 3000)
        if panel is not None and hasattr(panel, "set_prompt_mode"):
            panel.set_prompt_mode(False)
        return
    canvas._mask_mode = {"kind": "matte", "panel": panel, "drag": None}
    canvas.setCursor(Qt.CursorShape.CrossCursor)
    canvas.setFocus()
    canvas.update()


def end(canvas):
    mode = getattr(canvas, "_mask_mode", None)
    canvas._mask_mode = None
    canvas.unsetCursor()
    p = (mode or {}).get("panel")
    if p is not None and hasattr(p, "set_prompt_mode"):
        p.set_prompt_mode(False)
    canvas.update()


def hud_text(mode) -> str:
    return "AI 抠像：点 = 加正点 · Alt+点 = 负点 · 拖 = 框 · Enter = 计算 · H = 显示/隐藏彩色 · 右键 / Esc 退出"


# ───────────────────────────── 交互 ─────────────────────────────

def press(canvas, vx, vy, event, mode) -> bool:
    if event.button() == Qt.MouseButton.RightButton:
        end(canvas)
        return True
    if event.button() != Qt.MouseButton.LeftButton:
        return True
    ck = _mask_mod()
    host, rec, kind = ck._host_and_rec(canvas)
    frame = ck.Frame.of(host, rec) if host is not None and kind == "clip" else None
    if frame is None:
        end(canvas)
        return True
    neg = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
    mode["drag"] = {"start": (vx, vy), "cur": (vx, vy), "neg": neg, "frame": frame, "rec": rec, "host": host}
    return True


def move(canvas, vx, vy, event, mode) -> bool:
    d = mode.get("drag")
    if d is not None:
        d["cur"] = (vx, vy)
        canvas.update()
    return True


def release(canvas, mode) -> bool:
    d = mode.get("drag")
    if d is None:
        return True
    mode["drag"] = None
    frame = d["frame"]
    geom = _geom(d["rec"])
    (x0, y0), (x1, y1) = d["start"], d["cur"]
    unit = _mask_mod()._canvas_px(canvas)
    panel = mode.get("panel")
    if max(abs(x1 - x0), abs(y1 - y0)) >= BOX_MIN_PX * unit:
        a = A.layer_to_source_norm(*frame.to_norm(x0, y0), geom)
        b = A.layer_to_source_norm(*frame.to_norm(x1, y1), geom)
        prompt = {"box": [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]}
    else:
        sx, sy = A.layer_to_source_norm(*frame.to_norm(x0, y0), geom)
        if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
            canvas.update()
            return True
        prompt = {"x": sx, "y": sy, "label": 0 if d["neg"] else 1}
    if panel is not None and hasattr(panel, "add_prompt_from_canvas"):
        try:
            panel.add_prompt_from_canvas(prompt)
        except Exception:
            _LOG.exception("加提示点失败")
    canvas.update()
    return True


def key(canvas, event, mode) -> bool:
    k = event.key()
    if k == Qt.Key.Key_Escape:
        end(canvas)
        return True
    panel = mode.get("panel")
    if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and hasattr(panel, "compute_now"):
        # Q166：点好了一次算完
        panel.compute_now()
        canvas.update()
        return True
    if k == Qt.Key.Key_H and hasattr(panel, "set_show_tint"):
        panel.set_show_tint(not getattr(panel, "show_tint", True))
        canvas.update()
        return True
    return True


# ───────────────────────────── 绘制 ─────────────────────────────

def _overlay_image(canvas, host, rec, frame):
    sp = A.spec(host)
    if not sp["session"]:
        return None
    w = max(1, int(round(frame.rect.width())))
    h = max(1, int(round(frame.rect.height())))
    # 大画布别算太大：叠加图最多 960 宽
    scale = min(1.0, 960.0 / float(w))
    ow, oh = max(1, int(w * scale)), max(1, int(h * scale))
    geom = _geom(rec)
    layer = _mask_mod()._host_and_rec(canvas, layer=True)[0]
    src = {"src_t": _source_sec(canvas, layer if layer is not None else host), "crop": geom["crop"], "flip": geom["flip"], "order": geom["order"]}
    try:
        arr = A.overlay_rgba(host, src, ow, oh)
    except Exception:
        _LOG.exception("算叠加失败")
        return None
    if arr is None:
        return None
    img = QImage(arr.data, ow, oh, ow * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    return img


def draw(canvas, painter, mode):
    """提示模式下：当前帧每个对象的彩色结果 + 这一帧的提示点 / 框 + 正在拖的框。"""
    ck = _mask_mod()
    host, rec, kind = ck._host_and_rec(canvas)
    if host is None or kind != "clip":
        return
    frame = ck.Frame.of(host, rec)
    if frame is None:
        return
    unit = ck._canvas_px(canvas)
    sp = A.spec(host)
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        c = frame.rect.center()
        # 结果叠加（画面本身已经是「查看」模式的话就不重复盖；Q166：面板上可以关掉）
        p_tint = mode.get("panel")
        if sp["view"] != "overlay" and getattr(p_tint, "show_tint", True):
            img = _overlay_image(canvas, host, rec, frame)
            if img is not None:
                painter.save()
                painter.translate(c)
                painter.rotate(math.degrees(frame.rot))
                painter.drawImage(QRectF(-frame.rect.width() / 2.0, -frame.rect.height() / 2.0,
                                         frame.rect.width(), frame.rect.height()), img)
                painter.restore()
        # 这一帧的提示点
        fi = None
        p = mode.get("panel")
        if p is not None and hasattr(p, "current_frame"):
            try:
                fi = p.current_frame()
            except Exception:
                fi = None
        geom = _geom(rec)
        colors = {o["id"]: QColor(o["color"]) for o in sp["objects"]}
        if fi is not None:
            for pr in A.prompts_at(sp, fi):
                col = colors.get(int(pr.get("obj", 1)), QColor("#F2B33D"))
                if "box" in pr:
                    b = pr["box"]
                    pts = [frame.to_canvas(*A.source_to_layer_norm(bx, by, geom))
                           for bx, by in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))]
                    pen = QPen(col, 1.5 * unit, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    for i in range(4):
                        painter.drawLine(pts[i], pts[(i + 1) % 4])
                    continue
                pt = frame.to_canvas(*A.source_to_layer_norm(pr["x"], pr["y"], geom))
                r = DOT_PX * unit
                fill = col if int(pr.get("label", 1)) else QColor("#E04848")
                painter.setPen(QPen(QColor(255, 255, 255, 230), 1.5 * unit))
                painter.setBrush(fill)
                painter.drawEllipse(pt, r, r)
                if not int(pr.get("label", 1)):
                    painter.setPen(QPen(QColor(255, 255, 255, 230), 1.5 * unit))
                    painter.drawLine(QPointF(pt.x() - r * 0.6, pt.y()), QPointF(pt.x() + r * 0.6, pt.y()))
        # 正在拖的框
        d = mode.get("drag")
        if d is not None:
            (x0, y0), (x1, y1) = d["start"], d["cur"]
            if max(abs(x1 - x0), abs(y1 - y0)) >= BOX_MIN_PX * unit:
                painter.setPen(QPen(QColor("#FFFFFF"), 1.0 * unit, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
    finally:
        painter.restore()
