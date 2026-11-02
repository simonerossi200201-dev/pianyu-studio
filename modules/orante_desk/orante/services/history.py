# -*- coding: utf-8 -*-
"""Orante 全局专业历史记录与撤销/重做（Undo / Redo）引擎。

支持对以下所有用户编辑操作进行毫秒级快照记录与无损撤销/重做：
1. 2 排镜头 / 3 提示词：分段字段修改（文案、配图、时长、Flow模式、衔接、预设名称、提示词覆盖、附加提示词列）
2. 右键菜单批量操作：同图批量同步提示词、拷贝预设到全部段落、批量清空、粘贴、拖拽填充
3. 3 提示词：预设管理的新增、修改、删除、分组
4. 2 排镜头：图池「不使用」排除/恢复、自动排轨应用
"""
from __future__ import annotations

import copy
import dataclasses
import threading
import time
from pathlib import Path
from typing import Any

from .. import db, events, tasks as taskmod
from ..models import Manifest

_LOCK = threading.RLock()
_MAX_HISTORY = 60

# 每个快照结构:
# {
#   "id": int,
#   "label": str,
#   "ts": float,
#   "time_str": str,
#   "tasks": { tid: { "manifest": dict, "db_row": dict } },
#   "presets": list[dict] | None,
# }
_UNDO_STACK: list[dict[str, Any]] = []
_REDO_STACK: list[dict[str, Any]] = []
_SEQ = 0
_SUPPRESS = False


def _now_str() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def capture_state(
    task_ids: list[str] | set[str] | None = None,
    include_presets: bool = False,
) -> dict[str, Any]:
    """捕获指定任务（Manifest + DB 任务行）及可选预设表的当前完整状态。"""
    tasks_snap: dict[str, dict[str, Any]] = {}
    for tid in sorted(set(task_ids or [])):
        tid_clean = str(tid or "").strip()
        if not tid_clean:
            continue
        try:
            row = db.one("SELECT * FROM tasks WHERE id=?", (tid_clean,))
            if not row:
                continue
            d_str = row.get("dir") or ""
            m_raw: dict[str, Any] | None = None
            if d_str and Path(d_str).is_dir():
                m_path = Path(d_str) / "manifest.json"
                if m_path.is_file():
                    m_obj = Manifest.load(Path(d_str))
                    m_raw = copy.deepcopy(dataclasses.asdict(m_obj))
            tasks_snap[tid_clean] = {
                "db_row": copy.deepcopy(dict(row)),
                "manifest": m_raw,
            }
        except Exception:
            continue

    presets_snap: list[dict[str, Any]] | None = None
    if include_presets:
        try:
            rows = db.rows("SELECT * FROM prompt_presets ORDER BY sort_order ASC, created ASC, id ASC")
            presets_snap = [copy.deepcopy(dict(r)) for r in rows]
        except Exception:
            presets_snap = None

    return {
        "tasks": tasks_snap,
        "presets": presets_snap,
    }


def record_before(
    label: str,
    task_ids: list[str] | set[str] | None = None,
    include_presets: bool = False,
     coalesce_window_s: float = 1.2,
) -> int:
    """在执行修改前记录一次撤销点快照。若短时间内同一 label 连续输入（如打字），自动合并防抖。"""
    global _SEQ
    with _LOCK:
        if _SUPPRESS:
            return 0
        now = time.time()
        # 如果栈顶操作 label 相同且在防抖窗口内，保留栈顶最初的 before 快照即可
        if (
            coalesce_window_s > 0
            and _UNDO_STACK
            and _UNDO_STACK[-1].get("label") == label
            and (now - float(_UNDO_STACK[-1].get("ts") or 0)) <= coalesce_window_s
        ):
            _UNDO_STACK[-1]["ts"] = now
            return int(_UNDO_STACK[-1]["id"])

        snap = capture_state(task_ids=task_ids, include_presets=include_presets)
        if not snap["tasks"] and snap["presets"] is None:
            return 0

        _SEQ += 1
        entry = {
            "id": _SEQ,
            "label": str(label or "编辑操作"),
            "ts": now,
            "time_str": _now_str(),
            "tasks": snap["tasks"],
            "presets": snap["presets"],
        }
        _UNDO_STACK.append(entry)
        if len(_UNDO_STACK) > _MAX_HISTORY:
            _UNDO_STACK.pop(0)
        _REDO_STACK.clear()
        return _SEQ


def _apply_snapshot(snap: dict[str, Any]) -> list[str]:
    """将快照无损恢复到磁盘 manifest.json 与 SQLite 数据库。"""
    global _SUPPRESS
    restored_tids: list[str] = []
    _SUPPRESS = True
    try:
        # 1. 恢复预设表
        if snap.get("presets") is not None:
            presets_list = snap["presets"] or []
            db.run("DELETE FROM prompt_presets")
            for r in presets_list:
                db.run(
                    """INSERT INTO prompt_presets(
                         id, name, category, role, template, speech_gap_newlines,
                         builtin, sort_order, meta, created, updated
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        r.get("id"),
                        r.get("name") or "",
                        r.get("category") or "默认分组",
                        r.get("role") or "main",
                        r.get("template") or "",
                        int(r.get("speech_gap_newlines") or 0),
                        int(r.get("builtin") or 0),
                        int(r.get("sort_order") or 0),
                        r.get("meta") if isinstance(r.get("meta"), str) else db.j(r.get("meta") or {}),
                        r.get("created") or db.now(),
                        r.get("updated") or db.now(),
                    ),
                )

        # 2. 恢复任务表与 Manifest
        tasks_map = snap.get("tasks") or {}
        for tid, tdata in tasks_map.items():
            db_row = tdata.get("db_row")
            m_raw = tdata.get("manifest")
            if db_row:
                db.run(
                    """UPDATE tasks SET
                         name=?, status=?, engine=?,
                         flow_mode=?, preset_id=?, meta=?, updated=?
                       WHERE id=?""",
                    (
                        db_row.get("name") or tid,
                        db_row.get("status") or "prepared",
                        db_row.get("engine") or "flow",
                        db_row.get("flow_mode") or "auto",
                        db_row.get("preset_id") or "",
                        db_row.get("meta") if isinstance(db_row.get("meta"), str) else db.j(db_row.get("meta") or {}),
                        db.now(),
                        tid,
                    ),
                )
            if m_raw and db_row and db_row.get("dir"):
                d_path = Path(db_row["dir"])
                if d_path.is_dir():
                    m_obj = Manifest(**{k: copy.deepcopy(m_raw[k]) for k in Manifest.__dataclass_fields__ if k in m_raw})
                    m_obj.save(d_path)
                    try:
                        c = taskmod.remember_counts(d_path, m_obj)
                        taskmod.emit_seg_changed(tid, c, force=True)
                    except Exception:
                        pass
            restored_tids.append(tid)
            try:
                events.emit("arrange.changed", task_id=tid)
            except Exception:
                pass

        try:
            events.emit("tasks.changed")
        except Exception:
            pass
    finally:
        _SUPPRESS = False
    return restored_tids


def undo() -> dict[str, Any]:
    """撤销上一步操作，并将当前状态压入重做栈。"""
    with _LOCK:
        if not _UNDO_STACK:
            st = get_status()
            return {
                "ok": False,
                "applied": False,
                "message": "当前没有可撤销的操作",
                "history": st,
                **st,
            }
        entry = _UNDO_STACK.pop()
        tids = list((entry.get("tasks") or {}).keys())
        has_presets = entry.get("presets") is not None
        cur_snap = capture_state(task_ids=tids, include_presets=has_presets)
        redo_entry = {
            "id": entry["id"],
            "label": entry["label"],
            "ts": time.time(),
            "time_str": _now_str(),
            "tasks": cur_snap["tasks"],
            "presets": cur_snap["presets"],
        }
        _REDO_STACK.append(redo_entry)
        restored_tids = _apply_snapshot(entry)
        st = get_status()
        return {
            "ok": True,
            "applied": True,
            "action": "undo",
            "label": entry["label"],
            "restored_tasks": restored_tids,
            "restored_presets": has_presets,
            "history": st,
            **st,
        }


def redo() -> dict[str, Any]:
    """重做上一步被撤销的操作。"""
    with _LOCK:
        if not _REDO_STACK:
            st = get_status()
            return {
                "ok": False,
                "applied": False,
                "message": "当前没有可重做的操作",
                "history": st,
                **st,
            }
        entry = _REDO_STACK.pop()
        tids = list((entry.get("tasks") or {}).keys())
        has_presets = entry.get("presets") is not None
        cur_snap = capture_state(task_ids=tids, include_presets=has_presets)
        undo_entry = {
            "id": entry["id"],
            "label": entry["label"],
            "ts": time.time(),
            "time_str": _now_str(),
            "tasks": cur_snap["tasks"],
            "presets": cur_snap["presets"],
        }
        _UNDO_STACK.append(undo_entry)
        restored_tids = _apply_snapshot(entry)
        st = get_status()
        return {
            "ok": True,
            "applied": True,
            "action": "redo",
            "label": entry["label"],
            "restored_tasks": restored_tids,
            "restored_presets": has_presets,
            "history": st,
            **st,
        }


def get_status() -> dict[str, Any]:
    """获取当前撤销/重做栈状态与最近历史记录列表。"""
    with _LOCK:
        undo_list = [
            {
                "id": item["id"],
                "label": item["label"],
                "time": item["time_str"],
                "tasks": list((item.get("tasks") or {}).keys()),
                "has_presets": item.get("presets") is not None,
            }
            for item in reversed(_UNDO_STACK[-25:])
        ]
        redo_list = [
            {
                "id": item["id"],
                "label": item["label"],
                "time": item["time_str"],
                "tasks": list((item.get("tasks") or {}).keys()),
                "has_presets": item.get("presets") is not None,
            }
            for item in reversed(_REDO_STACK[-25:])
        ]
        return {
            "can_undo": len(_UNDO_STACK) > 0,
            "can_redo": len(_REDO_STACK) > 0,
            "undo_count": len(_UNDO_STACK),
            "redo_count": len(_REDO_STACK),
            "last_undo_label": _UNDO_STACK[-1]["label"] if _UNDO_STACK else "",
            "last_redo_label": _REDO_STACK[-1]["label"] if _REDO_STACK else "",
            "undo_stack": undo_list,
            "redo_stack": redo_list,
            "undo_list": undo_list,
            "redo_list": redo_list,
        }


def clear_history() -> dict[str, Any]:
    with _LOCK:
        _UNDO_STACK.clear()
        _REDO_STACK.clear()
        return {"ok": True, **get_status()}
