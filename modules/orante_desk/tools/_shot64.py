# -*- coding: utf-8 -*-
"""干净出厂截图：五个面板。不进分发包。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ORANTE_DATA"] = tempfile.mkdtemp(prefix="orante-shot64-")

from orante import config, db, paths  # noqa: E402
from orante.main import serve  # noqa: E402

db.init()
config.set("setup.done", True)
OUT = ROOT / "说明文档" / "分发图"
OUT.mkdir(parents=True, exist_ok=True)
PORT = 9419
URL = f"http://127.0.0.1:{PORT}/"


def wait():
    for _ in range(80):
        try:
            urllib.request.urlopen(URL + "api/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    t = threading.Thread(target=serve, args=(PORT,), daemon=True)
    t.start()
    if not wait():
        print("server not up")
        return 1
    from playwright.sync_api import sync_playwright
    shots = [
        ("tasks", "01-tasks.png"),
        ("gemini", "02-gemini.png"),
        ("track", "03-track.png"),
        ("generate", "04-generate.png"),
        ("review", "05-review.png"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_function("() => window.store && store.i18n && store.i18n['node.tasks']", timeout=20000)
        page.wait_for_selector("nav.nodebar", timeout=20000)
        page.evaluate("() => { if (window.ui) window.ui.showSetup = false; }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.store && store.i18n && store.i18n['node.tasks']", timeout=20000)
        page.evaluate("() => { if (window.ui) window.ui.showSetup = false; }")
        page.wait_for_timeout(800)
        buttons = page.locator("nav.nodebar button.node")
        for i, (_nid, name) in enumerate(shots):
            buttons.nth(i).click()
            page.wait_for_timeout(600)
            dest = OUT / name
            page.screenshot(path=str(dest), full_page=False)
            print("wrote", dest)
        browser.close()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
