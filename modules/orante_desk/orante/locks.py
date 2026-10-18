# -*- coding: utf-8 -*-
"""跨进程账号锁 + 按优先级排队。

为什么要这个：调度器（scheduler.py）只在自己进程里按账号排队。用户会同时开
好几个 Orante 窗口，每个窗口都是独立进程、独立调度器，于是两个进程可能同时
驱动同一个账号的同一个 Flow 标签页——一个在 page.evaluate，另一个刷新页面，
就报 "Execution context was destroyed"（0907 T0011 就是这么挂的）。

两张表都在共用的 SQLite 里（data/orante.db），所以跨进程有效：

  account_locks    谁正拿着这个账号（一行 = 一个账号）。持有者每 BEAT 秒
                   更新心跳；超过 STALE 秒没动静就认为它死了，可以接管。
  account_waiters  谁在等这个账号（一行 = 一个等着的 Job）。带 prio（旗子
                   序号，小的先跑）和 seq（发号顺序，同号时先到先得）。
                   等着的人每 POLL 秒刷一次心跳和 prio，超过 WAIT_STALE
                   秒没刷就当它走了。

排队规则（这是重点）：
  · 一个账号同一时刻只有一个 Job 在跑，别的排队——不同账号互不影响，并行。
  · 轮到谁，由 (prio, seq) 决定，不是谁先抢到谁跑。抢锁的人只有在自己是
    等待队列的队头时才拿得走锁，所以四个窗口的顺序也是一致的。
  · prio 每轮都重新读任务表：排队时把旗子改小，下一轮（≤2 秒）就插到前面。
  · 已经在跑的不打断。旗子只管排队，不抢占。
"""
from __future__ import annotations

import os
import socket
import threading
import time

from . import db
from .errors import OranteError

OWNER_PORT = 0       # 本窗口端口，main 起服务时 instance.configure 写进来

BEAT = 20.0        # 持有者心跳间隔（秒）
STALE = 90.0       # 持有者心跳这么久没更新 → 认为它已经死了，可以接管
POLL = 1.0        # 排队时多久重试一次（也是等待者的心跳间隔）
WAIT_STALE = 30.0  # 等待者心跳这么久没更新 → 认为它已经走了，从队列里清掉
AUTO_PRIO = 9999   # 「自动」（没插旗子）排在所有插了旗子的后面

_tx = threading.Lock()   # 同进程内串行地做「读-改-写」那一小段


def _now() -> float:
    return time.time()


def _token(job_id: str | None = None) -> str:
    return f"{socket.gethostname()}#{os.getpid()}#{job_id or ''}"


def seq_of(job_id: str | None) -> int:
    """发号顺序。J00098 → 98；db.next_id 全局递增，所以跨进程也能比大小。"""
    try:
        return int(str(job_id or "")[1:])
    except Exception:
        return int(_now())


def norm_prio(v) -> int:
    """0 / None / 负数 = 自动；其余就是旗子上的序号，越小越优先。"""
    try:
        n = int(v)
    except Exception:
        return AUTO_PRIO
    return n if n > 0 else AUTO_PRIO


def task_prio(task_id: str | None) -> int:
    if not task_id:
        return AUTO_PRIO
    try:
        r = db.one("SELECT priority FROM tasks WHERE id=?", (task_id,))
    except Exception:
        return AUTO_PRIO
    return norm_prio((r or {}).get("priority"))


def _write(sql: str, args=()) -> None:
    db.run(sql, args)


# 排队顺序永远现算：旗子从 tasks 表当场读，不认等待行里那份缓存。
# 不然「把排队中的任务提到旗1」要等它自己下一轮心跳（最多 POLL 秒）才算数，
# 期间别人可能已经拿走账号了。priority 为 0/NULL（自动）时退回等待行里的值。
_ORDER_SQL = """
SELECT w.*, COALESCE(NULLIF(t.priority, 0), w.prio, ?) AS eff
  FROM account_waiters w LEFT JOIN tasks t ON t.id = w.task_id
 WHERE w.ts > ? {extra}
 ORDER BY eff, w.seq, w.key
"""


def _fresh_waiters(c, account_id: str, now: float) -> list:
    return c.execute(_ORDER_SQL.format(extra="AND w.account_id = ?"),
                     (AUTO_PRIO, now - WAIT_STALE, account_id)).fetchall()


def _try_take(account_id: str, token: str, job_id: str | None, task_id: str | None,
              prio: int, label: str | None, owner_port: int = 0,
              owner_pid: int = 0) -> dict | None:
    """报到 + 抢一次。

    抢到返回 None；没抢到返回一个 dict，说明为什么：
      {"kind": "held", ...持有者那行}       账号正被别人拿着
      {"kind": "ahead", "ahead": N, ...队头那行}  轮不到我，前面还有 N 个
    """
    def _do():
        c = db.conn()
        now = _now()
        seq = seq_of(job_id)
        with _tx:
            if c.in_transaction:
                c.commit()
            c.execute("BEGIN IMMEDIATE")
            try:
                # ① 报到（每轮刷新 ts 和 prio，所以中途改旗子会立刻生效）
                c.execute(
                    "INSERT OR REPLACE INTO account_waiters"
                    "(key,account_id,job_id,task_id,label,prio,seq,ts) VALUES(?,?,?,?,?,?,?,?)",
                    (token, account_id, job_id, task_id, label, int(prio), seq, now))
                # ② 账号还被别人拿着吗
                r = c.execute("SELECT * FROM account_locks WHERE account_id=?",
                              (account_id,)).fetchone()
                if r is not None and r["owner"] != token and (now - (r["ts"] or 0)) < STALE:
                    out = dict(r)
                    out["kind"] = "held"
                    c.commit()
                    return out
                # ③ 空着——但只有队头能拿走，否则四个窗口就是谁手快谁跑
                q = _fresh_waiters(c, account_id, now)
                head = q[0] if q else None
                if head is not None and head["key"] != token:
                    ahead = 0
                    for w in q:
                        if w["key"] == token:
                            break
                        ahead += 1
                    out = dict(head)
                    out["prio"] = int(out.get("eff") or AUTO_PRIO)
                    out["kind"] = "ahead"
                    out["ahead"] = ahead
                    c.commit()
                    return out
                # ④ 我是队头，拿走；顺手把自己从等待队列里删掉
                c.execute(
                    "INSERT OR REPLACE INTO account_locks"
                    "(account_id,owner,job_id,task_id,prio,ts,owner_port,owner_pid)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (account_id, token, job_id, task_id, int(prio), now,
                     int(owner_port or 0), int(owner_pid or 0)))
                c.execute("DELETE FROM account_waiters WHERE key=?", (token,))
                c.execute("DELETE FROM account_waiters WHERE ts<?", (now - WAIT_STALE * 4,))
                c.commit()
                return None
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass
                raise
    return db.retry_write(_do)


def busy() -> set[str]:
    """当前被别人占着的账号 id（心跳还新鲜的）。给「挑一个空闲账号」用。"""
    out = set()
    try:
        now = _now()
        for r in db.rows("SELECT account_id, ts FROM account_locks"):
            if now - (r.get("ts") or 0) < STALE:
                out.add(r["account_id"])
    except Exception:
        pass
    return out


def holder(account_id: str) -> dict | None:
    try:
        r = db.one("SELECT * FROM account_locks WHERE account_id=?", (account_id,))
    except Exception:
        return None
    if not r or _now() - (r.get("ts") or 0) >= STALE:
        return None
    return r


def drop_job(job_id: str, keep_lock: bool = False) -> None:
    """点停时把这个 Job 的锁和排队位清掉，免得账号行显示空闲其实还被占着。

    keep_lock=True：规格 87 拼单时锁是同组共用的，只清排队位，锁留给还在跑的人。
    """
    if not job_id:
        return
    try:
        if not keep_lock:
            _write("DELETE FROM account_locks WHERE job_id=?", (job_id,))
        _write("DELETE FROM account_waiters WHERE job_id=?", (job_id,))
    except Exception:
        pass


def foreign_waiters(account_id: str) -> int:
    """规格 87：别的 Orante 窗口（别的进程）有几个在排这个账号。

    本窗口拼单时，只要别的窗口有人在等，就不再往组里加人，让这一组跑完把锁交出去，
    别让本窗口一直续着锁把别的窗口饿死。
    """
    mine = f"#{os.getpid()}#"
    try:
        rows = db.rows("SELECT key, ts FROM account_waiters WHERE account_id=?", (account_id,))
    except Exception:
        return 0
    now = _now()
    return sum(1 for r in rows
               if mine not in str(r.get("key") or "") and now - float(r.get("ts") or 0) < WAIT_STALE)


def queue_view() -> dict:
    """给界面用：每个账号「谁在跑 + 谁在排队（已按旗子排好）」。

    这是跨进程的真相——前端 store.jobs 只有本窗口的 Job，四开时不够看。
    返回 {account_id: {"running": {...}|None, "waiting": [{...}, ...]}}
    """
    now = _now()
    out: dict[str, dict] = {}

    def slot(aid: str) -> dict:
        return out.setdefault(aid, {"running": None, "waiting": []})

    try:
        for r in db.rows("SELECT * FROM account_locks"):
            if now - (r.get("ts") or 0) >= STALE:
                continue
            slot(r["account_id"])["running"] = {
                "job_id": r.get("job_id"), "task_id": r.get("task_id"),
                "prio": norm_prio(r.get("prio")), "since": r.get("ts"),
                "owner_port": r.get("owner_port"),
                "owner_pid": r.get("owner_pid"),
            }
        for r in db.rows(_ORDER_SQL.format(extra=""), (AUTO_PRIO, now - WAIT_STALE)):
            slot(r["account_id"])["waiting"].append({
                "job_id": r.get("job_id"), "task_id": r.get("task_id"),
                "label": r.get("label"), "prio": norm_prio(r.get("eff")),
                "seq": r.get("seq"),
            })
    except Exception:
        pass
    return out


def live_holders() -> list[dict]:
    """心跳还新鲜的账号锁持有者。同一账号同时只跑一个生成。"""
    now = _now()
    out = []
    try:
        for r in db.rows("SELECT * FROM account_locks"):
            if now - (r.get("ts") or 0) < STALE:
                out.append(dict(r))
    except Exception:
        pass
    return out


def annotate_accounts(accs: list[dict], my_port: int | None = None) -> list[dict]:
    """给账号列表贴上谁占着。抽屉 / 下拉共用。"""
    q = queue_view()
    mine = int(my_port if my_port is not None else OWNER_PORT or 0)
    my_pid = os.getpid()
    for a in accs:
        run = (q.get(a["id"]) or {}).get("running")
        a["lock"] = run
        if not run:
            a["lock_mine"] = False
            continue
        port = int(run.get("owner_port") or 0)
        pid = int(run.get("owner_pid") or 0)
        a["lock_mine"] = bool((port and port == mine) or (not port and pid and pid == my_pid))
    return accs


class AccountLock:
    """with 或 acquire/release 都行。job 传进来是为了排队时能响应「停止」。

    prio_fn: 每轮回来一次，返回当前旗子序号（默认读任务表，改了立刻生效）。
    """

    def __init__(self, account_id: str, job=None, prio_fn=None,
                 owner_port: int | None = None, owner_pid: int | None = None):
        self.account_id = account_id
        self.job = job
        self.token = _token(getattr(job, "id", None))
        self.prio_fn = prio_fn or (lambda: task_prio(getattr(job, "task_id", None)))
        self.owner_port = int(owner_port if owner_port is not None else OWNER_PORT or 0)
        self.owner_pid = int(owner_pid if owner_pid is not None else os.getpid())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.held = False

    # ---- 内部 ----
    def _log(self, msg: str) -> None:
        fn = getattr(self.job, "log", None)
        if callable(fn):
            try:
                fn(msg)
            except Exception:
                pass

    def _prio(self) -> int:
        try:
            return norm_prio(self.prio_fn())
        except Exception:
            return AUTO_PRIO

    def _flag(self, p: int) -> str:
        return "自动" if p >= AUTO_PRIO else f"旗{p}"

    def _beat_loop(self) -> None:
        while not self._stop.wait(BEAT):
            try:
                _write("UPDATE account_locks SET ts=? WHERE account_id=? AND owner=?",
                       (_now(), self.account_id, self.token))
            except Exception:
                pass

    def _drop_waiter(self) -> None:
        try:
            _write("DELETE FROM account_waiters WHERE key=?", (self.token,))
        except Exception:
            pass

    # ---- 对外 ----
    def acquire(self, wait: bool = True, timeout: float | None = None) -> bool:
        t0 = _now()
        said = ""
        while True:
            p = self._prio()
            h = _try_take(self.account_id, self.token,
                          getattr(self.job, "id", None),
                          getattr(self.job, "task_id", None), p,
                          getattr(self.job, "kind", None),
                          self.owner_port, self.owner_pid)
            if h is None:
                self.held = True
                self._thread = threading.Thread(target=self._beat_loop, daemon=True)
                self._thread.start()
                if said:
                    self._log(f"  · 轮到我了（{self._flag(p)}），账号已空出来")
                return True
            who = h.get("task_id") or h.get("job_id") or h.get("owner")
            if not wait:
                self._drop_waiter()
                raise OranteError(f"这个账号正被 {who} 占着",
                                  "等它跑完，或把这个任务换到别的账号。")
            if timeout is not None and _now() - t0 > timeout:
                self._drop_waiter()
                raise OranteError(f"等了 {int(_now() - t0)} 秒，账号还被 {who} 占着",
                                  "等它跑完，或把这个任务换到别的账号。")
            # 队形变了才再说一次，别把日志刷满
            if h.get("kind") == "held":
                msg = f"  · 账号被 {who} 占着，排队等它跑完…（我是 {self._flag(p)}）"
            else:
                n = int(h.get("ahead") or 0)
                msg = (f"  · 排队中：前面还有 {n} 个，队头是 {who}"
                       f"（{self._flag(norm_prio(h.get('prio')))}）；我是 {self._flag(p)}")
            if msg != said:
                self._log(msg)
                said = msg
            chk = getattr(self.job, "check", None)
            try:
                if callable(chk):
                    chk()                  # 排队时也能被「停止」打断
            except Exception:
                self._drop_waiter()        # 被取消了，别把队伍堵着
                raise
            time.sleep(POLL)

    def release(self) -> None:
        self._stop.set()
        self._drop_waiter()
        if not self.held:
            return
        self.held = False
        try:
            _write("DELETE FROM account_locks WHERE account_id=? AND owner=?",
                   (self.account_id, self.token))
        except Exception:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()
