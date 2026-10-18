# -*- coding: utf-8 -*-
"""看看 Grok 现在卡在哪，把 ToS 门点掉。"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orante import db, accounts, paths
from orante.engines.base import make_engine
from orante.engines.grok import IMAGINE_URL

OUT = paths.DATA / "capture" / "probe36"


def main():
    db.init()
    acc = [a for a in accounts.list_all() if a["engine"] == "grok" and a["enabled"]][-1]
    eng = make_engine(acc, print)
    eng.connect()
    page = eng.page
    print("url", page.url)
    page.screenshot(path=str(OUT / "b-gate.png"))
    info = page.evaluate("""() => {
      const btns = [];
      for (const b of document.querySelectorAll('button, [role=button], a')) {
        const t = ((b.innerText||'') + ' ' + (b.getAttribute('aria-label')||'')).replace(/\\s+/g,' ').trim();
        if (t) btns.push(t.slice(0, 80));
        if (btns.length > 30) break;
      }
      return {url: location.href, title: document.title, text: (document.body.innerText||'').slice(0, 1500), btns};
    }""")
    (OUT / "b-gate.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2)[:2000])
    # 把所有带 accept/agree/continue/接受/同意 的按钮点一遍
    for t in (info.get("btns") or []):
        if not any(k in t.lower() for k in ("accept", "agree", "continue", "i understand",
                                             "接受", "同意", "继续", "明白")):
            continue
        print("click", t)
        try:
            page.get_by_text(t.strip(), exact=False).first.click(timeout=3000)
        except Exception as e:
            print("  fail", e)
    page.wait_for_timeout(2000)
    if "tos-gate" in (page.url or ""):
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
    print("after", page.url)
    page.screenshot(path=str(OUT / "b-after-gate.png"))
    try:
        page.wait_for_selector("div.tiptap", state="visible", timeout=15000)
        print("tiptap OK")
    except Exception as e:
        print("no tiptap", e)
    eng.close()


if __name__ == "__main__":
    main()
