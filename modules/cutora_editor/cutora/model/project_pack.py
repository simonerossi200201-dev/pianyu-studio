# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect every media file a project uses into one folder; rewrite paths relative."""
from __future__ import annotations

import os
import shutil

from cutora.model.offline import _src_set
from cutora.model.project_media import iter_media_refs


def _unique_name(dest_dir: str, name: str, used: set) -> str:
    base, ext = os.path.splitext(name or "media.bin")
    candidate = name or "media.bin"
    n = 1
    while candidate.lower() in used or os.path.isfile(os.path.join(dest_dir, candidate)):
        n += 1
        candidate = "%s_%d%s" % (base, n, ext)
    used.add(candidate.lower())
    return candidate


def _collect_paths(task: dict) -> list[tuple[object, str, str]]:
    """Return (holder, key, path) triples for every media file the task uses."""
    out = []
    for holder, key, path in iter_media_refs(task):
        if path:
            out.append((holder, key, path))
    return out


def pack_tasks(tasks: list, out_dir: str, mode: str = "copy") -> dict:
    mode = (mode or "copy").strip().lower()
    if mode not in ("copy", "move"):
        raise ValueError("mode 只能是 copy 或 move")
    out_dir = os.path.abspath(out_dir)
    media_dir = os.path.join(out_dir, "media")
    os.makedirs(media_dir, exist_ok=True)
    used = set()
    copied = []
    missing = []
    bytes_total = 0
    mapping = {}  # old abs -> relative

    def place(old: str) -> str | None:
        old_abs = os.path.abspath(old)
        if old_abs in mapping:
            return mapping[old_abs]
        if not os.path.isfile(old_abs):
            missing.append(old)
            return None
        name = _unique_name(media_dir, os.path.basename(old_abs), used)
        dest = os.path.join(media_dir, name)
        if mode == "move":
            shutil.move(old_abs, dest)
        else:
            shutil.copy2(old_abs, dest)
        rel = os.path.join("media", name)
        mapping[old_abs] = rel
        size = os.path.getsize(dest)
        bytes_total_holder[0] += size
        copied.append({"from": old_abs, "to": rel, "bytes": size})
        return rel

    bytes_total_holder = [0]
    packed_tasks = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        t = dict(task)
        tl = t.get("timeline")
        if isinstance(tl, dict):
            t["timeline"] = dict(tl)
            t["timeline"]["sources"] = dict(tl.get("sources") or {})
        for holder, key, path in _collect_paths(t):
            rel = place(path)
            if rel is None:
                continue
            if isinstance(holder, dict):
                holder[key] = rel
            else:
                _src_set(holder, key, rel)
            if key == "path" and holder is t:
                t["video"] = rel
                t["path"] = rel
            try:
                _src_set(holder, "offline", False)
            except Exception:
                pass
        packed_tasks.append(t)
    bytes_total = bytes_total_holder[0]
    return {
        "tasks": packed_tasks,
        "copied": copied,
        "missing": missing,
        "files": len(copied),
        "bytes": bytes_total,
        "out_dir": out_dir,
        "mode": mode,
    }
