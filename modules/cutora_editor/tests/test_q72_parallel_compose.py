# -*- coding: utf-8 -*-
"""Q72: parallel export must share overlay threads and count inflight RAM.

Defects these parameters are meant to catch:
- 4 jobs on 16 cores each inherit min(CPU, 8) overlay threads (32 painters)
- plan_parallel_export admits workers as if the 256 MiB overlay queue were free
- a living decoder that takes >30 s/frame trips OrderedHandoff.take_next
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q72-compose"])


def test_q72_parallel_sets_compose_workers(tmp_path, monkeypatch):
    """4 parallel jobs, 16 cores → each child env CUTORA_COMPOSE_WORKERS=4.

    16 // 4 = 4. The OMP formula (cpu // n_jobs // 2 = 2) and the Q11
    default min(cpu, 8) = 8 would both miss this.
    """
    from cutora.render import parallel as P
    from cutora.render.compose_parallel import (
        COMPOSE_WORKERS_ENV,
        resolve_compose_workers,
    )

    captured = []

    class FakePopen:
        _n = 0

        def __init__(self, *args, **kwargs):
            FakePopen._n += 1
            self.pid = 4000 + FakePopen._n
            captured.append(dict(kwargs.get("env") or {}))

        def poll(self):
            return None

    monkeypatch.setattr(P.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(P.subprocess, "Popen", FakePopen)

    mgr = P.ParallelExportManager.__new__(P.ParallelExportManager)
    mgr._chunks = [[0], [1], [2], [3]]
    mgr._chunk_paths = {}
    for i in range(4):
        mgr._chunk_paths[i] = {
            "job_path": str(tmp_path / ("w%d.json" % i)),
            "progress_path": str(tmp_path / ("w%d.progress" % i)),
            "result_path": str(tmp_path / ("w%d.result" % i)),
            "log_path": str(tmp_path / ("w%d.log" % i)),
            "indices": [i],
            "use_gpu": False,
        }
    mgr._running = {}
    mgr._lock = threading.Lock()
    mgr._hb = None
    mgr._child_cmd = lambda job_path: ([sys.executable, "-c", "pass"], str(tmp_path))

    for wid in range(4):
        mgr._launch(wid)

    workers = [env for env in captured if COMPOSE_WORKERS_ENV in env]
    assert len(workers) == 4, captured
    for env in workers:
        assert env.get(COMPOSE_WORKERS_ENV) == "4", env.get(COMPOSE_WORKERS_ENV)
    # Child RenderWorker does not pass compose_workers; it reads this env.
    monkeypatch.setenv(COMPOSE_WORKERS_ENV, workers[0][COMPOSE_WORKERS_ENV])
    assert resolve_compose_workers(cpu_count=16) == 4


def test_q72_memory_budget_includes_inflight():
    """2 workers * 2.5 GB + 6 GB headroom = 11.0 GB. Leftover 0.1 GB cannot
    cover 2 × 256 MiB inflight, so the planner must drop to 1 worker.

    If inflight is only a decorative field, workers stay 2.
    """
    from cutora.render.export_guard import (
        COMPOSE_INFLIGHT_PER_WORKER_GB,
        MEM_HEADROOM_GB,
        plan_parallel_export,
    )

    per = 2.5
    avail = MEM_HEADROOM_GB + 2 * per + 0.1
    pl = plan_parallel_export(
        4, 4, False, cpu_count=32, avail_ram=avail, mem_per_worker=per)
    assert pl["mem_per_worker"] == per, pl
    assert COMPOSE_INFLIGHT_PER_WORKER_GB == 0.25
    assert pl["workers"] == 1, pl
    assert "compose_inflight_gb" in pl, pl
    assert abs(
        pl["compose_inflight_gb"]
        - COMPOSE_INFLIGHT_PER_WORKER_GB * pl["workers"]
    ) < 1e-6, pl


def test_q72_slow_decoder_does_not_timeout(qapp):
    """Decoder sleeps 40 s on the only frame but stays alive. Old take_next
    cap is 30 s; export must still finish.
    """
    from cutora.render.compose_parallel import run_compose_pool

    class RW:
        def _compose_video_frame(self, t, ctx):
            time.sleep(40.0)
            return object(), None

        def _compose_overlay_frame(self, t, canvas, ctx, video, backdrop):
            return canvas

    class Pipe:
        def __init__(self):
            self.frames = []

        def put(self, data):
            self.frames.append(data)
            return True

    pipe = Pipe()
    stop = threading.Event()
    t0 = time.monotonic()
    run_compose_pool(
        render_worker=RW(),
        decode_ctx=None,
        overlay_ctx_factory=lambda: None,
        frame_start=0,
        frame_end=1,
        fps=25.0,
        width=16,
        height=8,
        n_workers=1,
        pipe_writer=pipe,
        is_running=lambda: True,
        stop_event=stop,
        mad_want=None,
        mad_refs=None,
        on_progress=None,
        total_frames=1,
        budget_gb=2.5,
    )
    elapsed = time.monotonic() - t0
    assert elapsed >= 40.0, "decoder did not actually sleep 40s: %.2f" % elapsed
    assert len(pipe.frames) == 1, len(pipe.frames)
    assert len(pipe.frames[0]) == 16 * 8 * 4
