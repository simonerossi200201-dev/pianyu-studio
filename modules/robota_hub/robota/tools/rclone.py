"""rclone tools using the user's existing `gdrive` remote.

Only `lsjson` and `copy` are ever invoked. No sync / move / delete / purge, no
config commands, and nothing from rclone.conf is ever read, printed or logged.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from robota import config

REMOTE = "gdrive"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "RightClickTableTool", "rclone.exe"),
    r"D:\0_Dev\右键表格工具\app\rclone.exe",
    r"D:\0_Dev\下载图片\Upload\rclone.exe",
    r"C:\Tools\rclone.exe",
]
# scrub anything that looks like an OAuth token from rclone output
_SECRET_RX = re.compile(r"(ya29\.[\w\-\.]+|1//[\w\-]+|\"?(access|refresh)_token\"?\s*[:=]\s*\"?[^\s\",}]+)", re.I)


def find_rclone() -> str | None:
    """Same lookup order as 右键表格工具 (PATH first here, then its candidates)."""
    w = shutil.which("rclone")
    if w:
        return w
    for p in _CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def _scrub(s: str) -> str:
    return _SECRET_RX.sub("<redacted>", s or "")


def _is_remote(p: str) -> bool:
    return (p or "").startswith(REMOTE + ":")


def _bad_arg(p: str) -> str | None:
    if not p or not p.strip():
        return "empty path"
    if p.lstrip().startswith("-"):
        return f"paths may not start with '-': {p!r}"
    if "\n" in p or "\r" in p or "\x00" in p:
        return "invalid characters in path"
    if re.match(r"^[A-Za-z0-9_\-]+:", p) and not re.match(r"^[A-Za-z]:[\\/]", p) and not _is_remote(p):
        return f"only the '{REMOTE}:' remote is allowed: {p!r}"
    return None


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    exe = find_rclone()
    if not exe:
        raise FileNotFoundError("rclone.exe not found (PATH or %LOCALAPPDATA%\\RightClickTableTool)")
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)


def _log(rec: dict) -> None:
    try:
        d = config.DATA / "usage"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "rclone.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def rclone_ls(remote_path: str = "gdrive:", max_items: int = 500) -> dict:
    """List one level of a gdrive: folder. Returns {ok, count, items:[{name,is_dir,size,mod_time}]}."""
    remote_path = (remote_path or f"{REMOTE}:").strip()
    if not _is_remote(remote_path):
        return {"ok": False, "error": f"remote_path must start with '{REMOTE}:'"}
    err = _bad_arg(remote_path)
    if err:
        return {"ok": False, "error": err}
    try:
        r = _run(["lsjson", "--max-depth", "1", "--no-mimetype", remote_path], timeout=180)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "rclone lsjson timed out (180s)"}
    if r.returncode != 0:
        last = (r.stderr or "").strip().splitlines()
        return {"ok": False, "exit": r.returncode, "error": _scrub(last[-1] if last else "rclone failed")}
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        return {"ok": False, "error": "could not parse rclone output"}
    items = [{"name": e.get("Name"), "is_dir": bool(e.get("IsDir")), "size": e.get("Size"),
              "mod_time": e.get("ModTime")} for e in data[:max(1, int(max_items))]]
    return {"ok": True, "path": remote_path, "count": len(data), "truncated": len(data) > len(items),
            "items": items}


def rclone_copy(src: str, dst: str, dry_run: bool = False, timeout_s: int = 3600) -> dict:
    """`rclone copy src dst` (never deletes anything on either side). Exactly one side must be gdrive:."""
    for p in (src, dst):
        err = _bad_arg(p)
        if err:
            return {"ok": False, "error": err}
    if _is_remote(src) == _is_remote(dst):
        return {"ok": False, "error": f"exactly one of src/dst must be a '{REMOTE}:' path"}
    if not _is_remote(src) and not Path(src).exists():
        return {"ok": False, "error": f"local source does not exist: {src}"}
    args = ["copy", src, dst, "--stats-one-line", "--stats", "0", "-v"]
    if dry_run:
        args.append("--dry-run")
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "op": "copy", "src": src, "dst": dst,
           "dry_run": bool(dry_run)}
    try:
        r = _run(args, timeout=max(30, int(timeout_s)))
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        rec["result"] = "timeout"
        _log(rec)
        return {"ok": False, "error": f"rclone copy timed out ({timeout_s}s)"}
    lines = _scrub(r.stderr or "").strip().splitlines()
    rec.update(result="ok" if r.returncode == 0 else "failed", exit=r.returncode)
    _log(rec)
    return {"ok": r.returncode == 0, "exit": r.returncode, "dry_run": bool(dry_run),
            "log_tail": "\n".join(lines[-25:])}
