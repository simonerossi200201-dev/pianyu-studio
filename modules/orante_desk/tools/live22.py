# -*- coding: utf-8 -*-
"""规格 22 活测：起临时 uvicorn，收片/编排搜 T0013。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))
OUT = ROOT / "specs" / "reports"
PORT = 9499


def _serve():
    from orante import server, jobs  # noqa: F401
    jobs.load_all_services()
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


def wait_up(page, url, n=40):
    for _ in range(n):
        try:
            r = page.request.get(url)
            if r.ok:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("FAIL 服务器没起来")


def pick_task(page, tid):
    page.locator(".tp-trig").first.click()
    box = page.locator(".tp-search input")
    if box.count():
        box.fill(tid)
    page.locator(".tp-item", has_text=tid).first.click()
    page.wait_for_timeout(400)


def search(page, q):
    inp = page.locator(".ts-bar input")
    inp.click()
    inp.fill("")
    inp.fill(q)
    page.wait_for_timeout(250)


def main():
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        wait_up(page, f"http://127.0.0.1:{PORT}/api/state")
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.wait_for_timeout(600)

        page.locator("nav.nodebar .node", has_text="收片").click()
        page.wait_for_timeout(400)
        pick_task(page, "T0013")
        search(page, "Nineveh")
        card = page.locator('.panel-review .rv-card[data-s="3"]')
        card.wait_for(timeout=4000)
        cls = card.get_attribute("class") or ""
        if "ts-hit" not in cls and "ts-flash" not in cls:
            raise SystemExit(f"FAIL Nineveh 没点亮 s003 class={cls}")
        count = page.locator(".panel-review .ts-count").inner_text()
        card.screenshot(path=str(OUT / "22-review-nineveh.png"))
        print("OK 收片 Nineveh → s003", count, cls)

        search(page, "(John 8:34–35)")
        page.wait_for_timeout(250)
        card5 = page.locator('.panel-review .rv-card[data-s="5"]')
        cls5 = card5.get_attribute("class") or ""
        if "ts-hit" not in cls5 and "ts-flash" not in cls5:
            raise SystemExit(f"FAIL 弯破折号没点亮 s005 class={cls5}")
        print("OK 收片 (John 8:34–35) → s005", cls5)

        page.locator("nav.nodebar .node", has_text="编排").click()
        page.wait_for_timeout(500)
        pick_task(page, "T0013")
        search(page, "(John 8:34–35)")
        cell = page.locator('.panel-track .tcell[data-s="5"]')
        cell.wait_for(timeout=4000)
        ccls = cell.get_attribute("class") or ""
        if "ts-hit" not in ccls and "ts-flash" not in ccls:
            raise SystemExit(f"FAIL 编排没点亮配图轨 s005 class={ccls}")
        page.locator(".panel-track .bwrap").screenshot(path=str(OUT / "22-track-john.png"))
        print("OK 编排 (John 8:34–35) → 配图轨 s005", ccls)
        browser.close()
    print("活测通过")


if __name__ == "__main__":
    main()
