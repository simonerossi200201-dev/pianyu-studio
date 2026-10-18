# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q171 · 中央监视器右键「蓝绿幕抠像」：一批视频一次抠（纯数据，没有 Qt）。

**作用于哪些视频片段**（``TARGETS``，记在设置里，下次默认还是它）：

* ``external``（默认）—— 外部加载进来的视频：不是媒体库主视频的那些视频轨（「Video B」那种）。
  蓝绿幕素材几乎都是从外面拖进来叠在主视频上的。
* ``library`` —— 媒体库视频（每条任务自己的主视频轨）。
* ``both`` —— 两种都要。
* ``cell:<id>`` —— 右键点中的那一格（分屏里的 A / B…），选中的每条都抠同一格。

不碰：整幅背景轨、片头缩略图 / 片头视频、锁定的轨道 / 片段。用了节点图的片段改图里的「蓝绿幕抠像」节点
（顶层 keyer 在节点图里不生效），图里没有抠像节点就跳过并说明。

**全自动抠像**（``tune_frames``，见文件末尾）：每个片段抽 3 张小帧，认绿 / 蓝、在它自己的屏幕色附近试色配增益、
把阈值落在人和背景之间，去掉半透明的灰。抽帧在后台线程里一个一个来，不并发、不解整段视频、不占显卡。
"""
from __future__ import annotations

import json
import os

import numpy as np

from cutora.fx import keyer as K

TARGETS = (
    ("external", "外部加载的视频（默认）"),
    ("library", "媒体库视频（主视频）"),
    ("both", "两种都要"),
)
TARGET_HINTS = {
    "external": "叠在主视频上、从外面拖进来的视频（例如 Video B 轨）。蓝绿幕素材通常在这里",
    "library": "每条任务自己的主视频（媒体库里那一条）",
    "both": "主视频和外部视频都抠",
}
DEFAULT_TARGET = "external"
GRAB_SIZE = 192
STEP_KEYS = {           # 「边缘」菜单每点一下改多少
    "erode": 1.0,
    "blur": 1.0,
    "despot": 1.0,
    "gain": 0.1,
}


# ───────────────────────────── 设置 ─────────────────────────────

def _settings_path() -> str:
    from cutora.core.paths import settings_file
    return settings_file("batch_key.json")


def load_target() -> str:
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            t = str(json.load(fh).get("target") or "")
    except Exception:
        t = ""
    return t if t in dict(TARGETS) else DEFAULT_TARGET


def save_target(target: str) -> None:
    if target not in dict(TARGETS):
        return
    try:
        path = _settings_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"target": target}, fh)
    except Exception:
        pass


# ───────────────────────────── 选片段 ─────────────────────────────

def _timeline(task):
    from cutora.batch import monitor_model as mm
    return mm.timeline_of(task)


def _is_leadin(c) -> bool:
    return bool(getattr(c, "is_thumbnail_leadin", False) or getattr(c, "is_intro_video_leadin", False))


def target_tracks(task, target: str, cell: str | None = None) -> list:
    """这条任务里要抠的视频轨。"""
    from cutora.batch import monitor_model as mm
    from cutora.render import backdrop as BG
    tl = _timeline(task)
    tracks = [tr for tr in (getattr(tl, "tracks", None) or [])
              if getattr(tr, "type", "") == "video" and not BG.is_backdrop_track(tr)]
    if cell:
        tr = mm.cell_track(task, cell)
        return [tr] if tr is not None and tr in tracks else []
    main = mm.main_video_track(task)
    if target == "library":
        return [main] if main is not None else []
    if target == "both":
        return tracks
    return [tr for tr in tracks if tr is not main and not getattr(tr, "is_main_video", False)]


def target_clips(task, target: str, cell: str | None = None) -> tuple[list, dict]:
    """(片段列表, 统计 {"locked": n})。跳过片头、锁定的轨道 / 片段、转场块。"""
    out, locked = [], 0
    for tr in target_tracks(task, target, cell):
        tr_locked = bool(getattr(tr, "locked", False))
        for c in getattr(tr, "clips", None) or []:
            if getattr(c, "type", "media") != "media" or _is_leadin(c):
                continue
            if tr_locked or getattr(c, "locked", False):
                locked += 1
                continue
            out.append(c)
    return out, {"locked": locked}


# ───────────────────────────── 改参数 ─────────────────────────────

def _graph_keyer_nodes(clip) -> list | None:
    """用了节点图：返回图里的抠像节点（可能空）；没用节点图：None。"""
    from cutora.fx import graph as G
    g = G.graph_of(clip)
    if g is None:
        return None
    return [n for n in (g.get("nodes") or []) if n.get("type") == "keyer"]


def _write(clip, fn) -> str:
    """fn(dict 当前参数) → dict 新参数 | None（不改）。返回 "ok" / "graph_none" / "same"。"""
    nodes = _graph_keyer_nodes(clip)
    if nodes is not None:
        if not nodes:
            return "graph_none"
        changed = False
        for n in nodes:
            params = n.setdefault("params", {})
            cur = K.normalize(params.get("keyer"))
            new = fn(dict(cur))
            if new == "__remove__":
                new = dict(K.DEFAULTS)          # 节点不删（那是节点图的结构），只把它关掉回默认
            if new is not None and K.normalize(new) != cur:
                params["keyer"] = K.normalize(new)
                changed = True
        return "ok" if changed else "same"
    raw = getattr(clip, "keyer", None)
    cur = K.normalize(raw)
    new = fn(dict(cur))
    if new is None:
        return "same"
    if new == "__remove__":
        if raw:
            clip.keyer = {}
            return "ok"
        return "same"
    norm = K.normalize(new)
    if raw and norm == cur:
        return "same"
    clip.keyer = norm
    return "ok"


def op_preset(name: str, color: str | None = None):
    def fn(cur):
        cur.update(K.PRESETS.get(name) or K.PRESETS["green"])
        if color:
            cur["color"] = color
        cur["enabled"] = True
        return cur
    return fn


def op_patch(patch: dict):
    def fn(cur):
        if cur == K.normalize(None):
            return None            # 没抠像的片段：改溢色 / 查看方式没有意义，不凭空造一份
        cur.update({k: v for k, v in patch.items() if k in K.DEFAULTS})
        return cur
    return fn


def op_enable(on: bool):
    def fn(cur):
        if on and cur == K.normalize(None):
            return None            # 从来没设过抠像：「重新开启」不凭空造一个
        cur["enabled"] = bool(on)
        return cur
    return fn


def op_step(key: str, sign: int):
    def fn(cur):
        if not cur.get("enabled"):
            return None
        cur[key] = float(cur.get(key) or 0.0) + STEP_KEYS.get(key, 1.0) * (1 if sign > 0 else -1)
        return cur
    return fn


def op_threshold(d_black: float = 0.0, d_white: float = 0.0):
    """阈值（Matte 黑 / 白裁切）往两边收：透明阈值 +、不透明阈值 − → 半透明的灰全部推成纯透明 / 纯不透明。"""
    def fn(cur):
        if not cur.get("enabled"):
            return None
        b = min(0.9, max(0.0, float(cur.get("black") or 0.0) + float(d_black)))
        w = max(0.1, min(1.0, float(cur.get("white") if cur.get("white") is not None else 1.0) + float(d_white)))
        if w <= b + 0.02:
            mid = (b + w) / 2.0
            b, w = max(0.0, mid - 0.01), min(1.0, mid + 0.01)
        cur["black"], cur["white"] = round(b, 3), round(w, 3)
        return cur
    return fn


def op_threshold_default():
    def fn(cur):
        if not cur.get("enabled"):
            return None
        cur["black"], cur["white"], cur["gamma"] = 0.0, 1.0, 1.0
        return cur
    return fn


def op_tuned(params: dict):
    """自动精调的结果：屏幕色 / 增益 / 阈值 / 预模糊换成这条自己的；溢色、边缘等用户设过的别的不动。"""
    def fn(cur):
        cur.update({k: v for k, v in params.items() if k in K.DEFAULTS})
        cur["enabled"] = True
        cur["view"] = "comp"
        return cur
    return fn


def op_edges_default():
    def fn(cur):
        for k in ("erode", "blur", "despot", "preblur", "black", "white", "gamma"):
            cur[k] = K.DEFAULTS[k]
        return cur
    return fn


def op_paste(params: dict):
    def fn(cur):
        new = K.normalize(params)
        new["view"] = "comp"
        return new
    return fn


def op_remove():
    return lambda cur: "__remove__"


def apply_keyer(task, target: str, fn, cell: str | None = None) -> dict:
    """对这条任务里选中范围的视频片段执行 fn。返回 {"clips","changed","locked","graph_none"}。"""
    clips, st = target_clips(task, target, cell)
    res = {"clips": len(clips), "changed": 0, "locked": st["locked"], "graph_none": 0}
    for c in clips:
        r = _write(c, fn)
        if r == "ok":
            res["changed"] += 1
        elif r == "graph_none":
            res["graph_none"] += 1
    return res


def set_masks_enabled(task, target: str, on: bool, cell: str | None = None) -> dict:
    clips, st = target_clips(task, target, cell)
    res = {"clips": len(clips), "changed": 0, "locked": st["locked"], "graph_none": 0}
    for c in clips:
        masks = getattr(c, "masks", None) or []
        hit = False
        for m in masks:
            if isinstance(m, dict) and bool(m.get("enabled", True)) != bool(on):
                m["enabled"] = bool(on)
                hit = True
        res["changed"] += int(hit)
    return res


def set_ai_matte_enabled(task, target: str, on: bool, cell: str | None = None) -> dict:
    clips, st = target_clips(task, target, cell)
    res = {"clips": len(clips), "changed": 0, "locked": st["locked"], "graph_none": 0}
    for c in clips:
        am = getattr(c, "ai_matte", None)
        if isinstance(am, dict) and am and bool(am.get("enabled")) != bool(on):
            am["enabled"] = bool(on)
            res["changed"] += 1
    return res


def keyer_state(tasks, target: str, cell: str | None = None) -> dict:
    """给菜单打勾 / 置灰用：{"clips", "on", "off_with_params", "masks", "ai"}。只看数据，不解码。"""
    out = {"clips": 0, "on": 0, "off_with_params": 0, "masks": 0, "masks_off": 0, "ai": 0, "ai_off": 0,
           "first": None}
    for task in tasks:
        clips, _st = target_clips(task, target, cell)
        for c in clips:
            out["clips"] += 1
            nodes = _graph_keyer_nodes(c)
            specs = ([K.normalize((n.get("params") or {}).get("keyer")) for n in nodes] if nodes is not None
                     else [K.normalize(getattr(c, "keyer", None))])
            raw_set = bool(nodes) if nodes is not None else bool(getattr(c, "keyer", None))
            if any(s["enabled"] for s in specs):
                out["on"] += 1
                if out["first"] is None:
                    out["first"] = next(s for s in specs if s["enabled"])
            elif raw_set:
                out["off_with_params"] += 1
            for m in getattr(c, "masks", None) or []:
                if isinstance(m, dict):
                    if m.get("enabled", True):
                        out["masks"] += 1
                    else:
                        out["masks_off"] += 1
            am = getattr(c, "ai_matte", None)
            if isinstance(am, dict) and am:
                out["ai" if am.get("enabled") else "ai_off"] += 1
    return out


# ───────────────────────────── 自动识别屏幕色 ─────────────────────────────

def detect_screen(rgb_u8: np.ndarray, min_share: float = 0.3) -> dict | None:
    """看画面四周一圈（绿 / 蓝幕通常铺满背景、人在中间）：绿多还是蓝多，取那类像素的中位数当屏幕色。
    返回 {"kind": "green"|"blue", "color": "#RRGGBB", "share": 占比}；两种都不够多 → None。"""
    if rgb_u8 is None or rgb_u8.ndim != 3 or rgb_u8.shape[2] < 3:
        return None
    a = rgb_u8[..., :3].astype(np.int16)
    h, w = a.shape[:2]
    if h < 8 or w < 8:
        return None
    bh, bw = max(2, int(h * 0.18)), max(2, int(w * 0.18))
    ring = np.zeros((h, w), dtype=bool)
    ring[:bh, :] = True
    ring[-bh:, :] = True
    ring[:, :bw] = True
    ring[:, -bw:] = True

    def classify(px):
        r, g, b = px[:, 0], px[:, 1], px[:, 2]
        green = (g > 60) & (g * 100 > r * 118 + 1200) & (g * 100 > b * 118 + 1200)
        blue = (b > 60) & (b * 100 > r * 118 + 1200) & (b * 100 > g * 105 + 1200)
        return green, blue

    # 四周一圈里屏幕色要占 ≥ min_share；整幅兜底（人站得很满、四周被挡）要占一半以上
    for region, need in ((ring, min_share), (np.ones((h, w), dtype=bool), max(0.5, min_share))):
        px = a[region].reshape(-1, 3)
        if px.size == 0:
            continue
        green, blue = classify(px)
        sg, sb = float(green.mean()), float(blue.mean())
        kind, mask, share = ("green", green, sg) if sg >= sb else ("blue", blue, sb)
        if share >= need:
            sel = px[mask]
            med = np.median(sel, axis=0)
            # 屏幕是一大片「差不多同一个颜色」：离中位色不远的要占多数（乱七八糟的彩色画面过不了这关）
            near = float((np.abs(sel - med).max(axis=1) <= 45).mean())
            if near < 0.6:
                continue
            return {"kind": kind, "color": "#%02X%02X%02X" % tuple(int(round(v)) for v in med[:3]),
                    "share": round(share, 3)}
    return None


def grab_frame(path: str, t: float, size: int = GRAB_SIZE) -> np.ndarray | None:
    """ffmpeg 抽一帧、直接缩成 size×size 的 RGB（几十 KB）。快速定位（-ss 在 -i 前），不解整段。"""
    if not path or not os.path.isfile(path):
        return None
    from cutora.core.config import FFMPEG_PATH
    from cutora.core.proc import run
    cmd = [FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", "%.3f" % max(0.0, float(t or 0.0)), "-i", str(path),
           "-frames:v", "1", "-an", "-sn",
           "-vf", "scale=%d:%d:flags=area" % (int(size), int(size)),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    kw = {}
    if os.name == "nt":
        from cutora.core.proc import creationflags
        kw["creationflags"] = creationflags() | 0x00004000      # BELOW_NORMAL_PRIORITY_CLASS：不跟播放 / 导出抢 CPU
    try:
        r = run(cmd, capture_output=True, timeout=15, **kw)
    except Exception:
        return None
    need = int(size) * int(size) * 3
    data = r.stdout or b""
    if r.returncode != 0 or len(data) < need:
        return None
    return np.frombuffer(data[:need], dtype=np.uint8).reshape(int(size), int(size), 3)


# ───────────────────────────── 自动精调（每条自己拾色 + 自动阈值） ─────────────────────────────
#
# 目标（Luca）：每条视频按自己的绿 / 蓝幕取色；Matte 上不许留半透明的灰。做法（全部在缩小帧上算，毫秒级）：
#   1. 每个片段抽 3 帧（20% / 50% / 80%），长边缩到 TUNE_SIDE（几百 KB）；
#   2. 「肯定是背景」= 画面四周一圈里屏幕色像素、往里收几像素（避开人的边缘）；
#      「肯定是前景」= 画面中间、明显不是屏幕色的像素、同样往里收；
#   3. 在屏幕色附近搜（R / G / B 各加减，步长 16 → 8 → 4，再配几档 Key 增益 / 预模糊），
#      让「前景里最透明的那点」和「背景里最不透明的那点」之间的距离最大 —— 这就是把灰拉开；
#   4. 阈值直接落在两者之间：透明阈值 = 背景 99.5% 分位 + 余量，不透明阈值 = 前景 0.5% 分位 − 余量；
#   5. 用最终参数复查：背景还剩多少不透明、前景有多少破洞、半透明像素占比 → 打分，给出「干净 / 要看一眼」。

TUNE_SIDE = 360
TUNE_POINTS = (0.2, 0.5, 0.8)


def grab_frame_fit(path: str, t: float, side: int = TUNE_SIDE) -> np.ndarray | None:
    """抽一帧、按原比例缩到长边 side（PPM 管道，带尺寸头）。"""
    if not path or not os.path.isfile(path):
        return None
    from cutora.core.config import FFMPEG_PATH
    from cutora.core.proc import run
    cmd = [FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", "%.3f" % max(0.0, float(t or 0.0)), "-i", str(path),
           "-frames:v", "1", "-an", "-sn",
           "-vf", "scale=w=%d:h=%d:force_original_aspect_ratio=decrease:flags=area" % (int(side), int(side)),
           "-f", "image2pipe", "-vcodec", "ppm", "-"]
    kw = {}
    if os.name == "nt":
        from cutora.core.proc import creationflags
        kw["creationflags"] = creationflags() | 0x00004000
    try:
        r = run(cmd, capture_output=True, timeout=20, **kw)
    except Exception:
        return None
    return _parse_ppm(r.stdout or b"") if r.returncode == 0 else None


def _parse_ppm(data: bytes) -> np.ndarray | None:
    try:
        if not data.startswith(b"P6"):
            return None
        parts, pos = [], 2
        while len(parts) < 3:
            while data[pos:pos + 1].isspace():
                pos += 1
            if data[pos:pos + 1] == b"#":
                while data[pos:pos + 1] not in (b"\n", b""):
                    pos += 1
                continue
            end = pos
            while not data[end:end + 1].isspace():
                end += 1
            parts.append(int(data[pos:end]))
            pos = end
        pos += 1
        w, h, mx = parts
        if mx != 255:
            return None
        need = w * h * 3
        if len(data) - pos < need:
            return None
        return np.frombuffer(data[pos:pos + need], dtype=np.uint8).reshape(h, w, 3)
    except Exception:
        return None


def _screen_mask(a: np.ndarray, kind: str) -> np.ndarray:
    r, g, b = a[..., 0].astype(np.int16), a[..., 1].astype(np.int16), a[..., 2].astype(np.int16)
    if kind == "blue":
        return (b > 35) & (b * 100 > r * 115 + 600) & (b * 100 > g * 105 + 600)
    return (g > 35) & (g * 100 > r * 115 + 600) & (g * 100 > b * 115 + 600)


def sure_masks(rgb_u8: np.ndarray, kind: str, shrink: int = 3):
    """(肯定背景, 肯定前景) 两张 bool 图，都往里收 shrink 像素避开边缘。
    背景 = 整幅里的屏幕色像素（含地上的影子、暗角）；前景 = 画面中间明显不是屏幕色的像素。"""
    import cv2
    h, w = rgb_u8.shape[:2]
    scr = _screen_mask(rgb_u8, kind)
    ring = np.zeros((h, w), bool)
    bh, bw = max(2, int(h * 0.22)), max(2, int(w * 0.22))
    ring[:bh, :] = ring[-bh:, :] = True
    ring[:, :bw] = ring[:, -bw:] = True
    center = np.zeros((h, w), bool)
    center[int(h * 0.15):int(h * 0.95), int(w * 0.2):int(w * 0.8)] = True
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * shrink + 1, 2 * shrink + 1))
    scr_in = cv2.erode(scr.astype(np.uint8), k).astype(bool)
    not_scr_in = cv2.erode((~scr).astype(np.uint8), k).astype(bool)
    _ = ring
    return scr_in, not_scr_in & center


def _raw_alpha(f32: np.ndarray, color: str, gain: float, preblur: float) -> np.ndarray:
    k = K.normalize({"enabled": True, "color": color, "gain": gain, "preblur": preblur,
                     "black": 0.0, "white": 1.0, "gamma": 1.0, "despill_on": False})
    a, _out = K.matte_and_despill(f32, k)
    return a


def _shift(color: str, dr: int, dg: int, db: int) -> str:
    c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    c = [max(0, min(255, v + d)) for v, d in zip(c, (dr, dg, db))]
    return "#%02X%02X%02X" % tuple(c)


def _gap(samples, color, gain, preblur):
    """所有帧合起来：前景 0.5% 分位 − 背景 99.5% 分位（越大 = 灰越少，阈值越好下）。"""
    bg_vals, fg_vals = [], []
    for f32, bg, fg in samples:
        a = _raw_alpha(f32, color, gain, preblur)
        if bg.any():
            bg_vals.append(a[bg])
        if fg.any():
            fg_vals.append(a[fg])
    if not bg_vals or not fg_vals:
        return -9.0, 0.0, 1.0
    bgv, fgv = np.concatenate(bg_vals), np.concatenate(fg_vals)
    qb = float(np.quantile(bgv, 0.995))
    qf = float(np.quantile(fgv, 0.005))
    return qf - qb, qb, qf


def tune_frames(frames, kind: str | None = None, color: str | None = None) -> dict | None:
    """frames: [uint8 RGB]（缩小帧）。返回 {"kind","params","score","quality":{...}}；认不出屏幕 → None。"""
    frames = [f for f in (frames or []) if f is not None and f.ndim == 3]
    if not frames:
        return None
    if kind is None or color is None:
        dets = [d for d in (detect_screen(f) for f in frames) if d]
        if not dets:
            return None
        kinds = [d["kind"] for d in dets]
        kind = max(set(kinds), key=kinds.count)
        cols = np.array([[int(d["color"][i:i + 2], 16) for i in (1, 3, 5)] for d in dets if d["kind"] == kind])
        color = "#%02X%02X%02X" % tuple(int(v) for v in np.median(cols, axis=0))
    samples = []
    for f in frames:
        bg, fg = sure_masks(f, kind)
        if bg.sum() < 50 or fg.sum() < 50:
            continue
        samples.append((np.multiply(f, np.float32(1.0 / 255.0), dtype=np.float32), bg, fg))
    if not samples:
        return None
    best = {"gap": -9.0}

    def consider(c, g, pb):
        gap, qb, qf = _gap(samples, c, g, pb)
        if gap > best["gap"] + 1e-4:
            best.update(gap=gap, color=c, gain=g, preblur=pb, qb=qb, qf=qf)

    for g in (1.0, 1.4, 2.0, 3.0):
        consider(color, g, 0.0)
    # 屏幕色坐标下降：R / G / B 各加减，步长 16 → 8 → 4
    for step in (16, 8, 4):
        improved = True
        rounds = 0
        while improved and rounds < 6:
            improved = False
            rounds += 1
            base = (best["color"], best["gain"], best["preblur"])
            for d in ((step, 0, 0), (-step, 0, 0), (0, 0, step), (0, 0, -step), (0, step, 0), (0, -step, 0)):
                before = best["gap"]
                consider(_shift(base[0], *d), base[1], base[2])
                if best["gap"] > before + 1e-4:
                    improved = True
            for g in (base[1] * 0.8, base[1] * 1.25):
                before = best["gap"]
                consider(best["color"], max(0.5, min(6.0, g)), best["preblur"])
                if best["gap"] > before + 1e-4:
                    improved = True
    consider(best["color"], best["gain"], 1.0)        # 压缩块多的手机素材：预模糊一下更干净就用
    qb, qf = best["qb"], best["qf"]
    if qf - qb >= 0.06:
        black = min(0.85, max(0.0, qb + 0.02))
        white = max(black + 0.04, min(1.0, qf - 0.02))
    else:                                             # 分不开：阈值落在中间，宁可边缘硬一点也不留灰
        mid = (qb + qf) / 2.0
        black, white = max(0.0, mid - 0.02), min(1.0, mid + 0.02)
    params = {"color": best["color"], "gain": round(float(best["gain"]), 3),
              "preblur": float(best["preblur"]), "black": round(black, 3), "white": round(white, 3),
              "gamma": 1.0}
    params, quality = _refine_thresholds(samples, params)
    return {"kind": kind, "params": params, "gap": round(best["gap"], 3), "quality": quality}


def _refine_thresholds(samples, params: dict):
    """阈值再往两边收，直到整幅「半透明的灰」最少 —— 但不许把人抠出洞（前景破洞是硬约束）。
    原始 Matte 只算一次，换阈值只是查表式重映射，几十个候选也就几毫秒。"""
    raws = []
    for f32, bg, fg in samples:
        raws.append((_raw_alpha(f32, params["color"], params["gain"], params["preblur"]), bg, fg))

    def cost(b, w):
        bl = fh = se = 0.0
        for a, bg, fg in raws:
            m = np.clip((a - b) / max(1e-4, w - b), 0.0, 1.0)
            bl += float((m[bg] > 0.02).mean()) if bg.any() else 0.0
            fh += float((m[fg] < 0.98).mean()) if fg.any() else 0.0
            se += semi_area(m)
        n = max(1, len(raws))
        bl, fh, se = bl / n, fh / n, se / n
        penalty = (fh - 0.01) * 40.0 if fh > 0.01 else 0.0      # 人身上破洞：基本不允许
        soft = 0.004 * (1.0 - (w - b))                          # 同样干净时，留宽一点的过渡（边缘柔、头发不锯齿）
        return se * 4.0 + bl * 4.0 + fh * 2.0 + penalty + soft, (bl, fh, se)

    b, w = float(params["black"]), float(params["white"])
    best_c, _q = cost(b, w)
    for _round in range(2):
        for nb in np.arange(b + 0.03, min(w - 0.06, 0.9), 0.03):
            c, _q = cost(float(nb), w)
            if c < best_c - 0.002:             # 明显更好才收（一点点改善不值得把边缘弄硬）
                best_c, b = c, float(nb)
        for nw in np.arange(w - 0.03, max(b + 0.06, 0.1), -0.03):
            c, _q = cost(b, float(nw))
            if c < best_c - 0.002:
                best_c, w = c, float(nw)
    out = dict(params, black=round(b, 3), white=round(w, 3))
    return out, matte_quality(samples, out)


def semi_area(m: np.ndarray) -> float:
    """「成片的灰」占比：半透明像素先往里收 2 像素 —— 人和背景之间那一两像素的正常过渡被去掉，剩下的才是要消掉的灰。"""
    import cv2
    semi = ((m > 0.05) & (m < 0.95)).astype(np.uint8)
    if not semi.any():
        return 0.0
    core = cv2.erode(semi, np.ones((5, 5), np.uint8))
    return float(core.mean())


def matte_quality(samples, params: dict) -> dict:
    """用最终参数复查：背景残留（背景里 alpha > 2%）、前景破洞（前景里 alpha < 98%）、成片的灰（``semi_area``）。"""
    k = K.normalize(dict(params, enabled=True, despill_on=False))
    bg_left = fg_hole = semi = 0.0
    n = 0
    for f32, bg, fg in samples:
        a, _o = K.matte_and_despill(f32, k)
        bg_left += float((a[bg] > 0.02).mean()) if bg.any() else 0.0
        fg_hole += float((a[fg] < 0.98).mean()) if fg.any() else 0.0
        semi += semi_area(a)
        n += 1
    n = max(1, n)
    q = {"bg_left": round(bg_left / n, 4), "fg_hole": round(fg_hole / n, 4), "semi": round(semi / n, 4)}
    q["clean"] = q["bg_left"] < 0.01 and q["fg_hole"] < 0.02 and q["semi"] < 0.01
    return q


def tune_points(task, clip) -> tuple[str, list] | None:
    """这个片段拿哪几帧来精调：片段的 20% / 50% / 80% 处（源时间）。"""
    tl = _timeline(task)
    sources = getattr(tl, "sources", None) or {}
    src = sources.get(getattr(clip, "source_id", None)) if isinstance(sources, dict) else None
    path = getattr(src, "path", None) if src is not None else None
    if not path:
        return None
    try:
        a, b = float(clip.in_t or 0.0), float(clip.out_t or 0.0)
    except (TypeError, ValueError):
        a, b = 0.0, 0.0
    span = max(0.0, b - a)
    return str(path), [round(a + span * p, 2) for p in TUNE_POINTS] if span > 0 else [round(a, 2)]
