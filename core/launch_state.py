# -*- coding: utf-8 -*-
"""片语工作室跨进程启动状态与生命周期信标 (core/launch_state.py)。

解决总控台 (studio_launcher.py / core.table_sync) 与子进程 (CUTORA / Orante) 之间
「Popen 立即返回 True 误报已启动、缺少依赖或锁冲突退出时父进程不知情」的问题。

生命周期阶段 (Stage):
- BOOTSTRAP_START
- ENV_CHECK_START
- ENV_CHECK_OK
- ENV_CHECK_FAILED
- LOCK_ACQUIRED
- EXISTING_INSTANCE_FOCUSED
- LOCK_FAILED
- QT_READY
- MAIN_WINDOW_READY
- APP_EXIT_REQUESTED
- PROCESS_EXIT
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LTX_ROOT = Path(__file__).resolve().parent.parent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def launch_state_file(app_name: str = "cutora", root: Path | None = None) -> Path:
    base = Path(root).resolve() if root else LTX_ROOT
    log_dir = base / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{app_name}_launch_state.json"
    except OSError:
        return Path(tempfile.gettempdir()) / f"pianyu_{app_name}_launch_state.json"


def write_launch_state(
    stage: str,
    *,
    app_name: str = "cutora",
    session_id: str = "",
    pid: int | None = None,
    returncode: int | None = None,
    error: str = "",
    detail: dict[str, Any] | None = None,
    root: Path | None = None,
) -> Path:
    """原子写入当前子进程的启动阶段状态，供父进程监控和黑匣子诊断读取。"""
    target = launch_state_file(app_name=app_name, root=root)
    prev: dict[str, Any] = {}
    if target.is_file():
        try:
            prev = json.loads(target.read_text(encoding="utf-8", errors="ignore")) or {}
        except Exception:
            prev = {}

    eff_session = (
        session_id
        or os.environ.get("CUTORA_LAUNCH_SESSION_ID")
        or prev.get("session_id")
        or f"sess-{int(time.time() * 1000)}-{os.getpid()}"
    )
    history = list(prev.get("history") or [])
    if prev.get("session_id") != eff_session:
        history = []

    now_iso = _utc_now_iso()
    history.append({
        "stage": stage,
        "ts": now_iso,
        "pid": pid if pid is not None else os.getpid(),
    })

    payload: dict[str, Any] = {
        "app": app_name,
        "session_id": eff_session,
        "stage": stage,
        "pid": pid if pid is not None else os.getpid(),
        "updated_at": now_iso,
        "updated_epoch": time.time(),
        "main_window_ready": bool(
            stage == "MAIN_WINDOW_READY"
            or (prev.get("session_id") == eff_session and prev.get("main_window_ready"))
        ),
        "returncode": returncode,
        "error": error or (prev.get("error", "") if prev.get("session_id") == eff_session else ""),
        "detail": detail or {},
        "history": history[-20:],
    }
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return target


def read_launch_state(app_name: str = "cutora", root: Path | None = None) -> dict[str, Any]:
    target = launch_state_file(app_name=app_name, root=root)
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8", errors="ignore"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
