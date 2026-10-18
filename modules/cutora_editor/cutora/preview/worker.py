# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview compose on a worker thread. UI only pastes the last QImage."""
from __future__ import annotations

import threading
from cutora.core.counters import bump as _bump
import time

from PySide6.QtCore import QThread, Signal

from cutora.core.log import get_logger

_LOG = get_logger("cutora.preview.worker")


class FullFrame:
    """Q175：播放时送给画布的「整幅成品」（视频 + 覆层 + 调整图层都合好了，按显示像素）。"""
    __slots__ = ("image", "t")

    def __init__(self, image, t):
        self.image = image
        self.t = float(t)


class PreviewWorker(QThread):
    """Latest-frame wins. Decode / compose here; GUI thread only blits.

    Q175：``full=True``（播放中）→ 整幅画面都在这里合成（``display_frame_at``），画布只贴图。
    ``cost`` = 最近整幅合成用时的平滑值（秒）；主窗口按它往前多要一点，画面和声音对齐。"""

    frame_ready = Signal(object, float, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self._pending = None
        self._stop = False
        self._gen = 0
        self.engine = None
        self.cost = 0.0
        self.solo_ids = None
        self._last_emit = 0.0
        # 退出时线程还在跑 → QThread 析构直接 abort（测试 / 关窗口没走 closeEvent 时）。先叫停再说。
        import atexit
        import weakref
        ref = weakref.ref(self)

        def _stop_at_exit():
            w = ref()
            if w is None:
                return
            try:
                if w.isRunning():
                    w.shutdown(1500)
            except RuntimeError:
                pass
        atexit.register(_stop_at_exit)

    def request(self, task, t, scale=1.0, full=False):
        _bump("preview.request_full" if full else "preview.request")
        with self._cond:
            self._gen += 1
            self._pending = (task, float(t), float(scale), int(self._gen), bool(full))
            self._cond.notify()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        with self._cond:
            self._stop = True
            self._pending = None
            self._cond.notify()
        self.wait(max(1, int(timeout_ms)))

    def run(self):
        from cutora.preview.engine import PreviewEngine
        self.engine = PreviewEngine()
        try:
            while True:
                with self._cond:
                    while not self._stop and self._pending is None:
                        self._cond.wait()          # Q189g：没任务就睡，request / shutdown 会 notify
                    if self._stop:
                        break
                    item = self._pending
                    self._pending = None
                task, t, scale, gen, full = item
                _bump("preview.rendered")
                if full:
                    try:
                        t0 = time.perf_counter()
                        img = FullFrame(self.engine.display_frame_at(task, t, scale=scale,
                                                                     solo_ids=self.solo_ids), t)
                        dt = time.perf_counter() - t0
                        self.cost = dt if self.cost <= 0 else self.cost * 0.8 + dt * 0.2
                    except Exception:
                        _LOG.exception("preview worker display_frame_at failed")
                        continue
                    with self._cond:
                        if gen != self._gen and self._pending is not None and not self._pending[4]:
                            continue                 # 已经停下来要普通帧了：这张整幅的不要了
                    self._last_emit = time.perf_counter()
                    self.frame_ready.emit(img, t, gen)
                    continue
                try:
                    # Q130：工作线程只合成视频层。覆层 / 文字由画布自己画（它本来就
                    # 要画一遍收命中记录）—— 以前这里画一遍、画布再画一遍，拖动时
                    # 低分辨率那份错位的字从下面透出来就是「重影」。
                    img = None
                    # Q163：视频和文字 / 字幕按时间线交错叠压 → 每段视频带一张（列表），画布逐层叠
                    from cutora.render.visual_stack import build_stack, needs_stack
                    _st = build_stack(task)
                    if needs_stack(task, stack=_st):
                        from cutora.render.visual_stack import band_copy
                        img = [band_copy(b) for b in self.engine.video_bands_at(task, t, scale=scale)]
                    else:
                        img = self.engine.video_frame_at(task, t, scale=scale)
                        if img is not None and not img.isNull():
                            img = img.copy()
                except Exception:
                    _LOG.exception("preview worker frame_at failed")
                    continue
                with self._cond:
                    # 有更新的请求了：这张旧的一般不要 —— 但一直被新请求顶掉（每帧都比请求间隔慢）
                    # 就会一张都送不出去、画面不动；超过 0.1 秒没送过就照送。
                    if gen != self._gen and time.perf_counter() - self._last_emit < 0.1:
                        continue
                self._last_emit = time.perf_counter()
                self.frame_ready.emit(img, t, gen)
        finally:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None
