# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · 运动数据 → 目标图层的关键帧（跟随 / 稳定）。

做法和顶级软件的「烘焙」一致：每个源帧一个关键帧（线性），再按容差精简；结果是普通关键帧，
可以在曲线编辑器里继续改，导出路径完全不用动（golden 不受影响）。

坐标链（和导出 render_worker 同一套）：
    源帧像素 → 减裁切 → 翻转 → 归一化 → 适配框（fit / fill / stretch / source，含 scale / x / y / scale_y）
    → 绕框中心旋转 → 画布像素

绑定记录（存在被跟踪片段的 tracking.bindings 里，「更新全部绑定」用）::

    {"mode": "follow" | "stabilize", "source": "points" | "object", "points": [1, 2],
     "target": {"kind": "overlay", "list": "text_overlays", "id": 3, "index": 0}
             | {"kind": "clip", "track_id": "v1", "clip_index": 0} | {"kind": "self"},
     "apply": {"position": true, "rotation": false, "scale": false},
     "smooth": 0, "offset": [0, 0], "fill": "none" | "auto", "ref_frame": 120, "simplify": true}
"""
from __future__ import annotations

import math

MODES = ("follow", "stabilize")
SOURCES = ("points", "object")
FILLS = ("none", "auto")
TOLERANCE = {"x": 0.25, "y": 0.25, "rotation": 0.05, "scale": 0.0005}


def _g(obj, key, default=None):
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        v = getattr(obj, key, default)
    return default if v is None else v


# ───────────────────────────── 绑定记录 ─────────────────────────────

def normalize_binding(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or "follow")
    if mode not in MODES:
        return None
    src = str(raw.get("source") or "points")
    if src not in SOURCES:
        src = "points"
    tgt = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    kind = str(tgt.get("kind") or ("self" if mode == "stabilize" else ""))
    if kind == "overlay":
        lst = str(tgt.get("list") or "text_overlays")
        if lst not in ("text_overlays", "image_overlays"):
            return None
        target = {"kind": "overlay", "list": lst, "id": tgt.get("id"), "index": int(tgt.get("index") or 0)}
    elif kind == "clip":
        target = {"kind": "clip", "track_id": str(tgt.get("track_id") or ""), "clip_index": int(tgt.get("clip_index") or 0)}
    elif kind == "self":
        target = {"kind": "self"}
    else:
        return None
    if mode == "stabilize":
        target = {"kind": "self"}
    ap = raw.get("apply") if isinstance(raw.get("apply"), dict) else {}
    try:
        offset = [float(v) for v in (raw.get("offset") or [0, 0])][:2]
    except (TypeError, ValueError):
        offset = [0.0, 0.0]
    if len(offset) < 2:
        offset = [0.0, 0.0]
    fill = str(raw.get("fill") or "none")
    try:
        pts = [int(p) for p in (raw.get("points") or [])]
    except (TypeError, ValueError):
        pts = []
    try:
        ref = int(raw.get("ref_frame") or 0)
    except (TypeError, ValueError):
        ref = 0
    return {"mode": mode, "source": src, "points": pts, "target": target,
            "apply": {"position": bool(ap.get("position", True)), "rotation": bool(ap.get("rotation", False)),
                      "scale": bool(ap.get("scale", False))},
            "smooth": max(0, min(61, int(raw.get("smooth") or 0))), "offset": offset,
            "fill": fill if fill in FILLS else "none", "ref_frame": max(0, ref),
            "simplify": bool(raw.get("simplify", True))}


def list_follow_targets(task, host) -> list:
    """能接上已发布运动的图层：文字、图片、调整图层，以及任何其他视频片段。

    宿主自己不在列表里。不要求时间重叠 —— 蒙版片段即使起止和跟踪片段错开，也能挂上。
    发布仍然写成普通关键帧（bake_follow），导出路径不变。
    """
    import os

    out = []
    if host is None or not isinstance(task, dict):
        return out
    for lst in ("text_overlays", "image_overlays"):
        for i, ov in enumerate(task.get(lst) or []):
            if not isinstance(ov, dict):
                continue
            if lst == "text_overlays":
                role = "text"
                name = str(ov.get("content") or ov.get("text") or "").strip().replace("\n", " ")
            elif str(ov.get("type") or "") == "adjust":
                role = "adjust"
                name = str(ov.get("name") or ov.get("content") or "")
            else:
                role = "image"
                name = os.path.basename(str(ov.get("content") or ov.get("path") or ""))
            out.append({
                "kind": "overlay",
                "list": lst,
                "id": ov.get("id"),
                "index": i,
                "role": role,
                "name": name[:40],
            })
    tl = task.get("timeline")
    for tr_obj in getattr(tl, "tracks", []) or []:
        if getattr(tr_obj, "type", "") != "video":
            continue
        tid = str(getattr(tr_obj, "id", "") or "")
        for ci, c in enumerate(getattr(tr_obj, "clips", []) or []):
            if c is host:
                continue
            out.append({
                "kind": "clip",
                "track_id": tid,
                "clip_index": ci,
                "role": "video",
                "name": str(getattr(c, "name", "") or ""),
            })
    return out


def same_target(a: dict, b: dict) -> bool:
    ta, tb = a.get("target") or {}, b.get("target") or {}
    if ta.get("kind") != tb.get("kind"):
        return False
    if ta.get("kind") == "overlay":
        if ta.get("id") is not None and tb.get("id") is not None:
            return ta.get("id") == tb.get("id") and ta.get("list") == tb.get("list")
        return ta.get("list") == tb.get("list") and ta.get("index") == tb.get("index")
    if ta.get("kind") == "clip":
        return ta.get("track_id") == tb.get("track_id") and ta.get("clip_index") == tb.get("clip_index")
    return True


# ───────────────────────────── 片段几何 ─────────────────────────────

def fit_rect(src_w, src_h, dst_w, dst_h, fit_mode="fit", scale=1.0, offset_x=0.0, offset_y=0.0, scale_y=1.0):
    """和 RenderWorker._compute_fit_rect 同一算法（纯函数版）：(x, y, w, h)。"""
    if src_w <= 0 or src_h <= 0:
        return 0.0, 0.0, float(dst_w), float(dst_h)
    from cutora.model.timeline import DEFAULT_VIDEO_FIT_MODE
    fm = (fit_mode or DEFAULT_VIDEO_FIT_MODE).lower()
    sy = float(scale_y if scale_y not in (None, "") else 1.0)
    if sy <= 0:
        sy = 1.0
    if fm == "source":
        tw, th = src_w * scale, src_h * scale
    elif fm == "stretch":
        tw, th = dst_w * scale, dst_h * scale
    elif fm == "fill":
        r = max(dst_w / src_w, dst_h / src_h)
        tw, th = src_w * r * scale, src_h * r * scale
    else:
        r = min(dst_w / src_w, dst_h / src_h)
        tw, th = src_w * r * scale, src_h * r * scale
    if abs(sy - 1.0) > 1e-9:
        th *= sy
    return (dst_w - tw) / 2.0 + offset_x, (dst_h - th) / 2.0 + offset_y, tw, th


class ClipGeom:
    """某一时刻视频片段在画布上的几何。visual 可覆盖 x / y / scale / rotation（稳定时试算用）。"""

    def __init__(self, clip, timeline_t: float, canvas_w: int, canvas_h: int, src_w: int, src_h: int,
                 visual: dict | None = None):
        from cutora.model.project_schema import clip_scale_y
        from cutora.render.keyframe import resolve_clip_visual
        from cutora.model.timeline import DEFAULT_VIDEO_FIT_MODE
        vis = dict(resolve_clip_visual(clip, timeline_t))
        if visual:
            vis.update(visual)
        self.vis = vis
        self.src_w, self.src_h = float(src_w), float(src_h)
        self.crop = tuple(float(max(0, int(_g(clip, k, 0) or 0)))
                          for k in ("crop_left", "crop_right", "crop_top", "crop_bottom"))
        cl, cr, ct, cb = self.crop
        self.fw = max(1.0, self.src_w - cl - cr)
        self.fh = max(1.0, self.src_h - ct - cb)
        self.flip_x = bool(_g(clip, "flip_x", False))
        self.flip_y = bool(_g(clip, "flip_y", False))
        fit = str(_g(clip, "fit_mode", DEFAULT_VIDEO_FIT_MODE) or DEFAULT_VIDEO_FIT_MODE)
        if str(_g(clip, "size_mode", "project") or "project").lower() == "source":
            fit = "source"
        self.cw, self.ch = float(canvas_w), float(canvas_h)
        x, y, w, h = fit_rect(self.fw, self.fh, self.cw, self.ch, fit, float(vis["scale"]), float(vis["x"]),
                              float(vis["y"]), clip_scale_y(clip))
        self.rect = (x, y, w, h)
        self.center = (x + w / 2.0, y + h / 2.0)
        self.rotation = float(vis["rotation"])

    def to_canvas(self, sx: float, sy: float) -> tuple:
        cl, _cr, ct, _cb = self.crop
        x, y = sx - cl, sy - ct
        if self.flip_x:
            x = self.fw - x
        if self.flip_y:
            y = self.fh - y
        _rx, _ry, w, h = self.rect
        lx = (x / self.fw - 0.5) * w
        ly = (y / self.fh - 0.5) * h
        a = math.radians(self.rotation)
        c, s = math.cos(a), math.sin(a)
        return self.center[0] + lx * c - ly * s, self.center[1] + lx * s + ly * c

    def corners(self) -> list:
        x, y, w, h = self.rect
        a = math.radians(self.rotation)
        c, s = math.cos(a), math.sin(a)
        out = []
        for lx, ly in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            out.append((self.center[0] + lx * c - ly * s, self.center[1] + lx * s + ly * c))
        return out

    def covers_canvas(self, margin: float = 0.5) -> bool:
        """画布四角都在画面里（稳定后不露黑边）。"""
        x, y, w, h = self.rect
        a = math.radians(-self.rotation)
        c, s = math.cos(a), math.sin(a)
        for px, py in ((margin, margin), (self.cw - margin, margin), (self.cw - margin, self.ch - margin),
                       (margin, self.ch - margin)):
            dx, dy = px - self.center[0], py - self.center[1]
            lx, ly = dx * c - dy * s, dx * s + dy * c
            if abs(lx) > w / 2.0 + 1e-6 or abs(ly) > h / 2.0 + 1e-6:
                return False
        return True

    def mirrored(self) -> bool:
        return self.flip_x != self.flip_y


# ───────────────────────────── 采样 ─────────────────────────────

def _span(obj) -> tuple:
    """(开始, 结束) 时间线秒。"""
    if isinstance(obj, dict):
        st = float(obj.get("start") or 0.0)
        en = obj.get("end")
        if en is None:
            en = st + float(obj.get("duration") or 0.0)
        return st, float(en)
    st = float(_g(obj, "start_t", 0.0))
    dur = float(getattr(obj, "effective_duration", 0.0) or 0.0) if hasattr(obj, "effective_duration") else 0.0
    if dur <= 0:
        in_t, out_t = float(_g(obj, "in_t", 0.0)), float(_g(obj, "out_t", 0.0))
        dur = (out_t - in_t) / max(0.01, float(_g(obj, "speed", 1.0) or 1.0))
    return st, st + dur


def samples(tracked_clip, motion, target=None) -> list:
    """[(源帧号, 时间线秒)]：运动数据里落在被跟踪片段（和目标）时间范围内的帧。"""
    from cutora.fx.ai_matte import source_frame_to_timeline
    a0, a1 = _span(tracked_clip)
    if target is not None:
        b0, b1 = _span(target)
        a0, a1 = max(a0, b0), min(a1, b1)
    out = []
    for f in motion.frames:
        t = source_frame_to_timeline(tracked_clip, int(f), motion.fps)
        if a0 - 1e-6 <= t < a1 - 1e-9:
            out.append((int(f), float(t)))
    return out


# ───────────────────────────── 精简 ─────────────────────────────

def simplify(ts: list, vs: list, tol: float) -> list:
    """Ramer–Douglas–Peucker（在 时间 × 值 上，误差只量值方向）：留下的下标。"""
    n = len(ts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        t0, t1, v0, v1 = ts[i], ts[j], vs[i], vs[j]
        span = t1 - t0
        worst, k = -1.0, -1
        for m in range(i + 1, j):
            u = (ts[m] - t0) / span if span > 1e-12 else 0.0
            err = abs(vs[m] - (v0 + (v1 - v0) * u))
            if err > worst:
                worst, k = err, m
        if worst > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [i for i in range(n) if keep[i]]


def _to_points(local_ts: list, values: list, tol: float, simplify_on: bool) -> list:
    idx = simplify(local_ts, values, tol) if simplify_on else list(range(len(local_ts)))
    return [{"t": round(float(local_ts[i]), 6), "v": float(values[i]), "ease": "linear"} for i in idx]


# ───────────────────────────── 烘焙 ─────────────────────────────

def _static(obj, key, default):
    try:
        return float(_g(obj, key, default))
    except (TypeError, ValueError):
        return float(default)


def bake_follow(motion, tracked_clip, target, binding: dict, canvas_w: int, canvas_h: int) -> dict:
    """目标（覆层 dict 或另一条视频片段）跟着运动数据走：返回 {"paths": {path: [点…]}, "frames": n}。

    在参考帧时刻：目标中心相对跟踪锚点的偏移 d0 记下来；之后每帧
    中心 = 锚点(t) + R(Δθ)·S(Δs)·d0 + 用户偏移。"""
    from cutora.render.keyframe import bake_host, local_time, resolve_clip_visual
    from cutora.fx.ai_matte import source_frame_to_timeline
    ap = binding["apply"]
    sm = samples(tracked_clip, motion, target)
    if not sm:
        return {"paths": {}, "frames": 0}
    ref_f = int(binding.get("ref_frame") or motion.ref_frame)
    ref_t = source_frame_to_timeline(tracked_clip, ref_f, motion.fps)
    W, H = motion.src_w, motion.src_h
    g0 = ClipGeom(tracked_clip, ref_t, canvas_w, canvas_h, W, H)
    a0 = motion.at(ref_f)
    p0 = g0.to_canvas(a0[0], a0[1])
    is_overlay = isinstance(target, dict)
    if is_overlay:
        base = bake_host(target, local_time(target, ref_t))
        w, h = _static(base, "w", 0.0), _static(base, "h", 0.0)
        c0 = (_static(base, "x", 0.0) + w / 2.0, _static(base, "y", 0.0) + h / 2.0)
        rot0, sc0 = _static(base, "rotation", 0.0), _static(base, "scale", 1.0)
    else:
        vis = resolve_clip_visual(target, ref_t)
        c0 = (canvas_w / 2.0 + float(vis["x"]), canvas_h / 2.0 + float(vis["y"]))
        rot0, sc0 = float(vis["rotation"]), float(vis["scale"])
        w = h = 0.0
    d0 = (c0[0] - p0[0], c0[1] - p0[1])
    off = binding.get("offset") or [0.0, 0.0]
    ts, xs, ys, rs, ss = [], [], [], [], []
    for f, t in sm:
        ax, ay, rot, sc, _lost = motion.at(f)
        g = ClipGeom(tracked_clip, t, canvas_w, canvas_h, W, H)
        p = g.to_canvas(ax, ay)
        drot = (-rot if g.mirrored() else rot) if ap["rotation"] else 0.0
        dsc = sc if ap["scale"] else 1.0
        a = math.radians(drot)
        c, s = math.cos(a), math.sin(a)
        dx = dsc * (c * d0[0] - s * d0[1])
        dy = dsc * (s * d0[0] + c * d0[1])
        cx, cy = p[0] + dx + float(off[0]), p[1] + dy + float(off[1])
        ts.append(local_time(target, t))
        if is_overlay:
            xs.append(cx - w / 2.0)
            ys.append(cy - h / 2.0)
        else:
            xs.append(cx - canvas_w / 2.0)
            ys.append(cy - canvas_h / 2.0)
        rs.append(rot0 + drot)
        ss.append(sc0 * dsc)
    simp = bool(binding.get("simplify", True))
    paths = {}
    if ap["position"]:
        paths["x"] = _to_points(ts, xs, TOLERANCE["x"], simp)
        paths["y"] = _to_points(ts, ys, TOLERANCE["y"], simp)
    if ap["rotation"]:
        paths["rotation"] = _to_points(ts, rs, TOLERANCE["rotation"], simp)
    if ap["scale"]:
        paths["scale"] = _to_points(ts, ss, TOLERANCE["scale"], simp)
    return {"paths": paths, "frames": len(ts)}


def bake_stabilize(motion, clip, binding: dict, canvas_w: int, canvas_h: int) -> dict:
    """被跟踪的片段自己反着动：参考帧时锚点在画布哪里，之后每帧都钉在那里（可选抵消旋转 / 缩放）。

    fill="auto"：再整体放大到每一帧都不露黑边（取所有帧需要的最大倍数，上限 3×）。"""
    from cutora.render.keyframe import local_time, resolve_clip_visual
    from cutora.fx.ai_matte import source_frame_to_timeline
    ap = binding["apply"]
    sm = samples(clip, motion)
    if not sm:
        return {"paths": {}, "frames": 0, "zoom": 1.0}
    W, H = motion.src_w, motion.src_h
    ref_f = int(binding.get("ref_frame") or motion.ref_frame)
    ref_t = source_frame_to_timeline(clip, ref_f, motion.fps)
    a0 = motion.at(ref_f)
    p0 = ClipGeom(clip, ref_t, canvas_w, canvas_h, W, H).to_canvas(a0[0], a0[1])

    def _solve(f, t, zoom):
        ax, ay, rot, sc, _lost = motion.at(f)
        vis = resolve_clip_visual(clip, t)
        g_probe = ClipGeom(clip, t, canvas_w, canvas_h, W, H)
        drot = (-rot if g_probe.mirrored() else rot) if ap["rotation"] else 0.0
        new_rot = float(vis["rotation"]) - drot
        new_scale = float(vis["scale"]) / (sc if ap["scale"] else 1.0) * zoom
        g = ClipGeom(clip, t, canvas_w, canvas_h, W, H,
                     visual={"rotation": new_rot, "scale": new_scale, "x": float(vis["x"]), "y": float(vis["y"])})
        q = g.to_canvas(ax, ay)
        if ap["position"]:
            nx, ny = float(vis["x"]) + (p0[0] - q[0]), float(vis["y"]) + (p0[1] - q[1])
        else:
            nx, ny = float(vis["x"]), float(vis["y"])
        return nx, ny, new_rot, new_scale

    zoom = 1.0
    if binding.get("fill") == "auto":
        for f, t in sm:
            lo, hi = zoom, 3.0
            nx, ny, nr, ns = _solve(f, t, lo)
            g = ClipGeom(clip, t, canvas_w, canvas_h, W, H, visual={"x": nx, "y": ny, "rotation": nr, "scale": ns})
            if g.covers_canvas():
                continue
            for _ in range(24):
                mid = (lo + hi) / 2.0
                nx, ny, nr, ns = _solve(f, t, mid)
                g = ClipGeom(clip, t, canvas_w, canvas_h, W, H, visual={"x": nx, "y": ny, "rotation": nr, "scale": ns})
                if g.covers_canvas():
                    hi = mid
                else:
                    lo = mid
            zoom = max(zoom, hi)
    ts, xs, ys, rs, ss = [], [], [], [], []
    for f, t in sm:
        nx, ny, nr, ns = _solve(f, t, zoom)
        ts.append(local_time(clip, t))
        xs.append(nx)
        ys.append(ny)
        rs.append(nr)
        ss.append(ns)
    simp = bool(binding.get("simplify", True))
    paths = {}
    if ap["position"]:
        paths["x"] = _to_points(ts, xs, TOLERANCE["x"], simp)
        paths["y"] = _to_points(ts, ys, TOLERANCE["y"], simp)
    if ap["rotation"]:
        paths["rotation"] = _to_points(ts, rs, TOLERANCE["rotation"], simp)
    if ap["scale"] or zoom > 1.0 + 1e-6:
        paths["scale"] = _to_points(ts, ss, TOLERANCE["scale"], simp)
    return {"paths": paths, "frames": len(ts), "zoom": zoom}


def motion_for(clip, binding: dict, frame_range: tuple):
    """按绑定记录取运动数据（点跟踪 / 对象跟踪）。"""
    from cutora.fx import ai_matte as A
    from cutora.fx import motion as MO
    from cutora.fx import tracking as TR
    if binding["source"] == "object":
        return MO.object_motion(A.spec(clip), frame_range, binding["ref_frame"], smooth_frames=binding["smooth"],
                                with_rotation=binding["apply"]["rotation"])
    sp = TR.spec(clip)
    pts = binding["points"] or [p["id"] for p in sp["points"] if p.get("enabled", True)]
    if not sp["session"] or not pts:
        return None
    return MO.point_motion(sp["session"], pts, frame_range, binding["ref_frame"], smooth_frames=binding["smooth"])
