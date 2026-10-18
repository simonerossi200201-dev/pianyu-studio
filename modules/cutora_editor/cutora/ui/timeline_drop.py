# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q115 · 把外部文件拖进时间线时用得到的纯逻辑。

这里不碰 Qt，也不碰 MainWindow —— 只回答三个问题：
这个文件是什么素材、该落到哪种轨道、拖进来的一批文件按什么顺序摆。
"""
from __future__ import annotations

import os

# 纯文本 → 文本 overlay；字幕文件 → 走 subtitle.import_file 追加。
TEXT_EXT = {".txt", ".md"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa"}

# 轨道类型 → 人话（跟时间线上画的标签一致）
TRACK_LABELS = {
    "video": "视频轨道",
    "image": "图片轨道",
    "text": "文本轨道",
    "subs": "字幕轨道",
    "audio": "音频轨道",
    "adjust": "调整轨道",
}

_KIND_TO_TRACK = {
    "video": "video",
    "audio": "audio",
    "image": "image",
    "text": "text",
    "subs": "subs",
}


def _ext_sets():
    """延迟取扩展名表，避免在 import 期把 main_window 拽进来。"""
    from cutora.ui.main_window import AUD_EXT, IMG_EXT, VID_EXT
    return VID_EXT, AUD_EXT, IMG_EXT


def kind_for_path(path: str) -> str | None:
    """这个文件算哪一类素材；不认识就返回 None。"""
    ext = os.path.splitext(str(path or ""))[1].lower()
    if not ext:
        return None
    if ext in SUB_EXT:
        return "subs"
    if ext in TEXT_EXT:
        return "text"
    vid, aud, img = _ext_sets()
    if ext in vid:
        return "video"
    if ext in aud:
        return "audio"
    if ext in img or ext in {".gif", ".apng"}:
        return "image"
    return None


def supported_exts() -> set:
    vid, aud, img = _ext_sets()
    return set(vid) | set(aud) | set(img) | {".gif", ".apng"} | TEXT_EXT | SUB_EXT


def track_type_for_kind(kind: str) -> str:
    return _KIND_TO_TRACK.get(kind or "", "video")


def kind_for_track_type(t_type: str) -> str | None:
    for kind, tt in _KIND_TO_TRACK.items():
        if tt == t_type:
            return kind
    return None


def accepts(track, kind: str) -> bool:
    """这条轨道收不收这类素材。"""
    if track is None or not kind:
        return False
    return getattr(track, "type", None) == track_type_for_kind(kind)


def sort_for_drop(paths):
    """一次拖进来一批文件时的摆放顺序：按文件名自然序，跟资源管理器里看到的一致。"""
    import re

    def key(p):
        name = os.path.basename(str(p))
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r"(\d+)", name)]

    return sorted(list(paths or []), key=key)


def track_display_name(timeline_widget, tl, track) -> str:
    """轨道在界面上的名字，比如「视频 2」；拿不到就退回轨道类型。"""
    if track is None:
        return ""
    custom = str(getattr(track, "name", "") or "").strip()
    if custom:
        return custom
    t_type = getattr(track, "type", "") or ""
    base = TRACK_LABELS.get(t_type, t_type or "轨道")
    same = [t for t in (getattr(tl, "tracks", None) or [])
            if getattr(t, "type", None) == t_type]
    if len(same) <= 1:
        return base
    try:
        idx = same.index(track) + 1
    except ValueError:
        idx = len(same)
    return "%s %d" % (base, idx)


def read_drop_text(path: str, limit: int = 2000) -> str:
    """把拖进来的纯文本读成 overlay 内容。读不出来返回空串，绝不抛。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                body = fh.read(limit + 1)
            break
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError:
            return ""
    else:
        return ""
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(body) > limit:
        body = body[:limit].rstrip() + "…"
    return body
