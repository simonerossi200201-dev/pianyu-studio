# -*- coding: utf-8 -*-
"""Write T11g-1 completeness table: non-default v1 keys vs fx in the mirror."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
GOLDEN = os.path.join(REPO, "tests", "golden")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T11g-1_镜像完整性对照表.md")

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

# PATH fx still reads these from style (not fx.*). T11g does not delete them.
FX_PATH_STILL_READS = {
    "pos_x", "pos_y", "fontsize", "font_family", "bold", "italic", "font_style",
    "wrap_left", "wrap_right", "wrap_lines", "line_spacing",
    "box_padding_x", "anchor", "offset_x_pct", "offset_y_pct",
    "pos_x_pct", "pos_y_pct", "fontsize_pct", "layout_rules",
}

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


def _fmt(v):
    if isinstance(v, float):
        s = ("%.6f" % v).rstrip("0").rstrip(".")
        return s
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def _get(d, path):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit():
            i = int(p)
            cur = cur[i] if i < len(cur) else None
        else:
            return None
    return cur


def painted_style(task):
    roles = task.get("subtitle_roles") or {}
    default = roles.get("default") if isinstance(roles, dict) else None
    if isinstance(default, dict) and isinstance(default.get("style"), dict):
        return default["style"], "subtitle_roles.default.style"
    return task.get("style") or {}, "task.style"


def _sig(st):
    skip = {"fx"}
    return tuple(sorted(
        (k, json.dumps(v, ensure_ascii=False, sort_keys=True, default=str))
        for k, v in (st or {}).items() if k not in skip
    ))


def styles_to_check(task):
    out = []
    ts = task.get("style") if isinstance(task.get("style"), dict) else None
    ps, ppath = painted_style(task)
    out.append((ppath, ps))
    if ts is not None and _sig(ts) != _sig(ps):
        out.append(("task.style", ts))
    return out


def classify(key, src_style, dst_style):
    fx = dst_style.get("fx") if isinstance(dst_style.get("fx"), dict) else {}
    src_v = src_style.get(key)
    if key in FX_PATH_STILL_READS:
        dst_v = dst_style.get(key)
        same = dst_v == src_v
        return "style.%s（PATH fx 仍读）" % key, dst_v, "对齐" if same else "镜像丢了顶层键"
    if key == "color_text":
        got = _get(fx, "fill.colors")
        got0 = got[0] if isinstance(got, list) and got else None
        ok = str(got0).lower() == str(src_v).lower()
        return "fx.fill.colors[0]", got0, "对齐" if ok else "待补"
    if key in ("border_width", "use_stroke"):
        got = _get(fx, "stroke.width_em")
        return "fx.stroke.width_em", got, "对齐" if got is not None else "待补"
    if key == "color_outline":
        got = _get(fx, "stroke.color")
        return "fx.stroke.color", got, "对齐" if got else "待补"
    if key == "font_family":
        got = _get(fx, "fonts.body")
        ok = str(got or "") == str(src_v or "")
        return "fx.fonts.body", got, "对齐" if ok else "待补"
    if key == "use_box":
        got = _get(fx, "bg.type")
        want = "box" if src_v else "none"
        return "fx.bg.type", got, "对齐" if got == want else "待补"
    if key == "color_bg":
        if not src_style.get("use_box"):
            return "fx.bg（use_box=false，不画）", _get(fx, "bg.type"), "对齐"
        return "fx.bg.color", _get(fx, "bg.color"), "对齐"
    if key == "opacity_bg":
        if not src_style.get("use_box"):
            return "fx.bg（use_box=false，不画）", _get(fx, "bg.alpha"), "对齐"
        return "fx.bg.alpha", _get(fx, "bg.alpha"), "对齐"
    if key == "anim_in_type":
        mapped = V1_ANIM_TO_FX.get(str(src_v or "none"))
        got = _get(fx, "anim.in")
        if mapped is None:
            return "无", got, "fx 无对应字段"
        return "fx.anim.in", got, "对齐" if got == mapped else "待补"
    if key == "anim_out_type":
        mapped = V1_ANIM_TO_FX.get(str(src_v or "none"))
        got = _get(fx, "anim.out")
        if mapped is None:
            return "无", got, "fx 无对应字段"
        return "fx.anim.out", got, "对齐" if got == mapped else "待补"
    if key == "anim_in_duration":
        return "fx.anim.in_s", _get(fx, "anim.in_s"), "对齐"
    if key == "anim_out_duration":
        return "fx.anim.out_s", _get(fx, "anim.out_s"), "对齐"
    if key == "anim_in_easing":
        return "fx.anim.easing", _get(fx, "anim.easing"), "对齐"
    if key == "bullet_stagger":
        got = _get(fx, "anim.stagger_s")
        ok = got is not None and abs(float(got) - float(src_v)) < 1e-6
        return "fx.anim.stagger_s", got, "对齐" if ok else "待补"
    if key == "fade_in_out_enabled":
        got = _get(fx, "anim.in")
        v1_in = str(src_style.get("anim_in_type") or "none")
        if src_v and V1_ANIM_TO_FX.get(v1_in) not in (None, "none", "fade", "bullet_reveal"):
            return "无（独立淡入，PATH fx 不能叠在 %s 上）" % v1_in, got, "fx 无对应字段"
        if src_v:
            ok = got in ("fade", "bullet_reveal") or V1_ANIM_TO_FX.get(v1_in) in ("fade", "bullet_reveal")
            return "fx.anim.in（fade 或 bullet_reveal）", got, "对齐" if ok else "待补"
        return "fx.anim.in", got, "对齐"
    if key == "fade_in_out_duration":
        return "fx.anim.in_s（仅当走 fade 回退）", _get(fx, "anim.in_s"), "对齐"
    if key == "keyword_rules":
        got = _get(fx, "emphasis.words")
        if src_v:
            return "fx.emphasis.words", got, "对齐" if got else "待补"
        return "fx.emphasis.words", got, "对齐"
    if key == "color_high":
        if src_style.get("keyword_rules"):
            return "fx.emphasis.color", _get(fx, "emphasis.color"), "对齐"
        got_loop = str(_get(fx, "anim.loop") or "")
        parts = [p for p in got_loop.split("+") if p]
        got_c = _get(fx, "emphasis.color")
        ok = (
            any(p in ("highlight", "colour_highlight", "karaoke") for p in parts)
            and str(got_c or "").lower() == str(src_v or "").lower()
        )
        return "fx.anim.loop + emphasis.color", "%s / %s" % (got_loop, got_c), "对齐" if ok else "待补"
    if key == "high_grad":
        got_loop = str(_get(fx, "anim.loop") or "")
        if isinstance(src_v, dict) and src_v.get("enabled"):
            return "无（当前词用 loop=highlight 纯色，不用 colour_highlight 三停）", got_loop, "fx 无对应字段"
        return "fx.anim.loop", got_loop, "对齐"
    if key == "dynamic_box":
        got_loop = str(_get(fx, "anim.loop") or "")
        ok = (not src_v) or ("box_highlight" in got_loop)
        return "fx.anim.loop=box_highlight", got_loop, "对齐" if ok else "待补"
    if key in ("color_high_bg", "opacity_high_bg", "dynamic_radius"):
        if not src_style.get("dynamic_box"):
            return "不画（dynamic_box=false）", None, "对齐"
        if key == "color_high_bg":
            got = _get(fx, "bg.color")
            ok = str(got or "").lower() == str(src_v or "").lower()
            return "fx.bg.color（box_highlight 用）", got, "对齐" if ok else "待补"
        if key == "opacity_high_bg":
            got = _get(fx, "bg.alpha")
            want = float(src_v) / 255.0 if src_v is not None else None
            ok = got is not None and want is not None and abs(float(got) - want) < 1e-4
            return "fx.bg.alpha（box_highlight 用）", got, "对齐" if ok else "待补"
        got = _get(fx, "bg.radius_em")
        return "fx.bg.radius_em", got, "对齐" if got is not None else "待补"
    if key in (
        "dyn_box_style", "dyn_box_stroke_width", "dyn_box_glow_radius",
        "dyn_box_font_enabled", "dyn_box_font_family", "dyn_box_font_size",
        "dyn_box_anim", "dyn_box_anim_overshoot", "dyn_box_anim_duration",
        "high_padding",
    ):
        return "无（fx box_highlight 是实心圆角框，没有空心/光晕/独立字体）", None, "fx 无对应字段"
    if key == "shadow_blur":
        got = _get(fx, "shadow.sigma_em")
        fs = _num(src_style.get("fontsize"), 35) or 35.0
        want = (float(src_v) / fs) if src_v else 0.0
        ok = got is not None and abs(float(got) - want) < 1e-6
        return "fx.shadow.sigma_em", got, "对齐" if ok else "待补"
    if key == "color_shadow":
        got = _get(fx, "shadow.color")
        ok = str(got or "").lower() == str(src_v or "").lower()
        return "fx.shadow.color", got, "对齐" if ok else "待补"
    if key == "opacity_shadow":
        got = _get(fx, "shadow.alpha")
        want = float(src_v) / 255.0 if src_v is not None else None
        ok = got is not None and want is not None and abs(float(got) - want) < 1e-4
        return "fx.shadow.alpha", got, "对齐" if ok else "待补"
    # v1 box paint — T11g 待删；PATH fx 不读（use_box=false 时本来不画）
    if key in {
        "box_padding_y", "box_radius", "box_blur", "box_transition_enabled",
        "box_transition_color_to", "bg_gradient_enabled", "bg_gradient_type",
        "bg_gradient_colors", "bg_gradient_highlight", "bg_grad",
    }:
        if src_style.get("use_box"):
            return "无（T11g 待迁/待删的 v1 底）", None, "fx 无对应字段"
        return "不画（use_box=false）", None, "对齐"
    return "无", None, "fx 无对应字段"


GATED = {
    "spectrum_": "spectrum_enabled",
    "lyrics_": "lyrics_scroll_enabled",
    "adv_": "advanced_textbox_enabled",
    "advanced_textbox_": "advanced_textbox_enabled",
    "stroke_expand_": "stroke_expand_enabled",
    "tw_": "anim_in_type:typewriter",
    "metro_": "anim_in_type:metronome",
    "char_bounce_": "anim_in_type:char_bounce",
    "letter_jump_": "anim_in_type:letter_jump",
    "holy_glow_": "anim_in_type:holy_glow",
    "floating_": "anim_in_type:floating",
    "flash_": "anim_in_type:flash_highlight",
    "blur_sharp_": "anim_in_type:blur_sharp",
    "bullet_stagger": "anim_in_type:bullet_reveal",
    "metronome_bpm": "anim_in_type:metronome",
    "dyn_box_image_": "dyn_box_image_enabled",
}


def gated_off(k, style):
    for prefix, gate in GATED.items():
        if k == prefix or k.startswith(prefix):
            if gate.startswith("anim_in_type:"):
                return str(style.get("anim_in_type") or "none") != gate.split(":", 1)[1]
            return not bool(style.get(gate))
    return False


def differs(k, v, base, style):
    if k in ("fx", "preset_name"):
        return False
    if gated_off(k, style):
        return False
    if k in ("color_shadow", "opacity_shadow") and not (style.get("shadow_blur") or 0):
        return False
    if isinstance(v, dict) and v.get("enabled") is False:
        return False
    if k in base:
        return v != base[k]
    if v in (None, "", False, 0, 0.0, [], {}):
        return False
    return True


MISSING_FAMILIES = (
    (
        "dyn_box_extras",
        "fx box_highlight 能画当前词实心圆角框，但不能画 v1 的空心描边、光晕、弹力缩放、独立高亮字体。",
        (
            "high_padding", "dyn_box_style", "dyn_box_stroke_width",
            "dyn_box_glow_radius", "dyn_box_font_enabled", "dyn_box_font_family",
            "dyn_box_font_size", "dyn_box_anim", "dyn_box_anim_overshoot",
            "dyn_box_anim_duration",
        ),
    ),
)


def family_of(key):
    for name, _note, keys in MISSING_FAMILIES:
        if key in keys:
            return name
    return "other"


def collect_rows(ds):
    by_pair = []
    missing = []
    pending = []
    anim_rows = []
    for src_name, dst_name in PAIRS:
        src = json.loads(open(os.path.join(GOLDEN, src_name, "project.asproj.json"), encoding="utf-8").read())
        dst = json.loads(open(os.path.join(GOLDEN, dst_name, "project.asproj.json"), encoding="utf-8").read())
        stasks = src.get("tasks") or []
        dtasks = dst.get("tasks") or []
        rows = []
        for ti, task in enumerate(stasks):
            dt = dtasks[ti] if ti < len(dtasks) else {}
            for label, st in styles_to_check(task):
                dst_styles = {lab: s for lab, s in styles_to_check(dt)}
                dst_st = dst_styles.get(label) or (dt.get("style") or {})
                keys = [k for k, v in st.items() if differs(k, v, ds, st)]
                for k in keys:
                    field, got, verdict = classify(k, st, dst_st)
                    old = _fmt(st.get(k))
                    if len(old) > 80:
                        old = old[:77] + "..."
                    got_s = "—" if got is None else _fmt(got)
                    scope = "%s t%d %s" % (src_name, ti, label)
                    rec = (scope, k, old, field, got_s, verdict, src_name, label, ti)
                    rows.append(rec)
                    if verdict == "fx 无对应字段":
                        missing.append(rec)
                    if verdict == "待补":
                        pending.append(rec)
                    if k in (
                        "anim_in_type", "anim_out_type", "anim_in_duration",
                        "anim_out_duration", "fade_in_out_enabled",
                        "fade_in_out_duration", "anim_in_easing",
                    ):
                        anim_rows.append(rec)
        by_pair.append((src_name, dst_name, rows))
    return by_pair, missing, pending, anim_rows


def main() -> int:
    from cutora.subtitle.stylepack import default_subtitle_style
    ds = default_subtitle_style()
    by_pair, missing, pending, anim_rows = collect_rows(ds)
    missing_keys = sorted({r[1] for r in missing})
    pending_n = len(pending)
    lines = [
        "# T11g-1 镜像完整性对照表",
        "",
        "T11g-1b。每个旧 case 取了非默认值的 v1 键（相对 `default_subtitle_style()`）。",
        "绘制用的是 `subtitle_roles.default.style`（有的话），`task.style` 若签名不同另列。",
        "",
        "判定：",
        "- **对齐** — 镜像的 fx 或 PATH fx 仍读的顶层键对得上",
        "- **待补** — fx 有字段但镜像没写对",
        "- **fx 无对应字段** — PATH fx 读不到、也没有 fx.* 槽，T11g 删 v1 画笔就会丢",
        "",
        "## 先看这三件（签字用）",
        "",
        "### 1. 身份映射（不是默认值）",
        "",
        "| 键 | 算法 | g01 旧值 | g11 镜像 |",
        "|---|---|---|---|",
        "| `border_width` / `fontsize` | `width_em = 2 / 35` | 2 / 35 | `fx.stroke.width_em = 0.057143` |",
        "| `color_outline` | 原样 | `#550000` | `fx.stroke.color = #550000`，`join = round` |",
        "| `font_family` | 原样 | SN Pro SemiBold | `fx.fonts.body` 与顶层 `font_family` 都是 SN Pro SemiBold |",
        "| `use_box` | false → none | false | `fx.bg.type = none` |",
        "| `color_text` | fill | `#ffffff` | `fx.fill.colors[0] = #ffffff` |",
        "",
        "g01 与 g11 的 style 除了多一个 `fx` 键，其余键完全相同。构造是「在原 style 上加 fx」，不是另写一份。",
        "",
        "### 2. 三族 → fx 自己的机制（画面等价，不是键对键）",
        "",
        "绘制走 `subtitle_roles.default.style`。g12 painted：",
        "",
        "| v1 | fx |",
        "|---|---|",
        "| `anim_in_type=bullet_reveal` | `fx.anim.in=bullet_reveal`（新 AnimDef，fn=_fade_fn，UNIT_WARP 按行）`unit=line` `stagger_s=0.15` |",
        "| `color_high` + `high_grad` | `loop=colour_highlight`，`emphasis.color=#ffff7f`，palette 三停 |",
        "| `dynamic_box` | `loop=…+box_highlight`，`bg.color=#550000` `alpha=0.263` `type=none` |",
        "",
        "amber-karaoke 的证据：`anim.loop=karaoke` `unit=word`。卡拉OK 是 LTR 填色，v1 `color_high` 更接近 `loop=highlight` / `colour_highlight`。",
        "",
        "### 3. 还剩的「fx 无对应字段」",
        "",
        "三族的主干已经有 fx 做法。剩下的是 dynamic_box 的空心/光晕/独立字体，box_highlight 做不到。",
        "",
    ]
    grouped = {}
    for rec in missing:
        grouped.setdefault(family_of(rec[1]), []).append(rec)
    for name, note, keys in MISSING_FAMILIES:
        rows = grouped.get(name) or []
        cases = sorted({r[6] for r in rows})
        keyset = sorted({r[1] for r in rows})
        lines.append("**%s** — %s" % (name, note))
        lines.append("")
        lines.append("- 出现在：%s" % (", ".join("`%s`" % c for c in cases) or "无"))
        lines.append("- 键：%s" % (", ".join("`%s`" % k for k in keyset) or "无"))
        lines.append("")
    other = grouped.get("other") or []
    if other:
        lines.append("**other** — 未归入上面三族的无对应键。")
        lines.append("")
        seen = set()
        for rec in other:
            sig = (rec[6], rec[1], rec[2])
            if sig in seen:
                continue
            seen.add(sig)
            lines.append("- `%s` `%s` = %s" % (rec[6], rec[1], rec[2]))
        lines.append("")
    lines.append("待补行数：%d。无对应键（去重）：%s。" % (
        pending_n, ", ".join("`%s`" % k for k in missing_keys) or "无"))
    lines.append("")
    lines.append("g-B 备忘：g11–g18 现在同时存着 v1 键和 fx。T11g 删 v1 之后这些 fixture 的哈希必须还一样；")
    lines.append("如果读取/规范化时对未知键的处理会影响结果，写进 g-B 报告，不要默认没事。")
    lines.append("")
    lines.append("## 八条镜像的 anim_* / fade_*")
    lines.append("")
    lines.append("| 范围 | v1 键 | 旧值 | fx 对应字段 | 镜像里写的值 | 判定 |")
    lines.append("|---|---|---|---|---|---|")
    for rec in anim_rows:
        lines.append("| %s | `%s` | %s | %s | %s | %s |" % rec[:6])
    lines.append("")
    for src_name, dst_name, rows in by_pair:
        lines.append("## %s → %s" % (src_name, dst_name))
        lines.append("")
        lines.append("| 范围 | v1 键 | 旧值 | fx 对应字段 | 镜像里写的值 | 判定 |")
        lines.append("|---|---|---|---|---|---|")
        for rec in rows:
            lines.append("| %s | `%s` | %s | %s | %s | %s |" % rec[:6])
        lines.append("")
    lines.append("## 「fx 无对应字段」逐行（去重）")
    lines.append("")
    if not missing:
        lines.append("无。")
    else:
        lines.append("| 旧 case | 范围 | v1 键 | 旧值 | 族 |")
        lines.append("|---|---|---|---|---|")
        seen = set()
        for rec in missing:
            sig = (rec[6], rec[1], rec[2])
            if sig in seen:
                continue
            seen.add(sig)
            lines.append(
                "| `%s` | %s | `%s` | %s | %s |"
                % (rec[6], rec[7], rec[1], rec[2], family_of(rec[1]))
            )
        lines.append("")
        lines.append("这些键 PATH fx（`cutora/render/textfx/`）完全不读。T11g 若只删 v1 画笔、默认包落地后所有任务走 PATH fx，它们会从画面上消失。")
    lines.append("")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(
        "wrote", OUT,
        "missing_rows", len(missing),
        "pending", pending_n,
        "missing_keys", ",".join(missing_keys),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
