# -*- coding: utf-8 -*-
"""规格 31：扫 app.css 的 tabulator 规则，禁止浅色漏色；再扫 vendor midnight，
和中和清单比对，漏了就报。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "ui" / "css" / "app.css"
VENDOR = ROOT / "ui" / "vendor" / "tabulator_midnight.min.css"

BAD_COLOR = {"#000", "#000000", "#333", "#333333", "black"}
BAD_BG = {"#999", "#999999", "#ccc", "#cccccc", "#444", "#444444", "#888", "#888888"}
# #666 不在规格断言里，但 vendor 表体/行就写了它，漏出来整表发灰，覆盖检查要认。
LEAK_BG = BAD_BG | {"#666", "#666666", "#000", "#000000"}

# 规格第 1 节中和清单（裸类名 + 表头/行头那几条）
NEUTRALIZE = [
    ".tabulator-range-highlight",
    ".tabulator-range-selected",
    ".tabulator .tabulator-header .tabulator-col.tabulator-range-highlight",
    ".tabulator .tabulator-header .tabulator-col.tabulator-range-selected",
    ".tabulator-row .tabulator-cell.tabulator-range-row-header",
    ".tabulator-row.tabulator-range-highlight .tabulator-cell.tabulator-range-row-header",
    ".tabulator-row.tabulator-range-selected .tabulator-cell.tabulator-range-row-header",
]

# 这些是表格里会漏浅色、必须盖住的；footer/print/alert 不算日常用到
GRID_HINTS = (
    "range-highlight",
    "range-selected",
    "range-row-header",
    "row-even",
    "selectable:hover",
    "tabulator-selected",
    "tabulator-editing",
    "tableholder .tabulator-table",
)


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def parse_rules(css: str) -> list[tuple[str, str]]:
    css = strip_comments(css)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]+)\}", css):
        sel = " ".join(m.group(1).split())
        body = m.group(2).strip()
        if sel and body:
            out.append((sel, body))
    return out


def decls(body: str) -> list[tuple[str, str]]:
    items = []
    for part in body.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        items.append((k.strip().lower(), v.strip().split("!important")[0].strip().lower()))
    return items


def norm_color(v: str) -> str:
    v = v.strip().lower()
    if v.startswith("#") and len(v) == 4:
        v = "#" + v[1] * 2 + v[2] * 2 + v[3] * 2
    return v


def first_color_token(v: str) -> str:
    """background: red url() 里取第一个色值；rgba/var 原样返回。"""
    v = v.strip()
    m = re.match(r"(#[0-9a-f]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\)|transparent|inherit|currentcolor|var\([^)]+\)|[a-z]+)", v, re.I)
    return m.group(1) if m else v.split()[0]


def selector_has_tabulator(sel: str) -> bool:
    return "tabulator" in sel.lower()


def classes_of(sel: str) -> set[str]:
    s = re.sub(r":not\([^)]*\)", "", sel)
    s = re.sub(r"\[[^\]]*\]", "", s)
    tokens = set(re.findall(r"\.([a-zA-Z0-9_-]+)", s))
    for p in re.findall(r":([a-z-]+)", s):
        if p not in ("not", "nth-child", "before", "after"):
            tokens.add(":" + p)
    return tokens


def norm_sel(sel: str) -> str:
    return re.sub(r"\s+", " ", sel).strip()


def lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    L1, L2 = lum(a), lum(b)
    hi, lo = max(L1, L2), min(L1, L2)
    return (hi + 0.05) / (lo + 0.05)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def blend(fg: tuple[int, int, int], a: float, bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(int(round(fg[i] * a + bg[i] * (1 - a))) for i in range(3))  # type: ignore[return-value]


def main() -> int:
    failed = 0
    lines: list[str] = []

    def ok(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failed
        if cond:
            lines.append("  ok   " + name)
        else:
            failed += 1
            lines.append("  FAIL " + name + (("  " + detail) if detail else ""))

    app_text = APP_CSS.read_text(encoding="utf-8")
    vendor_text = VENDOR.read_text(encoding="utf-8")
    app_rules = parse_rules(app_text)
    vendor_rules = parse_rules(vendor_text)

    lines.append("== 1. app.css 里选择器含 tabulator 的规则：禁色 ==")
    color_hits = []
    bg_hits = []
    tab_n = 0
    for sel, body in app_rules:
        if not selector_has_tabulator(sel):
            continue
        tab_n += 1
        for k, v in decls(body):
            token = norm_color(first_color_token(v))
            if k == "color" and token in BAD_COLOR:
                color_hits.append(f"{sel} {{ {k}: {v} }}")
            if k in ("background", "background-color") and token in BAD_BG:
                bg_hits.append(f"{sel} {{ {k}: {v} }}")
    lines.append(f"  扫到 {tab_n} 条 tabulator 规则")
    ok("没有任何 color:#000 / #333 / black", not color_hits, "; ".join(color_hits[:5]))
    ok("没有任何 background:#999 / #ccc / #444 / #888", not bg_hits, "; ".join(bg_hits[:5]))
    if color_hits:
        for h in color_hits:
            lines.append("       " + h)
    if bg_hits:
        for h in bg_hits:
            lines.append("       " + h)

    lines.append("")
    lines.append("== 2. vendor midnight 里会漏浅色的规则 ==")
    vendor_leaks: list[tuple[str, str, str]] = []
    for sel, body in vendor_rules:
        for k, v in decls(body):
            token = norm_color(first_color_token(v))
            if k == "color" and token in BAD_COLOR:
                vendor_leaks.append((sel, k, v))
            elif k in ("background", "background-color") and token in LEAK_BG:
                vendor_leaks.append((sel, k, v))
    # 去重按 selector+prop
    seen = set()
    uniq = []
    for sel, k, v in vendor_leaks:
        key = (norm_sel(sel), k)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sel, k, v))
        lines.append(f"  {sel}")
        lines.append(f"      {k}: {v}")
    lines.append(f"  共 {len(uniq)} 条")

    lines.append("")
    lines.append("== 3. 和第 1 节中和清单比对 ==")
    app_sels = []
    for sel, _body in app_rules:
        for part in sel.split(","):
            app_sels.append(norm_sel(part))

    def in_app(target: str) -> bool:
        t = norm_sel(target)
        for a in app_sels:
            if a == t or t in a or a.endswith(t):
                return True
        return False

    for nsel in NEUTRALIZE:
        ok("中和清单有 " + nsel, in_app(nsel))

    # vendor 漏色里、跟表格选区/斑马/悬停相关的，必须被 app.css 盖住
    lines.append("")
    lines.append("== 4. vendor 浅色规则是否被 app.css 盖住 ==")
    app_cover: list[tuple[set[str], set[str]]] = []
    for sel, body in app_rules:
        if not selector_has_tabulator(sel):
            continue
        props = set()
        for k, v in decls(body):
            token = norm_color(first_color_token(v))
            if k == "color" and token not in BAD_COLOR:
                props.add("color")
            if k in ("background", "background-color") and token not in BAD_BG and token not in BAD_COLOR:
                props.add("background")
        if not props:
            continue
        for part in sel.split(","):
            app_cover.append((classes_of(part), props))

    missing = []
    extra_ok = []
    for sel, k, v in uniq:
        gridish = any(h in sel for h in GRID_HINTS)
        if not gridish:
            extra_ok.append((sel, k, "非选区/斑马/悬停，仅列出"))
            continue
        want = "color" if k == "color" else "background"
        tokens = classes_of(sel)
        covered = False
        for atokens, aprops in app_cover:
            if tokens <= atokens and want in aprops:
                covered = True
                break
            # 中和清单用裸类名盖：vendor 选择器里只要带上 range-highlight / range-selected
            if want in aprops and (
                ("tabulator-range-highlight" in tokens and "tabulator-range-highlight" in atokens)
                or ("tabulator-range-selected" in tokens and "tabulator-range-selected" in atokens)
            ):
                covered = True
                break
        in_neutralize = any(
            classes_of(n) <= tokens or tokens <= classes_of(n) or classes_of(n) & tokens
            for n in NEUTRALIZE
        )
        if covered:
            tag = "中和清单" if in_neutralize else "后续分层"
            extra_ok.append((sel, k, tag))
            ok(f"已覆盖 {k}  {sel[:80]}", True)
        else:
            missing.append((sel, k, v))
            ok(f"已覆盖 {k}  {sel[:80]}", False, f"{k}:{v}")

    lines.append("")
    lines.append("== 5. 中和清单未列、但表格会漏的（应靠后续分层盖住） ==")
    listed_classes = set()
    for n in NEUTRALIZE:
        listed_classes |= classes_of(n)
    for sel, k, v in uniq:
        if not any(h in sel for h in GRID_HINTS):
            continue
        toks = classes_of(sel)
        # 裸类名那几条算已列
        if toks <= listed_classes or "tabulator-range-highlight" in toks or "tabulator-range-selected" in toks:
            if ".tabulator-range-highlight" in NEUTRALIZE and "tabulator-range-highlight" in toks:
                continue
        in_list = in_app(sel.split(",")[0]) and any(norm_sel(n) in norm_sel(sel) for n in NEUTRALIZE)
        if in_list:
            continue
        # 明确不在第 1 节那 7 条里的
        exact = False
        for n in NEUTRALIZE:
            if norm_sel(sel) == norm_sel(n) or any(norm_sel(p) == norm_sel(n) for p in sel.split(",")):
                exact = True
        if exact:
            continue
        lines.append(f"  未列入第 1 节：{sel}  {{ {k}: {v} }}")

    if missing:
        lines.append("")
        lines.append("漏覆盖：")
        for sel, k, v in missing:
            lines.append(f"  - {sel} {{ {k}: {v} }}")
    else:
        lines.append("  表格相关浅色规则全部被 app.css 盖住。")

    lines.append("")
    lines.append("== 6. 对比度：#EFEAE0 落在分层底色上 ==")
    ink = hex_rgb("EFEAE0")
    panel = hex_rgb("191714")
    layers = [
        ("奇数行 transparent", panel),
        ("斑马 rgba(255,255,255,.022)", blend((255, 255, 255), 0.022, panel)),
        ("悬停 rgba(255,255,255,.055)", blend((255, 255, 255), 0.055, panel)),
        ("选区格 rgba(217,163,79,.13)", blend((217, 163, 79), 0.13, panel)),
        ("行头/列头 rgba(217,163,79,.20)", blend((217, 163, 79), 0.20, panel)),
        ("表头选中列 rgba(217,163,79,.22)", blend((217, 163, 79), 0.22, panel)),
        ("勾选行 rgba(217,163,79,.10)", blend((217, 163, 79), 0.10, panel)),
        ("编辑中 #211E19", hex_rgb("211E19")),
    ]
    for name, bg in layers:
        ratio = contrast(ink, bg)
        hx = "#{:02X}{:02X}{:02X}".format(*bg)
        ok(f"{name} → {hx}  {ratio:.1f}:1", ratio >= 7.0, f"{ratio:.2f}:1")

    text = "\n".join(lines)
    print(text)
    print()
    if failed:
        print(f"FAILED  {failed} 项")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
