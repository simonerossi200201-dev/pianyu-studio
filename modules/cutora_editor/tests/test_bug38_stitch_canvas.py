# -*- coding: utf-8 -*-
"""BUG-38: the stitcher wrote 1080×1920 no matter what the user picked.

Canvas priority for a stitched project: user's project canvas → first source
size → 1080×1920 only as the last resort.
"""
from __future__ import annotations

import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_960x540 = os.path.join(REPO, "tests", "data", "multilang", "en.mp4")


def _placement(path):
    from cutora.batch.timeline_stitch import ClipPlacement
    return ClipPlacement(source_path=path, in_t=0.0, out_t=1.0, start_t=0.0)


def test_source_wh_probes_real_size():
    from cutora.batch.timeline_stitch import source_wh
    assert os.path.isfile(SRC_960x540), SRC_960x540
    assert source_wh(SRC_960x540) == (960, 540)
    assert source_wh(os.path.join(REPO, "does_not_exist.mp4")) is None
    assert source_wh("") is None


def test_canvas_priority():
    from cutora.batch.timeline_stitch import (
        CANVAS_H, CANVAS_W, canvas_for_placements,
    )
    p = _placement(SRC_960x540)
    # 用户点了 720×1280 → 就是 720×1280，素材尺寸靠边
    assert canvas_for_placements([p], (720, 1280)) == (720, 1280)
    # 没点 → 素材尺寸
    assert canvas_for_placements([p], None) == (960, 540)
    # 素材读不到、也没点 → 才落到常量
    ghost = _placement(os.path.join(REPO, "ghost.mp4"))
    assert canvas_for_placements([ghost], None) == (CANVAS_W, CANVAS_H)
    # 第一个读不到、第二个读得到 → 用第二个
    assert canvas_for_placements([ghost, p], None) == (960, 540)
    # 坏的 canvas_wh 不算数
    assert canvas_for_placements([p], (0, 0)) == (960, 540)
    assert canvas_for_placements([p], ("x", 1)) == (960, 540)


@pytest.mark.parametrize("canvas,expect", [
    (None, (960, 540)),
    ((720, 1280), (720, 1280)),
    ((1080, 1920), (1080, 1920)),
])
def test_project_from_placements_writes_that_canvas(tmp_path, canvas, expect):
    from cutora.batch.timeline_stitch import project_from_placements
    dest = str(tmp_path / "g" / "_stitched" / "stitched.asproj.json")
    project_from_placements([_placement(SRC_960x540)], dest, text="hi", canvas_wh=canvas)
    data = json.load(open(dest, encoding="utf-8"))
    task = data["tasks"][0]
    assert tuple(task["dims"]) == expect
    tl = task["timeline"]
    assert (int(tl["width"]), int(tl["height"])) == expect


def test_projects_from_cutlist_passes_canvas(tmp_path):
    from cutora.batch.timeline_stitch import projects_from_cutlist
    cuts = {
        "A": [{"path": SRC_960x540, "in_t": 0.0, "out_t": 1.0, "keep": True}],
        "B": [{"path": SRC_960x540, "in_t": 0.0, "out_t": 1.0, "keep": True}],
    }
    paths = projects_from_cutlist(cuts, str(tmp_path), canvas_wh=(720, 1280))
    assert len(paths) == 2
    for p in paths:
        task = json.load(open(p, encoding="utf-8"))["tasks"][0]
        assert tuple(task["dims"]) == (720, 1280), p
    paths = projects_from_cutlist(cuts, str(tmp_path / "noc"))
    for p in paths:
        task = json.load(open(p, encoding="utf-8"))["tasks"][0]
        assert tuple(task["dims"]) == (960, 540), p


def test_stitcher_dialog_reads_host_canvas():
    """UI 侧：对话框把宿主窗口的 _timeline_template 交给 timeline_stitch。"""
    from cutora.batch.script_stitcher import StitcherDialog

    class _Host:
        def __init__(self, tpl):
            self._timeline_template = tpl

    class _Fake:
        def __init__(self, host):
            self._host = host

        def parent(self):
            return self._host

    fn = StitcherDialog._project_canvas_wh
    assert fn(_Fake(_Host({"width": 720, "height": 1280, "fps": 30}))) == (720, 1280)
    assert fn(_Fake(_Host(None))) is None
    assert fn(_Fake(None)) is None
    assert fn(_Fake(_Host({"width": 0, "height": 1280}))) is None
    src = open(os.path.join(REPO, "cutora", "batch", "script_stitcher.py"),
               encoding="utf-8").read()
    # 两条产出路径都必须把 canvas_wh 传下去，少一条就是又一个「只接读端」
    assert src.count("canvas_wh=canvas_wh") >= 2
