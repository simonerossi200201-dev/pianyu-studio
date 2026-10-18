# -*- coding: utf-8 -*-
"""规格 28 浏览器验收：对着 tools/_grid28.html 真点真贴。"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"

sys.path.insert(0, str(ROOT / "ui" / "js" / "parts"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


def make_tsv_200():
    rows = []
    for i in range(1, 201):
        if i % 7 == 0:
            copy = f'长文案第{i}条\n第二段 "引号" 和\ttab\n结尾'
        else:
            copy = f"短文案 {i}"
        rows.append([
            f"T{i:04d}",
            copy,
            f"https://drive.google.com/x/{i}",
            "短文案" if i % 2 else "长文案",
            "TIK-G-SIMONE" if i % 3 else "Lucy",
            str([6, 8, 10][i % 3]),
            f"note{i}",
            "ok" if i % 5 else "",
        ])
    # 与 sheet.js 相同的加引号规则
    def q(s):
        s = "" if s is None else str(s)
        if any(ch in s for ch in "\t\n\r\""):
            return '"' + s.replace('"', '""') + '"'
        return s
    return "\n".join("\t".join(q(c) for c in r) for r in rows), rows


def main() -> int:
    from playwright.sync_api import sync_playwright

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    tsv200, rows200 = make_tsv_200()
    failed = []

    def ok(name, cond, detail=""):
        if cond:
            print("  ok  " + name)
        else:
            failed.append(name)
            print("  FAIL " + name + (("  " + str(detail)) if detail else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        page.add_init_script(
            "window.confirm = (m) => { window._confirms = (window._confirms||[]); window._confirms.push(String(m)); return true; };"
        )
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")

        def data():
            return page.evaluate("() => window.__tasksDebug.data()")

        def count():
            return page.evaluate("() => window.__tasksDebug.count()")

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

        # --- 1. + 行… 160，继承最后一行 ---
        seed = "种子\thello\t\t短文案\tLucy\t6"
        paste(seed)
        page.wait_for_timeout(400)
        ok("seed row pasted", count() >= 1, count())
        page.fill(".tg-add-n", "160")
        page.click("button:has-text('+ 行')")
        page.wait_for_timeout(800)
        n1 = page.inner_text(".tg-count")
        ok("add 160 updates footer", "共 161" in n1 or "共 160" in n1, n1)
        ok("add 160 count", count() >= 160, count())
        confirms = page.evaluate("() => window._confirms || []")
        ok("confirm asked for 160", any("160" in str(c) for c in confirms), confirms)
        last = data()[-1]
        ok("new row inherited type", last.get("type") in ("short", "短文案"), last.get("type"))
        ok("new row inherited account", last.get("account") == "Lucy", last.get("account"))
        ok("new row inherited duration", str(last.get("duration_pref")) == "6", last.get("duration_pref"))
        ok("new row name empty", not str(last.get("name") or "").strip(), last.get("name"))

        # --- 2. 粘贴 200×8 带换行文案 ---
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")
        paste(tsv200)
        page.wait_for_timeout(1200)
        footer = page.inner_text(".tg-count")
        ok("paste 200 rows footer", "共 200" in footer, footer)
        ok("paste 200 count", count() == 200, count())
        toasts = page.evaluate("() => window._toasts || []")
        ok("auto-add toast", any("粘贴时自动加了" in str(x) for x in toasts), toasts)
        copies = [r.get("copy") or "" for r in data()]
        long_hits = [c for c in copies if "第二段" in c]
        ok("long copy kept newlines", any("\n" in c for c in long_hits), (long_hits[0][:80] if long_hits else "none"))
        ok("no copy cell split into extra rows", count() == 200, count())
        ok("first copy is not the whole block", copies and copies[0].count("短文案") <= 1, copies[0][:80] if copies else "")

        # --- 3. 复制出去 ---
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.evaluate("() => window.__tasksDebug.selectAll()")
        page.wait_for_timeout(80)
        page.keyboard.press("Control+A")
        page.wait_for_timeout(80)
        page.keyboard.press("Control+C")
        page.wait_for_timeout(250)
        copied = page.evaluate(
            """async () => {
              try { return await navigator.clipboard.readText(); }
              catch (e) { return window.__tasksDebug.copyTSV(); }
            }"""
        )
        if not copied or copied.count("\n") < 50:
            copied = page.evaluate("() => window.__tasksDebug.copyTSV()")
        # 文案列带换行的不应被拆行：parse 回来应是 200 行
        page.evaluate(
            """(text) => { window._copied = text; window._parsed = TasksSheet.parseTSVGrid(text); }""",
            copied,
        )
        parsed_n = page.evaluate("() => (window._parsed||[]).length")
        ok("copy parses to ~200 rows (not exploded)", 190 <= int(parsed_n or 0) <= 210, parsed_n)
        # 带换行的那条还在一个格子里
        kept = page.evaluate(
            """() => {
              const g = window._parsed || [];
              const hit = g.find(r => (r[1]||'').includes('第二段'));
              return hit ? hit[1] : '';
            }"""
        )
        ok("copied long copy still one cell", "\n" in kept and '"引号"' in kept, kept[:80])

        # --- 4. 单击不进编辑，双击才进 ---
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_timeout(100)
        editing = page.evaluate("() => !!document.querySelector('.tabulator-cell.tabulator-editing')")
        ok("single click does not edit", not editing)
        page.dblclick('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_timeout(150)
        editing = page.evaluate("() => !!document.querySelector('.tabulator-cell.tabulator-editing')")
        ok("double click enters edit", editing)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        editing = page.evaluate("() => !!document.querySelector('.tabulator-cell.tabulator-editing')")
        ok("Esc leaves edit", not editing)

        # --- 5 & 6. 填充柄：新表，一格 李良v01 往下拖 ---
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "40")
        page.click("button:has-text('+ 行')")
        page.wait_for_selector('.tabulator-cell[tabulator-field="name"]', timeout=10000)
        page.dblclick('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_timeout(80)
        page.keyboard.type("李良v01")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        # 选中该格，等填充柄
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_selector(".tg-fill", timeout=5000)
        box = page.query_selector(".tg-fill").bounding_box()
        ok("fill handle visible", bool(box))
        tip = page.evaluate(
            """() => {
              const el = document.querySelector('.tg-fill');
              if (!el) return '';
              const r = el.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.top + r.height/2;
              el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y + 20*34}));
              const t = document.querySelector('.tg-fill-tip');
              const text = t && t.style.display !== 'none' ? t.textContent : '';
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y + 20*34}));
              return text;
            }"""
        )
        ok("fill tip shows last value", "李良v" in (tip or ""), tip)
        page.wait_for_timeout(400)
        names = [r.get("name") or "" for r in data()]
        ok("fill produced 李良v02", "李良v02" in names, names[:22])
        ok("fill produced later v", any(x.startswith("李良v") and x != "李良v01" for x in names), names[:22])

        # Ctrl 拖：复制
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "30")
        page.click("button:has-text('+ 行')")
        page.wait_for_selector('.tabulator-cell[tabulator-field="name"]', timeout=10000)
        page.dblclick('.tabulator-cell[tabulator-field="name"]')
        page.keyboard.type("李良v01")
        page.keyboard.press("Enter")
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_selector(".tg-fill")
        page.evaluate(
            """() => {
              const el = document.querySelector('.tg-fill');
              const r = el.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.top + r.height/2;
              el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y, ctrlKey:true}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y + 16*34, ctrlKey:true}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y + 16*34, ctrlKey:true}));
            }"""
        )
        page.wait_for_timeout(300)
        names = [r.get("name") or "" for r in data() if r.get("name")]
        uniq = set(names)
        ok("ctrl fill copies not increments", uniq <= {"李良v01"} and len(names) >= 2, names[:10])

        # 等差 2,4
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "20")
        page.click("button:has-text('+ 行')")
        page.wait_for_selector('.tabulator-cell[tabulator-field="name"]', timeout=10000)
        cells = page.query_selector_all('.tabulator-cell[tabulator-field="name"]')
        if len(cells) >= 2:
            cells[0].dblclick()
            page.keyboard.type("2")
            page.keyboard.press("Enter")
            cells[1].dblclick()
            page.keyboard.type("4")
            page.keyboard.press("Enter")
            # 框选两格：点第一格，shift 点第二格 —— 区域选择是拖，不是 shift 行。
            # 从第一格拖到第二格。
            b0 = cells[0].bounding_box()
            b1 = cells[1].bounding_box()
            page.mouse.move(b0["x"] + 10, b0["y"] + 8)
            page.mouse.down()
            page.mouse.move(b1["x"] + 10, b1["y"] + 8, steps=5)
            page.mouse.up()
            page.wait_for_timeout(150)
            page.wait_for_selector(".tg-fill")
            page.evaluate(
                """() => {
                  const el = document.querySelector('.tg-fill');
                  const r = el.getBoundingClientRect();
                  const x = r.left + r.width/2, y = r.top + r.height/2;
                  el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y}));
                  document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y + 8*34}));
                  document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y + 8*34}));
                }"""
            )
            page.wait_for_timeout(300)
        nums = [str(r.get("name") or "") for r in data()]
        ok("arithmetic 2,4 continues 6", "6" in nums, nums[:10])

        # --- 7. 复选框刷选 ---
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.click(".tg-den")
        page.wait_for_timeout(150)
        page.fill(".tg-add-n", "120")
        page.click("button:has-text('+ 行')")
        page.wait_for_selector('.tabulator-cell[tabulator-field="_sel"]', timeout=10000)
        sel_cells = page.query_selector_all('.tabulator-cell[tabulator-field="_sel"]')
        ok("checkbox column present", len(sel_cells) >= 10, len(sel_cells))
        n_vis = page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              if (cells.length < 2) return 0;
              const a = cells[0].getBoundingClientRect();
              const b = cells[cells.length-1].getBoundingClientRect();
              cells[0].dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:a.left+8, clientY:a.top+8}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
              return cells.length;
            }"""
        )
        page.wait_for_timeout(200)
        footer = page.inner_text(".tg-count")
        nsel = page.evaluate("() => window.__tasksDebug.sel()")
        ok("brushed checkboxes counted", nsel >= 8, f"sel={nsel} vis={n_vis} footer={footer}")

        # Shift 点更下面的一行：先滚下去再点
        page.evaluate("() => { const h = document.querySelector('.tabulator-tableholder'); h.scrollTop = 24*80; }")
        page.wait_for_timeout(200)
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              const last = cells[cells.length-1];
              if (!last) return;
              const b = last.getBoundingClientRect();
              last.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, shiftKey:true, clientX:b.left+8, clientY:b.top+8}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, shiftKey:true}));
            }"""
        )
        page.wait_for_timeout(200)
        nsel2 = page.evaluate("() => window.__tasksDebug.sel()")
        ok("shift click extends checks", nsel2 > nsel, f"{nsel} -> {nsel2}")

        # --- 8. 查找替换 + 撤销 ---
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        paste("A\t\t\t\tTIK-G-SIMONE\nB\t\t\t\tTIK-G-SIMONE\nC\t\t\t\tother\nD\t\t\t\tTIK-G-SIMONE")
        page.wait_for_timeout(400)
        page.click('.tabulator-cell[tabulator-field="account"]')
        page.wait_for_timeout(300)
        page.keyboard.press("Control+F")
        page.wait_for_selector(".tg-find")
        page.fill(".tg-find input[type=text]", "TIK-G-SIMONE")
        page.fill(".tg-find input[type=text] >> nth=1", "Lucy")
        page.click("button:has-text('全部替换')")
        page.wait_for_timeout(300)
        confirms = page.evaluate("() => window._confirms || []")
        ok("replace-all confirmed N cells", any("格" in str(c) for c in confirms), confirms)
        accs = [r.get("account") or "" for r in data()]
        ok("replaced SIMONE with Lucy", accs.count("Lucy") >= 3 and "TIK-G-SIMONE" not in accs, accs)
        page.click(".tabulator-tableholder")
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(300)
        accs2 = [r.get("account") or "" for r in data()]
        ok("undo restore SIMONE in one step", accs2.count("TIK-G-SIMONE") >= 3, accs2)

        # --- 9. 2000 行滚动 ---
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "2000")
        t0 = time.perf_counter()
        page.click("button:has-text('+ 行')")
        page.wait_for_timeout(1500)
        add_ms = (time.perf_counter() - t0) * 1000
        footer = page.inner_text(".tg-count")
        ok("2000 rows added", "共 2000" in footer, footer)
        holder = page.query_selector(".tabulator-tableholder")
        # 测 40 帧滚动
        frames = page.evaluate(
            """async () => {
              const h = document.querySelector('.tabulator-tableholder');
              const times = [];
              h.scrollTop = 0;
              for (let i = 0; i < 40; i++) {
                const a = performance.now();
                h.scrollTop += 80;
                await new Promise(r => requestAnimationFrame(r));
                times.push(performance.now() - a);
              }
              return times;
            }"""
        )
        avg = sum(frames) / max(1, len(frames))
        p95 = sorted(frames)[int(len(frames) * 0.95)] if frames else 0
        fps = 1000 / avg if avg else 0
        ok("scroll avg frame < 40ms", avg < 40, f"avg={avg:.1f}ms p95={p95:.1f}ms ~{fps:.0f}fps add={add_ms:.0f}ms")
        print(f"    scroll avg {avg:.1f}ms  p95 {p95:.1f}ms  ~{fps:.0f} fps  add2000 {add_ms:.0f}ms")

        # --- 10. 列宽 / 行高档写入 settings ---
        page.click(".tg-den")
        page.wait_for_timeout(200)
        dens = page.evaluate("() => document.querySelector('.panel-tasks').classList.contains('tg-compact')")
        ok("density toggle compact class", dens)
        posts = page.evaluate("() => window._posts || []")
        dens_saved = any(
            isinstance(p, dict) and "ui.tasks.density" in str(p.get("d") or p)
            for p in posts
        )
        ok("density posted to settings", dens_saved, posts[-3:] if posts else posts)

        browser.close()

    httpd.shutdown()
    print()
    if failed:
        print("FAILED", len(failed), ":", ", ".join(failed))
        return 1
    print("all ui checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
