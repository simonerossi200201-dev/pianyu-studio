# -*- coding: utf-8 -*-
"""Rebuild g11-g18 from g01-g07/g09 with style.fx that PATH fx can honor.

Maps fill/stroke/fonts/bg and v1 anim_* / fade_* onto fx.anim when the
name exists in textfx. Does not invent fx fields for v1-only features.
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
GOLDEN = os.path.join(REPO, "tests", "golden")

PAIRS = [
    ("g01_vertical_basic", "g11_vertical_basic_fx"),
    ("g02_vertical_fx", "g12_vertical_fx_path"),
    ("g03_horizontal_it", "g13_horizontal_it_fx"),
    ("g04_stitch", "g14_stitch_fx"),
    ("g05_scatter_insert", "g15_scatter_insert_fx"),
    ("g06_parallel", "g16_parallel_fx"),
    ("g07_cli", "g17_cli_fx"),
    ("g09_linebreak_latin", "g18_linebreak_latin_fx"),
]

# v1 anim_in_type / anim_out_type → fx.anim.in / .out (textfx/anim.py ANIMS)
V1_ANIM_TO_FX = {
    "none": "none",
    "fade": "fade",
    "pop": "pop_in",
    "pop_in": "pop_in",
    "typewriter": "typewriter",
    "scale_in": "scale_in",
    "drop_in": "drop_in",
    "slide": "slide",
    "float_in_up": "float_in_up",
    "float_in_down": "float_in_down",
    "impact_pop": "impact_pop",
    "stomp": "stomp",
    "reveal_wipe": "reveal_wipe",
    "line_draw": "line_draw",
    "karaoke": "karaoke",
    "highlight": "highlight",
    "bullet_reveal": "bullet_reveal",
}


def _num(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def map_anim(style: dict) -> tuple[str, str, float, float, str]:
    v1_in = str(style.get("anim_in_type") or "none")
    v1_out = str(style.get("anim_out_type") or "none")
    in_s = _num(style.get("anim_in_duration"), 0.35)
    out_s = _num(style.get("anim_out_duration"), 0.25)
    easing = str(style.get("anim_in_easing") or "ease_out")
    fx_in = V1_ANIM_TO_FX.get(v1_in)
    fx_out = V1_ANIM_TO_FX.get(v1_out)
    fade_on = bool(style.get("fade_in_out_enabled"))
    fade_s = _num(style.get("fade_in_out_duration"), 0.3)
    # Independent v1 fade stacks on any entrance. PATH fx has one in/out slot.
    # If the entrance name has no fx counterpart, keep the independent fade.
    if fx_in is None and fade_on:
        fx_in = "fade"
        in_s = fade_s
    if fx_out is None and fade_on:
        fx_out = "fade"
        out_s = fade_s
    if fx_in == "none" and fade_on:
        fx_in = "fade"
        in_s = fade_s
    if fx_out == "none" and fade_on:
        fx_out = "fade"
        out_s = fade_s
    return fx_in or "none", fx_out or "none", in_s, out_s, easing


def fx_for_style(style: dict) -> dict:
    from cutora.subtitle.stylepack import default_fx

    fx = default_fx()
    color = str(style.get("color_text") or "#FFFFFF")
    fx["fill"] = {"type": "solid", "colors": [color]}
    fs = _num(style.get("fontsize"), 35) or 35.0
    if style.get("use_stroke"):
        bw = _num(style.get("border_width"), 0)
        fx["stroke"]["width_em"] = max(0.0, bw / fs)
        outline = str(style.get("color_outline") or "")
        if outline and outline.lower() not in ("#000", "#000000", "black"):
            fx["stroke"]["color"] = outline
    else:
        fx["stroke"]["width_em"] = 0
    if style.get("use_box"):
        fx["bg"]["type"] = "box"
        bg = str(style.get("color_bg") or "")
        if bg and bg.lower() not in ("#000", "#000000", "black"):
            fx["bg"]["color"] = bg
        op = _num(style.get("opacity_bg"), 191)
        fx["bg"]["alpha"] = max(0.0, min(1.0, op / 255.0))
    else:
        fx["bg"]["type"] = "none"
    fam = str(style.get("font_family") or "").strip()
    if fam:
        fx["fonts"]["body"] = fam
    blur = _num(style.get("shadow_blur"), 0)
    if blur > 0:
        # v1 is a centered 3x3 stamp (off_step = blur*0.1). PATH fx is gaussian.
        fx["shadow"]["sigma_em"] = max(0.0, blur / fs)
        fx["shadow"]["dx_em"] = 0.0
        fx["shadow"]["dy_em"] = 0.0
        scol = str(style.get("color_shadow") or "")
        if scol:
            fx["shadow"]["color"] = scol
        sop = _num(style.get("opacity_shadow"), 150)
        fx["shadow"]["alpha"] = max(0.0, min(1.0, sop / 255.0))
    fx_in, fx_out, in_s, out_s, easing = map_anim(style)
    fx["anim"]["in"] = fx_in
    fx["anim"]["out"] = fx_out
    fx["anim"]["in_s"] = in_s
    fx["anim"]["out_s"] = out_s
    fx["anim"]["easing"] = easing
    if fx_in == "bullet_reveal":
        fx["anim"]["unit"] = "line"
        fx["anim"]["stagger_s"] = _num(style.get("bullet_stagger"), 0.15)
    words = []
    for rule in style.get("keyword_rules") or []:
        if isinstance(rule, dict) and rule.get("word"):
            words.append(str(rule.get("word")))
    if words:
        fx["emphasis"]["words"] = words
        high = str(style.get("color_high") or "")
        if high:
            fx["emphasis"]["color"] = high
    loop_parts = []
    # v1 current-word fill is solid color_high. colour_highlight is LTR palette
    # (closer to high_grad) and reads as a different colour on these goldens.
    if str(style.get("color_high") or "") and not words:
        fx["emphasis"]["color"] = str(style.get("color_high"))
        loop_parts.append("highlight")
    if style.get("dynamic_box"):
        loop_parts.append("box_highlight")
        box_c = str(style.get("color_high_bg") or "")
        if box_c:
            fx["bg"]["color"] = box_c
        box_op = _num(style.get("opacity_high_bg"), 255)
        fx["bg"]["alpha"] = max(0.0, min(1.0, box_op / 255.0))
        rad = _num(style.get("dynamic_radius"), 0)
        if rad > 0:
            fx["bg"]["radius_em"] = rad / fs
    if loop_parts:
        fx["anim"]["loop"] = "+".join(loop_parts)
    return fx


def inject(obj):
    if isinstance(obj, dict):
        style = obj.get("style")
        if isinstance(style, dict):
            style["fx"] = fx_for_style(style)
        for v in obj.values():
            inject(v)
    elif isinstance(obj, list):
        for x in obj:
            inject(x)


def main() -> int:
    for src_name, dst_name in PAIRS:
        src = os.path.join(GOLDEN, src_name)
        dst = os.path.join(GOLDEN, dst_name)
        os.makedirs(dst, exist_ok=True)
        src_proj = os.path.join(src, "project.asproj.json")
        data = json.loads(open(src_proj, encoding="utf-8").read())
        inject(data)
        dst_proj = os.path.join(dst, "project.asproj.json")
        with open(dst_proj, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        man_src = os.path.join(src, "manifest.json")
        man = json.loads(open(man_src, encoding="utf-8").read())
        man["case"] = dst_name
        man["notes"] = (
            "T11g-1b fx-path mirror of %s. v1 style kept; style.fx filled from "
            "color/stroke/font/bg plus mapped anim_* / fade_*."
            % src_name
        )
        man["mirror_of"] = src_name
        man["path"] = "fx"
        with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(man, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        with open(os.path.join(dst, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# %s\n\n" % dst_name)
            fh.write("Fx-path mirror of `%s` (T11g-1b).\n" % src_name)
            fh.write("`style.fx` includes mapped anim when the v1 name exists in textfx.\n")
        print("built", dst_name, "←", src_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
