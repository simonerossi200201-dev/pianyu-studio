# -*- coding: utf-8 -*-
"""Intra-video compose parallelism: N workers synthesize, one pipe encodes.

This is not ``render/parallel.py`` (that fans out whole jobs). Here each
worker renders a contiguous chunk of frames with the same ``frame_at``
path, and the main thread writes them **in order** into a single ffmpeg
stdin. workers=1 and workers=4 must hash-identical.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.compose_parallel")

COMPOSE_CHUNK_FRAMES = 8
COMPOSE_WORKERS_MAX = 8
COMPOSE_WORKERS_ENV = "CUTORA_COMPOSE_WORKERS"
# In-flight BGRA cap: 256 MiB. 1080×1920×4 ≈ 8.3 MiB → ~30 frames.
COMPOSE_INFLIGHT_BYTES = 256 * 1024 * 1024
# Q180 replay：解码线程取好、还没被合成线程用掉的源帧最多占这么多（1080×1920 BGR ≈ 6 MiB/张）
REPLAY_INFLIGHT_BYTES = 160 * 1024 * 1024
# 批量并行导出（一个视频一个进程）时每个进程最多几个合成线程：画帧大头（Qt 画图）拿着 Python 的 GIL，
# 一个进程里线程再多也只能排队（Luca 实测 6 / 8 线程都是每进程约 15 帧/秒），多开进程才真的快
PARALLEL_JOB_COMPOSE_MAX = 4
COMPOSE_SWITCH_INTERVAL = 0.001
COMPOSE_REPORT_EVERY_S = 20.0

LAST_RUN = {
    "workers": 0,
    "requested": 0,
    "frames_by_worker": {},
    "peak_rss_gb": 0.0,
    "budget_gb": 0.0,
    "frames": 0,
    "chunk": COMPOSE_CHUNK_FRAMES,
}


def default_compose_workers(cpu_count=None) -> int:
    """Default compose workers: CPU count, capped so we don't spawn 32 painters."""
    try:
        n = int(cpu_count if cpu_count is not None else (os.cpu_count() or 4))
    except (TypeError, ValueError):
        n = 4
    return max(1, min(int(n), COMPOSE_WORKERS_MAX))


def resolve_compose_workers(requested=None, cpu_count=None) -> int:
    """Resolve compose worker count: explicit > env > CPU default."""
    if requested is not None:
        try:
            return max(1, int(requested))
        except (TypeError, ValueError):
            _LOG.warning("compose workers 无效 (%r)，回退到 CPU 默认", requested)
    env = os.environ.get(COMPOSE_WORKERS_ENV)
    if env not in (None, ""):
        try:
            return max(1, int(env))
        except (TypeError, ValueError):
            _LOG.warning(
                "环境变量 %s=%r 无效，回退到 CPU 默认", COMPOSE_WORKERS_ENV, env)
    return default_compose_workers(cpu_count=cpu_count)


def inflight_frame_cap(width, height, n_workers, chunk=COMPOSE_CHUNK_FRAMES) -> int:
    """Max frames allowed ahead of the pipe. Bounded by RAM, at least one chunk."""
    w = max(1, int(width))
    h = max(1, int(height))
    frame_bytes = w * h * 4
    by_ram = max(int(chunk), int(COMPOSE_INFLIGHT_BYTES // max(1, frame_bytes)))
    by_workers = max(int(chunk), int(chunk) * max(1, int(n_workers)) * 2)
    return max(int(chunk), min(by_ram, by_workers))


def _reset_last_run(n_workers, requested, budget_gb, chunk):
    LAST_RUN["workers"] = int(n_workers)
    LAST_RUN["requested"] = int(requested or n_workers)
    LAST_RUN["frames_by_worker"] = {}
    LAST_RUN["peak_rss_gb"] = 0.0
    LAST_RUN["budget_gb"] = float(budget_gb or 0.0)
    LAST_RUN["frames"] = 0
    LAST_RUN["chunk"] = int(chunk)


class OrderedHandoff:
    """Reorder buffer: workers put any idx, take_next yields 0,1,2,…"""

    def __init__(self, start, end, max_ahead):
        self._next = int(start)
        self._end = int(end)
        self._max_ahead = max(1, int(max_ahead))
        self._buf = {}
        self._cv = threading.Condition()
        self._error = None
        self._stopped = False

    @property
    def next_idx(self):
        return self._next

    def fail(self, exc):
        with self._cv:
            if self._error is None:
                self._error = exc if isinstance(exc, BaseException) else RuntimeError(str(exc))
            self._cv.notify_all()

    def stop(self):
        with self._cv:
            self._stopped = True
            self._cv.notify_all()

    def put(self, idx, payload):
        idx = int(idx)
        with self._cv:
            while (
                not self._stopped
                and self._error is None
                and idx >= self._next + self._max_ahead
            ):
                self._cv.wait(0.05)
            if self._stopped or self._error is not None:
                return False
            self._buf[idx] = payload
            self._cv.notify_all()
            return True

    def take_next(self, timeout=30.0, alive_fn=None):
        """Wait for the next in-order frame.

        If ``alive_fn`` is given, a slow-but-living decoder is not a timeout:
        keep waiting while it returns true, and fail only once producers are
        gone and the frame is still missing. Without ``alive_fn`` the old
        30 s cap still applies (direct callers / tests).
        """
        deadline = time.time() + float(timeout)
        with self._cv:
            while True:
                if self._error is not None:
                    raise self._error
                if self._next >= self._end:
                    return None
                if self._next in self._buf:
                    payload = self._buf.pop(self._next)
                    self._next += 1
                    self._cv.notify_all()
                    return payload
                if self._stopped:
                    raise RuntimeError(
                        "合成已停止，但第 %d 帧还没送到管道（已缓冲 %d 帧）"
                        % (self._next, len(self._buf)))
                producing = False
                if callable(alive_fn):
                    try:
                        producing = bool(alive_fn())
                    except Exception:
                        producing = False
                    if producing:
                        self._cv.wait(0.1)
                        continue
                    raise TimeoutError(
                        "等待第 %d 帧失败：解码线程已退出（已缓冲 %d 帧）"
                        % (self._next, len(self._buf)))
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError(
                        "等待第 %d 帧超时（已缓冲 %d 帧）" % (self._next, len(self._buf)))
                self._cv.wait(min(0.1, remain))


class ExportComposeCtx:
    __slots__ = (
        "project", "width", "height",
        "video_tracks", "video_caps", "frame_states",
        "fallback_cap", "overlay_path", "fallback_clip",
        "render_order", "overlay_index", "task_glass",
        "stack",
    )

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))


def _sample_rss(peak_holder):
    try:
        from cutora.render.export_guard import process_rss_gb
        gb = float(process_rss_gb(os.getpid()) or 0.0)
    except Exception:
        _LOG.exception("采样导出进程 RSS 失败")
        return
    if gb > peak_holder[0]:
        peak_holder[0] = gb
        LAST_RUN["peak_rss_gb"] = gb


def run_compose_pool(
    *,
    render_worker,
    decode_ctx,
    overlay_ctx_factory,
    frame_start,
    frame_end,
    fps,
    width,
    height,
    n_workers,
    pipe_writer,
    is_running,
    stop_event,
    mad_want,
    mad_refs,
    on_progress,
    total_frames,
    budget_gb,
    requested=None,
    chunk=COMPOSE_CHUNK_FRAMES,
    replay=False,
):
    """Decode serially (same cursor as workers=1), overlay on N threads, write in order.

    replay=True（Q180）：解码线程只取源帧（render_worker._record_video_fetch），合成线程用同一套串行代码画
    整帧（_compose_replay_frame）—— 统一叠压 / 抠像 / 遮罩 / 调整图层全部并行，逐像素等于串行。
    在途的源帧按字节数限流（REPLAY_INFLIGHT_BYTES）。

    Returns peak RSS in GB. Raises on worker failure. Does not close the pipe.
    """
    from PySide6.QtGui import QImage
    from cutora.render.pipe_writer import qimage_to_bgra_bytes

    n_workers = max(1, int(n_workers))
    frame_start = int(frame_start)
    frame_end = int(frame_end)
    chunk = max(1, int(chunk))
    expected_bytes = int(width) * int(height) * 4
    max_ahead = inflight_frame_cap(width, height, n_workers, chunk)
    if replay:
        # Q180：整帧并行时合成线程不会被一张大视频层卡住，管道前面留 3×线程数 张就够了 ——
        # 以前最多压 72 张成品（720×1280 ≈ 260 MB）在内存里，进程大了能开的进程就少
        max_ahead = min(max_ahead, max(int(chunk), 3 * n_workers))
    _reset_last_run(n_workers, requested, budget_gb, chunk)
    LAST_RUN["frames"] = frame_end - frame_start

    _LOG.info(
        "[Export] 合成并行 decode=1 overlay=%d chunk=%d frames=%d..%d inflight≤%d 预算 %.2f GB%s",
        n_workers, chunk, frame_start, frame_end - 1, max_ahead, float(budget_gb or 0.0),
        "（整帧并行：解码线程只取帧）" if replay else "",
    )

    handoff = OrderedHandoff(frame_start, frame_end, max_ahead)
    decoded_q = queue.Queue(maxsize=max(1, max_ahead))
    use_zero_copy_canvas = hasattr(pipe_writer, "ensure_canvas_pool") and getattr(pipe_writer, "_free", None) is not None
    if use_zero_copy_canvas:
        try:
            pipe_writer.ensure_canvas_pool(max_ahead + n_workers + getattr(pipe_writer, "maxsize", 8) + 4)
        except Exception:
            use_zero_copy_canvas = False
    # 各段耗时（秒）：解码线程取帧 / 解码线程等队列 / 合成线程画帧（合计）/ 合成线程等帧（合计）/ 主线程写管道
    stats = {"decode_s": 0.0, "decode_block_s": 0.0, "compose_s": 0.0, "starve_s": 0.0, "pipe_s": 0.0,
             "decode_cpu": 0.0, "compose_cpu": 0.0}
    LAST_RUN["stats"] = stats
    _t_pool0 = time.perf_counter()
    _cpu_pool0 = time.process_time()
    _main_cpu0 = time.thread_time()
    _last_report = [_t_pool0, 0]
    _stats_lock = threading.Lock()
    # Q180：多线程时 Python 默认 5 ms 才换一次 GIL；取帧线程每次要回 GIL 都得排队 —— 调小，取帧不被饿着
    _old_switch = sys.getswitchinterval()
    sys.setswitchinterval(min(_old_switch, COMPOSE_SWITCH_INTERVAL))

    def _report(final=False):
        now = time.perf_counter()
        wall = max(1e-6, now - _t_pool0)
        nf = max(1, _last_report[1])
        proc_cpu = time.process_time() - _cpu_pool0
        main_cpu = time.thread_time() - _main_cpu0
        py_cpu = stats["decode_cpu"] + stats["compose_cpu"] + main_cpu
        _LOG.info(
            "[Export] 合成%s %d 帧 %.1f fps｜取帧 %.1f ms/帧（解码线程忙 %d%%、CPU %.1f ms/帧、等队列 %d%%）｜"
            "画帧 %.1f ms/帧（%d 线程平均忙 %d%%、CPU %.1f ms/帧、等帧 %d%%）｜写管道 %.1f ms/帧｜"
            "进程 CPU %.0f ms/帧 = %.1f 核（其中解码器 / Qt / cv2 自己的线程 %.0f ms/帧）",
            "用时" if final else "进行中", nf, nf / wall,
            stats["decode_s"] * 1000.0 / nf, 100.0 * stats["decode_s"] / wall, stats["decode_cpu"] * 1000.0 / nf,
            100.0 * stats["decode_block_s"] / wall,
            stats["compose_s"] * 1000.0 / nf, n_workers, 100.0 * stats["compose_s"] / (wall * n_workers),
            stats["compose_cpu"] * 1000.0 / nf, 100.0 * stats["starve_s"] / (wall * n_workers),
            stats["pipe_s"] * 1000.0 / nf,
            proc_cpu * 1000.0 / nf, proc_cpu / wall, max(0.0, proc_cpu - py_cpu) * 1000.0 / nf)
        if final:
            try:
                dm = render_worker.__dict__.get("_decode_ms") or {}
                parts = []
                for k, (ms, n, wh) in sorted(dm.items(), key=lambda kv: -kv[1][0]):
                    parts.append("%s(%dx%d) %.1f ms/次 ×%d" % (k, wh // 10000, wh % 10000, ms / max(1, n), n))
                if parts:
                    _LOG.info("[Export] 各源取帧（含等预读解码）：%s", "；".join(parts))
            except Exception:
                pass
    # Q180：取好的源帧占多少内存（同一张源帧被几帧共用只算一次）；超了解码线程就等
    rp_bytes = [0]
    rp_cv = threading.Condition()

    def _rec_bytes(rec):
        seen = set()
        n = 0
        for lst in (rec or {}).values():
            for item in lst:
                fr = item[1]
                if fr is not None and id(fr) not in seen:
                    seen.add(id(fr))
                    n += int(getattr(fr, "nbytes", 0) or 0)
        return n
    compose_stop = threading.Event()
    peak = [0.0]
    _sample_rss(peak)
    counts_lock = threading.Lock()
    _SENTINEL = object()

    def _stopped():
        return (
            compose_stop.is_set()
            or stop_event.is_set()
            or not is_running()
            or handoff._error is not None
        )

    def _decode():
        _c0 = time.thread_time()
        try:
            for idx in range(frame_start, frame_end):
                if _stopped():
                    break
                t = idx / float(fps)
                _t0 = time.perf_counter()
                if replay:
                    rec = render_worker._record_video_fetch(t, decode_ctx)
                    nb = _rec_bytes(rec)
                    _t1 = time.perf_counter()
                    with rp_cv:
                        while rp_bytes[0] > 0 and rp_bytes[0] + nb > REPLAY_INFLIGHT_BYTES and not _stopped():
                            rp_cv.wait(0.05)
                        rp_bytes[0] += nb
                    item = (idx, rec, nb)
                else:
                    video, backdrop = render_worker._compose_video_frame(t, decode_ctx)
                    item = (idx, video, backdrop)
                    _t1 = time.perf_counter()
                stats["decode_s"] += _t1 - _t0
                while True:
                    if _stopped():
                        return
                    try:
                        decoded_q.put(item, timeout=0.05)
                        break
                    except queue.Full:
                        continue
                stats["decode_block_s"] += time.perf_counter() - _t1
                stats["decode_cpu"] = time.thread_time() - _c0
        except Exception as e:
            _LOG.exception("合成解码线程失败")
            handoff.fail(e)
        finally:
            for _ in range(n_workers):
                try:
                    decoded_q.put(_SENTINEL, timeout=0.2)
                except Exception:
                    _LOG.exception("合成解码结束标记入队失败")

    def _overlay(wid):
        from cutora.render.frame_renderer import cleanup_dbi_video_caps
        ctx = None
        canvas = None
        n_done = 0
        _c0 = time.thread_time()
        _c_last = [_c0]

        def _cpu_tick():
            c = time.thread_time()
            with _stats_lock:
                stats["compose_cpu"] += c - _c_last[0]
            _c_last[0] = c
        try:
            ctx = overlay_ctx_factory()
            if not use_zero_copy_canvas:
                canvas = QImage(
                    int(width), int(height),
                    QImage.Format.Format_ARGB32_Premultiplied)
            while True:
                if _stopped():
                    return
                _tw = time.perf_counter()
                try:
                    item = decoded_q.get(timeout=0.05)
                except queue.Empty:
                    stats["starve_s"] += time.perf_counter() - _tw
                    continue
                stats["starve_s"] += time.perf_counter() - _tw
                if item is _SENTINEL:
                    return
                if use_zero_copy_canvas:
                    canvas = pipe_writer.acquire_canvas()
                    if canvas is None:
                        return
                t = item[0] / float(fps)
                idx = item[0]
                _tc = time.perf_counter()
                if replay:
                    _i, rec, nb = item
                    try:
                        canvas = render_worker._compose_replay_frame(t, canvas, ctx, rec)
                    finally:
                        rec = None
                        with rp_cv:
                            rp_bytes[0] -= nb
                            rp_cv.notify_all()
                else:
                    _i, video, backdrop = item
                    canvas = render_worker._compose_overlay_frame(
                        t, canvas, ctx, video, backdrop)
                if use_zero_copy_canvas:
                    data = canvas
                else:
                    data = qimage_to_bgra_bytes(canvas, expected_bytes, idx)
                with _stats_lock:
                    stats["compose_s"] += time.perf_counter() - _tc
                _cpu_tick()
                extra = None
                if mad_want and idx in mad_want:
                    extra = canvas.copy()
                if not handoff.put(idx, (data, extra)):
                    if use_zero_copy_canvas and data is not None:
                        pipe_writer.release_canvas(data)
                    return
                if use_zero_copy_canvas:
                    canvas = None
                n_done += 1
        except Exception as e:
            _LOG.exception("合成叠层线程 %d 失败", wid)
            handoff.fail(e)
        finally:
            with counts_lock:
                LAST_RUN["frames_by_worker"][int(wid)] = n_done
            try:
                cleanup_dbi_video_caps()
            except Exception:
                _LOG.exception("合成叠层线程 %d 释放挂载视频解码器失败", wid)

    threads = [threading.Thread(target=_decode, name="cutora-compose-decode", daemon=True)]
    for i in range(n_workers):
        threads.append(threading.Thread(
            target=_overlay, args=(i,), name="cutora-compose-%d" % i, daemon=True))
    for t in threads:
        t.start()

    wrote = frame_start
    try:
        while wrote < frame_end:
            if stop_event.is_set() or not is_running():
                compose_stop.set()
                handoff.stop()
                break
            payload = handoff.take_next(
                alive_fn=lambda: any(th.is_alive() for th in threads))
            if payload is None:
                break
            data, extra = payload
            if extra is not None and mad_refs is not None and len(mad_refs) < 3:
                out_t = (wrote - frame_start) / max(1.0, float(fps))
                if not any(abs(r[0] - out_t) < 1e-6 for r in mad_refs):
                    mad_refs.append((out_t, extra))
            _tp = time.perf_counter()
            if not pipe_writer.put(data):
                raise BrokenPipeError("FFmpeg Encoder Error")
            stats["pipe_s"] += time.perf_counter() - _tp
            wrote += 1
            _last_report[1] = wrote - frame_start
            if time.perf_counter() - _last_report[0] > COMPOSE_REPORT_EVERY_S:
                _last_report[0] = time.perf_counter()
                _report()
            if wrote % 30 == 0:
                _sample_rss(peak)
                if callable(on_progress):
                    on_progress(wrote, int(total_frames))
        _sample_rss(peak)
    except Exception:
        handoff.stop()
        raise
    finally:
        compose_stop.set()
        handoff.stop()
        for t in threads:
            t.join(30.0)
        alive = [t.name for t in threads if t.is_alive()]
        if alive:
            _LOG.error("合成线程未退出: %s", alive)
        LAST_RUN["peak_rss_gb"] = peak[0]
        _LOG.info(
            "[Export] 合成并行结束 overlay=%d 写出 %d 帧 peak_rss=%.2f GB 预算 %.2f GB 分担 %s",
            n_workers, wrote - frame_start, peak[0], float(budget_gb or 0.0),
            LAST_RUN["frames_by_worker"],
        )
        sys.setswitchinterval(_old_switch)
        _last_report[1] = wrote - frame_start
        try:
            _report(final=True)
            from cutora.fx import gpu as _gpu
            if _gpu.STATS["calls"]:
                _LOG.info("[Export] %s", _gpu.stats_line())
        except Exception:
            _LOG.exception("合成耗时统计失败")
        if budget_gb and peak[0] > float(budget_gb) + 0.05:
            _LOG.warning(
                "[Export] 并行合成 RSS %.2f GB 超过 T03 估值 %.2f GB —— 下一轮体检会按实测收紧",
                peak[0], float(budget_gb))
    return peak[0]
