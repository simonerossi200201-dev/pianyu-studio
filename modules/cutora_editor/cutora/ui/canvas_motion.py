# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-A.4 · 画布上的运动路径（对标 After Effects 的位置路径）。

选中一个位置（x / y）打了关键帧的文本、图片或视频，画布上画出它中心走过的路线：
- 虚线 = 路线；小点 = 每一帧的位置（点越稀走得越快）；方块 = 关键帧。
- 拖方块 = 改那一刻的位置（x 和 y 一起；只开了 x 的就只改 x）。Shift 锁横 / 竖。
- 双击方块 = 播放头跳到那一刻。
拖动时直接改对象（画面跟着动），松手恢复原样再走一条 keyframe.replace —— 一次撤销。

路线怎么画：当前画面上选中框的中心，加上「x(t) − 当前 x」「y(t) − 当前 y」。
不需要知道 x / y 在文本、图片、视频里各自是左上角还是中心偏移 —— 平移量是一样的。
"""
from __future__ import annotations

import copy

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen

from cutora.core.log import get_logger
from cutora.render import kf_edit as ke
from cutora.render.keyframe import (
    HIT_EPS, keyframes_of, local_time, path_enabled, replace_paths, value_at,
)

_LOG = get_logger("cutora.ui.canvas_motion")

KEY_PX = 8.0          # 关键帧方块边长（屏幕像素）
HIT_PX = 8.0
MAX_SAMPLES = 900


def motion_host(canvas):
    """选中的、能画路径的对象 → (host, kind)。kind: overlay / clip。"""
    item = getattr(canvas, "_selected_item", None)
    if not isinstance(item, dict):
        return None, None
    typ = item.get("type")
    if typ == "overlay":
        host = canvas._record_data(item)
        return (host, "overlay") if isinstance(host, dict) else (None, None)
    if typ == "video_clip":
        clip = item.get("clip")
        return (clip, "clip") if clip is not None else (None, None)
    return None, None


def _static(host, key, default=0.0):
    raw = host.get(key, default) if isinstance(host, dict) else getattr(host, key, default)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _xy_at(host, t_local):
    x = float(value_at(host, "x", t_local, _static(host, "x"))) if path_enabled(host, "x") else _static(host, "x")
    y = float(value_at(host, "y", t_local, _static(host, "y"))) if path_enabled(host, "y") else _static(host, "y")
    return x, y


def _fps(canvas) -> float:
    try:
        from cutora.ui.canvas import _tl_of
        tl = _tl_of(canvas._task_data)
        return float(getattr(tl, "fps", 30.0) or 30.0)
    except Exception:
        return 30.0


def path_model(canvas):
    """算出路线：{"host", "kind", "keys": [(t_local, QPointF)], "samples": [QPointF], "now": QPointF}。"""
    host, kind = motion_host(canvas)
    if host is None or not (path_enabled(host, "x") or path_enabled(host, "y")):
        return None
    rect = None
    item = getattr(canvas, "_selected_item", None)
    live = getattr(canvas, "_live_record", None)
    if callable(live) and isinstance(item, dict):
        try:
            rect = (live(item) or {}).get("rect")    # 这一帧刚画出来的框（_selected_rect 会滞后）
        except Exception:
            rect = None
    if rect is None:
        rect = getattr(canvas, "_selected_rect", None)
    if rect is None or getattr(canvas, "_current_time", None) is None:
        return None
    try:
        center = QRectF(rect).center()
    except Exception:
        return None
    t_now = local_time(host, canvas._current_time)
    x_now, y_now = _xy_at(host, t_now)
    kfs = keyframes_of(host)
    times = sorted({float(p["t"]) for path in ("x", "y") for p in (kfs.get(path) or [])
                    if isinstance(p, dict) and "t" in p})
    if not times:
        return None

    def at(t):
        x, y = _xy_at(host, t)
        return QPointF(center.x() + (x - x_now), center.y() + (y - y_now))

    fps = _fps(canvas)
    t0, t1 = times[0], times[-1]
    n = int((t1 - t0) * fps) + 1
    step = 1.0 / fps
    if n > MAX_SAMPLES:
        step = (t1 - t0) / MAX_SAMPLES
        n = MAX_SAMPLES + 1
    samples = [at(t0 + i * step) for i in range(max(1, n))] + [at(t1)]
    keys = []
    for t in times:
        if not keys or abs(keys[-1][0] - t) > HIT_EPS:
            keys.append((t, at(t)))
    return {"host": host, "kind": kind, "keys": keys, "samples": samples,
            "now": at(t_now), "t_now": t_now}


def draw(canvas, painter):
    """在视频坐标系里画（画笔已经缩放过；线宽用 cosmetic）。"""
    try:
        model = path_model(canvas)
    except Exception:
        _LOG.exception("运动路径计算失败")
        model = None
    canvas._motion_model = model
    if model is None:
        return
    from cutora.ui.theme.tokens import current
    tok = current()
    scale = max(1e-6, abs(painter.transform().m11()))
    accent = QColor(tok.accent)
    painter.save()
    try:
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        line = QColor(accent)
        line.setAlpha(170)
        pen = QPen(line, 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        pts = model["samples"]
        for a, b in zip(pts, pts[1:]):
            painter.drawLine(a, b)
        dot = QPen(QColor(tok.fg0), 3.0)
        dot.setCosmetic(True)
        dot.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(dot)
        for p in pts[1:-1]:
            painter.drawPoint(p)
        half = KEY_PX / 2.0 / scale
        drag = getattr(canvas, "_motion_drag", None)
        for t, p in model["keys"]:
            on_key = abs(t - model["t_now"]) <= HIT_EPS
            hot = drag is not None and abs(drag.get("t", -1) - t) <= HIT_EPS
            edge = QPen(accent, 1.5)
            edge.setCosmetic(True)
            painter.setPen(edge)
            painter.setBrush(accent if (on_key or hot) else QColor(tok.bg0))
            painter.drawRect(QRectF(p.x() - half, p.y() - half, 2 * half, 2 * half))
    finally:
        painter.restore()


def key_at(canvas, vx, vy):
    model = getattr(canvas, "_motion_model", None)
    if not model:
        return None
    target = canvas._preview_target_rect()
    if target.width() <= 0:
        return None
    tol = HIT_PX * canvas._video_dims[0] / target.width()
    best = None
    for t, p in model["keys"]:
        d = max(abs(p.x() - vx), abs(p.y() - vy))
        if d <= tol and (best is None or d < best[0]):
            best = (d, t)
    return None if best is None else best[1]


def press(canvas, vx, vy, event) -> bool:
    if event.button() != Qt.MouseButton.LeftButton:
        return False
    t = key_at(canvas, vx, vy)
    if t is None:
        return False
    model = canvas._motion_model
    host = model["host"]
    orig = {p: ke.clone(keyframes_of(host).get(p) or []) for p in ("x", "y") if path_enabled(host, p)}
    x0, y0 = _xy_at(host, t)
    canvas._motion_drag = {"t": t, "host": host, "kind": model["kind"], "orig": orig,
                           "start": (vx, vy), "x0": x0, "y0": y0, "moved": False}
    canvas.update()
    return True


def _with_value(pts, t, v):
    out = ke.clone(pts)
    for p in out:
        if abs(float(p["t"]) - t) <= HIT_EPS:
            p["v"] = float(v)
            return out
    out.append({"t": float(t), "v": float(v), "ease": "linear"})
    return ke.clone(out)


def move(canvas, vx, vy, event) -> bool:
    d = getattr(canvas, "_motion_drag", None)
    if d is None:
        return False
    dx, dy = vx - d["start"][0], vy - d["start"][1]
    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        if abs(dx) >= abs(dy):
            dy = 0.0
        else:
            dx = 0.0
    new = {}
    if "x" in d["orig"]:
        new["x"] = _with_value(d["orig"]["x"], d["t"], d["x0"] + dx)
    if "y" in d["orig"]:
        new["y"] = _with_value(d["orig"]["y"], d["t"], d["y0"] + dy)
    try:
        replace_paths(d["host"], new)
    except Exception:
        _LOG.exception("运动路径拖动预览失败")
    d["new"] = new
    d["moved"] = d["moved"] or abs(dx) > 0.5 or abs(dy) > 0.5
    canvas.update()
    return True


def release(canvas) -> bool:
    d = getattr(canvas, "_motion_drag", None)
    if d is None:
        return False
    canvas._motion_drag = None
    host = d["host"]
    replace_paths(host, copy.deepcopy(d["orig"]))
    new = d.get("new")
    if d["moved"] and new:
        params = locator(canvas, host, d["kind"])
        if params:
            params["paths"] = new
            try:
                from cutora.core.commands import get_bus
                get_bus().execute("keyframe.replace", params, source="ui")
            except Exception as exc:
                _LOG.exception("运动路径提交失败")
                win = canvas.window()
                bar = getattr(win, "statusBar", None)
                if callable(bar):
                    bar().showMessage("位置关键帧没改成：%s" % exc, 4000)
    canvas.update()
    return True


def double_click(canvas, vx, vy) -> bool:
    t = key_at(canvas, vx, vy)
    if t is None:
        return False
    host = canvas._motion_model["host"]
    start = host.get("start", 0.0) if isinstance(host, dict) else getattr(host, "start_t", 0.0)
    win = canvas.window()
    tlw = getattr(win, "timeline", None)
    if tlw is not None and hasattr(tlw, "set_position"):
        tlw.set_position(int(round((float(start or 0.0) + t) * 1000.0)))
    return True


def locator(canvas, host, kind):
    task = getattr(canvas, "_task_data", None)
    if not isinstance(task, dict):
        return None
    if kind == "overlay":
        from cutora.render.keyframe import locate_overlay
        loc = locate_overlay(task, host)
        return dict(loc) if loc else None
    from cutora.ui.canvas import _tl_of
    tl = _tl_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        for i, c in enumerate(getattr(tr, "clips", None) or []):
            if c is host:
                return {"target": "clip", "track_id": str(getattr(tr, "id", "") or ""), "clip_index": i}
    return None
