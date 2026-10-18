# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline media: inspect on load, hard-stop export, relink.

Duration is never silently replaced. JSON value and ffprobe value are both kept.
"""
from __future__ import annotations

import os

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.log import get_logger
from cutora.model.timeline_model import probe_duration_sec

logger = get_logger("cutora.model.offline")

DURATION_EPS = 0.05
SETTING_LAST_ROOT = "last_media_root"


def _isfile(path) -> bool:
    return bool(path) and os.path.isfile(str(path))


def _src_get(src, key, default=None):
    if src is None:
        return default
    if isinstance(src, dict):
        return src.get(key, default)
    return getattr(src, key, default)


def _src_set(src, key, value):
    if src is None:
        return
    if isinstance(src, dict):
        src[key] = value
    else:
        setattr(src, key, value)


def inspect_path(path, duration_json=None) -> dict:
    path = str(path or "")
    rec = {
        "path": path,
        "offline": False,
        "duration_json": duration_json,
        "duration_probed": None,
        "duration_mismatch": False,
    }
    if not path:
        return rec
    if not _isfile(path):
        rec["offline"] = True
        return rec
    probed = probe_duration_sec(path)
    rec["duration_probed"] = probed
    if duration_json is not None and probed is not None:
        try:
            rec["duration_mismatch"] = (
                abs(float(duration_json) - float(probed)) > DURATION_EPS)
        except (TypeError, ValueError):
            rec["duration_mismatch"] = True
    return rec


def _timeline_sources(task: dict):
    tl = (task or {}).get("timeline")
    sources = {}
    if tl is not None:
        sources = getattr(tl, "sources", None) or (tl.get("sources") if isinstance(tl, dict) else {}) or {}
    return sources if isinstance(sources, dict) else {}


def _iter_sources(task: dict):
    sources = _timeline_sources(task)
    for sid, src in sources.items():
        yield str(sid), src
    if sources:
        return
    video = (task or {}).get("video") or (task or {}).get("path")
    if video:
        yield "_task_video", {"id": "_task_video", "path": video, "duration": (task or {}).get("duration")}


def _is_timeline_project(task: dict) -> bool:
    return bool(_timeline_sources(task))


def format_mismatch_lines(rows: list) -> list[str]:
    """User-facing duration mismatch copy. Includes what to do."""
    lines = []
    for rec in rows or []:
        name = os.path.basename(str(rec.get("path") or rec.get("source_id") or "片段"))
        try:
            j = float(rec.get("duration_json"))
        except (TypeError, ValueError):
            j = 0.0
        try:
            p = float(rec.get("duration_probed"))
        except (TypeError, ValueError):
            p = 0.0
        lines.append(
            "%s：工程记录 %.2f 秒，文件实际 %.2f 秒。工程里的时长已保留，导出按文件实际时长。"
            % (name, j, p)
        )
    if lines:
        lines.append(
            "怎么办：若画面或声音被截短，用「重新链接素材」指向正确文件；"
            "只差零点几秒可以忽略，不必重新生成。"
        )
    return lines


def inspect_task(task: dict) -> dict:
    """Flag offline / duration_mismatch on the task and its sources. Does not change duration.

    Timeline projects compare each source.duration to that file. They do NOT
    compare task.duration (full timeline) to task.video (first clip).
    """
    offline_paths = []
    mismatch_rows = []
    seen = set()
    video = (task or {}).get("video") or (task or {}).get("path") or ""
    timeline = _is_timeline_project(task)
    video_rec = {"offline": False, "duration_mismatch": False, "duration_probed": None}
    if video and not timeline:
        dur_json = (task or {}).get("duration")
        video_rec = inspect_path(video, dur_json)
        seen.add(os.path.normcase(os.path.normpath(video)))
        _apply_task_video_flags(task, video_rec)
        if video_rec["offline"]:
            offline_paths.append(video)
        if video_rec["duration_mismatch"]:
            mismatch_rows.append(video_rec)
    elif video:
        seen.add(os.path.normcase(os.path.normpath(video)))
        rec = inspect_path(video, None)
        try:
            from cutora.media.probe_cache import pump_ui
            pump_ui()
        except Exception:
            pass
        if rec["offline"]:
            offline_paths.append(video)
            task["offline"] = True
    for sid, src in _iter_sources(task):
        if sid == "_task_video":
            continue
        path = _src_get(src, "path") or ""
        key = os.path.normcase(os.path.normpath(path)) if path else sid
        rec = inspect_path(path, _src_get(src, "duration"))
        try:
            from cutora.media.probe_cache import pump_ui
            pump_ui()
        except Exception:
            pass
        _src_set(src, "offline", rec["offline"])
        _src_set(src, "duration_probed", rec["duration_probed"])
        _src_set(src, "duration_mismatch", rec["duration_mismatch"])
        if rec["offline"] and key not in seen:
            offline_paths.append(path)
            seen.add(key)
        if rec["duration_mismatch"]:
            mismatch_rows.append(dict(rec, source_id=sid))
    task["offline"] = bool(offline_paths)
    task["duration_mismatch"] = bool(mismatch_rows)
    if not timeline:
        task["duration_mismatch"] = bool(mismatch_rows) or bool(video_rec.get("duration_mismatch"))
    return {
        "offline_paths": offline_paths,
        "duration_mismatch": mismatch_rows,
    }


def _apply_task_video_flags(task, rec):
    task["offline"] = rec["offline"]
    task["duration_probed"] = rec["duration_probed"]
    task["duration_mismatch"] = rec["duration_mismatch"]
    # Keep task["duration"] as the JSON value.


def inspect_project_data(data: dict) -> dict:
    offline = []
    mismatch = []
    for task in (data or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        report = inspect_task(task)
        offline.extend(report["offline_paths"])
        mismatch.extend(report["duration_mismatch"])
    return {"offline_paths": offline, "duration_mismatch": mismatch}


def list_offline(tasks) -> list[str]:
    out = []
    seen = set()
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if not task.get("offline"):
            inspect_task(task)
        for p in _offline_paths_of(task):
            key = os.path.normcase(os.path.normpath(p))
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def _offline_paths_of(task: dict) -> list[str]:
    paths = []
    video = task.get("video") or task.get("path") or ""
    if video and not _isfile(video):
        paths.append(video)
    for _sid, src in _iter_sources(task):
        if _sid == "_task_video":
            continue
        if _src_get(src, "offline") or ( _src_get(src, "path") and not _isfile(_src_get(src, "path"))):
            p = _src_get(src, "path")
            if p:
                paths.append(p)
    return paths


def require_online(tasks, allow_offline=False):
    if allow_offline:
        return
    missing = list_offline(tasks)
    if missing:
        from cutora.core.commands import CommandError
        raise CommandError("离线素材，拒绝导出：\n" + "\n".join(missing))


def _rewrite_path(old, old_root, new_root):
    if not old:
        return old
    a = os.path.normcase(os.path.normpath(old))
    root = os.path.normcase(os.path.normpath(old_root))
    if a == root or a.startswith(root + os.sep):
        tail = os.path.normpath(old)[len(os.path.normpath(old_root)):].lstrip("\\/")
        return os.path.join(new_root, tail)
    return old


def relink_source(task: dict, source_id: str, new_path: str) -> int:
    new_path = os.path.abspath(str(new_path or ""))
    if not _isfile(new_path):
        raise ValueError("新路径不是文件: %s" % new_path)
    n = 0
    sid = str(source_id or "")
    tl = task.get("timeline")
    sources = getattr(tl, "sources", None) or (tl.get("sources") if isinstance(tl, dict) else None) or {}
    src = sources.get(sid) if sid else None
    if src is not None:
        old = _src_get(src, "path") or ""
        _src_set(src, "path", new_path)
        n += 1
        video = task.get("video") or task.get("path") or ""
        if old and video and os.path.normcase(os.path.normpath(video)) == os.path.normcase(os.path.normpath(old)):
            task["video"] = new_path
            task["path"] = new_path
    elif sid in ("_task_video", "video", ""):
        task["video"] = new_path
        task["path"] = new_path
        n += 1
    inspect_task(task)
    if n:
        _remember_root(os.path.dirname(new_path))
    return n


def relink_folder(tasks, old_root: str, new_root: str) -> int:
    old_root = os.path.abspath(str(old_root or ""))
    new_root = os.path.abspath(str(new_root or ""))
    if not os.path.isdir(new_root):
        raise ValueError("新根目录不存在: %s" % new_root)
    n = 0
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        video = task.get("video") or task.get("path") or ""
        nv = _rewrite_path(video, old_root, new_root)
        if nv != video:
            task["video"] = nv
            task["path"] = nv
            n += 1
        for _sid, src in _iter_sources(task):
            if _sid == "_task_video":
                continue
            p = _src_get(src, "path") or ""
            np = _rewrite_path(p, old_root, new_root)
            if np != p:
                _src_set(src, "path", np)
                n += 1
        inspect_task(task)
    if n:
        _remember_root(new_root)
    return n


def suggest_relink(path, project_dir=None, last_root=None) -> list[str]:
    name = os.path.basename(str(path or ""))
    if not name:
        return []
    roots = []
    for r in (project_dir, last_root, last_media_root()):
        if r and r not in roots:
            roots.append(r)
    found = []
    for root in roots:
        cand = os.path.join(root, name)
        if _isfile(cand) and cand not in found:
            found.append(os.path.abspath(cand))
    return found


def last_media_root() -> str | None:
    s = _load_app_settings() or {}
    r = s.get(SETTING_LAST_ROOT)
    return str(r) if r else None


def _remember_root(path: str) -> None:
    if not path:
        return
    s = _load_app_settings() or {}
    s[SETTING_LAST_ROOT] = path
    _save_app_settings(s)


def source_is_offline(src) -> bool:
    if src is None:
        return False
    if _src_get(src, "offline"):
        return True
    path = _src_get(src, "path")
    return bool(path) and not _isfile(path)
