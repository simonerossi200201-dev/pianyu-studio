# -*- coding: utf-8 -*-
"""Q40: unattended beta smoke must walk the product path and leave evidence.

The child is ``tools/smoke_beta.py``. It boots MainWindow, clicks the 720×1280
primary welcome button, imports g01, injects the frozen ASR fixture, applies a
style pack and nudges every inspector section, splits / adds a transition /
changes track opacity, then export.run + verify_export (Q34 contact sheet).
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
SMOKE_PY = os.path.join(REPO, "tools", "smoke_beta.py")
OUT_DIR = os.path.join(REPO, "docs", "plan", "reports", "Q40_smoke")
RESULT_JSON = os.path.join(OUT_DIR, "result.json")
CONTACT = os.path.join(OUT_DIR, "contact_sheet.png")
SHOTS = (
    "01_welcome.png",
    "02_new_import.png",
    "03_asr.png",
    "04_style.png",
    "05_timeline.png",
    "06_export.png",
)


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _run_smoke() -> subprocess.CompletedProcess:
    assert os.path.isfile(SMOKE_PY), SMOKE_PY
    return subprocess.run(
        [VENV_PY, SMOKE_PY],
        cwd=REPO,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _result() -> dict:
    assert os.path.isfile(RESULT_JSON), RESULT_JSON
    with open(RESULT_JSON, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def smoke_proc():
    return _run_smoke()


def test_q40_smoke_beta_exits_zero(smoke_proc):
    """Full product path. A missing 720×1280 primary, 0 ASR segments, no
    split, no transition, opacity stuck at 1, or a failed verify must make
    this red — not a 'window constructed' check."""
    proc = smoke_proc
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out[-8000:]
    assert "SMOKE_OK" in (proc.stdout or ""), out[-4000:]
    data = _result()
    assert data.get("ok") is True, data
    assert data.get("welcome_primary") == [720, 1280], data.get("welcome_primary")
    assert data.get("canvas_after_new") == [720, 1280], data.get("canvas_after_new")
    assert int(data.get("asr_segments") or 0) > 0, data.get("asr_segments")
    changed = data.get("style_sections_changed") or []
    titles = {row.get("section") for row in changed}
    for need in ("配色方案", "描边", "投影", "字幕排版"):
        assert need in titles, "missing section %s in %s" % (need, titles)
    assert len(changed) >= 8, changed
    assert int(data.get("clips_after_split") or 0) >= 2, data
    assert data.get("transition_preset") == "cross_dissolve", data
    assert abs(float(data.get("track_opacity") or 1.0) - 0.85) < 1e-6, data
    assert data.get("verify_ok") is True, data.get("verify_why")
    for name in SHOTS:
        path = os.path.join(OUT_DIR, name)
        assert os.path.isfile(path), path
        assert os.path.getsize(path) > 400, (name, os.path.getsize(path))


def test_q40_contact_sheet_has_nine_nonblack_cells(smoke_proc):
    """Independent of result.json: the Q34 3×3 sheet next to the smoke export
    must have nine cells, none of them all-black. A 1×1 or black sheet fails."""
    assert smoke_proc.returncode == 0, (smoke_proc.stdout or "")[-2000:]
    assert os.path.isfile(CONTACT), CONTACT
    import cv2
    img = cv2.imread(CONTACT, cv2.IMREAD_COLOR)
    assert img is not None, CONTACT
    h, w = img.shape[:2]
    assert w % 3 == 0 and h % 3 == 0, (w, h)
    cw, ch = w // 3, h // 3
    assert cw >= 8 and ch >= 8, (cw, ch)
    for row in range(3):
        for col in range(3):
            cell = img[row * ch:(row + 1) * ch, col * cw:(col + 1) * cw]
            mean = float(cell.mean())
            assert mean > 8.0, "cell %d,%d is all-black mean=%.3f" % (row, col, mean)
