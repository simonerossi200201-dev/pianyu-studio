# SPDX-License-Identifier: GPL-3.0-or-later
"""Q189g：暂停心跳不再反复要预览帧。

Luca 开着工程坐着：8 分钟 canvas.paint 4611、timeline.paint 2317、
preview.request/rendered 2304（刚好 200 ms 定时器）。空工程已经是 0.01 核
（curr_idx < 0 直接返回）；这一刀让开着工程空闲也停住。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q189g_"))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q189g"])


def test_set_position_noop_when_unchanged():
    _qapp()
    from cutora.ui.timeline.widget import TimelineWidget
    w = TimelineWidget()
    paints = []
    w.update = lambda *a, **k: paints.append(1)
    w.set_position(500)
    w.set_position(500)
    w.set_position(500)
    assert paints == [1]
    w.set_position(800)
    assert paints == [1, 1] and int(w.position) == 800


def test_paused_heartbeat_skips_preview_request_and_repaint():
    from PySide6.QtWidgets import QApplication
    from cutora.core.counters import value
    from test_q145b_timeline_shortcuts import _load

    win, tl, _task = _load()
    win._timeline_playing = False
    if getattr(win, "video_canvas", None) is not None:
        win.video_canvas._playback_full = False
    win._present_preview_frame()
    QApplication.processEvents()
    if getattr(win, "video_canvas", None) is not None:
        win.video_canvas._playback_full = False

    req0 = value("preview.request") + value("preview.request_full")
    paint0 = value("canvas.paint")
    tl0 = value("timeline.paint")
    skip0 = value("preview.idle_skip")
    for _ in range(12):
        win.sync_ui()
        QApplication.processEvents()
    assert value("preview.request") + value("preview.request_full") == req0
    assert value("preview.idle_skip") >= skip0 + 12
    assert value("canvas.paint") - paint0 <= 2
    assert value("timeline.paint") - tl0 <= 2


def test_seek_and_play_still_request_preview():
    from PySide6.QtWidgets import QApplication
    from cutora.core.counters import value
    from test_q145b_timeline_shortcuts import _load

    win, _tl, _task = _load()
    win._timeline_playing = False
    if getattr(win, "video_canvas", None) is not None:
        win.video_canvas._playback_full = False
    win.sync_ui()
    QApplication.processEvents()
    req0 = value("preview.request") + value("preview.request_full")

    win.on_seek_request(int(getattr(win, "_timeline_ms", 0) or 0) + 1000)
    QApplication.processEvents()
    assert value("preview.request") + value("preview.request_full") >= req0 + 1

    if getattr(win, "video_canvas", None) is not None:
        win.video_canvas._playback_full = False
    win.sync_ui()
    QApplication.processEvents()
    req1 = value("preview.request") + value("preview.request_full")
    win._timeline_playing = True
    win.sync_ui()
    QApplication.processEvents()
    assert value("preview.request") + value("preview.request_full") >= req1 + 1
    win._timeline_playing = False


def test_preview_worker_and_decoder_sleep_until_notified():
    """没任务时预览线程 / 解码器睡在 condition 上，不再 20 Hz / 2 Hz 轮询。"""
    import inspect
    from cutora.preview.worker import PreviewWorker
    from cutora.render import readers as R
    run = inspect.getsource(PreviewWorker.run)
    assert "self._cond.wait()" in run
    assert "wait(0.05)" not in run
    dec = inspect.getsource(R.AsyncVideoDecoder.run)
    assert "self._wake.wait()" in dec
    assert "wait(0.25)" not in dec and "wait(0.5)" not in dec


def test_timeline_scroll_range_skips_when_unchanged():
    _qapp()
    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.ui.timeline.container import TimelineContainer
    tl = TimelineWidget()
    tl.duration = 8000
    tl.pixels_per_second = 80.0
    box = TimelineContainer(tl)
    box.resize(800, 200)
    box.show()
    box.update_scroll_range()
    key = box._scroll_range_key
    calls = []
    orig = box.scrollbar.setRange
    box.scrollbar.setRange = lambda *a, **k: calls.append(a) or orig(*a, **k)
    box.update_scroll_range()
    box.update_scroll_range()
    assert calls == [] and box._scroll_range_key == key
    tl.duration = 12000
    box.update_scroll_range()
    assert calls and box._scroll_range_key != key
