# -*- coding: utf-8 -*-
"""BUG-40 / BUG-41: why 6/6 parallel exports died at frame 30.

BUG-41  worker_rss.json recorded 0.004 GB per worker: the parent measured
        the venv python.exe launcher stub, not the interpreter. The planner
        then budgeted 1.5 GB/worker (floor) instead of the real ~2.7 GB and
        over-committed RAM → watchdog degrade.
BUG-40  Any degrade level (even pause_spawn) aborted every in-flight worker
        at its next 30-frame check, CPU workers included.
"""
from __future__ import annotations

import json
import os
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_render_worker_degrade_only_for_nvenc_in_flight():
    src = open(os.path.join(REPO, "cutora", "render", "render_worker.py"), encoding="utf-8").read()
    i = src.index("read_degrade(job_dir)")
    body = src[i - 200:i + 900]
    assert '_lvl == "gpu_off" and self.use_gpu' in body, body
    # 老写法不许回来
    assert "if job_dir and read_degrade(job_dir):" not in src


def test_worker_reports_own_rss_in_progress_file():
    src = open(os.path.join(REPO, "cutora", "render", "parallel.py"), encoding="utf-8").read()
    i = src.index("def _write_progress")
    body = src[i:i + 900]
    assert '"rss_gb"' in body and "os.getpid()" in body, body


def test_parent_takes_rss_from_progress(tmp_path):
    """父进程 _sample：进度文件里有 rss_gb 就以它为峰值，不再只看启动壳。"""
    from cutora.render.parallel import ParallelExportManager

    mgr = ParallelExportManager.__new__(ParallelExportManager)
    mgr._lock = threading.Lock()
    mgr._running = {}
    mgr._finished_infos = []
    mgr._snapshot = {}
    mgr._hb = None
    mgr._done_items = 0
    mgr._failed = []
    mgr._launch_done = False
    mgr._degrade_reason = ""
    mgr._canvas_wh = (720, 1280)
    mgr._ui_owner = None

    class _Proc:
        pid = 999999  # 不存在的 pid → process_rss_gb 读不到 → 0

        def poll(self):
            return None

    prog = tmp_path / "w0.progress"
    prog.write_text(json.dumps({"done": 0, "cur": 40, "total": 1,
                                "rss_gb": 2.71, "pid": 4242}), encoding="utf-8")
    info = {"proc": _Proc(), "progress_path": str(prog), "indices": [0],
            "partial": 0, "peak_rss": 0.0}
    mgr._running[0] = info
    mgr._sample()
    assert abs(info["peak_rss"] - 2.71) < 1e-6, info["peak_rss"]
    assert abs(info["partial"] - 0.4) < 1e-6

    # 峰值只增不减
    prog.write_text(json.dumps({"done": 0, "cur": 80, "total": 1, "rss_gb": 1.9}), encoding="utf-8")
    mgr._sample()
    assert abs(info["peak_rss"] - 2.71) < 1e-6


def test_cleanup_keeps_forensics(tmp_path, monkeypatch):
    from cutora.render import parallel as P
    mgr = P.ParallelExportManager.__new__(P.ParallelExportManager)
    tmpdir = tmp_path / "cutora_parallel_x"
    tmpdir.mkdir()
    (tmpdir / "w0.log").write_text("hello", encoding="utf-8")
    (tmpdir / "degrade.flag").write_text('{"level":"pause_spawn"}', encoding="utf-8")
    (tmpdir / "w0.json").write_text("{}", encoding="utf-8")  # 任务文件不留（可能很大）
    mgr._tmpdir = str(tmpdir)
    logs_root = tmp_path / "logs"
    monkeypatch.setattr("cutora.core.paths.user_logs_dir", lambda: str(logs_root))
    mgr._cleanup_tmp()
    assert not tmpdir.exists()
    runs = list((logs_root / "export" / "parallel").iterdir())
    assert len(runs) == 1
    kept = sorted(p.name for p in runs[0].iterdir())
    assert kept == ["degrade.flag", "w0.log"], kept


def test_bogus_rss_samples_are_ignored(tmp_path, monkeypatch):
    """0.004 GB 的样本（启动壳）不许进 p90，也不许再被记下来。"""
    from cutora.render import export_guard as G
    path = tmp_path / "worker_rss.json"
    monkeypatch.setattr(G, "_rss_log_path", lambda: str(path))
    path.write_text(json.dumps({"samples": [
        {"peak_gb": 0.004, "canvas_px": 2073600},
        {"peak_gb": 0.004, "canvas_px": 2073600},
        {"peak_gb": 2.6, "canvas_px": 2073600},
        {"peak_gb": 2.8, "canvas_px": 2073600},
    ]}), encoding="utf-8")
    p90, px = G.measured_rss_p90()
    assert p90 >= 2.6, p90
    assert px == 2073600
    G.record_worker_rss(0.004, canvas_wh=(1080, 1920), pid=1)
    n = len(json.loads(path.read_text(encoding="utf-8"))["samples"])
    assert n == 4, "0.004 GB 不该被记录"
    G.record_worker_rss(2.7, canvas_wh=(720, 1280), pid=2)
    n = len(json.loads(path.read_text(encoding="utf-8"))["samples"])
    assert n == 5
    # 全是假样本时要回落到常量，而不是 1.5 GB 地板
    path.write_text(json.dumps({"samples": [{"peak_gb": 0.004, "canvas_px": 2073600}]}), encoding="utf-8")
    assert G.measured_rss_p90() == (None, 2073600) or G.measured_rss_p90()[0] is None
    assert G.mem_per_worker_gb((1080, 1920)) >= G.MEM_PER_WORKER_GB - 1e-6
