# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless handlers that must not import torch or PySide6."""
from __future__ import annotations

from typing import Any

from cutora.core.commands import CommandError, Context
from cutora.core.secrets import _is_secret_field, strip_secret_fields


def cmd_app_version(ctx: Context, params: dict) -> dict:
    from cutora import __version__
    return {"app": "CUTORA", "version": __version__}


def cmd_app_gpu_info(ctx: Context, params: dict) -> dict:
    from cutora.core.config import detect_gpu_config
    device, compute, reason = detect_gpu_config(verbose=False)
    info: dict[str, Any] = {
        "device": device,
        "compute_type": compute,
        "reason": reason,
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["name"] = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability()
            info["capability"] = "%s.%s" % cap
    except Exception:
        pass
    return info


def cmd_app_free_vram(ctx: Context, params: dict) -> dict:
    from cutora.render.export_guard import gpu_memory_gb
    val = gpu_memory_gb()
    if val is None:
        return {"ok": False, "total_gb": None, "used_gb": None, "free_gb": None}
    total, used, free = val
    return {"ok": True, "total_gb": total, "used_gb": used, "free_gb": free}


def cmd_app_settings_get(ctx: Context, params: dict) -> dict:
    from cutora.core.history import _load_app_settings
    data = strip_secret_fields(_load_app_settings() or {})
    key = params.get("key")
    if key:
        if _is_secret_field(str(key)):
            raise CommandError("拒绝读取密钥字段 %s" % key, code="secret")
        return {"key": key, "value": data.get(key)}
    return {"settings": data}


def cmd_app_settings_set(ctx: Context, params: dict) -> dict:
    from cutora.core.history import _load_app_settings, _save_app_settings
    key = str(params.get("key") or "")
    if not key:
        raise CommandError("缺少 key")
    if _is_secret_field(key):
        raise CommandError("密钥字段必须走 keyring，禁止写入设置文件", code="secret")
    data = _load_app_settings() or {}
    data[key] = params.get("value")
    _save_app_settings(data)
    return {"key": key, "ok": True}


def cmd_app_theme_list(ctx: Context, params: dict) -> dict:
    from cutora.ui.theme.tokens import list_themes
    rows = []
    for tid, data in list_themes():
        rows.append({
            "id": tid,
            "name_zh": data.get("name_zh") or tid,
            "name_en": data.get("name_en") or tid,
        })
    return {"themes": rows}


def cmd_style_list_presets(ctx: Context, params: dict) -> dict:
    from cutora.subtitle import presets as P
    from cutora.subtitle import text_preset as TP
    from cutora.subtitle.stylepack import list_style_entries
    sub = [row["name"] for row in list_style_entries()]
    text = [p.get("name") for p in TP.list_presets()]
    kw = list((P.load_keyword_presets().get("presets") or {}).keys())
    return {"subtitle": sub, "text": text, "keyword": kw}


def cmd_media_probe(ctx: Context, params: dict) -> dict:
    from cutora.batch.recipe import probe_media, RecipeError
    path = str(params.get("path") or "")
    try:
        return probe_media(path)
    except RecipeError as exc:
        raise CommandError(str(exc)) from exc


def cmd_subtitle_status(ctx: Context, params: dict) -> dict:
    """Headless read: whether this media/project already has segments."""
    window = ctx.window
    media_id = params.get("media_id")
    media = str(params.get("media") or "")
    project = params.get("project")
    segs: list = []
    language = None
    if window is not None:
        tasks = list(getattr(window, "tasks", None) or [])
        task = None
        if media_id is not None:
            try:
                idx = int(media_id)
            except (TypeError, ValueError):
                idx = -1
            if 0 <= idx < len(tasks):
                task = tasks[idx]
        elif 0 <= int(getattr(window, "curr_idx", -1) or -1) < len(tasks):
            task = tasks[int(window.curr_idx)]
        elif len(tasks) == 1:
            task = tasks[0]
        if task is not None:
            segs = list(task.get("segments") or [])
            language = task.get("language") or None
    if not segs and media:
        from cutora.batch.recipe import segments_for_media
        segs = list(segments_for_media(media, project=project if project else None))
    return {
        "has_segments": bool(segs),
        "segment_count": len(segs),
        "language": language,
    }


def cmd_subtitle_export_srt(ctx: Context, params: dict) -> dict:
    from cutora.batch.recipe import RecipeError, export_srt_for_media
    media = str(params.get("media") or "")
    out_dir = str(params.get("out_dir") or "")
    project = params.get("project")
    if not media or not out_dir:
        raise CommandError("subtitle.export_srt 需要 media 与 out_dir")
    try:
        path = export_srt_for_media(media, out_dir, project=project)
    except RecipeError as exc:
        raise CommandError(str(exc)) from exc
    return {"artifact": path, "path": path}


def _layout_params(ctx, params):
    return dict(
        window=ctx.window,
        media_id=params.get("media_id"),
        media=str(params.get("media") or ""),
        project=params.get("project") or None,
    )


def _idx_for(task, seg_id) -> int:
    from cutora.subtitle.seg_ids import ensure_segment_ids, resolve_seg_index
    ensure_segment_ids(task)
    return resolve_seg_index(task, seg_id)


def _idxs_for(task, seg_ids) -> list[int]:
    return [_idx_for(task, s) for s in (seg_ids or [])]


def cmd_subtitle_relayout(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import SubtitleIOError, mutate_task
    from cutora.subtitle.layout import LayoutRules, relayout_task
    raw_ids = params.get("seg_ids")
    def _run(task):
        selected = None
        if raw_ids:
            selected = set(_idxs_for(task, raw_ids))
        elif str(params.get("scope") or "all") == "selected" and ctx.window is not None:
            tl = getattr(ctx.window, "timeline", None)
            selected = set(getattr(tl, "selected_indices", None) or [])
        raw = task.get("layout_rules") if isinstance(task.get("layout_rules"), dict) else {}
        from cutora.subtitle.stylepack import effective_max_lines
        max_c = params.get("max_chars") or raw.get("max_chars_per_line") or 42
        max_l = params.get("max_lines")
        if max_l is None:
            max_l = effective_max_lines(task.get("style") if isinstance(task.get("style"), dict) else None, task)
        rules = LayoutRules(
            max_chars_per_line=int(max_c),
            max_lines=int(max_l),
            balance_lines=bool(raw.get("balance_lines", True)),
            line_break=str(raw.get("line_break") or "space"),
        )
        # Q137：用户按了「重新断句」/ 拖了宽度 = 要新的断句法。先跨字幕重排
        # （合并碎句、按意群切），再走原来的逐条断行把 lines 算出来。
        style = task.get("style") if isinstance(task.get("style"), dict) else None
        if isinstance(style, dict) and not params.get("legacy"):
            from cutora.subtitle.layout import LAYOUT_ENGINE_SMART
            from cutora.subtitle.reflow import reflow_task
            style["layout_engine"] = LAYOUT_ENGINE_SMART
            flow = reflow_task(task, selected=selected, max_lines=int(max_l),
                               lang=raw.get("lang"))
            out = relayout_task(task, rules, selected=None)
            out["reflow"] = flow
            out["count"] = len(task.get("segments") or [])
            return out
        return relayout_task(task, rules, selected=selected)

    try:
        return mutate_task(_run, **_layout_params(ctx, params))
    except SubtitleIOError as exc:
        raise CommandError(str(exc)) from exc


def cmd_subtitle_check_cps(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import SubtitleIOError, mutate_task
    from cutora.subtitle.layout import DEFAULT_CPS, check_cps_task
    threshold = float(params["threshold"]) if params.get("threshold") is not None else DEFAULT_CPS

    def _run(task):
        hits = check_cps_task(task, threshold)
        return {"over": hits, "count": len(hits), "threshold": threshold}

    try:
        return mutate_task(_run, write=False, **_layout_params(ctx, params))
    except SubtitleIOError as exc:
        raise CommandError(str(exc)) from exc


def cmd_subtitle_extend_durations(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import SubtitleIOError, mutate_task
    from cutora.subtitle.layout import DEFAULT_CPS, DEFAULT_MIN_GAP, extend_durations_task
    threshold = float(params["threshold"]) if params.get("threshold") is not None else DEFAULT_CPS
    min_gap = float(params["min_gap"]) if params.get("min_gap") is not None else DEFAULT_MIN_GAP
    try:
        return mutate_task(
            lambda task: extend_durations_task(task, threshold, min_gap),
            **_layout_params(ctx, params),
        )
    except SubtitleIOError as exc:
        raise CommandError(str(exc)) from exc


def cmd_subtitle_import_file(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import (
        SubtitleFormatError,
        SubtitleIOError,
        import_subtitle,
    )
    try:
        return import_subtitle(
            path=str(params.get("path") or ""),
            mode=str(params.get("mode") or "replace"),
            fmt=params.get("format") or None,
            window=ctx.window,
            media_id=params.get("media_id"),
            media=str(params.get("media") or ""),
            project=params.get("project") or None,
        )
    except (SubtitleIOError, SubtitleFormatError) as exc:
        raise CommandError(str(exc)) from exc


def cmd_subtitle_export_file(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import (
        SubtitleFormatError,
        SubtitleIOError,
        export_subtitle,
    )
    karaoke = params.get("karaoke")
    try:
        return export_subtitle(
            path=str(params.get("path") or ""),
            fmt=params.get("format") or None,
            window=ctx.window,
            media_id=params.get("media_id"),
            media=str(params.get("media") or ""),
            project=params.get("project") or None,
            bom=bool(params.get("bom")),
            karaoke=True if karaoke is None else bool(karaoke),
        )
    except (SubtitleIOError, SubtitleFormatError) as exc:
        raise CommandError(str(exc)) from exc


def cmd_recipe_record_start(ctx: Context, params: dict) -> dict:
    from cutora.batch.recipe import get_recorder
    get_recorder().start()
    return {"recording": True}


def cmd_recipe_record_stop(ctx: Context, params: dict) -> dict:
    from cutora.batch.recipe import get_recorder
    steps = get_recorder().stop()
    return {"recording": False, "steps": steps, "count": len(steps)}


def cmd_recipe_apply_folder(ctx: Context, params: dict) -> dict:
    from cutora.batch.recipe import RecipeError, apply_to_folder, load_recipe
    recipe_path = str(params.get("recipe") or "")
    folder = str(params.get("folder") or "")
    out_dir = str(params.get("out_dir") or "")
    jobs = params.get("jobs")
    recursive = bool(params.get("recursive"))
    try:
        recipe = load_recipe(recipe_path)
        return apply_to_folder(
            recipe, folder, out_dir, jobs=jobs, recursive=recursive)
    except RecipeError as exc:
        raise CommandError(str(exc)) from exc


def cmd_audio_loudness_set(ctx: Context, params: dict) -> dict:
    from cutora.audio.loudness import (
        MODES, get_loudness_settings, set_loudness_settings,
    )
    raw = str(params.get("mode") or "").strip().lower()
    aliases = {"-14": "lufs14", "14": "lufs14", "-16": "lufs16", "16": "lufs16"}
    mode = aliases.get(raw, raw)
    if mode not in MODES:
        raise CommandError("未知响度 mode：%s（off / lufs14 / lufs16 / custom）" % raw)
    before = get_loudness_settings()
    after = set_loudness_settings(mode, params.get("target_lufs"))
    return {
        "ok": True,
        "mode": after.mode,
        "target_lufs": after.target_i,
        "enabled": after.enabled,
        "previous": {"mode": before.mode, "target_lufs": before.target_i},
    }


def cmd_export_profile_list(ctx: Context, params: dict) -> dict:
    from cutora.render.export_profile import list_profiles, resolve_profile_id
    rows = list_profiles()
    return {"profiles": rows, "active": resolve_profile_id(), "count": len(rows)}


def cmd_export_profile_save(ctx: Context, params: dict) -> dict:
    from cutora.render.export_profile import save_profile
    try:
        row = save_profile(params)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    return {"ok": True, "profile": row}


def cmd_export_profile_delete(ctx: Context, params: dict) -> dict:
    from cutora.render.export_profile import delete_profile
    try:
        delete_profile(str(params.get("id") or ""))
    except (ValueError, KeyError) as exc:
        raise CommandError(str(exc)) from exc
    return {"ok": True, "id": str(params.get("id") or "")}


def cmd_export_settings(ctx: Context, params: dict) -> dict:
    from cutora.core.history import (
        _load_app_settings, _load_export_video_mode, _load_gpu_enabled,
    )
    s = strip_secret_fields(_load_app_settings() or {})
    from cutora.audio.loudness import get_loudness_settings
    ln = get_loudness_settings(s)
    return {
        "export_video_mode": _load_export_video_mode(),
        "parallel_export_enabled": s.get("parallel_export_enabled"),
        "parallel_export_workers": s.get("parallel_export_workers"),
        "gpu_enabled": _load_gpu_enabled(),
        "loudness_mode": ln.mode,
        "loudness_target_lufs": ln.target_i,
        "export_profile_id": s.get("export_profile_id"),
    }


def _edit_selected(ctx: Context, params: dict):
    if params.get("seg_ids"):
        return list(params["seg_ids"])
    if str(params.get("scope") or "all") == "selected" and ctx.window is not None:
        tl = getattr(ctx.window, "timeline", None)
        return list(getattr(tl, "selected_indices", None) or [])
    return None


def _resolve_selected(task, raw):
    if raw is None:
        return None
    return set(_idxs_for(task, raw))


def _mutate_edit(ctx: Context, params: dict, fn, write=True):
    from cutora.subtitle.io import SubtitleIOError, mutate_task
    try:
        return mutate_task(fn, write=write, **_layout_params(ctx, params))
    except SubtitleIOError as exc:
        raise CommandError(str(exc)) from exc
    except ValueError as exc:
        raise CommandError(str(exc)) from exc


def _image_overlay_at(task: dict, index: int) -> dict:
    ovs = list(task.get("image_overlays") or [])
    if index < 0 or index >= len(ovs):
        raise ValueError("overlay_index 不存在")
    ov = ovs[index]
    if not isinstance(ov, dict):
        raise ValueError("overlay_index 不是覆层")
    return ov


def _set_clip_fade(task: dict, track_id, clip_index: int, vals: tuple,
                   offsets=None) -> None:
    from cutora.render.textfx.fade import write_fade
    tl = task.get("timeline")
    tracks = getattr(tl, "tracks", None)
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    tid = str(track_id or "")
    for tr in tracks or []:
        cur = getattr(tr, "id", None) if not isinstance(tr, dict) else tr.get("id")
        if str(cur) != tid:
            continue
        clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
        clips = list(clips or [])
        if clip_index < 0 or clip_index >= len(clips):
            raise ValueError("clip_index 不存在")
        write_fade(clips[clip_index], *vals, offsets=offsets)
        return
    raise ValueError("track_id 不存在")


def _fade_offsets_from_params(params: dict):
    keys = (
        "fade_top_offset", "fade_bottom_offset",
        "fade_left_offset", "fade_right_offset",
    )
    if not any(k in params for k in keys):
        return None
    return tuple(float(params.get(k, 0) or 0) for k in keys)


def cmd_overlay_set_fade(ctx: Context, params: dict) -> dict:
    from cutora.render.textfx.fade import write_fade
    target = str(params.get("target") or "overlay")
    vals = (
        params.get("fade_top", 0) or 0,
        params.get("fade_bottom", 0) or 0,
        params.get("fade_left", 0) or 0,
        params.get("fade_right", 0) or 0,
    )
    offsets = _fade_offsets_from_params(params)

    def fn(task):
        written = None
        if target == "clip":
            _set_clip_fade(
                task, params.get("track_id"),
                int(params.get("clip_index", 0) or 0), vals, offsets=offsets)
        elif target == "element":
            ov = _image_overlay_at(task, int(params.get("overlay_index", 0) or 0))
            els = ov.get("elements") or []
            idx = int(params.get("element_index", 0) or 0)
            if idx < 0 or idx >= len(els) or not isinstance(els[idx], dict):
                raise ValueError("element_index 不存在")
            written = write_fade(els[idx], *vals, offsets=offsets)
        else:
            ov = _image_overlay_at(task, int(params.get("overlay_index", 0) or 0))
            written = write_fade(ov, *vals, offsets=offsets)
        return {"ok": True, "target": target, "fade": written}

    return _mutate_edit(ctx, params, fn)


def cmd_overlay_split(ctx: Context, params: dict) -> dict:
    """Split image/text overlay at t. portable=False, undoable via mutate_task."""
    kind = str(params.get("kind") or "")
    if kind not in ("image", "text"):
        raise CommandError("overlay.split kind 必须是 image 或 text")
    idx = int(params["idx"])
    t = float(params["t"])

    def fn(task):
        from cutora.model.track_guard import assert_type_unlocked
        for tr_type in ("video", "image", "text"):
            try:
                assert_type_unlocked(task, tr_type)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
        from cutora.ui.timeline.tools import split_overlay
        key = "image_overlays" if kind == "image" else "text_overlays"
        bag = list(task.get(key) or [])
        if idx < 0 or idx >= len(bag):
            raise CommandError("overlay idx 越界")
        right = split_overlay(bag[idx], t)
        if right is None:
            raise CommandError("无法在该时刻切开覆层")
        bag.insert(idx + 1, right)
        task[key] = bag
        return {"ok": True, "kind": kind, "idx": idx, "t": t}

    return _mutate_edit(ctx, params, fn)


def build_sticker_overlay(sid, w, h, start, end, anim_in="fade", anim_out="fade") -> dict:
    """Image-track overlay for one sticker. Aspect lock starts on."""
    import time as _time
    from cutora.render.stickers import default_size_px
    size = default_size_px(int(w), int(h))
    return {
        "type": "image",
        "start": float(start),
        "end": float(end),
        "x": 0, "y": 0, "w": w, "h": h,
        "rotation": 0, "opacity": 255,
        "elements": [{
            "kind": "image",
            "sticker_id": sid,
            "x": (w - size) / 2.0,
            "y": (h - size) / 2.0,
            "w": size,
            "h": size,
            "rotation": 0.0,
            "opacity": 255.0,
            "blend_mode": "normal",
            "anim_in": anim_in,
            "anim_out": anim_out,
            "anim_in_s": 0.25,
            "anim_out_s": 0.25,
            "lock_aspect": True,
            "aspect_ratio": 1.0,
        }],
        "scale": 1.0,
        "flip_x": False,
        "flip_y": False,
        "lock_aspect": True,
        "keep_aspect": True,
        "aspect_ratio": 1.0,
        "blend_mode": "normal",
        "id": "ov_sticker_%d" % int(_time.time() * 1000),
    }


def cmd_overlay_insert_sticker(ctx: Context, params: dict) -> dict:
    from cutora.render.stickers import anim_is_allowed, known_id
    from cutora.subtitle.io import canvas_size

    sid = str(params.get("sticker_id") or "").strip().lower()
    if not sid or not known_id(sid):
        raise CommandError("未知 sticker_id")
    anim_in = str(params.get("anim_in") or "fade")
    anim_out = str(params.get("anim_out") or "fade")
    if not anim_is_allowed(anim_in):
        anim_in = "fade"
    if not anim_is_allowed(anim_out):
        anim_out = "fade"

    def fn(task):
        w, h = canvas_size(task)
        start = float(params.get("start") or 0.0)
        end = params.get("end")
        if end is None:
            dur = 0.0
            try:
                dur = float(task.get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            if dur <= 0:
                ends = []
                for seg in (task.get("segments") or []):
                    if isinstance(seg, dict):
                        try:
                            ends.append(float(seg.get("end") or 0.0))
                        except (TypeError, ValueError):
                            pass
                dur = max(ends) if ends else 5.0
            end = start + dur if dur > start else start + 5.0
        else:
            end = float(end)
        if end <= start:
            end = start + 5.0
        ov = build_sticker_overlay(sid, w, h, start, end, anim_in, anim_out)
        task.setdefault("image_overlays", []).append(ov)
        return {"ok": True, "sticker_id": sid, "overlay_id": ov["id"]}

    return _mutate_edit(ctx, params, fn)


def cmd_subtitle_set_text(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import set_text_task
    text = str(params.get("text") or "")
    return _mutate_edit(
        ctx, params,
        lambda task: set_text_task(task, _idx_for(task, params["seg_id"]), text))


def cmd_subtitle_merge(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import merge_task
    direction = str(params.get("direction") or "next")
    return _mutate_edit(
        ctx, params,
        lambda task: merge_task(task, _idx_for(task, params["seg_id"]), direction))


def cmd_subtitle_split(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import split_task
    at_char = params.get("at_char")
    time = params.get("time")
    return _mutate_edit(
        ctx, params,
        lambda task: split_task(
            task, _idx_for(task, params["seg_id"]),
            at_char=None if at_char is None else int(at_char),
            time=None if time is None else float(time),
        ),
    )


def cmd_subtitle_delete(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import delete_task
    return _mutate_edit(
        ctx, params,
        lambda task: delete_task(task, _idx_for(task, params["seg_id"])))


def _require_subs_unlocked(task):
    from cutora.model.track_guard import assert_type_unlocked
    assert_type_unlocked(task, "subs")


def cmd_subtitle_trim_segment(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import trim_segment_task
    def _fn(task):
        _require_subs_unlocked(task)
        return trim_segment_task(
            task, _idx_for(task, params["seg_id"]),
            float(params["start"]), float(params["end"]),
        )
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_trim_word(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.word_trim import trim_word_task
    def _fn(task):
        _require_subs_unlocked(task)
        return trim_word_task(
            task, _idx_for(task, params["seg_id"]),
            int(params["word_index"]),
            str(params.get("edge") or ""),
            float(params["t"]),
        )
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_move_segments(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import move_segments_task
    def _fn(task):
        _require_subs_unlocked(task)
        rows = []
        for row in params.get("segments") or []:
            if not isinstance(row, dict):
                raise ValueError("segments 项必须是对象")
            item = dict(row)
            item["seg_id"] = _idx_for(task, row.get("seg_id"))
            rows.append(item)
        return move_segments_task(task, rows)
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_retime_segments(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import retime_segments_task
    def _fn(task):
        _require_subs_unlocked(task)
        return retime_segments_task(
            task, _idxs_for(task, params.get("seg_ids") or []),
            float(params["pivot"]), float(params["scale"]),
        )
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_delete_segments(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import delete_segments_task
    def _fn(task):
        _require_subs_unlocked(task)
        return delete_segments_task(task, _idxs_for(task, params.get("seg_ids") or []))
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_duplicate_segments(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import duplicate_segments_task
    def _fn(task):
        _require_subs_unlocked(task)
        return duplicate_segments_task(
            task,
            seg_ids=_idxs_for(task, params.get("seg_ids") or []) or None,
            items=params.get("items"),
            time=float(params.get("time") or 0.0),
        )
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_emphasis_auto(ctx: Context, params: dict) -> dict:
    from cutora.render.textfx.spec import EMPH_AUTO

    def fn(task):
        style = task.setdefault("style", {})
        fx = dict(style.get("fx") or {})
        emph = dict(fx.get("emphasis") or {})
        if "auto" in params and params.get("auto") is not None:
            emph["auto"] = [
                str(x) for x in (params.get("auto") or []) if str(x) in EMPH_AUTO
            ]
        if "words" in params and params.get("words") is not None:
            emph["words"] = [
                str(x).strip() for x in (params.get("words") or []) if str(x).strip()
            ]
        words = list(emph.get("words") or [])
        add = str(params.get("add_word") or "").strip()
        if add:
            folded = add.casefold()
            if not any(str(w).casefold() == folded for w in words):
                words.append(add)
            emph["words"] = words
        rem = str(params.get("remove_word") or "").strip()
        if rem:
            folded = rem.casefold()
            emph["words"] = [w for w in words if str(w).casefold() != folded]
        if params.get("max_per_cue") is not None:
            try:
                n = int(params.get("max_per_cue"))
            except (TypeError, ValueError):
                n = 2
            emph["max_per_cue"] = max(0, min(8, n))
        fx["emphasis"] = emph
        style["fx"] = fx
        return {"ok": True, "emphasis": emph}

    return _mutate_edit(ctx, params, fn)


def cmd_subtitle_lock(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import lock_task
    locked = bool(params.get("locked", True))
    def _fn(task):
        ids = _idxs_for(task, params.get("seg_ids") or [])
        if not ids and params.get("seg_id") is not None:
            ids = [_idx_for(task, params["seg_id"])]
        return lock_task(task, ids, locked)
    return _mutate_edit(ctx, params, _fn)


def cmd_subtitle_find(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import find_task
    query = str(params.get("query") or "")
    return _mutate_edit(
        ctx, params,
        lambda task: find_task(
            task, query,
            regex=bool(params.get("regex")),
            case=bool(params.get("case")),
        ),
        write=False,
    )


def cmd_subtitle_replace(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import replace_task
    selected = _edit_selected(ctx, params)
    query = str(params.get("query") or "")
    replacement = str(params.get("replacement") or "")
    return _mutate_edit(
        ctx, params,
        lambda task: replace_task(
            task, query, replacement,
            regex=bool(params.get("regex")),
            case=bool(params.get("case")),
            selected=_resolve_selected(task, selected),
        ),
    )


def cmd_subtitle_transform_case(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import transform_case_task
    selected = _edit_selected(ctx, params)
    mode = str(params.get("mode") or "lower")
    return _mutate_edit(
        ctx, params,
        lambda task: transform_case_task(
            task, mode, selected=_resolve_selected(task, selected)),
    )


def cmd_subtitle_strip_end_punct(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import strip_end_punct_task
    selected = _edit_selected(ctx, params)
    return _mutate_edit(
        ctx, params,
        lambda task: strip_end_punct_task(
            task, selected=_resolve_selected(task, selected)),
    )


def cmd_subtitle_apply_glossary(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.edit import apply_glossary_task, load_glossary_csv
    selected = _edit_selected(ctx, params)
    pairs = []
    path = str(params.get("csv") or "")
    if path:
        try:
            pairs = load_glossary_csv(path)
        except OSError as exc:
            raise CommandError("无法读取词典 CSV：%s" % exc) from exc
    raw = params.get("pairs") or []
    for row in raw:
        if isinstance(row, (list, tuple)) and row:
            pairs.append((str(row[0]), str(row[1]) if len(row) > 1 else ""))
        elif isinstance(row, dict) and row.get("from"):
            pairs.append((str(row.get("from")), str(row.get("to") or "")))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return _mutate_edit(
        ctx, params,
        lambda task: apply_glossary_task(
            task, pairs, selected=_resolve_selected(task, selected)),
    )


def cmd_stylepack_list(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import list_pack_dirs, load_pack, sanitize_pack_fonts, StylepackError
    rows = []
    for path in list_pack_dirs():
        try:
            pack = sanitize_pack_fonts(load_pack(path))
        except StylepackError:
            continue
        meta = pack.get("meta") or {}
        rows.append({
            "id": pack.get("id"),
            "path": pack.get("path"),
            "name": meta.get("name"),
            "tags": meta.get("tags") or [],
            "font_missing": bool(pack.get("font_missing")),
            "preview": pack.get("preview") or "",
            "builtin": bool(pack.get("builtin")),
        })
    return {"packs": rows, "count": len(rows)}


def cmd_text_preset_list(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.text_preset import list_presets
    rows = []
    for p in list_presets():
        lay = p.get("layout") or {}
        rows.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "text": p.get("text"),
            "anchor": lay.get("anchor"),
            "builtin": bool(p.get("builtin")),
        })
    return {"presets": rows, "count": len(rows)}


def cmd_text_preset_save(ctx: Context, params: dict) -> dict:
    from cutora.core.commands import CommandError
    from cutora.subtitle.io import canvas_size, window_curr_idx
    from cutora.subtitle.text_preset import preset_from_overlay, save_preset
    raw = dict(params or {})
    ov = raw.get("overlay") if isinstance(raw.get("overlay"), dict) else None
    win = ctx.window
    cw = ch = None
    task = None
    if win is not None:
        tasks = getattr(win, "tasks", None) or []
        idx = window_curr_idx(win)
        task = tasks[idx] if 0 <= idx < len(tasks) else None
        if task is not None:
            cw, ch = canvas_size(task)
            if ov is None:
                ovs = list(task.get("text_overlays") or [])
                if ovs:
                    ov = ovs[-1]
    if ov is not None and cw and ch:
        built = preset_from_overlay(
            ov, cw, ch,
            name=str(raw.get("name") or ""),
            preset_id=raw.get("id"),
        )
        if raw.get("text") is not None:
            built["text"] = str(raw.get("text"))
        try:
            saved = save_preset(built)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        return {"ok": True, "preset": saved}
    try:
        saved = save_preset(raw)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    return {"ok": True, "preset": saved}


def cmd_text_preset_delete(ctx: Context, params: dict) -> dict:
    from cutora.core.commands import CommandError
    from cutora.subtitle.text_preset import delete_preset
    try:
        ok = delete_preset(str(params.get("id") or ""))
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    if not ok:
        raise CommandError("文本预设不存在")
    return {"ok": True}


def cmd_text_preset_apply(ctx: Context, params: dict) -> dict:
    from cutora.core.commands import CommandError
    from cutora.subtitle.text_preset import apply_to_task, resolve_preset
    pid = str(params.get("id") or params.get("name") or "").strip()
    preset = resolve_preset(pid)
    if preset is None:
        raise CommandError("文本预设不存在: %s" % pid)
    idem = bool(params.get("idempotent"))
    proj = str(params.get("project") or "").strip()
    if proj and (idem or bool(params.get("all"))):
        from cutora.subtitle.text_preset import apply_to_project_file
        try:
            return apply_to_project_file(proj, preset["id"], idempotent=True if idem else bool(params.get("all")))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except OSError as exc:
            raise CommandError("无法写工程: %s" % exc) from exc

    def fn(task):
        return apply_to_task(task, preset, idempotent=idem)

    return _mutate_edit(ctx, params, fn)


def cmd_stylepack_apply(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.io import window_curr_idx
    from cutora.subtitle.stylepack import (
        StylepackError, apply_pack_to_task, diag, load_pack, style_snapshot,
    )
    try:
        pack = load_pack(str(params.get("path") or ""))
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc
    apply_anim = True if params.get("apply_animation") is None else bool(params.get("apply_animation"))
    win = ctx.window
    curr = window_curr_idx(win) if win is not None else -1
    diag(
        "cmd.apply.params",
        params=dict(params or {}),
        has_window=win is not None,
        curr_idx=curr,
        pack_id=pack.get("id"),
        apply_animation=apply_anim,
    )

    def _run(task):
        live = None
        if win is not None:
            tasks = getattr(win, "tasks", None) or []
            if 0 <= curr < len(tasks):
                live = tasks[curr]
        diag(
            "cmd.apply.task_identity",
            is_live=task is live,
            task_id=id(task),
            live_id=id(live) if live is not None else 0,
            before=style_snapshot(task.get("style"), task.get("layout_rules")),
        )
        out = apply_pack_to_task(task, pack, apply_animation=apply_anim)
        diag(
            "cmd.apply.written",
            is_live=task is live,
            after=style_snapshot(task.get("style"), task.get("layout_rules")),
        )
        return out

    result = _mutate_edit(ctx, params, _run)
    diag("cmd.apply.result", result=result)
    return result


def cmd_stylepack_import(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, import_cutorastyle
    try:
        dest = import_cutorastyle(str(params.get("path") or ""))
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc
    return {"path": dest}


def cmd_stylepack_export(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, export_cutorastyle
    try:
        dest = export_cutorastyle(
            str(params.get("pack_dir") or ""), str(params.get("path") or ""))
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc
    return {"path": dest}


def cmd_stylepack_save_current(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, save_task_as_pack

    def _run(task):
        try:
            return save_task_as_pack(task, params.get("name"), params.get("tags"))
        except StylepackError as exc:
            raise CommandError(str(exc)) from exc

    return _mutate_edit(ctx, params, _run, write=False)


def cmd_stylepack_update(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import (
        ANIM_KEYS, VERSION, StylepackError, update_pack_dir,
    )

    def _run(task):
        from cutora.subtitle.io import canvas_size
        from cutora.subtitle.stylepack import migrate_pack_style
        cw, ch = canvas_size(task)
        style = migrate_pack_style(dict(task.get("style") or {}), cw, ch)
        body = {
            "version": VERSION,
            "style": style,
            "layout_rules": dict(task.get("layout_rules") or {
                "max_chars_per_line": 42, "max_lines": 2, "balance_lines": True,
            }),
            "keyword_rules": list(style.get("keyword_rules") or []),
            "animation": {k: style.get(k) for k in ANIM_KEYS if k in style},
        }
        try:
            return update_pack_dir(str(params.get("path") or ""), body)
        except StylepackError as exc:
            raise CommandError(str(exc)) from exc

    return _mutate_edit(ctx, params, _run, write=False)


def cmd_stylepack_delete(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, delete_pack_dir
    try:
        dest = delete_pack_dir(str(params.get("path") or ""))
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc
    return {"path": dest}


def cmd_stylepack_rename(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, rename_pack
    try:
        return rename_pack(str(params.get("path") or ""), str(params.get("name") or ""))
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc


def cmd_stylepack_convert_preset(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.stylepack import StylepackError, convert_preset_to_pack
    name = str(params.get("name") or "").strip()
    try:
        dest = convert_preset_to_pack(name)
    except StylepackError as exc:
        raise CommandError(str(exc)) from exc
    return {"path": dest, "name": name}


def cmd_subtitle_check_overlong(ctx: Context, params: dict) -> dict:
    from cutora.subtitle.layout import check_overlong_task

    def _run(task):
        hits = check_overlong_task(task)
        return {"over": hits, "count": len(hits)}

    return _mutate_edit(ctx, params, _run, write=False)


def _kf_host(task, params):
    """定位命令要改的对象。带 node_id 时 → 这个图层效果节点图里那个节点的 params（Q152-F：
    遮罩 / 抠像 / AI 抠像的命令都能直接改节点，节点 params 和图层老键同形）。"""
    host, tr = _kf_host_layer(task, params)
    nid = params.get("node_id")
    if nid:
        from cutora.fx import graph as G
        g = G.graph_of(host)
        n = G.node_by_id(g, str(nid)) if g else None
        if n is None:
            raise ValueError("节点 %s 不存在" % nid)
        return n["params"], tr
    return host, tr


def _kf_host_layer(task, params):
    from cutora.model.track_guard import assert_unlocked

    kind = str(params.get("target") or "clip")
    if kind == "style":
        style = task.get("style")
        if not isinstance(style, dict):
            style = {}
            task["style"] = style
        return style, None
    if kind == "overlay":
        list_name = str(params.get("list") or "text_overlays")
        if list_name not in ("text_overlays", "image_overlays"):
            raise ValueError("list 必须是 text_overlays 或 image_overlays")
        ovs = task.get(list_name)
        if not isinstance(ovs, list):
            raise ValueError("没有 %s" % list_name)
        idx = int(params.get("overlay_index", 0) or 0)
        if idx < 0 or idx >= len(ovs) or not isinstance(ovs[idx], dict):
            raise ValueError("overlay_index 不存在")
        host = ovs[idx]
        if "element_index" in params and params.get("element_index") is not None:
            els = host.get("elements")
            if not isinstance(els, list):
                raise ValueError("没有 elements")
            ei = int(params.get("element_index"))
            if ei < 0 or ei >= len(els) or not isinstance(els[ei], dict):
                raise ValueError("element_index 不存在")
            return els[ei], None
        return host, None
    tid = str(params.get("track_id") or "")
    clip_index = int(params.get("clip_index", 0) or 0)
    tl = task.get("timeline")
    tracks = getattr(tl, "tracks", None)
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    for tr in tracks or []:
        cur = getattr(tr, "id", None) if not isinstance(tr, dict) else tr.get("id")
        if str(cur) != tid:
            continue
        if isinstance(tr, dict):
            if tr.get("locked"):
                raise ValueError("轨道已锁定")
        else:
            assert_unlocked(task, track=tr)
        clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
        clips = list(clips or [])
        if clip_index < 0 or clip_index >= len(clips):
            raise ValueError("clip_index 不存在")
        return clips[clip_index], tr
    raise ValueError("track_id 不存在")


def _kf_value(params):
    if params.get("color"):
        return str(params.get("color"))
    if "v" in params and params.get("v") is not None:
        return params.get("v")
    return None


def cmd_keyframe_set(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import set_point

    def fn(task):
        host, _tr = _kf_host(task, params)
        path = str(params.get("path") or "")
        if not path:
            raise ValueError("缺少 path")
        val = _kf_value(params)
        if val is None:
            raise ValueError("缺少 v 或 color")
        rec = set_point(
            host, path, float(params["t"]), val,
            ease=str(params.get("ease") or "linear"),
            bez=params.get("bez"),
        )
        return {"ok": True, "path": path, "t": rec["t"], "v": rec["v"]}

    return _mutate_edit(ctx, params, fn)


def cmd_grade_set(ctx: Context, params: dict) -> dict:
    from cutora.render.grade import merge_grade

    def fn(task):
        kind = str(params.get("target") or "clip")
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        if kind == "project":
            task["grade"] = merge_grade(task.get("grade"), patch)
            return {"ok": True, "target": "project"}
        if kind == "track":
            tid = str(params.get("track_id") or "")
            tl = task.get("timeline")
            tracks = getattr(tl, "tracks", None) if tl is not None else None
            if tracks is None and isinstance(tl, dict):
                tracks = tl.get("tracks") or []
            for tr in tracks or []:
                cur = getattr(tr, "id", None) if not isinstance(tr, dict) else tr.get("id")
                if str(cur) != tid:
                    continue
                if isinstance(tr, dict):
                    tr["grade"] = merge_grade(tr.get("grade"), patch)
                else:
                    tr.grade = merge_grade(getattr(tr, "grade", None), patch)
                return {"ok": True, "target": "track", "track_id": tid}
            raise ValueError("track_id 不存在")
        host, _tr = _kf_host(task, params)
        if isinstance(host, dict):
            host["grade"] = merge_grade(host.get("grade"), patch)
        else:
            host.grade = merge_grade(getattr(host, "grade", None), patch)
        return {"ok": True, "target": "clip"}

    return _mutate_edit(ctx, params, fn)


def _fx_get(host, key):
    return host.get(key) if isinstance(host, dict) else getattr(host, key, None)


def _fx_set(host, key, value):
    if isinstance(host, dict):
        if value in (None, [], {}):
            host.pop(key, None)
        else:
            host[key] = value
    else:
        setattr(host, key, value if value is not None else ([] if key == "masks" else {}))


def cmd_fx_mask_add(ctx: Context, params: dict) -> dict:
    from cutora.fx.masks import normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        m = normalize(params.get("mask") if isinstance(params.get("mask"), dict) else {})
        if m is None:
            raise ValueError("mask 不合法（多边形至少 3 个点）")
        masks = list(_fx_get(host, "masks") or [])
        if any(isinstance(x, dict) and x.get("id") == m["id"] for x in masks):
            raise ValueError("已有同 id 的遮罩")
        masks.append(m)
        _fx_set(host, "masks", masks)
        return {"ok": True, "mask_id": m["id"], "count": len(masks)}

    return _mutate_edit(ctx, params, fn)


def cmd_fx_mask_update(ctx: Context, params: dict) -> dict:
    from cutora.fx.masks import normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        mid = str(params.get("mask_id") or "")
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        masks = list(_fx_get(host, "masks") or [])
        for i, m in enumerate(masks):
            if isinstance(m, dict) and m.get("id") == mid:
                merged = dict(m)
                merged.update(patch)
                merged["id"] = mid
                n = normalize(merged)
                if n is None:
                    raise ValueError("改完的遮罩不合法")
                masks[i] = n
                _fx_set(host, "masks", masks)
                return {"ok": True, "mask_id": mid}
        raise ValueError("找不到遮罩 %s" % mid)

    return _mutate_edit(ctx, params, fn)


def cmd_fx_mask_delete(ctx: Context, params: dict) -> dict:
    def fn(task):
        host, _tr = _kf_host(task, params)
        mid = str(params.get("mask_id") or "")
        masks = list(_fx_get(host, "masks") or [])
        keep = [m for m in masks if not (isinstance(m, dict) and m.get("id") == mid)]
        if len(keep) == len(masks):
            raise ValueError("找不到遮罩 %s" % mid)
        _fx_set(host, "masks", keep)
        return {"ok": True, "count": len(keep)}

    return _mutate_edit(ctx, params, fn)


def cmd_fx_mask_reorder(ctx: Context, params: dict) -> dict:
    def fn(task):
        host, _tr = _kf_host(task, params)
        order = [str(x) for x in (params.get("order") or [])]
        masks = list(_fx_get(host, "masks") or [])
        by_id = {m.get("id"): m for m in masks if isinstance(m, dict)}
        if sorted(order) != sorted(by_id):
            raise ValueError("order 必须包含全部遮罩 id 且不重复")
        _fx_set(host, "masks", [by_id[i] for i in order])
        return {"ok": True}

    return _mutate_edit(ctx, params, fn)


def cmd_fx_keyer_set(ctx: Context, params: dict) -> dict:
    from cutora.fx.keyer import DEFAULTS, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        cur = dict(_fx_get(host, "keyer") or {})
        for k, v in patch.items():
            if k in DEFAULTS:
                cur[k] = v
        norm = normalize(cur)
        if not norm["enabled"] and all(norm[k] == DEFAULTS[k] for k in DEFAULTS if k != "enabled"):
            _fx_set(host, "keyer", {})
        else:
            _fx_set(host, "keyer", norm)
        return {"ok": True, "keyer": norm}

    return _mutate_edit(ctx, params, fn)


# ───────────────────────────── Q152-D · AI 抠像 ─────────────────────────────

def _matte_store(host, sp):
    from cutora.fx.ai_matte import is_empty
    _fx_set(host, "ai_matte", {} if is_empty(sp) else sp)


def cmd_ai_matte_set(ctx: Context, params: dict) -> dict:
    """合并 patch（enabled / model / feather / shrink / refine / view / objects 的 name·color·enabled）。"""
    from cutora.fx.ai_matte import DEFAULTS, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        cur = normalize(_fx_get(host, "ai_matte"))
        for k, v in patch.items():
            if k == "objects" and isinstance(v, list):
                by_id = {o["id"]: o for o in cur["objects"]}
                for item in v:
                    if not isinstance(item, dict):
                        continue
                    try:
                        oid = int(item.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if oid in by_id:
                        for kk in ("name", "color", "enabled", "mode"):
                            if kk in item:
                                by_id[oid][kk] = item[kk]
            elif k in DEFAULTS and k not in ("prompts", "session"):
                cur[k] = v
        sp = normalize(cur)
        _matte_store(host, sp)
        return {"ok": True, "ai_matte": sp}

    return _mutate_edit(ctx, params, fn)


def cmd_ai_matte_object(ctx: Context, params: dict) -> dict:
    """op=add 新建一个对象（最多 8 个）；op=remove 删对象及它的全部提示点。"""
    from cutora.fx.ai_matte import MAX_OBJECTS, new_object, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        sp = normalize(_fx_get(host, "ai_matte"))
        op = str(params.get("op") or "add")
        if op == "add":
            if len(sp["objects"]) >= MAX_OBJECTS:
                raise ValueError("最多 %d 个对象" % MAX_OBJECTS)
            obj = new_object(sp)
            name = params.get("name")
            if name:
                obj["name"] = str(name)
            sp["objects"].append(obj)
            _matte_store(host, sp)
            return {"ok": True, "object": obj, "ai_matte": sp}
        if op == "remove":
            oid = int(params.get("object_id"))
            before = len(sp["objects"])
            sp["objects"] = [o for o in sp["objects"] if o["id"] != oid]
            if len(sp["objects"]) == before:
                raise ValueError("对象 %d 不存在" % oid)
            for k in list(sp["prompts"]):
                sp["prompts"][k] = [p for p in sp["prompts"][k] if int(p.get("obj", 1)) != oid]
                if not sp["prompts"][k]:
                    sp["prompts"].pop(k)
            _matte_store(host, sp)
            return {"ok": True, "removed": oid, "ai_matte": sp}
        raise ValueError("op 必须是 add 或 remove")

    return _mutate_edit(ctx, params, fn)


def cmd_ai_matte_prompt(ctx: Context, params: dict) -> dict:
    """在某一源帧上加 / 删提示：op=add 加一个点或框；op=clear 清这一帧（object_id 可选）的提示。
    提示坐标是源帧归一化 0–1。只改数据；算 mask 由界面的后台任务做。"""
    from cutora.fx.ai_matte import normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        sp = normalize(_fx_get(host, "ai_matte"))
        if not sp["session"]:
            raise ValueError("先建会话（ai.matte.session）再加提示")
        fi = int(params.get("frame"))
        if fi < 0:
            raise ValueError("frame 不能为负")
        op = str(params.get("op") or "add")
        key = str(fi)
        if op == "add":
            prompt = params.get("prompt")
            if not isinstance(prompt, dict):
                raise ValueError("prompt 必须是对象")
            oid = int(prompt.get("obj", 1))
            if oid not in {o["id"] for o in sp["objects"]}:
                raise ValueError("对象 %d 不存在" % oid)
            lst = sp["prompts"].setdefault(key, [])
            if "box" in prompt:
                lst[:] = [p for p in lst if not (int(p.get("obj", 1)) == oid and "box" in p)]   # 一个对象一帧一个框
            lst.append(dict(prompt))
            sp = normalize(sp)
            if key not in sp["prompts"]:
                raise ValueError("prompt 格式不对（需要 x,y,label 或 box）")
            _matte_store(host, sp)
            return {"ok": True, "frame": fi, "prompts": sp["prompts"][key], "ai_matte": sp}
        if op == "clear":
            oid = params.get("object_id")
            if oid is None:
                sp["prompts"].pop(key, None)
            else:
                lst = [p for p in sp["prompts"].get(key, []) if int(p.get("obj", 1)) != int(oid)]
                if lst:
                    sp["prompts"][key] = lst
                else:
                    sp["prompts"].pop(key, None)
            _matte_store(host, sp)
            return {"ok": True, "frame": fi, "prompts": sp["prompts"].get(key, []), "ai_matte": sp}
        raise ValueError("op 必须是 add 或 clear")

    return _mutate_edit(ctx, params, fn)


def cmd_ai_matte_session(ctx: Context, params: dict) -> dict:
    """op=create 给图层建一个结果缓存会话（已有则原样返回）；op=clear 清掉全部提示和结果（保留对象）；
    op=detach 整个 ai_matte 拿掉并删缓存。"""
    from cutora.fx import matte_store as S
    from cutora.fx.ai_matte import new_object, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        sp = normalize(_fx_get(host, "ai_matte"))
        op = str(params.get("op") or "create")
        if op == "create":
            if not sp["session"]:
                sp["session"] = S.new_session_id()
            if params.get("model"):
                sp["model"] = str(params["model"])
            if not sp["objects"]:
                sp["objects"].append(new_object(sp))
            _matte_store(host, sp)
            return {"ok": True, "session": sp["session"], "ai_matte": sp}
        if op == "clear":
            if sp["session"]:
                try:
                    S.Session(sp["session"]).clear()
                except Exception:
                    pass
            sp["prompts"] = {}
            _matte_store(host, sp)
            return {"ok": True, "ai_matte": sp}
        if op == "detach":
            if sp["session"]:
                S.delete_session(sp["session"])
            _fx_set(host, "ai_matte", {})
            return {"ok": True, "ai_matte": {}}
        raise ValueError("op 必须是 create / clear / detach")

    return _mutate_edit(ctx, params, fn)


# ───────────────────────────── Q152-E · AI 跟踪 ─────────────────────────────

def _track_store(host, sp):
    from cutora.fx.tracking import is_empty
    _fx_set(host, "tracking", {} if is_empty(sp) else sp)


def _need_video_clip(host):
    if isinstance(host, dict) or not hasattr(host, "source_id"):
        raise ValueError("跟踪只能挂在视频片段上")


def cmd_track_session(ctx: Context, params: dict) -> dict:
    """op=create 建跟踪会话 + 第一个点（已有则原样）；op=clear 清掉全部点选和结果；op=detach 整个拿掉并删缓存。"""
    from cutora.fx import track_store as TS
    from cutora.fx.tracking import new_point, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        _need_video_clip(host)
        sp = normalize(_fx_get(host, "tracking"))
        op = str(params.get("op") or "create")
        if op == "create":
            if not sp["session"]:
                sp["session"] = TS.new_session_id()
            if params.get("model"):
                sp["model"] = str(params["model"])
            if not sp["points"]:
                sp["points"].append(new_point(sp))
            _track_store(host, sp)
            return {"ok": True, "session": sp["session"], "tracking": sp}
        if op == "clear":
            if sp["session"]:
                try:
                    TS.Session(sp["session"]).clear()
                except Exception:
                    pass
            sp["queries"] = {}
            _track_store(host, sp)
            return {"ok": True, "tracking": sp}
        if op == "detach":
            if sp["session"]:
                TS.delete_session(sp["session"])
            _fx_set(host, "tracking", {})
            return {"ok": True, "tracking": {}}
        raise ValueError("op 必须是 create / clear / detach")

    return _mutate_edit(ctx, params, fn)


def cmd_track_point(ctx: Context, params: dict) -> dict:
    """op=add 新建跟踪点；op=remove 删点（连同它所有帧上的点选）；
    op=place 在源帧 frame 上放 / 挪这个点（x, y 源帧归一化）；op=unplace 去掉这一帧上这个点的点选。"""
    from cutora.fx.tracking import MAX_POINTS, new_point, normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        _need_video_clip(host)
        sp = normalize(_fx_get(host, "tracking"))
        op = str(params.get("op") or "add")
        if op == "add":
            if len(sp["points"]) >= MAX_POINTS:
                raise ValueError("最多 %d 个跟踪点" % MAX_POINTS)
            pt = new_point(sp)
            if params.get("name"):
                pt["name"] = str(params["name"])
            sp["points"].append(pt)
            _track_store(host, sp)
            return {"ok": True, "point": pt, "tracking": sp}
        pid = int(params.get("point_id"))
        if pid not in {p["id"] for p in sp["points"]}:
            raise ValueError("跟踪点 %d 不存在" % pid)
        if op == "remove":
            sp["points"] = [p for p in sp["points"] if p["id"] != pid]
            for k in list(sp["queries"]):
                sp["queries"][k] = [q for q in sp["queries"][k] if int(q["pt"]) != pid]
                if not sp["queries"][k]:
                    sp["queries"].pop(k)
            _track_store(host, sp)
            return {"ok": True, "removed": pid, "tracking": sp}
        fi = int(params.get("frame"))
        if fi < 0:
            raise ValueError("frame 不能为负")
        key = str(fi)
        lst = [q for q in sp["queries"].get(key, []) if int(q["pt"]) != pid]
        if op == "place":
            lst.append({"pt": pid, "x": float(params.get("x")), "y": float(params.get("y"))})
        elif op != "unplace":
            raise ValueError("op 必须是 add / remove / place / unplace")
        if lst:
            sp["queries"][key] = lst
        else:
            sp["queries"].pop(key, None)
        sp = normalize(sp)
        _track_store(host, sp)
        return {"ok": True, "frame": fi, "tracking": sp}

    return _mutate_edit(ctx, params, fn)


def cmd_track_set(ctx: Context, params: dict) -> dict:
    """合并 patch：model、points（按 id 改 name / color / enabled）。"""
    from cutora.fx.tracking import normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        _need_video_clip(host)
        sp = normalize(_fx_get(host, "tracking"))
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        if patch.get("model"):
            sp["model"] = str(patch["model"])
        by_id = {p["id"]: p for p in sp["points"]}
        for item in patch.get("points") or []:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if pid in by_id:
                for k in ("name", "color", "enabled"):
                    if k in item:
                        by_id[pid][k] = item[k]
        sp = normalize(sp)
        _track_store(host, sp)
        return {"ok": True, "tracking": sp}

    return _mutate_edit(ctx, params, fn)


def cmd_track_apply(ctx: Context, params: dict) -> dict:
    """把算好的关键帧写到目标上，并把这条绑定记到被跟踪片段的 tracking.bindings（同一目标覆盖）。一次撤销。

    params：被跟踪片段的定位（track_id / clip_index）+ target_loc（目标的定位，形同 keyframe.replace）
    + paths（{参数: [点…]}）+ binding（track_bind.normalize_binding 的格式）。"""
    from cutora.fx.track_bind import normalize_binding, same_target
    from cutora.fx.tracking import normalize
    from cutora.render.keyframe import replace_paths

    def fn(task):
        host, _tr = _kf_host(task, params)
        _need_video_clip(host)
        binding = normalize_binding(params.get("binding"))
        if binding is None:
            raise ValueError("binding 格式不对")
        paths = params.get("paths")
        if not isinstance(paths, dict) or not paths:
            raise ValueError("paths 不能为空")
        loc = params.get("target_loc") if isinstance(params.get("target_loc"), dict) else None
        if binding["mode"] == "stabilize" or binding["target"]["kind"] == "self":
            target = host
        else:
            if loc is None:
                raise ValueError("缺 target_loc")
            target, _ttr = _kf_host(task, loc)
            if target is host:
                raise ValueError("跟随的目标不能是被跟踪的片段自己（要稳定请用稳定模式）")
        changed = replace_paths(target, paths)
        sp = normalize(_fx_get(host, "tracking"))
        sp["bindings"] = [b for b in sp["bindings"] if not same_target(b, binding)] + [binding]
        _track_store(host, sp)
        return {"ok": True, "paths": changed, "bindings": len(sp["bindings"])}

    return _mutate_edit(ctx, params, fn)


def cmd_track_unbind(ctx: Context, params: dict) -> dict:
    """删掉第 index 条绑定记录（已经写进去的关键帧不动，可在关键帧面板里删）。"""
    from cutora.fx.tracking import normalize

    def fn(task):
        host, _tr = _kf_host(task, params)
        _need_video_clip(host)
        sp = normalize(_fx_get(host, "tracking"))
        i = int(params.get("index"))
        if not (0 <= i < len(sp["bindings"])):
            raise ValueError("绑定 %d 不存在" % i)
        sp["bindings"].pop(i)
        _track_store(host, sp)
        return {"ok": True, "bindings": len(sp["bindings"])}

    return _mutate_edit(ctx, params, fn)


# ───────────────────────────── Q152-F · 效果节点图 ─────────────────────────────

def _graph_host(task, params):
    """节点图挂在图层上（params 里的 node_id 不参与定位）。"""
    p = dict(params)
    p.pop("node_id", None)
    host, tr = _kf_host_layer(task, p)
    if isinstance(host, dict) and host.get("type") == "text":
        raise ValueError("文字层没有节点图（用检查器里的遮罩）")
    return host, tr


def _graph_rw(host):
    from cutora.fx import graph as G
    g = G.graph_of(host)
    if g is None:
        raise ValueError("这个图层还没有节点图：先「转为节点」")
    return g


def _graph_store(host, g):
    from cutora.fx import graph as G
    _fx_set(host, "fx_graph", G.normalize(g))


def cmd_fx_graph(ctx: Context, params: dict) -> dict:
    """op=create：把图层上的蓝绿幕 / AI 抠像 / 遮罩搬进一张等价的节点图（老键清空）；
    op=delete：删掉节点图（图层回到直通）；op=replace：整张图换成 graph（粘贴 / 自动排布 / 撤销友好的批量改）。"""
    from cutora.fx import graph as G

    def fn(task):
        host, _tr = _graph_host(task, params)
        op = str(params.get("op") or "create")
        if op == "create":
            if G.has_graph(host):
                return {"ok": True, "graph": G.graph_of(host), "existed": True}
            g = G.from_legacy(host)
            for k in ("keyer", "masks", "ai_matte"):
                _fx_set(host, k, [] if k == "masks" else {})
            _graph_store(host, g)
            return {"ok": True, "graph": G.graph_of(host)}
        if op == "delete":
            _fx_set(host, "fx_graph", {})
            return {"ok": True}
        if op == "replace":
            g = G.normalize(params.get("graph"))
            if g is None:
                raise ValueError("graph 格式不对")
            old = G.graph_of(host)
            if old is not None:
                g["uid"] = old.get("uid") or g["uid"]
            _graph_store(host, g)
            return {"ok": True, "graph": G.graph_of(host)}
        raise ValueError("op 必须是 create / delete / replace")

    return _mutate_edit(ctx, params, fn)


def cmd_fx_node(ctx: Context, params: dict) -> dict:
    """节点增删改：op=add（type, pos, 可选 params、after=把它插到某节点后面）；op=remove（id，reconnect）；
    op=set（id, patch：label / color / enabled / pos / params 合并）；op=move（positions={id: [x, y]}，含 src / out）；
    op=swap（id：前两个同类输入互换）。"""
    from cutora.fx import graph as G

    def fn(task):
        host, _tr = _graph_host(task, params)
        g = _graph_rw(host)
        op = str(params.get("op") or "add")
        if op == "add":
            n = G.add_node(g, str(params.get("type") or ""), params.get("pos") or [0, 0],
                           params.get("params") if isinstance(params.get("params"), dict) else None)
            after = params.get("after")
            if after:
                G.insert_after(g, str(after), n["id"])
            _graph_store(host, g)
            return {"ok": True, "node": n["id"], "graph": G.graph_of(host)}
        if op == "remove":
            G.remove_node(g, str(params.get("id")), reconnect=bool(params.get("reconnect", True)))
            _graph_store(host, g)
            return {"ok": True, "graph": G.graph_of(host)}
        if op == "set":
            n = G.node_by_id(g, str(params.get("id")))
            if n is None:
                raise ValueError("节点 %s 不存在" % params.get("id"))
            patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
            for k in ("label", "color"):
                if k in patch:
                    n[k] = str(patch[k] or "")
            if "enabled" in patch:
                n["enabled"] = bool(patch["enabled"])
            if "pos" in patch:
                n["pos"] = [float(patch["pos"][0]), float(patch["pos"][1])]
            if isinstance(patch.get("params"), dict):
                n["params"].update(patch["params"])
            _graph_store(host, g)
            return {"ok": True, "graph": G.graph_of(host)}
        if op == "move":
            pos = params.get("positions") if isinstance(params.get("positions"), dict) else {}
            for nid, xy in pos.items():
                try:
                    xy = [float(xy[0]), float(xy[1])]
                except (TypeError, ValueError, IndexError):
                    continue
                if nid in (G.SRC, G.OUT):
                    g["io_pos"][nid] = xy
                    continue
                n = G.node_by_id(g, str(nid))
                if n is not None:
                    n["pos"] = xy
            _graph_store(host, g)
            return {"ok": True}
        if op == "swap":
            if not G.swap_inputs(g, str(params.get("id"))):
                raise ValueError("这个节点没有两个同类输入可换")
            _graph_store(host, g)
            return {"ok": True, "graph": G.graph_of(host)}
        raise ValueError("op 必须是 add / remove / set / move / swap")

    return _mutate_edit(ctx, params, fn)


def cmd_fx_link(ctx: Context, params: dict) -> dict:
    """连线：op=connect（from, to, port；类型不配或成环拒绝；目标端口原来的线被替换）；op=disconnect（to, port）。"""
    from cutora.fx import graph as G

    def fn(task):
        host, _tr = _graph_host(task, params)
        g = _graph_rw(host)
        op = str(params.get("op") or "connect")
        if op == "connect":
            G.connect(g, str(params.get("from")), str(params.get("to")), str(params.get("port") or "in"))
        elif op == "disconnect":
            G.disconnect(g, str(params.get("to")), str(params.get("port") or "in"))
        else:
            raise ValueError("op 必须是 connect / disconnect")
        _graph_store(host, g)
        return {"ok": True, "graph": G.graph_of(host)}

    return _mutate_edit(ctx, params, fn)


# ───────────────────────────── Q154 · 预合成（原位分组） ─────────────────────────────

def _group_tl(task):
    tl = task.get("timeline")
    if tl is None or not hasattr(tl, "tracks"):
        raise ValueError("这条视频还没有时间线")
    tl._task = task
    return tl


def _group_clips(task, refs) -> list:
    """[{track_id, clip_index}] 或 [{overlay_list, overlay_index}] 或 [{uid}] → [clip_or_ov]（守轨道锁；重复份不算）。"""
    from cutora.model import clip_groups as CG
    from cutora.model.track_guard import assert_unlocked
    tl = _group_tl(task)
    out = []
    by_uid = {}
    for tr, c in CG.iter_clips(tl):
        uid = str((c.get("uid") if isinstance(c, dict) else getattr(c, "uid", "")) or "")
        if uid:
            by_uid[uid] = (tr, c)
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        if ref.get("uid"):
            hit = by_uid.get(str(ref["uid"]))
            if hit is None:
                raise ValueError("片段 %s 不存在" % ref["uid"])
            tr, c = hit
        elif ref.get("overlay_list") or ref.get("list"):
            lname = str(ref.get("overlay_list") or ref.get("list"))
            oidx = int(ref.get("overlay_index", ref.get("index", 0)) or 0)
            c, _ = _kf_host_layer(task, {"target": "overlay", "list": lname, "overlay_index": oidx})
            tr = CG._overlay_track(tl, c, lname)
        else:
            c, tr = _kf_host_layer(task, {"target": "clip", "track_id": ref.get("track_id"),
                                          "clip_index": ref.get("clip_index", 0)})
        if tr is not None:
            assert_unlocked(task, track=tr)
        if CG.is_repeat(c):
            rep_of = str((CG.group_of(c) or {}).get("repeat_of") or "")
            c = next((x for _t, x in CG.iter_clips(tl)
                      if str((x.get("uid") if isinstance(x, dict) else getattr(x, "uid", "")) or "") == rep_of), None)
            if c is None:
                continue
        if not any(c is x for x in out):
            out.append(c)
    return out


def _group_after(task, host=None):
    from cutora.model import clip_groups as CG
    CG.resync_task(task, host)


def cmd_group_create(ctx: Context, params: dict) -> dict:
    """clips=[{track_id, clip_index} | {uid}]：打成一个预合成组（默认锁住组内编辑）。
    可选 name / color / locked / loop_fill。"""
    from cutora.model import clip_groups as CG

    def fn(task):
        clips = _group_clips(task, params.get("clips"))
        if not clips:
            raise ValueError("先选中至少一条片段")
        gid = CG.create(_group_tl(task), clips, name=str(params.get("name") or ""),
                        color=params.get("color") or None,
                        locked=bool(params.get("locked", True)),
                        loop_fill=bool(params.get("loop_fill", False)))
        _group_after(task, ctx.window)
        return {"ok": True, "group": gid, "count": len(clips),
                "meta": CG.meta(_group_tl(task), gid)}

    return _mutate_edit(ctx, params, fn)


def cmd_group_ungroup(ctx: Context, params: dict) -> dict:
    """group=组 id：解除预合成（片段留下，重复份删掉）。"""
    from cutora.model import clip_groups as CG

    def fn(task):
        tl = _group_tl(task)
        gid = str(params.get("group") or "")
        if CG.meta(tl, gid) is None:
            raise ValueError("这个组不存在了")
        n = CG.ungroup(tl, gid)
        return {"ok": True, "count": n}

    return _mutate_edit(ctx, params, fn)


def cmd_group_join(ctx: Context, params: dict) -> dict:
    """group=组 id，clips=[…]：把这些片段加入这个组。"""
    from cutora.model import clip_groups as CG

    def fn(task):
        tl = _group_tl(task)
        clips = _group_clips(task, params.get("clips"))
        n = CG.join(tl, str(params.get("group") or ""), clips)
        _group_after(task, ctx.window)
        return {"ok": True, "count": n}

    return _mutate_edit(ctx, params, fn)


def cmd_group_detach(ctx: Context, params: dict) -> dict:
    """clips=[…]：从各自的组里分离出来。"""
    from cutora.model import clip_groups as CG

    def fn(task):
        tl = _group_tl(task)
        clips = _group_clips(task, params.get("clips"))
        n = CG.detach(tl, clips)
        _group_after(task, ctx.window)
        return {"ok": True, "count": n}

    return _mutate_edit(ctx, params, fn)


def cmd_group_set(ctx: Context, params: dict) -> dict:
    """group=组 id，patch={name / color / locked / loop_fill}：改组的属性。loop_fill 一开就按组时长复制铺满。"""
    from cutora.model import clip_groups as CG

    def fn(task):
        tl = _group_tl(task)
        patch = params.get("patch") if isinstance(params.get("patch"), dict) else {}
        g = CG.set_meta(tl, str(params.get("group") or ""), **patch)
        _group_after(task, ctx.window)
        return {"ok": True, "meta": g}

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_replace(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import replace_paths

    def fn(task):
        host, _tr = _kf_host(task, params)
        paths = params.get("paths")
        if not isinstance(paths, dict) or not paths:
            raise ValueError("paths 必须是 {参数: [点…]} 的对象")
        for key, pts in paths.items():
            if pts is not None and not isinstance(pts, list):
                raise ValueError("%s 的点表必须是数组或 null" % key)
        changed = replace_paths(host, paths)
        return {"ok": True, "changed": changed}

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_delete(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import delete_point

    def fn(task):
        host, _tr = _kf_host(task, params)
        path = str(params.get("path") or "")
        if not delete_point(host, path, float(params["t"])):
            raise ValueError("该处没有关键帧")
        return {"ok": True, "path": path, "t": float(params["t"])}

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_move(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import move_point

    def fn(task):
        host, _tr = _kf_host(task, params)
        path = str(params.get("path") or "")
        if not move_point(host, path, float(params["t_from"]), float(params["t_to"])):
            raise ValueError("找不到要移动的关键帧")
        return {
            "ok": True, "path": path,
            "t_from": float(params["t_from"]), "t_to": float(params["t_to"]),
        }

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_set_interp(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import set_interp

    def fn(task):
        host, _tr = _kf_host(task, params)
        path = str(params.get("path") or "")
        if not set_interp(
            host, path, float(params["t"]),
            str(params.get("ease") or "linear"),
            bez=params.get("bez"),
        ):
            raise ValueError("该处没有关键帧")
        return {"ok": True, "path": path, "ease": str(params.get("ease"))}

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_enable(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import disable_path, enable_path, read_path

    def fn(task):
        host, _tr = _kf_host(task, params)
        path = str(params.get("path") or "")
        on = bool(params.get("enabled"))
        if on:
            val = _kf_value(params)
            if val is None:
                val = read_path(host, path)
            if val is None:
                val = 0.0
            t = float(params.get("t") or 0.0)
            return enable_path(host, path, t, val)
        return disable_path(host, path)

    return _mutate_edit(ctx, params, fn)


def cmd_keyframe_copy(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import copy_point

    def fn(task):
        host, _tr = _kf_host(task, params)
        rec = copy_point(host, str(params.get("path") or ""), float(params["t"]))
        if rec is None:
            raise ValueError("该处没有关键帧")
        return {"ok": True, "copied": rec}

    return _mutate_edit(ctx, params, fn, write=False)


def cmd_keyframe_paste(ctx: Context, params: dict) -> dict:
    from cutora.render.keyframe import clipboard, paste_point

    def fn(task):
        host, _tr = _kf_host(task, params)
        clip = clipboard()
        if clip is None:
            raise ValueError("关键帧剪贴板是空的")
        path = str(params.get("path") or clip.get("path") or "")
        rec = paste_point(host, path, float(params["t"]))
        if rec is None:
            raise ValueError("粘贴失败")
        return {"ok": True, "path": path, "t": rec["t"]}

    return _mutate_edit(ctx, params, fn)

