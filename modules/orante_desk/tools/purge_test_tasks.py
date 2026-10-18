# -*- coding: utf-8 -*-
"""清掉测试夹具写进任务库的行。默认 --dry-run，加 --yes 才真删。

只删同时满足：
  1. name 是「配图轨测」或「规格22」
  2. image_source 或 dir 位于 %TEMP% 下的 orante-task-* 目录
两条都成立才动，避免误伤用户真实任务。不动 counters。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402

TEST_NAMES = frozenset({"配图轨测", "规格22"})
RELATED_TABLES = ("jobs", "account_locks", "account_waiters")


def is_orante_task_temp(raw: str | None) -> bool:
    """路径是否在系统临时目录下，且某一段以 orante-task- 开头。目录不需要还在。"""
    if not raw:
        return False
    try:
        abs_p = Path(os.path.abspath(str(raw)))
        abs_t = Path(os.path.abspath(str(tempfile.gettempdir())))
    except Exception:
        return False
    child = os.path.normcase(str(abs_p))
    parent = os.path.normcase(str(abs_t))
    sep = os.sep
    parent_sep = parent if parent.endswith(sep) else parent + sep
    if not (child == parent or child.startswith(parent_sep)):
        return False
    return any(part.startswith("orante-task-") for part in abs_p.parts)


def find_targets(rows: list[dict] | None = None) -> list[dict]:
    if rows is None:
        rows = db.rows("SELECT id, name, dir, image_source FROM tasks")
    out = []
    for r in rows:
        if (r.get("name") or "") not in TEST_NAMES:
            continue
        if is_orante_task_temp(r.get("image_source")) or is_orante_task_temp(r.get("dir")):
            out.append(r)
    return out


def _wipe_related(task_id: str) -> None:
    """SCHEMA 里 jobs/locks/waiters 没有 ON DELETE CASCADE，手动清。"""
    for table in RELATED_TABLES:
        try:
            db.run(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        except Exception:
            pass


def purge(ids: list[str]) -> None:
    for tid in ids:
        _wipe_related(tid)
        taskmod.delete(tid, hard=True)


def _fmt(r: dict) -> str:
    return f"  {r.get('id'):<8} {r.get('name'):<8} {r.get('dir') or ''}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="清掉测试夹具写进任务库的行（默认只列出）")
    p.add_argument("--yes", action="store_true", help="真删；默认只列出")
    p.add_argument("--dry-run", action="store_true", help="只列出（默认就是）")
    args = p.parse_args(argv)
    dry = not args.yes

    db.init()
    targets = find_targets()
    if not targets:
        n = db.one("SELECT COUNT(*) AS n FROM tasks")["n"]
        print("没有匹配的测试夹具。")
        print(f"剩余任务数：{n}")
        return 0

    print(f"{'将删' if dry else '删除'} {len(targets)} 条测试夹具：")
    for r in targets:
        print(_fmt(r))

    if dry:
        print("（未加 --yes，没有真删）")
    else:
        purge([r["id"] for r in targets])
        print("已硬删除（jobs / account_locks / account_waiters 一并清）。")

    n = db.one("SELECT COUNT(*) AS n FROM tasks")["n"]
    print(f"剩余任务数：{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
