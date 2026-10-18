"""Wait for a file to appear, or for a regex to show up in its tail."""
from __future__ import annotations

import re
import time
from pathlib import Path

TAIL_BYTES = 64 * 1024


def _tail_lines(p: Path, nbytes: int = TAIL_BYTES) -> list[str]:
    with p.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - nbytes))
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()


def watch_file(path: str, pattern: str | None = None, timeout_s: float = 3600, poll_s: float = 5) -> dict:
    """Block until `path` exists (pattern=None) or a line matching regex `pattern` is in its last 64 KB.
    Returns {ok, matched, line, waited_s} or {ok: False, timed_out: True}."""
    p = Path(path)
    try:
        rx = re.compile(pattern) if pattern else None
    except re.error as exc:
        return {"ok": False, "error": f"bad regex: {exc}"}
    poll_s = max(0.2, float(poll_s))
    t0 = time.time()
    deadline = t0 + max(0.0, float(timeout_s))
    while True:
        try:
            if p.is_file():
                if rx is None:
                    return {"ok": True, "matched": True, "line": None, "path": str(p),
                            "waited_s": round(time.time() - t0, 1)}
                for line in reversed(_tail_lines(p)):
                    if rx.search(line):
                        return {"ok": True, "matched": True, "line": line[-2000:], "path": str(p),
                                "waited_s": round(time.time() - t0, 1)}
        except OSError:
            pass  # file locked / being rotated: try again next poll
        if time.time() >= deadline:
            return {"ok": False, "matched": False, "timed_out": True, "path": str(p),
                    "exists": p.exists(), "waited_s": round(time.time() - t0, 1)}
        time.sleep(min(poll_s, max(0.05, deadline - time.time())))
