# -*- coding: utf-8 -*-
"""Flow 抓包器 —— 把泳道 Chrome 里 flow.google.com 的真实接口调用原样录下来。

用法:  python tools\flow_capture.py [泳道名]     默认 TIK-L1

做的事:
  1. 泳道 Chrome 没开就按 lanes.json 的端口 + profile 把它拉起来
  2. 用 CDP 的 Network 域监听所有 flow.google.com 页面
  3. 只记录接口类请求 (fx/api、trpc、aisandbox、上传、媒体地址)，
     请求体 + 响应体一起落盘到 Orante\data\capture\flow_capture_<时间>.jsonl
  4. 你在浏览器里手动: 上传一张图 → 写提示词 → 选 4s/6s/8s → 生成 → 等出片 → 下载
  5. 回到黑窗口按回车结束
不记录 cookie / authorization 头。
"""
from __future__ import annotations
import asyncio, io, json, os, re, socket, subprocess, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
ORANTE = os.path.dirname(HERE)
DEV = os.path.dirname(ORANTE)

def find_pipe_root():
    for d in os.listdir(DEV):
        p = os.path.join(DEV, d)
        if os.path.isfile(os.path.join(p, "0-长文案", "lanes.json")):
            return p
    raise SystemExit("找不到含 0-长文案\\lanes.json 的流水线目录")

PIPE = find_pipe_root()
LANES = os.path.join(PIPE, "0-长文案", "lanes.json")
CHROME_ROOT = os.path.join(PIPE, "Chrome_Profile")
OUT_DIR = os.path.join(ORANTE, "data", "capture")
os.makedirs(OUT_DIR, exist_ok=True)

INTERESTING = re.compile(r"flow\.google\.com/(fx/)?api|/trpc/|aisandbox|labs\.google/fx/api|upload|generativelanguage|storage\.googleapis|lh3\.googleusercontent|/v1(beta)?/", re.I)
SKIP_HDR = {"cookie", "authorization", "set-cookie"}


HOOK_JS = r"""
(() => {
  if (window.__oranteHooked) return 'already';
  window.__oranteHooked = true; window.__rcCalls = [];
  const wrap = () => {
    try {
      const g = window.grecaptcha && window.grecaptcha.enterprise;
      if (!g || !g.execute || g.__wrapped) return false;
      const orig = g.execute.bind(g);
      g.execute = function(k, o) { try { window.__rcCalls.push({k: String(k), action: o && o.action, t: Date.now()}); } catch(e){}; return orig(k, o); };
      g.__wrapped = true; return true;
    } catch(e) { return false; }
  };
  if (!wrap()) { const iv = setInterval(() => { if (wrap()) clearInterval(iv); }, 500); }
  return 'hooked';
})()
"""
WIZ_JS = r"""() => { const w = window.WIZ_global_data || {}; const out = {}; for (const k of ['SNlM0e','cfb2h','FdrFJe','oPEP7c','hl','Yllh3e']) out[k] = w[k]; out.keys = Object.keys(w).length; return out; }"""


def port_alive(port):
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port)); return True
    except Exception:
        return False
    finally:
        s.close()

def chrome_exe():
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "")):
        p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 chrome.exe")

def launch(rec, url):
    port = int(rec["port"])
    prof = os.path.join(CHROME_ROOT, os.path.basename(rec["chrome_profile"]))
    os.makedirs(prof, exist_ok=True)
    if not port_alive(port):
        print(f"▶ 拉起泳道 Chrome  端口 {port}  profile {os.path.basename(prof)}")
        subprocess.Popen([chrome_exe(), f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                          "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                          "--disable-renderer-backgrounding", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=(0x200 | 0x1000000) if os.name == "nt" else 0)
        for _ in range(80):
            if port_alive(port): break
            time.sleep(0.5)
        else:
            raise SystemExit("Chrome 没在端口起来")
    else:
        print(f"· 泳道 Chrome 已在跑（端口 {port}），直接连")
    return port

def main():
    lane = sys.argv[1] if len(sys.argv) > 1 else "TIK-L1"
    lanes = json.load(open(LANES, encoding="utf-8"))
    if lane not in lanes:
        raise SystemExit(f"lanes.json 里没有 {lane}，有: {', '.join(lanes)}")
    rec = lanes[lane]
    url = rec.get("project_url") or "https://flow.google.com/"
    port = launch(rec, url)

    asyncio.run(record(port))


async def record(port):
    from playwright.async_api import async_playwright
    ts = datetime.now().strftime("%m%d-%H%M%S")
    out_path = os.path.join(OUT_DIR, f"flow_capture_{ts}.jsonl")
    out = open(out_path, "a", encoding="utf-8")
    dbg = open(os.path.join(OUT_DIR, "capture_debug.log"), "a", encoding="utf-8")
    reqs = {}
    n = [0]
    hooked_pages = []

    def write(rec_):
        out.write(json.dumps(rec_, ensure_ascii=False) + "\n"); out.flush()

    def short(u):
        m = re.search(r"/trpc/([^?]+)", u)
        if m: return "trpc:" + m.group(1)
        return re.sub(r"^https?://[^/]+", "", u)[:120]

    async def attach(page):
        try:
            cdp = await page.context.new_cdp_session(page)
        except Exception as e:
            print("  attach 失败", e); return
        await cdp.send("Network.enable", {"maxResourceBufferSize": 100 * 1024 * 1024,
                                          "maxTotalBufferSize": 300 * 1024 * 1024})

        def on_req(ev):
            r = ev.get("request", {}); u = r.get("url", "")
            if ev.get("type") not in ("Image", "Font", "Stylesheet", "Script", "Media"):
                dbg.write(f"{r.get('method')} {u[:160]}\n"); dbg.flush()
            if not INTERESTING.search(u): return
            reqs[ev["requestId"]] = {"t": time.time(), "type": ev.get("type"), "method": r.get("method"), "url": u,
                 "headers": {k: v for k, v in (r.get("headers") or {}).items() if k.lower() not in SKIP_HDR},
                 "postData": r.get("postData"), "hasPostData": r.get("hasPostData"), "page": page.url}

        def on_resp(ev):
            rec_ = reqs.get(ev["requestId"])
            if not rec_: return
            rp = ev.get("response", {})
            rec_.update({"status": rp.get("status"), "mime": rp.get("mimeType"),
                         "respHeaders": {k: v for k, v in (rp.get("headers") or {}).items() if k.lower() not in SKIP_HDR}})

        async def finish(ev):
            rid = ev["requestId"]
            rec_ = reqs.pop(rid, None)
            if not rec_: return
            mime = (rec_.get("mime") or "").lower()
            body = None
            try:
                if any(k in mime for k in ("json", "text", "protobuf", "javascript")) or mime == "":
                    b = await cdp.send("Network.getResponseBody", {"requestId": rid})
                    body = b.get("body")
                    if b.get("base64Encoded"): body = "<base64 %d bytes>" % len(body or "")
                    if body and len(body) > 400_000: body = body[:400_000] + f"...<truncated {len(body)} total>"
                else:
                    body = f"<{mime} {ev.get('encodedDataLength')} bytes>"
            except Exception as e:
                body = f"<no body: {e}>"
            if rec_.get("postData") is None and rec_.get("hasPostData"):
                try:
                    pd = await cdp.send("Network.getRequestPostData", {"requestId": rid})
                    rec_["postData"] = pd.get("postData")
                except Exception:
                    pass
            rec_["body"] = body
            write(rec_); n[0] += 1
            print(f"  [{n[0]:03d}] {rec_.get('status')} {rec_['method']:4s} {short(rec_['url'])}")

        cdp.on("Network.requestWillBeSent", on_req)
        cdp.on("Network.responseReceived", on_resp)
        cdp.on("Network.loadingFinished", lambda ev: asyncio.ensure_future(finish(ev)))
        cdp.on("Network.loadingFailed", lambda ev: reqs.pop(ev["requestId"], None))
        print(f"· 已挂上页面: {(page.url or '') or '(空白页/正在加载)'}"[:110])
        dbg.write(f"attach {page.url}\n"); dbg.flush()
        try:
            r1 = await page.evaluate(HOOK_JS); r2 = await page.evaluate(WIZ_JS)
            dbg.write(f"hook={r1} wiz={json.dumps(r2)[:600]}\n"); dbg.flush()
        except Exception as e:
            dbg.write(f"hook fail {e}\n"); dbg.flush()
        hooked_pages.append(page)

    async with async_playwright() as pw:
        br = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20000)
        ctx = br.contexts[0]
        # 所有页面都挂（刚起的 Chrome 首页 URL 可能还是空的），请求层再按 URL 过滤
        for pg in ctx.pages:
            await attach(pg)
        ctx.on("page", lambda pg: asyncio.ensure_future(attach(pg)))
        print()
        print("=" * 70)
        print(f"录制中 → {out_path}")
        print("现在到那个 Chrome 里手动做一遍：上传图片 → 写提示词 → 选时长/模型 → 生成 → 等出片 → 点下载。")
        print("做完回到这个窗口按回车结束。")
        print("=" * 70)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            pass
        for pg in hooked_pages:
            try:
                calls = await pg.evaluate("() => window.__rcCalls || []")
                dbg.write("recaptcha calls: " + json.dumps(calls) + "\n")
                write({"kind": "recaptcha_calls", "page": pg.url, "calls": calls})
            except Exception as e:
                dbg.write(f"rc read fail {e}\n")
        dbg.flush()
        await asyncio.sleep(1.0)
    out.close()
    print(f"\n✓ 共记录 {n[0]} 条请求 → {out_path}")


if __name__ == "__main__":
    main()
