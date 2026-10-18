# -*- coding: utf-8 -*-
"""规格 48：截编排段表，确认文案两行省略号、单击展开、时长列还在。"""
from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "specs" / "reports"
MANIFEST = Path(r"D:\1_PROJECT\2026-09-11\A\T0015-自媒体1-李星辰v01\manifest.json")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass


def _segs():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = []
    for sg in m.get("segments") or []:
        text = sg.get("text") or ""
        html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = html.replace("glacier", "<mark>glacier</mark>")
        out.append({
            "s": sg["s"],
            "text": text,
            "html": html,
            "dur": sg.get("duration") or 8,
            "warn": "补拍锁定" if sg["s"] == 2 else "",
        })
    return out


def main():
    segs = _segs()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/tools/_shot48.html"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 640, "height": 900})
        page.goto(url, wait_until="networkidle")
        page.evaluate("segs => renderSegs(segs)", segs)

        def measure():
            return page.evaluate("""() => {
              const rows = [...document.querySelectorAll('.btable tbody tr')];
              return rows.map(tr => {
                const tds = tr.querySelectorAll('td');
                const txt = tds[3];
                const span = txt.querySelector('span');
                const dur = tds[2].getBoundingClientRect();
                const cs = getComputedStyle(span);
                return {
                  s: tr.getAttribute('data-s'),
                  title: (tr.getAttribute('title') || '').length,
                  open: txt.classList.contains('open'),
                  clamp: cs.webkitLineClamp,
                  spanH: Math.round(span.getBoundingClientRect().height),
                  scrollH: span.scrollHeight,
                  clamped: span.scrollHeight > span.clientHeight + 2,
                  durW: Math.round(dur.width),
                  durX: Math.round(dur.left),
                  durVisible: dur.width > 20 && dur.right < window.innerWidth,
                  hasMark: !!txt.querySelector('mark'),
                  hasWarn: !!txt.querySelector('.w'),
                  warnH: txt.querySelector('.w') ? Math.round(txt.querySelector('.w').getBoundingClientRect().height) : 0,
                };
              });
            }""")

        collapsed = measure()
        page.screenshot(path=str(OUT_DIR / "48-track-text-collapsed.png"), full_page=True)

        page.locator('td.txt').nth(0).click()
        page.locator('td.txt').nth(1).click()
        expanded = measure()
        page.screenshot(path=str(OUT_DIR / "48-track-text-open.png"), full_page=True)

        page.locator('td.txt').nth(0).click()
        folded = measure()

        page.set_viewport_size({"width": 480, "height": 900})
        narrow = measure()
        page.screenshot(path=str(OUT_DIR / "48-track-text-narrow.png"), full_page=True)
        browser.close()
    httpd.shutdown()

    bad = []
    if not collapsed:
        bad.append("没有行")
    for i, row in enumerate(collapsed):
        if row["title"] < 20:
            bad.append(f"s{row['s']} 没有全文 title")
        if row["open"]:
            bad.append(f"s{row['s']} 默认不该 open")
        if i < 5:  # 长文案，窄列下应超过两行
            if row["clamp"] not in ("2", "2.0"):
                bad.append(f"s{row['s']} clamp={row['clamp']!r} 应为 2")
            if row["spanH"] > 44:
                bad.append(f"s{row['s']} 折叠后还高于两行 h={row['spanH']}")
        if not row["durVisible"] or row["durW"] < 30:
            bad.append(f"s{row['s']} 时长列被挤掉 w={row['durW']}")
    if not collapsed[0]["hasMark"]:
        bad.append("glacier 高亮 mark 没了")
    if not collapsed[1]["hasWarn"] or collapsed[1]["warnH"] < 10:
        bad.append(".w 警告行没单独露出来")

    if not expanded[0]["open"]:
        bad.append("单击后第 1 行没展开")
    if expanded[0]["clamp"] in ("2", "2.0"):
        bad.append("open 后还在 line-clamp:2")
    if expanded[0]["spanH"] <= collapsed[0]["spanH"]:
        bad.append(f"展开后高度没增加 {collapsed[0]['spanH']} → {expanded[0]['spanH']}")
    if folded[0]["open"] or folded[0]["clamp"] not in ("2", "2.0"):
        bad.append("再单击没有折回")

    for row in narrow:
        if not row["durVisible"] or row["durW"] < 30:
            bad.append(f"窄屏 s{row['s']} 时长列没了 w={row['durW']}")

    print("collapsed", collapsed[:3])
    print("expanded0", expanded[0])
    print("folded0", folded[0])
    print("narrow dur", [r["durW"] for r in narrow[:3]])
    if bad:
        raise SystemExit("FAIL " + "；".join(bad))
    print("OK 两行省略号 / 单击展开折回 / glacier mark / .w 单独一行 / 时长列还在")


if __name__ == "__main__":
    main()
