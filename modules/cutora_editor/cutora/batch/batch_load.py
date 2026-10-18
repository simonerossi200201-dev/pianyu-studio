# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T23 批量加载: pages + frame range + AnimDef + blend + preview + add-text.

Replaces 批量插图. Intro thumbnail / intro-video menu entries are gone:
0–20 frames is the thumbnail effect.
"""
from __future__ import annotations

import copy
import os

TABS = ("image", "video")
DEFAULT_ANIM_IN = "fade"
DEFAULT_ANIM_OUT = "fade"
DEFAULT_BLEND = "normal"
BLENDS = ("normal", "multiply", "screen", "overlay", "additive")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
TIME_UNITS = ("frame", "sec", "min")
FIT_MODES = ("fill", "fit", "source", "stretch")


def allowed_anims(phase: str) -> tuple[str, ...]:
    from cutora.render.textfx.anim import phase_anim_items
    return tuple(v for _lab, v in phase_anim_items(phase))


def normalize_params(raw: dict | None) -> dict:
    p = dict(raw or {})
    tab = p.get("tab") or "image"
    if tab not in TABS:
        tab = "image"
    start = int(p.get("start_frame") or 0)
    end = int(p.get("end_frame") if p.get("end_frame") not in (None, "") else 20)
    if end < start:
        start, end = end, start
    anim_in = str(p.get("anim_in") or DEFAULT_ANIM_IN)
    anim_out = str(p.get("anim_out") or DEFAULT_ANIM_OUT)
    blend = str(p.get("blend") or DEFAULT_BLEND)
    if blend not in BLENDS:
        blend = DEFAULT_BLEND
    in_ok = allowed_anims("in")
    out_ok = allowed_anims("out")
    if anim_in not in in_ok:
        anim_in = DEFAULT_ANIM_IN
    if anim_out not in out_ok:
        anim_out = DEFAULT_ANIM_OUT
    fit = str(p.get("fit_mode") or p.get("fit") or "fill").lower()
    if fit not in FIT_MODES:
        fit = "fill"
    out = {
        "tab": tab,
        "start_frame": max(0, start),
        "end_frame": max(start, end),
        "anim_in": anim_in,
        "anim_out": anim_out,
        "blend": blend,
        "image": p.get("image") or "",
        "video": p.get("video") or "",
        "x": float(p.get("x") or 0),
        "y": float(p.get("y") or 0),
        "scale": float(p.get("scale") if p.get("scale") not in (None, "") else 1.0),
        "fit_mode": fit,
        "head_pad_sec": max(0.0, float(p.get("head_pad_sec") or 0)),
        "tail_pad_sec": max(0.0, float(p.get("tail_pad_sec") or 0)),
        "text": p.get("text") or "",
        "text_preset": p.get("text_preset") or "",
        "platform_copies": p.get("platform_copies") or {},
    }
    if p.get("start_sec") not in (None, ""):
        out["start_sec"] = float(p.get("start_sec"))
    if p.get("end_sec") not in (None, ""):
        out["end_sec"] = float(p.get("end_sec"))
    return out


def frames_to_sec(start_frame: int, end_frame: int, fps: float = 30.0) -> tuple[float, float]:
    rate = max(1.0, float(fps))
    return float(start_frame) / rate, float(end_frame) / rate


def _norm_time_unit(unit: str | None) -> str:
    u = str(unit or "frame").strip().lower()
    if u in ("min", "minute", "minutes", "m"):
        return "min"
    if u in ("sec", "second", "seconds", "s"):
        return "sec"
    return "frame"


def sec_from_display(value, unit: str = "frame", fps: float = 30.0) -> float:
    """Display value in 帧/秒/分钟 → storage seconds."""
    v = float(value or 0)
    u = _norm_time_unit(unit)
    rate = max(1.0, float(fps))
    if u == "min":
        return v * 60.0
    if u == "sec":
        return v
    return v / rate


def display_from_sec(sec: float, unit: str = "frame", fps: float = 30.0) -> float:
    """Storage seconds → display value in 帧/秒/分钟."""
    s = float(sec or 0)
    u = _norm_time_unit(unit)
    rate = max(1.0, float(fps))
    if u == "min":
        return s / 60.0
    if u == "sec":
        return s
    return s * rate


def compute_fit_rect(src_w, src_h, dst_w, dst_h, fit_mode="fill",
                     scale=1.0, offset_x=0.0, offset_y=0.0):
    """Image dest rect in canvas pixels. Distorts only when fit_mode=stretch."""
    src_w = float(src_w or 0)
    src_h = float(src_h or 0)
    dst_w = float(dst_w or 0)
    dst_h = float(dst_h or 0)
    scale = float(scale if scale not in (None, "") else 1.0)
    if scale <= 0:
        scale = 1.0
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return 0.0, 0.0, max(0.0, dst_w), max(0.0, dst_h)
    fit_mode = (fit_mode or "fill").lower()
    if fit_mode == "source":
        tw = src_w * scale
        th = src_h * scale
    elif fit_mode == "stretch":
        tw = dst_w * scale
        th = dst_h * scale
    elif fit_mode == "fill":
        ratio = max(dst_w / src_w, dst_h / src_h)
        tw = src_w * ratio * scale
        th = src_h * ratio * scale
    else:
        ratio = min(dst_w / src_w, dst_h / src_h)
        tw = src_w * ratio * scale
        th = src_h * ratio * scale
    x = (dst_w - tw) / 2.0 + float(offset_x or 0)
    y = (dst_h - th) / 2.0 + float(offset_y or 0)
    return x, y, tw, th


def placed_offset(el_x, el_y, el_w, el_h, src_w, src_h, dst_w, dst_h,
                  fit_mode="fill"):
    """Inverse of compute_fit_rect. Element box → offset x/y and one scale.

    Scale is the uniform factor on the scale=1 box. A corner drag that keeps
    aspect (the default) round-trips. Offset is from the centered position,
    the same numbers the batch dialog stores.
    """
    _x, _y, bw, _bh = compute_fit_rect(
        src_w, src_h, dst_w, dst_h, fit_mode, 1.0, 0.0, 0.0)
    bw = float(bw)
    scale = float(el_w) / bw if bw > 1e-6 else 1.0
    if scale <= 0:
        scale = 1.0
    ox = float(el_x) - (float(dst_w) - float(el_w)) / 2.0
    oy = float(el_y) - (float(dst_h) - float(el_h)) / 2.0
    return ox, oy, scale


def list_image_paths(folder: str, limit: int = 500, exts=None) -> list[str]:
    from cutora.core.log import get_logger
    log = get_logger("cutora.batch.batch_load")
    folder = (folder or "").strip()
    if not folder:
        return []
    if not os.path.isdir(folder):
        log.warning("找图目录不存在: %s", folder)
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        log.exception("无法读取找图目录: %s", folder)
        return []
    allowed = tuple(exts) if exts else IMAGE_EXTS
    out = []
    for name in sorted(names):
        ext = os.path.splitext(name)[1].lower()
        if ext not in allowed:
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        out.append(path)
        if len(out) >= int(limit):
            log.warning("找图目录图片超过 %d，已截断: %s", int(limit), folder)
            break
    return out


def drag_preview(params: dict, dx_px: float, dy_px: float,
                 canvas_w: int = 1080, canvas_h: int = 1920,
                 view_w: int = 360, view_h: int = 640) -> dict:
    """Map preview-widget drag (pixels) onto canvas x/y."""
    p = dict(params)
    sx = float(canvas_w) / max(1.0, float(view_w))
    sy = float(canvas_h) / max(1.0, float(view_h))
    p["x"] = float(p.get("x") or 0) + float(dx_px) * sx
    p["y"] = float(p.get("y") or 0) + float(dy_px) * sy
    return p


def scale_preview(params: dict, factor: float) -> dict:
    p = dict(params)
    scale = float(p.get("scale") if p.get("scale") not in (None, "") else 1.0)
    p["scale"] = max(0.05, min(5.0, scale * float(factor)))
    return p


def make_text_overlay(preset_name: str | None = None, content: str = "文本",
                      start: float = 0.0, end: float = 1.0,
                      dims: tuple[int, int] = (1080, 1920)) -> dict:
    """Reuse T11 text-track presets. Same defaults as MainWindow.add_text_ov."""
    from cutora.subtitle import presets as preset_manager
    name = preset_name or preset_manager.get_default_text_preset_name()
    base = None
    if name:
        base = preset_manager.apply_text_preset(name)
    if not base or not isinstance(base, dict):
        base = {
            "fontsize": 80, "font_family": "Arimo", "bold": True, "color": "#FFFFFF",
            "bg_enabled": False, "use_stroke": True, "stroke_color": "#000000",
            "stroke_width": 4,
        }
    ov = copy.deepcopy(base)
    tw, th = int(dims[0] or 1080), int(dims[1] or 1920)
    # Q124：框是文字自己的框，不再是整块画布 —— 和 Ctrl+T 同一份几何。
    from cutora.model.factory_defaults import default_text_box
    text = content or ov.get("content") or "文本"
    try:
        em = float(ov.get("fontsize") or 80)
    except (TypeError, ValueError):
        em = 80.0
    x, y, w, h = default_text_box(tw, th, fontsize=em,
                                  lines=str(text).count("\n") + 1)
    ov.update({
        "type": "text",
        "start": float(start),
        "end": float(end),
        "x": x, "y": y, "w": w, "h": h,
        "rotation": ov.get("rotation", 0),
        "opacity": ov.get("opacity", 255),
        "blend_mode": ov.get("blend_mode", "normal"),
        "content_format": ov.get("content_format", "plain"),
        "content": text,
        "lock_aspect": False,
        "aspect_ratio": (float(w) / float(h)) if h else 1.0,
        "fit_mode": "auto_height",
        "_script": "batch_load",
    })
    if not isinstance(ov.get("text_layout"), dict):
        ov["text_layout"] = {
            "left_pad": 0, "right_pad": 0,
            "top_pad": 0, "bottom_pad": 0,
            "line_spacing": 0, "letter_spacing": 0,
            "align": "center", "valign": "top",
        }
    if name:
        ov["_preset_name"] = name
    from cutora.subtitle.text_layer import ensure_overlay_fx
    return ensure_overlay_fx(ov)


def apply_to_task(task: dict, params: dict, fps: float = 30.0) -> dict:
    """Write overlay/clip range + optional text into the task. Does not render."""
    p = normalize_params(params)
    fps = float(fps or task.get("fps") or 30.0)
    rate = max(1.0, fps)
    if "start_sec" in p:
        start_sec = float(p["start_sec"])
    else:
        start_sec = float(p["start_frame"]) / rate
    if "end_sec" in p:
        end_sec = float(p["end_sec"])
    else:
        end_sec = float(p["end_frame"]) / rate
    head = float(p.get("head_pad_sec") or 0)
    tail = float(p.get("tail_pad_sec") or 0)
    vis_start = start_sec + head
    vis_end = max(vis_start, end_sec - tail)
    path = p["image"] if p["tab"] == "image" else p["video"]
    kind = p["tab"]
    loop = False
    if path:
        from cutora.media.animated import classify_animated, import_animated_as_clip
        if classify_animated(path):
            info = import_animated_as_clip(path, fps=fps)
            path = info["path"]
            kind = "video"
            loop = True
    entry = {
        "kind": kind,
        "start_sec": vis_start,
        "end_sec": vis_end,
        "head_pad_sec": head,
        "tail_pad_sec": tail,
        "anim_in": p["anim_in"],
        "anim_out": p["anim_out"],
        "blend": p["blend"],
        "path": path,
        "x": p["x"],
        "y": p["y"],
        "scale": p["scale"],
        "fit_mode": p.get("fit_mode") or "fill",
        "loop": loop,
    }
    bag = list(task.get("batch_load") or [])
    bag.append(entry)
    task["batch_load"] = bag

    dims = task.get("dims") or (1080, 1920)
    if p.get("text") or p.get("text_preset"):
        text_ov = make_text_overlay(
            p.get("text_preset") or None,
            content=p.get("text") or "文本",
            start=vis_start, end=vis_end, dims=tuple(dims),
        )
        texts = list(task.get("text_overlays") or [])
        texts.append(text_ov)
        task["text_overlays"] = texts
        entry["text_preset"] = text_ov.get("_preset_name") or p.get("text_preset")
    return task
