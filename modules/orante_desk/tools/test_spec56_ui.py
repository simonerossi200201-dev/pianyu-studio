# -*- coding: utf-8 -*-
"""规格 56 前端：检查台三档 CSS 变量、快捷键、object-fit: contain。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "ui" / "css" / "app.css"
GEN = ROOT / "ui" / "js" / "panels" / "generate.js"
REV = ROOT / "ui" / "js" / "panels" / "review.js"
TRACK = ROOT / "ui" / "js" / "panels" / "track.js"
CFG = ROOT / "orante" / "config.py"


def test_css_vars_and_contain():
    css = CSS.read_text(encoding="utf-8")
    assert "--grid-density" in css
    assert "--bench-thumb" in css
    assert "object-fit:contain" in css.replace(" ", "") or "object-fit: contain" in css
    assert ".g-bench.den-compact" in css or "den-compact" in css
    print("  CSS --grid-density / --bench-thumb / object-fit:contain  OK")


def test_bench_keys_bound():
    src = GEN.read_text(encoding="utf-8")
    assert 'data-bench-keys="- = 0 1 2 3 J K Space S F Enter Esc"' in src
    # 快捷键表里那 10 档都绑上
    keys = {
        "-": "bumpBenchZoom(-10)",
        "=": "bumpBenchZoom(10)",
        "0": "setBenchZoom(100",
        "1": "setBenchDensity('compact')",
        "2": "setBenchDensity('comfy')",
        "3": "setBenchDensity('large')",
        "J": "moveBench(1)",
        "K": "moveBench(-1)",
        "Space": "benchPass(cur)",
        "S": "benchSwap(cur)",
        "F": "setSegMode(cur, 'single')",
        "Enter": "toggleBenchPeek()",
        "Escape": "closeBench()",
    }
    for name, needle in keys.items():
        assert needle in src, name
    print("  检查台快捷键 10 档都绑上  OK")


def test_density_presets_and_config():
    src = GEN.read_text(encoding="utf-8")
    cfg = CFG.read_text(encoding="utf-8")
    assert "ui.bench.density" in cfg
    assert "ui.bench.zoom" in cfg
    assert "gen.bench.density.compact" in src
    assert "gen.bench.density.comfy" in src
    assert "gen.bench.density.large" in src
    assert "setBenchDensity('compact')" in src
    assert "object-fit" in (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    print("  三档密度 + config 键  OK")


def test_seg_mode_entries():
    gen = GEN.read_text(encoding="utf-8")
    rev = REV.read_text(encoding="utf-8")
    track = TRACK.read_text(encoding="utf-8")
    assert "/seg-mode" in gen
    assert "/seg-mode" in rev
    assert "/seg-mode" in track
    assert "gen.bench.seg.to_single" in gen
    assert "review.seg.to_single" in rev
    assert "track.seg.single" in track
    print("  段级入口 ③④⑤ 都在  OK")
