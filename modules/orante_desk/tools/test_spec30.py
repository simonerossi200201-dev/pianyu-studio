# -*- coding: utf-8 -*-
"""规格 30：全局刷新扫盘/清缓存；编排间隔排列（hold 组内不拆、钉住不动）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec30-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, config, tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.services import arrange, refresh, track as trackmod, verify  # noqa: E402

db.init()
load_all_services()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _jpeg(path: Path, color=(40, 80, 60)):
    """写一个能过 is_file 的假 jpg，不依赖 Pillow。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 最小 SOI/EOI，颜色只用来让每张哈希不同
    path.write_bytes(b"\xff\xd8\xff\xd9" + bytes(color) + os.urandom(8))


def _ensure_account():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()))


def _make(n_seg=9, n_img=3, tid="T3001") -> tuple[str, Path, Manifest]:
    _ensure_account()
    task_dir = Path(tempfile.mkdtemp(prefix="orante-task-30-"))
    (task_dir / "images").mkdir()
    (task_dir / "clips").mkdir()
    images = []
    for i in range(1, n_img + 1):
        p = f"p{i:02d}"
        fp = task_dir / "images" / f"{p}.jpg"
        _jpeg(fp, (40 + i * 30, 80, 60))
        images.append({
            "p": p, "file": f"images/{p}.jpg", "sha1": sha1_file(fp),
            "role": "主体", "status": "ok", "desc": f"d{p}", "voice": "",
            "eye": "闭眼", "scene": "常规", "measure": {},
            "origin": "local", "src": str(fp),
        })
    segs = []
    for i in range(1, n_seg + 1):
        segs.append({
            "s": i, "text": f"seg {i}", "duration": 6,
            "image": "", "role": "主体", "shot": "", "prompt_final": "",
            "status": "planned", "gen_id": None, "takes": [], "error": None,
        })
    m = Manifest.new(tid, "间隔测", "flow", "A001", "long", "hello")
    m.images = images
    m.segments = segs
    m.save(task_dir)
    if db.one("SELECT 1 FROM tasks WHERE id=?", (tid,)):
        db.run("UPDATE tasks SET dir=?, status=?, updated=? WHERE id=?",
               (str(task_dir), "described", db.now(), tid))
    else:
        db.run(
            "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,"
            "duration_pref,status,dir,batch_id,row_order,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "间隔测", "flow", "A001", "long", "hello", str(task_dir / "images"),
             "", "described", str(task_dir), "", 0, db.now(), db.now()))
    return tid, task_dir, Manifest.load(task_dir)


def _blank_row(s, p, why="自动"):
    return {
        "s": s, "label": "s%03d" % s, "role": "主体",
        "state": "planned", "status": "planned", "dur": 6,
        "text": "", "p_old": "", "p_new": p, "why": why,
        "locked": None, "warn": None, "changed": False,
    }


def test_norm_default():
    print("== track.no_adjacent_repeat 默认 true ==")
    if config.DEFAULTS.get("track.no_adjacent_repeat") is not True:
        bad("DEFAULTS 里 track.no_adjacent_repeat 不是 True")
        return
    tid, d, m = _make(tid="T3010")
    tr = arrange._norm_track({}, m)
    if tr.get("no_adjacent_repeat") is not True:
        bad(f"缺省应该 true，实际 {tr.get('no_adjacent_repeat')}")
        return
    tr2 = arrange._norm_track({"no_adjacent_repeat": False}, m)
    if tr2.get("no_adjacent_repeat") is not False:
        bad("显式 false 没保住")
        return
    ok("默认开，显式关得住")


def test_hold1_no_adjacent():
    print("== 9 段 3 图 hold=1：相邻不许同图 ==")
    tid, d, m = _make(9, 3, tid="T3001")
    prev = arrange.preview(tid, {
        "hold": 1, "jitter": 0, "unit": "seg", "seed": 1,
        "no_adjacent_repeat": True, "pool": ["p01", "p02", "p03"],
    })
    ps = [r["p_new"] for r in prev["rows"]]
    hits = [(i + 1, ps[i], ps[i + 1]) for i in range(len(ps) - 1) if ps[i] and ps[i] == ps[i + 1]]
    if hits:
        bad(f"还有相邻同图：{hits}  整轨 {ps}")
        return
    ok(f"预演 {ps} 无相邻重复")


def test_hold1_forced_all_same():
    print("== 全是 p01 的轨，hold=1 后处理能拆开 ==")
    tid, d, m = _make(9, 3, tid="T3002")
    images = arrange._annotate_images(m, d)
    rows = [_blank_row(i, "p01") for i in range(1, 10)]
    res = {"rows": rows, "warnings": [], "summary": {}}
    tr = {"hold": 1, "jitter": 0, "unit": "seg", "pins": {},
          "pool": ["p01", "p02", "p03"], "no_adjacent_repeat": True}
    arrange.enforce_spacing(res, tr, images)
    ps = [r["p_new"] for r in res["rows"]]
    hits = [i for i in range(len(ps) - 1) if ps[i] == ps[i + 1]]
    if hits:
        bad(f"后处理没拆开：{ps}")
        return
    if (res["summary"] or {}).get("spacing_changed", 0) < 1:
        bad(f"该改几段，spacing_changed={res['summary']}")
        return
    ok(f"全 p01 → {ps}，改了 {res['summary']['spacing_changed']} 段")


def test_pins_blocked():
    print("== s004、s005 都钉 p03：钉住不动，warning 点名 ==")
    tid, d, m = _make(9, 3, tid="T3003")
    prev = arrange.preview(tid, {
        "hold": 1, "jitter": 0, "unit": "seg", "seed": 3,
        "no_adjacent_repeat": True,
        "pins": {"4": "p03", "5": "p03"},
        "pool": ["p01", "p02", "p03"],
    })
    by = {r["s"]: r["p_new"] for r in prev["rows"]}
    if by.get(4) != "p03" or by.get(5) != "p03":
        bad(f"钉住被改了：s4={by.get(4)} s5={by.get(5)}")
        return
    warns = "；".join(prev.get("warnings") or [])
    if "s004" not in warns or "s005" not in warns or "改不动" not in warns:
        bad(f"warning 没点名 s004/s005：{warns}")
        return
    sm = prev.get("summary") or {}
    if int(sm.get("spacing_blocked") or 0) < 1:
        bad(f"spacing_blocked 该 ≥1：{sm}")
        return
    # 其它相邻（除 4-5）不许同图
    ps = [r["p_new"] for r in prev["rows"]]
    bad_adj = []
    for i in range(len(ps) - 1):
        if ps[i] == ps[i + 1] and not (prev["rows"][i]["s"] == 4 and prev["rows"][i + 1]["s"] == 5):
            bad_adj.append((prev["rows"][i]["s"], prev["rows"][i + 1]["s"], ps[i]))
    if bad_adj:
        bad(f"非钉区还有相邻同图：{bad_adj}  {ps}")
        return
    ok(f"钉住仍是 p03，warning「{warns}」")


def test_hold3_keeps_groups():
    print("== hold=3：组内连着 3 段，只保证组界不同 ==")
    tid, d, m = _make(9, 3, tid="T3004")
    images = arrange._annotate_images(m, d)
    rows = [_blank_row(i, "p01") for i in range(1, 10)]
    res = {"rows": rows, "warnings": [], "summary": {}}
    tr = {"hold": 3, "jitter": 0, "unit": "seg", "pins": {},
          "pool": ["p01", "p02", "p03"], "no_adjacent_repeat": True}
    arrange.enforce_spacing(res, tr, images)
    ps = [r["p_new"] for r in res["rows"]]
    # 组内：0:3 / 3:6 / 6:9 各自相同
    for a, b in ((0, 3), (3, 6), (6, 9)):
        chunk = ps[a:b]
        if len(set(chunk)) != 1:
            bad(f"组内被拆开 {a}:{b} = {chunk}  整轨 {ps}")
            return
    if ps[2] == ps[3] or ps[5] == ps[6]:
        bad(f"组界还是同一张：{ps}")
        return
    ok(f"组内连 3 段、组界不同：{ps}")


def test_hold3_preview():
    print("== hold=3 预演：组界不同、组内可连 ==")
    tid, d, m = _make(9, 3, tid="T3005")
    prev = arrange.preview(tid, {
        "hold": 3, "jitter": 0, "unit": "seg", "seed": 11,
        "no_adjacent_repeat": True, "pool": ["p01", "p02", "p03"],
    })
    ps = [r["p_new"] for r in prev["rows"]]
    if len(ps) != 9:
        bad(f"行数 {len(ps)}")
        return
    # 组界（第 3/4、第 6/7 段，1-based）不许同图
    if ps[2] == ps[3]:
        bad(f"s3 和 s4 同图 {ps}")
        return
    if ps[5] == ps[6]:
        bad(f"s6 和 s7 同图 {ps}")
        return
    ok(f"预演组界不同：{ps}")


def test_off_does_not_touch():
    print("== 关掉间隔排列：全 p01 原样 ==")
    tid, d, m = _make(6, 3, tid="T3006")
    prev = arrange.preview(tid, {
        "hold": 6, "jitter": 0, "unit": "seg", "seed": 1,
        "no_adjacent_repeat": False, "pool": ["p01", "p02", "p03"],
        "pins": {"1": "p01", "2": "p01", "3": "p01", "4": "p01", "5": "p01", "6": "p01"},
    })
    ps = [r["p_new"] for r in prev["rows"]]
    if ps != ["p01"] * 6:
        bad(f"关着还动了：{ps}")
        return
    sm = prev.get("summary") or {}
    if "spacing_changed" in sm and sm.get("spacing_changed"):
        bad("关着不该有 spacing_changed")
        return
    ok("关掉后不跑后处理")


def test_refresh_rescan_and_prune():
    print("== 刷新：扫文件 + 丢掉对不上的 ahash ==")
    tid, d, m = _make(3, 2, tid="T3007")
    clip = d / "clips" / "T3007-s001-p01.mp4"
    clip.write_bytes(b"not-a-real-mp4-but-a-file")
    n = refresh.rescan_files(tid)
    if n < 3:  # 2 images + 1 clip
        bad(f"files_rescanned={n}，至少该有 2 图 + 1 片")
        return
    cache = TEST_DATA / "ahash-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    gone = str((d / "images" / "nope.jpg").resolve())
    stale = str((d / "images" / "p01.jpg").resolve())
    cache.write_text(json.dumps({
        gone: {"size": 1, "mtime": 1, "hash": "aa"},
        stale: {"size": 1, "mtime": 1, "hash": "bb"},  # size/mtime 对不上
    }), encoding="utf-8")
    dropped = verify.prune_ahash_cache()
    if dropped < 2:
        bad(f"该丢掉 2 条，实际 {dropped}")
        return
    data = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    if gone in data or stale in data:
        bad(f"过期条目还在：{list(data)}")
        return
    verify._CACHE[("x", 1, 1)] = 1
    verify.clear_mem_cache()
    if verify._CACHE:
        bad("verify._CACHE 没清")
        return
    from orante.services import checks
    checks._CACHE[("y", 1, 1)] = 1
    checks.clear_mem_cache()
    if checks._CACHE:
        bad("checks._CACHE 没清")
        return
    out = refresh.run(deep=True, tid=tid)
    if not out.get("accounts_alive"):
        bad(f"缺 accounts_alive：{out}")
        return
    if out.get("files_rescanned", 0) < 3:
        bad(f"run.files_rescanned={out.get('files_rescanned')}")
        return
    if "ahash_dropped" not in out:
        bad(f"缺 ahash_dropped：{out}")
        return
    ok(f"扫到 {out['files_rescanned']} 个文件，账号 {out['accounts_alive']}")


def test_review_mtime():
    print("== 收片 payload 带 exists/mtime，删了片子 exists=false ==")
    from orante.services import review
    tid, d, m = _make(2, 1, tid="T3008")
    rel = "clips/T3008-s001-p01.mp4"
    fp = d / rel
    fp.write_bytes(b"fake-mp4")
    m.segments[0]["takes"] = [{"file": rel, "at": "now"}]
    m.segments[0]["image"] = "p01"
    m.save(d)
    payload = review._seg_payload(d, Manifest.load(d), Manifest.load(d).segments[0])
    if not payload.get("exists"):
        bad("片子在盘上 exists 该是 true")
        return
    if not payload.get("mtime"):
        bad(f"该有 mtime：{payload}")
        return
    fp.unlink()
    payload2 = review._seg_payload(d, Manifest.load(d), Manifest.load(d).segments[0])
    if payload2.get("exists"):
        bad("删了还 exists=true")
        return
    if payload2.get("mtime"):
        bad("没文件不该有 mtime")
        return
    ok("删片 → exists=false；放回去那步由刷新重拉 review 覆盖")


if __name__ == "__main__":
    test_norm_default()
    test_hold1_no_adjacent()
    test_hold1_forced_all_same()
    test_pins_blocked()
    test_hold3_keeps_groups()
    test_hold3_preview()
    test_off_does_not_touch()
    test_refresh_rescan_and_prune()
    test_review_mtime()
    print()
    if fail:
        print(f"FAIL {fail}")
        sys.exit(1)
    print("ALL OK")
