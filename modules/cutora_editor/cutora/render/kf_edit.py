# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-A · 关键帧面板 / 曲线编辑器用的纯函数：在一组点上做编辑，返回新的点表。

不碰 Qt、不改传进来的对象（都先深拷贝）。面板拖动时在副本上预览，松手时把
结果整段交给命令 ``keyframe.replace``，一个手势一次撤销。

点的格式和 cutora/render/keyframe.py 一样：
    {"t": 秒（相对对象起点）, "v": 值, "ease": 缓动名, "bez": [x1, y1, x2, y2]?}
ease / bez 描述「从这个点到下一个点」那一段。

曲线编辑器的手柄就是 bez：
- 第 i 个点的「出手柄」= 第 i 段的 (x1, y1)，
- 第 i 个点的「入手柄」= 第 i−1 段的 (x2, y2)，
x 是这一段时长的比例（0–1，保证时间单调），y 是这一段值变化的比例；
y 可以超出 0–1 —— 那就是过冲（AE 的「超过目标再回来」）。
"""
from __future__ import annotations

import copy
import math

from cutora.render.keyframe import DEFAULT_BEZ, EASE_NAMES, HIT_EPS

# 预设缓动的等价贝塞尔（把非 bezier 段换成可拖的手柄时用）
PRESET_BEZ = {
    "linear": (1.0 / 3.0, 1.0 / 3.0, 2.0 / 3.0, 2.0 / 3.0),
    "ease_in": (0.32, 0.0, 0.67, 0.0),
    "ease_out": (0.33, 1.0, 0.68, 1.0),
    "ease_in_out": (0.65, 0.0, 0.35, 1.0),
    "bezier": DEFAULT_BEZ,
}
EASY = 1.0 / 3.0            # AE「缓动」的影响 33.33 %，速度 0
Y_MIN, Y_MAX = -2.0, 3.0    # 过冲范围，和 keyframe._bez_tuple 一致


def clone(points) -> list[dict]:
    pts = [copy.deepcopy(p) for p in (points or []) if isinstance(p, dict)]
    pts.sort(key=lambda p: float(p.get("t") or 0.0))
    return pts


def is_numeric_path(points) -> bool:
    for p in points or []:
        v = p.get("v")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    return True


def segment_bez(points, i) -> tuple[float, float, float, float]:
    """第 i 段（点 i → 点 i+1）的贝塞尔；非 bezier 段给等价预设。"""
    p = points[i]
    ease = str(p.get("ease") or "linear")
    if ease == "bezier":
        b = list(p.get("bez") or DEFAULT_BEZ)
        if len(b) == 4:
            return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        return DEFAULT_BEZ
    return PRESET_BEZ.get(ease, PRESET_BEZ["linear"])


def _set_seg(points, i, bez) -> None:
    x1, y1, x2, y2 = bez
    points[i]["ease"] = "bezier"
    points[i]["bez"] = [
        max(0.0, min(1.0, float(x1))), max(Y_MIN, min(Y_MAX, float(y1))),
        max(0.0, min(1.0, float(x2))), max(Y_MIN, min(Y_MAX, float(y2))),
    ]


# ───────────────────────────── 时间 ─────────────────────────────

def move(points, idxs, dt, *, lo: float = 0.0, hi: float | None = None) -> list[dict]:
    """把 idxs 这些点整体平移 dt 秒。被挪到别的点身上的那个点让位（删掉）。"""
    pts = clone(points)
    sel = set(int(i) for i in idxs)
    if not sel:
        return pts
    times = [float(pts[i]["t"]) for i in sel if 0 <= i < len(pts)]
    if not times:
        return pts
    d = float(dt)
    d = max(d, lo - min(times))
    if hi is not None:
        d = min(d, hi - max(times))
    moved = []
    rest = []
    for i, p in enumerate(pts):
        if i in sel:
            p["t"] = max(0.0, float(p["t"]) + d)
            moved.append(p)
        else:
            rest.append(p)
    rest = [p for p in rest if all(abs(float(p["t"]) - float(m["t"])) > HIT_EPS for m in moved)]
    out = rest + moved
    out.sort(key=lambda p: float(p["t"]))
    return out


def scale_time(points, idxs, anchor_t: float, factor: float) -> list[dict]:
    """以 anchor_t 为中心缩放选中点的时间（AE 的 Alt+拖首尾关键帧）。"""
    pts = clone(points)
    f = max(0.0, float(factor))
    sel = set(int(i) for i in idxs)
    for i, p in enumerate(pts):
        if i in sel:
            p["t"] = max(0.0, float(anchor_t) + (float(p["t"]) - float(anchor_t)) * f)
    # 缩成重叠的只留一个
    out: list[dict] = []
    for p in sorted(pts, key=lambda q: float(q["t"])):
        if out and abs(float(out[-1]["t"]) - float(p["t"])) <= HIT_EPS:
            continue
        out.append(p)
    return out


def delete(points, idxs) -> list[dict]:
    sel = set(int(i) for i in idxs)
    return [p for i, p in enumerate(clone(points)) if i not in sel]


def reverse(points, idxs) -> list[dict]:
    """把选中范围（第一个到最后一个选中点之间的全部点）在时间上倒过来。

    值的先后顺序反转；每一段的缓动跟着倒（贝塞尔手柄中心对称，缓入 ↔ 缓出）。"""
    pts = clone(points)
    sel = sorted(int(i) for i in idxs if 0 <= int(i) < len(pts))
    if len(sel) < 2:
        return pts
    a, b = sel[0], sel[-1]
    block = pts[a:b + 1]
    n = len(block)
    t0, t1 = float(block[0]["t"]), float(block[-1]["t"])
    flip = {"ease_in": "ease_out", "ease_out": "ease_in"}
    segs = []
    for k in range(n - 1):
        ease = str(block[k].get("ease") or "linear")
        x1, y1, x2, y2 = segment_bez(block, k)
        segs.append((ease, (1.0 - x2, 1.0 - y2, 1.0 - x1, 1.0 - y1)))
    tail = {key: copy.deepcopy(block[-1][key]) for key in ("ease", "bez") if key in block[-1]}
    new_block = []
    for m in range(n):
        src = block[n - 1 - m]
        rec = {"t": t0 + t1 - float(src["t"]), "v": copy.deepcopy(src["v"])}
        for key, val in src.items():
            if key not in ("t", "v", "ease", "bez"):
                rec[key] = copy.deepcopy(val)
        if m < n - 1:
            ease, bez = segs[n - 2 - m]
            if ease == "bezier":
                rec["ease"] = "bezier"
                rec["bez"] = [bez[0], bez[1], bez[2], bez[3]]
            else:
                rec["ease"] = flip.get(ease, ease)
        else:
            rec.update(tail)
            rec.setdefault("ease", "linear")
        new_block.append(rec)
    out = pts[:a] + new_block + pts[b + 1:]
    for i in range(len(out)):
        if out[i].get("ease") == "bezier":
            _set_seg(out, i, segment_bez(out, i))
    return out


def distribute(points, idxs) -> list[dict]:
    """选中的点在首尾之间等距排开。"""
    pts = clone(points)
    sel = sorted(int(i) for i in idxs if 0 <= int(i) < len(pts))
    if len(sel) < 3:
        return pts
    t0, t1 = float(pts[sel[0]]["t"]), float(pts[sel[-1]]["t"])
    step = (t1 - t0) / (len(sel) - 1)
    for k, i in enumerate(sel):
        pts[i]["t"] = t0 + step * k
    pts.sort(key=lambda p: float(p["t"]))
    return pts


# ───────────────────────────── 值 / 缓动 ─────────────────────────────

def set_values(points, changes: dict) -> list[dict]:
    """changes = {index: 新值}。"""
    pts = clone(points)
    for i, v in (changes or {}).items():
        i = int(i)
        if 0 <= i < len(pts):
            pts[i]["v"] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return pts


def set_ease(points, idxs, ease: str) -> list[dict]:
    """把选中点「往后那一段」设成某种缓动（最后一个点没有后段，跳过）。

    ease 可以是 linear / ease_in / ease_out / ease_in_out / hold，或者
    easy（AE F9：两头都缓）/ easy_in（只缓进这个点）/ easy_out（只缓出）/ smooth（自动平滑）。
    """
    name = str(ease or "linear")
    pts = clone(points)
    sel = sorted(int(i) for i in idxs if 0 <= int(i) < len(pts))
    if name in ("easy", "easy_in", "easy_out"):
        for i in sel:
            if name in ("easy", "easy_out") and i < len(pts) - 1:
                x1, y1, x2, y2 = segment_bez(pts, i)
                _set_seg(pts, i, (EASY, 0.0, x2, y2))
            if name in ("easy", "easy_in") and i > 0:
                x1, y1, x2, y2 = segment_bez(pts, i - 1)
                _set_seg(pts, i - 1, (x1, y1, 1.0 - EASY, 1.0))
        return pts
    if name == "smooth":
        return smooth(pts, sel)
    if name not in EASE_NAMES:
        name = "linear"
    for i in sel:
        if i >= len(pts) - 1 and len(sel) > 1:
            continue
        pts[i]["ease"] = name
        if name == "bezier":
            pts[i]["bez"] = list(DEFAULT_BEZ)
        else:
            pts[i].pop("bez", None)
    return pts


def _slope(pts, i) -> float:
    """Catmull-Rom 切线（值 / 秒）。两端为 0（停住），同号的峰谷也给 0（不过冲）。"""
    n = len(pts)
    if n < 2 or i <= 0 or i >= n - 1:
        return 0.0
    t0, v0 = float(pts[i - 1]["t"]), float(pts[i - 1]["v"])
    v1 = float(pts[i]["v"])
    t2, v2 = float(pts[i + 1]["t"]), float(pts[i + 1]["v"])
    if (v1 - v0) * (v2 - v1) <= 0:
        return 0.0                    # 峰 / 谷：平着过，不冲出去
    span = t2 - t0
    return (v2 - v0) / span if span > 1e-9 else 0.0


def smooth(points, idxs=None) -> list[dict]:
    """自动平滑（AE「自动贝塞尔」）：按前后两点算切线，写成 bez。"""
    pts = clone(points)
    if not is_numeric_path(pts) or len(pts) < 2:
        return pts
    sel = set(range(len(pts))) if not idxs else set(int(i) for i in idxs)
    slopes = [_slope(pts, i) for i in range(len(pts))]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        touches = i in sel or (i + 1) in sel
        if not touches:
            continue
        span = float(b["t"]) - float(a["t"])
        dv = float(b["v"]) - float(a["v"])
        x1, y1, x2, y2 = segment_bez(pts, i)
        if i in sel:
            x1 = 1.0 / 3.0
            y1 = (slopes[i] * span / 3.0) / dv if abs(dv) > 1e-12 else 0.0
        if (i + 1) in sel:
            x2 = 2.0 / 3.0
            y2 = 1.0 - ((slopes[i + 1] * span / 3.0) / dv if abs(dv) > 1e-12 else 0.0)
        _set_seg(pts, i, (x1, y1, x2, y2))
    return pts


# ───────────────────────────── 手柄（曲线编辑器）─────────────────────────────

def handle_points(points, i) -> dict:
    """点 i 的入 / 出手柄在（秒, 值）空间里的位置。没有就不给那个键。"""
    out = {}
    n = len(points)
    if not (0 <= i < n) or not is_numeric_path(points):
        return out
    if i < n - 1:
        a, b = points[i], points[i + 1]
        x1, y1, _x2, _y2 = segment_bez(points, i)
        span = float(b["t"]) - float(a["t"])
        dv = float(b["v"]) - float(a["v"])
        out["out"] = (float(a["t"]) + x1 * span, float(a["v"]) + y1 * dv)
    if i > 0:
        a, b = points[i - 1], points[i]
        _x1, _y1, x2, y2 = segment_bez(points, i - 1)
        span = float(b["t"]) - float(a["t"])
        dv = float(b["v"]) - float(a["v"])
        out["in"] = (float(a["t"]) + x2 * span, float(a["v"]) + y2 * dv)
    return out


def drag_handle(points, i, side: str, t: float, v: float, *, linked: bool = True) -> list[dict]:
    """把点 i 的 side（"in" / "out"）手柄拖到（t, v）。

    linked：另一侧手柄保持共线（方向相反、长度不变）—— 平滑关键帧；Alt 拖时 False。
    """
    pts = clone(points)
    n = len(pts)
    if not is_numeric_path(pts) or not (0 <= i < n):
        return pts
    k = pts[i]
    kt, kv = float(k["t"]), float(k["v"])
    if side == "out" and i < n - 1:
        seg, a, b = i, pts[i], pts[i + 1]
        span = float(b["t"]) - kt
        dv = float(b["v"]) - kv
        x = (float(t) - kt) / span if span > 1e-12 else 0.0
        y = (float(v) - kv) / dv if abs(dv) > 1e-12 else 0.0
        x1, y1, x2, y2 = segment_bez(pts, seg)
        _set_seg(pts, seg, (x, y, x2, y2))
    elif side == "in" and i > 0:
        seg, a, b = i - 1, pts[i - 1], pts[i]
        span = kt - float(a["t"])
        dv = kv - float(a["v"])
        x = (float(t) - float(a["t"])) / span if span > 1e-12 else 1.0
        y = (float(v) - float(a["v"])) / dv if abs(dv) > 1e-12 else 1.0
        x1, y1, x2, y2 = segment_bez(pts, seg)
        _set_seg(pts, seg, (x1, y1, x, y))
    else:
        return pts
    if not linked:
        return pts
    # 另一侧：同一条切线方向（值 / 秒的斜率相同），保留它原来的时间长度
    hp = handle_points(pts, i)
    me = hp.get(side)
    other = "in" if side == "out" else "out"
    if me is None or other not in hp:
        return pts
    dt_me = me[0] - kt
    if abs(dt_me) < 1e-9:
        return pts
    slope = (me[1] - kv) / dt_me
    ot, _ov = hp[other]
    dt_o = ot - kt
    return drag_handle(pts, i, other, ot, kv + slope * dt_o, linked=False)


def value_range(points, samples: int = 24) -> tuple[float, float]:
    """曲线（含过冲）的值域，给曲线编辑器自动缩放用。"""
    from cutora.render.keyframe import ease_unit
    vals = []
    pts = clone(points)
    if not is_numeric_path(pts) or not pts:
        return (0.0, 1.0)
    for i, p in enumerate(pts):
        vals.append(float(p["v"]))
        if i < len(pts) - 1:
            a, b = float(p["v"]), float(pts[i + 1]["v"])
            for s in range(1, samples):
                u = ease_unit(s / float(samples), p.get("ease") or "linear", p.get("bez"))
                vals.append(a + (b - a) * u)
    lo, hi = min(vals), max(vals)
    if math.isclose(lo, hi):
        pad = max(1.0, abs(lo) * 0.1)
        return (lo - pad, hi + pad)
    return (lo, hi)


def sanitize(points) -> list[dict]:
    """命令入口用：只留合法的点，规范 ease / bez，按时间排，同一时刻只留最后一个。"""
    out: list[dict] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        try:
            t = max(0.0, float(p.get("t")))
        except (TypeError, ValueError):
            continue
        if "v" not in p:
            continue
        v = p.get("v")
        if isinstance(v, bool):
            continue
        rec = {"t": t, "v": float(v) if isinstance(v, (int, float)) else v,
               "ease": str(p.get("ease") or "linear")}
        if rec["ease"] not in EASE_NAMES:
            rec["ease"] = "linear"
        if rec["ease"] == "bezier":
            b = list(p.get("bez") or DEFAULT_BEZ)
            if len(b) != 4:
                b = list(DEFAULT_BEZ)
            try:
                rec["bez"] = [max(0.0, min(1.0, float(b[0]))), max(Y_MIN, min(Y_MAX, float(b[1]))),
                              max(0.0, min(1.0, float(b[2]))), max(Y_MIN, min(Y_MAX, float(b[3])))]
            except (TypeError, ValueError):
                rec["bez"] = list(DEFAULT_BEZ)
        out = [q for q in out if abs(float(q["t"]) - t) > HIT_EPS]
        out.append(rec)
    out.sort(key=lambda q: float(q["t"]))
    return out
