# -*- coding: utf-8 -*-
"""T09 / T35 E3: ffmpeg pipe writer thread + one-frame decode prefetch.

修前红：cutora.render.pipe_writer / decode_prefetch 还不存在。
"""
from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def test_pipe_queue_maxsize_is_8():
    from cutora.render.pipe_writer import PIPE_QUEUE_SIZE, FfmpegPipeWriter

    assert PIPE_QUEUE_SIZE == 8
    sink = _ByteSink()
    writer = FfmpegPipeWriter(sink)
    assert writer.maxsize == 8


def test_pipe_put_interrupted_by_stop():
    """Full 8-frame queue must not hang when the stop flag is set (BUG-30)."""
    from cutora.render.pipe_writer import FfmpegPipeWriter, PIPE_QUEUE_SIZE

    class SlowSink:
        def write(self, data):
            time.sleep(1.0)

    stop = threading.Event()
    writer = FfmpegPipeWriter(SlowSink(), stop_event=stop, maxsize=PIPE_QUEUE_SIZE)
    writer.start()
    filled = {"n": 0, "stopped": False}

    def _fill():
        while filled["n"] < 64:
            if not writer.put(bytes([filled["n"] & 0xFF])):
                filled["stopped"] = True
                return
            filled["n"] += 1

    t = threading.Thread(target=_fill, name="t09-blocked-put")
    try:
        t.start()
        deadline = time.time() + 2.0
        while time.time() < deadline and t.is_alive() and filled["n"] < PIPE_QUEUE_SIZE:
            time.sleep(0.02)
        time.sleep(0.15)
        assert t.is_alive(), "fill thread should block once the 8-frame queue is full"
        stop.set()
        t.join(3.0)
        assert not t.is_alive(), "stop flag did not interrupt queue put"
        assert filled["stopped"] is True
        assert filled["n"] >= PIPE_QUEUE_SIZE
    finally:
        writer.abort()
    assert not writer.thread_alive(), writer.thread_names()


def test_pipe_thread_no_leak(tmp_path, monkeypatch):
    """Export must start a pipe thread and reap it: active_count back to start ±1."""
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.render import pipe_writer as pw
    from cutora.render.pipe_writer import THREAD_NAME as PIPE_THREAD_NAME
    from cutora.render.render_worker import RenderWorker

    _ = QApplication.instance() or QApplication(["t09-pipe-leak"])
    src = str(tmp_path / "src.mp4")
    _tiny_mp4(src, duration=2.0, fps=25, size="320x180")
    out = str(tmp_path / "out.mp4")
    task = {
        "video": src, "path": src, "duration": 2.0,
        "segments": [], "style": {},
        "timeline": Timeline(320, 180, 25.0),
    }
    started = []
    orig_start = pw.FfmpegPipeWriter.start

    def _start(self):
        started.append(self)
        return orig_start(self)

    monkeypatch.setattr(pw.FfmpegPipeWriter, "start", _start)
    n0 = threading.active_count()
    worker = RenderWorker(task, out, "quality", False, export_logs=False)
    err = []
    worker.error.connect(lambda m: err.append(m))
    try:
        worker.run()
    finally:
        worker.stop()
    deadline = time.time() + 5.0
    while time.time() < deadline and abs(threading.active_count() - n0) > 1:
        time.sleep(0.05)
    alive = [t.name for t in threading.enumerate()]
    assert started, "FfmpegPipeWriter.start was never called"
    assert not started[0].thread_alive(), started[0].thread_names()
    assert PIPE_THREAD_NAME not in alive, alive
    assert abs(threading.active_count() - n0) <= 1, (
        n0, threading.active_count(), alive)
    assert os.path.isfile(out) and os.path.getsize(out) > 1024, (out, err)
    if err:
        assert all("MAD" in str(e) for e in err), err


def test_decode_prefetch_thread_reaped():
    from cutora.render.decode_prefetch import kick_read, take

    class Cap:
        def read(self):
            time.sleep(0.02)
            return True, b"frame"

    n0 = threading.active_count()
    state = {"last_frame": None}
    kick_read(Cap(), state)
    got = take(state)
    assert got == (True, b"frame")
    deadline = time.time() + 2.0
    while time.time() < deadline and abs(threading.active_count() - n0) > 1:
        time.sleep(0.02)
    alive = [t.name for t in threading.enumerate()]
    assert abs(threading.active_count() - n0) <= 1, (n0, threading.active_count(), alive)


def test_decode_prefetch_copies_last_frame_before_next_read():
    """Next cap.read() must not clobber the frame still held as last_frame."""
    from cutora.render.decode_prefetch import kick_read, take

    class Cap:
        def __init__(self):
            self.buf = bytearray(b"AAAA")

        def read(self):
            self.buf[:] = b"BBBB"
            return True, self.buf

    last = bytearray(b"AAAA")
    state = {"last_frame": last}
    kick_read(Cap(), state)
    ret, nxt = take(state)
    assert ret is True
    assert bytes(state["last_frame"]) == b"AAAA"
    assert bytes(nxt) == b"BBBB"


def _tiny_mp4(path, duration=0.5, fps=10, size="160x90"):
    import subprocess
    from cutora.core.config import FFMPEG_PATH

    r = subprocess.run(
        [
            FFMPEG_PATH, "-y", "-f", "lavfi",
            "-i", "testsrc=size=%s:rate=%d:duration=%.2f" % (size, fps, duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode: %s" % (r.stderr or b"")[-200:])


class _ByteSink:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(bytes(data))
