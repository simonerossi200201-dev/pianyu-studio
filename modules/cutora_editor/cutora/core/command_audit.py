# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Append-only command audit log at ``user_logs_dir()/commands.jsonl``."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from cutora.core.log import get_logger, redact_text
from cutora.core.paths import user_logs_dir
from cutora.core.secrets import strip_secret_fields

logger = get_logger("cutora.core.command_audit")
TZ8 = timezone(timedelta(hours=8))
_HOME = os.path.expanduser("~")


def _now_iso() -> str:
    return datetime.now(TZ8).isoformat(timespec="milliseconds")


def redact_paths(obj: Any) -> Any:
    """Optionally mask the user home directory in logged paths."""
    if isinstance(obj, str):
        if _HOME and obj.startswith(_HOME):
            return "~" + obj[len(_HOME):]
        return obj
    if isinstance(obj, dict):
        return {k: redact_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_paths(x) for x in obj]
    return obj


def sanitize_params(params: Any) -> Any:
    """Drop secret fields, redact credential-like text, mask home paths."""
    cleaned = strip_secret_fields(params if params is not None else {})
    if isinstance(cleaned, dict):
        out = {}
        for key, val in cleaned.items():
            if isinstance(val, str):
                out[key] = redact_text(redact_paths(val))
            else:
                out[key] = redact_paths(val)
        return out
    return redact_paths(cleaned)


def audit_path() -> str:
    folder = user_logs_dir()
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "commands.jsonl")


def append_audit(
    *,
    source: str,
    command: str,
    params: Any,
    ms: float,
    ok: bool,
    error: str | None = None,
    result_keys: list | None = None,
) -> None:
    """Append one JSONL record. Never raises into the command bus."""
    record = {
        "ts": _now_iso(),
        "source": source,
        "command": command,
        "params": sanitize_params(params),
        "ms": round(float(ms), 3),
        "ok": bool(ok),
        "error": redact_text(error) if error else None,
        "result_keys": list(result_keys or []),
    }
    line = json.dumps(record, ensure_ascii=False)
    try:
        path = audit_path()
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        logger.warning("写入命令审计日志失败: %s", exc)
