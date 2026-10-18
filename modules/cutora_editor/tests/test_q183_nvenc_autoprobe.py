# SPDX-License-Identifier: GPL-3.0-or-later
"""Q183：NVENC 并发会话数由软件自己实测，不靠用户去设置里猜。

- probe_nvenc_sessions：一次拉起 N 个编码器，数正常结束的；超出上限的那几个失败
- nvenc_max_sessions：手动覆盖 > 本机实测缓存 > 保守默认 3
- 老设置里存着的 3（以前设置页每次保存都写默认值）不算手动选择
- 缓存按驱动版本 / 显卡型号 / 时间失效
- 规划器的说明行写清是 NVENC 上限还是显存在限制
"""
from __future__ import annotations

import time

import pytest

from cutora.render import export_guard as G


class _FakeProc:
    def __init__(self, rc):
        self.returncode = rc
        self.killed = False

    def communicate(self, timeout=None):
        return b"", b""

    def kill(self):
        self.killed = True


def test_probe_counts_successful_sessions_only():
    spawned = []

    def spawn(i):
        p = _FakeProc(0 if i < 5 else 1)          # 驱动只给 5 路，后 3 个开会话失败
        spawned.append(p)
        return p

    assert G.probe_nvenc_sessions(max_try=8, hold_seconds=0.0, _spawn=spawn) == 5
    assert len(spawned) == 8                       # 确实是同时拉起 8 个再数，不是一个个试


def test_probe_zero_when_nothing_opens():
    assert G.probe_nvenc_sessions(max_try=4, hold_seconds=0.0, _spawn=lambda i: _FakeProc(1)) == 0


def _settings(monkeypatch, store):
    import cutora.core.history as H
    monkeypatch.setattr(H, "_load_app_settings", lambda: dict(store))

    def _save(s):
        store.clear()
        store.update(s)
    monkeypatch.setattr(H, "_save_app_settings", _save)


def test_priority_manual_then_probe_then_default(monkeypatch):
    store = {}
    _settings(monkeypatch, store)
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("581.10", "NVIDIA GeForce RTX 5060 Ti"))
    # 什么都没有 → 保守默认
    assert G.nvenc_max_sessions() == G.NVENC_MAX_SESSIONS
    assert "尚未实测" in G.nvenc_sessions_source()
    # 有实测缓存 → 用实测
    store[G.NVENC_PROBE_KEY] = {"sessions": 8, "driver": "581.10", "gpu": "NVIDIA GeForce RTX 5060 Ti",
                                "when": time.time()}
    assert G.nvenc_max_sessions() == 8
    assert "实测 8 路" in G.nvenc_sessions_source()
    # 手动覆盖优先
    G.save_nvenc_max_sessions(2)
    assert store[G.NVENC_SESSIONS_KEY] == 2 and store[G.NVENC_MANUAL_KEY] is True
    assert G.nvenc_max_sessions() == 2
    assert G.nvenc_sessions_source().startswith("手动")
    # 设回 0 = 自动
    G.save_nvenc_max_sessions(0)
    assert G.nvenc_manual_override() is None
    assert G.nvenc_max_sessions() == 8


def test_legacy_stored_default_3_is_not_a_manual_choice(monkeypatch):
    store = {G.NVENC_SESSIONS_KEY: 3}             # 老版本设置页每次保存都写进去的默认值
    _settings(monkeypatch, store)
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("581.10", "RTX"))
    assert G.nvenc_manual_override() is None
    store[G.NVENC_PROBE_KEY] = {"sessions": 6, "driver": "581.10", "gpu": "RTX", "when": time.time()}
    assert G.nvenc_max_sessions() == 6
    # 但老设置里明确改成别的数（比如 5）就是用户的选择
    store[G.NVENC_SESSIONS_KEY] = 5
    assert G.nvenc_manual_override() == 5


def test_probe_cache_invalidation(monkeypatch):
    store = {}
    _settings(monkeypatch, store)
    rec = {"sessions": 8, "driver": "581.10", "gpu": "RTX", "when": time.time()}
    store[G.NVENC_PROBE_KEY] = dict(rec)
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("581.10", "RTX"))
    assert G.nvenc_probe_result() is not None
    # 驱动换了 → 失效
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("590.01", "RTX"))
    assert G.nvenc_probe_result() is None
    # 时间太久 → 失效（≥5 路 30 天，<5 路 1 天）
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("581.10", "RTX"))
    store[G.NVENC_PROBE_KEY] = dict(rec, when=time.time() - 31 * 86400)
    assert G.nvenc_probe_result() is None
    store[G.NVENC_PROBE_KEY] = dict(rec, sessions=3, when=time.time() - 2 * 86400)
    assert G.nvenc_probe_result() is None
    store[G.NVENC_PROBE_KEY] = dict(rec, sessions=3, when=time.time() - 3600)
    assert G.nvenc_probe_result() is not None


def test_ensure_probe_runs_once_and_caches(monkeypatch):
    store = {}
    _settings(monkeypatch, store)
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: ("581.10", "RTX 5060 Ti"))
    calls = []

    def fake_probe(max_try=8, hold_seconds=4.0, _spawn=None):
        calls.append(1)
        return 8
    monkeypatch.setattr(G, "probe_nvenc_sessions", fake_probe)
    logs = []
    assert G.ensure_nvenc_probe(log=logs.append) == 8
    assert store[G.NVENC_PROBE_KEY]["sessions"] == 8 and store[G.NVENC_PROBE_KEY]["driver"] == "581.10"
    assert any("实测本机可同时开 8 路" in m for m in logs)
    # 第二次：缓存有效，不再跑
    assert G.ensure_nvenc_probe(log=logs.append) == 8
    assert len(calls) == 1
    # 没有 N 卡 → 不测、不写
    store.clear()
    monkeypatch.setattr(G, "gpu_identity", lambda max_age=60.0: None)
    assert G.ensure_nvenc_probe(log=logs.append) == 0 and G.NVENC_PROBE_KEY not in store


def test_plan_note_names_the_binding_limit(monkeypatch):
    monkeypatch.setattr(G, "gpu_memory_gb", lambda max_age=2.0: (16.0, 2.0, 14.0))   # 显存充裕
    monkeypatch.setattr(G, "nvenc_max_sessions", lambda: 3)
    monkeypatch.setattr(G, "nvenc_sessions_source", lambda: "尚未实测，按 3 路")
    monkeypatch.setattr(G, "measured_rss_for_canvas", lambda px: (None, None, False), raising=False)
    plan = G.plan_parallel_export(6, 6, True, cpu_count=32, avail_ram=40.0, mem_per_worker=2.0)
    assert plan["gpu_workers"] == 3
    assert any("NVENC 并发上限" in n and "尚未实测" in n for n in plan["notes"])
    # 显存紧张时说的是显存
    monkeypatch.setattr(G, "gpu_memory_gb", lambda max_age=2.0: (16.0, 14.0, 2.6))
    monkeypatch.setattr(G, "nvenc_max_sessions", lambda: 8)
    plan = G.plan_parallel_export(6, 6, True, cpu_count=32, avail_ram=40.0, mem_per_worker=2.0)
    assert plan["gpu_workers"] == 2
    assert any("显存空闲" in n for n in plan["notes"])


def test_startup_schedules_probe_and_dialog_has_auto():
    import inspect
    from cutora import app as A
    src = inspect.getsource(A.run_gui_startup)
    assert "ensure_nvenc_probe" in src
    from cutora.ui import settings_dialog as SD
    s2 = inspect.getsource(SD)
    assert "setSpecialValueText" in s2 and "nvenc_manual_override" in s2


def test_memory_hogs_note_names_main_process():
    """Q185：导出前体检记下内存是谁占的（主界面进程 + 全机前几名），别让用户猜。"""
    note = G.memory_hogs_note(top_n=3)
    assert note and "全机占内存前" in note
    assert "CUTORA 主界面" in note


def test_gui_memory_note_counts_arrays_and_qimages():
    """Q186：主界面 5 GB 是谁占的 —— 体检时把 Python 堆里的 numpy / QImage 总量记下来。"""
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    QApplication.instance() or QApplication([])
    keep = {"a": np.zeros((5000, 5000, 4), np.uint8), "q": QImage(1000, 1000, QImage.Format.Format_ARGB32)}
    note = G.gui_memory_note()
    assert "numpy 数组" in note and "QImage" in note and "帧缓存" in note
    import re
    gb = float(re.search(r"numpy 数组 \d+ 个共 ([\d.]+) GB", note).group(1))
    assert gb >= 0.09, note                      # 100 MB 的数组被数到了（从持有它的 dict 里找到）
    assert "≥64 MB 的大块" in note and "5000, 5000, 4" in note
    del keep
