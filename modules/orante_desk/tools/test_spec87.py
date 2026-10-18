# -*- coding: utf-8 -*-
"""规格 87：Grok 同账号拼单——小任务一起跑，总段数不超过账号并发。

全部用假引擎，不连真 Grok / Flow，不花额度。先设 ORANTE_DATA。
重点守住：Chrome 只有一只"手"（所有引擎调用在同一个线程）、任何时刻在飞段数 ≤ 并发、
片子各归各的任务、日志不串台、停一个不影响别的、跨窗口的锁最后一个走才放。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-87-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from orante import db, locks, tasks as taskmod  # noqa: E402
from orante.jobs import load_all_services, REGISTRY  # noqa: E402
from orante.models import Manifest, sha1_file  # noqa: E402
from orante.engines import shared as sharedmod  # noqa: E402
from orante.services import generate as genmod  # noqa: E402
from orante import scheduler as schedmod  # noqa: E402

db.init()
load_all_services()

GACC = "G87"


# ---------------------------------------------------------------- 假引擎

class Tracker:
    """所有假引擎实例共用的观测台。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.alive = 0            # 同一时刻活着的引擎实例数（按账号只应有 1）
            self.max_alive = 0
            self.instances = []
            self.max_active = 0
            self.errors = []
            self.hold: set[str] = set()      # 这些 task 的段先别出片
            self.submits: list[str] = []


TR = Tracker()


class FakeGrok:
    name = "grok"
    can_gen_images = False

    def __init__(self, account, log):
        self.account = account
        self.log = log
        self.max_conc = int(account.get("max_concurrent") or 4)
        self._active: set[str] = set()
        self._polls: dict[str, int] = {}
        self.threads: set[int] = set()
        self.connects = 0
        self.closes = 0
        with TR.lock:
            TR.alive += 1
            TR.max_alive = max(TR.max_alive, TR.alive)
            TR.instances.append(self)

    def _t(self):
        self.threads.add(threading.get_ident())

    def connect(self):
        self._t()
        self.connects += 1
        time.sleep(0.02)

    def close(self):
        self._t()
        self.closes += 1
        with TR.lock:
            TR.alive -= 1

    def capacity(self):
        self._t()
        return max(0, self.max_conc - len(self._active))

    def submit(self, seg, image, prompt, duration, last_image=None):
        self._t()
        tid = Path(image).parent.parent.name.split("-")[0]
        gid = f"{tid}-s{int(seg['s']):03d}-{len(TR.submits)}"
        self._active.add(gid)
        with TR.lock:
            TR.submits.append(gid)
            TR.max_active = max(TR.max_active, len(self._active))
            if len(self._active) > self.max_conc:
                TR.errors.append(f"超发：{len(self._active)} > {self.max_conc}")
        self.log(f"  [fake] 提交 {gid}")
        time.sleep(0.01)
        return gid

    def poll(self, ids):
        self._t()
        out = {}
        for gid in ids:
            tid = gid.split("-")[0]
            if tid in TR.hold:
                out[gid] = {"state": "pending"}
                continue
            n = self._polls.get(gid, 0) + 1
            self._polls[gid] = n
            if n >= 2:
                self._active.discard(gid)
                out[gid] = {"state": "done", "url": f"https://x/{gid}.mp4"}
                self.log(f"  [fake] 出片 {gid}")
            else:
                out[gid] = {"state": "pending"}
        return out

    def download(self, gid, dest):
        self._t()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(gid, encoding="utf-8")
        return dest

    def forget_url(self, gid, url=""):
        self._t()


def _ok_verify(m, d, seg, dest, **kw):
    dest = Path(dest)
    return {
        "take": {"file": str(dest.relative_to(d)).replace("\\", "/"),
                 "gen_id": seg.get("gen_id") or "", "at": "t", "verdict": "ok"},
        "verdict": "ok", "voided": False,
    }


# ---------------------------------------------------------------- 造任务

def _ins_acc(aid=GACC, engine="grok", cap=4):
    db.run("DELETE FROM accounts WHERE id=? OR port=?", (aid, 9800 + (sum(map(ord, aid)) % 190)))
    db.run(
        "INSERT INTO accounts(id,name,engine,port,profile,project_url,model,"
        "aspect,max_concurrent,enabled,extra,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, f"acc-{aid}", engine, 9800 + (sum(map(ord, aid)) % 190), aid,
         "https://flow.google.com/project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" if engine == "flow" else "",
         "", "9:16", cap, 1, "{}", db.now()))


def _make_task(tid, n_segs=1, aid=GACC, engine="grok", mode=None):
    root = Path(tempfile.mkdtemp(prefix="orante-t87-"))
    d = root / f"{tid}-x"
    (d / "images").mkdir(parents=True)
    (d / "clips").mkdir()
    fp = d / "images" / "p01.jpg"
    Image.new("RGB", (160, 90), (40, 80, 60)).save(fp, "JPEG")
    m = Manifest.new(tid, f"拼单{tid}", engine, aid, "long", "")
    m.images = [{"p": "p01", "file": "images/p01.jpg", "sha1": sha1_file(fp), "role": "主体",
                 "status": "ok", "desc": "一个人在看视频", "voice": "", "eye": "闭眼",
                 "scene": "常规", "measure": {}, "origin": "local", "src": ""}]
    m.segments = [{"s": i, "text": "", "duration": 6, "image": "p01", "role": "主体", "shot": "",
                   "prompt_final": f"prompt {tid} {i}", "status": "planned", "gen_id": None,
                   "takes": [], "error": None} for i in range(1, n_segs + 1)]
    if mode:
        m.mode = mode
    m.save(d)
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,duration_pref,status,dir,"
        "batch_id,row_order,created,updated,run_to_completion) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, f"拼单{tid}", engine, aid, "long", "", str(d / "images"), "", "arranged", str(d),
         "", 0, db.now(), db.now(), 0))
    return d


@pytest.fixture(autouse=True)
def _env():
    TR.reset()
    sharedmod._reset_for_tests()
    db.run("DELETE FROM account_locks")
    db.run("DELETE FROM account_waiters")

    def fast_sleep(self, sec):
        end = time.time() + min(float(sec), 0.03)
        while time.time() < end:
            self.check()
            time.sleep(0.005)

    with patch("orante.engines.shared.make_engine", lambda acc, log: FakeGrok(acc, log)), \
         patch("orante.services.generate.make_engine", lambda acc, log: FakeGrok(acc, log)), \
         patch("orante.services.verify.after_download", _ok_verify), \
         patch("orante.services.generate._enqueue_relay", lambda *a, **k: None), \
         patch("orante.services.tails.after_collect", lambda *a, **k: None), \
         patch.object(genmod, "POLL_EVERY", 0.02), \
         patch.object(schedmod.Job, "sleep", fast_sleep):
        yield
        # 收尾：放掉所有 hold，等本测试起的 Job 全部走完，别拖到下一个测试里
        TR.hold.clear()
        t0 = time.time()
        while time.time() - t0 < 30:
            with SCH.lock:
                busy = [k for k in SCH.running if not k.startswith("local#")]
                queued = [j for q in SCH.queues.values() for j in q]
            if not busy and not queued:
                break
            time.sleep(0.02)
    sharedmod._reset_for_tests()


SCH = schedmod.scheduler


def _submit(tid, aid=GACC):
    return SCH.submit("generate", REGISTRY["generate"], task_id=tid, account_id=aid)


class Watch:
    """后台每 5ms 记一次：这个账号同时有几个 Job 在出片。"""

    def __init__(self, aid=GACC):
        self.aid = aid
        self.max_run = 0
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while not self.stop.is_set():
            with SCH.lock:
                jobs = list(SCH._running_on(self.aid))
            # 只数"还有段要出"的 Job；段都收完、正在收尾的那个不占并发位
            n = 0
            for j in jobs:
                d = genmod.pack_demand(j.task_id, j.args, running=True)
                if d is None or d > 0:
                    n += 1
            self.max_run = max(self.max_run, n)
            time.sleep(0.005)

    def done(self):
        self.stop.set()
        self.t.join()


def _wait(jobs, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if all(j.status in ("done", "failed", "cancelled", "halted") for j in jobs):
            return
        time.sleep(0.02)
    raise AssertionError("超时：" + ", ".join(f"{j.task_id}:{j.status}" for j in jobs))


def _check_engines():
    assert not TR.errors, TR.errors
    assert TR.max_alive <= 1, f"同账号同时活着 {TR.max_alive} 个引擎"
    for e in TR.instances:
        assert len(e.threads) == 1, f"一个引擎被 {len(e.threads)} 个线程调用"
        assert e.connects == 1 and e.closes == 1, (e.connects, e.closes)


def _check_clips(tids):
    for tid in tids:
        t = taskmod.get(tid)
        m = Manifest.load(Path(t["dir"]))
        for sg in m.segments:
            assert sg["status"] == "collected", (tid, sg)
            take = sg["takes"][-1]
            fp = Path(t["dir"]) / take["file"]
            assert fp.is_file(), fp
            assert fp.name.startswith(f"{tid}-s{int(sg['s']):03d}"), fp.name
            body = fp.read_text(encoding="utf-8")
            assert body.startswith(f"{tid}-s{int(sg['s']):03d}-"), (tid, body)   # 片子是这个任务这一段的


# ---------------------------------------------------------------- 测试

def test_pack_demand_rules():
    _ins_acc()
    _make_task("T8701", 3)
    assert genmod.pack_demand("T8701", {}) == 3
    _ins_acc("F87", engine="flow", cap=5)
    _make_task("T8702", 1, aid="F87", engine="flow")
    assert genmod.pack_demand("T8702", {}) == 1               # 0922：Flow 也能拼单
    _make_task("T8703", 2, mode="tail_chain")
    assert genmod.pack_demand("T8703", {}) is None            # 接龙独占
    _make_task("T8704", 2, mode="anchor_bridge")
    assert genmod.pack_demand("T8704", {}) is None            # 锚点独占


def test_thirteen_single_seg_tasks_pack_four():
    _ins_acc(cap=4)
    tids = [f"T88{i:02d}" for i in range(13)]
    for tid in tids:
        _make_task(tid, 1)
    w = Watch()
    jobs = [_submit(tid) for tid in tids]
    _wait(jobs)
    w.done()
    assert all(j.status == "done" for j in jobs), [(j.task_id, j.status, j.error) for j in jobs]
    assert w.max_run >= 3, w.max_run                            # 真的拼上了（几个任务一起跑）
    assert TR.max_active <= 4                                   # 硬约束：在飞的段任何时刻不超过 4
    assert TR.max_active <= 4
    _check_engines()
    _check_clips(tids)
    # 日志不串台：每个 Job 的日志里只有自己任务的 gid
    for j in jobs:
        text = Path(j.log_path).read_text(encoding="utf-8")
        for other in tids:
            if other != j.task_id:
                assert f"{other}-s" not in text, (j.task_id, other)
    assert not db.rows("SELECT * FROM account_locks WHERE account_id=?", (GACC,))


def test_two_seg_tasks_pack_two():
    _ins_acc(cap=4)
    tids = [f"T89{i:02d}" for i in range(5)]
    for tid in tids:
        _make_task(tid, 2)
    w = Watch()
    jobs = [_submit(tid) for tid in tids]
    _wait(jobs)
    w.done()
    assert all(j.status == "done" for j in jobs)
    assert w.max_run <= 2, w.max_run
    assert TR.max_active <= 4
    _check_engines()
    _check_clips(tids)


def test_big_task_runs_alone():
    _ins_acc(cap=4)
    _make_task("T8601", 5)
    _make_task("T8602", 1)
    TR.hold.add("T8601")
    j1 = _submit("T8601")
    time.sleep(0.4)
    j2 = _submit("T8602")
    time.sleep(0.4)
    assert j1.status == "running" and j2.status == "queued"      # 5 段的独占，1 段的不能挤进来
    TR.hold.clear()
    _wait([j1, j2])
    assert j1.status == j2.status == "done"
    _check_clips(["T8601", "T8602"])


def test_no_queue_jumping():
    """在跑的剩 2 段，队首 3 段放不下 → 后面那个 1 段的也不许插队。"""
    _ins_acc(cap=4)
    _make_task("T8501", 2)
    _make_task("T8502", 3)
    _make_task("T8503", 1)
    TR.hold.add("T8501")
    j1 = _submit("T8501")
    time.sleep(0.4)
    j2 = _submit("T8502")
    j3 = _submit("T8503")
    time.sleep(0.5)
    assert j1.status == "running"
    assert j2.status == "queued" and j3.status == "queued", (j2.status, j3.status)
    TR.hold.clear()
    _wait([j1, j2, j3])
    assert all(j.status == "done" for j in (j1, j2, j3))
    _check_engines()


class FakeFlow(FakeGrok):
    name = "flow"

    def __init__(self, account, log):
        super().__init__(account, log)
        self.begins = 0
        self._credit_b0 = None
        self._credit_continue = False

    def begin_credits(self, job=None):
        self._t()
        self.begins += 1
        self._credit_b0 = 1000
        if job is not None:
            job.credit_b0 = 1000
        return 1000

    def end_credits(self):
        self._t()
        return 1000

    def tick_credits(self):
        self._t()
        return 1000

    def submit(self, seg, image, prompt, duration, last_image=None, silent=False):
        return super().submit(seg, image, prompt, duration, last_image=last_image)


def test_flow_packs_and_credit_baseline_once():
    """Flow 并发 5、每个任务 1 段 → 5 个一起跑；额度核对 B0 整组只起算一次，后加入的抄同一个 B0。"""
    _ins_acc("F88", engine="flow", cap=5)
    tids = [f"T84{i:02d}" for i in range(7)]
    for tid in tids:
        _make_task(tid, 1, aid="F88", engine="flow")
    TR.hold.update(tids[:5])
    w = Watch("F88")
    with patch("orante.services.generate.make_engine", lambda acc, log: FakeFlow(acc, log)):
        jobs = [_submit(t, aid="F88") for t in tids]
        # 「在跑」是调度器先标的，起算额度（begin_credits）稍后才在任务线程里发生：
        # 等到 5 个都在跑、引擎也建好、B0 都抄到手，再看整组只起算一次。
        t0 = time.time()
        while time.time() - t0 < 20 and (
                sum(j.status == "running" for j in jobs) < 5
                or not TR.instances
                or sum(getattr(j, "credit_b0", None) == 1000 for j in jobs[:5]) < 5):
            time.sleep(0.02)
        assert sum(j.status == "running" for j in jobs) == 5, [j.status for j in jobs]
        assert all(j.status == "queued" for j in jobs[5:])        # 第 6、7 个等空位
        assert TR.instances, "共享引擎没建起来"
        eng = TR.instances[-1]
        assert eng.begins == 1                                     # 只起算一次
        assert all(getattr(j, "credit_b0", None) == 1000 for j in jobs[:5])
        TR.hold.clear()
        _wait(jobs)
    w.done()
    assert all(j.status == "done" for j in jobs)
    assert w.max_run <= 5 and TR.max_active <= 5
    _check_engines()
    _check_clips(tids)


def test_credit_continue_releases_whole_group():
    """共用引擎上已点「继续」→ 同组别的任务的等待也一起放行（不用每个都点）。"""
    from orante.engines.flow_api import wait_credit_continue

    class J:
        credit_continue = False
        status = "halted"
        credit_halt = {"x": 1}
        error = "e"

        def check(self):
            pass

        def sleep(self, s):
            time.sleep(0.01)

    class E:
        _credit_continue = True
    j = J()
    wait_credit_continue(j, E())
    assert j.status == "running" and j.credit_halt is None


def test_stop_one_others_continue_and_lock_kept():
    _ins_acc(cap=4)
    tids = ["T8301", "T8302", "T8303"]
    for tid in tids:
        _make_task(tid, 1)
    TR.hold.update(tids)
    jobs = [_submit(t) for t in tids]
    t0 = time.time()
    while time.time() - t0 < 5 and not all(j.status == "running" for j in jobs):
        time.sleep(0.02)
    assert all(j.status == "running" for j in jobs), [j.status for j in jobs]
    host = next(j for j in jobs if not getattr(j, "_pack_joiner", False))
    SCH.cancel(host.id)                       # 停组长
    t0 = time.time()
    while time.time() - t0 < 5 and host.status != "cancelled":
        time.sleep(0.02)
    assert host.status == "cancelled"
    t0 = time.time()
    while time.time() - t0 < 5 and SCH.group_size(host.id):
        time.sleep(0.02)                      # 等它的线程走完 finally（离组、交接锁表上的名字）
    # 锁还在（别的窗口不能趁机抢走），记在还在跑的那个任务名下
    row = db.one("SELECT * FROM account_locks WHERE account_id=?", (GACC,))
    assert row is not None
    assert row["task_id"] in [t for t in tids if t != host.task_id]
    other = locks.AccountLock(GACC, None, owner_port=1, owner_pid=1)
    other.token = "otherhost#1#Jx"
    with pytest.raises(Exception):
        other.acquire(wait=False)
    TR.hold.clear()
    rest = [j for j in jobs if j is not host]
    _wait(rest)
    assert all(j.status == "done" for j in rest)
    _check_clips([j.task_id for j in rest])
    assert not db.rows("SELECT * FROM account_locks WHERE account_id=?", (GACC,))
    assert other.acquire(wait=False)          # 全组结束，别的窗口才能拿到
    other.release()


def test_single_task_still_works_through_shared_engine():
    _ins_acc(cap=4)
    _make_task("T8201", 3)
    j = _submit("T8201")
    _wait([j])
    assert j.status == "done"
    _check_engines()
    _check_clips(["T8201"])


def test_proxy_forwards_and_close_is_noop():
    _ins_acc(cap=4)
    acc = {"id": GACC, "engine": "grok", "max_concurrent": 4, "name": "x", "port": 1}

    class J:
        id = "JX1"

        def __init__(self):
            self.lines = []

        def log(self, m):
            self.lines.append(m)
    j = J()
    p = sharedmod.acquire(acc, j)
    p.connect()
    p.connect()                                 # 第二次是空操作
    assert p.capacity() == 4
    assert p.name == "grok"
    assert getattr(p, "no_such_method", None) is None
    p.close()                                   # 代理上 close 不关真引擎
    eng = TR.instances[-1]
    assert eng.closes == 0 and eng.connects == 1
    sharedmod.release(j, GACC)
    assert eng.closes == 1
    assert sharedmod.users(GACC) == []


def test_foreign_waiter_stops_joining():
    """别的窗口在排这个账号时，本窗口不再往组里加人（跑完就交锁，不饿死别的窗口）。"""
    _ins_acc(cap=4)
    for tid in ("T8101", "T8102"):
        _make_task(tid, 1)
    TR.hold.add("T8101")
    j1 = _submit("T8101")
    t0 = time.time()
    while time.time() - t0 < 5 and j1.status != "running":
        time.sleep(0.02)
    time.sleep(0.2)
    db.run("INSERT OR REPLACE INTO account_waiters(key,account_id,job_id,task_id,label,prio,seq,ts)"
           " VALUES(?,?,?,?,?,?,?,?)", ("otherhost#1#J99999", GACC, "J99999", "T9999", "generate",
                                        9999, 99999, time.time()))
    j2 = _submit("T8102")
    time.sleep(0.5)
    assert j2.status == "queued"
    db.run("DELETE FROM account_waiters WHERE key=?", ("otherhost#1#J99999",))
    SCH._pump()
    t0 = time.time()
    while time.time() - t0 < 5 and j2.status == "queued":
        time.sleep(0.02)
    assert j2.status in ("running", "done")
    TR.hold.clear()
    _wait([j1, j2])


# ---- 0922：Flow 不说话的任务按"返回无声视频"提交 --------------------------------

def test_flow_gen_inner_audio_flag():
    """抓包对照：网页开「返回无声视频」时 eb1hJf 最外层最后一项是 [uuid, 1]，关时 [uuid, 2]。"""
    from orante.engines import flow_api
    eng = flow_api.FlowEngine.__new__(flow_api.FlowEngine)
    eng.model_name = "Veo 3.1 - Lite [Lower Priority]"
    eng.collection_id = "c"
    eng.project_id = "p"
    on = eng._gen_inner("x", "m", [None, None, 1, 1], 8, "tok", aspect_code=2, silent=True)
    off = eng._gen_inner("x", "m", [None, None, 1, 1], 8, "tok", aspect_code=2)
    assert on[-1][1] == 1 and off[-1][1] == 2
    assert on[0] == off[0] or on[0][0][1:5] == off[0][0][1:5]   # 其它部分不变


class FakeFlowRec(FakeFlow):
    calls: list = []

    def submit(self, seg, image, prompt, duration, last_image=None, silent=False):
        FakeFlowRec.calls.append((Path(image).parent.parent.name.split("-")[0], silent))
        return super().submit(seg, image, prompt, duration, last_image=last_image)


def test_flow_no_copy_task_submits_silent_and_copy_task_does_not():
    _ins_acc("F89", engine="flow", cap=5)
    FakeFlowRec.calls = []
    _make_task("T8001", 1, aid="F89", engine="flow")                 # 没文案
    d2 = _make_task("T8002", 1, aid="F89", engine="flow")
    db.run("UPDATE tasks SET copy=? WHERE id=?", ("这是一段有台词的文案。", "T8002"))
    m2 = Manifest.load(d2)
    m2.copy = "这是一段有台词的文案。"
    m2.save(d2)
    with patch("orante.services.generate.make_engine", lambda acc, log: FakeFlowRec(acc, log)):
        jobs = [_submit("T8001", aid="F89"), _submit("T8002", aid="F89")]
        _wait(jobs)
    assert all(j.status == "done" for j in jobs), [(j.status, j.error) for j in jobs]
    got = dict(FakeFlowRec.calls)
    assert got == {"T8001": True, "T8002": False}, got


# ---------------- 规格 91：同账号多任务开关 ----------------

def _set_multi(on: bool, max_tasks: int = 3):
    from orante import config
    config.set("engine.pack.multi_task", bool(on), touch_rev=False)
    config.set("engine.pack.max_tasks", int(max_tasks), touch_rev=False)


def test_multi_task_switch_off_keeps_old_rule():
    """开关关着：大任务照旧独占账号（加起来超过并发就不拼）。"""
    _set_multi(False)
    _ins_acc("M910", engine="grok", cap=4)
    tids = ["T9101", "T9102"]
    for tid in tids:
        _make_task(tid, 6, aid="M910")          # 每个 6 段 > cap 4
    TR.hold.update(tids)
    with patch("orante.services.generate.make_engine", lambda acc, log: FakeGrok(acc, log)):
        jobs = [_submit(t, aid="M910") for t in tids]
        t0 = time.time()
        while time.time() - t0 < 5 and not any(j.status == "running" for j in jobs):
            time.sleep(0.02)
        time.sleep(0.4)
        assert sum(j.status == "running" for j in jobs) == 1, [j.status for j in jobs]
        TR.hold.clear()
        _wait(jobs)
    assert all(j.status == "done" for j in jobs)
    _check_engines()
    _check_clips(tids)


def test_multi_task_switch_on_runs_three_together():
    """开关打开：三个大任务一起跑，在飞的段数仍然不超过账号并发。"""
    _set_multi(True, 3)
    try:
        _ins_acc("M911", engine="grok", cap=4)
        tids = ["T9111", "T9112", "T9113", "T9114"]
        for tid in tids:
            _make_task(tid, 5, aid="M911")      # 每个 5 段，单个就超过 cap
        TR.hold.update(tids)
        w = Watch("M911")
        with patch("orante.services.generate.make_engine", lambda acc, log: FakeGrok(acc, log)):
            jobs = [_submit(t, aid="M911") for t in tids]
            t0 = time.time()
            while time.time() - t0 < 8 and sum(j.status == "running" for j in jobs) < 3:
                time.sleep(0.02)
            assert sum(j.status == "running" for j in jobs) == 3, [j.status for j in jobs]
            assert jobs[3].status == "queued"    # 最多 3 个，第四个等着
            TR.hold.clear()
            _wait(jobs, timeout=120)
        w.done()
        assert all(j.status == "done" for j in jobs)
        assert TR.max_active <= 4, f"在飞的段超发了：{TR.max_active}"
        assert not TR.errors, TR.errors
        _check_engines()
        _check_clips(tids)
    finally:
        _set_multi(False)


def test_multi_task_respects_max_tasks_two():
    _set_multi(True, 2)
    try:
        _ins_acc("M912", engine="grok", cap=4)
        tids = ["T9121", "T9122", "T9123"]
        for tid in tids:
            _make_task(tid, 4, aid="M912")
        TR.hold.update(tids)
        with patch("orante.services.generate.make_engine", lambda acc, log: FakeGrok(acc, log)):
            jobs = [_submit(t, aid="M912") for t in tids]
            t0 = time.time()
            while time.time() - t0 < 8 and sum(j.status == "running" for j in jobs) < 2:
                time.sleep(0.02)
            time.sleep(0.3)
            assert sum(j.status == "running" for j in jobs) == 2, [j.status for j in jobs]
            TR.hold.clear()
            _wait(jobs, timeout=120)
        assert all(j.status == "done" for j in jobs)
        assert TR.max_active <= 4
        _check_clips(tids)
    finally:
        _set_multi(False)
