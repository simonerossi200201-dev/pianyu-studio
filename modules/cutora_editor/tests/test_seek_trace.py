# -*- coding: utf-8 -*-
"""T14 appendix C: seek_trace jsonl. No Qt."""
from __future__ import annotations

import json
import os

from cutora.core.preview_perf import reset_for_tests
from cutora.core import seek_trace
from cutora.core.paths import refresh_cached_paths, user_logs_dir


def test_emit_noop_when_perf_off(monkeypatch):
    monkeypatch.delenv("CUTORA_PERF", raising=False)
    reset_for_tests()
    seek_trace.reset_for_tests()
    refresh_cached_paths()
    seek_trace.emit("heartbeat", timeline_ms=1)
    path = os.path.join(user_logs_dir(), "seek_trace.jsonl")
    assert not os.path.isfile(path)
    reset_for_tests()
    seek_trace.reset_for_tests()


def test_emit_writes_jsonl_when_perf_on(monkeypatch):
    monkeypatch.setenv("CUTORA_PERF", "1")
    reset_for_tests()
    seek_trace.reset_for_tests()
    refresh_cached_paths()
    seek_trace.emit("seek_request", pos_ms=150000, video_ms=150000)
    seek_trace.emit("cap_set", msec=150000.0, dt_ms=12.5)
    path = os.path.join(user_logs_dir(), "seek_trace.jsonl")
    assert os.path.isfile(path)
    lines = [json.loads(x) for x in open(path, encoding="utf-8").read().splitlines()]
    assert lines[0]["kind"] == "seek_request"
    assert lines[0]["pos_ms"] == 150000
    assert lines[1]["kind"] == "cap_set"
    assert lines[1]["dt_ms"] == 12.5
    reset_for_tests()
    seek_trace.reset_for_tests()
