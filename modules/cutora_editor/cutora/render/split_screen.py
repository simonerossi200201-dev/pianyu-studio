# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Split-screen composite: cell fill + feathered mask + divider stroke.

Preview and export call paint_split_video_tracks (always offscreen, then blit).
Masks and stroke paths are hashed by (cells, dividers, border, canvas).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import threading
from collections import OrderedDict

import numpy as np

from cutora.core.log import get_logger
from cutora.render.split_layout import (
    CONTAIN_LAYOUTS,
    backdrop_is_full,
    backdrop_spec,
    cell_by_id,
    normalize_split,
    pixel_rect,
    split_enabled,
)

logger = get_logger("cutora.render.split_screen")

_CACHE: OrderedDict = OrderedDict()
_CACHE_MAX = 8
_CACHE_SMALL: OrderedDict = OrderedDict()
_CACHE_SMALL_MAX = 48
_SMALL_PX = 640 * 1138
_SMALL_BUDGET_PX = 12_000_000   # 小图遮罩 + 图层合计约 12 字节/像素 → ~150 MB 封顶


def reset_cache():
    _CACHE.clear()
    _CACHE_SMALL.clear()


def _cache_key(split, canvas_w, canvas_h, cell_order=None) -> str:
    base = split.get("_ckey_base") if isinstance(split, dict) else None
    if not base:
        payload = {
            "cells": split.get("cells"),
            "dividers": split.get("dividers"),
            "border": split.get("border"),
        }
        base = hashlib.sha1(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if isinstance(split, dict):
            split["_ckey_base"] = base
    co = ",".join(cell_order) if cell_order else ""
    return f"{base}:{int(canvas_w)}x{int(canvas_h)}:{co}"


def _divider_curve(split, divider, fallback_curve: str = "straight") -> str:
    """获取单条分界线的曲线类型：
    - 若 divider 显式指定了 curve，优先使用；
    - 否则在一对多 / L 形 / 横向夹心布局中，副屏内部次级分割线默认保持 straight，主分界线跟随 border.curve。
    """
    if isinstance(divider, dict) and divider.get("curve"):
        return str(divider.get("curve"))
    base_curve = str(fallback_curve or ((split or {}).get("border") or {}).get("curve") or "straight")
    if not isinstance(divider, dict) or not isinstance(split, dict):
        return base_curve
    ch_role = str(divider.get("ch_role") or "")
    if ch_role in ("top_sub", "bottom_sub", "top_sub_v", "bottom_sub_v"):
        return "straight"
    if divider.get("l_role"):
        return "straight" if str(divider.get("l_role")) not in ("main_v", "main_h", "left_v", "bottom_h") else base_curve
    did = str(divider.get("id") or "")
    lay = str(split.get("layout") or "")
    if did not in ("", "d1") and (
        lay.startswith("top1_bottom")
        or lay.startswith("bottom1_top")
        or lay.startswith("left1_right")
        or lay.startswith("right1_left")
    ):
        return "straight"
    return base_curve


def _eval_divider_hit_norm(d_main: dict, ortho_pos: float, split: dict, w: float = 1080.0, h: float = 1920.0) -> float:
    """计算主分界线 d_main 在正交位置 ortho_pos ∈ [0,1] 处的归一化坐标（用于副屏分界线精确 T 型交汇）。"""
    axis_m = "v" if str(d_main.get("axis")) == "v" else "h"
    pos_m = max(0.0, min(1.0, float(d_main.get("pos", 0.5))))
    rot_m = float(d_main.get("rotation_deg") or 0.0)
    c_m = _divider_curve(split, d_main)
    if abs(rot_m) <= 1e-4 and c_m == "straight":
        return pos_m
    pts = divider_polyline(d_main, w, h, curve=c_m, n=65, span=(0.0, 1.0))
    if not pts:
        return pos_m
    if axis_m == "h":
        target_x = ortho_pos * w
        best_y = min(pts, key=lambda p: abs(p[0] - target_x))[1]
        return max(0.0, min(1.0, best_y / max(1.0, h)))
    target_y = ortho_pos * h
    best_x = min(pts, key=lambda p: abs(p[1] - target_y))[0]
    return max(0.0, min(1.0, best_x / max(1.0, w)))


def _divider_span(split, divider, canvas_w: float = 1080.0, canvas_h: float = 1920.0) -> tuple[float, float]:
    """返回分界线有效绘制区间 [t0, t1]：
    - 若为横向夹心副屏分界线（top_sub / bottom_sub），动态交汇到上下主分界线 d1 / d2（支持斜切与弧线）；
    - 一对多布局（top1_bottomN / bottom1_topN / left1_rightN / right1_leftN）中，副屏分界线 d2..dN 动态交汇到 d1；
    - 其余若分界线显式指定了 span=[t0, t1]（如 L 形分屏），直接采用。
    """
    if not isinstance(split, dict) or not isinstance(divider, dict):
        return 0.0, 1.0
    if str(divider.get("axis")) in ("circle", "blade"):
        return 0.0, 1.0
    w = float(canvas_w or 1080.0)
    h = float(canvas_h or 1920.0)
    ch_role = str(divider.get("ch_role") or "")
    if ch_role in ("top_sub", "bottom_sub", "top_sub_v", "bottom_sub_v"):
        is_top = ch_role in ("top_sub", "top_sub_v")
        pos_sub = max(0.0, min(1.0, float(divider.get("pos", 0.5))))
        target_id = "d1" if is_top else "d2"
        target_role = "top_h" if is_top else "bottom_h"
        for d in split.get("dividers") or []:
            if str(d.get("id") or "") == target_id or str(d.get("ch_role") or "") == target_role:
                hit = _eval_divider_hit_norm(d, pos_sub, split, w, h)
                return (0.0, hit) if is_top else (hit, 1.0)
    did = str(divider.get("id") or "")
    lay = str(split.get("layout") or "")
    is_1_to_n = did not in ("", "d1") and (
        lay.startswith("top1_bottom")
        or lay.startswith("bottom1_top")
        or lay.startswith("left1_right")
        or lay.startswith("right1_left")
        or (lay == "custom" and len(split.get("cells") or []) >= 3 and not divider.get("l_role") and not ch_role)
    )
    if is_1_to_n:
        pos_sub = max(0.0, min(1.0, float(divider.get("pos", 0.5))))
        for d in split.get("dividers") or []:
            if str(d.get("id") or "") == "d1":
                try:
                    p1 = _eval_divider_hit_norm(d, pos_sub, split, w, h)
                    r1n = str(d.get("role_1n") or "")
                    if lay.startswith("bottom1_top") or lay.startswith("right1_left") or r1n in ("bottom1_top", "right1_left"):
                        return 0.0, p1
                    return p1, 1.0
                except (TypeError, ValueError):
                    break
    raw_span = divider.get("span")
    if isinstance(raw_span, (list, tuple)) and len(raw_span) >= 2:
        try:
            t0 = max(0.0, min(1.0, float(raw_span[0])))
            t1 = max(t0, min(1.0, float(raw_span[1])))
            return t0, t1
        except (TypeError, ValueError):
            pass
    return 0.0, 1.0


def _ellipse_radii_px(r, aspect, w, h):
    base = min(float(w), float(h))
    sa = math.sqrt(max(0.25, min(4.0, float(aspect or 1.0))))
    rx_px = max(6.0, float(r) * sa * base)
    ry_px = max(6.0, (float(r) / sa) * base)
    return rx_px, ry_px


def _shape_polar_radius(theta, shape: str = "circle", sides: int = 6):
    """计算圆 / 正多边形孔径在极角 theta 处的归一化半径 R(theta)（支持标量 float 或 numpy 数组）。
    - circle：标准光滑圆；
    - polygon / hexagon / octagon / diamond：支持 sides 从 3 到 64 连续可调，64 时平滑过渡为光滑圆形。
    （注：原碎边异形已统一平滑收敛为标准圆形，保证人物与画面完整优雅。）
    """
    sh = str(shape or "circle").lower()
    is_scalar = np.ndim(theta) == 0
    th = np.asarray(theta, dtype=np.float32)
    if sh in ("polygon", "hexagon", "pentagon", "octagon", "diamond"):
        default_n = {"hexagon": 6, "pentagon": 5, "octagon": 8, "diamond": 4}.get(sh, 6)
        try:
            n_sides = max(3, min(64, int(sides if sides is not None else default_n)))
        except (TypeError, ValueError):
            n_sides = default_n
        sector = 2.0 * math.pi / float(n_sides)
        half = math.pi / float(n_sides)
        alpha = np.mod(th + math.pi * 0.5 + half, sector) - half
        r_poly = math.cos(half) / np.maximum(np.cos(alpha), 0.15)
        r_poly = r_poly * (1.0 / max(0.78, math.cos(half) * 0.5 + 0.5))
        if n_sides >= 32:
            blend = min(1.0, (float(n_sides) - 32.0) / 32.0)
            r_poly = r_poly * (1.0 - blend) + 1.0 * blend
        return float(r_poly) if is_scalar else r_poly.astype(np.float32)

    res = np.ones_like(th)
    return float(res) if is_scalar else res.astype(np.float32)


def _blade_geometry_params(divider, canvas_w, canvas_h):
    """提取快门叶片 / 多边形外围分界线的几何锚点 (cx, cy, r0_px, r_max, theta0, twist_rad)。"""
    w = float(canvas_w)
    h = float(canvas_h)
    cx = float(divider.get("cx", 0.50)) * w
    cy = float(divider.get("cy", 0.50)) * h
    r = float(divider.get("r", 0.31))
    aspect = float(divider.get("aspect", 1.0) or 1.0)
    rot_deg = float(divider.get("rotation_deg") or 0.0)
    shape = str(divider.get("shape") or "polygon")
    sides = int(divider.get("sides") or 6)
    m = max(2, int(divider.get("blade_count") or 4))
    idx = int(divider.get("blade_idx") or 0) % m
    twist_rad = math.radians(float(divider.get("twist_deg") or 0.0))

    rx_px, ry_px = _ellipse_radii_px(r, aspect, w, h)
    local_ang = -math.pi + 2.0 * math.pi * idx / float(m)
    r_sh = float(_shape_polar_radius(local_ang, shape, sides))
    lx0 = rx_px * r_sh * math.cos(local_ang)
    ly0 = ry_px * r_sh * math.sin(local_ang)
    r0_px = max(4.0, math.hypot(lx0, ly0))
    theta0 = math.atan2(ly0, lx0) + math.radians(rot_deg)
    r_max = max(r0_px + 20.0, math.hypot(w, h) * 0.78)
    return cx, cy, r0_px, r_max, theta0, twist_rad


def _curve_amp(curve: str, w: float, h: float) -> float:
    c = str(curve or "straight")
    base = min(float(w), float(h))
    if c == "wave":
        return base * 0.028
    if c == "s_wave":
        return base * 0.062
    if c in ("arc", "arch_up"):
        return base * 0.078
    return 0.0


def divider_polyline(divider, canvas_w, canvas_h, curve="straight", n=48, span=(0.0, 1.0)):
    """Same path for mask edge and stroke. n samples along the divider."""
    n = max(2, int(n))
    raw_axis = str(divider.get("axis") or "h")
    w = float(canvas_w)
    h = float(canvas_h)
    if isinstance(divider, dict) and divider.get("curve"):
        curve = str(divider.get("curve"))
    if raw_axis == "circle":
        cx = float(divider.get("cx", 0.75)) * w
        cy = float(divider.get("cy", divider.get("pos", 0.75))) * h
        r = float(divider.get("r", 0.15))
        aspect = float(divider.get("aspect", 1.0) or 1.0)
        rot = float(divider.get("rotation_deg") or 0.0)
        shape = str(divider.get("shape") or "circle")
        sides = int(divider.get("sides") or 6)
        rx_px, ry_px = _ellipse_radii_px(r, aspect, w, h)
        rad = math.radians(rot)
        ct, st = math.cos(rad), math.sin(rad)
        samples = max(96 if shape != "circle" else 64, n)
        angs = np.linspace(-math.pi, math.pi, samples, dtype=np.float32)
        rs = _shape_polar_radius(angs, shape, sides)
        pts = []
        for i in range(samples):
            ang = float(angs[i])
            rr = float(rs[i])
            lx = rx_px * rr * math.cos(ang)
            ly = ry_px * rr * math.sin(ang)
            pts.append((cx + lx * ct - ly * st, cy + lx * st + ly * ct))
        if pts:
            pts[-1] = pts[0]
        return pts
    if raw_axis == "blade":
        cx, cy, r0_px, r_max, theta0, twist_rad = _blade_geometry_params(divider, w, h)
        curve = str(curve or "straight")
        samples = max(36, n)
        pts = []
        for i in range(samples):
            s = i / float(samples - 1)
            rho = r0_px + s * (r_max - r0_px)
            c_off = 0.0
            if curve == "arc":
                c_off = 0.14 * math.sin(math.pi * s)
            elif curve == "arch_up":
                c_off = -0.14 * math.sin(math.pi * s)
            elif curve in ("wave", "s_wave"):
                c_off = 0.055 * math.sin(2.0 * math.pi * s)
            ang = theta0 + twist_rad * s + c_off
            pts.append((cx + rho * math.cos(ang), cy + rho * math.sin(ang)))
        return pts
    axis = "v" if raw_axis == "v" else "h"
    pos = float(divider.get("pos") or 0.5)
    rot = float(divider.get("rotation_deg") or 0.0)
    t0, t1 = float(span[0]), float(span[1])
    if axis == "h":
        y = pos * h
        x0, x1 = t0 * w, t1 * w
        pts = [(x0 + (x1 - x0) * i / (n - 1), y) for i in range(n)]
        origin = ((x0 + x1) * 0.5, y)
        normal = (0.0, 1.0)
    else:
        x = pos * w
        y0, y1 = t0 * h, t1 * h
        pts = [(x, y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]
        origin = (x, (y0 + y1) * 0.5)
        normal = (1.0, 0.0)
    curve = str(curve or "straight")
    amp = _curve_amp(curve, w, h)
    if curve == "wave":
        pts = [
            (p[0] + normal[0] * amp * math.sin(2.0 * math.pi * 2.0 * i / (n - 1)),
             p[1] + normal[1] * amp * math.sin(2.0 * math.pi * 2.0 * i / (n - 1)))
            for i, p in enumerate(pts)
        ]
    elif curve == "s_wave":
        pts = [
            (p[0] + normal[0] * amp * math.sin(2.0 * math.pi * i / (n - 1)),
             p[1] + normal[1] * amp * math.sin(2.0 * math.pi * i / (n - 1)))
            for i, p in enumerate(pts)
        ]
    elif curve == "arc":
        pts = [
            (p[0] + normal[0] * amp * math.sin(math.pi * i / (n - 1)),
             p[1] + normal[1] * amp * math.sin(math.pi * i / (n - 1)))
            for i, p in enumerate(pts)
        ]
    elif curve == "arch_up":
        pts = [
            (p[0] - normal[0] * amp * math.sin(math.pi * i / (n - 1)),
             p[1] - normal[1] * amp * math.sin(math.pi * i / (n - 1)))
            for i, p in enumerate(pts)
        ]
    if abs(rot) > 1e-9:
        rad = math.radians(rot)
        c, s = math.cos(rad), math.sin(rad)
        ox, oy = origin
        rot_pts = []
        for x, y in pts:
            dx, dy = x - ox, y - oy
            rot_pts.append((ox + dx * c - dy * s, oy + dx * s + dy * c))
        pts = rot_pts
    return pts


def _path_from_pts(pts):
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    if not pts:
        return path
    path.moveTo(float(pts[0][0]), float(pts[0][1]))
    for x, y in pts[1:]:
        path.lineTo(float(x), float(y))
    return path


def mask_edge_points(split, divider_id, canvas, n=20):
    sp = normalize_split(split)
    w, h = int(canvas[0]), int(canvas[1])
    curve = str((sp.get("border") or {}).get("curve") or "straight")
    for d in sp.get("dividers") or []:
        if str(d.get("id")) == str(divider_id):
            c_use = _divider_curve(sp, d, curve)
            return divider_polyline(d, w, h, curve=c_use, n=n, span=_divider_span(sp, d, w, h))
    return []


def stroke_path_points(split, divider_id, canvas, n=20):
    return mask_edge_points(split, divider_id, canvas, n=n)


def _signed_dist_line(xx, yy, divider, canvas_w, canvas_h):
    """Positive on the +axis side (below a horizontal divider / right of a vertical)."""
    axis = "v" if str(divider.get("axis")) == "v" else "h"
    pos = float(divider.get("pos") or 0.5)
    rot = float(divider.get("rotation_deg") or 0.0)
    w = float(canvas_w)
    h = float(canvas_h)
    if axis == "h":
        ox, oy = w * 0.5, pos * h
        nx, ny = 0.0, 1.0
    else:
        ox, oy = pos * w, h * 0.5
        nx, ny = 1.0, 0.0
    if abs(rot) > 1e-9:
        rad = math.radians(rot)
        c, s = math.cos(rad), math.sin(rad)
        nx, ny = nx * c - ny * s, nx * s + ny * c
    return (xx - ox) * nx + (yy - oy) * ny


def _signed_dist_poly(xx, yy, pts, sign_ref):
    """Approx SDF to polyline; sign from sign_ref (same shape as xx)."""
    h, w = xx.shape
    best = np.full((h, w), 1e9, dtype=np.float32)
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        vx, vy = x1 - x0, y1 - y0
        seglen2 = vx * vx + vy * vy + 1e-6
        t = ((xx - x0) * vx + (yy - y0) * vy) / seglen2
        t = np.clip(t, 0.0, 1.0)
        px = x0 + t * vx
        py = y0 + t * vy
        dist = np.sqrt((xx - px) ** 2 + (yy - py) ** 2)
        best = np.minimum(best, dist)
    return best * np.sign(sign_ref + 1e-9)


def _dense_n(divider, canvas_w, canvas_h):
    """Samples along the divider, at most 4px apart, so strokes don't facet."""
    raw_axis = str(divider.get("axis") or "h")
    if raw_axis == "circle":
        return 144 if str(divider.get("shape") or "circle") != "circle" else 96
    if raw_axis == "blade":
        return 64
    axis = "v" if raw_axis == "v" else "h"
    span = float(canvas_h if axis == "v" else canvas_w)
    try:
        rot = abs(float(divider.get("rotation_deg") or 0.0))
    except (TypeError, ValueError):
        rot = 0.0
    if rot > 1.0:
        span = math.hypot(float(canvas_w), float(canvas_h))
    return max(2, int(math.ceil(span / 4.0)))


def _curve_offset(curve, s_unit, amp):
    """Normal offset of the divider curve at arc parameter s in [0, 1] and its
    derivative d(offset)/d(s). Mirrors divider_polyline exactly."""
    if curve == "wave":
        k = 2.0 * math.pi * 2.0
        return amp * np.sin(k * s_unit), amp * k * np.cos(k * s_unit)
    if curve == "s_wave":
        k = 2.0 * math.pi
        return amp * np.sin(k * s_unit), amp * k * np.cos(k * s_unit)
    if curve == "arc":
        k = math.pi
        return amp * np.sin(k * s_unit), amp * k * np.cos(k * s_unit)
    if curve == "arch_up":
        k = math.pi
        return -amp * np.sin(k * s_unit), -amp * k * np.cos(k * s_unit)
    z = np.zeros_like(s_unit)
    return z, z


def _divider_sd(xx, yy, divider, canvas_w, canvas_h, curve, for_neg: bool = False):
    """Signed distance to the divider curve; positive on the +normal side."""
    raw_axis = str(divider.get("axis") or "h")
    w = float(canvas_w)
    h = float(canvas_h)
    if isinstance(divider, dict) and divider.get("curve"):
        curve = str(divider.get("curve"))
    if raw_axis == "circle":
        cx = float(divider.get("cx", 0.75)) * w
        cy = float(divider.get("cy", divider.get("pos", 0.75))) * h
        r = float(divider.get("r", 0.15))
        aspect = float(divider.get("aspect", 1.0) or 1.0)
        rot = float(divider.get("rotation_deg") or 0.0)
        shape = str(divider.get("shape") or "circle")
        sides = int(divider.get("sides") or 6)
        rx_px, ry_px = _ellipse_radii_px(r, aspect, w, h)
        dx = xx - cx
        dy = yy - cy
        if abs(rot) > 1e-4:
            rad = math.radians(rot)
            ct, st = math.cos(rad), math.sin(rad)
            lx = dx * ct + dy * st
            ly = -dx * st + dy * ct
        else:
            lx, ly = dx, dy
        nx = lx / rx_px
        ny = ly / ry_px
        if shape != "circle":
            th = np.arctan2(ny, nx)
            r_sh = _shape_polar_radius(th, shape, sides)
            nx = nx / r_sh
            ny = ny / r_sh
            rx_eff = rx_px * r_sh
            ry_eff = ry_px * r_sh
        else:
            rx_eff = rx_px
            ry_eff = ry_px
        q = np.hypot(nx, ny)
        grad = np.hypot(nx / rx_eff, ny / ry_eff)
        sd = np.where(q > 1e-5, (1.0 - q) * q / np.maximum(grad, 1e-5), min(rx_px, ry_px))
        return sd.astype(np.float32)
    if raw_axis == "blade":
        cx, cy, r0_px, r_max, theta0, twist_rad = _blade_geometry_params(divider, w, h)
        dx = xx - cx
        dy = yy - cy
        rho = np.hypot(dx, dy)
        th = np.arctan2(dy, dx)
        span_r = max(1.0, r_max - r0_px)
        s = np.clip((rho - r0_px) / span_r, 0.0, 1.5)
        curve = str(curve or "straight")
        s_clamp = np.clip(s, 0.0, 1.0)
        if curve == "arc":
            c_off = 0.14 * np.sin(math.pi * s_clamp)
            dc_ds = 0.14 * math.pi * np.cos(math.pi * s_clamp)
        elif curve == "arch_up":
            c_off = -0.14 * np.sin(math.pi * s_clamp)
            dc_ds = -0.14 * math.pi * np.cos(math.pi * s_clamp)
        elif curve in ("wave", "s_wave"):
            c_off = 0.055 * np.sin(2.0 * math.pi * s_clamp)
            dc_ds = 0.055 * 2.0 * math.pi * np.cos(2.0 * math.pi * s_clamp)
        else:
            c_off = 0.0
            dc_ds = 0.0
        ang_blade = theta0 + twist_rad * s + c_off
        m = max(2, int(divider.get("blade_count") or 4))
        cut = math.pi - (math.pi / float(m))
        if for_neg:
            d_th = -(np.mod(ang_blade - th + cut, 2.0 * math.pi) - cut)
        else:
            d_th = np.mod(th - ang_blade + cut, 2.0 * math.pi) - cut
        dphi_drho = (twist_rad + dc_ds) / span_r
        sec_factor = np.sqrt(1.0 + (rho * dphi_drho) ** 2)
        sd = (rho * d_th) / np.maximum(sec_factor, 1.0)
        return sd.astype(np.float32)
    axis = "v" if raw_axis == "v" else "h"
    pos = float(divider.get("pos") or 0.5)
    rot = float(divider.get("rotation_deg") or 0.0)
    raw_span = divider.get("span")
    t0, t1 = 0.0, 1.0
    if isinstance(raw_span, (list, tuple)) and len(raw_span) >= 2:
        try:
            t0 = max(0.0, min(1.0, float(raw_span[0])))
            t1 = max(t0, min(1.0, float(raw_span[1])))
        except (TypeError, ValueError):
            t0, t1 = 0.0, 1.0
    if axis == "h":
        x0, x1 = t0 * w, t1 * w
        ox, oy = (x0 + x1) * 0.5, pos * h
        length = max(1.0, x1 - x0)
    else:
        y0, y1 = t0 * h, t1 * h
        ox, oy = pos * w, (y0 + y1) * 0.5
        length = max(1.0, y1 - y0)
    dx = xx - ox
    dy = yy - oy
    if abs(rot) > 1e-9:
        rad = math.radians(rot)
        c, s_ = math.cos(rad), math.sin(rad)
        dx, dy = dx * c + dy * s_, -dx * s_ + dy * c
    along = dx + length * 0.5 if axis == "h" else dy + length * 0.5
    normal = dy if axis == "h" else dx
    curve = str(curve or "straight")
    if curve not in ("wave", "s_wave", "arc", "arch_up"):
        return normal
    amp = _curve_amp(curve, w, h)
    s_unit = np.clip(along / max(1.0, length), 0.0, 1.0)
    off, d_ds = _curve_offset(curve, s_unit, amp)
    slope = d_ds / max(1.0, length)
    return (normal - off) / np.sqrt(1.0 + slope * slope)


def _cell_ids(spec) -> set:
    if not spec:
        return set()
    return {p.strip() for p in str(spec).split(",") if p.strip()}


def _feather_weight(sd, feather_px):
    """sd > 0 inside. Linear ramp across ±feather, float in [0, 1]."""
    f = max(0.0, float(feather_px))
    if f < 1e-6:
        return (sd >= 0).astype(np.float32)
    return np.clip((sd + f) / (2.0 * f), 0.0, 1.0).astype(np.float32)


def _cell_feather_px(dividers, cid):
    """Last divider that names this cell wins, same as the historical mask."""
    feather = 24.0
    used = False
    for d in dividers:
        neg = _cell_ids(d.get("neg_cell"))
        pos = _cell_ids(d.get("pos_cell"))
        if cid not in neg and cid not in pos:
            continue
        used = True
        if d.get("feather_px") is not None:
            try:
                feather = float(d.get("feather_px"))
            except (TypeError, ValueError):
                pass
    if not used:
        return 24.0
    return max(0.0, float(feather))


def _quantize_partition(weights, hard):
    """Float weights → uint8 that sum to 255 on every covered pixel.

    Hard edges (feather 0) are exclusive: the later cell (higher track on timeline)
    owns a tied/overlapping pixel, matching SourceOver of opaque layers.
    Soft edges use top-down SourceOver occlusion so overlapping cells never
    interpenetrate semi-transparently while 2-cell ramps stay a convex combination.
    """
    if not weights:
        return []
    try:
        from cutora.core.rust_engine import rust_quantize_partition
        rq = rust_quantize_partition(weights, bool(hard))
        if rq is not None:
            return rq
    except Exception:
        pass
    if hard:
        owner = np.full(weights[0].shape, -1, np.int16)
        for i, wgt in enumerate(weights):
            owner[wgt >= 0.5] = np.int16(i)
        weights = [(owner == i).astype(np.float32) for i in range(len(weights))]
        stack = np.stack(weights, axis=0)
    else:
        raw_stack = np.clip(np.stack(weights, axis=0), 0.0, 1.0)
        stack = np.zeros_like(raw_stack)
        rem = np.ones(raw_stack.shape[1:], dtype=np.float32)
        for i in range(raw_stack.shape[0] - 1, -1, -1):
            take = np.minimum(rem, raw_stack[i])
            stack[i] = take
            rem = np.maximum(0.0, rem - take)
    total = stack.sum(axis=0)
    covered = total > 1e-6
    norm = np.zeros_like(stack)
    if np.any(covered):
        norm[:, covered] = stack[:, covered] / total[covered]
    q = np.floor(norm * 255.0 + 0.5).astype(np.int16)
    q[:, ~covered] = 0
    if np.any(covered):
        delta = np.zeros(total.shape, dtype=np.int16)
        delta[covered] = np.int16(255) - q.sum(axis=0)[covered]
        winner = np.argmax(stack, axis=0)
        for i in range(stack.shape[0]):
            take = (winner == i) & covered & (delta != 0)
            if np.any(take):
                q[i, take] = np.clip(q[i, take] + delta[take], 0, 255)
    return [q[i].astype(np.uint8) for i in range(q.shape[0])]


def _build_masks(split, canvas_w, canvas_h, cell_order=None):
    w, h = int(canvas_w), int(canvas_h)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx = xx + 0.5
    yy = yy + 0.5
    curve = str((split.get("border") or {}).get("curve") or "straight")
    dividers = list(split.get("dividers") or [])
    raw_cells = list(split.get("cells") or [])
    if cell_order:
        rank = {str(cid): idx for idx, cid in enumerate(cell_order)}
        raw_cells = sorted(
            enumerate(raw_cells),
            key=lambda it: (rank.get(str(it[1].get("id")), len(rank) + it[0]), it[0]),
        )
        raw_cells = [c for _idx, c in raw_cells]
    order = []
    weights = []
    feathers = []
    for cell in raw_cells:
        cid = str(cell.get("id"))
        sd = np.full((h, w), 1e6, dtype=np.float32)
        used = False
        for d in dividers:
            neg = _cell_ids(d.get("neg_cell"))
            pos = _cell_ids(d.get("pos_cell"))
            if cid not in neg and cid not in pos:
                continue
            is_neg = cid in neg
            d_curve = _divider_curve(split, d, curve)
            dist = _divider_sd(xx, yy, d, w, h, d_curve, for_neg=is_neg)
            if is_neg:
                dist = -dist
            sd = np.minimum(sd, dist.astype(np.float32))
            used = True
        feather = _cell_feather_px(dividers, cid)
        if not used:
            rect = cell.get("rect") or [0, 0, 1, 1]
            x0, y0, cw, ch = [float(v) for v in rect]
            x0 *= w
            y0 *= h
            x1 = x0 + cw * w
            y1 = y0 + ch * h
            sd = np.minimum(np.minimum(xx - x0, x1 - xx), np.minimum(yy - y0, y1 - yy))
            feather = 0.0
        order.append(cid)
        weights.append(_feather_weight(sd, feather))
        feathers.append(feather)
    hard = bool(feathers) and all(f < 1e-6 for f in feathers)
    quantized = _quantize_partition(weights, hard)
    masks = {}
    raw = {}
    for cid, alpha in zip(order, quantized):
        raw[cid] = alpha
        masks[cid] = _alpha_qimage(alpha)
    return masks, raw


def _alpha_qimage(alpha_u8):
    from PySide6.QtGui import QImage
    h, w = alpha_u8.shape
    bgra = np.zeros((h, w, 4), dtype=np.uint8)
    bgra[:, :, 3] = alpha_u8
    return QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def _content_box(cell, canvas_w, canvas_h):
    circ = cell.get("circle") if isinstance(cell, dict) else None
    if isinstance(circ, (list, tuple)) and len(circ) >= 3:
        cx, cy, r = float(circ[0]), float(circ[1]), float(circ[2])
        aspect = float(circ[3]) if len(circ) >= 4 else 1.0
        rx_px, ry_px = _ellipse_radii_px(r, aspect, canvas_w, canvas_h)
        max_r_px = max(rx_px, ry_px)
        side = max(1, int(round(2.0 * max_r_px)))
        x = int(round(cx * float(canvas_w) - max_r_px))
        y = int(round(cy * float(canvas_h) - max_r_px))
        return x, y, side, side
    rect = pixel_rect(cell.get("rect") or [0, 0, 1, 1], canvas_w, canvas_h)
    x = int(rect.x())
    y = int(rect.y())
    cw = max(1, int(round(rect.width())))
    ch = max(1, int(round(rect.height())))
    return x, y, cw, ch


def cell_content_box(split, cell_id, canvas_w, canvas_h):
    """这个格子在画布上的矩形 (x, y, w, h)；没有这个格子就返回 None。

    paint_split_video_tracks 把画笔平移到这里再交给 paint_one，所以
    「格子里的坐标 + 这个原点 = 画布坐标」。点选命中要走同一套换算，
    否则分屏下点画面点不中片段。
    """
    sp = normalize_split(split)
    cell = cell_by_id(sp, str(cell_id))
    if cell is None:
        return None
    return _content_box(cell, int(canvas_w), int(canvas_h))


def _mask_bbox(alpha, x, y, cw, ch, canvas_w, canvas_h):
    """Union of the cell rect and every pixel whose mask is non-zero."""
    ys, xs = np.nonzero(alpha)
    if xs.size:
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
    else:
        x0, y0, x1, y1 = x, y, x + cw, y + ch
    x0 = max(0, min(x0, max(0, x)))
    y0 = max(0, min(y0, max(0, y)))
    x1 = min(int(canvas_w), max(x1, x + cw))
    y1 = min(int(canvas_h), max(y1, y + ch))
    if x1 <= x0:
        x1 = min(int(canvas_w), x0 + 1)
    if y1 <= y0:
        y1 = min(int(canvas_h), y0 + 1)
    return x0, y0, x1, y1


def _cell_buffers(split, raw_masks, canvas_w, canvas_h):
    """Per-cell layer sized to the mask support, not just the cell rect.

    The feather (and a curved divider) reaches outside the rect. The layer
    covers that support so the outer half of the ramp still has pixels.
    """
    from PySide6.QtGui import QImage
    out = {}
    fmt = QImage.Format.Format_ARGB32_Premultiplied
    curve = str((split.get("border") or {}).get("curve") or "straight")
    dividers = list(split.get("dividers") or [])
    for cell in split.get("cells") or []:
        cid = str(cell.get("id"))
        x, y, cw, ch = _content_box(cell, canvas_w, canvas_h)
        alpha = raw_masks.get(cid)
        feather = _cell_feather_px(dividers, cid)
        has_rot_or_circle = any(
            abs(float(d.get("rotation_deg") or 0.0)) > 1e-4
            or str(d.get("axis")) in ("circle", "blade")
            for d in dividers
            if cid in _cell_ids(d.get("neg_cell")) or cid in _cell_ids(d.get("pos_cell"))
        )
        has_curved_div = any(
            _divider_curve(split, d, curve) != "straight"
            for d in dividers
            if cid in _cell_ids(d.get("neg_cell")) or cid in _cell_ids(d.get("pos_cell"))
        )
        clip_rect = feather < 1e-6 and not has_curved_div and not has_rot_or_circle
        if alpha is None or clip_rect:
            x0, y0 = max(0, x), max(0, y)
            x1 = min(int(canvas_w), x + cw)
            y1 = min(int(canvas_h), y + ch)
            if alpha is None:
                local = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
            else:
                local = np.ascontiguousarray(alpha[y0:y1, x0:x1])
        else:
            x0, y0, x1, y1 = _mask_bbox(alpha, x, y, cw, ch, canvas_w, canvas_h)
            local = np.ascontiguousarray(alpha[y0:y1, x0:x1])
        lw, lh = max(1, x1 - x0), max(1, y1 - y0)
        layer = QImage(lw, lh, fmt)
        layer.fill(0)
        scratch = QImage(lw, lh, fmt)
        scratch.fill(0)
        pos_m = local > 0
        if np.any(pos_m):
            inner = np.zeros_like(pos_m)
            if lh > 2 and lw > 2:
                inner[1:-1, 1:-1] = (
                    pos_m[1:-1, 1:-1]
                    & pos_m[:-2, 1:-1]
                    & pos_m[2:, 1:-1]
                    & pos_m[1:-1, :-2]
                    & pos_m[1:-1, 2:]
                )
            mask_edge_yx = np.nonzero(pos_m & ~inner)
        else:
            mask_edge_yx = None
        is_circ_cell = isinstance(cell.get("circle"), (list, tuple))
        if feather < 1e-6 and not clip_rect and not is_circ_cell:
            content_box = (0, 0, int(lw), int(lh))
        else:
            content_box = (int(x - x0), int(y - y0), int(cw), int(ch))
        out[cid] = {
            "origin": (int(x0), int(y0)),
            "content": content_box,
            "mask": _alpha_qimage(local),
            "layer": layer,
            "scratch": scratch,
            "extend": content_box != (0, 0, int(lw), int(lh)) and not (x0 == x and y0 == y and (x1 - x0) == cw and (y1 - y0) == ch),
            "tilted": bool(has_rot_or_circle or has_curved_div),
            "spans": _mask_spans(local),
            "mask_edge_yx": mask_edge_yx,
        }
    return out


def _mask_spans(alpha_u8):
    """Q130 / 2D Interior Solid Rect：把 mask 切成「整块 255 内部矩形」和「边缘羽化条带」，
    直拷前者（90%~98% 面积）、只对后者过 mask，逐字节等价。
    """
    from PySide6.QtCore import QRect
    if alpha_u8 is None or alpha_u8.size == 0:
        return {"rects": True, "solid": [], "band": []}
    h, w = alpha_u8.shape
    not_full = (alpha_u8 != 255)
    if not not_full.any():
        return {"rects": True, "solid": [QRect(0, 0, int(w), int(h))], "band": []}

    y0, y1, x0, x1 = 0, int(h), 0, int(w)
    while y0 < y1 and x0 < x1:
        top_bad = int(not_full[y0, x0:x1].sum())
        bot_bad = int(not_full[y1 - 1, x0:x1].sum())
        left_bad = int(not_full[y0:y1, x0].sum())
        right_bad = int(not_full[y0:y1, x1 - 1].sum())
        if top_bad == 0 and bot_bad == 0 and left_bad == 0 and right_bad == 0:
            break
        hw = max(1, x1 - x0)
        hh = max(1, y1 - y0)
        scores = (
            (top_bad / hw, 0),
            (bot_bad / hw, 1),
            (left_bad / hh, 2),
            (right_bad / hh, 3),
        )
        _, side = max(scores)
        if side == 0:
            y0 += 1
        elif side == 1:
            y1 -= 1
        elif side == 2:
            x0 += 1
        else:
            x1 -= 1

    if y0 < y1 and x0 < x1 and not not_full[y0:y1, x0:x1].any():
        candidates = [
            (0, 0, w, y0),
            (0, y1, w, h - y1),
            (0, y0, x0, y1 - y0),
            (x1, y0, w - x1, y1 - y0),
            (x0, y0, x1 - x0, y1 - y0),
        ]
        solid, band = [], []
        for rx, ry, rw, rh in candidates:
            if rw <= 0 or rh <= 0:
                continue
            sub = alpha_u8[ry:ry + rh, rx:rx + rw]
            if not sub.any():
                continue
            if (sub == 255).all():
                solid.append(QRect(int(rx), int(ry), int(rw), int(rh)))
            else:
                band.append(QRect(int(rx), int(ry), int(rw), int(rh)))
        return {"rects": True, "solid": solid, "band": band}

    def _plan(axis):
        full = (alpha_u8 == 255).all(axis=1 if axis == "y" else 0)
        empty = (alpha_u8 == 0).all(axis=1 if axis == "y" else 0)
        n = full.shape[0]
        solid, band = [], []
        i = 0
        while i < n:
            if full[i]:
                j = i
                while j < n and full[j]:
                    j += 1
                solid.append((i, j))
            elif empty[i]:
                j = i
                while j < n and empty[j] and not full[j]:
                    j += 1
            else:
                j = i
                while j < n and not full[j] and not empty[j]:
                    j += 1
                band.append((i, j))
            i = j
        length = (w if axis == "y" else h)
        band_px = sum((b - a) for a, b in band) * length
        return {"axis": axis, "solid": solid, "band": band}, band_px

    py, cy = _plan("y")
    px, cx = _plan("x")
    return py if cy <= cx else px


def _view_bgra(layer):
    """Writable HxWx4 view. Caller must keep ``layer`` alive."""
    h, w = layer.height(), layer.width()
    bpl = layer.bytesPerLine()
    ptr = layer.bits()
    raw = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
    if bpl == w * 4:
        return raw.reshape(h, w, 4)
    padded = raw.reshape(h, bpl)
    view = np.ascontiguousarray(padded[:, : w * 4]).reshape(h, w, 4)
    return padded, view


def _replicate_into(region, edge):
    if region.size == 0:
        return
    a = region[:, :, 3]
    if int(a.max()) == 0:
        region[:] = edge
        return
    h, w = a.shape
    if edge.shape[1] == 1 and w > 1:
        r0, rm, r1 = a[0] == 0, a[h >> 1] == 0, a[-1] == 0
        if np.array_equal(r0, rm) and np.array_equal(r0, r1):
            idx = np.flatnonzero(r0)
            if idx.size == 0:
                return
            c0, c1 = int(idx[0]), int(idx[-1]) + 1
            if c1 - c0 == idx.size:
                region[:, c0:c1, :] = edge
                return
    elif edge.shape[0] == 1 and h > 1:
        c0_col, cm_col, c1_col = a[:, 0] == 0, a[:, w >> 1] == 0, a[:, -1] == 0
        if np.array_equal(c0_col, cm_col) and np.array_equal(c0_col, c1_col):
            idx = np.flatnonzero(c0_col)
            if idx.size == 0:
                return
            r0_i, r1_i = int(idx[0]), int(idx[-1]) + 1
            if r1_i - r0_i == idx.size:
                region[r0_i:r1_i, :, :] = edge
                return
    empty = a == 0
    if not np.any(empty):
        return
    src = np.broadcast_to(np.ascontiguousarray(edge), region.shape)
    region[empty] = src[empty]


_EXT_BLUR_LOCK = threading.Lock()
_EXT_BLUR_CACHE: OrderedDict = OrderedDict()
_EXT_BLUR_MAX = 1024


def _extend_edges(layer, cx, cy, cw, ch, tilted=False, src_qimg=None, mask_edge_yx=None):
    """Fill unpainted margin up to the divider mask.
    - For non-rotated dividers whose content box (cx, cy, cw, ch) is already fully opaque,
      only the feather/curve band exists outside (cx, cy, cw, ch), so fast edge replication
      is used (<0.02 ms, no dark seam, pixel-identical to golden baselines).
    - When a rotated/circle divider or a scaled-down clip leaves a visible unpainted margin,
      fill the unpainted pixels with a smooth Lens-Blurred background of the cell content."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter
    from cutora.render.fill_bg import blur_image, cover_rect, lens_blur

    h, w = layer.height(), layer.width()
    if h <= 0 or w <= 0:
        return
    got = _view_bgra(layer)
    if isinstance(got, tuple):
        padded, view = got
        writeback = padded
    else:
        view = got
        writeback = None

    x0 = max(0, min(w, int(cx)))
    y0 = max(0, min(h, int(cy)))
    x1 = max(x0, min(w, int(cx) + int(cw)))
    y1 = max(y0, min(h, int(cy) + int(ch)))
    if x1 <= x0 or y1 <= y0:
        return

    # Fast check: are the 4 outer edges and content box boundaries already opaque?
    content_opaque = (
        int(view[y0, x0:x1, 3].min()) >= 250
        and int(view[y1 - 1, x0:x1, 3].min()) >= 250
        and int(view[y0:y1, x0, 3].min()) >= 250
        and int(view[y0:y1, x1 - 1, 3].min()) >= 250
    )
    outer_opaque = (
        int(view[0, :, 3].min()) == 255
        and int(view[h - 1, :, 3].min()) == 255
        and int(view[:, 0, 3].min()) == 255
        and int(view[:, w - 1, 3].min()) == 255
    )
    if content_opaque and outer_opaque:
        return

    use_replicate = False
    if content_opaque:
        if not tilted:
            use_replicate = True
        else:
            ix0 = min(x0, min(w - 1, 16))
            iy0 = min(y0, min(h - 1, 16))
            ix1 = max(x1, max(ix0 + 1, w - 16))
            iy1 = max(y1, max(iy0 + 1, h - 16))
            use_replicate = (
                int(view[iy0, ix0:ix1, 3].min()) >= 250
                and int(view[iy1 - 1, ix0:ix1, 3].min()) >= 250
                and int(view[iy0:iy1, ix0, 3].min()) >= 250
                and int(view[iy0:iy1, ix1 - 1, 3].min()) >= 250
            )

    if use_replicate:
        if x0 > 0:
            _replicate_into(view[y0:y1, 0:x0, :], view[y0:y1, x0:x0 + 1, :])
        if x1 < w:
            _replicate_into(view[y0:y1, x1:w, :], view[y0:y1, x1 - 1:x1, :])
        if y0 > 0:
            _replicate_into(view[0:y0, :, :], view[y0:y0 + 1, :, :])
        if y1 < h:
            _replicate_into(view[y1:h, :, :], view[y1 - 1:y1, :, :])
        if writeback is not None:
            writeback[:, : w * 4] = view.reshape(h, w * 4)
        return

    # Fast check on mask boundary: if every pixel inside the cell mask is already opaque,
    # any unpainted margin lies completely outside the mask (alpha==0) and will be discarded!
    if mask_edge_yx is not None and len(mask_edge_yx) == 2 and mask_edge_yx[0].size > 0:
        if int(view[mask_edge_yx[0], mask_edge_yx[1], 3].min()) >= 250:
            return

    # Visible margin exists (either scaled-down clip with bg_fill='none' or tilted/circle wedge):
    if src_qimg is not None and not isinstance(src_qimg, bool) and not src_qimg.isNull():
        sw, sh = max(1, src_qimg.width()), max(1, src_qimg.height())
        bkey = (int(src_qimg.cacheKey()), bool(content_opaque))
        with _EXT_BLUR_LOCK:
            small = _EXT_BLUR_CACHE.get(bkey)
            if small is not None:
                _EXT_BLUR_CACHE.move_to_end(bkey)
        if small is None:
            small = blur_image(src_qimg, float(CONTAIN_BLUR), "legacy")
            if small is None or small.isNull():
                small = src_qimg
            elif not content_opaque:
                dim = int(round(CONTAIN_DIM * 255.0))
                if dim > 0:
                    small = small.copy()
                    dp = QPainter(small)
                    try:
                        dp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                        dp.fillRect(0, 0, small.width(), small.height(), QColor(0, 0, 0, dim))
                    except Exception:
                        pass
                    finally:
                        dp.end()
            with _EXT_BLUR_LOCK:
                _EXT_BLUR_CACHE[bkey] = small
                while len(_EXT_BLUR_CACHE) > _EXT_BLUR_MAX:
                    _EXT_BLUR_CACHE.popitem(last=False)
    else:
        if content_opaque:
            sx0, sy0, sx1, sy1 = x0, y0, x1, y1
        else:
            sub_alpha = view[y0:y1, x0:x1, 3]
            if int(sub_alpha[sub_alpha.shape[0] >> 1, sub_alpha.shape[1] >> 1]) == 0 and not np.any(sub_alpha):
                return
            rys = np.flatnonzero(sub_alpha.any(axis=1))
            rxs = np.flatnonzero(sub_alpha.any(axis=0))
            if rxs.size > 0 and rys.size > 0:
                sx0 = x0 + int(rxs[0])
                sy0 = y0 + int(rys[0])
                sx1 = x0 + int(rxs[-1]) + 1
                sy1 = y0 + int(rys[-1]) + 1
            else:
                return
        sw, sh = max(1, sx1 - sx0), max(1, sy1 - sy0)
        chip = layer.copy(sx0, sy0, sw, sh)
        if chip.isNull():
            return
        small = blur_image(chip, float(CONTAIN_BLUR), "legacy")
        if small is None or small.isNull():
            small = chip
        elif not content_opaque:
            dim = int(round(CONTAIN_DIM * 255.0))
            if dim > 0:
                dp = QPainter(small)
                try:
                    dp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                    dp.fillRect(0, 0, small.width(), small.height(), QColor(0, 0, 0, dim))
                finally:
                    dp.end()
    lp = QPainter(layer)
    try:
        lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
        r = cover_rect(sw, sh, w, h)
        lp.drawImage(QRectF(r).translated(w / 2.0, h / 2.0), small)
    finally:
        lp.end()


class _SplitCache:
    __slots__ = (
        "key", "masks", "mask_u8", "polylines", "scratch", "w", "h",
        "glass_band", "glass_width", "glass_sigma", "cells", "off", "border_overlay",
    )

    def __init__(self, key, split, w, h, cell_order=None):
        from PySide6.QtGui import QImage
        self.key = key
        self.w = int(w)
        self.h = int(h)
        t0 = time.perf_counter()
        self.masks, self.mask_u8 = _build_masks(split, w, h, cell_order=cell_order)
        self.cells = _cell_buffers(split, self.mask_u8, self.w, self.h)
        curve = str((split.get("border") or {}).get("curve") or "straight")
        self.polylines = {}
        for d in split.get("dividers") or []:
            did = str(d.get("id"))
            c_use = _divider_curve(split, d, curve)
            pts = divider_polyline(d, w, h, curve=c_use, n=_dense_n(d, w, h), span=_divider_span(split, d, w, h))
            self.polylines[did] = pts
        # Full-frame scratch is gone: each cell blits its own layer-sized buffer.
        self.scratch = None
        self.off = QImage(self.w, self.h, QImage.Format.Format_ARGB32_Premultiplied)
        self.off.fill(0)
        border = split.get("border") or {}
        self.glass_width = max(1.0, float(border.get("width_px") or 6))
        self.glass_sigma = float(border.get("glass_sigma") or 8)
        if str(border.get("style") or "none") == "glass":
            self.glass_band = _glass_band_mask(
                self.polylines, self.w, self.h, self.glass_width, self.glass_sigma)
            self.border_overlay = None
        else:
            self.glass_band = None
            self.border_overlay = _build_border_overlay(split, self)
        ms = (time.perf_counter() - t0) * 1000.0
        logger.info("split cache build %sx%s in %.2f ms", w, h, ms)

    @property
    def paths(self):
        return {did: _path_from_pts(pts) for did, pts in (self.polylines or {}).items()}


_OFF_TLS = threading.local()
_CACHE_LOCK = threading.Lock()


def _thread_scratch(cache):
    """拼格子用的离屏图 + 每格的 layer / scratch：每个线程一份（Q180 导出多线程合成时几个线程同时画分屏，
    不能共用缓存里那几张）。主线程（预览 / 串行导出）照旧用缓存里的。返回 (off, cells)。"""
    if threading.current_thread() is threading.main_thread():
        return cache.off, cache.cells
    store = getattr(_OFF_TLS, "imgs", None)
    if store is None:
        store = _OFF_TLS.imgs = {}
    hit = store.get(id(cache))
    if hit is not None and hit[0] is cache:
        return hit[1], hit[2]
    from PySide6.QtGui import QImage
    if len(store) > 4:
        store.clear()
    off = QImage(cache.off.width(), cache.off.height(), QImage.Format.Format_ARGB32_Premultiplied)
    cells = {}
    for cid, geom in (cache.cells or {}).items():
        g = dict(geom)
        for k in ("layer", "scratch"):
            img = geom.get(k)
            if img is not None:
                g[k] = QImage(img.width(), img.height(), img.format())
                g[k].fill(0)
        cells[cid] = g
    store[id(cache)] = (cache, off, cells)
    return off, cells


def get_cache(split, canvas_w, canvas_h, _normed: bool = False, cell_order=None) -> _SplitCache:
    sp = split if (_normed and isinstance(split, dict) and "cells" in split) else normalize_split(split)
    key = _cache_key(sp, canvas_w, canvas_h, cell_order=cell_order)
    # Q144：中央监视器按缩略图尺寸画几十个分屏；小图单独一个 LRU，
    # 不把主画布那几份大遮罩（一份 300 ms）挤出去。
    small = int(canvas_w) * int(canvas_h) <= _SMALL_PX
    store, cap = (_CACHE_SMALL, _CACHE_SMALL_MAX) if small else (_CACHE, _CACHE_MAX)
    with _CACHE_LOCK:
        hit = store.get(key)
        if hit is not None:
            try:
                store.move_to_end(key)
            except KeyError:
                pass
            return hit
        built = _SplitCache(key, sp, canvas_w, canvas_h, cell_order=cell_order)
        store[key] = built
        while len(store) > cap or (small and len(store) > 1 and sum(
                int(c.w) * int(c.h) for c in list(store.values())) > _SMALL_BUDGET_PX):
            try:
                store.popitem(last=False)
            except KeyError:
                break
        return built


def cell_mask_qimage(split, cell_id, canvas_w, canvas_h):
    cache = get_cache(split, canvas_w, canvas_h)
    img = cache.masks.get(str(cell_id))
    if img is None:
        from PySide6.QtGui import QImage
        img = QImage(int(canvas_w), int(canvas_h), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        logger.warning("没有格子 %s 的 mask", cell_id)
    return img


def _qimage_rgb(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    arr = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def mean_abs_diff(a, b) -> float:
    if a is None or b is None or a.isNull() or b.isNull():
        return 1.0
    if a.width() != b.width() or a.height() != b.height():
        logger.warning("MAD 尺寸不同 %sx%s vs %sx%s", a.width(), a.height(), b.width(), b.height())
        return 1.0
    aa = _qimage_rgb(a).astype(np.float64)
    bb = _qimage_rgb(b).astype(np.float64)
    return float(np.abs(aa - bb).mean() / 255.0)


def _parse_color(text, fallback=(242, 193, 78, 255)):
    from PySide6.QtGui import QColor
    c = QColor(str(text or ""))
    if not c.isValid():
        r, g, b, a = fallback
        return QColor(r, g, b, a)
    return c


def _build_border_overlay(split, cache):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QBrush

    border = split.get("border") or {}
    style = str(border.get("style") or "none")
    if style in ("none", "glass") or not cache.polylines:
        return None
    width = max(1.0, float(border.get("width_px") or 6))
    color = _parse_color(border.get("color"))
    color2 = _parse_color(border.get("color2"), (255, 255, 255, 255))
    shadow_px = float(border.get("shadow_px") or 12)
    bkey = (style, round(width, 3), int(color.rgba()), int(color2.rgba()), round(shadow_px, 3))
    pad = int(math.ceil(width + (shadow_px * 0.5 if style == "shadow" else 0.0) + 4.0))
    xs = [p[0] for pts in (cache.polylines or {}).values() for p in pts]
    ys = [p[1] for pts in (cache.polylines or {}).values() for p in pts]
    if not xs or not ys:
        return None
    bx0 = max(0, int(math.floor(min(xs))) - pad)
    by0 = max(0, int(math.floor(min(ys))) - pad)
    bx1 = min(int(cache.w), int(math.ceil(max(xs))) + pad)
    by1 = min(int(cache.h), int(math.ceil(max(ys))) + pad)
    if bx1 <= bx0 or by1 <= by0:
        return None
    bimg = QImage(bx1 - bx0, by1 - by0, QImage.Format.Format_ARGB32_Premultiplied)
    bimg.fill(0)
    bp = QPainter(bimg)
    try:
        bp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if bx0 or by0:
            bp.translate(-bx0, -by0)
        for did, pts in (cache.polylines or {}).items():
            path = _path_from_pts(pts)
            if path is None or path.isEmpty():
                continue
            bp.save()
            try:
                if style == "shadow":
                    sh = QColor(0, 0, 0, 110)
                    pen_s = QPen(sh, width + shadow_px * 0.5)
                    pen_s.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen_s.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    bp.setPen(pen_s)
                    bp.setBrush(Qt.BrushStyle.NoBrush)
                    bp.drawPath(path)
                if style == "gradient":
                    if len(pts) >= 2:
                        g = QLinearGradient(QPointF(*pts[0]), QPointF(*pts[-1]))
                        g.setColorAt(0.0, color)
                        g.setColorAt(1.0, color2)
                        pen = QPen(QBrush(g), width)
                    else:
                        pen = QPen(color, width)
                else:
                    pen = QPen(color, width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                bp.setPen(pen)
                bp.setBrush(Qt.BrushStyle.NoBrush)
                bp.drawPath(path)
            finally:
                bp.restore()
    finally:
        bp.end()
    bw, bh = bimg.width(), bimg.height()
    strips = []
    try:
        bpl = bimg.bytesPerLine()
        arr = np.frombuffer(bimg.constBits(), dtype=np.uint8, count=bh * bpl).reshape(bh, bpl)
        alpha = arr[:, 3:bw * 4:4]
        tile = 128
        for ty0 in range(0, bh, tile):
            ty1 = min(bh, ty0 + tile)
            for tx0 in range(0, bw, tile):
                tx1 = min(bw, tx0 + tile)
                sub_a = alpha[ty0:ty1, tx0:tx1]
                if not np.any(sub_a):
                    continue
                ys_nz, xs_nz = np.nonzero(sub_a)
                ry0 = ty0 + int(ys_nz.min())
                ry1 = ty0 + int(ys_nz.max()) + 1
                rx0 = tx0 + int(xs_nz.min())
                rx1 = tx0 + int(xs_nz.max()) + 1
                strips.append((bx0 + rx0, by0 + ry0, bimg.copy(rx0, ry0, rx1 - rx0, ry1 - ry0)))
    except Exception:
        strips = [(bx0, by0, bimg.copy())]
    return (bkey, strips)


def _draw_borders(painter, split, cache, composed):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QBrush

    border = split.get("border") or {}
    style = str(border.get("style") or "none")
    if style == "none":
        return
    width = max(1.0, float(border.get("width_px") or 6))
    color = _parse_color(border.get("color"))
    color2 = _parse_color(border.get("color2"), (255, 255, 255, 255))
    shadow_px = float(border.get("shadow_px") or 12)
    sigma = float(border.get("glass_sigma") or 8)

    if style != "glass":
        bkey = (style, round(width, 3), int(color.rgba()), int(color2.rgba()), round(shadow_px, 3))
        ov = getattr(cache, "border_overlay", None)
        if ov is None or ov[0] != bkey:
            ov = _build_border_overlay(split, cache)
            try:
                cache.border_overlay = ov
            except Exception:
                pass
        if ov is not None:
            for sx, sy, simg in ov[1]:
                painter.drawImage(int(sx), int(sy), simg)
            return

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if style == "glass" and composed is not None and not composed.isNull():
        _draw_glass_band(painter, composed, cache, width, sigma, color)
        return

    for did, pts in (cache.polylines or {}).items():
        path = _path_from_pts(pts)
        if path is None or path.isEmpty():
            continue
        painter.save()
        try:
            if style == "shadow":
                sh = QColor(0, 0, 0, 110)
                pen_s = QPen(sh, width + shadow_px * 0.5)
                pen_s.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen_s.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen_s)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            if style == "gradient":
                if len(pts) >= 2:
                    g = QLinearGradient(QPointF(*pts[0]), QPointF(*pts[-1]))
                    g.setColorAt(0.0, color)
                    g.setColorAt(1.0, color2)
                    pen = QPen(QBrush(g), width)
                else:
                    pen = QPen(color, width)
            else:
                pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if style != "glass":
                painter.drawPath(path)
        finally:
            painter.restore()


def _offset_polyline(pts, dist):
    """Shift a polyline along its left normal. dist may be negative."""
    if len(pts) < 2 or abs(float(dist)) < 1e-6:
        return list(pts)
    out = []
    last = len(pts) - 1
    for i, (x, y) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - x, pts[1][1] - y
        elif i == last:
            dx, dy = x - pts[i - 1][0], y - pts[i - 1][1]
        else:
            dx = pts[i + 1][0] - pts[i - 1][0]
            dy = pts[i + 1][1] - pts[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        out.append((x + nx * dist, y + ny * dist))
    return out


def _glass_band_mask(polylines, w, h, width, sigma):
    """Soft frosted strip, distance field only inside each divider's bbox.

    Polylines are already sampled at ≤4px, so every segment is used — no
    16-point corners. Cached once with the split geometry (plain Python tuples,
    never QPainterPath objects across threads).
    """
    core = max(0.75, float(width) * 0.5)
    soft = max(1.25, min(4.0, core * 0.35))
    pad = core + soft + max(2.0, float(sigma) * 3.0)
    bands = []
    for pts in (polylines or {}).values():
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0 = max(0, int(math.floor(min(xs) - pad)))
        y0 = max(0, int(math.floor(min(ys) - pad)))
        x1 = min(int(w), int(math.ceil(max(xs) + pad)))
        y1 = min(int(h), int(math.ceil(max(ys) + pad)))
        if x1 <= x0 or y1 <= y0:
            continue
        rh, rw = y1 - y0, x1 - x0
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        xx += 0.5
        yy += 0.5
        dmin = np.full((rh, rw), 1e9, dtype=np.float32)
        for i in range(len(pts) - 1):
            xa, ya = pts[i]
            xb, yb = pts[i + 1]
            vx, vy = xb - xa, yb - ya
            seglen2 = vx * vx + vy * vy + 1e-6
            t = np.clip(((xx - xa) * vx + (yy - ya) * vy) / seglen2, 0.0, 1.0)
            d = np.sqrt((xx - (xa + t * vx)) ** 2 + (yy - (ya + t * vy)) ** 2)
            dmin = np.minimum(dmin, d)
        mask = np.clip((core + soft - dmin) / (2.0 * soft), 0.0, 1.0).astype(np.float32)
        hi_pts, lo_pts = [], []
        for sign in (-1.0, 1.0):
            hi_pts.append(_offset_polyline(pts, sign * max(0.4, core - 0.5)))
            lo_pts.append(_offset_polyline(pts, sign * (core + 0.5)))
        bands.append({
            "rect": (x0, y0, x1, y1),
            "mask": mask,
            "hi_pts": hi_pts,
            "lo_pts": lo_pts,
        })
    return bands


def _draw_glass_band(painter, composed, cache, width, sigma, color):
    """Blur the divider strip only, then a 1px highlight and a 1px dark edge."""
    import cv2
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    bands = getattr(cache, "glass_band", None)
    if not isinstance(bands, list):
        bands = _glass_band_mask(
            cache.polylines, composed.width(), composed.height(), width, sigma)
        cache.glass_band = bands
    if not bands:
        return
    sigma = max(0.4, float(sigma))
    k = int(max(3, round(sigma * 2.0) * 2 + 1))
    lift = 0.065
    cr, cg, cb = int(color.red()), int(color.green()), int(color.blue())
    lr = 255.0 * 0.72 + cr * 0.28
    lg = 255.0 * 0.72 + cg * 0.28
    lb = 255.0 * 0.72 + cb * 0.28
    for entry in bands:
        x0, y0, x1, y1 = entry["rect"]
        rw, rh = int(x1 - x0), int(y1 - y0)
        chip = composed.copy(int(x0), int(y0), rw, rh)
        if chip.isNull() or chip.width() != rw or chip.height() != rh:
            continue
        chip = chip.convertToFormat(QImage.Format.Format_ARGB32)
        mask = entry["mask"]
        if mask.shape[0] != rh or mask.shape[1] != rw:
            continue
        bpl = chip.bytesPerLine()
        raw = chip.constBits()
        try:
            src = np.frombuffer(raw, dtype=np.uint8, count=rh * bpl).reshape(rh, bpl)
        except (TypeError, BufferError, ValueError):
            src = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(rh, bpl)
        bgra = np.ascontiguousarray(src[:, : rw * 4]).reshape(rh, rw, 4)
        kk = k
        limit = min(rw, rh)
        if kk > limit:
            kk = max(1, limit if limit % 2 else limit - 1)
        blurred = cv2.GaussianBlur(bgra, (kk, kk), sigma)
        rgb = blurred[:, :, :3].astype(np.float32)
        rgb[:, :, 0] = rgb[:, :, 0] * (1.0 - lift) + lb * lift
        rgb[:, :, 1] = rgb[:, :, 1] * (1.0 - lift) + lg * lift
        rgb[:, :, 2] = rgb[:, :, 2] * (1.0 - lift) + lr * lift
        a = mask.astype(np.float32)
        out = np.empty((rh, rw, 4), np.uint8)
        out[:, :, 0] = np.clip(rgb[:, :, 0] * a, 0, 255).astype(np.uint8)
        out[:, :, 1] = np.clip(rgb[:, :, 1] * a, 0, 255).astype(np.uint8)
        out[:, :, 2] = np.clip(rgb[:, :, 2] * a, 0, 255).astype(np.uint8)
        out[:, :, 3] = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
        qimg = QImage(
            out.data, rw, rh, rw * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        painter.drawImage(int(x0), int(y0), qimg)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        hi_pen = QPen(QColor(255, 255, 255, 188), 1.0)
        lo_pen = QPen(QColor(0, 0, 0, 96), 1.0)
        for pen in (hi_pen, lo_pen):
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        for entry in bands:
            painter.setPen(hi_pen)
            for pts in entry.get("hi_pts") or ():
                path = _path_from_pts(pts)
                if path is not None and not path.isEmpty():
                    painter.drawPath(path)
            painter.setPen(lo_pen)
            for pts in entry.get("lo_pts") or ():
                path = _path_from_pts(pts)
                if path is not None and not path.isEmpty():
                    painter.drawPath(path)
    finally:
        painter.restore()


def _blit_masked(painter, img, origin, mask, scratch, spans=None, img_opaque=False):
    """Mask a layer-sized image, then add it at origin. Weights sum to 255.

    Q130：给了 ``spans`` 就只让羽化条带过 mask，整段 255 的部分直接 Plus 上去
    （逐字节等价，见 _mask_spans）。没给就是原来的整幅路径。
    """
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QPainter

    ox, oy = int(origin[0]), int(origin[1])
    if mask is None or mask.isNull():
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
            if img_opaque
            else QPainter.CompositionMode.CompositionMode_Plus
        )
        painter.drawImage(QPoint(ox, oy), img)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        return
    w, h = img.width(), img.height()
    if spans is None:
        scratch.fill(0)
        sp = QPainter(scratch)
        try:
            sp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            sp.drawImage(0, 0, img)
            sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            sp.drawImage(0, 0, mask)
        finally:
            sp.end()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.drawImage(QPoint(ox, oy), scratch)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        return

    if spans.get("rects"):
        solid_rects = spans.get("solid") or ()
        band_rects = spans.get("band") or ()
    else:
        axis = spans.get("axis") or "y"

        def _rect(a, b):
            if axis == "y":
                return QRect(0, int(a), w, int(b - a))
            return QRect(int(a), 0, int(b - a), h)

        solid_rects = [_rect(a, b) for a, b in (spans.get("solid") or ())]
        band_rects = [_rect(a, b) for a, b in (spans.get("band") or ())]

    from PySide6.QtGui import QImage as _QImage
    dev = painter.device()
    is_premasked = bool(getattr(img, "_is_premasked_cell", False))
    if (
        isinstance(dev, _QImage)
        and painter.worldTransform().isIdentity()
        and (img_opaque or is_premasked)
    ):
        try:
            from cutora.core.rust_engine import rust_blit_cell_spans
            if rust_blit_cell_spans(
                img,
                mask,
                dev,
                solid_rects,
                band_rects,
                ox,
                oy,
                img_opaque=True,
                premasked=is_premasked,
            ):
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                return
        except Exception:
            pass

    if solid_rects:
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
            if img_opaque
            else QPainter.CompositionMode.CompositionMode_Plus
        )
        for r in solid_rects:
            painter.drawImage(QPoint(ox + r.x(), oy + r.y()), img, r)
    if band_rects:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        if getattr(img, "_is_premasked_cell", False):
            for r in band_rects:
                painter.drawImage(QPoint(ox + r.x(), oy + r.y()), img, r)
        else:
            done_rust = False
            if isinstance(dev, _QImage) and painter.worldTransform().isIdentity():
                try:
                    from cutora.core.rust_engine import rust_blit_masked_band_plus
                    done_rust = rust_blit_masked_band_plus(img, mask, dev, band_rects, ox, oy)
                except Exception:
                    done_rust = False
            if not done_rust:
                sp = QPainter(scratch)
                try:
                    sp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                    for r in band_rects:
                        sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                        sp.drawImage(r.topLeft(), img, r)
                        sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                        sp.drawImage(r.topLeft(), mask, r)
                finally:
                    sp.end()
                for r in band_rects:
                    painter.drawImage(QPoint(ox + r.x(), oy + r.y()), scratch, r)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


# 左右完整：两格几何和「左右」一样，但每格按适应画，源画面完整落在格内。
# 留白处垫模糊背景：Q151 起按分屏的 backdrop 设置 —— 每格垫自己那条的镜头模糊
# （each），或整幅垫第一条（first，Q148 原做法；没写 backdrop 的老分屏就是它）。
CONTAIN_BLUR = 45.0
CONTAIN_DIM = 0.25
_FIT_MISSING = object()


def layout_contains_sources(layout) -> bool:
    return str(layout or "") in CONTAIN_LAYOUTS


def _iter_clips(track):
    clips = getattr(track, "clips", None)
    if clips is None and isinstance(track, dict):
        clips = track.get("clips")
    return list(clips or [])


def _call_with_fit(track, mode, fn):
    """Paint with every clip's fit_mode forced, then put the old mode back."""
    saved = []
    for clip in _iter_clips(track):
        if isinstance(clip, dict):
            had = "fit_mode" in clip
            prev = clip.get("fit_mode", _FIT_MISSING)
            clip["fit_mode"] = mode
            saved.append((clip, True, had, prev))
            continue
        if clip is None or not hasattr(clip, "fit_mode"):
            continue
        prev = clip.fit_mode
        try:
            clip.fit_mode = mode
        except Exception:
            logger.exception("分屏临时改 fit_mode 失败")
            continue
        saved.append((clip, False, True, prev))
    try:
        return fn()
    finally:
        for clip, is_dict, had, prev in saved:
            if is_dict:
                if not had or prev is _FIT_MISSING:
                    clip.pop("fit_mode", None)
                else:
                    clip["fit_mode"] = prev
            else:
                try:
                    clip.fit_mode = prev
                except Exception:
                    logger.exception("分屏还原 fit_mode 失败")


_BACKDROP_DEPTH = [0]


def painting_backdrop() -> bool:
    """正在画模糊底（预览别把这一遍登记成可点的片段框）。"""
    return _BACKDROP_DEPTH[0] > 0


def _paint_as_backdrop(track, mode, fn):
    _BACKDROP_DEPTH[0] += 1
    try:
        return _call_with_fit(track, mode, fn)
    finally:
        _BACKDROP_DEPTH[0] -= 1


def _blur_plate(track, width, height, paint_one, bd):
    """一张 width×height 的模糊底：这条轨道按铺满画（不留洞），模糊，压暗。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.fill_bg import blur_image, blur_image_full

    w, h = max(1, int(width)), max(1, int(height))
    plate = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    plate.fill(0)
    pp = QPainter(plate)
    try:
        pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        _paint_as_backdrop(track, "fill", lambda: paint_one(pp, track, w, h))
    finally:
        pp.end()
    if bd.get("style") == "legacy":
        # Q148 原样：缩图模糊放回整幅
        out = blur_image_full(plate, float(bd.get("blur", CONTAIN_BLUR)), "legacy")
        if out is None or out.isNull():
            out = plate
        if out is plate:
            out = plate.copy()
    else:
        small = blur_image(plate, float(bd.get("blur", CONTAIN_BLUR)), "lens",
                           float(bd.get("highlights", 0.35)))
        if small is None or small.isNull() or small is plate:
            out = plate
        else:
            out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            out.fill(0)
            op = QPainter(out)
            try:
                op.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                op.drawImage(QRectF(0.0, 0.0, float(w), float(h)), small)
            finally:
                op.end()
    dim = int(round(max(0.0, min(1.0, float(bd.get("dim", CONTAIN_DIM)))) * 255.0))
    if dim > 0:
        dp = QPainter(out)
        try:
            dp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            dp.fillRect(0, 0, w, h, QColor(0, 0, 0, dim))
        finally:
            dp.end()
    return out


def _paint_contain_plate(painter, track, width, height, paint_one, bd=None):
    """Blurred full-canvas plate from one track. Cover-fit so the blur has no hole."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.fill_bg import blur_image_full

    bd = bd or backdrop_spec(None)
    if bd.get("style") != "legacy":
        painter.drawImage(0, 0, _blur_plate(track, width, height, paint_one, bd))
        return
    # 老分屏：Q148 原来的画法一点不改（压暗盖在整幅上）
    plate = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    plate.fill(0)
    pp = QPainter(plate)
    try:
        pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        _paint_as_backdrop(track, "fill", lambda: paint_one(pp, track, int(width), int(height)))
    finally:
        pp.end()
    blurred = blur_image_full(plate, float(bd.get("blur", CONTAIN_BLUR)))
    painter.drawImage(0, 0, blurred if blurred is not None else plate)
    dim = int(round(max(0.0, min(1.0, float(bd.get("dim", CONTAIN_DIM)))) * 255.0))
    if dim > 0:
        painter.fillRect(0, 0, int(width), int(height), QColor(0, 0, 0, dim))


def _full_source_track(sp, bd, participating):
    """「单个视频撑满」用哪一条：backdrop.cell 指定的格子 → 主轨 → 第一条。"""
    want = str((bd or {}).get("cell") or "")
    if want:
        for tr in participating:
            if str(getattr(tr, "split_cell", "") or "") == want:
                return tr
    for tr in participating:
        if getattr(tr, "is_main_video", False):
            return tr
    return participating[0]


class _LayerTrack:
    """Minimal track stand-in so tests can feed cell-tagged QImages."""
    __slots__ = ("split_cell", "id", "_image")

    def __init__(self, cell_id, image):
        self.split_cell = str(cell_id or "")
        self.id = self.split_cell
        self._image = image


def paint_split_video_tracks(painter, task, tracks, canvas_w, canvas_h, paint_one, bottom=True):
    """paint_one(painter, track, dest_w, dest_h) draws one track as if dest is the canvas.

    Always composites onto an offscreen QImage, then blits. Glass sampling
    must not read a QWidget device (window chrome / no copy()).
    """
    from PySide6.QtGui import QPainter

    from cutora.render import backdrop as BG
    try:
        from cutora.render.render_worker import _REPLAY_TLS
        is_record_only = getattr(_REPLAY_TLS, "record", None) is not None
    except Exception:
        is_record_only = False

    sp = (task or {}).get("_export_normed_split") if isinstance(task, dict) else None
    if sp is None:
        sp = normalize_split((task or {}).get("split"))
    w, h = int(canvas_w), int(canvas_h)
    participating = []
    overlay = []
    ext_tracks = []
    for tr in tracks or []:
        if BG.is_backdrop_track(tr):
            ext_tracks.append(tr)          # Q155 外部视频背景：不进格子、不叠最上，垫最底
            continue
        cid = getattr(tr, "split_cell", None)
        if cid:
            participating.append(tr)
        else:
            overlay.append(tr)
    ext = bool(ext_tracks) or BG.bg_image_spec(task) is not None
    if ext:
        # 外部图片 / 视频铺满整幅，画在最底下；格子拼好的 off 盖在上面（留白处透明）
        # Q163：统一叠压时上面那几段视频带（bottom=False）不再重画底图
        if bottom and not is_record_only:
            BG.paint_bg_image(painter, task, w, h)
        for tr in ext_tracks:
            try:
                _paint_as_backdrop(tr, "fill", lambda tr=tr: paint_one(painter, tr, w, h))
            except Exception:
                logger.exception("整幅背景视频绘制失败")

    # 检查是否存在跨视频带（例如主视频与副分屏视频之间夹了调整图层/文字轨）的分层分屏渲染
    later_cids: set[str] = set()
    try:
        tl_obj = (task or {}).get("timeline") if isinstance(task, dict) else None
        all_tr = list(getattr(tl_obj, "tracks", None) or [])
        all_split = [t for t in all_tr if getattr(t, "type", None) == "video"
                     and getattr(t, "visible", True) and getattr(t, "split_cell", None)
                     and (getattr(t, "clips", None) or [])]
        if len(participating) < len(all_split):
            part_ids = {id(t) for t in participating}
            last_idx = max((i for i, t in enumerate(all_split) if id(t) in part_ids), default=-1)
            if 0 <= last_idx < len(all_split) - 1:
                later_cids = {str(getattr(t, "split_cell")) for t in all_split[last_idx + 1:]
                              if getattr(t, "split_cell", None)}
    except Exception:
        later_cids = set()

    # 构建格子 -> 轨道映射：
    # 1. 绝不让超出当前布局格子数（或未分配格子）的普通视频轨道全屏盖在分屏最上方（否则会遮挡整个分屏！）
    # 2. 若布局有空闲格子（如从 2 格切到 4/5/6/9 格），优先由溢出/未分配轨道补位；若仍有多余空格，则用副屏轨道循环补满，杜绝黑洞。
    all_cell_ids = [str(c.get("id")) for c in (sp.get("cells") or []) if c.get("id")]
    valid_cell_set = set(all_cell_ids)
    cell_renders: list[tuple[str, object]] = []
    assigned_cids: set[str] = set()
    overflow_tracks: list = []
    fullscreen_overlays: list = []
    for tr in participating:
        cid = str(getattr(tr, "split_cell") or "")
        if cid in valid_cell_set and cid not in assigned_cids:
            cell_renders.append((cid, tr))
            assigned_cids.add(cid)
        else:
            overflow_tracks.append(tr)
    for tr in overlay:
        if getattr(tr, "_force_fullscreen_overlay", False):
            fullscreen_overlays.append(tr)
        else:
            overflow_tracks.append(tr)

    if not cell_renders and overflow_tracks and all_cell_ids:
        for cid in all_cell_ids:
            if cid in later_cids or not overflow_tracks:
                continue
            tr_fill = overflow_tracks.pop(0)
            cell_renders.append((cid, tr_fill))
            assigned_cids.add(cid)

    if not cell_renders:
        for tr in overlay:
            paint_one(painter, tr, w, h)
        return

    sec_pool = [tr for cid, tr in cell_renders if cid != "A"] or [tr for _cid, tr in cell_renders]
    sec_cycle_idx = 0
    for cid in all_cell_ids:
        if cid in assigned_cids or cid in later_cids:
            continue
        if overflow_tracks:
            tr_fill = overflow_tracks.pop(0)
            cell_renders.append((cid, tr_fill))
            assigned_cids.add(cid)
        elif not later_cids and sec_pool:
            tr_fill = sec_pool[sec_cycle_idx % len(sec_pool)]
            sec_cycle_idx += 1
            cell_renders.append((cid, tr_fill))
            assigned_cids.add(cid)

    active_tracks = [tr for _cid, tr in cell_renders]
    contain = layout_contains_sources(sp.get("layout"))
    full = backdrop_is_full(sp) and not ext
    bd = backdrop_spec(sp) if (contain or full) else None
    per_cell = bool(contain and not ext and not full and bd.get("mode") == "each")

    if is_record_only:
        # Q180 解码线程快路：只按相同顺序调用 paint_one 取帧登记，不做离屏图填充、镜头模糊、遮罩合成和边框绘制
        if bottom and full and active_tracks:
            src_tr = _full_source_track(sp, bd, active_tracks)
            try:
                _paint_as_backdrop(src_tr, "fill", lambda: paint_one(painter, src_tr, w, h))
            except Exception:
                pass
        elif bottom and contain and active_tracks and not per_cell and not ext:
            try:
                _paint_as_backdrop(active_tracks[0], "fill", lambda: paint_one(painter, active_tracks[0], w, h))
            except Exception:
                pass
        seen_rec: set[int] = set()
        for cid, tr in cell_renders:
            if id(tr) in seen_rec:
                continue
            seen_rec.add(id(tr))
            cell = cell_by_id(sp, cid)
            if cell is None:
                continue
            _x, _y, cw, ch = _content_box(cell, w, h)
            if per_cell:
                try:
                    _paint_as_backdrop(tr, "fill", lambda tr=tr, cw=cw, ch=ch: paint_one(painter, tr, cw, ch))
                except Exception:
                    pass
            if contain:
                _call_with_fit(tr, "fit", lambda tr=tr, cw=cw, ch=ch: paint_one(painter, tr, cw, ch))
            else:
                paint_one(painter, tr, cw, ch)
        for tr in fullscreen_overlays:
            paint_one(painter, tr, w, h)
        return

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage
    cell_order = tuple(cid for cid, _tr in cell_renders)
    cache = get_cache(sp, w, h, _normed=True, cell_order=cell_order)
    off, cells = _thread_scratch(cache)
    want_plate = bool((bottom and full and active_tracks) or (bottom and contain and active_tracks and not per_cell and not ext))
    direct_band = bool(later_cids and len(cell_renders) == 1 and not want_plate)
    dev = painter.device()
    direct_canvas = bool(
        not direct_band
        and not ext
        and not later_cids
        and not want_plate
        and str(border_style(sp)) != "glass"
        and (getattr(painter, "_canvas_cleared_black", False) or getattr(painter, "_canvas_needs_clear", False))
        and len(cell_renders) == len(all_cell_ids)
        and isinstance(dev, QImage)
        and dev.width() == w
        and dev.height() == h
        and painter.worldTransform().isIdentity()
    )
    band_jobs = []
    defer_band_clear = False
    # Rust DLL 不可用（老系统 / 杀毒拦截 / 加载失败）时，所有「延后到 Rust 批量执行」的快路径必须关掉，
    # 否则延后的格子在 flush 时会被丢弃或整屏涂黑。
    from cutora.core.rust_engine import is_rust_available as _rust_ok
    rust_ok = _rust_ok()
    if getattr(painter, "_canvas_needs_clear", False):
        painter._canvas_needs_clear = False
        if direct_canvas:
            can_partial = rust_ok
            for cid, _tr in cell_renders:
                geom = cells.get(cid)
                spans = geom.get("spans") if geom else None
                if not spans or not spans.get("rects"):
                    can_partial = False
                    break
                brects = spans.get("band") or ()
                if brects:
                    ox, oy = geom["origin"]
                    band_jobs.append((ox, oy, brects))
            if can_partial:
                defer_band_clear = True
            else:
                dev.fill(0xFF000000)
        elif isinstance(dev, QImage):
            dev.fill(0xFF000000)
        painter._canvas_cleared_black = True
    op = None
    if direct_canvas:
        op = painter
    elif not direct_band:
        off.fill(0)
        op = QPainter(off)
    # 整幅背景生效时，格子里片段自己的背景填充不画（留白露出整幅底）
    quiet = BG.suppress_fill() if (ext or full) else None
    if quiet is not None:
        quiet.__enter__()
    lazy_clear = bool(getattr(paint_one, "_supports_lazy_clear", False))
    fast_lookup = getattr(paint_one, "_fast_cell_lookup", None) if (rust_ok and not per_cell and not contain and not direct_band) else None
    pending_cell_blits = [] if (rust_ok and direct_canvas and not later_cids) else None

    def _flush_deferred_blits():
        nonlocal defer_band_clear
        if defer_band_clear or pending_cell_blits:
            from cutora.core.rust_engine import rust_exec_split_blits, rust_zero_band_rects
            if pending_cell_blits:
                if not rust_exec_split_blits(dev, band_jobs if defer_band_clear else None, pending_cell_blits):
                    if defer_band_clear and not rust_zero_band_rects(dev, band_jobs):
                        dev.fill(0xFF000000)
                pending_cell_blits.clear()
                defer_band_clear = False
            elif defer_band_clear:
                if not rust_zero_band_rects(dev, band_jobs):
                    dev.fill(0xFF000000)
                defer_band_clear = False

    try:
        if op is not None and not direct_canvas:
            op.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            op.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plate_img = None
        if bottom and full and active_tracks:
            # Q155 单个视频撑满：整幅垫「一格」那条视频的模糊，任何布局都行
            src_tr = _full_source_track(sp, bd, active_tracks)
            try:
                plate_img = _blur_plate(src_tr, w, h, paint_one, bd)
            except Exception:
                logger.exception("单个视频撑满的整幅背景绘制失败")
                plate_img = None
        elif bottom and contain and active_tracks and not per_cell and not ext:
            # 整幅底单独画一张，格子都拼完了再垫到后面（DestinationOver）。
            plate_img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            plate_img.fill(0)
            pp = QPainter(plate_img)
            try:
                pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                _paint_contain_plate(pp, active_tracks[0], w, h, paint_one, bd)
            except Exception:
                logger.exception("左右完整分屏的模糊背景绘制失败")
                plate_img = None
            finally:
                pp.end()
        part_cids = {cid for cid, _tr in cell_renders}
        for cid, tr in cell_renders:
            geom = cells.get(cid)
            if geom is None or cell_by_id(sp, cid) is None:
                continue
            layer = geom["layer"]
            use_lazy = bool(lazy_clear and not per_cell)
            if not use_lazy and geom.get("_dirty", True):
                layer.fill(0)
                layer._last_fill_state = None
                geom["_dirty"] = False
            cx, cy, cw, ch = geom["content"]
            lw, lh = layer.width(), layer.height()
            fast_res = fast_lookup(tr, cw, ch, lw, lh, cx, cy) if fast_lookup is not None else None
            if fast_res is not None:
                f_kind, f_val = fast_res
                direct_bgr = f_val if f_kind == "bgr" else None
                direct_img = f_val if f_kind == "img" else None
                cell_opaque = True
                bake_req = None
                lp = None
            else:
                lp = QPainter(layer)
                try:
                    lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    if cx or cy:
                        lp.translate(cx, cy)
                    lp._split_layer_rect = (-float(cx), -float(cy), float(lw), float(lh))
                    lp._split_target_layer = layer
                    lp._split_cell_mask = geom.get("mask")
                    lp._split_layer_img = layer if (use_lazy and geom.get("_dirty", True)) else None
                    lp._split_covered_all = False
                    lp._split_drew = None
                    lp._split_filled = False
                    lp._split_qimg = None
                    lp._split_direct_img = None
                    lp._split_direct_bgr = None
                    lp._split_loop_bake = None
                    lp._accepts_direct_cell_img = not per_cell
                    if per_cell:
                        try:
                            lp.drawImage(0, 0, _blur_plate(tr, cw, ch, paint_one, bd))
                        except Exception:
                            logger.exception("分屏格子 %s 的模糊背景绘制失败", cid)
                    if contain:
                        _call_with_fit(tr, "fit", lambda lp=lp, tr=tr, cw=cw, ch=ch: paint_one(lp, tr, cw, ch))
                    else:
                        paint_one(lp, tr, cw, ch)
                except Exception:
                    geom["_dirty"] = True
                    logger.exception("分屏格子 %s 绘制失败", cid)
                    raise
                finally:
                    lp.end()
                if getattr(lp, "_split_drew", None) is False and not per_cell:
                    if direct_canvas:
                        _flush_deferred_blits()
                        ox, oy = int(geom["origin"][0]), int(geom["origin"][1])
                        spans = geom.get("spans")
                        srects = (spans.get("solid") or ()) if (spans and spans.get("rects")) else ()
                        for r in srects:
                            op.fillRect(ox + r.x(), oy + r.y(), r.width(), r.height(), 0xFF000000)
                    continue
                direct_bgr = getattr(lp, "_split_direct_bgr", None)
                direct_img = getattr(lp, "_split_direct_img", None)
                cell_opaque = bool(getattr(lp, "_split_covered_all", False) or getattr(lp, "_split_filled", False))
                bake_req = getattr(lp, "_split_loop_bake", None)
            if direct_bgr is not None and not direct_band and bake_req is None:
                skip_mask = False
                if later_cids:
                    shared_with_band = False
                    for d in sp.get("dividers") or []:
                        neg = _cell_ids(d.get("neg_cell"))
                        pos = _cell_ids(d.get("pos_cell"))
                        if cid in neg or cid in pos:
                            others = (set(neg) | set(pos)) - {cid}
                            if others & part_cids:
                                shared_with_band = True
                                break
                    if not shared_with_band:
                        skip_mask = True
                spans = None if skip_mask else geom.get("spans")
                if spans and spans.get("rects") and isinstance(op.device(), QImage) and op.worldTransform().isIdentity():
                    ox, oy = int(geom["origin"][0]), int(geom["origin"][1])
                    if pending_cell_blits is not None:
                        pending_cell_blits.append((
                            "bgr",
                            direct_bgr,
                            None if skip_mask else geom.get("mask"),
                            spans.get("solid") or (),
                            spans.get("band") or (),
                            ox,
                            oy,
                        ))
                        continue
                    try:
                        from cutora.core.rust_engine import rust_blit_bgr_cell_spans
                        if rust_blit_bgr_cell_spans(
                            direct_bgr,
                            None if skip_mask else geom.get("mask"),
                            op.device(),
                            spans.get("solid") or (),
                            spans.get("band") or (),
                            ox,
                            oy,
                        ):
                            continue
                    except Exception:
                        pass
                try:
                    from cutora.core.rust_engine import rust_bgr_to_rgb32_qimage
                    direct_img = rust_bgr_to_rgb32_qimage(
                        direct_bgr, direct_bgr.shape[1], direct_bgr.shape[0], reuse=True
                    )
                except Exception:
                    pass
                if direct_img is None:
                    # Rust 不可用时的纯 Qt 回退：BGR888 → RGB32（像素一致）
                    try:
                        _b = direct_bgr if direct_bgr.flags["C_CONTIGUOUS"] else direct_bgr.copy()
                        direct_img = QImage(
                            _b.data, _b.shape[1], _b.shape[0], int(_b.strides[0]),
                            QImage.Format.Format_BGR888,
                        ).convertToFormat(QImage.Format.Format_RGB32)
                    except Exception:
                        logger.exception("分屏格子 BGR→RGB32 回退转换失败")
            if direct_img is not None:
                src_layer = direct_img
            else:
                if lp is not None and getattr(lp, "_split_layer_img", None) is not None:
                    layer.fill(0)
                    layer._last_fill_state = None
                    lp._split_layer_img = None
                if lp is not None and not getattr(lp, "_split_filled", False):
                    layer._last_fill_state = None
                geom["_dirty"] = True
                if lp is not None and geom["extend"] and not getattr(lp, "_split_filled", False) and not getattr(lp, "_split_covered_all", False):
                    _extend_edges(layer, cx, cy, cw, ch, tilted=geom.get("tilted", False),
                                  src_qimg=getattr(lp, "_split_qimg", None),
                                  mask_edge_yx=geom.get("mask_edge_yx"))
                src_layer = layer
            if bake_req is not None:
                try:
                    lcache, lkey, lrect = bake_req
                    if len(lcache) < 4096 or lkey in lcache:
                        from cutora.core.rust_engine import rust_premask_cell_qimg
                        m_img = geom.get("mask")
                        baked = rust_premask_cell_qimg(src_layer, m_img)
                        if baked is None:
                            baked = QImage(
                                src_layer.width(),
                                src_layer.height(),
                                QImage.Format.Format_ARGB32_Premultiplied,
                            )
                            bmp = QPainter(baked)
                            try:
                                bmp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                                bmp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                                bmp.drawImage(0, 0, src_layer)
                                if m_img is not None and not m_img.isNull():
                                    bmp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                                    bmp.drawImage(0, 0, m_img)
                            finally:
                                bmp.end()
                            baked._is_premasked_cell = True
                        lcache[lkey] = (baked, lrect)
                        src_layer = baked
                        cell_opaque = True
                except Exception:
                    pass
            if direct_band:
                ox, oy = int(geom["origin"][0]), int(geom["origin"][1])
                painter.drawImage(QPoint(ox, oy), src_layer)
                continue
            # 若当前是下层分屏带（later_cids 非空），且与 cid 相邻的分界线另一侧全在上层分屏带里，
            # 则不在下层提前挖空，避免中间的调整图层把挖空的黑底模糊进画面，由上层分屏带自然覆盖。
            skip_mask = False
            if later_cids:
                shared_with_band = False
                for d in sp.get("dividers") or []:
                    neg = _cell_ids(d.get("neg_cell"))
                    pos = _cell_ids(d.get("pos_cell"))
                    if cid in neg or cid in pos:
                        others = (set(neg) | set(pos)) - {cid}
                        if others & part_cids:
                            shared_with_band = True
                            break
                if not shared_with_band:
                    skip_mask = True
            spans = None if skip_mask else geom.get("spans")
            if (
                pending_cell_blits is not None
                and not skip_mask
                and spans
                and spans.get("rects")
                and cell_opaque
            ):
                ox, oy = int(geom["origin"][0]), int(geom["origin"][1])
                pending_cell_blits.append((
                    "qimg",
                    src_layer,
                    geom.get("mask"),
                    spans.get("solid") or (),
                    spans.get("band") or (),
                    ox,
                    oy,
                    True,
                    bool(getattr(src_layer, "_is_premasked_cell", False)),
                ))
                continue
            _flush_deferred_blits()
            if skip_mask:
                _blit_masked(op, src_layer, geom["origin"], None, geom["scratch"], spans=None, img_opaque=cell_opaque)
            else:
                _blit_masked(op, src_layer, geom["origin"], geom["mask"], geom["scratch"],
                             spans=spans, img_opaque=cell_opaque)
        _flush_deferred_blits()
        if op is not None:
            if plate_img is not None:
                op.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
                op.drawImage(0, 0, plate_img)
                op.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if not later_cids:
                composed = None
                if str(border_style(sp)) == "glass":
                    composed = off.copy()
                _draw_borders(op, sp, cache, composed)
    finally:
        if op is not None and not direct_canvas:
            op.end()
        if quiet is not None:
            quiet.__exit__(None, None, None)
    if op is not None and not direct_canvas:
        if not ext and not later_cids:
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(0, 0, off)
            painter.restore()
        else:
            painter.drawImage(0, 0, off)
    for tr in fullscreen_overlays:
        paint_one(painter, tr, w, h)



def border_style(split) -> str:
    return str((split.get("border") or {}).get("style") or "none")


def handle_centers(split, canvas_w, canvas_h):
    """Divider handle in canvas pixels: (id, x, y, axis) + rim handles for circle/ellipse."""
    sp = normalize_split(split)
    curve = str((sp.get("border") or {}).get("curve") or "straight")
    out = []
    for d in sp.get("dividers") or []:
        c_use = _divider_curve(sp, d, curve)
        pts = divider_polyline(d, canvas_w, canvas_h, curve=c_use, n=48, span=_divider_span(sp, d, canvas_w, canvas_h))
        if not pts:
            continue
        if str(d.get("axis")) == "circle":
            cx_n = float(d.get("cx", 0.75))
            cy_n = float(d.get("cy", d.get("pos", 0.75)))
            r_n = float(d.get("r", 0.15))
            aspect = float(d.get("aspect", 1.0) or 1.0)
            rot_deg = float(d.get("rotation_deg") or 0.0)
            hx = cx_n * float(canvas_w)
            hy = cy_n * float(canvas_h)
            rx_px, ry_px = _ellipse_radii_px(r_n, aspect, canvas_w, canvas_h)
            rad = math.radians(rot_deg)
            ct, st = math.cos(rad), math.sin(rad)
            s45 = 0.70710678
            lx_se, ly_se = rx_px * s45, ry_px * s45
            scale_pt = (hx + lx_se * ct - ly_se * st, hy + lx_se * st + ly_se * ct)
            rx_pt = (hx + rx_px * ct, hy + rx_px * st)
            ry_pt = (hx - ry_px * st, hy + ry_px * ct)
            rot_pt = (hx + ry_px * st, hy - ry_px * ct)
            out.append({
                "id": str(d.get("id")),
                "x": hx,
                "y": hy,
                "axis": "circle",
                "pts": pts,
                "pos": cy_n,
                "cx": cx_n,
                "cy": cy_n,
                "r": r_n,
                "aspect": aspect,
                "rx_px": rx_px,
                "ry_px": ry_px,
                "rotation_deg": rot_deg,
                "scale_pt": scale_pt,
                "rx_pt": rx_pt,
                "ry_pt": ry_pt,
                "rot_pt": rot_pt,
            })
            continue
        mid = pts[len(pts) // 2]
        out.append({
            "id": str(d.get("id")),
            "x": float(mid[0]),
            "y": float(mid[1]),
            "axis": d.get("axis") or "h",
            "pts": pts,
            "pos": float(d.get("pos") or 0.5),
            "rotation_deg": float(d.get("rotation_deg") or 0.0),
        })
    return out

