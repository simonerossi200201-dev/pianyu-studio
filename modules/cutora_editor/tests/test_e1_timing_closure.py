# -*- coding: utf-8 -*-
"""T08 / T35 E1: raster row splits into five buckets; stages do not exceed wall.

Probe-only. Does not change pixels. Requires CUTORA_PROFILE_EXPORT via
export_probe.configure (tests do not rely on import-time env).
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.render.export_probe import (
    LEAF_STAGES,
    RASTER_PARTS,
    add_ms,
    begin,
    configure,
    dump,
    end,
    note,
    raster_breakdown_ok,
    reset,
    stages_le_wall,
    stage_sum_s,
)


def _arm():
    configure(True)
    reset()


def test_dump_exposes_raster_breakdown_and_instrumentation():
    _arm()
    stats = dump()
    per = stats["ms_per_frame"]
    for key in RASTER_PARTS:
        assert key in per, key
    assert "raster" in per
    assert "instrumentation" in per
    assert "raster_miss_by_family" in stats
    assert stats["probe_on"] is True
    assert "closure" in stats
    assert "breakdown_vs_raster" in stats["closure"]
    assert "stage_sum_le_wall" in stats["closure"]


def test_nested_sibling_stages_are_exclusive():
    """decode inside overlay must not be counted twice (十三报 阶段和 > 墙钟)."""
    _arm()
    begin("overlay")
    t0 = time.perf_counter()
    time.sleep(0.015)
    add_ms("decode", time.perf_counter() - t0)
    t1 = time.perf_counter()
    time.sleep(0.010)
    add_ms("overlay_tail", time.perf_counter() - t1)
    end("overlay")
    note("frames", 1)
    stats = dump()
    totals = stats["ms_total"]
    overlay = totals["overlay"]
    decode = totals["decode"]
    assert decode >= 8.0, decode
    # Exclusive overlay is the sleep after decode, plus tiny enter/exit, NOT
    # overlay_inclusive = decode + tail.
    assert overlay < decode + totals.get("overlay_tail", 0) + 5.0, (
        overlay, decode, totals)
    assert overlay >= 6.0, overlay
    # Inclusive would be decode+tail; exclusive overlay is the tail.


def test_raster_parts_sum_to_raster_row():
    """Helper identity: five buckets vs raster ±5% (live draw is the other test)."""
    stats = {
        "wall_s": 1.0,
        "frames": 100,
        "ms_per_frame": {
            "raster": 10.0,
            "raster_miss": 4.0,
            "layer_blit": 3.5,
            "layout": 0.4,
            "word_fx": 0.3,
            "other": 1.8,
        },
        "ms_total": {
            "raster": 1000.0,
            "raster_miss": 400.0,
            "layer_blit": 350.0,
            "layout": 40.0,
            "word_fx": 30.0,
            "other": 180.0,
        },
    }
    assert raster_breakdown_ok(stats, tol=0.05)
    stats["ms_per_frame"]["other"] = 0.0
    stats["ms_total"]["other"] = 0.0
    assert not raster_breakdown_ok(stats, tol=0.05)


def test_stage_sum_does_not_exceed_wall():
    """Every add_ms is real elapsed time inside dump()'s wall window."""
    _arm()
    begin("overlay")
    t0 = time.perf_counter()
    time.sleep(0.008)
    add_ms("decode", time.perf_counter() - t0)
    t1 = time.perf_counter()
    time.sleep(0.004)
    add_ms("layout", time.perf_counter() - t1)
    end("overlay")
    t2 = time.perf_counter()
    time.sleep(0.003)
    add_ms("pipe", time.perf_counter() - t2)
    note("frames", 1)
    stats = dump()
    assert stages_le_wall(stats), {
        "stage_sum_s": stage_sum_s(stats),
        "wall_s": stats["wall_s"],
        "ms_per_frame": stats["ms_per_frame"],
        "leaf": list(LEAF_STAGES),
    }
    assert stats["closure"]["stage_sum_le_wall"] is True
    # Inclusive overlay+decode would exceed the exclusive sum; decode must
    # not be inside overlay.
    totals = stats["ms_total"]
    assert totals["overlay"] + totals["decode"] <= (stats["wall_s"] * 1000.0) + 2.0


def test_probe_off_is_noop_and_reports_flag():
    configure(False)
    reset()
    add_ms("overlay", 1.0)
    add_ms("decode", 0.5)
    note("frames", 10)
    stats = dump()
    assert stats["probe_on"] is False
    assert stats["ms_total"].get("overlay", 0) == 0
    assert stats["ms_total"].get("decode", 0) == 0


def test_instrumentation_row_grows_with_probe_calls():
    _arm()
    for _ in range(200):
        add_ms("other", 0.0)
    stats = dump()
    assert stats["ms_total"]["instrumentation"] > 0, stats["ms_total"]


def test_draw_frames_close_raster_row():
    """Real pipeline.draw: five buckets cover the overlay/raster span ±5%."""
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.render.textfx.pipeline import draw as fx_draw, reset_draw_counters
    from cutora.render.textfx.cache import ROI_CACHE

    app = QApplication.instance() or QApplication(["t08-e1"])
    _ = app
    _arm()
    reset_draw_counters()
    ROI_CACHE.clear()

    event = {
        "text": "Find its place in the frame",
        "start": 0.0,
        "end": 2.0,
        "id": "seg0",
        "words": [
            {"word": "Find", "start": 0.0, "end": 0.4},
            {"word": "its", "start": 0.4, "end": 0.8},
            {"word": "place", "start": 0.8, "end": 1.2},
            {"word": "in", "start": 1.2, "end": 1.5},
            {"word": "the", "start": 1.5, "end": 1.7},
            {"word": "frame", "start": 1.7, "end": 2.0},
        ],
    }
    style = {
        "font_family": "Arimo",
        "fontsize": 42,
        "pos_y": 0.78,
        "fx": {
            "bg": {"type": "box", "color": "#00000099"},
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "emphasis": {"anim": "karaoke", "auto": True},
        },
    }
    w, h = 480, 848
    n = 12
    frames = []
    for _i in range(n):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        frames.append((img, QPainter(img)))
    begin("overlay")
    try:
        for i, (_img, p) in enumerate(frames):
            fx_draw(p, event, style, 0.05 + i * 0.04, w, h)
            note("frames")
    finally:
        end("overlay")
        for _img, p in frames:
            p.end()
    stats = dump()
    per = stats["ms_per_frame"]
    assert per["raster"] > 0, per
    assert per["layer_blit"] > 0, per
    assert per["layout"] > 0, per
    assert per["raster_miss"] > 0, per
    assert raster_breakdown_ok(stats, tol=0.05), stats
    assert stages_le_wall(stats), stats["closure"]
    fam = stats["raster_miss_by_family"]
    assert "text" in fam or "box" in fam, fam


def test_render_frame_returns_canvas():
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from cutora.render.export_renderer import ExportRenderer

    app = QApplication.instance() or QApplication(["t08-e1-rf"])
    _ = app
    img = ExportRenderer.render_frame({"segments": []}, 16, 16, 0.0)
    assert img is not None and not img.isNull()
    assert img.width() == 16
    assert img.height() == 16
