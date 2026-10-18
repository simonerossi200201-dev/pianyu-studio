# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply subtitle import / export to a live window or a project file."""
from __future__ import annotations

import json
import os

from cutora.core.log import get_logger
from cutora.subtitle.track_source import set_segments
from cutora.subtitle.formats import (
    SubtitleFormatError,
    dump_subtitle,
    load_subtitle,
    save_subtitle,
)
from cutora.subtitle.model import (
    Segment,
    segments_from_dicts,
    segments_to_project_dicts,
)

logger = get_logger(__name__)

MODES = ("replace", "append")


class SubtitleIOError(ValueError):
    """Import / export could not complete."""


def apply_imported_segments(task: dict, segments: list[Segment], mode: str) -> dict:
    """Mutate ``task`` in place. ``mode`` is replace / append."""
    kind = (mode or "replace").strip().lower()
    if kind not in MODES:
        raise SubtitleIOError("未知导入方式: %s" % mode)
    dicts = segments_to_project_dicts(segments)
    if kind == "replace":
        set_segments(task, dicts)
        task["text"] = _join_text(dicts)
    else:
        cur = list(task.get("segments") or [])
        set_segments(task, cur + dicts)
        extra = _join_text(dicts)
        old = str(task.get("text") or "").strip()
        task["text"] = (old + " " + extra).strip() if extra else old
    return {
        "count": len(dicts),
        "mode": kind,
        "segment_count": len(task.get("segments") or []),
        "track_count": len(task.get("subtitle_tracks") or []),
    }


def apply_imported_text(task: dict, text: str, mode: str) -> dict:
    """TXT is copy waiting for align: fill ``task['text']``, do not create segments."""
    kind = (mode or "replace").strip().lower()
    if kind not in MODES:
        raise SubtitleIOError("未知导入方式: %s" % mode)
    body = (text or "").strip()
    if kind == "replace":
        task["text"] = body
    else:
        old = str(task.get("text") or "").strip()
        task["text"] = (old + " " + body).strip() if body else old
    return {
        "count": len([p for p in body.split() if p]) if body else 0,
        "mode": kind,
        "text_only": True,
        "segment_count": len(task.get("segments") or []),
        "track_count": len(task.get("subtitle_tracks") or []),
    }


def _join_text(dicts: list[dict]) -> str:
    parts = [str(d.get("text") or "").strip() for d in dicts]
    return " ".join(p for p in parts if p)


def find_task_in_project(data: dict, media: str | None) -> dict | None:
    tasks = list((data or {}).get("tasks") or [])
    if not tasks:
        return None
    media = media or ""
    if media:
        want = os.path.normcase(os.path.abspath(media))
        base = os.path.basename(media).lower()
        for task in tasks:
            path = task.get("video") or task.get("path") or ""
            if not path:
                continue
            if os.path.normcase(os.path.abspath(path)) == want:
                return task
            if os.path.basename(path).lower() == base:
                return task
    if len(tasks) == 1:
        return tasks[0]
    return None


def canvas_size(task: dict | None) -> tuple[int, int]:
    task = task or {}
    dims = task.get("dims")
    if isinstance(dims, (list, tuple)) and len(dims) == 2:
        try:
            return int(dims[0]), int(dims[1])
        except (TypeError, ValueError):
            pass
    tl = task.get("timeline") or {}
    if isinstance(tl, dict):
        return int(tl.get("width") or 1920), int(tl.get("height") or 1080)
    w = getattr(tl, "width", 1920)
    h = getattr(tl, "height", 1080)
    return int(w or 1920), int(h or 1080)


def canvas_size_for_window(window) -> tuple[int, int]:
    """Canvas of the window's current task, else the default 1920×1080."""
    tasks = getattr(window, "tasks", None)
    try:
        idx = int(getattr(window, "curr_idx", -1))
    except (TypeError, ValueError):
        idx = -1
    task = None
    if isinstance(tasks, list) and 0 <= idx < len(tasks):
        task = tasks[idx]
    return canvas_size(task)


def window_curr_idx(window) -> int:
    """``curr_idx`` 合法时可为 0，不能写成 ``x or -1``。"""
    try:
        return int(getattr(window, "curr_idx", -1))
    except (TypeError, ValueError):
        return -1


def _task_from_window(window, media_id, media: str):
    tasks = list(getattr(window, "tasks", None) or [])
    if media_id is not None:
        try:
            idx = int(media_id)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(tasks):
            return idx, tasks[idx]
        raise SubtitleIOError("没有这条媒体（id=%s）" % media_id)
    if media:
        want = os.path.normcase(os.path.abspath(media))
        for i, task in enumerate(tasks):
            path = task.get("video") or task.get("path") or ""
            if path and os.path.normcase(os.path.abspath(path)) == want:
                return i, task
    idx = window_curr_idx(window)
    if 0 <= idx < len(tasks):
        return idx, tasks[idx]
    if len(tasks) == 1:
        return 0, tasks[0]
    raise SubtitleIOError("没有可写入的字幕轨：请先打开工程并选中一条视频")


def _load_project(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise SubtitleIOError("无法读取工程: %s" % exc) from exc
    if not isinstance(data, dict):
        raise SubtitleIOError("工程文件不是 JSON 对象")
    return data


def _save_project(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _sibling_project(media: str) -> str | None:
    if not media:
        return None
    sibling = os.path.splitext(media)[0] + ".asproj.json"
    folder = os.path.join(os.path.dirname(media), "project.asproj.json")
    for cand in (sibling, folder):
        if os.path.isfile(cand):
            return cand
    return None


def import_subtitle(
    path: str,
    mode: str = "replace",
    fmt: str | None = None,
    window=None,
    media_id=None,
    media: str = "",
    project: str | None = None,
) -> dict:
    if not path:
        raise SubtitleIOError("subtitle.import_file 需要 path")
    if not os.path.isfile(path):
        raise SubtitleIOError("找不到字幕文件: %s" % path)
    from cutora.subtitle.formats import detect_format, read_text
    kind = detect_format(path, fmt)
    if kind == "txt":
        from cutora.subtitle.formats.txt import split_sentences
        text = " ".join(split_sentences(read_text(path)))
        return _commit_import(window, media_id, media, project, path, mode, text=text)
    segments = load_subtitle(path, fmt=fmt)
    return _commit_import(
        window, media_id, media, project, path, mode, segments=segments)


def _commit_import(window, media_id, media, project, path, mode, *, segments=None, text=None):
    if window is not None:
        idx, task = _task_from_window(window, media_id, media)
        if text is not None:
            result = apply_imported_text(task, text, mode)
        else:
            result = apply_imported_segments(task, segments or [], mode)
        refresh = getattr(window, "_refresh_task_view", None)
        if callable(refresh):
            refresh(idx)
        result["media_id"] = idx
        result["path"] = os.path.abspath(path)
        return result
    proj = project or _sibling_project(media)
    if not proj:
        raise SubtitleIOError("无头导入需要 project 或与视频同目录的工程文件")
    data = _load_project(proj)
    task = find_task_in_project(data, media)
    if task is None:
        raise SubtitleIOError("工程里没有匹配的视频任务")
    if text is not None:
        result = apply_imported_text(task, text, mode)
    else:
        result = apply_imported_segments(task, segments or [], mode)
    _save_project(proj, data)
    result["project"] = os.path.abspath(proj)
    result["path"] = os.path.abspath(path)
    return result


def _resolve_task(window=None, media_id=None, media="", project=None):
    if window is not None:
        idx, task = _task_from_window(window, media_id, media)
        return idx, task, None, None
    proj = project or _sibling_project(media)
    if not proj:
        raise SubtitleIOError("无头操作需要 project 或与视频同目录的工程文件")
    data = _load_project(proj)
    task = find_task_in_project(data, media)
    if task is None:
        raise SubtitleIOError("工程里没有匹配的视频任务")
    return None, task, proj, data


def mutate_task(fn, *, window=None, media_id=None, media="", project=None, write=True):
    """Run ``fn(task)``. ``write=False`` is read-only (no project save)."""
    idx, task, proj, data = _resolve_task(window, media_id, media, project)
    result = fn(task)
    result = dict(result or {})
    if window is not None:
        if write:
            refresh = getattr(window, "_refresh_task_view", None)
            if callable(refresh):
                refresh(idx)
        result["media_id"] = idx
        return result
    if write:
        _save_project(proj, data)
    result["project"] = os.path.abspath(proj)
    return result


def _segments_of(task: dict) -> list[Segment]:
    return segments_from_dicts(list(task.get("segments") or []))


def export_subtitle(
    path: str,
    fmt: str | None = None,
    window=None,
    media_id=None,
    media: str = "",
    project: str | None = None,
    bom: bool = False,
    karaoke: bool = True,
) -> dict:
    if not path:
        raise SubtitleIOError("subtitle.export_file 需要 path")
    task = None
    if window is not None:
        _idx, task = _task_from_window(window, media_id, media)
    else:
        proj = project or _sibling_project(media)
        if not proj:
            raise SubtitleIOError("无头导出需要 project 或与视频同目录的工程文件")
        data = _load_project(proj)
        task = find_task_in_project(data, media)
        if task is None:
            raise SubtitleIOError("工程里没有匹配的视频任务")
    segments = _segments_of(task)
    if not segments:
        raise SubtitleIOError("没有字幕可导出")
    play_w, play_h = canvas_size(task)
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    dest = save_subtitle(
        path,
        segments,
        fmt=fmt,
        style=style,
        play_w=play_w,
        play_h=play_h,
        karaoke=karaoke,
        bom=bom,
    )
    return {
        "path": dest,
        "artifact": dest,
        "count": len(segments),
        "format": fmt or os.path.splitext(dest)[1].lstrip(".").lower(),
    }


# Re-export for callers that only import io.
__all__ = [
    "SubtitleIOError",
    "SubtitleFormatError",
    "MODES",
    "apply_imported_segments",
    "import_subtitle",
    "export_subtitle",
    "find_task_in_project",
    "canvas_size",
    "canvas_size_for_window",
    "dump_subtitle",
    "load_subtitle",
]
