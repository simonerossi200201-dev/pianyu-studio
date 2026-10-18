# -*- coding: utf-8 -*-
"""规格 43 浏览器验收：Ctrl+X 剪切、拖选区边框移动、右键单元格菜单。
对着 tools/_grid28.html，不入库。"""
from __future__ import annotations

import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8768
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


SEED = "\n".join(
    f"{name}\t文案{name}\thttps://x/{name}\t短文案\tLucy\t6"
    for name in ("A", "B", "C", "D", "E", "F")
)


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
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
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

        def col_fields():
            return page.evaluate(
                """() => {
                  const seen = [];
                  document.querySelectorAll('.tabulator-header .tabulator-col').forEach(c => {
                    const f = c.getAttribute('tabulator-field');
                    if (f && seen.indexOf(f) < 0) seen.push(f);
                  });
                  return seen;
                }"""
            )

        def reset():
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".tabulator-tableholder", timeout=15000)
            page.wait_for_function("() => window.__tasksDebug")
            paste(SEED)
            page.wait_for_timeout(350)

        def set_range(top, left, bottom, right):
            page.evaluate(
                """([t,l,b,r]) => window.__tasksDebug.setRange(t,l,b,r)""",
                [top, left, bottom, right],
            )
            page.wait_for_timeout(80)

        reset()
        ok("seed 6 rows", count() == 6, count())
        fields = col_fields()
        i_name = fields.index("name") if "name" in fields else 2
        i_copy = fields.index("copy") if "copy" in fields else 3
        i_img = fields.index("image_source") if "image_source" in fields else 4
        ok("name/copy columns", i_name >= 0 and i_copy == i_name + 1, fields)

        # --- 1. 选 3×2 Ctrl+X：TSV、源清空、toast、Ctrl+Z ---
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        page.wait_for_selector(".tg-move-hit", timeout=4000)
        page.keyboard.press("Control+X")
        page.wait_for_timeout(250)
        clip = page.evaluate(
            """async () => {
              try { return await navigator.clipboard.readText(); }
              catch (e) { return window.__tasksDebug.copyTSV(); }
            }"""
        )
        if not clip or "文案A" not in (clip or ""):
            clip = page.evaluate("() => window._lastCut || window.__tasksDebug.copyTSV()")
        # 剪切后选区已空，copyTSV 可能是空的；剪贴板才是准的
        rows0 = data()
        ok("cut cleared A name", not (rows0[0].get("name") or "").strip(), rows0[0].get("name"))
        ok("cut cleared A copy", not (rows0[0].get("copy") or "").strip(), rows0[0].get("copy"))
        ok("cut cleared C name", not (rows0[2].get("name") or "").strip(), rows0[2].get("name"))
        ok("cut left D name", rows0[3].get("name") == "D", rows0[3].get("name"))
        toasts = page.evaluate("() => window._toasts || []")
        toast_s = " | ".join(str(x) for x in toasts)
        ok(
            "cut toast 3x2",
            ("已剪切" in toast_s and "3" in toast_s) or ("tasks.cut.done" in toast_s),
            toasts,
        )
        ok(
            "clipboard has 文案A and 文案C",
            clip and "文案A" in clip and "文案C" in clip and "A" in clip,
            (clip or "")[:120],
        )
        ok("clipboard 3 lines", clip and clip.count("\n") >= 2, clip)
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(250)
        rows_u = data()
        ok("undo cut restores A/B/C", [r.get("name") for r in rows_u[:3]] == ["A", "B", "C"], [r.get("name") for r in rows_u[:3]])
        ok("undo cut restores copy", rows_u[0].get("copy") == "文案A" and rows_u[2].get("copy") == "文案C", rows_u[0].get("copy"))

        # --- 2. 已开工文案格不清 ---
        reset()
        page.evaluate("() => window.__tasksDebug.patchRow(1, {status:'prepared', id:'T0099'})")
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        page.evaluate("() => window.__tasksDebug.cutSelected()")
        page.wait_for_timeout(150)
        rows2 = data()
        ok("started copy kept", rows2[1].get("copy") == "文案B", rows2[1].get("copy"))
        ok("started name cleared", not (rows2[1].get("name") or "").strip(), rows2[1].get("name"))
        ok("draft A copy cleared", not (rows2[0].get("copy") or "").strip(), rows2[0].get("copy"))
        ok("draft C copy cleared", not (rows2[2].get("copy") or "").strip(), rows2[2].get("copy"))
        toasts2 = page.evaluate("() => window._toasts || []")
        t2 = " | ".join(str(x) for x in toasts2)
        ok(
            "skipped toast",
            "已开工" in t2 or "tasks.cut.skipped" in t2,
            toasts2,
        )

        # --- 3. 拖边框移动（moveBy）源空、值到新位置、选区跟着走、一步撤销 ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        moved = page.evaluate("async () => await window.__tasksDebug.moveBy(3, 0, false)")
        page.wait_for_timeout(200)
        rows3 = data()
        ok("move source A empty", not (rows3[0].get("name") or "").strip(), rows3[0])
        ok("move dest row3 is A", rows3[3].get("name") == "A" and rows3[3].get("copy") == "文案A", rows3[3])
        ok("move dest row5 is C", rows3[5].get("name") == "C" and rows3[5].get("copy") == "文案C", rows3[5])
        box3 = page.evaluate("() => window.__tasksDebug.rangeBox()")
        ok(
            "selection followed dest",
            box3 and box3.get("top") == 3 and box3.get("bottom") == 5,
            box3,
        )
        page.keyboard.press("Control+Z")
        page.wait_for_timeout(250)
        rows3u = data()
        ok("undo move restores A-F", [r.get("name") for r in rows3u[:6]] == ["A", "B", "C", "D", "E", "F"], [r.get("name") for r in rows3u[:6]])

        # --- 4. Ctrl 拖 = 复制，源保留，轮廓有 + ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 0, i_copy)
        page.wait_for_selector(".tg-move-n")
        plus = page.evaluate(
            """() => {
              const edge = document.querySelector('.tg-move-n');
              if (!edge) return {ok:false, why:'no edge'};
              const r = edge.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.top + 1;
              edge.dispatchEvent(new MouseEvent('mousedown', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y, ctrlKey:true
              }));
              const ghost0 = document.querySelector('.tg-move-ghost');
              const hasPlus = !!(ghost0 && ghost0.classList.contains('is-copy'));
              const plusEl = document.querySelector('.tg-move-plus');
              const plusShown = plusEl && getComputedStyle(plusEl).display !== 'none';
              document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles:true, cancelable:true, view:window, clientX:x, clientY:y + 34*2, ctrlKey:true
              }));
              const ghost = document.querySelector('.tg-move-ghost');
              const copyClass = !!(ghost && ghost.classList.contains('is-copy'));
              document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
              return {
                hasPlus: hasPlus || copyClass,
                plusShown: plusShown || copyClass,
                ghostDisp: ghost ? ghost.style.display : '',
                cls: ghost ? ghost.className : '',
              };
            }"""
        )
        ok("ctrl drag ghost is-copy / plus", bool(plus.get("hasPlus") or plus.get("plusShown")), plus)
        copied = page.evaluate("async () => await window.__tasksDebug.moveBy(2, 0, true)")
        page.wait_for_timeout(150)
        rows4 = data()
        ok("copy keeps source A", rows4[0].get("name") == "A" and rows4[0].get("copy") == "文案A", rows4[0])
        ok("copy wrote dest C", rows4[2].get("name") == "A" and rows4[2].get("copy") == "文案A", rows4[2])

        # --- 5. 往下超出最后一行 → 自动加行 ---
        reset()
        n0 = count()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(5, i_name, 5, i_copy)
        add = page.evaluate("async () => await window.__tasksDebug.moveBy(2, 0, false)")
        page.wait_for_timeout(200)
        n1 = count()
        rows5 = data()
        ok("added rows below last", n1 >= n0 + 2, (n0, n1, add))
        last = rows5[-1] if rows5 else {}
        ok("last row is F after down-move", last.get("name") == "F" and last.get("copy") == "文案F", last)
        toasts5 = page.evaluate("() => window._toasts || []")
        t5 = " | ".join(str(x) for x in toasts5)
        ok("added-rows toast", "自动加了" in t5 or "tasks.move.added" in t5, toasts5)

        # --- 6. 往右超出最后一列 → 轮廓红，松手不变 ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 0, i_copy)
        before6 = [(r.get("name"), r.get("copy")) for r in data()]
        oob = page.evaluate("async () => await window.__tasksDebug.moveBy(0, 20, false)")
        page.wait_for_timeout(80)
        after6 = [(r.get("name"), r.get("copy")) for r in data()]
        ok("right oob rejected", isinstance(oob, dict) and oob.get("ok") is False and oob.get("oobCol"), oob)
        ok("right oob data unchanged", before6 == after6, after6[:3])
        # 选最后两列再往右拖，轮廓应变红（先读样式再 Esc，免得 hide 把 display 清掉）
        i_last = len(fields) - 1
        set_range(0, max(0, i_last - 1), 2, i_last)
        page.wait_for_selector(".tg-move-e")
        red = page.evaluate(
            """() => {
              const edge = document.querySelector('.tg-move-e');
              if (!edge) return {ok:false};
              const r = edge.getBoundingClientRect();
              const x = r.left + Math.min(2, Math.max(0, r.width - 1));
              const y = r.top + Math.min(8, Math.max(1, r.height / 2));
              edge.dispatchEvent(new MouseEvent('mousedown', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y
              }));
              document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles:true, cancelable:true, view:window, clientX:x + 240, clientY:y
              }));
              const ghost = document.querySelector('.tg-move-ghost');
              const bad = !!(ghost && ghost.classList.contains('is-bad'));
              const disp = ghost ? ghost.style.display : '';
              document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
              return {bad, disp};
            }"""
        )
        ok("right oob ghost is-bad", bool(red.get("bad")), red)

        # --- 7. 目标盖到已开工文案列 → 整次拒绝 ---
        reset()
        page.evaluate("() => window.__tasksDebug.patchRow(2, {status:'arranged', id:'T0014'})")
        page.click('.tabulator-cell[tabulator-field="copy"]')
        set_range(0, i_copy, 0, i_copy)
        before7 = [r.get("copy") for r in data()]
        blk = page.evaluate("async () => await window.__tasksDebug.moveBy(2, 0, false)")
        page.wait_for_timeout(80)
        after7 = [r.get("copy") for r in data()]
        ok("blocked onto started copy", isinstance(blk, dict) and blk.get("ok") is False and blk.get("blocked"), blk)
        ok("blocked data unchanged", before7 == after7, after7)
        toasts7 = page.evaluate("() => window._toasts || []")
        # 只有松手 apply 才会 toast；moveBy 非法直接返回，不 toast。再走一遍拖放手。
        page.evaluate(
            """() => {
              window._toasts = [];
              const edge = document.querySelector('.tg-move-s');
              if (!edge) return;
              const r = edge.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.bottom - 1;
              edge.dispatchEvent(new MouseEvent('mousedown', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y
              }));
              document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles:true, cancelable:true, view:window, clientX:x, clientY:y + 34*2
              }));
              document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y + 34*2
              }));
            }"""
        )
        page.wait_for_timeout(150)
        toasts7b = page.evaluate("() => window._toasts || []")
        t7 = " | ".join(str(x) for x in toasts7b)
        ok(
            "blocked toast names cell",
            "去不了" in t7 or "tasks.move.blocked" in t7 or "已开工" in t7 or "文案" in t7,
            toasts7b,
        )
        after7b = [r.get("copy") for r in data()]
        ok("blocked drag did not write", after7b[0] == "文案A" and after7b[2] == "文案C", after7b)

        # --- 8. 源和目标重叠（右下挪一格）---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 1, i_copy)  # A/B name+copy
        ov = page.evaluate("async () => await window.__tasksDebug.moveBy(1, 1, false)")
        page.wait_for_timeout(150)
        rows8 = data()
        ok("overlap move ok", not (isinstance(ov, dict) and ov.get("ok") is False), ov)
        ok("overlap source A name empty", not (rows8[0].get("name") or "").strip(), rows8[0])
        # 列 +1 / 行 +1：name→copy，copy→image。先读源再清再写，重叠格不会被自己写成空。
        ok("overlap dest B copy is A (not emptied)", rows8[1].get("copy") == "A", rows8[1].get("copy"))
        ok("overlap A.copy went to B.image", rows8[1].get("image_source") == "文案A", rows8[1].get("image_source"))
        ok("overlap B.name went to C.copy", rows8[2].get("copy") == "B", rows8[2].get("copy"))
        ok("overlap B.copy went to C.image", rows8[2].get("image_source") == "文案B", rows8[2].get("image_source"))

        # --- 9. 填充柄 14×14 仍在；南/东边让出；把手还在 ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        page.wait_for_selector(".tg-fill")
        geo = page.evaluate(
            """() => {
              const fill = document.querySelector('.tg-fill');
              const hit = document.querySelector('.tg-move-hit');
              const s = document.querySelector('.tg-move-s');
              const e = document.querySelector('.tg-move-e');
              const h = document.querySelector('.tg-handle');
              const fr = fill.getBoundingClientRect();
              const sr = s.getBoundingClientRect();
              const er = e.getBoundingClientRect();
              const hr = hit.getBoundingClientRect();
              const cs = getComputedStyle(s);
              const ce = getComputedStyle(e);
              return {
                fillW: Math.round(fr.width), fillH: Math.round(fr.height),
                fillVis: getComputedStyle(fill).display !== 'none',
                hitVis: getComputedStyle(hit).display !== 'none',
                sRightGap: Math.round(hr.right - sr.right),
                eBottomGap: Math.round(hr.bottom - er.bottom),
                handle: !!(h && getComputedStyle(h).cursor === 'grab'),
                sCursor: cs.cursor, eCursor: ce.cursor,
              };
            }"""
        )
        ok("fill handle visible ~7px", geo.get("fillVis") and 5 <= geo.get("fillW", 0) <= 16, geo)
        ok("move hit visible", geo.get("hitVis"), geo)
        ok("south edge leaves ~14px for fill", geo.get("sRightGap", 0) >= 10, geo)
        ok("east edge leaves ~14px for fill", geo.get("eBottomGap", 0) >= 10, geo)
        ok("row handle still grab", geo.get("handle"), geo)
        ok("edge cursor move", geo.get("sCursor") == "move" and geo.get("eCursor") == "move", geo)

        # 点填充柄不该启动移动（源不应被清空成搬家）
        names_before_fill = [r.get("name") for r in data()]
        page.evaluate(
            """() => {
              const el = document.querySelector('.tg-fill');
              const r = el.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.top + r.height/2;
              el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y}));
              document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:x, clientY:y + 3*34}));
              document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:x, clientY:y + 3*34}));
            }"""
        )
        page.wait_for_timeout(200)
        names_after_fill = [r.get("name") for r in data()]
        ok("fill handle still fills (source A kept)", names_after_fill[0] == "A", names_after_fill[:8])
        ok("fill did not move-clear source", names_before_fill[0] == names_after_fill[0], names_after_fill[:8])

        # --- 10. Esc 取消，幽灵消失，数据不动 ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        before10 = [(r.get("name"), r.get("copy")) for r in data()]
        esc = page.evaluate(
            """() => {
              const edge = document.querySelector('.tg-move-n');
              const r = edge.getBoundingClientRect();
              const x = r.left + r.width/2, y = r.top + 1;
              edge.dispatchEvent(new MouseEvent('mousedown', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y
              }));
              document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles:true, cancelable:true, view:window, clientX:x, clientY:y + 80
              }));
              const mid = document.querySelector('.tg-move-ghost');
              const shown = mid && mid.style.display !== 'none';
              document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true, code:'Escape'}));
              const after = document.querySelector('.tg-move-ghost');
              return {
                shown,
                afterDisp: after ? after.style.display : 'none',
              };
            }"""
        )
        after10 = [(r.get("name"), r.get("copy")) for r in data()]
        ok("esc ghost was shown then hidden", esc.get("shown") and esc.get("afterDisp") == "none", esc)
        ok("esc leaves data", before10 == after10, after10[:3])

        # --- 11. 右键菜单四项 ---
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 2, i_copy)
        page.wait_for_timeout(80)
        cell = page.query_selector('.tabulator-cell[tabulator-field="name"]')
        cell.click(button="right")
        page.wait_for_timeout(200)
        menu = page.evaluate(
            """() => {
              const m = document.querySelector('.tabulator-menu');
              if (!m) return {on:false, text:''};
              return {on:true, text: m.innerText || ''};
            }"""
        )
        mt = menu.get("text") or ""
        ok("context menu opened", menu.get("on"), menu)
        ok(
            "menu has cut/copy/paste/clear",
            (
                ("剪切" in mt or "tasks.ctx.cut" in mt)
                and ("Ctrl+X" in mt)
                and ("Ctrl+C" in mt)
                and ("Ctrl+V" in mt)
                and ("Delete" in mt or "清除" in mt or "tasks.ctx.clear" in mt)
            ),
            mt[:400],
        )
        page.keyboard.press("Escape")
        page.wait_for_timeout(80)

        # 已开工文案格：剪切置灰
        page.evaluate("() => window.__tasksDebug.patchRow(0, {status:'prepared', id:'T0014'})")
        page.click('.tabulator-cell[tabulator-field="copy"]')
        set_range(0, i_copy, 0, i_copy)
        page.wait_for_timeout(80)
        page.query_selector('.tabulator-cell[tabulator-field="copy"]').click(button="right")
        page.wait_for_timeout(200)
        menu2 = page.evaluate(
            """() => {
              const m = document.querySelector('.tabulator-menu');
              if (!m) return {on:false};
              const items = [...m.querySelectorAll('.tabulator-menu-item')].map(el => ({
                text: el.innerText, dis: el.classList.contains('tabulator-menu-item-disabled')
              }));
              return {on:true, items};
            }"""
        )
        items = menu2.get("items") or []
        cut_item = next((x for x in items if "剪切" in (x.get("text") or "") or "tasks.ctx.cut" in (x.get("text") or "")), None)
        copy_item = next((x for x in items if "Ctrl+C" in (x.get("text") or "")), None)
        ok("started copy: cut disabled", bool(cut_item and cut_item.get("dis")), items[:6])
        ok("started copy: copy still enabled", bool(copy_item and not copy_item.get("dis")), items[:6])

        # 真拖一次边框（不只 moveBy）
        reset()
        page.click('.tabulator-cell[tabulator-field="name"]')
        set_range(0, i_name, 0, i_copy)
        page.wait_for_selector(".tg-move-s")
        page.evaluate(
            """() => {
              const edge = document.querySelector('.tg-move-s');
              const r = edge.getBoundingClientRect();
              const x = r.left + Math.max(8, r.width/2);
              const y = r.top + 1;
              const destY = y + 34 * 2;
              edge.dispatchEvent(new MouseEvent('mousedown', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:y
              }));
              document.dispatchEvent(new MouseEvent('mousemove', {
                bubbles:true, cancelable:true, view:window, clientX:x, clientY:destY
              }));
              document.dispatchEvent(new MouseEvent('mouseup', {
                bubbles:true, cancelable:true, view:window, button:0, clientX:x, clientY:destY
              }));
            }"""
        )
        page.wait_for_timeout(250)
        rowsD = data()
        ok(
            "real border drag moved A onto row 2",
            not (rowsD[0].get("name") or "").strip() and rowsD[2].get("name") == "A",
            [r.get("name") for r in rowsD[:4]],
        )

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
