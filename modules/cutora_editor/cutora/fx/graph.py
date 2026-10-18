# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-F · 每个图层一张效果节点图（fx_graph）：数据、注册表、校验、求值。没有 Qt。

图层（视频片段 / 图片覆层）上可选键 ``fx_graph``::

    {"version": 1, "uid": "g3fa9…",
     "nodes": [{"id": "n1", "type": "keyer", "params": {"keyer": {…}}, "pos": [260, 40],
                "enabled": true, "label": "", "color": ""}],
     "edges": [["src", "n1", "in"], ["n1", "out", "in"]],
     "io_pos": {"src": [40, 40], "out": [600, 40]}}

* 隐式节点 ``src``（这个图层这一帧的画面，已经裁切 / 翻转 / 调色 / 渐隐）和 ``out``（送回合成器）。
* 边 = [上游节点, 下游节点, 下游端口]。每个节点一个输出；每个输入端口最多一条边。
* 有图（哪怕只有 src → out）就**只**按图求值，图层上的 keyer / masks / ai_matte 老键不再生效
  （「转为节点」时把它们搬进节点里）。没有图 = 老流水线，字节不变。
* 节点参数的形状和图层上的老键一样（keyer 节点 params["keyer"]，遮罩节点 params["masks"]，
  AI 抠像节点 params["ai_matte"]），所以检查器的遮罩 / 抠像面板和画布工具直接拿节点 params 当「宿主」用。

数据类型：image = (rgb uint8 (h, w, 3), alpha float32 (h, w) 0–1 或 None=不透明)；matte = float32 (h, w)。
image 接到 matte 端口时自动取它的 alpha（Fusion 习惯）。
"""
from __future__ import annotations

import contextlib
import copy
import threading
import uuid

import numpy as np

from cutora.fx import ai_matte as A
from cutora.fx import keyer as K
from cutora.fx import masks as M

IMAGE, MATTE = "image", "matte"
SRC, OUT = "src", "out"

# 节点注册表：输入端口 [(名字, 类型, 必填, 中文)]、输出类型、默认参数、中文名、分类、说明
NODE_TYPES = {
    "keyer": {
        "title": "蓝绿幕抠像", "category": "抠像", "out": IMAGE,
        "inputs": [("in", IMAGE, True, "画面"), ("mask", MATTE, False, "限定区域")],
        "params": lambda: {"keyer": dict(K.DEFAULTS, enabled=True)},
        "doc": "色差键：屏幕色变透明 + 溢色抑制。接了「限定区域」就只在区域里抠。",
    },
    "ai_matte": {
        "title": "AI 抠像", "category": "抠像", "out": IMAGE,
        "inputs": [("in", IMAGE, True, "画面")],
        "params": lambda: {"ai_matte": {}},
        "doc": "SAM 2.1：点一下对象，传播到整段；结果乘到画面的透明度上。",
    },
    "mask": {
        "title": "形状遮罩", "category": "遮罩", "out": MATTE,
        "inputs": [],
        "params": lambda: {"masks": []},
        "doc": "矩形 / 椭圆 / 多边形，可多个叠加（加 / 减 / 交），羽化、扩展。输出一张遮罩。",
    },
    "matte_ops": {
        "title": "遮罩运算", "category": "遮罩", "out": MATTE,
        "inputs": [("a", MATTE, True, "遮罩 A"), ("b", MATTE, False, "遮罩 B")],
        "params": lambda: {"op": "add", "invert": False, "shrink": 0.0, "feather": 0.0},
        "doc": "两张遮罩合并 / 相减 / 相交 / 取亮 / 取暗 / 差值，再反转、收缩扩张、羽化。",
    },
    "matte_apply": {
        "title": "套用遮罩", "category": "遮罩", "out": IMAGE,
        "inputs": [("in", IMAGE, True, "画面"), ("matte", MATTE, True, "遮罩")],
        "params": lambda: {"mode": "multiply", "invert": False},
        "doc": "把遮罩用到画面的透明度上：相乘（只留遮罩里）/ 替换 / 挖掉（去掉遮罩里）。",
    },
    "blur": {
        "title": "模糊", "category": "画面", "out": IMAGE,
        "inputs": [("in", IMAGE, True, "画面"), ("mask", MATTE, False, "限定区域")],
        "params": lambda: {"size": 8.0, "alpha": True},
        "doc": "高斯模糊（画布像素）。接了「限定区域」就只糊区域里（打码、虚化背景）。",
    },
    "color": {
        "title": "调色", "category": "画面", "out": IMAGE,
        "inputs": [("in", IMAGE, True, "画面"), ("mask", MATTE, False, "限定区域")],
        "params": lambda: {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0, "gain": 1.0},
        "doc": "亮度 / 对比度 / 饱和度 / 增益。接了「限定区域」就只调区域里。",
    },
}
MATTE_OPS = ("add", "subtract", "intersect", "max", "min", "difference")
APPLY_MODES = ("multiply", "replace", "cutout")
NODE_COLORS = ("", "#F2B33D", "#4FC3F7", "#81C784", "#EF7A7A", "#CE93D8", "#FFB74D")


# ───────────────────────────── 数据 ─────────────────────────────

def graph_of(host) -> dict | None:
    g = host.get("fx_graph") if isinstance(host, dict) else getattr(host, "fx_graph", None)
    return g if isinstance(g, dict) and g.get("nodes") is not None else None


def has_graph(host) -> bool:
    return graph_of(host) is not None


def new_uid() -> str:
    return "g" + uuid.uuid4().hex[:10]


def empty_graph() -> dict:
    return {"version": 1, "uid": new_uid(), "nodes": [], "edges": [[SRC, OUT, "in"]],
            "io_pos": {SRC: [40.0, 120.0], OUT: [560.0, 120.0]}}


def node_type(t: str) -> dict | None:
    return NODE_TYPES.get(str(t))


def inputs_of(t: str) -> list:
    if t == OUT:
        return [("in", IMAGE, True, "画面")]
    nt = node_type(t)
    return list(nt["inputs"]) if nt else []


def output_of(t: str):
    if t == SRC:
        return IMAGE
    if t == OUT:
        return None
    nt = node_type(t)
    return nt["out"] if nt else None


def node_by_id(g: dict, nid: str) -> dict | None:
    for n in g.get("nodes") or []:
        if n.get("id") == nid:
            return n
    return None


def type_of(g: dict, nid: str):
    if nid in (SRC, OUT):
        return nid
    n = node_by_id(g, nid)
    return n.get("type") if n else None


def new_node_id(g: dict) -> str:
    used = {n.get("id") for n in g.get("nodes") or []}
    i = 1
    while "n%d" % i in used:
        i += 1
    return "n%d" % i


def normalize(raw) -> dict | None:
    """规范化（丢掉坏节点 / 坏边 / 环）；不是图 → None。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list):
        return None
    g = {"version": 1, "uid": str(raw.get("uid") or new_uid()), "nodes": [], "edges": [], "io_pos": {}}
    seen = set()
    for n in raw.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        nid, t = str(n.get("id") or ""), str(n.get("type") or "")
        if not nid or nid in seen or nid in (SRC, OUT) or t not in NODE_TYPES:
            continue
        seen.add(nid)
        params = n.get("params") if isinstance(n.get("params"), dict) else {}
        base = NODE_TYPES[t]["params"]()
        for k, v in params.items():
            base[k] = v
        try:
            pos = [float(n.get("pos", [0, 0])[0]), float(n.get("pos", [0, 0])[1])]
        except (TypeError, ValueError, IndexError):
            pos = [0.0, 0.0]
        g["nodes"].append({"id": nid, "type": t, "params": base, "pos": pos,
                           "enabled": bool(n.get("enabled", True)), "label": str(n.get("label") or ""),
                           "color": str(n.get("color") or "")})
    for k in (SRC, OUT):
        try:
            p = (raw.get("io_pos") or {}).get(k)
            g["io_pos"][k] = [float(p[0]), float(p[1])]
        except (TypeError, ValueError, IndexError):
            g["io_pos"][k] = [40.0, 120.0] if k == SRC else [560.0, 120.0]
    taken = set()
    for e in raw.get("edges") or []:
        try:
            a, b, port = str(e[0]), str(e[1]), str(e[2])
        except (TypeError, IndexError):
            continue
        if (b, port) in taken:
            continue
        if not can_connect(g, a, b, port):
            continue
        g["edges"].append([a, b, port])
        taken.add((b, port))
    return g


def upstream(g: dict, nid: str, port: str):
    for a, b, p in g.get("edges") or []:
        if b == nid and p == port:
            return a
    return None


def downstream(g: dict, nid: str) -> list:
    return [(b, p) for a, b, p in g.get("edges") or [] if a == nid]


def _reaches(g: dict, start: str, target: str) -> bool:
    """从 start 顺着边往下游能不能走到 target。"""
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(b for b, _p in downstream(g, cur))
    return False


def can_connect(g: dict, a: str, b: str, port: str) -> bool:
    """a 的输出能不能接到 b 的 port：类型相容（image→matte 自动取 alpha，matte→image 不行）、不成环。"""
    ta, tb = type_of(g, a), type_of(g, b)
    if ta is None or tb is None or a == b or a == OUT or b == SRC:
        return False
    out_t = output_of(ta)
    ports = {p[0]: p[1] for p in inputs_of(tb)}
    if out_t is None or port not in ports:
        return False
    if ports[port] == IMAGE and out_t != IMAGE:
        return False
    return not _reaches(g, b, a)


def signature(g: dict) -> tuple:
    import json
    return ("graph", json.dumps({"n": g.get("nodes"), "e": g.get("edges")}, sort_keys=True, default=str))


# ───────────────────────────── 编辑（纯函数，命令里用） ─────────────────────────────

def add_node(g: dict, t: str, pos=None, params=None, nid=None) -> dict:
    if t not in NODE_TYPES:
        raise ValueError("未知节点类型 %s" % t)
    n = {"id": nid or new_node_id(g), "type": t, "params": NODE_TYPES[t]["params"](),
         "pos": [float((pos or [0, 0])[0]), float((pos or [0, 0])[1])], "enabled": True, "label": "", "color": ""}
    if isinstance(params, dict):
        n["params"].update(copy.deepcopy(params))
    g["nodes"].append(n)
    return n


def connect(g: dict, a: str, b: str, port: str) -> None:
    if not can_connect(g, a, b, port):
        raise ValueError("不能这样连：类型不配或会成环")
    g["edges"] = [e for e in g["edges"] if not (e[1] == b and e[2] == port)]
    g["edges"].append([a, b, port])


def disconnect(g: dict, b: str, port: str) -> None:
    g["edges"] = [e for e in g["edges"] if not (e[1] == b and e[2] == port)]


def remove_node(g: dict, nid: str, reconnect: bool = True) -> None:
    """删节点；reconnect=True 时把它主输入的上游直接接到它下游同类型的端口上（Fusion 习惯）。"""
    n = node_by_id(g, nid)
    if n is None:
        raise ValueError("节点 %s 不存在" % nid)
    ins = inputs_of(n["type"])
    main_up = upstream(g, nid, ins[0][0]) if ins else None
    downs = downstream(g, nid)
    g["nodes"] = [x for x in g["nodes"] if x["id"] != nid]
    g["edges"] = [e for e in g["edges"] if e[0] != nid and e[1] != nid]
    if reconnect and main_up is not None:
        for b, p in downs:
            if can_connect(g, main_up, b, p) and upstream(g, b, p) is None:
                g["edges"].append([main_up, b, p])


def insert_after(g: dict, a: str, new_id: str) -> None:
    """把新节点插到 a 和它的下游之间（a → new → 原下游，主端口）。"""
    n = node_by_id(g, new_id)
    ins = inputs_of(n["type"])
    if not ins:
        return
    downs = downstream(g, a)
    if can_connect(g, a, new_id, ins[0][0]):
        connect(g, a, new_id, ins[0][0])
    for b, p in downs:
        if b == new_id:
            continue
        if can_connect(g, new_id, b, p):
            connect(g, new_id, b, p)


def swap_inputs(g: dict, nid: str) -> bool:
    """Ctrl+T：前两个同类型输入互换。"""
    n = node_by_id(g, nid)
    if n is None:
        return False
    ins = inputs_of(n["type"])
    if len(ins) < 2 or ins[0][1] != ins[1][1]:
        return False
    p0, p1 = ins[0][0], ins[1][0]
    u0, u1 = upstream(g, nid, p0), upstream(g, nid, p1)
    disconnect(g, nid, p0)
    disconnect(g, nid, p1)
    if u1 is not None:
        g["edges"].append([u1, nid, p0])
    if u0 is not None:
        g["edges"].append([u0, nid, p1])
    return True


def auto_layout(g: dict, dx: float = 200.0, dy: float = 110.0) -> None:
    """按「离 src 最长路径」分列，列内按原 y 排；src 最左，out 最右。"""
    depth = {SRC: 0}
    ids = [n["id"] for n in g["nodes"]] + [OUT]
    for _ in range(len(ids) + 2):
        changed = False
        for a, b, _p in g["edges"]:
            d = depth.get(a, 0) + 1
            if depth.get(b, -1) < d:
                depth[b] = d
                changed = True
        if not changed:
            break
    for nid in ids:
        depth.setdefault(nid, 1)
    others = [v for k, v in depth.items() if k != OUT]
    max_d = max(others) if others else 0
    depth[OUT] = max(depth.get(OUT, 1), max_d + 1)          # out 永远在最右一列
    cols: dict = {}
    for n in g["nodes"]:
        cols.setdefault(depth[n["id"]], []).append(n)
    for d, lst in cols.items():
        lst.sort(key=lambda n: n["pos"][1])
        for i, n in enumerate(lst):
            n["pos"] = [40.0 + d * dx, 40.0 + i * dy]
    g["io_pos"][SRC] = [40.0, 40.0]
    g["io_pos"][OUT] = [40.0 + depth[OUT] * dx, 40.0]


def from_legacy(host) -> dict:
    """「转为节点」：把图层上的 keyer / ai_matte / masks 搬成等价的图（结果逐位一致）。

    src → 蓝绿幕 → AI 抠像 → 套用遮罩(形状遮罩) → out。"""
    g = empty_graph()
    g["edges"] = []
    prev, x = SRC, 240.0
    k = K.spec(host)
    raw_k = host.get("keyer") if isinstance(host, dict) else getattr(host, "keyer", None)
    if k["enabled"]:
        n = add_node(g, "keyer", [x, 120.0], {"keyer": copy.deepcopy(raw_k or {})})
        g["edges"].append([prev, n["id"], "in"])
        prev, x = n["id"], x + 200.0
    raw_ai = host.get("ai_matte") if isinstance(host, dict) else getattr(host, "ai_matte", None)
    if isinstance(raw_ai, dict) and raw_ai.get("session"):
        n = add_node(g, "ai_matte", [x, 120.0], {"ai_matte": copy.deepcopy(raw_ai)})
        g["edges"].append([prev, n["id"], "in"])
        prev, x = n["id"], x + 200.0
    raw_m = host.get("masks") if isinstance(host, dict) else getattr(host, "masks", None)
    if M.masks_of(host):
        mk = add_node(g, "mask", [x, 260.0], {"masks": copy.deepcopy(list(raw_m or []))})
        ap = add_node(g, "matte_apply", [x, 120.0])
        g["edges"].append([prev, ap["id"], "in"])
        g["edges"].append([mk["id"], ap["id"], "matte"])
        prev, x = ap["id"], x + 200.0
    g["edges"].append([prev, OUT, "in"])
    g["io_pos"][OUT] = [x, 120.0]
    return g


# ───────────────────────────── 求值 ─────────────────────────────

def _as_matte(value, h, w):
    if value is None:
        return None
    if isinstance(value, tuple):            # image → 它的 alpha
        a = value[1]
        return np.ones((h, w), np.float32) if a is None else a.astype(np.float32)
    return value.astype(np.float32)


def _blend(base_rgb, base_a, new_rgb, new_a, m):
    """在遮罩 m 里用新的、外面保留原来的。"""
    if m is None:
        return new_rgb, new_a
    mm = m[..., None]
    rgb = np.clip(new_rgb.astype(np.float32) * mm + base_rgb.astype(np.float32) * (1.0 - mm) + 0.5, 0, 255)
    rgb = rgb.astype(np.uint8)
    if base_a is None and new_a is None:
        return rgb, None
    ba = np.ones(m.shape, np.float32) if base_a is None else base_a
    na = np.ones(m.shape, np.float32) if new_a is None else new_a
    return rgb, (na * m + ba * (1.0 - m)).astype(np.float32)


def _morph(m, px):
    import cv2
    if abs(px) < 0.5:
        return m
    k = int(round(abs(px))) * 2 + 1
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.erode(m, kern) if px > 0 else cv2.dilate(m, kern)


def _gauss(img, px):
    import cv2
    if px < 0.5:
        return img
    sigma = px / 2.0
    ks = int(sigma * 3) * 2 + 1
    return cv2.GaussianBlur(img, (ks, ks), sigma)


def _eval_node(n, ins, h, w, px_scale, src):
    t, p = n["type"], n["params"]
    if t == "mask":
        ms = M.masks_of(p)
        total = M.combine(ms, w, h, px_scale) if ms else None
        return np.ones((h, w), np.float32) if total is None else total
    if t == "matte_ops":
        a = _as_matte(ins.get("a"), h, w)
        b = _as_matte(ins.get("b"), h, w)
        if a is None:
            a = np.ones((h, w), np.float32)
        op = p.get("op", "add")
        if b is not None:
            if op == "add":
                a = np.clip(a + b, 0.0, 1.0)
            elif op == "subtract":
                a = a * (1.0 - b)
            elif op == "intersect":
                a = a * b
            elif op == "max":
                a = np.maximum(a, b)
            elif op == "min":
                a = np.minimum(a, b)
            elif op == "difference":
                a = np.abs(a - b)
        a = np.ascontiguousarray(a, np.float32)
        if p.get("invert"):
            a = 1.0 - a
        a = _morph(a, float(p.get("shrink") or 0.0) * px_scale)
        a = _gauss(a, float(p.get("feather") or 0.0) * px_scale)
        return np.clip(a, 0.0, 1.0).astype(np.float32)
    rgb, alpha = ins["in"]
    m = _as_matte(ins.get("mask"), h, w)
    if t == "keyer":
        k = K.normalize(p.get("keyer"))
        if not k["enabled"]:
            return rgb, alpha
        out, a_key = K.apply(rgb, k)
        new_a = alpha
        if a_key is not None:
            ak = a_key.astype(np.float32) / 255.0
            new_a = ak if alpha is None else alpha * ak
        if m is None:
            return out, new_a
        return _blend(rgb, alpha, out, new_a, m)
    if t == "ai_matte":
        if src is None or not A.is_active(p):
            return rgb, alpha
        from cutora.fx.layer_fx import apply_ai_matte
        return apply_ai_matte(rgb, alpha, p, px_scale, src)
    if t == "matte_apply":
        mt = _as_matte(ins.get("matte"), h, w)
        if mt is None:
            return rgb, alpha
        if p.get("invert"):
            mt = 1.0 - mt
        mode = p.get("mode", "multiply")
        if mode == "replace":
            return rgb, mt.astype(np.float32)
        if mode == "cutout":
            mt = 1.0 - mt
        return rgb, (mt if alpha is None else alpha * mt).astype(np.float32)
    if t == "blur":
        px = max(0.0, float(p.get("size") or 0.0)) * px_scale
        if px < 0.5:
            return rgb, alpha
        out = _gauss(rgb, px)
        new_a = _gauss(alpha, px) if (alpha is not None and p.get("alpha", True)) else alpha
        return _blend(rgb, alpha, out, new_a, m) if m is not None else (out, new_a)
    if t == "color":
        f = rgb.astype(np.float32) / 255.0
        f = f * float(p.get("gain", 1.0) or 1.0) + float(p.get("brightness") or 0.0)
        c = float(p.get("contrast") or 0.0)
        if abs(c) > 1e-6:
            f = (f - 0.5) * (1.0 + c) + 0.5
        sat = float(p.get("saturation") or 0.0)
        if abs(sat) > 1e-6:
            lum = (f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114)[..., None]
            f = lum + (f - lum) * (1.0 + sat)
        out = np.clip(f * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return _blend(rgb, alpha, out, alpha, m) if m is not None else (out, alpha)
    return rgb, alpha


def _bypass(n, ins, h, w):
    """旁路（Ctrl+P）：图像节点把主输入原样传下去；遮罩节点给第一个遮罩输入（没有 = 全白）。"""
    if output_of(n["type"]) == IMAGE:
        return ins.get("in")
    a = ins.get("a")
    return _as_matte(a, h, w) if a is not None else np.ones((h, w), np.float32)


def evaluate(rgb: np.ndarray, alpha, host, px_scale: float = 1.0, src: dict | None = None, view: str | None = None):
    """按图求值 → (rgb, alpha 或 None)。view = 只看到某个节点（查看器）：遮罩节点显示成黑白图。"""
    g = graph_of(host)
    if g is None:
        return rgb, alpha
    h, w = rgb.shape[:2]
    cache: dict = {SRC: (rgb, alpha)}
    busy: set = set()

    def value(nid):
        if nid in cache:
            return cache[nid]
        if nid in busy:                       # 坏图（成环）兜底
            return None
        busy.add(nid)
        n = node_by_id(g, nid)
        if n is None:
            cache[nid] = None
            return None
        ins = {}
        for port, ptype, _req, _lbl in inputs_of(n["type"]):
            up = upstream(g, nid, port)
            v = value(up) if up is not None else None
            if v is not None and ptype == MATTE:
                v = _as_matte(v, h, w)
            ins[port] = v
        if output_of(n["type"]) == IMAGE and ins.get("in") is None:
            res = None                         # 主输入没接：这个节点没有画面
        elif not n.get("enabled", True):
            res = _bypass(n, ins, h, w)
        else:
            res = _eval_node(n, ins, h, w, px_scale, src)
        busy.discard(nid)
        cache[nid] = res
        return res

    target = view if view and (view == SRC or node_by_id(g, view) is not None) else None
    if target is not None:
        res = value(target)
        if res is None:
            return rgb, alpha
        if isinstance(res, tuple):
            return res
        gray = np.clip(res * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=-1), None
    up = upstream(g, OUT, "in")
    if up is None:
        return rgb, alpha                      # out 没接：原样（不黑屏）
    res = value(up)
    if res is None:
        return rgb, alpha
    return res


def ai_nodes(host) -> list:
    g = graph_of(host)
    return [n for n in (g.get("nodes") if g else []) if n["type"] == "ai_matte"]


# ───────────────────────────── 查看器（只在预览里用，不存工程、不影响导出） ─────────────────────────────

_VIEWER: dict = {}


def set_viewer(graph_uid: str, node_id: str | None) -> None:
    if not graph_uid:
        return
    if node_id:
        _VIEWER[graph_uid] = node_id
    else:
        _VIEWER.pop(graph_uid, None)


def viewer_for(host):
    g = graph_of(host)
    if not g:
        return None
    return _VIEWER.get(g.get("uid"))


def clear_viewers() -> None:
    _VIEWER.clear()


_TLS = threading.local()


@contextlib.contextmanager
def preview_scope(on: bool = True):
    """画布预览画覆层时套上：图片层的节点图查看器只在这里面生效（导出 / 子进程永远不在里面）。"""
    prev = getattr(_TLS, "on", False)
    _TLS.on = bool(on)
    try:
        yield
    finally:
        _TLS.on = prev


def preview_view(host):
    return viewer_for(host) if getattr(_TLS, "on", False) else None
