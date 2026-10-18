# -*- coding: utf-8 -*-
"""规格 54 前端：applyMode 没选不发请求；running 跳过并点名。"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-54ui-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)

GEN = ROOT / "ui" / "js" / "panels" / "generate.js"
TASKS = ROOT / "ui" / "js" / "panels" / "tasks.js"
INDEX = ROOT / "ui" / "index.html"


def test_generate_js_no_global_fallback():
    src = GEN.read_text(encoding="utf-8")
    assert "selected.length ? selected : null" not in src
    assert "store.settings['generate.mode']" not in src
    assert "const genMode = computed" in src
    assert "modeDisabled" in src
    assert "gen.mode.pick" in src
    assert "g-mode-badge" in src
    assert "runApplyMode" in src
    assert "g-mode-hint" in src


def test_tasks_js_has_gen_mode_column():
    src = TASKS.read_text(encoding="utf-8")
    assert "field: 'gen_mode'" in src
    assert "tasks.col.gen_mode" in src
    assert "fmtGenMode" in src
    assert "mismatchWarn" in src


def test_index_loads_logic():
    html = INDEX.read_text(encoding="utf-8")
    assert "gen-mode.js" in html
    assert html.index("gen-mode.js") < html.index("panels/tasks.js")
    assert html.index("gen-mode.js") < html.index("panels/generate.js")


def test_apply_mode_logic_with_node():
    script = ROOT / "tools" / "test_spec54_ui.js"
    r = subprocess.run(
        ["node", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    assert "run empty posted" in (r.stdout or "")
    assert "run live skipped" in (r.stdout or "")
