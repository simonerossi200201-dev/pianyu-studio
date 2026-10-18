# -*- coding: utf-8 -*-
"""探针 B：Grok 双图首尾帧，4 种写法各 3 次。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, accounts, paths, tasks as taskmod
from orante.engines.base import make_engine
from orante.engines.grok import IMAGINE_URL, dur_label
from orante.models import Manifest
from orante.services import tails, ai, verify as verifymod
from orante.services.procutil import run_quiet
from orante.services.checks import find_ffmpeg

paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402
from grok_batch import pass_tos_gate, set_params  # noqa: E402

OUT = paths.DATA / "capture" / "probe36"
OUT.mkdir(parents=True, exist_ok=True)
LINE = "Repentance means a change of mind that leads to a change of direction."


class Job:
    def log(self, m):
        print(m, flush=True)

    def check(self):
        pass

    id = "probe36b"
    task_id = "T0013"


def dist(p, q):
    ha = verifymod.ahash_image(p)
    hb = verifymod.ahash_image(q)
    if ha is None or hb is None:
        return None
    return bin(ha ^ hb).count("1")


def score(video: Path, start: Path, end: Path, work: Path) -> dict:
    ff = find_ffmpeg()
    first = work / "out_first.jpg"
    last = work / "out_last.jpg"
    run_quiet([ff, "-hide_banner", "-loglevel", "error", "-ss", "0", "-i", str(video),
               "-frames:v", "1", "-y", str(first)], timeout=30)
    tails.extract_from_end(video, 3, last)  # 倒数第 3 帧，躲开黑尾
    info = tails.video_info(video)
    has_audio = None
    try:
        fp = ff.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
        r = run_quiet([fp, "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
                      timeout=20)
        has_audio = bool((r.stdout or b"").strip())
    except Exception:
        pass
    d_fs, d_fe = dist(first, start), dist(first, end)
    d_ls, d_le = dist(last, start), dist(last, end)
    return {
        "duration": info.get("duration"),
        "has_audio": has_audio,
        "dist_first_start": d_fs, "dist_first_end": d_fe,
        "dist_last_start": d_ls, "dist_last_end": d_le,
        "start_match": d_fs is not None and d_fe is not None and d_fs < d_fe,
        "end_match": d_le is not None and d_ls is not None and d_le < d_ls,
    }


def run_once(eng, start, end, prompt, method, trial) -> dict:
    work = OUT / f"b-m{method}-t{trial}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    a = work / "01_首帧_start.jpg"
    b = work / "02_尾帧_end.jpg"
    shutil.copy2(start, a)
    shutil.copy2(end, b)
    rec = {"method": method, "trial": trial, "ok": False, "error": ""}
    print(f"\n===== 写法 {method} 第 {trial} 次 =====", flush=True)
    page = None
    try:
        page = eng._ctx.new_page()
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        pass_tos_gate(page)
        try:
            eng._dismiss_consent(page)
        except Exception:
            pass
        # 2026-09 条款更新页：按钮是「知道了」，不是第一个 button
        for lab in ("知道了", "Got it", "I understand", "Continue", "Accept"):
            try:
                loc = page.get_by_role("button", name=lab, exact=False).first
                if loc.count():
                    loc.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    print(f"  点了条款门：{lab}", flush=True)
                    break
            except Exception:
                continue
        if "tos-gate" in (page.url or ""):
            page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
            pass_tos_gate(page)
        page.wait_for_selector("div.tiptap", state="visible", timeout=60000)
        set_params(page, "6s", log=print)
        grok_pipe.attach_by_drop(page, str(a))
        page.wait_for_selector("img[src^='blob:']", state="attached", timeout=45000)
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0, tag=f"b{method}{trial}a")
        grok_pipe.attach_by_drop(page, str(b))
        t0 = time.time()
        n = 0
        while time.time() - t0 < 50:
            n = page.evaluate("() => document.querySelectorAll(\"img[src^='blob:']\").length") or 0
            if n >= 2:
                break
            time.sleep(0.6)
        rec["n_thumbs"] = n
        print(f"  缩略图 {n} 张", flush=True)
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0, tag=f"b{method}{trial}b")
        set_params(page, "6s", log=print)
        grok_pipe.type_prompt(page, prompt)
        grok_pipe.click_submit(page)
        post, conv = grok_pipe.capture_anchor(page)
        rec["post"] = post
        if not post:
            rec["error"] = "没抓到 postId"
            return rec
        print(f"  post {post[:12]}…", flush=True)
        url = ""
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                found = page.evaluate(grok_pipe.FIND_VIDEO_JS) or {}
            except Exception:
                found = {}
            strong, weak = grok_pipe._normalize_find(found)
            for u in list(strong) + list(weak):
                if u and grok_pipe.url_matches_post(u, post):
                    url = u
                    break
            if url:
                rec["probe"] = grok_pipe.S_READY
                break
            st, info, *_ = grok_pipe.probe(page, post=post)
            rec["probe"] = st
            if st == grok_pipe.S_FAILED:
                rec["error"] = str(info)[:200]
                return rec
            time.sleep(5)
        if not url:
            rec["error"] = rec.get("error") or f"超时 probe={rec.get('probe')}"
            return rec
        dest = work / "out.mp4"
        row = {"postId": post, "会话Id": conv or "", "视频URL": url,
               "完整提示词": prompt, "时长": "6s", "编号": f"b{method}", "第几段": trial}
        grok_pipe.download_one(eng._ctx, row, str(dest))
        if dest.is_file() and dest.stat().st_size > 50000:
            rec["file"] = dest.name
            rec["bytes"] = dest.stat().st_size
            rec.update(score(dest, a, b, work))
            rec["ok"] = bool(rec.get("start_match") and rec.get("end_match"))
            print(f"  时长 {rec.get('duration')}  语音 {rec.get('has_audio')}  "
                  f"首帧对={rec.get('start_match')} 尾帧对={rec.get('end_match')}", flush=True)
        else:
            rec["error"] = "下载文件太小或不存在"
        return rec
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
        print("  ERR", rec["error"], flush=True)
        return rec
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def main():
    db.init()
    t = taskmod.get("T0013")
    d = Path(t["dir"])
    m = Manifest.load(d)
    start = tails.tail_path(d, 1)
    if not start.is_file():
        tails.extract(d, m.seg(1), inspect=False)
    im = m.image((m.seg(3) or {}).get("image") or "p17")
    end = d / im["file"]
    print("start", start, "end", end)
    job = Job()
    gem = ai.client(job)
    raw = gem.ask_images(
        '两张图。只输出 JSON：{"start":"第一张一句话","end":"第二张一句话"}',
        [start, end], json_mode=True)
    dd = tails.parse_json_obj(raw) or {}
    desc_s = str(dd.get("start") or "人物站在讲台上说话")
    desc_e = str(dd.get("end") or "人物换了一个姿态")
    print("desc start:", desc_s)
    print("desc end:", desc_e)
    prompts = {
        1: f"第一张是起始画面，第二张是结束画面。人物把这段话说完。只说下面这句英文，一字不差：\n{LINE}",
        2: (f"请阅读两张图的文件名：01_首帧_start.jpg 是起始画面，"
            f"02_尾帧_end.jpg 是结束画面。人物把这段话说完。只说下面这句英文，一字不差：\n{LINE}"),
        3: (f"请阅读文件名：01_首帧_start.jpg 是起始画面（{desc_s}），"
            f"02_尾帧_end.jpg 是结束画面（{desc_e}）。"
            f"人物把这段话说完。只说下面这句英文，一字不差：\n{LINE}"),
    }
    prompts[4] = prompts[3] + "画面从第一张的状态自然过渡到第二张的状态，中间不切镜。"

    accs = [a for a in accounts.list_all() if a.get("engine") == "grok" and a.get("enabled")]
    acc = accs[-1]
    print("Grok 账号", acc["name"], acc["port"])
    eng = make_engine(acc, print)
    results = []
    # 支持 resume：已有结果跳过
    prev_p = OUT / "b-results.json"
    done = set()
    if prev_p.is_file():
        try:
            results = json.loads(prev_p.read_text(encoding="utf-8"))
            for r in results:
                if r.get("file"):
                    done.add((r["method"], r["trial"]))
        except Exception:
            results = []
    try:
        eng.connect()
        h = eng.health()
        print("health", h)
        for method in (1, 2, 3, 4):
            for trial in (1, 2, 3):
                if (method, trial) in done:
                    print(f"跳过 写法{method} 第{trial}（已有成片）")
                    continue
                try:
                    if eng._ctx is None:
                        raise RuntimeError("no ctx")
                    _ = list(eng._ctx.pages)
                except Exception:
                    print("  Chrome 断了，重连…", flush=True)
                    try:
                        eng.close()
                    except Exception:
                        pass
                    eng = make_engine(acc, print)
                    eng.connect()
                rec = run_once(eng, start, end, prompts[method], method, trial)
                rec["desc_start"] = desc_s
                rec["desc_end"] = desc_e
                results = [r for r in results
                           if not (r.get("method") == method and r.get("trial") == trial)]
                results.append(rec)
                prev_p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        try:
            eng.close()
        except Exception:
            pass
    print("\n===== 汇总 =====")
    for method in (1, 2, 3, 4):
        xs = [r for r in results if r.get("method") == method]
        ok = sum(1 for r in xs if r.get("ok"))
        print(f"写法 {method}: {ok}/{len(xs)}")
    print("wrote", prev_p)


if __name__ == "__main__":
    main()
