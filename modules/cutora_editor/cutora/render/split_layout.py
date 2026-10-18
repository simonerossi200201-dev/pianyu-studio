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
    "top1_bottom4",
    "left1_right3",
    "left1_right4",
    "center_h",
    "center_h_tilt",
    "l_left2_bottom3",
    "l_left1_bottom2",
    "hex_center_4",
    "shutter_4",
    "shape_heart_3",
    "shape_dove_3",
    "shape_clover_3",
    "shape_olive_3",
    "custom",
)

SPLIT_SHAPES = (
    "circle",
    "polygon",
    "hexagon",
    "pentagon",
    "octagon",
    "diamond",
    "heart",
    "clover",
    "dove",
    "olive",
    "star",
    "flower",
    "shield",
    "butterfly",
)

SHAPE_LABELS = {
    "circle": "⭕ 光滑圆形",
    "polygon": "⬢ 多边形 (分段可调→圆)",
    "hexagon": "⬢ 六角形",
    "pentagon": "⬠ 五边形",
    "octagon": "⯃ 八角形",
    "diamond": "◆ 菱形",
    "heart": "❤️ 心形",
    "clover": "🍀 幸运草形",
    "dove": "🕊️ 鸽子形",
    "olive": "🌿 橄榄叶形",
    "star": "⭐ 五角星形",
    "flower": "🌸 海棠花形",
    "shield": "🛡️ 盾牌形",
    "butterfly": "🦋 蝴蝶形",
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
    "left1_right3": "左1右3",
    "left1_right4": "左1右4",
    "center_h": "中主上下副",
    "center_h_tilt": "动感斜夹心",
    "center_h_auto": "中主上下（自动）",
    "l_left2_bottom3": "L形·左2下3",
    "l_left1_bottom2": "L形·左1下2",
    "l_left2_bottom2": "L形·左2下2",
    "l_shape_auto": "L形分屏（自动）",
    "hex_center_4": "六角中主四角",
    "hex_center_auto": "六角中主（自动）",
    "shutter_4": "相机快门穿插",
    "shutter_auto": "相机快门（自动）",
    "shape_heart_3": "心形分屏",
    "shape_dove_3": "鸽子形分屏",
    "shape_clover_3": "幸运草分屏",
    "shape_olive_3": "橄榄叶分屏",
    "shape_pentagon_3": "五边形分屏",
    "shape_star_3": "五角星分屏",
    "shape_flower_3": "海棠花分屏",
    "top1_bottom_auto": "上1下多（自动）",
    "left1_right_auto": "左1右多（自动）",
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
    """解析一对多布局名：'top1_bottom<N>' / 'left1_right<N>' (N >= 2) -> (mode, N)。"""
    name = str(layout or "").strip()
    for prefix in ("top1_bottom", "left1_right"):
        if name.startswith(prefix):
            tail = name[len(prefix):]
            if tail.isdigit():
                n = int(tail)
                if n >= 2:
                    return prefix, n
    return None


def parse_l_layout(layout: str | None) -> tuple[int, int] | None:
    """解析 L 形分屏布局名：'l_left<NL>_bottom<NB>' 或 'l_shape_auto' -> (n_left, n_bottom)。"""
    name = str(layout or "").strip()
    if name == "l_shape_auto":
        return 2, 3
    if name.startswith("l_left") and "_bottom" in name:
        try:
            body = name[len("l_left"):]
            s_left, s_bot = body.split("_bottom", 1)
            nl, nb = int(s_left), int(s_bot)
            if nl >= 1 and nb >= 1:
                return nl, nb
        except (TypeError, ValueError):
            pass
    return None


def parse_center_h_layout(layout: str | None) -> tuple[int, int, float] | None:
    """解析横向夹心分屏（主视频在中间，上下副屏，分界线可旋转）：
    'center_h' / 'center_h_tilt' / 'center_h<NT>_<NB>' / 'center_h_tilt<NT>_<NB>' -> (n_top, n_bottom, rot_deg)。
    """
    name = str(layout or "").strip()
    if name in ("center_h", "center_h_auto"):
        return 1, 1, 0.0
    if name in ("center_h_tilt", "center_h_tilt_auto"):
        return 1, 1, 9.0
    for prefix, def_rot in (("center_h_tilt", 9.0), ("center_h", 0.0)):
        if name.startswith(prefix):
            tail = name[len(prefix):].lstrip("_")
            if "_" in tail:
                try:
                    st, sb = tail.split("_", 1)
                    nt, nb = int(st), int(sb)
                    if nt >= 1 and nb >= 1:
                        return nt, nb, def_rot
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
    """解析圆圈/异形气泡分屏布局名：
    - 'circle_bottom<N>' / 'circle_right<N>' / 'circle_scatter<N>' (N >= 1)
    - 'shape_<shape>_<N>' / 'shape_<pos>_<shape>_<N>' -> (mode_with_shape, N)
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
        if len(parts) == 2 and parts[0] in SPLIT_SHAPES and parts[1].isdigit():
            n = max(1, int(parts[1]))
            return f"circle_bottom:{parts[0]}", n
        if len(parts) == 2 and parts[0] in SPLIT_SHAPES and parts[1] == "auto":
            return f"circle_bottom:{parts[0]}", 3
        if len(parts) == 3 and parts[0] in ("bottom", "right", "scatter") and parts[1] in SPLIT_SHAPES and parts[2].isdigit():
            n = max(1, int(parts[2]))
            return f"circle_{parts[0]}:{parts[1]}", n
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
        return ("上1下%d" if mode == "top1_bottom" else "左1右%d") % n
    pl = parse_l_layout(name)
    if pl is not None:
        nl, nb = pl
        return "L形·左%d下%d" % (nl, nb)
    pch = parse_center_h_layout(name)
    if pch is not None:
        nt, nb, rot = pch
        prefix = "斜夹心" if abs(rot) > 0.5 else "中主"
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
        sh_name = "圆圈" if sh == "circle" else SHAPE_LABELS.get(sh, sh).split()[-1]
        if cmode == "circle_right":
            return "右侧%d%s" % (cn, sh_name)
        if cmode == "circle_scatter":
            return "错落%d%s" % (cn, sh_name)
        return "底部%d%s" % (cn, sh_name)
    return name or "上下"


BORDER_STYLES = ("none", "solid", "gradient", "glass", "shadow")
BORDER_CURVES = ("straight", "wave", "arc")

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


def _circle_preset_geometry(
    mode: str,
    n: int,
    radius_override: float | None = None,
    shape: str | None = None,
    sides: int | None = None,
):
    """生成主视频全屏 + N 个圆形/异形气泡副屏的几何结构（bottom / right / scatter + 任意形状）。"""
    n = max(1, int(n))
    sh = str(shape or "circle")
    if ":" in str(mode):
        mode, sh_from_mode = str(mode).split(":", 1)
        if not shape:
            sh = sh_from_mode
    if sh not in SPLIT_SHAPES:
        sh = "circle"
    n_sides = int(sides) if sides is not None else SHAPE_DEFAULT_SIDES.get(sh, 6)
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
            cells.append(_cell(cid, max(0.0, cx - r), max(0.0, cy - r), min(1.0, 2 * r), min(1.0, 2 * r), circle=[cx, cy, r]))
            dividers.append(_div(
                "d%d" % (i + 1), "circle", cy,
                cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
                neg_cell="A", pos_cell=cid, feather_px=3.0,
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
            cells.append(_cell(cid, max(0.0, cx - r), max(0.0, cy - r), min(1.0, 2 * r), min(1.0, 2 * r), circle=[cx, cy, r]))
            dividers.append(_div(
                "d%d" % (i + 1), "circle", cy,
                cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
                neg_cell="A", pos_cell=cid, feather_px=3.0,
            ))
        return cells, dividers
    # 默认 circle_bottom：底部横排圆圈/异形气泡
    base_r = float(radius_override) if radius_override else min(0.165, 0.44 / max(1.2, float(n)))
    span_x0, span_x1 = (0.24, 0.76) if n == 2 else ((0.18, 0.82) if n == 3 else (0.14, 0.86))
    for i, cid in enumerate(sec_ids):
        cx = 0.5 if n == 1 else (span_x0 + (span_x1 - span_x0) * i / float(n - 1))
        cy = 0.76 - (0.035 if (n >= 3 and i % 2 == 1) else 0.0)
        r = max(0.06, min(0.34, base_r))
        cells.append(_cell(cid, max(0.0, cx - r), max(0.0, cy - r), min(1.0, 2 * r), min(1.0, 2 * r), circle=[cx, cy, r]))
        dividers.append(_div(
            "d%d" % (i + 1), "circle", cy,
            cx=cx, cy=cy, r=r, shape=sh, sides=n_sides,
            neg_cell="A", pos_cell=cid, feather_px=3.0,
        ))
    return cells, dividers


def _l_preset_geometry(n_left: int = 2, n_bottom: int = 3, p_left: float = 0.32, p_bottom: float = 0.72):
    """L 形分屏：主视频 A 在右上方占最大画面，左侧纵向排 n_left 个副屏，底部横向排 n_bottom 个副屏。"""
    nl = max(1, int(n_left))
    nb = max(1, int(n_bottom))
    pl = _clamp_pos(p_left, 0.14, 0.72)
    pb = _clamp_pos(p_bottom, 0.28, 0.86)
    left_ids = [cell_id_for_index(1 + i) for i in range(nl)]
    bot_ids = [cell_id_for_index(1 + nl + j) for j in range(nb)]
    cells = [_cell("A", pl, 0.0, 1.0 - pl, pb)]
    step_l = pb / float(nl)
    for i, cid in enumerate(left_ids):
        y0 = i * step_l
        y1 = pb if i == nl - 1 else (i + 1) * step_l
        cells.append(_cell(cid, 0.0, y0, pl, y1 - y0))
    step_b = 1.0 / float(nb)
    for j, cid in enumerate(bot_ids):
        x0 = j * step_b
        x1 = 1.0 if j == nb - 1 else (j + 1) * step_b
        cells.append(_cell(cid, x0, pb, x1 - x0, 1.0 - pb))

    dividers = [
        _div(
            "d1", "v", pl,
            span=[0.0, pb],
            neg_cell=",".join(left_ids),
            pos_cell="A",
            l_role="main_v",
            l_nl=nl,
            l_nb=nb,
            feather_px=10.0,
        ),
        _div(
            "d2", "h", pb,
            span=[0.0, 1.0],
            neg_cell="A," + ",".join(left_ids),
            pos_cell=",".join(bot_ids),
            l_role="main_h",
            l_nl=nl,
            l_nb=nb,
            feather_px=10.0,
        ),
    ]
    for k in range(1, nl):
        dividers.append(_div(
            "d%d" % (2 + k), "h", k * step_l,
            span=[0.0, pl],
            neg_cell=left_ids[k - 1],
            pos_cell=left_ids[k],
            l_role="left_sub",
            feather_px=8.0,
        ))
    for j in range(1, nb):
        dividers.append(_div(
            "d%d" % (1 + nl + j), "v", j * step_b,
            span=[pb, 1.0],
            neg_cell=bot_ids[j - 1],
            pos_cell=bot_ids[j],
            l_role="bottom_sub",
            feather_px=8.0,
        ))
    return cells, dividers


def _center_h_preset_geometry(
    n_top: int = 1,
    n_bottom: int = 1,
    rot_deg: float = 0.0,
    p_top: float = 0.25,
    p_bottom: float = 0.75,
):
    """横向夹心分屏（上-中-下）：主视频 A 在中间占最大画面，上方 n_top 条，下方 n_bottom 条；
    上下两条横向分界线 d1 / d2 支持任意角度旋转，更具动感。
    """
    nt = max(1, int(n_top))
    nb = max(1, int(n_bottom))
    pt = _clamp_pos(p_top, 0.10, 0.45)
    pb = _clamp_pos(p_bottom, pt + 0.20, 0.90)
    rot = float(rot_deg or 0.0)
    top_ids = [cell_id_for_index(1 + i) for i in range(nt)]
    bot_ids = [cell_id_for_index(1 + nt + j) for j in range(nb)]
    pad_y = min(0.28, 0.55 * abs(math.tan(math.radians(rot)))) if abs(rot) > 0.1 else 0.0
    ay0 = max(0.0, pt - pad_y)
    ay1 = min(1.0, pb + pad_y)
    cells = [_cell("A", 0.0, ay0, 1.0, max(0.15, ay1 - ay0))]
    step_t = 1.0 / float(nt)
    for i, cid in enumerate(top_ids):
        x0 = i * step_t
        x1 = 1.0 if i == nt - 1 else (i + 1) * step_t
        cells.append(_cell(cid, x0, 0.0, x1 - x0, min(1.0, pt + pad_y)))
    step_b = 1.0 / float(nb)
    by0 = max(0.0, pb - pad_y)
    for j, cid in enumerate(bot_ids):
        x0 = j * step_b
        x1 = 1.0 if j == nb - 1 else (j + 1) * step_b
        cells.append(_cell(cid, x0, by0, x1 - x0, max(0.10, 1.0 - by0)))

    dividers = [
        _div(
            "d1", "h", pt,
            rotation_deg=rot,
            neg_cell=",".join(top_ids),
            pos_cell="A," + ",".join(bot_ids),
            ch_role="top_h",
            ch_nt=nt,
            ch_nb=nb,
            feather_px=12.0,
        ),
        _div(
            "d2", "h", pb,
            rotation_deg=rot,
            neg_cell="A," + ",".join(top_ids),
            pos_cell=",".join(bot_ids),
            ch_role="bottom_h",
            ch_nt=nt,
            ch_nb=nb,
            feather_px=12.0,
        ),
    ]
    for k in range(1, nt):
        dividers.append(_div(
            "d%d" % (2 + k), "v", k * step_t,
            span=[0.0, pt],
            neg_cell=top_ids[k - 1],
            pos_cell=top_ids[k],
            ch_role="top_sub",
            feather_px=8.0,
        ))
    for j in range(1, nb):
        dividers.append(_div(
            "d%d" % (1 + nt + j), "v", j * step_b,
            span=[pb, 1.0],
            neg_cell=bot_ids[j - 1],
            pos_cell=bot_ids[j],
            ch_role="bottom_sub",
            feather_px=8.0,
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
    for s in range(9):
        ang = a0 + (a1 - a0) * (s / 8.0)
        xs.append(max(0.0, min(1.0, cx + r * 0.75 * math.cos(ang))))
        ys.append(max(0.0, min(1.0, cy + r * 0.75 * math.sin(ang))))
        for dist in (0.45, 0.85):
            ang_out = ang + tau * (dist / 0.85)
            xs.append(max(0.0, min(1.0, cx + dist * math.cos(ang_out))))
            ys.append(max(0.0, min(1.0, cy + dist * math.sin(ang_out))))
    x0, x1 = max(0.0, min(xs) - 0.04), min(1.0, max(xs) + 0.04)
    y0, y1 = max(0.0, min(ys) - 0.04), min(1.0, max(ys) + 0.04)
    if x1 - x0 < 0.30:
        mid_x = 0.5 * (x0 + x1)
        x0 = max(0.0, min(0.70, mid_x - 0.15))
        x1 = min(1.0, x0 + 0.30)
    if y1 - y0 < 0.30:
        mid_y = 0.5 * (y0 + y1)
        y0 = max(0.0, min(0.70, mid_y - 0.15))
        y1 = min(1.0, y0 + 0.30)
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
    """中央多边形/六角形/异形/快门孔径主屏 A + 四角/穿插叶片副屏 B, C, D...：
    - 当 twist_deg == 0 且 sides == 6 时，就是「中央六角形主屏 + 四角副屏」；
    - 当 sides 从 6 调高到 64 时，六角形可平滑过渡为光滑圆形；
    - 当 twist_deg != 0（如 36°）时，四周分界线呈相机快门叶片穿插式环绕中央主屏！
    """
    m = max(1, int(n_sec))
    sh = str(shape or "polygon")
    if sh not in SPLIT_SHAPES:
        sh = "polygon"
    n_sides = max(3, min(64, int(sides or SHAPE_DEFAULT_SIDES.get(sh, 6))))
    sec_ids = [cell_id_for_index(1 + i) for i in range(m)]
    all_sec = ",".join(sec_ids)
    sa = math.sqrt(max(0.3, min(3.0, float(aspect or 1.0))))
    max_r = min(0.48, float(r) * max(sa, 1.0 / sa) * 1.15)
    cells = [
        _cell(
            "A",
            max(0.0, cx - max_r),
            max(0.0, cy - max_r),
            min(1.0, 2.0 * max_r),
            min(1.0, 2.0 * max_r),
            circle=[cx, cy, float(r) * 1.12, float(aspect or 1.0), float(rot_deg or 0.0)],
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
            feather_px=4.0,
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
                feather_px=4.0,
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
        return _l_preset_geometry(pl[0], pl[1])
    pch = parse_center_h_layout(name)
    if pch is not None:
        return _center_h_preset_geometry(pch[0], pch[1], rot_deg=pch[2])
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
            dividers = [_div("d1", "v", 0.5, neg_cell="A", pos_cell=all_sec)]
            for k in range(1, n):
                dividers.append(_div(
                    "d%d" % (k + 1), "h", k * step,
                    neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
                ))
            return cells, dividers
        cells = [_cell("A", 0.0, 0.0, 1.0, 0.5)]
        for i, cid in enumerate(sec_ids):
            x0 = i * step
            x1 = 1.0 if i == n - 1 else (i + 1) * step
            cells.append(_cell(cid, x0, 0.5, x1 - x0, 0.5))
        dividers = [_div("d1", "h", 0.5, neg_cell="A", pos_cell=all_sec)]
        for k in range(1, n):
            dividers.append(_div(
                "d%d" % (k + 1), "v", k * step,
                neg_cell=sec_ids[k - 1], pos_cell=sec_ids[k],
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
    if isinstance(src.get("span"), (list, tuple)) and len(src["span"]) >= 2:
        try:
            s0 = max(0.0, min(1.0, float(src["span"][0])))
            s1 = max(s0, min(1.0, float(src["span"][1])))
            d["span"] = [s0, s1]
        except (TypeError, ValueError):
            pass
    for meta_k in ("l_role", "ch_role"):
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
        sa = math.sqrt(aspect)
        max_r = min(0.48, r * max(sa, 1.0 / sa) * 1.15)
        cells = [
            _cell(
                "A",
                max(0.0, cx - max_r),
                max(0.0, cy - max_r),
                min(1.0, 2.0 * max_r),
                min(1.0, 2.0 * max_r),
                circle=[cx, cy, r * 1.12, aspect, rot],
            )
        ]
        for i, cid in enumerate(sec_ids):
            rect_i = _blade_sector_rect(cx, cy, r, i, m, twist_deg, rot)
            cells.append(_cell(cid, rect_i[0], rect_i[1], rect_i[2], rect_i[3]))
        out_divs = [d1_obj]
        f_px = float(d1_obj.get("feather_px") if d1_obj.get("feather_px") is not None else 4.0)
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

    # 2. 圆圈/椭圆/异形气泡分屏 (circle_bottomN / circle_rightN / circle_scatterN / shape_* 或 custom 下的 circle dividers)
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
            sides = max(3, min(64, int(dv.get("sides") or SHAPE_DEFAULT_SIDES.get(sh, 6))))
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
            sa = math.sqrt(aspect)
            max_r = r * max(sa, 1.0 / sa)
            cells.append(_cell(
                cid,
                max(0.0, cx - max_r),
                max(0.0, cy - max_r),
                min(1.0, 2.0 * max_r),
                min(1.0, 2.0 * max_r),
                circle=[cx, cy, r, aspect, rot],
            ))
        sp["cells"] = cells
        sp["dividers"] = circle_divs
        return sp

    # 3. L 形分屏（左侧 NL 个视频 + 底部 NB 个视频 + 右上主视频 A）
    pl_spec = parse_l_layout(layout)
    if pl_spec is None and "d1" in d and "d2" in d and str(d["d1"].get("l_role") or "") == "main_v":
        pl_spec = (int(d["d1"].get("l_nl") or 2), int(d["d1"].get("l_nb") or 3))
    if pl_spec is not None and "d1" in d and "d2" in d:
        nl, nb = max(1, int(pl_spec[0])), max(1, int(pl_spec[1]))
        pl = _clamp_pos(d["d1"].get("pos", 0.32), 0.12, 0.75)
        pb = _clamp_pos(d["d2"].get("pos", 0.72), 0.25, 0.88)
        left_ids = [cell_id_for_index(1 + i) for i in range(nl)]
        bot_ids = [cell_id_for_index(1 + nl + j) for j in range(nb)]
        d["d1"].update({
            "axis": "v", "pos": pl, "span": [0.0, pb],
            "neg_cell": ",".join(left_ids), "pos_cell": "A",
            "l_role": "main_v", "l_nl": nl, "l_nb": nb,
        })
        d["d2"].update({
            "axis": "h", "pos": pb, "span": [0.0, 1.0],
            "neg_cell": "A," + ",".join(left_ids), "pos_cell": ",".join(bot_ids),
            "l_role": "main_h", "l_nl": nl, "l_nb": nb,
        })
        l_stops = [0.0]
        for k in range(1, nl):
            did = "d%d" % (2 + k)
            if did not in d:
                d[did] = _div(did, "h", (k / float(nl)) * pb, span=[0.0, pl], neg_cell=left_ids[k - 1], pos_cell=left_ids[k], l_role="left_sub")
            lo = l_stops[-1] + 0.04
            hi = max(lo, pb - (nl - k) * 0.04)
            pk = _clamp_pos(d[did].get("pos", (k / float(nl)) * pb), lo, hi)
            d[did].update({
                "axis": "h", "pos": pk, "span": [0.0, pl],
                "neg_cell": left_ids[k - 1], "pos_cell": left_ids[k], "l_role": "left_sub",
            })
            l_stops.append(pk)
        l_stops.append(pb)
        b_stops = [0.0]
        for j in range(1, nb):
            did = "d%d" % (1 + nl + j)
            if did not in d:
                d[did] = _div(did, "v", j / float(nb), span=[pb, 1.0], neg_cell=bot_ids[j - 1], pos_cell=bot_ids[j], l_role="bottom_sub")
            lo = b_stops[-1] + 0.04
            hi = max(lo, 0.96 - (nb - 1 - j) * 0.04)
            pj = _clamp_pos(d[did].get("pos", j / float(nb)), lo, hi)
            d[did].update({
                "axis": "v", "pos": pj, "span": [pb, 1.0],
                "neg_cell": bot_ids[j - 1], "pos_cell": bot_ids[j], "l_role": "bottom_sub",
            })
            b_stops.append(pj)
        b_stops.append(1.0)
        cells = [_cell("A", pl, 0.0, 1.0 - pl, pb)]
        for i, cid in enumerate(left_ids):
            cells.append(_cell(cid, 0.0, l_stops[i], pl, max(0.04, l_stops[i + 1] - l_stops[i])))
        for j, cid in enumerate(bot_ids):
            cells.append(_cell(cid, b_stops[j], pb, max(0.04, b_stops[j + 1] - b_stops[j]), 1.0 - pb))
        sp["cells"] = cells
        ordered_ids = ["d1", "d2"] + ["d%d" % (2 + k) for k in range(1, nl)] + ["d%d" % (1 + nl + j) for j in range(1, nb)]
        sp["dividers"] = [d[k] for k in ordered_ids if k in d]
        return sp

    # 4. 横向夹心分屏（主视频 A 居中，上方 NT 条，下方 NB 条，上下分界线可旋转角度）
    pch_spec = parse_center_h_layout(layout)
    if pch_spec is None and "d1" in d and "d2" in d and str(d["d1"].get("ch_role") or "") == "top_h":
        pch_spec = (int(d["d1"].get("ch_nt") or 1), int(d["d1"].get("ch_nb") or 1), float(d["d1"].get("rotation_deg") or 0.0))
    if pch_spec is not None and "d1" in d and "d2" in d:
        nt, nb, _def_rot = max(1, int(pch_spec[0])), max(1, int(pch_spec[1])), pch_spec[2]
        pt = _clamp_pos(d["d1"].get("pos", 0.25), 0.08, 0.48)
        pb = _clamp_pos(d["d2"].get("pos", 0.75), pt + 0.16, 0.92)
        rot1 = float(d["d1"].get("rotation_deg") or 0.0)
        rot2 = float(d["d2"].get("rotation_deg") or 0.0)
        top_ids = [cell_id_for_index(1 + i) for i in range(nt)]
        bot_ids = [cell_id_for_index(1 + nt + j) for j in range(nb)]
        d["d1"].update({
            "axis": "h", "pos": pt, "rotation_deg": rot1,
            "neg_cell": ",".join(top_ids), "pos_cell": "A," + ",".join(bot_ids),
            "ch_role": "top_h", "ch_nt": nt, "ch_nb": nb,
        })
        d["d2"].update({
            "axis": "h", "pos": pb, "rotation_deg": rot2,
            "neg_cell": "A," + ",".join(top_ids), "pos_cell": ",".join(bot_ids),
            "ch_role": "bottom_h", "ch_nt": nt, "ch_nb": nb,
        })
        t_stops = [0.0]
        for k in range(1, nt):
            did = "d%d" % (2 + k)
            if did not in d:
                d[did] = _div(did, "v", k / float(nt), span=[0.0, pt], neg_cell=top_ids[k - 1], pos_cell=top_ids[k], ch_role="top_sub")
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
                d[did] = _div(did, "v", j / float(nb), span=[pb, 1.0], neg_cell=bot_ids[j - 1], pos_cell=bot_ids[j], ch_role="bottom_sub")
            lo = b_stops[-1] + 0.04
            hi = max(lo, 0.96 - (nb - 1 - j) * 0.04)
            pj = _clamp_pos(d[did].get("pos", j / float(nb)), lo, hi)
            d[did].update({
                "axis": "v", "pos": pj, "span": [pb, 1.0],
                "neg_cell": bot_ids[j - 1], "pos_cell": bot_ids[j], "ch_role": "bottom_sub",
            })
            b_stops.append(pj)
        b_stops.append(1.0)
        pad1 = min(0.28, 0.55 * abs(math.tan(math.radians(rot1)))) if abs(rot1) > 0.1 else 0.0
        pad2 = min(0.28, 0.55 * abs(math.tan(math.radians(rot2)))) if abs(rot2) > 0.1 else 0.0
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
    # 若是 custom 布局但内部是一主多副结构 (len(cells) >= 3 且 d1 neg_cell == 'A' 且无特殊角色)，同样按一主多副联动重算
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
        mode_custom = "left1_right" if str(d["d1"].get("axis")) == "v" else "top1_bottom"
        parsed = (mode_custom, n_custom)

    if parsed is not None and "d1" in d:
        mode, n = parsed
        sec_ids = [cell_id_for_index(1 + i) for i in range(n)]
        all_sec = ",".join(sec_ids)
        p1 = _clamp_pos(d["d1"]["pos"])
        d["d1"]["pos"] = p1
        d["d1"]["axis"] = "v" if mode == "left1_right" else "h"
        d["d1"]["neg_cell"] = "A"
        d["d1"]["pos_cell"] = all_sec
        m = n - 1
        min_gap = min(0.04, 0.6 / max(2, n))
        sec_pos = []
        sec_axis = "h" if mode == "left1_right" else "v"
        for idx in range(m):
            did = "d%d" % (idx + 2)
            if did not in d:
                d[did] = _div(
                    did, sec_axis, (idx + 1) / float(n),
                    neg_cell=sec_ids[idx], pos_cell=sec_ids[idx + 1],
                )
            lo = _POS_MIN if idx == 0 else (sec_pos[idx - 1] + min_gap)
            hi = _POS_MAX - (m - 1 - idx) * min_gap
            pk = _clamp_pos(d[did]["pos"], lo, max(lo, hi))
            d[did]["pos"] = pk
            d[did]["axis"] = sec_axis
            d[did]["neg_cell"] = sec_ids[idx]
            d[did]["pos_cell"] = sec_ids[idx + 1]
            sec_pos.append(pk)
        stops = [0.0] + sec_pos + [1.0]
        if mode == "left1_right":
            cells = [_cell("A", 0.0, 0.0, p1, 1.0)]
            for i, cid in enumerate(sec_ids):
                cells.append(_cell(cid, p1, stops[i], 1.0 - p1, stops[i + 1] - stops[i]))
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
    """获取当前任务时间线上的所有非背景视频轨道（主视频轨排在第一位）。"""
    tl = (task or {}).get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return []
    try:
        from cutora.render import backdrop as BG
        is_bg = BG.is_backdrop_track
    except Exception:
        is_bg = lambda _t: False
    main_tracks = []
    other_tracks = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", "video") != "video":
            continue
        if is_bg(tr):
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
    支持：
    - L 形分屏（左侧 2 个 + 底部 3 个，或按轨道数自动 L 形）
    - 横向动感夹心分屏（主视频在中间，上 1 下 1 或上 N 下 M，上下分割线可旋转角度）
    - 六角形/多边形中心主屏 + 四角副屏（可调节分段数，平滑过渡到光滑圆形）
    - 相机快门穿插式分屏（六角快门 / 八角弧叶快门 / 菱形快门 / 心形快门，穿插角度可调）
    - 多样化异形气泡分屏（心形 / 五边形 / 六角形 / 鸽子形 / 幸运草形 / 橄榄叶形 / 五角星 / 海棠花 / 蝴蝶形）
    - 斜切 / 波浪 / 电影弧线分屏
    并且自动把时间线上的视频轨道依次分配进 A, B, C, D... 格子。
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
    r_feather = 6.0

    if chosen_mode == "random":
        # 18 种大师级创意分屏模板库，每次点击随机轮换，全部兼容所有新形式！
        recipes = [
            # (kind, main_pos, rot_deg, curve, bstyle, bcol, bw, feather, sh, sds, tw, title)
            ("l_shape", 0.72, 0.0, "straight", "solid", "#E5A93C", 6, 8.0, "circle", 6, 0.0, "📐 L形黄金分屏 · 右上主屏+左副下副"),
            ("center_h_tilt", 0.24, 9.0, "straight", "gradient", "#F3C668", 7, 10.0, "circle", 6, 0.0, "🎞️ 动感斜切夹心 · 主视频居中+上下斜线"),
            ("hex_center", 0.50, 0.0, "straight", "solid", "#E5A93C", 6, 4.0, "polygon", 6, 0.0, "⬢ 六角中主+四角副屏 (分段可调至圆)"),
            ("shutter_hex", 0.50, 0.0, "straight", "gradient", "#F3C668", 6, 4.0, "polygon", 6, 38.0, "📷 相机快门穿插 · 六角孔径叶片分屏"),
            ("shutter_iris", 0.50, 0.0, "arc", "solid", "#E5A93C", 6, 4.0, "octagon", 8, 42.0, "📷 旋风弧叶快门 · 八角电影虹膜分屏"),
            ("circle_bottom", 0.76, 0.0, "straight", "solid", "#FF6B8B", 6, 2.5, "heart", 64, 0.0, "❤️ 浪漫心形气泡分屏"),
            ("circle_scatter", 0.75, 0.0, "straight", "gradient", "#88E0EF", 6, 2.5, "dove", 64, 0.0, "🕊️ 灵动和平鸽异形分屏"),
            ("circle_bottom", 0.76, 0.0, "straight", "solid", "#59CE8F", 6, 2.5, "clover", 64, 0.0, "🍀 四叶幸运草创意分屏"),
            ("circle_right", 0.78, 0.0, "straight", "gradient", "#A6E3E9", 6, 2.5, "olive", 64, 0.0, "🌿 橄榄叶流线画中画分屏"),
            ("circle_bottom", 0.76, 0.0, "straight", "solid", "#F3C668", 6, 2.5, "pentagon", 5, 0.0, "⬠ 五边形几何阵列分屏"),
            ("hex_center", 0.50, 0.0, "straight", "glass", "#F5D082", 10, 6.0, "polygon", 64, 0.0, "⭕ 光滑圆中主+四角毛玻璃分屏"),
            ("shutter_diamond", 0.50, 0.0, "straight", "shadow", "#E5A93C", 7, 4.0, "diamond", 4, -36.0, "📷 菱形反向快门穿插分屏"),
            ("center_h_wave", 0.25, -7.0, "wave", "solid", "#E5A93C", 6, 12.0, "circle", 6, 0.0, "🌊 斜波浪横向夹心 · 主视频居中"),
            ("circle_scatter", 0.75, 0.0, "straight", "solid", "#FFD966", 6, 2.5, "star", 5, 0.0, "⭐ 璀璨五角星芒错落分屏"),
            ("circle_bottom", 0.76, 0.0, "straight", "gradient", "#F9B5D0", 6, 2.5, "flower", 5, 0.0, "🌸 国风海棠花窗分屏"),
            ("slash_top", 0.60, 12.0, "straight", "gradient", "#E5A93C", 8, 12.0, "circle", 6, 0.0, "🔪 12° 黄金斜切 · 上主下副"),
            ("slash_left", 0.60, -14.0, "straight", "shadow", "#E5A93C", 8, 10.0, "circle", 6, 0.0, "🔪 -14° 反斜切刀 · 左主右副"),
            ("circle_scatter", 0.75, 0.0, "straight", "gradient", "#D4ADFC", 6, 2.5, "butterfly", 64, 0.0, "🦋 灵动蝴蝶画中画分屏"),
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

    if chosen_mode in ("l_shape", "l_left2_bottom3", "l_left1_bottom2"):
        if chosen_mode == "l_left2_bottom3" or n >= 5:
            nl = max(2, n // 2) if chosen_mode == "l_shape" else 2
            nb = max(3, n - nl) if chosen_mode == "l_shape" else 3
        elif chosen_mode == "l_left1_bottom2" or n == 3:
            nl, nb = 1, 2
        elif n == 4:
            nl, nb = 2, 2
        else:
            nl, nb = 2, 3
        cells, dividers = _l_preset_geometry(nl, nb)
        out_layout = "l_left%d_bottom%d" % (nl, nb)
        b_curve = "straight"
    elif chosen_mode in ("center_h", "center_h_tilt", "center_h_wave"):
        nt = max(1, n // 2) if n >= 2 else 1
        nb = max(1, n - nt) if n >= 2 else 1
        r_deg = float(rot_deg if rot_deg is not None else (9.0 if "tilt" in chosen_mode else (-7.0 if "wave" in chosen_mode else 0.0)))
        cells, dividers = _center_h_preset_geometry(nt, nb, rot_deg=r_deg)
        out_layout = "center_h_tilt" if abs(r_deg) > 0.5 else "center_h"
    elif chosen_mode in ("hex_center", "poly_center", "shutter", "shutter_hex", "shutter_iris", "shutter_diamond"):
        is_shutter = chosen_mode.startswith("shutter")
        sh = shape or ("octagon" if chosen_mode == "shutter_iris" else ("diamond" if chosen_mode == "shutter_diamond" else "polygon"))
        sds = int(sides if sides is not None else SHAPE_DEFAULT_SIDES.get(sh, 6))
        tw = float(twist_deg if twist_deg is not None else (38.0 if is_shutter else 0.0))
        m_sec = max(4, n) if n >= 3 else 4
        cells, dividers = _center_aperture_preset_geometry(
            "shutter" if is_shutter else "hex_center",
            shape=sh, sides=sds, twist_deg=tw, n_sec=m_sec,
            r=float(circle_r or 0.31),
        )
        out_layout = ("shutter_%d" % m_sec) if is_shutter else ("hex_center_%d" % m_sec)
    elif chosen_mode.startswith("circle_") or chosen_mode.startswith("shape_"):
        sh = shape or "circle"
        if chosen_mode.startswith("shape_"):
            sh = chosen_mode[len("shape_"):]
            cmode = "circle_bottom"
        else:
            cmode = chosen_mode
        sds = int(sides if sides is not None else SHAPE_DEFAULT_SIDES.get(sh, 6))
        cells, dividers = _circle_preset_geometry(cmode, max(2, n), radius_override=circle_r, shape=sh, sides=sds)
        for dv in dividers:
            dv["feather_px"] = float(r_feather)
        b_curve = "straight"
    else:
        is_left = (chosen_mode == "slash_left")
        if n <= 1:
            base_lay = "left_right" if is_left else "top_bottom"
        else:
            base_lay = ("left1_right%d" if is_left else "top1_bottom%d") % n
        cells, dividers = preset_geometry(base_lay)
        p_val = _clamp_pos(main_pos if main_pos is not None else 0.60, 0.50, 0.82)
        rot_val = float(rot_deg if rot_deg is not None else 12.0)
        if dividers:
            dividers[0]["pos"] = p_val
            dividers[0]["rotation_deg"] = rot_val
            for dv in dividers:
                dv["feather_px"] = float(r_feather)

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

    # 自动把当前时间线上的所有视频轨按顺序放入 A, B, C, D... 格子，并将任何拉伸背景改为镜头模糊
    cell_ids = [str(c.get("id")) for c in (new_sp.get("cells") or [])]
    for i, tr in enumerate(v_tracks):
        if i < len(cell_ids):
            tr.split_cell = cell_ids[i]
        for clip in getattr(tr, "clips", []) or []:
            bgf = getattr(clip, "bg_fill", None)
            if isinstance(bgf, dict) and bgf.get("mode") == "stretch":
                bgf["mode"] = "blur"
                bgf["blur_style"] = "lens"
                bgf.setdefault("blur", 45.0)
    return new_sp


def apply_split_to_task(task, layout=None, enabled=None, border=None, backdrop=None):
    if not isinstance(task, dict):
        from cutora.core.commands import CommandError
        raise CommandError("split.set_layout 需要当前任务")
    # 当用户在面板点击「自定义」按钮 (layout == 'custom') 且没传其它局部微调参数时，直接触发智能创意分屏！
    if layout == "custom" and border is None and backdrop is None and enabled in (None, True):
        return apply_custom_creative_split(task, mode="random")
    sp = normalize_split(task.get("split"))
    layout_changed = bool(layout and str(layout) != str(sp.get("layout")))
    if layout:
        sp = apply_layout(sp, layout)
        if enabled is None:
            sp["enabled"] = True
        # Q151：新开的左右完整 → 每格各自的镜头模糊背景（老工程里已有的不动）
        if sp.get("layout") in CONTAIN_LAYOUTS and not isinstance(sp.get("backdrop"), dict):
            sp["backdrop"] = dict(NEW_BACKDROP)
        # 对于快门/六角中主/异形气泡布局，若描边是 none，自动给一条精致实线边框方便看清分界形状
        if border is None and str((sp.get("border") or {}).get("style") or "none") == "none":
            lname = str(sp.get("layout") or "")
            if lname.startswith(("hex_center", "shutter", "shape_", "l_left", "center_h_tilt")):
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
    # 当切换到多格新布局时，自动为尚未进格或旧格不存在的视频轨道分配空闲格子
    if layout_changed:
        v_tracks = _collect_video_tracks_for_split(task)
        cell_ids = [str(c.get("id")) for c in (task["split"].get("cells") or [])]
        valid_set = set(cell_ids)
        used = {str(getattr(tr, "split_cell", "") or "") for tr in v_tracks if str(getattr(tr, "split_cell", "") or "") in valid_set}
        free = [cid for cid in cell_ids if cid not in used]
        for tr in v_tracks:
            cur = str(getattr(tr, "split_cell", "") or "")
            if cur not in valid_set and free:
                tr.split_cell = free.pop(0)
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

