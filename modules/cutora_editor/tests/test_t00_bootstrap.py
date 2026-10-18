# -*- coding: utf-8 -*-
"""T00 bootstrap sanity checks (no GPU, no render)."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_runtime_layout():
    assert os.path.isfile(os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe"))
    assert os.path.isfile(os.path.join(REPO, "runtime", "ffmpeg", "ffprobe.exe"))
    assert os.path.isdir(os.path.join(REPO, "runtime", "models", "asr_small"))
    assert os.path.isdir(os.path.join(REPO, "runtime", "models", "align_wav2vec2"))


def test_config_prefers_runtime(monkeypatch):
    from cutora.core import config as config
    assert os.path.normcase(config.FFMPEG_PATH).endswith(
        os.path.normcase(os.path.join("runtime", "ffmpeg", "ffmpeg.exe")))
    assert os.path.normcase(config.FFPROBE_PATH).endswith(
        os.path.normcase(os.path.join("runtime", "ffmpeg", "ffprobe.exe")))
    assert os.path.normcase(config.MODELS_DIR).endswith(
        os.path.normcase(os.path.join("runtime", "models")))


def test_tools_exist():
    assert os.path.isfile(os.path.join(REPO, "tools", "golden_check.py"))
    assert os.path.isfile(os.path.join(REPO, "tools", "security_scan.ps1"))
    assert os.path.isfile(os.path.join(REPO, "tools", "report_template.md"))
    assert os.path.isfile(os.path.join(REPO, "CUTORA.bat"))
    assert os.path.isfile(os.path.join(REPO, "CUTORA_Install.bat"))


def test_cutora_bat_no_runtime_pip():
    path = os.path.join(REPO, "CUTORA.bat")
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    assert "pip install" not in text.lower()
    assert "依赖不完整，请运行 CUTORA_Install.bat" in text
    assert "exit /b 2" in text


def test_golden_check_repeat_flag():
    path = os.path.join(REPO, "tools", "golden_check.py")
    text = open(path, "r", encoding="utf-8").read()
    assert '"--repeat"' in text
    assert "FAIL (nondeterministic)" in text
    assert "docs" in text and "nondeterminism" in text
