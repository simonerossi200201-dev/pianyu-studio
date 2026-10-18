# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q144 · 中央监视器的数据层：只改工程数据、不碰界面。

监视器上每个格子是一条视频（一个 task）。这里放：
- ``view_task``：按格子分辨率缩小的「看的副本」，分屏遮罩按小图算（快几十倍），
  几何和原工程一致（片段 / 轨道偏移、分界线羽化、边框宽度都按比例缩）；
- 分屏 / 格子内容的编辑（分界线、平移、以光标为中心缩放、换格子、布局、边框）；
- 覆层的移动、缩放、跨视频移动 / 复制、删除、粘贴；
- 从素材库拖进来的图片 / 视频落位；
- 撤销用的快照。

所有函数只接受 / 修改 task 字典和里面的 Timeline，调用方负责刷新画面。
"""
from __future__ import annotations

import copy
import math
import os
import time
import uuid

from cutora.core.log import get_logger

_LOG = get_logger("cutora.batch.monitor_model")

OVERLAY_LISTS = ("image_overlays", "text_overlays")
SNAP_DIVIDER = (1.0 / 3.0, 0.5, 2.0 / 3.0)
MIN_TRACK_SCALE = 0.05
MAX_TRACK_SCALE = 20.0
MIN_OV_SCALE = 0.1
MAX_OV_SCALE = 10.0
MONITOR_SCRIPT_TAG = "central_monitor"


# ───────────────────────── 基础 ─────────────────────────

def canvas_size(task) -> tuple[int, int]:
    from cutora.model.canvas import canvas_wh
    w, h = canvas_wh(task)
    return max(2, int(w or 1080)), max(2, int(h or 1920))


def timeline_of(task):
    return task.get("timeline") if isinstance(task, dict) else None


def task_duration(task) -> float:
    """最长的视频片段结束点；没有视频就看 task 自带的时长。"""
    end = 0.0
    tl = timeline_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video":
            continue
        for c in getattr(tr, "clips", None) or []:
            try:
                d = float(getattr(c, "effective_duration", None) or c.duration)
                end = max(end, float(c.start_t) + d)
            except Exception:
                continue
    if end <= 0 and isinstance(task, dict):
        for key in ("duration", "trim_end_sec"):
            try:
                v = float(task.get(key) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                return v
    return end


def task_label(task, idx: int) -> str:
    path = ""
    if isinstance(task, dict):
        path = str(task.get("video") or task.get("path") or task.get("name") or "")
    name = os.path.splitext(os.path.basename(path))[0] if path else "视频"
    return "%02d  %s" % (idx + 1, name)


def has_video(task) -> bool:
    tl = timeline_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") == "video" and (getattr(tr, "clips", None) or []):
            return True
    return False


def main_video_track(task):
    tl = timeline_of(task)
    first = None
    from cutora.render.backdrop import is_backdrop_track
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video" or not (getattr(tr, "clips", None) or []):
            continue
        if is_backdrop_track(tr):          # Q155 整幅背景视频不是原视频
            continue
        if getattr(tr, "is_main_video", False):
            return tr
        if first is None:
            first = tr
    return first


def video_tracks(task) -> list:
    """内容视频轨（不含 Q155 的整幅背景视频轨：它不进格子、不参与拖动缩放）。"""
    from cutora.render.backdrop import is_backdrop_track
    tl = timeline_of(task)
    return [tr for tr in getattr(tl, "tracks", None) or []
            if getattr(tr, "type", "") == "video" and (getattr(tr, "clips", None) or [])
            and not is_backdrop_track(tr)]


# ───────────────────────── 缩小的看图副本 ─────────────────────────

def _source_key(src, sid) -> str:
    path = getattr(src, "preview_path", None) or getattr(src, "path", None)
    if isinstance(src, dict):
        path = src.get("preview_path") or src.get("path")
    if path:
        return "p:" + os.path.normcase(os.path.abspath(str(path)))
    return "s:" + str(sid)


def _scaled_kfs(kfs, kx, ky, ks, scale_scale):
    if not isinstance(kfs, dict) or not kfs:
        return kfs
    out = {}
    for path, pts in kfs.items():
        mul = None
        if path == "x":
            mul = kx
        elif path == "y":
            mul = ky
        elif path == "scale" and scale_scale:
            mul = ks
        if mul is None or not isinstance(pts, list):
            out[path] = pts
            continue
        new_pts = []
        for p in pts:
            if isinstance(p, dict) and isinstance(p.get("v"), (int, float)):
                q = dict(p)
                q["v"] = float(p["v"]) * mul
                new_pts.append(q)
            else:
                new_pts.append(p)
        out[path] = new_pts
    return out


def view_task(task, k: float) -> dict:
    """task 缩小 k 倍后的只读副本，给监视器的渲染线程用。

    只复制视频轨和它们的片段（浅拷贝，素材共享），像素量按比例缩：
    片段 x/y、轨道 x/y、关键帧 x/y、源像素模式的缩放、分界线羽化、
    边框宽度 / 投影 / 毛玻璃半径。素材 id 换成按路径的 key —— 不同工程
    里重名的 source id 不会串帧，同一个文件还能共用解码器。
    """
    from cutora.model.timeline import Timeline
    from cutora.render.split_layout import normalize_split, split_enabled
    w, h = canvas_size(task)
    vw = max(2, int(round(w * float(k))))
    vh = max(2, int(round(h * float(k))))
    kx, ky = vw / float(w), vh / float(h)
    ks = (kx + ky) / 2.0
    tl = timeline_of(task)
    fps = float(getattr(tl, "fps", 0) or (task.get("fps") if isinstance(task, dict) else 0) or 30.0)
    vtl = Timeline(vw, vh, fps)
    sources = getattr(tl, "sources", None) or {}
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video":
            continue
        t2 = copy.copy(tr)
        props = dict(getattr(tr, "props", None) or {})
        for key, mul in (("x", kx), ("y", ky)):
            try:
                props[key] = float(props.get(key) or 0.0) * mul
            except (TypeError, ValueError):
                props[key] = 0.0
        t2.props = props
        clips = []
        for c in getattr(tr, "clips", None) or []:
            c2 = copy.copy(c)
            try:
                c2.x = float(getattr(c, "x", 0.0) or 0.0) * kx
                c2.y = float(getattr(c, "y", 0.0) or 0.0) * ky
            except (TypeError, ValueError):
                pass
            src_mode = (str(getattr(c, "size_mode", "") or "").lower() == "source"
                        or str(getattr(c, "fit_mode", "") or "").lower() == "source")
            if src_mode:
                try:
                    c2.scale = float(getattr(c, "scale", 1.0) or 1.0) * ks
                except (TypeError, ValueError):
                    pass
            kfs = getattr(c, "keyframes", None)
            if kfs:
                c2.keyframes = _scaled_kfs(kfs, kx, ky, ks, src_mode)
            sid = getattr(c, "source_id", None)
            src = sources.get(sid) if sid is not None else None
            if src is not None:
                key = _source_key(src, sid)
                vtl.sources[key] = src
                c2.source_id = key
            clips.append(c2)
        t2.clips = clips
        vtl.tracks.append(t2)
    vt = {"timeline": vtl, "fps": fps, "dims": (vw, vh)}
    if isinstance(task, dict) and isinstance(task.get("bg_image"), dict):
        vt["bg_image"] = dict(task["bg_image"])       # Q155 外部背景图（按铺满画，跟分辨率无关）
    if split_enabled(task):
        sp = copy.deepcopy(normalize_split(task.get("split")))
        border = sp.get("border") or {}
        for key in ("width_px", "shadow_px", "glass_sigma"):
            if border.get(key) is not None:
                try:
                    border[key] = max(0.5 if key != "shadow_px" else 0.0,
                                      float(border[key]) * ks)
                except (TypeError, ValueError):
                    pass
        for d in sp.get("dividers") or []:
            if d.get("feather_px") is not None:
                try:
                    d["feather_px"] = max(0.0, float(d["feather_px"]) * ks)
                except (TypeError, ValueError):
                    pass
        sp["enabled"] = True
        vt["split"] = sp
    if not has_video(task) and isinstance(task, dict):
        vt["_fallback_path"] = str(task.get("video") or task.get("path") or "")
    return vt


def view_signature(task) -> tuple:
    """画面（视频层）相关的东西有没有变 —— 外面改了工程，监视器靠它知道要不要重画。"""
    from cutora.render.split_layout import split_enabled
    tl = timeline_of(task)
    rows = []
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video":
            continue
        props = getattr(tr, "props", None) or {}
        rows.append((
            id(tr), bool(getattr(tr, "visible", True)), bool(getattr(tr, "mute", False)),
            str(getattr(tr, "split_cell", None) or ""), str(getattr(tr, "bg_role", "") or ""),
            tuple(sorted((str(k), str(v)) for k, v in props.items())),
            tuple((id(c), getattr(c, "source_id", None), getattr(c, "start_t", 0),
                   getattr(c, "in_t", 0), getattr(c, "out_t", 0), getattr(c, "x", 0),
                   getattr(c, "y", 0), getattr(c, "scale", 1), getattr(c, "fit_mode", ""),
                   getattr(c, "rotation", 0), getattr(c, "speed", 1),
                   getattr(c, "opacity", 255), repr(getattr(c, "bg_fill", None)),
                   getattr(c, "scale_y", 1), getattr(c, "flip_x", False), getattr(c, "flip_y", False))
                  for c in getattr(tr, "clips", None) or []),
        ))
    sp = task.get("split") if isinstance(task, dict) else None
    sp_sig = repr(sp) if split_enabled(task) else ""
    bg_sig = repr(task.get("bg_image")) if isinstance(task, dict) else ""
    try:
        from cutora.render.visual_stack import stack_signature
        z_sig = stack_signature(task)              # Q163：轨道上下一挪，格子就要重画
    except Exception:
        z_sig = ()
    return (canvas_size(task), tuple(rows), sp_sig, bg_sig, z_sig)


def overlay_signature(task) -> tuple:
    out = []
    if not isinstance(task, dict):
        return ()
    for name in OVERLAY_LISTS:
        for ov in task.get(name) or []:
            if isinstance(ov, dict):
                out.append((name, id(ov), repr(sorted(
                    (k, v) for k, v in ov.items()
                    if not str(k).startswith("_") or k == "_track_id"
                ))))
    segs = task.get("segments") or []
    out.append(("segs", len(segs), id(task.get("style"))))
    return tuple(out)


# ───────────────────────── 覆层时间 ─────────────────────────

def overlay_time(ov, t: float, show_all: bool) -> float | None:
    """这层在监视器上按哪个时间画；None = 这个时间不显示。

    「显示全部」开着：t 落在区间里就用 t，不在就用区间中点（避开淡入淡出）。
    """
    try:
        s = float(ov.get("start") or 0.0)
        e = float(ov.get("end") if ov.get("end") is not None else s)
    except (TypeError, ValueError):
        return None
    if e < s:
        s, e = e, s
    if s <= t <= e:
        return float(t)
    if not show_all:
        return None
    return (s + e) / 2.0


def first_overlay_time(task) -> float | None:
    """这条视频第一条文字 / 图片覆层的中点 —— 「跟随覆层」时间模式用。"""
    best = None
    if not isinstance(task, dict):
        return None
    for name in ("text_overlays", "image_overlays"):
        for ov in task.get(name) or []:
            try:
                s = float(ov.get("start") or 0.0)
                e = float(ov.get("end") if ov.get("end") is not None else s)
            except (TypeError, ValueError):
                continue
            if best is None or s < best[0]:
                best = (s, (s + e) / 2.0)
    return None if best is None else best[1]


# ───────────────────────── 分屏几何 ─────────────────────────

def split_on(task) -> bool:
    from cutora.render.split_layout import split_enabled
    return split_enabled(task)


def layout_name(task) -> str | None:
    """开着的分屏布局 id。关着、或没有分屏时是 None。不整理整份几何。"""
    if not split_on(task) or not isinstance(task, dict):
        return None
    raw = task.get("split")
    if not isinstance(raw, dict):
        return None
    return str(raw.get("layout") or "")


def split_of(task) -> dict:
    from cutora.render.split_layout import normalize_split
    return normalize_split(task.get("split") if isinstance(task, dict) else None)


def cell_rects(task) -> dict:
    """cell id → (x, y, w, h) 画布像素。没开分屏返回 {}。"""
    if not split_on(task):
        return {}
    w, h = canvas_size(task)
    out = {}
    for c in split_of(task).get("cells") or []:
        try:
            x, y, cw, ch = [float(v) for v in c.get("rect") or (0, 0, 1, 1)]
        except (TypeError, ValueError):
            continue
        out[str(c.get("id"))] = (x * w, y * h, cw * w, ch * h)
    return out


def cell_at(task, x: float, y: float) -> str | None:
    best = None
    for cid, (cx, cy, cw, ch) in cell_rects(task).items():
        if cx <= x <= cx + cw and cy <= y <= cy + ch:
            area = cw * ch
            if best is None or area < best[1]:
                best = (cid, area)
    return None if best is None else best[0]


def divider_lines(task) -> list[dict]:
    """[{id, axis, pos, pts:[(x,y)…]}] 画布像素。"""
    if not split_on(task):
        return []
    from cutora.render.split_screen import _divider_span, divider_polyline
    w, h = canvas_size(task)
    sp = split_of(task)
    curve = str((sp.get("border") or {}).get("curve") or "straight")
    out = []
    for d in sp.get("dividers") or []:
        try:
            pts = divider_polyline(d, w, h, curve=curve, n=24, span=_divider_span(sp, d))
        except Exception:
            _LOG.exception("分界线 %s 取点失败", d.get("id"))
            continue
        if not pts:
            continue
        out.append({"id": str(d.get("id")), "axis": d.get("axis") or "h",
                    "pos": float(d.get("pos") or 0.5),
                    "pts": [(float(p[0]), float(p[1])) for p in pts]})
    return out


def _dist_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    if L <= 1e-12:
        return math.hypot(px - ax, py - ay)
    u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + u * dx), py - (ay + u * dy))


def divider_at(task, x: float, y: float, tol: float) -> dict | None:
    best = None
    for d in divider_lines(task):
        pts = d["pts"]
        dist = min((_dist_seg(x, y, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1)),
                   default=1e9)
        if dist <= tol and (best is None or dist < best[0]):
            best = (dist, d)
    return None if best is None else best[1]


def snap_divider_pos(pos: float, tol: float) -> float:
    for s in SNAP_DIVIDER:
        if abs(pos - s) <= tol:
            return s
    return pos


def set_divider_pos(task, did: str, pos: float) -> float:
    from cutora.render.split_layout import set_divider_on_task
    sp = set_divider_on_task(task, did, {"pos": float(pos)})
    for d in sp.get("dividers") or []:
        if str(d.get("id")) == str(did):
            return float(d.get("pos") or pos)
    return float(pos)


def set_divider_rotation(task, did: str, deg: float) -> float:
    """分界线旋转（和主画布 Alt+拖一样，限 ±45°）。返回实际角度。"""
    from cutora.render.split_layout import set_divider_on_task
    deg = max(-45.0, min(45.0, float(deg)))
    set_divider_on_task(task, did, {"rotation_deg": deg})
    return deg


def divider_compatible(sp_a: dict, sp_b: dict, did: str) -> bool:
    """两条分屏视频是否可同步联动同一条分界线 did（同布局，或同属上下/上1下N、左右/左1右N 家族且分界线方向一致）。"""
    if not isinstance(sp_a, dict) or not isinstance(sp_b, dict):
        return False
    da = next((d for d in (sp_a.get("dividers") or []) if str(d.get("id")) == str(did)), None)
    db = next((d for d in (sp_b.get("dividers") or []) if str(d.get("id")) == str(did)), None)
    if da is None or db is None:
        return False
    if (da.get("axis") or "h") != (db.get("axis") or "h"):
        return False
    la = str(sp_a.get("layout") or "")
    lb = str(sp_b.get("layout") or "")
    if la == lb:
        return True
    if la.startswith("top") and lb.startswith("top"):
        return True
    if la.startswith("left") and lb.startswith("left"):
        return True
    return False


def reset_dividers(task) -> None:
    from cutora.render.split_layout import apply_split_to_task, preset_geometry, recompute
    if not split_on(task):
        return
    sp = split_of(task)
    _cells, divs = preset_geometry(sp.get("layout") or "top_bottom")
    by_id = {str(d.get("id")): d for d in divs}
    for cur in sp.get("dividers") or []:
        ref = by_id.get(str(cur.get("id")))
        if ref is not None:
            cur["pos"] = float(ref["pos"])
            cur["rotation_deg"] = 0.0
    task["split"] = recompute(sp)
    apply_split_to_task(task, enabled=True)


def reset_secondary_dividers(task) -> None:
    """保持主分界线 d1 位置不变，仅将一对多副屏分界线（d2, d3, …）重置为等宽/等高均分。"""
    from cutora.render.split_layout import apply_split_to_task, preset_geometry, recompute
    if not split_on(task):
        return
    sp = split_of(task)
    _cells, divs = preset_geometry(sp.get("layout") or "top_bottom")
    by_id = {str(d.get("id")): d for d in divs if str(d.get("id")) != "d1"}
    if not by_id:
        return
    for cur in sp.get("dividers") or []:
        ref = by_id.get(str(cur.get("id")))
        if ref is not None:
            cur["pos"] = float(ref["pos"])
            cur["rotation_deg"] = 0.0
    task["split"] = recompute(sp)
    apply_split_to_task(task, enabled=True)


def cell_track(task, cid):
    from cutora.render.split_layout import occupied_cells
    return occupied_cells(task).get(str(cid)) if cid else None


def content_target(task, x: float, y: float, t: float | None = None):
    """点在画面上 → (轨道, 格子 id 或 None, 格子矩形)。拖动 / 缩放改的就是这条轨道。

    Q172：不分屏时以前一律返回「媒体库主视频」轨 —— 上面压着一条铺满的外部视频（绿幕人像）时，
    拖的其实是被盖住的主视频：看不见动、外部视频也不动，主视频却被悄悄挪走 / 缩小（主窗口里缩小外部视频后
    才露出来「主视频是黑的」）。现在点到哪一层就是哪一层：从最上面往下找，第一个在这个时刻有画面、
    并且画面框盖住这个点的视频轨。"""
    w, h = canvas_size(task)
    full = (0.0, 0.0, float(w), float(h))
    if split_on(task):
        # 分屏格子上面还可以压一条不进格子的全幅视频（圣光、亮度蒙版）。
        # 点在它的画面框里就选它，不能一律落到下面的格子。格子轨的缩放看起来也铺满画布，
        # 所以不能用 top_layer_at 从上往下扫全部视频轨，否则最上的格子会抢走每一次点击。
        hit = _overlay_track_at(task, x, y, t)
        if hit is not None:
            tr, rect = hit
            return tr, None, rect
        cid = cell_at(task, x, y)
        if cid is None:
            return None, None, full
        rect = cell_rects(task).get(cid) or full
        return cell_track(task, cid), cid, rect
    top = top_layer_at(task, x, y, t)
    return (top if top is not None else main_video_track(task)), None, full


def _overlay_track_at(task, x: float, y: float, t: float | None = None):
    """分屏开着时：最上面那条不进格子的视频轨，画面框盖住 (x, y) 就返回 (轨, 框)。"""
    for tr in reversed(video_tracks(task)):
        if str(getattr(tr, "split_cell", "") or ""):
            continue
        if not getattr(tr, "visible", True) or getattr(tr, "locked", False):
            continue
        for c in getattr(tr, "clips", None) or []:
            if getattr(c, "hidden", False) or getattr(c, "type", "media") != "media":
                continue
            if not _clip_active(c, t):
                continue
            rx, ry, rw, rh = clip_canvas_rect(task, tr, c)
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return tr, (rx, ry, rw, rh)
    return None


def _clip_active(c, t) -> bool:
    if t is None:
        return True
    try:
        st = float(getattr(c, "start_t", 0.0) or 0.0)
        dur = float(getattr(c, "effective_duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        return True
    if dur <= 0:
        try:
            dur = (float(c.out_t) - float(c.in_t)) / max(0.01, float(getattr(c, "speed", 1.0) or 1.0))
        except (TypeError, ValueError, AttributeError):
            return True
    return st - 1e-6 <= float(t) < st + dur + 1e-6


def clip_canvas_rect(task, track, clip) -> tuple[float, float, float, float]:
    """片段在画布上大致的框（铺满画布 × 片段缩放 × 轨道缩放，加上平移）。只给点选用，不用于渲染。"""
    w, h = canvas_size(task)
    tp = track_props(track)
    s = float(getattr(clip, "scale", 1.0) or 1.0) * float(tp.get("scale", 1.0) or 1.0)
    cx = w / 2.0 + float(getattr(clip, "x", 0.0) or 0.0) + float(tp.get("x", 0.0) or 0.0)
    cy = h / 2.0 + float(getattr(clip, "y", 0.0) or 0.0) + float(tp.get("y", 0.0) or 0.0)
    return cx - w * s / 2.0, cy - h * s / 2.0, w * s, h * s


def top_layer_at(task, x: float, y: float, t: float | None = None):
    """不分屏：(x, y) 处最上面那一层视频轨（看得见、没锁、这个时刻有画面、框盖住这个点）。"""
    for tr in reversed(video_tracks(task)):
        if not getattr(tr, "visible", True) or getattr(tr, "locked", False):
            continue
        for c in getattr(tr, "clips", None) or []:
            if getattr(c, "hidden", False) or getattr(c, "type", "media") != "media":
                continue
            if not _clip_active(c, t):
                continue
            rx, ry, rw, rh = clip_canvas_rect(task, tr, c)
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return tr
    return None


def layer_slot(task, track):
    """「同一批」的身份：媒体库主视频 = ("main",)；外部加载的视频 = ("ext", 第几条外部视频轨)。
    一批外部视频是按同样的顺序加到每条视频上的，所以第几条就是同一批（Luca：同一个图层位置）。"""
    main = main_video_track(task)
    if track is None:
        return None
    if track is main:
        return ("main",)
    ext = [tr for tr in video_tracks(task) if tr is not main]
    for i, tr in enumerate(ext):
        if tr is track:
            return ("ext", i)
    return None


def track_display_name(task, track) -> str:
    """图层的名字：轨道自己的名字，没有就用第一个片段的素材文件名。"""
    name = str(getattr(track, "name", "") or "")
    if name:
        return name
    tl = timeline_of(task)
    sources = getattr(tl, "sources", None) or {}
    for c in getattr(track, "clips", None) or []:
        src = sources.get(getattr(c, "source_id", None)) if isinstance(sources, dict) else None
        path = getattr(src, "path", None) if src is not None else None
        if path:
            return os.path.basename(str(path))
    return ""


def track_for_slot(task, slot):
    if not slot:
        return None
    main = main_video_track(task)
    if slot[0] == "main":
        return main
    ext = [tr for tr in video_tracks(task) if tr is not main]
    i = int(slot[1])
    return ext[i] if 0 <= i < len(ext) else None


def sibling_video_track(task, cid, slot):
    """另一条视频里的同一层。格子 id 优先。否则按槽（主视频 / 第 N 条外部视频），
    分屏开着也认外部槽 —— 不能因为开了分屏就把压在格子上的全幅视频丢掉。
    没有槽、又开着分屏：不猜一条格子轨。没有槽、也没分屏：主视频。"""
    if cid:
        return cell_track(task, cid)
    if slot:
        return track_for_slot(task, slot)
    if split_on(task):
        return None
    return main_video_track(task)


# ───────────────────────── 格子内容：平移 / 缩放 ─────────────────────────

def track_props(track) -> dict:
    from cutora.render.track_transform import normalize_track_props
    return normalize_track_props(getattr(track, "props", None))


def set_track_props(track, **patch) -> dict:
    props = dict(getattr(track, "props", None) or {})
    for key, val in patch.items():
        props[key] = val
    try:
        s = float(props.get("scale", 1.0))
    except (TypeError, ValueError):
        s = 1.0
    props["scale"] = max(MIN_TRACK_SCALE, min(MAX_TRACK_SCALE, s))
    track.props = props
    return props


def zoom_props(props: dict, factor: float, mx: float, my: float, cell_w: float, cell_h: float) -> dict:
    """以格子里 (mx, my) 为不动点缩放。轨道变换绕格子中心缩放 + 平移，
    新平移 t' = (1-f)(m - c) + f·t，光标下那个像素原地不动。"""
    s0 = float(props.get("scale", 1.0) or 1.0)
    s1 = max(MIN_TRACK_SCALE, min(MAX_TRACK_SCALE, s0 * float(factor)))
    f = s1 / s0
    cx, cy = cell_w / 2.0, cell_h / 2.0
    x0 = float(props.get("x", 0.0) or 0.0)
    y0 = float(props.get("y", 0.0) or 0.0)
    out = dict(props)
    out["scale"] = s1
    out["x"] = (1.0 - f) * (mx - cx) + f * x0
    out["y"] = (1.0 - f) * (my - cy) + f * y0
    return out


# Q145 修复：格子内容的平移 / 缩放改到「片段变换」上。
#
# 以前改的是轨道变换（track.props）。轨道变换是「嵌套序列」语义：整条轨道先按
# 格子大小画进一张图，再整体平移 / 缩放 —— 格子外面那部分原视频像素在第一步就被
# 裁掉了，拖回来就是黑的，导出也是黑的。片段变换是在画原始画面时就定位，画面有
# 多大就能拖出多大，和主画布上直接拖视频是同一个东西。
#
# 平移 / 缩放都是仿射：x' = a·x + bx，y' = a·y + by，scale' = a·scale
# （x、y 是片段中心相对格子中心的偏移）。关键帧里的每个值同样变换。

_GEOM_PATHS = ("x", "y", "scale")


def _kf_points(clip, path):
    from cutora.render.keyframe import keyframes_of
    pts = keyframes_of(clip).get(path)
    return pts if isinstance(pts, list) and pts else None


def clip_state(track) -> list:
    """每个片段的 (clip, x, y, scale, {path: [关键帧值…]})，作为一次拖动的基准。"""
    out = []
    for c in getattr(track, "clips", None) or []:
        kfs = {}
        for path in _GEOM_PATHS:
            pts = _kf_points(c, path)
            if pts:
                kfs[path] = [p.get("v") if isinstance(p, dict) else None for p in pts]
        out.append((c, float(getattr(c, "x", 0.0) or 0.0), float(getattr(c, "y", 0.0) or 0.0),
                    float(getattr(c, "scale", 1.0) or 1.0), kfs))
    return out


def content_xy_scale(track) -> tuple[float, float, float]:
    """这一格内容的代表值（第一个片段），给 HUD、吸附用。"""
    for c in getattr(track, "clips", None) or []:
        return (float(getattr(c, "x", 0.0) or 0.0), float(getattr(c, "y", 0.0) or 0.0),
                float(getattr(c, "scale", 1.0) or 1.0))
    return 0.0, 0.0, 1.0


def clamp_zoom_factor(state, a: float) -> float:
    """缩放倍数夹在 [MIN, MAX] 里（按第一个片段算，整格一起缩）。"""
    if not state:
        return a
    s0 = state[0][3] or 1.0
    s1 = max(MIN_TRACK_SCALE, min(MAX_TRACK_SCALE, s0 * float(a)))
    return s1 / s0


def apply_content_affine(state, a: float, bx: float, by: float) -> None:
    """从基准 state 出发：x' = a·x + bx，y' = a·y + by，scale' = a·scale。"""
    for c, x, y, sc, kfs in state:
        c.x = a * x + bx
        c.y = a * y + by
        c.scale = max(0.01, a * sc)
        for path, vals in kfs.items():
            pts = _kf_points(c, path)
            if not pts:
                continue
            for p, v0 in zip(pts, vals):
                if not isinstance(p, dict) or not isinstance(v0, (int, float)):
                    continue
                if path == "x":
                    p["v"] = a * float(v0) + bx
                elif path == "y":
                    p["v"] = a * float(v0) + by
                else:
                    p["v"] = max(0.01, a * float(v0))


def pan_content(track, dx: float, dy: float) -> None:
    apply_content_affine(clip_state(track), 1.0, dx, dy)


def zoom_content(track, factor: float, ux: float, uy: float, snap_one: bool = True) -> float:
    """以格子里相对格子中心 (ux, uy) 的点为不动点缩放整格内容。返回新的缩放（第一个片段）。

    片段中心 c 缩放后 c' = u + f·(c − u) = f·c + (1 − f)·u。
    """
    state = clip_state(track)
    if not state:
        return 1.0
    f = clamp_zoom_factor(state, factor)
    s0 = state[0][3] or 1.0
    if snap_one and abs(s0 * f - 1.0) < 0.02 and abs(s0 - 1.0) > 1e-6:
        f = 1.0 / s0
    apply_content_affine(state, f, (1.0 - f) * ux, (1.0 - f) * uy)
    return s0 * f


def track_has_geometry(track) -> bool:
    p = track_props(track)
    return (abs(p["x"]) > 1e-9 or abs(p["y"]) > 1e-9 or abs(p["scale"] - 1.0) > 1e-9)


def bake_track_geometry(track) -> bool:
    """把轨道的平移 / 缩放并进每个片段（等效画面，但格子外的原像素不再被裁成黑）。

    轨道变换：中心 = 格子中心 + s·(x, y) + t，大小 × s → 片段 x' = s·x + tx，scale' = s·scale。
    有旋转 / 翻转的轨道没法等效，保持原样返回 False。
    """
    p = track_props(track)
    if not track_has_geometry(track):
        return False
    if abs(p["rotation"]) > 1e-9 or p["flip_x"] or p["flip_y"]:
        return False
    apply_content_affine(clip_state(track), float(p["scale"]), float(p["x"]), float(p["y"]))
    set_track_props(track, x=0.0, y=0.0, scale=1.0)
    return True


def bake_task_split_geometry(task) -> int:
    """这条视频里所有「在分屏格子里、带轨道平移 / 缩放」的视频轨都并进片段。返回改了几条轨。"""
    n = 0
    for tr in video_tracks(task):
        if not getattr(tr, "split_cell", None):
            continue
        if bake_track_geometry(tr):
            n += 1
    return n


def reset_track_transform(track) -> None:
    """重置这一格：片段位置归零、缩放 1；轨道上的平移 / 缩放也清掉。"""
    for c, _x, _y, _s, kfs in clip_state(track):
        c.x, c.y, c.scale = 0.0, 0.0, 1.0
        for path in kfs:
            pts = _kf_points(c, path)
            for pt in pts or []:
                if isinstance(pt, dict):
                    pt["v"] = 1.0 if path == "scale" else 0.0
    if track_has_geometry(track):
        set_track_props(track, x=0.0, y=0.0, scale=1.0, rotation=0.0)


# ───────────────────────── 布局 / 换格子 / 边框 ─────────────────────────

def set_layout(task, layout: str | None) -> None:
    """换布局（None = 关分屏）。已在格子里的轨道按原格子顺序先放，其余视频轨（主轨优先）补空格。"""
    from cutora.batch.split_assign import layout_cells, resolve_auto_layout
    from cutora.render.split_layout import apply_split_to_task, parse_1_to_n
    if not layout:
        if isinstance(task.get("split"), dict):
            apply_split_to_task(task, enabled=False)
        return
    old_order = [str(c.get("id")) for c in (split_of(task).get("cells") or [])] if split_on(task) else []
    vids = video_tracks(task)
    if layout in ("top1_bottom_auto", "left1_right_auto"):
        layout = resolve_auto_layout(layout, max(1, len(vids) - 1))
    placed = []
    if old_order:
        by_cell = {}
        for tr in vids:
            cid = str(getattr(tr, "split_cell", None) or "")
            if cid and cid not in by_cell:
                by_cell[cid] = tr
        placed = [by_cell[c] for c in old_order if c in by_cell]
    main = main_video_track(task)
    rest = [tr for tr in vids if tr not in placed]
    rest.sort(key=lambda tr: 0 if tr is main else 1)
    order = placed + rest
    if main is not None and parse_1_to_n(layout) is not None:
        order = [main] + [tr for tr in order if tr is not main]
    apply_split_to_task(task, layout=layout, enabled=True)
    for tr in vids:
        tr.split_cell = None
    cells = layout_cells(layout)
    for cid, tr in zip(cells, order):
        tr.split_cell = cid


def swap_cells(task, a: str, b: str) -> bool:
    ta, tb = cell_track(task, a), cell_track(task, b)
    if ta is None and tb is None:
        return False
    if ta is not None:
        ta.split_cell = str(b)
    if tb is not None:
        tb.split_cell = str(a)
    return True


def set_border_style(task, style: str) -> None:
    from cutora.render.split_layout import apply_split_to_task
    if not split_on(task):
        return
    apply_split_to_task(task, border={"style": str(style)})


def split_clipboard(task) -> dict | None:
    if not split_on(task):
        return None
    cells = {}
    for cid in cell_rects(task):
        tr = cell_track(task, cid)
        if tr is not None:
            x, y, sc = content_xy_scale(tr)
            cells[cid] = {"x": x, "y": y, "scale": sc}
    return {"split": copy.deepcopy(split_of(task)), "cell_props": cells}


def paste_split(task, clip: dict) -> None:
    """把复制的分屏（布局、分界线、边框、每格的平移缩放）套到这条视频上。"""
    from cutora.render.split_layout import recompute
    sp = copy.deepcopy((clip or {}).get("split") or {})
    if not sp:
        return
    layout = sp.get("layout") or "top_bottom"
    if not split_on(task) or split_of(task).get("layout") != layout:
        set_layout(task, layout)
    sp["enabled"] = True
    task["split"] = recompute(sp)
    valid = set(cell_rects(task))
    for tr in video_tracks(task):
        cid = str(getattr(tr, "split_cell", None) or "")
        if cid and cid not in valid:
            tr.split_cell = None
    for cid, props in ((clip or {}).get("cell_props") or {}).items():
        tr = cell_track(task, cid)
        if tr is None:
            continue
        bake_track_geometry(tr)
        for c in getattr(tr, "clips", None) or []:
            c.x = float(props.get("x", 0.0))
            c.y = float(props.get("y", 0.0))
            c.scale = float(props.get("scale", 1.0))


# ───────────────────────── 覆层 ─────────────────────────

def is_full_canvas_container(ov, cw, ch) -> bool:
    try:
        return (abs(float(ov.get("x") or 0)) < 1 and abs(float(ov.get("y") or 0)) < 1
                and abs(float(ov.get("w") or 0) - cw) < 2 and abs(float(ov.get("h") or 0) - ch) < 2)
    except (TypeError, ValueError):
        return False


def image_hit_rects(ov) -> list:
    """图片覆层可以点中的矩形（画布像素）：每个元素一块。"""
    out = []
    try:
        ox, oy = float(ov.get("x") or 0), float(ov.get("y") or 0)
    except (TypeError, ValueError):
        ox, oy = 0.0, 0.0
    for el in ov.get("elements") or []:
        if not isinstance(el, dict):
            continue
        try:
            out.append((ox + float(el.get("x") or 0), oy + float(el.get("y") or 0),
                        float(el.get("w") or 0), float(el.get("h") or 0)))
        except (TypeError, ValueError):
            continue
    if not out and ov.get("w"):
        try:
            out.append((ox, oy, float(ov.get("w")), float(ov.get("h") or 0)))
        except (TypeError, ValueError):
            pass
    return out


def overlay_kind(ov) -> str:
    """text / image / adjust（Q174 调整图层：也在 image_overlays 里，但挂在调整轨上，不是图片）。"""
    t = str(ov.get("type") or "")
    if t == "adjust":
        return "adjust"
    if t == "image" or ov.get("elements"):
        return "image"
    return "text"


# ─────────── Q158 同类素材：别的视频里「同一条」文字 / 图片 ───────────

def _ov_list_name(task, ov):
    for name in OVERLAY_LISTS:
        if any(o is ov for o in (task.get(name) or [])):
            return name
    return "text_overlays" if overlay_kind(ov) == "text" else "image_overlays"


def _ov_index_in_kind(task, ov):
    name = _ov_list_name(task, ov)
    rows = [o for o in (task.get(name) or []) if isinstance(o, dict)]
    for i, o in enumerate(rows):
        if o is ov:
            return i
    return -1


def _image_path(ov):
    els = ov.get("elements") or []
    if els and isinstance(els[0], dict) and els[0].get("path"):
        return os.path.normcase(str(els[0].get("path")))
    return os.path.normcase(str(ov.get("path") or ov.get("image_path") or ""))


def sibling_overlay(src_task, ov, dst_task):
    """``dst_task`` 里和 ``ov`` 算「同一条」的文字 / 图片（同步编辑时一起动）。

    优先级：同 id → 同批次标记（_script）+ 同图片文件 / 同预设或同内容 → 同类第 N 条。找不到 → None。"""
    if dst_task is src_task or not isinstance(ov, dict):
        return None
    name = _ov_list_name(src_task, ov)
    rows = [o for o in (dst_task.get(name) or []) if isinstance(o, dict)]
    if not rows:
        return None
    oid = ov.get("id")
    if oid is not None:
        for o in rows:
            if o.get("id") == oid:
                return o
    tag = str(ov.get("_script") or "")
    kind = overlay_kind(ov)
    if kind == "adjust":
        # 调整图层：对方的第 N 块调整图层（按出现顺序）
        mine = [o for o in (src_task.get(name) or []) if isinstance(o, dict) and overlay_kind(o) == "adjust"]
        theirs = [o for o in rows if overlay_kind(o) == "adjust"]
        k = next((i for i, o in enumerate(mine) if o is ov), -1)
        return theirs[k] if 0 <= k < len(theirs) else None
    rows = [o for o in rows if overlay_kind(o) != "adjust"]
    same_tag = [o for o in rows if str(o.get("_script") or "") == tag]
    if kind == "image":
        path = _image_path(ov)
        if path:
            hits = [o for o in same_tag if _image_path(o) == path] or [o for o in rows if _image_path(o) == path]
            if len(hits) == 1:
                return hits[0]
            if hits:
                idx = _ov_index_in_kind(src_task, ov)
                mine = [o for o in rows if _image_path(o) == path]
                k = sum(1 for o in (src_task.get(name) or [])[:max(0, idx)] if isinstance(o, dict) and _image_path(o) == path)
                return mine[k] if k < len(mine) else mine[-1]
    else:
        preset = ov.get("_preset_name") or ov.get("source_preset_id")
        content = str(ov.get("content") or "")
        pool = same_tag or rows
        hits = [o for o in pool if content and str(o.get("content") or "") == content]
        if len(hits) == 1:
            return hits[0]
        if preset:
            hits = [o for o in pool if (o.get("_preset_name") or o.get("source_preset_id")) == preset]
            if len(hits) == 1:
                return hits[0]
    idx = _ov_index_in_kind(src_task, ov)
    if 0 <= idx < len(rows) and (not tag or str(rows[idx].get("_script") or "") == tag):
        return rows[idx]
    return None


def sibling_ratio(src_task, dst_task) -> float:
    sw, sh = canvas_size(src_task)
    dw, dh = canvas_size(dst_task)
    return min(dw / float(sw or 1), dh / float(sh or 1))


def move_overlay(task, ov, dx: float, dy: float) -> None:
    cw, ch = canvas_size(task)
    if overlay_kind(ov) == "image" and ov.get("elements") and is_full_canvas_container(ov, cw, ch):
        for el in ov.get("elements") or []:
            if isinstance(el, dict):
                el["x"] = float(el.get("x") or 0) + dx
                el["y"] = float(el.get("y") or 0) + dy
        return
    ov["x"] = float(ov.get("x") or 0) + dx
    ov["y"] = float(ov.get("y") or 0) + dy


def overlay_anchor(task, ov) -> tuple[float, float]:
    """移动的参照点：文字 = 框左上角；整画布容器的图片 = 第一个元素左上角。"""
    cw, ch = canvas_size(task)
    if overlay_kind(ov) == "image" and ov.get("elements") and is_full_canvas_container(ov, cw, ch):
        rects = image_hit_rects(ov)
        if rects:
            return rects[0][0], rects[0][1]
    return float(ov.get("x") or 0), float(ov.get("y") or 0)


def overlay_bounds(task, ov) -> tuple[float, float, float, float]:
    cw, ch = canvas_size(task)
    if overlay_kind(ov) == "image":
        rects = image_hit_rects(ov) if ov.get("elements") else []
        if rects:
            x0 = min(r[0] for r in rects)
            y0 = min(r[1] for r in rects)
            x1 = max(r[0] + r[2] for r in rects)
            y1 = max(r[1] + r[3] for r in rects)
            return x0, y0, x1 - x0, y1 - y0
    try:
        x, y = float(ov.get("x") or 0), float(ov.get("y") or 0)
        w, h = float(ov.get("w") or 0), float(ov.get("h") or 0)
        s = float(ov.get("scale") or 1.0) if overlay_kind(ov) in ("text", "adjust") else 1.0
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0, 0.0
    if abs(s - 1.0) > 1e-6:
        cx, cy = x + w / 2, y + h / 2
        w, h = w * s, h * s
        x, y = cx - w / 2, cy - h / 2
    return x, y, w, h


def scale_overlay(task, ov, factor: float, ax: float, ay: float) -> float:
    """以 (ax, ay) 为不动点缩放覆层。文字改 scale（无损）并挪中心；图片直接改元素框。
    返回新的倍率（文字）或 1（图片）。"""
    f = float(factor)
    cw, ch = canvas_size(task)
    if overlay_kind(ov) == "image" and ov.get("elements") and is_full_canvas_container(ov, cw, ch):
        for el in ov.get("elements") or []:
            if not isinstance(el, dict):
                continue
            ex = float(el.get("x") or 0)
            ey = float(el.get("y") or 0)
            ew = float(el.get("w") or 0)
            eh = float(el.get("h") or 0)
            if ew * f < 4 or eh * f < 4:
                return 1.0
            el["x"] = ax + (ex - ax) * f
            el["y"] = ay + (ey - ay) * f
            el["w"] = ew * f
            el["h"] = eh * f
        return 1.0
    x = float(ov.get("x") or 0)
    y = float(ov.get("y") or 0)
    w = float(ov.get("w") or 0)
    h = float(ov.get("h") or 0)
    s0 = float(ov.get("scale") or 1.0)
    s1 = max(MIN_OV_SCALE, min(MAX_OV_SCALE, s0 * f))
    f = s1 / s0
    cx, cy = x + w / 2.0, y + h / 2.0
    ncx, ncy = ax + (cx - ax) * f, ay + (cy - ay) * f
    ov["scale"] = s1
    ov["x"] = ncx - w / 2.0
    ov["y"] = ncy - h / 2.0
    return s1


def find_overlay(task, ov) -> tuple[str, int] | None:
    for name in OVERLAY_LISTS:
        for i, o in enumerate(task.get(name) or []):
            if o is ov:
                return name, i
    return None


def delete_overlay(task, ov) -> bool:
    hit = find_overlay(task, ov)
    if hit is None:
        return False
    name, i = hit
    lst = task.get(name)
    del lst[i]
    return True


def _plain_copy(obj):
    """dict/list/标量深拷贝；别的对象（QImage 缓存之类）只拷引用。"""
    if isinstance(obj, dict):
        return {k: _plain_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain_copy(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_plain_copy(v) for v in obj)
    return obj


_ID_SEQ = [0]


def _new_overlay_id(old):
    _ID_SEQ[0] += 1
    if isinstance(old, int) and not isinstance(old, bool):
        return int(time.time() * 1000000) + _ID_SEQ[0]
    s = str(old or "")
    if "_" in s:
        return s.rsplit("_", 1)[0] + "_" + uuid.uuid4().hex[:12]
    if s:
        return "%s_%s" % (s, uuid.uuid4().hex[:8])
    return "ov_mon_%s" % uuid.uuid4().hex[:12]


def _tracks_of_type(task, kind: str) -> list:
    tl = timeline_of(task)
    return [tr for tr in getattr(tl, "tracks", None) or [] if getattr(tr, "type", "") == kind]


def _ensure_track_of_type(task, kind: str, nth: int = 0):
    tl = timeline_of(task)
    if tl is None:
        return None
    trs = _tracks_of_type(task, kind)
    if trs:
        return trs[min(max(0, nth), len(trs) - 1)]
    from cutora.model.timeline_model import Track
    tr = Track(kind)
    tl.add_track(tr)
    return tr


def _nth_track_index(task, kind: str, track_id) -> int:
    for i, tr in enumerate(_tracks_of_type(task, kind)):
        if getattr(tr, "id", None) == track_id:
            return i
    return 0


def rescale_overlay(ov, r: float) -> dict:
    """画布尺寸不同的两条视频之间搬覆层：整层按 r 缩放（坐标系也缩）。"""
    if abs(r - 1.0) < 1e-6:
        return ov
    if overlay_kind(ov) == "text":
        try:
            from cutora.render.text_layer_paint import scaled_overlay
            out = scaled_overlay(ov, r)
            for key in ("start", "end", "id", "_track_id", "keyframes", "scale"):
                if key in ov:
                    out[key] = copy.deepcopy(ov[key]) if key == "keyframes" else ov[key]
            return out
        except Exception:
            _LOG.exception("文本层按比例缩放失败，只缩坐标")
        out = dict(ov)
        for key in ("x", "y", "w", "h", "fontsize"):
            if isinstance(out.get(key), (int, float)):
                out[key] = float(out[key]) * r
        return out
    out = _plain_copy(ov)
    for key in ("x", "y", "w", "h"):
        if isinstance(out.get(key), (int, float)):
            out[key] = float(out[key]) * r
    if overlay_kind(ov) == "adjust":          # 羽化 / 边框宽度也是画布像素
        if isinstance(out.get("feather"), (int, float)):
            out["feather"] = float(out["feather"]) * r
        sol = out.get("solid")
        if isinstance(sol, dict) and isinstance(sol.get("border_width"), (int, float)):
            sol["border_width"] = float(sol["border_width"]) * r
        xf = out.get("xform")
        if isinstance(xf, dict):
            for key in ("x", "y"):
                if isinstance(xf.get(key), (int, float)):
                    xf[key] = float(xf[key]) * r
    for el in out.get("elements") or []:
        if isinstance(el, dict):
            for key in ("x", "y", "w", "h"):
                if isinstance(el.get(key), (int, float)):
                    el[key] = float(el[key]) * r
    return out


def copy_overlay_into(src_task, ov, dst_task, *, dx: float = 0.0, dy: float = 0.0) -> dict:
    """ov 原模原样复制一份进 dst_task（新 id、挂到对应类型的轨道），再平移 (dx, dy)。

    两条视频画布不一样大时整层按宽度比例缩放。时间超出目标视频就往回收。
    """
    kind = overlay_kind(ov)
    list_name = "text_overlays" if kind == "text" else "image_overlays"
    sw, sh = canvas_size(src_task)
    dw, dh = canvas_size(dst_task)
    r = min(dw / float(sw), dh / float(sh))
    new = _plain_copy(ov)
    for key in list(new.keys()):
        if str(key).startswith("_c") or key in ("_hit", "_raster"):
            new.pop(key, None)
    new = rescale_overlay(new, r)
    new = _plain_copy(new)
    new["id"] = _new_overlay_id(ov.get("id"))
    track_kind = kind                 # text / image / adjust：挂到同类型的第 N 条轨道（没有就建）
    nth = _nth_track_index(src_task, track_kind, ov.get("_track_id"))
    tr = _ensure_track_of_type(dst_task, track_kind, nth)
    if tr is not None:
        new["_track_id"] = getattr(tr, "id", None)
    dur = task_duration(dst_task)
    if dur > 0:
        try:
            s = float(new.get("start") or 0.0)
            e = float(new.get("end") if new.get("end") is not None else s)
            length = max(0.0, e - s)
            if e > dur:
                e = dur
                s = max(0.0, min(s, e - min(length, dur)))
                new["start"], new["end"] = s, e
        except (TypeError, ValueError):
            pass
    new["_canvas_w"], new["_canvas_h"] = dw, dh
    move_overlay(dst_task, new, dx, dy)
    dst_task.setdefault(list_name, []).append(new)
    return new


# ───────────────────────── 共用剪贴板 ─────────────────────────
# Q145：主画布和中央监视器共用一份：在哪里 Ctrl+C，都能在另一边、另一条视频上 Ctrl+V。
# {"kind": "overlays", "items": [(源 task, 覆层副本), …]} 或 {"kind": "split", "data": …}

_CLIPBOARD: dict = {"data": None}


def set_clipboard(data) -> None:
    _CLIPBOARD["data"] = data


def get_clipboard():
    return _CLIPBOARD["data"]


# ───────────────────────── 素材拖进来 ─────────────────────────

def media_kind(path: str) -> str:
    from cutora.batch.batch_load import IMAGE_EXTS, VIDEO_EXTS
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return ""


def drop_image(task, path: str, x: float, y: float, *, width_frac: float = 0.4) -> dict:
    """一张图落在 (x, y)：以落点为中心，宽 = 画布 40%（图本身更小就用原尺寸），整段时长。"""
    from cutora.batch.script_insert_image import ensure_image_track, image_size, make_overlay
    tl = timeline_of(task)
    if tl is None:
        raise ValueError("这条视频没有时间线")
    cw, ch = canvas_size(task)
    iw, ih = image_size(path)
    if iw <= 0 or ih <= 0:
        raise ValueError("读不了这张图：%s" % os.path.basename(path))
    ew = min(float(iw), cw * float(width_frac))
    eh = ew * ih / float(iw)
    if eh > ch * 0.6:
        eh = ch * 0.6
        ew = eh * iw / float(ih)
    dur = task_duration(task) or 3600.0
    tr = ensure_image_track(tl, 1)
    ov = make_overlay(path, 0.0, dur, cw, ch, iw, ih, "fit", 1.0, 0.0, 0.0,
                      getattr(tr, "id", None), 1)
    ov["id"] = "ov_mon_%s" % uuid.uuid4().hex[:12]
    ov["_script"] = MONITOR_SCRIPT_TAG
    el = ov["elements"][0]
    el["x"], el["y"], el["w"], el["h"] = x - ew / 2.0, y - eh / 2.0, ew, eh
    task.setdefault("image_overlays", []).append(ov)
    return ov


_DUR_CACHE: dict = {}


def _probe_duration(path: str) -> float:
    key = os.path.normcase(os.path.abspath(path))
    hit = _DUR_CACHE.get(key)
    if hit:
        return hit
    from cutora.batch.script_insert_image import probe_video_duration
    d = float(probe_video_duration(path) or 0.0)
    if d > 0:
        _DUR_CACHE[key] = d
    return d


def _new_video_track(task):
    from cutora.batch.script_insert_image import BATCH_VIDEO_TRACK_NAME
    from cutora.model.timeline_model import Track
    tl = timeline_of(task)
    tr = Track("video")
    tr.name = BATCH_VIDEO_TRACK_NAME
    tr.is_main_video = False
    tl.add_track(tr)
    return tr


def _fill_track(task, tr, path: str) -> None:
    from cutora.batch.script_insert_image import _place_video_clip
    dur = task_duration(task)
    src_dur = _probe_duration(path)
    if src_dur <= 0:
        raise ValueError("读不到视频时长：%s" % os.path.basename(path))
    if dur <= 0:
        dur = src_dur
    tr.clips = []
    _place_video_clip(task, timeline_of(task), path, 0.0, dur, loop=True, mute=True,
                      replace=False, track=tr, src_dur=src_dur)


def drop_video(task, path: str, x: float, y: float, *, layout: str = "top_bottom") -> str:
    """一段视频落在 (x, y)。返回一句给用户看的话。

    没开分屏：自动开分屏（默认上下），落在上半 → 新视频在上、原视频在下，反之亦然。
    开着分屏：落在哪一格就进哪一格 ——
      空格子 → 直接放；
      放着别的素材 → 换成新视频（平移缩放保留）；
      放着主视频 → 主视频挪去空格；没有空格就和另一格互换后替换那一格。
    """
    from cutora.batch.split_assign import assign_split, layout_cells
    tl = timeline_of(task)
    if tl is None:
        raise ValueError("这条视频没有时间线")
    cw, ch = canvas_size(task)
    main = main_video_track(task)
    if not split_on(task):
        cells = layout_cells(layout)
        first_half = (y < ch / 2.0) if layout in ("top_bottom", "top1_bottom2") else (x < cw / 2.0)
        tr = _new_video_track(task)
        _fill_track(task, tr, path)
        order = [tr, main] if first_half else [main, tr]
        order = [t for t in order if t is not None]
        if len(order) < 2:
            order = [tr]
        assign_split(task, layout, order, cells=cells)
        return "已开分屏，新视频放在%s" % ("第一格" if first_half else "第二格")
    cid = cell_at(task, x, y)
    if cid is None:
        cid = next(iter(cell_rects(task)), "A")
    occ = cell_track(task, cid)
    if occ is None:
        tr = _new_video_track(task)
        _fill_track(task, tr, path)
        tr.split_cell = cid
        return "已放进格子 %s" % cid
    if occ is not main:
        _fill_track(task, occ, path)
        return "格子 %s 的素材已换成新视频" % cid
    free = [c for c in cell_rects(task) if cell_track(task, c) is None]
    if free:
        main.split_cell = free[0]
        tr = _new_video_track(task)
        _fill_track(task, tr, path)
        tr.split_cell = cid
        return "主视频挪到格子 %s，新视频放进格子 %s" % (free[0], cid)
    others = [c for c in cell_rects(task) if c != cid and cell_track(task, c) is not main]
    if not others:
        tr = _new_video_track(task)
        _fill_track(task, tr, path)
        main.split_cell = None
        tr.split_cell = cid
        return "格子 %s 换成新视频" % cid
    other = others[0]
    otr = cell_track(task, other)
    _fill_track(task, otr, path)
    swap_cells(task, cid, other)
    return "新视频放进格子 %s，主视频挪到格子 %s" % (cid, other)


def drop_as_layer(task, path: str) -> str:
    """图层面板拖进来：新开一层，不改分屏格子。选中的其他视频由调用方各调一次。"""
    kind = media_kind(path)
    cw, ch = canvas_size(task)
    if kind == "image":
        drop_image(task, path, cw * 0.5, ch * 0.5)
        return "已放入图片 %s" % os.path.basename(path)
    if kind == "video":
        tr = _new_video_track(task)
        _fill_track(task, tr, path)
        tr.split_cell = None
        return "已放入视频层 %s" % os.path.basename(path)
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext in (".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"):
        return _drop_audio_layer(task, path)
    raise ValueError("不支持的素材：%s" % os.path.basename(path))


def _drop_audio_layer(task, path: str) -> str:
    from cutora.audio.sfx_library import apply_new_bgm_defaults, get_or_create_bgm_track
    from cutora.model.timeline_model import Clip, MediaSource
    tl = timeline_of(task)
    if tl is None:
        raise ValueError("这条视频没有时间线")
    src = MediaSource(path)
    src.has_video = False
    src.has_audio = True
    tl.sources[src.id] = src
    tr = get_or_create_bgm_track(tl)
    dur = float(task_duration(task) or 0.0)
    src_dur = float(getattr(src, "duration", 0.0) or 0.0)
    if dur <= 0:
        dur = src_dur or 1.0
    clip = Clip(src.id, 0.0, dur, 0.0)
    apply_new_bgm_defaults(clip)
    tr.clips.append(clip)
    return "已放入配乐 %s" % os.path.basename(path)


def drop_media(task, path: str, x: float, y: float) -> str:
    kind = media_kind(path)
    if kind == "image":
        drop_image(task, path, x, y)
        return "已放入图片 %s" % os.path.basename(path)
    if kind == "video":
        return drop_video(task, path, x, y)
    raise ValueError("不支持的素材类型：%s" % os.path.basename(path))


# ───────────────────────── 撤销快照 ─────────────────────────

_MISSING = object()


def snapshot_split(task) -> dict:
    """只记分屏开关和每条视频轨在哪一格。批量换布局用这份，不复制文字、图片和关键帧。"""
    tl = timeline_of(task)
    cells = []
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") == "video":
            cells.append((tr, getattr(tr, "split_cell", None)))
    if isinstance(task, dict) and "split" in task:
        split = copy.deepcopy(task.get("split"))
    else:
        split = _MISSING
    return {"kind": "split", "split": split, "cells": cells}


def snapshot(task) -> dict:
    tl = timeline_of(task)
    tracks = list(getattr(tl, "tracks", None) or [])
    return {
        "split": copy.deepcopy(task.get("split")) if "split" in task else _MISSING,
        # Q155 整幅背景图 + 各片段背景填充（右键批量改的就是它们，撤销要能回来）
        "bg_image": copy.deepcopy(task.get("bg_image")) if "bg_image" in task else _MISSING,
        "bg_fill": [(c, copy.deepcopy(getattr(c, "bg_fill", None)))
                    for tr in tracks for c in (getattr(tr, "clips", None) or [])],
        "lists": {
            name: ([(ov, _plain_copy(ov)) for ov in task.get(name) or []]
                   if name in task else _MISSING)
            for name in OVERLAY_LISTS
        },
        "tracks": tracks,
        "track_state": [
            (tr, list(getattr(tr, "clips", None) or []),
             copy.deepcopy(getattr(tr, "props", None)),
             getattr(tr, "split_cell", None))
            for tr in tracks
        ],
        # 片段本身的位置 / 缩放（格子平移缩放改的是它们）
        "clip_geom": [
            (c, getattr(c, "x", 0.0), getattr(c, "y", 0.0), getattr(c, "scale", 1.0),
             copy.deepcopy(getattr(c, "keyframes", None)))
            for tr in tracks for c in (getattr(tr, "clips", None) or [])
        ],
        "layer_folders": (copy.deepcopy(task.get("layer_folders"))
                          if isinstance(task, dict) and "layer_folders" in task else _MISSING),
    }


def snapshot_layers(task) -> dict:
    """图层粘贴会改音量、时间和素材来源。这些不在普通快照里，撤销要单独记回来。"""
    snap = snapshot(task)
    snap["kind"] = "layers"
    tl = timeline_of(task)
    tracks = list(getattr(tl, "tracks", None) or [])
    snap["clip_fields"] = [
        (c, _clip_record(c))
        for tr in tracks for c in (getattr(tr, "clips", None) or [])
    ]
    return snap


def _clip_record(clip) -> dict:
    from cutora.model.project_schema import CLIP_KEYS
    data = {
        "source_id": getattr(clip, "source_id", None),
        "in_t": getattr(clip, "in_t", 0.0),
        "out_t": getattr(clip, "out_t", 0.0),
        "start_t": getattr(clip, "start_t", 0.0),
    }
    for key in CLIP_KEYS:
        if key == "keyframes" or not hasattr(clip, key):
            continue
        data[key] = copy.deepcopy(getattr(clip, key))
    for key in ("fade_top_offset", "fade_bottom_offset", "fade_left_offset", "fade_right_offset", "opacity"):
        if hasattr(clip, key) and key not in data:
            data[key] = copy.deepcopy(getattr(clip, key))
    # Q173：没调过色的片段没有 grade 属性 —— 也要记一个「没有」，撤销批量调色才能回到没调色
    if "grade" not in data:
        data["grade"] = None
    # Q177：没改过不透明度的片段没有 opacity 属性 —— 记默认 255，撤销批量改不透明度才回得去
    if "opacity" not in data:
        data["opacity"] = 255
    return data


def _apply_clip_record(clip, data) -> None:
    for key, val in (data or {}).items():
        setattr(clip, key, copy.deepcopy(val))


FX_FIELDS = ("keyer", "masks", "ai_matte", "fx_graph")


def snapshot_fx(task) -> dict:
    """Q171：批量抠像 / 遮罩开关只改片段上这四样 —— 撤销只记它们（不深拷整份时间线，上百条也是毫秒级）。"""
    tl = timeline_of(task)
    rows = []
    for tr in getattr(tl, "tracks", None) or []:
        for c in getattr(tr, "clips", None) or []:
            rows.append((c, {k: copy.deepcopy(getattr(c, k, None)) for k in FX_FIELDS if hasattr(c, k)}))
    return {"kind": "fx", "clips": rows}


def restore(task, snap: dict) -> None:
    if snap.get("kind") == "fx":
        for c, fields in snap.get("clips") or []:
            for k, v in fields.items():
                try:
                    setattr(c, k, copy.deepcopy(v))
                except Exception:
                    pass
        return
    if snap.get("kind") == "split":
        if snap["split"] is _MISSING:
            task.pop("split", None)
        else:
            task["split"] = copy.deepcopy(snap["split"])
        for tr, cell in snap.get("cells") or []:
            tr.split_cell = cell
        return
    if snap["split"] is _MISSING:
        task.pop("split", None)
    else:
        task["split"] = copy.deepcopy(snap["split"])
    if "bg_image" in snap:
        if snap["bg_image"] is _MISSING:
            task.pop("bg_image", None)
        else:
            task["bg_image"] = copy.deepcopy(snap["bg_image"])
    for c, fill in snap.get("bg_fill") or []:
        try:
            c.bg_fill = copy.deepcopy(fill)
        except Exception:
            pass
    for name, rows in snap["lists"].items():
        if rows is _MISSING:
            task.pop(name, None)
            continue
        out = []
        for ref, val in rows:
            ref.clear()
            ref.update(_plain_copy(val))
            out.append(ref)
        task[name] = out
    tl = timeline_of(task)
    if tl is not None:
        tl.tracks[:] = snap["tracks"]
        for tr, clips, props, cell in snap["track_state"]:
            tr.clips = list(clips)
            tr.props = copy.deepcopy(props)
            tr.split_cell = cell
    for c, x, y, sc, kfs in snap.get("clip_geom") or []:
        c.x, c.y, c.scale = x, y, sc
        if kfs is not None or hasattr(c, "keyframes"):
            c.keyframes = copy.deepcopy(kfs)
    for c, fields in snap.get("clip_fields") or []:
        _apply_clip_record(c, fields)
    if "layer_folders" in snap:
        if snap["layer_folders"] is _MISSING:
            task.pop("layer_folders", None)
        else:
            task["layer_folders"] = copy.deepcopy(snap["layer_folders"])


class History:
    """每一次手势（按下 → 松开）一条记录，记录里是受影响那几条视频的「之前」。"""

    def __init__(self, limit: int = 120):
        self.limit = int(limit)
        self._undo: list = []
        self._redo: list = []

    def begin(self, tasks, snap=None) -> list:
        take = snap or snapshot
        seen = set()
        out = []
        for t in tasks:
            if id(t) in seen or not isinstance(t, dict):
                continue
            seen.add(id(t))
            out.append((t, take(t)))
        return out

    @staticmethod
    def cancel(token) -> None:
        for task, snap in token or []:
            restore(task, snap)

    def commit(self, token, label: str = "") -> None:
        if not token:
            return
        self._undo.append((label, token))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def _swap(self, src, dst):
        if not src:
            return None, []
        label, token = src.pop()
        now = []
        for task, snap in token:
            kind = snap.get("kind") if isinstance(snap, dict) else None
            if kind == "split":
                now.append((task, snapshot_split(task)))
            elif kind == "fx":
                now.append((task, snapshot_fx(task)))
            elif kind == "layers":
                now.append((task, snapshot_layers(task)))
            else:
                now.append((task, snapshot(task)))
        for task, snap in token:
            restore(task, snap)
        dst.append((label, now))
        return label, [t for t, _s in token]

    def undo(self):
        return self._swap(self._undo, self._redo)

    def redo(self):
        return self._swap(self._redo, self._undo)
