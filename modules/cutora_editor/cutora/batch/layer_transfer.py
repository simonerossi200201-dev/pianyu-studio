# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""跨视频搬时间线上的一层。

素材：按原来的起止时间复制一份。同一条轨道这个时间已经被占了，就新开一条同类型轨道。
属性：只改对方同一种类、同一个序号的那一层（文本样式、画面位置、音量等），不换内容、不改时间。
全部属性：在对应层上连内容、时间和素材来源一起换，层自己的 id 和所在轨道留着。

剪贴板放在 monitor_model 那一份里，时间线和中央监视器共用。
"""
from __future__ import annotations

import copy
import os

from cutora.batch import monitor_model as mm
from cutora.model.project_schema import CLIP_KEYS, SOURCE_KEYS

KIND_WORD = {
    "video": "视频", "audio": "音频", "bgm": "音乐", "sfx": "音效",
    "text": "文本", "image": "图片", "adjust": "调整图层",
}
_AUDIO_TYPES = ("audio", "bgm", "sfx")
_FADE_KEYS = (
    "fade_top", "fade_bottom", "fade_left", "fade_right",
    "fade_top_offset", "fade_bottom_offset", "fade_left_offset", "fade_right_offset",
)
_IMAGE_STYLE = (
    "x", "y", "w", "h", "rotation", "opacity", "scale", "blend_mode", "fx", "keyframes",
    # Q158：开头 / 结尾转场（淡入淡出等）、翻转、羽化、遮罩 / 抠像 / 节点图也是「属性」——
    # 以前粘贴属性不带转场，批量给图片加的开头淡入淡出传不到别的视频
    "transition_preset", "transition_duration", "anim_in", "anim_out",
    "flip_x", "flip_y", "feather", "lock_aspect", "keep_aspect", "masks", "keyer", "fx_graph",
) + _FADE_KEYS
# 批量 / 监视器放进来的图片是整幅容器：真正的位置 / 大小 / 混合在 elements[0] 里
_ELEMENT_STYLE = ("x", "y", "w", "h", "rotation", "opacity", "blend_mode", "lock_aspect",
                  "flip_x", "flip_y", "feather")
_CLIP_STYLE = (
    "fit_mode", "blend_mode", "x", "y", "scale", "scale_y", "rotation",
    "flip_x", "flip_y", "speed", "alpha", "alpha_mode", "size_mode",
    "gain_db", "pitch_semitones", "fade_in_dur", "fade_out_dur",
    "spatial_enabled", "spatial_amount", "bg_fill", "keyframes", "lock_aspect",
    "opacity",
) + _FADE_KEYS
# 这些是身份，不是可以刷到另一条上的属性
_CLIP_SKIP = frozenset({
    "is_main", "link_group_id", "audio_role",
    "is_thumbnail_leadin", "freeze_source_t", "freeze_frame_index",
    "thumbnail_text", "thumbnail_duration", "thumb_duration",
    "is_intro_video_leadin", "intro_video_path",
})
_TRACK_TITLE = {
    "video": "复制的视频", "audio": "复制的音频", "bgm": "复制的音乐",
    "sfx": "复制的音效", "text": "复制的文本", "image": "复制的图片", "adjust": "复制的调整",
}


def list_layers(task) -> list[dict]:
    """这条视频时间线上的文本、图片、视频、音频，按轨道顺序。"""
    rows: list[dict] = []
    counts: dict[str, int] = {}
    tl = mm.timeline_of(task)
    tracks = list(getattr(tl, "tracks", None) or [])
    sources = getattr(tl, "sources", None) or {}
    for tr in tracks:
        kind = str(getattr(tr, "type", "") or "")
        if kind != "video" and kind not in _AUDIO_TYPES:
            continue
        clips = list(getattr(tr, "clips", None) or [])
        order = sorted(range(len(clips)), key=lambda i: float(getattr(clips[i], "start_t", 0.0) or 0.0))
        nth = _track_nth(tracks, tr)
        for i in order:
            clip = clips[i]
            start, end = _clip_span(clip)
            src = _source_dict(sources.get(getattr(clip, "source_id", None)))
            idx = counts.get(kind, 0)
            counts[kind] = idx + 1
            rows.append({
                "kind": kind,
                "index": idx,
                "label": _base_name(src.get("path"), KIND_WORD.get(kind, kind)),
                "start": start,
                "end": end,
                "track_type": kind,
                "track_nth": nth,
                "locator": {"where": "clip", "track_id": str(getattr(tr, "id", "") or ""),
                            "clip_index": i},
            })
    for list_name, kind in (("text_overlays", "text"), ("image_overlays", "image"),
                            ("image_overlays", "adjust")):
        for i, ov in enumerate(task.get(list_name) or [] if isinstance(task, dict) else []):
            if not isinstance(ov, dict):
                continue
            if list_name == "image_overlays" and (ov.get("type") == "adjust") != (kind == "adjust"):
                continue                     # Q174：调整图层单独一类（挂调整轨），不混进图片
            start = _num(ov.get("start"), 0.0)
            end = _num(ov.get("end"), start)
            if end < start:
                start, end = end, start
            nth = _nth_of_track(tracks, kind, ov.get("_track_id"))
            idx = counts.get(kind, 0)
            counts[kind] = idx + 1
            if kind == "text":
                label = _short(str(ov.get("content") or "").replace("\n", " ").strip(), "文本")
            elif kind == "adjust":
                label = _adjust_label(ov)
            else:
                label = _base_name(_image_path(ov), "图片")
            rows.append({
                "kind": kind,
                "index": idx,
                "label": label,
                "start": start,
                "end": end,
                "track_type": kind,
                "track_nth": nth,
                "locator": {"where": "overlay", "list": list_name, "index": i},
            })
    return rows


def layer_title(layer: dict) -> str:
    word = KIND_WORD.get(layer.get("kind"), "图层")
    return "%s  %s  %.1f–%.1fs" % (word, layer.get("label") or word, _num(layer.get("start")), _num(layer.get("end")))


def pack_locators(task, locators, mode: str) -> dict | None:
    """把选中的层做成剪贴板。mode 是 material / style / all。"""
    if mode not in ("material", "style", "all"):
        mode = "material"
    layers = list_layers(task)
    by_loc = {_loc_key(layer["locator"]): layer for layer in layers}
    picked = []
    for loc in locators or []:
        layer = by_loc.get(_loc_key(loc))
        if layer is not None:
            picked.append(layer)
    if not picked:
        return None
    cw, ch = mm.canvas_size(task)
    items = []
    for layer in picked:
        payload = _read_payload(task, layer["locator"])
        if payload is None:
            continue
        items.append({
            "kind": layer["kind"],
            "index": layer["index"],
            "label": layer["label"],
            "start": layer["start"],
            "end": layer["end"],
            "track_type": layer["track_type"],
            "track_nth": layer["track_nth"],
            "canvas": (cw, ch),
            "payload": payload,
        })
    if not items:
        return None
    return {"kind": "layers", "mode": mode, "items": items}


def pack_layers(task, layers, mode: str) -> dict | None:
    return pack_locators(task, [layer.get("locator") for layer in layers or []], mode)


def apply_pack(task, pack: dict) -> dict:
    """把一份图层剪贴板贴到一条视频上。不改剪贴板本身。"""
    out = {"added": 0, "styled": 0, "missed": 0}
    if not isinstance(task, dict) or not isinstance(pack, dict):
        return out
    mode = pack.get("mode") or "material"
    bucket: dict = {}
    for item in pack.get("items") or []:
        if not isinstance(item, dict):
            out["missed"] += 1
            continue
        if mode == "material":
            ok = _paste_material(task, item, bucket)
            out["added" if ok else "missed"] += 1
        else:
            flag = _paste_props(task, item, mode)
            if flag == "styled":
                out["styled"] += 1
            elif flag != "same":
                out["missed"] += 1
    return out


def describe_copy(pack: dict) -> str:
    mode = {"material": "素材", "style": "属性", "all": "全部属性"}.get(pack.get("mode"), "图层")
    return "已复制 %d 层%s（原时间）。选中别的视频后粘贴" % (len(pack.get("items") or []), mode)


def describe_apply(tasks: int, totals: dict) -> str:
    added = int(totals.get("added") or 0)
    styled = int(totals.get("styled") or 0)
    missed = int(totals.get("missed") or 0)
    if added == 0 and styled == 0:
        if missed:
            return "没有贴上，%d 层没有对应图层" % missed
        return "没有需要改的地方"
    if added and not styled:
        msg = "已把素材贴到 %d 条视频，新增 %d 层" % (tasks, added)
    elif styled and not added:
        msg = "已把属性贴到 %d 条视频的 %d 层" % (tasks, styled)
    else:
        msg = "已贴到 %d 条视频" % tasks
    if missed:
        msg += "，%d 层没有对应图层" % missed
    return msg


# ───────────────────────── 读取 ─────────────────────────

def _read_payload(task, locator) -> dict | None:
    where = (locator or {}).get("where")
    if where == "overlay":
        rows = task.get(locator.get("list")) or []
        i = int(locator.get("index") or 0)
        if not (0 <= i < len(rows)) or not isinstance(rows[i], dict):
            return None
        ov = mm._plain_copy(rows[i])
        for key in list(ov.keys()):
            if str(key).startswith("_c") or key in ("_hit", "_raster"):
                ov.pop(key, None)
        return {"overlay": ov}
    if where == "clip":
        tr, clip = _clip_at(task, locator)
        if clip is None:
            return None
        tl = mm.timeline_of(task)
        sources = getattr(tl, "sources", None) or {}
        data = {
            "in_t": _num(getattr(clip, "in_t", 0.0)),
            "out_t": _num(getattr(clip, "out_t", 0.0)),
            "start_t": _num(getattr(clip, "start_t", 0.0)),
            "source": _source_dict(sources.get(getattr(clip, "source_id", None))),
        }
        for key in CLIP_KEYS:
            if hasattr(clip, key):
                data[key] = copy.deepcopy(getattr(clip, key))
        for key in _FADE_KEYS:
            if hasattr(clip, key) and key not in data:
                data[key] = copy.deepcopy(getattr(clip, key))
        if hasattr(clip, "opacity"):
            data["opacity"] = copy.deepcopy(getattr(clip, "opacity"))
        return data
    return None


def _clip_at(task, locator):
    tl = mm.timeline_of(task)
    want = str((locator or {}).get("track_id") or "")
    idx = int((locator or {}).get("clip_index") or 0)
    for tr in getattr(tl, "tracks", None) or []:
        if str(getattr(tr, "id", "") or "") != want:
            continue
        clips = list(getattr(tr, "clips", None) or [])
        if 0 <= idx < len(clips):
            return tr, clips[idx]
    return None, None


def _source_dict(src) -> dict:
    if src is None:
        return {}
    out = {}
    for key in SOURCE_KEYS:
        if isinstance(src, dict):
            if key in src:
                out[key] = copy.deepcopy(src.get(key))
        elif hasattr(src, key):
            out[key] = copy.deepcopy(getattr(src, key))
    return out


# ───────────────────────── 贴素材 ─────────────────────────

def _paste_material(task, item, bucket) -> bool:
    tl = mm.timeline_of(task)
    if tl is None:
        return False
    payload = copy.deepcopy(item.get("payload") or {})
    kind = str(item.get("track_type") or item.get("kind") or "")
    if "overlay" in payload:
        return _paste_overlay_material(task, item, payload["overlay"], bucket)
    if kind in ("video",) or kind in _AUDIO_TYPES:
        return _paste_clip_material(task, item, payload, bucket)
    return False


def _paste_overlay_material(task, item, ov, bucket) -> bool:
    kind = item.get("kind") if item.get("kind") in ("image", "adjust") else "text"
    list_name = "text_overlays" if kind == "text" else "image_overlays"
    new = _with_fades(ov, mm.rescale_overlay(ov, _ratio(item, task)))
    new = mm._plain_copy(new)
    new["id"] = mm._new_overlay_id(ov.get("id"))
    new["start"] = _num(item.get("start"), _num(new.get("start")))
    new["end"] = _num(item.get("end"), _num(new.get("end"), new["start"]))
    start, end = new["start"], new["end"]
    tr = _dest_track(task, kind, int(item.get("track_nth") or 0), start, end, bucket)
    if tr is None:
        return False
    new["_track_id"] = getattr(tr, "id", None)
    dw, dh = mm.canvas_size(task)
    new["_canvas_w"], new["_canvas_h"] = dw, dh
    task.setdefault(list_name, []).append(new)
    return True


def _paste_clip_material(task, item, payload, bucket) -> bool:
    from cutora.model.timeline_model import Clip
    kind = str(item.get("track_type") or item.get("kind") or "video")
    start = _num(payload.get("start_t"), _num(item.get("start")))
    end = start + _payload_dur(payload)
    tr = _dest_track(task, kind, int(item.get("track_nth") or 0), start, end, bucket)
    if tr is None:
        return False
    tl = mm.timeline_of(task)
    sid = _ensure_source(tl, payload.get("source") or {})
    clip = Clip(sid, _num(payload.get("in_t")), _num(payload.get("out_t")), start)
    _write_clip_fields(clip, payload, _ratio(item, task), timing=False)
    tr.clips.append(clip)
    return True


def _dest_track(task, kind, nth, start, end, bucket):
    tl = mm.timeline_of(task)
    if tl is None:
        return None
    tracks = [tr for tr in (tl.tracks or []) if str(getattr(tr, "type", "") or "") == kind]
    if 0 <= nth < len(tracks) and _range_free(task, tracks[nth], kind, start, end):
        return tracks[nth]
    extra = bucket.get((kind, nth))
    if extra is not None and _range_free(task, extra, kind, start, end):
        return extra
    from cutora.model.timeline_model import Track
    tr = Track(kind)
    tr.name = _TRACK_TITLE.get(kind, "复制的轨道")
    tl.add_track(tr)
    bucket[(kind, nth)] = tr
    return tr


def _range_free(task, track, kind, start, end) -> bool:
    if getattr(track, "locked", False):
        return False
    if kind in ("text", "image", "adjust"):
        for ov in _overlays_on(task, track, kind):
            a = _num(ov.get("start"))
            b = _num(ov.get("end"), a)
            if b < a:
                a, b = b, a
            if _overlap(start, end, a, b):
                return False
        return True
    for clip in getattr(track, "clips", None) or []:
        a, b = _clip_span(clip)
        if _overlap(start, end, a, b):
            return False
    return True


def _overlays_on(task, track, kind):
    list_name = "text_overlays" if kind == "text" else "image_overlays"
    tid = getattr(track, "id", None)
    tracks = [tr for tr in (getattr(mm.timeline_of(task), "tracks", None) or [])
              if str(getattr(tr, "type", "") or "") == kind]
    first_id = getattr(tracks[0], "id", None) if tracks else None
    out = []
    for ov in task.get(list_name) or []:
        if not isinstance(ov, dict):
            continue
        if list_name == "image_overlays" and (ov.get("type") == "adjust") != (kind == "adjust"):
            continue
        oid = ov.get("_track_id")
        if oid == tid or (not oid and tid == first_id):
            out.append(ov)
    return out


def _ensure_source(tl, source: dict):
    sources = getattr(tl, "sources", None)
    if not isinstance(sources, dict):
        tl.sources = {}
        sources = tl.sources
    path = str(source.get("path") or source.get("source_path") or "")
    if path:
        want = os.path.normcase(os.path.abspath(path))
        for sid, src in sources.items():
            got = src.get("path") if isinstance(src, dict) else getattr(src, "path", None)
            if got and os.path.normcase(os.path.abspath(str(got))) == want:
                return sid if not isinstance(src, dict) else (src.get("id") or sid)
    from cutora.model.timeline_model import MediaSource
    dur = source.get("duration")
    src = MediaSource(path, duration=dur if isinstance(dur, (int, float)) else 0.0, probe=False)
    for key in ("has_video", "has_audio", "alpha", "alpha_mode", "preview_path"):
        if source.get(key) is not None:
            setattr(src, key, copy.deepcopy(source.get(key)))
    sources[src.id] = src
    return src.id


# ───────────────────────── 贴属性 ─────────────────────────

def _paste_props(task, item, mode: str) -> str:
    """styled 改了，same 对应层已经一样，miss 没有这一层。"""
    layer = _layer_by_kind_index(task, item.get("kind"), item.get("index"))
    if layer is None:
        return "miss"
    payload = copy.deepcopy(item.get("payload") or {})
    if layer["locator"]["where"] == "overlay":
        flag = _props_overlay(task, layer, payload, item, mode)
    else:
        flag = _props_clip(task, layer, payload, item, mode)
    if flag is None:
        return "miss"
    return "styled" if flag else "same"


def _layer_by_kind_index(task, kind, index):
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None
    for layer in list_layers(task):
        if layer["kind"] == kind and layer["index"] == index:
            return layer
    return None


def _props_overlay(task, layer, payload, item, mode):
    rows = task.get(layer["locator"]["list"]) or []
    i = int(layer["locator"]["index"])
    if not (0 <= i < len(rows)) or not isinstance(rows[i], dict):
        return None
    dest = rows[i]
    ov = payload.get("overlay") or {}
    if mode == "all":
        before = mm._plain_copy(dest)
        scaled = _with_fades(ov, mm.rescale_overlay(mm._plain_copy(ov), _ratio(item, task)))
        keep_id = dest.get("id")
        keep_track = dest.get("_track_id")
        for key in list(dest.keys()):
            if key in ("id", "_track_id") or str(key).startswith("_c") or key in ("_hit", "_raster"):
                continue
            if key not in scaled:
                dest.pop(key, None)
        for key, val in scaled.items():
            if key in ("id", "_track_id"):
                continue
            dest[key] = val
        if keep_id is not None:
            dest["id"] = keep_id
        if keep_track is not None:
            dest["_track_id"] = keep_track
        return _overlay_view(before) != _overlay_view(dest)
    if layer["kind"] == "adjust":
        return _props_adjust(dest, ov, _ratio(item, task))
    if layer["kind"] == "text":
        from cutora.ui.text_style_clipboard import copy_style, paste_style
        changed = paste_style(dest, copy_style(ov))
        for key in _FADE_KEYS:
            if key not in ov:
                continue
            val = copy.deepcopy(ov[key])
            if dest.get(key) != val:
                dest[key] = val
                changed = True
        return changed
    r = _ratio(item, task)
    wrote = False
    for key in _IMAGE_STYLE:
        if key not in ov:
            continue
        val = copy.deepcopy(ov[key])
        if key in ("x", "y", "w", "h") and isinstance(val, (int, float)) and abs(r - 1.0) > 1e-6:
            val = float(val) * r
        if dest.get(key) != val:
            dest[key] = val
            wrote = True
    # Q158：容器图片的元素几何 / 混合也刷过去（按画布比例缩放；图片文件本身不换）
    src_els = [e for e in (ov.get("elements") or []) if isinstance(e, dict)]
    dst_els = [e for e in (dest.get("elements") or []) if isinstance(e, dict)]
    for se, de in zip(src_els, dst_els):
        for key in _ELEMENT_STYLE:
            if key not in se:
                continue
            val = copy.deepcopy(se[key])
            if key in ("x", "y", "w", "h") and isinstance(val, (int, float)) and abs(r - 1.0) > 1e-6:
                val = float(val) * r
            if de.get(key) != val:
                de[key] = val
                wrote = True
    return wrote


_ADJUST_STYLE = ("x", "y", "w", "h", "rotation", "opacity", "scale", "keyframes", "masks",
                 "feather", "solid", "effect", "name", "xform", "range")


def _props_adjust(dest, ov, r) -> bool:
    """Q174：调整图层的属性 = 形状位置大小旋转 + 纯色 / 效果设置 + 羽化 + 遮罩 + 关键帧（按画布比例缩放像素量）。"""
    wrote = False
    for key in _ADJUST_STYLE:
        if key not in ov:
            continue
        val = copy.deepcopy(ov[key])
        if abs(r - 1.0) > 1e-6:
            if key in ("x", "y", "w", "h", "feather") and isinstance(val, (int, float)):
                val = float(val) * r
            elif key == "solid" and isinstance(val, dict) and isinstance(val.get("border_width"), (int, float)):
                val["border_width"] = float(val["border_width"]) * r
            elif key == "xform" and isinstance(val, dict):
                for k2 in ("x", "y"):
                    if isinstance(val.get(k2), (int, float)):
                        val[k2] = float(val[k2]) * r
        if dest.get(key) != val:
            dest[key] = val
            wrote = True
    return wrote


def _adjust_label(ov) -> str:
    from cutora.render import adjust_layer as AL
    name = str(ov.get("name") or "调整")
    eff = AL.effect_of(ov)
    if AL.effect_active(eff):
        name += "·" + AL.effect_label(eff)
    elif AL.solid_of(ov)["on"]:
        name += "·纯色"
    return _short(name, "调整")


def _props_clip(task, layer, payload, item, mode):
    _tr, clip = _clip_at(task, layer["locator"])
    if clip is None:
        return None
    r = _ratio(item, task)
    changed = _write_clip_fields(clip, payload, r, timing=(mode == "all"))
    if mode == "all":
        tl = mm.timeline_of(task)
        src = payload.get("source") or {}
        if tl is not None and (src.get("path") or src.get("source_path")):
            new_sid = _ensure_source(tl, src)
            if clip.source_id != new_sid:
                clip.source_id = new_sid
                changed = True
        for attr in ("in_t", "out_t", "start_t"):
            val = _num(payload.get(attr), getattr(clip, attr, 0.0))
            if getattr(clip, attr, None) != val:
                setattr(clip, attr, val)
                changed = True
    return changed


def _write_clip_fields(clip, payload, ratio, *, timing: bool) -> bool:
    changed = False
    keys = list(_CLIP_STYLE)
    if timing:
        keys.extend(k for k in CLIP_KEYS if k not in _CLIP_SKIP and k not in keys)
    for key in keys:
        if key not in payload or key in _CLIP_SKIP:
            continue
        val = copy.deepcopy(payload[key])
        if key in ("x", "y") and isinstance(val, (int, float)) and abs(ratio - 1.0) > 1e-6:
            val = float(val) * ratio
        if getattr(clip, key, None) != val:
            setattr(clip, key, val)
            changed = True
    return changed


def _overlay_view(ov) -> dict:
    if not isinstance(ov, dict):
        return {}
    return {
        k: v for k, v in ov.items()
        if not (str(k).startswith("_c") or k in ("_hit", "_raster"))
    }


# ───────────────────────── 小工具 ─────────────────────────

def _with_fades(src, scaled):
    if not isinstance(scaled, dict):
        return scaled
    for key in _FADE_KEYS:
        if key in src and key not in scaled:
            scaled[key] = copy.deepcopy(src[key])
    return scaled


def _ratio(item, task) -> float:
    canvas = item.get("canvas") or (0, 0)
    try:
        sw, sh = float(canvas[0]), float(canvas[1])
    except (TypeError, ValueError, IndexError):
        return 1.0
    if sw <= 1 or sh <= 1:
        return 1.0
    dw, dh = mm.canvas_size(task)
    return min(dw / sw, dh / sh)


def _payload_dur(payload) -> float:
    speed = _num(payload.get("speed"), 1.0)
    if speed <= 0:
        speed = 1.0
    inn = _num(payload.get("in_t"))
    out = _num(payload.get("out_t"), inn)
    return max(0.0, (out - inn) / speed)


def _clip_span(clip) -> tuple[float, float]:
    start = _num(getattr(clip, "start_t", 0.0))
    try:
        dur = float(getattr(clip, "effective_duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= 0:
        dur = max(0.0, _num(getattr(clip, "out_t", 0.0)) - _num(getattr(clip, "in_t", 0.0)))
    return start, start + dur


def _track_nth(tracks, track) -> int:
    kind = str(getattr(track, "type", "") or "")
    n = 0
    for tr in tracks:
        if tr is track:
            return n
        if str(getattr(tr, "type", "") or "") == kind:
            n += 1
    return 0


def _nth_of_track(tracks, kind, track_id) -> int:
    ids = [getattr(tr, "id", None) for tr in tracks if str(getattr(tr, "type", "") or "") == kind]
    if track_id in ids:
        return ids.index(track_id)
    return 0


def _overlap(a0, a1, b0, b1) -> bool:
    return float(a0) < float(b1) - 1e-4 and float(b0) < float(a1) - 1e-4


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _short(text: str, fallback: str) -> str:
    text = (text or "").strip()
    if not text:
        return fallback
    return text[:16]


def _base_name(path, fallback: str) -> str:
    name = os.path.basename(str(path or ""))
    return _short(name, fallback)


def _image_path(ov) -> str:
    for el in ov.get("elements") or []:
        if isinstance(el, dict):
            path = el.get("path") or el.get("src") or ""
            if path:
                return str(path)
    return str(ov.get("path") or "")


def _loc_key(locator) -> tuple:
    loc = locator or {}
    if loc.get("where") == "overlay":
        return ("overlay", str(loc.get("list") or ""), int(loc.get("index") or 0))
    return ("clip", str(loc.get("track_id") or ""), int(loc.get("clip_index") or 0))
