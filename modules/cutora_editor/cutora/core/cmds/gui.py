# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI command handlers. Call MainWindow body methods; never open dialogs."""
from __future__ import annotations

from cutora.core.commands import CommandError, Context, NeedsGui


def _win(ctx: Context):
    window = ctx.window
    if window is None:
        raise NeedsGui()
    return window


def _call(ctx: Context, method: str, **kwargs):
    window = _win(ctx)
    fn = getattr(window, method, None)
    if not callable(fn):
        raise CommandError("主窗口没有方法 %s" % method)
    window._bus_source = ctx.source or "ui"
    out = fn(**kwargs)
    return {} if out is None else out


def cmd_app_theme_set(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_set_theme", theme_id=str(params.get("theme_id") or ""))


def cmd_app_ui_mode_set(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_set_ui_mode", mode=str(params.get("mode") or "quick"))


def cmd_project_new(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_new")


def cmd_project_open(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_open", path=str(params.get("path") or ""))


def cmd_project_save(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_save")


def cmd_project_save_as(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_save_as", path=str(params.get("path") or ""))


def cmd_project_close(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_close")


def cmd_project_info(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_project_info")


def cmd_project_pack(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_project_pack",
        out_dir=str(params.get("out_dir") or ""),
        mode=str(params.get("mode") or "copy"),
    )


def cmd_project_autosave_status(ctx: Context, params: dict) -> dict:
    from cutora.core.autosave import status
    window = ctx.window
    path = getattr(window, "project_path", None) if window is not None else None
    if window is not None and hasattr(window, "command_autosave_status"):
        return window.command_autosave_status()
    return status(path)


def cmd_project_autosave_restore(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_autosave_restore", path=params.get("path"))


def cmd_media_import(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_media_import", paths=list(params.get("paths") or []))


def cmd_media_remove(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_media_remove", ids=list(params.get("ids") or []))


def cmd_media_list(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_media_list")


def cmd_media_info(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_media_info", media_id=int(params["id"]))


def cmd_media_select(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_media_select", ids=list(params.get("ids") or []))


def cmd_media_relink(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_media_relink",
        source_id=str(params.get("source_id") or ""),
        new_path=str(params.get("new_path") or ""),
    )


def cmd_media_relink_folder(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_media_relink_folder",
        old_root=str(params.get("old_root") or ""),
        new_root=str(params.get("new_root") or ""),
    )


def cmd_subtitle_auto(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_auto",
        media_id=params.get("media_id"),
        language=params.get("language"),
        model=params.get("model"),
        glossary=params.get("glossary"),
        force=bool(params.get("force")),
    )


def cmd_subtitle_list(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_subtitle_list", media_id=params.get("media_id"))


def cmd_subtitle_set_text(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_set_text",
        seg_id=params["seg_id"],
        text=str(params.get("text") or ""),
        media_id=params.get("media_id"),
    )


def cmd_subtitle_merge(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_merge",
        seg_id=params["seg_id"],
        media_id=params.get("media_id"),
    )


def cmd_subtitle_split(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_split",
        seg_id=params["seg_id"],
        time=params.get("time"),
        media_id=params.get("media_id"),
    )


def cmd_subtitle_delete(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_delete",
        seg_id=params["seg_id"],
        media_id=params.get("media_id"),
    )


def cmd_subtitle_apply_preset(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_subtitle_apply_preset",
        name=str(params.get("name") or ""),
        media_id=params.get("media_id"),
    )


def cmd_subtitle_word_appear(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_word_appear_set", on=bool(params.get("on")))


def cmd_style_apply_subtitle(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_apply_subtitle_preset", name=str(params.get("name") or ""))


def cmd_style_apply_text(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_apply_text_preset", name=str(params.get("name") or ""))


def cmd_style_apply_keyword(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_apply_keyword_preset", name=str(params.get("name") or ""))


def cmd_style_save_current(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_save_style_as", name=str(params.get("name") or ""))


def cmd_timeline_info(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_timeline_info")


def cmd_timeline_add_track(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_add_track", track_type=str(params.get("type") or ""))


def cmd_timeline_remove_track(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_remove_track", track_id=str(params.get("track_id") or ""))


def cmd_timeline_split_at(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_split_at", time=float(params["time"]))


def cmd_timeline_join_clips(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_join_clips",
        track_id=str(params.get("track_id") or ""),
        left_index=int(params["left_index"]),
        right_index=int(params["right_index"]),
    )


def cmd_timeline_add_transition(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_add_transition",
        track_id=str(params.get("track_id") or ""),
        start_t=float(params["start_t"]),
        duration=float(params["duration"]),
        preset=str(params.get("preset") or ""),
        domain=str(params.get("domain") or "visual"),
    )


def cmd_timeline_set_transition(ctx: Context, params: dict) -> dict:
    kwargs = {
        "track_id": str(params.get("track_id") or ""),
        "start_t": float(params["start_t"]) if params.get("start_t") is not None else None,
        "duration": float(params["duration"]) if params.get("duration") is not None else None,
        "transition_id": str(params.get("transition_id") or "") or None,
    }
    if params.get("preset") is not None:
        kwargs["preset"] = str(params.get("preset") or "")
    return _call(ctx, "command_set_transition", **kwargs)


def cmd_timeline_delete_transition(ctx: Context, params: dict) -> dict:
    kwargs = {
        "track_id": str(params.get("track_id") or ""),
        "transition_id": str(params.get("transition_id") or "") or None,
    }
    if params.get("start_t") is not None:
        kwargs["start_t"] = float(params["start_t"])
    if params.get("domain"):
        kwargs["domain"] = str(params.get("domain") or "")
    return _call(ctx, "command_delete_transition", **kwargs)


def cmd_timeline_razor(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_timeline_razor",
        time=float(params["time"]),
        clips=params.get("clips") or [],
        overlays=params.get("overlays") or [],
        subtitles=params.get("subtitles") or [],
    )


def cmd_timeline_delete_clip(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_delete_clip",
        track_id=str(params.get("track_id") or ""),
        clip_index=int(params["clip_index"]),
    )


def cmd_timeline_move_clip(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_move_clip",
        track_id=str(params.get("track_id") or ""),
        clip_index=int(params["clip_index"]),
        start=float(params["start"]),
    )


def cmd_timeline_trim_clip(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_trim_clip",
        track_id=str(params.get("track_id") or ""),
        clip_index=int(params["clip_index"]),
        in_t=float(params["in_t"]),
        out_t=float(params["out_t"]),
        start_t=float(params["start_t"]),
        speed=params.get("speed"),
    )


def cmd_timeline_trim_clips(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_trim_clips", clips=params.get("clips") or [])


def cmd_timeline_unlink_clips(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_unlink_clips", clips=params.get("clips") or [])


def cmd_timeline_link_clips(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_link_clips", clips=params.get("clips") or [])


def cmd_timeline_set_clip_fades(ctx: Context, params: dict) -> dict:
    kwargs = {
        "track_id": str(params.get("track_id") or ""),
        "clip_index": int(params["clip_index"]),
    }
    if params.get("fade_in_dur") is not None:
        kwargs["fade_in_dur"] = float(params["fade_in_dur"])
    if params.get("fade_out_dur") is not None:
        kwargs["fade_out_dur"] = float(params["fade_out_dur"])
    return _call(ctx, "command_set_clip_fades", **kwargs)


def cmd_timeline_move_clips(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_move_clips", clips=params.get("clips") or [])


def cmd_timeline_move_clip_to_track(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_move_clip_to_track",
        track_id=str(params.get("track_id") or ""),
        clip_index=int(params["clip_index"]),
        dest_track_id=str(params.get("dest_track_id") or ""),
        start=float(params["start"]),
    )


def cmd_timeline_duplicate_clips(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_duplicate_clips",
        clips=params.get("clips") or [],
        items=params.get("items") or [],
        time=float(params.get("time") or 0.0),
    )


def cmd_timeline_ripple_intro(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_ripple_intro",
        in_t=float(params["in_t"]),
        out_t=float(params["out_t"]),
        start_t=float(params["start_t"]),
        intro_duration=params.get("intro_duration"),
    )


def cmd_timeline_ripple_delete(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_ripple_delete", targets=params.get("targets") or [])


def cmd_timeline_ripple_insert(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_ripple_insert",
        t=float(params["t"]),
        duration=float(params["duration"]),
        track_id=params.get("track_id"),
    )


def cmd_timeline_close_gaps(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_close_gaps",
        track_id=params.get("track_id"),
        at_t=params.get("at_t"),
    )


def cmd_timeline_set_track_mute(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_set_track_mute",
        track_id=str(params.get("track_id") or ""),
        on=bool(params["on"]),
    )


def cmd_timeline_track_solo(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_set_track_solo",
        track_id=str(params.get("track_id") or ""),
        on=bool(params["on"]),
    )


def cmd_timeline_set_track_locked(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_set_track_locked",
        track_id=str(params.get("track_id") or ""),
        on=bool(params["on"]),
    )


def cmd_timeline_set_track_height(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_set_track_height",
        track_id=str(params.get("track_id") or ""),
        height=params["height"],
        all_tracks=bool(params.get("all") or False),
    )


def cmd_track_set_props(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_set_track_props",
        track_ids=list(params.get("track_ids") or []),
        patch=dict(params.get("patch") or {}),
    )


def cmd_split_set_layout(ctx: Context, params: dict) -> dict:
    kw = {}
    if params.get("backdrop") is not None:      # Q151；不给就不传，老的窗口接口照旧
        kw["backdrop"] = params.get("backdrop")
    return _call(
        ctx, "command_split_set_layout",
        layout=params.get("layout"),
        enabled=params.get("enabled"),
        border=params.get("border"),
        **kw,
    )


def cmd_split_set_divider(ctx: Context, params: dict) -> dict:
    patch = {}
    for key in (
        "pos", "cx", "cy", "r", "aspect",
        "rotation_deg", "feather_px", "apply_all_circles",
    ):
        if key in params and params[key] is not None:
            patch[key] = params[key]
    return _call(
        ctx, "command_split_set_divider",
        divider_id=str(params.get("id") or ""),
        patch=patch,
    )


def cmd_track_set_cell(ctx: Context, params: dict) -> dict:
    cell = params.get("cell")
    if cell is False or cell is None:
        cell = None
    elif str(cell).strip() in ("", "none", "None", "null"):
        cell = None
    else:
        cell = str(cell)
    return _call(
        ctx, "command_track_set_cell",
        track_id=str(params.get("track_id") or ""),
        cell=cell,
    )


def cmd_timeline_set_playhead(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_set_playhead", time=float(params["time"]))


def cmd_timeline_set_in_point(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_set_in_point", t=float(params["t"]))


def cmd_timeline_set_out_point(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_set_out_point", t=float(params["t"]))


def cmd_timeline_clear_io_points(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_clear_io_points",
        which=str(params.get("which") or "both"),
    )


def cmd_marker_add(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_marker_add",
        t=float(params["t"]),
        name=str(params.get("name") or ""),
        color=params.get("color"),
    )


def cmd_marker_remove(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_marker_remove", ids=list(params.get("ids") or []))


def cmd_marker_rename(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_marker_rename",
        mid=str(params.get("id") or ""),
        name=str(params.get("name") or ""),
    )


def cmd_marker_move(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_marker_move",
        mid=str(params.get("id") or ""),
        t=float(params["t"]),
    )


def cmd_marker_set_color(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_marker_set_color",
        mid=str(params.get("id") or ""),
        color=str(params.get("color") or ""),
    )


def cmd_audio_spatial_set(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_spatial_set",
        preset=params.get("preset"),
        wet=params.get("wet"),
        width=params.get("width"),
        apply_all=bool(params.get("all")),
    )


def cmd_audio_sfx_current(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_sfx_apply", current_only=True)


def cmd_audio_sfx_all(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_sfx_apply", current_only=False)


def cmd_audio_volume_master(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_volume_master",
        src=params.get("src"),
        bgm=params.get("bgm"),
        sfx=params.get("sfx"),
        scope=str(params.get("scope") or "all"),
    )


def cmd_audio_denoise(ctx: Context, params: dict) -> dict:
    clip_index = params.get("clip_index")
    return _call(
        ctx, "command_audio_denoise",
        preset=params.get("preset"),
        strength=params.get("strength") or "mid",
        scope=str(params.get("scope") or "current"),
        track_id=params.get("track_id"),
        clip_index=None if clip_index is None else int(clip_index),
    )


def cmd_audio_detect_silence(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_detect_silence",
        scope=str(params.get("scope") or "current"),
    )


def cmd_audio_remove_silences(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_remove_silences",
        min_dur=float(params.get("min_dur") or 0.8),
        scope=str(params.get("scope") or "current"),
        start=params.get("start"),
        end=params.get("end"),
    )


def cmd_audio_duck_set(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_duck_set",
        enabled=bool(params.get("enabled")),
        depth_db=params.get("depth_db"),
        release_ms=params.get("release_ms"),
        scope=str(params.get("scope") or "current"),
    )


def cmd_audio_isolate(ctx: Context, params: dict) -> dict:
    raw = params.get("clip_index")
    clip_index = None if raw is None or raw == "" else int(raw)
    track_id = params.get("track_id") or None
    return _call(
        ctx, "command_audio_isolate",
        mode=str(params.get("mode") or "vocals"),
        scope=str(params.get("scope") or "current"),
        track_id=track_id,
        clip_index=clip_index,
    )


def cmd_audio_auto_fades(ctx: Context, params: dict) -> dict:
    dur = params.get("dur")
    return _call(
        ctx, "command_auto_fades",
        dur=0.03 if dur is None else float(dur),
        scope=str(params.get("scope") or "current"),
    )


def cmd_export_run(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_export_run",
        out=params.get("out"),
        out_dir=params.get("out_dir"),
        honor_io=bool(params.get("honor_io")),
        profile=params.get("profile"),
        allow_offline=bool(params.get("allow_offline")),
    )


def cmd_export_batch(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_export_batch",
        project_paths=list(params.get("project_paths") or []),
        out_dir=params.get("out_dir"),
        allow_offline=bool(params.get("allow_offline")),
    )


def cmd_export_status(ctx: Context, params: dict) -> dict:
    return _call(ctx, "command_export_status")


def cmd_batch_bgm_assign(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_bgm_assign",
        folder=str(params.get("dir") or ""),
        ids=params.get("ids"),
    )


def cmd_batch_copy_style(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_copy_subtitle_style",
        src_id=int(params["src_id"]),
        target_ids=list(params.get("target_ids") or []),
    )


def cmd_batch_share_tracks(ctx: Context, params: dict) -> dict:
    return _call(
        ctx, "command_share_tracks",
        src_id=int(params["src_id"]),
        target_ids=list(params.get("target_ids") or []),
        track_types=list(params.get("track_types") or []),
        track_ids=list(params.get("track_ids") or []),
        keep_duration_track_ids=list(params.get("keep_duration_track_ids") or []),
        auto_fit_duration=bool(params.get("auto_fit_duration", True)),
    )
