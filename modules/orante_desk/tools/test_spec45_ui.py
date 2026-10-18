# -*- coding: utf-8 -*-
"""规格 45：粘贴 Google Sheet / Excel 超链接，图片来源列拿网址。对着 tools/_grid28.html。"""
from __future__ import annotations

import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8767
URL = f"http://127.0.0.1:{PORT}/tools/_grid28.html"

DRIVE = "https://drive.google.com/drive/folders/1ZaBUtKjNWSv9TzJGND3IKCwAxBk4amMX"
DRIVE2 = "https://drive.google.com/drive/folders/1Cr0XdmUOAfjy8EwsRlBHSjWo4QSqcM2o"


def sheets_html(rows):
    """2026 实测结构：google-sheets-html-origin + a.in-cell-link，没有 data-sheets-hyperlink。"""
    trs = []
    for row in rows:
        tds = []
        for text, href in row:
            if href:
                tds.append(
                    f'<td style="text-decoration:underline;color:rgb(17,85,204)">'
                    f'<a class="in-cell-link" href="{href}" target="_blank">{text}</a></td>'
                )
            else:
                tds.append(f"<td>{text}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (
        '<google-sheets-html-origin><table xmlns="http://www.w3.org/1999/xhtml" '
        'cellspacing="0" cellpadding="0" dir="ltr" border="1" data-sheets-root="1">'
        "<tbody>" + "".join(trs) + "</tbody></table></google-sheets-html-origin>"
    )


def tsv_of(rows):
    return "\n".join("\t".join(text for text, _href in row) for row in rows)


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
    failed = []

    def ok(name, cond, detail=""):
        if cond:
            print("  ok  " + name)
        else:
            failed.append(name)
            print("  FAIL " + name + (("  " + str(detail)) if detail else ""))

    rows5 = []
    for i in range(1, 6):
        href = DRIVE if i % 2 else DRIVE2
        rows5.append([
            (f"李星辰v0{i}", None),
            (f"文案{i}", None),
            ("李良素材" if i % 2 else "网盘B", href),
            ("长文案" if i % 2 else "短文案", None),
            ("Lucy", None),
            ("6", None),
        ])
    html5 = sheets_html(rows5)
    text5 = tsv_of(rows5)

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

        def paste_both(text, html):
            page.evaluate(
                """({text, html}) => {
                  const dt = new DataTransfer();
                  dt.setData('text/plain', text);
                  if (html) dt.setData('text/html', html);
                  const el = document.querySelector('.tabulator');
                  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
                }""",
                {"text": text, "html": html},
            )

        # 1. 5×6，图片来源列还原 Drive 网址，其余列仍是文字
        paste_both(text5, html5)
        page.wait_for_timeout(500)
        rows = data()
        ok("pasted 5 rows", len(rows) >= 5, len(rows))
        imgs = [r.get("image_source") or "" for r in rows[:5]]
        names = [r.get("name") or "" for r in rows[:5]]
        copies = [r.get("copy") or "" for r in rows[:5]]
        ok("image col got drive url", all(x.startswith("https://drive.google.com/") for x in imgs), imgs)
        ok("name col stayed text", names[0].startswith("李星辰"), names)
        ok("copy col stayed text", copies[0].startswith("文案"), copies)
        toasts = page.evaluate("() => window._toasts || []")
        ok("toast mentions restored links", any("超链接还原" in str(x) for x in toasts), toasts)
        mark = page.evaluate("() => !!document.querySelector('.tg-link-mark')")
        ok("link mark shown", mark)

        # 2. 导入对话框同样吃 HTML
        page.evaluate("() => window.__tasksDebug.openImport('paste')")
        page.wait_for_selector(".dlg-import .paste-box")
        page.evaluate(
            """({text, html}) => {
              const dt = new DataTransfer();
              dt.setData('text/plain', text);
              dt.setData('text/html', html);
              const el = document.querySelector('.dlg-import .paste-box');
              el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
              el.value = text;
              el.dispatchEvent(new Event('input', {bubbles: true}));
            }""",
            {"text": text5, "html": html5},
        )
        page.wait_for_timeout(300)
        sample = page.evaluate(
            """() => {
              const em = document.querySelector('.imp-chip-val');
              return em ? em.textContent : '';
            }"""
        )
        chips = page.evaluate(
            """() => [...document.querySelectorAll('.imp-chip')].map(x => x.innerText)"""
        )
        ok("import preview shows drive url", "drive.google.com" in (sample or "") or any("drive.google.com" in str(c) for c in chips), {"sample": sample, "chips": chips})
        page.evaluate("() => window.__tasksDebug.confirmImport()")
        page.wait_for_timeout(400)
        rows2 = data()
        extra = [r.get("image_source") or "" for r in rows2[5:10]]
        ok("import appended drive urls", extra and all(x.startswith("https://drive.google.com/") for x in extra), extra)

        # 3. 编辑态 Ctrl+V
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        page.fill(".tg-add-n", "1")
        page.click("button:has-text('+ 行')")
        page.wait_for_timeout(200)
        page.dblclick('.tabulator-cell[tabulator-field="image_source"]')
        page.wait_for_timeout(150)
        page.evaluate(
            """({html, text}) => {
              const dt = new DataTransfer();
              dt.setData('text/plain', text);
              dt.setData('text/html', html);
              const inp = document.querySelector('.tabulator-editing input');
              if (!inp) { window._noInp = true; return; }
              inp.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
            }""",
            {"html": sheets_html([[("李良素材", DRIVE)]]), "text": "李良素材"},
        )
        page.wait_for_timeout(200)
        editing_val = page.evaluate(
            """() => {
              const inp = document.querySelector('.tabulator-editing input');
              return inp ? inp.value : '';
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        img0 = (data()[0] or {}).get("image_source") or ""
        ok("editor paste filled url", DRIVE in editing_val or img0 == DRIVE, {"inp": editing_val, "cell": img0})

        # 4. 纯文字，行为不变
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        paste_both("普通名字\t一段文案\t李良素材\t短文案\tLucy\t6", "")
        page.wait_for_timeout(300)
        r0 = data()[0]
        ok("plain paste keeps display name", (r0.get("image_source") or "") == "李良素材", r0.get("image_source"))
        warn = page.evaluate(
            """() => {
              const el = document.querySelector('.tabulator-cell[tabulator-field="image_source"]');
              return el ? el.classList.contains('cell-warn') : false;
            }"""
        )
        ok("plain name gets yellow warn", warn)
        toasts = page.evaluate("() => window._toasts || []")
        ok("plain paste no restore toast", not any("超链接还原" in str(x) for x in toasts), toasts)

        # 5. 合并单元格：HTML 1 列 vs TSV 2 列 → 丢弃链接，不串列
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        html_merge = (
            '<table><tr><td colspan="2"><a href="' + DRIVE + '">李良素材</a></td>'
            "<td>尾</td></tr></table>"
        )
        # TSV 是 3 列（合并展开后的形状）；HTML 若不展开会是 2 列。我们的解析会展开成 3 列。
        paste_both("李良素材\t\t尾", html_merge)
        page.wait_for_timeout(300)
        r0 = data()[0]
        ok("merged colspan: name is 李良素材", (r0.get("name") or "") == "李良素材", r0.get("name"))
        ok("merged colspan: copy not stolen", (r0.get("copy") or "") == "", r0.get("copy"))
        ok("merged colspan: image is 尾 or empty not 李良素材 in wrong col",
           (r0.get("image_source") or "") in ("尾", ""), r0)

        # 正确展开的合并：TSV 3 列，HTML colspan=2 展开后 3 列，图片列无链接
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        html_ok = (
            "<table><tr>"
            f'<td colspan="2"><a href="{DRIVE}">李良素材</a></td>'
            "<td>尾</td></tr></table>"
        )
        paste_both("李良素材\t\t尾", html_ok)
        page.wait_for_timeout(300)
        r0 = data()[0]
        # 从 name 列开始贴：name=李良素材, copy=空, image=尾（文字）
        ok("aligned merge did not scramble", (r0.get("name") or "") == "李良素材" and (r0.get("image_source") or "") == "尾", r0)

        # 6. google.com/url?q= 解开
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        wrapped = "https://www.google.com/url?q=" + DRIVE.replace(":", "%3A").replace("/", "%2F") + "&sa=D&source=editors"
        html_w = sheets_html([[("n", None), ("c", None), ("李良素材", wrapped)]])
        paste_both("n\tc\t李良素材", html_w)
        page.wait_for_timeout(300)
        got = (data()[0] or {}).get("image_source") or ""
        ok("unwrapped google url", got == DRIVE, got)

        # 7. Excel 风格 td a[href]
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".tabulator-tableholder")
        page.wait_for_function("() => window.__tasksDebug")
        html_xls = f'<html><body><table><tr><td><a href="{DRIVE}">李良素材</a></td></tr></table></body></html>'
        paste_both("李良素材", html_xls)
        page.wait_for_timeout(300)
        # 单列贴到当前格（默认第一数据格是 name）。要贴到图片来源，先点那一列。
        # 上面这次贴到了 name。再选图片来源贴一次。
        page.click('.tabulator-cell[tabulator-field="image_source"]')
        page.wait_for_timeout(80)
        paste_both("李良素材", html_xls)
        page.wait_for_timeout(300)
        got = (data()[0] or {}).get("image_source") or ""
        ok("excel a[href] into image col", got == DRIVE, got)

        browser.close()

    httpd.shutdown()
    if failed:
        print("\nFAILED " + str(len(failed)) + ": " + ", ".join(failed))
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
