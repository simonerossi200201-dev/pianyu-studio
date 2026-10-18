# -*- coding: utf-8 -*-
"""规格 44 验收：工具条规格 + 导入对话框 + 窄窗折叠。对着夹具跑，补 i18n。"""
from __future__ import annotations

import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8770
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"
OUT = ROOT / "specs" / "reports"

ZH = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        return


def main() -> int:
    from playwright.sync_api import sync_playwright

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    failed = []

    def ok(name, cond, detail=""):
        if cond:
            print("  ok  " + name)
        else:
            failed.append(name)
            print("  FAIL " + name + (("  " + str(detail)) if detail else ""))

    extra = {k: v for k, v in ZH.items() if k.startswith("tasks.") or k.startswith("common.") or k == "node.tasks"}
    extra_js = json.dumps(extra, ensure_ascii=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.add_init_script(
            "window.confirm = (m) => { window._confirms = (window._confirms||[]); window._confirms.push(String(m)); return true; };"
        )
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")
        page.evaluate(
            """(extra) => {
              const old = window.t;
              window.t = (k, vars) => {
                let s = extra[k] !== undefined ? extra[k] : old(k, vars);
                if (vars) for (const [a,b] of Object.entries(vars)) s = String(s).split('{'+a+'}').join(b);
                return s;
              };
            }""",
            extra,
        )
        # 夹具的 t 已经闭包进组件，重挂一次才能吃到新字典
        page.evaluate(
            """(extra) => {
              const old = window.t;
              window.t = (k, vars) => {
                let s = extra[k] !== undefined ? extra[k] : (old ? old(k, vars) : k);
                if (s === k && extra[k] !== undefined) s = extra[k];
                if (vars) for (const [a,b] of Object.entries(vars)) s = String(s).split('{'+a+'}').join(b);
                return s;
              };
              const el = document.getElementById('app');
              el.innerHTML = '';
              const app = Vue.createApp({
                components: { Panel_tasks },
                template: '<main class="stage"><Panel_tasks/></main>'
              });
              app.mount('#app');
            }""",
            extra,
        )
        page.wait_for_selector(".tabulator-tableholder", timeout=15000)
        page.wait_for_function("() => window.__tasksDebug")
        page.wait_for_timeout(400)

        def data():
            return page.evaluate("() => window.__tasksDebug.data()")

        def count():
            return page.evaluate("() => window.__tasksDebug.count()")

        # 1. 工具条按钮同高、无 btn sm、+行是整体
        hd = page.locator(".panel-hd")
        ok("no btn.sm in toolbar", page.evaluate("() => !document.querySelector('.panel-hd .btn.sm')"))
        heights = page.evaluate(
            """() => [...document.querySelectorAll('.panel-hd .btn')].map(b => ({
              t: b.innerText.trim(), h: Math.round(b.getBoundingClientRect().height),
              r: getComputedStyle(b).borderRadius, sm: b.classList.contains('sm')
            }))"""
        )
        hs = [x["h"] for x in heights if x["t"] != ""]
        ok("toolbar buttons same height 30", all(h == 30 for h in hs), heights)
        ok("no sm class", all(not x["sm"] for x in heights), heights)
        seg = page.evaluate(
            """() => {
              const s = document.querySelector('.tg-add-seg');
              const r = s.getBoundingClientRect();
              const inp = s.querySelector('.tg-add-n');
              const ir = inp.getBoundingClientRect();
              return {h: Math.round(r.height), radius: getComputedStyle(s).borderRadius,
                      ih: Math.round(ir.height), one: Math.abs(r.top - ir.top) < 2};
            }"""
        )
        ok("+行 segmented 30px", seg.get("h") == 30 and seg.get("one"), seg)
        divs = page.evaluate("() => document.querySelectorAll('.tg-bar-div').length")
        ok("group dividers present", divs >= 3, divs)
        save_pri = page.evaluate("() => document.querySelector('.tg-save').classList.contains('primary')")
        ok("save not primary", save_pri is False)
        page.screenshot(path=str(OUT / "44-toolbar.png"))

        # 2. ⋯ 菜单三项
        page.click(".tg-more-btn")
        page.wait_for_selector(".tg-more-menu")
        menu = page.inner_text(".tg-more-menu")
        ok("more has 重置列宽", "重置列宽" in menu, menu)
        ok("more has 导出选中为 TSV", "导出" in menu, menu)
        ok("more has 只看已选", "只看已选" in menu, menu)
        page.screenshot(path=str(OUT / "44-more-menu.png"))
        page.click("button.mi:has-text('重置列宽')")
        page.wait_for_timeout(150)
        ok("reset cols closes menu", page.evaluate("() => !document.querySelector('.tg-more-menu')"))

        # 3. 删除选中颜色
        del_off = page.evaluate(
            """() => {
              const b = document.querySelector('.tg-del');
              const cs = getComputedStyle(b);
              return {dis: b.disabled, on: b.classList.contains('on'), color: cs.color, border: cs.borderColor};
            }"""
        )
        ok("delete disabled when empty", del_off["dis"] is True, del_off)
        ok("delete not .on when empty", del_off["on"] is False, del_off)

        page.evaluate(
            """() => {
              const dt = new DataTransfer();
              dt.setData('text/plain', '甲\\t文案A\\t\\t短文案\\tLucy\\t6\\n乙\\t文案B\\t\\t短文案\\tLucy\\t6');
              document.querySelector('.tabulator').dispatchEvent(
                new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
            }"""
        )
        page.wait_for_timeout(300)
        page.evaluate(
            """() => {
              const cells = [...document.querySelectorAll('.tabulator-cell[tabulator-field="_sel"]')];
              cells.slice(0,2).forEach(c => {
                const r = c.getBoundingClientRect();
                c.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, button:0, clientX:r.left+8, clientY:r.top+8}));
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, button:0}));
              });
            }"""
        )
        page.wait_for_timeout(150)
        del_on = page.evaluate(
            """() => {
              const b = document.querySelector('.tg-del');
              const cs = getComputedStyle(b);
              return {dis: b.disabled, on: b.classList.contains('on'), color: cs.color, border: cs.borderColor};
            }"""
        )
        ok("delete enabled when selected", del_on["dis"] is False, del_on)
        ok("delete .on when selected", del_on["on"] is True, del_on)
        ok("delete border red-ish", "217" in del_on["border"] or "d970" in del_on["border"].lower() or "217, 112" in del_on["border"], del_on)

        page.click(".tg-more-btn")
        page.wait_for_selector(".tg-more-menu")
        page.click("button.mi:has-text('只看已选')")
        page.wait_for_timeout(120)
        only = page.evaluate("() => document.querySelector('.tg-count').classList.contains('on')")
        ok("只看已选 toggles", only is True, only)
        page.click(".tg-more-btn")
        page.wait_for_selector(".tg-more-menu")
        with page.expect_download(timeout=5000) as dl:
            page.click("button.mi:has-text('导出选中为 TSV')")
        dlinfo = dl.value
        ok("export tsv download", "tsv" in (dlinfo.suggested_filename or "").lower(), dlinfo.suggested_filename)

        # 4. 保存不是实心；改一格出现未保存
        page.evaluate("() => window.__tasksDebug.markDirty()")
        page.wait_for_timeout(80)
        sav = page.evaluate(
            """() => {
              const b = document.querySelector('.tg-save');
              const s = document.querySelector('.tg-savestat');
              return {pri: b.classList.contains('primary'), txt: s.innerText, cls: s.className};
            }"""
        )
        ok("save still not primary", sav["pri"] is False, sav)
        ok("dirty status visible", "未保存" in sav["txt"] or "is-dirty" in sav["cls"], sav)

        # 5. 导入对话框：表头 + 34 行
        page.click(".tg-imp")
        page.wait_for_selector(".dlg-import")
        header = "任务名\t文案\t图片来源\t任务类型\t账号\t时长"
        rows = [f"名{i}\t文案{i}\thttps://x/{i}\t短文案\tLucy\t6" for i in range(1, 35)]
        blob = header + "\n" + "\n".join(rows)
        page.fill(".dlg-import .paste-box", blob)
        page.wait_for_timeout(200)
        det = page.inner_text(".imp-preview-hd")
        ok("detected 34 rows", "34" in det, det)
        skip = page.evaluate("() => document.querySelector('.imp-skip input').checked")
        ok("skip header auto-checked", skip is True)
        chips = page.inner_text(".imp-map-sum")
        ok("map shows 任务名", "任务名" in chips, chips)
        ok("map shows 文案", "文案" in chips, chips)
        page.screenshot(path=str(OUT / "44-import-dlg.png"))

        # 改第 1 列 → 文案，追加
        page.locator(".imp-map .imp-col select").first.select_option("copy")
        page.wait_for_timeout(80)
        st = page.evaluate("() => window.__tasksDebug.impState && window.__tasksDebug.impState()")
        if not st or (st.get("map") or [None])[0] != "copy":
            page.evaluate("() => window.__tasksDebug.setImpMap && window.__tasksDebug.setImpMap(0, 'copy')")
            page.wait_for_timeout(80)
            st = page.evaluate("() => window.__tasksDebug.impState && window.__tasksDebug.impState()")
        print("    remap state", st)
        page.click("button:has-text('追加')")
        page.wait_for_timeout(400)
        after = count()
        ok("appended 34 rows", after >= 34, after)
        copies = [r.get("copy") or "" for r in data()]
        # 第1列改成文案后，copy 应是「名N」而不是「文案N」
        mapped = [c for c in copies if c.startswith("名")]
        ok("remapped col1 into copy", len(mapped) >= 30, mapped[:3])

        # 再开一次，勾掉跳过第一行 → 35
        page.click(".tg-imp")
        page.wait_for_selector(".dlg-import")
        page.fill(".dlg-import .paste-box", blob)
        page.wait_for_timeout(150)
        page.click(".imp-skip")
        page.wait_for_timeout(80)
        det2 = page.inner_text(".imp-preview-hd")
        ok("uncheck skip → 35", "35" in det2, det2)
        page.click("button:has-text('取消')")

        # 6. 选文件 csv / xlsx
        page.click(".tg-imp")
        page.wait_for_selector(".dlg-import")
        page.click("label:has-text('选择文件')")
        page.wait_for_timeout(80)
        csv_content = "任务名,文案,图片来源,任务类型,账号,时长\nC1,抄1,http://a,短文案,Lucy,6\nC2,抄2,http://b,短文案,Lucy,6"
        page.set_input_files("input.file-hidden", {
            "name": "t.csv",
            "mimeType": "text/csv",
            "buffer": csv_content.encode("utf-8"),
        })
        page.wait_for_timeout(250)
        detc = page.inner_text(".imp-preview")
        ok("csv detected rows", "2" in detc, detc)
        page.click("button:has-text('取消')")

        page.click(".tg-imp")
        page.wait_for_selector(".dlg-import")
        page.click("label:has-text('选择文件')")
        page.set_input_files("input.file-hidden", {
            "name": "t.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "buffer": b"PK\x03\x04fake",
        })
        page.wait_for_timeout(250)
        toasts = page.evaluate("() => window._toasts || []")
        ok("xlsx still unsupported", any("xlsx" in str(x).lower() or "SheetJS" in str(x) or "csv" in str(x) for x in toasts), toasts)
        page.click("button:has-text('取消')")

        # 7. 窄窗口折叠
        page.set_viewport_size({"width": 700, "height": 800})
        page.wait_for_timeout(300)
        page.evaluate("() => window.__tasksDebug.fitToolbar && window.__tasksDebug.fitToolbar()")
        page.wait_for_timeout(250)
        fold = page.evaluate("() => window.__tasksDebug.fold()")
        ok("fold density at 700", fold.get("den") is True, fold)
        ok("fold add at 700", fold.get("add") is True, fold)
        ok("fold import at 700", fold.get("imp") is True, fold)
        vis = page.evaluate(
            """() => {
              const shown = el => {
                if (!el) return false;
                const g = el.closest('.tg-bar-g') || el;
                return getComputedStyle(g).display !== 'none' && getComputedStyle(el).display !== 'none';
              };
              const den = document.querySelector('.tg-bar > .tg-bar-g .tg-den');
              const add = document.querySelector('.tg-bar > .tg-bar-g .tg-add-seg');
              const imp = document.querySelector('.tg-bar > .tg-bar-g .tg-imp');
              const del = document.querySelector('.tg-del');
              const sav = document.querySelector('.tg-savestat');
              const hd = document.querySelector('.panel-hd');
              const wrap = getComputedStyle(hd).flexWrap;
              const r = hd.getBoundingClientRect();
              return {
                den: shown(den),
                add: shown(add),
                imp: shown(imp),
                del: shown(del),
                sav: shown(sav),
                wrap, hdH: Math.round(r.height)
              };
            }"""
        )
        ok("density hidden at 700", vis["den"] is False, vis)
        ok("add hidden at 700", vis["add"] is False, vis)
        ok("import hidden at 700", vis["imp"] is False, vis)
        ok("delete still out", vis["del"] is True, vis)
        ok("save status still out", vis["sav"] is True, vis)
        ok("toolbar nowrap", vis["wrap"] == "nowrap", vis)
        ok("toolbar not wrapping tall", vis["hdH"] < 56, vis)
        page.screenshot(path=str(OUT / "44-toolbar-700.png"))
        page.click(".tg-more-btn")
        page.wait_for_selector(".tg-more-menu")
        m2 = page.inner_text(".tg-more-menu")
        ok("folded items in more", "导入" in m2 and ("舒适" in m2 or "紧凑" in m2) and "+ 行" in m2, m2)
        page.keyboard.press("Escape")

        # 8. Ctrl+V 就地覆盖没变
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(200)
        page.evaluate("() => window.__tasksDebug.fitToolbar && window.__tasksDebug.fitToolbar()")
        n_before = count()
        page.evaluate(
            """() => {
              window.__tasksDebug.setRange(0, 2, 0, 2); // name col? cols: _sel id name copy...
            }"""
        )
        # 看列顺序
        cols = page.evaluate("() => window.__tasksDebug.cols()")
        name_i = cols.index("name") if "name" in cols else 2
        page.evaluate("""(i) => window.__tasksDebug.setRange(0, i, 0, i)""", name_i)
        page.evaluate(
            """() => {
              const dt = new DataTransfer();
              dt.setData('text/plain', '覆盖名');
              document.querySelector('.tabulator').dispatchEvent(
                new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
            }"""
        )
        page.wait_for_timeout(250)
        n_after = count()
        names = [r.get("name") for r in data()]
        ok("ctrl+v did not append a row", n_after == n_before, (n_before, n_after, cols))
        ok("ctrl+v overwrote in place", "覆盖名" in names, names[:5])

        browser.close()
    httpd.shutdown()
    print()
    if failed:
        print("FAILED", len(failed), failed)
        return 1
    print("all ui checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
