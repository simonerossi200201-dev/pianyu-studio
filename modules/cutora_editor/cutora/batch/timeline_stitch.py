# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T20a: stitch cutlist → timeline placements. No intermediate _stitched mp4."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass

CANVAS_W = 1080
CANVAS_H = 1920
CANVAS_FPS = 30.0
ASR_CACHE_NAME = "_asr_cache.json"
ASR_PARAM_VER = 4


def source_wh(path: str) -> tuple[int, int] | None:
    """Probe a source's pixel size. None when unreadable. Cheap (one ffprobe)."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from cutora.render.readers import probe_bitrates
        info = probe_bitrates(path) or {}
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return None


def canvas_for_placements(placements, canvas_wh=None) -> tuple[int, int]:
    """BUG-38: the stitched project's canvas.

    Priority: the canvas the user picked (``canvas_wh``, from the open
    project) → the first source's own size (E5: new project = source size)
    → 1080×1920 only when nothing else is known. Never silently 1080×1920
    while the user clicked 720×1280.
    """
    if canvas_wh and len(canvas_wh) >= 2:
        try:
            w, h = int(canvas_wh[0]), int(canvas_wh[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
    for p in placements or []:
        wh = source_wh(getattr(p, "source_path", None) or "")
        if wh:
            return wh
    return CANVAS_W, CANVAS_H


@dataclass
class ClipPlacement:
    source_path: str
    in_t: float
    out_t: float
    start_t: float
    link_audio: bool = True
    fit_mode: str = "fill"  # cover the project canvas (T21 folded into T20a)

    def as_dict(self) -> dict:
        return asdict(self)


def _placements_for_items(items) -> list[ClipPlacement]:
    t = 0.0
    out: list[ClipPlacement] = []
    for it in items or []:
        if not it.get("keep", True):
            continue
        path = str(it.get("path") or it.get("name") or "")
        segs = it.get("segs") or [[it.get("in_t", 0.0), it.get("out_t", 0.0)]]
        for pair in segs:
            a, b = float(pair[0]), float(pair[1])
            if b <= a:
                continue
            out.append(ClipPlacement(
                source_path=path,
                in_t=a,
                out_t=b,
                start_t=t,
                link_audio=True,
                fit_mode="fill",
            ))
            t += b - a
    return out


def placements_by_group(cuts_map, group_order=None) -> dict:
    """One placement list per group. A group is one video / one script."""
    if not isinstance(cuts_map, dict):
        return {}
    keys = list(group_order) if group_order else list(cuts_map.keys())
    out = {}
    for g in keys:
        plist = _placements_for_items(cuts_map.get(g) or [])
        if plist:
            out[str(g)] = plist
    return out


def placements_from_cutlist(cuts_map, group_order=None) -> list[ClipPlacement]:
    """Flatten keep-items. Prefer placements_by_group for one-timeline-per-group."""
    grouped = placements_by_group(cuts_map, group_order=group_order)
    if len(grouped) <= 1:
        for plist in grouped.values():
            return list(plist)
        return []
    t = 0.0
    out: list[ClipPlacement] = []
    for g in (group_order or grouped.keys()):
        for p in grouped.get(str(g), []):
            dur = max(0.0, p.out_t - p.in_t)
            out.append(ClipPlacement(
                source_path=p.source_path, in_t=p.in_t, out_t=p.out_t,
                start_t=t, link_audio=p.link_audio, fit_mode=p.fit_mode,
            ))
            t += dur
    return out


def projects_from_cutlist(cuts_map, out_root: str, *, text_by_group=None,
                          canvas_wh=None) -> list[str]:
    """Write <group>/_stitched/stitched.asproj.json for each group.

    ``canvas_wh`` is the user's project canvas; None → per-group source size.
    """
    grouped = placements_by_group(cuts_map)
    texts = text_by_group or {}
    paths = []
    for g, places in grouped.items():
        dest = os.path.join(out_root, str(g), "_stitched", "stitched.asproj.json")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        write_placements(os.path.join(out_root, str(g)), places)
        project_from_placements(places, dest, text=str(texts.get(g) or ""),
                                canvas_wh=canvas_wh)
        paths.append(dest)
    return paths


def load_cutlist(task_dir: str) -> dict:
    for rel in (
        os.path.join("_stitched", "cutlist.json"),
        "cutlist.json",
    ):
        path = os.path.join(task_dir, rel)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    raise FileNotFoundError("cutlist.json not in %s" % task_dir)


def write_placements(task_dir: str, placements: list[ClipPlacement]) -> str:
    folder = os.path.join(task_dir, "_stitched")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "timeline_placements.json")
    payload = {
        "fit_mode": "fill",
        "n": len(placements),
        "placements": [p.as_dict() for p in placements],
    }
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, dest)
    return dest


def _clip_dict(clip) -> dict:
    return {
        "source_id": clip.source_id,
        "in_t": float(clip.in_t),
        "out_t": float(clip.out_t),
        "start_t": float(clip.start_t),
        "fit_mode": getattr(clip, "fit_mode", "fill"),
        "blend_mode": "normal",
        "x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0,
        "flip_x": False, "flip_y": False, "speed": 1.0,
        "loop": False, "match_duration": False,
        "transition_preset": "none", "transition_duration": 0.35,
        "gain_db": 0.0, "pitch_semitones": 0.0,
        "fade_in_dur": 0.0, "fade_out_dur": 0.0,
        "spatial_enabled": bool(getattr(clip, "spatial_enabled", False)),
        "spatial_amount": float(getattr(clip, "spatial_amount", 0.0) or 0.0),
        "is_main": bool(getattr(clip, "is_main", False)),
        "audio_role": getattr(clip, "audio_role", "") or "",
        "link_group_id": getattr(clip, "link_group_id", None),
    }


def timeline_from_placements(
    placements: list[ClipPlacement],
    *,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
    fps: float = CANVAS_FPS,
):
    """One video track + linked audio track. fit_mode=fill covers 1080×1920."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    tl = Timeline(int(width), int(height), float(fps))
    vtrack = Track("video")
    vtrack.is_main_video = True
    strack = Track("subs")
    atrack = Track("audio")
    atrack.audio_role = "main"
    atrack.name = "原声"
    by_path: dict[str, MediaSource] = {}
    for p in placements:
        path = os.path.abspath(p.source_path)
        src = by_path.get(path)
        if src is None:
            src = MediaSource(path)
            by_path[path] = src
            tl.sources[src.id] = src
        gid = uuid.uuid4().hex
        vc = Clip(src.id, p.in_t, p.out_t, p.start_t)
        vc.fit_mode = p.fit_mode or "fill"
        vc.link_group_id = gid
        ac = Clip(src.id, p.in_t, p.out_t, p.start_t)
        ac.link_group_id = gid
        ac.is_main = True
        ac.audio_role = "main"
        try:
            from cutora.audio.sfx_library import apply_default_spatial_to_clip
            apply_default_spatial_to_clip(ac)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.batch.timeline_stitch").exception(
                "apply_default_spatial_to_clip failed")
            ac.spatial_enabled = True
        vtrack.clips.append(vc)
        atrack.clips.append(ac)
    tl.add_track(vtrack)
    tl.add_track(strack)
    tl.add_track(atrack)
    return tl


def timeline_to_dict(tl) -> dict:
    sources = {}
    for sid, src in (tl.sources or {}).items():
        if hasattr(src, "id"):
            sources[sid] = {
                "id": src.id,
                "path": src.path,
                "duration": src.duration,
                "has_video": getattr(src, "has_video", True),
                "has_audio": getattr(src, "has_audio", True),
            }
        elif isinstance(src, dict):
            sources[sid] = src
    tracks = []
    for tr in tl.tracks:
        tracks.append({
            "id": getattr(tr, "id", None),
            "type": getattr(tr, "type", None),
            "enable_batch": bool(getattr(tr, "enable_batch", False)),
            "is_main_video": bool(getattr(tr, "is_main_video", False)),
            "domain": getattr(tr, "domain", None),
            "visible": True,
            "locked": False,
            "order": int(getattr(tr, "order", 0)),
            "pinned": False,
            "blend_mode": "normal",
            "clips": [_clip_dict(c) for c in getattr(tr, "clips", [])],
            "transitions": [],
        })
    return {
        "width": int(tl.width),
        "height": int(tl.height),
        "fps": float(tl.fps),
        "sources": sources,
        "tracks": tracks,
    }


def _clip_fingerprint(path: str, language: str) -> str:
    try:
        st = os.stat(path)
        sig = "%s:%d" % (st.st_size, int(st.st_mtime))
    except Exception:
        sig = "nostat"
    return "%s|%s|%s|p%d" % (os.path.basename(path), sig, language, ASR_PARAM_VER)


def load_asr_cache(cache_dir: str) -> dict:
    if not cache_dir:
        return {}
    path = os.path.join(cache_dir, ASR_CACHE_NAME)
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _cache_entry_for(cache: dict, source_path: str) -> dict:
    path = os.path.abspath(source_path)
    base = os.path.basename(path)
    for lang in ("en", "zh", "it", "fr", "es", "de", "pt", "auto"):
        hit = cache.get(_clip_fingerprint(path, lang))
        if isinstance(hit, dict) and hit.get("words") is not None:
            return hit
    for key, hit in cache.items():
        if not isinstance(hit, dict) or hit.get("words") is None:
            continue
        if str(key).split("|", 1)[0] == base:
            return hit
    return {}


def _pack_words_to_segments(words: list, meta: dict) -> list[dict]:
    """Split offset words into subtitle segments; keep cache marks."""
    segs: list[dict] = []
    cur: list[dict] = []

    def _flush():
        if not cur:
            return
        start = float(cur[0]["start"])
        end = float(cur[-1]["end"])
        text = " ".join(w["word"] for w in cur)
        rec = {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "words": [dict(w) for w in cur],
        }
        if meta.get("timing_tier") is not None:
            rec["timing_tier"] = int(meta["timing_tier"])
        if meta.get("incomplete"):
            rec["incomplete"] = True
        if meta.get("role"):
            rec["role"] = meta["role"]
        if meta.get("env") and not segs:
            rec["env"] = meta["env"]
        segs.append(rec)
        cur.clear()

    for w in words:
        if cur:
            gap = float(w["start"]) - float(cur[-1]["end"])
            prev = cur[-1]["word"]
            if gap > 0.55 or prev.endswith((".", "?", "!", "。", "？", "！")):
                _flush()
        cur.append(w)
    _flush()
    return segs


def segments_from_asr_cache(
    placements: list[ClipPlacement], cache_dir: str
) -> list[dict]:
    """Map stitcher word cache onto timeline time: t' = t + (start_t − in_t)."""
    cache = load_asr_cache(cache_dir)
    if not cache or not placements:
        return []
    out: list[dict] = []
    for place in placements:
        hit = _cache_entry_for(cache, place.source_path)
        raw_words = hit.get("words") or []
        in_t = float(place.in_t)
        out_t = float(place.out_t)
        delta = float(place.start_t) - in_t
        kept: list[dict] = []
        for item in raw_words:
            try:
                tok, ws, we = item[0], float(item[1]), float(item[2])
            except (TypeError, ValueError, IndexError):
                continue
            if we <= in_t or ws >= out_t:
                continue
            kept.append({
                "word": str(tok),
                "start": round(max(in_t, ws) + delta, 3),
                "end": round(min(out_t, we) + delta, 3),
            })
        if not kept:
            continue
        meta = {
            "timing_tier": hit.get("timing_tier"),
            "incomplete": hit.get("incomplete"),
            "role": hit.get("role") or "default",
            "env": hit.get("env"),
        }
        out.extend(_pack_words_to_segments(kept, meta))
    return out


def project_from_placements(
    placements: list[ClipPlacement],
    dest: str,
    *,
    text: str = "",
    width: int | None = None,
    height: int | None = None,
    fps: float = CANVAS_FPS,
    canvas_wh=None,
) -> str:
    """Write a .asproj.json. Does not write any stitched mp4.

    Canvas: explicit ``width``/``height`` → ``canvas_wh`` (user's project
    canvas) → first source's size → 1080×1920. See ``canvas_for_placements``.
    """
    if not (width and height):
        width, height = canvas_for_placements(placements, canvas_wh)
    width, height = int(width), int(height)
    tl = timeline_from_placements(placements, width=width, height=height, fps=fps)
    duration = 0.0
    if placements:
        last = placements[-1]
        duration = float(last.start_t) + max(0.0, float(last.out_t) - float(last.in_t))
    first = os.path.abspath(placements[0].source_path) if placements else ""
    group = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(dest))))
    if (not group) or group in (".", "_stitched"):
        group = os.path.splitext(os.path.basename(dest or ""))[0] or "stitch"
    task = {
        "name": group,
        "video": first,
        "path": first,
        "text": text or "",
        "segments": segments_from_asr_cache(
            placements, os.path.dirname(os.path.abspath(dest))),
        "overlays": [],
        "text_overlays": [],
        "image_overlays": [],
        "audio_clips": [],
        "dims": [int(width), int(height)],
        "style": {},
        "src_volume": 1.0,
        "intro_media": None,
        "timeline": timeline_to_dict(tl),
        "video_overlay": {},
        "audio_tracks": [],
        "duration": duration,
        "trim_end_sec": duration,
        "subtitle_roles": {"default": {"name": "默认", "color": "#CCCCCC"}},
    }
    data = {
        "version": 2,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": "CUTORA",
        "export_video_mode": "compressed",
        "tasks": [task],
        "curr_idx": 0 if placements else -1,
    }
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, dest)
    return dest
