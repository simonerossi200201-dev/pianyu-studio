# -*- coding: utf-8 -*-
"""T09: ffmpeg stdin writer thread. 8-frame queue, stop-aware backpressure."""
from __future__ import annotations

import queue
import threading

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.pipe_writer")

PIPE_QUEUE_SIZE = 8
THREAD_NAME = "cutora-ffmpeg-pipe"
_SENTINEL = object()
_PUT_WAIT = 0.1
_JOIN_TIMEOUT = 30.0


class FrameSizeMismatch(RuntimeError):
    """写给 ffmpeg 的一帧字节数不等于 -s WxH 约定的字节数。

    rawvideo 管道没有帧边界：少写或多写一个字节，后面每一帧都会整体错位，
    画面表现为竖向彩色条纹（BGRA 通道被轮转）。历史上这是静默发生的 ——
    导出照样"成功"，产物全废。这个异常就是为了让它不再静默。
    """


def qimage_to_bgra_bytes(canvas, expected_bytes=None, frame_idx=None):
    """Pack QImage pixels to tight BGRA. Same bytes as the old sync write path.

    ``expected_bytes`` 给了就强校验：不等就抛 FrameSizeMismatch，绝不把错位的
    数据喂进 rawvideo 管道。
    """
    bytes_per_line = canvas.bytesPerLine()
    width_bytes = canvas.width() * 4
    ptr = canvas.constBits()
    if bytes_per_line == width_bytes:
        data = bytes(ptr)
        if len(data) != canvas.height() * width_bytes:
            data = data[: canvas.height() * width_bytes]
    else:
        raw = bytes(ptr)
        h = canvas.height()
        data = b"".join(
            raw[y * bytes_per_line: y * bytes_per_line + width_bytes]
            for y in range(h)
        )
    if expected_bytes is not None and len(data) != int(expected_bytes):
        raise FrameSizeMismatch(
            "帧 %s 字节数不符：写出 %d，应为 %d（canvas %dx%d bpl=%d fmt=%s）。"
            "rawvideo 管道没有帧边界，继续写下去整条视频都会错位成彩色条纹，"
            "已中止本次导出。" % (
                "?" if frame_idx is None else frame_idx,
                len(data), int(expected_bytes),
                canvas.width(), canvas.height(), bytes_per_line,
                getattr(canvas.format(), "name", canvas.format()),
            )
        )
    return data


def _as_payload(item, expected_bytes=None, frame_idx=None):
    if item is _SENTINEL:
        return item
    if isinstance(item, (bytes, bytearray, memoryview)):
        data = bytes(item)
        if expected_bytes is not None and len(data) != int(expected_bytes):
            raise FrameSizeMismatch(
                "帧 %s 字节数不符：写出 %d，应为 %d（预打包 bytes）。已中止本次导出。"
                % ("?" if frame_idx is None else frame_idx,
                   len(data), int(expected_bytes)))
        return data
    return qimage_to_bgra_bytes(item, expected_bytes, frame_idx)


class FfmpegPipeWriter:
    """Background stdin writer. Queue of at most 8 frames; put() blocks when full.

    put() returns False if ``stop_event`` is set or the writer already failed.
    close() always joins the thread so export cannot leave a hanging writer
    (BUG-30).
    """

    def __init__(self, stdin, stop_event=None, maxsize=PIPE_QUEUE_SIZE,
                 canvas_wh=None, use_yuv420p=False):
        if stdin is None:
            raise ValueError("ffmpeg stdin is required")
        size = int(maxsize)
        if size < 1:
            raise ValueError("pipe queue maxsize must be >= 1")
        self._stdin = stdin
        self._stop = stop_event if stop_event is not None else threading.Event()
        self._q = queue.Queue(maxsize=size)
        self._free = None
        self._error = None
        self._written = 0
        self._use_yuv420p = bool(use_yuv420p)
        self._yuv_buf = None
        self._expected_bytes = None
        if canvas_wh is not None:
            if self._use_yuv420p:
                self._expected_bytes = (int(canvas_wh[0]) * int(canvas_wh[1]) * 3) // 2
            else:
                self._expected_bytes = int(canvas_wh[0]) * int(canvas_wh[1]) * 4
        self._thread = threading.Thread(
            target=self._run, name=THREAD_NAME, daemon=True)
        if canvas_wh is not None:
            from PySide6.QtGui import QImage
            w, h = int(canvas_wh[0]), int(canvas_wh[1])
            fmt = QImage.Format.Format_ARGB32_Premultiplied
            self._free = queue.Queue(maxsize=size)
            for _ in range(size):
                self._free.put(QImage(w, h, fmt))

    @property
    def maxsize(self):
        return self._q.maxsize

    @property
    def error(self):
        return self._error

    @property
    def frames_written(self):
        """成功写进 ffmpeg 的帧数。导出结束应等于 total_frames。"""
        return self._written

    @property
    def expected_frame_bytes(self):
        return self._expected_bytes

    def start(self):
        if self._thread.is_alive():
            return
        if self._thread.ident is not None:
            return
        self._thread.start()

    def ensure_canvas_pool(self, min_count: int):
        """Grow the free canvas pool so parallel compose threads + handoff never starve."""
        if self._free is None or self._expected_bytes is None:
            return
        from PySide6.QtGui import QImage
        need = max(0, int(min_count) - self._free.maxsize)
        if need <= 0:
            return
        # Measure w, h from an existing canvas or expected_bytes
        sample = None
        try:
            sample = self._free.get_nowait()
            self._free.put_nowait(sample)
        except Exception:
            sample = None
        if sample is None:
            return
        w, h, fmt = sample.width(), sample.height(), sample.format()
        new_q = queue.Queue(maxsize=int(min_count))
        while True:
            try:
                new_q.put_nowait(self._free.get_nowait())
            except queue.Empty:
                break
        for _ in range(need):
            new_q.put_nowait(QImage(w, h, fmt))
        self._free = new_q

    def acquire_canvas(self):
        """Take a free QImage. Blocks while 8 frames are in flight, unless stopped."""
        if self._free is None:
            raise RuntimeError("pipe writer has no canvas pool")
        while True:
            if self._stop.is_set() or self._error is not None:
                return None
            try:
                return self._free.get(timeout=_PUT_WAIT)
            except queue.Empty:
                continue

    def release_canvas(self, canvas):
        """Return a canvas to the pool after pixels have been packed."""
        self._recycle(canvas)

    def put(self, payload):
        """Enqueue a packed frame (bytes or QImage). Blocks while the queue is full."""
        if payload is None:
            return False
        while True:
            if self._stop.is_set() or self._error is not None:
                self._recycle(payload)
                return False
            try:
                self._q.put(payload, timeout=_PUT_WAIT)
                return True
            except queue.Full:
                continue

    def _recycle(self, item):
        if self._free is None:
            return
        if isinstance(item, (bytes, bytearray, memoryview)):
            return
        if item is _SENTINEL:
            return
        try:
            self._free.put_nowait(item)
        except queue.Full:
            pass

    def close(self, timeout=_JOIN_TIMEOUT):
        """Flush remaining frames, then join. Logs if the thread is still alive."""
        if self._thread.ident is None:
            return
        try:
            if self._error is None and not self._stop.is_set():
                self._q.put(_SENTINEL, timeout=timeout)
            else:
                try:
                    self._q.put_nowait(_SENTINEL)
                except queue.Full:
                    self._stop.set()
        except Exception:
            _LOG.exception("ffmpeg 管道结束标记入队失败")
            self._stop.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            _LOG.error(
                "ffmpeg 管道写线程在 %.1f s 后仍未退出（队列 %d/%d）",
                timeout, self._q.qsize(), self._q.maxsize)
        if self._error is not None:
            raise self._error

    def abort(self, timeout=5.0):
        """Stop without flushing. Always joins."""
        self._stop.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        if self._thread.ident is not None:
            self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            _LOG.error("ffmpeg 管道写线程 abort 后仍存活")

    def thread_alive(self):
        return self._thread.is_alive()

    def thread_names(self):
        return [t.name for t in threading.enumerate()]

    def _run(self):
        try:
            while True:
                if self._stop.is_set():
                    self._drop_pending()
                    break
                try:
                    item = self._q.get(timeout=_PUT_WAIT)
                except queue.Empty:
                    continue
                if item is _SENTINEL:
                    break
                if (
                    self._use_yuv420p
                    and not isinstance(item, (bytes, bytearray, memoryview))
                    and hasattr(item, "constBits")
                ):
                    from cutora.core.rust_engine import rust_bgra_qimage_to_yuv420p
                    self._yuv_buf, mv = rust_bgra_qimage_to_yuv420p(item, self._yuv_buf)
                    if mv is None:
                        raise FrameSizeMismatch("Rust BGRA->YUV420p 转换失败")
                    if self._expected_bytes is not None and len(mv) != int(self._expected_bytes):
                        mv_len = len(mv)
                        del mv
                        raise FrameSizeMismatch(
                            "帧 %s 字节数不符：写出 %d，应为 %d。" % (
                                self._written, mv_len, int(self._expected_bytes))
                        )
                    n = self._stdin.write(mv)
                    mv_len = len(mv)
                    del mv
                    if n is not None and n != mv_len:
                        raise FrameSizeMismatch(
                            "帧 %d 短写：只写进 %s/%d 字节，后续帧会整体错位，已中止。"
                            % (self._written, n, mv_len))
                    self._written += 1
                    if (self._written & 7) == 0:
                        try:
                            self._stdin.flush()
                        except Exception:
                            _LOG.exception("ffmpeg stdin flush 失败")
                            raise
                    self._recycle(item)
                    continue
                if (
                    not isinstance(item, (bytes, bytearray, memoryview))
                    and hasattr(item, "constBits")
                    and item.bytesPerLine() == item.width() * 4
                ):
                    mv = memoryview(item.constBits())
                    want_len = item.height() * item.width() * 4
                    if len(mv) != want_len:
                        mv = mv[:want_len]
                    if self._expected_bytes is not None and len(mv) != int(self._expected_bytes):
                        mv_len = len(mv)
                        del mv
                        raise FrameSizeMismatch(
                            "帧 %s 字节数不符：写出 %d，应为 %d。" % (
                                self._written, mv_len, int(self._expected_bytes))
                        )
                    n = self._stdin.write(mv)
                    mv_len = len(mv)
                    del mv
                    if n is not None and n != mv_len:
                        raise FrameSizeMismatch(
                            "帧 %d 短写：只写进 %s/%d 字节，后续帧会整体错位，已中止。"
                            % (self._written, n, mv_len))
                    self._written += 1
                    if (self._written & 7) == 0:
                        try:
                            self._stdin.flush()
                        except Exception:
                            _LOG.exception("ffmpeg stdin flush 失败")
                            raise
                    self._recycle(item)
                    continue
                payload = _as_payload(
                    item, self._expected_bytes, self._written)
                n = self._stdin.write(payload)
                if n is not None and n != len(payload):
                    raise FrameSizeMismatch(
                        "帧 %d 短写：只写进 %s/%d 字节，后续帧会整体错位，已中止。"
                        % (self._written, n, len(payload)))
                self._written += 1
                try:
                    self._stdin.flush()
                except Exception:
                    _LOG.exception("ffmpeg stdin flush 失败")
                    raise
                self._recycle(item)
            if self._written > 0 and not self._stop.is_set():
                try:
                    self._stdin.flush()
                except Exception:
                    _LOG.exception("ffmpeg stdin flush 失败")
                    raise
        except Exception as e:
            _LOG.exception("ffmpeg 管道写入失败")
            self._error = e
            self._stop.set()

    def _drop_pending(self):
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            if item is _SENTINEL:
                return
            self._recycle(item)
