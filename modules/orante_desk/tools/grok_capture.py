# -*- coding: utf-8 -*-
r"""Grok 生图摸底 + 抓包。

    python tools\grok_capture.py probe            只看界面：截图 + 控件清单，不点任何东西
    python tools\grok_capture.py auto             挂参考图 + 写提示词 + 点生成，全程录接口
    python tools\grok_capture.py watch --secs 600 只录接口，你自己在浏览器里操作

选项： --acc 账号名/ID   --secs 秒数   --image 参考图路径   --prompt 提示词
       --click "按钮上的字"   auto 模式下点哪个按钮（默认自动猜）

产物都在 data\capture\：
    grok_capture_<时间>.jsonl   接口原文（去掉了 cookie / authorization）
    grok-<步骤>.png             截图
    grok-ui-<步骤>.json         控件清单（按钮/输入框/可编辑区/文件输入）
"""
from __future__ import annotations
import argparse, asyncio, base64, io, json, os, re, socket, sqlite3, subprocess, sys, time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
ORANTE = os.path.dirname(HERE)
DB = os.path.join(ORANTE, "data", "orante.db")
OUT_DIR = os.path.join(ORANTE, "data", "capture")
os.makedirs(OUT_DIR, exist_ok=True)
IMAGINE = "https://grok.com/imagine"

INTERESTING = re.compile(
    r"grok\.com/(rest|api)/|/trpc/|upload|/assets/|imagine|x\.ai/|generate|inference", re.I)
SKIP_HDR = {"cookie", "authorization", "set-cookie", "x-csrf-token"}
_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
         ".bmp": "image/bmp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# 控件清单：把页面上能点、能输入的东西全列出来，附上位置，方便照着写自动化
UI_JS = r"""() => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4 ? [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] : null; };
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: ((el.innerText || el.value || '') + '').replace(/\s+/g, ' ').trim().slice(0, 60),
    aria: el.getAttribute('aria-label') || '',
    title: el.getAttribute('title') || '',
    type: el.getAttribute('type') || '',
    role: el.getAttribute('role') || '',
    testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || '',
    cls: (el.className || '').toString().split(/\s+/).slice(0, 6).join(' '),
    box: vis(el),
  });
  const pick = (sel) => Array.from(document.querySelectorAll(sel)).map(desc);
  return {
    url: location.href, title: document.title,
    buttons: pick('button, [role=button], a[href*="imagine"]').filter(b => b.box),
    inputs: pick('input, textarea, select'),
    editable: pick('[contenteditable=true], div.tiptap'),
    files: pick('input[type=file]'),
    switches: pick('[role=switch], [role=tab], [role=radio], [role=menuitem], [role=option]').filter(b => b.box),
    text: (document.body.innerText || '').replace(/\n{2,}/g, '\n').slice(0, 3000),
  };
}"""

DROP_JS = r"""async ({b64, name, type}) => {
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const file = new File([arr], name, {type});
  const dt = new DataTransfer(); dt.items.add(file);
  const ed = document.querySelector('div.tiptap');
  const targets = [];
  if (ed) { targets.push(ed);
    const form = ed.closest('form') || ed.parentElement;
    if (form) targets.push(form);
    if (form && form.parentElement) targets.push(form.parentElement); }
  targets.push(document.body);
  const fired = [];
  for (const el of targets) {
    for (const t of ['dragenter', 'dragover', 'drop'])
      el.dispatchEvent(new DragEvent(t, {bubbles: true, cancelable: true, dataTransfer: dt}));
    fired.push(el.tagName.toLowerCase() + '.' + ((el.className || '').toString().split(/\s+/)[0] || ''));
    await new Promise(r => setTimeout(r, 700));
    if (document.querySelector("img[src^='blob:']")) return {ok: true, fired};
  }
  return {ok: false, fired};
}"""


def port_alive(port):
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", int(port))); return True
    except Exception:
        return False
    finally:
        s.close()


def chrome_exe():
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        if not base:
            continue
        p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 chrome.exe")


def _db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    return c


def pick_account(want):
    c = _db()
    rows = [dict(r) for r in c.execute("SELECT * FROM accounts WHERE engine='grok' ORDER BY created")]
    c.close()
    if not rows:
        raise SystemExit("库里没有 grok 账号")
    if want:
        for r in rows:
            if want.lower() in (str(r["id"]).lower(), str(r["name"]).lower()):
                return r
        raise SystemExit(f"没有叫 {want} 的 grok 账号，有：{', '.join(r['name'] for r in rows)}")
    for r in rows:
        if r["enabled"]:
            return r
    return rows[0]


def default_image():
    """随便找一张现成的主体图当参考：最新那个任务的 images\\ 里第一张。"""
    c = _db()
    rows = [dict(r) for r in c.execute(
        "SELECT dir FROM tasks WHERE dir IS NOT NULL AND dir<>'' ORDER BY id DESC LIMIT 8")]
    c.close()
    for r in rows:
        d = os.path.join(r["dir"], "images")
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if os.path.splitext(n)[1].lower() in _MIME and not n.startswith("_"):
                return os.path.join(d, n)
    return ""


def profile_dir(acc):
    prof = acc.get("profile") or acc["id"]
    return prof if os.path.isabs(prof) else os.path.join(ORANTE, "data", "chrome", prof)


def launch(acc):
    port = int(acc["port"]); prof = profile_dir(acc)
    os.makedirs(prof, exist_ok=True)
    if not port_alive(port):
        print(f"▶ 拉起 {acc['name']} 的 Chrome  端口 {port}")
        subprocess.Popen([chrome_exe(), f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                          "--no-first-run", "--no-default-browser-check",
                          "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                          "--disable-renderer-backgrounding", IMAGINE],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=(0x200 | 0x1000000) if os.name == "nt" else 0)
        for _ in range(80):
            if port_alive(port):
                break
            time.sleep(0.5)
        else:
            raise SystemExit("Chrome 没在端口起来")
        time.sleep(4)
    else:
        print(f"· {acc['name']} 的 Chrome 已在跑（端口 {port}），直接连")
    return port


class Rec:
    """网络录制器。"""

    def __init__(self, tag):
        ts = datetime.now().strftime("%m%d-%H%M%S")
        self.path = os.path.join(OUT_DIR, f"grok_capture_{ts}.jsonl")
        self.f = open(self.path, "a", encoding="utf-8")
        self.n = 0
        self.reqs = {}

    def write(self, rec):
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n"); self.f.flush()

    def note(self, what, **kw):
        self.write({"kind": "note", "t": time.time(), "what": what, **kw})

    async def attach(self, page):
        try:
            cdp = await page.context.new_cdp_session(page)
        except Exception as e:
            print("  attach 失败", e); return
        await cdp.send("Network.enable", {"maxResourceBufferSize": 100 * 1024 * 1024,
                                          "maxTotalBufferSize": 300 * 1024 * 1024})

        def on_req(ev):
            r = ev.get("request", {}); u = r.get("url", "")
            if not INTERESTING.search(u):
                return
            self.reqs[ev["requestId"]] = {
                "t": time.time(), "type": ev.get("type"), "method": r.get("method"), "url": u,
                "headers": {k: v for k, v in (r.get("headers") or {}).items() if k.lower() not in SKIP_HDR},
                "postData": r.get("postData"), "hasPostData": r.get("hasPostData"), "page": page.url}

        def on_resp(ev):
            rec = self.reqs.get(ev["requestId"])
            if not rec:
                return
            rp = ev.get("response", {})
            rec.update({"status": rp.get("status"), "mime": rp.get("mimeType"),
                        "respHeaders": {k: v for k, v in (rp.get("headers") or {}).items()
                                        if k.lower() not in SKIP_HDR}})

        async def finish(ev):
            rid = ev["requestId"]
            rec = self.reqs.pop(rid, None)
            if not rec:
                return
            mime = (rec.get("mime") or "").lower()
            try:
                if any(k in mime for k in ("json", "text", "event-stream", "protobuf", "javascript")) or mime == "":
                    b = await cdp.send("Network.getResponseBody", {"requestId": rid})
                    body = b.get("body")
                    if b.get("base64Encoded"):
                        body = "<base64 %d bytes>" % len(body or "")
                    if body and len(body) > 400_000:
                        body = body[:400_000] + f"...<truncated {len(body)} total>"
                else:
                    body = f"<{mime} {ev.get('encodedDataLength')} bytes>"
            except Exception as e:
                body = f"<no body: {e}>"
            if rec.get("postData") is None and rec.get("hasPostData"):
                try:
                    pd = await cdp.send("Network.getRequestPostData", {"requestId": rid})
                    rec["postData"] = pd.get("postData")
                except Exception:
                    pass
            rec["body"] = body
            self.write(rec); self.n += 1
            u = re.sub(r"^https?://[^/]+", "", rec["url"])[:120]
            print(f"  [{self.n:03d}] {rec.get('status')} {rec['method']:4s} {u}")

        cdp.on("Network.requestWillBeSent", on_req)
        cdp.on("Network.responseReceived", on_resp)
        cdp.on("Network.loadingFinished", lambda ev: asyncio.ensure_future(finish(ev)))
        cdp.on("Network.loadingFailed", lambda ev: self.reqs.pop(ev["requestId"], None))
        print(f"· 已挂上页面: {(page.url or '(空白)')}"[:110])


async def snap(page, step):
    """截图 + 控件清单，落到 data\\capture\\。"""
    png = os.path.join(OUT_DIR, f"grok-{step}.png")
    try:
        await page.screenshot(path=png, full_page=False)
    except Exception as e:
        print(f"  · 截图失败 {e}")
    try:
        ui = await page.evaluate(UI_JS)
    except Exception as e:
        ui = {"error": str(e)}
    with open(os.path.join(OUT_DIR, f"grok-ui-{step}.json"), "w", encoding="utf-8") as f:
        json.dump(ui, f, ensure_ascii=False, indent=1)
    nb = len(ui.get("buttons") or []) if isinstance(ui, dict) else 0
    print(f"  · [{step}] 截图 + 控件清单已存（按钮 {nb} 个，url {str(ui.get('url'))[:60]}）")
    return ui


# composer 上的模式 radio：不同账号界面语言不一样，几种写法都试
MODE_WORDS = ["Immagine", "Image", "图片", "图像"]


async def pick_mode(page, want=""):
    """把 composer 从 Video 切到 图片模式。返回实际点到的那个的说明。"""
    words = ([want] if want else []) + MODE_WORDS
    for w in words:
        try:
            loc = page.get_by_role("radio", name=w, exact=False).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=5000)
                return f"点了 radio「{w}」"
        except Exception:
            continue
    # 兜底：拿这组 radio 的第一个（界面顺序是 图片 / 视频 / 代理）
    try:
        radios = page.get_by_role("radio")
        n = await radios.count()
        if n:
            await radios.nth(0).click(timeout=5000)
            return f"按顺序点了第 1 个 radio（共 {n} 个）"
    except Exception as e:
        return f"没切成：{e}"
    return "页面上没找到模式 radio"


async def find_page(ctx, rec):
    for pg in ctx.pages:
        if "grok.com" in (pg.url or ""):
            return pg
    pg = await ctx.new_page()
    await rec.attach(pg)
    await pg.goto(IMAGINE, wait_until="domcontentloaded", timeout=60000)
    return pg


async def run(args):
    from playwright.async_api import async_playwright
    acc = pick_account(args.acc)
    print(f"账号 {acc['name']}（{acc['id']}）")
    port = launch(acc)
    rec = Rec(args.mode)
    async with async_playwright() as pw:
        br = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20000)
        ctx = br.contexts[0]
        for pg in ctx.pages:
            await rec.attach(pg)
        ctx.on("page", lambda pg: asyncio.ensure_future(rec.attach(pg)))
        page = await find_page(ctx, rec)
        await page.bring_to_front()
        if "imagine" not in (page.url or ""):
            await page.goto(IMAGINE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        print(f"录制中 → {rec.path}")
        await snap(page, "01-home")

        if args.mode == "watch":
            print(f"· 只录不动：{args.secs} 秒内请自己在浏览器里操作")
            t0 = time.time()
            while time.time() - t0 < args.secs:
                await asyncio.sleep(1)
        elif args.mode == "auto":
            # ⓪ 先重开一次 imagine，把上一轮残留的图和字清掉
            await page.goto(IMAGINE, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            # ① 先把模式从 Video 切到 Image（composer 上那组 radio）
            picked = await pick_mode(page, args.mode_label)
            print(f"  · 模式切换：{picked}")
            await asyncio.sleep(1.5)
            await snap(page, "02-image-mode")
            img = args.image or default_image()
            if not img or not os.path.isfile(img):
                print(f"✗ 找不到参考图（{img}），用 --image 指一张")
            else:
                print(f"· 参考图 {img}")
                with open(img, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                mime = _MIME.get(os.path.splitext(img)[1].lower(), "image/jpeg")
                rec.note("drop_image", file=img)
                r = await page.evaluate(DROP_JS, {"b64": b64, "name": os.path.basename(img), "type": mime})
                print(f"  · 拖图结果 {r}")
                await asyncio.sleep(5)
                await snap(page, "03-dropped")
            prompt = args.prompt
            try:
                ed = page.locator("div.tiptap").first
                await ed.click(timeout=8000)
                await page.keyboard.type(prompt, delay=8)
                print(f"  · 已写提示词：{prompt[:40]}…")
            except Exception as e:
                print(f"  · 写提示词失败：{e}")
            await asyncio.sleep(2)
            await snap(page, "04-typed")
            if args.dry:
                print("  · --dry：写完就停，不提交")
                await asyncio.sleep(2)
            else:
                rec.note("submit", how=args.click or "enter")
                try:
                    if args.click:
                        await page.get_by_role("button", name=args.click).first.click(timeout=8000)
                        print(f"  · 已点「{args.click}」")
                    else:
                        await page.keyboard.press("Enter")
                        print("  · 已按回车提交")
                except Exception as e:
                    print(f"  · 提交失败：{e}")
                for i in range(max(1, args.secs // 10)):
                    await asyncio.sleep(10)
                    if i in (0, 2, 5, 11, 17):
                        await snap(page, f"05-after-{(i + 1) * 10}s")
        else:  # probe
            await asyncio.sleep(2)
            await snap(page, "02-probe")
        await asyncio.sleep(1)
    rec.f.close()
    print(f"\n✓ 共记录 {rec.n} 条请求 → {rec.path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="probe", choices=["probe", "auto", "watch"])
    ap.add_argument("--acc", default="")
    ap.add_argument("--secs", type=int, default=180)
    ap.add_argument("--image", default="")
    ap.add_argument("--prompt", default="保持上传图像中的人物形象服装和动作都不变，背景场景也保持不变，"
                                        "只更换拍摄距离为胸部以上的中近景，拍摄角度更改为平视的拍摄角度，"
                                        "更换人物的朝向为朝左的正侧面（脸部轮廓清楚可见），"
                                        "最终成品图片必须保持画面有且只有一个完整的高清真实色彩漂亮清晰"
                                        "好看真实的获奖级别的画质的整幅摄影图片。")
    ap.add_argument("--click", default="")
    ap.add_argument("--mode-label", dest="mode_label", default="")
    ap.add_argument("--dry", action="store_true")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
