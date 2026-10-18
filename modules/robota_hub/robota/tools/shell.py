"""Run a command inside a project root, gated by robota.safety.check_command."""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime

from robota import config, safety

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TAIL = 4000


def _log(rec: dict) -> None:
    try:
        d = config.DATA / "usage"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "commands.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _tail(s: str, n: int = TAIL) -> str:
    return s if len(s) <= n else "…" + s[-n:]


def run_in_project(project: str, command: str, timeout_s: int = 600) -> dict:
    """Run `command` with `powershell -NoProfile -Command` in the project's root.
    Returns {ok, exit, stdout_tail, stderr_tail}."""
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "project": project,
           "command": command, "timeout_s": timeout_s}
    p = config.project(project)
    if not p:
        rec.update(result="unknown_project")
        _log(rec)
        return {"ok": False, "exit": None, "error": f"unknown project '{project}'",
                "known": list(config.projects()), "stdout_tail": "", "stderr_tail": ""}
    allowed, reason = safety.check_command(command)
    if not allowed:
        rec.update(result="blocked", reason=reason)
        _log(rec)
        return {"ok": False, "exit": None, "blocked": True, "error": reason, "stdout_tail": "", "stderr_tail": ""}
    if not p.path.is_dir():
        rec.update(result="no_root")
        _log(rec)
        return {"ok": False, "exit": None, "error": f"project root not found: {p.root}",
                "stdout_tail": "", "stderr_tail": ""}
    timeout_s = max(1, min(int(timeout_s or 600), 6 * 3600))
    # prefix forces UTF-8 output so Chinese text survives the pipe
    ps = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; " + command
    t0 = time.time()
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           cwd=str(p.path), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout_s, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW)
        out = {"ok": r.returncode == 0, "exit": r.returncode,
               "stdout_tail": _tail(r.stdout or ""), "stderr_tail": _tail(r.stderr or "")}
    except subprocess.TimeoutExpired as exc:
        so = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        se = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        out = {"ok": False, "exit": None, "error": f"timeout after {timeout_s}s",
               "stdout_tail": _tail(so), "stderr_tail": _tail(se)}
    out["seconds"] = round(time.time() - t0, 1)
    rec.update(result="ok" if out["ok"] else "failed", exit=out["exit"], seconds=out["seconds"])
    _log(rec)
    return out
