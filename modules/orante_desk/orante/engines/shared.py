# -*- coding: utf-8 -*-
"""规格 87：同一个 Grok 账号上几个小任务一起出片时，共用一套引擎。

为什么不能各开各的：每个 Job 在自己的线程里跑，Playwright 同步对象只能在创建它的线程里用；
两个线程各连一次同一个 Chrome、同时去铺页 / 上传 / 点时长，会互相踩。

做法：
  · 每个账号（本进程内）最多一个 SharedEngine。它自己开一个专用线程，真正的引擎对象
    （make_engine 造出来的那个）只在这个线程里创建、调用、关闭。
  · 每个 Job 拿到的是 EngineProxy。代理上的每个方法调用都被投递到专用线程排队执行，
    调用方阻塞等结果，异常原样抛回调用方线程。所以 Chrome 永远只有一只"手"。
  · 调用期间把真引擎的 log 临时换成调用方 Job 的 log，引擎打的日志落到发起调用的那个任务里。
  · capacity() 是真引擎上的 max_conc - len(_active)，_active 里是所有任务在飞的段，
    所以每个任务照旧 `while queue and eng.capacity() > 0` 就不会超发。
  · 引用计数：最后一个 Job 还回来时，在专用线程里 close() 真引擎、结束线程。
"""
from __future__ import annotations

import queue as _queue
import threading
from typing import Any

from .base import make_engine

_registry: dict[str, "SharedEngine"] = {}
_reg_lock = threading.Lock()

# 这些方法在代理上有特殊语义，不直接转发
_NOOP_ON_PROXY = {"close"}


class _Call:
    __slots__ = ("fn", "args", "kwargs", "log", "done", "result", "error")

    def __init__(self, fn, args, kwargs, log):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.log = log
        self.done = threading.Event()
        self.result = None
        self.error: BaseException | None = None


class SharedEngine:
    def __init__(self, account: dict, make=None):
        self.account = dict(account)
        self.account_id = str(account.get("id") or "")
        self._make = make  # None → 运行时取模块里的 make_engine（测试可打补丁）
        self._q: _queue.Queue = _queue.Queue()
        self._eng = None
        self._default_log = lambda msg: None
        self.refs: dict[str, Any] = {}       # job_id -> job
        self.connected = False
        self.credits_begun = False            # Flow 额度核对：整组只在第一个 Job 起算一次
        self._connect_lock = threading.Lock()
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name=f"orante-engine-{self.account_id}")
        self.thread.start()
        self._ready.wait()
        if self._init_error is not None:
            raise self._init_error

    # ---- 专用线程 ----
    def _loop(self) -> None:
        try:
            mk = self._make or make_engine
            self._eng = mk(self.account, self._route_log)
        except BaseException as e:  # noqa: BLE001
            self._init_error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            call = self._q.get()
            if call is None:
                break
            eng = self._eng
            old = getattr(eng, "log", None)
            try:
                if call.log is not None:
                    eng.log = call.log
                call.result = call.fn(eng, *call.args, **call.kwargs)
            except BaseException as e:  # noqa: BLE001
                call.error = e
            finally:
                try:
                    eng.log = old
                except Exception:
                    pass
                call.done.set()

    def _route_log(self, msg: str) -> None:
        # 调用之外（理论上不会有）打的日志兜底丢给默认 log
        try:
            self._default_log(msg)
        except Exception:
            pass

    # ---- 调用方线程 ----
    def run(self, fn, *args, log=None, **kwargs):
        """在专用线程里执行 fn(engine, *args, **kwargs)，阻塞等结果。"""
        if threading.current_thread() is self.thread:
            # 不应发生：引擎内部回调里又调代理。直接执行，避免自己等自己。
            return fn(self._eng, *args, **kwargs)
        call = _Call(fn, args, kwargs, log)
        self._q.put(call)
        call.done.wait()
        if call.error is not None:
            raise call.error
        return call.result

    def call(self, name: str, *args, log=None, **kwargs):
        return self.run(lambda eng, *a, **k: getattr(eng, name)(*a, **k), *args, log=log, **kwargs)

    def peek(self, name: str):
        """读真引擎上的普通属性（name / can_gen_images / _active 之类）。"""
        return getattr(self._eng, name)

    def connect(self, log=None) -> None:
        with self._connect_lock:
            if self.connected:
                return
            self.call("connect", log=log)
            self.connected = True

    def shutdown(self) -> None:
        if self.thread is None:
            return
        try:
            if self.connected:
                self.call("close")
        except Exception:
            pass
        self.connected = False
        self._q.put(None)
        self.thread.join(timeout=30)


class EngineProxy:
    """发给每个 Job 的"引擎"。接口和真引擎一样，调用全都转到专用线程。"""

    def __init__(self, shared: SharedEngine, job):
        object.__setattr__(self, "_shared", shared)
        object.__setattr__(self, "_job", job)
        object.__setattr__(self, "shared_proxy", True)

    def _log(self):
        job = object.__getattribute__(self, "_job")
        return getattr(job, "log", None)

    def connect(self) -> None:
        sh = object.__getattribute__(self, "_shared")
        sh.connect(log=self._log())

    def close(self) -> None:
        # 共享引擎由 release() 统一关，代理上的 close 什么都不做
        return None

    @property
    def log(self):
        # 调用方（notify_credit_halt 之类）拿 eng.log 打日志时，打到这个 Job 自己的日志里
        return self._log()

    def begin_credits(self, job=None):
        """Flow 额度核对的起点（B0）。

        拼单时整组共用一个引擎：只在第一个 Job 起算一次。后加入的 Job 如果再起算，
        会把 B0 挪到"现在"，把加入前已经发生的异常扣费一笔勾销——所以后来的只抄现有的 B0。
        引擎没有这个方法（Grok）时什么都不做。
        """
        sh = object.__getattribute__(self, "_shared")
        try:
            sh.peek("begin_credits")
        except AttributeError:
            return None
        with sh._connect_lock:
            if not sh.credits_begun:
                sh.credits_begun = True
                return sh.call("begin_credits", job, log=self._log())
        try:
            b0 = sh.peek("_credit_b0")
        except AttributeError:
            b0 = None
        if job is not None:
            try:
                job.credit_b0 = b0
            except Exception:
                pass
        return b0

    def __getattr__(self, name: str):
        sh = object.__getattribute__(self, "_shared")
        val = sh.peek(name)          # 没有这个属性 → AttributeError，getattr(eng, x, None) 照常拿到 None
        if callable(val):
            log = self._log()

            def _bound(*args, **kwargs):
                return sh.call(name, *args, log=log, **kwargs)
            _bound.__name__ = name
            return _bound
        return val

    def __setattr__(self, name, value):
        sh = object.__getattribute__(self, "_shared")
        sh.run(lambda eng: setattr(eng, name, value))


def acquire(account: dict, job, make=None) -> EngineProxy:
    """拿这个账号的共享引擎代理。第一次会建引擎（还没连，调用方自己 connect）。"""
    aid = str(account.get("id") or "")
    with _reg_lock:
        sh = _registry.get(aid)
        if sh is None:
            sh = SharedEngine(account, make=make)
            _registry[aid] = sh
        sh.refs[getattr(job, "id", str(id(job)))] = job
    return EngineProxy(sh, job)


def release(job, account_id: str | None = None) -> None:
    """还回来。最后一个还的人负责关引擎（在专用线程里 close）。"""
    jid = getattr(job, "id", str(id(job)))
    with _reg_lock:
        targets = ([account_id] if account_id else list(_registry))
        for aid in targets:
            sh = _registry.get(aid)
            if sh is None or jid not in sh.refs:
                continue
            sh.refs.pop(jid, None)
            if not sh.refs:
                _registry.pop(aid, None)
                sh.shutdown()
            return


def users(account_id: str) -> list[str]:
    with _reg_lock:
        sh = _registry.get(str(account_id))
        return list(sh.refs) if sh else []


def _reset_for_tests() -> None:
    with _reg_lock:
        for sh in list(_registry.values()):
            try:
                sh.shutdown()
            except Exception:
                pass
        _registry.clear()
