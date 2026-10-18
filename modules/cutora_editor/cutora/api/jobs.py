# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job files under USER_DATA/jobs — same directory the CLI --status reads."""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from cutora.core.paths import resolve_user_data

_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def jobs_dir() -> str:
    path = os.path.join(resolve_user_data(), "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def job_path(job_id: str) -> str:
    jid = str(job_id or "")
    if not _JOB_ID.match(jid):
        raise ValueError("bad job id")
    root = os.path.abspath(jobs_dir())
    path = os.path.abspath(os.path.join(root, jid + ".json"))
    if os.path.dirname(path) != root:
        raise ValueError("bad job id")
    return path


def write_job(info: dict) -> dict:
    job_id = str(info.get("job_id") or new_job_id())
    info = dict(info)
    info["job_id"] = job_id
    path = job_path(job_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return info


def read_job(job_id: str) -> dict | None:
    try:
        path = job_path(job_id)
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def create_job(command: str, params: dict | None = None) -> dict:
    job_id = new_job_id()
    return write_job({
        "job_id": job_id,
        "op": command,
        "command": command,
        "status": "starting",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": dict(params or {}),
        "result": None,
        "error": None,
    })


def update_job(job_id: str, **fields) -> dict | None:
    info = read_job(job_id) or {"job_id": job_id}
    info.update(fields)
    return write_job(info)
