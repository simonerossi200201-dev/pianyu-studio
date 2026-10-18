# -*- coding: utf-8 -*-
"""进程内事件总线 → SSE。任何模块 emit()，前端 /api/events 收。"""
from __future__ import annotations

import json
import queue
import threading
import time

_subs: list[queue.Queue] = []
_lock = threading.Lock()
_recent: list[dict] = []          # 最近 500 条，新连上的前端先补一遍
RECENT_MAX = 500


def emit(kind: str, **data) -> None:
    ev = {"kind": kind, "t": time.time(), **data}
    with _lock:
        _recent.append(ev)
        if len(_recent) > RECENT_MAX:
            del _recent[: len(_recent) - RECENT_MAX]
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _lock:
        _subs.append(q)
        for ev in _recent[-100:]:
            q.put_nowait(ev)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subs:
            _subs.remove(q)


def sse_iter(q: queue.Queue):
    """生成 SSE 帧；每 15 秒发一个心跳注释防止代理断开。"""
    try:
        while True:
            try:
                ev = q.get(timeout=15)
                yield f"event: {ev['kind']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": ping\n\n"
    finally:
        unsubscribe(q)
