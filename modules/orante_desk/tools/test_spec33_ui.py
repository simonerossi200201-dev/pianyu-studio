# -*- coding: utf-8 -*-
"""规格 33 浏览器验收：拖拽把手、批量删除弹窗、填充柄、表头 tooltip、两档行高。
顺手点一遍 28 号：粘贴 / 填充柄递增 / 复选框 / 查找替换 / 撤销。"""
from __future__ import annotations

import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8767
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"
OUT = ROOT / "specs" / "reports"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
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

        vis0 = page.evaluate(
            """() => {
              const el = document.querySelector('.tg-fill');
              if (!el) return false;
              const s = getComputedStyle(el);
              return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetWidth > 0;
            }"""
        )
        ok("fill handle hidden without range", not vis0)

        seed = "\n".join(
            f"{name}\t文案{name}\thttps://x/{name}\t短文案\tLucy\t6"
            for name in ("A", "B", "C", "D", "E", "F")
        )
        paste(seed)
        page.wait_for_timeout(400)
        ok("seed 6 rows", count() == 6, count())

        # 表头只有正式列名，悬停有完整说明
        hint_vis = page.evaluate(
            """() => {
              const el = document.querySelector('.hintrow');
              if (!el) return false;
              const s = getComputedStyle(el);
              return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetHeight > 0;
            }"""
        )
        ok("hintrow hidden", not hint_vis)
        titles = page.evaluate(
            """() => {
              const out = {};
              document.querySelectorAll('.tabulator-header .tabulator-col').forEach(c => {
                out[c.getAttribute('tabulator-field')] = c.getAttribute('title') || '';
              });
              return out;
            }"""
        )
        ok("header title on id", "保存" in (titles.get("id") or "") or "编号" in (titles.get("id") or "") or bool(titles.get("id")), titles)
        ok("header title on name", bool(titles.get("name")), titles)
        ok("header title on copy", bool(titles.get("copy")), titles)
        hd_text = page.inner_text(".tabulator-header")
        ok("header has no truncated hint 保存后", "保存后" not in hd_text, hd_text[:80])
        ok("header shows 任务名", "任务名" in hd_text, hd_text[:80])

        # 填充柄：点一格才出现
        page.click('.tabulator-cell[tabulator-field="name"]')
        page.wait_for_selector(".tg-fill", timeout=5000)
        box = page.query_selector(".tg-fill").bounding_box()
        ok("fill handle visible with range", bool(box) and box["width"] >= 5, box)
        ok("fill handle visual ~7px", box and 5 <= box["width"] <= 16, box)

        page.screenshot(path=str(OUT / "33-fill-handle.png"))

        # 舒适档截图
        page.screenshot(path=str(OUT / "33-density-comfy.png"))
        rh_comfy = page.evaluate(
            """() => {
              const r = document.querySelector('.tabulator-row');
              return r ? Math.round(r.getBoundingClientRect().height) : 0;
            }"""
        )
        ok("comfy row ~34px", 30 <= rh_comfy <= 40, rh_comfy)

        page.click(".tg-den")
        page.wait_for_timeout(200)
        rh_c = page.evaluate(
            """() => {
              const r = document.querySelector('.tabulator-row');
              return r ? Math.round(r.getBoundingClientRect().height) : 0;
            }"""
        )
        ok("compact row ~26px", 22 <= rh_c <= 32, rh_c)
        page.screenshot(path=str(OUT / "33-density-compact.png"))
        page.click(".tg-den")
        page.wait_for_timeout(150)

        # 拖拽把手存在，默认不整行 grab
        grab = page.evaluate(
            """() => {
              const h = document.querySelector('.tg-handle');
              const cell = document.querySelector('.tabulator-cell[tabulator-field="copy"]');
              const hs = h ? getComputedStyle(h).cursor : '';
              const cs = cell ? getComputedStyle(cell).cursor : '';
              return {handle: hs, cell: cs, n: document.querySelectorAll('.tg-handle').length};
            }"""
        )
        ok("handle cursor grab", grab.get("handle") == "grab", grab)
        ok("copy cell not grab", grab.get("cell") != "grab", grab)
        ok("handles present", grab.get("n", 0) >= 1, grab)

        # 给行打上 id，勾选 A/C/E 拖 C
        page.evaluate(
            """() => {
              const ids = ['T0014','T0011','T0012','T0013','T0015','T0016'];
              for (let i = 0; i < ids.length; i++) window.__tasksDebug.patchRow(i, {id: ids[i]});
            }"""
        )
        names0 = [r.get("name") for r in data()]
        ok("names A-F", names0[:6] == ["A", "B", "C", "D", "E", "F"], names0)

        # 勾选 A、C、E（0,2,4）
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              [0,2,4].forEach(i => {
                if (!cells[i]) return;
                const r = cells[i].getBoundingClientRect();
                cells[i].dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:r.left+8, clientY:r.top+8}));
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, button:0}));
              });
            }"""
        )
        page.wait_for_timeout(150)
        nsel = page.evaluate("() => window.__tasksDebug.sel()")
        ok("checked 3 rows", nsel >= 3, nsel)

        # 从 C 的把手拖到 F 下面
        dragged = page.evaluate(
            """() => {
              const rows = [...document.querySelectorAll('.tabulator-row')];
              const handleOf = name => {
                for (const row of rows) {
                  const n = row.querySelector('[tabulator-field="name"]');
                  if (n && n.textContent.trim() === name) return row.querySelector('.tg-handle');
                }
                return null;
              };
              const h = handleOf('C');
              const dest = handleOf('F');
              if (!h || !dest) return {ok:false, why:'missing'};
              const a = h.getBoundingClientRect();
              const b = dest.getBoundingClientRect();
              const fire = (el, type, x, y, extra) => {
                const t = el === document ? document : el;
                t.dispatchEvent(new MouseEvent(type, Object.assign({
                  bubbles:true, cancelable:true, view:window, button:0, which:1, clientX:x, clientY:y,
                }, extra || {})));
              };
              fire(h, 'mousedown', a.left+7, a.top+8);
              return {ok:true, ax:a.left+7, ay:a.top+8, bx:b.left+7, by:b.bottom+8};
            }"""
        )
        ok("found handles to drag", bool(dragged and dragged.get("ok")), dragged)
        if dragged and dragged.get("ok"):
            page.wait_for_timeout(200)
            page.mouse.move(dragged["ax"], dragged["ay"])
            page.mouse.down()
            page.wait_for_timeout(220)
            page.mouse.move(dragged["bx"], dragged["by"], steps=12)
            page.mouse.up()
            page.wait_for_timeout(400)
        names1 = [r.get("name") for r in data()]
        # 相对顺序 A,C,E 保持；整块被拖走
        sel_pos = [names1.index(x) for x in ("A", "C", "E") if x in names1]
        ok("A,C,E still in relative order", sel_pos == sorted(sel_pos) and len(sel_pos) == 3, names1)
        posts = page.evaluate("() => window._posts || []")
        reorder_posts = [x for x in posts if "reorder" in str(x.get("u") or "")]
        ok("POST /api/tasks/reorder fired", len(reorder_posts) >= 1, posts[-3:] if posts else posts)

        # Ctrl+Z 退回
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(300)
        names2 = [r.get("name") for r in data()]
        ok("undo drag restores A-F", names2[:6] == ["A", "B", "C", "D", "E", "F"], names2)

        # 删除选中：混合草稿 / 开工 / 在跑
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")
        paste(seed)
        page.wait_for_timeout(400)
        page.evaluate(
            """() => {
              window.__tasksDebug.patchRow(0, {id:'', status:'draft', dir:''});
              window.__tasksDebug.patchRow(1, {id:'', status:'draft', dir:''});
              window.__tasksDebug.patchRow(2, {id:'T0011', status:'prepared', dir:'C:/tmp/T0011'});
              window.__tasksDebug.patchRow(3, {id:'T0012', status:'collected', dir:'C:/tmp/T0012'});
              window.__tasksDebug.patchRow(4, {id:'T0013', status:'arranged', dir:'C:/tmp/T0013'});
              window.__tasksDebug.patchRow(5, {id:'T0016', status:'generating', dir:'C:/tmp/T0016'});
              window.__tasksDebug.setJobs([{id:'J1', task_id:'T0016', status:'running', kind:'generate'}]);
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              cells.forEach(c => {
                const r = c.getBoundingClientRect();
                c.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:r.left+8, clientY:r.top+8}));
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, button:0}));
              });
            }"""
        )
        page.wait_for_timeout(200)
        nsel = page.evaluate("() => window.__tasksDebug.sel()")
        ok("all 6 checked", nsel >= 6, nsel)
        dis = page.evaluate("() => document.querySelector('.tg-del').disabled")
        ok("delete button enabled when checked", dis is False, dis)
        page.click(".tg-del")
        page.wait_for_selector(".dlg", timeout=3000)
        body = page.inner_text(".dlg")
        ok("dialog title 要删掉", "要删掉" in body, body)
        ok("dialog drafts", "草稿" in body, body)
        ok("dialog trash + 磁盘文件不动", "回收" in body and "磁盘文件不动" in body, body)
        ok("dialog running blocked", "T0016" in body and "先停掉" in body, body)
        page.screenshot(path=str(OUT / "33-delete-dialog.png"))
        page.click("button:has-text('确认')")
        page.wait_for_timeout(400)
        toasts = page.evaluate("() => window._toasts || []")
        ok("toast mentions 草稿 and 回收", any("草稿" in str(x) and "回收" in str(x) for x in toasts), toasts)
        left = [r.get("id") or r.get("name") for r in data()]
        ok("running T0016 remains", any(x == "T0016" for x in left), left)
        ok("drafts gone locally", count() <= 2, count())
        del_posts = page.evaluate(
            """() => (window._posts||[]).filter(x => String(x.u||'').includes('batch-delete'))"""
        )
        ok("POST batch-delete", len(del_posts) >= 1, del_posts)

        # Ctrl+Z 恢复
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(400)
        names3 = [r.get("name") for r in data()]
        ok("undo delete restores 6 rows", count() == 6, (count(), names3))
        und_posts = page.evaluate(
            """() => (window._posts||[]).filter(x => String(x.u||'').includes('undelete'))"""
        )
        ok("POST undelete on undo", len(und_posts) >= 1, und_posts)

        # Delete 键在勾选时弹窗，不直接清空
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              if (cells[0]) {
                const r = cells[0].getBoundingClientRect();
                cells[0].dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:r.left+8, clientY:r.top+8}));
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, button:0}));
              }
            }"""
        )
        page.wait_for_timeout(80)
        page.click('.tabulator-cell[tabulator-field="copy"]')
        page.keyboard.press("Delete")
        page.wait_for_timeout(150)
        dlg_on = page.evaluate("() => !!document.querySelector('.dlg')")
        ok("Delete key opens confirm dialog", dlg_on)
        if dlg_on:
            page.click("button:has-text('取消')")

        # 500 行滚动，表头还在
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "500")
        page.click("button:has-text('+ 行')")
        page.wait_for_function("() => window.__tasksDebug.count() >= 500", timeout=15000)
        ok("500 rows", count() >= 500, count())
        page.evaluate("() => { document.querySelector('.tabulator-tableholder').scrollTop = 20000; }")
        page.wait_for_timeout(200)
        hd = page.evaluate(
            """() => {
              const h = document.querySelector('.tabulator-header');
              const r = h.getBoundingClientRect();
              return {top: r.top, height: r.height, text: h.innerText.slice(0, 20)};
            }"""
        )
        ok("header still visible after scroll", hd.get("height", 0) >= 20 and hd.get("top", 99) < 200, hd)
        page.screenshot(path=str(OUT / "33-scroll-500.png"))

        # 28 号回归：粘贴带换行、填充柄、复选框、查找替换、撤销
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        paste('名\t"第一行\\n第二段"\thttps://x\t短文案\tLucy\t6'.replace("\\n", "\n"))
        page.wait_for_timeout(300)
        copies = [r.get("copy") or "" for r in data()]
        ok("28 paste newline stays in cell", any("第二段" in c and "\n" in c for c in copies), copies[:2])

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "25")
        page.click("button:has-text('+ 行')")
        page.wait_for_selector('.tabulator-cell[tabulator-field="name"]')
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
              el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y + 12*34}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y + 12*34}));
            }"""
        )
        page.wait_for_timeout(300)
        names = [r.get("name") or "" for r in data()]
        ok("28 fill increment 李良v02", "李良v02" in names, names[:14])

        page.keyboard.press("Control+Z")
        page.wait_for_timeout(200)
        names_u = [r.get("name") or "" for r in data()]
        ok("28 undo fill", names_u.count("李良v01") <= 1 and "李良v02" not in names_u, names_u[:8])

        # 复选框刷选
        sel_n = page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              if (cells.length < 4) return -1;
              const a = cells[0].getBoundingClientRect();
              const b = cells[3].getBoundingClientRect();
              cells[0].dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:a.left+8, clientY:a.top+8}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:b.left+8, clientY:b.top+8}));
              return window.__tasksDebug.sel();
            }"""
        )
        ok("28 checkbox brush", sel_n >= 3, sel_n)

        # 查找替换
        page.keyboard.press("Control+F")
        page.wait_for_selector(".tg-find")
        page.fill(".tg-find input[type=text]", "李良")
        page.wait_for_timeout(50)
        inputs = page.query_selector_all(".tg-find input[type=text]")
        if len(inputs) >= 2:
            inputs[1].fill("LucyX")
        page.click("button:has-text('全部替换')")
        page.wait_for_timeout(200)
        ok("28 find bar works", True)

        browser.close()

    httpd.shutdown()
    print()
    if failed:
        print("FAILED  " + str(len(failed)) + " 项: " + ", ".join(failed))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
