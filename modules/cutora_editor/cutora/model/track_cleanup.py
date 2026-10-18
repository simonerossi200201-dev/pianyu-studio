# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q176 · 删轨道：批量删空轨道（当前视频 / 全部视频）+ 连同素材整条删除。

「空」按最严的口径算，宁可少删也绝不删错：
- 视频 / 音频轨：没有片段，也没有转场；
- 文字 / 图片 / 调整轨：没有挂在它上面的覆层（老工程没写 ``_track_id`` 的覆层算第一条同类轨的）；
- 字幕轨：没有字幕条；第一条字幕轨还要求工程里的字幕列表也是空的（字幕实际存在第一条字幕轨上）。

下面这些轨道就算是空的也不在批量里删（留着它们是有原因的）：主视频轨、分屏格子轨、整幅背景轨、
锁住的轨道、钉住的轨道。整条删除时主视频轨和锁住的轨道也不许删。

不碰 Qt，能单独测。撤销由调用方在动手前拍快照。
"""
from __future__ import annotations

SUBS_TYPES = ("subs", "subtitle")
OVERLAY_TYPES = {"text": "text_overlays", "image": "image_overlays", "adjust": "image_overlays"}
TYPE_WORD = {"video": "视频", "audio": "音频", "bgm": "音乐", "sfx": "音效", "text": "文本",
             "image": "图片", "adjust": "调整", "subs": "字幕", "subtitle": "字幕"}


def _tl(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    return tl if tl is not None and hasattr(tl, "tracks") else None


def _first_of_type(tl, ttype):
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", None) == ttype:
            return tr
    return None


def overlays_on_track(task, track) -> list:
    """[(列表名, 覆层)]：挂在这条文字 / 图片 / 调整轨上的覆层。"""
    ttype = getattr(track, "type", None)
    list_name = OVERLAY_TYPES.get(ttype)
    if not list_name or not isinstance(task, dict):
        return []
    tl = _tl(task)
    first = _first_of_type(tl, ttype) if tl is not None else None
    tid = getattr(track, "id", None)
    out = []
    for ov in task.get(list_name) or []:
        if not isinstance(ov, dict):
            continue
        if list_name == "image_overlays" and (ov.get("type") == "adjust") != (ttype == "adjust"):
            continue
        oid = ov.get("_track_id")
        if oid == tid or (oid is None and track is first):
            out.append((list_name, ov))
    return out


def _subs_count(task, track) -> int:
    from cutora.subtitle.track_source import first_subs_track, track_event_count
    n = int(track_event_count(track))
    if track is first_subs_track(task) and isinstance(task, dict):
        segs = task.get("segments") or []
        n = max(n, len(segs) if isinstance(segs, list) else 0)
    return n


def track_item_count(task, track) -> int:
    """这条轨道上有几样东西（片段 / 转场 / 覆层 / 字幕条）。0 = 空。"""
    ttype = getattr(track, "type", None)
    n = len(getattr(track, "clips", None) or [])
    n += len(getattr(track, "transitions", None) or [])
    if ttype in OVERLAY_TYPES:
        n += len(overlays_on_track(task, track))
    elif ttype in SUBS_TYPES:
        n += _subs_count(task, track)
    return n


def protected_reason(track) -> str:
    """批量删空轨道时不碰它的原因；空字符串 = 可以删。"""
    if getattr(track, "is_main_video", False):
        return "主视频轨"
    if getattr(track, "split_cell", None):
        return "分屏格子轨"
    if str(getattr(track, "bg_role", "") or ""):
        return "整幅背景轨"
    if getattr(track, "locked", False):
        return "已锁定"
    if getattr(track, "pinned", False):
        return "已钉住"
    return ""


def empty_tracks(task) -> list:
    """可以删的空轨道（按时间线顺序）。"""
    tl = _tl(task)
    if tl is None:
        return []
    return [tr for tr in list(tl.tracks) if not protected_reason(tr) and track_item_count(task, tr) == 0]


def remove_empty_tracks(task) -> list:
    """删掉这条视频里全部可以删的空轨道，返回删了哪些（Track 对象）。删之前逐条再核一遍。"""
    tl = _tl(task)
    if tl is None:
        return []
    removed = []
    for tr in empty_tracks(task):
        if tr in tl.tracks and not protected_reason(tr) and track_item_count(task, tr) == 0:
            tl.tracks.remove(tr)
            removed.append(tr)
    return removed


def force_delete_block(track) -> str:
    """整条删除（连素材）不允许的原因；空 = 允许。"""
    if getattr(track, "is_main_video", False):
        return "主视频轨不能整条删除（只能删掉上面的片段）"
    if getattr(track, "locked", False):
        return "轨道已锁定：先解锁再删"
    return ""


def describe_items(task, track) -> str:
    """给确认框用：「12 个片段、1 个转场」这样的一句话。"""
    ttype = getattr(track, "type", None)
    parts = []
    nc = len(getattr(track, "clips", None) or [])
    if nc:
        parts.append("%d 个片段" % nc)
    nt = len(getattr(track, "transitions", None) or [])
    if nt:
        parts.append("%d 个转场" % nt)
    if ttype in OVERLAY_TYPES:
        no = len(overlays_on_track(task, track))
        if no:
            word = {"text": "条文字", "image": "张图片", "adjust": "条调整条"}[ttype]
            parts.append("%d %s" % (no, word))
    elif ttype in SUBS_TYPES:
        ns = _subs_count(task, track)
        if ns:
            parts.append("%d 条字幕" % ns)
    return "、".join(parts)


def delete_track_with_content(task, track) -> dict:
    """整条删除：轨道 + 上面的片段 / 覆层 / 字幕。返回 {"removed": bool, "overlays": n, "subs": n}。"""
    tl = _tl(task)
    out = {"removed": False, "overlays": 0, "subs": 0}
    if tl is None or track not in tl.tracks or force_delete_block(track):
        return out
    ttype = getattr(track, "type", None)
    if ttype in OVERLAY_TYPES:
        doomed = overlays_on_track(task, track)
        for list_name, ov in doomed:
            rows = task.get(list_name) or []
            for i, o in enumerate(rows):
                if o is ov:
                    del rows[i]
                    out["overlays"] += 1
                    break
    was_first_subs = False
    if ttype in SUBS_TYPES:
        from cutora.subtitle.track_source import first_subs_track
        was_first_subs = track is first_subs_track(task)
        out["subs"] = _subs_count(task, track)
    tl.tracks.remove(track)
    out["removed"] = True
    if was_first_subs:
        # 工程字幕列表跟着第一条字幕轨走：这条没了 → 换成下一条字幕轨的（没有就清空）
        from cutora.subtitle.track_source import events_of, first_subs_track
        nxt = first_subs_track(task)
        ev = events_of(nxt) if nxt is not None else None
        task["segments"] = ev if isinstance(ev, list) else []
    return out
