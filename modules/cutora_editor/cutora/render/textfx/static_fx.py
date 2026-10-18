# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T04f-2 static-effect contact-sheet cells. One cell per newly enabled control."""
from __future__ import annotations

# Contact-sheet geometry (also imported by tools/fx_contact_sheet.py).
SHEET_CELL_W = 960
SHEET_CELL_H = 540
SHEET_LABEL_H = 40
SHEET_COLS = 4
SHEET_DARK = "#141A26"
SHEET_CAP_RATIO = 0.25

# Paths / enum values that 1d-2 kept disabled; each must appear on the sheet.
T04F2_ENABLED_COVERS = (
    "fill.angle", "fill.metal",
    "fill.type:gradient", "fill.type:metal",
    "glow.mode:neon",
    "extrude.depth_em", "extrude.angle", "extrude.color",
    "bg.type:texture", "bg.type:bubble",
    "bg.texture", "bg.texture_tint", "bg.align", "bg.line_gap_em",
    "bg.glass_thickness_em", "bg.glass_refraction", "bg.glass_bevel",
    "bg.glass_sheen", "bg.glass_light_deg",
    "fonts.emphasis",
)


def _fx(**kwargs) -> dict:
    return dict(kwargs)


# Deep-sheet-only color overrides (rendering defaults stay; the dark ground
# swallows navy/charcoal). Keys are cell ids.
DARK_SHEET_OVERRIDES = {
    "stroke": {"stroke": {"color": "#22D3EE"}},
    "bg-box": {"bg": {"color": "#3A0A14"}},
    "bubble-alt": {"bg": {"color": "#062E2E"}},
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = dict(val) if isinstance(val, dict) else val
    return out


def effect_cells(ground: str = "frame") -> list[dict]:
    """Each dict: id, title, covers (tuple of control ids), fx patch, extra style."""
    gold = _fx(fill={"type": "metal", "metal": "gold", "angle": 100})
    cells = [
        {
            "id": "fill-solid", "title": "填充 · 纯色",
            "covers": (),
            "fx": _fx(fill={"type": "solid", "colors": ["#F4EBD8"]}),
            "sample": "Place",
        },
        {
            "id": "stroke", "title": "描边",
            "covers": (),
            "fx": _fx(
                fill={"type": "solid", "colors": ["#F4EBD8"]},
                stroke={"width_em": 0.08, "color": "#141A26", "join": "round"},
            ),
        },
        {
            "id": "glow-soft", "title": "发光 · 柔光",
            "covers": (),
            "fx": _fx(glow={
                "sigma_em": 0.25, "spread_em": 0.04, "color": "#22D3EE",
                "strength": 1.2, "mode": "soft",
            }),
        },
        {
            "id": "shadow", "title": "投影",
            "covers": (),
            "fx": _fx(shadow={
                "sigma_em": 0.25, "dx_em": 0.10, "dy_em": 0.14,
                "color": "#141A26", "alpha": 0.75,
            }),
        },
        {
            "id": "bg-box", "title": "背景 · 色块",
            "covers": (),
            "fx": _fx(bg={
                "type": "box", "color": "#0B1A33", "alpha": 0.55,
                "radius_em": 0.4, "pad_em": [0.5, 0.28],
            }),
        },
        {
            "id": "bg-glass-thick", "title": "背景 · 厚玻璃",
            "covers": (
                "bg.glass_thickness_em", "bg.glass_refraction",
                "bg.glass_bevel", "bg.glass_sheen", "bg.glass_light_deg",
            ),
            "fx": _fx(bg={
                "type": "glass", "color": "#0B1A33", "alpha": 0.35,
                "radius_em": 0.45, "pad_em": [0.55, 0.32],
                "glass_sigma_em": 0.7, "glass_border": True,
                "glass_thickness_em": 0.14, "glass_refraction": 0.55,
                "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
            }),
        },
        {
            "id": "bg-glass-flat", "title": "背景 · 毛玻璃贴纸",
            "covers": (),
            "fx": _fx(bg={
                "type": "glass", "color": "#0B1A33", "alpha": 0.35,
                "radius_em": 0.45, "pad_em": [0.55, 0.32],
                "glass_sigma_em": 0.7, "glass_border": True,
                "glass_thickness_em": 0,
            }),
        },
        {
            "id": "gradient-h", "title": "渐变 · 水平",
            "covers": ("fill.type:gradient", "fill.angle"),
            "fx": _fx(fill={
                "type": "gradient",
                "colors": ["#22D3EE", "#7C3AED", "#F4EBD8"],
                "angle": 0,
            }),
        },
        {
            "id": "gradient-v", "title": "渐变 · 垂直",
            "covers": ("fill.angle",),
            "fx": _fx(fill={
                "type": "gradient",
                "colors": ["#F5D77A", "#7A3B3B"],
                "angle": 90,
            }),
        },
        {
            "id": "metal-gold", "title": "金属 · 金",
            "covers": ("fill.type:metal", "fill.metal"),
            "fx": gold,
        },
        {
            "id": "metal-silver", "title": "金属 · 银",
            "covers": ("fill.metal",),
            "fx": _fx(fill={"type": "metal", "metal": "silver", "angle": 100}),
        },
        {
            "id": "metal-chrome", "title": "金属 · 铬",
            "covers": ("fill.metal",),
            "fx": _fx(fill={"type": "metal", "metal": "chrome", "angle": 100}),
        },
        {
            "id": "metal-rose", "title": "金属 · 玫瑰金",
            "covers": ("fill.metal",),
            "fx": _fx(fill={"type": "metal", "metal": "rose", "angle": 100}),
        },
        {
            "id": "neon", "title": "霓虹 · 青",
            "covers": ("glow.mode:neon",),
            "fx": _fx(
                fill={"type": "solid", "colors": ["#F4EBD8"]},
                glow={
                    "sigma_em": 0.30, "spread_em": 0.05, "color": "#22D3EE",
                    "strength": 1.2, "mode": "neon",
                },
            ),
        },
        {
            "id": "extrude", "title": "3D 挤出 · Anton",
            "covers": ("extrude.depth_em", "extrude.angle", "extrude.color"),
            "fx": _fx(
                fill={"type": "solid", "colors": ["#F4EBD8"]},
                extrude={"depth_em": 0.18, "angle": 135, "color": "#5A3A14"},
                fonts={"body": "Anton"},
            ),
            "sample": "WOW",
        },
        {
            "id": "texture-paper", "title": "纹理 · 撕纸",
            "covers": ("bg.type:texture", "bg.texture", "bg.texture_tint"),
            "fx": _fx(bg={
                "type": "texture", "texture": "paper",
                "texture_tint": "#F1E9D8", "alpha": 0.92,
                "radius_em": 0.2, "pad_em": [0.45, 0.28],
            }),
        },
        {
            "id": "texture-linen", "title": "纹理 · 亚麻",
            "covers": ("bg.texture",),
            "fx": _fx(bg={
                "type": "texture", "texture": "linen",
                "texture_tint": "#E4D5C0", "alpha": 0.9,
                "radius_em": 0.3, "pad_em": [0.45, 0.28],
            }),
        },
        {
            "id": "texture-grid", "title": "纹理 · 网格",
            "covers": ("bg.texture",),
            "fx": _fx(bg={
                "type": "texture", "texture": "grid",
                "texture_tint": "#D7E2EC", "alpha": 0.88,
                "radius_em": 0.15, "pad_em": [0.45, 0.28],
            }),
        },
        {
            "id": "bubble-alt", "title": "气泡 · 左右交替",
            "covers": ("bg.type:bubble", "bg.align", "bg.line_gap_em"),
            "fx": _fx(bg={
                "type": "bubble", "align": "alternate",
                "color": "#0B1A33", "alpha": 0.72,
                "radius_em": 0.55, "pad_em": [0.45, 0.22],
                "line_gap_em": 0.28,
            }),
            "sample": "Hello\nGo",
        },
        {
            "id": "dual-font", "title": "双字体 · Caveat",
            "covers": ("fonts.emphasis",),
            "fx": _fx(
                fonts={"body": "Arimo", "emphasis": "Caveat"},
                emphasis={
                    "words": ["place"], "color": "#22D3EE",
                    "scale": 1.35, "font": "emphasis",
                },
            ),
            "sample": "Go place",
        },
        {
            "id": "bg-glass-thick_stripes", "title": "厚玻璃 · 斜条折射",
            "covers": (),
            "fx": _fx(bg={
                "type": "glass", "color": "#0B1A33", "alpha": 0.28,
                "radius_em": 0.45, "pad_em": [0.55, 0.32],
                "glass_sigma_em": 0.7, "glass_border": True,
                "glass_thickness_em": 0.14, "glass_refraction": 0.55,
                "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
            }),
            "ground": "stripes",
        },
    ]
    if ground == "dark":
        for cell in cells:
            patch = DARK_SHEET_OVERRIDES.get(cell["id"])
            if patch:
                cell["fx"] = _deep_merge(cell["fx"], patch)
    return cells


def emphasis_cells(ground: str = "frame") -> list[dict]:
    """T04f-3 contact-sheet cells. Kept off the T04f-2 default sheet."""
    cells = [
        {
            "id": "emph-color", "title": "强调 · 单色",
            "covers": ("emphasis.color",),
            "fx": _fx(emphasis={
                "words": ["costs", "office"], "color": "#7C3AED",
                "scale": 1.0, "font": "body", "max_per_cue": 0,
            }),
            "sample": "cut costs office",
        },
        {
            "id": "emph-palette", "title": "强调 · 双色跳字",
            "covers": ("emphasis.palette", "emphasis.max_per_cue"),
            "fx": _fx(emphasis={
                "words": ["cut", "costs", "office"],
                "palette": ["#7C3AED", "#22D3EE"],
                "scale": 1.0, "font": "body", "max_per_cue": 0,
            }),
            "sample": "cut costs office",
        },
        {
            "id": "emph-scale", "title": "强调 · 放大",
            "covers": ("emphasis.scale",),
            "fx": _fx(emphasis={
                "words": ["office"], "color": "#22D3EE",
                "scale": 1.8, "font": "body", "max_per_cue": 0,
            }),
            "sample": "at the office today",
        },
        {
            "id": "emph-dualfont", "title": "强调 · 双字体",
            "covers": ("fonts.emphasis", "emphasis.font"),
            "fx": _fx(
                fonts={"body": "Arimo", "emphasis": "Caveat"},
                emphasis={
                    "words": ["office"], "color": "#22D3EE",
                    "scale": 1.35, "font": "emphasis", "max_per_cue": 0,
                },
            ),
            "sample": "at the office",
        },
        {
            "id": "emph-pop-0", "title": "强调 · pop t0",
            "covers": ("emphasis.anim",),
            "fx": _fx(emphasis={
                "words": ["office"], "color": "#22D3EE",
                "scale": 1.25, "anim": "pop", "max_per_cue": 0,
            }),
            "sample": "the office",
            "t": 0.20,
        },
        {
            "id": "emph-pop-1", "title": "强调 · pop 顶点",
            "covers": (),
            "fx": _fx(emphasis={
                "words": ["office"], "color": "#22D3EE",
                "scale": 1.25, "anim": "pop", "max_per_cue": 0,
            }),
            "sample": "the office",
            "t": 0.31,
        },
        {
            "id": "emph-pop-2", "title": "强调 · pop 回落",
            "covers": (),
            "fx": _fx(emphasis={
                "words": ["office"], "color": "#22D3EE",
                "scale": 1.25, "anim": "pop", "max_per_cue": 0,
            }),
            "sample": "the office",
            "t": 0.48,
        },
        {
            "id": "emph-scatter", "title": "散排 · 开",
            "covers": (
                "scatter.enabled", "scatter.size_jitter",
                "scatter.pos_jitter_em", "scatter.rot_jitter_deg",
            ),
            "fx": _fx(
                scatter={
                    "enabled": True, "size_jitter": 0.22,
                    "pos_jitter_em": [0.8, 0.4], "rot_jitter_deg": 6,
                },
                emphasis={"words": ["office"], "color": "#22D3EE", "scale": 1.15},
            ),
            "sample": "the office",
        },
    ]
    _ = ground
    return cells


def anim_cells(ground: str = "frame") -> list[dict]:
    """Each implemented anim at three non-degenerate sample times."""
    from cutora.render.textfx.anim import (
        ANIMS, LOOP_PERIOD_S, SLIDE_DIRS, TRACKING_IN_EM,
        _colour_scale, _flip_clock_fn, _outline_to_fill_fn, _rotate_flip_fn,
        _tracking_in_fn,
    )
    cells = []
    sample = "the office"
    four = "one two three four"
    times = (("t0", 0.0), ("t50", 0.5), ("t100", 0.99))
    color_loops = (
        "karaoke", "highlight", "box_highlight",
        "colour_highlight", "rotate_highlight",
    )
    in_ids = []
    loop_ids = []
    for aid, defn in ANIMS.items():
        if aid == "none":
            continue
        if "in" in defn.phases:
            if aid == "slide":
                in_ids.append(("slide+left", "滑入←"))
            else:
                in_ids.append((aid, defn.label_zh))
        if "loop" in defn.phases:
            loop_ids.append((aid, defn.label_zh))

    def _add(aid, title, fx_anim, extra, cell_sample, tag, u, note="",
             end=None):
        cell = {
            "id": "anim-%s-%s" % (str(aid).replace("+", "_"), tag),
            "title": "%s · %s%s" % (
                title, tag, (" · %s" % note) if note else ""),
            "covers": (),
            "fx": _fx(anim=fx_anim, **extra),
            "sample": cell_sample,
            "t": u,
        }
        if end is not None:
            cell["end"] = float(end)
        cells.append(cell)

    for aid, title in in_ids:
        fx_anim = {
            "in": aid, "loop": "none", "out": "none",
            "in_s": 1.0, "out_s": 0.25, "easing": "ease_out",
        }
        extra = {}
        if aid in ("line_draw", "outline_to_fill"):
            extra["stroke"] = {"width_em": 0.08, "color": "#22D3EE"}
        if aid == "typewriter":
            fx_anim = dict(fx_anim)
            fx_anim["easing"] = "linear"
            for tag, u, note in (
                ("t25", 0.25, "1 词"),
                ("t50", 0.50, "2 词"),
                ("t75", 0.75, "3 词"),
            ):
                _add(aid, title, fx_anim, extra, four, tag, u, note)
            continue
        if aid in ("flip_clock", "rotate_flip"):
            # Linear. u=0.5 is edge-on (scale 0); 0 / 0.35 / 0.85 are
            # back / mid-flip / face. ease_out(0.35)≈0.73 already face-on.
            fx_anim = dict(fx_anim)
            fx_anim["easing"] = "linear"
            for tag, u, note in (
                ("t0", 0.0, "背面"),
                ("t35", 0.35, "半翻"),
                ("t85", 0.85, "正面"),
            ):
                if aid == "flip_clock":
                    xf = _flip_clock_fn(u, {})
                    print("PLAN", aid, u, "sy=%.3f extra=%s" % (
                        xf.scale_y, xf.extra_color), flush=True)
                else:
                    xf = _rotate_flip_fn(u, {})
                    print("PLAN", aid, u, "sx=%.3f rot=%.2f extra=%s" % (
                        xf.scale_x, xf.rot_deg, xf.extra_color), flush=True)
                _add(aid, title, fx_anim, extra, sample, tag, u, note, end=5.0)
            continue
        if aid == "stack":
            fx_anim = dict(fx_anim)
            fx_anim["easing"] = "linear"
            fx_anim["in_s"] = 0.8
            # Line 0 in_s=0.8, line 1 starts at 0.8. 0.3 / 0.9 / 1.4 are
            # one-line rising / line2 entering / line2 almost in.
            for tag, u, note in (
                ("t30", 0.3, "行1升"),
                ("t90", 0.9, "行2起"),
                ("t140", 1.4, "行2升"),
            ):
                print("PLAN", aid, u, note, flush=True)
                _add(aid, title, fx_anim, extra, "the office\nnext line",
                     tag, u, note, end=4.0)
            continue
        if aid == "outline_to_fill":
            fx_anim = dict(fx_anim)
            fx_anim["easing"] = "linear"
            for tag, u, note in (
                ("t30", 0.3, "仅描边"),
                ("t80", 0.8, "半填"),
                ("t100", 1.0, "满填"),
            ):
                xf = _outline_to_fill_fn(u, {})
                print("PLAN", aid, u, "fill_progress=%.3f" % xf.fill_progress,
                      flush=True)
                _add(aid, title, fx_anim, extra, sample, tag, u, note)
            continue
        if aid == "tracking_in":
            fx_anim = dict(fx_anim)
            fx_anim["easing"] = "linear"
            for tag, u in (("t25", 0.25), ("t50", 0.50), ("t75", 0.75)):
                xf = _tracking_in_fn(u, {})
                print("PLAN", aid, u, "tracking_em=%.3f (peak=%.2f)" % (
                    xf.tracking_em, TRACKING_IN_EM), flush=True)
                _add(aid, title, fx_anim, extra, sample, tag, u,
                     "track=%.3f" % xf.tracking_em)
            continue
        for tag, u in times:
            _add(aid, title, fx_anim, extra, sample, tag, u)
    for aid, title in loop_ids:
        extra = {}
        if aid == "colour_highlight":
            extra["emphasis"] = {
                "color": "#22D3EE",
                "palette": ["#7C3AED", "#22D3EE"],
            }
        if aid in color_loops:
            # Word mids (or bounce/rot peaks). Loop-period samples all land
            # on the last word after t=0.4.
            for i, tag in enumerate(("w0", "w1", "w2")):
                t = i * 0.2 + 0.10
                note = "词%d" % (i + 1)
                if aid == "colour_highlight":
                    t = i * 0.2 + 0.03
                    local_u = 0.03 / 0.2
                    note = "词%d scale=%.3f" % (i + 1, _colour_scale(local_u))
                    print("PLAN", aid, t, note, flush=True)
                elif aid == "rotate_highlight":
                    t = i * 0.2 + 0.04
                    fade = min(1.0, 0.04 / 0.120)
                    rot = (1.0 if i % 2 == 0 else -1.0) * 6.0 * (1.0 - fade)
                    note = "词%d rot=%.2f" % (i + 1, rot)
                    print("PLAN", aid, t, note, flush=True)
                else:
                    print("PLAN", aid, t, note, "local_u=0.5", flush=True)
                _add(aid, title, {
                    "in": "none", "loop": aid, "out": "none",
                }, extra, four, tag, t, note)
            continue
        for tag, u in (("t25", 0.25), ("t50", 0.5), ("t75", 0.75)):
            _add(aid, title, {
                "in": "none", "loop": aid, "out": "none",
            }, extra, sample, tag, u * LOOP_PERIOD_S)
    _ = ground
    _ = SLIDE_DIRS
    return cells


def covered_controls() -> set[str]:
    out: set[str] = set()
    for cell in effect_cells():
        out.update(cell["covers"])
    return out
