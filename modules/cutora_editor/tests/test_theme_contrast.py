# -*- coding: utf-8 -*-
"""T03c §8.3: every bundled palette must pass WCAG gates; a dimmed fg1 must fail."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_check_theme_contrast_script_pass():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tools", "check_theme_contrast.py")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "PASS" in (proc.stdout or "")


def test_rose_fg1_darkened_fails():
    from cutora.ui.theme.contrast import check_palette
    from cutora.ui.theme.tokens import darken_hex, load_palette_data

    data = dict(load_palette_data("rose"))
    assert check_palette(data, "rose") == []
    data["fg1"] = darken_hex(data["fg1"], 0.55)
    fails = check_palette(data, "rose")
    assert fails, "dimming rose fg1 must fail the 4.5:1 gate"
    assert any("fg1 on bg1" in line for line in fails)


def test_contrast_all_surfaces():
    """fg0/fg1/fg2 × bg1/bg2/bg3 and ok/warn/err × bg1/bg2, all six palettes."""
    from cutora.ui.theme.contrast import CHECKS, check_palette
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, load_palette_data

    labels = [row[0] for row in CHECKS]
    for fg in ("fg0", "fg1", "fg2"):
        for bg in ("bg1", "bg2", "bg3"):
            assert ("%s on %s" % (fg, bg)) in labels, (fg, bg, labels)
    for fg in ("ok", "warn", "err"):
        for bg in ("bg1", "bg2"):
            assert ("%s on %s" % (fg, bg)) in labels, (fg, bg, labels)
    for bg in ("bg1", "bg2"):
        assert ("err_text on %s" % bg) in labels, (bg, labels)
    all_fails = []
    for tid in BUILTIN_THEME_IDS:
        data = load_palette_data(tid)
        all_fails.extend(check_palette(data, tid))
    assert all_fails == [], all_fails


def test_roma_gold_json_matches_frozen_constants():
    from cutora.ui.theme.tokens import (
        ACCENT, BG0, BG1, FG0, INFO, ROMA, TRACK, load_palette_data,
    )
    data = load_palette_data("roma-gold")
    assert data["bg0"] == BG0 == "#141210"
    assert data["fg0"] == FG0
    assert data["accent"] == ACCENT
    assert data["info"] == INFO
    assert data["bg1"] == BG1
    assert data["track_video"] == TRACK["video"]
    assert ROMA["accent"] == ACCENT
