# -*- coding: utf-8 -*-
"""规格 49：测试不得写真实库；conftest + db.conn 安全网；purge 只动夹具。"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-49-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from orante import db, paths  # noqa: E402

db.init()

REAL_DB = ROOT / "data" / "orante.db"


def _count_real_tasks() -> int:
    """只读真实库，不走 orante.db.conn（那个在测试进程里会被安全网拦住）。"""
    if not REAL_DB.is_file():
        return 0
    con = sqlite3.connect(str(REAL_DB))
    try:
        return int(con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
    finally:
        con.close()


def _child_env() -> dict:
    env = os.environ.copy()
    env.pop("ORANTE_DATA", None)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _write(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return p


def test_conftest_insert_does_not_touch_real_db() -> None:
    """子进程先收集「不设 ORANTE_DATA 直接 import」，再收集会 INSERT 的模块。
    子目录自己的 conftest 与 tools/conftest.py 同一套 setdefault。"""
    before = _count_real_tasks()
    iso = Path(tempfile.mkdtemp(prefix="orante-iso49-conf-"))
    _write(iso, "conftest.py", f"""
        import os, sys, tempfile
        from pathlib import Path
        ROOT = Path({str(ROOT)!r})
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("ORANTE_DATA", tempfile.mkdtemp(prefix="orante-pytest-"))
        """)
    _write(iso, "test_a_plain.py", f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from orante import db, paths  # 不设 ORANTE_DATA

        def test_imported():
            assert paths.data_dir() is not None
            assert not paths.is_under_real_data(paths.db_file())
        """)
    _write(iso, "test_b_insert.py", f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from orante import db

        def test_insert():
            db.init()
            db.run(
                "INSERT INTO tasks(id,name,status,created,updated) VALUES(?,?,?,?,?)",
                ("Tiso49", "iso-insert", "draft", "t", "t"))
            assert db.one("SELECT id FROM tasks WHERE id=?", ("Tiso49",))
        """)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(iso / "test_a_plain.py"),
         str(iso / "test_b_insert.py")],
        cwd=str(iso), env=_child_env(), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"conftest 子进程失败\n{r.stdout}\n{r.stderr}"
    after = _count_real_tasks()
    assert after == before, f"真实 tasks 行数变了：{before} → {after}\n{r.stdout}\n{r.stderr}"


def test_guard_raises_without_conftest() -> None:
    """绕过 conftest：不设 ORANTE_DATA，db.init() 必须抛 RuntimeError。"""
    before = _count_real_tasks()
    iso = Path(tempfile.mkdtemp(prefix="orante-iso49-noconf-"))
    _write(iso, "test_bare_init.py", f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        import pytest
        from orante import db

        def test_blocked():
            with pytest.raises(RuntimeError, match="先设 ORANTE_DATA"):
                db.init()
        """)
    env = _child_env()
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest",
         str(iso / "test_bare_init.py")],
        cwd=str(iso), env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"安全网子进程失败\n{r.stdout}\n{r.stderr}"
    after = _count_real_tasks()
    assert after == before, f"绕过 conftest 仍写了真实库：{before} → {after}"


def test_guard_direct() -> None:
    """本进程故意清掉 ORANTE_DATA，conn() 当场拦住。"""
    old_env = os.environ.get("ORANTE_DATA")
    old_conn = getattr(db._local, "conn", None)
    db._local.conn = None
    try:
        os.environ.pop("ORANTE_DATA", None)
        paths.reset()
        with pytest.raises(RuntimeError, match="先设 ORANTE_DATA"):
            db.conn()
    finally:
        if old_env is not None:
            os.environ["ORANTE_DATA"] = old_env
        paths.reset()
        db._local.conn = old_conn


def test_purge_only_fixtures(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("ORANTE_DATA", str(data))
    old_conn = getattr(db._local, "conn", None)
    db._local.conn = None
    try:
        paths.reset()
        db.init()
        temp = Path(tempfile.gettempdir())
        fx1 = Path(tempfile.mkdtemp(prefix="orante-task-track-", dir=str(temp)))
        fx2 = Path(tempfile.mkdtemp(prefix="orante-task-22-", dir=str(temp)))
        real = data / "workspace" / "T9C-real"
        real.mkdir(parents=True)

        def ins(tid, name, d):
            db.run(
                "INSERT INTO tasks(id,name,image_source,dir,status,created,updated)"
                " VALUES(?,?,?,?,?,?,?)",
                (tid, name, str(d / "images"), str(d), "draft", "t", "t"))

        ins("T9A", "配图轨测", fx1)
        ins("T9B", "规格22", fx2)
        ins("T9C", "李良v04", real)
        db.run(
            "INSERT INTO jobs(id,task_id,kind,status) VALUES(?,?,?,?)",
            ("J9A", "T9A", "track", "done"))

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "purge_test_tasks", ROOT / "tools" / "purge_test_tasks.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        listed = mod.find_targets()
        assert {r["id"] for r in listed} == {"T9A", "T9B"}

        rc = mod.main(["--dry-run"])
        assert rc == 0
        assert db.one("SELECT COUNT(*) AS n FROM tasks")["n"] == 3
        assert db.one("SELECT 1 FROM jobs WHERE id=?", ("J9A",))

        rc = mod.main(["--yes"])
        assert rc == 0
        left = db.rows("SELECT id, name FROM tasks")
        assert [r["id"] for r in left] == ["T9C"]
        assert db.one("SELECT 1 FROM jobs WHERE id=?", ("J9A",)) is None
    finally:
        db._local.conn = old_conn
        if os.environ.get("ORANTE_DATA") == str(data):
            pass
        paths.reset()
