# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Text-overlay fx migrate, image-element strip, overlay → paint style."""
from __future__ import annotations

import copy
import math
from typing import Any

from cutora.core.log import get_logger
from cutora.render.textfx.spec import parse_color
from cutora.subtitle.style_schema import (
    TEXT_LAYER_DEFAULTS,
    TEXT_LAYOUT_DEFAULT,
    normalize_text_orientation,
    normalize_writing_mode,
)

logger = get_logger("cutora.subtitle.text_layer")

# Q127：文本层折行行数上限 —— 实际上就是「不限」。字幕才有 2 行 / 3 行的约束。
TEXT_LAYER_MAX_LINES = 400

# Flat overlay keys that fold into fx. Popped by migrate_text_overlay.
TEXT_LAYER_LEGACY_FX_KEYS = (
    "color",
    "use_stroke", "stroke_width", "stroke_color",
    "bg_enabled", "bg_fill_type", "bg_grad_dir",
    "bg_color", "bg_color2", "bg_color3", "bg_color4",
    "bg_opacity", "bg_radius", "bg_padding", "bg_padding_x", "bg_padding_y",
    "bg_expand_w", "bg_expand_h",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_blur",
    "shadow_spread", "shadow_offset_x", "shadow_offset_y",
)

REMOVED_IMAGE_ELEMENT_MSG = "已移除 %d 个图片元素"

_SHADOW_RETIRED = ("sigma_em", "dx_em", "dy_em", "alpha")
_OV_ANIM_TO_FX = {
    "none": "none",
    "fade": "fade",
    "slide_up": "slide+up",
    "slide_down": "slide+down",
    "slide_left": "slide+left",
    "slide_right": "slide+right",
    "pop": "pop_in",
    "typewriter": "typewriter",
    "floating": "float_loop",
    "bullet_reveal": "bullet_reveal",
    "flash_highlight": "colour_highlight",
    "char_bounce": "pop_in",
    "letter_jump": "impact_pop",
    "metronome": "stomp",
    "blur_sharp": "reveal_zoom_blur",
    "holy_glow": "fade",
}
_OV_ANIM_FLAT_KEYS = (
    "anim_in_type", "anim_in_duration", "anim_in_easing",
    "anim_out_type", "anim_out_duration", "anim_out_easing",
    "floating_amplitude", "floating_period",
    "flash_color", "flash_duration",
)


def _em(ov: dict) -> float:
    try:
        return max(8.0, float(ov.get("fontsize") or 40))
    except (TypeError, ValueError):
        return 40.0


_GRAD_DIR_TO_TYPE = {
    "top_bottom": "linear_v",
    "bottom_top": "linear_v",
    "left_right": "linear_h",
    "right_left": "linear_h",
    "tl_br": "linear_h",
    "br_tl": "linear_h",
    "tr_bl": "linear_h",
    "bl_tr": "linear_h",
    "center": "center",
    "top": "linear_v",
    "bottom": "linear_v",
    "left": "linear_h",
    "right": "linear_h",
    "tl": "linear_h",
    "tr": "linear_h",
    "bl": "linear_h",
    "br": "linear_h",
}


def _px(ov: dict, *keys, default=0.0) -> float:
    for key in keys:
        if key not in ov:
            continue
        try:
            return float(ov.get(key) if ov.get(key) is not None else default)
        except (TypeError, ValueError):
            continue
    try:
        return float(default)
    except (TypeError, ValueError):
        return 0.0


def ensure_overlay_fx(ov: dict) -> dict:
    """Q127：新建 / 编辑文本层时保证有 fx，让它从第一帧起就走 textfx 路径。

    以前没预设的新文本要等用户碰一下属性面板（_write_ov_fx）或重开工程
    才迁到 fx，之前走的是 frame_renderer 里另一套排版 —— 同一条文字两种
    折行两种描边。不删旧键（migrate_text_overlay 才删），只补 fx。
    """
    if isinstance(ov, dict) and not isinstance(ov.get("fx"), dict):
        ov["fx"] = overlay_fx_from_legacy(ov)
    return ov


def overlay_fx_from_legacy(ov: dict) -> dict:
    """Build an fx subtree from flat overlay keys. Does not mutate *ov*."""
    em = _em(ov)
    color = parse_color(ov.get("color") or "#FFFFFF", "#FFFFFF")
    fx: dict[str, Any] = {
        "fill": {"type": "solid", "colors": [color], "angle": 90.0},
        "stroke": {"width_em": 0.0, "color": "#141A26", "join": "round"},
        "shadow": {
            "light": "parallel",
            "angle_deg": 45.0,
            "distance_em": 0.0,
            "size_em": 0.12,
            "falloff": 0.0,
            "color": "#141A26",
            "strength": 0.6,
            "enabled": False,
        },
        "bg": {
            "type": "none",
            "color": "#0B1A33",
            "alpha": 0.55,
            "radius_em": 0.4,
            "pad_em": [0.5, 0.25],
            "grad_type": "none",
            "grad_colors": [],
            "grad_angle": 0.0,
            "grad_highlight": False,
            "blend": "normal",
        },
        "tracking_em": 0.0,
    }
    if ov.get("use_stroke"):
        w = _px(ov, "stroke_width") / em
        sc = parse_color(ov.get("stroke_color") or "#141A26", "#141A26")
        fx["stroke"] = {
            "width_em": max(0.0, w),
            "color": sc,
            "join": "round",
        }
    dx = _px(ov, "shadow_offset_x", default=4.0) / em
    dy = _px(ov, "shadow_offset_y", default=4.0) / em
    blur = max(0.0, _px(ov, "shadow_blur") / em)
    spread = max(0.0, _px(ov, "shadow_spread") / em)
    sh_alpha = max(0.0, min(1.0, _px(ov, "shadow_opacity", default=120.0) / 255.0))
    sh_c = parse_color(ov.get("shadow_color") or "#141A26", "#141A26")
    dist = math.hypot(dx, dy)
    ang = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    if dist <= 1e-9:
        ang = 45.0
    if ov.get("shadow_enabled"):
        fx["shadow"] = {
            "light": "parallel",
            "angle_deg": ang,
            "distance_em": dist,
            "size_em": max(blur + spread, 0.0),
            "falloff": 0.0,
            "color": sh_c,
            "strength": sh_alpha,
            "enabled": True,
        }
    lay = ov.get("text_layout") if isinstance(ov.get("text_layout"), dict) else {}
    tracking_px = _px(lay, "letter_spacing")
    fx["tracking_em"] = tracking_px / em
    if not ov.get("bg_enabled"):
        return fx
    bg_c = parse_color(ov.get("bg_color") or "#0B1A33", "#0B1A33")
    alpha = max(0.0, min(1.0, _px(ov, "bg_opacity") / 255.0))
    rad = max(0.0, _px(ov, "bg_radius") / em)
    pad_x = _px(ov, "bg_padding_x", "bg_padding") / em
    pad_y = _px(ov, "bg_padding_y", "bg_padding") / em
    pad_x += _px(ov, "bg_expand_w") / em
    pad_y += _px(ov, "bg_expand_h") / em
    fill_type = str(ov.get("bg_fill_type") or "solid")
    grad_dir = str(ov.get("bg_grad_dir") or "top_bottom")
    bg: dict[str, Any] = {
        "type": "box",
        "color": bg_c,
        "alpha": alpha,
        "radius_em": rad,
        "pad_em": [max(0.0, pad_x), max(0.0, pad_y)],
        "grad_type": "none",
        "grad_colors": [],
        "grad_angle": 0.0,
        "grad_highlight": False,
        "blend": "normal",
    }
    if fill_type and fill_type != "solid":
        cols = []
        for key in ("bg_color", "bg_color2", "bg_color3", "bg_color4"):
            raw = ov.get(key)
            if raw:
                cols.append(parse_color(raw, bg_c))
        if fill_type.startswith("radial"):
            gtype = "center"
        else:
            gtype = _GRAD_DIR_TO_TYPE.get(grad_dir, "linear_v")
        if grad_dir in ("bottom_top", "right_left", "br_tl", "bl_tr"):
            cols = list(reversed(cols))
        if len(cols) >= 2:
            bg["grad_type"] = gtype
            bg["grad_colors"] = cols[:4]
    fx["bg"] = bg
    return fx


def migrate_text_overlay(ov: dict | None) -> dict:
    """Fold flat bg_*/stroke_*/shadow_*/color into fx and pop the old keys.

    Existing fx (including bg.type=none) is kept; only missing branches are
    filled from the legacy keys before they are popped.
    """
    if not isinstance(ov, dict):
        return {}
    derived = overlay_fx_from_legacy(ov)
    fx = ov.get("fx")
    if not isinstance(fx, dict):
        ov["fx"] = derived
    else:
        merged = copy.deepcopy(derived)
        merged.update(copy.deepcopy(fx))
        for branch in ("fill", "stroke", "shadow", "bg"):
            if branch in fx and isinstance(fx[branch], dict):
                merged[branch] = copy.deepcopy(fx[branch])
        ov["fx"] = merged
    for key in TEXT_LAYER_LEGACY_FX_KEYS:
        ov.pop(key, None)
    _promote_shadow(ov.get("fx") if isinstance(ov.get("fx"), dict) else {})
    _migrate_overlay_anim(ov)
    if "writing_mode" in ov:
        from cutora.subtitle.style_schema import normalize_writing_mode
        ov["writing_mode"] = normalize_writing_mode(ov.get("writing_mode"))
    if "text_orientation" in ov:
        from cutora.subtitle.style_schema import normalize_text_orientation
        ov["text_orientation"] = normalize_text_orientation(
            ov.get("text_orientation"))
    return ov


def overlay_fill_color(ov: dict | None, default: str = "#FFFFFF") -> str:
    """Fill hex from fx.fill.colors[0], then leftover ov['color']."""
    ov = ov if isinstance(ov, dict) else {}
    fx = ov.get("fx") if isinstance(ov.get("fx"), dict) else {}
    fill = fx.get("fill") if isinstance(fx.get("fill"), dict) else {}
    colors = fill.get("colors") if isinstance(fill.get("colors"), list) else []
    if colors:
        return parse_color(colors[0], default)
    if ov.get("color"):
        return parse_color(ov.get("color"), default)
    return default


def _promote_shadow(fx: dict) -> None:
    """Drop retired sigma/dx/dy/alpha after copying them into the new model."""
    sh = fx.get("shadow") if isinstance(fx, dict) else None
    if not isinstance(sh, dict):
        return
    try:
        sigma = float(sh.get("sigma_em") or 0)
    except (TypeError, ValueError):
        sigma = 0.0
    try:
        dx = float(sh.get("dx_em") or 0.04)
        dy = float(sh.get("dy_em") or 0.06)
        alpha = float(sh.get("alpha") or 0.6)
    except (TypeError, ValueError):
        dx, dy, alpha = 0.04, 0.06, 0.6
    has_new = any(
        k in sh for k in (
            "light", "angle_deg", "distance_em", "size_em", "falloff",
            "strength", "enabled",
        )
    )
    if not has_new and sigma > 1e-9:
        dist = math.hypot(dx, dy)
        ang = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
        if dist <= 1e-9:
            ang = 45.0
        sh["light"] = "parallel"
        sh["angle_deg"] = ang
        sh["distance_em"] = dist
        sh["size_em"] = sigma
        sh["falloff"] = 0.0
        sh["strength"] = alpha
        sh["enabled"] = True
    for key in _SHADOW_RETIRED:
        sh.pop(key, None)


def _migrate_overlay_anim(ov: dict) -> None:
    """Fold ov_anim_* flats into fx.anim so paint_text_overlay can play them."""
    fx = ov.get("fx")
    if not isinstance(fx, dict):
        fx = {}
        ov["fx"] = fx
    anim = fx.get("anim") if isinstance(fx.get("anim"), dict) else {}
    in_type = str(ov.get("anim_in_type") or "")
    out_type = str(ov.get("anim_out_type") or "")
    mapped_in = _OV_ANIM_TO_FX.get(in_type)
    mapped_out = _OV_ANIM_TO_FX.get(out_type)
    if mapped_in and not anim.get("in") and mapped_in != "float_loop":
        anim["in"] = mapped_in
    if mapped_in == "float_loop" and not anim.get("loop"):
        anim["loop"] = "float_loop"
    if mapped_out and mapped_out not in ("float_loop", "colour_highlight") and not anim.get("out"):
        anim["out"] = mapped_out
    try:
        in_s = float(ov.get("anim_in_duration") or 0)
    except (TypeError, ValueError):
        in_s = 0.0
    try:
        out_s = float(ov.get("anim_out_duration") or 0)
    except (TypeError, ValueError):
        out_s = 0.0
    if in_s > 0 and not anim.get("in_s"):
        anim["in_s"] = max(0.05, min(1.5, in_s))
    if out_s > 0 and not anim.get("out_s"):
        anim["out_s"] = max(0.05, min(1.5, out_s))
    ease = str(ov.get("anim_in_easing") or ov.get("anim_out_easing") or "")
    if ease and not anim.get("easing"):
        anim["easing"] = ease
    if anim:
        fx["anim"] = anim
    for key in _OV_ANIM_FLAT_KEYS:
        ov.pop(key, None)


def _is_sticker(el: dict) -> bool:
    return bool(str(el.get("sticker_id") or "").strip())


def _strip_overlay_elements(ov: dict, keep_media_images: bool = False) -> int:
    """Drop nested composer elements. Stickers (sticker_id) stay.

    keep_media_images: 图片层（image_overlays）里带路径的 image 元素就是这一层的
    素材本身 —— 批量加载、资产库、拖放插进来的图片都是这种结构。它们不是 Q27 要删的
    「文本层里的图片元素」功能，绝不能删。

    2026-09-21 的事故：这里曾对 image_overlays 一视同仁地删 kind=="image"，
    每次打开工程都把用户放的图片清空，时间线上只剩空条；再一保存，硬盘上也没了。
    """
    elems = ov.get("elements")
    if not isinstance(elems, list) or not elems:
        return 0
    kept = []
    n = 0
    for el in elems:
        if not isinstance(el, dict):
            n += 1
            continue
        if _is_sticker(el):
            kept.append(el)
            continue
        kind = str(el.get("kind") or "")
        if (keep_media_images and kind == "image"
                and str(el.get("path") or "").strip()):
            kept.append(el)
            continue
        if kind in ("text", "shape", "image"):
            n += 1
            continue
        kept.append(el)
    if n:
        ov["elements"] = kept
        logger.info("removed %d nested composer elements from overlay", n)
    return n


def strip_image_elements(task: dict | None) -> int:
    """Remove nested composer elements from overlays. Returns count."""
    if not isinstance(task, dict):
        return 0
    n = 0
    for key in ("text_overlays", "image_overlays", "overlays"):
        # 只有文本层要删那个废弃的「图片元素」功能；图片层里的图片是素材本身。
        keep = key != "text_overlays"
        for ov in task.get(key) or []:
            if isinstance(ov, dict):
                n += _strip_overlay_elements(ov, keep_media_images=keep)
    return n


def migrate_task_text_layers(task: dict | None) -> tuple[int, str]:
    """Migrate overlays to fx and strip composer elements.

    Returns (removed_image_element_count, status_message_or_empty).
    Does not run on the golden export path; call from UI open.
    """
    if not isinstance(task, dict):
        return 0, ""
    for ov in task.get("text_overlays") or []:
        if isinstance(ov, dict):
            migrate_text_overlay(ov)
    n = strip_image_elements(task)
    msg = REMOVED_IMAGE_ELEMENT_MSG % n if n else ""
    if msg:
        logger.info(msg)
    return n, msg


def migrate_open_project(data: dict | None) -> tuple[int, str]:
    """UI-open migrate. Sums image-element removals across tasks."""
    if not isinstance(data, dict):
        return 0, ""
    total = 0
    for task in data.get("tasks") or []:
        n, _msg = migrate_task_text_layers(task)
        total += n
    msg = REMOVED_IMAGE_ELEMENT_MSG % total if total else ""
    return total, msg


def overlay_text_layout(ov: dict) -> dict:
    raw = ov.get("text_layout") if isinstance(ov.get("text_layout"), dict) else {}
    out = dict(TEXT_LAYOUT_DEFAULT)
    for key, default in TEXT_LAYOUT_DEFAULT.items():
        if key in raw:
            out[key] = copy.deepcopy(raw[key])
    out.setdefault("first_indent", 0)
    out.setdefault("para_spacing", 0)
    return out


def overlay_to_style(ov: dict, video_w: int, video_h: int) -> dict:
    """Style dict for from_style / layout_event. Does not mutate *ov*."""
    ov = ov if isinstance(ov, dict) else {}
    fx = ov.get("fx") if isinstance(ov.get("fx"), dict) else overlay_fx_from_legacy(ov)
    fx = copy.deepcopy(fx)
    tmp = {"fx": fx}
    for key in _OV_ANIM_FLAT_KEYS:
        if key in ov:
            tmp[key] = ov[key]
    _migrate_overlay_anim(tmp)
    fx = tmp.get("fx") if isinstance(tmp.get("fx"), dict) else fx
    color = overlay_fill_color(ov)
    em = _em(ov)
    x = float(ov.get("x") or 0)
    y = float(ov.get("y") or 0)
    w = float(ov.get("w") or 100)
    h = float(ov.get("h") or 100)
    lay = overlay_text_layout(ov)
    writing = normalize_writing_mode(ov.get("writing_mode"))
    # Q127：框就是排版宽度。用户在 text_layout 里设的左右（竖排：上下）
    # 内边距是框里面的留白，折行宽度得把它们扣掉，否则文字贴着框甚至伸出框。
    wrap_pct = 86.0
    if writing != "horizontal" and video_h > 0:
        inner_h = h - float(lay.get("top_pad") or 0) - float(lay.get("bottom_pad") or 0)
        wrap_pct = max(1.0, min(100.0, (inner_h / float(video_h)) * 100.0))
    elif video_w > 0:
        inner_w = w - float(lay.get("left_pad") or 0) - float(lay.get("right_pad") or 0)
        wrap_pct = max(1.0, min(100.0, (inner_w / float(video_w)) * 100.0))
    style = {
        "font_family": ov.get("font_family") or TEXT_LAYER_DEFAULTS.get("font_family") or "Arimo",
        "font_style": ov.get("font_style") or "",
        "fontsize": em,
        "bold": bool(ov.get("bold", False)),
        "italic": bool(ov.get("italic", False)),
        "pos_x": x + w * 0.5,
        "pos_y": y + h * 0.5,
        "wrap_width_percent": wrap_pct,
        # Q127：以前是 max(8, 换行数+4) —— 超过 8 行的文本第 8 行之后全被
        # 并进末行再缩到 0.5 倍、打省略号，就是用户看到的「文字被裁剪」。
        # 文本层不设行数上限，高度由 auto_height 跟着长。
        "wrap_lines": TEXT_LAYER_MAX_LINES,
        "line_spacing": float(lay.get("line_spacing") or 0),
        "color_text": color,
        "fx": copy.deepcopy(fx),
        "_overlay_box": (x, y, w, h),
        "_text_layout": lay,
        "_first_indent": float(lay.get("first_indent") or 0),
        "_para_spacing": float(lay.get("para_spacing") or 0),
        "writing_mode": writing,
        "text_orientation": normalize_text_orientation(ov.get("text_orientation")),
        "fit_mode": str(ov.get("fit_mode") or "none"),
        "_video_w": int(video_w),
        "_video_h": int(video_h),
    }
    return style


def overlay_event(ov: dict) -> dict:
    text = str(ov.get("content") or ov.get("text") or "")
    start = float(ov.get("start") or 0.0)
    end = float(ov.get("end") or (start + 1.0))
    words = []
    if text:
        parts = text.split(" ")
        step = (end - start) / max(1, len([p for p in parts if p]) or 1)
        t = start
        wid = 0
        for i, part in enumerate(parts):
            token = part if i == len(parts) - 1 else part + " "
            if not token:
                continue
            words.append({
                "word": token.strip() or token,
                "start": t,
                "end": t + step,
                "wid": wid,
            })
            t += step
            wid += 1
    return {
        "text": text,
        "start": start,
        "end": end,
        "id": str(ov.get("id") or "ov"),
        "words": words,
        "runs": list(ov.get("runs") or []),
        "_preserve_newlines": True,
    }


# Q145 文字修复：富文本工具条（选中文字的字体 / 字号）如果作用在**整段**文字上，
# 以前存成一条覆盖全文的 run。排版（折行、适配框、光栅范围）按本层字体 / 字号算，
# run 换字体是画的时候才换 —— 于是折行宽度和真正画出来的宽度对不上，字伸出框、
# 被光栅范围切掉；属性面板上的字体显示的是本层字体（Arimo），改一下字号又回到
# Arimo。整段都一样的格式就是这一层自己的格式：提升成本层属性，run 里删掉。
_PROMOTE_RUN_KEYS = ("font_family", "fontsize", "bold", "italic")


def promote_whole_runs(ov: dict, text: str | None = None) -> bool:
    """把覆盖全文、取值一致的 run 格式（字体 / 字号 / 粗 / 斜）提升成本层属性。

    就地改 *ov*，返回是否改了。字体同时写进 fx.fonts.body（渲染优先读它）。
    只动覆盖全文的键；局部富文本原样保留。"""
    if not isinstance(ov, dict):
        return False
    runs = ov.get("runs")
    if not isinstance(runs, list) or not runs:
        return False
    if text is None:
        text = str(ov.get("content") or ov.get("text") or "")
    n = len(text or "")
    if n <= 0:
        return False
    norm = normalize_runs(runs, text)
    changed = False
    promoted: dict = {}
    for key in _PROMOTE_RUN_KEYS:
        cover = [None] * n
        ok = True
        for item in norm:
            ovr = item["overrides"]
            if key not in ovr:
                continue
            for i in range(item["start"], item["end"]):
                if cover[i] is not None and cover[i] != ovr[key]:
                    ok = False
                    break
                cover[i] = ovr[key]
            if not ok:
                break
        if not ok:
            continue
        vals = {repr(v) for v in cover}
        if len(vals) != 1 or cover[0] is None:
            continue
        val = cover[0]
        if key == "font_family" and not str(val or "").strip():
            continue
        if key == "fontsize":
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
        promoted[key] = val
    if not promoted:
        return False
    for key, val in promoted.items():
        if key == "fontsize":
            try:
                old_fs = float(ov.get("fontsize") or 0)
            except (TypeError, ValueError):
                old_fs = 0.0
            ov["fontsize"] = int(round(val)) if abs(val - round(val)) < 1e-6 else val
            pct = ov.get("fontsize_pct")
            if pct is not None:
                try:
                    if old_fs > 0:
                        ov["fontsize_pct"] = float(pct) * float(val) / old_fs
                except (TypeError, ValueError):
                    pass
        else:
            ov[key] = val
        changed = True
    fam = promoted.get("font_family")
    if fam:
        fx = ov.get("fx")
        if isinstance(fx, dict):
            fonts = fx.get("fonts") if isinstance(fx.get("fonts"), dict) else None
            if fonts is not None:
                old_body = str(fonts.get("body") or "")
                fonts["body"] = fam
                if not fonts.get("emphasis") or str(fonts.get("emphasis")) == old_body:
                    fonts["emphasis"] = fam
    new_runs = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        r2 = dict(item)
        ovr = dict(item.get("overrides") or {}) if isinstance(item.get("overrides"), dict) else {}
        for key in promoted:
            ovr.pop(key, None)
        if not ovr:
            continue
        r2["overrides"] = ovr
        new_runs.append(r2)
    if new_runs:
        ov["runs"] = new_runs
    else:
        ov.pop("runs", None)
    return changed


def normalize_runs(runs: Any, text: str) -> list[dict]:
    """Keep {start,end,overrides} with 0 ≤ start < end ≤ len(text)."""
    n = len(text or "")
    out = []
    if not isinstance(runs, list):
        return out
    for item in runs:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start") or 0)
            end = int(item.get("end") or 0)
        except (TypeError, ValueError):
            continue
        start = max(0, min(n, start))
        end = max(0, min(n, end))
        ov = item.get("overrides") if isinstance(item.get("overrides"), dict) else {}
        if end > start:
            out.append({"start": start, "end": end, "overrides": dict(ov)})
    return out
