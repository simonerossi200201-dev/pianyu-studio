"""Robota launcher: start the console in the background (if not already running) and open the browser."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from robota import config  # noqa: E402

PORT = int(config.settings()["console"]["port"])
URL = f"http://127.0.0.1:{PORT}/"


def alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/health", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    if not alive():
        pyw = Path(config.PYTHON).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else config.PYTHON)
        log = open(config.LOGS / "console.log", "a", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env["PATH"] = str(Path(config.AGY).parent) + os.pathsep + env.get("PATH", "")
        base = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        cmd = [exe, "-m", "robota.console.server"]
        try:
            subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             creationflags=base | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             creationflags=base)
        for _ in range(60):
            time.sleep(0.5)
            if alive():
                break
        else:
            print("Robota 控制台启动失败，看 logs\\console.log")
            return 1
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
