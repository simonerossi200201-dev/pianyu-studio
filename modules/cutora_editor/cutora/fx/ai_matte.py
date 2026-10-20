# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · AI 抠像：图层上的设置 + 把缓存里的 mask 变成这一帧的 alpha。

图层（视频 Clip）上存一个 dict ``ai_matte``::

    {
      "enabled": true,                 # 关掉 = 不套，但提示点 / 结果都还在
      "session": "m1a2b3c4d5",         # matte_store 的会话 id（结果缓存文件夹）
      "model": "matte-sam21-large",
      "objects": [{"id": 1, "name": "对象 1", "color": "#F2B33D", "enabled": true}],
      "prompts": {"123": [{"obj": 1, "x": 0.41, "y": 0.52, "label": 1},        # 源帧归一化坐标 0–1
                          {"obj": 1, "box": [0.2, 0.1, 0.7, 0.9]}]},
      "feather": 0.0,                  # 画布像素
      "shrink": 0.0,                   # 画布像素，+ 收缩 / − 扩张
      "refine": false,                 # 导向滤波精修边缘（用画面当引导）
      "merge": true,                   # Q166：「保留」的几个对象当成一整块（去掉相邻对象之间的接缝）
      "view": "comp"                   # comp 合成 | matte 黑白 | overlay 彩色叠加（只在预览里用）
    }

提示点用源帧的归一化坐标（0–1，没裁切没翻转的那张），所以改裁切 / 翻转 / 缩放不影响已有的点。
求值入口 :func:`alpha_for_frame`：按源时间找缓存里的 mask → 按图层同样的裁切 / 翻转顺序变换 →
缩放到这一帧的尺寸 → 收缩 / 羽化 / 精修 → float32 0–1。没有这一帧的结果 → None（画面原样）。
"""
from __future__ import annotations

import numpy as np

from cutora.fx import matte_store as S

DEFAULTS = {
    "enabled": True,
    "session": "",
    "model": "matte-sam21-large",
    "objects": [],
    "prompts": {},
    "feather": 0.0,
    "shrink": 0.0,
    "refine": False,
    "merge": True,
    "view": "comp",
}
VIEWS = ("comp", "matte", "overlay")
OBJECT_COLORS = ("#F2B33D", "#4FC3F7", "#EF7A7A", "#81C784", "#CE93D8", "#FFB74D", "#4DB6AC", "#F06292")
MAX_OBJECTS = 8
OBJECT_MODES = ("add", "subtract")      # 保留（并进来）/ 挖掉（从保留的里减掉）


def spec(host) -> dict:
    raw = host.get("ai_matte") if isinstance(host, dict) else getattr(host, "ai_matte", None)
    return normalize(raw)


def normalize(raw) -> dict:
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULTS.items()}
    if not isinstance(raw, dict):
        return out
    out["enabled"] = bool(raw.get("enabled", True))
    out["session"] = str(raw.get("session") or "")
    out["model"] = str(raw.get("model") or DEFAULTS["model"])
    objs = []
    for o in raw.get("objects") or []:
        if not isinstance(o, dict):
            continue
        try:
            oid = int(o.get("id"))
        except (TypeError, ValueError):
            continue
        mode = str(o.get("mode") or "add")
        objs.append({"id": oid, "name": str(o.get("name") or ("对象 %d" % oid)),
                     "color": str(o.get("color") or OBJECT_COLORS[(oid - 1) % len(OBJECT_COLORS)]),
                     "enabled": bool(o.get("enabled", True)),
                     "mode": mode if mode in OBJECT_MODES else "add"})
    out["objects"] = objs
    prompts = {}
    for k, lst in (raw.get("prompts") or {}).items():
        try:
            fi = int(k)
        except (TypeError, ValueError):
            continue
        clean = []
        for p in lst or []:
            if not isinstance(p, dict):
                continue
            try:
                oid = int(p.get("obj", 1))
            except (TypeError, ValueError):
                continue
            if "box" in p and p["box"] is not None:
                try:
                    b = [max(0.0, min(1.0, float(v))) for v in p["box"]][:4]
                except (TypeError, ValueError):
                    continue
                if len(b) == 4:
                    clean.append({"obj": oid, "box": b})
            elif "x" in p and "y" in p:
                try:
                    clean.append({"obj": oid, "x": max(0.0, min(1.0, float(p["x"]))),
                                  "y": max(0.0, min(1.0, float(p["y"]))), "label": 1 if int(p.get("label", 1)) else 0})
                except (TypeError, ValueError):
                    continue
        if clean:
            prompts[str(fi)] = clean
    out["prompts"] = prompts
    for k, lo, hi in (("feather", 0.0, 200.0), ("shrink", -100.0, 100.0)):
        try:
            out[k] = max(lo, min(hi, float(raw.get(k, 0.0) or 0.0)))
        except (TypeError, ValueError):
            out[k] = 0.0
    out["refine"] = bool(raw.get("refine", False))
    out["merge"] = bool(raw.get("merge", True))
    v = str(raw.get("view") or "comp")
    out["view"] = v if v in VIEWS else "comp"
    return out


def is_active(host) -> bool:
    """有会话且启用 → 渲染要查缓存。"""
    if host is None:
        return False
    raw = host.get("ai_matte") if isinstance(host, dict) else getattr(host, "ai_matte", None)
    if not isinstance(raw, dict) or not raw.get("session") or not raw.get("enabled", True):
        return False
    return True


def has_data(host) -> bool:
    sp = spec(host)
    return bool(sp["session"] or sp["objects"] or sp["prompts"])


def signature(sp: dict) -> tuple:
    return (sp["session"], tuple((o["id"], o["enabled"], o.get("mode", "add")) for o in sp["objects"]),
            round(sp["feather"], 2), round(sp["shrink"], 2), bool(sp["refine"]), sp["view"],
            bool(sp.get("merge", True)))


def is_empty(sp: dict) -> bool:
    """全默认（没有会话、对象、提示）→ 可以把 dict 从图层上拿掉。"""
    return not (sp["session"] or sp["objects"] or sp["prompts"])


# ───────────────────────────── 对象 / 提示的纯函数 ─────────────────────────────

def new_object(sp: dict) -> dict:
    used = {o["id"] for o in sp["objects"]}
    oid = 1
    while oid in used:
        oid += 1
    return {"id": oid, "name": "对象 %d" % oid, "color": OBJECT_COLORS[(oid - 1) % len(OBJECT_COLORS)],
            "enabled": True, "mode": "add"}


def combined_raw(sp: dict, frame_idx: int, tolerance: int = 1):
    """缓存里这一帧的合成 alpha（缓存尺寸，float32）：保留的对象取并集，再减掉「挖掉」的对象。

    有「保留」对象但这一帧它们没结果 → None（不能退化成「全留只挖」，否则和相邻帧对不上会闪）；
    一个「保留」对象都没有 → 全画面减掉「挖掉」的对象。"""
    add_ids = [o["id"] for o in sp["objects"] if o.get("enabled", True) and o.get("mode", "add") == "add"]
    sub_ids = [o["id"] for o in sp["objects"] if o.get("enabled", True) and o.get("mode") == "subtract"]
    if not add_ids and not sub_ids:
        return None
    keep = None
    if add_ids:
        if sp.get("merge", True) and len(add_ids) >= 2:
            keep = _merged_keep(sp["session"], frame_idx, add_ids, tolerance)
        else:
            keep, _u = S.read_alpha(sp["session"], frame_idx, add_ids, tolerance=tolerance)
        if keep is None:
            return None
    cut = None
    if sub_ids:
        cut, _u = S.read_alpha(sp["session"], frame_idx, sub_ids, tolerance=tolerance)
    if keep is None:
        if cut is None:
            return None
        return (1.0 - cut).astype(np.float32)
    if cut is not None and cut.shape == keep.shape:
        keep = keep * (1.0 - cut)
    return keep.astype(np.float32)


def seam_radius(h: int, w: int) -> int:
    """接缝能补多宽（缓存像素）：短边的 0.8%，至少 2。"""
    return max(2, int(round(0.008 * min(int(h), int(w)))))


def merge_masks(masks) -> np.ndarray | None:
    """Q166：几个对象的 alpha（同尺寸 float32 0–1）合成一整块。

    以前逐像素取 max：两个对象挨着的地方（人和手里的书）各自边缘都是半透明 / 隔着一两个像素的缝，
    max 以后那条线还是半透明 → 合成出来一条接缝。这里先取并集，再只在「两个以上对象都很近」的交界带
    里做一次闭运算（先膨胀再腐蚀）把缝填实；对象和背景之间的外轮廓不动。"""
    import cv2
    ms = [np.ascontiguousarray(m, dtype=np.float32) for m in masks if m is not None]
    if not ms:
        return None
    shape = ms[0].shape
    ms = [m for m in ms if m.shape == shape]
    union = ms[0].copy()
    for m in ms[1:]:
        np.maximum(union, m, out=union)
    if len(ms) < 2:
        return union
    r = seam_radius(*shape)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    # 交界带：两个对象各自往外扩 2r 以后重叠的地方（缝最宽能补到约 2r）
    kz = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (4 * r + 1, 4 * r + 1))
    count = np.zeros(shape, np.uint8)
    for m in ms:
        near = cv2.dilate((m > 0.1).astype(np.uint8), kz)
        count += near
    zone = count >= 2
    if not zone.any():
        return union
    closed = cv2.morphologyEx(union, cv2.MORPH_CLOSE, kern)
    out = union.copy()
    out[zone] = np.maximum(union[zone], closed[zone])
    return out


def _merged_keep(sid, frame_idx, ids, tolerance):
    parts = []
    for oid in ids:
        a, _u = S.read_alpha(sid, frame_idx, [oid], tolerance=tolerance)
        if a is not None:
            parts.append(a)
    return merge_masks(parts) if parts else None


def enabled_object_ids(sp: dict) -> list:
    return [o["id"] for o in sp["objects"] if o.get("enabled", True)]


def prompts_at(sp: dict, frame_idx: int) -> list:
    return list(sp["prompts"].get(str(int(frame_idx)), []))


def prompt_frames(sp: dict) -> list:
    return sorted(int(k) for k in sp["prompts"])


def to_pixels(prompts, src_w: int, src_h: int) -> list:
    """归一化提示 → 源帧像素（后端要的）。"""
    out = []
    for p in prompts:
        q = dict(p)
        if "box" in q:
            b = q["box"]
            q["box"] = [b[0] * src_w, b[1] * src_h, b[2] * src_w, b[3] * src_h]
        else:
            q["x"], q["y"] = q["x"] * src_w, q["y"] * src_h
        out.append(q)
    return out


# ───────────────────────────── 坐标：显示帧 ⇄ 源帧 ─────────────────────────────

def layer_to_source_norm(x: float, y: float, geom: dict) -> tuple:
    """显示出来的那一帧里的归一化点 (0–1) → 源帧归一化点。

    geom: {"src_w", "src_h", "crop": (l, r, t, b), "flip": (fx, fy), "order": "crop_flip" | "flip_crop"}。
    导出链是 裁切 → 翻转；画布单层预览是 翻转 → 裁切。"""
    W, H = float(geom["src_w"]), float(geom["src_h"])
    cl, cr, ct, cb = [max(0.0, float(v)) for v in geom.get("crop", (0, 0, 0, 0))]
    fx, fy = geom.get("flip", (False, False))
    order = geom.get("order", "crop_flip")
    cw, ch = max(1.0, W - cl - cr), max(1.0, H - ct - cb)
    if order == "crop_flip":
        # 显示 = flip(crop(src))：先解翻转（在裁切框内），再解裁切
        if fx:
            x = 1.0 - x
        if fy:
            y = 1.0 - y
        sx, sy = (cl + x * cw) / W, (ct + y * ch) / H
    else:
        # 显示 = crop(flip(src))：先解裁切（在翻转后的图里），再解翻转
        px, py = cl + x * cw, ct + y * ch
        if fx:
            px = W - px
        if fy:
            py = H - py
        sx, sy = px / W, py / H
    return max(0.0, min(1.0, sx)), max(0.0, min(1.0, sy))


def source_to_layer_norm(sx: float, sy: float, geom: dict) -> tuple:
    """反方向（画提示点用）。"""
    W, H = float(geom["src_w"]), float(geom["src_h"])
    cl, cr, ct, cb = [max(0.0, float(v)) for v in geom.get("crop", (0, 0, 0, 0))]
    fx, fy = geom.get("flip", (False, False))
    order = geom.get("order", "crop_flip")
    cw, ch = max(1.0, W - cl - cr), max(1.0, H - ct - cb)
    px, py = sx * W, sy * H
    if order == "crop_flip":
        x, y = (px - cl) / cw, (py - ct) / ch
        if fx:
            x = 1.0 - x
        if fy:
            y = 1.0 - y
    else:
        if fx:
            px = W - px
        if fy:
            py = H - py
        x, y = (px - cl) / cw, (py - ct) / ch
    return x, y


def transform_mask(mask: np.ndarray, geom: dict, out_w: int, out_h: int) -> np.ndarray:
    """源帧尺寸的 mask → 显示帧（同样的裁切 / 翻转顺序）→ 缩放到 (out_w, out_h)。"""
    import cv2
    h, w = mask.shape[:2]
    cl, cr, ct, cb = [int(max(0, round(float(v)))) for v in geom.get("crop", (0, 0, 0, 0))]
    fx, fy = geom.get("flip", (False, False))
    order = geom.get("order", "crop_flip")

    def _crop(m):
        hh, ww = m.shape[:2]
        if cl <= 0 and cr <= 0 and ct <= 0 and cb <= 0:
            return m
        x0 = min(cl, ww - 1)
        y0 = min(ct, hh - 1)
        x1 = max(x0 + 1, ww - cr)
        y1 = max(y0 + 1, hh - cb)
        return m[y0:y1, x0:x1]

    def _flip(m):
        if fx and fy:
            return m[::-1, ::-1]
        if fx:
            return m[:, ::-1]
        if fy:
            return m[::-1, :]
        return m

    # 缓存里的 mask 可能不是源分辨率（例如将来存半分辨率）：先把裁切量换算到 mask 的尺度
    sw, sh = float(geom.get("src_w") or w), float(geom.get("src_h") or h)
    if (w, h) != (int(sw), int(sh)) and sw > 0 and sh > 0:
        kx, ky = w / sw, h / sh
        cl, cr, ct, cb = int(round(cl * kx)), int(round(cr * kx)), int(round(ct * ky)), int(round(cb * ky))
    m = _flip(_crop(mask)) if order == "crop_flip" else _crop(_flip(mask))
    m = np.ascontiguousarray(m, dtype=np.float32)
    if m.shape[1] != int(out_w) or m.shape[0] != int(out_h):
        interp = cv2.INTER_AREA if m.shape[1] > out_w else cv2.INTER_LINEAR
        m = cv2.resize(m, (int(out_w), int(out_h)), interpolation=interp)
    return m


# ───────────────────────────── 边缘整理 ─────────────────────────────

def guided_refine(alpha: np.ndarray, guide_rgb: np.ndarray, radius: int = 8, eps: float = 1e-3) -> np.ndarray:
    """导向滤波（He 2010）把二值感的 mask 贴到画面边缘上：alpha ≈ a·I + b，窗口内线性。纯 cv2 box filter。"""
    import cv2
    if guide_rgb is None or guide_rgb.shape[:2] != alpha.shape[:2]:
        return alpha
    guide = cv2.cvtColor(np.ascontiguousarray(guide_rgb[..., :3]), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    r = max(1, int(radius))
    ks = (2 * r + 1, 2 * r + 1)
    mean_i = cv2.blur(guide, ks)
    mean_p = cv2.blur(alpha, ks)
    corr_i = cv2.blur(guide * guide, ks)
    corr_ip = cv2.blur(guide * alpha, ks)
    var_i = corr_i - mean_i * mean_i
    cov_ip = corr_ip - mean_i * mean_p
    a = cov_ip / (var_i + np.float32(eps))
    b = mean_p - a * mean_i
    out = cv2.blur(a, ks) * guide + cv2.blur(b, ks)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def finish_alpha(alpha: np.ndarray, sp: dict, px_scale: float = 1.0, guide_rgb=None) -> np.ndarray:
    """收缩 / 扩张 → 羽化 → 精修。单位画布像素 × px_scale。"""
    import cv2
    a = np.ascontiguousarray(alpha, dtype=np.float32)
    shrink = float(sp.get("shrink") or 0.0) * float(px_scale)
    if abs(shrink) >= 0.5:
        k = int(round(abs(shrink))) * 2 + 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        a = cv2.erode(a, kern) if shrink > 0 else cv2.dilate(a, kern)
    feather = float(sp.get("feather") or 0.0) * float(px_scale)
    if feather >= 0.5:
        sigma = feather / 2.0
        ks = int(sigma * 3) * 2 + 1
        a = cv2.GaussianBlur(a, (ks, ks), sigma)
    if sp.get("refine") and guide_rgb is not None:
        a = guided_refine(a, guide_rgb, radius=max(2, int(round(6 * float(px_scale)))))
    return np.clip(a, 0.0, 1.0).astype(np.float32)


# ───────────────────────────── 求值入口 ─────────────────────────────

def frame_index_for(sp_or_sid, src_t: float) -> int | None:
    sid = sp_or_sid["session"] if isinstance(sp_or_sid, dict) else str(sp_or_sid or "")
    idx = S.load_index(sid)
    if not idx:
        return None
    return S.frame_index(src_t, float(idx.get("fps") or 30.0))


def session_geom(sp: dict) -> dict | None:
    idx = S.load_index(sp["session"]) if sp.get("session") else None
    if not idx:
        return None
    return {"src_w": int(idx.get("src_w") or 0), "src_h": int(idx.get("src_h") or 0),
            "fps": float(idx.get("fps") or 30.0)}


def alpha_for_frame(host, src: dict, out_w: int, out_h: int, *, px_scale: float = 1.0, guide_rgb=None):
    """这一帧的 AI alpha（float32 (out_h, out_w)）或 None。

    src: {"src_t": 源时间秒, "crop": (l, r, t, b), "flip": (fx, fy), "order": "crop_flip" | "flip_crop"}"""
    sp = spec(host)
    if not (sp["enabled"] and sp["session"]):
        return None
    idx = S.load_index(sp["session"])
    if not idx:
        return None
    fps = float(idx.get("fps") or 30.0)
    fi = S.frame_index(float(src.get("src_t") or 0.0), fps)
    raw = combined_raw(sp, fi, tolerance=1)
    if raw is None:
        return None
    geom = {"src_w": int(idx.get("src_w") or raw.shape[1]), "src_h": int(idx.get("src_h") or raw.shape[0]),
            "crop": src.get("crop", (0, 0, 0, 0)), "flip": src.get("flip", (False, False)),
            "order": src.get("order", "crop_flip")}
    a = transform_mask(raw, geom, out_w, out_h)
    return finish_alpha(a, sp, px_scale, guide_rgb)


def overlay_rgba(host, src: dict, out_w: int, out_h: int, *, px_scale: float = 1.0):
    """预览的「彩色叠加」：每个对象一种颜色，(out_h, out_w, 4) uint8 预乘 BGRA；没有 → None。"""
    sp = spec(host)
    if not sp["session"]:
        return None
    idx = S.load_index(sp["session"])
    if not idx:
        return None
    fps = float(idx.get("fps") or 30.0)
    fi = S.frame_index(float(src.get("src_t") or 0.0), fps)
    geom = {"src_w": int(idx.get("src_w") or 0), "src_h": int(idx.get("src_h") or 0),
            "crop": src.get("crop", (0, 0, 0, 0)), "flip": src.get("flip", (False, False)),
            "order": src.get("order", "crop_flip")}
    out = np.zeros((int(out_h), int(out_w), 4), np.float32)
    any_obj = False
    for o in sp["objects"]:
        if not o.get("enabled", True):
            continue
        a, _used = S.read_alpha(sp["session"], fi, [o["id"]], tolerance=1)
        if a is None:
            continue
        any_obj = True
        if not geom["src_w"]:
            geom["src_w"], geom["src_h"] = a.shape[1], a.shape[0]
        m = transform_mask(a, geom, out_w, out_h) * 0.55
        c = o["color"].lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        out[..., 0] = np.maximum(out[..., 0], m * b)
        out[..., 1] = np.maximum(out[..., 1], m * g)
        out[..., 2] = np.maximum(out[..., 2], m * r)
        out[..., 3] = np.maximum(out[..., 3], m * 255.0)
    if not any_obj:
        return None
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# ───────────────────────────── 低置信度帧 / 时间换算（时间线条、跳转按钮用）─────────────────────────────

LOW_SCORE = 0.5


def source_frame_to_timeline(clip, frame_idx: int, fps: float) -> float:
    """源帧号 → 时间线秒（考虑 in 点和速度；循环片段只取第一遍）。"""
    def g(k, d):
        v = clip.get(k, d) if isinstance(clip, dict) else getattr(clip, k, d)
        return d if v is None else v
    start_t = float(g("start_t", 0.0))
    in_t = float(g("in_t", 0.0))
    speed = float(g("speed", 1.0) or 1.0) or 1.0
    return start_t + (float(frame_idx) / max(1e-6, float(fps)) - in_t) / speed


def coverage_runs(clip) -> list:
    """片段的覆盖段：[(t0, t1, low)]（时间线秒），连续且置信度同档的帧合成一段。"""
    sp = clip.get("ai_matte") if isinstance(clip, dict) else getattr(clip, "ai_matte", None)
    if not isinstance(sp, dict) or not sp.get("session"):
        from cutora.fx import graph as _G          # Q152-F：节点图里的 AI 抠像节点（取第一个有结果的）
        sp = None
        for n in _G.ai_nodes(clip):
            cand = (n.get("params") or {}).get("ai_matte")
            if isinstance(cand, dict) and cand.get("session"):
                sp = cand
                break
        if sp is None:
            return []
    idx = S.load_index(sp["session"])
    if not idx:
        return []
    cov = S.coverage(sp["session"])
    if not cov:
        return []
    fps = float(idx.get("fps") or 30.0)
    frames = sorted(cov)
    runs = []
    a = prev = frames[0]
    low = cov[a] < LOW_SCORE
    for f in frames[1:]:
        lf = cov[f] < LOW_SCORE
        if f == prev + 1 and lf == low:
            prev = f
            continue
        runs.append((a, prev, low))
        a = prev = f
        low = lf
    runs.append((a, prev, low))
    return [(source_frame_to_timeline(clip, x, fps), source_frame_to_timeline(clip, y + 1, fps), lo)
            for x, y, lo in runs]


def low_frames(sp: dict) -> list:
    if not sp.get("session"):
        return []
    return sorted(f for f, v in S.coverage(sp["session"]).items() if v < LOW_SCORE)


def neighbour_low_frame(sp: dict, current: int, direction: int):
    """当前帧之后（direction=1）/ 之前（-1）最近的低置信度段的第一帧；没有 → None。"""
    lows = low_frames(sp)
    if not lows:
        return None
    starts = [f for i, f in enumerate(lows) if i == 0 or lows[i - 1] != f - 1]
    if direction > 0:
        cand = [f for f in starts if f > current]
        return cand[0] if cand else None
    cand = [f for f in starts if f < current]
    return cand[-1] if cand else None
