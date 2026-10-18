# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q138 · 把 AutoSub v6 时代的字幕预设 JSON 转成新版风格包（近似还原）。

老格式（core/preset_manager.py 导出的）有三种样子：
- ``{"default": {...}, "presets": {"名字": {...}, ...}, "default_preset_name": "..."}``
- ``{"style": {...}, "version": 1}``（default_style.json）
- 光秃秃一个样式字典（有 color_text / fontsize 这些老键）

老键 → 新 fx 的对应关系大部分已经在 stylepack.migrate_v1_bg_to_fx 里
（字色 / 描边 / 底块 / 渐变）。这里补上它不管的：投影、当前词底块 / 字色、
下划线，并把每条能转的写成用户风格包。转不过去的键列在 notes 里告诉用户。
"""
from __future__ import annotations

import json
import os
from typing import Any

# 老键里我们知道但新版没有对应画法的 —— 报出来，不假装转了
_LEGACY_UNSUPPORTED = {
    "use_underline": "下划线",
    "color_underline": "下划线颜色",
    "box_blur": "底块模糊",
    "dynamic_radius": "当前词底块动画半径",
}
_LEGACY_MARKERS = ("color_text", "color_outline", "use_box", "color_bg", "opacity_bg",
                   "dynamic_box", "color_high", "border_width")


def looks_legacy_style(style: Any) -> bool:
    if not isinstance(style, dict):
        return False
    if isinstance(style.get("fx"), dict):
        return False
    return any(k in style for k in _LEGACY_MARKERS)


def extract_presets(data: Any, fallback_name: str = "导入的预设") -> tuple[list[tuple[str, dict]], str]:
    """(名字, 样式) 列表 + 默认预设名。认得上面三种老格式和新版导出。"""
    out: list[tuple[str, dict]] = []
    default_name = ""
    if not isinstance(data, dict):
        return out, default_name
    presets = data.get("presets")
    if isinstance(presets, dict) and presets:
        default_name = str(data.get("default_preset_name") or "")
        for name, style in presets.items():
            if isinstance(style, dict):
                out.append((str(name).strip() or fallback_name, style))
        return out, default_name
    if isinstance(data.get("style"), dict):
        name = str(data.get("name") or (data.get("meta") or {}).get("name") or fallback_name)
        out.append((name, data["style"]))
        return out, default_name
    if looks_legacy_style(data) or "fontsize" in data:
        out.append((fallback_name, data))
    return out, default_name


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def convert_legacy_style(style: dict) -> tuple[dict, list[str]]:
    """老样式 → 新样式（带 fx）。返回 (新样式, 没法转的说明)。

    不改传进来的字典。已经是新版（带 fx）的原样深拷贝。
    """
    import copy
    from cutora.render.textfx.spec import is_pure_black, parse_color
    from cutora.subtitle.stylepack import migrate_v1_bg_to_fx
    src = copy.deepcopy(style if isinstance(style, dict) else {})
    notes: list[str] = []
    if isinstance(src.get("fx"), dict):
        return src, notes
    em = max(8.0, _f(src.get("fontsize"), 60))
    new = migrate_v1_bg_to_fx(src)          # 字色 / 描边 / 底块 / 渐变
    fx = new.setdefault("fx", {})
    # 投影：color_shadow / opacity_shadow / shadow_blur
    op = _f(src.get("opacity_shadow"), 0)
    if op > 0 and (src.get("shadow_blur") is not None or src.get("color_shadow")):
        col = parse_color(src.get("color_shadow") or "", "#141A26")
        if is_pure_black(col):
            col = "#141A26"
        fx["shadow"] = {
            "enabled": True,
            "distance_em": 0.06,
            "size_em": max(0.04, min(0.6, _f(src.get("shadow_blur"), 0) / em + 0.08)),
            "color": col,
            "strength": max(0.0, min(1.0, op / 255.0)),
        }
    # 当前词底块 / 字色（老版「dynamic_box」+ color_high / color_high_bg）
    if src.get("dynamic_box"):
        box_col = parse_color(src.get("color_high_bg") or "", "#E9C46A")
        if is_pure_black(box_col):
            box_col = "#E9C46A"
        fw = fx.setdefault("follow_word", {})
        fw["box"] = {
            "enabled": True,
            "color": box_col,
            "alpha": max(0.0, min(1.0, _f(src.get("opacity_high_bg"), 255) / 255.0)),
            "pad_em": max(0.0, min(1.0, _f(src.get("high_padding"), 5) / em)),
            "offset_y_em": max(-1.0, min(1.0, _f(src.get("high_offset_y"), 0) / em)),
            "radius_em": max(0.0, min(1.0, _f(src.get("dynamic_radius"), 12) / em)),
        }
        tint_col = src.get("color_high")
        if tint_col:
            tc = parse_color(tint_col, "#FFFFFF")
            fill_col = (fx.get("fill") or {}).get("colors", ["#F4EBD8"])[0]
            if tc.lower() != str(fill_col).lower():
                fw["tint"] = {"enabled": True, "type": "solid", "colors": [tc]}
    # 老的 wrap_left / wrap_right → 宽度百分比（新版只认百分比）
    wl, wr = src.get("wrap_left"), src.get("wrap_right")
    if isinstance(wl, (int, float)) and isinstance(wr, (int, float)) and wr > wl + 50:
        cw = _f(src.get("_canvas_w"), 1080) or 1080
        new["wrap_width_percent"] = int(max(10, min(100, round((wr - wl) / cw * 100))))
    for key, label in _LEGACY_UNSUPPORTED.items():
        if src.get(key) not in (None, False, 0, ""):
            notes.append("%s（%s）新版没有对应效果，已忽略" % (label, key))
    if is_pure_black(parse_color(src.get("color_text") or "", "#F4EBD8")):
        notes.append("纯黑字色不允许，已换成默认米白")
    if src.get("use_box") and is_pure_black(parse_color(src.get("color_bg") or "", "#e0c3fc")):
        notes.append("纯黑底块不允许，已换成默认淡紫")
    return new, notes


def import_legacy_file(path: str) -> dict:
    """读老预设 JSON，每条转成一个用户风格包。返回 {"imported": [...], "notes": {...}, "skipped": [...]}。"""
    from cutora.subtitle.stylepack import (
        StylepackError, _write_migrated_pack, find_user_pack_by_name,
    )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise StylepackError("读不了这个文件：%s" % exc) from exc
    base = os.path.splitext(os.path.basename(path))[0] or "导入的预设"
    rows, default_name = extract_presets(data, fallback_name=base)
    if not rows:
        raise StylepackError("这个 JSON 里没有认得出来的字幕预设（老版 presets / style，或新版风格包）。"
                             "新版风格包请用 .cutorastyle 文件导入。")
    imported: list[str] = []
    skipped: list[str] = []
    notes: dict[str, list[str]] = {}
    for name, style in rows:
        label = name
        n = 2
        while find_user_pack_by_name(label):
            label = "%s (%d)" % (name, n)
            n += 1
        try:
            converted, why = convert_legacy_style(style)
            dest = _write_migrated_pack(label, converted, is_default=False)
        except Exception as exc:  # 一条坏了不影响别的
            skipped.append("%s：%s" % (name, exc))
            continue
        if not dest:
            skipped.append(name)
            continue
        imported.append(label)
        if why:
            notes[label] = why
    return {"imported": imported, "skipped": skipped, "notes": notes,
            "default_name": default_name}
