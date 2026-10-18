# -*- coding: utf-8 -*-
"""规格 25：任务状态单调性 + 生成面板兜底 + 卡住 Job。"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = "--live" in sys.argv

if not LIVE:
    TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-status-"))
    os.environ["ORANTE_DATA"] = str(TEST_DATA)

sys.path.insert(0, str(ROOT))

from orante import db, paths  # noqa: E402
from orante import tasks as taskmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.scheduler import scheduler, stuck_jobs  # noqa: E402

db.init()
fail = 0


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    global fail
    fail += 1
    print("  FAIL", msg)


def captured(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        ret = fn()
    return ret, buf.getvalue()


def _mk_task(tid: str, status: str, acc="A001"):
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,status,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "测", "flow", acc, "short", "hello", "x", status, 0, db.now(), db.now()),
    )


def _mk_manifest(d: Path, tid: str, images=True, n=3):
    d.mkdir(parents=True, exist_ok=True)
    m = Manifest.new(tid, "测", "flow", "A001", "short", "hello")
    m.images = []
    m.segments = []
    for i in range(1, n + 1):
        p = "p%02d" % i
        m.images.append({
            "p": p, "file": f"images/{p}.jpg", "role": "主体",
            "status": "ok", "desc": "有了", "sha1": "",
        })
        m.segments.append({
            "s": i, "text": "x", "duration": 6,
            "image": p if images else "",
            "role": "主体", "status": "planned",
        })
    m.save(d)
    return m


def test_monotonic_isolated():
    print("== set_status 单调性 ==")
    _mk_task("T9A", "arranged")
    got, log = captured(lambda: taskmod.set_status("T9A", "described"))
    if got == "arranged" and taskmod.get("T9A")["status"] == "arranged":
        ok("arranged ← described 不写")
    else:
        bad(f"应停在 arranged，实际 {got!r} / {taskmod.get('T9A')['status']}")
    if "跳过状态回退 T9A arranged ← described" in log:
        ok("日志 跳过状态回退")
    else:
        bad(f"没看到跳过日志：{log!r}")

    got, log = captured(lambda: taskmod.set_status("T9A", "generating"))
    if got == "generating":
        ok("arranged → generating 前进")
    else:
        bad(f"前进失败 {got!r}")

    got, log = captured(lambda: taskmod.set_status("T9A", "arranged", force=True))
    if got == "arranged" and taskmod.get("T9A")["status"] == "arranged":
        ok("force 拉回 arranged")
    else:
        bad(f"force 失败 {got!r}")
    if "状态回退 T9A generating → arranged（force）" in log:
        ok("日志 状态回退 …（force）")
    else:
        bad(f"没看到 force 日志：{log!r}")

    _mk_task("T9B", "collected")
    got, _ = captured(lambda: taskmod.set_status("T9B", "trash"))
    if got == "trash" and taskmod.get("T9B")["status"] == "trash":
        ok("collected → trash 不受单调性挡")
    else:
        bad(f"进回收站失败 {got!r}")

    got, log = captured(lambda: taskmod.set_status("T9B", "arranged", force=True))
    if got == "arranged":
        ok("trash → arranged force 恢复")
    else:
        bad(f"恢复失败 {got!r}")
    if "状态回退 T9B trash → arranged（force）" in log:
        ok("恢复日志 状态回退")
    else:
        bad(f"恢复日志不对：{log!r}")

    try:
        taskmod.set_status("T9A", "nope")
        bad("未知状态没报错")
    except OranteError:
        ok("未知状态报错")

    print("\n== 补活儿不改更高状态 ==")
    from orante.services.describe import _mark_described
    d = Path(os.environ["ORANTE_DATA"]) / "T9A"
    m = _mk_manifest(d, "T9A", images=True)
    m.images = [{"p": "p01", "file": "images/p01.jpg", "role": "主体", "desc": "有了", "status": "ok"}]
    _mk_task("T9A", "arranged")
    db.run("UPDATE tasks SET dir=? WHERE id=?", (str(d), "T9A"))
    _mark_described("T9A", m)
    if taskmod.get("T9A")["status"] == "arranged":
        ok("_mark_described 不会把 arranged 拽回 described")
    else:
        bad(f"_mark_described 改成了 {taskmod.get('T9A')['status']}")

    print("\n== images_ready 兜底 ==")
    _mk_task("T9C", "described")
    d2 = Path(os.environ["ORANTE_DATA"]) / "T9C"
    _mk_manifest(d2, "T9C", images=True, n=2)
    db.run("UPDATE tasks SET dir=? WHERE id=?", (str(d2), "T9C"))
    t = [x for x in taskmod.list_all() if x["id"] == "T9C"][0]
    if t["counts"] and t["counts"].get("images_ready"):
        ok("described + 每段有图 → counts.images_ready")
    else:
        bad(f"images_ready 应为 true：{t.get('counts')}")

    _mk_task("T9D", "prepared")
    d3 = Path(os.environ["ORANTE_DATA"]) / "T9D"
    _mk_manifest(d3, "T9D", images=False, n=2)
    db.run("UPDATE tasks SET dir=? WHERE id=?", (str(d3), "T9D"))
    t = [x for x in taskmod.list_all() if x["id"] == "T9D"][0]
    if t["counts"] and not t["counts"].get("images_ready"):
        ok("prepared 没配图 → images_ready false")
    else:
        bad(f"没配图不该 ready：{t.get('counts')}")

    print("\n== 卡住 Job ==")
    jid = "J9STUK"
    logp = paths.LOGS / f"{jid}.log"
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    logp.write_text("[00:00:00] hung expand\n", encoding="utf-8")
    old = time.time() - 23 * 60
    os.utime(logp, (old, old))
    db.run("DELETE FROM jobs WHERE id=?", (jid,))
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,log) VALUES(?,?,?,?,?,?)",
        (jid, "T9A", "expand", "running", "2026-09-09 00:00:00", str(logp)),
    )
    hits = [s for s in stuck_jobs() if s["job_id"] == jid]
    if hits and hits[0]["kind"] == "expand" and hits[0]["idle_min"] >= 10:
        ok(f"卡住 {jid} expand {hits[0]['idle_min']}分钟")
    else:
        bad(f"没识别卡住：{hits}")
    scheduler.cancel(jid)
    row = db.one("SELECT status, error FROM jobs WHERE id=?", (jid,))
    if row and row["status"] == "cancelled":
        ok("点停僵尸 Job → cancelled")
    else:
        bad(f"点停失败 {row}")
    db.run("DELETE FROM jobs WHERE id=?", (jid,))
    try:
        logp.unlink()
    except Exception:
        pass

    print("\n== 重生 force 拉回 arranged ==")
    src = (ROOT / "orante" / "services" / "generate.py").read_text(encoding="utf-8")
    if 'set_status(job.task_id, "arranged", force=True)' in src:
        ok("regenerate 带 force=True 拉回 arranged")
    else:
        bad("regenerate 没接 force=True")

    print("\n== 前端兜底 ==")
    js = (ROOT / "ui" / "js" / "panels" / "generate.js").read_text(encoding="utf-8")
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    checks = [
        ("isStaleTrack", "isStaleTrack" in js),
        ("ACTIVE_ST 仍只含三档", "ACTIVE_ST.has(t.status) || isStaleTrack(t)" in js),
        ("去编排按钮", "gen.stale_track.go" in js and "goTrack" in js),
        ("卡住可点停", "stopStuck" in js and "g-stuck" in js),
        ("stale 不给生成", "if (isStaleTrack(task)) return false" in js),
        ("勾选 stale 不点亮生成选中", "canStartSel" in js),
    ]
    for name, cond in checks:
        (ok if cond else bad)(name)
    for k, needle in [
        ("gen.stale_track", "配图已齐，但状态还停在"),
        ("gen.stale_track.go", "去编排"),
        ("gen.lane.stuck", "卡住"),
    ]:
        if k in zh and needle in zh[k]:
            ok(f"i18n {k}")
        else:
            bad(f"i18n {k} = {zh.get(k)!r}")


def test_live():
    print("== 现场 T0014 ==")
    t = db.one("SELECT * FROM tasks WHERE id=?", ("T0014",))
    if not t:
        bad("库里没有 T0014")
        return
    orig = t["status"]
    ok(f"T0014 当前 status={orig} dir={t.get('dir')}")
    # 先保证在 arranged，再模拟补跑 describe
    if orig != "arranged":
        captured(lambda: taskmod.set_status("T0014", "arranged", force=True))
        ok(f"先 force 到 arranged（原先 {orig}）")
    got, log = captured(lambda: taskmod.set_status("T0014", "described"))
    if got == "arranged" and taskmod.get("T0014")["status"] == "arranged":
        ok("describe 不会拽回 described")
    else:
        bad(f"T0014 被改成 {got}")
    if "跳过状态回退 T0014 arranged ← described" in log:
        ok("现场日志 跳过状态回退")
    else:
        bad(f"现场没跳过日志：{log!r}")

    listed = [x for x in taskmod.list_all() if x["id"] == "T0014"]
    if listed and listed[0]["status"] == "arranged":
        ok("T0014 在任务列表，status=arranged → ④ 进行中能勾选生成")
    else:
        bad("T0014 没在 list_all")
    if listed and listed[0].get("counts") and listed[0]["counts"].get("images_ready"):
        ok("T0014 images_ready")
    else:
        bad(f"T0014 counts={listed[0].get('counts') if listed else None}")

    # 验收 3：手工改回 described，仍应被兜底列出
    db.run("UPDATE tasks SET status='described', updated=? WHERE id=?", (db.now(), "T0014"))
    listed = [x for x in taskmod.list_all() if x["id"] == "T0014"]
    c = (listed[0].get("counts") or {}) if listed else {}
    if listed and listed[0]["status"] == "described" and c.get("images_ready"):
        ok("手工 described 仍 images_ready → 生成面板兜底列出、无生成按钮")
    else:
        bad(f"兜底失败 status={listed[0]['status'] if listed else None} counts={c}")
    # 改回进场时的状态，别把用户的任务留在 described / arranged
    captured(lambda: taskmod.set_status("T0014", orig, force=True))
    if taskmod.get("T0014")["status"] == orig:
        ok(f"验完恢复 T0014 {orig}")
    else:
        bad(f"没能恢复 {orig}，现在是 {taskmod.get('T0014')['status']}")

    print("\n== 现场 打回 / 回收（用临时行，不动别人的任务）==")
    _mk_task("T9LIVE", "collected", acc=t.get("account_id") or "A001")
    got, log = captured(lambda: taskmod.set_status("T9LIVE", "arranged", force=True))
    if got == "arranged" and "状态回退 T9LIVE collected → arranged（force）" in log:
        ok("打回 arranged + force 日志")
    else:
        bad(f"打回失败 {got!r} {log!r}")
    got, _ = captured(lambda: taskmod.set_status("T9LIVE", "trash"))
    if got == "trash":
        ok("进回收站")
    else:
        bad(f"回收失败 {got!r}")
    db.run("DELETE FROM tasks WHERE id=?", ("T9LIVE",))

    print("\n== 现场假卡住 Job ==")
    jid = "J9STUK"
    logp = paths.LOGS / f"{jid}.log"
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    logp.write_text("[00:00:00] hung expand\n", encoding="utf-8")
    old_ts = time.time() - 23 * 60
    os.utime(logp, (old_ts, old_ts))
    db.run("DELETE FROM jobs WHERE id=?", (jid,))
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,log) VALUES(?,?,?,?,?,?)",
        (jid, "T0014", "expand", "running", "2026-09-09 00:00:00", str(logp)),
    )
    acc = t.get("account_id") or ""
    hits = [s for s in stuck_jobs() if s["job_id"] == jid]
    if hits:
        ok(f"卡住 {hits[0]['job_id']} {hits[0]['kind']} {hits[0]['idle_min']}分钟 account={hits[0].get('account_id')}")
    else:
        bad("现场没识别卡住 Job")
    scheduler.cancel(jid)
    row = db.one("SELECT status FROM jobs WHERE id=?", (jid,))
    if row and row["status"] == "cancelled":
        ok("点停成功")
    else:
        bad(f"点停失败 {row}")
    db.run("DELETE FROM jobs WHERE id=?", (jid,))
    try:
        logp.unlink()
    except Exception:
        pass


def main():
    print("规格 25 验收", "(live)" if LIVE else "(isolated)")
    if LIVE:
        test_live()
    else:
        test_monotonic_isolated()
    if fail:
        print(f"\nFAIL x{fail}")
        sys.exit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
