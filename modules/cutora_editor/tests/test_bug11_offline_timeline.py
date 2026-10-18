# -*- coding: utf-8 -*-
"""BUG-11: timeline inspect does not compare task.duration to first clip."""
from __future__ import annotations

import json
import os

from cutora.model.offline import format_mismatch_lines, inspect_task
from cutora.model.project_io import deserialize_render_task

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19 = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline", "project.asproj.json")


def test_g19_inspect_no_false_duration_mismatch():
    data = json.loads(open(G19, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    assert float(task.get("duration") or 0) > 20
    report = inspect_task(task)
    assert report["duration_mismatch"] == []
    assert task.get("duration_mismatch") is False


def test_one_source_wrong_duration_reports_only_that(monkeypatch):
    data = json.loads(open(G19, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    tl = task["timeline"]
    sources = getattr(tl, "sources", {}) or {}
    sid, src = next(iter(sources.items()))
    from cutora.model.offline import _src_get
    target_path = str(_src_get(src, "path") or "")
    if hasattr(src, "duration"):
        src.duration = 999.0
    else:
        src["duration"] = 999.0
    target = os.path.normcase(os.path.normpath(target_path))
    def _probe(p):
        if os.path.normcase(os.path.normpath(str(p))) == target:
            return 8.0
        return None
    monkeypatch.setattr("cutora.model.offline.probe_duration_sec", _probe)
    report = inspect_task(task)
    rows = report["duration_mismatch"]
    assert len(rows) == 1
    assert str(rows[0].get("path") or "").replace("\\", "/").endswith(
        os.path.basename(target_path).replace("\\", "/"))
    lines = format_mismatch_lines(rows)
    assert "怎么办" in "\n".join(lines)
    assert "工程记录" in lines[0]
