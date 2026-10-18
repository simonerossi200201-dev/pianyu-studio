# -*- coding: utf-8 -*-
"""规格 36 三个探针 + T0013 尾帧/姿态实跑。

  python tools/probe36.py a        Flow 尾帧载荷格
  python tools/probe36.py c        T0013 倒数 1~10 帧锐度
  python tools/probe36.py extract  抽全部尾帧并填 bridge
  python tools/probe36.py pose     给已有描述语的图补 pose
  python tools/probe36.py b        Grok 双图 4×3
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from orante import db, paths, tasks as taskmod, accounts  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import tails, describe, ai  # noqa: E402

OUT = paths.DATA / "capture" / "probe36"
OUT.mkdir(parents=True, exist_ok=True)
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class LogJob:
    def __init__(self, name="probe36"):
        self.id = name
        self.task_id = ""
        self.lines = []
        self.cancelled = None

    def log(self, msg):
        print(msg, flush=True)
        self.lines.append(msg)

    def check(self):
        pass


def redact(obj):
    s = json.dumps(obj, ensure_ascii=False)
    s = UUID_RE.sub("«uuid»", s)
    s = re.sub(r"at=[^&\s]{8,}", "at=«xsrf»", s)
    return json.loads(s)


def t0013_dir() -> Path:
    db.init()
    t = taskmod.get("T0013")
    d = Path(t["dir"])
    if not d.is_dir():
        raise SystemExit(f"T0013 目录不在：{d}")
    return d


def dump(name: str, obj) -> Path:
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  写了 {p}", flush=True)
    return p


# ================================================================= A′
def _inner_from_post(pd: str):
    if not pd:
        return None
    try:
        qs = urllib.parse.parse_qs(pd, keep_blank_values=True)
        freq = (qs.get("f.req") or [""])[0]
        if not freq:
            return None
        outer = json.loads(freq)
        inner_s = outer[0][0][1]
        return json.loads(inner_s) if isinstance(inner_s, str) else inner_s
    except Exception:
        return None


def parse_existing_eb1hJf() -> list[dict]:
    cap = paths.DATA / "capture"
    rows = []
    for p in sorted(cap.glob("flow_capture_*.jsonl")):
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
            if "eb1hJf" not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            inner = _inner_from_post(rec.get("postData") or rec.get("post") or "")
            if inner is None:
                continue
            job = None
            try:
                job = inner[0][0]
            except Exception:
                job = inner
            img_slot = None
            if isinstance(job, list) and len(job) >= 5:
                img_slot = job[4]
            rows.append({
                "file": p.name, "line": i + 1,
                "img_slot": img_slot,
                "job_len": len(job) if isinstance(job, list) else None,
                "inner": inner,
            })
    return rows


def slot_summary(slot) -> str:
    if not isinstance(slot, list):
        return repr(slot)[:80]
    bits = []
    for i, v in enumerate(slot):
        if v is None:
            bits.append(f"[{i}]=null")
        elif isinstance(v, str) and len(v) > 12:
            bits.append(f"[{i}]=media:{v[:8]}…")
        else:
            bits.append(f"[{i}]={v!r}"[:40])
    return "  ".join(bits)


def probe_a_parse() -> dict:
    rows = parse_existing_eb1hJf()
    print(f"历史抓包里 eb1hJf {len(rows)} 条", flush=True)
    start_only = None
    for r in rows:
        slot = r.get("img_slot")
        print(f"  {r['file']}#{r['line']}  {slot_summary(slot)}", flush=True)
        if isinstance(slot, list) and slot:
            # 只有一格有 media_id 的，当作「仅首帧」
            ids = [i for i, v in enumerate(slot) if isinstance(v, str) and len(v) > 8]
            if len(ids) == 1:
                start_only = r
    dump("a-start-only.json", redact(start_only) if start_only else {"n": len(rows)})
    return {"n": len(rows), "start_only": start_only}


HOOK_JS = r"""
() => {
  if (window.__oranteEb1) return 'already';
  window.__oranteEb1 = [];
  const wrap = (orig) => async function(input, init) {
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    const res = orig.apply(this, arguments);
    try {
      if (/batchexecute/.test(url) && /eb1hJf/.test(url)) {
        const body = (init && init.body) ? String(init.body) : '';
        window.__oranteEb1.push({t: Date.now(), url: url.slice(0, 240), body: body.slice(0, 80000)});
      }
    } catch (e) {}
    return res;
  };
  window.fetch = wrap(window.fetch.bind(window));
  return 'hooked';
}
"""


def probe_a_live() -> dict:
    """连一个 Flow 号，钩 fetch，搜 JS 里尾帧字段，并尽量在页面上走一次首+尾。"""
    db.init()
    accs = [a for a in accounts.list_all() if a.get("engine") == "flow" and a.get("enabled")]
    if not accs:
        return {"ok": False, "error": "没有启用的 Flow 账号"}
    acc = accs[0]
    print(f"连 Flow {acc['name']} 端口 {acc['port']}", flush=True)
    from orante.engines.base import make_engine
    job = LogJob("probe36a")
    eng = make_engine(acc, job.log)
    out = {"account": acc["name"], "js_hits": [], "captured": [], "ui": ""}
    try:
        eng.connect()
        page = eng.page
        try:
            page.evaluate(HOOK_JS)
        except Exception as e:
            out["hook_err"] = str(e)[:120]
        # 搜页面加载过的 JS 里有没有 endImage / lastFrame 和数组下标线索
        try:
            urls = page.evaluate("""() => performance.getEntriesByType('resource')
                .map(e => e.name).filter(u => /\\.js(\\?|$)/.test(u)).slice(0, 40)""") or []
        except Exception:
            urls = []
        keys = ("endImage", "lastFrame", "last_frame", "StartAndEnd",
                "end_image", "endMedia", "lastMedia")
        for u in urls:
            try:
                txt = page.evaluate("""async (u) => {
                  try { const r = await fetch(u, {credentials:'include'});
                        const t = await r.text(); return t.slice(0, 2500000); }
                  catch(e) { return ''; }
                }""", u)
            except Exception:
                continue
            if not txt:
                continue
            for k in keys:
                if k in txt:
                    idx = txt.find(k)
                    out["js_hits"].append({
                        "url": u.split("?")[0][-80:],
                        "key": k,
                        "around": txt[max(0, idx - 180): idx + 220],
                    })
        dump("a-js-hits.json", out["js_hits"])

        # 试着在 UI 上点 Frames / End。找不到就记下控件清单。
        try:
            ui = page.evaluate("""() => {
              const bits = [];
              for (const el of document.querySelectorAll('button, [role=button], span, div, label')) {
                const t = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).replace(/\\s+/g,' ').trim();
                if (!t || t.length > 40) continue;
                if (/start|end|frame|首帧|尾帧|结束|开始|frames/i.test(t))
                  bits.push({t, tag: el.tagName});
                if (bits.length > 40) break;
              }
              return bits;
            }""")
            out["ui"] = ui
        except Exception as e:
            out["ui"] = str(e)[:120]
        dump("a-ui-labels.json", out["ui"])

        # 用两张 T0013 的图走一遍「上传 + 提交」，如果 UI 有 End 槽就点。
        d = t0013_dir()
        imgs = sorted((d / "images").glob("p*.jp*g"))[:2]
        if len(imgs) >= 2 and hasattr(eng, "upload_image"):
            try:
                mid1 = eng.upload_image(imgs[0])
                mid2 = eng.upload_image(imgs[1])
                out["uploaded"] = {"start": (mid1 or "")[:12], "end": (mid2 or "")[:12]}
                # 不在这里乱打 eb1hJf：要等 UI 钩子抓到真载荷。
                # 尝试点页面上的 End / 尾帧，把第二张挂进去再点生成太贵且 UI 不稳，
                # 所以只把两张 media_id 和「仅首帧」的现有 inner 对比用。
            except Exception as e:
                out["upload_err"] = str(e)[:200]

        try:
            hooked = page.evaluate("() => window.__oranteEb1 || []")
            out["captured"] = hooked
        except Exception:
            pass
        dump("a-live.json", redact(out))
        out["ok"] = True
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:300]
        dump("a-live.json", redact(out))
        return out
    finally:
        try:
            eng.close()
        except Exception:
            pass


# ================================================================= C
def probe_c() -> dict:
    d = t0013_dir()
    m = Manifest.load(d)
    report = {"task": "T0013", "dir": str(d), "segs": []}
    for sg in m.segments:
        clip = tails.clip_of(d, m, sg)
        if not clip:
            report["segs"].append({"s": sg["s"], "error": "no clip"})
            continue
        print(f"s{sg['s']:03d} {clip.name}", flush=True)
        info = tails.video_info(clip)
        work = OUT / f"c-s{sg['s']:03d}"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        cands = tails.scan_tail_candidates(clip, 10, work)
        rows = []
        for c in cands:
            rows.append({
                "k": c["k"],
                "sharp": None if c.get("sharp") is None else round(float(c["sharp"]), 2),
                "error": c.get("error"),
            })
            sh = rows[-1]["sharp"]
            print(f"  倒数第 {c['k']:2d}  锐度 {sh}", flush=True)
        picked = tails.pick_sharpest(cands, 3, 8)
        report["segs"].append({
            "s": sg["s"], "clip": clip.name,
            "fps": info["fps"], "duration": info["duration"],
            "nframes": info["nframes"], "wh": [info["width"], info["height"]],
            "frames": rows,
            "pick_k": None if not picked else picked["k"],
            "pick_sharp": None if not picked else round(float(picked["sharp"]), 2),
        })
    dump("c-sharpness.json", report)
    return report


# ================================================================= extract / pose
def run_extract() -> dict:
    d = t0013_dir()
    m = Manifest.load(d)
    job = LogJob("extract")
    job.task_id = "T0013"
    print("抽 T0013 全部尾帧…", flush=True)
    rows = tails.extract_all(d, m, log=job.log, inspect=True)
    dump("extract.json", [
        {k: v for k, v in r.items() if k not in ("path",)}
        for r in rows
    ])
    return {"n": len(rows), "rows": rows}


def run_pose() -> dict:
    d = t0013_dir()
    m = Manifest.load(d)
    job = LogJob("pose")
    job.task_id = "T0013"
    before = ai.calls()
    # 已有描述语的图：只补 pose，不重写描述语
    n = describe.fill_missing_poses(job, m, d)
    after = ai.calls()
    out = {"filled": n, "ai_calls_delta": after - before, "ai_calls": after}
    dump("pose.json", out)
    return out


# ================================================================= B
PROMPTS_B = {
    1: (
        "第一张是起始画面，第二张是结束画面。"
        "人物把这段话说完。只说下面这句英文，一字不差：\n"
        "Repentance means a change of mind that leads to a change of direction."
    ),
    2: (
        "请阅读两张图的文件名：01_首帧_start.jpg 是起始画面，"
        "02_尾帧_end.jpg 是结束画面。"
        "人物把这段话说完。只说下面这句英文，一字不差：\n"
        "Repentance means a change of mind that leads to a change of direction."
    ),
    3: None,  # 运行时填内容描述
    4: None,
}


def _prep_pair(start: Path, end: Path, work: Path) -> tuple[Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    a = work / "01_首帧_start.jpg"
    b = work / "02_尾帧_end.jpg"
    shutil.copy2(start, a)
    shutil.copy2(end, b)
    return a, b


def probe_b() -> dict:
    """4 种写法 × 3 次。用 T0013 一段尾帧 + 一张锚点图。"""
    d = t0013_dir()
    m = Manifest.load(d)
    # 先保证至少有一张尾帧
    sg = m.seg(1)
    clip = tails.clip_of(d, m, sg)
    if not clip:
        raise SystemExit("T0013 s001 没有成片")
    start = tails.tail_path(d, 1)
    if not start.is_file():
        print("先抽 s001 尾帧作起始图…", flush=True)
        tails.extract(d, sg, inspect=False)
    # 锚点图：s003 的配图（奇数段，交替里的下一个锚点）
    im = m.image((m.seg(3) or {}).get("image") or "") or m.subject_images()[0]
    end = d / im["file"]
    print(f"起始 {start.name}  结束 {end.name} ({im['p']})", flush=True)

    # 内容描述（写法 ③④ 用）——一次多模态读两张
    job = LogJob("probe36b")
    desc_s = desc_e = ""
    try:
        gem = ai.client(job)
        raw = gem.ask_images(
            "两张图。只输出 JSON："
            '{"start":"第一张一句话","end":"第二张一句话"}',
            [start, end], json_mode=True)
        dd = tails.parse_json_obj(raw) or {}
        desc_s = str(dd.get("start") or "")
        desc_e = str(dd.get("end") or "")
    except Exception as e:
        print("读图失败，用占位描述：", e, flush=True)
        desc_s, desc_e = "人物站在讲台上说话", "人物换了一个姿态站在讲台上"
    p3 = (
        f"请阅读文件名：01_首帧_start.jpg 是起始画面（{desc_s}），"
        f"02_尾帧_end.jpg 是结束画面（{desc_e}）。"
        "人物把这段话说完。只说下面这句英文，一字不差：\n"
        "Repentance means a change of mind that leads to a change of direction."
    )
    p4 = p3 + "画面从第一张的状态自然过渡到第二张的状态，中间不切镜。"
    prompts = {1: PROMPTS_B[1], 2: PROMPTS_B[2], 3: p3, 4: p4}

    db.init()
    accs = [a for a in accounts.list_all() if a.get("engine") == "grok" and a.get("enabled")]
    if not accs:
        return {"ok": False, "error": "没有启用的 Grok 账号"}
    acc = accs[-1]  # Lucy / 最近的
    print(f"用 Grok 账号 {acc['name']} 端口 {acc['port']}", flush=True)

    from orante.engines.base import make_engine
    from orante.legacy import grok_pipe  # may fail; paths.add
    paths.add_legacy_to_sys_path()
    import grok_pipe  # noqa: E402
    import grok_batch  # noqa: E402

    eng = make_engine(acc, job.log)
    results = []
    try:
        eng.connect()
        for method in (1, 2, 3, 4):
            for trial in (1, 2, 3):
                rec = _run_b_once(eng, grok_pipe, grok_batch, start, end,
                                  prompts[method], method, trial, job)
                results.append(rec)
                dump("b-results.json", results)
    finally:
        try:
            eng.close()
        except Exception:
            pass
    dump("b-results.json", results)
    return {"ok": True, "results": results, "desc_start": desc_s, "desc_end": desc_e}


def _run_b_once(eng, grok_pipe, grok_batch, start, end, prompt, method, trial, job) -> dict:
    work = OUT / f"b-m{method}-t{trial}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    a, b = _prep_pair(start, end, work)
    rec = {"method": method, "trial": trial, "ok": False, "error": "",
           "has_audio": None, "duration": None, "start_match": None, "end_match": None,
           "file": ""}
    print(f"\n=== 写法 {method} 第 {trial} 次 ===", flush=True)
    page = None
    try:
        page = eng._ctx.new_page()
        page.goto(eng.__class__.__module__ and "https://grok.com/imagine",
                  wait_until="domcontentloaded", timeout=60000)
        # 上面那行太绕，直接用常量
    except Exception:
        pass
    try:
        if page is None:
            page = eng._ctx.new_page()
        page.goto("https://grok.com/imagine", wait_until="domcontentloaded", timeout=60000)
        grok_pipe.pass_tos_gate(page) if hasattr(grok_pipe, "pass_tos_gate") else None
        from grok_batch import pass_tos_gate, set_params, verify_tab
        pass_tos_gate(page)
        page.wait_for_selector("div.tiptap", state="visible", timeout=30000)
        set_params(page, "6s", log=job.log)
        grok_pipe.attach_by_drop(page, str(a))
        page.wait_for_selector("img[src^='blob:']", state="attached", timeout=45000)
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0, tag=f"b{method}-{trial}a")
        grok_pipe.attach_by_drop(page, str(b))
        # 等到两张缩略图
        t0 = time.time()
        while time.time() - t0 < 45:
            n = page.evaluate("() => document.querySelectorAll(\"img[src^='blob:']\").length")
            if n >= 2:
                break
            time.sleep(0.5)
        rec["n_thumbs"] = n if 'n' in dir() else None
        grok_pipe.wait_quiet(page, quiet=2.0, timeout=40.0, tag=f"b{method}-{trial}b")
        set_params(page, "6s", log=job.log)
        grok_pipe.type_prompt(page, prompt)
        how = grok_pipe.click_submit(page)
        rec["how"] = str(how)[:80]
        post, conv = grok_pipe.capture_anchor(page)
        rec["post"] = post
        if not post:
            rec["error"] = "没抓到 postId"
            return rec
        # 轮询
        row = {"postId": post, "会话Id": conv or "", "完整提示词": prompt,
               "时长": "6s", "编号": f"b{method}", "第几段": trial}
        deadline = time.time() + 240
        url = None
        while time.time() < deadline:
            st = grok_pipe.probe(page, post=post)
            rec["probe"] = str(st)[:120]
            if isinstance(st, dict):
                state = st.get("状态") or st.get("state") or ""
                if "已生成" in str(st) or state in ("done", "已生成"):
                    url = st.get("视频URL") or st.get("url")
                    break
                if "失败" in str(st) or state in ("failed", "失败"):
                    rec["error"] = str(st)[:200]
                    return rec
            elif isinstance(st, str) and "已生成" in st:
                break
            time.sleep(6)
        dest = work / "out.mp4"
        if url:
            row["视频URL"] = url
            try:
                grok_pipe.download_one(eng._ctx, row, str(dest))
            except Exception as e:
                rec["error"] = f"下载失败 {e}"[:160]
        elif dest.is_file() is False:
            # 退回 GrokEngine.download 需要 gen_id
            rec["error"] = rec.get("error") or "没拿到视频 URL"
        if dest.is_file() and dest.stat().st_size > 50000:
            rec["file"] = str(dest)
            rec["bytes"] = dest.stat().st_size
            rec.update(_score_b_video(dest, a, b, work))
            rec["ok"] = bool(rec.get("start_match") and rec.get("end_match"))
        return rec
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:240]
        return rec
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def _score_b_video(video: Path, start: Path, end: Path, work: Path) -> dict:
    """抽成品首尾帧，和两张参考图比对（拉普拉斯不管，用 aHash）。"""
    from orante.services import verify as verifymod
    first = work / "out_first.jpg"
    last = work / "out_last.jpg"
    try:
        tails.extract_from_end(video, 9999, first)  # will fail; use sseof from start
    except Exception:
        pass
    # 首帧：-ss 0
    from orante.services.procutil import run_quiet
    from orante.services.checks import find_ffmpeg
    ff = find_ffmpeg()
    run_quiet([ff, "-hide_banner", "-loglevel", "error", "-ss", "0", "-i", str(video),
               "-frames:v", "1", "-y", str(first)], timeout=30)
    tails.extract_from_end(video, 1, last)
    info = tails.video_info(video)
    # 有没有音轨
    has_audio = None
    try:
        r = run_quiet([ff.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
                      timeout=20)
        has_audio = bool((r.stdout or b"").strip())
    except Exception:
        has_audio = None
    def dist(p, q):
        ha = verifymod.ahash_image(p)
        hb = verifymod.ahash_image(q)
        if ha is None or hb is None:
            return None
        return bin(ha ^ hb).count("1")
    d_fs = dist(first, start)
    d_fe = dist(first, end)
    d_ls = dist(last, start)
    d_le = dist(last, end)
    start_match = d_fs is not None and d_fe is not None and d_fs < d_fe
    end_match = d_le is not None and d_ls is not None and d_le < d_ls
    return {
        "duration": info.get("duration"),
        "has_audio": has_audio,
        "dist_first_start": d_fs, "dist_first_end": d_fe,
        "dist_last_start": d_ls, "dist_last_end": d_le,
        "start_match": start_match, "end_match": end_match,
    }


def main():
    args = sys.argv[1:] or ["c"]
    cmd = args[0]
    if cmd == "a":
        probe_a_parse()
        probe_a_live()
    elif cmd == "a-parse":
        probe_a_parse()
    elif cmd == "c":
        probe_c()
    elif cmd == "extract":
        run_extract()
    elif cmd == "pose":
        run_pose()
    elif cmd == "b":
        probe_b()
    else:
        print("用法: probe36.py a|a-parse|c|extract|pose|b")
        sys.exit(2)


if __name__ == "__main__":
    main()
