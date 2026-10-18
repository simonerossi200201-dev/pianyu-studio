# -*- coding: utf-8 -*-
"""「这批数据存到哪」：根目录 / 日期 / 版本 A、B、C… 永不覆盖。"""
from __future__ import annotations

import re
import string
from datetime import date
from pathlib import Path

from . import config, db
from .errors import OranteError

_SAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s: str, limit: int = 40) -> str:
    s = _SAFE.sub("_", (s or "").strip()).strip(" ._")
    return (s or "untitled")[:limit]


def new_batch(root: str | None = None) -> dict:
    """在 根\\YYYY-MM-DD\\<版本> 下开一个新版本文件夹并登记到 batches。"""
    root_p = Path(root or config.get("workspace.root")).expanduser()
    try:
        root_p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OranteError(f"建不了工作区目录 {root_p}", str(e))
    day = date.today().isoformat()
    day_dir = root_p / day if config.get("workspace.by_date") else root_p
    day_dir.mkdir(parents=True, exist_ok=True)
    for letter in string.ascii_uppercase:
        cand = day_dir / letter
        existing_b = db.one("SELECT id, dir FROM batches WHERE day=? AND version=?", (day, letter))
        if not cand.exists() or (not any(cand.iterdir()) and not existing_b):
            cand.mkdir(parents=True, exist_ok=True)
            bid = existing_b["id"] if existing_b else db.next_id("batch", "B")
            if not existing_b:
                db.run("INSERT INTO batches(id,root,day,version,dir,created) VALUES(?,?,?,?,?,?)",
                       (bid, str(root_p), day, letter, str(cand), db.now()))
            config.set("workspace.root", str(root_p))
            return {"id": bid, "dir": str(cand), "day": day, "version": letter}
        if existing_b and not any(cand.iterdir()):
            return {"id": existing_b["id"], "dir": str(cand), "day": day, "version": letter}
    raise OranteError("今天的版本文件夹已经用到 Z 了", "换个工作区根目录，或者清理一下。")


def task_dir(batch_dir: str, task_id: str, name: str) -> Path:
    d = Path(batch_dir) / f"{task_id}-{safe_name(name)}"
    for sub in ("images", "空镜头", "clips", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d
