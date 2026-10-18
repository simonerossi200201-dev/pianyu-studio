# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q193 · 低配机体检：没有 N 卡要先说清楚、1366×768 小屏不把窗口撑出屏幕、16 GB 内存也能导出。"""
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
os.environ.setdefault("CUTORA_GPU_FX", "0")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q193"])


def _answer(monkeypatch, label):
    from PySide6.QtWidgets import QMessageBox
    asked = []

    def fake_exec(self):
        asked.append(self.informativeText())
        for b in self.buttons():
            if b.text() == label:
                b.click()
                break
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return asked


def test_no_gpu_asks_once_and_says_why(monkeypatch):
    _app()
    from cutora.ui import gpu_notice as GN
    GN.reset()
    monkeypatch.setenv("CUTORA_FAKE_NO_GPU", "1")
    asked = _answer(monkeypatch, "继续（用 CPU）")
    assert GN.confirm(None, "ai_matte") is True
    assert len(asked) == 1 and "AI 抠像" in asked[0] and "NVIDIA" in asked[0] and "CPU" in asked[0]
    assert GN.confirm(None, "ai_matte") is True and len(asked) == 1      # 同一功能这次会话只问一次
    asked2 = _answer(monkeypatch, "取消")
    assert GN.confirm(None, "locate") is False and GN.declined("locate")
    assert asked2 and "识别开头时间戳" in asked2[0]
    assert "NVIDIA" in GN.startup_note()
    GN.reset()


def test_prompt_never_stacks(monkeypatch):
    """提示框开着时，粘贴后自动识别的定时器又来问 → 不叠第二个框（实测会一层套一层把界面卡死）。"""
    _app()
    from cutora.ui import gpu_notice as GN
    from PySide6.QtWidgets import QMessageBox
    GN.reset()
    monkeypatch.setenv("CUTORA_FAKE_NO_GPU", "1")
    nested = []

    def fake_exec(self):
        nested.append(GN.confirm(None, "locate"))       # 框开着时又来一次
        for b in self.buttons():
            if b.text() == "继续（用 CPU）":
                b.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert GN.confirm(None, "locate") is True
    assert nested == [False] and not GN.declined("locate")
    GN.reset()


def test_with_gpu_never_asks(monkeypatch):
    _app()
    from cutora.ui import gpu_notice as GN
    GN.reset()
    monkeypatch.delenv("CUTORA_FAKE_NO_GPU", raising=False)
    monkeypatch.setattr(GN, "has_gpu", lambda: True)
    asked = _answer(monkeypatch, "取消")
    for key in GN.FEATURES:
        assert GN.confirm(None, key) is True
    assert asked == [] and GN.startup_note() == ""


def test_gpu_features_are_gated_at_their_entry_points():
    import inspect
    from cutora.ui import main_window as MW
    from cutora.ui import batch_asr_tab, batch_text_tab
    from cutora.ui.panels import ai_matte_panel, ai_track_panel
    from cutora.batch import script_stitcher
    assert '_gpu_ok(self, "asr")' in inspect.getsource(MW.MainWindow._ui_auto_subtitle)
    assert '"batch_asr"' in inspect.getsource(batch_asr_tab)
    assert '_gpu_ok(page, "locate")' in inspect.getsource(batch_text_tab)
    assert '"ai_matte"' in inspect.getsource(ai_matte_panel)
    assert '"tracking"' in inspect.getsource(ai_track_panel)
    assert '_gpu_ok(self, "stitch")' in inspect.getsource(script_stitcher.StitcherDialog._start)


def test_16gb_4core_still_exports(monkeypatch):
    from cutora.render import export_guard as G
    monkeypatch.setattr(G, "gpu_memory_gb", lambda max_age=2.0: None)
    plan = G.plan_parallel_export(20, 6, False, cpu_count=4, avail_ram=9.0, canvas_wh=(1080, 1920))
    assert plan["workers"] >= 1 and plan["gpu_workers"] == 0
    plan = G.plan_parallel_export(20, 6, False, cpu_count=8, avail_ram=20.0, canvas_wh=(1080, 1920))
    assert plan["workers"] >= 2


@pytest.fixture(scope="module")
def small_win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q193_")
    app = _app()
    from PySide6.QtCore import QEventLoop, QTimer
    from cutora.ui.main_window import MainWindow
    from cutora.model.timeline import Timeline

    def pump(ms=80):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()
    w = MainWindow()
    w.resize(1366, 728)                  # 1366×768 屏幕减任务栏
    w.show()
    pump()
    w.show_editor()
    pump()
    w.tasks = [{"video": "/nonexistent/v%d.mp4" % i, "name": "v%d.mp4" % i, "segments": [],
                "timeline": Timeline(1080, 1920, 30.0), "text_overlays": [], "image_overlays": [],
                "dims": [1080, 1920], "style": {}, "duration": 120.0, "text": ""} for i in range(6)]
    w._pump = pump
    yield w
    try:
        w.room_host.enter("edit")
    except Exception:
        pass


def test_small_screen_rooms_never_grow_the_window(small_win):
    w = small_win
    h0 = w.height()
    for key in ("batch", "monitor", "stitch"):
        w.room_host.enter(key)
        w._pump(120)
        assert w.height() == h0, "%s 把主窗口撑高到 %d（屏幕只有 768）" % (key, w.height())
    w.room_host.enter("batch")
    d = w.room_host.widget("batch")
    d.tabs.setCurrentWidget(d.scatter_tab)
    w._pump(120)
    assert w.height() == h0, "scatter tab 把主窗口撑高到 %d（屏幕只有 768）" % w.height()
    w.room_host.enter("edit")
    w._pump(80)
    assert w.height() == h0 and w.minimumSizeHint().height() <= 728


def test_small_screen_text_toolbar_fits(small_win):
    w = small_win
    w.room_host.enter("batch")
    d = w.room_host.widget("batch")
    d.tabs.setCurrentWidget(d.text_tab)
    w._pump(150)
    page = d.text_tab
    right = page.mapTo(d, page.rect().topRight()).x()
    for b in (page.btn_said_all, page.btn_said_bad, page.btn_zoom):
        x = b.mapTo(d, b.rect().topRight()).x()
        assert x <= right + 1, "%s 被切到栏外" % b.text()
    w.room_host.enter("edit")
