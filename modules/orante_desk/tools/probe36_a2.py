# -*- coding: utf-8 -*-
"""探针 A′ 第二轮：已开着的 Flow Chrome 上搜 JS + 截图 + 尝试首尾帧 UI。"""
from __future__ import annotations
import json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, accounts, paths
from orante.engines import chrome
from orante.engines.base import make_engine

OUT = paths.DATA / "capture" / "probe36"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    db.init()
    acc = [a for a in accounts.list_all() if a["engine"] == "flow" and a["enabled"]][0]
    print("account", acc["name"], acc["port"], "alive", chrome.port_alive(acc["port"]))
    job_log = lambda m: print(m, flush=True)
    eng = make_engine(acc, job_log)
    eng.connect()
    page = eng.page
    print("url", page.url)
    page.screenshot(path=str(OUT / "a-flow.png"), full_page=False)

    info = page.evaluate(r"""() => {
      const scripts = [...document.querySelectorAll('script[src]')].map(s => s.src);
      const text = (document.body && document.body.innerText || '').slice(0, 2500);
      const labels = [];
      for (const el of document.querySelectorAll('*')) {
        const t = ((el.getAttribute && (el.getAttribute('aria-label')||'')) + ' ' + (el.innerText||'')).replace(/\s+/g,' ').trim();
        if (t && t.length < 48 && /frame|Frame|start|Start|end|End|首|尾|帧|视频|生成|Frames|Ingredients/i.test(t))
          labels.push({tag: el.tagName, t});
        if (labels.length > 60) break;
      }
      return {scripts: scripts.slice(0, 80), text, labels, title: document.title};
    }""")
    (OUT / "a-page.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("scripts", len(info.get("scripts") or []), "labels", len(info.get("labels") or []))
    print("title", info.get("title"))
    print("text sample:\n", (info.get("text") or "")[:800])

    hits = []
    keys = ("eb1hJf", "endImage", "lastFrame", "last_frame", "i2v_fl",
            "StartAndEnd", "endMedia", "lastMedia", "FRAMES", "end_image")
    for u in (info.get("scripts") or [])[:50]:
        try:
            txt = page.evaluate("""async (u) => {
              try { const r = await fetch(u, {credentials:'include'});
                    return await r.text(); }
              catch(e) { return ''; }
            }""", u)
        except Exception:
            continue
        if not txt:
            continue
        found = [k for k in keys if k in txt]
        if not found:
            continue
        rec = {"url": u.split("/")[-1][:80], "keys": found, "snips": []}
        for k in found:
            i = txt.find(k)
            rec["snips"].append({"key": k, "around": txt[max(0, i-240): i+320]})
        hits.append(rec)
        print("HIT", rec["url"], found)
    (OUT / "a-js-hits2.json").write_text(json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8")
    print("hits", len(hits))

    # 尝试点「Frames」类按钮
    clicked = []
    for lab in ("Frames to video", "Frames", "Frame", "首尾帧", "从帧生成", "Ingredients"):
        try:
            loc = page.get_by_text(lab, exact=False).first
            if loc.count() > 0:
                loc.click(timeout=2000)
                clicked.append(lab)
                time.sleep(1)
                print("clicked", lab)
        except Exception as e:
            clicked.append(f"{lab}: {e}"[:80])
    page.screenshot(path=str(OUT / "a-flow-after-click.png"), full_page=False)
    (OUT / "a-clicks.json").write_text(json.dumps(clicked, ensure_ascii=False, indent=2), encoding="utf-8")
    eng.close()
    print("done")


if __name__ == "__main__":
    main()
