# -*- coding: utf-8 -*-
"""片语工作室跨进程启动状态与全栈三软件黑匣子信标 (core/launch_state.py)。

统一覆盖整个工作室四大组件：
1. `launcher`   (片语工作室总控台)
2. `orante`     (Orante 2.0 批量制片台)
3. `cutora`     (片语剪辑台 CUTORA)
4. `table_tool` (右键表格工具)

生命周期阶段 (Stage):
- BOOTSTRAP_START
- ENV_CHECK_START
- ENV_CHECK_OK
- ENV_CHECK_FAILED
- LOCK_ACQUIRED
- EXISTING_INSTANCE_FOCUSED
- LOCK_FAILED
- SERVER_READY
- QT_READY
- MAIN_WINDOW_READY
- BROWSER_FALLBACK_READY
- STARTUP_FAILED
- APP_EXIT_REQUESTED
- PROCESS_EXIT
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LTX_ROOT = Path(__file__).resolve().parent.parent

_PRIVACY_USER_RE = re.compile(r"([A-Za-z]:\\Users\\|/Users/)([^\\/]+)", re.IGNORECASE)
_SECRET_TOKEN_RE = re.compile(
    r"(?:Bearer\s+\S+|(?:xai-|sk-|sk-ant-|AIza|ya29\.|1//|gsk_|hf_|ghp_)[A-Za-z0-9_\-\.]{8,}|"
    r"(?:__Secure-1PSID|__Secure-3PSID|SID|SAPISID|orante_session|X-Rct-Token)\s*[=:]\s*[^\s;,\"']+)",
    re.IGNORECASE,
)


def scrub_privacy_text(text: str) -> str:
    """脱敏路径中的操作系统用户名及任何潜在凭据/会话 Token。"""
    if not text:
        return ""
    out = _SECRET_TOKEN_RE.sub("***", str(text))
    out = _PRIVACY_USER_RE.sub(r"\1<USER>", out)
    return out


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


def studio_breadcrumbs_file(root: Path | None = None) -> Path:
    base = Path(root).resolve() if root else LTX_ROOT
    log_dir = base / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "studio_flight_breadcrumbs.jsonl"
    except OSError:
        return Path(tempfile.gettempdir()) / "pianyu_studio_flight_breadcrumbs.jsonl"


def record_studio_breadcrumb(
    app_name: str,
    stage: str,
    *,
    session_id: str = "",
    pid: int | None = None,
    returncode: int | None = None,
    error: str = "",
    detail: dict[str, Any] | None = None,
    root: Path | None = None,
) -> None:
    """向工作室统一黑匣子轨迹流 (data/logs/studio_flight_breadcrumbs.jsonl) 追加一条跨软件记录。"""
    bc_path = studio_breadcrumbs_file(root=root)
    safe_detail = {}
    if isinstance(detail, dict):
        for k, v in detail.items():
            safe_detail[str(k)] = scrub_privacy_text(str(v))[:240]
    entry = {
        "ts": _utc_now_iso(),
        "app": app_name,
        "session_id": session_id,
        "stage": stage,
        "pid": pid if pid is not None else os.getpid(),
        "returncode": returncode,
        "error": scrub_privacy_text(error)[:300] if error else "",
        "detail": safe_detail,
    }
    try:
        with open(bc_path, "ab") as fh:
            fh.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
    except OSError:
        pass


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
    """原子写入当前子进程的启动阶段状态，并同步记入全工作室统一黑匣子轨迹流。"""
    target = launch_state_file(app_name=app_name, root=root)
    prev: dict[str, Any] = {}
    if target.is_file():
        try:
            prev = json.loads(target.read_text(encoding="utf-8", errors="ignore")) or {}
        except Exception:
            prev = {}

    env_sess_key = f"{app_name.upper()}_LAUNCH_SESSION_ID"
    eff_session = (
        session_id
        or os.environ.get(env_sess_key)
        or (os.environ.get("CUTORA_LAUNCH_SESSION_ID") if app_name == "cutora" else "")
        or prev.get("session_id")
        or f"{app_name}-{int(time.time() * 1000)}-{os.getpid()}"
    )
    history = list(prev.get("history") or [])
    if prev.get("session_id") != eff_session:
        history = []

    now_iso = _utc_now_iso()
    eff_pid = pid if pid is not None else os.getpid()
    history.append({
        "stage": stage,
        "ts": now_iso,
        "pid": eff_pid,
    })

    is_ready = bool(
        stage in ("MAIN_WINDOW_READY", "BROWSER_FALLBACK_READY")
        or (prev.get("session_id") == eff_session and prev.get("main_window_ready"))
    )
    payload: dict[str, Any] = {
        "app": app_name,
        "session_id": eff_session,
        "stage": stage,
        "pid": eff_pid,
        "updated_at": now_iso,
        "updated_epoch": time.time(),
        "main_window_ready": is_ready,
        "returncode": returncode,
        "error": error or (prev.get("error", "") if prev.get("session_id") == eff_session else ""),
        "detail": detail or {},
        "history": history[-25:],
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

    record_studio_breadcrumb(
        app_name=app_name,
        stage=stage,
        session_id=eff_session,
        pid=eff_pid,
        returncode=returncode,
        error=error,
        detail=detail,
        root=root,
    )
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


def _tail_redacted_lines(path: Path, max_lines: int = 20) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return [scrub_privacy_text(ln.strip())[:260] for ln in lines if ln.strip()][-max_lines:]
    except Exception:
        return []


def inspect_table_tool_registration(root: Path | None = None) -> dict[str, Any]:
    """检查右键表格工具在当前操作系统中的注册健康状态（含是否存在重复/失效旧键）。"""
    base = Path(root).resolve() if root else LTX_ROOT
    script_path = base / "modules" / "table_tool" / "app" / "表格工具.py"
    info: dict[str, Any] = {
        "platform": sys.platform,
        "script_exists": script_path.is_file(),
        "context_menu_ok": False,
        "legacy_duplicate_present": False,
        "registered_command": "",
        "icon_path": "",
    }
    if sys.platform == "darwin":
        wf = Path.home() / "Library" / "Services" / "用表格工具打开.workflow"
        app_b = Path.home() / "Applications" / "右键表格工具.app"
        info["context_menu_ok"] = wf.is_dir() or app_b.is_dir()
        info["registered_command"] = scrub_privacy_text(str(wf if wf.is_dir() else app_b))
        info["canonical_registered"] = bool(info["context_menu_ok"])
        info["legacy_recycle_bin_found"] = bool(info["legacy_duplicate_present"])
        info["target_python_exists"] = bool(info["script_exists"])
        return info
    if os.name != "nt":
        info["canonical_registered"] = bool(info["context_menu_ok"])
        info["legacy_recycle_bin_found"] = bool(info["legacy_duplicate_present"])
        info["target_python_exists"] = bool(info["script_exists"])
        return info
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Directory\Background\shell\LTXStudioTableTool",
        ) as k:
            ico, _ = winreg.QueryValueEx(k, "Icon")
            info["icon_path"] = scrub_privacy_text(str(ico))
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Directory\Background\shell\LTXStudioTableTool\command",
        ) as ck:
            cmd, _ = winreg.QueryValueEx(ck, "")
            info["registered_command"] = scrub_privacy_text(str(cmd))
            info["context_menu_ok"] = "表格工具.py" in str(cmd)
        # 检查是否还残留带回收站图标的 RightClickTableTool
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Classes\Directory\Background\shell\RightClickTableTool",
            ):
                info["legacy_duplicate_present"] = True
        except OSError:
            info["legacy_duplicate_present"] = False
    except Exception:
        pass
    info["canonical_registered"] = bool(info["context_menu_ok"])
    info["legacy_recycle_bin_found"] = bool(info["legacy_duplicate_present"])
    info["target_python_exists"] = bool(info["script_exists"])
    return info


def inspect_orante_health(root: Path | None = None) -> dict[str, Any]:
    """读取 Orante 2.0 制片台的启动信标、脱敏数据库摘要与最近错误日志（零用户隐私/文案/凭据泄露）。"""
    base = Path(root).resolve() if root else LTX_ROOT
    orante_root = base / "modules" / "orante_desk"
    orante_db = orante_root / "data" / "orante.db"
    orante_log = orante_root / "data" / "logs" / "orante.log"
    stdout_log = base / "data" / "logs" / "orante_stdout.log"
    stderr_log = base / "data" / "logs" / "orante_stderr.log"

    summary: dict[str, Any] = {
        "installed": (orante_root / "orante" / "main.py").is_file(),
        "launch_state": read_launch_state("orante", root=base),
        "db_exists": orante_db.is_file(),
        "tasks_total": 0,
        "jobs_running": 0,
        "jobs_failed": 0,
        "recent_failed_jobs": [],
        "log_tail": _tail_redacted_lines(orante_log, 15) or _tail_redacted_lines(stdout_log, 15),
        "stderr_tail": _tail_redacted_lines(stderr_log, 10),
    }
    if orante_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{orante_db}?mode=ro", uri=True, timeout=1.5)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
                summary["tasks_total"] = int(row["c"]) if row else 0
            except Exception:
                pass
            try:
                r_run = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='running'").fetchone()
                r_fail = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='failed'").fetchone()
                summary["jobs_running"] = int(r_run["c"]) if r_run else 0
                summary["jobs_failed"] = int(r_fail["c"]) if r_fail else 0
                rows = conn.execute(
                    "SELECT id, kind, task_id, status, error, finished_at FROM jobs WHERE status='failed' ORDER BY id DESC LIMIT 5"
                ).fetchall()
                summary["recent_failed_jobs"] = [
                    {
                        "id": r["id"],
                        "kind": r["kind"],
                        "task_id": r["task_id"],
                        "error": scrub_privacy_text(str(r["error"] or ""))[:200],
                        "finished_at": r["finished_at"],
                    }
                    for r in rows
                ]
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
    return summary


def collect_studio_apps_snapshot(root: Path | None = None) -> dict[str, Any]:
    """汇总整个片语工作室（总控台 + Orante 2.0 + CUTORA + 右键表格工具）的全栈运行与健康状态快照。"""
    base = Path(root).resolve() if root else LTX_ROOT
    log_dir = base / "data" / "logs"

    # 读取最近 40 条跨软件轨迹
    breadcrumbs: list[dict[str, Any]] = []
    bc_file = studio_breadcrumbs_file(root=base)
    if bc_file.is_file():
        try:
            for ln in bc_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]:
                ln = ln.strip()
                if ln:
                    breadcrumbs.append(json.loads(ln))
        except Exception:
            pass

    apps_dict = {
        "launcher": {
            "label": "片语工作室总控台",
            "launch_state": read_launch_state("launcher", root=base),
            "error_log_tail": _tail_redacted_lines(base / "logs" / "launcher_error.log", 10),
        },
        "orante": {
            "label": "Orante 2.0 制片台",
            **inspect_orante_health(root=base),
        },
        "cutora": {
            "label": "片语剪辑台 CUTORA",
            "launch_state": read_launch_state("cutora", root=base),
            "stdout_tail": _tail_redacted_lines(log_dir / "cutora_stdout.log", 15),
            "stderr_tail": _tail_redacted_lines(log_dir / "cutora_stderr.log", 10),
        },
        "table_tool": {
            "label": "右键表格工具",
            "launch_state": read_launch_state("table_tool", root=base),
            "registration": inspect_table_tool_registration(root=base),
            "registry_check": inspect_table_tool_registration(root=base),
            "log_tail": _tail_redacted_lines(log_dir / "table_tool.log", 15),
            "stdout_tail": _tail_redacted_lines(log_dir / "table_tool_stdout.log", 10),
            "stderr_tail": _tail_redacted_lines(log_dir / "table_tool_stderr.log", 10),
        },
    }
    return {
        "captured_at": _utc_now_iso(),
        "apps": apps_dict,
        **apps_dict,
        "recent_studio_breadcrumbs": breadcrumbs,
    }
