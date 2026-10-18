# -*- coding: utf-8 -*-
"""说明书 HTML 验收：图、目录、灯箱、400px、深色。不进分发包。"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "说明文档" / "使用说明.html").as_uri()


def main() -> int:
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.goto(HTML, wait_until="load")
        page.wait_for_timeout(400)
        n = page.evaluate(
            """() => {
              const imgs = [...document.querySelectorAll('figure.shot img')];
              return {
                total: imgs.length,
                complete: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
                todo: document.querySelectorAll('.shot-todo').length,
              };
            }"""
        )
        print("imgs", n)
        if n["total"] != 9 or n["complete"] != 9 or n["todo"] != 0:
            raise SystemExit(f"截图没齐：{n}")

        page.click('a[href="#p3"]')
        page.wait_for_timeout(400)
        toc = page.evaluate(
            """() => {
              const a = document.querySelector('.toc a[href="#p3"]');
              const sec = document.getElementById('p3');
              return {
                on: !!(a && a.classList.contains('on')),
                top: sec ? Math.round(sec.getBoundingClientRect().top) : 999,
              };
            }"""
        )
        print("toc", toc)
        if toc["top"] > 80:
            raise SystemExit(f"目录没跳到 #p3：{toc}")

        page.locator("figure.shot img").first.click()
        page.wait_for_selector("#lb.on")
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        if page.evaluate("() => document.getElementById('lb').classList.contains('on')"):
            raise SystemExit("Esc 没关掉灯箱")
        print("lightbox ok")

        page.set_viewport_size({"width": 400, "height": 800})
        page.wait_for_timeout(250)
        mobile = page.evaluate(
            """() => ({
              sw: document.documentElement.scrollWidth,
              cw: document.documentElement.clientWidth,
              cols: getComputedStyle(document.querySelector('.toc-nav')).columnCount,
            })"""
        )
        print("mobile", mobile)
        if mobile["sw"] > mobile["cw"] + 2:
            raise SystemExit(f"400px 出现横向滚动 {mobile}")
        if str(mobile["cols"]) not in ("2", "auto"):
            raise SystemExit(f"400px 目录不是两栏：{mobile['cols']}")

        page.emulate_media(color_scheme="dark")
        page.wait_for_timeout(200)
        colors = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.body);
              const bg = cs.backgroundColor;
              const fg = cs.color;
              const n = (s) => s.match(/\\d+/g).map(Number);
              const [br,bg_,bb] = n(bg), [fr,fg_,fb] = n(fg);
              const lum = (r,g,b) => (0.2126*r + 0.7152*g + 0.0722*b)/255;
              return {bg, fg, bgL: lum(br,bg_,bb), fgL: lum(fr,fg_,fb)};
            }"""
        )
        print("dark", colors)
        if colors["fgL"] - colors["bgL"] < 0.35:
            raise SystemExit(f"深色对比不够：{colors}")
        browser.close()
    print("html ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
