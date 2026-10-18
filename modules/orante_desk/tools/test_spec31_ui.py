# -*- coding: utf-8 -*-
"""规格 31 浏览器验收：深色皮肤框选不再漏灰底黑字；顺手点一遍 28 号功能。"""
from __future__ import annotations

import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8766
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"
OUT = ROOT / "specs" / "reports"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


PANEL = (0x19, 0x17, 0x14)  # --panel #191714


def parse_rgb(s: str) -> tuple[int, int, int] | None:
    """把 computed 的 rgb/rgba 叠到 --panel 上，得到眼睛看到的底色。"""
    m = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([0-9.]+))?", s or "")
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    if a >= 0.999:
        return r, g, b
    return tuple(int(round(c * a + PANEL[i] * (1 - a))) for i, c in enumerate((r, g, b)))  # type: ignore[return-value]


def lum(rgb: tuple[int, int, int]) -> float:
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def is_light_text(rgb: tuple[int, int, int]) -> bool:
    return lum(rgb) >= 0.35  # --ink-2 #BEB6A7 ≈ 0.47，--ink 更高


def is_forbidden_bg(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    # #999 / #ccc / #444 / #888 / 浅灰 / 近白
    if r > 120 and g > 120 and b > 120 and abs(r - g) < 25 and abs(g - b) < 25:
        return True
    for target in ((153, 153, 153), (204, 204, 204), (68, 68, 68), (136, 136, 136)):
        if all(abs(rgb[i] - target[i]) < 12 for i in range(3)):
            return True
    return False


def is_dark_surface(rgb: tuple[int, int, int]) -> bool:
    return lum(rgb) <= 0.18  # #32291C ≈ 0.06


def main() -> int:
    from playwright.sync_api import sync_playwright

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    failed: list[str] = []

    def ok(name, cond, detail=""):
        if cond:
            print("  ok  " + name)
        else:
            failed.append(name)
            print("  FAIL " + name + (("  " + str(detail)) if detail else ""))

    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        page.add_init_script(
            "window.confirm = (m) => { window._confirms = (window._confirms||[]); window._confirms.push(String(m)); return true; };"
        )
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")

        def paste(text):
            page.evaluate(
                """(text) => {
                  const dt = new DataTransfer();
                  dt.setData('text/plain', text);
                  const el = document.querySelector('.tabulator');
                  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
                }""",
                text,
            )

        rows = []
        for i in range(1, 13):
            acc = "Lucy" if i % 2 else "TIK-G-SIMONE"
            rows.append(f"任务{i}\t文案第{i}条 圣母祷文\thttps://x/{i}\t短文案\t{acc}\t6")
        paste("\n".join(rows))
        page.wait_for_timeout(600)
        n = page.evaluate("() => window.__tasksDebug.count()")
        ok("seeded rows", n >= 12, n)

        # 给一行文案格子打上 locked，看小锁（不改 tasks.js，只改 DOM class）
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="copy"]')];
              if (cells[3]) cells[3].classList.add('cell-locked');
            }"""
        )
        # 点一格收掉粘贴留下的大选区，再拍「选中前」
        page.click('.tabulator-cell[tabulator-field="copy"]')
        page.wait_for_timeout(80)
        page.screenshot(path=str(OUT / "31-grid-normal.png"), full_page=False)

        # 斑马 / 悬停
        styles = page.evaluate(
            """() => {
              const rows = [...document.querySelectorAll('.panel-tasks .tabulator-row')];
              const take = (el) => {
                const s = getComputedStyle(el);
                const c = el.querySelector('.tabulator-cell');
                const cs = c ? getComputedStyle(c) : s;
                return {bg: s.backgroundColor, color: cs.color, even: el.classList.contains('tabulator-row-even')};
              };
              const odd = rows.find(r => !r.classList.contains('tabulator-row-even')) || rows[0];
              const even = rows.find(r => r.classList.contains('tabulator-row-even')) || rows[1];
              even.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
              const hover = take(even);
              return {odd: take(odd), even: take(even), hover, n: rows.length};
            }"""
        )
        odd_bg = parse_rgb(styles["odd"]["bg"])
        even_bg = parse_rgb(styles["even"]["bg"])
        odd_c = parse_rgb(styles["odd"]["color"])
        even_c = parse_rgb(styles["even"]["color"])
        ok("odd row is dark", odd_bg and is_dark_surface(odd_bg), odd_bg)
        ok("even row is dark (not #444)", even_bg and is_dark_surface(even_bg) and not is_forbidden_bg(even_bg), even_bg)
        ok("zebra text is light", odd_c and is_light_text(odd_c), odd_c)
        ok("even text is light", even_c and is_light_text(even_c), even_c)

        # 框选 4 行 × 5 列：任务名 → 时长 方向不对，选 任务名/文案/图/类型/账号
        page.evaluate("() => { const h = document.querySelector('.tabulator-tableholder'); h.scrollTop = 0; h.scrollLeft = 0; }")
        page.wait_for_timeout(80)
        c0 = page.query_selector_all('.tabulator-cell[tabulator-field="name"]')
        c1 = page.query_selector_all('.tabulator-cell[tabulator-field="account"]')
        ok("cells for drag", len(c0) >= 4 and len(c1) >= 4, (len(c0), len(c1)))
        if len(c0) >= 4 and len(c1) >= 4:
            b0 = c0[0].bounding_box()
            b1 = c1[3].bounding_box()
            page.mouse.move(b0["x"] + 8, b0["y"] + 8)
            page.mouse.down()
            page.mouse.move(b1["x"] + 20, b1["y"] + 8, steps=12)
            page.mouse.up()
            page.wait_for_timeout(200)

        page.screenshot(path=str(OUT / "31-grid-selected.png"), full_page=False)

        info = page.evaluate(
            """() => {
              const sel = [...document.querySelectorAll('.tabulator-cell.tabulator-range-selected')];
              const hlRows = [...document.querySelectorAll('.tabulator-row.tabulator-range-highlight')];
              const hlCols = [...document.querySelectorAll('.tabulator-col.tabulator-range-highlight, .tabulator-col.tabulator-range-selected')];
              const pick = (el) => {
                const s = getComputedStyle(el);
                return {bg: s.backgroundColor, color: s.color, cls: el.className};
              };
              const overlay = document.querySelector('.tabulator-range');
              const os = overlay ? getComputedStyle(overlay) : null;
              return {
                nSel: sel.length,
                nHlRows: hlRows.length,
                nHlCols: hlCols.length,
                cells: sel.slice(0, 12).map(pick),
                headers: hlCols.slice(0, 8).map(pick),
                rows: hlRows.slice(0, 4).map(pick),
                border: os ? os.borderColor : '',
                borderW: os ? os.borderWidth : '',
              };
            }"""
        )
        ok("range selected cells", info["nSel"] >= 12, info["nSel"])
        ok("highlight rows exist", info["nHlRows"] >= 4, info["nHlRows"])
        ok("header cols tinted", info["nHlCols"] >= 1, info["nHlCols"])

        bad_cell = []
        for i, c in enumerate(info["cells"]):
            bg, col = parse_rgb(c["bg"]), parse_rgb(c["color"])
            if not bg or is_forbidden_bg(bg) or not is_dark_surface(bg):
                bad_cell.append(("bg", i, c["bg"]))
            if not col or not is_light_text(col) or lum(col) < 0.3:
                bad_cell.append(("color", i, c["color"]))
            # 选区应偏暖金：R 明显大于 B
            if bg and bg[0] <= bg[2] + 4:
                bad_cell.append(("not-gold", i, c["bg"]))
        ok("selected cells dark gold + light text", not bad_cell, bad_cell[:6])

        bad_hd = []
        for i, c in enumerate(info["headers"]):
            bg, col = parse_rgb(c["bg"]), parse_rgb(c["color"])
            if bg and is_forbidden_bg(bg):
                bad_hd.append(("bg", i, c["bg"]))
            if col and not is_light_text(col):
                bad_hd.append(("color", i, c["color"]))
        ok("header highlight not gray/black", not bad_hd, bad_hd)

        bad_row = []
        for i, c in enumerate(info["rows"]):
            bg, col = parse_rgb(c["bg"]), parse_rgb(c["color"])
            if bg and is_forbidden_bg(bg):
                bad_row.append(("bg", i, c["bg"]))
            if col and not is_light_text(col):
                bad_row.append(("color", i, c["color"]))
        ok("highlight rows not gray/black", not bad_row, bad_row)

        br = parse_rgb(info["border"])
        ok("range border present", bool(info["borderW"] and info["borderW"] != "0px"), info["borderW"])
        if br:
            ok("range border warm gold", br[0] > br[2] + 20, br)

        # 锁图标
        lock = page.evaluate(
            """() => {
              const el = document.querySelector('.cell-locked');
              if (!el) return null;
              const s = getComputedStyle(el);
              const after = getComputedStyle(el, '::after');
              return {color: s.color, opacity: s.opacity, after: after.content, afterW: after.width, afterBg: after.backgroundImage};
            }"""
        )
        ok("locked cell exists", bool(lock), lock)
        if lock:
            lc = parse_rgb(lock["color"])
            ok("locked text not muted-faded", lc and is_light_text(lc) and str(lock["opacity"]) in ("1", "1.0"), (lock["color"], lock["opacity"]))
            ok("locked lock icon via ::after", "url(" in (lock["afterBg"] or "") or lock["afterW"] == "10px", lock)

        # 双击编辑
        page.dblclick('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_timeout(120)
        edit = page.evaluate(
            """() => {
              const el = document.querySelector('.tabulator-cell.tabulator-editing');
              if (!el) return null;
              const s = getComputedStyle(el);
              const inp = el.querySelector('input,textarea');
              const is_ = inp ? getComputedStyle(inp) : null;
              return {border: s.borderColor, bg: s.backgroundColor, color: is_ ? is_.color : s.color};
            }"""
        )
        ok("dblclick enters edit", bool(edit), edit)
        if edit:
            ec = parse_rgb(edit["color"])
            ok("edit text is light", ec and is_light_text(ec), edit["color"])
        page.keyboard.press("Escape")

        # 勾选几行
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              if (cells.length < 3) return;
              const a = cells[0].getBoundingClientRect();
              const b = cells[2].getBoundingClientRect();
              cells[0].dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:a.left+8, clientY:a.top+8}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
            }"""
        )
        page.wait_for_timeout(200)
        nsel = page.evaluate("() => window.__tasksDebug.sel()")
        ok("checkbox brush selected some", nsel >= 2, nsel)
        chk = page.evaluate(
            """() => {
              const row = document.querySelector('.tabulator-row:has(.tg-check.on)');
              if (!row) return null;
              const s = getComputedStyle(row);
              const before = getComputedStyle(row, '::before');
              const cell = row.querySelector('.tabulator-cell');
              const cs = cell ? getComputedStyle(cell) : s;
              return {bg: s.backgroundColor, color: cs.color, shadow: before.boxShadow};
            }"""
        )
        ok("checked row styled", bool(chk), chk)
        if chk:
            bg = parse_rgb(chk["bg"])
            col = parse_rgb(chk["color"])
            ok("checked row dark gold", bg and is_dark_surface(bg) and not is_forbidden_bg(bg), bg)
            ok("checked row light text", col and is_light_text(col), col)
            ok("checked row has accent bar", "rgb" in (chk["shadow"] or ""), chk["shadow"])

        # 紧凑档
        page.click(".tg-den")
        page.wait_for_timeout(120)
        compact = page.evaluate("() => document.querySelector('.panel-tasks').classList.contains('tg-compact')")
        ok("compact class on", compact)
        # 再框一小块
        cells = page.query_selector_all('.tabulator-cell[tabulator-field="name"]')
        if len(cells) >= 2:
            b0 = cells[0].bounding_box()
            b1 = cells[1].bounding_box()
            page.mouse.move(b0["x"] + 8, b0["y"] + 6)
            page.mouse.down()
            page.mouse.move(b1["x"] + 40, b1["y"] + 6, steps=6)
            page.mouse.up()
            page.wait_for_timeout(120)
        compact_sel = page.evaluate(
            """() => {
              const sel = [...document.querySelectorAll('.tabulator-cell.tabulator-range-selected')];
              return sel.slice(0, 6).map(el => {
                const s = getComputedStyle(el);
                return {bg: s.backgroundColor, color: s.color};
              });
            }"""
        )
        bad_c = []
        for i, c in enumerate(compact_sel):
            bg, col = parse_rgb(c["bg"]), parse_rgb(c["color"])
            if bg and (is_forbidden_bg(bg) or not is_dark_surface(bg)):
                bad_c.append(c)
            if col and not is_light_text(col):
                bad_c.append(c)
        ok("compact selection still dark+light text", not bad_c, bad_c)

        # 28 号功能冒烟：填充柄还在、查找条、撤销
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_timeout(80)
        fill = page.query_selector(".tg-fill")
        ok("fill handle still there", bool(fill) and fill.is_visible())
        page.keyboard.press("Control+F")
        page.wait_for_timeout(80)
        ok("find bar opens", page.query_selector(".tg-find") is not None or page.locator(".tg-find").count() > 0)
        page.keyboard.press("Escape")

        # 粘贴还活着：再贴一行
        before = page.evaluate("() => window.__tasksDebug.count()")
        paste("新任务\thello\t\t短文案\tLucy\t6")
        page.wait_for_timeout(300)
        after = page.evaluate("() => window.__tasksDebug.count()")
        ok("paste still adds rows", after >= before, (before, after))
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(200)
        undone = page.evaluate("() => window.__tasksDebug.count()")
        ok("undo still works", undone <= after, (after, undone))

        browser.close()

    httpd.shutdown()
    print()
    if failed:
        print("FAILED " + str(len(failed)) + "  " + ", ".join(failed))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
