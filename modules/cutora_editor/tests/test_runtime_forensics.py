# -*- coding: utf-8 -*-
"""Crash forensics: faulthandler log + STARTUP identity line on GUI entry."""
from __future__ import annotations

import os
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")

_STARTUP_KEYS = (
    "git=",
    "branch=",
    "torch=",
    "ctranslate2=",
    "transformers=",
    "cuda_warmup=",
    "device=",
    "msvcp140=",
)


def _env(user_data: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["CUTORA_USER_DATA"] = user_data
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _startup_line(text: str) -> str:
    for ln in text.splitlines():
        if "STARTUP git=" in ln:
            return ln
    return ""


def test_main_enables_faulthandler_before_pyside6():
    app_py = os.path.join(REPO, "cutora", "app.py")
    with open(app_py, encoding="utf-8") as f:
        src = f.read()
    main_at = src.find("def main(")
    body = src[main_at:]
    fh_at = body.find("_enable_faulthandler()")
    banner_at = body.find("_log_startup_identity()")
    pyqt_at = body.find("from PySide6.QtCore")
    assert fh_at != -1
    assert banner_at != -1
    assert pyqt_at != -1
    assert fh_at < banner_at < pyqt_at
    assert "faulthandler.enable" in src
    assert "all_threads=True" in src
    assert "faulthandler.log" in src
    assert 'open(path, "a"' in src
    assert "prepare_faulthandler_log" in src


def test_python_m_cutora_self_test_writes_forensics():
    """Product entry ``python -m cutora --self-test`` must emit STARTUP + log file."""
    with tempfile.TemporaryDirectory(prefix="cutora_forensics_") as tmp:
        proc = subprocess.run(
            [VENV_PY, "-m", "cutora", "--self-test"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=180,
            env=_env(tmp),
        )
        combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
        assert proc.returncode == 0, combined
        log_path = os.path.join(tmp, "logs", "faulthandler.log")
        assert os.path.isfile(log_path), log_path + "\n" + combined
        line = _startup_line(combined)
        assert line, "STARTUP line missing:\n" + combined
        for key in _STARTUP_KEYS:
            assert key in line, key + " missing in: " + line

        def _field(name: str) -> str:
            return line.split(name, 1)[1].split()[0]

        assert _field("git=") != "unknown"
        assert _field("branch=") != "unknown"
        assert _field("torch=") != "unknown"
        assert _field("ctranslate2=") != "unknown"
        assert _field("transformers=") != "unknown"
        # Q187：主界面不再在进程内建 CUDA 上下文（skipped）；显卡名来自 nvidia-smi
        assert _field("cuda_warmup=") in ("ok", "skipped")
        assert _field("device=") not in ("-", "cpu")
        msvc = _field("msvcp140=")
        assert "system32" in msvc.lower()
        assert "pyside6" not in msvc.lower()
        with open(log_path, encoding="utf-8") as fh:
            fh_text = fh.read()
        assert fh_text.startswith("===== "), fh_text[:80]


def test_prepare_faulthandler_log_appends_and_keeps_last_five(tmp_path):
    from datetime import datetime

    from cutora.app import prepare_faulthandler_log, split_faulthandler_sessions

    path = str(tmp_path / "faulthandler.log")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("legacy crash before separators existed\n")
    for i in range(6):
        prepare_faulthandler_log(
            path, keep=5, now=datetime(2026, 9, 9, 10, i, 0))
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("stack-%d\n" % i)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    sessions = split_faulthandler_sessions(text)
    assert len(sessions) == 5, sessions
    assert "legacy crash" not in text
    assert "stack-0" not in text
    assert "stack-1" in text
    assert "===== 2026-09-09 10:02:00 =====" in text
    assert "stack-5" in text
    assert sessions[-1].startswith("===== 2026-09-09 10:05:00 =====")
