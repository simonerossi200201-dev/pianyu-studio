# -*- coding: utf-8 -*-
"""规格 63 A/B：同一段、同一图，旧情绪（激昂）vs 新情绪（温暖）各出一条。

  python tools/probe63.py              写提示词 + 生成 + 拼静帧
  python tools/probe63.py --prompts    只写提示词，不烧额度
  python tools/probe63.py --compose    已有视频时只抽帧拼图
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import accounts, db, paths, tasks as taskmod  # noqa: E402
from orante.engines.base import make_engine, BlockedImage  # noqa: E402
from orante.errors import CreditHalted, NeedRedownload, OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import generate as genmod  # noqa: E402
from orante.services import prompts, tails  # noqa: E402
from orante.services.procutil import run_quiet  # noqa: E402
from orante.services.verify import find_ffmpeg  # noqa: E402

OUT = paths.DATA / "capture" / "probe63"
IMG_DIR = ROOT / "specs" / "reports" / "img"
TID = "T0020"
# 开头 / 中间 / 结尾三根锚点（睁眼讲话，不是接片）
PICKS = (1, 17, 35)
PRESETS = ("激昂", "温暖")
TAG = {"激昂": "fiery", "温暖": "warm"}
POLL_EVERY = 6
GEN_TIMEOUT = 15 * 60


class Job:
    id = "probe63"
    task_id = TID

    def log(self, m):
        print(m, flush=True)

    def check(self):
        pass

    def sleep(self, n):
        time.sleep(n)


def _out_name(s: int, preset: str) -> str:
    return f"s{s:03d}-{TAG[preset]}.mp4"


def _prompt_name(s: int, preset: str) -> str:
    return f"s{s:03d}-{TAG[preset]}.txt"


def build_prompt(m: Manifest, seg: dict, name: str) -> str:
    """临时切预设拼词，立刻把 tail 写回，避免弄脏用户设置。"""
    saved = prompts.get_block("tail")
    try:
        t = deepcopy(saved)
        prompts._apply_slot_to_live(t, name)
        prompts.set_block("tail", t)
        return prompts.build_final_prompt(m, seg)
    finally:
        prompts.set_block("tail", saved)


def dump_prompts(m: Manifest, segs: list[dict]) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"tid": TID, "picks": [], "forbidden_in_warm": {}, "required_in_warm": {}}
    for sg in segs:
        item = {"s": sg["s"], "duration": sg.get("duration"), "image": sg.get("image"),
                "text": sg.get("text") or ""}
        for name in PRESETS:
            text = build_prompt(m, sg, name)
            (OUT / _prompt_name(sg["s"], name)).write_text(text, encoding="utf-8")
            item[name] = {"chars": len(text), "file": _prompt_name(sg["s"], name)}
            if name == "温暖":
                meta["forbidden_in_warm"][sg["s"]] = [
                    w for w in ("下颌用力", "目光炯炯", "直视镜头", "扬声激昂",
                                "powerful voice", "emphatic gestures")
                    if w in text]
                meta["required_in_warm"][sg["s"]] = [
                    w for w in ("掌心", "前倾", "从不看镜头", "放轻")
                    if w in text]
        meta["picks"].append(item)
        print(f"  s{sg['s']:03d} 激昂 {item['激昂']['chars']} 字 / 温暖 {item['温暖']['chars']} 字",
              flush=True)
    (OUT / "prompts.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def extract_frame(video: Path, dest: Path, t_frac: float) -> bool:
    ff = find_ffmpeg()
    if not ff or not video.is_file():
        return False
    info = tails.video_info(video)
    dur = float(info.get("duration") or 0) or 8.0
    ss = max(0.0, min(dur - 0.05, dur * t_frac))
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-ss", f"{ss:.3f}", "-i", str(video),
           "-frames:v", "1", "-q:v", "2", "-an", "-y", str(dest)]
    r = run_quiet(cmd, timeout=60)
    return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 100


def _font(size: int):
    from PIL import ImageFont
    for p in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def compose_one(s: int) -> Path | None:
    from PIL import Image, ImageDraw
    fiery = OUT / _out_name(s, "激昂")
    warm = OUT / _out_name(s, "温暖")
    if not fiery.is_file() or not warm.is_file():
        print(f"  skip s{s:03d} 缺视频", flush=True)
        return None
    fracs = (0.0, 0.5, 0.95)
    labels_row = ("t=0", "t=0.5", "t=0.95")
    cells = []
    work = OUT / "frames"
    work.mkdir(parents=True, exist_ok=True)
    for side, vid in (("fiery", fiery), ("warm", warm)):
        row = []
        for frac in fracs:
            dest = work / f"s{s:03d}-{side}-{frac:.2f}.jpg"
            if not extract_frame(vid, dest, frac):
                print(f"  抽帧失败 {dest.name}", flush=True)
                return None
            row.append(Image.open(dest).convert("RGB"))
        cells.append(row)
    # 统一格子大小
    cw = min(im.width for row in cells for im in row)
    ch = min(im.height for row in cells for im in row)
    cells = [[im.resize((cw, ch), Image.Resampling.LANCZOS) for im in row] for row in cells]
    pad, header, gutter = 8, 36, 6
    W = pad * 2 + cw * 2 + gutter
    H = header + pad + (ch + 22) * 3
    canvas = Image.new("RGB", (W, H), (18, 18, 22))
    draw = ImageDraw.Draw(canvas)
    font = _font(18)
    font_s = _font(14)
    draw.text((pad, 8), f"s{s:03d}  左=激昂（改前）  右=温暖（新）", fill=(240, 240, 240), font=font)
    for r, frac_lab in enumerate(labels_row):
        y = header + r * (ch + 22)
        draw.text((pad, y), frac_lab, fill=(180, 180, 190), font=font_s)
        y2 = y + 18
        canvas.paste(cells[0][r], (pad, y2))
        canvas.paste(cells[1][r], (pad + cw + gutter, y2))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    dest = IMG_DIR / f"63-ab-s{s:03d}.png"
    canvas.save(dest, "PNG")
    print(f"  拼图 {dest}", flush=True)
    return dest


def generate_all(m: Manifest, d: Path, segs: list[dict], acc: dict) -> dict:
    job = Job()
    jobs = []
    for sg in segs:
        im = m.image(sg.get("image") or "") or {}
        img = genmod._resolve_image(d, im)
        if not img.is_file():
            raise SystemExit(f"找不到图 {sg.get('image')} → {img}")
        for name in PRESETS:
            dest = OUT / _out_name(sg["s"], name)
            prompt = (OUT / _prompt_name(sg["s"], name)).read_text(encoding="utf-8")
            jobs.append({
                "s": sg["s"], "preset": name, "seg": sg, "img": img,
                "prompt": prompt, "duration": int(sg.get("duration") or 8),
                "dest": dest,
            })
    already = [j for j in jobs if j["dest"].is_file() and j["dest"].stat().st_size > 50_000]
    todo = [j for j in jobs if j not in already]
    for j in already:
        job.log(f"  · 已有 {j['dest'].name}，跳过")
    if not todo:
        return {"submitted": 0, "done": len(already), "fail": 0}
    eng = make_engine(acc, job.log)
    eng.connect()
    inflight = {}
    started = {}
    n_done = n_fail = 0
    queue = list(todo)
    try:
        while queue or inflight:
            job.check()
            while queue and eng.capacity() > 0:
                j = queue.pop(0)
                fake = {"s": j["s"], "image": j["seg"].get("image")}
                try:
                    gid = eng.submit(fake, j["img"], j["prompt"], j["duration"])
                except CreditHalted as e:
                    job.log(f"  额度停了：{e}")
                    queue.insert(0, j)
                    time.sleep(30)
                    continue
                except BlockedImage as e:
                    job.log(f"  ✗ s{j['s']:03d} {j['preset']} 图被拒：{e}")
                    n_fail += 1
                    continue
                except OranteError as e:
                    job.log(f"  ✗ s{j['s']:03d} {j['preset']} 提交失败：{e.message}")
                    n_fail += 1
                    continue
                inflight[gid] = j
                started[gid] = time.time()
                job.log(f"  → s{j['s']:03d} {j['preset']} {j['duration']}s")
                time.sleep(1.2)
            if not inflight:
                continue
            time.sleep(POLL_EVERY)
            try:
                tick = getattr(eng, "tick_credits", None)
                if callable(tick):
                    tick()
            except CreditHalted:
                job.log("  额度暂停，继续等")
            res = eng.poll(list(inflight))
            for gid, r in res.items():
                j = inflight.get(gid)
                if not j:
                    continue
                if time.time() - started.get(gid, time.time()) > GEN_TIMEOUT:
                    job.log(f"  ✗ s{j['s']:03d} {j['preset']} 超时")
                    n_fail += 1
                    inflight.pop(gid, None)
                    continue
                if r["state"] == "pending":
                    continue
                if r["state"] != "done":
                    job.log(f"  ✗ s{j['s']:03d} {j['preset']} {r.get('error') or r['state']}")
                    n_fail += 1
                    inflight.pop(gid, None)
                    continue
                try:
                    eng.download(gid, j["dest"])
                except NeedRedownload:
                    job.log(f"  ↻ s{j['s']:03d} {j['preset']} 地址作废，继续等")
                    continue
                except OranteError as e:
                    job.log(f"  ✗ s{j['s']:03d} {j['preset']} 下载失败：{e.message}")
                    n_fail += 1
                    inflight.pop(gid, None)
                    continue
                job.log(f"  ✓ {j['dest'].name}  {j['dest'].stat().st_size // 1024} KB")
                n_done += 1
                inflight.pop(gid, None)
    finally:
        try:
            eng.close()
        except Exception:
            pass
    return {"submitted": len(todo), "done": n_done + len(already), "fail": n_fail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", action="store_true", help="只写提示词")
    ap.add_argument("--compose", action="store_true", help="只拼静帧")
    args = ap.parse_args()

    db.init()
    OUT.mkdir(parents=True, exist_ok=True)
    t = taskmod.get(TID)
    if not t:
        raise SystemExit(f"没有 {TID}")
    d = Path(t["dir"])
    m = Manifest.load(d)
    segs = []
    for n in PICKS:
        sg = next((x for x in m.segments if int(x.get("s") or 0) == n), None)
        if not sg:
            raise SystemExit(f"{TID} 没有 s{n:03d}")
        segs.append(sg)

    if not args.compose:
        print("== 写提示词 ==", flush=True)
        dump_prompts(m, segs)

    if args.prompts:
        print(f"提示词在 {OUT}")
        return 0

    if not args.compose:
        print("== 生成 A/B ==", flush=True)
        acc = accounts.get(t["account_id"])
        r = generate_all(m, d, segs, acc)
        print(f"生成结果 {r}", flush=True)
        (OUT / "gen.json").write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        if r["fail"] and r["done"] < 6:
            print("有失败，先把已有的拼上", flush=True)

    print("== 拼静帧 ==", flush=True)
    done = []
    for sg in segs:
        p = compose_one(sg["s"])
        if p:
            done.append(str(p))
    (OUT / "compose.json").write_text(
        json.dumps({"images": done}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"静帧 {len(done)} 张 → {IMG_DIR}")
    return 0 if len(done) == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
