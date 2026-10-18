# -*- coding: utf-8 -*-
"""规格 22：截一张「中」尺寸收片卡片，确认四个按钮字没被截。"""
from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "specs" / "reports" / "22-card-m.png"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/tools/_shot22.html"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url, wait_until="networkidle")
        cards = page.locator(".rv-card")
        cards.nth(0).screenshot(path=str(OUT))
        cards.nth(1).screenshot(path=str(ROOT / "specs" / "reports" / "22-card-m-narrow.png"))
        # 量一下按钮文字有没有被裁
        info = page.evaluate("""() => {
          const cards = [...document.querySelectorAll('.rv-card')];
          return cards.map(c => {
            const btns = [...c.querySelectorAll('.tools button')].map(b => ({
              text: b.textContent.trim(),
              w: Math.round(b.getBoundingClientRect().width),
              sw: b.scrollWidth,
              clipped: b.scrollWidth > b.clientWidth + 1,
            }));
            const chip = c.querySelector('.swap-chip');
            const flags = c.querySelector('.flags').getBoundingClientRect();
            const cr = chip.getBoundingClientRect();
            return {
              cardW: Math.round(c.getBoundingClientRect().width),
              btns,
              chipRight: Math.round(cr.right),
              flagsRight: Math.round(flags.right),
              chipAtRight: Math.abs(cr.right - flags.right) < 2,
            };
          });
        }""")
        browser.close()
    httpd.shutdown()
    print("shot", OUT)
    print(info)
    bad = []
    for i, card in enumerate(info):
        for b in card["btns"]:
            if b["clipped"]:
                bad.append(f"card{i} {b['text']} clipped w={b['w']} sw={b['sw']}")
        if not card["chipAtRight"]:
            bad.append(f"card{i} 换图 chip 不在旗子行右端")
    if bad:
        raise SystemExit("FAIL " + "；".join(bad))
    print("OK 按钮文字完整，换图 chip 在右端")


if __name__ == "__main__":
    main()
