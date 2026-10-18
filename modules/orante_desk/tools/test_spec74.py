# -*- coding: utf-8 -*-
"""规格 74：下拉点选 / 滚轮 / 拖拽。逻辑走 node test_spec74_ui.js。"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-74-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)

UI = ROOT / "ui"
PARTS = UI / "js" / "parts"
PANELS = UI / "js" / "panels"


def test_backups_exist():
    for rel in (
        "ui/js/parts/taskpick.js.bak-0920pick",
        "ui/js/parts/gen-mode.js.bak-0920pick",
        "ui/js/panels/track.js.bak-0920pick",
        "ui/js/panels/review.js.bak-0920pick",
        "ui/js/panels/generate.js.bak-0920pick",
        "ui/css/app.css.bak-0920pick",
        "ui/index.html.bak-0920pick",
    ):
        assert (ROOT / rel).is_file(), rel


def test_pickgest_file():
    p = PARTS / "pickgest.js"
    assert p.is_file()
    src = p.read_text(encoding="utf-8")
    assert "function usePickerGestures" in src
    assert "THROTTLE_MS = 70" in src
    assert "COMMIT_MS = 250" in src
    assert "WHEEL_UNIT = 40" in src
    assert "STEP_PX = 28" in src
    assert "CLICK_PX = 6" in src
    assert "setPointerCapture" in src


def test_index_loads_pickgest_first():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "/ui/js/parts/pickgest.js" in html
    assert html.index("/ui/js/parts/pickgest.js") < html.index("/ui/js/parts/taskpick.js")
    assert html.index("/ui/js/parts/pickgest.js") < html.index("/ui/js/parts/gen-mode.js")


def test_five_pickers_wired():
    files = [
        PARTS / "taskpick.js",
        PANELS / "track.js",
        PARTS / "gen-mode.js",
        PANELS / "review.js",
        PANELS / "generate.js",
    ]
    for p in files:
        src = p.read_text(encoding="utf-8")
        assert "usePickerGestures" in src, p.name


def test_node_syntax():
    files = [
        PARTS / "pickgest.js",
        PARTS / "taskpick.js",
        PARTS / "gen-mode.js",
        PANELS / "track.js",
        PANELS / "review.js",
        PANELS / "generate.js",
    ]
    for p in files:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name} {r.stderr or r.stdout}"


def test_node_logic():
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "test_spec74_ui.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "LOGIC_OK" in (r.stdout or "")
