# -*- coding: utf-8 -*-
"""规格 77：多开。先设 ORANTE_DATA。不重写 locks.py 那套锁。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-spec77-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import config, db, locks, paths, tasks  # noqa: E402
from orante import main as mainmod  # noqa: E402
from orante.errors import OranteError  # noqa: E402
from orante.filelock import MANIFEST_LOCK_SEC, manifest_lock  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import project, suites  # noqa: E402

db.init()
paths.ensure_dirs()


class FakeJob:
    def __init__(self, jid, tid=None):
        self.id = jid
        self.task_id = tid
        self.kind = "generate"

    def log(self, m):
        pass

    def check(self):
        pass


def _quiet(monkeypatch):
    monkeypatch.setattr(mainmod, "_hook_stdio", lambda: None)
    monkeypatch.setattr(mainmod, "_set_app_user_model_id", lambda: None)


def test_pick_port_skips_occupied():
    occupied = {9400}

    def classify(p, *a, **k):
        return "orante" if int(p) in occupied else "free"

    assert mainmod.pick_port(None, classify=classify, base=9400) == 9401


def test_pick_port_full_says_20():
    def classify(p, *a, **k):
        return "orante"

    with pytest.raises(mainmod.PortFull) as ei:
        mainmod.pick_port(None, classify=classify, base=9400)
    assert "20 个" in ei.value.message


def test_prefer_orante_errors_no_switch():
    with pytest.raises(mainmod.PortBusy) as ei:
        mainmod.pick_port(9401, classify=lambda p, *a, **k: "orante")
    assert ei.value.port == 9401
    assert "9401" in ei.value.message


def test_port_flag_orante_exits(monkeypatch):
    _quiet(monkeypatch)
    seen = {}

    def show(url, error="", browser=False):
        seen["error"] = error
        seen["url"] = url
        return 1

    monkeypatch.setattr(mainmod, "classify_port", lambda *a, **k: "orante")
    monkeypatch.setattr(mainmod, "show_boot_error_window", show)
    monkeypatch.setattr(mainmod, "serve", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("指定端口被占不该起服务")))
    monkeypatch.setattr(mainmod, "open_ui", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("指定端口被占不该开窗")))
    rc = mainmod.main(["--port", "9401"])
    assert rc == 1
    assert "9401" in (seen.get("error") or "")
    assert "已经有一个 Orante" in (seen.get("error") or "")


def test_two_locks_same_account_one_runs():
    db.run("DELETE FROM account_locks")
    db.run("DELETE FROM account_waiters")
    L1 = locks.AccountLock("A77a", FakeJob("J00001", "T77a"), owner_port=9401, owner_pid=11)
    L2 = locks.AccountLock("A77a", FakeJob("J00002", "T77b"), owner_port=9402, owner_pid=22)
    try:
        assert L1.acquire(wait=False) is True
        h = locks._try_take("A77a", L2.token, "J00002", "T77b", 9999, "generate", 9402, 22)
        assert h is not None
        assert h.get("kind") in ("held", "ahead")
        waiters = db.rows("SELECT * FROM account_waiters WHERE account_id=?", ("A77a",))
        assert any(w["key"] == L2.token for w in waiters)
        holder = locks.holder("A77a")
        assert holder is not None
        assert int(holder.get("owner_port") or 0) == 9401
    finally:
        L1.release()
        L2.release()


def test_stale_heartbeat_can_takeover():
    db.run("DELETE FROM account_locks")
    db.run("DELETE FROM account_waiters")
    L1 = locks.AccountLock("A77b", FakeJob("J00011", "T1"), owner_port=9401, owner_pid=11)
    L2 = locks.AccountLock("A77b", FakeJob("J00012", "T2"), owner_port=9402, owner_pid=22)
    try:
        assert L1.acquire(wait=False) is True
        L1._stop.set()
        db.run("UPDATE account_locks SET ts=? WHERE account_id=?",
               (time.time() - 100, "A77b"))
        assert L2.acquire(wait=False) is True
        holder = locks.holder("A77b")
        assert holder is not None
        assert int(holder.get("owner_port") or 0) == 9402
    finally:
        L1.release()
        L2.release()


def test_different_accounts_parallel():
    db.run("DELETE FROM account_locks")
    db.run("DELETE FROM account_waiters")
    L1 = locks.AccountLock("A77c", FakeJob("J00021"), owner_port=9401)
    L2 = locks.AccountLock("A77d", FakeJob("J00022"), owner_port=9402)
    try:
        assert L1.acquire(wait=False) is True
        assert L2.acquire(wait=False) is True
        assert locks.holder("A77c") is not None
        assert locks.holder("A77d") is not None
    finally:
        L1.release()
        L2.release()


def test_db_write_retry_third_succeeds():
    n = {"i": 0}

    def boom():
        n["i"] += 1
        if n["i"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert db.retry_write(boom, sleep=lambda _x: None) == "ok"
    assert n["i"] == 3


def test_db_write_retry_three_fails():
    n = {"i": 0}

    def boom():
        n["i"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError) as ei:
        db.retry_write(boom, sleep=lambda _x: None)
    assert "database is locked" in str(ei.value)
    assert n["i"] == 3


def test_rev_bumps_on_set_and_suite_switch():
    n0 = int(config.get("_rev") or 0)
    old_port = locks.OWNER_PORT
    try:
        locks.OWNER_PORT = 9411
        config.set("split.flow.max_chars", 123)
        n1 = int(config.get("_rev") or 0)
        assert n1 == n0 + 1
        assert int(config.get("_rev_port") or 0) == 9411
        sid = suites.current_id()
        other = suites.create("规格77套装", "测 rev", None, adopt=False)
        assert other["id"] != sid
        locks.OWNER_PORT = 9412
        suites.switch(other["id"], save=False)
        n2 = int(config.get("_rev") or 0)
        assert n2 > n1
        hint = str(config.get("_rev_hint") or "")
        assert "规格77套装" in hint
        assert int(config.get("_rev_port") or 0) == 9412
        suites.switch(sid, save=False)
    finally:
        locks.OWNER_PORT = old_port


def test_manifest_lock_blocks_then_times_out():
    assert MANIFEST_LOCK_SEC == 10
    td = Path(tempfile.mkdtemp(prefix="orante-mf-"))
    with manifest_lock(td, timeout=1):
        t0 = time.time()
        with pytest.raises(OranteError) as ei:
            with manifest_lock(td, timeout=0.4):
                pass
        assert time.time() - t0 >= 0.35
        msg = ei.value.message + (ei.value.hint or "")
        assert "文件锁" in msg or "占着" in msg


def test_manifest_save_takes_file_lock():
    td = Path(tempfile.mkdtemp(prefix="orante-mf2-"))
    m = Manifest.new("T77", "n", "flow", "A1", "short", "一段文案")
    held = {"n": 0}

    def fake(task_dir, timeout=MANIFEST_LOCK_SEC):
        held["n"] += 1
        held["timeout"] = timeout
        raise OranteError("拿不到文件锁", "被另一个窗口占着")

    with patch("orante.filelock.manifest_lock", fake):
        with pytest.raises(OranteError) as ei:
            m.save(td)
    assert held["n"] == 1
    assert held["timeout"] == 10
    assert "文件锁" in ei.value.message


def _seed_task_row():
    db.run("DELETE FROM tasks WHERE id=?", ("T77x",))
    db.run("DELETE FROM accounts WHERE id=?", ("A77x",))
    now = db.now()
    db.run(
        "INSERT INTO accounts(id,name,engine,port,enabled,created) VALUES(?,?,?,?,1,?)",
        ("A77x", "规格77号", "flow", 19977, now))
    db.run(
        "INSERT INTO tasks(id,name,engine,account_id,type,copy,image_source,status,"
        "row_order,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("T77x", "旧名", "flow", "A77x", "short", "文案文案文案", "http://example/img",
         "draft", 0, now, "2020-01-01 00:00:00"))


def test_task_row_conflict_does_not_write():
    _seed_task_row()
    r = tasks.upsert_rows([{
        "id": "T77x",
        "name": "新名不该写进去",
        "copy": "文案文案文案",
        "image_source": "http://example/img",
        "type": "短文案",
        "account": "规格77号",
        "updated": "不是这个时间",
    }])
    assert r["errors"], r
    err = r["errors"][0]
    assert err.get("conflict") or err.get("code") == "conflict"
    cur = tasks.get("T77x")
    assert cur["name"] == "旧名"
    assert "T77x" not in (r.get("saved") or [])


def test_archive_dedup_within_5_min():
    config.set("archive.auto_on", True, touch_rev=False)
    r1 = project.auto_archive_on_startup()
    r2 = project.auto_archive_on_startup()
    assert r2 and r2.get("skipped")
    # 第一次可能没任务可归档（返回 None），但 last_run 已经写上
    last = config.get("archive.last_run")
    assert last
    if r1 is not None:
        assert not r1.get("skipped")
