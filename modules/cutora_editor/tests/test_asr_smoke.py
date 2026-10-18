# -*- coding: utf-8 -*-
"""Headless AlignmentWorker smoke (T01c D-1).

golden only covers frozen ASR output. This test runs the live worker so a
broken resource_path / missing import cannot hang the GUI at 20% again.
Structure only — ASR text is non-deterministic.

Runs in a subprocess: pytest collection of script_stitcher imports PySide6
at module level, so an in-process init_torch_runtime() would raise. The
child is the product path (init, then Qt, then AlignmentWorker).
"""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")

# g04 clip 01 is English speech, 4 s (D-1: sample ≤ 5 s from tests/golden/_media/).
SAMPLE = os.path.join(REPO, "tests", "golden", "_media", "g04_stitch", "01.mp4")


def test_alignment_worker_run_emits_segments():
    assert os.path.isfile(SAMPLE), SAMPLE
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["CUTORA_ASR_SMOKE_SAMPLE"] = SAMPLE
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    code = (
        "import os, numbers\n"
        "from cutora.core.runtime import init_torch_runtime\n"
        "init_torch_runtime()\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from cutora.asr.align_worker import AlignmentWorker\n"
        "sample = os.environ['CUTORA_ASR_SMOKE_SAMPLE']\n"
        "app = QApplication.instance() or QApplication([])\n"
        "errors, finished = [], []\n"
        "worker = AlignmentWorker(sample, '', style={}, video_w=1080)\n"
        "worker.error.connect(errors.append)\n"
        "worker.finished.connect(finished.append)\n"
        "worker.run()\n"
        "assert not errors, errors[0] if errors else 'AlignmentWorker.error'\n"
        "assert finished, 'AlignmentWorker.finished was not emitted'\n"
        "segments = finished[0]\n"
        "assert isinstance(segments, list) and len(segments) > 0\n"
        "for seg in segments:\n"
        "    assert isinstance(seg, dict)\n"
        "    assert 'start' in seg and 'end' in seg and 'text' in seg\n"
        "    assert isinstance(seg['start'], numbers.Real) and not isinstance(seg['start'], bool)\n"
        "    assert isinstance(seg['end'], numbers.Real) and not isinstance(seg['end'], bool)\n"
        "    assert isinstance(seg['text'], str)\n"
        "_ = app\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [VENV_PY, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.strip().endswith("ok")
