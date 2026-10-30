# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project-level split layout: presets, recompute, track cells.

Pure functions. Preview / export / UI all call recompute after divider edits.
"""
from __future__ import annotations

import copy
import math

from cutora.core.log import get_logger

logger = get_logger("cutora.render.split_layout")

LAYOUTS = (
    "top_bottom",
    "left_right",
    "left_right_fit",
    "grid_2x2",
    "grid_3x3",
    "left1_right2",
    "top1_bottom2",
    "top1_bottom3",
    "bottom1_top2",
    "bottom1_top3",
    "left1_right3",
    "left1_right4",
    "right1_left2",
    "right1_left3",
    "center_h",
    "center_h_tilt",
    "l_left2_bottom2",
    "l_right2_bottom2",
    "l_left2_bottom3",
    "l_left1_bottom2",
    "l_right1_bottom2",
    "hex_center_4",
    "shutter_4",
    "circle_bottom3",
    "circle_right3",
    "custom",
)

SPLIT_SHAPES = (
    "circle",
    "polygon",
    "hexagon",
    "octagon",
    "diamond",
)

SHAPE_LABELS = {
    "circle": "⭕ 光滑圆形",
    "polygon": "⬢ 多边形 (分段可调→圆)",
    "hexagon": "⬢ 六角形",
    "octagon": "⯃ 八角形",
    "diamond": "◆ 菱形",
}

SHAPE_DEFAULT_SIDES = {
    "triangle": 3,
    "diamond": 4,
    "pentagon": 5,
    "polygon": 6,
    "hexagon": 6,
    "octagon": 8,
    "circle": 64,
}

LAYOUT_LABELS = {
    "top_bottom": "上下",
    "left_right": "左右裁切",
    "left_right_fit": "左右完整",
    "grid_2x2": "2×2",
    "grid_3x3": "3×3",
    "left1_right2": "左1右2",
    "top1_bottom2": "上1下2",
    "top1_bottom3": "上1下3",
    "top1_bottom4": "上1下4",
    "bottom1_top2": "下1上2",
    "bottom1_top3": "下1上3",
    "bottom1_top4": "下1上4",
    "left1_right3": "左1右3",
    "left1_right4": "左1右4",
    "right1_left2": "右1左2",
    "right1_left3": "右1左3",
    "right1_left4": "右1左4",
    "center_h": "中主上下副",
    "center_h_arc": "双层弧夹心",
    "center_h_embrace": "怀抱弧夹心",
    "center_h_swave": "流线S弧夹心",
    "center_h_tilt": "动感斜夹心",
    "center_h_auto": "中主上下（自动）",
    "l_left2_bottom3": "L形·左2下3",
    "l_left1_bottom2": "L形·左1下2",
    "l_left2_bottom2": "L形·左2下2",
    "l_right1_bottom2": "反L·右1下2",
    "l_right2_bottom2": "反L·右2下2",
    "l_left1_top2": "顶L·左1上2",
    "l_left2_top2": "顶L·左2上2",
    "l_shape_auto": "L形分屏（自动）",
    "hex_center_4": "六角中主四角",
    "hex_center_auto": "六角中主（自动）",
    "shutter_4": "相机快门穿插",
    "shutter_auto": "相机快门（自动）",
    "circle_bottom3": "底部圆窗",
    "circle_right3": "右侧圆窗",
    "top1_bottom_auto": "上1下多（自动）",
    "bottom1_top_auto": "下1上多（自动）",
    "left1_right_auto": "左1右多（自动）",
    "right1_left_auto": "右1左多（自动）",
    "circle_bottom_auto": "底部圆圈（自动）",
    "circle_right_auto": "右侧圆圈（自动）",
    "circle_scatter_auto": "错落圆圈（自动）",
    "custom": "自定义",
}


def cell_id_for_index(idx: int) -> str:
    """0 -> 'A', 1 -> 'B', ..., 25 -> 'Z', 26 -> 'C27', ..."""
    i = max(0, int(idx))
    if i < 26:
        return chr(ord("A") + i)
    return "C%d" % (i + 1)


def parse_1_to_n(layout: str | None) -> tuple[str, int] | None:
    """解析一对多布局名：
    - 'top1_bottom<N>' / 'bottom1_top<N>' 仅允许 2 <= N <= 3（4个及以上挤在一横排太挤，自动走 L 形分屏）
    - 'left1_right<N>' / 'right1_left<N>' (N >= 2) -> (mode, N)。
    """
    name = str(layout or "").strip()
    for prefix in ("top1_bottom", "bottom1_top", "left1_right", "right1_left"):
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit():
                n = int(tail)
                if prefix in ("top1_bottom", "bottom1_top") and n >= 4:
                    return None
                if n >= 2:
                    return prefix, n
    return None


def parse_l_layout(layout: str | None) -> tuple[int, int, str, str] | None:
    """解析 L 形分屏布局名：
    'l_left<NL>_bottom<NB>' / 'l_right<NR>_bottom<NB>' / 'l_left<NL>_top<NT>' / 'l_right<NR>_top<NT>'
    -> (n_side, n_band, side, band)。
    同时将旧版 'top1_bottom<N>' / 'bottom1_top<N>' (N >= 4) 自动转化为 L 形分屏，避免 4 个视频挤在一横排。
    """
    name = str(layout or "").strip()
    if name == "l_shape_auto":
        return 2, 2, "left", "bottom"
    for prefix, band_k in (("top1_bottom", "bottom"), ("bottom1_top", "top")):
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit() and int(tail) >= 4:
                n = int(tail)
                ns = max(2, n // 2)
                nb = max(2, n - ns)
                return ns, nb, "left", band_k
    for side_key, side_prefix in (("left", "l_left"), ("right", "l_right")):
        if name.startswith(side_prefix):
            body = name[len(side_prefix):]
            for band_key, band_sep in (("bottom", "_bottom"), ("top", "_top")):
                if band_sep in body:
                    try:
                        s_side, s_band = body.split(band_sep, 1)
                        ns, nb = int(s_side), int(s_band)
                        if ns >= 1 and nb >= 1:
                            return ns, nb, side_key, band_key
                    except (TypeError, ValueError):
                        pass
    return None


def parse_center_h_layout(layout: str | None) -> tuple[int, int, float, str, str] | None:
    """解析横向夹心分屏（主视频在中间，上下副屏，默认全部采用直线分界）：
    返回 (n_top, n_bottom, rot_deg, c_top, c_bottom)。
    """
    name = str(layout or "").strip()
    if name in ("center_h", "center_h_auto", "center_h_arc", "center_h_arc_auto", "center_h_embrace", "center_h_embrace_auto", "center_h_swave", "center_h_swave_auto"):
        return 1, 1, 0.0, "straight", "straight"
    if name in ("center_h_tilt", "center_h_tilt_auto"):
        return 1, 1, 9.0, "straight", "straight"
    prefixes = (
        ("center_h_embrace", 0.0, "straight", "straight"),
        ("center_h_arc", 0.0, "straight", "straight"),
        ("center_h_swave", 0.0, "straight", "straight"),
        ("center_h_tilt", 9.0, "straight", "straight"),
        ("center_h", 0.0, "straight", "straight"),
    )
    for prefix, def_rot, ct, cb in prefixes:
        if name.startswith(prefix):
            tail = name[len(prefix):].lstrip("_")
            if "_" in tail:
                try:
                    st, sb = tail.split("_", 1)
                    nt, nb = int(st), int(sb)
                    if nt >= 1 and nb >= 1:
                        return nt, nb, def_rot, ct, cb
                except (TypeError, ValueError):
                    pass
    return None


def parse_center_aperture_layout(layout: str | None) -> tuple[str, str, int, float, int] | None:
    """解析中央多边形/六角形/快门孔径 + 四角/穿插叶片副屏：
    返回 (kind, shape, sides, twist_deg, n_sec)。
    - 'hex_center_4' / 'hex_center_<N>' / 'poly_center_<N>' -> ('hex_center', 'polygon', 6, 0.0, N)
    - 'shutter_4' / 'shutter_<N>' / 'shutter_<shape>_<N>' -> ('shutter', shape, sides, 36.0, N)
    """
    name = str(layout or "").strip()
    if name in ("hex_center_auto", "hex_center_4", "hex_center"):
        return ("hex_center", "polygon", 6, 0.0, 4)
    for prefix in ("hex_center_", "poly_center_"):
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit() and int(tail) >= 2:
                return ("hex_center", "polygon", 6, 0.0, int(tail))
    if name in ("shutter_auto", "shutter_4", "shutter"):
        return ("shutter", "polygon", 6, 36.0, 4)
    if name.startswith("shutter_"):
        tail = name[len("shutter_"):]
        if tail.isdigit() and int(tail) >= 2:
            return ("shutter", "polygon", 6, 36.0, int(tail))
        if "_" in tail:
            sh, sn = tail.rsplit("_", 1)
            if sh in SPLIT_SHAPES and sn.isdigit() and int(sn) >= 2:
                sides = SHAPE_DEFAULT_SIDES.get(sh, 6)
                return ("shutter", sh, sides, 36.0, int(sn))
    return None


def parse_circle_layout(layout: str | None) -> tuple[str, int] | None:
    """解析圆圈画中画分屏布局名：
    - 'circle_bottom<N>' / 'circle_right<N>' / 'circle_scatter<N>' (N >= 1)
    - 兼容旧工程 'shape_<shape>_<N>' 自动映射为圆滑画中画
    """
    name = str(layout or "").strip()
    for prefix in ("circle_bottom", "circle_right", "circle_scatter"):
        if name == f"{prefix}_auto":
            return prefix, 3
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit():
                n = int(tail)
                if n >= 1:
                    return prefix, n
    if name.startswith("shape_"):
        tail = name[len("shape_"):]
        parts = tail.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            n = max(1, int(parts[1]))
            sh = parts[0] if parts[0] in SPLIT_SHAPES else "circle"
            return f"circle_bottom:{sh}", n
        if len(parts) == 2 and parts[1] == "auto":
            sh = parts[0] if parts[0] in SPLIT_SHAPES else "circle"
            return f"circle_bottom:{sh}", 3
        if len(parts) == 3 and parts[0] in ("bottom", "right", "scatter") and parts[2].isdigit():
            n = max(1, int(parts[2]))
            sh = parts[1] if parts[1] in SPLIT_SHAPES else "circle"
            return f"circle_{parts[0]}:{sh}", n
    return None


def is_valid_layout(layout: str | None) -> bool:
    name = str(layout or "").strip()
    return (
        name in LAYOUTS
        or name in LAYOUT_LABELS
        or parse_1_to_n(name) is not None
        or parse_l_layout(name) is not None
        or parse_center_h_layout(name) is not None
        or parse_center_aperture_layout(name) is not None
        or parse_circle_layout(name) is not None
    )


def layout_label_for(layout: str | None) -> str:
    name = str(layout or "").strip()
    if name in LAYOUT_LABELS:
        return LAYOUT_LABELS[name]
    parsed = parse_1_to_n(name)
    if parsed is not None:
        mode, n = parsed
        label_map = {
            "top1_bottom": "上1下%d",
            "bottom1_top": "下1上%d",
            "left1_right": "左1右%d",
            "right1_left": "右1左%d",
        }
        return label_map.get(mode, "上1下%d") % n
    pl = parse_l_layout(name)
    if pl is not None:
        ns, nb, side_k, band_k = pl
        s_zh = "左" if side_k == "left" else "右"
        b_zh = "下" if band_k == "bottom" else "上"
        return "L形·%s%d%s%d" % (s_zh, ns, b_zh, nb)
    pch = parse_center_h_layout(name)
    if pch is not None:
        nt, nb, rot, ct, cb = pch
        if ct == "arch_up" and cb == "arc":
            prefix = "怀抱弧夹心"
        elif ct in ("arc", "arch_up"):
            prefix = "双层弧夹心"
        elif ct == "s_wave":
            prefix = "S弧夹心"
        elif abs(rot) > 0.5:
            prefix = "斜夹心"
        else:
            prefix = "中主"
        return "%s·上%d下%d" % (prefix, nt, nb)
    pca = parse_center_aperture_layout(name)
    if pca is not None:
        kind, sh, _sides, _tw, n_sec = pca
        sh_name = SHAPE_LABELS.get(sh, sh).split()[-1]
        if kind == "shutter":
            return "快门穿插·%s%d叶" % (sh_name, n_sec)
        return "中主%s·%d角" % (sh_name, n_sec)
    p_circ = parse_circle_layout(name)
    if p_circ is not None:
        cmode, cn = p_circ
        sh = "circle"
        if ":" in cmode:
            cmode, sh = cmode.split(":", 1)
        sh_name = "圆窗" if sh == "circle" else SHAPE_LABELS.get(sh, sh).split()[-1]
        if cmode == "circle_right":
            return "右侧%d%s" % (cn, sh_name)
        if cmode == "circle_scatter":
            return "错落%d%s" % (cn, sh_name)
        return "底部%d%s" % (cn, sh_name)
    return name or "上下"


BORDER_STYLES = ("none", "solid", "gradient", "glass", "shadow")
BORDER_CURVES = ("straight", "wave", "arc", "arch_up", "s_wave")

DEFAULT_BORDER = {
    "style": "none",
    "width_px": 12,
    "color": "#F2C14E",
    "color2": "#FFFFFF",
    "curve": "straight",
    "shadow_px": 12,
    "glass_sigma": 6,
}

_POS_MIN = 0.05
_POS_MAX = 0.95

# Q151 左右完整的模糊背景。each = 每格垫自己那条视频的模糊（Luca 要的抖音样式），
# first = 整幅垫第一条轨道的模糊（Q148 Grok 的做法）。style 同 fill_bg：lens 镜头模糊 /
# legacy 旧缩图模糊。工程里没写 backdrop 的老分屏按 LEGACY_BACKDROP 画，观感不变；
# 新开的左右完整（apply_split_to_task）写 NEW_BACKDROP。
CONTAIN_LAYOUTS = frozenset({"left_right_fit"})
# Q155：full = 整幅垫「一条」视频的模糊（cell 指定哪一格的那条，空 = 原视频那格），
# 所有布局都认（左右裁切 / 上下 / 三格……），格子里片段自己的背景填充这时不画，
# 留白处统一露出整幅底，不再一格一块。first 是 Q148 的老写法，只在左右完整里生效。
BACKDROP_MODES = ("each", "first", "full")
LEGACY_BACKDROP = {"mode": "first", "blur": 45.0, "style": "legacy",
                   "highlights": 0.35, "dim": 0.25}
NEW_BACKDROP = {"mode": "each", "blur": 45.0, "style": "lens",
                "highlights": 0.35, "dim": 0.25}


def _norm_backdrop(raw) -> dict:
    out = dict(NEW_BACKDROP)        # 写了 backdrop 的都是 Q151 之后的，缺的键按新默认补
    if isinstance(raw, dict):
        for k in out:
            if raw.get(k) is not None:
                out[k] = raw[k]
    mode = str(out.get("mode") or "").lower()
    out["mode"] = mode if mode in BACKDROP_MODES else "each"
    cell = raw.get("cell") if isinstance(raw, dict) else None
    if cell not in (None, "", "none", "None"):
        out["cell"] = str(cell)
    style = str(out.get("style") or "").lower()
    out["style"] = style if style in ("lens", "legacy") else "lens"
    for k, lo, hi in (("blur", 0.0, 100.0), ("highlights", 0.0, 1.0), ("dim", 0.0, 1.0)):
        try:
            out[k] = max(lo, min(hi, float(out[k])))
        except (TypeError, ValueError):
            out[k] = float(NEW_BACKDROP[k])
    return out


def backdrop_spec(split) -> dict:
    """这个分屏的模糊背景设置（没写过的老分屏 = LEGACY_BACKDROP）。"""
    raw = split.get("backdrop") if isinstance(split, dict) else None
    return _norm_backdrop(raw) if isinstance(raw, dict) else dict(LEGACY_BACKDROP)


def backdrop_is_full(split) -> bool:
    """Q155：用户明确选了「单个视频撑满整幅背景」（任何布局都生效）。"""
    raw = split.get("backdrop") if isinstance(split, dict) else None
    return isinstance(raw, dict) and str(raw.get("mode") or "").lower() == "full"


def default_split():
    cells, dividers = preset_geometry("top_bottom")
    return {
        "enabled": False,
        "layout": "top_bottom",
        "cells": cells,
        "dividers": dividers,
        "border": dict(DEFAULT_BORDER),
    }


def split_enabled(task) -> bool:
    if not isinstance(task, dict):
        return False
    raw = task.get("split")
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("enabled"))


def _cell(cid, x, y, w, h, circle=None):
    c = {"id": str(cid), "rect": [float(x), float(y), float(w), float(h)]}
    if isinstance(circle, (list, tuple)) and len(circle) >= 3:
        aspect = float(circle[3]) if len(circle) >= 4 else 1.0
        rot = float(circle[4]) if len(circle) >= 5 else 0.0
        c["circle"] = [float(circle[0]), float(circle[1]), float(circle[2]), aspect, rot]
    return c


def _div(did, axis, pos, **extra):
    ax_str = str(axis or "h")
    if ax_str not in ("h", "v", "circle", "blade"):
        ax_str = "h"
    d = {
        "id": str(did),
        "axis": ax_str,
        "pos": float(pos),
        "rotation_deg": 0.0,
        "feather_px": 24.0,
    }
    d.update(extra)
    return d


def _shape_extent_factor(shape: str | None, sides: int = 6) -> float:
    """异形气泡（心形、和平鸽、橄榄叶、蝴蝶、五角星等）的极径峰值可达 1.25~1.28 * r，
    为 cell['circle'] 乘以扩展系数，保证内容框完全覆盖异形边角，绝不出现裁切黑边。
    """
    sh = str(shape or "circle")
    if sh == "circle":
        return 1.0
    if sh in ("polygon", "pentagon", "hexagon", "octagon", "diamond") and int(sides or 6) >= 24:
        return 1.05
    return 1.30


def _circle_preset_geometry(
    mode: str,
    n: int,
    radius_override: float | None = None,
    shape: str | None = None,
    sides: int | None = None,
):
    """生成主视频全屏 + N 个圆窗画中画副屏的几何结构（bottom / right / scatter）。"""
    n = max(1, int(n))
    sh = str(shape or "circle")
    if ":" in str(mode):
        mode, sh_from_mode = str(mode).split(":", 1)
        if not shape:
            sh = sh_from_mode
    if sh not in SPLIT_SHAPES:
        sh = "circle"
    n_sides = int(sides) if sides is not None else SHAPE_DEFAULT_SIDES.get(sh, 64)
    ext_f = _shape_extent_factor(sh, n_sides)
    sec_ids = [cell_id_for_index(1 + i) for i in range(n)]
    cells = [_cell("A", 0.0, 0.0, 1.0, 1.0)]
    dividers = []
    if mode == "circle_right":
        base_r = float(radius_override) if radius_override else min(0.155, 0.42 / max(1.2, float(n)))
        span_y0, span_y1 = (0.20, 0.80) if n <= 3 else (0.14, 0.86)
        for i, cid in enumerate(sec_ids):
            cy = 0.5 if n == 1 else (span_y0 + (span_y1 - span_y0) * i / float(n - 1))
            cx = 0.78 - (0.04 if (n >= 3 and i % 2 == 1) else 0.0)
            r = max(0.06, min(0.34, base_r))
            cr = min(0.48, r * ext_f)
            cells.append(_cell(cid, max(0.0, cx - cr), max(0.0, cy - cr), min(1.0, 2 * cr), min(1.0, 2 * cr), circle=[cx, cy, cr]))
            dividers.append(_div(
                "d%d" % (i + 1), "circle", cy,
                cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
                neg_cell="A", pos_cell=cid, feather_px=0.0,
            ))
        return cells, dividers
    if mode == "circle_scatter":
        base_r = float(radius_override) if radius_override else min(0.16, 0.43 / max(1.2, float(n)))
        anchors = [
            (0.24, 0.75, 1.05),
            (0.52, 0.80, 0.92),
            (0.79, 0.71, 1.08),
            (0.80, 0.38, 0.88),
            (0.22, 0.42, 0.90),
            (0.50, 0.24, 0.85),
        ]
        for i, cid in enumerate(sec_ids):
            if i < len(anchors):
                cx, cy, scale = anchors[i]
            else:
                ang = math.pi * (0.15 + 0.70 * (i / max(1, n - 1)))
                cx = 0.5 + 0.32 * math.cos(ang)
                cy = 0.55 + 0.28 * math.sin(ang)
                scale = 0.9
            r = max(0.06, min(0.34, base_r * scale))
            cr = min(0.48, r * ext_f)
            cells.append(_cell(cid, max(0.0, cx - cr), max(0.0, cy - cr), min(1.0, 2 * cr), min(1.0, 2 * cr), circle=[cx, cy, cr]))
            dividers.append(_div(
                "d%d" % (i + 1), "circle", cy,
                cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
                neg_cell="A", pos_cell=cid, feather_px=0.0,
            ))
        return cells, dividers
    # 默认 circle_bottom：底部横排圆圈
    base_r = float(radius_override) if radius_override else min(0.165, 0.44 / max(1.2, float(n)))
    span_x0, span_x1 = (0.24, 0.76) if n == 2 else ((0.18, 0.82) if n == 3 else (0.14, 0.86))
    for i, cid in enumerate(sec_ids):
        cx = 0.5 if n == 1 else (span_x0 + (span_x1 - span_x0) * i / float(n - 1))
        cy = 0.76 - (0.035 if (n >= 3 and i % 2 == 1) else 0.0)
        r = max(0.06, min(0.34, base_r))
        cr = min(0.48, r * ext_f)
        cells.append(_cell(cid, max(0.0, cx - cr), max(0.0, cy - cr), min(1.0, 2 * cr), min(1.0, 2 * cr), circle=[cx, cy, cr]))
        dividers.append(_div(
            "d%d" % (i + 1), "circle", cy,
            cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
            neg_cell="A", pos_cell=cid, feather_px=0.0,
        ))
    return cells, dividers


def _l_preset_geometry(
    n_side: int = 2,
    n_band: int = 3,
    p_side: float | None = None,
    p_band: float | None = None,
    side: str = "left",
    band: str = "bottom",
):
    """四方位 L 形分屏：
    - side='left',  band='bottom': 主视频 A 在右上，左侧 n_side 个 + 底部 n_band 个
    - side='right', band='bottom': 主视频 A 在左上，右侧 n_side 个 + 底部 n_band 个
    - side='left',  band='top':    主视频 A 在右下，左侧 n_side 个 + 顶部 n_band 个
    - side='right', band='top':    主视频 A 在左下，右侧 n_side 个 + 顶部 n_band 个
    """
    nl = max(1, int(n_side))
    nb = max(1, int(n_band))
    side_k = "right" if str(side) == "right" else "left"
    band_k = "top" if str(band) == "top" else "bottom"
    def_ps = 0.68 if side_k == "right" else 0.32
    def_pb = 0.28 if band_k == "top" else 0.72
    ps = _clamp_pos(p_side if p_side is not None else def_ps, 0.14, 0.86)
    pb = _clamp_pos(p_band if p_band is not None else def_pb, 0.14, 0.86)
    side_ids = [cell_id_for_index(1 + i) for i in range(nl)]
    band_ids = [cell_id_for_index(1 + nl + j) for j in range(nb)]

    v_span = [pb, 1.0] if band_k == "top" else [0.0, pb]
    v_len = max(0.08, v_span[1] - v_span[0])
    ax_x = 0.0 if side_k == "right" else ps
    ax_w = ps if side_k == "right" else (1.0 - ps)
    sx_x = ps if side_k == "right" else 0.0
    sx_w = (1.0 - ps) if side_k == "right" else ps

    cells = [_cell("A", ax_x, v_span[0], ax_w, v_len)]
    step_l = v_len / float(nl)
    for i, cid in enumerate(side_ids):
        y0 = v_span[0] + i * step_l
        y1 = v_span[1] if i == nl - 1 else (v_span[0] + (i + 1) * step_l)
        cells.append(_cell(cid, sx_x, y0, sx_w, y1 - y0))

    by_y = 0.0 if band_k == "top" else pb
    by_h = pb if band_k == "top" else (1.0 - pb)
    step_b = 1.0 / float(nb)
    for j, cid in enumerate(band_ids):
        x0 = j * step_b
        x1 = 1.0 if j == nb - 1 else (j + 1) * step_b
        cells.append(_cell(cid, x0, by_y, x1 - x0, by_h))

    d1_neg = "A" if side_k == "right" else ",".join(side_ids)
    d1_pos = ",".join(side_ids) if side_k == "right" else "A"
    upper_group = ",".join(band_ids) if band_k == "top" else ("A," + ",".join(side_ids))
    lower_group = ("A," + ",".join(side_ids)) if band_k == "top" else ",".join(band_ids)

    dividers = [
        _div(
            "d1", "v", ps,
            span=v_span,
            neg_cell=d1_neg,
            pos_cell=d1_pos,
            l_role="main_v",
            l_nl=nl,
            l_nb=nb,
            l_side=side_k,
            l_band=band_k,
            feather_px=0.0,
        ),
        _div(
            "d2", "h", pb,
            span=[0.0, 1.0],
            neg_cell=upper_group,
            pos_cell=lower_group,
            l_role="main_h",
            l_nl=nl,
            l_nb=nb,
            l_side=side_k,
            l_band=band_k,
            feather_px=0.0,
        ),
    ]
    sub_v_span = [ps, 1.0] if side_k == "right" else [0.0, ps]
    for k in range(1, nl):
        dividers.append(_div(
            "d%d" % (2 + k), "h", v_span[0] + k * step_l,
            span=sub_v_span,
            neg_cell=side_ids[k - 1],
            pos_cell=side_ids[k],
            l_role="left_sub",
            curve="straight",
            feather_px=0.0,
        ))
    sub_h_span = [0.0, pb] if band_k == "top" else [pb, 1.0]
    for j in range(1, nb):
        dividers.append(_div(
            "d%d" % (1 + nl + j), "v", j * step_b,
            span=sub_h_span,
            neg_cell=band_ids[j - 1],
            pos_cell=band_ids[j],
            l_role="bottom_sub",
            curve="straight",
            feather_px=0.0,
        ))
    return cells, dividers


def _center_h_preset_geometry(
    n_top: int = 1,
    n_bottom: int = 1,
    rot_deg: float = 0.0,
    p_top: float = 0.25,
    p_bottom: float = 0.75,
    c_top: str = "straight",
    c_bottom: str = "straight",
):
    """横向夹心分屏（上-中-下）：主视频 A 在中间占最大画面，上方 n_top 条，下方 n_bottom 条；
    上下两条横向分界线 d1 / d2 支持任意角度旋转或双层弧线（同向层叠弧 / 对称怀抱弧 / 流线S弧）。
    """
    nt = max(1, int(n_top))
    nb = max(1, int(n_bottom))
    pt = _clamp_pos(p_top, 0.10, 0.45)
    pb = _clamp_pos(p_bottom, pt + 0.20, 0.90)
    rot = float(rot_deg or 0.0)
    ct = str(c_top or "straight")
    cb = str(c_bottom or "straight")
    top_ids = [cell_id_for_index(1 + i) for i in range(nt)]
    bot_ids = [cell_id_for_index(1 + nt + j) for j in range(nb)]
    pad_rot = min(0.28, 0.55 * abs(math.tan(math.radians(rot)))) if abs(rot) > 0.1 else 0.0
    pad_ct = 0.085 if ct != "straight" else 0.0
    pad_cb = 0.085 if cb != "straight" else 0.0
    pad1 = max(pad_rot, pad_ct)
    pad2 = max(pad_rot, pad_cb)
    ay0 = max(0.0, pt - pad1)
    ay1 = min(1.0, pb + pad2)
    cells = [_cell("A", 0.0, ay0, 1.0, max(0.15, ay1 - ay0))]
    step_t = 1.0 / float(nt)
    for i, cid in enumerate(top_ids):
        x0 = i * step_t
        x1 = 1.0 if i == nt - 1 else (i + 1) * step_t
        cells.append(_cell(cid, x0, 0.0, x1 - x0, min(1.0, pt + pad1)))
    step_b = 1.0 / float(nb)
    by0 = max(0.0, pb - pad2)
    for j, cid in enumerate(bot_ids):
        x0 = j * step_b
        x1 = 1.0 if j == nb - 1 else (j + 1) * step_b
        cells.append(_cell(cid, x0, by0, x1 - x0, max(0.10, 1.0 - by0)))

    dividers = [
        _div(
            "d1", "h", pt,
            rotation_deg=rot,
            curve=ct,
            neg_cell=",".join(top_ids),
            pos_cell="A," + ",".join(bot_ids),
            ch_role="top_h",
            ch_nt=nt,
            ch_nb=nb,
            feather_px=0.0,
        ),
        _div(
            "d2", "h", pb,
            rotation_deg=rot,
            curve=cb,
            neg_cell="A," + ",".join(top_ids),
            pos_cell=",".join(bot_ids),
            ch_role="bottom_h",
            ch_nt=nt,
            ch_nb=nb,
            feather_px=0.0,
        ),
    ]
    for k in range(1, nt):
        dividers.append(_div(
            "d%d" % (2 + k), "v", k * step_t,
            span=[0.0, pt],
            neg_cell=top_ids[k - 1],
            pos_cell=top_ids[k],
            ch_role="top_sub",
            curve="straight",
            feather_px=0.0,
        ))
    for j in range(1, nb):
        dividers.append(_div(
            "d%d" % (1 + nt + j), "v", j * step_b,
            span=[pb, 1.0],
            neg_cell=bot_ids[j - 1],
            pos_cell=bot_ids[j],
            ch_role="bottom_sub",
            curve="straight",
            feather_px=0.0,
        ))
    return cells, dividers


def _blade_sector_rect(cx: float, cy: float, r: float, idx: int, count: int, twist_deg: float, rot_deg: float) -> list[float]:
    """计算中央多边形/快门分屏中第 idx 个环绕格子的包围盒 [x, y, w, h]，使副屏视频自然铺满该角/快门叶片。"""
    m = max(1, int(count))
    if m == 1:
        return [0.0, 0.0, 1.0, 1.0]
    if m == 4 and abs(float(twist_deg or 0.0)) < 8.0 and abs(float(rot_deg or 0.0)) < 8.0:
        # 标准四角（左上 B、右上 C、右下 D、左下 E）稍微向中心延伸，确保完全覆盖六角形外围
        pad = 0.06
        quads = [
            [0.0, 0.0, min(1.0, cx + pad), min(1.0, cy + pad)],
            [max(0.0, cx - pad), 0.0, min(1.0, 1.0 - cx + pad), min(1.0, cy + pad)],
            [max(0.0, cx - pad), max(0.0, cy - pad), min(1.0, 1.0 - cx + pad), min(1.0, 1.0 - cy + pad)],
            [0.0, max(0.0, cy - pad), min(1.0, cx + pad), min(1.0, 1.0 - cy + pad)],
        ]
        return quads[idx % 4]
    rot_rad = math.radians(float(rot_deg or 0.0))
    tau = math.radians(float(twist_deg or 0.0))
    a0 = -math.pi + 2.0 * math.pi * idx / float(m) + rot_rad
    a1 = -math.pi + 2.0 * math.pi * (idx + 1) / float(m) + rot_rad
    xs, ys = [], []
    # 在 9:16 竖屏或 16:9 横屏下，采样从内圈孔径边缘 (s=0) 到画布四角 (s=1.2) 的完整螺旋扇区包络
    for s_step in range(13):
        s_frac = s_step / 10.0  # 0.0 .. 1.2
        ang_shift = tau * s_frac
        for a_step in range(13):
            ang = a0 + (a1 - a0) * (a_step / 12.0) + ang_shift
            ca, sa_v = math.cos(ang), math.sin(ang)
            for rx_scale, ry_scale in ((1.0, 1.0), (1.0, 0.56), (0.56, 1.0)):
                rad_x = (r * 0.65 + s_frac * 0.75) * rx_scale
                rad_y = (r * 0.65 + s_frac * 0.75) * ry_scale
                xs.append(max(0.0, min(1.0, cx + rad_x * ca)))
                ys.append(max(0.0, min(1.0, cy + rad_y * sa_v)))
    x0, x1 = max(0.0, min(xs) - 0.06), min(1.0, max(xs) + 0.06)
    y0, y1 = max(0.0, min(ys) - 0.06), min(1.0, max(ys) + 0.06)
    if x1 - x0 < 0.32:
        mid_x = 0.5 * (x0 + x1)
        x0 = max(0.0, min(0.68, mid_x - 0.16))
        x1 = min(1.0, x0 + 0.32)
    if y1 - y0 < 0.32:
        mid_y = 0.5 * (y0 + y1)
        y0 = max(0.0, min(0.68, mid_y - 0.16))
        y1 = min(1.0, y0 + 0.32)
    return [x0, y0, x1 - x0, y1 - y0]


def _center_aperture_preset_geometry(
    kind: str = "hex_center",
    shape: str = "polygon",
    sides: int = 6,
    twist_deg: float = 0.0,
    n_sec: int = 4,
    r: float = 0.31,
    cx: float = 0.50,
    cy: float = 0.50,
    aspect: float = 1.0,
    rot_deg: float = 0.0,
):
    """中央多边形/六角形/快门孔径主屏 A + 四角/穿插叶片副屏 B, C, D...：
    - 当 twist_deg == 0 且 sides == 6 时，就是「中央六角形主屏 + 四角副屏」；
    - 当 sides 从 6 调高到 64 时，六角形可平滑过渡为光滑圆形；
    - 当 twist_deg != 0（如 36°）时，四周分界线呈相机快门叶片穿插式环绕中央主屏！
    """
    m = max(1, int(n_sec))
    sh = str(shape or "polygon")
    if sh not in SPLIT_SHAPES:
        sh = "polygon"
    n_sides = max(3, min(64, int(sides or SHAPE_DEFAULT_SIDES.get(sh, 6))))
    ext_f = max(1.12, _shape_extent_factor(sh, n_sides))
    sec_ids = [cell_id_for_index(1 + i) for i in range(m)]
    all_sec = ",".join(sec_ids)
    sa = math.sqrt(max(0.3, min(3.0, float(aspect or 1.0))))
    max_r = min(0.48, float(r) * max(sa, 1.0 / sa) * ext_f)
    cells = [
        _cell(
            "A",
            max(0.0, cx - max_r),
            max(0.0, cy - max_r),
            min(1.0, 2.0 * max_r),
            min(1.0, 2.0 * max_r),
            circle=[cx, cy, min(0.48, float(r) * ext_f), float(aspect or 1.0), float(rot_deg or 0.0)],
        )
    ]
    for i, cid in enumerate(sec_ids):
        rect_i = _blade_sector_rect(cx, cy, r, i, m, twist_deg, rot_deg)
        cells.append(_cell(cid, rect_i[0], rect_i[1], rect_i[2], rect_i[3]))
    dividers = [
        _div(
            "d1", "circle", cy,
            cx=cx, cy=cy, r=r, aspect=aspect, rotation_deg=rot_deg,
            shape=sh, sides=n_sides,
            center_main=True,
            twist_deg=float(twist_deg or 0.0),
            blade_count=m,
            neg_cell=all_sec,
            pos_cell="A",
            feather_px=0.0,
        )
    ]
    if m >= 2:
        for i in range(m):
            dividers.append(_div(
                "d%d" % (i + 2), "blade", 0.5,
                cx=cx, cy=cy, r=r, aspect=aspect, rotation_deg=rot_deg,
                shape=sh, sides=n_sides,
                blade_idx=i,
                blade_count=m,
                twist_deg=float(twist_deg or 0.0),
                neg_cell=sec_ids[(i - 1) % m],
                pos_cell=sec_ids[i],
                feather_px=0.0,
            ))
    return cells, dividers


def preset_geometry(layout: str):
    name = str(layout or "top_bottom")
    if not is_valid_layout(name):
        logger.warning("未知分屏布局 %s，回退 top_bottom", name)
        name = "top_bottom"
    if name in ("top_bottom", "custom"):
        cells = [_cell("A", 0, 0, 1, 0.5), _cell("B", 0, 0.5, 1, 0.5)]
        dividers = [_div("d1", "h", 0.5, neg_cell="A", pos_cell="B")]
        return cells, dividers
    if name in ("left_right", "left_right_fit"):
        cells = [_cell("A", 0, 0, 0.5, 1), _cell("B", 0.5, 0, 0.5, 1)]
        dividers = [_div("d1", "v", 0.5, neg_cell="A", pos_cell="B")]
        return cells, dividers
    if name == "grid_2x2":
        cells = [
            _cell("A", 0, 0, 0.5, 0.5), _cell("B", 0.5, 0, 0.5, 0.5),
            _cell("C", 0, 0.5, 0.5, 0.5), _cell("D", 0.5, 0.5, 0.5, 0.5),
        ]
        dividers = [
            _div("d1", "v", 0.5, neg_cell="A,C", pos_cell="B,D"),
            _div("d2", "h", 0.5, neg_cell="A,B", pos_cell="C,D"),
        ]
        return cells, dividers
    if name == "grid_3x3":
        a, b = 1.0 / 3.0, 2.0 / 3.0
        s = 1.0 / 3.0
        ids = "ABCDEFGHI"
        cells = []
        for r in range(3):
            for c in range(3):
                cells.append(_cell(ids[r * 3 + c], c * s, r * s, s, s))
        dividers = [
            _div("d1", "v", a, neg_cell="A,D,G", pos_cell="B,E,H"),
            _div("d2", "v", b, neg_cell="B,E,H", pos_cell="C,F,I"),
            _div("d3", "h", a, neg_cell="A,B,C", pos_cell="D,E,F"),
            _div("d4", "h", b, neg_cell="D,E,F", pos_cell="G,H,I"),
        ]
        return cells, dividers
    pl = parse_l_layout(name)
    if pl is not None:
        return _l_preset_geometry(pl[0], pl[1], side=pl[2], band=pl[3])
    pch = parse_center_h_layout(name)
    if pch is not None:
        return _center_h_preset_geometry(pch[0], pch[1], rot_deg=pch[2], c_top=pch[3], c_bottom=pch[4])
    pca = parse_center_aperture_layout(name)
    if pca is not None:
        kind, sh, sides, tw, n_sec = pca
        return _center_aperture_preset_geometry(kind, shape=sh, sides=sides, twist_deg=tw, n_sec=n_sec)
    p_circ = parse_circle_layout(name)
    if p_circ is not None:
        cmode, cn = p_circ
        return _circle_preset_geometry(cmode, cn)
    parsed = parse_1_to_n(name)
    if parsed is not None:
        mode, n = parsed
        sec_ids = [cell_id_for_index(1 + i) for i in range(n)]
        all_sec = ",".join(sec_ids)
        step = 1.0 / float(n)
        if mode == "left1_right":
            cells = [_cell("A", 0.0, 0.0, 0.5, 1.0)]
            for i, cid in enumerate(sec_ids):
                y0 = i * step
                y1 = 1.0 if i == n - 1 else (i + 1) * step
                cells.append(_cell(cid, 0.5, y0, 0.5, y1 - y0))
            dividers = [_div("d1", "v", 0.5, neg_cell="A", pos_cell=all_sec, role_1n=mode)]
            for k in range(1, n):
                dividers.append(_div(
                    "d%d" % (k + 1), "h", k * step,
                    span=[0.5, 1.0],
                    neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
                    curve="straight",
                ))
            return cells, dividers
        if mode == "right1_left":
            p1 = 0.44
            cells = [_cell("A", p1, 0.0, 1.0 - p1, 1.0)]
            for i, cid in enumerate(sec_ids):
                y0 = i * step
                y1 = 1.0 if i == n - 1 else (i + 1) * step
                cells.append(_cell(cid, 0.0, y0, p1, y1 - y0))
            dividers = [_div("d1", "v", p1, neg_cell=all_sec, pos_cell="A", role_1n=mode, feather_px=0.0)]
            for k in range(1, n):
                dividers.append(_div(
                    "d%d" % (k + 1), "h", k * step,
                    span=[0.0, p1],
                    neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
                    curve="straight", feather_px=0.0,
                ))
            return cells, dividers
        if mode == "bottom1_top":
            p1 = 0.40
            cells = [_cell("A", 0.0, p1, 1.0, 1.0 - p1)]
            for i, cid in enumerate(sec_ids):
                x0 = i * step
                x1 = 1.0 if i == n - 1 else (i + 1) * step
                cells.append(_cell(cid, x0, 0.0, x1 - x0, p1))
            dividers = [_div("d1", "h", p1, neg_cell=all_sec, pos_cell="A", role_1n=mode, feather_px=0.0)]
            for k in range(1, n):
                dividers.append(_div(
                    "d%d" % (k + 1), "v", k * step,
                    span=[0.0, p1],
                    neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
                    curve="straight", feather_px=0.0,
                ))
            return cells, dividers
        cells = [_cell("A", 0.0, 0.0, 1.0, 0.5)]
        for i, cid in enumerate(sec_ids):
            x0 = i * step
            x1 = 1.0 if i == n - 1 else (i + 1) * step
            cells.append(_cell(cid, x0, 0.5, x1 - x0, 0.5))
        dividers = [_div("d1", "h", 0.5, neg_cell="A", pos_cell=all_sec, role_1n=mode)]
        for k in range(1, n):
            dividers.append(_div(
                "d%d" % (k + 1), "v", k * step,
                span=[0.5, 1.0],
                neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
                curve="straight",
            ))
        return cells, dividers
    cells, dividers = preset_geometry("top_bottom")
    return cells, dividers


def cells_overlap_area(a, b) -> float:
    ax, ay, aw, ah = [float(v) for v in a]
    bx, by, bw, bh = [float(v) for v in b]
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def _clamp_pos(pos, lo=_POS_MIN, hi=_POS_MAX) -> float:
    try:
        p = float(pos)
    except (TypeError, ValueError):
        logger.warning("divider.pos 非法: %r", pos)
        return 0.5
    if not math.isfinite(p):
        logger.warning("divider.pos 非有限: %r", pos)
        return 0.5
    return max(lo, min(hi, p))


def _norm_border(raw) -> dict:
    out = dict(DEFAULT_BORDER)
    if not isinstance(raw, dict):
        return out
    style = str(raw.get("style") or "none")
    if style not in BORDER_STYLES:
        logger.warning("未知描边样式 %s，回退 none", style)
        style = "none"
    out["style"] = style
    try:
        out["width_px"] = max(0.0, float(raw.get("width_px", out["width_px"])))
    except (TypeError, ValueError):
        logger.warning("border.width_px 非法: %r", raw.get("width_px"))
    if raw.get("color"):
        out["color"] = str(raw["color"])
    if raw.get("color2"):
        out["color2"] = str(raw["color2"])
    curve = str(raw.get("curve") or "straight")
    if curve not in BORDER_CURVES:
        logger.warning("未知描边曲线 %s，回退 straight", curve)
        curve = "straight"
    out["curve"] = curve
    try:
        out["shadow_px"] = max(0.0, float(raw.get("shadow_px", out["shadow_px"])))
    except (TypeError, ValueError):
        logger.warning("border.shadow_px 非法: %r", raw.get("shadow_px"))
    try:
        out["glass_sigma"] = max(0.1, float(raw.get("glass_sigma", out["glass_sigma"])))
    except (TypeError, ValueError):
        logger.warning("border.glass_sigma 非法: %r", raw.get("glass_sigma"))
    return out


def _norm_divider(raw, fallback_id="d1") -> dict:
    src = raw if isinstance(raw, dict) else {}
    raw_ax = str(src.get("axis") or "h")
    axis = raw_ax if raw_ax in ("h", "v", "circle", "blade") else "h"
    try:
        rot = float(src.get("rotation_deg") or 0.0)
    except (TypeError, ValueError):
        logger.warning("divider.rotation_deg 非法: %r", src.get("rotation_deg"))
        rot = 0.0
    try:
        feather = max(0.0, float(src.get("feather_px") if src.get("feather_px") is not None else 24.0))
    except (TypeError, ValueError):
        logger.warning("divider.feather_px 非法: %r", src.get("feather_px"))
        feather = 24.0
    d = {
        "id": str(src.get("id") or fallback_id),
        "axis": axis,
        "pos": _clamp_pos(src.get("pos", 0.5)),
        "rotation_deg": rot,
        "feather_px": feather,
    }
    if src.get("curve") in BORDER_CURVES:
        d["curve"] = str(src["curve"])
    if isinstance(src.get("span"), (list, tuple)) and len(src["span"]) >= 2:
        try:
            s0 = max(0.0, min(1.0, float(src["span"][0])))
            s1 = max(s0, min(1.0, float(src["span"][1])))
            d["span"] = [s0, s1]
        except (TypeError, ValueError):
            pass
    for meta_k in ("l_role", "l_side", "l_band", "ch_role", "role_1n"):
        if src.get(meta_k):
            d[meta_k] = str(src[meta_k])
    for int_k in ("l_nl", "l_nb", "ch_nt", "ch_nb", "blade_idx", "blade_count"):
        if src.get(int_k) is not None:
            try:
                d[int_k] = int(src[int_k])
            except (TypeError, ValueError):
                pass
    if axis in ("circle", "blade"):
        default_xy = 0.50 if axis == "blade" else 0.75
        try:
            d["cx"] = _clamp_pos(src.get("cx", default_xy), 0.04, 0.96)
        except (TypeError, ValueError):
            d["cx"] = default_xy
        try:
            d["cy"] = _clamp_pos(src.get("cy", d["pos"] if axis == "circle" else default_xy), 0.04, 0.96)
        except (TypeError, ValueError):
            d["cy"] = d["pos"] if axis == "circle" else default_xy
        try:
            d["r"] = max(0.04, min(0.48, float(src.get("r", 0.31 if axis == "blade" else 0.15))))
        except (TypeError, ValueError):
            d["r"] = 0.31 if axis == "blade" else 0.15
        try:
            d["aspect"] = max(0.30, min(3.00, float(src.get("aspect", 1.0) or 1.0)))
        except (TypeError, ValueError):
            d["aspect"] = 1.0
        sh = str(src.get("shape") or ("polygon" if axis == "blade" else "circle"))
        d["shape"] = sh if sh in SPLIT_SHAPES else ("polygon" if axis == "blade" else "circle")
        try:
            d["sides"] = max(3, min(64, int(src.get("sides") or SHAPE_DEFAULT_SIDES.get(d["shape"], 6))))
        except (TypeError, ValueError):
            d["sides"] = SHAPE_DEFAULT_SIDES.get(d["shape"], 6)
        if axis == "blade" or src.get("center_main"):
            if src.get("center_main"):
                d["center_main"] = True
            try:
                d["twist_deg"] = max(-75.0, min(75.0, float(src.get("twist_deg") or 0.0)))
            except (TypeError, ValueError):
                d["twist_deg"] = 0.0
        if axis == "circle":
            d["pos"] = d["cy"]
    if src.get("neg_cell"):
        d["neg_cell"] = str(src["neg_cell"])
    if src.get("pos_cell"):
        d["pos_cell"] = str(src["pos_cell"])
    return d


def normalize_split(raw) -> dict:
    base = default_split()
    if not isinstance(raw, dict):
        return base
    layout = str(raw.get("layout") or "top_bottom")
    if not is_valid_layout(layout):
        logger.warning("未知分屏布局 %s，回退 top_bottom", layout)
        layout = "top_bottom"
    out = {
        "enabled": bool(raw.get("enabled")),
        "layout": layout,
        "border": _norm_border(raw.get("border")),
        "cells": [],
        "dividers": [],
    }
    if raw.get("custom_kind"):
        out["custom_kind"] = str(raw.get("custom_kind"))
    if raw.get("custom_seed") is not None:
        try:
            out["custom_seed"] = int(raw.get("custom_seed"))
        except (TypeError, ValueError):
            pass
    if isinstance(raw.get("backdrop"), dict):
        out["backdrop"] = _norm_backdrop(raw.get("backdrop"))
    cells = raw.get("cells")
    dividers = raw.get("dividers")
    if not cells or not dividers:
        geom_c, geom_d = preset_geometry(layout)
        out["cells"] = geom_c
        out["dividers"] = geom_d
        if isinstance(dividers, list) and dividers:
            out["dividers"] = [_norm_divider(d, "d%d" % (i + 1)) for i, d in enumerate(dividers)]
            out = recompute(out)
        return out
    out["cells"] = []
    for c in cells:
        if not isinstance(c, dict):
            continue
        rect = c.get("rect") or [0, 0, 1, 1]
        if not isinstance(rect, (list, tuple)) or len(rect) < 4:
            logger.warning("cell.rect 非法: %r", rect)
            continue
        try:
            rr = [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])]
        except (TypeError, ValueError):
            logger.warning("cell.rect 非数字: %r", rect)
            continue
        cell_entry = {"id": str(c.get("id") or "?"), "rect": rr}
        circ = c.get("circle")
        if isinstance(circ, (list, tuple)) and len(circ) >= 3:
            try:
                aspect = float(circ[3]) if len(circ) >= 4 else 1.0
                rot = float(circ[4]) if len(circ) >= 5 else 0.0
                cell_entry["circle"] = [float(circ[0]), float(circ[1]), float(circ[2]), aspect, rot]
            except (TypeError, ValueError):
                pass
        out["cells"].append(cell_entry)
    out["dividers"] = [
        _norm_divider(d, "d%d" % (i + 1))
        for i, d in enumerate(dividers or [])
        if isinstance(d, dict)
    ]
    if not out["cells"] or not out["dividers"]:
        geom_c, geom_d = preset_geometry(layout)
        out["cells"] = geom_c
        out["dividers"] = geom_d
    return out


def _div_map(split):
    return {str(d.get("id")): d for d in (split.get("dividers") or [])}


def recompute(split) -> dict:
    """Rebuild cell rects from divider positions. Pure; returns a new dict."""
    sp = copy.deepcopy(normalize_split(split))
    layout = sp.get("layout") or "top_bottom"
    d = _div_map(sp)

    # 1. 中央多边形/六角形/快门孔径主屏 + 四角/穿插叶片副屏 (center_main == True)
    d1_obj = d.get("d1")
    if isinstance(d1_obj, dict) and str(d1_obj.get("axis")) == "circle" and bool(d1_obj.get("center_main")):
        cx = _clamp_pos(d1_obj.get("cx", 0.50), 0.15, 0.85)
        cy = _clamp_pos(d1_obj.get("cy", d1_obj.get("pos", 0.50)), 0.15, 0.85)
        r = max(0.08, min(0.46, float(d1_obj.get("r", 0.31))))
        aspect = max(0.30, min(3.00, float(d1_obj.get("aspect", 1.0) or 1.0)))
        rot = float(d1_obj.get("rotation_deg") or 0.0)
        sh = str(d1_obj.get("shape") or "polygon")
        if sh not in SPLIT_SHAPES:
            sh = "polygon"
        sides = max(3, min(64, int(d1_obj.get("sides") or SHAPE_DEFAULT_SIDES.get(sh, 6))))
        twist_deg = max(-75.0, min(75.0, float(d1_obj.get("twist_deg") or 0.0)))
        pca = parse_center_aperture_layout(layout)
        m = int(d1_obj.get("blade_count") or (pca[4] if pca else max(1, len(sp.get("cells") or []) - 1)))
        m = max(1, m)
        sec_ids = [cell_id_for_index(1 + i) for i in range(m)]
        all_sec = ",".join(sec_ids)
        d1_obj.update({
            "cx": cx, "cy": cy, "pos": cy, "r": r, "aspect": aspect,
            "rotation_deg": rot, "shape": sh, "sides": sides,
            "center_main": True, "twist_deg": twist_deg, "blade_count": m,
            "neg_cell": all_sec, "pos_cell": "A",
        })
        ext_f = max(1.12, _shape_extent_factor(sh, sides))
        sa = math.sqrt(aspect)
        max_r = min(0.48, r * max(sa, 1.0 / sa) * ext_f)
        cells = [
            _cell(
                "A",
                max(0.0, cx - max_r),
                max(0.0, cy - max_r),
                min(1.0, 2.0 * max_r),
                min(1.0, 2.0 * max_r),
                circle=[cx, cy, min(0.48, r * ext_f), aspect, rot],
            )
        ]
        for i, cid in enumerate(sec_ids):
            rect_i = _blade_sector_rect(cx, cy, r, i, m, twist_deg, rot)
            cells.append(_cell(cid, rect_i[0], rect_i[1], rect_i[2], rect_i[3]))
        out_divs = [d1_obj]
        f_px = float(d1_obj.get("feather_px") if d1_obj.get("feather_px") is not None else 0.0)
        if m >= 2:
            for i in range(m):
                did = "d%d" % (i + 2)
                prev_b = d.get(did) or {}
                bf = float(prev_b.get("feather_px") if prev_b.get("feather_px") is not None else f_px)
                out_divs.append(_div(
                    did, "blade", 0.5,
                    cx=cx, cy=cy, r=r, aspect=aspect, rotation_deg=rot,
                    shape=sh, sides=sides,
                    blade_idx=i,
                    blade_count=m,
                    twist_deg=twist_deg,
                    neg_cell=sec_ids[(i - 1) % m],
                    pos_cell=sec_ids[i],
                    feather_px=bf,
                ))
        sp["cells"] = cells
        sp["dividers"] = out_divs
        return sp

    # 2. 圆窗/椭圆画中画分屏 (circle_bottomN / circle_rightN / circle_scatterN)
    circle_divs = [dv for dv in (sp.get("dividers") or []) if str(dv.get("axis")) == "circle"]
    if circle_divs:
        cells = [_cell("A", 0.0, 0.0, 1.0, 1.0)]
        for dv in circle_divs:
            cid = str(dv.get("pos_cell") or "B").split(",")[0].strip() or "B"
            cx = _clamp_pos(dv.get("cx", 0.75), 0.04, 0.96)
            cy = _clamp_pos(dv.get("cy", dv.get("pos", 0.75)), 0.04, 0.96)
            r = max(0.04, min(0.48, float(dv.get("r", 0.15))))
            aspect = max(0.30, min(3.00, float(dv.get("aspect", 1.0) or 1.0)))
            rot = float(dv.get("rotation_deg") or 0.0)
            sh = str(dv.get("shape") or "circle")
            if sh not in SPLIT_SHAPES:
                sh = "circle"
            sides = max(3, min(64, int(dv.get("sides") or SHAPE_DEFAULT_SIDES.get(sh, 64))))
            dv["cx"] = cx
            dv["cy"] = cy
            dv["r"] = r
            dv["aspect"] = aspect
            dv["rotation_deg"] = rot
            dv["shape"] = sh
            dv["sides"] = sides
            dv["pos"] = cy
            dv["neg_cell"] = "A"
            dv["pos_cell"] = cid
            ext_f = _shape_extent_factor(sh, sides)
            cr = min(0.48, r * ext_f)
            sa = math.sqrt(aspect)
            max_r = min(0.48, cr * max(sa, 1.0 / sa))
            cells.append(_cell(
                cid,
                max(0.0, cx - max_r),
                max(0.0, cy - max_r),
                min(1.0, 2.0 * max_r),
                min(1.0, 2.0 * max_r),
                circle=[cx, cy, cr, aspect, rot],
            ))
        sp["cells"] = cells
        sp["dividers"] = circle_divs
        return sp

    # 3. 四方位 L 形分屏（左/右侧 NL 个视频 + 底/顶部 NB 个视频 + 对角主视频 A）
    pl_spec = parse_l_layout(layout)
    if "d1" in d and "d2" in d and str(d["d1"].get("l_role") or "") == "main_v" and d["d1"].get("l_nl") and d["d1"].get("l_nb"):
        pl_spec = (
            int(d["d1"].get("l_nl") or 2),
            int(d["d1"].get("l_nb") or 3),
            str(d["d1"].get("l_side") or "left"),
            str(d["d1"].get("l_band") or "bottom"),
        )
    if pl_spec is not None and "d1" in d and "d2" in d:
        nl, nb = max(1, int(pl_spec[0])), max(1, int(pl_spec[1]))
        side_k = "right" if str(pl_spec[2]) == "right" else "left"
        band_k = "top" if str(pl_spec[3]) == "top" else "bottom"
        def_ps = 0.68 if side_k == "right" else 0.32
        def_pb = 0.28 if band_k == "top" else 0.72
        ps = _clamp_pos(d["d1"].get("pos", def_ps), 0.12, 0.88)
        pb = _clamp_pos(d["d2"].get("pos", def_pb), 0.12, 0.88)
        side_ids = [cell_id_for_index(1 + i) for i in range(nl)]
        band_ids = [cell_id_for_index(1 + nl + j) for j in range(nb)]
        v_span = [pb, 1.0] if band_k == "top" else [0.0, pb]
        v_len = max(0.08, v_span[1] - v_span[0])
        d1_neg = "A" if side_k == "right" else ",".join(side_ids)
        d1_pos = ",".join(side_ids) if side_k == "right" else "A"
        upper_group = ",".join(band_ids) if band_k == "top" else ("A," + ",".join(side_ids))
        lower_group = ("A," + ",".join(side_ids)) if band_k == "top" else ",".join(band_ids)
        d["d1"].update({
            "axis": "v", "pos": ps, "span": v_span,
            "neg_cell": d1_neg, "pos_cell": d1_pos,
            "l_role": "main_v", "l_nl": nl, "l_nb": nb, "l_side": side_k, "l_band": band_k,
        })
        d["d2"].update({
            "axis": "h", "pos": pb, "span": [0.0, 1.0],
            "neg_cell": upper_group, "pos_cell": lower_group,
            "l_role": "main_h", "l_nl": nl, "l_nb": nb, "l_side": side_k, "l_band": band_k,
        })
        sub_v_span = [ps, 1.0] if side_k == "right" else [0.0, ps]
        l_stops = [v_span[0]]
        for k in range(1, nl):
            did = "d%d" % (2 + k)
            def_pk = v_span[0] + (k / float(nl)) * v_len
            if did not in d:
                d[did] = _div(did, "h", def_pk, span=sub_v_span, neg_cell=side_ids[k - 1], pos_cell=side_ids[k], l_role="left_sub", curve="straight", feather_px=0.0)
            lo = l_stops[-1] + 0.04
            hi = max(lo, v_span[1] - (nl - k) * 0.04)
            pk = _clamp_pos(d[did].get("pos", def_pk), lo, hi)
            d[did].update({
                "axis": "h", "pos": pk, "span": sub_v_span,
                "neg_cell": side_ids[k - 1], "pos_cell": side_ids[k], "l_role": "left_sub",
            })
            l_stops.append(pk)
        l_stops.append(v_span[1])
        sub_h_span = [0.0, pb] if band_k == "top" else [pb, 1.0]
        b_stops = [0.0]
        for j in range(1, nb):
            did = "d%d" % (1 + nl + j)
            if did not in d:
                d[did] = _div(did, "v", j / float(nb), span=sub_h_span, neg_cell=band_ids[j - 1], pos_cell=band_ids[j], l_role="bottom_sub", curve="straight", feather_px=0.0)
            lo = b_stops[-1] + 0.04
            hi = max(lo, 0.96 - (nb - 1 - j) * 0.04)
            pj = _clamp_pos(d[did].get("pos", j / float(nb)), lo, hi)
            d[did].update({
                "axis": "v", "pos": pj, "span": sub_h_span,
                "neg_cell": band_ids[j - 1], "pos_cell": band_ids[j], "l_role": "bottom_sub",
            })
            b_stops.append(pj)
        b_stops.append(1.0)
        ax_x = 0.0 if side_k == "right" else ps
        ax_w = ps if side_k == "right" else (1.0 - ps)
        sx_x = ps if side_k == "right" else 0.0
        sx_w = (1.0 - ps) if side_k == "right" else ps
        by_y = 0.0 if band_k == "top" else pb
        by_h = pb if band_k == "top" else (1.0 - pb)
        cells = [_cell("A", ax_x, v_span[0], ax_w, v_len)]
        for i, cid in enumerate(side_ids):
            cells.append(_cell(cid, sx_x, l_stops[i], sx_w, max(0.04, l_stops[i + 1] - l_stops[i])))
        for j, cid in enumerate(band_ids):
            cells.append(_cell(cid, b_stops[j], by_y, max(0.04, b_stops[j + 1] - b_stops[j]), max(0.04, by_h)))
        sp["cells"] = cells
        ordered_ids = ["d1", "d2"] + ["d%d" % (2 + k) for k in range(1, nl)] + ["d%d" % (1 + nl + j) for j in range(1, nb)]
        sp["dividers"] = [d[k] for k in ordered_ids if k in d]
        return sp

    # 4. 横向夹心分屏（主视频 A 居中，上方 NT 条，下方 NB 条，上下分界线可旋转角度或呈双层弧线）
    pch_spec = parse_center_h_layout(layout)
    if "d1" in d and "d2" in d and str(d["d1"].get("ch_role") or "") == "top_h" and d["d1"].get("ch_nt") and d["d1"].get("ch_nb"):
        pch_spec = (
            int(d["d1"].get("ch_nt") or 1),
            int(d["d1"].get("ch_nb") or 1),
            float(d["d1"].get("rotation_deg") or 0.0),
            str(d["d1"].get("curve") or "straight"),
            str(d["d2"].get("curve") or "straight"),
        )
    if pch_spec is not None and "d1" in d and "d2" in d:
        nt, nb = max(1, int(pch_spec[0])), max(1, int(pch_spec[1]))
        pt = _clamp_pos(d["d1"].get("pos", 0.25), 0.08, 0.48)
        pb = _clamp_pos(d["d2"].get("pos", 0.75), pt + 0.16, 0.92)
        rot1 = float(d["d1"].get("rotation_deg") or 0.0)
        rot2 = float(d["d2"].get("rotation_deg") or 0.0)
        b_curve = str((sp.get("border") or {}).get("curve") or "straight")
        ct = str(d["d1"].get("curve") or (pch_spec[3] if len(pch_spec) > 3 else "") or b_curve)
        cb = str(d["d2"].get("curve") or (pch_spec[4] if len(pch_spec) > 4 else "") or b_curve)
        top_ids = [cell_id_for_index(1 + i) for i in range(nt)]
        bot_ids = [cell_id_for_index(1 + nt + j) for j in range(nb)]
        d["d1"].update({
            "axis": "h", "pos": pt, "rotation_deg": rot1,
            "neg_cell": ",".join(top_ids), "pos_cell": "A," + ",".join(bot_ids),
            "ch_role": "top_h", "ch_nt": nt, "ch_nb": nb,
        })
        if ct in BORDER_CURVES and ct != "straight":
            d["d1"]["curve"] = ct
        d["d2"].update({
            "axis": "h", "pos": pb, "rotation_deg": rot2,
            "neg_cell": "A," + ",".join(top_ids), "pos_cell": ",".join(bot_ids),
            "ch_role": "bottom_h", "ch_nt": nt, "ch_nb": nb,
        })
        if cb in BORDER_CURVES and cb != "straight":
            d["d2"]["curve"] = cb
        t_stops = [0.0]
        for k in range(1, nt):
            did = "d%d" % (2 + k)
            if did not in d:
                d[did] = _div(did, "v", k / float(nt), span=[0.0, pt], neg_cell=top_ids[k - 1], pos_cell=top_ids[k], ch_role="top_sub", curve="straight", feather_px=0.0)
            lo = t_stops[-1] + 0.04
            hi = max(lo, 0.96 - (nt - 1 - k) * 0.04)
            pk = _clamp_pos(d[did].get("pos", k / float(nt)), lo, hi)
            d[did].update({
                "axis": "v", "pos": pk, "span": [0.0, pt],
                "neg_cell": top_ids[k - 1], "pos_cell": top_ids[k], "ch_role": "top_sub",
            })
            t_stops.append(pk)
        t_stops.append(1.0)
        b_stops = [0.0]
        for j in range(1, nb):
            did = "d%d" % (1 + nt + j)
            if did not in d:
                d[did] = _div(did, "v", j / float(nb), span=[pb, 1.0], neg_cell=bot_ids[j - 1], pos_cell=bot_ids[j], ch_role="bottom_sub", curve="straight", feather_px=0.0)
            lo = b_stops[-1] + 0.04
            hi = max(lo, 0.96 - (nb - 1 - j) * 0.04)
            pj = _clamp_pos(d[did].get("pos", j / float(nb)), lo, hi)
            d[did].update({
                "axis": "v", "pos": pj, "span": [pb, 1.0],
                "neg_cell": bot_ids[j - 1], "pos_cell": bot_ids[j], "ch_role": "bottom_sub",
            })
            b_stops.append(pj)
        b_stops.append(1.0)
        pad1 = max(min(0.28, 0.55 * abs(math.tan(math.radians(rot1)))) if abs(rot1) > 0.1 else 0.0, 0.085 if ct != "straight" else 0.0)
        pad2 = max(min(0.28, 0.55 * abs(math.tan(math.radians(rot2)))) if abs(rot2) > 0.1 else 0.0, 0.085 if cb != "straight" else 0.0)
        ay0 = max(0.0, pt - pad1)
        ay1 = min(1.0, pb + pad2)
        cells = [_cell("A", 0.0, ay0, 1.0, max(0.15, ay1 - ay0))]
        for i, cid in enumerate(top_ids):
            cells.append(_cell(cid, t_stops[i], 0.0, max(0.04, t_stops[i + 1] - t_stops[i]), min(1.0, pt + pad1)))
        by0 = max(0.0, pb - pad2)
        for j, cid in enumerate(bot_ids):
            cells.append(_cell(cid, b_stops[j], by0, max(0.04, b_stops[j + 1] - b_stops[j]), max(0.08, 1.0 - by0)))
        sp["cells"] = cells
        ordered_ids = ["d1", "d2"] + ["d%d" % (2 + k) for k in range(1, nt)] + ["d%d" % (1 + nt + j) for j in range(1, nb)]
        sp["dividers"] = [d[k] for k in ordered_ids if k in d]
        return sp

    if layout in ("top_bottom", "custom") and "d1" in d:
        p = _clamp_pos(d["d1"]["pos"])
        d["d1"]["pos"] = p
        if layout == "top_bottom" or (
            len(sp.get("cells") or []) == 2
            and {c["id"] for c in sp["cells"]} == {"A", "B"}
        ):
            if str(d["d1"].get("axis")) == "v":
                sp["cells"] = [_cell("A", 0, 0, p, 1), _cell("B", p, 0, 1 - p, 1)]
            else:
                sp["cells"] = [_cell("A", 0, 0, 1, p), _cell("B", 0, p, 1, 1 - p)]
            d["d1"]["neg_cell"] = "A"
            d["d1"]["pos_cell"] = "B"
    if layout in ("left_right", "left_right_fit") and "d1" in d:
        p = _clamp_pos(d["d1"]["pos"])
        d["d1"]["pos"] = p
        sp["cells"] = [_cell("A", 0, 0, p, 1), _cell("B", p, 0, 1 - p, 1)]
        d["d1"]["neg_cell"] = "A"
        d["d1"]["pos_cell"] = "B"
    if layout == "grid_2x2" and "d1" in d and "d2" in d:
        v = _clamp_pos(d["d1"]["pos"])
        h = _clamp_pos(d["d2"]["pos"])
        d["d1"]["pos"], d["d2"]["pos"] = v, h
        sp["cells"] = [
            _cell("A", 0, 0, v, h), _cell("B", v, 0, 1 - v, h),
            _cell("C", 0, h, v, 1 - h), _cell("D", v, h, 1 - v, 1 - h),
        ]
    if layout == "grid_3x3" and all(k in d for k in ("d1", "d2", "d3", "d4")):
        v1 = _clamp_pos(d["d1"]["pos"], _POS_MIN, 0.90)
        v2 = _clamp_pos(d["d2"]["pos"], v1 + 0.04, _POS_MAX)
        h1 = _clamp_pos(d["d3"]["pos"], _POS_MIN, 0.90)
        h2 = _clamp_pos(d["d4"]["pos"], h1 + 0.04, _POS_MAX)
        d["d1"]["pos"], d["d2"]["pos"] = v1, v2
        d["d3"]["pos"], d["d4"]["pos"] = h1, h2
        xs = (0.0, v1, v2, 1.0)
        ys = (0.0, h1, h2, 1.0)
        ids = "ABCDEFGHI"
        cells = []
        for r in range(3):
            for c in range(3):
                cells.append(_cell(
                    ids[r * 3 + c],
                    xs[c], ys[r],
                    xs[c + 1] - xs[c], ys[r + 1] - ys[r],
                ))
        sp["cells"] = cells

    parsed = parse_1_to_n(layout)
    # 若是 custom 布局但内部是一主多副结构 (len(cells) >= 3 且无特殊角色)，同样按一主多副联动重算
    if (
        parsed is None
        and layout == "custom"
        and "d1" in d
        and len(sp.get("cells") or []) >= 3
        and not d["d1"].get("l_role")
        and not d["d1"].get("ch_role")
        and not d["d1"].get("center_main")
    ):
        n_custom = len(sp.get("cells") or []) - 1
        r1n = str(d["d1"].get("role_1n") or "")
        if r1n in ("top1_bottom", "bottom1_top", "left1_right", "right1_left"):
            mode_custom = r1n
        elif str(d["d1"].get("pos_cell") or "") == "A":
            mode_custom = "right1_left" if str(d["d1"].get("axis")) == "v" else "bottom1_top"
        else:
            mode_custom = "left1_right" if str(d["d1"].get("axis")) == "v" else "top1_bottom"
        parsed = (mode_custom, n_custom)

    if parsed is not None and "d1" in d:
        mode, n = parsed
        sec_ids = [cell_id_for_index(1 + i) for i in range(n)]
        all_sec = ",".join(sec_ids)
        p1 = _clamp_pos(d["d1"]["pos"])
        d["d1"]["pos"] = p1
        is_horiz_split = mode in ("left1_right", "right1_left")
        is_main_pos = mode in ("bottom1_top", "right1_left")
        d["d1"]["axis"] = "v" if is_horiz_split else "h"
        d["d1"]["neg_cell"] = all_sec if is_main_pos else "A"
        d["d1"]["pos_cell"] = "A" if is_main_pos else all_sec
        d["d1"]["role_1n"] = mode
        m = n - 1
        min_gap = min(0.04, 0.6 / max(2, n))
        sec_pos = []
        sec_axis = "h" if is_horiz_split else "v"
        sec_span = [0.0, p1] if is_main_pos else [p1, 1.0]
        for idx in range(m):
            did = "d%d" % (idx + 2)
            if did not in d:
                d[did] = _div(
                    did, sec_axis, (idx + 1) / float(n),
                    span=sec_span,
                    neg_cell=sec_ids[idx], pos_cell=sec_ids[idx + 1],
                    curve="straight",
                )
            lo = _POS_MIN if idx == 0 else (sec_pos[idx - 1] + min_gap)
            hi = _POS_MAX - (m - 1 - idx) * min_gap
            pk = _clamp_pos(d[did]["pos"], lo, max(lo, hi))
            d[did]["pos"] = pk
            d[did]["axis"] = sec_axis
            d[did]["span"] = sec_span
            d[did]["neg_cell"] = sec_ids[idx]
            d[did]["pos_cell"] = sec_ids[idx + 1]
            sec_pos.append(pk)
        stops = [0.0] + sec_pos + [1.0]
        if mode == "left1_right":
            cells = [_cell("A", 0.0, 0.0, p1, 1.0)]
            for i, cid in enumerate(sec_ids):
                cells.append(_cell(cid, p1, stops[i], 1.0 - p1, stops[i + 1] - stops[i]))
            sp["cells"] = cells
        elif mode == "right1_left":
            cells = [_cell("A", p1, 0.0, 1.0 - p1, 1.0)]
            for i, cid in enumerate(sec_ids):
                cells.append(_cell(cid, 0.0, stops[i], p1, stops[i + 1] - stops[i]))
            sp["cells"] = cells
        elif mode == "bottom1_top":
            cells = [_cell("A", 0.0, p1, 1.0, 1.0 - p1)]
            for i, cid in enumerate(sec_ids):
                cells.append(_cell(cid, stops[i], 0.0, stops[i + 1] - stops[i], p1))
            sp["cells"] = cells
        else:
            cells = [_cell("A", 0.0, 0.0, 1.0, p1)]
            for i, cid in enumerate(sec_ids):
                cells.append(_cell(cid, stops[i], p1, stops[i + 1] - stops[i], 1.0 - p1))
            sp["cells"] = cells
    sp["dividers"] = list(d.values()) if d else sp.get("dividers") or []
    # keep original divider order
    order = [str(x.get("id")) for x in (split.get("dividers") or [])] if isinstance(split, dict) else []
    if order:
        by_id = {str(x.get("id")): x for x in sp["dividers"]}
        ordered = [by_id[i] for i in order if i in by_id]
        for i, x in by_id.items():
            if i not in order:
                ordered.append(x)
        sp["dividers"] = ordered
    return sp


def apply_layout(split, layout=None) -> dict:
    """Select a preset → regenerate cells + dividers. Keeps feather/rotation of same ids."""
    src = normalize_split(split) if isinstance(split, dict) else default_split()
    name = str(layout or src.get("layout") or "top_bottom")
    if not is_valid_layout(name):
        logger.warning("未知分屏布局 %s，回退 top_bottom", name)
        name = "top_bottom"
    cells, dividers = preset_geometry(name)
    old = {str(d.get("id")): d for d in (src.get("dividers") or [])}
    same_layout = (name == src.get("layout"))
    for d in dividers:
        prev = old.get(d["id"])
        if not prev:
            continue
        if same_layout:
            try:
                d["rotation_deg"] = float(prev.get("rotation_deg") or 0.0)
            except (TypeError, ValueError):
                pass
            try:
                d["feather_px"] = max(0.0, float(prev.get("feather_px") if prev.get("feather_px") is not None else d.get("feather_px", 24.0)))
            except (TypeError, ValueError):
                pass
            if prev.get("curve") in BORDER_CURVES:
                d["curve"] = prev["curve"]
        if "pos" in prev and same_layout:
            d["pos"] = _clamp_pos(prev.get("pos"), _POS_MIN, _POS_MAX)
        if str(d.get("axis")) == "circle" and str(prev.get("axis")) == "circle" and same_layout:
            for ck, lo, hi in (("cx", 0.04, 0.96), ("cy", 0.04, 0.96), ("r", 0.04, 0.48), ("aspect", 0.30, 3.00)):
                if ck in prev and prev.get(ck) is not None:
                    try:
                        d[ck] = max(lo, min(hi, float(prev[ck])))
                    except (TypeError, ValueError):
                        pass
            if prev.get("shape") in SPLIT_SHAPES:
                d["shape"] = prev["shape"]
            if prev.get("sides") is not None:
                d["sides"] = max(3, min(64, int(prev["sides"])))
            if prev.get("twist_deg") is not None:
                d["twist_deg"] = max(-75.0, min(75.0, float(prev["twist_deg"])))
            d["pos"] = d.get("cy", d.get("pos", 0.75))
    out = {
        "enabled": bool(src.get("enabled")),
        "layout": name,
        "cells": cells,
        "dividers": dividers,
        "border": _norm_border(src.get("border")),
    }
    if isinstance(src.get("backdrop"), dict):
        out["backdrop"] = dict(src["backdrop"])
    return recompute(out)


def set_divider(split, divider_id, patch) -> dict:
    sp = normalize_split(split)
    did = str(divider_id or "")
    raw = dict(patch or {})
    found = False
    parsed = parse_1_to_n(sp.get("layout"))
    if parsed is None and sp.get("layout") == "custom" and len(sp.get("cells") or []) >= 3:
        d1_obj = (sp.get("dividers") or [{}])[0]
        if str(d1_obj.get("axis")) != "circle" and not d1_obj.get("l_role") and not d1_obj.get("ch_role"):
            r1n = str(d1_obj.get("role_1n") or "")
            if r1n in ("top1_bottom", "bottom1_top", "left1_right", "right1_left"):
                parsed = (r1n, len(sp.get("cells") or []) - 1)
            else:
                parsed = ("left1_right" if str(d1_obj.get("axis")) == "v" else "top1_bottom", len(sp.get("cells") or []) - 1)
    div_by_id = {str(x.get("id")): x for x in (sp.get("dividers") or [])}
    apply_all = bool(raw.get("apply_all_circles")) or did == "all_circles"
    for d in sp.get("dividers") or []:
        if not (apply_all and str(d.get("axis")) == "circle") and str(d.get("id")) != did:
            continue
        found = True
        if str(d.get("axis")) == "circle":
            if "cx" in raw and not apply_all:
                d["cx"] = _clamp_pos(raw["cx"], 0.04, 0.96)
            if "cy" in raw and not apply_all:
                d["cy"] = _clamp_pos(raw["cy"], 0.04, 0.96)
                d["pos"] = d["cy"]
            elif "pos" in raw:
                d["cy"] = _clamp_pos(raw["pos"], 0.04, 0.96)
                d["pos"] = d["cy"]
            if "r" in raw:
                try:
                    d["r"] = max(0.04, min(0.48, float(raw["r"])))
                except (TypeError, ValueError):
                    pass
            if "aspect" in raw:
                try:
                    d["aspect"] = max(0.30, min(3.00, float(raw["aspect"])))
                except (TypeError, ValueError):
                    pass
            if "shape" in raw and str(raw["shape"]) in SPLIT_SHAPES:
                d["shape"] = str(raw["shape"])
            if "sides" in raw:
                try:
                    d["sides"] = max(3, min(64, int(raw["sides"])))
                except (TypeError, ValueError):
                    pass
            if "twist_deg" in raw:
                try:
                    d["twist_deg"] = max(-75.0, min(75.0, float(raw["twist_deg"])))
                except (TypeError, ValueError):
                    pass
        elif str(d.get("axis")) == "blade":
            if "twist_deg" in raw and "d1" in div_by_id:
                try:
                    div_by_id["d1"]["twist_deg"] = max(-75.0, min(75.0, float(raw["twist_deg"])))
                except (TypeError, ValueError):
                    pass
            if "rotation_deg" in raw and "d1" in div_by_id:
                try:
                    div_by_id["d1"]["rotation_deg"] = float(raw["rotation_deg"])
                except (TypeError, ValueError):
                    pass
        elif "pos" in raw:
            lo, hi = _POS_MIN, _POS_MAX
            if parsed is not None and did != "d1" and did.startswith("d") and did[1:].isdigit():
                _mode, n = parsed
                idx = int(did[1:]) - 2
                min_gap = min(0.04, 0.6 / max(2, n))
                if idx > 0 and ("d%d" % (idx + 1)) in div_by_id:
                    lo = max(_POS_MIN, float(div_by_id["d%d" % (idx + 1)].get("pos", _POS_MIN)) + min_gap)
                if idx + 1 < n - 1 and ("d%d" % (idx + 3)) in div_by_id:
                    hi = min(_POS_MAX, float(div_by_id["d%d" % (idx + 3)].get("pos", _POS_MAX)) - min_gap)
                if hi < lo:
                    hi = lo
            d["pos"] = _clamp_pos(raw["pos"], lo, hi)
        if "rotation_deg" in raw:
            try:
                d["rotation_deg"] = float(raw["rotation_deg"])
            except (TypeError, ValueError):
                logger.warning("rotation_deg 非法: %r", raw["rotation_deg"])
        if "feather_px" in raw:
            try:
                d["feather_px"] = max(0.0, float(raw["feather_px"]))
            except (TypeError, ValueError):
                logger.warning("feather_px 非法: %r", raw["feather_px"])
        if "curve" in raw and str(raw["curve"]) in BORDER_CURVES:
            d["curve"] = str(raw["curve"])
        if not apply_all:
            break
    if not found:
        logger.warning("找不到分界线 %s", did)
    return recompute(sp)


def cell_by_id(split, cid):
    want = str(cid or "")
    for c in (split or {}).get("cells") or []:
        if str(c.get("id")) == want:
            return c
    return None


def pixel_rect(rect, canvas_w, canvas_h):
    from PySide6.QtCore import QRectF
    x, y, w, h = [float(v) for v in rect]
    return QRectF(x * canvas_w, y * canvas_h, w * canvas_w, h * canvas_h)


def _collect_video_tracks_for_split(task) -> list:
    """获取当前任务时间线上的所有参与分屏的非背景视频轨道（主视频轨排在第一位，排除全屏混合特效层）。"""
    tl = (task or {}).get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return []
    try:
        from cutora.render import backdrop as BG
        is_bg = BG.is_backdrop_track
    except Exception:
        is_bg = lambda _t: False
    try:
        from cutora.render.split_screen import is_fullscreen_overlay_track
    except Exception:
        is_fullscreen_overlay_track = lambda _t: False
    main_tracks = []
    other_tracks = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", "video") != "video":
            continue
        if is_bg(tr):
            continue
        if not getattr(tr, "is_main_video", False) and is_fullscreen_overlay_track(tr):
            continue
        if getattr(tr, "is_main_video", False):
            main_tracks.append(tr)
        else:
            other_tracks.append(tr)
    return main_tracks + other_tracks


def shuffle_secondary_tracks(task: dict) -> dict:
    """随机交换外部加载的视频在各个副屏小格子中的位置（保持媒体库主视频在主屏 A 不动；
    若总共只有 A、B 两格，则互换 A、B）。每次点击保证与当前排布不同！
    返回 {"swapped": [(track_id, old_cell, new_cell), ...], "cells": [...]}.
    """
    import random
    if not isinstance(task, dict):
        return {"swapped": [], "cells": []}
    v_tracks = _collect_video_tracks_for_split(task)
    if len(v_tracks) < 2:
        return {"swapped": [], "cells": []}
    sp = normalize_split(task.get("split"))
    if not sp.get("enabled"):
        sp["enabled"] = True
        task["split"] = sp
    all_cells = [str(c.get("id")) for c in (sp.get("cells") or []) if c.get("id")]
    if not all_cells:
        return {"swapped": [], "cells": []}
    main_tr = v_tracks[0]
    ext_tracks = list(v_tracks[1:])
    sec_cells = [cid for cid in all_cells if cid != "A"]

    # 如果只有 2 个格子 (A, B) 且只有 1 条外部视频，则对调主副屏以提供直观交换反馈
    if len(sec_cells) <= 1 and len(ext_tracks) == 1 and len(all_cells) == 2:
        c0 = str(getattr(main_tr, "split_cell", None) or "A")
        c1 = str(getattr(ext_tracks[0], "split_cell", None) or "B")
        if c0 == c1:
            c0, c1 = "A", "B"
        main_tr.split_cell = c1
        ext_tracks[0].split_cell = c0
        return {
            "swapped": [
                (str(getattr(main_tr, "id", "")), c0, c1),
                (str(getattr(ext_tracks[0], "id", "")), c1, c0),
            ],
            "cells": [c1, c0],
        }

    # 主视频固定在 A，外部加载的视频在副屏格子 B, C, D... 之间随机交换位置
    if "A" in all_cells:
        main_tr.split_cell = "A"
    pool = list(sec_cells) if len(sec_cells) >= len(ext_tracks) else list(sec_cells) + [None] * (len(ext_tracks) - len(sec_cells))
    old_assign = [getattr(tr, "split_cell", None) for tr in ext_tracks]
    # 保证所有外部视频如果之前未分配格子，先拿到一套完整副屏格子池
    if len(pool) >= 2:
        new_pool = list(pool)
        for _attempt in range(16):
            random.shuffle(new_pool)
            candidate = new_pool[:len(ext_tracks)]
            if candidate != old_assign:
                break
        else:
            # 兜底循环移位一位，确保 100% 发生变化
            new_pool = pool[1:] + pool[:1]
            candidate = new_pool[:len(ext_tracks)]
    else:
        candidate = pool[:len(ext_tracks)]

    swapped = []
    for tr, old_c, new_c in zip(ext_tracks, old_assign, candidate):
        tr.split_cell = new_c
        swapped.append((str(getattr(tr, "id", "")), old_c, new_c))
    return {"swapped": swapped, "cells": candidate}


def apply_custom_creative_split(
    task: dict,
    *,
    mode: str = "random",
    n_sec: int | None = None,
    main_pos: float | None = None,
    rot_deg: float | None = None,
    curve: str | None = None,
    border_style: str | None = None,
    circle_r: float | None = None,
    shape: str | None = None,
    sides: int | None = None,
    twist_deg: float | None = None,
) -> dict:
    """全方位「自定义创意分屏」引擎：
    采用完整、优美、富有层次感与设计感的分屏形式：
    - 多层次弧线分屏（上主下弧、下主上弧、双层同向层叠弧、双层对称怀抱弧、流线S弧、侧翼流线弧）
    - 四方位 L 形黄金分屏（右上主、左上主、右下主、左下主）
    - 横向动感斜切夹心分屏
    - 中央圆窗 / 六角孔径 / 弧叶快门分屏
    - 底部 / 右侧圆窗画中画
    并且自动把时间线上的视频轨道依次分配进 A, B, C, D... 格子，严格遵循时间线轨道叠压顺序。
    """
    if not isinstance(task, dict):
        return default_split()
    v_tracks = _collect_video_tracks_for_split(task)
    auto_n_sec = max(1, len(v_tracks) - 1)
    n = max(1, int(n_sec)) if (n_sec is not None and int(n_sec) >= 1) else auto_n_sec

    prev_sp = task.get("split") if isinstance(task.get("split"), dict) else {}
    seed = int(prev_sp.get("custom_seed") or 0) + 1

    chosen_mode = str(mode or "random")
    recipe_name = chosen_mode
    r_bcol = "#E5A93C"
    r_bw = 6
    r_feather = 0.0

    if chosen_mode == "random":
        recipes = [
            # (kind, main_pos, rot_deg, curve, bstyle, bcol, bw, feather, sh, sds, tw, title)
            ("straight_top", 0.58, 0.0, "straight", "gradient", "#F3C668", 7, 0.0, "circle", 64, 0.0, "✨ 经典黄金直线 · 上主下副"),
            ("straight_bottom", 0.40, 0.0, "straight", "gradient", "#88E0EF", 7, 0.0, "circle", 64, 0.0, "✨ 经典苍穹直线 · 下主上副"),
            ("center_h", 0.25, 0.0, "straight", "gradient", "#F3C668", 7, 0.0, "circle", 64, 0.0, "📐 经典上下双栏夹心 · 主视频居中"),
            ("center_h_tilt", 0.24, 8.0, "straight", "gradient", "#F3C668", 7, 0.0, "circle", 64, 0.0, "🎞️ 动感斜切夹心 · 主视频居中+上下斜线"),
            ("center_h_tilt", 0.24, -8.0, "straight", "solid", "#E5A93C", 6, 0.0, "circle", 64, 0.0, "🎞️ 反斜切夹心 · 主视频居中+上下斜线"),
            ("l_shape", 0.72, 0.0, "straight", "solid", "#E5A93C", 6, 0.0, "circle", 64, 0.0, "📐 L形黄金分屏 · 右上主屏+左副下副"),
            ("l_right_bottom", 0.72, 0.0, "straight", "gradient", "#F3C668", 6, 0.0, "circle", 64, 0.0, "📐 反L形黄金分屏 · 左上主屏+右副下副"),
            ("l_left_top", 0.28, 0.0, "straight", "solid", "#E5A93C", 6, 0.0, "circle", 64, 0.0, "📐 顶L形黄金分屏 · 右下主屏+左副上副"),
            ("slash_left", 0.56, 0.0, "straight", "solid", "#E5A93C", 6, 0.0, "circle", 64, 0.0, "📐 侧翼直线分屏 · 左主右副"),
            ("slash_right", 0.44, 0.0, "straight", "gradient", "#F3C668", 6, 0.0, "circle", 64, 0.0, "📐 侧翼直线分屏 · 右主左副"),
            ("hex_center", 0.50, 0.0, "straight", "glass", "#F5D082", 9, 0.0, "polygon", 64, 0.0, "⭕ 中央圆窗主屏+四角毛玻璃分屏"),
            ("hex_center", 0.50, 0.0, "straight", "solid", "#E5A93C", 6, 0.0, "polygon", 6, 0.0, "⬢ 六角中主+四角副屏 (分段可调至圆)"),
            ("shutter_iris", 0.50, 0.0, "straight", "solid", "#E5A93C", 6, 0.0, "octagon", 8, 38.0, "📷 旋风快门 · 八角电影虹膜分屏"),
            ("circle_bottom", 0.76, 0.0, "straight", "solid", "#F3C668", 6, 0.0, "circle", 64, 0.0, "⭕ 底部圆窗画中画分屏"),
            ("circle_right", 0.78, 0.0, "straight", "gradient", "#E5A93C", 6, 0.0, "circle", 64, 0.0, "⭕ 右侧圆窗画中画分屏"),
            ("slash_top", 0.58, 8.0, "straight", "gradient", "#E5A93C", 7, 0.0, "circle", 64, 0.0, "📐 8° 黄金斜切 · 上主下副"),
            ("slash_bottom", 0.40, -8.0, "straight", "shadow", "#E5A93C", 7, 0.0, "circle", 64, 0.0, "📐 -8° 黄金斜切 · 下主上副"),
        ]
        pick = recipes[(seed - 1) % len(recipes)]
        chosen_mode, r_pos, r_rot, r_curve, r_bstyle, r_bcol, r_bw, r_feather, r_sh, r_sds, r_tw, recipe_name = pick
        if main_pos is None:
            main_pos = r_pos
        if rot_deg is None:
            rot_deg = r_rot
        if curve is None:
            curve = r_curve
        if border_style is None:
            border_style = r_bstyle
        if shape is None:
            shape = r_sh
        if sides is None:
            sides = r_sds
        if twist_deg is None:
            twist_deg = r_tw

    b_style = border_style or str((prev_sp.get("border") or {}).get("style") or "solid")
    if b_style == "none":
        b_style = "solid"
    b_curve = curve or "straight"
    out_layout = "custom"

    if chosen_mode in ("l_shape", "l_left2_bottom3", "l_left2_bottom2", "l_right2_bottom2", "l_left1_bottom2", "l_right_bottom", "l_left_top", "l_right1_bottom2", "l_left1_top2"):
        side_k = "right" if "right" in chosen_mode else "left"
        band_k = "top" if "top" in chosen_mode else "bottom"
        if chosen_mode == "l_left2_bottom3" or n >= 5:
            nl = max(2, n // 2) if chosen_mode != "l_left2_bottom3" else 2
            nb = max(3, n - nl) if chosen_mode != "l_left2_bottom3" else 3
        elif n <= 3:
            nl, nb = 1, max(1, n - 1)
        elif n == 4:
            nl, nb = 2, 2
        else:
            nl, nb = 2, 3
        cells, dividers = _l_preset_geometry(nl, nb, side=side_k, band=band_k)
        out_layout = "l_%s%d_%s%d" % (side_k, nl, band_k, nb)
        b_curve = "straight"
    elif chosen_mode in ("center_h", "center_h_tilt", "center_h_wave", "center_h_arc", "center_h_embrace", "center_h_swave"):
        nt = max(1, n // 2) if n >= 2 else 1
        nb = max(1, n - nt) if n >= 2 else 1
        r_deg = float(rot_deg if rot_deg is not None else (8.0 if "tilt" in chosen_mode else (-6.0 if "wave" in chosen_mode else 0.0)))
        ct, cb = "straight", "straight"
        base_ch = "center_h_tilt" if abs(r_deg) > 0.5 else "center_h"
        cells, dividers = _center_h_preset_geometry(nt, nb, rot_deg=r_deg, c_top=ct, c_bottom=cb)
        out_layout = base_ch if (nt, nb) == (1, 1) else ("%s_%d_%d" % (base_ch, nt, nb))
    elif chosen_mode in ("hex_center", "poly_center", "shutter", "shutter_hex", "shutter_iris", "shutter_diamond"):
        is_shutter = chosen_mode.startswith("shutter")
        sh = shape or ("octagon" if chosen_mode == "shutter_iris" else ("diamond" if chosen_mode == "shutter_diamond" else "polygon"))
        sds = int(sides if sides is not None else SHAPE_DEFAULT_SIDES.get(sh, 6))
        tw = float(twist_deg if twist_deg is not None else (38.0 if is_shutter else 0.0))
        m_sec = max(2, n)
        cells, dividers = _center_aperture_preset_geometry(
            "shutter" if is_shutter else "hex_center",
            shape=sh, sides=sds, twist_deg=tw, n_sec=m_sec,
            r=float(circle_r or 0.31),
        )
        out_layout = ("shutter_%d" % m_sec) if is_shutter else ("hex_center_%d" % m_sec)
    elif chosen_mode.startswith("circle_") or chosen_mode.startswith("shape_"):
        cmode = "circle_right" if "right" in chosen_mode else ("circle_scatter" if "scatter" in chosen_mode else "circle_bottom")
        m_sec = max(1, n)
        cells, dividers = _circle_preset_geometry(cmode, m_sec, radius_override=circle_r, shape="circle", sides=64)
        for dv in dividers:
            dv["feather_px"] = 0.0
        b_curve = "straight"
        out_layout = "%s%d" % (cmode, m_sec)
    else:
        if chosen_mode in ("arch_up_bottom", "slash_bottom", "arc_bottom", "straight_bottom", "arch_bottom_top"):
            prefix_1n = "bottom1_top"
            def_p = 0.40
        elif chosen_mode in ("arc_left", "slash_left"):
            prefix_1n = "left1_right"
            def_p = 0.56
        elif chosen_mode in ("arc_right", "slash_right"):
            prefix_1n = "right1_left"
            def_p = 0.44
        else:
            prefix_1n = "top1_bottom"
            def_p = 0.58
        if n <= 1:
            base_lay = "left_right" if prefix_1n in ("left1_right", "right1_left") else "top_bottom"
        elif n >= 4 and prefix_1n in ("top1_bottom", "bottom1_top"):
            nl = max(2, n // 2)
            nb = max(2, n - nl)
            band_k = "top" if prefix_1n == "bottom1_top" else "bottom"
            base_lay = "l_left%d_%s%d" % (nl, band_k, nb)
        else:
            base_lay = "%s%d" % (prefix_1n, n)
        cells, dividers = preset_geometry(base_lay)
        p_val = _clamp_pos(main_pos if main_pos is not None else def_p, 0.25, 0.78)
        rot_val = float(rot_deg if rot_deg is not None else 0.0)
        if dividers and not base_lay.startswith("l_"):
            dividers[0]["pos"] = p_val
            dividers[0]["rotation_deg"] = rot_val
            if b_curve in BORDER_CURVES and b_curve != "straight":
                dividers[0]["curve"] = b_curve
            for dv in dividers:
                dv["feather_px"] = float(r_feather)
        out_layout = base_lay

    border_cfg = _norm_border({
        **(prev_sp.get("border") or {}),
        "style": b_style,
        "width_px": r_bw,
        "color": r_bcol,
        "curve": b_curve,
    })

    new_sp = recompute({
        "enabled": True,
        "layout": out_layout,
        "custom_kind": recipe_name,
        "custom_seed": seed,
        "cells": cells,
        "dividers": dividers,
        "border": border_cfg,
        "backdrop": prev_sp["backdrop"] if isinstance(prev_sp.get("backdrop"), dict) else dict(NEW_BACKDROP),
    })
    new_sp["custom_kind"] = recipe_name
    new_sp["custom_seed"] = seed
    task["split"] = new_sp

    cell_ids = [str(c.get("id")) for c in (new_sp.get("cells") or [])]
    for i, tr in enumerate(v_tracks):
        tr.split_cell = cell_ids[i] if i < len(cell_ids) else None
        for clip in getattr(tr, "clips", []) or []:
            bgf = getattr(clip, "bg_fill", None)
            if isinstance(bgf, dict) and bgf.get("mode") == "stretch":
                bgf["mode"] = "blur"
                bgf["blur_style"] = "lens"
                bgf.setdefault("blur", 45.0)
    return new_sp


def _adapt_preset_layout_for_tracks(layout: str, n_tracks: int) -> str:
    """当用户在属性面板点击按钮预设时，若当前时间线有 >= 3 条视频轨道，
    自动将该系列预设的副屏格子数适配到 n_sec = n_tracks - 1，让每一条副视频轨道都拥有独立格子！
    """
    name = str(layout or "")
    n_sec = max(1, int(n_tracks) - 1)
    if n_tracks < 2:
        return name
    if name in ("center_h", "center_h_tilt", "center_h_arc", "center_h_embrace", "center_h_swave"):
        base_ch = "center_h_tilt" if "tilt" in name else "center_h"
        if n_sec <= 2:
            return base_ch
        nt = max(1, n_sec // 2)
        nb = max(1, n_sec - nt)
        return ("%s_%d_%d" % (base_ch, nt, nb))
    if name in ("l_left2_bottom3", "l_left2_bottom2", "l_right2_bottom2", "l_left1_bottom2", "l_right1_bottom2"):
        if n_sec >= 2:
            side_k = "right" if "right" in name else "left"
            band_k = "top" if "top" in name else "bottom"
            nl = max(1, n_sec // 2)
            nb = max(1, n_sec - nl)
            return "l_%s%d_%s%d" % (side_k, nl, band_k, nb)
    if name in ("circle_bottom3", "circle_right3"):
        base_c = name[:-1]
        return "%s%d" % (base_c, max(1, n_sec))
    if name in ("hex_center_4", "shutter_4"):
        base_ap = name.rsplit("_", 1)[0]
        return "%s_%d" % (base_ap, max(2, n_sec))
    return name


def apply_split_to_task(task, layout=None, enabled=None, border=None, backdrop=None):
    if not isinstance(task, dict):
        from cutora.core.commands import CommandError
        raise CommandError("split.set_layout 需要当前任务")
    # 当用户在面板点击「自定义」按钮 (layout == 'custom') 且没传其它局部微调参数时，直接触发智能创意分屏！
    if layout == "custom" and border is None and backdrop is None and enabled in (None, True):
        return apply_custom_creative_split(task, mode="random")
    v_tracks = _collect_video_tracks_for_split(task)
    sp = normalize_split(task.get("split"))
    target_layout = _adapt_preset_layout_for_tracks(str(layout), len(v_tracks)) if layout else None
    layout_changed = bool(target_layout and str(target_layout) != str(sp.get("layout")))
    if target_layout:
        sp = apply_layout(sp, target_layout)
        for dv in sp.get("dividers") or []:
            dv["feather_px"] = 0.0
        if enabled is None:
            sp["enabled"] = True
        # Q151：新开的左右完整 → 每格各自的镜头模糊背景（老工程里已有的不动）
        if sp.get("layout") in CONTAIN_LAYOUTS and not isinstance(sp.get("backdrop"), dict):
            sp["backdrop"] = dict(NEW_BACKDROP)
        if border is None and str((sp.get("border") or {}).get("style") or "none") == "none":
            lname = str(sp.get("layout") or "")
            if lname.startswith(("hex_center", "shutter", "circle_", "l_", "center_h", "bottom1_top", "right1_left")):
                sp["border"] = _norm_border({**(sp.get("border") or {}), "style": "solid", "width_px": 6})
    if isinstance(backdrop, dict):
        merged = backdrop_spec(sp)
        merged.update({k: v for k, v in backdrop.items() if v is not None})
        sp["backdrop"] = _norm_backdrop(merged)
    if enabled is not None:
        sp["enabled"] = bool(enabled)
        if sp["enabled"] and not sp.get("cells"):
            sp = apply_layout(sp, sp.get("layout") or "top_bottom")
            sp["enabled"] = True
    if border:
        merged = dict(sp.get("border") or {})
        merged.update(dict(border))
        sp["border"] = _norm_border(merged)
    task["split"] = recompute(sp)
    # 当切换或应用布局时，自动为视频轨道分配格子（主视频 -> A，副视频 -> B, C, D...，超出格子数的轨道置 None）
    if target_layout or layout_changed:
        cell_ids = [str(c.get("id")) for c in (task["split"].get("cells") or []) if c.get("id")]
        for i, tr in enumerate(v_tracks):
            tr.split_cell = cell_ids[i] if i < len(cell_ids) else None
            for clip in getattr(tr, "clips", []) or []:
                bgf = getattr(clip, "bg_fill", None)
                if isinstance(bgf, dict) and bgf.get("mode") == "stretch":
                    bgf["mode"] = "blur"
                    bgf["blur_style"] = "lens"
                    bgf.setdefault("blur", 45.0)
    return task["split"]


def set_divider_on_task(task, divider_id, patch):
    if not isinstance(task, dict):
        from cutora.core.commands import CommandError
        raise CommandError("split.set_divider 需要当前任务")
    sp = normalize_split(task.get("split"))
    if not (sp.get("dividers") or []):
        sp = apply_layout(sp, sp.get("layout") or "top_bottom")
    task["split"] = set_divider(sp, divider_id, patch)
    task["split"]["enabled"] = True
    return task["split"]


def set_track_cell(task, track_id, cell):
    from cutora.core.commands import CommandError
    from cutora.model.track_guard import find_track

    if not isinstance(task, dict):
        raise CommandError("track.set_cell 需要当前任务")
    tid = str(track_id or "")
    tr = find_track(task, track_id=tid)
    if tr is None:
        raise CommandError("找不到轨道 %s" % tid)
    if cell in (None, "", "none", "None"):
        tr.split_cell = None
        return {"track_id": tid, "cell": None}
    cid = str(cell)
    sp = normalize_split(task.get("split"))
    ids = {str(c.get("id")) for c in (sp.get("cells") or [])}
    if ids and cid not in ids:
        raise CommandError("没有格子 %s" % cid)
    tl = task.get("timeline")
    for other in getattr(tl, "tracks", []) or []:
        if other is tr:
            continue
        if str(getattr(other, "split_cell", None) or "") == cid:
            name = str(getattr(other, "name", "") or "") or str(getattr(other, "id", ""))
            raise CommandError("格子 %s 已被 %s 占用" % (cid, name))
    tr.split_cell = cid
    return {"track_id": tid, "cell": cid}


def occupied_cells(task) -> dict:
    """cell_id → track (first occupant)."""
    out = {}
    tl = (task or {}).get("timeline") if isinstance(task, dict) else None
    for tr in getattr(tl, "tracks", []) or []:
        cid = getattr(tr, "split_cell", None)
        if cid and str(cid) not in out:
            out[str(cid)] = tr
    return out

