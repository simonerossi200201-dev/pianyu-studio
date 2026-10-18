# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""显卡任务排队（Q152-D 第二批）：AI 字幕、AI 抠像、AI 跟踪这些吃显存的任务一个一个来。

先到先得（取号排队），后来的在**自己的工作线程里**等，界面不卡；等的时候回调 on_wait(当前占用者名字)，
可以随时用 should_stop() 放弃排队。没有 Qt，任何线程都能用。

    with hold("AI 抠像", should_stop=lambda: cancel.is_set(), on_wait=lambda who: ...) as ok:
        if not ok:      # 排队时被取消
            return
        ...
"""
from __future__ import annotations

import contextlib
import itertools
import threading
import time

_COND = threading.Condition()
_TICKETS = itertools.count(1)
_STATE = {"holder": "", "ticket": 0, "since": 0.0}
_QUEUE: list = []          # [(ticket, owner)] 按取号顺序


def holder() -> str:
    with _COND:
        return _STATE["holder"]


def waiting() -> list:
    """排队中的任务名（按顺序）。"""
    with _COND:
        return [o for _t, o in _QUEUE]


def busy() -> bool:
    with _COND:
        return bool(_STATE["holder"])


def acquire(owner: str, *, should_stop=None, on_wait=None, poll: float = 0.2, timeout: float | None = None) -> int:
    """排队拿显卡。返回票号（> 0）= 拿到了；0 = 排队中被 should_stop 取消 / 超时。"""
    owner = str(owner or "任务")
    with _COND:
        ticket = next(_TICKETS)
        _QUEUE.append((ticket, owner))
        told = False
        t0 = time.monotonic()
        try:
            while True:
                if not _STATE["holder"] and _QUEUE and _QUEUE[0][0] == ticket:
                    _QUEUE.pop(0)
                    _STATE.update(holder=owner, ticket=ticket, since=time.monotonic())
                    return ticket
                if should_stop is not None:
                    try:
                        if should_stop():
                            break
                    except Exception:
                        break
                if timeout is not None and time.monotonic() - t0 >= timeout:
                    break
                if on_wait is not None and not told:
                    told = True
                    who = _STATE["holder"] or (_QUEUE[0][1] if _QUEUE else "")
                    _COND.release()
                    try:
                        on_wait(who)
                    except Exception:
                        pass
                    finally:
                        _COND.acquire()
                    continue
                _COND.wait(poll)
        finally:
            # 没拿到就把号撤掉（拿到的分支已经 pop 过）
            for i, (t, _o) in enumerate(_QUEUE):
                if t == ticket:
                    _QUEUE.pop(i)
                    _COND.notify_all()
                    break
        return 0


def release(ticket: int) -> None:
    with _COND:
        if ticket and _STATE["ticket"] == ticket:
            _STATE.update(holder="", ticket=0, since=0.0)
            _COND.notify_all()


@contextlib.contextmanager
def hold(owner: str, *, should_stop=None, on_wait=None, timeout: float | None = None):
    ticket = acquire(owner, should_stop=should_stop, on_wait=on_wait, timeout=timeout)
    try:
        yield bool(ticket)
    finally:
        if ticket:
            release(ticket)


def reset_for_tests() -> None:
    with _COND:
        _STATE.update(holder="", ticket=0, since=0.0)
        _QUEUE.clear()
        _COND.notify_all()
