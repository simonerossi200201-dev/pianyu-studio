from __future__ import annotations
import os
# -*- coding: utf-8 -*-
"""调度器：每个账号一条队列、一次只跑一个 Job；不同账号并行。

Job 是一个 Python 可调用 step 序列（见 jobs.py 里的各种 kind），
每一步之间检查取消；日志逐行落盘 + 事件总线。
"""

import threading
import traceback
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from . import db, events, locks, paths
from .errors import CreditHalted, OranteError

# 生成面板「卡住」：running 这么久没新日志，账号行从「空闲」改成可点停。
STUCK_IDLE = 10 * 60


class Job:
    def __init__(self, job_id: str, task_id: str | None, kind: str, account_id: str | None, fn, args: dict):
        self.id = job_id
        self.task_id = task_id
        self.kind = kind
        self.account_id = account_id      # None = 不占账号（纯本地计算）
        self.fn = fn
        self.args = args
        self.status = "queued"
        self.cancelled = threading.Event()
        self.log_path = paths.LOGS / f"{job_id}.log"
        self._fh = None
        self.error = None
        self.result = None
        self.credit_halt = None
        self.credit_continue = False
        self.credit_b0 = None
        self.flow_throttle = None

    # 供 step 用
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()
        events.emit("job.log", job_id=self.id, task_id=self.task_id, line=line)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise JobCancelled()

    def sleep(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            self.check()
            time.sleep(min(0.5, end - time.time()))

    # 旗子序号（小的先跑）。放在任务表上，所以排队中改旗子，下一轮就生效。
    @property
    def prio(self) -> int:
        return locks.task_prio(self.task_id)

    @property
    def seq(self) -> int:
        return locks.seq_of(self.id)

    def to_dict(self) -> dict:
        return {"id": self.id, "task_id": self.task_id, "kind": self.kind, "account_id": self.account_id,
                "status": self.status, "error": self.error, "log": str(self.log_path), "result": self.result,
                "prio": self.prio, "credit_halt": self.credit_halt,
                "flow_throttle": self.flow_throttle}


class JobCancelled(Exception):
    pass


class Scheduler:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.queues: dict[str, deque] = defaultdict(deque)   # account_id('' = 本地) -> jobs
        self.running: dict[str, Job] = {}                    # account_id -> job；拼单加入的是 "account_id|job_id"
        # 规格 87：同账号拼单。account_id -> {"host", "members", "lock", "held"}
        self.groups: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._local_workers = 3                              # 本地计算类可并行几条
        self._ticker_on = False

    def submit(self, kind: str, fn, task_id: str | None = None, account_id: str | None = None, **args) -> Job:
        jid = db.next_id("job", "J", 5)
        job = Job(jid, task_id, kind, account_id, fn, args)
        db.run("INSERT INTO jobs(id,task_id,kind,status,started,log,owner_pid,owner_port,beat) VALUES(?,?,?,?,?,?,?,?,?)",
               (jid, task_id, kind, "queued", db.now(), str(job.log_path),
                os.getpid(), int(getattr(locks, "OWNER_PORT", 0) or 0), time.time()))
        _ensure_job_beat_thread()   # 83 号：心跳线程第一次提交 Job 时拉起，之后每 locks.BEAT 秒刷一次 beat
        with self.lock:
            self.jobs[jid] = job
            self.queues[account_id or ""].append(job)
        events.emit("job.state", job=job.to_dict())
        self._pump()
        return job

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.cancelled.set()
            with self.lock:
                for q in self.queues.values():
                    if job in q and job.status == "queued":
                        q.remove(job)
                        job.status = "cancelled"
                        self._finish(job)
            # 卡住的 Job 可能已经不 check() 了，锁也要当场放，别挡后面的队。
            # 规格 87：同组还有别的任务在跑时，锁是大家共用的，只清它的排队位，锁不能放。
            try:
                locks.drop_job(job_id, keep_lock=self.group_size(job_id) > 1)
            except Exception:
                pass
            return
        # 本进程没有：库里的僵尸（崩溃/卡死留下 running）。改库、放锁。
        row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise OranteError("没有这个任务")
        db.run("UPDATE jobs SET status='cancelled', finished=?, error=? WHERE id=?",
               (db.now(), "界面点停（卡住的 Job）", job_id))
        try:
            locks.drop_job(job_id)
        except Exception:
            pass
        events.emit("job.state", job={
            "id": job_id, "task_id": row.get("task_id"), "kind": row.get("kind"),
            "account_id": None, "status": "cancelled",
            "error": "界面点停（卡住的 Job）", "log": row.get("log"),
            "result": None, "prio": 9999,
        })

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self, active_only=True) -> list[dict]:  # noqa: A003
        out = [j.to_dict() for j in self.jobs.values()]
        if active_only:
            out = [j for j in out if j["status"] in ("queued", "running", "halted")]
        return out

    # ---- 内部 --------------------------------------------------------
    def _running_on(self, acc: str) -> list[Job]:
        return [j for k, j in self.running.items()
                if not k.startswith("local#") and (k == acc or k.startswith(acc + "|"))]

    def _pump(self) -> None:
        with self.lock:
            for acc, q in self.queues.items():
                if not q:
                    continue
                if acc == "":
                    n_local = sum(1 for k in self.running if k.startswith("local#"))
                    while q and n_local < self._local_workers:
                        job = q.popleft()
                        key = f"local#{job.id}"
                        self.running[key] = job
                        n_local += 1
                        threading.Thread(target=self._run, args=(job, key), daemon=True).start()
                elif not self._running_on(acc):
                    # 本进程内也按旗子挑，不是先进先出：
                    # (旗子序号, 发号顺序)，同号先到先得。跨进程的顺序由
                    # locks.py 的等待队列保证，两边用的是同一个排序键。
                    job = min(q, key=lambda j: (j.prio, j.seq))
                    if _job_blocked_by_throttle(job, acc):
                        continue
                    q.remove(job)
                    self.running[acc] = job
                    self.groups[acc] = {"host": job.id, "members": {job.id},
                                        "lock": None, "held": False}
                    threading.Thread(target=self._run, args=(job, acc), daemon=True).start()
                else:
                    # 规格 87：账号上已经有 Job 在跑。只有全是 Grok 普通 generate、
                    # 且加上队首之后总段数不超过账号并发，才让队首一起跑。队首放不下就谁都不放（不插队）。
                    while q:
                        job = min(q, key=lambda j: (j.prio, j.seq))
                        if not self._can_join(acc, job):
                            break
                        q.remove(job)
                        job._pack_joiner = True
                        self.running[f"{acc}|{job.id}"] = job
                        self.groups[acc]["members"].add(job.id)
                        threading.Thread(target=self._run, args=(job, f"{acc}|{job.id}"),
                                         daemon=True).start()

    def _can_join(self, acc: str, job: Job) -> bool:
        """调用方持有 self.lock。"""
        g = self.groups.get(acc)
        if not g or not g.get("held"):
            return False               # 组长还没拿到跨窗口的账号锁
        if job.kind not in ("generate", "regenerate"):
            return False
        if _job_blocked_by_throttle(job, acc):
            return False
        if locks.foreign_waiters(acc):
            return False               # 别的窗口在等这个账号：不再续人，这组跑完就交锁
        try:
            from .services.generate import pack_demand
            from . import accounts, config
            cap = int((accounts.get(acc) or {}).get("max_concurrent") or 4)
        except Exception:
            return False
        # 同一个任务不能在同组里重复进
        for r in self._running_on(acc):
            if r.task_id and r.task_id == job.task_id:
                return False
        need = pack_demand(job.task_id, job.args, running=False, account_id=acc)
        if not need or need <= 0:
            return False
        used = 0
        for r in self._running_on(acc):
            if r.kind not in ("generate", "regenerate") or r.status not in ("queued", "running"):
                return False
            n = pack_demand(r.task_id, r.args, running=True, account_id=acc)
            if n is None:
                return False
            used += n
        # 规格 91：打开「同账号多任务」之后，不再要求"所有任务的剩余段数加起来
        # 不超过账号并发"——那条规则只放得下几个小任务。改成限任务个数，
        # 同时在飞的段数仍然由共享引擎的 capacity() 卡死（Flow 5 / Grok 4），破不了。
        if bool(config.get("engine.pack.multi_task", False)):
            try:
                max_tasks = int(config.get("engine.pack.max_tasks", 3) or 3)
            except (TypeError, ValueError):
                max_tasks = 3
            max_tasks = max(2, min(max_tasks, 5))
            return len(self.groups[acc]["members"]) < max_tasks
        return used + need <= cap

    def pack_view(self) -> dict:
        """给界面：每个账号本进程里一起在跑的任务和占用的并发位。"""
        out: dict[str, dict] = {}
        with self.lock:
            accs = {j.account_id for k, j in self.running.items()
                    if not k.startswith("local#") and j.account_id}
            snap = {a: list(self._running_on(a)) for a in accs}
        try:
            from .services.generate import pack_demand
            from . import accounts
        except Exception:
            return out
        for a, jobs in snap.items():
            try:
                cap = int((accounts.get(a) or {}).get("max_concurrent") or 4)
            except Exception:
                cap = 4
            used = 0
            packable = True
            for j in jobs:
                n = pack_demand(j.task_id, j.args, running=True, account_id=a) if j.kind in ("generate", "regenerate") else None
                if n is None:
                    packable = False
                else:
                    used += n
            out[a] = {"tasks": [j.task_id for j in jobs if j.task_id],
                      "jobs": [j.id for j in jobs], "used": used, "cap": cap,
                      "packable": packable}
        return out

    def _leave_group(self, job: Job, own_lock, key: str | None = None):
        """Job 结束时调用。返回需要真正释放的锁（同组还有人在跑就返回 None）。

        顺手把自己从 running 里拿掉（和离组在同一把锁里做），免得调度器还把它算作在跑。
        """
        acc = job.account_id or ""
        with self.lock:
            if key is not None:
                self.running.pop(key, None)
            g = self.groups.get(acc)
            if g is None or job.id not in g["members"]:
                return own_lock
            g["members"].discard(job.id)
            if g["members"]:
                lk = g.get("lock")
                rest = [j for j in self._running_on(acc) if j.id in g["members"]]
                if lk is not None and rest:
                    nxt = rest[0]
                    try:
                        locks._write("UPDATE account_locks SET job_id=?, task_id=? "
                                     "WHERE account_id=? AND owner=?",
                                     (nxt.id, nxt.task_id, acc, lk.token))
                    except Exception:
                        pass
                return None
            self.groups.pop(acc, None)
            return g.get("lock") or own_lock

    def group_size(self, job_id: str) -> int:
        with self.lock:
            for g in self.groups.values():
                if job_id in g["members"]:
                    return len(g["members"])
        return 0

    def _run(self, job: Job, key: str) -> None:
        job.status = "running"
        job._fh = open(job.log_path, "a", encoding="utf-8")
        db.run("UPDATE jobs SET status='running', started=? WHERE id=?", (db.now(), job.id))
        events.emit("job.state", job=job.to_dict())
        joiner = bool(getattr(job, "_pack_joiner", False))
        lock = locks.AccountLock(job.account_id, job) if (job.account_id and not joiner) else None
        try:
            job.log(f"▶ 开始 {job.kind}" + (f"  任务 {job.task_id}" if job.task_id else ""))
            if joiner:
                job.log("  · 和同账号的任务一起出片（总段数不超过账号并发）")
            if lock is not None:
                # 跨进程排队：同一个账号（同一个 Chrome）同时只许一个 Job 驱动。
                # 多开 Orante 窗口时，这是唯一挡得住「两个进程抢同一个标签页」的地方。
                # 规格 87：拼单的几个 Job 共用这一把锁，组长拿，最后一个走的人放。
                lock.acquire()
                with self.lock:
                    g = self.groups.get(job.account_id)
                    if g is not None and g.get("host") == job.id:
                        g["lock"] = lock
                        g["held"] = True
                self._pump()
            job.result = job.fn(job, **job.args)
            job.status = "done"
            job.log(_finish_line(job))
        except JobCancelled:
            job.status = "cancelled"
            job.log("■ 已取消")
        except CreditHalted as e:
            job.status = "halted"
            job.error = e.message
            job.credit_halt = {
                "b0": e.b0, "b": e.b, "message": e.message,
                "recent": list(e.recent or []),
            }
            job.log(f"■ 已拦停：{e.message}")
        except OranteError as e:
            job.status = "failed"
            job.error = e.message + (f"（{e.hint}）" if e.hint else "")
            job.log(f"✗ {job.error}")
        except Exception as e:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.log("✗ " + job.error)
            job.log(traceback.format_exc())
        finally:
            try:
                from .engines import shared as _sharedmod
                _sharedmod.release(job, job.account_id)   # 兜底：generate 没走到自己的 finally 也要还
            except Exception:
                pass
            to_release = self._leave_group(job, lock, key) if job.account_id else lock
            if to_release is not None:
                to_release.release()
            with self.lock:
                self.running.pop(key, None)
            self._finish(job)
            self._pump()

    def _finish(self, job: Job) -> None:
        if job._fh:
            job._fh.close()
            job._fh = None
        db.run("UPDATE jobs SET status=?, finished=?, error=? WHERE id=?", (job.status, db.now(), job.error, job.id))
        events.emit("job.state", job=job.to_dict())
        # 生成类 Job 收尾：无条件再推 counts，折叠行最后一格不能靠轮询才绿
        if job.task_id and job.kind in ("generate", "regenerate", "refill"):
            try:
                from . import tasks as taskmod
                taskmod.emit_seg_changed(job.task_id, force=True)
                events.emit("tasks.changed")
            except Exception:
                pass
        # 规格 98：Flow 这一轮结束了 → 排给本地模型的段现在交出去（这时这个 Job 已不算在跑）
        if job.task_id and job.kind in ("generate", "regenerate"):
            try:
                from .services import studio_handoff
                studio_handoff.on_job_finished(job)
            except Exception:
                pass


def _parse_job_ts(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0.0


def _job_last_ts(row: dict) -> float:
    """这个 Job 最后一次写日志的时间；没有日志文件就用 started。"""
    logp = row.get("log") or ""
    if logp:
        p = Path(logp)
        try:
            if p.is_file():
                return p.stat().st_mtime
        except Exception:
            pass
    return _parse_job_ts(row.get("started"))


def _account_of_job(row: dict) -> str | None:
    jid = row.get("id")
    kind = row.get("kind") or ""
    tid = row.get("task_id")
    job = scheduler.jobs.get(jid) if jid else None
    if job and job.account_id:
        return job.account_id
    try:
        lock = db.one("SELECT account_id FROM account_locks WHERE job_id=?", (jid,))
        if lock and lock.get("account_id"):
            return lock["account_id"]
    except Exception:
        pass
    if not tid:
        return None
    t = db.one("SELECT account_id, expand_account_id FROM tasks WHERE id=?", (tid,))
    if not t:
        return None
    if kind in ("expand", "refill", "upscale", "upscale_tail", "card_swap") and t.get("expand_account_id"):
        return t["expand_account_id"]
    return t.get("account_id")




# ---- specs/83-zombie-jobs.md ----

def _job_has_owner(row: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    beat = row.get("beat")
    if beat is None:
        return False
    try:
        return (now - float(beat)) <= float(locks.STALE)
    except (TypeError, ValueError):
        return False


def refresh_job_beats() -> None:
    try:
        db.run(
            "UPDATE jobs SET beat=? WHERE owner_pid=? AND status IN ('queued','running','halted')",
            (time.time(), os.getpid()),
        )
    except Exception:
        pass


def _revert_generating(task_id: str) -> None:
    try:
        from . import tasks as taskmod
        for name in ("set_status", "transition", "set_task_status"):
            fn = getattr(taskmod, name, None)
            if not callable(fn):
                continue
            for args in ((task_id, "arranged"), (task_id, "generating", "arranged")):
                try:
                    fn(*args)
                    return
                except Exception:
                    continue
    except Exception:
        pass
    try:
        db.run("UPDATE tasks SET status=? WHERE id=? AND status=?", ("arranged", task_id, "generating"))
    except Exception:
        pass


def reap_orphans() -> list[dict]:
    now = time.time()
    try:
        finished = db.now()
    except Exception:
        from datetime import datetime
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = db.rows("SELECT * FROM jobs WHERE status IN ('queued','running','halted')")
    except Exception:
        return []
    reaped = []
    for r in rows:
        if _job_has_owner(r, now):
            continue
        jid = r["id"]
        db.run(
            "UPDATE jobs SET status='lost', finished=?, error=? WHERE id=? AND status IN ('queued','running','halted')",
            (finished, "程序关闭时还没做完（上次运行遗留）", jid),
        )
        try:
            locks.drop_job(jid)
        except Exception:
            pass
        reaped.append(dict(r))
        tid = r.get("task_id")
        if not tid:
            continue
        try:
            trow = db.one("SELECT status FROM tasks WHERE id=?", (tid,))
        except Exception:
            trow = None
        if trow and trow.get("status") == "generating":
            left = db.rows(
                "SELECT * FROM jobs WHERE task_id=? AND status IN ('queued','running','halted')",
                (tid,),
            )
            if not any(_job_has_owner(x, now) for x in left):
                _revert_generating(tid)
    if reaped:
        bits = [f"{r['id']} · {r.get('task_id') or '?'}" for r in reaped[:5]]
        more = "" if len(reaped) <= 5 else f" 等 {len(reaped)} 条"
        print(f"收捡了 {len(reaped)} 条上次遗留的 Job（{'；'.join(bits)}{more}）", flush=True)
    return reaped


def live_jobs() -> list[dict]:
    now = time.time()
    out = []
    have = set()

    def task_ok(tid):
        if not tid:
            return True
        try:
            t = db.one("SELECT status FROM tasks WHERE id=?", (tid,))
        except Exception:
            return True
        if not t:
            return True
        return t.get("status") not in ("trash", "finished")

    def age_sec(row):
        v = row.get("started")
        if isinstance(v, (int, float)):
            return max(0, int(now - float(v)))
        if v:
            try:
                from datetime import datetime
                ts = datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                return max(0, int(now - ts))
            except Exception:
                pass
        beat = row.get("beat")
        if isinstance(beat, (int, float)):
            return max(0, int(now - float(beat)))
        return 0

    def pack(row, stale):
        tid = row.get("task_id") or ""
        tname, tstatus = "", ""
        if tid:
            try:
                t = db.one("SELECT name, status FROM tasks WHERE id=?", (tid,))
                if t:
                    tname = t.get("name") or ""
                    tstatus = t.get("status") or ""
            except Exception:
                pass
        age = age_sec(row)
        if row.get("status") == "queued" and age > 3600:
            stale = True
        if tstatus in ("trash", "finished"):
            stale = True
        return {
            "id": row.get("id"),
            "task_id": tid,
            "task_name": tname,
            "task_status": tstatus,
            "kind": row.get("kind") or "",
            "status": row.get("status") or "",
            "age_sec": age,
            "stale": bool(stale),
        }

    try:
        for j in scheduler.list(active_only=True):
            jid = j.get("id")
            if not jid or jid in have:
                continue
            if j.get("status") not in ("queued", "running", "halted"):
                continue
            if not task_ok(j.get("task_id")):
                continue
            out.append(pack(j, False))
            have.add(jid)
    except Exception:
        pass

    try:
        rows = db.rows("SELECT * FROM jobs WHERE status IN ('queued','running','halted')")
    except Exception:
        rows = []
    for r in rows:
        jid = r.get("id")
        if not jid or jid in have:
            continue
        if not _job_has_owner(r, now):
            continue
        if not task_ok(r.get("task_id")):
            continue
        out.append(pack(dict(r), False))
        have.add(jid)

    try:
        holders = locks.live_holders()
    except Exception:
        holders = []
    for h in holders or []:
        jid = h.get("job_id") or ""
        tid = h.get("task_id") or ""
        if jid and jid in have:
            continue
        if not task_ok(tid):
            continue
        fake = {"id": jid or f"lock:{h.get('account_id')}", "task_id": tid, "kind": "lock",
                "status": "running", "started": None, "beat": now}
        out.append(pack(fake, False))
        if jid:
            have.add(jid)
    return out


def _ensure_job_beat_thread() -> None:
    if getattr(_ensure_job_beat_thread, "_on", False):
        return
    def _loop():
        while True:
            try:
                refresh_job_beats()
            except Exception:
                pass
            try:
                delay = float(getattr(locks, "BEAT", 20.0) or 20.0)
            except Exception:
                delay = 20.0
            time.sleep(delay)
    threading.Thread(target=_loop, name="orante-job-beat", daemon=True).start()
    _ensure_job_beat_thread._on = True  # type: ignore[attr-defined]

def stuck_jobs(idle_sec: float = STUCK_IDLE) -> list[dict]:
    """running 超过 idle_sec 秒没新日志的 Job。给生成面板账号行用。"""
    now = time.time()
    out = []
    try:
        rows = db.rows("SELECT * FROM jobs WHERE status='running'")
    except Exception:
        return out
    seen = set()
    for r in rows:
        last = _job_last_ts(r)
        if last <= 0:
            continue
        idle = now - last
        if idle < idle_sec:
            continue
        item = {
            "job_id": r["id"],
            "task_id": r.get("task_id") or "",
            "kind": r.get("kind") or "",
            "account_id": _account_of_job(r) or "",
            "idle_sec": int(idle),
            "idle_min": max(1, int(idle // 60)),
        }
        out.append(item)
        seen.add(r["id"])
    # 本进程内存里有、库还没刷成 running 的，也看一眼（极少）。
    for j in list(scheduler.jobs.values()):
        if j.id in seen or j.status != "running":
            continue
        last = 0.0
        try:
            if j.log_path.is_file():
                last = j.log_path.stat().st_mtime
        except Exception:
            pass
        if last <= 0:
            continue
        idle = now - last
        if idle < idle_sec:
            continue
        out.append({
            "job_id": j.id, "task_id": j.task_id or "", "kind": j.kind or "",
            "account_id": j.account_id or "",
            "idle_sec": int(idle), "idle_min": max(1, int(idle // 60)),
        })
    # spec 83: queued > 30min without owner
    try:
        qrows = db.rows("SELECT * FROM jobs WHERE status='queued'")
    except Exception:
        qrows = []
    seen_ids = {x.get("job_id") for x in out}
    for r in qrows:
        jid = r.get("id")
        if jid in seen_ids:
            continue
        if _job_has_owner(r, now):
            continue
        last = _job_last_ts(r)
        if last <= 0:
            continue
        idle = now - last
        if idle < 30 * 60:
            continue
        out.append({
            "job_id": jid,
            "task_id": r.get("task_id") or "",
            "kind": r.get("kind") or "",
            "account_id": "",
            "idle_sec": int(idle),
            "idle_min": max(1, int(idle // 60)),
            "status": "queued",
        })
        seen_ids.add(jid)

    return out


def _job_blocked_by_throttle(job: Job, acc_id: str) -> bool:
    """冷却中的账号 / 网络限流时的 Flow 任务，先留在队列里，到点再跑。"""
    if not acc_id:
        return False
    try:
        from . import accounts
    except Exception:
        return False
    try:
        if accounts.network_throttled():
            if job.kind in ("generate", "regenerate", "refill", "expand"):
                a = accounts.get(acc_id)
                if (a.get("engine") or "") == "flow":
                    # 规格 94：全网暂停也要放探路，否则这 30 分钟里一次都不试，
                    # 而 Google 那边可能早就放行了（浏览器里能生成就是证据）。
                    if job.kind == "generate" and accounts.net_probe_due(every=_probe_every()):
                        return False
                    return True
        if accounts.is_throttled(acc_id):
            # 规格 91：冷却是 Orante 自己定的时长。到了探路的点就放这个任务过去，
            # 让它真的提交一次；成功就当场解除冷却，失败照旧记一笔继续等。
            if job.kind == "generate" and accounts.probe_due(acc_id, every=_probe_every()):
                return False
            return True
    except Exception:
        return False
    return False


def _probe_every() -> float:
    try:
        from .services.generate import _probe_every as f
        return f()
    except Exception:  # noqa: BLE001
        from . import accounts
        return accounts.THROTTLE_PROBE_EVERY


def _finish_line(job: Job) -> str:
    """这一轮有失败或限流没提交，结尾不许再写绿色的 ✓ 完成。"""
    r = job.result if isinstance(getattr(job, "result", None), dict) else {}
    n_fail = int(r.get("failed") or 0)
    n_th = int(r.get("pending_throttled") or 0)
    if not n_th and r.get("throttled"):
        n_th = 1
    leftover = n_fail + n_th
    if leftover <= 0:
        return "✓ 完成"
    why = []
    if n_th:
        why.append("账号限流中")
    if n_fail and not n_th:
        why.append("还有失败的段")
    tail = "（" + "，".join(why) + "）" if why else ""
    return f"⚠ 这一轮结束，还有 {leftover} 段没做成{tail}"


def start_ticker(interval: float = 10.0) -> None:
    """冷却到期后把排队里的 generate 拉起来。main.serve 里开一次。"""
    if scheduler._ticker_on:
        return
    scheduler._ticker_on = True

    def _loop():
        last_studio = 0.0
        while True:
            time.sleep(interval)
            try:
                scheduler._pump()
            except Exception:
                pass
            # 规格 98：等着转交本地模型的任务，每 60 秒再试一次
            if time.time() - last_studio >= 60:
                last_studio = time.time()
                try:
                    from .services import studio_handoff
                    studio_handoff.tick()
                except Exception:
                    pass

    threading.Thread(target=_loop, daemon=True, name="orante-sched-tick").start()


scheduler = Scheduler()


# 给 server 当方法用：scheduler.stuck_jobs()
Scheduler.stuck_jobs = staticmethod(stuck_jobs)
