# -*- coding: utf-8 -*-
"""T14 §2: CUTORA_PERF collector math and jsonl flush. No Qt."""
from __future__ import annotations

import json
import time

from cutora.core.preview_perf import PreviewPerf, percentile, reset_for_tests


def test_percentile_empty_and_single():
    assert percentile([], 50) == 0.0
    assert percentile([7.5], 95) == 7.5


def test_percentile_linear():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(xs, 0) == 1.0
    assert percentile(xs, 50) == 3.0
    assert percentile(xs, 100) == 5.0
    assert 4.0 <= percentile(xs, 75) <= 4.1


def test_collector_records_marks_and_spans(tmp_path):
    path = tmp_path / "preview_perf.jsonl"
    col = PreviewPerf(jsonl_path=str(path), window_s=10.0, fps=30.0)
    col.extra = {"mode": "test"}
    col.begin_frame()
    time.sleep(0.002)
    col.mark("video")
    time.sleep(0.003)
    col.add_span("layout", 0.001)
    col.add_span("stroke_shadow", 0.0005)
    col.add_span("bg_box", 0.0004)
    col.add_span("fx_spec", 0.0002)
    col.add_span("fx_raster", 0.0008)
    col.mark("draw_layers")
    col.mark("chrome")
    col.end_frame()
    col.flush()
    assert path.is_file()
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["n"] == 1
    assert line["extra"]["mode"] == "test"
    assert line["draw_layers_ms"]["mean"] >= 2.0
    assert line["layout_ms"]["mean"] >= 0.8
    summary = col.summary()
    assert summary["n"] == 1
    assert summary["draw_layers_pct_of_total"] > 0
    assert summary["fx_spec_ms"]["mean"] >= 0.15
    assert summary["fx_raster_ms"]["mean"] >= 0.7


def test_dropped_counts_over_budget(tmp_path):
    path = tmp_path / "preview_perf.jsonl"
    col = PreviewPerf(jsonl_path=str(path), window_s=10.0, fps=30.0)
    col.begin_frame()
    time.sleep(0.05)
    col.end_frame()
    assert col._dropped_total == 1
    col.flush()
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["dropped"] == 1


def test_disabled_global_wrappers_are_noop(monkeypatch):
    monkeypatch.delenv("CUTORA_PERF", raising=False)
    reset_for_tests()
    from cutora.core import preview_perf as pp
    assert pp.enabled() is False
    pp.begin_frame()
    pp.mark("video")
    pp.add_span("layout", 1.0)
    pp.end_frame()
    assert pp.summary()["enabled"] is False
    assert pp.get_collector() is None
    reset_for_tests()
