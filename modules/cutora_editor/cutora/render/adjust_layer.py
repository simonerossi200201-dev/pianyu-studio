# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q174 · 调整轨道（调整图层）：纯色块 + 「对它下面所有画面」的局部效果。

时间线上一条 ``type == "adjust"`` 的轨道；轨道上的每一段是 ``task["image_overlays"]`` 里一条
``type == "adjust"`` 的覆层（``_track_id`` 指向这条轨道）—— 放在 image_overlays 里，所以移动 / 旋转 / 缩放、
关键帧、遮罩、跟踪这些覆层已有的本事全部直接能用。

一段调整图层 = 一个矩形（框 x / y / w / h + 旋转 + 缩放），两种身份可以同时开：

* **纯色**（``solid``）：填充色 / 不透明度 / 圆角 / 边框（宽度、颜色）。默认就是一块纯色矩形。
* **效果**（``effect``）：把它**下面**已经合成好的整幅画面（所有视频、图片、文字、字幕）在这个形状里处理一遍：
  镜头模糊、高斯模糊、马赛克、调色 —— Q179 起可以多选、按固定顺序串起来同时生效。形状 = 圆角矩形 × 羽化 × 这一段自己画的遮罩（遮罩面板里画的那些）。

渲染（预览 / 导出同一个函数 ``apply_track``）：在统一叠压（``render/visual_stack``）里，调整轨就是一摞里的一层；
画到它时，把画布上到此为止的内容拿出来、只在形状外接框（加模糊半径的余量）里处理，再合回去。
效果在前、纯色在后（开了填充就盖在效果上面，适合「半透明色块 + 底下模糊」）。
"""
from __future__ import annotations

import copy
import math
import threading

import numpy as np

ADJUST = "adjust"
EFFECT_KINDS = (("lens_blur", "镜头模糊"), ("blur", "高斯模糊"), ("mosaic", "马赛克"), ("grade", "调色"))
# Q179 效果可以多选、同时起作用（effect["kinds"]）。固定按这个顺序串起来做：先马赛克 / 模糊，最后调色
# （调色作用在模糊好的画面上 = 「模糊 + 压暗」最常见的做法）。每种模糊有自己的强度 amount_<种类>。
EFFECT_CHAIN = ("mosaic", "blur", "lens_blur", "grade")
AMOUNT_KINDS = ("lens_blur", "blur", "mosaic")

SOLID_DEFAULTS = {
    "on": True,
    "color": "#7A5C8E",
    "opacity": 1.0,          # 0–1
    "radius": 0.0,           # 圆角：短边的比例 0–0.5
    "border_on": False,
    "border_width": 6.0,     # 画布像素
    "border_color": "#FFFFFF",
    # Q175 描边生成动画：段开头用 stroke_in 秒把边框沿形状一笔画出来，结尾用 stroke_out 秒收回去（0 = 不动画）
    "stroke_in": 0.0,
    "stroke_out": 0.0,
    "stroke_start": "top_left",   # 从哪儿起笔（STROKE_STARTS）
    "stroke_dir": "cw",           # cw 顺时针 / ccw 逆时针 / both 向两边同时
    "fill_follow": False,         # 填充等描边快画完时再淡入（收回时先淡出）
}
STROKE_STARTS = (("top_left", "左上角"), ("top", "上边中间"), ("top_right", "右上角"), ("right", "右边中间"),
                 ("bottom_right", "右下角"), ("bottom", "下边中间"), ("bottom_left", "左下角"),
                 ("left", "左边中间"))
STROKE_DIRS = (("cw", "顺时针"), ("ccw", "逆时针"), ("both", "向两边同时"))
EFFECT_DEFAULTS = {
    "on": False,
    "kind": "lens_blur",
    "amount": 40.0,          # 0–100（模糊程度 / 马赛克块大小）
    "highlights": 0.35,      # 镜头模糊的亮点
    "mix": 1.0,              # 效果强度 0–1
    "grade": {"exposure": 0.0, "contrast": 0.0, "saturation": 0.0, "temperature": 0.0},
}
FEATHER_DEFAULT = 0.0
# Q175 画面变换：把它**下面已经合成好的画面**在范围里平移 / 缩放 / 旋转（以范围中心为轴，可打关键帧）。
# 范围 = 整个画面时就是「全局调整图层」：运镜推拉、晃动、整体旋转；遮罩可以再收窄。
XFORM_DEFAULTS = {"on": False, "x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0, "edge": "black"}
XFORM_EDGES = (("black", "露黑边"), ("mirror", "镜像补边"), ("clamp", "延伸边缘"))
RANGES = (("shape", "这块形状"), ("full", "整个画面"))


def is_adjust_track(tr) -> bool:
    return getattr(tr, "type", None) == ADJUST


def is_adjust(ov) -> bool:
    return isinstance(ov, dict) and ov.get("type") == ADJUST


def adjust_defaults(canvas_w: int, canvas_h: int, *, start: float, end: float, track_id=None) -> dict:
    """新建一段：画面中间 60% 宽、40% 高的纯色矩形。"""
    w, h = float(canvas_w) * 0.6, float(canvas_h) * 0.4
    ov = {
        "type": ADJUST,
        "start": float(start), "end": float(end),
        "x": (float(canvas_w) - w) / 2.0, "y": (float(canvas_h) - h) / 2.0, "w": w, "h": h,
        "rotation": 0.0, "scale": 1.0, "opacity": 255,
        "lock_aspect": False,
        "solid": dict(SOLID_DEFAULTS),
        "effect": copy.deepcopy(EFFECT_DEFAULTS),
        "feather": FEATHER_DEFAULT,
        "name": "调整",
    }
    if track_id is not None:
        ov["_track_id"] = track_id
    return ov


def range_of(ov) -> str:
    r = str((ov or {}).get("range") or "shape") if isinstance(ov, dict) else "shape"
    return r if r in dict(RANGES) else "shape"


def xform_of(ov) -> dict:
    out = dict(XFORM_DEFAULTS)
    raw = ov.get("xform") if isinstance(ov, dict) else None
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in XFORM_DEFAULTS and v is not None})
    out["on"] = bool(out["on"])
    if out["edge"] not in dict(XFORM_EDGES):
        out["edge"] = "black"
    for k, lo, hi, d in (("x", -1e5, 1e5, 0.0), ("y", -1e5, 1e5, 0.0), ("scale", 0.05, 20.0, 1.0),
                         ("rotation", -36000.0, 36000.0, 0.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = d
    return out


def xform_active(xf: dict) -> bool:
    return bool(xf["on"]) and (abs(xf["x"]) > 1e-3 or abs(xf["y"]) > 1e-3 or abs(xf["scale"] - 1.0) > 1e-5
                               or abs(xf["rotation"]) > 1e-4)


def xform_matrix(xf: dict, cx: float, cy: float) -> np.ndarray:
    """源 → 目标：绕 (cx, cy) 缩放、旋转，再平移 (x, y)。"""
    a = math.radians(xf["rotation"])
    s = xf["scale"]
    c, n = math.cos(a) * s, math.sin(a) * s
    return np.array([[c, -n, cx + xf["x"] - c * cx + n * cy],
                     [n, c, cy + xf["y"] - n * cx - c * cy]], np.float64)


def match_timeline_duration(ov, task) -> bool:
    """Q176「匹配时间线时长」：这条调整条从 0 铺到时间线结尾（最远的片段 / 渲染终点，和播放头能走到的
    一样；不算调整条自己）。关键帧按绝对时间保留（开头往前挪了多少，关键帧的本地时间就往后补多少）。"""
    from cutora.preview.engine import playback_end_sec
    if not isinstance(ov, dict):
        return False
    end = float(playback_end_sec(task) or 0.0)
    if end <= 0.0:
        return False
    try:
        old_s = float(ov.get("start", 0.0) or 0.0)
        old_e = float(ov.get("end", 0.0) or 0.0)
    except (TypeError, ValueError):
        old_s, old_e = 0.0, 0.0
    if abs(old_s) < 1e-6 and abs(old_e - end) < 1e-6:
        return False
    kfs = ov.get("keyframes")
    if isinstance(kfs, dict) and abs(old_s) > 1e-9:
        for pts in kfs.values():
            for pt in pts if isinstance(pts, list) else []:
                if isinstance(pt, dict) and isinstance(pt.get("t"), (int, float)):
                    pt["t"] = round(float(pt["t"]) + old_s, 6)
    ov["start"] = 0.0
    ov["end"] = end
    return True


def solid_of(ov) -> dict:
    out = dict(SOLID_DEFAULTS)
    raw = ov.get("solid") if isinstance(ov, dict) else None
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in SOLID_DEFAULTS and v is not None})
    out["on"] = bool(out["on"])
    out["border_on"] = bool(out["border_on"])
    out["fill_follow"] = bool(out["fill_follow"])
    if out["stroke_start"] not in dict(STROKE_STARTS):
        out["stroke_start"] = "top_left"
    if out["stroke_dir"] not in dict(STROKE_DIRS):
        out["stroke_dir"] = "cw"
    for k, lo, hi in (("opacity", 0.0, 1.0), ("radius", 0.0, 0.5), ("border_width", 0.0, 500.0),
                      ("stroke_in", 0.0, 60.0), ("stroke_out", 0.0, 60.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = SOLID_DEFAULTS[k]
    return out


def _amount_key(kind: str) -> str:
    return "amount_" + str(kind)


def effect_of(ov) -> dict:
    """效果参数（补齐默认、钳住范围）。

    Q179：``kinds`` = 同时开着的几种效果（按 EFFECT_CHAIN 排好）；``amount_<种类>`` = 各自的强度。
    老数据只有单选的 ``kind`` / ``amount``：kinds = [kind]，各种强度都退回 ``amount``（关键帧照样驱动）。"""
    out = copy.deepcopy(EFFECT_DEFAULTS)
    raw = ov.get("effect") if isinstance(ov, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    for k, v in raw.items():
        if k == "grade" and isinstance(v, dict):
            out["grade"].update({gk: gv for gk, gv in v.items() if gk in out["grade"]})
        elif k in EFFECT_DEFAULTS and v is not None:
            out[k] = v
    out["on"] = bool(out["on"])
    if out["kind"] not in dict(EFFECT_KINDS):
        out["kind"] = "lens_blur"
    for k, lo, hi in (("amount", 0.0, 100.0), ("highlights", 0.0, 1.0), ("mix", 0.0, 1.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = EFFECT_DEFAULTS[k]
    kinds = raw.get("kinds")
    if isinstance(kinds, (list, tuple)):
        want = {str(k) for k in kinds}
        out["kinds"] = [k for k in EFFECT_CHAIN if k in want]
    else:
        out["kinds"] = [out["kind"]]
    for k in AMOUNT_KINDS:
        v = raw.get(_amount_key(k))
        try:
            out[_amount_key(k)] = max(0.0, min(100.0, float(v if v is not None else out["amount"])))
        except (TypeError, ValueError):
            out[_amount_key(k)] = out["amount"]
    return out


def effect_active(eff: dict) -> bool:
    return bool(eff.get("on")) and bool(eff.get("kinds")) and float(eff.get("mix", 1.0)) > 0


def effect_label(eff: dict) -> str:
    """「镜头模糊+调色」这样的短名（时间线 / 图层面板标签用）。"""
    names = dict(EFFECT_KINDS)
    return "+".join(names[k] for k in eff.get("kinds") or [] if k in names)


def migrate_effect(ov) -> bool:
    """把老的单选效果原地改成多选的存法（kinds + amount_<种类>，effect.amount 的关键帧跟着改名）。
    画出来一模一样；面板 / 监视器要改效果参数前调一次。改了返回 True。"""
    if not is_adjust(ov):
        return False
    raw = ov.get("effect")
    if isinstance(raw, dict) and isinstance(raw.get("kinds"), (list, tuple)):
        return False
    eff = effect_of(ov)
    k = eff["kinds"][0]
    new = dict(raw) if isinstance(raw, dict) else {}
    new["kinds"] = [k]
    for ak in AMOUNT_KINDS:
        new[_amount_key(ak)] = eff[_amount_key(ak)]
    ov["effect"] = new
    kfs = ov.get("keyframes")
    if isinstance(kfs, dict) and kfs.get("effect.amount") and k in AMOUNT_KINDS:
        path = "effect." + _amount_key(k)
        if not kfs.get(path):
            kfs[path] = kfs.pop("effect.amount")
    return True


def set_effect_kind(ov, kind: str, on: bool) -> bool:
    """勾 / 取消一种效果（勾上时顺手把「效果」总开关打开）。"""
    if kind not in dict(EFFECT_KINDS) or not is_adjust(ov):
        return False
    migrate_effect(ov)
    eff = dict(ov.get("effect") or {})
    kinds = [k for k in eff.get("kinds") or [] if k != kind]
    if on:
        kinds.append(kind)
        eff["on"] = True
    eff["kinds"] = [k for k in EFFECT_CHAIN if k in kinds]
    if eff["kinds"]:
        eff["kind"] = eff["kinds"][0]
    ov["effect"] = eff
    return True


# ───────────────────────────── 哪些段、什么形状 ─────────────────────────────

def items_on_track(task, track) -> list:
    """[(在 image_overlays 里的下标, 覆层)]。"""
    tid = getattr(track, "id", None)
    out = []
    for i, ov in enumerate((task or {}).get("image_overlays") or []):
        if is_adjust(ov) and ov.get("_track_id") == tid:
            out.append((i, ov))
    return out


def active_items(task, track, t: float) -> list:
    """这一刻在画面上的段（关键帧 / 跟踪烘焙过的数值）：[(下标, 原覆层, 当前覆层)]。"""
    from cutora.render.keyframe import bake_host, local_time
    out = []
    for i, ov in items_on_track(task, track):
        try:
            s, e = float(ov.get("start", 0.0)), float(ov.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if not (s - 1e-6 <= float(t) < e + 1e-6) or ov.get("hidden"):
            continue
        if isinstance(task, dict) and task.get("layer_folders"):
            from cutora.batch.layer_folders import hidden_by_folder
            if hidden_by_folder(task, ov):
                continue
        try:
            cur = bake_host(ov, local_time(ov, t))
        except Exception:
            cur = ov
        p = stroke_progress(ov, t)
        if p is not None:
            cur = dict(cur)
            cur["_stroke_p"] = p
        out.append((i, ov, cur))
    return out


def _ease(p: float) -> float:
    p = max(0.0, min(1.0, float(p)))
    return 4.0 * p * p * p if p < 0.5 else 1.0 - ((-2.0 * p + 2.0) ** 3) / 2.0


def stroke_progress(ov, t: float):
    """描边生成动画在 t 时刻画到几成（0–1，缓入缓出）；没开动画 / 没开边框 → None。"""
    sol = solid_of(ov)
    if not (sol["on"] and sol["border_on"] and sol["border_width"] > 0):
        return None
    din, dout = sol["stroke_in"], sol["stroke_out"]
    if din <= 0 and dout <= 0:
        return None
    try:
        s, e = float(ov.get("start", 0.0)), float(ov.get("end", 0.0))
    except (TypeError, ValueError):
        return None
    p = 1.0
    if din > 0:
        p = min(p, (float(t) - s) / din)
    if dout > 0:
        p = min(p, (e - float(t)) / dout)
    return _ease(p)


def fill_follow_k(p: float) -> float:
    """「填充跟着描边」：描边画到六成以后填充才慢慢出来，画满时填满。"""
    return _ease((float(p) - 0.6) / 0.4)


def _perimeter(x0, y0, w, h, r):
    """圆角矩形中线（顺时针，从上边左端起）→ (总长, point_at(d))。"""
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    lt, lr = max(0.0, w - 2 * r), max(0.0, h - 2 * r)
    arc = math.pi * r / 2.0
    segs = []                         # (长度, 函数(u∈[0,1]) → 点)
    x1, y1 = x0 + w, y0 + h

    def line(ax, ay, bx, by):
        return lambda u: (ax + (bx - ax) * u, ay + (by - ay) * u)

    def corner(cx, cy, a0):
        return lambda u: (cx + r * math.cos(math.radians(a0 + 90.0 * u)),
                          cy + r * math.sin(math.radians(a0 + 90.0 * u)))
    segs.append((lt, line(x0 + r, y0, x1 - r, y0)))
    segs.append((arc, corner(x1 - r, y0 + r, -90.0)))
    segs.append((lr, line(x1, y0 + r, x1, y1 - r)))
    segs.append((arc, corner(x1 - r, y1 - r, 0.0)))
    segs.append((lt, line(x1 - r, y1, x0 + r, y1)))
    segs.append((arc, corner(x0 + r, y1 - r, 90.0)))
    segs.append((lr, line(x0, y1 - r, x0, y0 + r)))
    segs.append((arc, corner(x0 + r, y0 + r, 180.0)))
    total = sum(L for L, _f in segs)
    starts = {"top": lt / 2, "top_right": lt + arc / 2, "right": lt + arc + lr / 2,
              "bottom_right": lt + arc + lr + arc / 2, "bottom": lt + 2 * arc + lr + lt / 2,
              "bottom_left": 2 * lt + 2 * arc + lr + arc / 2, "left": 2 * lt + 3 * arc + lr + lr / 2,
              "top_left": (total - arc / 2) if arc > 0 else 0.0}

    def point_at(d):
        if total <= 0:
            return (x0, y0)
        d = d % total
        for L, f in segs:
            if L <= 0:
                continue
            if d <= L:
                return f(d / L)
            d -= L
        return segs[0][1](0.0)
    return total, starts, point_at


def stroke_paths(bw_f, bh_f, radius_px, width, p, start="top_left", direction="cw"):
    """画到 p 成的描边（框坐标）：沿中线取一段折线 → QPainterPathStroker 描成宽 width 的形状。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QPainterPath, QPainterPathStroker
    half = width / 2.0
    total, starts, point_at = _perimeter(half, half, bw_f - width, bh_f - width, max(0.0, radius_px - half))
    if total <= 0 or p <= 0:
        return []
    d0 = starts.get(start, 0.0)
    length = total * min(1.0, p)
    runs = [(d0, length)] if direction == "cw" else [(d0, -length)] if direction == "ccw" else \
        [(d0, length / 2.0), (d0, -length / 2.0)]
    st = QPainterPathStroker()
    st.setWidth(width)
    st.setCapStyle(Qt.PenCapStyle.FlatCap)
    st.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    out = []
    step = max(1.0, min(4.0, total / 600.0))
    for a, span in runs:
        n = max(2, int(abs(span) / step) + 1)
        poly = QPainterPath()
        for k in range(n + 1):
            x, y = point_at(a + span * k / n)
            if k == 0:
                poly.moveTo(QPointF(x, y))
            else:
                poly.lineTo(QPointF(x, y))
        out.append(st.createStroke(poly))
    return out


def _box(ov):
    x, y = float(ov.get("x", 0) or 0), float(ov.get("y", 0) or 0)
    w, h = max(1.0, float(ov.get("w", 1) or 1)), max(1.0, float(ov.get("h", 1) or 1))
    rot = float(ov.get("rotation", 0) or 0)
    sc = float(ov.get("scale", 1.0) or 1.0)
    return x, y, w, h, rot, sc


def box_matrix(ov, bw: float, bh: float) -> np.ndarray:
    """框坐标（bw×bh 像素）→ 画布：先绕中心缩放，再旋转，再放到 (x, y, w, h) 的中心。和 mask_canvas_image 一样。"""
    x, y, w, h, rot, sc = _box(ov)
    kx, ky = w / float(bw), h / float(bh)
    cx, cy = x + w / 2.0, y + h / 2.0
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    m = np.array([[c * sc * kx, -s * sc * ky, 0.0], [s * sc * kx, c * sc * ky, 0.0]], dtype=np.float64)
    m[0, 2] = cx - (m[0, 0] * bw / 2.0 + m[0, 1] * bh / 2.0)
    m[1, 2] = cy - (m[1, 0] * bw / 2.0 + m[1, 1] * bh / 2.0)
    return m.astype(np.float32)


def canvas_corners(ov):
    """形状四个角在画布上的位置（选中框 / 点选用）。"""
    x, y, w, h, _rot, _sc = _box(ov)
    m = box_matrix(ov, int(round(w)), int(round(h)))
    bw, bh = int(round(w)), int(round(h))
    pts = []
    for px, py in ((0, 0), (bw, 0), (bw, bh), (0, bh)):
        pts.append((float(m[0, 0] * px + m[0, 1] * py + m[0, 2]), float(m[1, 0] * px + m[1, 1] * py + m[1, 2])))
    return pts


def _qt_affine(m):
    from PySide6.QtGui import QTransform
    return QTransform(float(m[0, 0]), float(m[1, 0]), float(m[0, 1]), float(m[1, 1]),
                      float(m[0, 2]), float(m[1, 2]))


def _blur_u8(a: np.ndarray, sigma: float) -> np.ndarray:
    """大半径高斯模糊：先缩小再模糊再放大（σ > 3 时），和原尺寸直接模糊看不出差别，快一个数量级。"""
    import cv2
    if sigma < 0.3:
        return a
    h, w = a.shape[:2]
    f = 1.0
    if sigma > 3.0:
        f = min(sigma / 2.0, max(1.0, min(w, h) / 4.0))
    if f > 1.01:
        sw, sh = max(2, int(round(w / f))), max(2, int(round(h / f)))
        small = cv2.resize(a, (sw, sh), interpolation=cv2.INTER_AREA)
        s2 = sigma / f
        k = int(s2 * 3) * 2 + 1
        small = cv2.GaussianBlur(small, (k, k), s2, borderType=cv2.BORDER_REFLECT)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    k = int(sigma * 3) * 2 + 1
    return cv2.GaussianBlur(a, (k, k), sigma, borderType=cv2.BORDER_REFLECT)


_MASK_CACHE: "dict" = {}
_MASK_ORDER: list = []
_MASK_CAP = 24
_MASK_LOCK = threading.Lock()


def _mask_key(ov, w, h):
    import json
    sol = solid_of(ov)
    try:
        mk = json.dumps(ov.get("masks") or [], sort_keys=True, default=str)
    except Exception:
        mk = repr(ov.get("masks"))
    x, y, bw, bh, rot, sc = _box(ov)
    return (int(w), int(h), round(x, 2), round(y, 2), round(bw, 2), round(bh, 2), round(rot, 3), round(sc, 4),
            round(sol["radius"], 4), bool(sol["on"] and sol["border_on"]), round(sol["border_width"], 2),
            round(float(ov.get("feather", 0.0) or 0.0), 2), round(float(ov.get("_px_scale", 1.0) or 1.0), 4),
            round(float(ov.get("_stroke_p", 1.0)), 4), sol["stroke_start"], sol["stroke_dir"], mk)


def item_masks(ov, w: int, h: int):
    """这一段在 w×h 画布上的形状：(x0, y0, 形状 uint8, 边框 uint8 或 None)，只覆盖外接框那一块；画不出来 → None。

    圆角矩形用 Qt 光栅化（抗锯齿，直接带旋转 / 缩放画到画布坐标），羽化在外接框里做；遮罩面板画的遮罩
    在框坐标里算好再贴过去。同样的几何（不动的调整图层）有缓存，一帧只算一次。"""
    key = _mask_key(ov, w, h)
    with _MASK_LOCK:
        hit = _MASK_CACHE.get(key)
    if hit is not None:
        return hit
    res = _build_masks(ov, w, h)
    with _MASK_LOCK:                     # 播放时工作线程、停下来时界面线程都会进来
        if key not in _MASK_CACHE:
            _MASK_CACHE[key] = res
            _MASK_ORDER.append(key)
        while len(_MASK_ORDER) > _MASK_CAP:
            _MASK_CACHE.pop(_MASK_ORDER.pop(0), None)
    return res


def _build_masks(ov, w: int, h: int):
    import cv2
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
    _x, _y, bw_f, bh_f, _r, _s = _box(ov)
    sol = solid_of(ov)
    fe = max(0.0, float(ov.get("feather", 0.0) or 0.0))
    m = box_matrix(ov, bw_f, bh_f)
    pts = [(m[0, 0] * px + m[0, 1] * py + m[0, 2], m[1, 0] * px + m[1, 1] * py + m[1, 2])
           for px, py in ((0, 0), (bw_f, 0), (bw_f, bh_f), (0, bh_f))]
    pad = int(math.ceil(fe * 1.5)) + 2
    x0 = max(0, int(math.floor(min(p[0] for p in pts))) - pad)
    y0 = max(0, int(math.floor(min(p[1] for p in pts))) - pad)
    x1 = min(int(w), int(math.ceil(max(p[0] for p in pts))) + pad)
    y1 = min(int(h), int(math.ceil(max(p[1] for p in pts))) + pad)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    cw, ch = x1 - x0, y1 - y0
    r = max(0.0, float(sol["radius"])) * min(bw_f, bh_f)
    r = min(r, bw_f / 2.0, bh_f / 2.0)
    tf = _qt_affine(m)

    def _paint(paths, fill_rule=None):
        img = QImage(cw, ch, QImage.Format.Format_Alpha8)
        img.fill(0)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.translate(-x0, -y0)
            p.setTransform(tf, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 255))
            for path in paths:
                if fill_rule is not None:
                    path.setFillRule(fill_rule)
                p.drawPath(path)
        finally:
            p.end()
        buf = np.frombuffer(img.constBits(), np.uint8).reshape(ch, img.bytesPerLine())[:, :cw]
        return np.array(buf)

    def _rr(inset):
        path = QPainterPath()
        rr = max(0.0, r - inset)
        rect = QRectF(inset, inset, bw_f - 2 * inset, bh_f - 2 * inset)
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        if rr > 0.01:
            path.addRoundedRect(rect, rr, rr)
        else:
            path.addRect(rect)
        return path

    outer = _rr(0.0)
    if outer is None:
        return None
    shape = _paint([outer])
    border = None
    bwid = float(sol["border_width"])
    sp = float(ov.get("_stroke_p", 1.0))
    if sol["on"] and sol["border_on"] and bwid > 0 and sp < 0.9999:
        # Q175 描边生成动画：只画出一段
        paths = stroke_paths(bw_f, bh_f, r, min(bwid, min(bw_f, bh_f) / 2.0), sp,
                             sol["stroke_start"], sol["stroke_dir"])
        border = _paint(paths, Qt.FillRule.WindingFill) if paths else np.zeros((ch, cw), np.uint8)
    elif sol["on"] and sol["border_on"] and bwid > 0:
        ring = QPainterPath(outer)
        inner = _rr(bwid)
        if inner is not None:
            ring.addPath(inner)
        border = _paint([ring], Qt.FillRule.OddEvenFill)
    if fe >= 0.5:
        shape = _blur_u8(shape, fe / 2.0)
    from cutora.fx import masks as M
    ms = M.masks_of(ov)
    if ms:
        bw_i, bh_i = max(1, int(round(bw_f))), max(1, int(round(bh_f)))
        k = min(1.0, 1600.0 / float(max(bw_i, bh_i)))
        mw, mh = max(1, int(bw_i * k)), max(1, int(bh_i * k))
        user = M.combine(ms, mw, mh, k * float(ov.get("_px_scale", 1.0) or 1.0))
        if user is not None:
            um = box_matrix(ov, mw, mh).astype(np.float64)
            um[0, 2] -= x0
            um[1, 2] -= y0
            u = cv2.warpAffine((np.clip(user, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), um.astype(np.float32),
                               (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            shape = cv2.multiply(shape, u, scale=1.0 / 255.0)
            if border is not None:
                border = cv2.multiply(border, u, scale=1.0 / 255.0)
    if not shape.any() and (border is None or not border.any()):
        return None
    return x0, y0, shape, border


# ───────────────────────────── 效果 ─────────────────────────────

def _qimage_bgra(img):
    """ARGB32_Premultiplied QImage → (h, w, 4) uint8 视图（可写，和 img 共用内存）。"""
    buf = np.frombuffer(img.bits(), dtype=np.uint8)
    return buf.reshape((img.height(), img.bytesPerLine()))[:, :img.width() * 4].reshape(img.height(), img.width(), 4)


def effect_radius_px(eff: dict, long_side: float) -> float:
    """效果会往外「借」多远的像素（按整幅画面长边，和分辨率无关）。多种效果串着做：半径相加。"""
    kinds = eff.get("kinds")
    if isinstance(kinds, (list, tuple)):
        tot = 0.0
        for k in kinds:
            amt = eff.get(_amount_key(k), eff.get("amount", 0.0))
            tot += _kind_radius_px(k, amt, long_side)
        return tot
    return _kind_radius_px(eff.get("kind"), eff.get("amount", 0.0), long_side)


def _kind_radius_px(kind, amount, long_side: float) -> float:
    try:
        a = max(0.0, min(100.0, float(amount or 0.0))) / 100.0
    except (TypeError, ValueError):
        a = 0.0
    if kind == "lens_blur":
        from cutora.render.fill_bg import lens_radius_px
        return lens_radius_px(a * 100.0, long_side)
    if kind == "blur":
        return 3.0 * blur_sigma_px(a * 100.0, long_side)
    if kind == "mosaic":
        return mosaic_cell_px(a * 100.0, long_side)
    return 0.0


def blur_sigma_px(amount: float, long_side: float) -> float:
    """高斯模糊：强度 100 → σ = 长边 2.5 %（1920 上 48 px），幂曲线，前段细。"""
    a = max(0.0, min(100.0, float(amount or 0.0))) / 100.0
    return float(long_side) * 0.025 * (a ** 1.4)


def mosaic_cell_px(amount: float, long_side: float) -> float:
    """马赛克格子边长：强度 0 → 长边 0.4 %，100 → 6 %。"""
    a = max(0.0, min(100.0, float(amount or 0.0))) / 100.0
    return float(long_side) * (0.004 + 0.056 * a)


def _effect_crop(crop_img, eff: dict, long_side: float):
    """对一块 QImage 做效果，返回同尺寸的 (h, w, 4) BGRA 预乘 uint8。long_side = 整幅画面（这张图）的长边像素，
    强度都按它换算 —— 预览缩小 / 导出原尺寸看起来一样。"""
    from PySide6.QtGui import QImage
    import cv2
    w, h = crop_img.width(), crop_img.height()
    kind = eff["kind"]
    amt = float(eff["amount"])
    if kind == "lens_blur":
        # Q180：全程数组（不再 QImage ↔ 数组来回拷贝、不再用 Qt 放大）；放大用 cv2 双线性，多线程、不占 GIL
        from cutora.render.fill_bg import lens_blur
        small = lens_blur(crop_img, amt, float(eff.get("highlights", 0.35)), radius_long_side=long_side,
                          as_array=True)
        if small.shape[1] != w or small.shape[0] != h:
            small = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.ascontiguousarray(small)
    src = _qimage_bgra(crop_img).copy()
    if kind == "blur":
        return _blur_u8(src, blur_sigma_px(amt, long_side))
    if kind == "mosaic":
        cell = max(2, int(round(mosaic_cell_px(amt, long_side))))
        sw, sh = max(1, w // cell), max(1, h // cell)
        tiny = cv2.resize(src, (sw, sh), interpolation=cv2.INTER_AREA)
        return cv2.resize(tiny, (w, h), interpolation=cv2.INTER_NEAREST)
    if kind == "grade":
        from cutora.render.grade import apply_grade_bgr_u8, is_identity, normalize_grade
        g = normalize_grade(dict(eff.get("grade") or {}))
        if is_identity(g):
            return src
        if int(src[..., 3].min()) >= 255:
            # Q180：不透明（调整图层下面已经合成好的画面都是）→ 直接在数组上调，省掉 4 次整块拷贝
            out = apply_grade_bgr_u8(cv2.cvtColor(src, cv2.COLOR_BGRA2BGR), [g])
            return cv2.cvtColor(out, cv2.COLOR_BGR2BGRA)
        from cutora.render.grade import apply_grade_to_qimage
        out = apply_grade_to_qimage(crop_img, [g])
        out = out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return _qimage_bgra(out).copy()
    return src


def _bgra_qimage(arr):
    """(h, w, 4) BGRA 预乘 uint8 → 独立的 QImage（ARGB32_Premultiplied）。"""
    from PySide6.QtGui import QImage
    a = np.ascontiguousarray(arr)
    h, w = a.shape[:2]
    return QImage(a.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def _effect_chain(crop_img, eff: dict, long_side: float):
    """Q179：开着的几种效果按 EFFECT_CHAIN 串起来做（上一种的结果喂给下一种）。返回 BGRA 预乘 uint8。"""
    out = None
    cur = crop_img
    for k in eff.get("kinds") or []:
        if out is not None:
            cur = _bgra_qimage(out)
        one = dict(eff, kind=k, amount=float(eff.get(_amount_key(k), eff.get("amount", 0.0))))
        out = _effect_crop(cur, one, long_side)
    if out is None:
        out = _qimage_bgra(crop_img).copy()
    return out


def _hex_bgra(c: str):
    from PySide6.QtGui import QColor
    q = QColor(str(c or "#FFFFFF"))
    if not q.isValid():
        q = QColor("#FFFFFF")
    return np.array([q.blue(), q.green(), q.red(), 255], np.uint8)


def _scaled(ov, scale: float):
    """画布逻辑像素 → 输出像素（预览缩小时）：框、羽化、边框宽度一起缩。"""
    if abs(scale - 1.0) <= 1e-6:
        return ov
    o = dict(ov)
    for k in ("x", "y", "w", "h"):
        o[k] = float(o.get(k, 0) or 0) * scale
    o["feather"] = float(o.get("feather", 0) or 0) * scale
    sol = dict(o.get("solid") or {})
    if "border_width" in sol:
        sol["border_width"] = float(sol["border_width"]) * scale
    o["solid"] = sol
    xf = o.get("xform")
    if isinstance(xf, dict):
        xf = dict(xf)
        for k in ("x", "y"):
            if isinstance(xf.get(k), (int, float)):
                xf[k] = float(xf[k]) * scale
        o["xform"] = xf
    o["_px_scale"] = float(scale)
    return o


def _blend_into(arr, x0, y0, mask_u8, src, alpha: float) -> None:
    """arr[y0:, x0:] ← arr × (1 − m) + src × m，m = mask × alpha。src 是同尺寸的 BGRA uint8 或 4 元颜色。"""
    import cv2
    h, w = mask_u8.shape[:2]
    region = arr[y0:y0 + h, x0:x0 + w]
    wt = mask_u8.astype(np.float32) * (float(alpha) / 255.0)
    if not isinstance(src, np.ndarray) or src.ndim == 1:
        col = np.asarray(src, np.uint8)
        src = np.empty_like(region)
        src[:] = col
    region[:] = cv2.blendLinear(src, np.ascontiguousarray(region), wt, 1.0 - wt)


_GPU_AUTO = object()


def _group_cell_mask(task, ov, w: int, h: int):
    """若该调整图层与部分视频轨道打成了预合成组（group），且在它下面还有不属于该组的其它分屏视频轨，
    返回组内视频轨所属分屏格子的合并掩码 (h, w) uint8，使调整图层仅作用于组内视频素材。"""
    if not isinstance(task, dict) or not isinstance(ov, dict):
        return None
    from cutora.model import clip_groups as CG
    gid = CG.gid_of(ov)
    if not gid:
        return None
    tl = task.get("timeline")
    if tl is None:
        return None
    tl._task = task
    from cutora.render.split_screen import get_cache, normalize_split
    sp = normalize_split(task.get("split") or task.get("split_screen"))
    if not sp.get("enabled"):
        return None
    from cutora.render.visual_stack import _group_ordered_tracks, _tracks
    ordered = _group_ordered_tracks(task, _tracks(task))
    member_tracks = {id(tr) for tr, _c in CG.members(tl, gid)
                     if tr is not None and getattr(tr, "type", None) == "video"}
    if not member_tracks:
        return None
    adj_tid = ov.get("_track_id")
    below_non_member = False
    member_cids = set()
    for tr in ordered:
        if adj_tid is not None and (getattr(tr, "id", None) == adj_tid or str(getattr(tr, "id", "")) == str(adj_tid)):
            break
        if getattr(tr, "type", None) == "video" and (getattr(tr, "clips", None) or []):
            cid = getattr(tr, "split_cell", None)
            if id(tr) in member_tracks:
                if cid:
                    member_cids.add(str(cid))
            else:
                below_non_member = True
    if not below_non_member or not member_cids:
        return None
    cache = get_cache(sp, w, h)
    union = np.zeros((h, w), dtype=np.uint8)
    for cid in member_cids:
        m = cache.mask_u8.get(cid)
        if m is not None and m.shape == (h, w):
            np.maximum(union, m, out=union)
    return union if union.any() else None


def apply_item(img, ov, *, scale: float = 1.0, task=None, _gpu=_GPU_AUTO) -> None:
    """把一段调整图层合到 img（ARGB32_Premultiplied，就地改）上。scale = 画布逻辑像素 → img 像素的倍数。
    _gpu：Q181 显卡后端（默认自动选；None = 强制 CPU；测试 / 自检用）。"""
    from PySide6.QtCore import QRect
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return
    o = _scaled(ov, scale)
    op = max(0.0, min(1.0, float(o.get("opacity", 255) or 0) / 255.0))
    if op <= 0:
        return
    if range_of(o) == "full":
        # 整个画面：框就是画布（遮罩也按整幅画面算）
        o = dict(o, x=0.0, y=0.0, w=float(w), h=float(h), rotation=0.0, scale=1.0)
    eff = effect_of(o)
    sol = solid_of(o)
    xf = xform_of(o)
    do_xf = xform_active(xf)
    do_fx = effect_active(eff)
    do_fill = sol["on"] and sol["opacity"] > 0
    do_border = sol["on"] and sol["border_on"] and sol["border_width"] > 0
    if not (do_xf or do_fx or do_fill or do_border):
        return
    got = item_masks(o, w, h)
    if got is None:
        return
    x0, y0, shape, border = got
    sh, sw = shape.shape[:2]
    gmask = _group_cell_mask(task, ov, w, h)
    if gmask is not None:
        import cv2
        sub_gm = gmask[y0:y0 + sh, x0:x0 + sw]
        shape = cv2.multiply(shape, sub_gm, scale=1.0 / 255.0)
        if border is not None:
            border = cv2.multiply(border, sub_gm, scale=1.0 / 255.0)
        if not shape.any() and (border is None or not border.any()):
            return
    arr = _qimage_bgra(img)
    if do_xf:
        # 画面变换：只算范围外接框里的像素（反向取样整幅图），cv2 多线程、不占 GIL
        import cv2
        bx, by, bw_, bh_, _r, _s = _box(o)
        m = xform_matrix(xf, bx + bw_ / 2.0, by + bh_ / 2.0)
        m[0, 2] -= x0
        m[1, 2] -= y0
        border_mode = {"mirror": cv2.BORDER_REFLECT_101, "clamp": cv2.BORDER_REPLICATE}.get(
            xf["edge"], cv2.BORDER_CONSTANT)
        moved = cv2.warpAffine(arr, m, (sw, sh), flags=cv2.INTER_LINEAR, borderMode=border_mode,
                               borderValue=(0, 0, 0, 255))
        _blend_into(arr, x0, y0, shape, moved, op)
    k_fill = 1.0
    if do_fill and sol["fill_follow"] and "_stroke_p" in o:
        k_fill = fill_follow_k(o["_stroke_p"])
    long_side = float(max(w, h))
    pad = int(min(max(w, h), 4 + effect_radius_px(eff, long_side) * 1.5)) if do_fx else 0
    ex0, ey0 = max(0, x0 - pad), max(0, y0 - pad)
    ex1, ey1 = min(w, x0 + sw + pad), min(h, y0 + sh + pad)
    # Q181：效果链 + 区域混合 + 填充 + 边框一次在显卡上做（一上一下各一块外接框）
    be = _gpu
    if be is _GPU_AUTO:
        from cutora.fx import gpu as _gpu_mod
        be = _gpu_mod.backend()
    if be is not None and (do_fx or (do_fill and k_fill > 0) or (do_border and border is not None)):
        try:
            crop = arr[ey0:ey1, ex0:ex1]
            fill = (_hex_bgra(sol["color"]), sol["opacity"] * op * k_fill) if (do_fill and k_fill > 0) else None
            bord = (border, _hex_bgra(sol["border_color"]), op) if (do_border and border is not None) else None
            res = be.adjust_region(crop, eff if do_fx else {"kinds": []}, long_side, ox=x0 - ex0, oy=y0 - ey0,
                                   shape_u8=shape, mix=(float(eff["mix"]) * op) if do_fx else 0.0,
                                   fill=fill, border=bord)
            arr[y0:y0 + sh, x0:x0 + sw] = res
            return
        except Exception as e:
            from cutora.fx import gpu as _gpu_mod
            _gpu_mod.disable("调整图层：%s: %s" % (type(e).__name__, e))
    if do_fx:
        crop = img.copy(QRect(ex0, ey0, ex1 - ex0, ey1 - ey0))
        fx = _effect_chain(crop, eff, long_side)
        sub = np.ascontiguousarray(fx[y0 - ey0:y0 - ey0 + sh, x0 - ex0:x0 - ex0 + sw])
        _blend_into(arr, x0, y0, shape, sub, float(eff["mix"]) * op)
    if do_fill and k_fill > 0:
        _blend_into(arr, x0, y0, shape, _hex_bgra(sol["color"]), sol["opacity"] * op * k_fill)
    if do_border and border is not None:
        _blend_into(arr, x0, y0, border, _hex_bgra(sol["border_color"]), op)


def track_blend(track):
    """Q178 调整轨自己的混合模式 / 不透明度：轨道「合成」里设的优先，其次是轨道头右键的混合模式。"""
    mode, op = "normal", 1.0
    try:
        from cutora.render.track_transform import track_props_of
        props = track_props_of(track)
        mode = str(props.get("blend_mode") or "normal")
        op = float(props.get("opacity", 1.0))
    except Exception:
        pass
    if mode == "normal":
        mode = str(getattr(track, "blend_mode", "normal") or "normal")
    return mode, max(0.0, min(1.0, op))


def apply_track(img, task, track, t: float, *, scale: float = 1.0) -> int:
    """这一条调整轨在 t 时刻的全部段，按时间线顺序（先到先画）合到 img 上。返回画了几段。

    Q178：调整轨设了混合模式 / 不透明度 → 先在一张副本上算出调整结果，再按这个模式（和不透明度）
    叠回它下面的画面上（= 调整图层自己的结果和下面一起算完，上面的轨道再作用在这个结果上）。"""
    mode, op = track_blend(track)
    if mode == "normal" and op >= 0.999:
        return _apply_track_plain(img, task, track, t, scale=scale)
    items = active_items(task, track, t)
    if not items:
        return 0
    from PySide6.QtGui import QPainter
    from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL
    w, h = img.width(), img.height()
    work = img.copy()
    n = _apply_track_plain(work, task, track, t, scale=scale)
    if not n:
        return 0
    blended = img.copy()
    p = QPainter(blended)
    try:
        p.setCompositionMode(BLEND_MODES.get(mode, _BLEND_NORMAL))
        p.setOpacity(op)
        p.drawImage(0, 0, work)
    finally:
        p.end()
    # 只在调整图层的范围里换成混合结果（范围外 work 和原图一样，但「滤色自己」会变亮 —— 不能整幅换）
    region = np.zeros((h, w), np.uint8)
    for _i, _ov, cur in items:
        o = _scaled(cur, scale)
        if range_of(o) == "full":
            o = dict(o, x=0.0, y=0.0, w=float(w), h=float(h), rotation=0.0, scale=1.0)
        got = item_masks(o, w, h)
        if got is None:
            continue
        x0, y0, shape, border = got
        sh, sw = shape.shape[:2]
        gmask = _group_cell_mask(task, cur, w, h)
        if gmask is not None:
            import cv2
            sub_gm = gmask[y0:y0 + sh, x0:x0 + sw]
            shape = cv2.multiply(shape, sub_gm, scale=1.0 / 255.0)
            if border is not None:
                border = cv2.multiply(border, sub_gm, scale=1.0 / 255.0)
        np.maximum(region[y0:y0 + sh, x0:x0 + sw], shape, out=region[y0:y0 + sh, x0:x0 + sw])
        if border is not None:
            np.maximum(region[y0:y0 + sh, x0:x0 + sw], border, out=region[y0:y0 + sh, x0:x0 + sw])
    if region.any():
        _blend_into(_qimage_bgra(img), 0, 0, region, np.ascontiguousarray(_qimage_bgra(blended)), 1.0)
    return n


def _apply_track_plain(img, task, track, t: float, *, scale: float = 1.0) -> int:
    from cutora.model.track_guard import track_outputs
    try:
        if not track_outputs(track, task=task):
            return 0
    except Exception:
        if not getattr(track, "visible", True):
            return 0
    n = 0
    for _i, _ov, cur in active_items(task, track, t):
        try:
            apply_item(img, cur, scale=scale, task=task)
            n += 1
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.render.adjust").exception("调整图层渲染失败")
    return n


def apply_on_painter(painter, task, track, t: float, w: int, h: int) -> None:
    """painter 正画在一张 QImage 上：结束 → 处理这张图 → 接着画（画笔状态复原）。"""
    from PySide6.QtGui import QImage, QPainter
    dev = painter.device()
    if not isinstance(dev, QImage):
        return
    hints = painter.renderHints()
    tf = painter.worldTransform()
    painter.end()
    try:
        if dev.format() != QImage.Format.Format_ARGB32_Premultiplied:
            conv = dev.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            sc = dev.width() / float(max(1, w))
            apply_track(conv, task, track, t, scale=sc)
            p2 = QPainter(dev)
            p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p2.drawImage(0, 0, conv)
            p2.end()
        else:
            sc = dev.width() / float(max(1, w))
            apply_track(dev, task, track, t, scale=sc)
    finally:
        painter.begin(dev)
        painter.setRenderHints(hints)
        painter.setWorldTransform(tf)


def hit_records(task, track, t: float) -> list:
    """调整图层在画布上的点选记录（画不出东西的「纯效果」段也要点得中）：
    形状框（含缩放）+ 旋转，整块都算命中（1×1 全不透明的掩码 → hitmask 按旋转做反变换判断在不在框里）。"""
    from PySide6.QtCore import QRectF
    out = []
    for i, _ov, cur in active_items(task, track, t):
        if range_of(cur) == "full":
            continue        # 整个画面的调整图层不挡点选（在时间线 / 图层面板里选它）
        x, y, w, h, rot, sc = _box(cur)
        cx, cy = x + w / 2.0, y + h / 2.0
        rect = QRectF(cx - w * sc / 2.0, cy - h * sc / 2.0, w * sc, h * sc)
        out.append({"type": "overlay", "list": "image_overlays", "idx": i, "rect": rect,
                    "alpha_mask": np.full((1, 1), 255, np.uint8), "mask_src": (int(rect.width()), int(rect.height())),
                    "rotation": rot, "flip_x": False, "flip_y": False, "adjust": True,
                    "locked": bool(cur.get("locked"))})
    return out
