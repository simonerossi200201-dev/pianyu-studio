# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical subtitle style keys. Unique source for panel / pack / export.

Overlay text uses a parallel namespace (color / stroke_width / bg_*).
migrate_subtitle_style() only runs on task.style and subtitle_roles.*.style.
"""
from __future__ import annotations

# Unique, ordered. Dead flat keys omitted: stroke, shadow, shadow_offset.
# Unpainted v1 leftovers live in DEPRECATED_UNPAINTED_KEYS.
CANONICAL_STYLE_KEYS = (
    "font_family", "fontsize", "bold", "italic", "font_style",
    "pos_x", "pos_y",
    "rotation", "opacity_text_global",
    "color_bg",
    "opacity_bg",
    "use_box", "box_padding_x", "box_padding_y", "box_radius",
    "use_underline", "color_underline",
    "high_padding", "high_offset_y",
    "wrap_width_percent", "wrap_lines",
    "line_spacing",
    "bg_padding",
    "anim_in_type", "anim_in_duration", "anim_in_easing",
    "anim_out_type", "anim_out_duration", "anim_out_easing",
    "fade_in_out_enabled", "fade_in_out_duration",
    "tw_revealed_color", "tw_revealed_stroke_color",
    "tw_revealed_opacity", "tw_revealed_stroke_opacity",
    "tw_unrevealed_color", "tw_unrevealed_stroke_color",
    "tw_unrevealed_opacity",
    "char_bounce_height", "char_bounce_stagger",
    "floating_amplitude", "floating_period",
    "bullet_stagger",
    "metronome_bpm",
    "flash_color", "flash_duration",
    "holy_glow_radius", "holy_glow_color", "holy_glow_period",
    "letter_jump_scale", "letter_jump_duration",
    "blur_sharp_max", "blur_sharp_clear_frac",
    "stroke_expand_enabled", "stroke_expand_layers",
    "stroke_expand_step", "stroke_expand_feather",
    "stroke_expand_colors",
    "stroke_expand_layer_widths", "stroke_expand_layer_feathers",
    "metro_read_color", "metro_read_stroke_color", "metro_read_stroke_width",
    "metro_read_opacity", "metro_unread_color", "metro_unread_stroke_color",
    "metro_unread_stroke_width", "metro_unread_opacity",
    "advanced_textbox_enabled", "advanced_textbox_x", "advanced_textbox_y",
    "advanced_textbox_w", "advanced_textbox_h", "advanced_textbox_align",
    "adv_bg_enabled", "adv_bg_offset_x", "adv_bg_offset_y",
    "adv_bg_w", "adv_bg_h", "adv_bg_radius",
    "adv_bg_color", "adv_bg_opacity",
    "adv_bg_gradient_enabled", "adv_bg_gradient_type",
    "adv_bg_gradient_colors", "adv_bg_gradient_highlight",
    "adv_line_spacing", "adv_text_align",
    "adv_stroke_enabled", "adv_stroke_width",
    "adv_stroke_color", "adv_stroke_opacity",
    "spectrum_enabled",
    "spectrum_bands", "spectrum_bar_width", "spectrum_bar_gap",
    "spectrum_max_height", "spectrum_color_bottom", "spectrum_color_top",
    "spectrum_position", "spectrum_shape", "spectrum_dual",
    "spectrum_top_shape", "spectrum_opacity",
    "spectrum_roundness",
    "lyrics_scroll_enabled", "lyrics_scroll_damping", "lyrics_scroll_transition",
    "lyrics_center_scale", "lyrics_edge_feather", "lyrics_alpha_decay",
    "lyrics_line_gap", "lyrics_center_offset", "lyrics_center_color",
    "shadow_enabled", "shadow_opacity",
    "shadow_offset_x", "shadow_offset_y", "shadow_color", "shadow_spread",
    "keyword_rules",
    "word_mode",
    # Q137：断句 / 排版
    "layout_engine", "safe_margin_pct", "max_words_per_line", "pause_break_sec",
)

DEAD_STYLE_KEYS = frozenset({"stroke", "shadow", "shadow_offset"})

# Kept on old project JSON, not painted, not in packs. textfx does not read them.
DEPRECATED_UNPAINTED_KEYS = frozenset({
    "color_text", "color_high",
    "use_stroke", "border_width", "opacity_outline", "color_outline",
    "shadow_blur", "color_shadow", "opacity_shadow",
    "box_blur", "box_transition", "box_transition_enabled", "box_transition_color_to",
    "dynamic_box",
    "adv_bar_enabled", "adv_bar_opacity", "adv_bar_padding", "adv_bar_offset_y",
    "adv_bar_radius", "adv_bar_glow", "adv_bar_style", "adv_bar_border_width",
    "adv_bar_color",
    "color_high_bg", "opacity_high_bg", "dynamic_radius",
    "dyn_box_anim", "dyn_box_anim_overshoot", "dyn_box_anim_duration",
    "dyn_box_style", "dyn_box_stroke_width", "dyn_box_glow_radius",
    "dyn_box_font_enabled", "dyn_box_font_family", "dyn_box_font_size",
    "dyn_box_image_enabled", "dyn_box_images",
    "dyn_box_image_path", "dyn_box_image_scale",
    "dyn_box_image_offset_x", "dyn_box_image_offset_y", "dyn_box_image_blend",
    "bg_gradient_enabled", "bg_gradient_type",
    "bg_gradient_colors", "bg_gradient_highlight",
    "wrap_left", "wrap_right",
})

# Subtitle-only. Overlay dicts keep color / stroke_width / bg_* as their names.
# Alias sources are not canonical; migrate_subtitle_style folds them on read.
STYLE_KEY_ALIASES = {
    "color": "color_text",
    "stroke_width": "border_width",
    "stroke_color": "color_outline",
    "bg_opacity": "opacity_bg",
    "bg_color": "color_bg",
    "bg_enabled": "use_box",
    "bg_radius": "box_radius",
}

# Pack/apply complete set. Derived — not a second handwritten universe.
LIVE_CANONICAL_KEYS = tuple(
    k for k in CANONICAL_STYLE_KEYS if k not in DEPRECATED_UNPAINTED_KEYS
)

# Overlay / text-layer namespace. Parallel to LIVE_CANONICAL_KEYS; not subtitle
# aliases. QW-05 lock: saved preset.style keys == this set (leftover=0, reset=0).
TEXT_LAYER_LAYOUT_KEYS = (
    "anchor", "margin_x", "margin_y", "size_pct", "z",
    # Q136：整个框按画布比例记下来（x/cw, y/ch, w/cw, h/ch），加载出来就是存的那个
    # 成品 —— 位置、折行宽度、高度都一样。老预设没有 box（None）走锚点+边距估算。
    "box", "scale",
)

TEXT_LAYER_CANONICAL_KEYS = (
    "font_family", "font_style", "fontsize", "bold", "italic",
    "color",
    "use_stroke", "stroke_width", "stroke_color",
    "bg_enabled", "bg_fill_type", "bg_grad_dir",
    "bg_color", "bg_color2", "bg_color3", "bg_color4",
    "bg_opacity", "bg_radius", "bg_padding", "bg_padding_x", "bg_padding_y",
    "bg_expand_w", "bg_expand_h",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_blur",
    "shadow_spread", "shadow_offset_x", "shadow_offset_y",
    "opacity", "rotation",
    "text_layout", "blend_mode",
    "anim_in_type", "anim_in_duration", "anim_in_easing",
    "anim_out_type", "anim_out_duration", "anim_out_easing",
    "floating_amplitude", "floating_period",
    "flash_color", "flash_duration",
    "stroke_expand_enabled", "stroke_expand_layers", "stroke_expand_step",
    "stroke_expand_feather", "stroke_expand_colors",
    "stroke_expand_layer_widths", "stroke_expand_layer_feathers",
    "fx", "runs", "writing_mode", "text_orientation", "fit_mode",
)

TEXT_LAYOUT_DEFAULT = {
    "left_pad": 0, "right_pad": 0, "top_pad": 0, "bottom_pad": 0,
    "line_spacing": 0, "letter_spacing": 0, "align": "left",
    # Q124：垂直对齐。老工程没存这个键 → top，和以前的画法一模一样
    "valign": "top",
    "first_indent": 0, "para_spacing": 0,
}

TEXT_VALIGNS = ("top", "middle", "bottom")

TEXT_LAYER_DEFAULTS = {
    "font_family": "Arimo",
    "font_style": "",
    "fontsize": 40,
    "bold": False,
    "italic": False,
    "color": "#FFFFFF",
    "use_stroke": False,
    "stroke_width": 0,
    "stroke_color": "#000000",
    "bg_enabled": False,
    "bg_fill_type": "solid",
    "bg_grad_dir": "top_bottom",
    "bg_color": "#000000",
    "bg_color2": "#000000",
    "bg_color3": "#000000",
    "bg_color4": "#000000",
    "bg_opacity": 0,
    "bg_radius": 0,
    "bg_padding": 0,
    "bg_padding_x": 0,
    "bg_padding_y": 0,
    "bg_expand_w": 0,
    "bg_expand_h": 0,
    "shadow_enabled": False,
    "shadow_color": "#000000",
    "shadow_opacity": 120,
    "shadow_blur": 0,
    "shadow_spread": 0,
    "shadow_offset_x": 4,
    "shadow_offset_y": 4,
    "opacity": 255,
    "rotation": 0,
    "text_layout": dict(TEXT_LAYOUT_DEFAULT),
    "blend_mode": "normal",
    "anim_in_type": "none",
    "anim_in_duration": 0.3,
    "anim_in_easing": "ease_out",
    "anim_out_type": "none",
    "anim_out_duration": 0.3,
    "anim_out_easing": "ease_out",
    "floating_amplitude": 8,
    "floating_period": 2.0,
    "flash_color": "#FFFF00",
    "flash_duration": 0.1,
    "stroke_expand_enabled": False,
    "stroke_expand_layers": 1,
    "stroke_expand_step": 4.0,
    "stroke_expand_feather": 8.0,
    "stroke_expand_colors": [],
    "stroke_expand_layer_widths": [],
    "stroke_expand_layer_feathers": [],
    "fx": {
        "fill": {"type": "solid", "colors": ["#FFFFFF"]},
        "stroke": {"width_em": 0, "color": "#141A26", "join": "round"},
        "bg": {"type": "none"},
        "shadow": {
            "distance_em": 0, "size_em": 0.12,
            "color": "#141A26", "strength": 0.6,
        },
        "tracking_em": 0.0,
    },
    "runs": [],
    "writing_mode": "horizontal",
    "text_orientation": "mixed",
    "fit_mode": "none",
}


def normalize_writing_mode(raw) -> str:
    """horizontal / vertical_rl / vertical_lr. Legacy ``vertical`` → vertical_rl."""
    s = str(raw or "horizontal").strip().lower().replace("-", "_")
    if s in ("vertical", "vertical_rl"):
        return "vertical_rl"
    if s == "vertical_lr":
        return "vertical_lr"
    return "horizontal"


def normalize_text_orientation(raw) -> str:
    """mixed (default) / upright. Anything else folds to mixed."""
    s = str(raw or "mixed").strip().lower()
    if s == "upright":
        return "upright"
    return "mixed"

PACK_STYLE_EXTRAS = frozenset({
    "fx", "geom_user_set", "anchor",
    "offset_x_pct", "offset_y_pct", "fontsize_pct",
    "pos_x_pct", "pos_y_pct", "layout_rules", "preset_name",
})

T11G_PENDING_V1_BG = frozenset({
    "use_box", "box_radius", "opacity_bg",
    "box_padding_x", "box_padding_y", "color_bg",
})

T11G_PENDING_V1_GRADIENT = frozenset({
    "bg_gradient_enabled", "bg_gradient_type",
    "bg_gradient_colors", "bg_gradient_highlight",
})


def aliases_acyclic(mapping: dict | None = None) -> bool:
    mapping = STYLE_KEY_ALIASES if mapping is None else mapping
    for src, dst in mapping.items():
        seen = set()
        cur = dst
        while cur in mapping:
            if cur in seen or cur == src:
                return False
            seen.add(cur)
            cur = mapping[cur]
    return True


def _task_canvas_w(task: dict | None) -> int:
    """Best-effort canvas width for wrap_left/right migration. Default 1080."""
    if not isinstance(task, dict):
        return 1080
    dims = task.get("dims")
    if isinstance(dims, (list, tuple)) and len(dims) >= 1:
        try:
            w = int(dims[0])
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    tl = task.get("timeline")
    if isinstance(tl, dict):
        try:
            w = int(tl.get("width") or 0)
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    return 1080


def _fold_wrap_bounds(style: dict, canvas_w: int | None = None) -> None:
    """wrap_left/right → wrap_width_percent, then drop the old keys."""
    if "wrap_left" not in style and "wrap_right" not in style:
        return
    wrap_left = style.get("wrap_left")
    wrap_right = style.get("wrap_right")
    if (isinstance(wrap_left, (int, float))
            and isinstance(wrap_right, (int, float))
            and float(wrap_right) > float(wrap_left) + 50):
        if style.get("wrap_width_percent") is None:
            try:
                cw = float(canvas_w) if canvas_w else 1080.0
            except (TypeError, ValueError):
                cw = 1080.0
            if cw <= 0:
                cw = 1080.0
            style["wrap_width_percent"] = int(round(
                (float(wrap_right) - float(wrap_left)) / cw * 100.0))
    style.pop("wrap_left", None)
    style.pop("wrap_right", None)


def _migrate_font_family_to_fonts_body(style: dict) -> None:
    """Old projects stored the face on font_family; textfx reads fx.fonts.body first.

    Only fills fonts.body when it is missing/empty. Does not create an fx dict
    on styles that never had one (from_style already falls back to font_family).
    """
    family = str(style.get("font_family") or "").strip()
    if not family:
        return
    fx = style.get("fx")
    if not isinstance(fx, dict):
        return
    fonts = fx.get("fonts")
    if not isinstance(fonts, dict):
        fonts = {}
        fx["fonts"] = fonts
    if not str(fonts.get("body") or "").strip():
        fonts["body"] = family


def migrate_subtitle_style(style: dict | None, canvas_w: int | None = None) -> dict:
    """Fold subtitle aliases into canonical names. Drop dead flat keys.

    Never call this on text_overlays / overlay dicts.
    Canonical already present wins; alias is then removed.
    """
    if not isinstance(style, dict):
        return {}
    pad = style.get("bg_padding")
    for alias, canon in STYLE_KEY_ALIASES.items():
        if alias not in style:
            continue
        if canon not in style or style.get(canon) is None:
            style[canon] = style[alias]
        del style[alias]
    if pad is not None:
        if style.get("box_padding_x") is None:
            style["box_padding_x"] = pad
        if style.get("box_padding_y") is None:
            style["box_padding_y"] = pad
    _migrate_font_family_to_fonts_body(style)
    for dead in DEAD_STYLE_KEYS:
        style.pop(dead, None)
    _fold_wrap_bounds(style, canvas_w)
    for dead in DEPRECATED_UNPAINTED_KEYS:
        style.pop(dead, None)
    if "writing_mode" in style:
        style["writing_mode"] = normalize_writing_mode(style.get("writing_mode"))
    if "text_orientation" in style:
        style["text_orientation"] = normalize_text_orientation(
            style.get("text_orientation"))
    return style


def migrate_task_styles(task: dict | None) -> None:
    if not isinstance(task, dict):
        return
    cw = _task_canvas_w(task)
    style = task.get("style")
    if isinstance(style, dict):
        _fold_wrap_bounds(style, cw)
        migrate_subtitle_style(style)
    roles = task.get("subtitle_roles")
    if isinstance(roles, dict):
        for info in roles.values():
            if isinstance(info, dict) and isinstance(info.get("style"), dict):
                _fold_wrap_bounds(info["style"], cw)
                migrate_subtitle_style(info["style"])
