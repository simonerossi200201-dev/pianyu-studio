# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""连线导入：把外部视频按连线加到媒体库视频各自的时间线上。

一条连线 = 「把这条外部视频，作为一条**新的视频轨道**，加到被连的那条
媒体库视频的时间线上」。同一条媒体库视频连了几条外部视频，就新建几条轨道。

这个模块不碰界面。界面在 `cutora/ui/link_import_dialog.py`。
"""
from __future__ import annotations

import os

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")


def list_videos(folder: str) -> list[str]:
    """一个文件夹下一层的视频，自然序。跳过自产目录。"""
    if not folder or not os.path.isdir(folder):
        return []
    skip = {"_stitched", "_scatter", "_invalid"}
    out = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
            continue
        if os.path.basename(os.path.dirname(path)) in skip:
            continue
        out.append(path)
    out.sort(key=_natural_key)
    return out


def _natural_key(path: str):
    import re
    base = os.path.basename(path or "")
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", base)]


def resolve_sources(entries) -> list[str]:
    """文件夹 / 单个视频混着给，摊平成视频列表，按第一次出现去重。"""
    out, seen = [], set()

    def _add(path):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for raw in entries or []:
        item = str(raw or "").strip().strip('"')
        if not item:
            continue
        if os.path.isdir(item):
            for v in list_videos(item):
                _add(v)
        elif (os.path.isfile(item)
              and os.path.splitext(item)[1].lower() in VIDEO_EXTS):
            _add(item)
    return out


def auto_pairs(n_left: int, n_right: int) -> list[tuple[int, int]]:
    """默认连法：从上到下一一对应，多出来的一边留空。"""
    return [(i, i) for i in range(max(0, min(int(n_left), int(n_right))))]


def plan_from_links(links, tasks, sources) -> list[dict]:
    """把 [(左下标, 右下标)] 变成一条条「要做什么」。

    同一条任务下的多条连线按右下标排序，决定新建轨道的先后 —— 界面上
    从上到下连的顺序，就是轨道从下到上叠的顺序，所见即所得。
    """
    plan = {}
    for li, ri in sorted(links or []):
        if not (0 <= li < len(tasks or [])) or not (0 <= ri < len(sources or [])):
            continue
        plan.setdefault(li, []).append(sources[ri])
    return [{"task_index": li, "paths": paths}
            for li, paths in sorted(plan.items())]


def main_video_track(tl):
    """原视频那条轨：优先 is_main_video，退而求其次是最靠下的视频轨。"""
    from cutora.render.backdrop import is_backdrop_track
    vids = [t for t in (getattr(tl, "tracks", []) or [])
            if getattr(t, "type", "") == "video" and not is_backdrop_track(t)]
    for t in vids:
        if getattr(t, "is_main_video", False):
            return t
    return vids[0] if vids else None


def _free_cell(tl, cell_id, keep=None) -> None:
    """把这个格子从别的轨道上摘下来 —— set_track_cell 撞到占用会直接抛。"""
    for tr in getattr(tl, "tracks", []) or []:
        if tr is keep:
            continue
        if str(getattr(tr, "split_cell", None) or "") == str(cell_id):
            tr.split_cell = None


def _row_groups(row) -> list:
    """老计划 {"paths": [...]}（一段一轨）和新计划 {"groups": [[...], ...]}（一组一轨）都认。"""
    if isinstance(row.get("groups"), list):
        return [list(g) for g in row["groups"] if g]
    return [[p] for p in (row.get("paths") or [])]


def _place_group(tl, paths, *, host_dur, loop, match_duration, mute, name="", color=None, log=None, task=None):
    """给一组片段新建一条轨道，按顺序首尾相接放进去。多段就打成预合成组（锁住）。

    * loop：单段 = 片段自己循环（老行为）；多段 = 组开「循环铺满」，按整组时长续接到视频结尾。
    * match_duration：单段照老行为裁到视频时长；多段不循环时，超出视频时长的部分裁掉。
    返回 (track, [clip, …])。"""
    from cutora.model import clip_groups as CG
    from cutora.model.timeline_model import Clip, Track
    from cutora.batch.script_broll_scatter import _add_source, probe_duration

    durs = []
    for path in paths:
        dur = probe_duration(path)
        if dur <= 0:
            raise ValueError("读不到时长：%s" % path)
        durs.append(float(dur))
    track = Track("video")
    track.is_main_video = False
    tl.add_track(track)
    clips = []
    cursor = 0.0
    single = len(paths) == 1
    for path, dur in zip(paths, durs):
        sid = _add_source(tl, path, dur, has_audio=not mute)
        use = dur
        if single and match_duration and host_dur > 0:
            use = host_dur
        clip = Clip(sid, 0.0, float(min(dur, use) if (single and not loop) else dur), cursor)
        clip.fit_mode = "fill"
        clip.blend_mode = "normal"
        clip.x = 0.0
        clip.y = 0.0
        clip.scale = 1.0
        clip.rotation = 0.0
        clip.speed = 1.0
        clip.is_main = False
        if single:
            clip.loop = bool(loop)
            clip.match_duration = bool(match_duration)
            if match_duration and host_dur > 0:
                clip._match_target_dur = host_dur
        else:
            clip.loop = False
            clip.match_duration = False
        track.clips.append(clip)
        clips.append(clip)
        cursor += float(clip.effective_duration)
        if log:
            log("    %s  (%.2fs)" % (os.path.basename(path), dur))
    if not single:
        if match_duration and not loop and host_dur > 0:
            keep = []
            for c in clips:
                if c.start_t >= host_dur - 1e-6:
                    track.clips.remove(c)
                    continue
                end = c.start_t + c.effective_duration
                if end > host_dur:
                    c.out_t = c.in_t + (host_dur - c.start_t) * max(0.01, c.speed)
                keep.append(c)
            clips = keep
        if clips:
            CG.create(tl, clips, name=str(name or ""), color=color or None, locked=True, loop_fill=bool(loop))
            if loop and host_dur > 0:
                CG.resync(tl, host_dur)
    return track, clips


def pick_split_layout(layout, n_groups: int, auto_three: bool = True) -> str:
    """根据连入的外部视频组数自动匹配一对多分屏（主视频占最大画面，外部视频依次排布，无数量上限）。"""
    from cutora.batch.split_assign import SPLIT_DEFAULT_LAYOUT
    from cutora.render.split_layout import parse_1_to_n
    lay = layout or SPLIT_DEFAULT_LAYOUT
    n = int(n_groups)
    if not auto_three or n < 1:
        return lay
    if lay == "top1_bottom_auto":
        return "top_bottom" if n == 1 else ("top1_bottom%d" % n)
    if lay == "left1_right_auto":
        return "left_right" if n == 1 else ("left1_right%d" % n)
    parsed = parse_1_to_n(lay)
    if parsed is not None:
        mode, _old_n = parsed
        if n == 1:
            return "left_right" if mode == "left1_right" else "top_bottom"
        return "%s%d" % (mode, n)
    if n >= 2 and lay == SPLIT_DEFAULT_LAYOUT:
        return "top1_bottom%d" % n
    if n >= 3 and lay in ("left_right", "left_right_fit"):
        return "left1_right%d" % n
    return lay


def apply_plan(plan, tasks, *, host=None, loop=False, match_duration=True,
               mute=False, split_b=False, log=None, split_layout=None,
               split_border=None, split_feather=None, split_backdrop=None,
               split_cells=None, auto_three=True) -> dict:
    """按计划建轨道、放片段。

    计划的每一行 {"task_index": i, "groups": [[path, …], …]}（老格式 {"paths": [...]} 也认）：
    一组一条新轨道；多段的组按顺序首尾相接并打成**预合成组**（见 model/clip_groups.py）。

    ``split_layout`` 给了就顺便开分屏：原视频那条轨进第一格，新建的轨道依次进后面的格子
    （``split_cells`` 可指定格子顺序）。一条媒体正好两组、布局还是默认上下 → 自动「上 1 下 2」。
    格子不够的轨道不分格子，计在 ``split_skipped``。``split_b`` 是老参数 = 上下。

    返回 {"tasks":n, "tracks":n, "clips":n, "groups":n, "split_skipped":n}。
    """
    from cutora.batch.script_broll_scatter import _timeline_of, task_duration

    if split_b and not split_layout:
        split_layout = "top_bottom"
    split_b = bool(split_layout)
    n_tasks = n_tracks = n_clips = n_groups = n_split_skipped = 0
    for row in plan or []:
        task = tasks[row["task_index"]]
        tl = _timeline_of(task, host)
        if tl is None:
            raise ValueError("读不到时间线：%s"
                             % os.path.basename(task.get("video") or "?"))
        host_dur = float(task_duration(task, host) or 0.0)
        main_tr = main_video_track(tl) if split_b else None
        groups = _row_groups(row)
        names = list(row.get("names") or [])
        colors = list(row.get("colors") or [])
        new_tracks = []
        for gi, paths in enumerate(groups):
            if log:
                log("  %s  ← 新轨道%s" % (
                    os.path.basename(task.get("video") or "?"),
                    "（预合成 %d 段）" % len(paths) if len(paths) > 1 else ""))
            track, clips = _place_group(
                tl, paths, host_dur=host_dur, loop=loop, match_duration=match_duration,
                mute=mute, name=names[gi] if gi < len(names) and len(paths) > 1 else "",
                color=colors[gi] if gi < len(colors) else None, log=log, task=task)
            new_tracks.append(track)
            n_tracks += 1
            n_clips += len(clips)
            n_groups += int(len(paths) > 1)
        if groups:
            n_tasks += 1
        if split_b and new_tracks:
            from cutora.batch.split_assign import assign_split, layout_label
            lay = pick_split_layout(split_layout, len(new_tracks), auto_three)
            cells = None
            if split_cells and lay == split_layout:
                from cutora.batch.split_assign import layout_cells
                allc = layout_cells(lay)
                cells = [c for c in split_cells if c in allc] + [c for c in allc if c not in split_cells]
            res = assign_split(task, lay, [main_tr] + new_tracks,
                               border=split_border, feather_px=split_feather,
                               cells=cells, backdrop=split_backdrop)
            n_split_skipped += int(res["skipped"])
            if log:
                log("  %s  分屏（%s）：%s" % (
                    os.path.basename(task.get("video") or "?"),
                    layout_label(res["layout"]),
                    "、".join("%s 屏" % c for c in res["assigned"])))
    return {"tasks": n_tasks, "tracks": n_tracks, "clips": n_clips, "groups": n_groups,
            "split_skipped": n_split_skipped}
