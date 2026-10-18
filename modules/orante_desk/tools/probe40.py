# -*- coding: utf-8 -*-
"""探针 40：Grok 双图反序（写法⑤/⑥）+ 首帧自检重试。

  python tools/probe40.py              # 全跑（⑤⑥ + 真接片 + 失配重生）
  python tools/probe40.py --unit       # 只跑自检重试单测（不烧额度）
  python tools/probe40.py --phase 5    # 只跑某一阶段（5 / 6 / prod / mismatch）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, accounts, paths, tasks as taskmod
from orante.engines import chrome as chrome_mod
from orante.engines.base import make_engine
from orante.engines.grok import IMAGINE_URL
from orante.models import Manifest
from orante.services import tails, pipeline, verify as verifymod
from orante.services.procutil import run_quiet
from orante.services.checks import find_ffmpeg

paths.add_legacy_to_sys_path()
import grok_pipe  # noqa: E402
from grok_batch import pass_tos_gate, set_params  # noqa: E402

OUT = paths.DATA / "capture" / "probe40"
OUT.mkdir(parents=True, exist_ok=True)
LINE = "Repentance means a change of mind that leads to a change of direction."
# 探针 B 写法④ 的成绩，并排用
REF_4 = {
    "method": 4, "dual": 1, "start": 1, "end": 3, "n": 3,
    "rows": [
        {"trial": 1, "dist_first_start": 47, "dist_first_end": 32,
         "dist_last_start": 63, "dist_last_end": 24,
         "start_match": False, "end_match": True},
        {"trial": 2, "dist_first_start": 41, "dist_first_end": 44,
         "dist_last_start": 69, "dist_last_end": 18,
         "start_match": True, "end_match": True},
        {"trial": 3, "dist_first_start": 51, "dist_first_end": 18,
         "dist_last_start": 51, "dist_last_end": 18,
         "start_match": False, "end_match": True},
    ],
}


class Job:
    def log(self, m):
        print(m, flush=True)

    def check(self):
        pass

    id = "probe40"
    task_id = "T0013"


def dist(p, q):
    ha = verifymod.ahash_image(p)
    hb = verifymod.ahash_image(q)
    if ha is None or hb is None:
        return None
    return bin(ha ^ hb).count("1")


def score(video: Path, start: Path, end: Path, work: Path) -> dict:
    """和探针 B 同一把尺子：首帧 -ss 0，尾帧倒数第 3。"""
    ff = find_ffmpeg()
    first = work / "out_first.jpg"
    last = work / "out_last.jpg"
    run_quiet([ff, "-hide_banner", "-loglevel", "error", "-ss", "0",
               "-i", str(video), "-frames:v", "1", "-y", str(first)],
              timeout=30)
    tails.extract_from_end(video, 3, last)
    info = tails.video_info(video)
    has_audio = None
    try:
        fp = ff.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
        r = run_quiet([fp, "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                       str(video)], timeout=20)
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


def _tos(page):
    pass_tos_gate(page)
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


def run_once(eng, start, end, prompt, method, trial, reverse: bool) -> dict:
    work = OUT / f"b-m{method}-t{trial}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    a = work / "01_首帧_start.jpg"
    b = work / "02_尾帧_end.jpg"
    shutil.copy2(start, a)
    shutil.copy2(end, b)
    rec = {"method": method, "trial": trial, "ok": False, "error": "",
           "reverse": reverse}
    print(f"\n===== 写法 {method} 第 {trial} 次  上传={'终→起' if reverse else '起→终'} =====",
          flush=True)
    page = None
    try:
        page = eng._ctx.new_page()
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        _tos(page)
        try:
            eng._dismiss_consent(page)
        except Exception:
            pass
        page.wait_for_selector("div.tiptap", state="visible", timeout=60000)
        set_params(page, "6s", log=print)
        first, second = (b, a) if reverse else (a, b)
        grok_pipe.attach_by_drop(page, str(first))
        page.wait_for_selector("img[src^='blob:']", state="attached", timeout=45000)
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0,
                             tag=f"b{method}{trial}a")
        grok_pipe.attach_by_drop(page, str(second), min_count=2)
        t0 = time.time()
        n = 0
        while time.time() - t0 < 50:
            n = page.evaluate(
                "() => document.querySelectorAll(\"img[src^='blob:']\").length") or 0
            if n >= 2:
                break
            time.sleep(0.6)
        rec["n_thumbs"] = n
        print(f"  缩略图 {n} 张", flush=True)
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0,
                             tag=f"b{method}{trial}b")
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
               "完整提示词": prompt, "时长": "6s",
               "编号": f"b{method}", "第几段": trial}
        grok_pipe.download_one(eng._ctx, row, str(dest))
        if dest.is_file() and dest.stat().st_size > 50000:
            rec["file"] = dest.name
            rec["bytes"] = dest.stat().st_size
            rec.update(score(dest, a, b, work))
            rec["ok"] = bool(rec.get("start_match") and rec.get("end_match"))
            print(f"  时长 {rec.get('duration')}  语音 {rec.get('has_audio')}  "
                  f"首帧对={rec.get('start_match')} 尾帧对={rec.get('end_match')}  "
                  f"首↔起 {rec.get('dist_first_start')} 首↔终 {rec.get('dist_first_end')}  "
                  f"尾↔终 {rec.get('dist_last_end')}", flush=True)
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


def rescore_existing(results: list[dict], start: Path, end: Path) -> list[dict]:
    """成片在、分数缺（当时没 PIL）→ 就地重打，不烧额度。"""
    out = []
    for rec in results:
        method, trial = rec.get("method"), rec.get("trial")
        work = OUT / f"b-m{method}-t{trial}"
        video = work / "out.mp4"
        a = work / "01_首帧_start.jpg"
        b = work / "02_尾帧_end.jpg"
        if rec.get("dist_first_start") is not None or not video.is_file():
            out.append(rec)
            continue
        if not a.is_file():
            a = start
        if not b.is_file():
            b = end
        try:
            rec.update(score(video, a, b, work))
            rec["ok"] = bool(rec.get("start_match") and rec.get("end_match"))
            rec["error"] = ""
            rec["file"] = "out.mp4"
            rec["bytes"] = video.stat().st_size
            print(f"  补打分 写法{method} 第{trial}  "
                  f"首↔起 {rec.get('dist_first_start')} 尾↔终 {rec.get('dist_last_end')}  "
                  f"双对={rec.get('ok')}", flush=True)
        except Exception as e:
            rec["error"] = f"rescore: {e}"[:200]
            print("  补打分失败", method, trial, rec["error"], flush=True)
        out.append(rec)
    return out


def _save(results, path: Path):
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _load(path: Path):
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def ensure_chrome(acc) -> None:
    """先把带调试口的 Chrome 拉起来。不用 BREAKAWAY_FROM_JOB——
    Grok Build 的 Job 不允许，CreateProcess 会 WinError 5。"""
    port = int(acc["port"])
    if chrome_mod.cdp_version(port):
        return
    prof = accounts._profile_dir(acc)
    prof.mkdir(parents=True, exist_ok=True)
    args = [chrome_mod.chrome_exe(), f"--remote-debugging-port={port}",
            f"--user-data-dir={prof}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=CalculateNativeWinOcclusion",
            IMAGINE_URL]
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kw["creationflags"] = 0x00000200  # NEW_PROCESS_GROUP，不要 BREAKAWAY
    subprocess.Popen(args, **kw)
    t0 = time.time()
    while time.time() - t0 < 45:
        if chrome_mod.cdp_version(port):
            print(f"  Chrome 已在端口 {port} 起来", flush=True)
            return
        time.sleep(0.4)
    raise SystemExit(f"Chrome 没有在端口 {port} 起来")


def _reconnect(eng, acc):
    try:
        if eng._ctx is None:
            raise RuntimeError("no ctx")
        _ = list(eng._ctx.pages)
        return eng
    except Exception:
        print("  Chrome 断了，重连…", flush=True)
        try:
            eng.close()
        except Exception:
            pass
        ensure_chrome(acc)
        eng = make_engine(acc, print)
        eng.connect()
        return eng


def materials():
    db.init()
    t = taskmod.get("T0013")
    d = Path(t["dir"])
    m = Manifest.load(d)
    start = tails.tail_path(d, 1)
    if not start.is_file():
        tails.extract(d, m.seg(1), inspect=False)
    im = m.image((m.seg(3) or {}).get("image") or "p17")
    end = d / im["file"]
    desc_s, desc_e = "人物站在讲台上说话", "人物换了一个姿态"
    prev = paths.DATA / "capture" / "probe36" / "b-results.json"
    if prev.is_file():
        try:
            rows = json.loads(prev.read_text(encoding="utf-8"))
            for r in reversed(rows):
                if r.get("method") == 4 and r.get("desc_start"):
                    desc_s = r["desc_start"]
                    desc_e = r["desc_end"]
                    break
        except Exception:
            pass
    return d, m, start, end, desc_s, desc_e


def prompts_of(desc_s, desc_e):
    p4 = (f"请阅读文件名：01_首帧_start.jpg 是起始画面（{desc_s}），"
          f"02_尾帧_end.jpg 是结束画面（{desc_e}）。"
          f"人物把这段话说完。只说下面这句英文，一字不差：\n{LINE}"
          "画面从第一张的状态自然过渡到第二张的状态，中间不切镜。")
    p6 = (f"请阅读文件名：01_首帧_start.jpg 是起始画面（{desc_s}），"
          f"02_尾帧_end.jpg 是结束画面（{desc_e}）。"
          "后上传的那张是起始画面，先上传的那张是结束画面。"
          f"人物把这段话说完。只说下面这句英文，一字不差：\n{LINE}"
          "画面从后上传那张的状态自然过渡到先上传那张的状态，中间不切镜。")
    return {4: p4, 5: p4, 6: p6}


def pick_winner(results: list[dict]) -> dict:
    """双对最高；都不比 ④ 好就维持 ④。并列看首对、再尾对，再偏向少改提示词的。"""
    def tally(method):
        xs = [r for r in results if r.get("method") == method and r.get("file")]
        dual = sum(1 for r in xs if r.get("ok"))
        st = sum(1 for r in xs if r.get("start_match"))
        en = sum(1 for r in xs if r.get("end_match"))
        return {"method": method, "dual": dual, "start": st, "end": en,
                "n": max(len(xs), 1)}

    t4 = dict(REF_4)
    t5, t6 = tally(5), tally(6)
    cands = [t4, t5, t6]
    best = t4
    for t in (t5, t6):
        if t["dual"] > best["dual"]:
            best = t
        elif t["dual"] == best["dual"] and t["dual"] > t4["dual"]:
            if t["start"] > best["start"] or (
                    t["start"] == best["start"] and t["end"] > best["end"]):
                best = t
    # 和 ④ 打平或更差 → 维持 ④
    if best["dual"] <= t4["dual"] and best is not t4:
        # 只有严格更好才换
        best = t4
    order = "end_then_start" if best["method"] in (5, 6) else "start_then_end"
    flip = best["method"] == 6
    return {"winner": best["method"], "order": order, "flip_prompt": flip,
            "t4": t4, "t5": t5, "t6": t6, "why": _why(best, t4, t5, t6)}


def _why(best, t4, t5, t6):
    bits = [f"④ 双对 {t4['dual']}/3",
            f"⑤ 双对 {t5['dual']}/{t5['n']}",
            f"⑥ 双对 {t6['dual']}/{t6['n']}"]
    if best["method"] == 4:
        bits.append("⑤⑥ 都不比 ④ 好，维持写法④ 起→终")
    elif best["method"] == 5:
        bits.append("⑤ 双对最高，上传改成终→起，提示词不改")
    else:
        bits.append("⑥ 双对最高，上传终→起，提示词改成后传起/先传终")
    return "；".join(bits)


def apply_winner(decision: dict) -> None:
    """把选定的顺序写进 pipeline 常量。"""
    src = ROOT / "orante" / "services" / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    order = decision["order"]
    flip = "True" if decision["flip_prompt"] else "False"
    text2 = text
    text2 = _repl_assign(text2, "GROK_BRIDGE_UPLOAD", f'"{order}"')
    text2 = _repl_assign(text2, "GROK_BRIDGE_FLIP_PROMPT", flip)
    if text2 != text:
        src.write_text(text2, encoding="utf-8")
        print(f"  已写入 GROK_BRIDGE_UPLOAD={order}  "
              f"GROK_BRIDGE_FLIP_PROMPT={flip}", flush=True)
    # 运行时也改，本进程后头的真接片能用到
    pipeline.GROK_BRIDGE_UPLOAD = order
    pipeline.GROK_BRIDGE_FLIP_PROMPT = bool(decision["flip_prompt"])


def _repl_assign(text: str, name: str, value: str) -> str:
    lines = text.splitlines(True)
    out = []
    done = False
    for ln in lines:
        if not done and ln.startswith(name + " ="):
            out.append(f"{name} = {value}\n")
            done = True
        else:
            out.append(ln)
    return "".join(out)


def grok_acc():
    accs = [a for a in accounts.list_all()
            if a.get("engine") == "grok" and a.get("enabled")]
    if not accs:
        raise SystemExit("没有启用的 Grok 账号")
    return accs[-1]


# ---------- 自检重试单测（用探针 B 写法④第 3 次那条，首帧长得像结束图） ----------
def unit_test_retry() -> dict:
    print("\n===== 自检重试单测（④3 成片，不烧额度） =====", flush=True)
    src_vid = paths.DATA / "capture" / "probe36" / "b-m4-t3" / "out.mp4"
    src_start = paths.DATA / "capture" / "probe36" / "b-m4-t3" / "01_首帧_start.jpg"
    src_end = paths.DATA / "capture" / "probe36" / "b-m4-t3" / "02_尾帧_end.jpg"
    if not src_vid.is_file():
        raise SystemExit(f"找不到 {src_vid}")
    d = Path(tempfile.mkdtemp(prefix="probe40-retry-"))
    (d / "clips" / "_tails").mkdir(parents=True)
    (d / "images").mkdir(parents=True)
    shutil.copy2(src_start, d / "clips" / "_tails" / "s001.jpg")
    shutil.copy2(src_end, d / "images" / "p17.jpg")
    clip = d / "clips" / "T0040-s002-p17.mp4"
    shutil.copy2(src_vid, clip)

    m = Manifest.new("T0040", "探针40", "grok", "A006", "long", "hello " * 20)
    m.mode = "anchor_bridge"
    m.images = [{
        "p": "p17", "file": "images/p17.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": "", "voice": "", "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "download", "src": "", "pose": {"text": "侧身"},
    }]
    m.segments = [
        {"s": 1, "text": "a", "duration": 6, "image": "p17", "role": "anchor",
         "shot": "", "prompt_final": "", "status": "collected",
         "gen_id": None, "takes": [], "error": None, "bridge_ready": True},
        {"s": 2, "text": "b", "duration": 6, "image": "p17", "role": "bridge",
         "shot": "", "prompt_final": "同提示词不要改", "status": "collected",
         "gen_id": "g1", "takes": [{"file": "clips/T0040-s002-p17.mp4"}],
         "error": None,
         "bridge": {
             "from_seg": 1, "to_image": "p17",
             "tail_frame": "clips/_tails/s001.jpg",
             "gap_score": 1, "gap_why": "小动作",
         }},
        {"s": 3, "text": "c", "duration": 6, "image": "p17", "role": "anchor",
         "shot": "", "prompt_final": "", "status": "planned",
         "gen_id": None, "takes": [], "error": None},
    ]
    logs = []
    r1 = pipeline.cascade_after_anchor(d, m, m.seg(2), log=logs.append)
    assert r1.get("action") == "reset", r1
    assert m.seg(2)["status"] == "planned", m.seg(2)
    assert m.seg(2)["prompt_final"] == "同提示词不要改"
    assert int(m.seg(2)["bridge"].get("mismatch_retries") or 0) == 1
    assert any("重生一次" in x for x in logs), logs
    print(f"  第一次：action=reset 距离 {r1.get('first_dist')}  OK", flush=True)

    # 模拟重生后又收了一条同样对不上的片
    shutil.copy2(src_vid, clip)
    m.seg(2)["status"] = "collected"
    m.seg(2)["takes"] = [{"file": "clips/T0040-s002-p17.mp4"}]
    logs2 = []
    r2 = pipeline.cascade_after_anchor(d, m, m.seg(2), log=logs2.append)
    assert r2.get("action") == "keep", r2
    assert m.seg(2)["status"] == "collected"
    br = m.seg(2)["bridge"]
    assert br.get("mismatch") is True
    assert br.get("mismatch_why") == "接片首帧和上一段对不上"
    assert br.get("gap_score") == pipeline.GAP_ENQUEUE
    assert "对不上" in (br.get("gap_why") or "")
    issues = (m.seg(2).get("check") or {}).get("issues") or []
    assert any(i.get("code") == "bridge_mismatch" for i in issues), issues
    assert any("标黄" in x for x in logs2), logs2
    print(f"  第二次：action=keep 标黄 距离 {r2.get('first_dist')}  OK", flush=True)

    # 链继续：s002 仍 collected，s003 是主片，可以直接进队
    pipeline.stamp_waits(m)
    assert pipeline.can_enqueue(m, m.seg(3)), pipeline.wait_reason(m, m.seg(3))
    print("  链继续：s003 可进队  OK", flush=True)

    # Flow 接片不做自检
    m.engine = "flow"
    m.seg(2)["status"] = "collected"
    m.seg(2)["bridge"]["mismatch_retries"] = 0
    logs3 = []
    r3 = pipeline.cascade_after_anchor(d, m, m.seg(2), log=logs3.append)
    assert r3.get("action") == "none", r3
    assert not any("重生" in x for x in logs3)
    print("  Flow 接片不自检  OK", flush=True)

    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True, "first_dist": r1.get("first_dist"),
            "second_dist": r2.get("first_dist"),
            "logs": logs + logs2}


def prod_prompt(desc_s, desc_e, flip: bool) -> str:
    if flip:
        head = pipeline.BRIDGE_PROMPT_FLIPPED.format(start=desc_s, end=desc_e)
    else:
        head = pipeline.BRIDGE_PROMPT_DEFAULT.format(start=desc_s, end=desc_e)
    return (head.rstrip() + "\n人物把这段话说完。只说下面这句英文，一字不差：\n"
            + LINE)


def mismatch_prompt(desc_e) -> str:
    """故意把起始画面写成完全不对的东西，逼首帧对不上。"""
    wrong = "一片空白的白色墙壁，房间里没有任何人"
    head = pipeline.BRIDGE_PROMPT_DEFAULT.format(start=wrong, end=desc_e)
    return (head.rstrip() + "\n人物把这段话说完。只说下面这句英文，一字不差：\n"
            + LINE)


def run_mismatch_live(eng, start, end, desc_e) -> dict:
    """真生成一条对不上的接片，走 pipeline 自检：第一次重生，第二次标黄。"""
    print("\n===== 故意写错起始描述，触发重生 + 黄区 =====", flush=True)
    d = Path(tempfile.mkdtemp(prefix="probe40-mis-"))
    (d / "clips" / "_tails").mkdir(parents=True)
    (d / "images").mkdir(parents=True)
    shutil.copy2(start, d / "clips" / "_tails" / "s001.jpg")
    shutil.copy2(end, d / "images" / "p17.jpg")
    m = Manifest.new("T0040m", "探针40失配", "grok", "A006", "long", "hello " * 20)
    m.mode = "anchor_bridge"
    m.images = [{
        "p": "p17", "file": "images/p17.jpg", "sha1": "a" * 40, "role": "主体",
        "status": "ok", "desc": "", "voice": "", "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "download", "src": "", "pose": {"text": "侧身"},
    }]
    prompt = mismatch_prompt(desc_e)
    sg = {
        "s": 2, "text": LINE, "duration": 6, "image": "p17", "role": "bridge",
        "shot": "", "prompt_final": prompt, "status": "collected",
        "gen_id": None, "takes": [], "error": None,
        "bridge": {
            "from_seg": 1, "to_image": "p17",
            "tail_frame": "clips/_tails/s001.jpg",
            "gap_score": 1, "gap_why": "小动作",
        },
    }
    m.segments = [
        {"s": 1, "text": "a", "duration": 6, "image": "p17", "role": "anchor",
         "shot": "", "prompt_final": "", "status": "collected",
         "gen_id": None, "takes": [], "error": None, "bridge_ready": True},
        sg,
        {"s": 3, "text": "c", "duration": 6, "image": "p17", "role": "anchor",
         "shot": "", "prompt_final": "", "status": "planned",
         "gen_id": None, "takes": [], "error": None},
    ]
    out = {"rounds": []}
    reverse = pipeline.GROK_BRIDGE_UPLOAD == "end_then_start"
    for round_i in (1, 2):
        rec = run_once(eng, start, end, prompt, f"mis{round_i}", 1,
                       reverse=reverse)
        rec["round"] = round_i
        if not rec.get("file"):
            out["rounds"].append(rec)
            out["error"] = rec.get("error") or "没出片"
            return out
        dest = OUT / f"b-mmis{round_i}-t1" / "out.mp4"
        clip = d / "clips" / f"round{round_i}.mp4"
        shutil.copy2(dest, clip)
        sg["status"] = "collected"
        sg["takes"] = [{"file": str(clip.relative_to(d)).replace("\\", "/")}]
        logs = []
        r = pipeline.cascade_after_anchor(d, m, sg, log=logs.append)
        rec["cascade"] = {"action": r.get("action"),
                          "first_dist": r.get("first_dist"),
                          "last_dist": r.get("last_dist")}
        rec["logs"] = logs
        rec["status"] = sg.get("status")
        rec["mismatch"] = (sg.get("bridge") or {}).get("mismatch")
        rec["gap_score"] = (sg.get("bridge") or {}).get("gap_score")
        rec["gap_why"] = (sg.get("bridge") or {}).get("gap_why")
        out["rounds"].append(rec)
        print(f"  第 {round_i} 轮 cascade action={r.get('action')} "
              f"首距 {r.get('first_dist')} 尾距 {r.get('last_dist')}", flush=True)
        if round_i == 1 and r.get("action") != "reset":
            # 没触发重生：要么碰巧对上了，记下，不再烧第二次
            out["note"] = "第一次没有触发重生（首帧距离可能 ≤30）"
            break
    pipeline.stamp_waits(m)
    out["s003_can_enqueue"] = pipeline.can_enqueue(m, m.seg(3))
    out["yellow"] = bool((sg.get("bridge") or {}).get("mismatch"))
    shutil.rmtree(d, ignore_errors=True)
    return out


def summarize(results):
    print("\n===== 汇总 =====")
    for method in (4, 5, 6):
        if method == 4:
            xs = REF_4["rows"]
            dual = REF_4["dual"]
            st, en = REF_4["start"], REF_4["end"]
            print(f"写法 {method}: 双对 {dual}/3  首对 {st}/3  尾对 {en}/3  （已有）")
            continue
        xs = [r for r in results if r.get("method") == method]
        dual = sum(1 for r in xs if r.get("ok"))
        st = sum(1 for r in xs if r.get("start_match"))
        en = sum(1 for r in xs if r.get("end_match"))
        print(f"写法 {method}: 双对 {dual}/{len(xs)}  首对 {st}/{len(xs)}  尾对 {en}/{len(xs)}")
        for r in xs:
            print(f"  {method}{r.get('trial')}  首↔起 {r.get('dist_first_start')}  "
                  f"首↔终 {r.get('dist_first_end')}  "
                  f"尾↔起 {r.get('dist_last_start')}  "
                  f"尾↔终 {r.get('dist_last_end')}  "
                  f"{'双对' if r.get('ok') else ''} {r.get('error') or ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", action="store_true", help="只跑自检重试单测")
    ap.add_argument("--phase", default="",
                    help="5 / 6 / prod / mismatch / unit")
    args = ap.parse_args()
    unit = unit_test_retry()
    unit_p = OUT / "unit-retry.json"
    _save(unit, unit_p)
    if args.unit or args.phase == "unit":
        print("wrote", unit_p)
        return

    d, m, start, end, desc_s, desc_e = materials()
    print("start", start, "end", end)
    print("desc start:", desc_s)
    print("desc end:", desc_e)
    ps = prompts_of(desc_s, desc_e)
    acc = grok_acc()
    print("Grok 账号", acc["name"], acc["port"])
    eng = make_engine(acc, print)
    results_p = OUT / "b-results.json"
    results = _load(results_p)
    results = rescore_existing(results, start, end)
    _save(results, results_p)
    done = {(r.get("method"), r.get("trial"))
            for r in results
            if r.get("file") and r.get("dist_first_start") is not None}
    decision_p = OUT / "decision.json"
    prod_p = OUT / "prod-results.json"
    mis_p = OUT / "mismatch.json"

    want_methods = []
    if args.phase in ("", "5"):
        want_methods.append(5)
    if args.phase in ("", "6"):
        want_methods.append(6)

    try:
        if want_methods:
            ensure_chrome(acc)
            eng.connect()
            print("health", eng.health())
            for method in want_methods:
                for trial in (1, 2, 3):
                    if (method, trial) in done:
                        print(f"跳过 写法{method} 第{trial}（已有成片）")
                        continue
                    eng = _reconnect(eng, acc)
                    rec = run_once(eng, start, end, ps[method], method, trial,
                                   reverse=True)
                    rec["desc_start"] = desc_s
                    rec["desc_end"] = desc_e
                    results = [r for r in results
                               if not (r.get("method") == method
                                       and r.get("trial") == trial)]
                    results.append(rec)
                    _save(results, results_p)
            summarize(results)
            decision = pick_winner(results)
            _save(decision, decision_p)
            print("决定：", decision["why"], flush=True)
            apply_winner(decision)
        else:
            decision = _load(decision_p) or pick_winner(results)
            apply_winner(decision)

        if args.phase in ("", "prod"):
            if eng._ctx is None:
                ensure_chrome(acc)
                eng.connect()
            prod = _load(prod_p)
            prod_done = {r.get("trial") for r in prod if r.get("file")}
            flip = bool(pipeline.GROK_BRIDGE_FLIP_PROMPT)
            reverse = pipeline.GROK_BRIDGE_UPLOAD == "end_then_start"
            p = prod_prompt(desc_s, desc_e, flip)
            print(f"\n===== 真接片 ×3  顺序={pipeline.GROK_BRIDGE_UPLOAD}  "
                  f"flip={flip} =====", flush=True)
            for trial in (1, 2, 3):
                if trial in prod_done:
                    print(f"跳过 真接片 第{trial}")
                    continue
                eng = _reconnect(eng, acc)
                rec = run_once(eng, start, end, p, "prod", trial,
                               reverse=reverse)
                rec["upload"] = pipeline.GROK_BRIDGE_UPLOAD
                rec["flip_prompt"] = flip
                prod = [r for r in prod if r.get("trial") != trial]
                prod.append(rec)
                _save(prod, prod_p)
            print("真接片：")
            for r in sorted(prod, key=lambda x: x.get("trial") or 0):
                print(f"  prod{r.get('trial')}  首↔起 {r.get('dist_first_start')}  "
                      f"尾↔终 {r.get('dist_last_end')}  "
                      f"≤30首 {r.get('dist_first_start') is not None and (r.get('dist_first_start') or 99) <= 30}  "
                      f"≤30尾 {r.get('dist_last_end') is not None and (r.get('dist_last_end') or 99) <= 30}")

        if args.phase in ("", "mismatch"):
            if eng._ctx is None:
                ensure_chrome(acc)
                eng.connect()
            eng = _reconnect(eng, acc)
            mis = run_mismatch_live(eng, start, end, desc_e)
            _save(mis, mis_p)
            print("失配：", json.dumps({k: mis.get(k) for k in
                                      ("yellow", "s003_can_enqueue", "note", "error")},
                                     ensure_ascii=False), flush=True)
    finally:
        try:
            eng.close()
        except Exception:
            pass
    print("wrote", results_p, decision_p)


if __name__ == "__main__":
    main()
