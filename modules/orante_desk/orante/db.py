# -*- coding: utf-8 -*-
"""SQLite：连接、建表、迁移。线程安全（每线程一个连接）。"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import paths

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, engine TEXT NOT NULL CHECK(engine IN ('flow','grok')),
  port INTEGER UNIQUE, profile TEXT, project_url TEXT, model TEXT, aspect TEXT DEFAULT '9:16',
  max_concurrent INTEGER DEFAULT 5, enabled INTEGER DEFAULT 1, extra TEXT DEFAULT '{}', created TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, name TEXT, engine TEXT, account_id TEXT, type TEXT CHECK(type IN ('long','short')),
  copy TEXT, image_source TEXT, duration_pref TEXT, expand_account_id TEXT,
  status TEXT DEFAULT 'draft', dir TEXT, batch_id TEXT, row_order INTEGER DEFAULT 0,
  created TEXT, updated TEXT, meta TEXT DEFAULT '{}', gen_mode TEXT);
CREATE TABLE IF NOT EXISTS batches(
  id TEXT PRIMARY KEY, root TEXT, day TEXT, version TEXT, dir TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, task_id TEXT, kind TEXT, status TEXT, started TEXT, finished TEXT, log TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER);
CREATE TABLE IF NOT EXISTS account_locks(
  account_id TEXT PRIMARY KEY, owner TEXT, job_id TEXT, task_id TEXT, prio INTEGER, ts REAL,
  owner_port INTEGER, owner_pid INTEGER);
CREATE TABLE IF NOT EXISTS account_waiters(
  key TEXT PRIMARY KEY, account_id TEXT NOT NULL, job_id TEXT, task_id TEXT, label TEXT,
  prio INTEGER DEFAULT 9999, seq INTEGER DEFAULT 0, ts REAL);
CREATE INDEX IF NOT EXISTS idx_waiters_acc ON account_waiters(account_id, prio, seq);
CREATE TABLE IF NOT EXISTS ai_providers(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  base_url TEXT, api_key TEXT, model TEXT,
  priority INTEGER DEFAULT 1, max_inflight INTEGER DEFAULT 2,
  daily_limit INTEGER DEFAULT 0, vision INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1, row_order INTEGER DEFAULT 0,
  extra TEXT DEFAULT '{}', created TEXT);
CREATE TABLE IF NOT EXISTS ai_leases(
  key TEXT PRIMARY KEY, provider_id TEXT NOT NULL, owner TEXT, job_id TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS idx_ai_leases_p ON ai_leases(provider_id);
CREATE TABLE IF NOT EXISTS ai_state(
  provider_id TEXT PRIMARY KEY, cool_until REAL DEFAULT 0, cool_why TEXT,
  day TEXT, calls INTEGER DEFAULT 0, fails INTEGER DEFAULT 0,
  last_error TEXT, last_ts REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS desc_cards(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT,
  sort INTEGER DEFAULT 0, builtin INTEGER DEFAULT 0,
  created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS suites(
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, note TEXT,
  color TEXT, builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 0,
  used_at TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS suite_data(
  suite_id TEXT NOT NULL, grp TEXT NOT NULL, payload TEXT,
  PRIMARY KEY(suite_id, grp));
"""

# 老库里加字段用的：表名 → [(字段, 定义), ...]。加过就跳过，反复跑没关系。
ADD_COLUMNS = {
    "jobs": [
        ("owner_pid", "INTEGER"),
        ("owner_port", "INTEGER"),
        ("beat", "REAL"),
    ],
    "tasks": [
        ("priority", "INTEGER DEFAULT 0"),      # 0 = 自动；>0 = 旗子上的序号
        ("archived", "INTEGER DEFAULT 0"),      # 1 = 从工程文件导回来的只读任务
        ("gen_mode", "TEXT"),                   # 空 = 跟随引擎；first_frame / anchor_bridge
        ("run_to_completion", "INTEGER DEFAULT 1"),  # 1 = 被拒也接力到全部出片
    ],
    "account_locks": [
        ("prio", "INTEGER"),
        ("owner_port", "INTEGER"),   # 持有者那个 Orante 的端口
        ("owner_pid", "INTEGER"),    # 持有者进程号
    ],
    "suites": [("used_at", "TEXT")],
}

# database is locked → 退避重试 3 次。只包写。
WRITE_RETRY_DELAYS = (0.08, 0.24, 0.6)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _in_test_process() -> bool:
    """pytest 进程，或直接 python tools/test_*.py。"""
    if "pytest" in sys.modules:
        return True
    if not sys.argv:
        return False
    try:
        p = Path(sys.argv[0])
    except Exception:
        return False
    name = p.name
    if not (name.startswith("test_") and name.endswith(".py")):
        return False
    parts = [x.lower() for x in p.parts]
    if "tools" in parts:
        return True
    try:
        return p.resolve().parent == (paths.ROOT / "tools").resolve()
    except Exception:
        return False


def _guard_real_db() -> None:
    """测试进程不许碰真实库。宁可挂掉，也不再污染 data/orante.db。"""
    if not _in_test_process():
        return
    if not paths.is_under_real_data(paths.DB_FILE):
        return
    raise RuntimeError("测试进程不许碰真实库 data/orante.db，先设 ORANTE_DATA")


def conn() -> sqlite3.Connection:
    paths.reset()
    _guard_real_db()
    want = str(paths.DB_FILE)
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "db_path", None) != want:
        try:
            c.close()
        except Exception:
            pass
        c = None
        _local.conn = None
    if c is None:
        paths.ensure_dirs()
        c = sqlite3.connect(want, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass                      # 网络盘/FUSE 上 WAL 可能不可用，退回默认日志
        try:
            c.execute("PRAGMA busy_timeout=30000")   # 和 connect(timeout=30) 双保险
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("PRAGMA synchronous=NORMAL")   # WAL 下够用，写快很多
        except sqlite3.OperationalError:
            pass
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
        _local.db_path = want
    return c


def _migrate(c) -> None:
    """老库补字段。SCHEMA 里的 CREATE TABLE IF NOT EXISTS 管不到已存在的表。"""
    import re
    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    decl_re = re.compile(r"^[A-Za-z0-9_ '.\-()]+$")
    for table, cols in ADD_COLUMNS.items():
        if not ident_re.fullmatch(table):
            continue
        try:
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        if not have:
            continue
        for name, decl in cols:
            if name in have or not ident_re.fullmatch(name) or not decl_re.fullmatch(decl):
                continue
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            except Exception:
                pass
    c.commit()



def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    _migrate(c)
    try:
        from .services import desc_cards
        desc_cards.seed_if_empty()
    except Exception:
        pass
    try:
        from .services import suites
        suites.ensure_builtin()
    except Exception:
        pass


def _is_locked(e: BaseException) -> bool:
    s = str(e).lower()
    return "database is locked" in s or "database is busy" in s


def retry_write(fn, *, delays=WRITE_RETRY_DELAYS, sleep=time.sleep):
    """写失败遇 database is locked 就退避重试 3 次（80/240/600 ms），仍失败才抛。"""
    last = None
    n = len(delays)
    for i in range(n):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if not _is_locked(e):
                raise
            last = e
            if i + 1 >= n:
                break
            sleep(delays[i])
    raise last


def rows(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql: str, args=()) -> None:
    def _do():
        c = conn()
        c.execute(sql, args)
        c.commit()
    retry_write(_do)


def next_id(name: str, prefix: str, width: int = 4) -> str:
    """发号：T0001 / A0001 / J0001，全局递增。

    用 BEGIN IMMEDIATE 把「读 + 写」包成一个写事务：同时开几个 Orante 窗口时，
    两个进程也不会拿到同一个号。
    """
    def _do():
        c = conn()
        with _lock:
            if c.in_transaction:
                c.commit()
            c.execute("BEGIN IMMEDIATE")
            try:
                r = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
                v = (r["value"] if r else 0) + 1
                c.execute("INSERT OR REPLACE INTO counters(name,value) VALUES(?,?)", (name, v))
                c.commit()
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass
                raise
            return f"{prefix}{v:0{width}d}"
    return retry_write(_do)


_lock = threading.Lock()


def j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def unj(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
