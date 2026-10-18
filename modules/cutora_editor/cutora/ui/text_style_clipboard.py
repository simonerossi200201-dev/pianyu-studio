# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q128：复制样式 / 粘贴样式（Canva 的 Ctrl+Alt+C / Ctrl+Alt+V）。

「样式」= 字体、字号、粗斜、填充 / 描边 / 阴影 / 底板（fx）、排版
（text_layout 全部键）、书写方向、适配方式、不透明度、混合模式、预设名。

不算样式、粘贴时保留目标自己的：正文 content、几何 x/y/w/h/rotation、
时间 start/end、id / 轨道、关键帧、逐段 runs / 富文本、动画（fx.anim 和
anim_* 平铺键 —— Canva 的动画也是单独一套，样式刷不会把动画刷过去）。

剪贴板是进程内的一份 dict，不进系统剪贴板（系统剪贴板留给正文）。
"""
from __future__ import annotations

import copy

STYLE_KEYS = (
    "font_family", "font_style", "fontsize", "bold", "italic",
    "fx", "text_layout",
    "writing_mode", "text_orientation", "fit_mode",
    "opacity", "blend_mode",
    "_preset_name",
)

# 老式平铺键：目标身上有 fx 时它们不参与渲染，但粘贴时一起带过去，
# 免得目标是老格式、还没迁移时读到源的 fx 却留着自己的老 color。
LEGACY_STYLE_KEYS = (
    "color",
    "use_stroke", "stroke_width", "stroke_color",
    "bg_enabled", "bg_fill_type", "bg_grad_dir",
    "bg_color", "bg_color2", "bg_color3", "bg_color4",
    "bg_opacity", "bg_radius", "bg_padding", "bg_padding_x", "bg_padding_y",
    "bg_expand_w", "bg_expand_h",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_blur",
    "shadow_spread", "shadow_offset_x", "shadow_offset_y",
)

_CLIP: dict | None = None


def copy_style(ov: dict) -> dict:
    """从一条文本层抠出样式（深拷贝，动画剥掉）。不动 *ov*。"""
    if not isinstance(ov, dict):
        return {}
    out: dict = {}
    for key in STYLE_KEYS + LEGACY_STYLE_KEYS:
        if key in ov:
            out[key] = copy.deepcopy(ov[key])
    fx = out.get("fx")
    if isinstance(fx, dict):
        fx.pop("anim", None)
    return out


def paste_style(ov: dict, style: dict) -> bool:
    """把样式盖到 *ov* 上。返回是否改了东西。目标的 fx.anim 原样保留。"""
    if not isinstance(ov, dict) or not isinstance(style, dict) or not style:
        return False
    changed = False
    keep_anim = None
    if isinstance(ov.get("fx"), dict) and isinstance(ov["fx"].get("anim"), dict):
        keep_anim = copy.deepcopy(ov["fx"]["anim"])
    for key, val in style.items():
        if key not in STYLE_KEYS and key not in LEGACY_STYLE_KEYS:
            continue
        new = copy.deepcopy(val)
        if key == "fx" and isinstance(new, dict) and keep_anim is not None:
            new["anim"] = keep_anim
        if ov.get(key) != new:
            ov[key] = new
            changed = True
    if "fx" in style and isinstance(ov.get("fx"), dict) and keep_anim is not None:
        ov["fx"]["anim"] = keep_anim
    return changed


def set_clipboard(style: dict | None) -> None:
    global _CLIP
    _CLIP = copy.deepcopy(style) if style else None


def clipboard() -> dict | None:
    return copy.deepcopy(_CLIP) if _CLIP else None


def has_clipboard() -> bool:
    return bool(_CLIP)


def copy_from(ov: dict) -> dict:
    style = copy_style(ov)
    set_clipboard(style)
    return style


def paste_to(overlays) -> int:
    """把剪贴板样式刷到一组文本层上，返回改动了几条。"""
    style = clipboard()
    if not style:
        return 0
    n = 0
    for ov in overlays or []:
        if isinstance(ov, dict) and str(ov.get("type") or "text") == "text":
            if paste_style(ov, style):
                n += 1
    return n
