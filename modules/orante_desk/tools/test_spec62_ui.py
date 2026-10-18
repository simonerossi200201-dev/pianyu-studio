# -*- coding: utf-8 -*-
"""规格 62 前端：全局当前任务跟着走 + ③ 镜头序列。不启浏览器。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-62ui-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)

UI = ROOT / "ui"
PANELS = UI / "js" / "panels"
PARTS = UI / "js" / "parts"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_backups_exist():
    for rel in (
        "ui/js/store.js.bak-0913global",
        "ui/js/parts/taskpick.js.bak-0913global",
        "ui/js/panels/track.js.bak-0913global",
        "ui/js/panels/gemini.js.bak-0913global",
        "ui/js/panels/review.js.bak-0913global",
        "ui/js/panels/generate.js.bak-0913global",
        "ui/js/app.js.bak-0913global",
        "ui/index.html.bak-0913global",
        "orante/config.py.bak-0913global",
    ):
        assert (ROOT / rel).is_file(), rel


def test_index_has_capsule():
    html = read(UI / "index.html")
    assert 'variant="capsule"' in html
    assert "capTid" in html
    assert html.index("/ui/js/parts/taskpick.js") < html.index("/ui/js/app.js")


def test_store_persist_and_restore():
    src = read(UI / "js" / "store.js")
    assert "ui.current_task" in src
    assert "persistPanelTid" in src
    assert "if (saved) ui.panelTid = saved" in src


def test_panels_adopt_not_silent_switch():
    gem = read(PANELS / "gemini.js")
    trk = read(PANELS / "track.js")
    rev = read(PANELS / "review.js")
    gen = read(PANELS / "generate.js")
    assert "adoptPanelTid(tid, 'gemini')" in gem
    assert "adoptPanelTid(tid, 'track')" in trk
    assert "adoptPanelTid(tid, 'review')" in rev
    assert "adoptPanelTid(tid, 'generate')" in gen
    for src, node in ((gem, "gemini"), (trk, "track"), (rev, "review"), (gen, "generate")):
        assert f"watch(() => ui.panelTid" in src, node
        assert "gate" in read(PARTS / "taskpick.js")
    # 准入不够时不许改成 readyTasks[0]：adopt 成功就不再默认挑
    assert "if (!adoptPanelTid(tid, 'track') && !tid.value && readyTasks.value.length)" in trk
    assert "if (!adoptPanelTid(tid, 'gemini') && !tid.value && readyTasks.value.length)" in gem
    assert "pickDefaultTid" in rev
    assert "if (cur) { tid.value = cur; return; }" in rev


def test_generate_has_picker_and_current():
    gen = read(PANELS / "generate.js")
    assert "<task-pick" in gen
    assert 'min-status="arranged"' in gen
    assert "current: tsk.id===tid" in gen
    assert "setCurrent" in gen


def test_tasks_writes_panel_tid():
    src = read(PANELS / "tasks.js")
    assert "ui.panelTid = id" in src
    assert "markCurrent" in src


def test_track_shot_sequence():
    trk = read(PANELS / "track.js")
    assert "window.TrackShotLogic" in trk
    assert "track.view.shots" in trk
    assert "preview_bar" in trk
    assert "revertPreview" in trk
    assert "seq-hi" in trk
    assert "seq-mark" in trk
    assert 'stripView===\'shots\'' in trk or 'stripView==="shots"' in trk
    assert "loading=\"lazy\"" in trk
    assert "seq-scroller" in trk
    assert "shotStats.longestWarn" in trk
    assert "track.strip_view" in trk
    assert "focusSeg" in trk


def test_i18n_keys():
    zh = json.loads((ROOT / "orante" / "i18n" / "zh.json").read_text(encoding="utf-8"))
    for k in (
        "taskpick.gate.draft", "taskpick.gate.described", "taskpick.go.tasks",
        "taskpick.go.track", "taskpick.ready.arranged",
        "track.view.pool", "track.view.shots", "track.revert", "track.preview_bar",
        "track.seq.longest", "track.seq.anchor", "track.seq.from_tail",
    ):
        assert k in zh, k
    assert zh["taskpick.gate.draft"] == "{id} 还没拆段"
    assert zh["taskpick.go.tasks"] == "去 ① 任务"
    assert "预览中" in zh["track.preview_bar"]
    assert zh["track.seq.longest"] == "最长连用 {p} ×{k}"


def test_css_capsule_and_seq():
    css = read(UI / "css" / "app.css")
    for sel in (
        ".tp-cap", ".tp-dot", ".tp-gate", ".tp-go-line",
        ".seq-scroller", ".seq-cell", ".seq-mark", ".seq-long.warn",
        ".seq-hi", ".edge-ok", ".g-item.current",
    ):
        assert sel in css, sel


def test_config_defaults():
    src = read(ROOT / "orante" / "config.py")
    assert '"ui.current_task"' in src
    assert '"track.strip_view"' in src


def test_node_logic():
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "test_spec62_ui.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "LOGIC_OK" in (r.stdout or "")


def test_node_syntax():
    files = [
        PARTS / "taskpick.js",
        PANELS / "track.js",
        PANELS / "gemini.js",
        PANELS / "review.js",
        PANELS / "generate.js",
        UI / "js" / "store.js",
        UI / "js" / "app.js",
    ]
    for p in files:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{p.name} {r.stderr or r.stdout}"
