# -*- coding: utf-8 -*-
"""T04f-6a: write the 14 builtin v2 style packs + gallery preview.png."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = os.path.join(REPO, "resources", "stylepacks")
REF = 1080


def fs_pct(px: int) -> float:
    return round(float(px) / float(REF), 6)


def style_base(font, px, color_text, **extra):
    high = extra.pop("color_high", color_text)
    out = {
        "font_family": font,
        "fontsize": int(px),
        "bold": extra.pop("bold", True),
        "italic": extra.pop("italic", False),
        "pos_x": 540,
        "pos_y": 1114,
        "anchor": "bottom-center",
        "offset_x_pct": 0.0,
        "offset_y_pct": -0.42,
        "fontsize_pct": fs_pct(px),
        "rotation": 0,
        "color_text": color_text,
        "color_high": high,
        "color_bg": extra.pop("color_bg", "#0B1A33"),
        "opacity_bg": extra.pop("opacity_bg", 180),
        "use_box": extra.pop("use_box", False),
        "box_padding_x": extra.pop("box_padding_x", 20),
        "box_padding_y": extra.pop("box_padding_y", 8),
        "box_radius": extra.pop("box_radius", 8),
        "use_stroke": extra.pop("use_stroke", False),
        "border_width": extra.pop("border_width", 2),
        "opacity_outline": extra.pop("opacity_outline", 255),
        "color_outline": extra.pop("color_outline", "#141A26"),
        "wrap_lines": 2,
        "line_spacing": extra.pop("line_spacing", 4),
        "dynamic_box": False,
    }
    out.update(extra)
    return out


def meta(name, sample, tags, desc):
    return {
        "name": name,
        "tags": tags,
        "author": "CUTORA",
        "license": "OFL-1.1",
        "aspect": ["9:16", "16:9"],
        "sample": sample,
        "description_zh": desc,
    }


def layout(cap=32, max_lines=2):
    return {
        "max_chars_per_line": cap,
        "max_lines": max_lines,
        "balance_lines": True,
        "line_break": "space",
    }


def anim(in_type="none", loop="none", out="none", unit="segment", in_s=0.35, extra=None):
    d = {
        "in": in_type,
        "loop": loop,
        "out": out,
        "unit": unit,
        "in_s": in_s,
        "seed": 0,
    }
    if extra:
        d.update(extra)
    return d


PACKS_SPEC = [
    ("glass-white", None),  # keep existing, only touch if needed
]


def write_pack(pack_id, name, sample, tags, desc, body, cap=32):
    d = os.path.join(PACKS, pack_id)
    os.makedirs(d, exist_ok=True)
    style = {
        "version": 2,
        "style": body,
        "layout_rules": layout(cap),
        "keyword_rules": body.pop("_keyword_rules", []) if "_keyword_rules" in body else [],
        "animation": {
            "anim_in_type": (body.get("fx") or {}).get("anim", {}).get("in") or "none",
            "anim_out_type": "none",
            "fade_in_out_enabled": False,
        },
    }
    # keyword_rules already extracted
    fx = body.get("fx") or {}
    kr = []
    if pack_id == "amber-karaoke":
        kr = [{"pattern": "\\d+", "color": "#E0BE68"}]
    style["keyword_rules"] = kr
    with open(os.path.join(d, "style.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(style, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta(name, sample, tags, desc), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("wrote", pack_id)


def main() -> int:
    # 1. 极简白 — no karaoke gold
    write_pack(
        "minimal-white", "极简白", "Find its place",
        ["竖屏", "横屏", "极简"], "白字炭蓝描边，无逐词高亮",
        style_base("Arimo", 68, "#F4F7FB", color_high="#F4F7FB",
                   use_stroke=True, border_width=2, color_outline="#141A26",
                   opacity_bg=180, fx={
                       "fill": {"type": "solid", "colors": ["#F4F7FB"]},
                       "stroke": {"width_em": 0.04, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "shadow": {"sigma_em": 0, "color": "#141A26"},
                       "fonts": {"body": "Arimo"},
                       "anim": anim("none"),
                   }),
        cap=34,
    )
    # 2. 字幕条·深蓝
    write_pack(
        "bar-black", "字幕条·深蓝", "Find its place",
        ["竖屏", "横屏", "字幕条"], "深蓝底条，无逐词高亮",
        style_base("Arimo", 64, "#F4F7FB", color_high="#F4F7FB",
                   color_bg="#0B1A33", opacity_bg=220, use_box=True,
                   box_padding_x=28, box_padding_y=10, box_radius=0,
                   use_stroke=False, fx={
                       "fill": {"type": "solid", "colors": ["#F4F7FB"]},
                       "stroke": {"width_em": 0, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "bg": {"type": "box", "color": "#0B1A33", "alpha": 0.86,
                              "radius_em": 0.0, "pad_em": [0.55, 0.28]},
                       "fonts": {"body": "Arimo"},
                       "anim": anim("none"),
                   }),
        cap=32,
    )
    # 3. glass-white: keep fx, drop karaoke gold
    gw = json.load(open(os.path.join(PACKS, "glass-white", "style.json"), encoding="utf-8"))
    gw["style"]["color_high"] = gw["style"]["color_text"]
    json.dump(gw, open(os.path.join(PACKS, "glass-white", "style.json"), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=2)
    open(os.path.join(PACKS, "glass-white", "style.json"), "a", encoding="utf-8").write("\n")
    print("patched glass-white color_high")
    # 4. amber-karaoke — keep highlight
    write_pack(
        "amber-karaoke", "琥珀卡拉OK", "Find its place",
        ["竖屏", "横屏", "卡拉OK"], "琥珀逐词高亮",
        style_base("SN Pro", 72, "#F4EBD8", color_high="#E0BE68",
                   use_stroke=True, border_width=2, color_outline="#3A2A10",
                   fx={
                       "fill": {"type": "gradient", "colors": ["#F4EBD8", "#E0BE68"], "angle": 90},
                       "stroke": {"width_em": 0.04, "color": "#3A2A10"},
                       "glow": {"sigma_em": 0, "color": "#E0BE68"},
                       "fonts": {"body": "SN Pro"},
                       "anim": anim("none", loop="karaoke", unit="word"),
                   }),
        cap=32,
    )
    # 5. chant already v2 no glow — leave body, ensure color_high ok
    # 6. podcast-card
    write_pack(
        "podcast-card", "播客卡片", "Find its place",
        ["竖屏", "横屏", "播客"], "圆角卡片 + 双字体",
        style_base("Arimo", 64, "#EFE7DA", color_high="#EFE7DA",
                   color_bg="#1C1815", opacity_bg=230, use_box=True,
                   box_padding_x=24, box_padding_y=14, box_radius=16,
                   fx={
                       "fill": {"type": "solid", "colors": ["#EFE7DA"]},
                       "stroke": {"width_em": 0, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "bg": {"type": "box", "color": "#1C1815", "alpha": 0.90,
                              "radius_em": 0.40, "pad_em": [0.50, 0.30]},
                       "fonts": {"body": "Arimo", "emphasis": "Caveat"},
                       "emphasis": {"auto": ["numbers"], "color": "#E0BE68",
                                    "scale": 1.15, "font": "emphasis"},
                       "anim": anim("none"),
                   }),
        cap=32,
    )
    write_pack(
        "pop-word", "弹出词", "WOW",
        ["竖屏", "弹出"], "单词随机大小位置弹出",
        style_base("Bangers", 80, "#F4EBD8", color_high="#F4EBD8",
                   use_stroke=True, color_outline="#3A0A14",
                   fx={
                       "fill": {"type": "solid", "colors": ["#F4EBD8"]},
                       "stroke": {"width_em": 0.05, "color": "#3A0A14"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "fonts": {"body": "Bangers"},
                       "anim": anim("impact_pop", unit="word", in_s=0.28),
                       "scatter": {"enabled": True, "size_jitter": 0.28,
                                   "pos_jitter_em": [1.2, 0.6]},
                   }),
        cap=20,
    )
    write_pack(
        "duo-pop", "双词紫青", "yes now",
        ["竖屏", "强调"], "两词交替紫/青弹出",
        style_base("Arimo", 72, "#F4EBD8", color_high="#F4EBD8",
                   fx={
                       "fill": {"type": "solid", "colors": ["#F4EBD8"]},
                       "stroke": {"width_em": 0.03, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "fonts": {"body": "Arimo"},
                       "emphasis": {"auto": ["longest"], "color": "#7C3AED",
                                    "scale": 1.18, "font": "body",
                                    "palette": ["#7C3AED", "#22D3EE"]},
                       "anim": anim("pop_in", unit="word", in_s=0.28),
                   }),
        cap=24,
    )
    write_pack(
        "handwrite-duo", "叙述+手写", "Find its place",
        ["竖屏", "横屏", "手写"], "叙述体 + Caveat 手写强调",
        style_base("Arimo", 68, "#F4EBD8", color_high="#F4EBD8",
                   fx={
                       "fill": {"type": "solid", "colors": ["#F4EBD8"]},
                       "stroke": {"width_em": 0.03, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "fonts": {"body": "Arimo", "emphasis": "Caveat"},
                       "emphasis": {"auto": ["numbers", "caps"], "color": "#C45C26",
                                    "scale": 1.20, "font": "emphasis"},
                       "anim": anim("none"),
                   }),
        cap=32,
    )
    write_pack(
        "neon-cyan", "霓虹·青", "NEON",
        ["竖屏", "霓虹"], "青色霓虹，无外圈分层描边",
        style_base("Bebas Neue", 80, "#E8FFFC", color_high="#E8FFFC",
                   fx={
                       "fill": {"type": "solid", "colors": ["#E8FFFC"]},
                       "stroke": {"width_em": 0.02, "color": "#062E2E"},
                       "glow": {"sigma_em": 0.18, "spread_em": 0.02,
                                "color": "#22D3EE", "strength": 0.85, "mode": "neon"},
                       "fonts": {"body": "Bebas Neue"},
                       "anim": anim("none"),
                   }),
        cap=22,
    )
    write_pack(
        "chunky-3d", "厚字·3D", "BOOM",
        ["竖屏", "3D"], "Anton 厚挤出",
        style_base("Anton", 84, "#F4EBD8", color_high="#F4EBD8",
                   fx={
                       "fill": {"type": "solid", "colors": ["#F4EBD8"]},
                       "stroke": {"width_em": 0.03, "color": "#3A0A14"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "extrude": {"depth_em": 0.14, "angle": 135, "color": "#3A0A14"},
                       "fonts": {"body": "Anton"},
                       "anim": anim("drop_in", unit="segment", in_s=0.32),
                   }),
        cap=18,
    )
    write_pack(
        "chat-bubbles", "聊天气泡", "hey there",
        ["竖屏", "气泡"], "分行圆角气泡左右对齐",
        style_base("Arimo", 60, "#F4F7FB", color_high="#F4F7FB",
                   fx={
                       "fill": {"type": "solid", "colors": ["#F4F7FB"]},
                       "stroke": {"width_em": 0, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "bg": {"type": "bubble", "color": "#0B1A33", "alpha": 0.82,
                              "radius_em": 0.55, "pad_em": [0.45, 0.28],
                              "align": "alternate"},
                       "fonts": {"body": "Arimo"},
                       "anim": anim("none"),
                   }),
        cap=28,
    )
    write_pack(
        "paper-torn", "撕纸·划线", "note this",
        ["竖屏", "纹理"], "纸纹底 + 划线强调",
        style_base("Permanent Marker", 70, "#3A0A14", color_high="#3A0A14",
                   color_bg="#F1E9D8", opacity_bg=230,
                   fx={
                       "fill": {"type": "solid", "colors": ["#3A0A14"]},
                       "stroke": {"width_em": 0, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "bg": {"type": "texture", "texture": "paper",
                              "color": "#F1E9D8", "alpha": 0.92,
                              "radius_em": 0.12, "pad_em": [0.50, 0.32]},
                       "fonts": {"body": "Permanent Marker"},
                       "emphasis": {"auto": ["longest"], "color": "#C45C26",
                                    "scale": 1.05, "font": "body",
                                    "anim": "line_draw"},
                       "anim": anim("none"),
                   }),
        cap=26,
    )
    write_pack(
        "lyrics-scroll", "滚动歌词", "Find its place",
        ["竖屏", "歌词"], "现有滚动字幕作为风格",
        style_base("Arimo", 64, "#FFD700", color_high="#FFD700",
                   fx={
                       "fill": {"type": "solid", "colors": ["#EFE7DA"]},
                       "stroke": {"width_em": 0.03, "color": "#141A26"},
                       "glow": {"sigma_em": 0, "color": "#22D3EE"},
                       "fonts": {"body": "Arimo"},
                       "anim": anim("none", loop="scroll", unit="segment"),
                   }),
        cap=30,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
