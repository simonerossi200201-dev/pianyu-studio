# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Seek / clock trace. Enabled only when CUTORA_PERF=1.

Writes one JSON object per event to USER_DATA/logs/seek_trace.jsonl.
Default path is a no-op besides preview_perf.enabled() (cached bool).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from cutora.core.log import get_logger
from cutora.core.paths import ensure_user_layout, user_logs_dir
from cutora.core.preview_perf import enabled

logger = get_logger("cutora.core.seek_trace")

JSONL_NAME = "seek_trace.jsonl"

_lock = threading.Lock()
_path: str | None = None
_logged_path = False


def reset_for_tests() -> None:
    """Drop cached path. Tests only."""
    global _path, _logged_path
    _path = None
    _logged_path = False


def jsonl_path() -> str:
    global _path
    if _path is None:
        ensure_user_layout()
        _path = os.path.join(user_logs_dir(), JSONL_NAME)
    return _path


def emit(kind: str, **fields: Any) -> None:
    """Append one event. No-op when CUTORA_PERF is not 1."""
    if not enabled():
        return
    rec = {
        "kind": kind,
        "t": time.time(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False)
    path = jsonl_path()
    folder = os.path.dirname(path)
    global _logged_path
    with _lock:
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        if not _logged_path:
            logger.info("CUTORA_PERF=1 seek_trace=%s", path)
            _logged_path = True
