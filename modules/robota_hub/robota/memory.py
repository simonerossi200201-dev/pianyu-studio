"""Long-term memory: markdown notes per topic under data/memory/<topic>.md (e.g. sop, habits, tutor)."""
from __future__ import annotations

import re
import threading
from datetime import datetime

from robota import config

MEM_DIR = config.DATA / "memory"
_LOCK = threading.Lock()
_MAX_READ = 200_000


def _topic(topic: str) -> str:
    """Sanitize: letters/digits/CJK/_/- only, no path separators, max 64 chars."""
    t = (topic or "").strip().lower()
    t = re.sub(r"\.md$", "", t)
    t = re.sub(r"[^\w\-]+", "_", t, flags=re.UNICODE).strip("_.")
    if not t:
        raise ValueError("empty topic name")
    return t[:64]


def memory_topics() -> list[str]:
    """Existing memory topics."""
    if not MEM_DIR.is_dir():
        return []
    return sorted(p.stem for p in MEM_DIR.glob("*.md"))


def memory_read(topic: str) -> dict:
    """Read a topic's markdown (empty text if it doesn't exist yet)."""
    try:
        t = _topic(topic)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    p = MEM_DIR / f"{t}.md"
    if not p.is_file():
        return {"ok": True, "topic": t, "exists": False, "text": "", "topics": memory_topics()}
    text = p.read_text(encoding="utf-8", errors="replace")
    trunc = len(text) > _MAX_READ
    return {"ok": True, "topic": t, "exists": True, "truncated": trunc,
            "text": text[-_MAX_READ:] if trunc else text}


def memory_append(topic: str, text: str) -> dict:
    """Append a timestamped entry to a topic (creates the file)."""
    try:
        t = _topic(topic)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    p = MEM_DIR / f"{t}.md"
    with _LOCK:
        new = not p.exists()
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            if new:
                fh.write(f"# {t}\n")
            fh.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{text}\n")
    return {"ok": True, "topic": t, "path": str(p), "bytes": p.stat().st_size}


def memory_compact(topic: str, max_bytes: int = 250_000) -> None:
    """Trim a topic markdown file if it exceeds max_bytes, keeping header and latest notes."""
    try:
        t = _topic(topic)
    except ValueError:
        return
    p = MEM_DIR / f"{t}.md"
    if not p.is_file() or p.stat().st_size <= max_bytes:
        return
    with _LOCK:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        header = lines[:5]
        tail = lines[-1200:]
        p.write_text("\n".join(header + ["\n<!-- [Auto-compacted older entries] -->\n"] + tail) + "\n",
                     encoding="utf-8")


def housekeeping() -> dict:
    """Prune temporary voice files, rotate logs, and check storage bounds to keep disk footprint tiny."""
    import tempfile
    import time
    from pathlib import Path
    
    freed_bytes = 0
    # 1. Clean temp voice recordings older than 2 hours
    tmp_voice = Path(tempfile.gettempdir()) / "robota_voice"
    if tmp_voice.is_dir():
        now = time.time()
        for f in tmp_voice.glob("*.*"):
            try:
                if now - f.stat().st_mtime > 7200:
                    sz = f.stat().st_size
                    f.unlink()
                    freed_bytes += sz
            except Exception:
                continue

    # 2. Trim logs > 20 MB
    logs_dir = config.LOGS
    if logs_dir.is_dir():
        for f in logs_dir.glob("*.log"):
            try:
                if f.stat().st_size > 20 * 1024 * 1024:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    f.write_text(content[-5 * 1024 * 1024:], encoding="utf-8")
            except Exception:
                continue

    # 3. Compact all memory files
    if MEM_DIR.is_dir():
        for f in MEM_DIR.glob("*.md"):
            memory_compact(f.stem)

    # 4. Total data directory size
    total_sz = sum(f.stat().st_size for f in config.DATA.rglob("*") if f.is_file())
    return {"ok": True, "data_dir_mb": round(total_sz / (1024 * 1024), 2), "freed_mb": round(freed_bytes / (1024 * 1024), 2)}

