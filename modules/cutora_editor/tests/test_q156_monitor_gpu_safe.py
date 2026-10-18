# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q156 · 中央监视器显卡不爆：解码一律软解；没显示出来之前不开解码；开窗头几格放慢；
同时播放有上限；解码器闲置 / 关窗时一个一个放。"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_monitor_pool_is_software_decode(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q156"])
    import cutora.preview.engine as E
    import cutora.ui.central_monitor as CM
    seen = []

    def fake_open(path, hw=True, alpha_mode=None, threads=None):
        seen.append(hw)
        return None
    monkeypatch.setattr(E, "_open_video_capture", fake_open)
    eng = CM._make_engine()
    assert eng.pool.real.hw is False and eng.pool.real.capacity == CM.MONITOR_POOL
    eng.pool.real.get("k", "/nonexistent/x.mp4")
    assert seen == [False]
    assert E.CapturePool(3).hw is False                      # Q189d：主预览也默认软解
    monkeypatch.setenv("CUTORA_PREVIEW_HW", "1")
    assert E.CapturePool(3).hw is True                       # 环境变量可切回硬解


def test_release_gradually_one_by_one():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q156"])
    import cutora.ui.central_monitor as CM

    class _Cap:
        released = []

        def __init__(self, n):
            self.n = n

        def isOpened(self):
            return True

        def release(self):
            _Cap.released.append((self.n, time.monotonic()))
    pool = CM._LazyPool(4)
    for i in range(4):
        pool.real._caps["c%d" % i] = _Cap(i)
    t0 = time.monotonic()
    pool.release_gradually(step_sec=0.03)
    assert [n for n, _t in _Cap.released] == [0, 1, 2, 3] and len(pool.real) == 0
    assert time.monotonic() - t0 >= 0.09                     # 中间真的歇了


def test_nothing_scheduled_before_show_and_play_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from test_q144_central_monitor import _app, _clip, _task, _wait
    import cutora.ui.central_monitor as CM
    app = _app()
    monkeypatch.setattr(CM, "PLAY_MAX", 3)
    tasks = [_task(_clip(tmp_path, "0x%02X%02X40" % (40 + 20 * i, 200 - 20 * i), dur=2)) for i in range(6)]

    class _Host:
        pass
    host = _Host()
    host.tasks = tasks
    dlg = CM.CentralMonitorDialog(host, tasks)
    g = dlg.grid
    try:
        # 还没 show：什么都不在屏幕里，渲染队列是空的
        for _ in range(20):
            app.processEvents()
        assert g.viewport_rect().isEmpty()
        assert g._renderer.pending_count() == 0 and all(t.req_gen != t.gen for t in g.tiles)
        dlg.resize(1200, 800)
        dlg.show()
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles), 20.0)
        assert g._warm_left < CM.WARM_COUNT                   # 热身间隔用上了
        # 同时播放上限：全部播放只开 3 格，再点第 4 格 ▶ 把最早的换下来
        g.play_all(True)
        assert len(g.playing_tiles()) == 3
        first = g.playing_tiles()[0]
        g.set_tile_playing(g.tiles[5], True)
        assert len(g.playing_tiles()) == 3 and g.tiles[5].playing and not first.playing
        g.play_all(False)
        assert not g.playing_tiles()
        # relayout 不再把正在后台画的格子作废（同尺寸）
        gens = [t.gen for t in g.tiles]
        g.relayout()
        assert [t.gen for t in g.tiles] == gens
    finally:
        dlg.close()
        for _ in range(10):
            app.processEvents()
            time.sleep(0.01)
