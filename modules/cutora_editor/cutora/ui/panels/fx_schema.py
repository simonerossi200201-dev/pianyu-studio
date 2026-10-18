# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fx field paths and the panel widget registry (no Qt).

FX_FIELD_WIDGETS keys must equal schema_paths() plus ``word_mode``.
Adding an FxSpec field without a widget makes the 1d-2 registry test red.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import get_type_hints

from cutora.render.textfx.spec import FxSpec

# dataclass field → JSON path (AnimSpec in/out are Python-reserved).
_JSON_FIELD = {"in_name": "in", "out_name": "out"}
_SKIP_SPEC_FIELDS = frozenset({
    "has_pure_black",
    # Toggles + box live on 跟随单词 / 跟随句子, not the fx_fields registry.
    "follow_word", "follow_word_tint", "follow_word_box",
    "follow_sentence", "box_highlight",
})
# Retired shadow paint inputs: kept on ShadowSpec for old projects, no widgets.
_SKIP_SPEC_PATHS = frozenset({
    "shadow.sigma_em", "shadow.dx_em", "shadow.dy_em", "shadow.alpha",
    "shadow._legacy", "shadow.enabled",
    "stroke.enabled", "glow.enabled", "bg.enabled",
    "line_bar._alpha", "line_bar._use_bg_radius",
    # Kept in sync with colors[0] for old readers; widget is emphasis.colors.
    "emphasis.color",
})

# JSON path → SubtitleStylePanel attribute.
FX_FIELD_WIDGETS: dict[str, str] = {
    "fill.type": "fx_fill_type",
    "fill.colors": "fx_fill_colors",
    "fill.angle": "fx_fill_angle",
    "fill.metal": "fx_fill_metal",
    "stroke.width_em": "fx_stroke_width_em",
    "stroke.color": "fx_stroke_color",
    "stroke.join": "fx_stroke_join",
    "glow.sigma_em": "fx_glow_sigma_em",
    "glow.spread_em": "fx_glow_spread_em",
    "glow.color": "fx_glow_color",
    "glow.strength": "fx_glow_strength",
    "glow.mode": "fx_glow_mode",
    "shadow.light": "fx_shadow_light",
    "shadow.angle_deg": "fx_shadow_angle_deg",
    "shadow.distance_em": "fx_shadow_distance_em",
    "shadow.size_em": "fx_shadow_size_em",
    "shadow.falloff": "fx_shadow_falloff",
    "shadow.color": "fx_shadow_color",
    "shadow.strength": "fx_shadow_strength",
    "extrude.depth_em": "fx_extrude_depth_em",
    "extrude.angle": "fx_extrude_angle",
    "extrude.color": "fx_extrude_color",
    "bg.type": "fx_bg_type",
    "bg.color": "fx_bg_color",
    "bg.alpha": "fx_bg_alpha",
    "bg.radius_em": "fx_bg_radius_em",
    "bg.pad_em": "fx_bg_pad_em",
    "bg.grow_em": "fx_bg_grow_em",
    "bg.size_mode": "fx_bg_size_mode",
    "bg.width_px": "fx_bg_width_px",
    "bg.height_px": "fx_bg_height_px",
    "bg.offset_x": "fx_bg_offset_x",
    "bg.offset_y": "fx_bg_offset_y",
    "bg.hug_pad_em": "fx_bg_hug_pad_em",
    "bg.hug_join_em": "fx_bg_hug_join_em",
    "bg.glass_sigma_em": "fx_bg_glass_sigma_em",
    "bg.glass_border": "fx_bg_glass_border",
    "bg.glass_thickness_em": "fx_bg_glass_thickness_em",
    "bg.glass_refraction": "fx_bg_glass_refraction",
    "bg.glass_bevel": "fx_bg_glass_bevel",
    "bg.glass_sheen": "fx_bg_glass_sheen",
    "bg.glass_light_deg": "fx_bg_glass_light_deg",
    "bg.texture": "fx_bg_texture",
    "bg.texture_tint": "fx_bg_texture_tint",
    "bg.align": "fx_bg_align",
    "bg.line_gap_em": "fx_bg_line_gap_em",
    "bg.grad_type": "fx_bg_grad_type",
    "bg.grad_colors": "fx_bg_grad_colors",
    "bg.grad_angle": "fx_bg_grad_angle",
    "bg.grad_highlight": "fx_bg_grad_highlight",
    "bg.blend": "fx_bg_blend",
    "bg.fade.top": "fx_bg_fade_top",
    "bg.fade.bottom": "fx_bg_fade_bottom",
    "bg.fade.left": "fx_bg_fade_left",
    "bg.fade.right": "fx_bg_fade_right",
    "bg.fade.top_offset": "fx_bg_fade_top_offset",
    "bg.fade.bottom_offset": "fx_bg_fade_bottom_offset",
    "bg.fade.left_offset": "fx_bg_fade_left_offset",
    "bg.fade.right_offset": "fx_bg_fade_right_offset",
    "fonts.body": "fx_fonts_body",
    "fonts.emphasis": "fx_fonts_emphasis",
    "emphasis.auto": "fx_emphasis_auto",
    "emphasis.words": "fx_emphasis_words",
    "emphasis.type": "fx_emphasis_type",
    "emphasis.colors": "fx_emphasis_color",
    "emphasis.angle": "fx_emphasis_angle",
    "emphasis.palette": "fx_emphasis_palette",
    "emphasis.scale": "fx_emphasis_scale",
    "emphasis.font": "fx_emphasis_font",
    "emphasis.anim": "fx_emphasis_anim",
    "emphasis.max_per_cue": "fx_emphasis_max_per_cue",
    "anim.in": "fx_anim_in",
    "anim.loop": "fx_anim_loop",
    "anim.out": "fx_anim_out",
    "anim.unit": "fx_anim_unit",
    "anim.stagger_s": "fx_anim_stagger_s",
    "anim.in_s": "fx_anim_in_s",
    "anim.out_s": "fx_anim_out_s",
    "anim.easing": "fx_anim_easing",
    "anim.seed": "fx_anim_seed",
    "scatter.enabled": "fx_scatter_enabled",
    "scatter.size_jitter": "fx_scatter_size_jitter",
    "scatter.pos_jitter_em": "fx_scatter_pos_jitter_em",
    "scatter.rot_jitter_deg": "fx_scatter_rot_jitter_deg",
    "tracking_em": "fx_tracking_em",
    "letter_case": "fx_letter_case",
    "line_bar.color": "fx_line_bar_color",
    "line_bar.height_em": "fx_line_bar_height_em",
    "line_bar.pad_em": "fx_line_bar_pad_em",
    "line_bar.offset_y_em": "fx_line_bar_offset_y_em",
    "line_bar.radius_em": "fx_line_bar_radius_em",
    "line_bar.glow_em": "fx_line_bar_glow_em",
    "line_bar.style": "fx_line_bar_style",
    "line_bar.anim": "fx_line_bar_anim",
    "word_mode": "sub_word_mode",
}

# Single label source for both subtitle inspector and text overlay panel.
# Values have no trailing colon; InspectForm / Row add none.
FX_LABELS: dict[str, str] = {
    "fill.type": "填充类型",
    "fill.colors": "字色",
    "fill.angle": "渐变角度",
    "fill.metal": "金属预设",
    "stroke.width_em": "描边宽度",
    "stroke.color": "描边颜色",
    "stroke.join": "描边拐角",
    "glow.sigma_em": "发光柔度",
    "glow.spread_em": "发光膨胀 (em)",
    "glow.color": "发光色",
    "glow.strength": "发光强度",
    "glow.mode": "发光模式",
    "shadow.light": "光源类型",
    "shadow.angle_deg": "光源角度",
    "shadow.distance_em": "光源距离",
    "shadow.size_em": "光源大小",
    "shadow.falloff": "投影衰减",
    "shadow.color": "投影色",
    "shadow.strength": "投影浓度",
    "extrude.depth_em": "深度 (em)",
    "extrude.angle": "挤出角度",
    "extrude.color": "挤出色",
    "bg.type": "背景类型",
    "bg.color": "背景色",
    "bg.alpha": "背景透明度",
    "bg.radius_em": "圆角 (em)",
    "bg.pad_em": "内边距 (em)",
    "bg.grow_em": "四边扩展 (em)",
    "bg.size_mode": "尺寸",
    "bg.width_px": "宽",
    "bg.height_px": "高",
    "bg.offset_x": "偏移 X",
    "bg.offset_y": "偏移 Y",
    "bg.hug_pad_em": "扩展 / 收缩",
    "bg.hug_join_em": "连字",
    "bg.glass_sigma_em": "毛玻璃模糊强度",
    "bg.glass_border": "玻璃反光边",
    "bg.glass_thickness_em": "玻璃厚度",
    "bg.glass_refraction": "边缘折射",
    "bg.glass_bevel": "倒角明暗",
    "bg.glass_sheen": "顶面光泽",
    "bg.glass_light_deg": "光线方向",
    "bg.texture": "纹理",
    "bg.texture_tint": "纹理着色",
    "bg.align": "气泡对齐",
    "bg.line_gap_em": "气泡行距 (em)",
    "bg.grad_type": "色块渐变",
    "bg.grad_colors": "渐变色标",
    "bg.grad_angle": "色块渐变角度",
    "bg.grad_highlight": "渐变高光",
    "bg.blend": "叠加模式",
    "bg.fade.top": "背景框上渐隐",
    "bg.fade.bottom": "背景框下渐隐",
    "bg.fade.left": "背景框左渐隐",
    "bg.fade.right": "背景框右渐隐",
    "bg.fade.top_offset": "背景框上偏移",
    "bg.fade.bottom_offset": "背景框下偏移",
    "bg.fade.left_offset": "背景框左偏移",
    "bg.fade.right_offset": "背景框右偏移",
    "fonts.body": "正文字体",
    "fonts.emphasis": "强调字体",
    "emphasis.auto": "自动强调",
    "emphasis.words": "强调词表",
    "emphasis.type": "强调类型",
    "emphasis.colors": "强调色",
    "emphasis.angle": "强调角度",
    "emphasis.palette": "强调调色板",
    "emphasis.scale": "强调缩放",
    "emphasis.font": "强调用字",
    "emphasis.anim": "强调动画",
    "emphasis.max_per_cue": "每条上限",
    "anim.in": "入场",
    "anim.loop": "循环",
    "anim.out": "出场",
    "anim.unit": "动画单元",
    "anim.stagger_s": "错峰 (s)",
    "anim.in_s": "入场时长 (s)",
    "anim.out_s": "出场时长 (s)",
    "anim.easing": "缓动",
    "anim.seed": "随机种子",
    "scatter.enabled": "随机大小/位置",
    "scatter.size_jitter": "字号抖动",
    "scatter.pos_jitter_em": "位置抖动 (em)",
    "scatter.rot_jitter_deg": "旋转抖动 (°)",
    "tracking_em": "字距 (em)",
    "letter_case": "大小写",
    "line_bar.color": "底条颜色",
    "line_bar.height_em": "粗细",
    "line_bar.pad_em": "左右扩张",
    "line_bar.offset_y_em": "上下偏移",
    "line_bar.radius_em": "圆角",
    "line_bar.glow_em": "发光",
    "line_bar.style": "线型",
    "line_bar.anim": "动画",
    "word_mode": "每条字幕",
    "follow_word.tint.type": "类型",
    "follow_word.tint.colors": "跟随字色",
    "follow_word.tint.angle": "角度",
    "follow_word.box.color": "底块颜色",
}

# T04f-2 visual effects are enabled. Empty sets keep 1d-2 tests compiling.
T04F2_DISABLED_PATHS = frozenset()
T04F2_DISABLED_ENUM: dict[str, frozenset] = {}

GLASS_SIGMA_LO = 0.2
GLASS_SIGMA_HI = 1.5
GLASS_THICK_HI = 0.5

WORD_MODE_ITEMS = (
    ("整句", ""),
    ("单字蹦（一条一个词）", "single"),
)

FILL_TYPE_ITEMS = (("纯色", "solid"), ("渐变", "gradient"), ("金属", "metal"))
EMPH_TYPE_ITEMS = (("纯色", "solid"), ("渐变", "gradient"))
METAL_ITEMS = (("金", "gold"), ("银", "silver"), ("铬", "chrome"), ("玫瑰金", "rose"))
JOIN_ITEMS = (("圆角", "round"), ("尖角", "miter"))
GLOW_MODE_ITEMS = (("柔光", "soft"), ("霓虹", "neon"))
SHADOW_LIGHT_ITEMS = (("平行光", "parallel"), ("点光", "point"))
BG_TYPE_ITEMS = (
    ("无", "none"), ("色块", "box"), ("毛玻璃", "glass"),
    ("纹理", "texture"), ("气泡", "bubble"),
)
BG_SIZE_MODE_ITEMS = (
    ("贴合文字", "fit_text"),
    ("固定", "fixed"),
    ("轮廓包裹", "hug_glyphs"),
    ("行轮廓", "line_hug"),
)
BG_GRAD_ITEMS = (
    ("无", "none"), ("水平", "linear_h"), ("垂直", "linear_v"),
    ("斜向/旋转", "linear_angle"), ("中心", "center"), ("菲涅尔", "fresnel"),
)
TEXTURE_ITEMS = (("纸", "paper"), ("亚麻", "linen"), ("网格", "grid"))
ALIGN_ITEMS = (
    ("居中", "center"), ("左", "left"), ("右", "right"), ("左右交替", "alternate"),
)
EMPH_FONT_ITEMS = (("强调字体", "emphasis"), ("正文", "body"))
EMPH_ANIM_ITEMS = (("无", "none"), ("划线", "line_draw"), ("弹出", "pop"))
EMPH_AUTO_FLAGS = (
    ("数字", "numbers"), ("全大写", "caps"), ("术语表", "glossary"),
    ("最长实词", "longest"), ("序号/枚举", "list"),
)
UNIT_ITEMS = (
    ("整段", "segment"), ("行", "line"), ("词", "word"), ("字", "char"),
)
LETTER_CASE_ITEMS = (("原样", "none"), ("大写", "upper"))
LINE_BAR_STYLE_ITEMS = (
    ("直线", "solid"),
    ("虚线", "dashed"),
    ("点线", "dotted"),
    ("波浪", "wave"),
    ("笔刷", "brush"),
)
LINE_BAR_ANIM_ITEMS = (
    ("画线", "draw"),
    ("整行", "none"),
)
FX_ANIM_ITEMS = (
    ("无", "none"),
    ("淡入淡出", "fade"),
    ("上浮入", "float_in_up"),
    ("下浮入", "float_in_down"),
    ("缩放入", "scale_in"),
    ("落下", "drop_in"),
    ("弹出", "pop_in"),
    ("冲击弹出", "impact_pop"),
    ("重踏", "stomp"),
    ("擦除揭示", "reveal_wipe"),
    ("大→正常·模糊→清晰", "reveal_zoom_blur"),
    ("逐字翻牌", "flip_clock"),
    ("旋转翻转", "rotate_flip"),
    ("堆叠", "stack"),
    ("字距收拢", "tracking_in"),
    ("描边→填充", "outline_to_fill"),
    ("打字机", "typewriter"),
    ("逐行出现", "bullet_reveal"),
    ("滑入", "slide"),
    ("卡拉OK", "karaoke"),
    ("高亮当前词", "highlight"),
    ("框高亮", "box_highlight"),
    ("跟随句子", "line_bar"),
    ("彩色高亮", "colour_highlight"),
    ("旋转高亮", "rotate_highlight"),
    ("划线强调", "line_draw"),
    ("慢速缩放", "zoom_slow"),
    ("漂浮", "float_loop"),
    ("滚动歌词", "scroll"),
)


def schema_paths(cls=FxSpec, prefix: str = "") -> set[str]:
    """JSON paths for every user-editable FxSpec field (recursive)."""
    out: set[str] = set()
    hints = get_type_hints(cls)
    for item in fields(cls):
        if item.name in _SKIP_SPEC_FIELDS:
            continue
        json_name = _JSON_FIELD.get(item.name, item.name)
        path = "%s.%s" % (prefix, json_name) if prefix else json_name
        if path in _SKIP_SPEC_PATHS:
            continue
        nested = hints.get(item.name, item.type)
        if isinstance(nested, type) and is_dataclass(nested):
            out |= schema_paths(nested, path)
        else:
            out.add(path)
    return out


def registry_expected() -> set[str]:
    return schema_paths() | {"word_mode"}


def json_to_asdict_path(path: str) -> str:
    parts = path.split(".")
    if len(parts) >= 2 and parts[0] == "anim" and parts[1] == "in":
        parts[1] = "in_name"
    elif len(parts) >= 2 and parts[0] == "anim" and parts[1] == "out":
        parts[1] = "out_name"
    return ".".join(parts)


def nested_get(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def nested_set(data: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def glass_sigma_to_pct(sigma: float) -> int:
    span = GLASS_SIGMA_HI - GLASS_SIGMA_LO
    pct = (float(sigma) - GLASS_SIGMA_LO) / span * 100.0
    return max(0, min(100, int(round(pct))))


def pct_to_glass_sigma(pct: int) -> float:
    span = GLASS_SIGMA_HI - GLASS_SIGMA_LO
    val = GLASS_SIGMA_LO + max(0, min(100, int(pct))) / 100.0 * span
    return max(GLASS_SIGMA_LO, min(GLASS_SIGMA_HI, val))


FADE_PATHS = frozenset({
    "bg.fade.top", "bg.fade.bottom", "bg.fade.left", "bg.fade.right",
})
FADE_OFFSET_PATHS = frozenset({
    "bg.fade.top_offset", "bg.fade.bottom_offset",
    "bg.fade.left_offset", "bg.fade.right_offset",
})


def fade_to_pct(value: float) -> int:
    from cutora.render.textfx.fade import FADE_CAP
    hi = int(round(FADE_CAP * 100.0))
    return max(0, min(hi, int(round(float(value) * 100.0))))


def pct_to_fade(pct: int) -> float:
    from cutora.render.textfx.fade import FADE_CAP
    hi = int(round(FADE_CAP * 100.0))
    return max(0.0, min(FADE_CAP, max(0, min(hi, int(pct))) / 100.0))


def fade_offset_to_pct(value: float) -> int:
    from cutora.render.textfx.fade import OFFSET_CAP
    hi = int(round(OFFSET_CAP * 100.0))
    return max(-hi, min(hi, int(round(float(value) * 100.0))))


def pct_to_fade_offset(pct: int) -> float:
    from cutora.render.textfx.fade import OFFSET_CAP
    hi = int(round(OFFSET_CAP * 100.0))
    return max(-OFFSET_CAP, min(OFFSET_CAP, max(-hi, min(hi, int(pct))) / 100.0))


def unit_to_pct(value: float) -> int:
    return max(0, min(100, int(round(float(value) * 100.0))))


def pct_to_unit(pct: int) -> float:
    return max(0.0, min(1.0, max(0, min(100, int(pct))) / 100.0))


THICK_PATHS = frozenset({
    "bg.glass_refraction", "bg.glass_bevel", "bg.glass_sheen",
})


def thick_to_pct(value: float) -> int:
    return max(0, min(100, int(round(float(value) / GLASS_THICK_HI * 100.0))))


def pct_to_thick(pct: int) -> float:
    return max(0.0, min(GLASS_THICK_HI,
                        max(0, min(100, int(pct))) / 100.0 * GLASS_THICK_HI))
