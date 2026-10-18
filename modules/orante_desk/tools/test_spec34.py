# -*- coding: utf-8 -*-
"""规格 34：生成面板折叠行 counts 实时更新 / 缓存 / seg.changed 节流。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec34-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, events, tasks as taskmod  # noqa: E402
from orante.models import Manifest  # noqa: E402

db.init()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def _acc():
    if db.one("SELECT 1 FROM accounts WHERE id=?", ("A001",)):
        return
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A001", "t-flow", "flow", 9398, "A001", "", "", "9:16", 5, 1, "{}", db.now()),
    )


def _mk_manifest(tdir: Path, tid: str, n: int, collected: int = 0, submitted: int = 0) -> Manifest:
    tdir.mkdir(parents=True, exist_ok=True)
    m = Manifest.new(tid, tid, "flow", "t-flow", "short", "文案" + tid)
    m.images = [{
        "p": "p01", "file": "images/p01.jpg", "sha1": "x", "role": "主体",
        "status": "ok", "desc": "d", "voice": "", "eye": "闭眼", "scene": "常规",
        "measure": {}, "origin": "download",
    }]
    segs = []
    for i in range(1, n + 1):
        if i <= collected:
            st = "collected"
        elif i <= collected + submitted:
            st = "submitted"
        else:
            st = "planned"
        segs.append({
            "s": i, "text": f"段{i} " + ("文案" * 8), "duration": 6, "image": "p01", "role": "主体",
            "shot": "【镜头】中景正面缓慢推进",
            "prompt_final": ("成品提示词 " + "描述语" * 40)[:400],
            "status": st, "gen_id": None,
            "takes": [], "error": None,
        })
    m.segments = segs
    m.save(tdir)
    return m


def _mk_task(tid: str, tdir: Path, status="arranged", n=9, collected=0, submitted=0):
    _acc()
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    _mk_manifest(tdir, tid, n, collected, submitted)
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,status,dir,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, tid, "flow", "A001", "short", "文案", "x", status, str(tdir), 0, db.now(), db.now()),
    )


def test_counts_shape():
    print("== counts 形状 ==")
    tdir = TEST_DATA / "ws" / "T34A"
    _mk_task("T34A", tdir, n=9, collected=3, submitted=2)
    taskmod.clear_counts_cache()
    rows = taskmod.list_all()
    hit = next((x for x in rows if x["id"] == "T34A"), None)
    if not hit:
        bad("list_all 没有 T34A")
        return
    c = hit.get("counts") or {}
    need = ("planned", "submitted", "collected", "failed", "blocked", "total", "images_ready")
    if all(k in c for k in need):
        ok("counts 含 " + ",".join(need))
    else:
        bad("counts 缺字段 " + str(c))
    if c.get("collected") == 3 and c.get("submitted") == 2 and c.get("planned") == 4 and c.get("total") == 9:
        ok("9 段 3 收 2 提交 4 待做")
    else:
        bad("计数不对 " + str(c))
    if c.get("images_ready") is True:
        ok("images_ready")
    else:
        bad("images_ready=" + repr(c.get("images_ready")))
    c2 = taskmod.counts_of("T34A")
    if c2 and c2.get("collected") == 3:
        ok("counts_of 一致")
    else:
        bad("counts_of " + str(c2))


def test_cache():
    print("== (size,mtime) 缓存 ==")
    ws = TEST_DATA / "ws20"
    taskmod.clear_counts_cache()
    for i in range(20):
        tid = f"T3{i:03d}"
        _mk_task(tid, ws / tid, n=40, collected=i, submitted=2)
    taskmod.clear_counts_cache()
    t0 = time.perf_counter()
    a = taskmod.list_all()
    cold = (time.perf_counter() - t0) * 1000
    n = len([x for x in a if (x.get("counts") or {}).get("total") == 40])
    if n >= 20:
        ok(f"冷读 20 个任务都有 counts（{cold:.1f} ms）")
    else:
        bad(f"冷读只有 {n} 个带 40 段 counts")
    t1 = time.perf_counter()
    b = taskmod.list_all()
    warm = (time.perf_counter() - t1) * 1000
    if warm < cold * 0.8 or warm < 30:
        ok(f"热读走缓存 {warm:.1f} ms（冷读 {cold:.1f} ms）")
    else:
        bad(f"热读没有明显变快：冷 {cold:.1f} 热 {warm:.1f}")
    # 改一个文件，缓存应按 mtime 失效
    tdir = ws / "T3000"
    m = Manifest.load(tdir)
    m.segments[0]["status"] = "collected"
    m.save(tdir)
    c = taskmod.counts_of("T3000")
    # T3000 原来 collected=0 submitted=2 → 现在 collected 至少 1（第一段刚改）
    if c and c.get("collected") >= 1:
        ok("mtime 变了缓存失效，读到新 counts")
    else:
        bad("改盘后 counts 还是旧的 " + str(c))
    return {"cold_ms": cold, "warm_ms": warm, "n": len(b)}


def test_throttle():
    print("== seg.changed 节流 ==")
    got = []

    def _grab(kind, **data):
        got.append((kind, data))

    orig = events.emit
    events.emit = _grab
    try:
        taskmod._seg_last.clear()
        taskmod._seg_timer.clear()
        taskmod._seg_payload.clear()
        c = {"planned": 5, "submitted": 1, "collected": 3, "failed": 0, "blocked": 0, "total": 9, "images_ready": True}
        for i in range(5):
            cc = dict(c)
            cc["collected"] = 3 + i
            cc["planned"] = 5 - i
            taskmod.emit_seg_changed("T34A", cc)
        time.sleep(0.05)
        n0 = sum(1 for k, _ in got if k == "seg.changed")
        if n0 == 1:
            ok("500ms 内连发 5 次只出去 1 条")
        else:
            bad(f"500ms 内发出 {n0} 条，期望 1")
        time.sleep(0.55)
        n1 = sum(1 for k, _ in got if k == "seg.changed")
        if n1 == 2:
            ok("trailing 补发最新一条")
        else:
            bad(f"等 500ms 后共 {n1} 条，期望 2")
        last = [d for k, d in got if k == "seg.changed"][-1]
        if last.get("counts", {}).get("collected") == 7:
            ok("trailing 带的是最后一次 counts")
        else:
            bad("trailing counts=" + str(last.get("counts")))
        taskmod.emit_seg_changed("T34A", dict(c, collected=9, planned=0), force=True)
        n2 = sum(1 for k, _ in got if k == "seg.changed")
        if n2 == 3:
            ok("force=True 立刻再发一条")
        else:
            bad(f"force 后共 {n2} 条，期望 3")
        kinds = [k for k, _ in got]
        if "task.update" not in kinds:
            ok("节流通道只发 seg.changed，不整页 tasks.changed")
        else:
            bad("节流通道不该发 task.update")
    finally:
        events.emit = orig


def test_static():
    print("== 源码约定 ==")
    gen = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    store = (ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")
    py = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    sch = (ROOT / "orante" / "scheduler.py").read_text(encoding="utf-8")
    srv = (ROOT / "orante" / "server.py").read_text(encoding="utf-8")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    checks = [
        ("loadManifest 不再自己算 counts", "hit.counts = c" not in gen),
        ("折叠行用 counts", "statusesFromCounts(countsOf(task))" in gen),
        ("只轮询 running", "runningTaskIds()" in gen and "fetchCountsSilent" in gen),
        ("Job 结束拉 counts", "wasRun && nowEnd" in gen),
        ("90 秒没动静", "isIdle" in gen and "gen.idle_warn" in gen),
        ("细进度条", "g-thin" in gen and "runPct" in gen),
        ("store 听 seg.changed", "seg.changed" in store),
        ("store 就地 applyTaskCounts", "applyTaskCounts" in store),
        ("segAt 只在 counts 变了才刷新", "if (changed) store.segAt[tid]" in store),
        ("没动静从本轮 running 起算", "Math.max(runAt, segAt)" in gen),
        ("SSE 重连 refreshTasks", "reopen && store.loaded" in store),
        ("generate.py emit_seg_changed", "emit_seg_changed" in py),
        ("generate.py 收尾 force", "force=True" in py),
        ("scheduler 收尾 seg.changed", "emit_seg_changed" in sch),
        ("/api/tasks/{tid}/counts", "/api/tasks/{tid}/counts" in srv),
        ("呼吸动画", "g-seg-breathe" in css),
        ("i18n idle", zh.get("gen.idle_warn") == "90 秒没动静"),
    ]
    for name, cond in checks:
        (ok if cond else bad)(name)
    # 没在跑时不应再按 expanded 轮询 manifest
    if "Object.keys(expanded).forEach" in gen and "loadManifest(id)" in gen:
        # 允许展开+running 时拉 manifest，但不该是唯一条件
        if "runningTaskIds()" in gen:
            ok("轮询入口是 running 任务，不是 expanded")
        else:
            bad("还在按 expanded 轮询")
    else:
        ok("不再按 expanded 无条件轮询 manifest")


def test_http():
    print("== HTTP /api/tasks/{id}/counts ==")
    tdir = TEST_DATA / "ws" / "T34H"
    _mk_task("T34H", tdir, n=9, collected=4, submitted=1)
    taskmod.clear_counts_cache()
    try:
        from orante.server import app, task_counts, task_list
    except Exception as e:
        bad("import server " + str(e))
        return
    paths = [getattr(r, "path", "") for r in app.routes]
    if "/api/tasks/{tid}/counts" in paths:
        ok("路由挂了 /api/tasks/{tid}/counts")
    else:
        bad("路由表没有 counts: " + str([p for p in paths if "tasks" in p][:12]))
    body = task_counts("T34H")
    if body.get("ok") and (body.get("counts") or {}).get("collected") == 4:
        ok("task_counts collected=4")
    else:
        bad("task_counts body " + str(body))
    listed = task_list(all=0)
    tasks = (listed or {}).get("tasks") or []
    hit = next((x for x in tasks if x["id"] == "T34H"), None)
    if hit and (hit.get("counts") or {}).get("total") == 9:
        ok("task_list 带 counts")
    else:
        bad("list 没有 counts")


if __name__ == "__main__":
    print("规格 34 验收")
    test_counts_shape()
    timing = test_cache()
    test_throttle()
    test_static()
    test_http()
    print()
    if timing:
        print(f"  /api/tasks 20 任务：冷读 {timing['cold_ms']:.1f} ms，热读 {timing['warm_ms']:.1f} ms")
    if fail:
        print(f"失败 {fail} 项")
        sys.exit(1)
    print("全部通过")
