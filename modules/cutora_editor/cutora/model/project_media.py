# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every file a project uses: collect, resolve relative, warn if missing."""
from __future__ import annotations

import os

from cutora.core.log import get_logger
from cutora.model.offline import _iter_sources, _src_get, _src_set

_LOG = get_logger("cutora.model.project_media")

_PATH_KEYS = (
    "path", "preview_path", "source_path", "lut_path", "font_path",
    "intro_video_path", "file",
)


def _is_rel(path: str) -> bool:
    p = str(path or "")
    if not p:
        return False
    if os.path.isabs(p):
        return False
    if len(p) >= 2 and p[1] == ":":
        return False
    return True


def _set_path(holder, key, value):
    if isinstance(holder, dict):
        holder[key] = value
    else:
        setattr(holder, key, value)


def _walk_overlay_paths(ov):
    if not isinstance(ov, dict):
        return
    for k in ("path", "image_path", "font_path", "lut_path"):
        p = ov.get(k)
        if p:
            yield ov, k, str(p)
    for el in ov.get("elements") or []:
        if isinstance(el, dict):
            p = el.get("path")
            if p:
                yield el, "path", str(p)


def iter_media_refs(task: dict):
    """Yield (holder, key, path) for every media path on a task."""
    if not isinstance(task, dict):
        return
    for k in ("video", "path"):
        p = task.get(k)
        if p:
            yield task, k, str(p)
    for _sid, src in _iter_sources(task):
        for k in ("path", "preview_path", "source_path"):
            p = _src_get(src, k)
            if p:
                yield src, k, str(p)
    for ov in list(task.get("image_overlays") or []) + list(task.get("text_overlays") or []):
        yield from _walk_overlay_paths(ov)
    for clip in task.get("audio_clips") or []:
        if isinstance(clip, dict):
            p = clip.get("path")
            if p:
                yield clip, "path", str(p)
    style = task.get("style")
    if isinstance(style, dict):
        for k in ("lut_path", "font_path", "font_file"):
            p = style.get(k)
            if p:
                yield style, k, str(p)
    tl = task.get("timeline")
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    for tr in tracks or []:
        clips = getattr(tr, "clips", None)
        if clips is None and isinstance(tr, dict):
            clips = tr.get("clips") or []
        for c in clips or []:
            if isinstance(c, dict):
                p = c.get("intro_video_path")
                if p:
                    yield c, "intro_video_path", str(p)
            else:
                p = getattr(c, "intro_video_path", None)
                if p:
                    yield c, "intro_video_path", str(p)


def collect_missing(tasks) -> list[str]:
    seen = []
    got = set()
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        for _h, _k, p in iter_media_refs(task):
            if not p or p in got:
                continue
            if os.path.isfile(p):
                continue
            got.add(p)
            seen.append(p)
    return seen


def relativize_under_project(tasks, project_path: str) -> int:
    """Rewrite paths that already live in the project folder to relative."""
    root = os.path.dirname(os.path.abspath(project_path or "")) or ""
    if not root or not os.path.isdir(root):
        return 0
    root_abs = os.path.abspath(root)
    n = 0
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        for holder, key, p in iter_media_refs(task):
            if not p or _is_rel(p):
                continue
            abs_p = os.path.abspath(p)
            try:
                common = os.path.commonpath([root_abs, abs_p])
            except ValueError:
                continue
            if os.path.normcase(common) != os.path.normcase(root_abs):
                continue
            rel = os.path.relpath(abs_p, root_abs)
            _set_path(holder, key, rel.replace("\\", "/"))
            n += 1
    return n


def resolve_relative_paths(tasks, project_path: str) -> int:
    """Join relative media paths onto the project folder. Returns how many changed."""
    root = os.path.dirname(os.path.abspath(project_path or "")) or ""
    if not root:
        return 0
    n = 0
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        for holder, key, p in iter_media_refs(task):
            if not _is_rel(p):
                continue
            abs_p = os.path.normpath(os.path.join(root, p))
            _set_path(holder, key, abs_p)
            n += 1
    return n


def rebuild_animated_missing(tasks) -> int:
    """If the transcoded MOV is gone but the original GIF is still there, rebuild."""
    from cutora.media.animated import import_animated_as_clip
    n = 0
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        for sid, src in _iter_sources(task):
            path = _src_get(src, "path") or ""
            orig = _src_get(src, "source_path") or ""
            kind = _src_get(src, "kind")
            if path and os.path.isfile(path):
                continue
            if not orig or not os.path.isfile(orig):
                continue
            try:
                info = import_animated_as_clip(orig)
                new_path = info.get("path") or ""
                if new_path and os.path.isfile(new_path):
                    _src_set(src, "path", new_path)
                    if kind:
                        _src_set(src, "kind", kind)
                    _src_set(src, "source_path", os.path.abspath(orig))
                    n += 1
            except Exception:
                _LOG.exception("rebuild animated from %s", orig)
    return n
