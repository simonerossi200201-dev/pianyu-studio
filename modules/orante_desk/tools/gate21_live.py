# -*- coding: utf-8 -*-
"""规格 21 验收第 6 条：真实 Grok 出片 3 段，确认闸门不误伤。

用法：python tools/gate21_live.py
落盘到 data/tmp/gate21-live/，不改 T0013。
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orante import db, accounts, paths  # noqa: E402
from orante.engines.base import make_engine, EngineUnavailable  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import verify  # noqa: E402
from orante.errors import OranteError, NeedRedownload  # noqa: E402

SRC = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04")
OUT = paths.DATA / "tmp" / "gate21-live"
SEGS = [
    # 用 T0013 里首帧对得上的三段配图，文案缩短到 6s
    (1, "p04", "Look at Nineveh. They stopped doing evil."),
    (2, "p17", "True repentance is not a moment. It is a changed life."),
    (3, "p05", "God is holy. Forgiveness is power to walk in holiness."),
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_account():
    grok = [a for a in accounts.list_all() if a.get("engine") == "grok" and a.get("enabled")]
    if not grok:
        raise SystemExit("没有启用的 Grok 账号")
    # Lucy A006 是 T0013 用的
    for a in grok:
        if a.get("id") == "A006":
            return a
    return grok[0]


def setup_dir() -> tuple[Path, Manifest]:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True)
    (OUT / "clips").mkdir()
    src_m = Manifest.load(SRC)
    images = []
    segs = []
    texts = []
    for s, p, text in SEGS:
        im = src_m.image(p)
        if not im:
            raise SystemExit(f"源任务没有 {p}")
        src_f = SRC / (im.get("file") or "")
        if not src_f.is_file():
            raise SystemExit(f"找不到 {src_f}")
        dest = OUT / "images" / src_f.name
        shutil.copy2(src_f, dest)
        images.append({
            "p": p, "file": f"images/{dest.name}", "sha1": im.get("sha1") or "",
            "role": "主体", "status": "ok",
            "desc": im.get("desc") or "", "voice": im.get("voice") or "",
            "eye": im.get("eye") or "闭眼", "scene": im.get("scene") or "常规",
            "measure": im.get("measure") or {}, "origin": "local",
        })
        # 用源任务里同图某段的 prompt_final，把尾部英文换成这段短文案
        pf = ""
        for sg in src_m.segments:
            if sg.get("image") == p and sg.get("prompt_final"):
                pf = sg["prompt_final"]
                break
        if pf and sg_text_tail(pf):
            pf = replace_tail(pf, text)
        else:
            pf = (im.get("desc") or "") + "\n\n" + text
        segs.append({
            "s": s, "text": text, "duration": 6, "image": p, "role": "主体",
            "shot": "", "prompt_final": pf, "status": "planned",
            "gen_id": None, "takes": [], "error": None,
        })
        texts.append(text)
    copy_text = " ".join(texts)
    m = Manifest.new("T9900", "gate21-live", "grok", "A006", "short", copy_text)
    m.images = images
    m.segments = segs
    m.save(OUT)
    return OUT, m


def sg_text_tail(pf: str) -> bool:
    return bool(pf and len(pf) > 40)


def replace_tail(pf: str, text: str) -> str:
    # 成品提示词通常最后一块是本段英文。粗暴：从最后一个空行切开换掉。
    parts = pf.rsplit("\n\n", 1)
    if len(parts) == 2 and len(parts[1].split()) < 80:
        return parts[0] + "\n\n" + text
    return pf + "\n\n" + text


def main() -> int:
    db.init()
    acc = pick_account()
    log(f"账号 {acc['id']} {acc.get('name')} 端口 {acc.get('port')}")
    d, m = setup_dir()
    log(f"任务目录 {d}")
    eng = make_engine(acc, log)
    try:
        eng.connect()
    except EngineUnavailable as e:
        log(f"连不上：{e}")
        log("第 6 条未跑：Grok Chrome 没登录或没起来")
        return 2
    h = eng.health()
    log(f"health {h}")
    if not h.get("logged_in"):
        eng.close()
        log("第 6 条未跑：还没登录")
        return 2

    started = {}
    inflight = {}
    n_ok = 0
    try:
        for sg in m.segments:
            im = m.image(sg["image"])
            img = d / im["file"]
            gid = eng.submit(sg, img, sg["prompt_final"], 6)
            sg["status"] = "submitted"
            sg["gen_id"] = gid
            inflight[gid] = sg
            started[gid] = time.time()
            log(f"已提交 s{sg['s']:03d} → {gid[:8]}…")
            m.save(d)
            time.sleep(1.5)

        deadline = time.time() + 12 * 60
        while inflight and time.time() < deadline:
            time.sleep(6)
            res = eng.poll(list(inflight))
            for gid, r in list(res.items()):
                sg = inflight.get(gid)
                if not sg:
                    continue
                if r.get("state") == "done":
                    dest = d / "clips" / m.clip_name(sg)
                    try:
                        eng.download(gid, dest)
                    except NeedRedownload as e:
                        log(f"↻ s{sg['s']:03d} 闸3 重取：{e.message}")
                        continue
                    except OranteError as e:
                        log(f"✗ s{sg['s']:03d} 下载失败 {e.message}")
                        sg["status"] = "failed"
                        inflight.pop(gid, None)
                        continue
                    vr = verify.after_download(
                        m, d, sg, dest,
                        asset_url=r.get("url") or "", gen_id=gid, log=log, apply=True)
                    if vr.get("voided"):
                        forget = getattr(eng, "forget_url", None)
                        if callable(forget):
                            forget(gid, r.get("url") or "")
                        log(f"作废 s{sg['s']:03d} {vr.get('code')}，重取")
                        continue
                    sg.setdefault("takes", []).append(vr["take"])
                    sg["status"] = "collected"
                    inflight.pop(gid, None)
                    n_ok += 1
                    tk = vr["take"]
                    log(f"✓ s{sg['s']:03d} verdict={tk.get('verdict')} "
                        f"dist={tk.get('frame_dist')} sha={str(tk.get('sha256'))[:12]}…")
                    m.save(d)
                elif r.get("state") == "failed":
                    log(f"✗ s{sg.get('s')} 生成失败 {r.get('error')}")
                    sg["status"] = "failed"
                    inflight.pop(gid, None)
                    m.save(d)
            log(f"… 在飞 {len(inflight)} 已收 {n_ok}")
    finally:
        eng.close()
        m.save(d)

    print(json.dumps({
        "ok": n_ok == 3,
        "collected": n_ok,
        "segments": [
            {"s": sg["s"], "status": sg.get("status"),
             "take": (sg.get("takes") or [None])[-1]}
            for sg in m.segments
        ],
    }, ensure_ascii=False, indent=2))
    if n_ok != 3:
        log(f"第 6 条未齐：收了 {n_ok}/3")
        return 1
    for sg in m.segments:
        tk = (sg.get("takes") or [{}])[-1]
        if not tk.get("sha256") or tk.get("frame_dist") is None or tk.get("verdict") != "ok":
            log(f"s{sg['s']:03d} 凭证不齐 {tk}")
            return 1
    log("第 6 条通过：3 段落盘，sha256 / frame_dist / verdict=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
