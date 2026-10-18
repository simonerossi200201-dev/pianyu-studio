# -*- coding: utf-8 -*-
"""规格 33：任务表 reorder / 批量删除 / 撤销。磁盘文件一个都不许少。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec33-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante import tasks as taskmod  # noqa: E402

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


def _mk(tid, name, status="draft", d=None, order=0):
    _acc()
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,status,dir,row_order,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, "flow", "A001", "short", "文案" + name, "x", status, d, order, db.now(), db.now()),
    )


def test_reorder():
    print("== reorder ==")
    for i, tid in enumerate(("T0014", "T0011", "T0012", "T0013")):
        _mk(tid, tid, "draft", order=i)
    taskmod.reorder(["T0014", "T0011", "T0012", "T0013"])
    ids = [t["id"] for t in taskmod.list_all()]
    if ids == ["T0014", "T0011", "T0012", "T0013"]:
        ok("initial order T0014 T0011 T0012 T0013")
    else:
        bad("initial order " + str(ids))
    taskmod.reorder(["T0011", "T0013", "T0014", "T0012"])
    ids = [t["id"] for t in taskmod.list_all()]
    if ids == ["T0011", "T0013", "T0014", "T0012"]:
        ok("reordered to T0011 T0013 T0014 T0012")
    else:
        bad("reordered " + str(ids))
    # 不在名单里的回收任务不动位置语义：过滤后仍按新顺序
    _mk("T0099", "trashme", "trash", order=99)
    taskmod.reorder(["T0014", "T0012", "T0011", "T0013"])
    ids = [t["id"] for t in taskmod.list_all()]
    if ids == ["T0014", "T0012", "T0011", "T0013"]:
        ok("trash excluded from list_all, visible order kept")
    else:
        bad("with trash " + str(ids))
    t99 = db.one("SELECT status, row_order FROM tasks WHERE id=?", ("T0099",))
    if t99 and t99["status"] == "trash":
        ok("trash row still in db")
    else:
        bad("trash row missing")


def test_batch_delete_and_disk():
    print("== batch-delete / disk / undelete ==")
    work = Path(tempfile.mkdtemp(prefix="orante-t33-disk-"))
    d11 = work / "T0011-开工"
    d12 = work / "T0012-开工"
    d16 = work / "T0016-在跑"
    for d in (d11, d12, d16):
        d.mkdir(parents=True, exist_ok=True)
        (d / "copy.txt").write_text("keep-me", encoding="utf-8")
        (d / "images").mkdir(exist_ok=True)
        (d / "images" / "p01.jpg").write_bytes(b"\xff\xd8\xff\xd9xx")

    def snap_disk():
        out = {}
        for p in work.rglob("*"):
            if p.is_file():
                out[str(p.relative_to(work)).replace("\\", "/")] = p.read_bytes()
        return out

    before = snap_disk()

    _mk("T0001", "草稿甲")
    _mk("T0002", "草稿乙")
    _mk("T0011", "开工甲", "prepared", str(d11), 10)
    _mk("T0012", "开工乙", "collected", str(d12), 11)
    _mk("T0016", "在跑", "generating", str(d16), 12)
    db.run("DELETE FROM jobs WHERE id=?", ("Jrun",))
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,log) VALUES(?,?,?,?,?,?)",
        ("Jrun", "T0016", "generate", "running", db.now(), ""),
    )

    res = taskmod.batch_delete(
        ["T0001", "T0002", "T0011", "T0016"],
        ["T0012"],
    )
    if sorted(res["hard"]) == ["T0001", "T0002"]:
        ok("drafts hard-deleted")
    else:
        bad("hard " + str(res["hard"]))
    if sorted(res["trash"]) == ["T0011", "T0012"]:
        ok("started moved to trash (T0011 was sent as hard but has dir)")
    else:
        bad("trash " + str(res["trash"]))
    if res["blocked"] == ["T0016"]:
        ok("running T0016 blocked")
    else:
        bad("blocked " + str(res["blocked"]))

    if db.one("SELECT 1 FROM tasks WHERE id=?", ("T0001",)):
        bad("T0001 still in db")
    else:
        ok("T0001 gone from db")
    t11 = db.one("SELECT status, dir FROM tasks WHERE id=?", ("T0011",))
    if t11 and t11["status"] == "trash" and t11["dir"] == str(d11):
        ok("T0011 trash, dir kept")
    else:
        bad("T0011 " + str(t11))
    t16 = db.one("SELECT status FROM tasks WHERE id=?", ("T0016",))
    if t16 and t16["status"] == "generating":
        ok("T0016 still generating")
    else:
        bad("T0016 " + str(t16))

    after = snap_disk()
    if before == after:
        ok("disk files identical after batch-delete")
    else:
        missing = set(before) - set(after)
        extra = set(after) - set(before)
        bad(f"disk changed missing={missing} extra={extra}")

    und = taskmod.undelete(
        [{"id": "T0001", "name": "草稿甲", "copy": "文案草稿甲", "image_source": "x",
          "type": "short", "account": "t-flow", "status": "draft"}],
        [{"id": "T0011", "status": "prepared"}, {"id": "T0012", "status": "collected"}],
    )
    if "T0001" in und["restored"]:
        ok("T0001 restored")
    else:
        bad("restored " + str(und))
    t1 = db.one("SELECT status, name FROM tasks WHERE id=?", ("T0001",))
    if t1 and t1["status"] == "draft" and t1["name"] == "草稿甲":
        ok("T0001 draft again")
    else:
        bad("T0001 after undelete " + str(t1))
    t11 = db.one("SELECT status FROM tasks WHERE id=?", ("T0011",))
    t12 = db.one("SELECT status FROM tasks WHERE id=?", ("T0012",))
    if t11 and t11["status"] == "prepared" and t12 and t12["status"] == "collected":
        ok("trashed tasks restored to original status")
    else:
        bad(f"status T0011={t11} T0012={t12}")
    after2 = snap_disk()
    if before == after2:
        ok("disk still identical after undelete")
    else:
        bad("disk changed on undelete")


def main() -> int:
    test_reorder()
    test_batch_delete_and_disk()
    print()
    if fail:
        print(f"FAILED  {fail} 项")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
