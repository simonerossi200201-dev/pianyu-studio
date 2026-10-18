# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 预合成（原位分组）。没有 Qt。

时间线上选几条片段（同一条轨道或跨轨道）→ Ctrl+G 打成一组。片段还在原来的轨道上，
只是每条身上多了一个 ``group`` 字典（同一组里每条一份、内容一样）::

    clip.group = {"id": "pg3f…", "name": "", "color": "#F2B33D",
                  "locked": True, "loop_fill": False, "repeat": False}

* ``locked``：锁住组内编辑（默认）。时间线上点一条 = 选整组、拖一条 = 整组一起走；
  检查器里改一条的外观参数，整组一起变（``sync_props``）。解锁以后各条可以单独改。
* ``loop_fill``：以整组的时长为一个循环单位，自动复制整组续接到视频结尾
  （「循环播放匹配视频时长」）。复制出来的叫 **重复份**（``repeat=True``，
  ``repeat_of`` = 来源片段的 uid），由 ``resync`` 维护：组内一改就全部重算，用户不能单独编辑它们。
* ``uid``：每条片段一个稳定 id（撤销深拷贝后不变），重复份靠它找来源。

渲染 / 导出 / 中央监视器完全不认识「组」：重复份就是普通片段，所以零改动、零风险。
"""
from __future__ import annotations

import copy
import uuid

# 组颜色：挑出来的 8 个柔和色，深色主题上够亮、互相分得开。按组的创建顺序轮着用。
GROUP_COLORS = (
    "#F2B33D",   # 金
    "#4FC3F7",   # 天蓝
    "#81C784",   # 草绿
    "#CE93D8",   # 紫
    "#FFB74D",   # 橙
    "#4DB6AC",   # 青
    "#F48FB1",   # 粉
    "#AED581",   # 黄绿
)

# 锁住的组里改一条，这些键跟着同步到整组（外观 / 变换 / 裁切 / 渐隐 / 调色 / 抠像）。
# 时间（start_t / in_t / out_t）、变速、循环这些不同步：那是每条自己的位置。
GROUP_SYNC_KEYS = (
    "fit_mode", "size_mode", "x", "y", "scale", "scale_y", "lock_aspect", "rotation",
    "flip_x", "flip_y", "opacity", "blend_mode", "bg_fill",
    "crop_left", "crop_right", "crop_top", "crop_bottom", "feather",
    "fade_top", "fade_bottom", "fade_left", "fade_right",
    "transition_preset", "transition_duration",
    "grade", "keyer", "masks", "fx_graph", "label_color",
    "gain_db", "pitch_semitones", "fade_in_dur", "fade_out_dur",
    "spatial_enabled", "spatial_amount",
)


# ───────────────────────────── 读 ─────────────────────────────

def new_gid() -> str:
    return "pg" + uuid.uuid4().hex[:10]


def ensure_uid(clip) -> str:
    if isinstance(clip, dict):
        uid = clip.get("uid")
        if not uid:
            uid = uuid.uuid4().hex
            clip["uid"] = uid
        return str(uid)
    uid = getattr(clip, "uid", None)
    if not uid:
        uid = uuid.uuid4().hex
        try:
            clip.uid = uid
        except Exception:
            pass
    return str(uid)


def group_of(clip) -> dict | None:
    g = clip.get("group") if isinstance(clip, dict) else getattr(clip, "group", None)
    return g if isinstance(g, dict) and g.get("id") else None


def gid_of(clip) -> str:
    g = group_of(clip)
    return str(g["id"]) if g else ""


def is_repeat(clip) -> bool:
    g = group_of(clip)
    return bool(g and g.get("repeat"))


def is_locked_member(clip) -> bool:
    g = group_of(clip)
    return bool(g and g.get("locked", True))


def _item_start(c) -> float:
    if isinstance(c, dict):
        return float(c.get("start", 0.0) or 0.0)
    return float(getattr(c, "start_t", 0.0) or 0.0)


def _item_dur(c) -> float:
    if isinstance(c, dict):
        s = float(c.get("start", 0.0) or 0.0)
        e = float(c.get("end", s) or s)
        return max(0.0, e - s)
    return float(getattr(c, "effective_duration", 0.0) or 0.0)


def _overlay_track(tl, ov: dict, list_name: str):
    tracks = list(getattr(tl, "tracks", []) or [])
    tid = ov.get("_track_id")
    if tid is not None:
        for tr in tracks:
            if getattr(tr, "id", None) == tid or str(getattr(tr, "id", "")) == str(tid):
                return tr
    want_type = "text" if list_name == "text_overlays" else (
        "adjust" if ov.get("type") == "adjust" else "image"
    )
    for tr in tracks:
        if getattr(tr, "type", None) == want_type:
            return tr
    return None


def iter_clips(tl):
    for tr in getattr(tl, "tracks", []) or []:
        for c in list(getattr(tr, "clips", []) or []):
            yield tr, c
    task = getattr(tl, "_task", None)
    if isinstance(task, dict):
        for list_name in ("image_overlays", "text_overlays"):
            for ov in list(task.get(list_name) or []):
                if isinstance(ov, dict):
                    yield _overlay_track(tl, ov, list_name), ov


def members(tl, gid: str, *, repeats: bool = True) -> list:
    """[(track, clip_or_ov)]，按 start_t 排序。repeats=False 只给真身。"""
    out = [(tr, c) for tr, c in iter_clips(tl)
           if gid_of(c) == str(gid) and (repeats or not is_repeat(c))]
    out.sort(key=lambda tc: (_item_start(tc[1]), id(tc[0])))
    return out


def groups(tl) -> dict:
    """{gid: 组元数据}（取第一条真身上的那份）。"""
    out = {}
    for _tr, c in iter_clips(tl):
        g = group_of(c)
        if g and g["id"] not in out:
            out[g["id"]] = dict(g, repeat=False)
    return out


def meta(tl, gid: str) -> dict | None:
    for _tr, c in iter_clips(tl):
        g = group_of(c)
        if g and g["id"] == str(gid):
            return dict(g, repeat=False)
    return None


def span(tl, gid: str) -> tuple[float, float]:
    """真身覆盖的时间范围（时间线秒）。"""
    lo, hi = None, 0.0
    for _tr, c in members(tl, gid, repeats=False):
        s = _item_start(c)
        d = _item_dur(c)
        lo = s if lo is None else min(lo, s)
        hi = max(hi, s + d)
    return (lo or 0.0, hi) if lo is not None else (0.0, 0.0)


def display_name(g: dict, index: int | None = None) -> str:
    name = str((g or {}).get("name") or "").strip()
    if name:
        return name
    return "预合成" + ("" if index is None else " %d" % (int(index) + 1))


def _next_color(tl) -> str:
    used = [g.get("color") for g in groups(tl).values()]
    for col in GROUP_COLORS:
        if col not in used:
            return col
    return GROUP_COLORS[len(used) % len(GROUP_COLORS)]


# ───────────────────────────── 写 ─────────────────────────────

def _set(clip, g: dict | None) -> None:
    if isinstance(clip, dict):
        if g:
            clip["group"] = dict(g)
        else:
            clip.pop("group", None)
    else:
        clip.group = dict(g) if g else {}


def create(tl, clips, *, name: str = "", color: str | None = None,
           locked: bool = True, loop_fill: bool = False) -> str:
    """把这些片段打成一组。已经在别的组里的先搬出来（那个组空了就自动没了）。"""
    real = [c for c in clips if c is not None and not is_repeat(c)]
    if not real:
        raise ValueError("先选中至少一条片段")
    detach(tl, real)
    g = {"id": new_gid(), "name": str(name or ""), "color": color or _next_color(tl),
         "locked": bool(locked), "loop_fill": bool(loop_fill), "repeat": False}
    for c in real:
        ensure_uid(c)
        _set(c, g)
    return g["id"]


def ungroup(tl, gid: str) -> int:
    """解除预合成：真身留下（去掉组标记），重复份删掉。"""
    n = 0
    for tr, c in members(tl, gid):
        if is_repeat(c):
            _remove(tr, c, tl=tl)
        else:
            _set(c, None)
            n += 1
    return n


def join(tl, gid: str, clips) -> int:
    g = meta(tl, gid)
    if g is None:
        raise ValueError("这个组不存在了")
    real = [c for c in clips if c is not None and not is_repeat(c) and gid_of(c) != gid]
    detach(tl, real)
    for c in real:
        ensure_uid(c)
        _set(c, g)
    return len(real)


def detach(tl, clips) -> int:
    """把这些片段从各自的组里分离出来。组里一条真身都不剩 → 它的重复份一起删。"""
    touched = set()
    n = 0
    for c in clips:
        if c is None or is_repeat(c):
            continue
        gid = gid_of(c)
        if gid:
            touched.add(gid)
            _set(c, None)
            n += 1
    for gid in touched:
        if not members(tl, gid, repeats=False):
            for tr, c in members(tl, gid):
                _remove(tr, c, tl=tl)
    return n


def set_meta(tl, gid: str, **patch) -> dict:
    """改组的名字 / 颜色 / 锁 / 循环铺满：同一组每条上的那份一起改。"""
    g = meta(tl, gid)
    if g is None:
        raise ValueError("这个组不存在了")
    for k, v in patch.items():
        if k == "name":
            g["name"] = str(v or "")
        elif k == "color":
            g["color"] = str(v or "") or g.get("color") or GROUP_COLORS[0]
        elif k in ("locked", "loop_fill"):
            g[k] = bool(v)
    for _tr, c in members(tl, gid):
        cur = group_of(c) or {}
        _set(c, dict(g, repeat=bool(cur.get("repeat")), repeat_of=cur.get("repeat_of")))
        if g.get("loop_fill") and not cur.get("repeat") and not isinstance(c, dict):
            # 整组循环铺满了，单条的「匹配时长」就得关：不然一条自己撑到结尾，组的周期就乱了
            c.match_duration = False
            c._match_target_dur = 0.0
    return g


def sync_props(tl, src_clip, keys=GROUP_SYNC_KEYS) -> int:
    """锁住的组：把 src_clip 身上的外观参数抄给同组其它每条（含重复份）。返回改了几条。"""
    if isinstance(src_clip, dict):
        return 0
    g = group_of(src_clip)
    if not g or not g.get("locked", True):
        return 0
    n = 0
    for _tr, c in members(tl, g["id"]):
        if c is src_clip or isinstance(c, dict):
            continue
        changed = False
        for k in keys:
            if not hasattr(src_clip, k):
                continue
            v = getattr(src_clip, k)
            if getattr(c, k, _MISSING) != v:
                setattr(c, k, copy.deepcopy(v))
                changed = True
        n += int(changed)
    return n


_MISSING = object()


def _remove(tr, c, tl=None) -> None:
    if isinstance(c, dict):
        task = getattr(tl, "_task", None) if tl is not None else None
        if isinstance(task, dict):
            for list_name in ("image_overlays", "text_overlays"):
                rows = task.get(list_name)
                if isinstance(rows, list):
                    for i, o in enumerate(rows):
                        if o is c:
                            del rows[i]
                            return
        return
    bag = getattr(tr, "clips", None)
    if bag is not None and c in bag:
        bag.remove(c)


# ───────────────────────────── 循环铺满（重复份） ─────────────────────────────

def _repeat_signature(tl, gid: str) -> tuple:
    rows = []
    for tr, c in members(tl, gid, repeats=False):
        if isinstance(c, dict):
            rows.append((str(getattr(tr, "id", "")), ensure_uid(c),
                         round(_item_start(c), 4),
                         0.0,
                         round(_item_dur(c), 4),
                         1.0, False, 0.0))
        else:
            rows.append((str(getattr(tr, "id", "")), ensure_uid(c),
                         round(float(getattr(c, "start_t", 0.0) or 0.0), 4),
                         round(float(getattr(c, "in_t", 0.0) or 0.0), 4),
                         round(float(getattr(c, "out_t", 0.0) or 0.0), 4),
                         round(float(getattr(c, "speed", 1.0) or 1.0), 4),
                         bool(getattr(c, "loop", False)),
                         round(float(getattr(c, "_match_target_dur", 0.0) or 0.0), 4)))
    return tuple(rows)


def _get_rep_sig(c):
    return c.get("_repeat_sig") if isinstance(c, dict) else getattr(c, "_repeat_sig", None)


def resync(tl, target_dur: float) -> bool:
    """按每个组的 loop_fill 重算重复份。target_dur = 要铺到的时间线秒数（视频时长）。

    幂等：重复份的内容只由真身 + 目标时长决定，所以每次都删掉重建；没变化就不动。
    返回有没有改动。"""
    changed = False
    target = float(target_dur or 0.0)
    task = getattr(tl, "_task", None)
    for gid, g in groups(tl).items():
        reps = [(tr, c) for tr, c in members(tl, gid) if is_repeat(c)]
        want = []
        if g.get("loop_fill") and target > 0:
            lo, hi = span(tl, gid)
            period = hi - lo
            if period > 1e-3 and hi < target - 1e-3:
                k = 1
                while lo + k * period < target - 1e-3:
                    want.append(k)
                    k += 1
                    if k > 5000:
                        break
        sig = (_repeat_signature(tl, gid), tuple(want), round(target, 4))
        old_sig = None
        if reps:
            old_sig = _get_rep_sig(reps[0][1])
        if sig == old_sig and all(_get_rep_sig(c) == sig for _tr, c in reps):
            continue
        for tr, c in reps:
            _remove(tr, c, tl=tl)
        if not want:
            changed = changed or bool(reps)
            continue
        lo, hi = span(tl, gid)
        period = hi - lo
        for tr, c in members(tl, gid, repeats=False):
            base_uid = ensure_uid(c)
            if isinstance(c, dict):
                if not isinstance(task, dict):
                    continue
                list_name = "text_overlays" if any(o is c for o in (task.get("text_overlays") or [])) else "image_overlays"
                rows = task.setdefault(list_name, [])
                dur = _item_dur(c)
                for k in want:
                    ns = _item_start(c) + k * period
                    ne = min(target, ns + dur)
                    if ne - ns <= 1e-3:
                        continue
                    r = copy.deepcopy(c)
                    r["uid"] = uuid.uuid4().hex
                    r["start"] = ns
                    r["end"] = ne
                    r["group"] = dict(g, repeat=True, repeat_of=base_uid)
                    r["_repeat_sig"] = sig
                    rows.append(r)
                continue
            for k in want:
                r = c.clone()
                r.uid = uuid.uuid4().hex
                r.start_t = float(getattr(c, "start_t", 0.0) or 0.0) + k * period
                # 最后一份超出结尾的部分裁掉（照顾变速；循环单条就靠 _match_target_dur 收口）
                end = r.start_t + float(getattr(r, "effective_duration", 0.0) or 0.0)
                if end > target + 1e-6:
                    keep = max(0.0, target - r.start_t)
                    if keep <= 1e-3:
                        continue
                    if getattr(r, "match_duration", False) and float(getattr(r, "_match_target_dur", 0.0) or 0.0) > 0:
                        r._match_target_dur = keep
                    sp = max(0.01, float(getattr(r, "speed", 1.0) or 1.0))
                    r.out_t = min(float(r.out_t), float(r.in_t) + keep * sp)
                    if getattr(r, "match_duration", False):
                        r._match_target_dur = min(float(getattr(r, "_match_target_dur", 0.0) or 0.0), keep) or 0.0
                r.group = dict(g, repeat=True, repeat_of=base_uid)
                r._repeat_sig = sig
                tr.clips.append(r)
        changed = True
    return changed


def task_target_duration(task, host=None) -> float:
    try:
        from cutora.batch.script_broll_scatter import task_duration
        return float(task_duration(task, host) or 0.0)
    except Exception:
        try:
            return float((task or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            return 0.0


def resync_task(task, host=None) -> bool:
    """给界面用：工程里当前这条视频的时间线重算一遍重复份。"""
    tl = task.get("timeline") if isinstance(task, dict) else getattr(task, "timeline", None)
    if tl is not None and isinstance(task, dict):
        tl._task = task
    if tl is None or not any(group_of(c) for _tr, c in iter_clips(tl)):
        return False
    return resync(tl, task_target_duration(task, host))
