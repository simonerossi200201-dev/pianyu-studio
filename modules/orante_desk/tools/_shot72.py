# -*- coding: utf-8 -*-
"""规格 72：干净演示库截 9 张说明书配图。不进分发包。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEMO_STILLS = ROOT / "tools" / "_demo72"
FFMPEG = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
OUT = ROOT / "说明文档" / "分发图"
SHOT_W, SHOT_H = 1500, 920

# 公版《北风和太阳》（伊索寓言，公有领域）切成十句，当演示口播稿。
NORTH_WIND = [
    "北风和太阳争论谁的本领更大。",
    "他们看见一个行人走在路上，身上穿着一件厚外套。",
    "两人约定：谁能先让行人脱掉外套，就算谁赢。",
    "北风先动手，它鼓起腮帮，拼命地吹。",
    "风越吹越猛，行人把外套裹得越紧。",
    "北风吹累了，只好停下来。",
    "太阳从云后走出来，暖洋洋地照着大地。",
    "行人觉得身上渐渐热起来。",
    "他解开扣子，把外套脱了下来。",
    "于是北风只好认输。",
]
JINGYE = "床前明月光，疑是地上霜。举头望明月，低头思故乡。"
CHUNXIAO = "春眠不觉晓，处处闻啼鸟。夜来风雨声，花落知多少。"
IMG_DESC = {
    "p01": "一只白瓷杯盛着热咖啡，放在折叠的亚麻餐巾上，木桌，晨光。",
    "p02": "金色黄昏下的山谷，近处几棵松树，远处一层层山脊。",
    "p03": "窗边一盆龟背竹，陶盆，白色窗框，日光斜照。",
    "p04": "打开的旧书和一盏铜油灯，放在深色木桌上。",
}


def _free_port(start: int = 9420) -> int:
    for p in range(start, start + 40):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit("找不到截图用的空闲端口")


def _encode_clip(jpg: Path, dest: Path, mark: int = 0) -> None:
    """每段片子略有不同，避免收片墙标「同一条视频」。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not FFMPEG.is_file():
        raise SystemExit(f"找不到 ffmpeg：{FFMPEG}")
    x = 8 + (mark % 7) * 6
    y = 8 + (mark // 7) * 6
    vf = f"scale=480:640,drawbox=x={x}:y={y}:w=6:h=6:color=white@0.15:t=fill"
    r = subprocess.run(
        [str(FFMPEG), "-y", "-loop", "1", "-i", str(jpg), "-t", str(1.6 + mark * 0.05),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", vf, "-an", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dest.is_file():
        raise SystemExit(f"ffmpeg 失败：{r.stderr[-400:]}")


def _seed(data: Path, ws: Path) -> str:
    """种账号、三行任务、图、片。返回主任务 id。"""
    from orante import accounts, config, db, tasks as taskmod
    from orante.models import Manifest, sha1_file

    db.init()
    config.set("setup.done", True)
    config.set("gemini.api_key", "")
    config.set("workspace.root", str(ws))
    config.set("track.strip_view", "shots")
    config.set("ui.lang", "zh")

    a_flow = accounts.create("演示flow", "flow")
    a_grok = accounts.create("演示grok", "grok")

    stills = [DEMO_STILLS / f"p{i:02d}.jpg" for i in range(1, 5)]
    for p in stills:
        if not p.is_file():
            raise SystemExit(f"缺演示静物图 {p}")

    def write_task(tid, name, acc, engine, status, gen_mode, copy_text, segs_text, with_clips):
        tdir = ws / tid
        (tdir / "images").mkdir(parents=True)
        (tdir / "clips").mkdir(parents=True)
        images = []
        for i, jpg in enumerate(stills, 1):
            p = f"p{i:02d}"
            dest = tdir / "images" / f"{p}.jpg"
            shutil.copy2(jpg, dest)
            images.append({
                "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(dest),
                "role": "主体", "status": "ok", "desc": IMG_DESC[p],
                "voice": "", "eye": "闭眼", "scene": "常规",
                "measure": {}, "origin": "local", "src": "demo",
            })
        segs = []
        for i, text in enumerate(segs_text, 1):
            p = f"p{((i - 1) % 4) + 1:02d}"
            take = []
            if with_clips:
                clip_name = f"{tid}-s{i:03d}-{p}.mp4"
                jpg = stills[(i - 1) % 4]
                _encode_clip(jpg, tdir / "clips" / clip_name, mark=i)
                take = [{"file": f"clips/{clip_name}"}]
            segs.append({
                "s": i, "text": text, "duration": 6, "image": p,
                "role": "主体", "shot": "", "prompt_final": "",
                "status": "collected" if with_clips else "planned",
                "gen_id": None, "takes": take, "error": None,
            })
        m = Manifest.new(tid, name, engine, acc["id"], "short", copy_text)
        m.images = images
        m.segments = segs
        m.mode = gen_mode or "first_frame"
        m.track = {
            "pins": {}, "pools": [], "hold": 2, "jitter": 1,
            "unit": "seg", "seed": 7,
        }
        m.save(tdir)
        now = db.now()
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,gen_mode,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, name, engine, acc["id"], "short", copy_text, "演示图库",
             "6", gen_mode, status, str(tdir), "", 0, now, now),
        )
        return tid

    write_task(
        "T0001", "北风与太阳", a_flow, "flow", "collected", "first_frame",
        "\n".join(NORTH_WIND), NORTH_WIND, True,
    )
    write_task(
        "T0002", "静夜思", a_grok, "grok", "arranged", "anchor_bridge",
        JINGYE, [JINGYE[:10], JINGYE[10:20], JINGYE[20:]], False,
    )
    write_task(
        "T0003", "春晓", a_flow, "flow", "prepared", "",
        CHUNXIAO, [CHUNXIAO[:12], CHUNXIAO[12:]], False,
    )
    # 列表顺序：先插的 row_order=0 会排一起；按创建时间。改一下 row_order。
    db.run("UPDATE tasks SET row_order=? WHERE id=?", (0, "T0001"))
    db.run("UPDATE tasks SET row_order=? WHERE id=?", (1, "T0002"))
    db.run("UPDATE tasks SET row_order=? WHERE id=?", (2, "T0003"))
    return "T0001"


def wait_up(url: str, n: int = 80) -> None:
    for _ in range(n):
        try:
            urllib.request.urlopen(url + "api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("服务没起来")


def save_shot(page, name: str) -> Path:
    dest = OUT / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(dest), full_page=False, type="png")
    im = Image.open(dest)
    w, h = im.size
    print(f"  wrote {dest.name}  {w}x{h}")
    if w < 1400 or w > 1600:
        raise SystemExit(f"{name} 宽度 {w} 不在 1400~1600")
    return dest


def main() -> int:
    if not (DEMO_STILLS / "p01.jpg").is_file():
        raise SystemExit(f"先准备 {DEMO_STILLS}\\p0n.jpg")
    data = Path(tempfile.mkdtemp(prefix="orante-shot72-"))
    ws = data / "workspace"
    ws.mkdir()
    os.environ["ORANTE_DATA"] = str(data)
    sys.path.insert(0, str(ROOT))

    from orante.main import serve  # noqa: E402  必须在 ORANTE_DATA 之后

    _seed(data, ws)
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    t = threading.Thread(target=serve, args=(port,), daemon=True)
    t.start()
    wait_up(url)
    print("server", url, "data", data)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": SHOT_W, "height": SHOT_H})
        page.set_default_timeout(25000)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_function(
            "() => window.store && store.i18n && store.i18n['tasks.col.name']==='任务名'",
            timeout=25000,
        )
        page.wait_for_selector("nav.nodebar", timeout=20000)
        page.evaluate("() => { if (window.ui) { ui.showSetup = false; ui.drawer = null; } }")
        # 任务表在 i18n 到之前就建了，表头会留下键名。切走再回来按中文重建列。
        page.evaluate("() => { ui.node = 'gemini'; }")
        page.wait_for_timeout(300)
        page.evaluate("() => { ui.node = 'tasks'; }")
        page.wait_for_function(
            """() => [...document.querySelectorAll('.tabulator-col-title')]
                 .some(el => (el.textContent || '').indexOf('任务名') >= 0)""",
            timeout=15000,
        )
        page.wait_for_timeout(400)

        def node(i: int) -> None:
            page.locator("nav.nodebar button.node").nth(i).click()
            page.wait_for_timeout(500)

        def dismiss() -> None:
            page.evaluate("() => { if (window.ui) { ui.showSetup = false; ui.drawer = null; ui.toasts = []; } }")

        # 01 任务
        node(0)
        page.wait_for_selector(".tabulator-row", timeout=15000)
        page.wait_for_function(
            "() => document.querySelectorAll('.tabulator-row').length >= 3")
        page.wait_for_timeout(400)
        save_shot(page, "01-tasks.png")

        # 02 多模态：展开固定结语，露出情绪预设
        node(1)
        page.wait_for_selector(".panel-gemini", timeout=15000)
        page.wait_for_timeout(600)
        tail = page.locator(".panel-gemini article.g-card[data-block='tail'] .g-hd")
        if tail.count():
            tail.first.click()
            page.wait_for_timeout(300)
        page.wait_for_selector(".emotion-preset", timeout=8000)
        page.evaluate(
            """() => {
              const el = document.querySelector('.emotion-preset');
              if (el) el.scrollIntoView({block:'nearest'});
            }""")
        page.wait_for_timeout(300)
        save_shot(page, "02-gemini.png")

        # 03 编排：预演 + 镜头序列 → 黄条
        node(2)
        page.wait_for_selector(".panel-track .bctl", timeout=15000)
        page.wait_for_timeout(400)
        page.locator(".panel-track .bctl button", has_text="预演").click()
        page.wait_for_timeout(600)
        shots_btn = page.locator(".panel-track .strip-hd button", has_text="镜头序列")
        if shots_btn.count():
            shots_btn.first.click()
            page.wait_for_timeout(300)
        page.wait_for_selector(".panel-track .seq-bar.warn", timeout=10000)
        save_shot(page, "03-track.png")

        # 04 生成：三项 radio 本来就展开
        node(3)
        page.wait_for_selector(".panel-generate .g-mode", timeout=15000)
        page.wait_for_timeout(500)
        save_shot(page, "04-generate.png")

        # 05 收片：中等、两行、悬停进度条
        node(4)
        page.wait_for_selector(".panel-review .rv-card", timeout=15000)
        mid = page.locator(".panel-review button", has_text="中")
        if mid.count():
            mid.first.click()
            page.wait_for_timeout(400)
        page.wait_for_function(
            "() => document.querySelectorAll('.panel-review .rv-card').length >= 6")
        page.locator(".panel-review .rv-card .stage").nth(0).hover()
        page.wait_for_selector(".panel-review .stage.bar-on .bar", timeout=5000)
        page.wait_for_timeout(200)
        save_shot(page, "05-review.png")

        # 08 换图面板（还在收片）
        page.locator(".panel-review .swap-chip").first.click()
        page.wait_for_selector("aside.rv-picker .pk-cards", timeout=8000)
        page.wait_for_timeout(300)
        save_shot(page, "08-cards.png")
        page.evaluate("() => { const x = document.querySelector('aside.rv-picker .x'); if (x) x.click(); }")
        page.wait_for_timeout(200)

        # 09 设置 → 多镜头扩图
        dismiss()
        page.evaluate("() => { ui.drawer = 'settings'; }")
        page.wait_for_selector("aside.drawer.wide .bd", timeout=8000)
        page.wait_for_timeout(400)
        ok = page.evaluate(
            """() => {
              const bd = document.querySelector('aside.drawer.wide .bd');
              if (!bd) return false;
              const h = [...bd.querySelectorAll('h3')].find(
                x => (x.textContent || '').indexOf('多镜头扩图') >= 0);
              if (!h) return false;
              h.scrollIntoView({block: 'start'});
              return true;
            }""")
        if not ok:
            raise SystemExit("设置抽屉里找不到「多镜头扩图」")
        page.wait_for_timeout(300)
        save_shot(page, "09-settings.png")
        page.evaluate("() => { ui.drawer = null; }")
        page.wait_for_timeout(200)

        # 06 向导岔路口
        page.evaluate("() => { ui.showSetup = true; }")
        page.wait_for_selector("label.setup-choice", timeout=8000)
        page.wait_for_function(
            "() => document.querySelectorAll('label.setup-choice').length >= 2")
        page.wait_for_timeout(300)
        save_shot(page, "06-wizard-start.png")

        # 07 向导第 3 步：mock health，点检测
        page.route(
            "**/api/accounts/*/health",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                body=json.dumps(
                    {"ok": True, "state": "ready", "port": 9300, "pages": []},
                    ensure_ascii=False,
                ).encode("utf-8"),
            ),
        )
        # 第 0 → 1
        page.locator(".setup-ft .btn.primary").click()
        page.wait_for_selector(".setup-check", timeout=20000)
        page.wait_for_function(
            """() => {
              const b = document.querySelector('.setup-ft .btn.primary');
              return b && !b.disabled;
            }""",
            timeout=20000,
        )
        # 1 → 2
        page.locator(".setup-ft .btn.primary").click()
        page.wait_for_timeout(400)
        # 2 → 3
        page.locator(".setup-ft .btn.primary").click()
        page.wait_for_selector(".setup-acc", timeout=10000)
        det = page.locator(".setup-acc button", has_text="我登好了，检测一下")
        if det.count():
            det.first.click()
            page.wait_for_timeout(400)
        page.wait_for_selector(".setup-acc .tag.ok", timeout=8000)
        page.wait_for_timeout(200)
        save_shot(page, "07-wizard-accounts.png")

        browser.close()
    print("ok", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
