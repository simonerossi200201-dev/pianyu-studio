# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q189f：极轻的活动计数器。空闲时进程还在烧 CPU 的时候，日志里要能回答「这 10 分钟里谁动了多少次」：
时间线重画、画布重画、缩略图请求 / ffmpeg 抽帧、预览工作线程的请求……bump() 是一次 dict 自增，随处可调。"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_C: dict = {}
_PREV: dict = {}


def bump(name: str, n: int = 1) -> None:
    try:
        _C[name] = _C.get(name, 0) + int(n)
    except Exception:
        pass


def value(name: str) -> int:
    try:
        return int(_C.get(name, 0) or 0)
    except Exception:
        return 0


def delta_note() -> str:
    """自上次调用以来各计数器的增量，按大小排序；用于每 10 分钟的 [Memory] 行。"""
    global _PREV
    with _LOCK:
        cur = dict(_C)
        prev = _PREV
        _PREV = cur
    d = sorted(((k, cur[k] - prev.get(k, 0)) for k in cur), key=lambda kv: -kv[1])
    d = [(k, v) for k, v in d if v > 0]
    if not d:
        return "活动计数：无"
    return "活动计数：" + "、".join("%s %d" % (k, v) for k, v in d[:14])
