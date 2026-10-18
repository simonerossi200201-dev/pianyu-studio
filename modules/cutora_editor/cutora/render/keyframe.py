# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyframe evaluation. Preview and export share value_at — one path per param.

t is seconds from the host object's own start. Missing keyframes return fallback
so old projects keep their static fields byte-for-byte.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from cutora.core.log import get_logger
from cutora.render.anim_engine import EASING_MAP

logger = get_logger("cutora.render.keyframe")

EASE_NAMES = ("linear", "ease_in", "ease_out", "ease_in_out", "hold", "bezier")
DEFAULT_BEZ = (0.32, 0.0, 0.67, 1.0)
_BEZ_Y_MIN, _BEZ_Y_MAX = -2.0, 3.0
HIT_EPS = 1.0 / 60.0

CLIP_VISUAL_PATHS = ("x", "y", "scale", "rotation", "opacity")
CLIP_AUDIO_PATHS = ("gain_db",)
TEXT_PATHS = (
    "opacity",
    "scale",
    "tracking_em",
    "fill.colors[0]",
    "shadow.angle_deg",
    "shadow.distance_em",
)
ALL_PATHS = frozenset(CLIP_VISUAL_PATHS + CLIP_AUDIO_PATHS + TEXT_PATHS)

# 关键帧面板 / 曲线编辑器里每个参数的中文名和单位
PATH_LABELS = {
    "x": ("位置 X", "px"),
    "y": ("位置 Y", "px"),
    "scale": ("缩放", "×"),
    "rotation": ("旋转", "°"),
    "opacity": ("不透明度", ""),
    "gain_db": ("音量", "dB"),
    "tracking_em": ("字距", "em"),
    "fill.colors[0]": ("填充色", ""),
    "shadow.angle_deg": ("阴影角度", "°"),
    "shadow.distance_em": ("阴影距离", "em"),
    "stroke.width_em": ("描边宽度", "em"),
    "glow.strength": ("发光强度", ""),
    "glow.sigma_em": ("发光柔度", "em"),
    "glow.spread_em": ("发光膨胀", "em"),
    "bg_fill.blur": ("背景模糊", ""),
    "bg_fill.dim": ("背景压暗", ""),
    "w": ("框宽", "px"),
    "h": ("框高", "px"),
    # Q175 调整图层
    "xform.x": ("画面位置 X", "px"),
    "xform.y": ("画面位置 Y", "px"),
    "xform.scale": ("画面缩放", "×"),
    "xform.rotation": ("画面旋转", "°"),
    "effect.amount": ("效果强度", ""),
    "effect.amount_lens_blur": ("镜头模糊强度", ""),
    "effect.amount_blur": ("高斯模糊强度", ""),
    "effect.amount_mosaic": ("马赛克大小", ""),
    "effect.mix": ("效果混合", ""),
    "effect.highlights": ("光斑", ""),
    "effect.grade.exposure": ("调色·曝光", ""),
    "effect.grade.contrast": ("调色·对比", ""),
    "effect.grade.saturation": ("调色·饱和", ""),
    "effect.grade.temperature": ("调色·色温", "K"),
    "solid.opacity": ("填充不透明度", ""),
    "solid.radius": ("圆角", ""),
    "solid.border_width": ("边框宽度", "px"),
    "feather": ("边缘羽化", "px"),
}


def path_label(path: str) -> str:
    return PATH_LABELS.get(str(path), (str(path), ""))[0]


def path_unit(path: str) -> str:
    return PATH_LABELS.get(str(path), ("", ""))[1]


def replace_paths(obj, mapping: dict) -> list[str]:
    """把若干参数的点表整段换掉（None / 空表 = 关掉这个参数的关键帧）。

    关键帧面板、曲线编辑器拖完松手时用：一次改好多点，只记一次撤销。
    返回真正改动过的 path。"""
    from cutora.render.kf_edit import sanitize
    kfs = dict(keyframes_of(obj))
    changed = []
    for path, pts in (mapping or {}).items():
        path = str(path)
        clean = sanitize(pts) if pts else []
        if clean:
            if kfs.get(path) != clean:
                kfs[path] = clean
                changed.append(path)
        elif path in kfs:
            kfs.pop(path, None)
            changed.append(path)
    if changed:
        _assign_kfs(obj, kfs)
    return changed


_PATH_TOKEN = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_HEX = re.compile(r"^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8}|[0-9A-Fa-f]{3})$")

_CLIPBOARD: dict[str, Any] | None = None


def keyframes_of(obj) -> dict:
    raw = _raw_kfs(obj)
    return raw if isinstance(raw, dict) else {}


def has_keyframes(obj) -> bool:
    kfs = keyframes_of(obj)
    if not kfs:
        return False
    for pts in kfs.values():
        if pts:
            return True
    return False


def path_enabled(obj, path: str) -> bool:
    pts = keyframes_of(obj).get(str(path))
    return bool(pts)


def local_time(obj, timeline_t) -> float:
    start = _start_of(obj)
    return float(timeline_t) - start


def locate_overlay(task, host) -> dict | None:
    """Keyframe command fields for an overlay dict, or an element inside one.

    Identity match. Returns None when *host* is not in text_overlays or
    image_overlays (including their elements lists).
    """
    if not isinstance(task, dict) or not isinstance(host, dict):
        return None
    for list_name in ("text_overlays", "image_overlays"):
        ovs = task.get(list_name) or []
        if not isinstance(ovs, list):
            continue
        for i, item in enumerate(ovs):
            if item is host:
                return {"target": "overlay", "list": list_name, "overlay_index": i}
            if not isinstance(item, dict):
                continue
            els = item.get("elements") or []
            if not isinstance(els, list):
                continue
            for ei, el in enumerate(els):
                if el is host:
                    return {
                        "target": "overlay",
                        "list": list_name,
                        "overlay_index": i,
                        "element_index": ei,
                    }
    return None


def value_at(obj, path, t_local, fallback):
    """Unique evaluation. Preview and export both call this."""
    pts = _points_for(obj, path)
    if not pts:
        return fallback
    if len(pts) == 1:
        return _coerce_like(pts[0]["v"], fallback)
    t = float(t_local)
    if t <= float(pts[0]["t"]):
        return _coerce_like(pts[0]["v"], fallback)
    if t >= float(pts[-1]["t"]):
        return _coerce_like(pts[-1]["v"], fallback)
    lo = pts[0]
    hi = pts[-1]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if float(a["t"]) <= t <= float(b["t"]):
            lo, hi = a, b
            break
    t0 = float(lo["t"])
    t1 = float(hi["t"])
    span = t1 - t0
    if span <= 1e-12:
        return _coerce_like(hi["v"], fallback)
    u = (t - t0) / span
    eased = ease_unit(u, lo.get("ease") or "linear", lo.get("bez"))
    return interpolate(lo["v"], hi["v"], eased, fallback)


def ease_unit(u: float, ease, bez=None) -> float:
    u = max(0.0, min(1.0, float(u)))
    name = str(ease or "linear")
    if name == "hold":
        return 0.0 if u < 1.0 else 1.0
    if name == "bezier":
        x1, y1, x2, y2 = _bez_tuple(bez)
        return _cubic_bezier_y(u, x1, y1, x2, y2)
    fn = EASING_MAP.get(name) or EASING_MAP["linear"]
    return float(fn(u))


def interpolate(v0, v1, u: float, fallback=None):
    u = float(u)
    if _is_color(v0) or _is_color(v1) or _is_color(fallback):
        u = max(0.0, min(1.0, u))           # 颜色不过冲
        c0 = _as_hex(v0, fallback if _is_color(fallback) else "#FFFFFF")
        c1 = _as_hex(v1, c0)
        return mix_oklab(c0, c1, u)
    a = _as_float(v0, fallback)
    b = _as_float(v1, a)
    return a + (b - a) * u


def mix_oklab(hex_a: str, hex_b: str, u: float) -> str:
    """Lerp in OKLCH (OKLab polar). Shortest hue. Keeps chroma off the gray axis."""
    u = max(0.0, min(1.0, float(u)))
    L0, C0, h0 = _srgb_to_oklch(hex_a)
    L1, C1, h1 = _srgb_to_oklch(hex_b)
    L = L0 + (L1 - L0) * u
    C = C0 + (C1 - C0) * u
    dh = h1 - h0
    if dh > math.pi:
        dh -= 2.0 * math.pi
    elif dh < -math.pi:
        dh += 2.0 * math.pi
    h = h0 + dh * u
    return _oklch_to_srgb(L, C, h)


def oklab_chroma(hex_color: str) -> float:
    _L, C, _h = _srgb_to_oklch(hex_color)
    return float(C)


def resolve_clip_visual(clip, timeline_t) -> dict:
    """x/y/scale/rotation/opacity at timeline_t. opacity is 0–255."""
    t_local = local_time(clip, timeline_t)
    kfs = keyframes_of(clip)
    out = {
        "x": _static_num(clip, "x", 0.0),
        "y": _static_num(clip, "y", 0.0),
        "scale": _static_num(clip, "scale", 1.0),
        "rotation": _static_num(clip, "rotation", 0.0),
        "opacity": _static_num(clip, "opacity", 255.0),
    }
    if not kfs:
        return out
    for path in CLIP_VISUAL_PATHS:
        if not kfs.get(path):
            continue
        out[path] = float(value_at(clip, path, t_local, out[path]))
    return out


def resolve_gain_db(clip, timeline_t) -> float:
    fb = _static_num(clip, "gain_db", 0.0)
    if not path_enabled(clip, "gain_db"):
        return fb
    return float(value_at(clip, "gain_db", local_time(clip, timeline_t), fb))


def bake_host(obj, t_local):
    """Copy of a dict host with keyframed fx/overlay fields written in.

    Returns the original object when there are no keyframes (legacy path).
    """
    if not isinstance(obj, dict) or not has_keyframes(obj):
        return obj
    out = copy.deepcopy(obj)
    # Q152-A：打了关键帧的参数全部烘进副本（以前只认 TEXT_PATHS + CLIP_VISUAL_PATHS 这十个，
    # 别的路径存得进去却画不出来）。固定顺序先走老的十个，结果和以前一样。
    order = list(TEXT_PATHS + CLIP_VISUAL_PATHS)
    order += sorted(p for p in keyframes_of(out) if p not in order)
    seen = set()
    for path in order:
        if path in seen or not path_enabled(out, path):
            continue
        seen.add(path)
        fb = read_path(out, path)
        out_v = value_at(out, path, t_local, fb)
        write_path(out, path, out_v)
    return out


def ffmpeg_gain_filter(clip, static_gain_db) -> str | None:
    """ffmpeg volume fragment when gain_db is keyframed; None → use the old static path."""
    if clip is None or not path_enabled(clip, "gain_db"):
        return None
    expr = _gain_volume_expr(clip)
    if not expr:
        return None
    return ",volume=volume='%s':eval=frame" % expr


def read_path(obj, path: str):
    for candidate in _path_candidates(path):
        found, val = _walk(obj, candidate)
        if found:
            return val
    return None


def write_path(obj, path: str, value) -> None:
    if obj is None:
        return
    # Prefer an existing fx subtree for fill/shadow/tracking.
    if _is_fx_path(path):
        fx = _fx_dict(obj, create=True)
        if fx is not None and fx is not obj:
            _write_walk(fx, path, value)
            return
    _write_walk(obj, path, value)


def set_point(obj, path: str, t: float, v, ease: str = "linear", bez=None) -> dict:
    path = str(path)
    t = max(0.0, float(t))
    ease = _norm_ease(ease)
    kfs = _ensure_kfs(obj)
    pts = list(kfs.get(path) or [])
    hit = None
    for p in pts:
        if abs(float(p.get("t") or 0.0) - t) <= HIT_EPS:
            hit = p
            break
    rec = {"t": t, "v": _store_v(v), "ease": ease}
    if ease == "bezier":
        rec["bez"] = list(_bez_tuple(bez if bez is not None else (hit or {}).get("bez")))
    if hit is not None:
        hit.clear()
        hit.update(rec)
    else:
        pts.append(rec)
    pts.sort(key=lambda p: float(p.get("t") or 0.0))
    kfs[path] = pts
    _assign_kfs(obj, kfs)
    return rec


def delete_point(obj, path: str, t: float) -> bool:
    kfs = keyframes_of(obj)
    pts = list(kfs.get(str(path)) or [])
    if not pts:
        return False
    keep = [p for p in pts if abs(float(p.get("t") or 0.0) - float(t)) > HIT_EPS]
    if len(keep) == len(pts):
        return False
    if keep:
        kfs[str(path)] = keep
    else:
        kfs.pop(str(path), None)
    _assign_kfs(obj, kfs)
    return True


def move_point(obj, path: str, t_from: float, t_to: float) -> bool:
    kfs = keyframes_of(obj)
    pts = list(kfs.get(str(path)) or [])
    hit = None
    for p in pts:
        if abs(float(p.get("t") or 0.0) - float(t_from)) <= HIT_EPS:
            hit = p
            break
    if hit is None:
        return False
    t_to = max(0.0, float(t_to))
    # Drop a point already at the destination so we don't stack two ts.
    pts = [
        p for p in pts
        if p is hit or abs(float(p.get("t") or 0.0) - t_to) > HIT_EPS
    ]
    hit["t"] = t_to
    pts.sort(key=lambda p: float(p.get("t") or 0.0))
    kfs[str(path)] = pts
    _assign_kfs(obj, kfs)
    return True


def set_interp(obj, path: str, t: float, ease: str, bez=None) -> bool:
    kfs = keyframes_of(obj)
    pts = list(kfs.get(str(path)) or [])
    for p in pts:
        if abs(float(p.get("t") or 0.0) - float(t)) <= HIT_EPS:
            p["ease"] = _norm_ease(ease)
            if p["ease"] == "bezier":
                p["bez"] = list(_bez_tuple(bez if bez is not None else p.get("bez")))
            else:
                p.pop("bez", None)
            _assign_kfs(obj, kfs)
            return True
    return False


def enable_path(obj, path: str, t: float, v) -> dict:
    if path_enabled(obj, path):
        return {"enabled": True, "path": str(path)}
    set_point(obj, path, t, v, ease="linear")
    return {"enabled": True, "path": str(path)}


def disable_path(obj, path: str) -> dict:
    kfs = keyframes_of(obj)
    if str(path) in kfs:
        kfs.pop(str(path), None)
        _assign_kfs(obj, kfs)
    return {"enabled": False, "path": str(path)}


def hits_at(obj, path: str, t_local: float, eps: float = HIT_EPS) -> bool:
    for p in _points_for(obj, path):
        if abs(float(p.get("t") or 0.0) - float(t_local)) <= float(eps):
            return True
    return False


def all_times(obj) -> list[float]:
    times = []
    for pts in keyframes_of(obj).values():
        for p in pts or []:
            times.append(float(p.get("t") or 0.0))
    times.sort()
    return times


def copy_point(obj, path: str, t: float) -> dict | None:
    global _CLIPBOARD
    for p in _points_for(obj, path):
        if abs(float(p.get("t") or 0.0) - float(t)) <= HIT_EPS:
            _CLIPBOARD = {
                "path": str(path),
                "v": copy.deepcopy(p.get("v")),
                "ease": p.get("ease") or "linear",
                "bez": list(p.get("bez") or []) or None,
            }
            return dict(_CLIPBOARD)
    return None


def clipboard() -> dict | None:
    return None if _CLIPBOARD is None else dict(_CLIPBOARD)


def paste_point(obj, path: str, t: float) -> dict | None:
    clip = clipboard()
    if clip is None:
        return None
    use_path = str(path or clip.get("path") or "")
    if not use_path:
        return None
    return set_point(
        obj, use_path, t, clip.get("v"),
        ease=str(clip.get("ease") or "linear"),
        bez=clip.get("bez"),
    )


def split_keyframes(left, right, cut_local, left_dur=None, right_dur=None) -> None:
    """After a cut: shift right-side t by -cut_local, drop out-of-range
    points, and pin value_at(cut) at the cut so the picture stays continuous.
    """
    kfs = keyframes_of(left)
    if not kfs:
        return
    cut_local = max(0.0, float(cut_local))
    left_lim = cut_local if left_dur is None else max(0.0, float(left_dur))
    right_lim = None if right_dur is None else max(0.0, float(right_dur))
    new_left = {}
    new_right = {}
    for path, pts in list(kfs.items()):
        if not pts:
            continue
        fb = read_path(left, path)
        v_cut = value_at(left, path, cut_local, fb)
        left_pts = []
        for p in pts:
            if not isinstance(p, dict):
                continue
            t = float(p.get("t") or 0.0)
            if t <= left_lim + HIT_EPS:
                rec = dict(p)
                rec["t"] = t
                left_pts.append(rec)
        if not any(abs(float(p.get("t") or 0.0) - cut_local) <= HIT_EPS for p in left_pts):
            rec = {"t": cut_local, "v": _store_v(v_cut), "ease": "linear"}
            left_pts.append(rec)
        left_pts.sort(key=lambda p: float(p.get("t") or 0.0))
        if left_pts:
            new_left[str(path)] = left_pts
        right_pts = []
        for p in pts:
            if not isinstance(p, dict):
                continue
            t = float(p.get("t") or 0.0) - cut_local
            if t < -HIT_EPS:
                continue
            if right_lim is not None and t > right_lim + HIT_EPS:
                continue
            rec = dict(p)
            rec["t"] = max(0.0, t)
            right_pts.append(rec)
        if not any(abs(float(p.get("t") or 0.0) - 0.0) <= HIT_EPS for p in right_pts):
            rec = {"t": 0.0, "v": _store_v(v_cut), "ease": "linear"}
            right_pts.append(rec)
        # Collapse two points stacked at t=0 (original-at-cut + pin).
        seen0 = False
        collapsed = []
        for p in sorted(right_pts, key=lambda r: float(r.get("t") or 0.0)):
            t = float(p.get("t") or 0.0)
            if t <= HIT_EPS:
                if seen0:
                    continue
                seen0 = True
                p = dict(p)
                p["t"] = 0.0
                p["v"] = _store_v(v_cut)
            collapsed.append(p)
        if collapsed:
            new_right[str(path)] = collapsed
    _assign_kfs(left, new_left)
    _assign_kfs(right, new_right)


def clear_clipboard() -> None:
    global _CLIPBOARD
    _CLIPBOARD = None


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _raw_kfs(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get("keyframes")
    return getattr(obj, "keyframes", None)


def _ensure_kfs(obj) -> dict:
    kfs = _raw_kfs(obj)
    if isinstance(kfs, dict):
        return kfs
    kfs = {}
    _assign_kfs(obj, kfs)
    return kfs


def _assign_kfs(obj, kfs: dict) -> None:
    if obj is None:
        return
    # Drop empty map so serialize skips it (legacy JSON stays clean).
    store = kfs if kfs else None
    if isinstance(obj, dict):
        if store is None:
            obj.pop("keyframes", None)
        else:
            obj["keyframes"] = store
        return
    if store is None:
        if hasattr(obj, "keyframes"):
            try:
                delattr(obj, "keyframes")
            except Exception:
                obj.keyframes = {}
        return
    obj.keyframes = store


def _points_for(obj, path) -> list:
    raw = keyframes_of(obj).get(str(path)) or []
    pts = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item.get("t"))
        except (TypeError, ValueError):
            logger.warning("keyframe t 不是数字 path=%s rec=%r", path, item)
            continue
        pts.append(item)
        item["t"] = t
    pts.sort(key=lambda p: float(p.get("t") or 0.0))
    return pts


def _start_of(obj) -> float:
    if obj is None:
        return 0.0
    if isinstance(obj, dict):
        if obj.get("start") is not None:
            try:
                return float(obj.get("start") or 0.0)
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(obj.get("start_t") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    start = getattr(obj, "start_t", None)
    if start is None:
        start = getattr(obj, "start", 0.0)
    try:
        return float(start or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _static_num(obj, name, default) -> float:
    if isinstance(obj, dict):
        raw = obj.get(name, default)
    else:
        raw = getattr(obj, name, default)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _norm_ease(ease) -> str:
    name = str(ease or "linear")
    if name not in EASE_NAMES:
        logger.warning("未知缓动 %r，回退 linear", ease)
        return "linear"
    return name


def _bez_tuple(bez) -> tuple[float, float, float, float]:
    vals = list(bez or [])
    if len(vals) != 4:
        return DEFAULT_BEZ
    out = []
    for k, x in enumerate(vals):
        try:
            f = float(x)
        except (TypeError, ValueError):
            return DEFAULT_BEZ
        # x 是时间比例，必须在 0–1（时间单调）；y 是值比例，Q152-A 起允许
        # -2–3 表示过冲。老工程里的 y 都在 0–1，求值结果一个字节不变。
        out.append(max(0.0, min(1.0, f)) if k % 2 == 0 else max(_BEZ_Y_MIN, min(_BEZ_Y_MAX, f)))
    return (out[0], out[1], out[2], out[3])


def _store_v(v):
    if _is_color(v):
        return _as_hex(v, "#FFFFFF")
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _coerce_like(v, fallback):
    if _is_color(fallback) or _is_color(v):
        return _as_hex(v, fallback if _is_color(fallback) else "#FFFFFF")
    if isinstance(fallback, int) and not isinstance(fallback, bool):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return fallback
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def _as_float(v, fallback) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(fallback)
        except (TypeError, ValueError):
            return 0.0


def _is_color(v) -> bool:
    if not isinstance(v, str):
        return False
    return bool(_HEX.match(v.strip()))


def _as_hex(v, default: str) -> str:
    if _is_color(v):
        raw = v.strip()
        body = raw[1:]
        if len(body) == 3:
            body = "".join(ch * 2 for ch in body)
        return ("#" + body).upper()
    if _is_color(default):
        return _as_hex(default, "#FFFFFF")
    return "#FFFFFF"


def _is_fx_path(path: str) -> bool:
    head = str(path).split(".", 1)[0].split("[", 1)[0]
    return head in ("fill", "shadow", "tracking_em", "stroke", "glow", "bg")


def _path_candidates(path: str) -> list[str]:
    path = str(path)
    out = [path]
    if not path.startswith("fx.") and _is_fx_path(path):
        out.append("fx." + path)
    return out


def _fx_dict(obj, create=False):
    if obj is None:
        return None
    if isinstance(obj, dict):
        fx = obj.get("fx")
        if isinstance(fx, dict):
            return fx
        if create:
            fx = {}
            obj["fx"] = fx
            return fx
        return obj
    return None


def _walk(root, path: str):
    cur = root
    tokens = list(_PATH_TOKEN.finditer(path))
    if not tokens:
        return False, None
    for tok in tokens:
        name, idx = tok.group(1), tok.group(2)
        if name:
            if isinstance(cur, dict):
                if name not in cur:
                    return False, None
                cur = cur[name]
            else:
                if not hasattr(cur, name):
                    return False, None
                cur = getattr(cur, name)
        else:
            i = int(idx)
            if not isinstance(cur, (list, tuple)) or i < 0 or i >= len(cur):
                return False, None
            cur = cur[i]
    return True, cur


def _write_walk(root, path: str, value) -> None:
    tokens = list(_PATH_TOKEN.finditer(str(path)))
    if not tokens:
        return
    cur = root
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        name, idx = tok.group(1), tok.group(2)
        if name:
            if last:
                if isinstance(cur, dict):
                    cur[name] = value
                else:
                    setattr(cur, name, value)
                return
            nxt = cur.get(name) if isinstance(cur, dict) else getattr(cur, name, None)
            if nxt is None or not isinstance(nxt, (dict, list)):
                # Look ahead: next token index → list, else dict.
                nxt_tok = tokens[i + 1]
                nxt = [] if nxt_tok.group(2) is not None else {}
                if isinstance(cur, dict):
                    cur[name] = nxt
                else:
                    setattr(cur, name, nxt)
            cur = nxt
        else:
            iidx = int(idx)
            if not isinstance(cur, list):
                return
            while len(cur) <= iidx:
                cur.append(None)
            if last:
                cur[iidx] = value
                return
            if cur[iidx] is None or not isinstance(cur[iidx], (dict, list)):
                nxt_tok = tokens[i + 1]
                cur[iidx] = [] if nxt_tok.group(2) is not None else {}
            cur = cur[iidx]


def _cubic(t: float, a: float, b: float) -> float:
    omt = 1.0 - t
    return 3.0 * omt * omt * t * a + 3.0 * omt * t * t * b + t * t * t


def _cubic_deriv(t: float, a: float, b: float) -> float:
    # d/dt of 3(1-t)^2 t a + 3(1-t) t^2 b + t^3
    omt = 1.0 - t
    return (
        3.0 * omt * omt * a
        + 6.0 * omt * t * (b - a)
        + 3.0 * t * t * (1.0 - b)
    )


def _cubic_bezier_y(u: float, x1: float, y1: float, x2: float, y2: float) -> float:
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    t = u
    for _ in range(10):
        x = _cubic(t, x1, x2)
        dx = _cubic_deriv(t, x1, x2)
        if abs(dx) < 1e-9:
            break
        t = max(0.0, min(1.0, t - (x - u) / dx))
    # Bisect if Newton drifted.
    if abs(_cubic(t, x1, x2) - u) > 1e-4:
        lo, hi = 0.0, 1.0
        t = u
        for _ in range(20):
            x = _cubic(t, x1, x2)
            if x < u:
                lo = t
            else:
                hi = t
            t = 0.5 * (lo + hi)
    # y1 / y2 在 0–1 时结果天然在 0–1（凸组合），不用夹；超出 = 过冲，要留着
    return _cubic(t, y1, y2)


def _srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, c))
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, c)
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _hex_to_srgb(hex_color: str) -> tuple[float, float, float]:
    h = _as_hex(hex_color, "#FFFFFF").lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b


def _srgb_to_oklab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = _hex_to_srgb(hex_color)
    lr, lg, lb = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    l = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb
    m = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb
    s = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb
    l_ = math.copysign(abs(l) ** (1.0 / 3.0), l)
    m_ = math.copysign(abs(m) ** (1.0 / 3.0), m)
    s_ = math.copysign(abs(s) ** (1.0 / 3.0), s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b2


def _oklab_to_srgb_tuple(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ * l_ * l_
    m = m_ * m_ * m_
    s = s_ * s_ * s_
    lr = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    lb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return (
        max(0.0, min(1.0, _linear_to_srgb(lr))),
        max(0.0, min(1.0, _linear_to_srgb(lg))),
        max(0.0, min(1.0, _linear_to_srgb(lb))),
    )


def _srgb_to_oklch(hex_color: str) -> tuple[float, float, float]:
    L, a, b = _srgb_to_oklab(hex_color)
    C = math.hypot(a, b)
    h = math.atan2(b, a)
    return L, C, h


def _oklch_to_srgb(L: float, C: float, h: float) -> str:
    a = C * math.cos(h)
    b = C * math.sin(h)
    r, g, bl = _oklab_to_srgb_tuple(L, a, b)
    return "#%02X%02X%02X" % (
        int(round(r * 255.0)),
        int(round(g * 255.0)),
        int(round(bl * 255.0)),
    )


def _gain_volume_expr(clip) -> str:
    """ffmpeg volume= eval expression in linear amplitude, t = local seconds."""
    pts = _points_for(clip, "gain_db")
    if not pts:
        return ""
    if len(pts) == 1 or all(str(p.get("ease") or "linear") == "hold" for p in pts[:-1]):
        # stepwise hold
        parts = ["%.8f" % _db_to_lin(pts[-1]["v"])]
        for p in reversed(pts[:-1]):
            parts = [
                "if(lt(t,%.6f),%.8f,%s)" % (
                    float(pts[pts.index(p) + 1]["t"]),
                    _db_to_lin(p["v"]),
                    parts[0],
                )
            ]
        return parts[0]
    linear_all = all(str(p.get("ease") or "linear") == "linear" for p in pts[:-1])
    if linear_all and len(pts) >= 2:
        expr = "%.8f" % _db_to_lin(pts[-1]["v"])
        for i in range(len(pts) - 2, -1, -1):
            a, b = pts[i], pts[i + 1]
            t0, t1 = float(a["t"]), float(b["t"])
            v0, v1 = _db_to_lin(a["v"]), _db_to_lin(b["v"])
            span = max(1e-9, t1 - t0)
            lerp = "%.8f+(%.8f-(%.8f))*clip((t-(%.6f))/(%.6f),0,1)" % (
                v0, v1, v0, t0, span)
            if i == 0 and i == len(pts) - 2:
                expr = lerp
            else:
                expr = "if(lt(t,%.6f),%s,%s)" % (t1, lerp, expr)
        return expr
    # Eased: sample value_at every 50 ms.
    t0 = float(pts[0]["t"])
    t1 = float(pts[-1]["t"])
    n = max(2, int(math.ceil((t1 - t0) / 0.05)) + 1)
    samples = []
    fb = pts[0]["v"]
    for i in range(n):
        tt = t0 + (t1 - t0) * i / (n - 1)
        db = value_at(clip, "gain_db", tt, fb)
        samples.append((tt, _db_to_lin(db)))
    expr = "%.8f" % samples[-1][1]
    for i in range(len(samples) - 2, -1, -1):
        ta, va = samples[i]
        tb, vb = samples[i + 1]
        span = max(1e-9, tb - ta)
        lerp = "%.8f+(%.8f-(%.8f))*clip((t-(%.6f))/(%.6f),0,1)" % (
            va, vb, va, ta, span)
        expr = "if(lt(t,%.6f),%s,%s)" % (tb, lerp, expr)
    return expr


def _db_to_lin(db) -> float:
    try:
        return 10.0 ** (float(db) / 20.0)
    except (TypeError, ValueError):
        return 1.0
