# -*- coding: utf-8 -*-
"""MSVCP140 must come from System32, not PySide6's bundled copy."""
from __future__ import annotations

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _run(args=None, code=None, timeout=180):
    cmd = [VENV_PY]
    if code is not None:
        cmd.extend(["-c", code])
    else:
        cmd.extend(args or [])
    return subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_env(),
    )


def test_check_runtime_msvcp140_is_system32():
    proc = _run(args=["-m", "cutora", "--check-runtime"])
    assert proc.returncode == 0, proc.stderr + proc.stdout
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, "stdout empty:\n" + proc.stderr
    path = lines[-1]
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    assert os.path.normcase(path).startswith(os.path.normcase(system32)), path
    assert "pyside6" not in os.path.normcase(path)


def test_init_after_pyside6_raises_runtime_error():
    code = (
        "import PySide6.QtCore\n"
        "from cutora.core.runtime import init_torch_runtime\n"
        "try:\n"
        "    init_torch_runtime()\n"
        "except RuntimeError as e:\n"
        "    assert 'MSVCP140' in str(e), e\n"
        "    print('raised')\n"
        "else:\n"
        "    raise SystemExit('expected RuntimeError')\n"
    )
    proc = _run(code=code, timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "raised" in proc.stdout


def test_mcp_stdio_entry_does_not_import_torch_or_pyside6():
    code = (
        "import sys\n"
        "from cutora.api import mcp_server\n"
        "bad = [m for m in sys.modules if m == 'torch' or m.startswith('PySide6')]\n"
        "assert not bad, bad\n"
        "print('clean')\n"
    )
    proc = _run(code=code, timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "clean" in proc.stdout


def test_main_calls_init_before_importing_app():
    main_py = os.path.join(REPO, "cutora", "__main__.py")
    with open(main_py, encoding="utf-8") as f:
        src = f.read()
    guard = "if __name__ == \"__main__\":"
    body = src.split(guard, 1)[1]
    init_at = body.find("init_torch_runtime(render_child=False)")
    app_at = body.find("from cutora.app import main")
    assert init_at != -1
    assert app_at != -1
    assert init_at < app_at
    assert "--check-runtime" in src
    assert "run_render_job_entry" in body
    render_at = body.find("run_render_job_entry")
    assert render_at != -1
    assert render_at < init_at
    assert "is_commands_argv" in src
    assert body.find("dispatch_late") > init_at
