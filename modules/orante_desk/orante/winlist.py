# -*- coding: utf-8 -*-
"""窗口登记表：每个开着的 Orante 窗口在 data/windows/ 下留一条记录。

悬浮图标（orante/floaticon.py）是独立进程，它靠这张表认得所有窗口：
谁还活着、窗口句柄是多少、是不是全都最小化了。

一条记录就是一个 JSON 文件，文件名是端口号（9400.json / 9401.json），
内容 {"port","pid","hwnd","title","beat"}。心跳过期（默认 30 秒）就当这个窗口没了，
所以进程被强杀、掉电、忘了注销，都不会留下假记录，也不需要谁去打扫。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STALE = 30.0          # 超过这么久没心跳 = 这个窗口不算数了
BEAT_EVERY = 8.0      # 主程序心跳线程的间隔


def dir_() -> Path:
    from . import paths
    return paths.data_dir() / "windows"


def path_for(port: int) -> Path:
    return dir_() / f"{int(port)}.json"


def write(port: int, *, pid: int | None = None, hwnd: int = 0, title: str = "") -> None:
    """登记 / 刷新一条。hwnd=0 表示窗口还没建起来（浏览器模式就一直是 0）。"""
    rec = {
        "port": int(port),
        "pid": int(pid if pid is not None else os.getpid()),
        "hwnd": int(hwnd or 0),
        "title": str(title or ""),
        "beat": time.time(),
    }
    d = dir_()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{int(port)}.tmp"
    try:
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path_for(port))
    except OSError:
        # 登记失败不能影响开窗：大不了这一轮没有悬浮图标
        try:
            tmp.unlink()
        except OSError:
            pass


def remove(port: int) -> None:
    try:
        path_for(port).unlink()
    except OSError:
        pass


def read_all() -> list[dict]:
    out: list[dict] = []
    d = dir_()
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json")):
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("port"):
            out.append(rec)
    return out


def live(records: list[dict] | None = None, *, now: float | None = None,
         stale: float = STALE) -> list[dict]:
    """心跳还新鲜的那些，按端口排序。"""
    now = time.time() if now is None else float(now)
    recs = read_all() if records is None else list(records)
    fresh = [r for r in recs
             if isinstance(r, dict) and (now - float(r.get("beat") or 0)) <= stale]
    fresh.sort(key=lambda r: int(r.get("port") or 0))
    return fresh
