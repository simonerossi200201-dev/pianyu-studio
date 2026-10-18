# -*- coding: utf-8 -*-
"""优先级队列自检（规格 19）。

在一个临时数据库里模拟：同账号按旗子排队、不同账号并行、排队中改旗子插队。
不碰真实的 data\orante.db，也不开 Chrome。双击 tools\5-Prio-Test.bat 就跑。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="orante-prio-"))
os.environ["ORANTE_DATA"] = str(TMP)
sys.path.insert(0, str(ROOT))

from orante import db, locks  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}\n      实际 {got}\n      期望 {want}")
    if not ok:
        FAILED.append(name)


def reset(rows):
    db.init()
    for tb in ("account_locks", "account_waiters", "tasks"):
        db.run(f"DELETE FROM {tb}")
    for tid, acc, prio in rows:
        db.run("INSERT INTO tasks(id,name,account_id,priority,status)"
               " VALUES(?,?,?,?,'arranged')", (tid, tid, acc, prio))


class FakeJob:
    def __init__(self, jid, tid):
        self.id, self.task_id, self.kind = jid, tid, "generate"

    def log(self, m):
        pass

    def check(self):
        pass


def test_order_and_parallel():
    print("\n[1] 同账号按旗子排队 / 不同账号并行")
    plan = [("T01", "A", 3), ("T02", "A", 0), ("T03", "A", 1), ("T04", "A", 1),
            ("T05", "B", 1), ("T06", "B", 1)]
    reset(plan)
    order, live, peak = [], {}, {"A": 0, "B": 0, "ALL": 0}
    mu = threading.Lock()

    def run(jid, tid, acc, delay):
        time.sleep(delay)
        L = locks.AccountLock(acc, FakeJob(jid, tid))
        L.acquire()
        with mu:
            order.append((acc, tid))
            live[acc] = live.get(acc, 0) + 1
            peak[acc] = max(peak[acc], live[acc])
            peak["ALL"] = max(peak["ALL"], sum(live.values()))
        time.sleep(0.8)
        with mu:
            live[acc] -= 1
        L.release()

    ths = [threading.Thread(target=run, args=(f"J{i + 1:05d}", tid, acc, 0.1 * i))
           for i, (tid, acc, _p) in enumerate(plan)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    # T01 最先进场时账号空着、队里没别人，它直接跑（不能让空账号干等还没来的任务）；
    # 之后队伍站满，严格按 (旗子, 发号)：旗1 的 T03、T04，再自动的 T02。
    check("A 账号执行顺序", [t for a, t in order if a == "A"], ["T01", "T03", "T04", "T02"])
    check("B 账号执行顺序", [t for a, t in order if a == "B"], ["T05", "T06"])
    check("同账号最大并发", max(peak["A"], peak["B"]), 1)
    check("两账号并行", peak["ALL"], 2)
    check("跑完没留下锁", db.rows("SELECT * FROM account_locks"), [])
    check("跑完没留下等待行", db.rows("SELECT * FROM account_waiters"), [])


def test_multiprocess_and_bump():
    print("\n[2] 四个进程（多开窗口）+ 排队中途改旗子插队")
    plan = [("T01", "A", 5), ("T02", "A", 5), ("T03", "A", 5), ("T04", "A", 5)]
    reset(plan)
    child = ROOT / "tools" / "prio_test_child.py"
    procs = []
    for i, (tid, _acc, _p) in enumerate(plan):
        procs.append(subprocess.Popen(
            [sys.executable, str(child), f"J{i + 1:05d}", tid, "A", "1.5", str(TMP), str(ROOT)],
            stdout=subprocess.PIPE, text=True))
        time.sleep(0.25)
    time.sleep(1.0)
    db.run("UPDATE tasks SET priority=1 WHERE id='T04'")
    print("      （此刻 T01 在跑，T02/T03/T04 排队；把最后进场的 T04 改成 旗1）")
    head = [w["task_id"] for w in locks.queue_view().get("A", {}).get("waiting", [])]
    check("改完立刻看队形，T04 到队头", head[:1], ["T04"])
    got = []
    for p in procs:
        out, _ = p.communicate()
        got.append(json.loads(out.strip().splitlines()[-1]))
    got.sort(key=lambda r: r["t"])
    check("实际执行顺序（T01 没被打断）", [r["task"] for r in got], ["T01", "T04", "T02", "T03"])


if __name__ == "__main__":
    print("优先级队列自检 · 临时库", TMP)
    test_order_and_parallel()
    test_multiprocess_and_bump()
    print("\n" + ("全部通过 ✓" if not FAILED else "有失败：" + "、".join(FAILED)))
    sys.exit(0 if not FAILED else 1)
