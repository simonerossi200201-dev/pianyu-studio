# -*- coding: utf-8 -*-
"""抓「影格 / 素材」两种模式下 eb1hJf 的载荷差别（0920）。

背景：Orante 出片一直走的是**影格**（首帧/尾帧）那条路，尾帧那一格空着
（见 specs/reports/36-probe.md）。0920 起 TRE-1 上这条路被 Flow 判成
「偵測到異常活動」，而在网页上把模式切成**素材**，同一张图同一段提示词就能出片。
所以要把两种模式的载荷都抓下来逐格对比，才知道该改哪一格。

怎么用（双击 tools\\8-Flow-Mode-Capture.bat，或者带账号名）：
  1. 脚本会连上该账号那个 Chrome（没开就拉起来），开始录。
  2. 到那个 Chrome 里，**手动**做两次生成，中间不用停：
       第一次：模式选「素材」（第二行第二个），随便一张图 + 一句提示词 → 发。
       第二次：模式选「影格」（第二行第一个），同一张图同一句话 → 发（预期会失败）。
  3. 回到这个窗口按回车。脚本会把两条载荷逐格打出来并存盘。

落盘：Orante\\data\\capture\\flow_mode_<时间>.jsonl（原文）
      Orante\\data\\capture\\flow_mode_<时间>.txt  （对比，直接发给 Claude 看）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, paths, accounts          # noqa: E402
from orante.engines import chrome               # noqa: E402

OUT_DIR = paths.DATA / "capture"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RPC_RE = re.compile(r"rpcids=([^&]+)")
WATCH = ("eb1hJf",)          # 图生视频。别的 rpc 也记，但只逐格解析这个


def pick_account(name: str) -> dict:
    db.init()
    r = db.one("SELECT * FROM accounts WHERE (name=? OR id=?) AND engine='flow'", (name, name))
    if not r:
        have = [x["name"] for x in db.rows("SELECT name FROM accounts WHERE engine='flow'")]
        raise SystemExit(f"没有这个 Flow 账号：{name}（有：{'、'.join(have)}）")
    return dict(r)


def inner_of(body: str) -> tuple[str, object] | None:
    """从 batchexecute 的请求体里把 (rpc, 内层载荷) 拆出来。"""
    if not body or "f.req=" not in body:
        return None
    raw = body.split("f.req=", 1)[1].split("&", 1)[0]
    try:
        outer = json.loads(unquote_plus(raw))
        rpc, inner_json = outer[0][0][0], outer[0][0][1]
        return rpc, json.loads(inner_json)
    except Exception:
        return None


def describe(inner) -> list[str]:
    """把 eb1hJf 的内层载荷讲成人话，重点是图片槽和顶层那几个标志位。"""
    out = []
    try:
        jobs, ctx = inner[0], inner[1]
        job = jobs[0]
        out.append(f"  job 长度 {len(job)}；顶层 ctx 长度 {len(ctx)}")
        out.append(f"  [1] 模型 id      = {job[1]!r}")
        out.append(f"  [2] ?           = {job[2]!r}")
        out.append(f"  [3] ?           = {job[3]!r}")
        slot = job[4] if len(job) > 4 else None
        out.append(f"  [4] 图片槽       = {json.dumps(slot, ensure_ascii=False)}")
        if isinstance(slot, list):
            for i, v in enumerate(slot):
                out.append(f"        下标 {i}: {json.dumps(v, ensure_ascii=False)}")
        out.append(f"  [5] 集合槽       = {json.dumps(job[5] if len(job) > 5 else None, ensure_ascii=False)}")
        for i, v in enumerate(job[6:], start=6):
            out.append(f"  [{i}] 多出来的     = {json.dumps(v, ensure_ascii=False)}")
        out.append(f"  ctx             = {json.dumps(ctx, ensure_ascii=False)[:400]}")
    except Exception as e:
        out.append(f"  （解析失败 {e}）原文：{json.dumps(inner, ensure_ascii=False)[:600]}")
    return out


async def record(port: int, out_jsonl: Path, out_txt: Path) -> None:
    from playwright.async_api import async_playwright

    hits: list[dict] = []
    seen_pages = set()

    def note(rpc: str, inner, url: str) -> None:
        rec = {"t": time.strftime("%H:%M:%S"), "rpc": rpc, "url": url[:200], "inner": inner}
        with out_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if rpc in WATCH:
            hits.append(rec)
            print(f"\n[{len(hits)}] {rec['t']}  抓到一条 {rpc}")
            for line in describe(inner):
                print(line)
            print("-" * 70)

    async with async_playwright() as pw:
        br = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctxs = br.contexts

        async def attach(page):
            if id(page) in seen_pages:
                return
            seen_pages.add(id(page))

            def on_request(req):
                try:
                    if "batchexecute" not in req.url:
                        return
                    m = RPC_RE.search(req.url)
                    rpc = m.group(1) if m else "?"
                    body = req.post_data or ""
                    got = inner_of(body)
                    if got:
                        note(got[0], got[1], req.url)
                    elif rpc in WATCH:
                        note(rpc, {"_raw_body": body[:500]}, req.url)
                except Exception:
                    pass

            page.on("request", on_request)

        for c in ctxs:
            for p in c.pages:
                await attach(p)
            c.on("page", lambda p: asyncio.create_task(attach(p)))

        print("=" * 70)
        print(f"录制中 → {out_jsonl.name}")
        print("现在到那个 Chrome 里手动做两次生成：")
        print("   ① 模式选「素材」（第二行第二个），一张图 + 一句提示词 → 发")
        print("   ② 模式选「影格」（第二行第一个），同一张图同一句话 → 发")
        print("做完回到这个窗口按回车。")
        print("=" * 70)
        await asyncio.get_event_loop().run_in_executor(None, input)

    lines = [f"# Flow 模式对比 · {datetime.now():%Y-%m-%d %H:%M}", ""]
    for i, rec in enumerate(hits, 1):
        lines.append(f"## 第 {i} 条  {rec['t']}  {rec['rpc']}")
        lines += describe(rec["inner"])
        lines.append("")
        lines.append("原文：")
        lines.append(json.dumps(rec["inner"], ensure_ascii=False))
        lines.append("")
    out_txt.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ 抓到 {len(hits)} 条 eb1hJf")
    print(f"  原文  → {out_jsonl}")
    print(f"  对比  → {out_txt}   ← 把这个文件发给 Claude")


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "TRE-1"
    acc = pick_account(name)
    port = int(acc["port"])
    prof = accounts.profile_dir(acc)
    url = acc.get("project_url") or "https://flow.google.com/"
    print(f"账号 {acc['name']}（{acc['id']}）端口 {port}")
    chrome.launch(port, prof, url, tab_if_alive=True)
    for _ in range(60):
        if chrome.port_alive(port):
            break
        time.sleep(0.5)
    else:
        raise SystemExit("Chrome 没在端口起来")
    ts = datetime.now().strftime("%m%d-%H%M%S")
    asyncio.run(record(port, OUT_DIR / f"flow_mode_{ts}.jsonl", OUT_DIR / f"flow_mode_{ts}.txt"))


if __name__ == "__main__":
    main()
