# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""黑白视频只挖它的上一条视频，不挖已经画好的整幅画面。

clip.matte_for 空着 = 普通视频，绘制路径和以前一样。
有值 = 这条片段不再自己画一层，而是先画父层，再用这条的亮度做 DestinationIn。
透明边不挖：沿用 luma_coverage_image 的盖不住就不挖。
"""
from __future__ import annotations


def matte_parent_id(clip) -> str:
    return str(getattr(clip, "matte_for", "") or "")


def is_child_matte(clip) -> bool:
    return bool(matte_parent_id(clip))


def project_has_child_matte(task) -> bool:
    tl = task.get("timeline") if isinstance(task, dict) else None
    for tr in getattr(tl, "tracks", None) or []:
        for c in getattr(tr, "clips", None) or []:
            if is_child_matte(c):
                return True
    return False


def video_above(task, track):
    """叠放顺序里，紧挨在 track 上面的那条视频轨。没有就 None。"""
    from cutora.batch.layer_model import stack_tracks
    videos = [t for t in stack_tracks(task) if getattr(t, "type", "") == "video"]
    if track not in videos:
        return None
    i = videos.index(track)
    if i + 1 >= len(videos):
        return None
    return videos[i + 1]


def set_track_matte(track, parent_id: str, invert: bool) -> int:
    n = 0
    for c in getattr(track, "clips", None) or []:
        if getattr(c, "type", "media") != "media":
            continue
        if getattr(c, "is_thumbnail_leadin", False) or getattr(c, "is_intro_video_leadin", False):
            continue
        c.matte_for = str(parent_id or "")
        c.matte_invert = bool(invert)
        n += 1
    return n


def partition_clips(task, track, t, active):
    """没有子蒙版时原样返回 active，调用方的循环一个字节都不用改。

    有的话：own = 这一轨上还要自己画的片段；mattes = 指向这一轨、当前时刻有效的蒙版片段。
    """
    if not project_has_child_matte(task):
        return active, []
    from cutora.model.timeline_model import active_clips_at
    own = [c for c in (active or []) if not is_child_matte(c)]
    want = str(getattr(track, "id", "") or "")
    mattes = []
    if not want:
        return own, mattes
    tl = task.get("timeline") if isinstance(task, dict) else None
    for tr in getattr(tl, "tracks", None) or []:
        if getattr(tr, "type", "") != "video" or not getattr(tr, "visible", True):
            continue
        for c in active_clips_at(tr, t) or []:
            if matte_parent_id(c) == want:
                mattes.append(c)
    return own, mattes


def paint_parent_matted(dest, width, height, own, mattes, draw_clip, blend_for, task=None) -> None:
    """先把父层画进透明图（按正常覆盖留下像素），再用每条蒙版的亮度挖这张图，最后按父层的混合贴回去。"""
    from PySide6.QtGui import QImage, QPainter

    from cutora.batch.layer_folders import hidden_by_folder
    from cutora.render.frame_renderer import (
        BLEND_MODES, _BLEND_NORMAL, luma_coverage_image, paint_blended_video_clip,
    )

    w, h = int(width), int(height)
    if dest is None or w <= 0 or h <= 0:
        return
    buf = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    buf.fill(0)
    p = QPainter(buf)
    try:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for clip in own or []:
            if hidden_by_folder(task, clip):
                continue
            paint_blended_video_clip(
                p, "normal", False, lambda pp, c=clip: draw_clip(pp, c), w, h)
    finally:
        if p.isActive():
            p.end()
    for clip in mattes or []:
        if hidden_by_folder(task, clip):
            continue
        src = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        src.fill(0)
        mp = QPainter(src)
        try:
            mp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            draw_clip(mp, clip)
        finally:
            if mp.isActive():
                mp.end()
        cover = luma_coverage_image(src, invert=bool(getattr(clip, "matte_invert", False)))
        bp = QPainter(buf)
        try:
            bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            if cover is not None and not cover.isNull():
                bp.drawImage(0, 0, cover)
        finally:
            if bp.isActive():
                bp.end()
    blend = "normal"
    for clip in own or []:
        name = str(blend_for(clip) or "normal")
        if name not in ("normal", "luma_matte", "luma_matte_inv"):
            blend = name
            break
    dest.save()
    try:
        dest.setCompositionMode(BLEND_MODES.get(blend, _BLEND_NORMAL))
        dest.drawImage(0, 0, buf)
    finally:
        dest.restore()
