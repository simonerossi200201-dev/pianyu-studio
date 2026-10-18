# -*- coding: utf-8 -*-
"""BUG-42: black screen = display driver reset caused by the export's decoders.

Worker logs (logs/export/parallel/20260918-154755/w0.log):
    Unable to lock D3D11VA surface (887a0005)      ← DXGI_ERROR_DEVICE_REMOVED
    retrieveFrame Error copying data from GPU to CPU
Every source was opened up front with VIDEO_ACCELERATION_ANY: 27 hardware
decoders × 5 workers + 3 NVENC sessions. Fix: export decodes in software,
opens sources lazily, keeps ≤ EXPORT_MAX_OPEN_CAPS open, releases finished
ones. Planner headroom / per-worker floor raised; watchdog sheds load.
"""
from __future__ import annotations

import os
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "tests", "data", "multilang", "en.mp4")


def test_export_sites_use_software_decoding():
    src = open(os.path.join(REPO, "cutora", "render", "render_worker.py"), encoding="utf-8").read()
    assert src.count("_open_video_capture(path, hw=export_hw_decode(), threads=export_decode_threads())") == 1  # pool
    assert src.count("_open_video_capture(overlay_path, hw=export_hw_decode())") == 1  # fallback
    # 导出路径不许再有开硬解的调用
    i = src.index("class RenderWorker")
    body = src[i:]
    assert "_open_video_capture(src_path)" not in body
    assert "_open_video_capture(overlay_path)" not in body


def test_open_video_capture_hw_false_reads():
    from cutora.render.readers import _open_video_capture
    cap = _open_video_capture(SRC, hw=False)
    assert cap is not None and cap.isOpened()
    ret, frame = cap.read()
    assert ret and frame is not None and frame.shape[1] == 960
    cap.release()


def test_pool_is_lazy_bounded_and_releases_finished_sources():
    from cutora.render.render_worker import EXPORT_MAX_OPEN_CAPS, _ExportCapPool
    sids = ["s%d" % i for i in range(6)]
    paths = {s: SRC for s in sids}
    last_end = {s: float(i + 1) for i, s in enumerate(sids)}   # s0 ends at 1.0, s5 at 6.0
    states = {}
    pool = _ExportCapPool(paths, last_end, states)
    assert len(pool) == 0 and pool.opened_total == 0          # 不预开
    assert pool.get("s0") is not None and len(pool) == 1
    assert "s0" in states and states["s0"]["src_path"] == SRC
    for s in sids[1:]:
        assert pool.get(s) is not None
    assert len(pool) <= EXPORT_MAX_OPEN_CAPS                    # LRU 上限
    assert pool.opened_total == 6
    assert pool.evicted_total == 6 - EXPORT_MAX_OPEN_CAPS
    # 时间线走到 4.5 s：结束时刻 + 1 s 余量 < 4.5 的（s0/s1/s2）该释放；s3/s4/s5 留着
    states["s5"]["decode_cursor"] = 12.0
    pool.evict_done(4.5)
    assert "s5" in pool and "s4" in pool and "s3" in pool
    for s in ("s0", "s1", "s2"):
        assert s not in pool
    assert states["s5"]["decode_cursor"] == 12.0               # 没被动到
    # 已释放的源再要 → 重开，游标作废
    states["s0"]["decode_cursor"] = 99.0
    states["s0"]["_pf_ret"] = True
    assert pool.get("s0") is not None
    assert states["s0"]["decode_cursor"] == -1.0
    assert "_pf_ret" not in states["s0"]
    # 读不到的源：一次失败后不再反复尝试
    pool2 = _ExportCapPool({"ghost": os.path.join(REPO, "ghost.mp4")}, {}, {})
    assert pool2.get("ghost") is None and pool2.get("ghost") is None
    pool.clear()
    assert len(pool) == 0


def test_pool_get_none_and_dict_api():
    from cutora.render.render_worker import _ExportCapPool
    pool = _ExportCapPool({"a": SRC}, {"a": 1.0}, {})
    assert pool.get(None) is None
    assert pool.get("nope", "dflt") == "dflt"
    assert pool.items() == [] and pool.values() == []
    cap = pool.get("a")
    assert [k for k, _ in pool.items()] == ["a"] and pool.values() == [cap]
    pool.clear()


def test_planner_on_lucas_machine_numbers():
    """2026-09-18 实机：32 GB / 可用 14.1 / 32 核 / 720×1280，以前派 5 个 → 黑屏。"""
    from cutora.render import export_guard as G
    plan = G.plan_parallel_export(6, 6, False, cpu_count=32, avail_ram=14.1,
                                  canvas_wh=(720, 1280), mem_per_worker=3.0)
    assert plan["workers"] == int((14.1 - G.MEM_HEADROOM_GB) // 3.0) == 2
    # 没有实测样本时的默认，也不能再派到 5
    per = G.mem_per_worker_gb((720, 1280), measured_gb=None)
    assert per >= G.MEM_PER_WORKER_MIN_GB
    plan = G.plan_parallel_export(6, 6, False, cpu_count=32, avail_ram=14.1,
                                  canvas_wh=(720, 1280), mem_per_worker=per)
    assert plan["workers"] <= 3
    # 低配：8 GB 机器可用 5 GB → 1 个
    plan = G.plan_parallel_export(6, 6, False, cpu_count=8, avail_ram=5.0,
                                  canvas_wh=(720, 1280), mem_per_worker=per)
    assert plan["workers"] == 1
    # 高配：64 GB 可用 50 GB、32 核 → 6 个
    plan = G.plan_parallel_export(6, 6, False, cpu_count=32, avail_ram=50.0,
                                  canvas_wh=(720, 1280), mem_per_worker=per)
    assert plan["workers"] == 6


def test_watchdog_sheds_newest_worker(monkeypatch):
    from cutora.render import export_guard as G
    from cutora.render import parallel as P
    killed = []
    monkeypatch.setattr(G, "system_memory_gb", lambda: (32.0, 1.0))
    monkeypatch.setattr(G, "terminate_pids", lambda pids: killed.extend(pids))
    mgr = P.ParallelExportManager.__new__(P.ParallelExportManager)
    mgr._lock = threading.Lock()
    mgr._snapshot = {}
    mgr._degrade_reason = ""

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

    mgr._running = {0: {"proc": _Proc(100), "indices": [0, 1]},
                    1: {"proc": _Proc(200), "indices": [2]}}
    assert mgr._shed_if_starving() is True
    assert killed == [200]                                  # 最晚启动的那个
    assert mgr._snapshot.get("pause_spawn") is True
    assert mgr._shed_if_starving() is False                 # 6 秒内不连杀
    mgr._last_shed_t = 0.0
    mgr._running = {0: {"proc": _Proc(100), "indices": [0, 1]}}
    assert mgr._shed_if_starving() is False                 # 只剩一个不杀
    monkeypatch.setattr(G, "system_memory_gb", lambda: (32.0, 9.0))
    mgr._running = {0: {"proc": _Proc(100), "indices": [0]}, 1: {"proc": _Proc(200), "indices": [1]}}
    assert mgr._shed_if_starving() is False                 # 内存够不杀


def test_export_hw_decode_defaults_off(monkeypatch):
    """诊断开关默认必须是关的：产品路径永远软件解码（不变量 #1）。"""
    from cutora.render.render_worker import export_hw_decode
    monkeypatch.delenv("CUTORA_EXPORT_HW_DECODE", raising=False)
    assert export_hw_decode() is False
    monkeypatch.setenv("CUTORA_EXPORT_HW_DECODE", "0")
    assert export_hw_decode() is False
    monkeypatch.setenv("CUTORA_EXPORT_HW_DECODE", "1")
    assert export_hw_decode() is True


def test_pool_scales_for_multitrack_split_screen_without_thrashing():
    """5 格分屏 (top1_bottom4) 同帧访问 5 条视频轨时，池容量按 max(EXPORT_MAX_OPEN_CAPS, 轨数+2) 抬升，0 次淘汰。"""
    from cutora.render.render_worker import EXPORT_MAX_OPEN_CAPS, _ExportCapPool
    sids = ["A", "B", "C", "D", "E"]
    paths = {s: SRC for s in sids}
    last_end = {s: float("inf") for s in sids}
    states = {}
    max_open = max(EXPORT_MAX_OPEN_CAPS, len(sids) + 2)
    pool = _ExportCapPool(paths, last_end, states, max_open=max_open)
    for _frame in range(5):
        for s in sids:
            assert pool.get(s) is not None
    assert pool.opened_total == 5
    assert pool.evicted_total == 0
    pool.clear()

