# -*- coding: utf-8 -*-
"""T04f-6b-1 B: truth table for apply_pack_to_task / _merge_incoming_fx. No product change."""
from __future__ import annotations

import json
import os
import sys
import copy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_fx_truth.json")
MD = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_fx_truth.md")
PACK_DIR = os.path.join(REPO, "resources", "stylepacks")
FX_KEYS = (
    "fill", "stroke", "glow", "shadow", "extrude", "bg", "fonts",
    "emphasis", "anim", "scatter", "tracking_em", "letter_case",
)
ANIM_TOP = (
    "anim_in_type", "anim_in_duration", "anim_out_type", "fade_in_out_enabled",
    "holy_glow_radius", "dyn_box_anim",
)
MARKER = {
    "fill": {"type": "solid", "colors": ["#FF00AA"]},
    "stroke": {"width_em": 0.2, "color": "#00FF00"},
    "glow": {"sigma_em": 0.4, "color": "#0000FF"},
    "shadow": {"sigma_em": 0.3, "color": "#111111"},
    "extrude": {"depth_em": 0.2, "color": "#222222"},
    "bg": {"type": "box", "color": "#ABCDEF"},
    "fonts": {"body": "MarkerBody", "emphasis": "MarkerEmph"},
    "emphasis": {"auto": ["caps"], "color": "#FEED00"},
    "anim": {"in": "pop_in", "loop": "float", "out": "fade", "seed": 99},
    "scatter": {"enabled": True, "size_jitter": 0.9},
    "tracking_em": 0.42,
    "letter_case": "upper",
}


def _v1_pack():
    return {
        "id": "legacy-v1",
        "version": 1,
        "style": {
            "font_family": "Arimo",
            "color_text": "#F4F7FB",
            "fontsize": 48,
            "anim_in_type": "none",
        },
        "layout_rules": {"max_lines": 2},
        "animation": {},
        "keyword_rules": [],
    }


def _load_v2(name):
    from cutora.subtitle.stylepack import load_pack
    return load_pack(os.path.join(PACK_DIR, name))


def _fx_view(style):
    fx = style.get("fx") if isinstance(style.get("fx"), dict) else None
    out = {}
    if fx is None:
        for k in FX_KEYS:
            out[k] = None
        out["_fx_present"] = False
        return out
    out["_fx_present"] = True
    for k in FX_KEYS:
        out[k] = copy.deepcopy(fx[k]) if k in fx else "__ABSENT__"
    return out


def _classify(before_val, after_val, pack_val, incoming_had_fx):
    if after_val is None and before_val is not None and not incoming_had_fx:
        return "清空"
    if after_val == "__ABSENT__" and pack_val is None:
        return "清空(包未定义该子键)"
    if before_val == after_val and after_val != pack_val:
        return "保留"
    if pack_val is not None and after_val == pack_val:
        return "覆盖"
    if pack_val is None and after_val == before_val:
        return "保留"
    if pack_val is None and after_val != before_val:
        return "变为包默认/其它"
    return "覆盖"


def _pack_fx_view(pack):
    style = pack.get("style") or {}
    return _fx_view(style)


def run_pair(label, first, second, apply_anim):
    from cutora.subtitle.stylepack import apply_pack_to_task
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {
        "fx": copy.deepcopy(MARKER),
        "anim_in_type": "stomp",
        "anim_in_duration": 1.5,
        "anim_out_type": "fade",
        "fade_in_out_enabled": True,
        "holy_glow_radius": 12,
        "dyn_box_anim": True,
        "pos_x": 111,
        "pos_y": 222,
        "fontsize": 99,
        "color_high": "#DEAD00",
    }}
    apply_pack_to_task(task, first, apply_animation=True)
    mid = copy.deepcopy(task["style"])
    apply_pack_to_task(task, second, apply_animation=apply_anim)
    after = task["style"]
    pack_fx = _pack_fx_view(second)
    mid_fx = _fx_view(mid)
    after_fx = _fx_view(after)
    incoming_had_fx = isinstance((second.get("style") or {}).get("fx"), dict)
    rows = {}
    for k in FX_KEYS:
        rows[k] = {
            "mid": mid_fx.get(k),
            "after": after_fx.get(k),
            "pack": pack_fx.get(k),
            "verdict": _classify(
                mid_fx.get(k), after_fx.get(k), pack_fx.get(k), incoming_had_fx),
        }
    top = {}
    for k in ANIM_TOP:
        top[k] = {
            "mid": mid.get(k),
            "after": after.get(k),
            "pack_style": (second.get("style") or {}).get(k),
            "pack_animation": (second.get("animation") or {}).get(k),
        }
        if after.get(k) == mid.get(k) and k not in (second.get("style") or {}) and k not in (second.get("animation") or {}):
            top[k]["verdict"] = "保留(包未写该键)"
        elif after.get(k) != mid.get(k):
            top[k]["verdict"] = "覆盖/改写"
        else:
            top[k]["verdict"] = "相同"
    return {
        "label": label,
        "apply_animation": apply_anim,
        "fx_present_after": after_fx["_fx_present"],
        "fx": rows,
        "top_level_anim": top,
        "color_high_mid": mid.get("color_high"),
        "color_high_after": after.get("color_high"),
        "pos_after": (after.get("pos_x"), after.get("pos_y"), after.get("fontsize")),
    }


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=True)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["t04f6b1-b"])
    _ = app
    v1 = _v1_pack()
    v2a = _load_v2("pop-word")
    v2b = _load_v2("paper-torn")
    combos = [
        ("v1→v1", v1, v1),
        ("v1→v2", v1, v2a),
        ("v2→v1", v2a, v1),
        ("v2→v2", v2a, v2b),
    ]
    results = []
    for name, a, b in combos:
        for anim in (True, False):
            results.append(run_pair("%s apply_animation=%s" % (name, anim), a, b, anim))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2, default=str)

    lines = ["# T04f-6b-1 B — apply_pack_to_task 真值表", "",
             "`apply_animation=True` 是画廊勾选「同时应用该风格的动画」时的路径。",
             "默认偏好为 True；若 `user_has_custom_animation`（顶层 `anim_in_type` 不是 none）则画廊把勾选关掉。",
             ""]
    for rec in results:
        lines.append("## %s" % rec["label"])
        lines.append("")
        lines.append("| 子块 | 裁决 |")
        lines.append("|---|---|")
        for k in FX_KEYS:
            lines.append("| `fx.%s` | %s |" % (k, rec["fx"][k]["verdict"]))
        lines.append("| 顶层 anim_in_type | %s |" % rec["top_level_anim"]["anim_in_type"]["verdict"])
        lines.append("| 顶层 color_high | mid=%s → after=%s |" % (
            rec["color_high_mid"], rec["color_high_after"]))
        lines.append("| pos/fontsize after | %s |" % (rec["pos_after"],))
        lines.append("| fx 整块存在 | %s |" % rec["fx_present_after"])
        lines.append("")
    with open(MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote", OUT)
    print("wrote", MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
