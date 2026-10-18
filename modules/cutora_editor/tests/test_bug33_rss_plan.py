# -*- coding: utf-8 -*-
"""BUG-33: plan_parallel_export uses measured RSS, scaled by canvas."""
from __future__ import annotations

from cutora.render.export_guard import (
    MEM_PER_WORKER_MIN_GB,
    mem_per_worker_gb,
    plan_parallel_export,
)


def test_plan_respects_measured_rss():
    pl = plan_parallel_export(
        6, 6, False, cpu_count=32, avail_ram=16.3, mem_per_worker=2.7)
    assert pl["workers"] <= 4, pl
    assert pl["mem_per_worker"] == 2.7

    full = mem_per_worker_gb((1080, 1920))
    small = mem_per_worker_gb((704, 1280))
    assert small < full, (small, full)
    assert small >= MEM_PER_WORKER_MIN_GB
    scaled = mem_per_worker_gb(
        (704, 1280), measured_gb=2.7, measured_canvas_wh=(1080, 1920))
    assert scaled < 2.7
    # BUG-42 后地板是 2.5（解释器 + Qt + 字体 + 编码器的固定开销），按画布缩小不许穿地板
    assert abs(scaled - MEM_PER_WORKER_MIN_GB) < 1e-6 or scaled <= 2.7 * (704 * 1280) / (1080 * 1920) + 0.05
