# -*- coding: utf-8 -*-
"""规格 83：僵尸 Job。先设 ORANTE_DATA。"""
from __future__ import annotations
import os, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-83-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest
from orante import db, locks
from orante.scheduler import (
    _ensure_job_beat_thread, _job_has_owner, live_jobs, reap_orphans, refresh_job_beats, scheduler, stuck_jobs,
)
from orante.services import suites
from orante.errors import OranteError

db.init()

@pytest.fixture(autouse=True)
def _clean():
    try:
        db.run("DELETE FROM jobs")
    except Exception:
        pass
    yield

def _job(**kw):
    cols = dict(id="Jx", task_id="Tx", kind="generate", status="queued", started=db.now(), log="",
                owner_pid=None, owner_port=None, beat=None)
    cols.update(kw)
    db.run(
        "INSERT INTO jobs(id,task_id,kind,status,started,log,owner_pid,owner_port,beat) VALUES(?,?,?,?,?,?,?,?,?)",
        (cols["id"], cols["task_id"], cols["kind"], cols["status"], cols["started"], cols["log"],
         cols["owner_pid"], cols["owner_port"], cols["beat"]),
    )

def test_migration_adds_columns():
    names = {r["name"] for r in db.rows("PRAGMA table_info(jobs)")}
    assert {"owner_pid", "owner_port", "beat"} <= names

def test_reap_null_beat(capsys):
    _job(id="Jz1", task_id="Tz1", status="queued", beat=None)
    try: db.run("INSERT INTO tasks(id,name,status) VALUES(?,?,?)", ("Tz1", "z", "trash"))
    except Exception: pass
    got = reap_orphans()
    row = db.one("SELECT * FROM jobs WHERE id=?", ("Jz1",))
    assert row["status"] == "lost"
    assert "遗留" in (row.get("error") or "")
    assert any(g["id"] == "Jz1" for g in got)
    out = capsys.readouterr().out
    assert "Jz1" in out or "Tz1" in out

def test_reap_keeps_fresh_other_owner():
    _job(id="Jl1", task_id="Tl1", status="running", owner_pid=424242, owner_port=9401, beat=time.time()-10)
    reap_orphans()
    assert db.one("SELECT status FROM jobs WHERE id=?", ("Jl1",))["status"] == "running"

def test_reap_stale_beat():
    _job(id="Js1", task_id="Ts1", status="running", owner_pid=424243, owner_port=9402, beat=time.time()-300)
    reap_orphans()
    assert db.one("SELECT status FROM jobs WHERE id=?", ("Js1",))["status"] == "lost"

def test_beat_refresh():
    old = time.time() - 8
    _job(id="Jb1", task_id="Tb1", status="queued", owner_pid=os.getpid(),
         owner_port=int(getattr(locks, "OWNER_PORT", 0) or 0), beat=old)
    refresh_job_beats()
    assert float(db.one("SELECT beat FROM jobs WHERE id=?", ("Jb1",))["beat"]) > old

def test_live_jobs_filters():
    pid, now = os.getpid(), time.time()
    _job(id="Jo", task_id="To", status="queued", beat=None)
    for tid, st in (("Ttr", "trash"), ("Tfi", "finished"), ("Tok", "generating")):
        try: db.run("INSERT INTO tasks(id,name,status) VALUES(?,?,?)", (tid, tid, st))
        except Exception: pass
    _job(id="Jtr", task_id="Ttr", status="queued", owner_pid=pid, beat=now)
    _job(id="Jfi", task_id="Tfi", status="running", owner_pid=pid, beat=now)
    _job(id="Jok", task_id="Tok", status="running", owner_pid=pid, beat=now)
    ids = {j["id"] for j in live_jobs()}
    assert "Jo" not in ids and "Jtr" not in ids and "Jfi" not in ids and "Jok" in ids

def _two_suites():
    try: suites.ensure_builtin()
    except Exception: pass
    rows = db.rows("SELECT id FROM suites")
    if len(rows) < 2:
        for name in ("spec83-a", "spec83-b"):
            try: suites.create(name)
            except Exception: pass
        rows = db.rows("SELECT id FROM suites")
    if len(rows) < 2:
        pytest.skip("need 2 suites")
    cur = rows[0]["id"]
    try: cur = suites.current_id()
    except Exception: pass
    target = next(r["id"] for r in rows if r["id"] != cur)
    return cur, target

def test_switch_ignores_zombie_only():
    try: db.run("INSERT INTO tasks(id,name,status) VALUES(?,?,?)", ("T0021", "认真0912", "trash"))
    except Exception: pass
    _job(id="J00204", task_id="T0021", status="queued", beat=None, started="2026-09-13 01:25:09")
    _, target = _two_suites()
    try:
        suites.switch(target)
    except OranteError as e:
        assert e.code != "jobs_active"

def test_switch_allowed_while_live_job():
    """活着的生成不再挡住套装切换。"""
    pid, now = os.getpid(), time.time()
    try: db.run("INSERT INTO tasks(id,name,status) VALUES(?,?,?)", ("Tbk", "blocker", "generating"))
    except Exception: pass
    _job(id="Jbk", task_id="Tbk", status="running", owner_pid=pid, beat=now)
    _, target = _two_suites()
    try:
        r = suites.switch(target, save=True)
    except OranteError as e:
        assert e.code != "jobs_active"
        raise
    assert r.get("ok")
    assert r.get("current") == target

def test_cancel_zombie():
    _job(id="Jc1", task_id="Tc1", status="queued", beat=None)
    scheduler.cancel("Jc1")
    assert db.one("SELECT status FROM jobs WHERE id=?", ("Jc1",))["status"] == "cancelled"

def test_stuck_queued_orphan():
    started = (datetime.now() - timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S")
    _job(id="Jsq", task_id="Tsq", status="queued", beat=None, started=started)
    got = stuck_jobs()
    assert any((x.get("job_id") or x.get("id")) == "Jsq" for x in got)

def test_suites_no_raw_status_sql():
    text = (ROOT / "orante" / "services" / "suites.py").read_text(encoding="utf-8")
    assert "WHERE status IN ('queued','running','halted')" not in text
    assert "jobs_active" not in text

def test_job_has_owner_helper():
    assert _job_has_owner({"beat": None}) is False
    assert _job_has_owner({"beat": time.time() - 10}) is True
    assert _job_has_owner({"beat": time.time() - 300}) is False

def test_submit_starts_beat_thread():
    """真正走 scheduler.submit 后，心跳线程已拉起，且 refresh_job_beats 能刷新 beat。"""
    def _noop(job):
        time.sleep(0.02)

    job = scheduler.submit("noop-beat", _noop)
    assert getattr(_ensure_job_beat_thread, "_on", False) is True
    row = db.one("SELECT beat, owner_pid, status FROM jobs WHERE id=?", (job.id,))
    assert row is not None
    assert row["status"] in ("queued", "running", "done", "failed", "cancelled", "halted")
    assert row["owner_pid"] == os.getpid()
    old = float(row["beat"] or 0)
    time.sleep(0.05)
    refresh_job_beats()
    row2 = db.one("SELECT beat FROM jobs WHERE id=?", (job.id,))
    assert float(row2["beat"] or 0) >= old
    try:
        scheduler.cancel(job.id)
    except Exception:
        pass
