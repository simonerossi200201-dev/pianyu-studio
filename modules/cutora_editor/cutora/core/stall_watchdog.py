# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q170 · 界面线程卡住就把「卡在哪一行」写进日志。

「软件卡死」最难的是不知道卡在哪。这里开一条守护线程，每 50 ms 看一眼界面线程的心跳
（界面线程上一个 QTimer 每 100 ms 写一次时间戳）。心跳超过 ``threshold_ms`` 没更新 = 界面线程被
什么东西堵住了 → 用 ``sys._current_frames()`` 取界面线程此刻的 Python 调用栈记下来；
一直堵着就每隔一会儿再采一次；解开后把「卡了多久 + 卡住时的栈」写进 cutora.log（``cutora.stall``）。
真卡死（一直不恢复）时也在 3 / 10 / 30 / 60 秒各写一次「此刻卡在哪」，之后每分钟一次。

开销：守护线程每 50 ms 一次时间比较；只有真卡住了才取栈。
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

from cutora.core.log import get_logger

_LOG = get_logger("cutora.stall")
_STATE = {"watchdog": None}
DEFAULT_THRESHOLD_MS = 500
MAX_FRAMES = 22
LIVE_MARKS = (3.0, 10.0, 30.0, 60.0)    # 还卡着的时候在这些秒数各写一次日志（之后每 60 秒一次）


def _fmt_stack(frame) -> str:
    """最内层在最上面；cutora 自己的帧全留，库里的帧只留最内 4 层（看得出是哪个库）。"""
    rows = traceback.extract_stack(frame)
    rows = list(reversed(rows))[:60]
    out = []
    lib_kept = 0
    for fs in rows:
        mine = "cutora" in fs.filename.replace("\\", "/")
        if not mine:
            if lib_kept >= 4:
                continue
            lib_kept += 1
        out.append("    %s:%d %s" % (fs.filename.replace("\\", "/").rsplit("/", 2)[-1] if mine else fs.filename.rsplit("/", 1)[-1],
                                     fs.lineno, fs.name))
        if len(out) >= MAX_FRAMES:
            break
    return "\n".join(out)


def _annotate(stack: str) -> str:
    """最内层就是事件循环（app.exec 那一行）= 此刻没有 Python 代码在跑，卡在 Qt 的 C++ 里（画字、排版、系统字体渲染……）。"""
    first = (stack or "").strip().splitlines()[:1]
    if first and ("run_gui_startup" in first[0] or " exec" in first[0]):
        return "    （没有 Python 代码在跑 → 卡在 Qt 内部的 C++ 里：画控件 / 排版 / 系统字体渲染）\n" + stack
    return stack


class StallWatchdog:
    def __init__(self, threshold_ms: int = DEFAULT_THRESHOLD_MS, poll_ms: int = 50):
        self.threshold = max(0.1, float(threshold_ms) / 1000.0)
        self.poll = max(0.01, float(poll_ms) / 1000.0)
        self._beat = time.monotonic()
        self._gui_tid = threading.main_thread().ident
        self._stop = threading.Event()
        self._thread = None
        self._timer = None
        self.stalls: list[dict] = []          # 最近几次：{"seconds", "stack"}（给界面 / 测试看）
        self.on_stall = None                  # callable(seconds, stack_text) — 在守护线程里调

    # ---- 界面线程 ----
    def beat(self) -> None:
        self._beat = time.monotonic()

    def start(self, qt: bool = True) -> None:
        if self._thread is not None:
            return
        if qt:
            from PySide6.QtCore import Qt, QTimer
            t = QTimer()
            t.setInterval(100)
            t.setTimerType(Qt.TimerType.CoarseTimer)
            t.timeout.connect(self.beat)
            t.start()
            self._timer = t
            # Q189h：用户关软件以后（最后一个窗口关掉 / 事件循环要退出）不再报「卡住」——
            # 那是拆窗口、停线程、关子进程，以前每次退出都在日志末尾留一条「卡了 3 秒（取不到栈）」的误报
            try:
                from PySide6.QtCore import QCoreApplication
                app = QCoreApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(self.stop)
                    sig = getattr(app, "lastWindowClosed", None)
                    if sig is not None:
                        sig.connect(self.stop)
            except Exception:
                pass
        self.beat()
        self._thread = threading.Thread(target=self._run, name="cutora-stall-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    # ---- 守护线程 ----
    def _sample(self) -> str:
        try:
            frame = sys._current_frames().get(self._gui_tid)
        except Exception:
            return ""
        return _fmt_stack(frame) if frame is not None else ""

    def _main_thread_gone(self) -> bool:
        """界面线程已经不在跑 Python 了（事件循环里它至少还有 app.exec 那一帧）= 进程在退出。"""
        try:
            if not threading.main_thread().is_alive():
                return True
            return sys._current_frames().get(self._gui_tid) is None
        except Exception:
            return False

    def _run(self) -> None:
        stalled_since = None
        first_stack = ""
        last_sample = 0.0
        live_idx = 0
        while not self._stop.wait(self.poll):
            now = time.monotonic()
            age = now - self._beat
            if age >= self.threshold:
                if self._main_thread_gone():
                    break                       # Q189h：在退出，不是卡住
                if stalled_since is None:
                    stalled_since = self._beat
                    first_stack = self._sample()
                    last_sample = now
                    live_idx = 0
                elif now - last_sample >= 2.0:
                    # 卡很久：隔 2 秒再采一次（看是不是换了地方）
                    stack = self._sample()
                    if stack and stack != first_stack:
                        first_stack += "\n    ---- %.1f s 后 ----\n" % (now - stalled_since) + stack
                    last_sample = now
                # 一直没恢复（真卡死）也要留下证据：卡到 3 / 10 / 30 / 60 秒……时立刻写一次，不等恢复
                held = now - stalled_since
                if live_idx < len(LIVE_MARKS) and held >= LIVE_MARKS[live_idx]:
                    live_idx += 1
                    self._report_live(held, self._sample())
                elif live_idx >= len(LIVE_MARKS) and held >= LIVE_MARKS[-1] + 60.0 * (live_idx - len(LIVE_MARKS) + 1):
                    live_idx += 1
                    self._report_live(held, self._sample())
            elif stalled_since is not None:
                if self._stop.is_set():
                    break
                seconds = now - stalled_since
                stalled_since = None
                self._report(seconds, first_stack)
                first_stack = ""

    def _report_live(self, seconds: float, stack: str) -> None:
        try:
            _LOG.warning("界面线程已经卡了 %.0f 秒（还没恢复），此刻卡在：\n%s", seconds,
                         _annotate(stack) or "    (取不到栈)")
            import logging
            for h in list(getattr(_LOG, "handlers", []) or []) + list(logging.getLogger().handlers) + \
                    list(logging.getLogger("cutora").handlers):
                try:
                    h.flush()
                except Exception:
                    pass
        except Exception:
            pass

    def _report(self, seconds: float, stack: str) -> None:
        rec = {"seconds": round(seconds, 2), "stack": stack, "at": time.strftime("%H:%M:%S")}
        self.stalls.append(rec)
        del self.stalls[:-20]
        try:
            _LOG.warning("界面线程卡了 %.1f 秒，卡住时在：\n%s", seconds, _annotate(stack) or "    (取不到栈)")
        except Exception:
            pass
        cb = self.on_stall
        if callable(cb):
            try:
                cb(seconds, stack)
            except Exception:
                pass


def install(threshold_ms: int = DEFAULT_THRESHOLD_MS) -> StallWatchdog:
    """在界面线程上调一次（主窗口建好以后）。重复调返回同一个。"""
    wd = _STATE.get("watchdog")
    if wd is not None:
        return wd
    wd = StallWatchdog(threshold_ms=threshold_ms)
    wd.start(qt=True)
    _STATE["watchdog"] = wd
    return wd


def current() -> StallWatchdog | None:
    return _STATE.get("watchdog")
