# -*- coding: utf-8 -*-
"""T09: one-frame decode lookahead. Overlaps cap.read with overlay compose."""
from __future__ import annotations

import threading

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.decode_prefetch")

THREAD_NAME = "cutora-decode-prefetch"
_JOIN_TIMEOUT = 5.0


def _owning_copy(frame):
    if frame is None:
        return None
    flags = getattr(frame, "flags", None)
    if flags is not None:
        try:
            if flags["OWNDATA"]:
                return frame
        except Exception:
            pass
    copy = getattr(frame, "copy", None)
    if callable(copy):
        return copy()
    return bytes(frame)


_IDLE_EXIT_S = 10.0


class _Prefetcher:
    """Q189b：每个帧状态一条常驻的预读线程。以前每读一帧就 new 一条线程（Luca 机器启动 39 s 里
    建了 1400 条线程）：线程创建 / 栈 / mimalloc 每线程堆的反复分配与遗弃，CPU 和内存都在白耗。
    闲 10 s 自动退出，下次再用重建。"""

    def __init__(self, state):
        self.state = state
        self._req = None
        self._cv = threading.Condition()
        self._busy = False
        self._alive = True
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def submit(self, cap, stop_event):
        with self._cv:
            self._req = (cap, stop_event)
            self._busy = True
            self._cv.notify_all()

    def wait(self, timeout):
        with self._cv:
            if self._busy:
                self._cv.wait_for(lambda: not self._busy, timeout=timeout)
            return not self._busy

    def alive(self):
        return self._alive and self._thread.is_alive()

    def shutdown(self, timeout=_JOIN_TIMEOUT):
        with self._cv:
            self._alive = False
            self._cv.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)

    def _run(self):
        state = self.state
        while True:
            with self._cv:
                if not self._alive:
                    return
                if self._req is None:
                    if not self._cv.wait(timeout=_IDLE_EXIT_S):
                        if self._req is None:
                            self._alive = False
                            return
                    continue
                cap, stop_event = self._req
                self._req = None
            try:
                if stop_event is not None and stop_event.is_set():
                    state["_pf_ret"] = False
                    state["_pf_frame"] = None
                else:
                    ret, frame = cap.read()
                    state["_pf_ret"] = bool(ret)
                    state["_pf_frame"] = frame if ret else None
            except Exception:
                _LOG.exception("解码预读失败")
                state["_pf_ret"] = False
                state["_pf_frame"] = None
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()


def join(state, timeout=_JOIN_TIMEOUT):
    """Wait for an in-flight prefetch. Safe to call when none is running."""
    if not isinstance(state, dict):
        return
    w = state.get("_pf_worker")
    if w is None:
        return
    if not w.wait(timeout):
        _LOG.error("解码预读线程在 %.1f s 后仍未退出", timeout)
    state["_pf_thread"] = None


def shutdown(state, timeout=_JOIN_TIMEOUT):
    """释放解码器前：等完在途预读并停掉这个状态的预读线程。"""
    if not isinstance(state, dict):
        return
    w = state.pop("_pf_worker", None)
    if w is not None:
        try:
            w.shutdown(timeout)
        except Exception:
            pass
    state["_pf_thread"] = None


def kick_read(cap, state, stop_event=None):
    """Copy last_frame, then cap.read() the next source frame on the state's prefetch thread.

    Caller must not use ``cap`` until ``join`` / ``take``. ``last_frame`` is
    copied first so the next read cannot clobber a buffer still in use.
    """
    if not isinstance(state, dict) or cap is None:
        return
    join(state)
    if stop_event is not None and stop_event.is_set():
        return
    last = state.get("last_frame")
    if last is not None:
        state["last_frame"] = _owning_copy(last)
    w = state.get("_pf_worker")
    if w is None or not w.alive():
        w = state["_pf_worker"] = _Prefetcher(state)
    state["_pf_thread"] = w._thread          # 老代码 / 测试看这个字段判断「有没有在途预读」
    w.submit(cap, stop_event)


def take(state):
    """Join and return ``(ret, frame)`` if a prefetch completed, else None."""
    if not isinstance(state, dict):
        return None
    join(state)
    if "_pf_ret" not in state:
        return None
    ret = state.pop("_pf_ret")
    frame = state.pop("_pf_frame", None)
    return ret, frame


def stash(state, result):
    """Keep an unused prefetch so a later take() can consume it."""
    if not isinstance(state, dict) or result is None:
        return
    ret, frame = result
    state["_pf_ret"] = ret
    state["_pf_frame"] = frame


def join_all(states, timeout=_JOIN_TIMEOUT):
    if not states:
        return
    for st in states:
        join(st, timeout=timeout)


def shutdown_all(states, timeout=_JOIN_TIMEOUT):
    if not states:
        return
    for st in states:
        shutdown(st, timeout=timeout)
