# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable uuid for subtitle segments (T20b). Index addressing is migration-only."""
from __future__ import annotations

import uuid


def new_seg_id() -> str:
    return str(uuid.uuid4())


def looks_like_uuid(value) -> bool:
    s = str(value or "").strip()
    if len(s) == 36 and s.count("-") == 4:
        return True
    if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        return True
    return False


def ensure_segment_ids(task: dict | None) -> int:
    """Assign uuid to segments that lack id. Returns how many were created."""
    if not isinstance(task, dict):
        return 0
    n = 0
    seen: set[str] = set()
    for seg in task.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        cur = str(seg.get("id") or "").strip()
        if cur and cur not in seen:
            seen.add(cur)
            continue
        # 没有 id，或者和前面某条重复（Q139：老工程里单字蹦切出来的词全共用
        # 母句 id，光栅缓存会把上一个词画到这个词上）→ 发新的
        seg["id"] = new_seg_id()
        seen.add(seg["id"])
        n += 1
    return n


def ensure_tasks_segment_ids(tasks) -> int:
    n = 0
    for task in tasks or []:
        n += ensure_segment_ids(task)
    return n


def resolve_seg_index(task: dict, seg_id) -> int:
    """uuid → index. Integer (and numeric string) is the migration path."""
    segs = list((task or {}).get("segments") or [])
    if looks_like_uuid(seg_id):
        want = str(seg_id).strip()
        for i, seg in enumerate(segs):
            if isinstance(seg, dict) and str(seg.get("id") or "") == want:
                return i
        raise ValueError("seg_id 未找到: %s" % want)
    try:
        i = int(seg_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("seg_id 无效: %r" % seg_id) from exc
    if i < 0 or i >= len(segs):
        raise ValueError("seg_id 越界")
    return i


def resolve_seg_indices(task: dict, seg_ids) -> list[int]:
    out = []
    for sid in seg_ids or []:
        i = resolve_seg_index(task, sid)
        if i not in out:
            out.append(i)
    return out


def id_of(task: dict, index: int) -> str | None:
    segs = list((task or {}).get("segments") or [])
    if 0 <= index < len(segs) and isinstance(segs[index], dict):
        return segs[index].get("id")
    return None
