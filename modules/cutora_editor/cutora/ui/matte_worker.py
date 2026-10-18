# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · 把 AI 抠像的任务放进 QThread：单帧分割、传播、模型下载。

同一时刻只跑一个抠像任务（全局 ``MatteWorker.current``）；跑的时候主窗口 ``is_ai_running=True``，
「释放显存」会拒绝。显卡和 AI 字幕 / 跟踪共用 ``cutora.core.gpu_gate`` 排队：先到先得，后来的在
工作线程里等（发 queued 信号），排队中也能取消。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from cutora.core import gpu_gate
from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.matte_worker")


def gpu_owner() -> str:
    return gpu_gate.holder()


class MatteWorker(QThread):
    """kind: "segment" | "propagate" | "download"。

    progress(done, total, frame_idx, score)；finished(result dict)；failed(message)。"""

    progress = Signal(int, int, int, float)
    done = Signal(object)
    failed = Signal(str)
    queued = Signal(str)            # 排队等显卡（参数 = 当前占用者）

    current = None          # 正在跑的那个（类属性）

    def __init__(self, kind: str, params: dict, parent=None):
        super().__init__(parent)
        self.kind = str(kind)
        self.params = dict(params or {})
        self.cancel_event = threading.Event()
        self.started_at = 0.0
        self.result = None

    def cancel(self) -> None:
        self.cancel_event.set()

    @classmethod
    def busy(cls) -> bool:
        w = cls.current
        return w is not None and w.isRunning()

    def run(self) -> None:
        self.started_at = time.perf_counter()
        needs_gpu = self.kind in ("segment", "propagate", "track")
        ticket = 0
        if needs_gpu:
            ticket = gpu_gate.acquire(str(self.params.get("gpu_label") or "AI 抠像"),
                                      should_stop=self.cancel_event.is_set,
                                      on_wait=lambda who: self.queued.emit(str(who or "")))
            if not ticket:
                self.failed.emit("已取消（排队中）")
                return
            self.started_at = time.perf_counter()        # 计时从真正开始算，不含排队
        try:
            if self.kind == "segment":
                self.result = self._run_segment()
            elif self.kind == "propagate":
                self.result = self._run_propagate()
            elif self.kind == "track":
                self.result = self._run_track()
            elif self.kind == "download":
                self.result = self._run_download()
            else:
                raise RuntimeError("未知任务 %s" % self.kind)
            self.done.emit(self.result)
        except Exception as exc:
            _LOG.exception("AI 抠像任务失败 kind=%s", self.kind)
            msg = str(exc) or exc.__class__.__name__
            try:
                from cutora.asr.tier import is_oom_error
                if is_oom_error(exc):
                    msg = "显存不够：先点「释放显存」，或在「设置 → 模型」换 Base+ 模型。(%s)" % msg
            except Exception:
                pass
            self.failed.emit(msg)
        finally:
            if ticket:
                gpu_gate.release(ticket)

    # ---- 任务体 ----
    def _backend(self):
        from cutora.fx.segment import make_backend
        return make_backend(str(self.params.get("model") or "matte-sam21-large"))

    def _run_segment(self):
        from cutora.fx.matte_jobs import segment_frame
        be = self._backend()
        scores = segment_frame(be, self.params["path"], self.params["spec"], int(self.params["frame"]))
        return {"kind": "segment", "frame": int(self.params["frame"]), "scores": scores,
                "seconds": time.perf_counter() - self.started_at}

    def _run_propagate(self):
        from cutora.fx.matte_jobs import propagate
        be = self._backend()
        sp = self.params["spec"]
        path = self.params["path"]
        start = int(self.params["start"])
        passes = list(self.params.get("passes") or [])          # [("forward", end), ("backward", end)]
        total_frames = sum(abs(int(end) - start) + 1 for _d, end in passes) or 1
        base = {"n": 0}
        out = {"kind": "propagate", "frames": 0, "low": 0, "cancelled": False}

        def _prog(done, _total, frame_idx, score):
            self.progress.emit(base["n"] + int(done), int(total_frames), int(frame_idx), float(score))

        for direction, end in passes:
            if self.cancel_event.is_set():
                out["cancelled"] = True
                break
            r = propagate(be, path, sp, start, direction, int(end), on_progress=_prog, cancel=self.cancel_event)
            base["n"] += int(r.get("frames", 0))
            out["frames"] += int(r.get("frames", 0))
            out["low"] += int(r.get("low", 0))
            if r.get("cancelled"):
                out["cancelled"] = True
                break
        out["seconds"] = time.perf_counter() - self.started_at
        return out

    def _run_track(self):
        """Q152-E：点跟踪（TAPNext++ / 测试用模板匹配）。"""
        from cutora.fx import tracking as TR
        be = TR.make_backend(str(self.params.get("model") or "track-tapnextpp-512"))

        def _prog(done, total, frame_idx, vis):
            self.progress.emit(int(done), int(total), int(frame_idx), float(vis))

        r = TR.track(be, self.params["path"], self.params["spec"], tuple(self.params["frame_range"]),
                     str(self.params.get("direction") or "all"), on_progress=_prog, cancel=self.cancel_event)
        r["kind"] = "track"
        r["seconds"] = time.perf_counter() - self.started_at
        if getattr(be, "name", "") != "fake" and r.get("frames", 0) >= 8:
            from cutora.fx import matte_store as S
            dev = str(getattr(be, "device", "cpu") or "cpu")
            S.record_speed(str(self.params.get("model") or ""), "cuda" if dev.startswith("cuda") else "cpu",
                           r["seconds"] / float(r["frames"]), int(r["frames"]))
        return r

    def _run_download(self):
        from cutora.asr.download import download_model
        mid = str(self.params["model"])

        def _prog(done, total, _name):
            self.progress.emit(int(done // 1024), int(max(1, total) // 1024), 0, 0.0)

        info = download_model(mid, progress_cb=_prog, cancel_event=self.cancel_event)
        return {"kind": "download", "model": mid, "info": info,
                "seconds": time.perf_counter() - self.started_at}
