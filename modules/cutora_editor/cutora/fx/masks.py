# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-B · 形状遮罩：数据、规范化、栅格化、叠加运算。纯 numpy / cv2，不碰 Qt。

一个遮罩（dict）：
    id        "m3a1f"
    type      "rect" | "ellipse" | "polygon" | "bezier"
    rect      [nx, ny, nw, nh]        rect / ellipse：在图层自己的坐标里，0–1 归一化
    points    [[nx, ny], ...]          polygon：同上，≥ 3 个点
              [[nx, ny, inx, iny, outx, outy], ...]
                                       bezier（Q167）：锚点 + 进 / 出手柄（相对锚点的偏移，同一坐标系），
                                       ≥ 2 个锚点，首尾自动闭合；手柄全 0 = 尖角
    rotation  度，rect / ellipse 绕中心
    radius    rect 圆角，占短边的比例 0–0.5
    feather   px（图层像素），边缘柔化
    expand    px，正 = 向外扩，负 = 向内收
    opacity   0–1
    invert    bool
    mode      "add" | "subtract" | "intersect"
    enabled   bool
    name      显示名（可空）

遮罩坐标跟着图层走（图层平移、缩放、旋转，遮罩一起动）—— 这是 AE 蒙版的做法，
也是批量视频最省心的做法：同一个预设套到每条视频上，遮罩位置都对。

栅格化在图层像素空间（视频 = 解出来的那一帧，图片 / 文字 = 它的框），2× 超采样抗锯齿，
按（形状, 尺寸）缓存；feather / expand 的像素按 px_scale 换算（画布像素 → 图层像素）。
"""
from __future__ import annotations

import math
import uuid

import numpy as np

MASK_TYPES = ("rect", "ellipse", "polygon", "bezier")
PATH_TYPES = ("polygon", "bezier")      # 用 points 描述形状的
BEZIER_STEPS = 24                       # 每段曲线折线逼近的段数
MASK_MODES = ("add", "subtract", "intersect")
DEFAULTS = {
    "type": "rect", "rect": [0.25, 0.25, 0.5, 0.5], "points": [], "rotation": 0.0,
    "radius": 0.0, "feather": 0.0, "expand": 0.0, "opacity": 1.0, "invert": False,
    "mode": "add", "enabled": True, "name": "",
}
_SS = 2                     # 超采样倍数
_CACHE: dict = {}
_CACHE_MAX = 64


def new_id() -> str:
    return "m" + uuid.uuid4().hex[:6]


def normalize(raw) -> dict | None:
    """一个遮罩 dict → 规范化副本；不是 dict 或形状不合法 → None。"""
    if not isinstance(raw, dict):
        return None
    out = dict(DEFAULTS)
    out["rect"] = list(DEFAULTS["rect"])
    out["points"] = []
    for k in DEFAULTS:
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    out["id"] = str(raw.get("id") or new_id())
    t = str(out.get("type") or "rect")
    out["type"] = t if t in MASK_TYPES else "rect"
    mode = str(out.get("mode") or "add")
    out["mode"] = mode if mode in MASK_MODES else "add"
    try:
        r = [float(v) for v in (out.get("rect") or [])]
        if len(r) != 4:
            raise ValueError
        out["rect"] = [r[0], r[1], max(1e-4, r[2]), max(1e-4, r[3])]
    except (TypeError, ValueError):
        out["rect"] = list(DEFAULTS["rect"])
    pts = []
    is_bz = out["type"] == "bezier"
    for p in out.get("points") or []:
        try:
            if is_bz:
                vals = [float(v) for v in list(p)[:6]]
                if len(vals) < 2:
                    continue
                pts.append(vals + [0.0] * (6 - len(vals)))
            else:
                pts.append([float(p[0]), float(p[1])])
        except (TypeError, ValueError, IndexError):
            continue
    out["points"] = pts
    if out["type"] == "polygon" and len(pts) < 3:
        return None
    if is_bz and len(pts) < 2:
        return None
    for k, lo, hi in (("rotation", -3600.0, 3600.0), ("radius", 0.0, 0.5), ("feather", 0.0, 4000.0),
                      ("expand", -4000.0, 4000.0), ("opacity", 0.0, 1.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = float(DEFAULTS[k])
    out["invert"] = bool(out.get("invert"))
    out["enabled"] = bool(out.get("enabled", True))
    out["name"] = str(out.get("name") or "")
    return out


def masks_of(host) -> list[dict]:
    """图层上启用的、合法的遮罩（规范化副本）。"""
    raw = host.get("masks") if isinstance(host, dict) else getattr(host, "masks", None)
    out = []
    for m in raw or []:
        n = normalize(m)
        if n is not None and n["enabled"]:
            out.append(n)
    return out


def has_masks(host) -> bool:
    return bool(masks_of(host))


def signature(masks) -> tuple:
    """缓存键（和内容一一对应）。"""
    sig = []
    for m in masks:
        sig.append((m["type"], tuple(m["rect"]), tuple(tuple(p) for p in m["points"]),
                    round(m["rotation"], 3), round(m["radius"], 4), round(m["feather"], 2),
                    round(m["expand"], 2), round(m["opacity"], 4), m["invert"], m["mode"]))
    return tuple(sig)


def default_mask(kind: str, name: str = "") -> dict:
    """「+ 矩形 / + 椭圆」：居中、占一半大小的新遮罩。多边形走画布画。"""
    m = dict(DEFAULTS)
    m["id"] = new_id()
    m["type"] = kind if kind in MASK_TYPES else "rect"
    m["rect"] = [0.25, 0.25, 0.5, 0.5]
    m["points"] = []
    m["name"] = name
    return m


# ───────────────────────────── 栅格化 ─────────────────────────────

def shape_polygon(mask: dict, w: int, h: int, n_arc: int = 72) -> np.ndarray:
    """形状轮廓 → 图层像素坐标的多边形点 (N, 2) float32（椭圆 / 圆角用折线逼近）。"""
    t = mask["type"]
    if t == "polygon":
        pts = np.array([[p[0] * w, p[1] * h] for p in mask["points"]], dtype=np.float32)
        return pts
    if t == "bezier":
        pts = bezier_outline(mask["points"])
        return (pts * np.array([w, h], dtype=np.float32)).astype(np.float32)
    nx, ny, nw, nh = mask["rect"]
    cx, cy = (nx + nw / 2.0) * w, (ny + nh / 2.0) * h
    hw, hh = nw * w / 2.0, nh * h / 2.0
    if t == "ellipse":
        ang = np.linspace(0.0, 2.0 * math.pi, n_arc, endpoint=False)
        local = np.stack([np.cos(ang) * hw, np.sin(ang) * hh], axis=1)
    else:
        r = float(mask.get("radius") or 0.0) * min(hw, hh) * 2.0
        if r <= 0.5:
            local = np.array([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]], dtype=np.float32)
        else:
            r = min(r, hw, hh)
            local = []
            for (sx, sy, a0) in ((1, 1, 0.0), (-1, 1, 90.0), (-1, -1, 180.0), (1, -1, 270.0)):
                ccx, ccy = sx * (hw - r), sy * (hh - r)
                for k in range(n_arc // 4 + 1):
                    a = math.radians(a0 + 90.0 * k / (n_arc // 4))
                    local.append([ccx + r * math.cos(a), ccy + r * math.sin(a)])
            local = np.array(local, dtype=np.float32)
    rot = math.radians(float(mask.get("rotation") or 0.0))
    if abs(rot) > 1e-9:
        c, s = math.cos(rot), math.sin(rot)
        local = np.stack([local[:, 0] * c - local[:, 1] * s, local[:, 0] * s + local[:, 1] * c], axis=1)
    return (local + np.array([cx, cy], dtype=np.float32)).astype(np.float32)


# ───────────────────────────── 贝塞尔（Q167）─────────────────────────────

def _bz_pt(p) -> list:
    v = [float(x) for x in list(p)[:6]]
    return v + [0.0] * (6 - len(v))


def bezier_segments(points) -> list:
    """闭合路径的每一段 → [(P0, P1, P2, P3), …]（归一化坐标的 (x, y) 元组）。"""
    pts = [_bz_pt(p) for p in points or []]
    n = len(pts)
    out = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        out.append(((a[0], a[1]), (a[0] + a[4], a[1] + a[5]), (b[0] + b[2], b[1] + b[3]), (b[0], b[1])))
    return out


def _cubic(p0, p1, p2, p3, t):
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def bezier_outline(points, steps: int = BEZIER_STEPS) -> np.ndarray:
    """贝塞尔路径 → 折线 (N, 2) float32（归一化坐标）。直段（两头手柄都是 0）只取端点。"""
    out = []
    for p0, p1, p2, p3 in bezier_segments(points):
        straight = p1 == p0 and p2 == p3
        n = 1 if straight else max(2, int(steps))
        for k in range(n):
            out.append(_cubic(p0, p1, p2, p3, k / float(n)))
    return np.array(out, dtype=np.float32).reshape(-1, 2)


def split_bezier(mask: dict, seg: int, t: float = 0.5) -> dict:
    """在第 seg 段（锚点 seg → seg+1）的 t 处插一个锚点，形状不变（de Casteljau）。"""
    pts = [_bz_pt(p) for p in mask["points"]]
    n = len(pts)
    if not (0 <= seg < n):
        return dict(mask)
    p0, p1, p2, p3 = bezier_segments(pts)[seg]

    def lerp(a, b):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    q0, q1, q2 = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
    r0, r1 = lerp(q0, q1), lerp(q1, q2)
    m = lerp(r0, r1)
    a, b = pts[seg], pts[(seg + 1) % n]
    a[4], a[5] = q0[0] - a[0], q0[1] - a[1]
    b[2], b[3] = q2[0] - b[0], q2[1] - b[1]
    new = [m[0], m[1], r0[0] - m[0], r0[1] - m[1], r1[0] - m[0], r1[1] - m[1]]
    pts.insert(seg + 1, new)
    out = dict(mask)
    out["points"] = pts
    return out


def with_handle(mask: dict, i: int, side: str, nx, ny, symmetric: bool = True) -> dict:
    """拖手柄：side "in" / "out"，(nx, ny) 是手柄的新位置。symmetric = 对面那根反向跟着转（长度不变；
    原来是 0 就一样长）；Alt 拖 = 不对称（尖角）。"""
    pts = [_bz_pt(p) for p in mask["points"]]
    if not (0 <= i < len(pts)):
        return dict(mask)
    p = pts[i]
    dx, dy = float(nx) - p[0], float(ny) - p[1]
    me, other = (2, 4) if side == "in" else (4, 2)
    p[me], p[me + 1] = dx, dy
    if symmetric:
        ol = math.hypot(p[other], p[other + 1])
        ml = math.hypot(dx, dy)
        k = (ol / ml) if (ol > 1e-9 and ml > 1e-9) else 1.0
        p[other], p[other + 1] = -dx * k, -dy * k
    out = dict(mask)
    out["points"] = pts
    return out


def toggle_smooth(mask: dict, i: int) -> dict:
    """双击锚点：尖角 ↔ 平滑（平滑手柄 = 前后两个锚点连线方向，长度约 1/3 邻距，Catmull-Rom 那种）。"""
    pts = [_bz_pt(p) for p in mask["points"]]
    n = len(pts)
    if not (0 <= i < n):
        return dict(mask)
    p = pts[i]
    if any(abs(v) > 1e-9 for v in p[2:6]):
        p[2:6] = [0.0, 0.0, 0.0, 0.0]
    else:
        a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        tx, ty = (b[0] - a[0]) / 6.0, (b[1] - a[1]) / 6.0
        p[2:6] = [-tx, -ty, tx, ty]
    out = dict(mask)
    out["points"] = pts
    return out


def rasterize(mask: dict, w: int, h: int, px_scale: float = 1.0) -> np.ndarray:
    """一个遮罩 → (h, w) float32 0–1（含 expand / feather，不含 opacity / invert）。"""
    import cv2
    w, h = max(1, int(w)), max(1, int(h))
    key = (signature([mask])[0][:5], w, h, round(float(px_scale), 4), round(mask["feather"], 2),
           round(mask["expand"], 2))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    poly = shape_polygon(mask, w * _SS, h * _SS)
    big = np.zeros((h * _SS, w * _SS), dtype=np.uint8)
    if len(poly) >= 3:
        cv2.fillPoly(big, [np.round(poly).astype(np.int32)], 255, lineType=cv2.LINE_AA)
    a = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    expand = float(mask.get("expand") or 0.0) * float(px_scale)
    if abs(expand) >= 0.5:
        k = int(round(abs(expand))) * 2 + 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        a = cv2.dilate(a, kern) if expand > 0 else cv2.erode(a, kern)
    feather = float(mask.get("feather") or 0.0) * float(px_scale)
    if feather >= 0.5:
        sigma = feather / 2.0
        ks = int(sigma * 3) * 2 + 1
        a = cv2.GaussianBlur(a, (ks, ks), sigma, borderType=cv2.BORDER_CONSTANT)
    a = np.clip(a, 0.0, 1.0).astype(np.float32)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = a
    return a


def combine(masks, w: int, h: int, px_scale: float = 1.0) -> np.ndarray | None:
    """按顺序叠加成一张 (h, w) float32 的总遮罩；没有启用的遮罩 → None（= 不裁）。"""
    total = None
    for m in masks:
        a = rasterize(m, w, h, px_scale) * float(m.get("opacity", 1.0))
        if m.get("invert"):
            a = 1.0 - a
        mode = m.get("mode", "add")
        if total is None:
            total = a if mode != "subtract" else (1.0 - a)
            continue
        if mode == "add":
            total = np.clip(total + a, 0.0, 1.0)
        elif mode == "subtract":
            total = total * (1.0 - a)
        else:
            total = total * a
    return None if total is None else total.astype(np.float32)


def clear_cache() -> None:
    _CACHE.clear()


# ───────────────────────────── 编辑（画布 / 检查器用）─────────────────────────────

def bounds(mask: dict) -> tuple[float, float, float, float]:
    """归一化外接框 (nx, ny, nw, nh)。"""
    if mask["type"] == "polygon" and mask["points"]:
        xs = [p[0] for p in mask["points"]]
        ys = [p[1] for p in mask["points"]]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if mask["type"] == "bezier" and mask["points"]:
        pts = bezier_outline(mask["points"])
        x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
        return x0, y0, float(pts[:, 0].max()) - x0, float(pts[:, 1].max()) - y0
    return tuple(mask["rect"])


def moved(mask: dict, dx: float, dy: float) -> dict:
    out = dict(mask)
    if out["type"] in PATH_TYPES:
        out["points"] = [[p[0] + dx, p[1] + dy] + list(p[2:]) for p in out["points"]]
    else:
        r = list(out["rect"])
        out["rect"] = [r[0] + dx, r[1] + dy, r[2], r[3]]
    return out


def with_rect(mask: dict, nx, ny, nw, nh) -> dict:
    out = dict(mask)
    out["rect"] = [float(nx), float(ny), max(1e-4, float(nw)), max(1e-4, float(nh))]
    return out


def with_point(mask: dict, i: int, nx, ny) -> dict:
    out = dict(mask)
    pts = [list(p) for p in out["points"]]
    if 0 <= i < len(pts):
        pts[i] = [float(nx), float(ny)] + list(pts[i][2:])     # 贝塞尔：手柄是相对偏移，跟着锚点走
    out["points"] = pts
    return out


def insert_point(mask: dict, after: int, nx, ny) -> dict:
    if mask.get("type") == "bezier":
        return split_bezier(mask, after, 0.5)
    out = dict(mask)
    pts = [list(p) for p in out["points"]]
    pts.insert(after + 1, [float(nx), float(ny)])
    out["points"] = pts
    return out


def remove_point(mask: dict, i: int) -> dict | None:
    pts = [list(p) for p in mask["points"]]
    least = 2 if mask.get("type") == "bezier" else 3
    if len(pts) <= least or not (0 <= i < len(pts)):
        return None
    del pts[i]
    out = dict(mask)
    out["points"] = pts
    return out
