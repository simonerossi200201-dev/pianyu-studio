# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-G · 图层面板的数据层：一条视频里每一层的状态与可做的操作。

面板（cutora/ui/layer_panel.py）只画、只转发；这里不碰 Qt，能单独测。

一层 = 时间线上的一个视频 / 音频片段，或一个文本 / 图片覆层。每层有：
- 显隐 hidden（视频 / 图片 / 文本：不画；音频：不混进成片）
- 锁定 locked（画布 / 监视器上点不中、拖不动；面板里仍可改）
- 颜色标签 label_color（8 色，只是分类记号，不影响画面）
- 名字 name（空 = 自动名：文件名 / 文字内容）
- 不透明度 opacity（0–255）、混合模式 blend_mode

存法：覆层是 dict，直接写键；片段是 Clip 对象，写属性，并且这些键都在
project_schema.CLIP_KEYS 里，存工程 / 导出子进程都带着。老工程没有这些键 →
全部默认值 → 画面一个字节不变。

显示顺序：**上面的层在列表上面**（Photoshop / Resolve 的习惯）：覆层按轨道从上到下、
同一轨道里后画的在前；片段按轨道堆叠。
"""
from __future__ import annotations

from cutora.batch import layer_transfer as lt
from cutora.batch import monitor_model as mm

# 颜色标签：Roma 调色板里的低饱和色，面板左侧一条 3 px 色带 + 右键菜单色点
COLOR_LABELS = (
    ("", "无"),
    ("red", "赤"),
    ("orange", "橙"),
    ("yellow", "金"),
    ("green", "橄榄"),
    ("teal", "青铜"),
    ("blue", "群青"),
    ("purple", "紫"),
    ("gray", "石灰"),
)
LABEL_HEX = {
    "red": "#B4544A",
    "orange": "#C8913A",
    "yellow": "#C8A24A",
    "green": "#7D9B6A",
    "teal": "#8FA89B",
    "blue": "#5F7FA8",
    "purple": "#8C6E9E",
    "gray": "#8A8178",
}
KIND_ORDER = {"adjust": -1, "text": 0, "image": 1, "video": 2, "audio": 3, "bgm": 3, "sfx": 3}
AUDIO_KINDS = frozenset({"audio", "bgm", "sfx"})
_FLAG_KEYS = ("hidden", "locked")


# ───────────────────────────── 读 ─────────────────────────────

def resolve(task, locator):
    """locator → 覆层 dict 或 Clip 对象；找不到返回 None。"""
    if not isinstance(task, dict) or not isinstance(locator, dict):
        return None
    where = locator.get("where")
    if where == "folder":
        from cutora.batch.layer_folders import find
        return find(task, locator.get("id"))
    if where == "overlay":
        lst = task.get(locator.get("list")) or []
        i = int(locator.get("index", -1))
        return lst[i] if 0 <= i < len(lst) and isinstance(lst[i], dict) else None
    if where == "clip":
        tl = mm.timeline_of(task)
        for tr in getattr(tl, "tracks", None) or []:
            if str(getattr(tr, "id", "") or "") != str(locator.get("track_id") or ""):
                continue
            clips = list(getattr(tr, "clips", None) or [])
            i = int(locator.get("clip_index", -1))
            return clips[i] if 0 <= i < len(clips) else None
    return None


def locator_of(task, obj) -> dict | None:
    """对象 → 现在的定位器（挪动 / 删除之后索引会变，按身份重新找）。"""
    if not isinstance(task, dict) or obj is None:
        return None
    for name in mm.OVERLAY_LISTS:
        for i, ov in enumerate(task.get(name) or []):
            if ov is obj:
                return {"where": "overlay", "list": name, "index": i}
    tl = mm.timeline_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        for i, c in enumerate(getattr(tr, "clips", None) or []):
            if c is obj:
                return {"where": "clip", "track_id": str(getattr(tr, "id", "") or ""), "clip_index": i}
    return None


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _set(obj, key, value) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


def state_of(obj) -> dict:
    """一层的面板状态（缺的键按默认）。"""
    try:
        opacity = float(_get(obj, "opacity", 255) if _get(obj, "opacity", 255) is not None else 255)
    except (TypeError, ValueError):
        opacity = 255.0
    return {
        "hidden": bool(_get(obj, "hidden", False)),
        "locked": bool(_get(obj, "locked", False)),
        "color": str(_get(obj, "label_color", "") or ""),
        "name": str(_get(obj, "name", "") or ""),
        "opacity": max(0.0, min(255.0, opacity)),
        "blend": str(_get(obj, "blend_mode", "normal") or "normal"),
    }


def rows(task) -> list[dict]:
    """面板要显示的全部层，上面的层在前。每行 = layer_transfer 的行 + 状态。"""
    base = lt.list_layers(task)
    out = []
    for layer in base:
        obj = resolve(task, layer.get("locator"))
        if obj is None:
            continue
        row = dict(layer)
        row.update(state_of(obj))
        row["is_audio"] = row["kind"] in AUDIO_KINDS
        row["title"] = row["name"] or row["label"]
        # Q154：预合成组 —— 没打颜色标签的用组色；重复份标 ↻
        grp = getattr(obj, "group", None) if not isinstance(obj, dict) else obj.get("group")
        if isinstance(grp, dict) and grp.get("id"):
            row["group"] = str(grp.get("id"))
            if not row["color"] and grp.get("color"):
                row["color"] = str(grp["color"])
            gname = str(grp.get("name") or "").strip() or "预合成"
            row["title"] = ("↻ " if grp.get("repeat") else "") + row["title"] + "  · " + gname
        out.append(row)
    # Q177：面板顺序 = 时间线上的叠压顺序（上面的轨道排在前面；同一条轨道里后画的在前）；声音放最后。
    tl = mm.timeline_of(task)
    order = {id(t): i for i, t in enumerate(getattr(tl, "tracks", None) or [])}

    def _key(r):
        loc = r.get("locator") or {}
        idx = int(loc.get("index", loc.get("clip_index", 0)) or 0)
        tr = track_of_row(task, r)
        if tr is not None:
            pos = order.get(id(tr), -1)
        else:
            # 老工程：覆层没有对应的轨道 —— 老画法里它们按类型叠在全部视频上面（图片 < 调整 < 文字）
            pos = len(order) + {"image": 0, "adjust": 1, "text": 2}.get(r["kind"], 0)
        return (1 if r["kind"] in AUDIO_KINDS else 0, -pos, -idx)
    out.sort(key=_key)
    from cutora.batch.layer_folders import decorate
    return decorate(task, out)


# ───────────────────────────── Q177 叠压顺序 = 时间线轨道顺序 ─────────────────────────────

def track_of_row(task, row):
    """这一行所在的时间线轨道（覆层：_track_id，老覆层没写的算第一条同类轨）。"""
    loc = (row or {}).get("locator") or {}
    tl = mm.timeline_of(task)
    tracks = list(getattr(tl, "tracks", None) or [])
    if loc.get("where") == "clip":
        want = str(loc.get("track_id") or "")
        return next((t for t in tracks if str(getattr(t, "id", "") or "") == want), None)
    ov = resolve(task, loc)
    return track_of_overlay(task, ov)


def track_of_overlay(task, ov):
    if not isinstance(ov, dict):
        return None
    tl = mm.timeline_of(task)
    tracks = list(getattr(tl, "tracks", None) or [])
    tid = ov.get("_track_id")
    if tid is not None:
        hit = next((t for t in tracks if getattr(t, "id", None) == tid), None)
        if hit is not None:
            return hit
    kind = mm.overlay_kind(ov)
    return next((t for t in tracks if getattr(t, "type", "") == kind), None)


def stack_tracks(task) -> list:
    """能上下挪的画面轨道（下 → 上）：不含声音轨、不含永远垫底的整幅背景轨。"""
    from cutora.render.backdrop import is_backdrop_track
    tl = mm.timeline_of(task)
    return [t for t in getattr(tl, "tracks", None) or []
            if str(getattr(t, "domain", "visual") or "visual") != "audio"
            and str(getattr(t, "type", "") or "") not in AUDIO_KINDS and not is_backdrop_track(t)]


def move_track(task, track, how: str) -> bool:
    """how：up 上移一层 / down 下移一层 / top 置顶 / bottom 置底。只在画面轨之间挪。"""
    tl = mm.timeline_of(task)
    tracks = getattr(tl, "tracks", None)
    vis = stack_tracks(task)
    if tracks is None or track not in vis or len(vis) < 2:
        return False
    i = vis.index(track)
    if how == "up":
        if i >= len(vis) - 1:
            return False
        a, b = tracks.index(track), tracks.index(vis[i + 1])
        tracks[a], tracks[b] = tracks[b], tracks[a]
    elif how == "down":
        if i <= 0:
            return False
        a, b = tracks.index(track), tracks.index(vis[i - 1])
        tracks[a], tracks[b] = tracks[b], tracks[a]
    elif how == "top":
        if i == len(vis) - 1:
            return False
        tracks.remove(track)
        tracks.insert(tracks.index(vis[-1]) + 1, track)
    elif how == "bottom":
        if i == 0:
            return False
        tracks.remove(track)
        tracks.insert(tracks.index(vis[0]), track)
    else:
        return False
    task["z_order_v2"] = True            # 用户自己排过：打开老工程时不再按类型重排
    return True


def move_track_above(task, track, anchor=None) -> bool:
    """把 track 放到 anchor 正上方（anchor=None → 最底下）。"""
    tl = mm.timeline_of(task)
    tracks = getattr(tl, "tracks", None)
    vis = stack_tracks(task)
    if tracks is None or track not in vis or track is anchor:
        return False
    if anchor is not None and anchor not in vis:
        return False
    before = list(tracks)
    tracks.remove(track)
    if anchor is None:
        rest = [t for t in vis if t is not track]
        pos = tracks.index(rest[0]) if rest else len(tracks)
    else:
        pos = tracks.index(anchor) + 1
    tracks.insert(pos, track)
    if tracks == before:
        return False
    task["z_order_v2"] = True
    return True


def filter_rows(all_rows, text: str = "", kind: str = "") -> list[dict]:
    """搜索框 + 类型过滤。text 匹配标题 / 自动名 / 类型词，不分大小写。"""
    q = str(text or "").strip().lower()
    want = str(kind or "")
    out = []
    for r in all_rows:
        if want and want != "all":
            if want == "audio" and not r.get("is_audio"):
                continue
            if want != "audio" and r.get("kind") != want:
                continue
        if q:
            hay = " ".join((r.get("title") or "", r.get("label") or "",
                            lt.KIND_WORD.get(r.get("kind"), ""))).lower()
            if q not in hay:
                continue
        out.append(r)
    return out


# ───────────────────────────── 写（都在 monitor 的 History 里撤销）─────────────────────────────

def set_flag(task, locator, key: str, on: bool) -> bool:
    if key not in _FLAG_KEYS:
        raise ValueError("unknown layer flag %r" % key)
    obj = resolve(task, locator)
    if obj is None:
        return False
    on = bool(on)
    if bool(_get(obj, key, False)) == on:
        return False
    if on:
        _set(obj, key, True)
    elif isinstance(obj, dict):
        obj.pop(key, None)          # 关掉就不留键，老工程存回去还是老样子
    else:
        setattr(obj, key, False)
    return True


def set_color(task, locator, color: str) -> bool:
    obj = resolve(task, locator)
    if obj is None:
        return False
    color = str(color or "")
    if color and color not in LABEL_HEX:
        return False
    if str(_get(obj, "label_color", "") or "") == color:
        return False
    if color:
        _set(obj, "label_color", color)
    elif isinstance(obj, dict):
        obj.pop("label_color", None)
    else:
        setattr(obj, "label_color", "")
    return True


def rename(task, locator, name: str) -> bool:
    obj = resolve(task, locator)
    if obj is None:
        return False
    name = str(name or "").strip()
    if str(_get(obj, "name", "") or "") == name:
        return False
    if name:
        _set(obj, "name", name)
    elif isinstance(obj, dict):
        obj.pop("name", None)
    else:
        setattr(obj, "name", "")
    return True


def set_opacity(task, locator, value) -> bool:
    obj = resolve(task, locator)
    if obj is None:
        return False
    try:
        v = int(round(max(0.0, min(255.0, float(value)))))
    except (TypeError, ValueError):
        return False
    if int(round(state_of(obj)["opacity"])) == v:
        return False
    _set(obj, "opacity", v)
    return True


def set_blend(task, locator, mode: str) -> bool:
    obj = resolve(task, locator)
    if obj is None:
        return False
    mode = str(mode or "normal")
    changed = state_of(obj)["blend"] != mode
    _set(obj, "blend_mode", mode)
    # 视频片段的混合模式和时间线属性、轨道合成是同一份，图层面板改了剪辑页也要看得到。
    if isinstance(locator, dict) and locator.get("where") == "clip":
        tr = track_of_row(task, {"locator": locator})
        if tr is not None:
            if str(getattr(tr, "blend_mode", "normal") or "normal") != mode:
                changed = True
            tr.blend_mode = mode
            props = getattr(tr, "props", None)
            if isinstance(props, dict):
                if str(props.get("blend_mode") or "normal") != mode:
                    changed = True
                props["blend_mode"] = mode
    return changed


def solo(task, locator, all_rows=None) -> int:
    """只留这一层可见（同类里的其它层隐藏）；再按一次恢复全部。返回改了几层。"""
    rows_ = all_rows if all_rows is not None else rows(task)
    me = resolve(task, locator)
    if me is None:
        return 0
    others = [r for r in rows_ if resolve(task, r["locator"]) is not me]
    is_solo = (not state_of(me)["hidden"]) and others and all(r["hidden"] for r in others)
    n = 0
    if is_solo:
        for r in others:
            n += int(set_flag(task, r["locator"], "hidden", False))
        return n
    for r in others:
        n += int(set_flag(task, r["locator"], "hidden", True))
    n += int(set_flag(task, locator, "hidden", False))
    return n


def can_reorder(row: dict) -> bool:
    """Q177：画面图层都能拖上下（拖的是它所在的轨道，和时间线上一回事）；声音不参与叠压。"""
    if not row or row.get("kind") == "folder" or row.get("kind") in AUDIO_KINDS:
        return False
    return True


def group_layers(task, locators) -> int:
    """把选中的层收成一个文件夹。少于两层不编。默认不改画面。"""
    import uuid
    from cutora.batch.layer_folders import add_folder, member_key
    keys = []
    for loc in locators or []:
        if not isinstance(loc, dict) or loc.get("where") == "folder":
            continue
        obj = resolve(task, loc)
        if isinstance(obj, dict) and not obj.get("id"):
            obj["id"] = "ov_fld_" + uuid.uuid4().hex[:10]
        if obj is not None and not isinstance(obj, dict) and not getattr(obj, "uid", None):
            try:
                obj.uid = uuid.uuid4().hex
            except Exception:
                pass
        key = member_key(obj, loc)
        if key:
            keys.append(key)
    if len(keys) < 2:
        return 0
    add_folder(task, keys)
    return 1


def reorder_rows(task, rows: list, before: dict | None) -> int:
    """面板里把 rows 拖到 before 那一行上面（None = 最底）。

    同一条轨道里的两个覆层 → 只换它们在列表里的先后（同轨道内的上下）；
    不同轨道 → 挪轨道：拖动的那几条轨道依次放到目标那一行所在轨道的上面（保持它们之间原来的上下）。"""
    objs = [(r, resolve(task, r.get("locator"))) for r in rows or []]
    objs = [(r, o) for r, o in objs if o is not None and can_reorder(r)]
    if not objs:
        return 0
    anchor_obj = resolve(task, before.get("locator")) if before else None
    anchor_tr = track_of_row(task, before) if before else None
    moved = 0
    # 老工程没有轨道的覆层：只能在自己的列表里换先后（拖到最后 = 列表最底）
    legacy = [(r, o) for r, o in objs if (r.get("locator") or {}).get("where") == "overlay"
              and track_of_row(task, r) is None]
    if legacy:
        if before is not None and track_of_row(task, before) is not None:
            return 0
        a = anchor_obj
        for _r, o in reversed(legacy):
            loc = locator_of(task, o)
            a_loc = locator_of(task, a) if a is not None else None
            if loc is not None and move_overlay(task, loc, a_loc):
                moved += 1
            a = o
        return moved
    # 同一轨道内换先后
    same = [(r, o) for r, o in objs if before and (r.get("locator") or {}).get("where") == "overlay"
            and (before.get("locator") or {}).get("where") == "overlay"
            and track_of_row(task, r) is anchor_tr]
    if same:
        a = anchor_obj
        for _r, o in reversed(same):
            loc = locator_of(task, o)
            a_loc = locator_of(task, a) if a is not None else None
            if loc is not None and move_overlay(task, loc, a_loc):
                moved += 1
            a = o
        return moved
    tracks = []
    for r, _o in objs:
        tr = track_of_row(task, r)
        if tr is not None and tr not in tracks and tr is not anchor_tr:
            tracks.append(tr)
    order = {id(t): i for i, t in enumerate(stack_tracks(task))}
    tracks.sort(key=lambda t: order.get(id(t), 0))
    a = anchor_tr
    for tr in tracks:                     # 从下往上放：每条放在上一条的上面
        if move_track_above(task, tr, a):
            moved += 1
        a = tr
    return moved


def move_overlay(task, locator, before_locator=None) -> bool:
    """把一个覆层挪到另一个覆层「上面」（before_locator=None → 挪到最底）。

    只在同一个列表（文本 / 图片）里挪；跨轨道会顺带改 _track_id。
    列表里靠后 = 画得晚 = 在上面。"""
    if not isinstance(task, dict) or not isinstance(locator, dict) or locator.get("where") != "overlay":
        return False
    name = locator.get("list")
    lst = task.get(name)
    if not isinstance(lst, list):
        return False
    i = int(locator.get("index", -1))
    if not (0 <= i < len(lst)):
        return False
    ov = lst[i]
    if before_locator is None:
        target = 0
        track_id = ov.get("_track_id")
    else:
        if before_locator.get("list") != name:
            return False
        j = int(before_locator.get("index", -1))
        if not (0 <= j < len(lst)) or j == i:
            return False
        anchor = lst[j]
        if (anchor.get("type") == "adjust") != (ov.get("type") == "adjust"):
            return False            # Q174：调整图层和图片在同一个列表里，但不能互相挪到对方的轨道上
        track_id = anchor.get("_track_id", ov.get("_track_id"))
        target = j + 1              # 放在 anchor 之后 = 画在它上面
    del lst[i]
    if target > i:
        target -= 1
    target = max(0, min(len(lst), target))
    lst.insert(target, ov)
    if track_id is not None and ov.get("_track_id") != track_id:
        ov["_track_id"] = track_id
    return True


def delete(task, locators) -> int:
    """删掉这些层（覆层从列表里拿掉；片段从轨道里拿掉）。返回删了几层。"""
    if not isinstance(task, dict):
        return 0
    targets = [resolve(task, loc) for loc in locators or []]
    targets = [t for t in targets if t is not None]
    if not targets:
        return 0
    ids = {id(t) for t in targets}
    n = 0
    for name in mm.OVERLAY_LISTS:
        lst = task.get(name)
        if isinstance(lst, list):
            keep = [ov for ov in lst if id(ov) not in ids]
            n += len(lst) - len(keep)
            lst[:] = keep
    tl = mm.timeline_of(task)
    for tr in getattr(tl, "tracks", None) or []:
        clips = getattr(tr, "clips", None)
        if isinstance(clips, list):
            keep = [c for c in clips if id(c) not in ids]
            n += len(clips) - len(keep)
            clips[:] = keep
    return n


def describe(row: dict) -> str:
    """状态栏 / tooltip 的一句话。"""
    word = lt.KIND_WORD.get(row.get("kind"), "图层")
    bits = ["%s · %s" % (word, row.get("title") or word),
            "%.1f–%.1fs" % (float(row.get("start") or 0.0), float(row.get("end") or 0.0))]
    if row.get("hidden"):
        bits.append("已隐藏" if not row.get("is_audio") else "已静音")
    if row.get("locked"):
        bits.append("已锁定")
    if row.get("blend") and row["blend"] != "normal":
        bits.append("混合 %s" % row["blend"])
    if row.get("opacity", 255) < 255:
        bits.append("不透明度 %d%%" % int(round(row["opacity"] / 2.55)))
    return " · ".join(bits)
