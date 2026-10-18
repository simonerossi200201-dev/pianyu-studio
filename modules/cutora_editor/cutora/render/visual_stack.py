# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q163 · 所有视觉轨道（视频 / 图片 / 文本 / 字幕）按时间线顺序统一叠压。

约定（和视频轨一直以来的规则一样）：``timeline.tracks`` 列表下标 0 = 最底层（先画），
越往后越在上面；时间线界面里越靠上的那一行就是列表越靠前的、画面里越在底下的。

以前视频轨全部先画，图片 / 文本 / 字幕永远压在所有视频上面（再按类型分块），所以把文字
挪到视频下面（比如视频抠像后字放在人后面）根本合成不出来。

这里把视觉轨道排成一摞：

* ``("video", [轨道, …])``：连续的一段视频轨（一次画完，一张「视频带」）。
  整幅背景轨（bg_role=backdrop）永远在最底那一段；分屏格子里的轨道是一个整体，放在第一条格子轨所在的那一段。
* ``("overlay", 轨道)``：一条图片 / 文本 / 字幕轨。
* ``("subs_orphan", None)``：有字幕但时间线上没有字幕轨（老工程 / 测试）→ 照老样子压最上。
* ``("adjust", 轨道)``：Q174 调整轨。画到它时，把到此为止画好的整幅画面（它下面的全部视频 / 图片 / 文字 / 字幕）
  按它的形状做效果、再盖纯色（``render/adjust_layer``）。有调整轨就一定走逐层画。

``needs_stack(task)``：这一摞是不是和老画法一样（全部视频在下、覆层按类型成块在上）。一样就走老代码
（逐字节不变，金样例不动）；不一样才按这一摞逐层画。
"""
from __future__ import annotations

OVERLAY_TYPES = ("image", "text", "subs")


def _tracks(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    return list(getattr(tl, "tracks", None) or []) if tl is not None else []


def _outputs(tr, task, solo_ids) -> bool:
    from cutora.model.track_guard import track_outputs
    try:
        return bool(track_outputs(tr, task=task, solo_ids=solo_ids))
    except Exception:
        return bool(getattr(tr, "visible", True))


def _group_ordered_tracks(task, tracks: list) -> list:
    """若某条调整轨上的调整图层与特定轨道（如主视频轨）打成了预合成组（group），
    将该调整轨在渲染栈中紧跟在同组最后一条成员轨道之后渲染，使其只作用于组内素材而不影响组外素材。"""
    if not isinstance(task, dict) or not tracks:
        return tracks
    try:
        from cutora.model import clip_groups as CG
        from cutora.render.adjust_layer import items_on_track
        tl = task.get("timeline")
        if tl is not None:
            tl._task = task
    except Exception:
        return tracks
    by_id = {str(getattr(tr, "id", "")): tr for tr in tracks}
    # 收集每个预合成组 gid 包含哪些非调整视觉轨道
    gid_tracks: dict[str, list] = {}
    for tr in tracks:
        if getattr(tr, "domain", "visual") != "visual" or getattr(tr, "type", None) == "adjust":
            continue
        for c in getattr(tr, "clips", None) or ():
            g = CG.gid_of(c)
            if g:
                gid_tracks.setdefault(g, []).append(tr)
    for lname in ("image_overlays", "text_overlays"):
        for ov in task.get(lname) or ():
            if not isinstance(ov, dict) or ov.get("type") == "adjust":
                continue
            g = CG.gid_of(ov)
            tr = by_id.get(str(ov.get("_track_id") or ""))
            if g and tr is not None:
                gid_tracks.setdefault(g, []).append(tr)
    if not gid_tracks:
        return tracks
    # 检查是否有调整轨属于某个有视觉成员的预合成组
    adj_target_after: dict = {}
    for tr in tracks:
        if getattr(tr, "type", None) != "adjust":
            continue
        items = items_on_track(task, tr)
        gids = {CG.gid_of(ov) for _i, ov in items if CG.gid_of(ov) in gid_tracks}
        if gids:
            member_set = {id(mt) for g in gids for mt in gid_tracks.get(g, ())}
            last_mt = None
            for cand in tracks:
                if id(cand) in member_set:
                    last_mt = cand
            if last_mt is not None:
                adj_target_after[id(tr)] = last_mt
    if not adj_target_after:
        return tracks
    base = [tr for tr in tracks if id(tr) not in adj_target_after]
    out = []
    for tr in base:
        out.append(tr)
        for orig_tr in tracks:
            if adj_target_after.get(id(orig_tr)) is tr:
                out.append(orig_tr)
    return out


def build_stack(task, solo_ids=None) -> list:
    """底→顶的视觉层列表（见模块说明）。"""
    from cutora.render.backdrop import is_backdrop_track
    tracks = _group_ordered_tracks(task, _tracks(task))
    entries: list = []
    backdrops: list = []
    has_subs_track = False
    for tr in tracks:
        if getattr(tr, "domain", "visual") != "visual":
            continue
        tt = getattr(tr, "type", None)
        if tt == "subs":
            has_subs_track = True
        if tt == "video":
            if not (getattr(tr, "clips", None) or []) or not _outputs(tr, task, solo_ids):
                continue
            if is_backdrop_track(tr):
                backdrops.append(tr)
                continue
            # 严格按时间线顺序分层：当两个分屏视频轨之间夹了调整图层/文字/图片轨时，
            # 绝不把后面的分屏视频轨强行拉回第一段视频带，从而保证夹在中间的调整图层只作用于下层视频而不模糊上层分屏素材。
            if entries and entries[-1][0] == "video":
                entries[-1][1].append(tr)
            else:
                entries.append(("video", [tr]))
            continue
        if tt in OVERLAY_TYPES:
            if not _outputs(tr, task, solo_ids):
                continue
            entries.append(("overlay", tr))
            continue
        if tt == "adjust":
            if not _outputs(tr, task, solo_ids):
                continue
            from cutora.render.adjust_layer import items_on_track
            if items_on_track(task, tr):
                entries.append(("adjust", tr))
    if backdrops:
        if entries and entries[0][0] == "video":
            entries[0] = ("video", backdrops + list(entries[0][1]))
        else:
            entries.insert(0, ("video", list(backdrops)))
    if not has_subs_track and isinstance(task, dict) and (task.get("segments") or []):
        entries.append(("subs_orphan", None))
    return entries


def video_bands(stack) -> list:
    """这一摞里每一段视频轨（底→顶）。"""
    return [list(e[1]) for e in stack if e[0] == "video"]


def needs_stack(task, solo_ids=None, stack=None) -> bool:
    """True = 有视频压在图片 / 文字 / 字幕上面，或者覆层类型交错（文本 / 图片 / 文本），老画法画不对。"""
    st = stack if stack is not None else build_stack(task, solo_ids)
    if any(kind == "adjust" for kind, _p in st):
        return True                     # 调整图层要拿到它下面已经合成好的画面
    seen_overlay = False
    types_done: list = []
    for kind, payload in st:
        if kind == "video":
            if seen_overlay:
                return True
            continue
        seen_overlay = True
        t = "subs" if kind == "subs_orphan" else getattr(payload, "type", "")
        if types_done and types_done[-1] == t:
            continue
        if t in types_done:
            return True                 # 同一类型被别的类型隔开了
        types_done.append(t)
    return False


def stack_signature(task, solo_ids=None) -> tuple:
    """这一摞的形状（给缓存 / 监视器签名用）。"""
    out = []
    for kind, payload in build_stack(task, solo_ids):
        if kind == "video":
            out.append(("v",) + tuple(str(getattr(t, "id", "")) for t in payload))
        elif kind == "overlay":
            out.append(("o", str(getattr(payload, "id", ""))))
        elif kind == "adjust":
            out.append(("a", str(getattr(payload, "id", ""))))
        else:
            out.append(("s",))
    return tuple(out)


def paint_overlay_entry(painter, task, kind, payload, w, h, t, *, hit_recorder=None, backdrop=None):
    """画这一摞里的一层覆层（一条图片 / 文本 / 字幕轨，或没有轨道的字幕）。"""
    from cutora.render.frame_renderer import FrameRenderer
    if kind == "adjust":
        from cutora.render import adjust_layer as AL
        AL.apply_on_painter(painter, task, payload, t, w, h)
        if hit_recorder is not None:
            hit_recorder.extend(AL.hit_records(task, payload, t))
        return
    if kind == "subs_orphan":
        FrameRenderer.draw_layers(painter, task, w, h, t, hit_recorder,
                                  render_order=["subs"], backdrop=backdrop)
        return
    tt = getattr(payload, "type", "")
    FrameRenderer.draw_layers(painter, task, w, h, t, hit_recorder,
                              render_order=[tt], backdrop=backdrop, only_track=payload)


def paint_stack(painter, task, w, h, t, paint_video_band, *, stack=None, hit_recorder=None,
                backdrop=None, backdrop_after_first_band=None):
    """按这一摞从底到顶画。paint_video_band(painter, tracks, band_index) 画一段视频轨。

    backdrop_after_first_band：给毛玻璃字幕用的底图要不要在第一段视频画完后取（callable(painter) -> QImage）。"""
    st = stack if stack is not None else build_stack(task)
    vi = 0
    bd = backdrop
    for kind, payload in st:
        if kind == "video":
            paint_video_band(painter, payload, vi)
            if vi == 0 and bd is None and callable(backdrop_after_first_band):
                try:
                    bd = backdrop_after_first_band(painter)
                except Exception:
                    bd = None
            vi += 1
            continue
        paint_overlay_entry(painter, task, kind, payload, w, h, t, hit_recorder=hit_recorder, backdrop=bd)
    return bd


def place_new_video_track(tl, track) -> None:
    """新视频轨放在最上面那条视频轨之后（叠在视频上、但不盖住图片 / 文字 / 字幕）。

    Timeline.add_track 会把它放到视觉域最后 = 叠在所有字幕文字上面；以前覆层永远置顶看不出问题，
    统一叠压之后新加的 B-roll / 连线视频会把字幕盖住。"""
    tracks = getattr(tl, "tracks", None)
    if tracks is None:
        return
    if track in tracks:
        tracks.remove(track)
    last_video = -1
    first_audio = len(tracks)
    for i, t in enumerate(tracks):
        if getattr(t, "domain", "visual") == "audio":
            first_audio = min(first_audio, i)
            continue
        if getattr(t, "type", None) == "video":
            last_video = i
    if last_video >= 0:
        tracks.insert(last_video + 1, track)
    else:
        # 没有视频轨：放到视觉域最前（最底）
        first_visual = next((i for i, t in enumerate(tracks) if getattr(t, "domain", "visual") != "audio"), first_audio)
        tracks.insert(min(first_visual, first_audio), track)


Z_FLAG = "z_order_v2"


def migrate_old_order(task) -> bool:
    """老工程（没有 z_order_v2 标记）：老渲染器里视频一律在覆层下面 —— 把排在覆层后面的视频轨挪到
    第一条覆层轨之前（保持各自相对顺序），打开后画面和以前一样。返回改没改。只做一次（打上标记）。"""
    if not isinstance(task, dict) or task.get(Z_FLAG):
        return False
    tl = task.get("timeline")
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    task[Z_FLAG] = True
    if not tracks:
        return False
    visual = [t for t in tracks if getattr(t, "domain", "visual") != "audio"]
    first_overlay = next((i for i, t in enumerate(visual) if getattr(t, "type", None) in OVERLAY_TYPES), None)
    if first_overlay is None:
        return False
    late_videos = [t for t in visual[first_overlay:] if getattr(t, "type", None) == "video"]
    if not late_videos:
        return False
    keep = [t for t in visual if t not in late_videos]
    new_visual = keep[:first_overlay] + late_videos + keep[first_overlay:]
    audio = [t for t in tracks if getattr(t, "domain", "visual") == "audio"]
    tracks[:] = new_visual + audio
    return True


# ───────────────────────────── Q178 视频带里的混合模式 ─────────────────────────────
#
# 预览把一段视频带先画成一张透明图再叠到下面的结果上。带里某条轨道用了「滤色 / 正片叠底…」时，
# 它在透明图上没有东西可以混合，叠上去就成了普通覆盖（Luca：星空视频滤色盖在调整图层上面，黑底整片盖住）。
# 这种带（不是最底下那段）就拆成一条轨道一张图，叠的时候各自按自己的混合模式算。导出本来就逐条直接画在
# 画布上，不受影响；预览和导出因此一致。

class BandLayers(list):
    """一段视频带拆成的几层：[(QImage, 混合模式名), …]（下 → 上）。"""


def track_blend_at(track, t: float) -> str:
    """这条视频轨在 t 时刻和下面叠的混合模式：轨道合成（离屏层）的模式，否则正在播的片段的模式。"""
    from cutora.render.track_transform import track_needs_layer, track_props_of
    try:
        if track_needs_layer(track):
            return str(track_props_of(track).get("blend_mode") or "normal")
        from cutora.render.render_worker import RenderWorker
        for c in RenderWorker._find_active_clips_in_track_export(track, t) or []:
            m = getattr(c, "blend_mode", None) or getattr(track, "blend_mode", None) or "normal"
            if str(m) != "normal":
                return str(m)
    except Exception:
        return "normal"
    return "normal"


def band_valid(band) -> bool:
    if band is None:
        return False
    if isinstance(band, BandLayers):
        return any(img is not None and not img.isNull() for img, _m in band)
    return not band.isNull()


def band_copy(band):
    if isinstance(band, BandLayers):
        return BandLayers((img.copy(), m) for img, m in band if img is not None)
    return band.copy() if band is not None else None


def draw_band(painter, band, rect) -> None:
    """把一段视频带画到 rect（逻辑坐标）。拆开的带每层按自己的混合模式叠。"""
    from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL, draw_luma_image, is_luma_matte
    if band is None:
        return
    layers = band if isinstance(band, BandLayers) else [(band, "normal")]
    for img, mode in layers:
        if img is None or img.isNull():
            continue
        if is_luma_matte(mode):
            draw_luma_image(painter, img, mode, rect)
            continue
        painter.save()
        try:
            from PySide6.QtGui import QPainter as _QP
            painter.setRenderHint(_QP.RenderHint.SmoothPixmapTransform, True)
            if mode and mode != "normal":
                painter.setCompositionMode(BLEND_MODES.get(mode, _BLEND_NORMAL))
            if img.width() == int(rect.width()) and img.height() == int(rect.height()) \
                    and rect.x() == 0 and rect.y() == 0:
                painter.drawImage(0, 0, img)
            else:
                painter.drawImage(rect, img)
        finally:
            painter.restore()
