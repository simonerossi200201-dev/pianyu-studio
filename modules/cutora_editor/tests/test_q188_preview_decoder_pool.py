# SPDX-License-Identifier: GPL-3.0-or-later
"""Q188：预览解码器有上限、会回收、换工程会停。

Luca 的 41 源工程：画布给每个源各开一个 AsyncVideoDecoder（16 条 FFmpeg 线程 + 30 帧预读）永不关 →
主界面常驻 3.6 GB、私有提交 7 GB。现在 LRU 池（6 个）+ 30 s 闲置回收 + 每个解码器 4 条线程 / 16 帧预读。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-test"])


class _FakeDecoder:
    instances = []

    def __init__(self, path, source_id):
        self.path, self.source_id = path, source_id
        self.seek_req = 0.0
        self.last_used = time.perf_counter()
        self.running = True
        self.stopped = False
        _FakeDecoder.instances.append(self)

    def start(self):
        pass

    def get_frame(self, msec):
        self.last_used = time.perf_counter()
        from PySide6.QtGui import QImage
        return QImage(4, 4, QImage.Format.Format_ARGB32)

    def stop(self, join_timeout=1.0):
        self.stopped = True


def _canvas_with_sources(n):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(720, 1280, 30.0)
    tl.tracks.append(Track("video"))
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [], "image_overlays": [],
            "duration": 5.0, "dims": [720, 1280]}
    c = VideoCanvas()
    c.set_data(task, 0.0)
    return c


def test_decoder_pool_is_bounded_lru_and_reaped(monkeypatch):
    _qapp()
    import cutora.ui.canvas as CV
    monkeypatch.setattr(CV, "AsyncVideoDecoder", _FakeDecoder)
    monkeypatch.setattr(CV, "PREVIEW_DECODER_POOL", 3)
    monkeypatch.setattr(CV, "PREVIEW_DECODER_IDLE_S", 0.2)
    _FakeDecoder.instances.clear()
    c = _canvas_with_sources(0)

    class _Clip:
        start_t = 0.0
        in_t = 0.0
        out_t = 10.0
        speed = 1.0
        loop = False
        source_id = ""
    for i in range(6):
        clip = _Clip()
        clip.source_id = "s%d" % i
        img = c._get_cv_frame_for_preview("s%d" % i, "/fake/%d.mp4" % i, clip, 0.5)
        assert img is not None
    assert len(c._async_decoders) == 3                           # 上限
    assert list(c._async_decoders) == ["s3", "s4", "s5"]         # 最久没用的先被挤掉
    assert sum(1 for d in _FakeDecoder.instances if d.stopped) == 3
    # 再碰 s3 → 它变成最近用的，下一次新源挤掉的是 s4
    c._get_cv_frame_for_preview("s3", "/fake/3.mp4", _Clip(), 0.6)
    clip = _Clip()
    clip.source_id = "s9"
    c._get_cv_frame_for_preview("s9", "/fake/9.mp4", clip, 0.5)
    assert list(c._async_decoders) == ["s5", "s3", "s9"]
    # 闲置回收
    time.sleep(0.3)
    c._reap_idle_decoders()
    assert len(c._async_decoders) == 0
    assert all(d.stopped for d in _FakeDecoder.instances)
    # 换工程：全停
    c._get_cv_frame_for_preview("s1", "/fake/1.mp4", _Clip(), 0.5)
    assert len(c._async_decoders) == 1
    c.set_data({"timeline": None, "segments": [], "style": {}, "text_overlays": [], "image_overlays": [],
                "duration": 1.0, "dims": [720, 1280]}, 0.0)
    assert len(c._async_decoders) == 0


def test_async_decoder_limits_threads_and_lookahead():
    import inspect
    from cutora.render import readers as R
    assert R.PREVIEW_DECODE_THREADS <= 4 and R.PREVIEW_LOOKAHEAD <= 16
    src = inspect.getsource(R.AsyncVideoDecoder.__init__)
    assert "threads=int(threads or PREVIEW_DECODE_THREADS)" in src
    assert "maxsize=int(lookahead or PREVIEW_LOOKAHEAD)" in src
    from cutora.preview import engine as E
    assert "PREVIEW_DECODE_THREADS" in inspect.getsource(E.CapturePool._open) if hasattr(E.CapturePool, "_open") \
        else "PREVIEW_DECODE_THREADS" in inspect.getsource(E)


def test_capture_pool_release_idle_and_registry(monkeypatch):
    """Q189：所有 CapturePool 进注册表，闲置超时的解码器统一放掉；取帧刷新时间戳。"""
    import time as _t
    import cutora.preview.engine as E

    class _Cap:
        def __init__(self):
            self.released = False

        def isOpened(self):
            return not self.released

        def release(self):
            self.released = True
    monkeypatch.setattr(E, "_open_video_capture", lambda path, hw=True, alpha_mode=None, threads=None: _Cap())
    pool = E.CapturePool(4)
    a = pool.get("a", "/x/a.mp4")
    b = pool.get("b", "/x/b.mp4")
    assert len(pool) == 2 and pool in E._POOLS
    pool._last["a"] -= 100.0                      # a 闲了 100 s
    assert E.reap_idle_pools(30.0) >= 1
    assert a.released and not b.released and len(pool) == 1
    assert pool.get("b", "/x/b.mp4") is b         # 还活着的继续用
    assert E.live_decoder_count() >= 1
    pool.clear()
    assert b.released and len(pool) == 0


def test_cmap_bitmap_set_matches_frozenset_semantics():
    from cutora.subtitle.font_coverage import _CpSet
    cps = [0x41, 0x42, 0x4E2D, 0x1F600, 0x10FFFF]
    s = _CpSet(iter(cps))                         # 生成器也行
    assert len(s) == 5 and bool(s)
    assert all(c in s for c in cps)
    assert 0x43 not in s and -1 not in s and 0x200000 not in s and "x" not in s
    assert sorted(s) == sorted(cps)
    assert len(s._bits) <= 0x110000 // 8 + 1
    assert not _CpSet([]) and 0 not in _CpSet([])


def test_prefetch_reuses_one_thread_per_state_and_shuts_down():
    """Q189b：预读不再每帧开一条线程（启动 39 s 建了 1400 条）：同一个帧状态复用一条，shutdown 后退出。"""
    import threading
    from cutora.render import decode_prefetch as P

    class Cap:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            return True, b"f%d" % self.n
    state = {"last_frame": None}
    cap = Cap()
    before = set(t.ident for t in threading.enumerate())
    for i in range(50):
        P.kick_read(cap, state)
        got = P.take(state)
        assert got == (True, b"f%d" % (i + 1))
    new_threads = [t for t in threading.enumerate() if t.ident not in before]
    assert len(new_threads) == 1 and new_threads[0].name == P.THREAD_NAME
    w = state["_pf_worker"]
    P.shutdown(state)
    assert "_pf_worker" not in state and not w.alive()
    # 停了以后再 kick 会重建一条
    P.kick_read(cap, state)
    assert P.take(state) == (True, b"f51")
    P.shutdown(state)
