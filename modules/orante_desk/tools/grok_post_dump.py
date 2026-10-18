# -*- coding: utf-8 -*-
"""规格 51 A 节：把当前 Chrome 里 /imagine/post/ 详情页的 video / 网络 / probe JS 原样 dump 出来。

只读，不点、不导航、不关标签。给「页上明明有视频却永远不收」定性：到底是
FIND_VIDEO_JS 把 blob 滤掉了，还是 is_own_video 不认无扩展名地址。

    python tools/grok_post_dump.py            # 端口从账号库 / 默认 9251
    python tools/grok_post_dump.py --port 9251
    python tools/grok_post_dump.py --acc TIK-G-SIMONE

产物：tools/grok-post-dump.txt
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

os.chdir(ROOT)

from orante import paths  # noqa: E402

paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402

OUT = HERE / "grok-post-dump.txt"
DEFAULT_PORT = 9251
DEFAULT_ACC = "TIK-G-SIMONE"

DUMP_VIDEO_JS = r"""() => {
  const vis = (el) => {
    try {
      const st = getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0')
        return false;
    } catch (e) {}
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const ancestors = (el) => {
    const out = [];
    let p = el;
    for (let i = 0; i < 8 && p; i++) {
      const tag = (p.tagName || '').toLowerCase();
      const cls = ((p.className || '') + '').toString().slice(0, 160);
      const id = (p.id || '').toString().slice(0, 40);
      out.push(tag + (id ? '#' + id : '') + (cls ? '.' + cls.replace(/\s+/g, '.') : ''));
      p = p.parentElement;
    }
    return out;
  };
  return [...document.querySelectorAll('video')].map(v => {
    const r = v.getBoundingClientRect();
    return {
      src: v.getAttribute('src') || v.src || '',
      currentSrc: v.currentSrc || '',
      sources: [...v.querySelectorAll('source')].map(s => s.src || s.getAttribute('src') || ''),
      readyState: v.readyState,
      duration: (isFinite(v.duration) ? v.duration : null),
      paused: !!v.paused,
      muted: !!v.muted,
      rect: {x: r.x, y: r.y, w: r.width, h: r.height,
             top: r.top, left: r.left, bottom: r.bottom, right: r.right},
      visible: vis(v),
      ancestors: ancestors(v.parentElement || v),
    };
  });
}"""

PERF_JS = r"""() => {
  const re = /grok\.com|x\.ai|mp4|video|m3u8|generated/i;
  return (performance.getEntriesByType('resource') || [])
    .filter(e => re.test(e.name || ''))
    .map(e => ({
      name: e.name,
      initiatorType: e.initiatorType,
      duration: Math.round(e.duration || 0),
      transferSize: e.transferSize || 0,
    }));
}"""

TEXT_JS = r"""() => (document.body && document.body.innerText || '').slice(0, 600)"""


def _looks_like_post(ancestors):
    """对照 FIND_VIDEO_JS 的 looksLikePost：article/main 或 class 含 post|imagine|media|player|content。"""
    for a in ancestors or []:
        low = (a or "").lower()
        tag = low.split(".", 1)[0].split("#", 1)[0]
        if tag in ("article", "main"):
            return True, a
        if any(k in low for k in ("post", "imagine", "media", "player", "content")):
            return True, a
    return False, ""


def _post_id_of(url: str) -> str:
    m = grok_pipe.POST_RE.search(url or "")
    return m.group(1) if m else ""


def resolve_port(cli_port, cli_acc) -> tuple[int, str]:
    """命令行 > 账号库里的名字 > 默认 9251。"""
    if cli_port:
        return int(cli_port), f"命令行 --port {cli_port}"
    want = (cli_acc or DEFAULT_ACC).strip()
    db_path = paths.DATA / "orante.db"
    if db_path.is_file():
        try:
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            rows = list(c.execute(
                "SELECT id, name, port, engine FROM accounts WHERE engine='grok'"))
            c.close()
            for r in rows:
                if want.lower() in (str(r["name"] or "").lower(), str(r["id"] or "").lower()):
                    return int(r["port"]), f"账号库 {r['name']} ({r['id']})"
            if rows and not cli_acc:
                # 没点名就优先 SIMONE，否则第一个 grok
                for r in rows:
                    if "simone" in (str(r["name"] or "")).lower():
                        return int(r["port"]), f"账号库 {r['name']} ({r['id']})"
                r = rows[0]
                return int(r["port"]), f"账号库第一个 grok {r['name']} ({r['id']})"
        except Exception as e:
            print(f"! 读账号库失败：{e}", flush=True)
    return DEFAULT_PORT, f"默认 {DEFAULT_PORT}（{DEFAULT_ACC}）"


def dump_page(page, idx: int) -> list[str]:
    lines: list[str] = []
    try:
        url = page.url or ""
    except Exception as e:
        return [f"=== 页 {idx} 读 url 失败：{e} ==="]
    post = _post_id_of(url)
    lines.append("=" * 72)
    lines.append(f"页 {idx}")
    lines.append(f"page.url = {url}")
    lines.append(f"postId   = {post or '（URL 里抠不到）'}")
    lines.append("")

    try:
        videos = page.evaluate(DUMP_VIDEO_JS) or []
    except Exception as e:
        videos = []
        lines.append(f"<video> 探测失败：{e}")
    lines.append(f"<video> 共 {len(videos)} 个：")
    if not videos:
        lines.append("  （没有）")
    for i, v in enumerate(videos, 1):
        hit, which = _looks_like_post(v.get("ancestors") or [])
        lines.append(f"  [{i}] src        = {v.get('src')!r}")
        lines.append(f"      currentSrc = {v.get('currentSrc')!r}")
        lines.append(f"      <source>   = {v.get('sources')!r}")
        lines.append(f"      readyState = {v.get('readyState')}  duration={v.get('duration')}"
                     f"  paused={v.get('paused')}  muted={v.get('muted')}")
        r = v.get("rect") or {}
        lines.append(f"      rect       = x={r.get('x')} y={r.get('y')} "
                     f"w={r.get('w')} h={r.get('h')}  visible={v.get('visible')}")
        lines.append(f"      祖先 8 层  =")
        for a in (v.get("ancestors") or []):
            lines.append(f"        {a}")
        lines.append(f"      looksLikePost = {'会命中' if hit else '不会命中'}"
                     + (f"（{which}）" if which else ""))
    lines.append("")

    try:
        perf = page.evaluate(PERF_JS) or []
    except Exception as e:
        perf = []
        lines.append(f"performance 探测失败：{e}")
    lines.append(f"performance.resource 含 grok.com|x.ai|mp4|video|m3u8|generated：{len(perf)} 条")
    if not perf:
        lines.append("  （没有）")
    for e in perf[:40]:
        lines.append(f"  [{e.get('initiatorType')}|{e.get('transferSize')}B|{e.get('duration')}ms] {e.get('name')}")
    if len(perf) > 40:
        lines.append(f"  … 还有 {len(perf) - 40} 条")
    lines.append("")

    def _eval(js, name):
        try:
            return page.evaluate(js)
        except Exception as e:
            return f"<evaluate 失败：{e}>"

    find_raw = _eval(grok_pipe.FIND_VIDEO_JS, "FIND_VIDEO_JS")
    gen_raw = _eval(grok_pipe.IS_GENERATING_JS, "IS_GENERATING_JS")
    fail_raw = _eval(grok_pipe.IS_FAILED_JS, "IS_FAILED_JS")
    lines.append("FIND_VIDEO_JS 原样返回：")
    lines.append(json.dumps(find_raw, ensure_ascii=False, indent=2)[:4000])
    lines.append("")
    lines.append(f"IS_GENERATING_JS 原样返回：{gen_raw!r}")
    lines.append(f"IS_FAILED_JS 原样返回：{fail_raw!r}")
    lines.append("")

    strong, weak = grok_pipe._normalize_find(find_raw if isinstance(find_raw, (dict, list)) else {})
    lines.append(f"strong ({len(strong)}) / weak ({len(weak)})：")
    if not strong and not weak:
        lines.append("  （空）")
    for kind, arr in (("strong", strong), ("weak", weak)):
        for u in arr:
            own = grok_pipe.is_own_video(u, post, True, True)
            host = urlparse(u).hostname or ""
            lines.append(f"  [{kind}] is_own_video={own}  host={host}")
            lines.append(f"         {u}")
    lines.append("")

    text = _eval(TEXT_JS, "innerText")
    lines.append("document.body.innerText 前 600 字：")
    lines.append(repr(text) if not isinstance(text, str) else text)
    lines.append("")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="CDP 端口，覆盖账号库")
    ap.add_argument("--acc", default="", help="账号名/ID，默认 TIK-G-SIMONE")
    args = ap.parse_args(argv)

    port, how = resolve_port(args.port, args.acc)
    banner = [
        f"grok_post_dump  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"端口 {port}（{how}）",
        f"CDP  http://127.0.0.1:{port}",
        "",
    ]
    print("\n".join(banner), flush=True)

    from orante.engines import chrome
    if not chrome.port_alive(port):
        msg = (
            f"端口 {port} 上没有 Chrome。\n"
            "下次卡住时：先确认那个账号的 Chrome 还开着，再双击 tools\\11-Grok-Post-Dump.bat。\n"
        )
        print(msg, flush=True)
        OUT.write_text("".join(banner) + msg, encoding="utf-8")
        return 2
    if chrome.cdp_version(port) is None:
        msg = f"端口 {port} 被占着，但不是 Chrome 的 CDP。\n"
        print(msg, flush=True)
        OUT.write_text("".join(banner) + msg, encoding="utf-8")
        return 2

    from playwright.sync_api import sync_playwright
    lines = list(banner)
    n_post = 0
    n_pages = 0
    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20000)
        try:
            ctxs = list(br.contexts)
            lines.append(f"contexts: {len(ctxs)}")
            for ci, ctx in enumerate(ctxs):
                pages = list(ctx.pages)
                lines.append(f"  context[{ci}] pages={len(pages)}")
                for pi, pg in enumerate(pages):
                    n_pages += 1
                    try:
                        u = pg.url or ""
                    except Exception:
                        lines.append(f"    page[{pi}] 读 url 失败")
                        continue
                    lines.append(f"    page[{pi}] {u[:160]}")
                    if "/imagine/post/" not in u:
                        continue
                    n_post += 1
                    lines.extend(dump_page(pg, n_post))
            if n_post == 0:
                lines.append("")
                lines.append("没有 URL 含 /imagine/post/ 的标签页。")
                lines.append("那页已经关了，或当前停在 /imagine 首页。下次卡住时再双击这个 bat。")
        finally:
            try:
                br.close()
            except Exception:
                pass

    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(text, flush=True)
    print(f"→ 写了 {OUT}  （扫了 {n_pages} 页，dump 了 {n_post} 个详情页）", flush=True)
    return 0 if n_post else 3


if __name__ == "__main__":
    raise SystemExit(main())
