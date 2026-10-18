# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 1248-1249, 1250-1250, 1253-1492, 10927-10966, 10969-10979, 10982-10992, 10995-11002, 11005-11023, 11026-11035, 11038-11055, 11058-11112, 11115-11125, 11128-11135

import math
import uuid
from cutora.model.timeline_model import (
    Track,
    Clip,
    TransitionItem,
)
from cutora.render import anim_engine as anim_engine
from cutora.model.timeline import (
    DEFAULT_TIMELINE_WIDTH,
    DEFAULT_TIMELINE_HEIGHT,
    DEFAULT_TIMELINE_FPS,
)
from cutora.model.project_schema import (
    CLIP_KEYS as _RJ_CLIP_KEYS,
    PERSIST_UNDERSCORE_KEYS,
    SOURCE_KEYS as _RJ_SOURCE_KEYS,
    TRACK_KEYS as _RJ_TRACK_KEYS,
)
# --- 项目文件版本（只增不减）---
PROJECT_VERSION = 2          # v1 = 初始版本，v2 = 增加 order/pinned/blend_mode

PROJECT_VERSION_MIN = 1      # 可加载的最低版本


def _legacy_spatial_default_on(fx=None) -> bool:
    """Old renderer followed global fx.spatial_enabled when the clip was not pinned."""
    if isinstance(fx, dict) and "spatial_enabled" in fx:
        return bool(fx.get("spatial_enabled"))
    try:
        from cutora.audio.sfx_library import load_fx_settings
        fx = load_fx_settings()
    except Exception:
        fx = {}
    if isinstance(fx, dict) and "spatial_enabled" in fx:
        return bool(fx.get("spatial_enabled"))
    return True


def _migrate_clip_spatial(cl, fx=None):
    """Promote old three-layer spatial flags into clip.spatial_enabled.

    Missing key: main → True, bgm/sfx → False.
    The legacy per-clip pin flag (False) on a main clip meant the renderer
    used global fx.spatial_enabled — persist that, then drop the flag.
    A stored False without that flag is a real uncheck and is kept.
    """
    pin_key = "spatial_" + "user_set"
    user_set_present = pin_key in cl
    user_set = bool(cl.pop(pin_key, False))
    role = str(cl.get("audio_role") or "")
    is_main = bool(cl.get("is_main")) or role == "main"
    if role in ("bgm", "sfx"):
        default_on = False
    else:
        default_on = bool(is_main)
    if "spatial_enabled" not in cl:
        cl["spatial_enabled"] = default_on
    elif user_set_present and not user_set and is_main and role != "sfx":
        cl["spatial_enabled"] = _legacy_spatial_default_on(fx)


def _clip_source_path(tl, clip):
    sid = clip.get("source_id") if isinstance(clip, dict) else getattr(clip, "source_id", None)
    sources = tl.get("sources") if isinstance(tl, dict) else getattr(tl, "sources", None) or {}
    if not isinstance(sources, dict) or not sid:
        return ""
    src = sources.get(sid)
    if src is None:
        return ""
    if isinstance(src, dict):
        return str(src.get("path") or "")
    return str(getattr(src, "path", None) or "")


def _paths_match(a, b):
    if not a or not b:
        return False
    try:
        import os
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    except Exception:
        return str(a) == str(b)


def _infer_audio_clip_role(tl, clip, task):
    from cutora.audio.sfx_library import _clip_get, _clip_set
    role = str(_clip_get(clip, "audio_role", None) or "")
    if role:
        return role
    if _clip_get(clip, "is_main", False):
        _clip_set(clip, "audio_role", "main")
        return "main"
    video = ""
    if isinstance(task, dict):
        video = str(task.get("video") or "")
    if video and _paths_match(_clip_source_path(tl, clip), video):
        _clip_set(clip, "audio_role", "main")
        return "main"
    _clip_set(clip, "audio_role", "bgm")
    return "bgm"


def _keep_role_for_audio_track(tr, tl, task):
    from cutora.audio.sfx_library import _clip_get, clip_audio_role
    existing = str(_clip_get(tr, "audio_role", "") or "")
    if existing in ("main", "bgm", "sfx"):
        return existing
    clips = _clip_get(tr, "clips", None) or []
    if not clips:
        return "bgm"
    roles = {clip_audio_role(c) for c in clips}
    if len(roles) == 1:
        return next(iter(roles))
    for c in clips:
        if _clip_get(c, "is_main", False) or clip_audio_role(c) == "main":
            return "main"
        video = str((task or {}).get("video") or "") if isinstance(task, dict) else ""
        if video and _paths_match(_clip_source_path(tl, c), video):
            return "main"
    return "bgm"


def _migrate_mixed_audio_tracks(tl, task):
    """Split mixed audio-domain tracks. Never touch video/subs/text/image clips."""
    from cutora.audio.sfx_library import (
        ROLE_TRACK_NAME, _clip_get, _clip_set, clip_audio_role,
        ensure_role_track, is_audio_domain_track,
    )
    from cutora.core.log import get_logger
    if tl is None:
        return
    tracks = tl.get("tracks") if isinstance(tl, dict) else getattr(tl, "tracks", None)
    if not tracks:
        return
    video = str((task or {}).get("video") or "") if isinstance(task, dict) else ""
    stem = ""
    if video:
        import os
        stem = os.path.basename(video)
    for tr in list(tracks):
        if not is_audio_domain_track(tr):
            continue
        clips = list(_clip_get(tr, "clips", None) or [])
        if not clips:
            if not str(_clip_get(tr, "audio_role", "") or ""):
                continue
            if not (_clip_get(tr, "name", "") or ""):
                role = str(_clip_get(tr, "audio_role", "") or "")
                if role in ROLE_TRACK_NAME:
                    _clip_set(tr, "name", ROLE_TRACK_NAME[role])
            continue
        for c in clips:
            _infer_audio_clip_role(tl, c, task)
        keep = _keep_role_for_audio_track(tr, tl, task)
        _clip_set(tr, "audio_role", keep)
        if not (_clip_get(tr, "name", "") or ""):
            _clip_set(tr, "name", ROLE_TRACK_NAME.get(keep, ""))
        keep_clips = []
        moved_bgm = 0
        moved_sfx = 0
        moved_main = 0
        for c in clips:
            role = clip_audio_role(c)
            if role == keep:
                keep_clips.append(c)
                continue
            dest = ensure_role_track(tl, role)
            dest_clips = _clip_get(dest, "clips", None)
            if dest_clips is None:
                _clip_set(dest, "clips", [])
                dest_clips = _clip_get(dest, "clips")
            dest_clips.append(c)
            if role == "bgm":
                moved_bgm += 1
            elif role == "sfx":
                moved_sfx += 1
            else:
                moved_main += 1
        if isinstance(tr, dict):
            tr["clips"] = keep_clips
        else:
            tr.clips = keep_clips
        if moved_bgm or moved_sfx or moved_main:
            tid = str(_clip_get(tr, "id", "") or "")
            get_logger("cutora.audio").info(
                "[Audio] migrate task=%s split track=%s keep=%s moved_bgm=%s moved_sfx=%s",
                stem or "-", tid, keep, moved_bgm, moved_sfx,
            )


def _normalize_project_data(data):
    """加载项目后立刻调用：补齐旧版本缺失的字段。
    只补字段，不改语义。保留所有未知字段（向前兼容）。"""
    from cutora.subtitle.text_utils import ENABLE_RICH_TEXT, _rich_plain_text
    data.setdefault("version", 1)
    data.setdefault("export_video_mode", "compressed")
    for task in data.get("tasks", []):
        # --- timeline 层 ---
        tl = task.get("timeline")
        if not isinstance(tl, dict):
            continue
        tl.setdefault("width", DEFAULT_TIMELINE_WIDTH)
        tl.setdefault("height", DEFAULT_TIMELINE_HEIGHT)
        tl.setdefault("fps", DEFAULT_TIMELINE_FPS)
        tl.setdefault("sources", {})
        tl.setdefault("tracks", [])
        fx = task.get("audio_fx") if isinstance(task.get("audio_fx"), dict) else None
        # --- 每条 Track 补齐字段 ---
        for tr in tl.get("tracks", []):
            if not isinstance(tr, dict):
                continue
            tr.setdefault("id", uuid.uuid4().hex)
            tr.setdefault("domain", "audio" if tr.get("type") in ("audio", "bgm", "sfx") else "visual")
            tr.setdefault("visible", True)
            tr.setdefault("locked", False)
            tr.setdefault("mute", False)
            tr.setdefault("enable_batch", False)
            tr.setdefault("is_main_video", False)
            tr.setdefault("order", 0)
            tr.setdefault("pinned", False)
            tr.setdefault("blend_mode", "normal")
            tr.setdefault("name", "")
            tr.setdefault("audio_role", "")
            from cutora.model.timeline_model import TRACK_HEIGHT_STANDARD
            tr.setdefault("height", TRACK_HEIGHT_STANDARD)
            from cutora.render.track_transform import default_track_props
            tr.setdefault("props", default_track_props())
            tr.setdefault("clips", [])
            # --- 每个 Clip 补齐音频字段 ---
            for cl in tr.get("clips", []):
                if not isinstance(cl, dict):
                    continue
                cl.setdefault("blend_mode", "normal")
                cl.setdefault("gain_db", 0.0)
                cl.setdefault("pitch_semitones", 0.0)
                cl.setdefault("fade_in_dur", 0.0)
                cl.setdefault("fade_out_dur", 0.0)
                cl.setdefault("is_main", False)
                cl.setdefault("audio_role", "main" if cl.get("is_main") else "")
                _migrate_clip_spatial(cl, fx)
                cl.setdefault("spatial_amount", 0.0)
                cl.setdefault("transition_preset", "none")
                cl.setdefault("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)
                cl.setdefault("fade_top", 0.0)
                cl.setdefault("fade_bottom", 0.0)
                cl.setdefault("fade_left", 0.0)
                cl.setdefault("fade_right", 0.0)
        _migrate_mixed_audio_tracks(tl, task)
        # --- task 层 ---
        task.setdefault("video_overlay", {})
        task.setdefault("audio_tracks", [])
        task.setdefault("audio_clips", [])
        task.setdefault("text_overlays", [])
        task.setdefault("image_overlays", [])
        task.setdefault("src_volume", 1.0)
        # 补齐字幕段 role 字段（旧项目兼容）
        for seg in task.get("segments", []) or []:
            if isinstance(seg, dict) and "role" not in seg:
                seg["role"] = "default"
        for ov in task.get("text_overlays", []):
            if isinstance(ov, dict):
                ov.setdefault("blend_mode", "normal")
                ov.setdefault("transition_preset", "none")
                ov.setdefault("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)
                ov.setdefault("content_format", "plain")
                ov.setdefault("text_layout", {
                    "left_pad": 0, "right_pad": 0,
                    "top_pad": 0, "bottom_pad": 0,
                    "line_spacing": 0, "letter_spacing": 0,
                    "align": "left"
                })
                ov.setdefault("rich_content", [])
                ov.setdefault("bg_fill_type", "solid")
                ov.setdefault("bg_grad_dir", "top_bottom")
                ov.setdefault("bg_color2", ov.get("bg_color", "#000000"))
                ov.setdefault("bg_color3", ov.get("bg_color2", ov.get("bg_color", "#000000")))
                ov.setdefault("bg_color4", ov.get("bg_color3", ov.get("bg_color2", ov.get("bg_color", "#000000"))))
                ov.setdefault("bg_padding_x", ov.get("bg_padding", 12))
                ov.setdefault("bg_padding_y", ov.get("bg_padding", 12))
                ov.setdefault("bg_expand_w", 0)
                ov.setdefault("bg_expand_h", 0)
                if not ENABLE_RICH_TEXT and ov.get("content_format") == "rich_spans":
                    ov["content"] = _rich_plain_text(ov.get("rich_content", [])) or ov.get("content", "")
                    ov["content_format"] = "plain"
                    ov.pop("rich_content", None)
        for ov in task.get("image_overlays", []):
            if isinstance(ov, dict):
                ov.setdefault("blend_mode", "normal")
                ov.setdefault("transition_preset", "none")
                ov.setdefault("transition_duration", anim_engine.DEFAULT_TRANSITION_DURATION)
                for el in ov.get("elements", []) or []:
                    if isinstance(el, dict):
                        el.setdefault("blend_mode", "normal")
                        if el.get("kind") == "text":
                            el.setdefault("content_format", "plain")
                            el.setdefault("text_layout", {
                                "left_pad": 0, "right_pad": 0,
                                "top_pad": 0, "bottom_pad": 0,
                                "line_spacing": 0, "letter_spacing": 0,
                                "align": "left"
                            })
                            el.setdefault("rich_content", [])
                            el.setdefault("bg_fill_type", "solid")
                            el.setdefault("bg_grad_dir", "top_bottom")
                            el.setdefault("bg_color2", el.get("bg_color", "#000000"))
                            el.setdefault("bg_color3", el.get("bg_color2", el.get("bg_color", "#000000")))
                            el.setdefault("bg_color4", el.get("bg_color3", el.get("bg_color2", el.get("bg_color", "#000000"))))
                            el.setdefault("bg_padding_x", el.get("bg_padding", 12))
                            el.setdefault("bg_padding_y", el.get("bg_padding", 12))
                            el.setdefault("bg_expand_w", 0)
                            el.setdefault("bg_expand_h", 0)
                            if not ENABLE_RICH_TEXT and el.get("content_format") == "rich_spans":
                                el["content"] = _rich_plain_text(el.get("rich_content", [])) or el.get("content", "")
                                el["content_format"] = "plain"
                                el.pop("rich_content", None)
        # --- style 层：补齐动画字段 ---
        style = task.get("style")
        if isinstance(style, dict):
            style.setdefault("anim_in_type", "none")
            style.setdefault("anim_in_duration", 0.3)
            style.setdefault("anim_in_easing", "ease_out")
            style.setdefault("anim_out_type", "none")
            style.setdefault("anim_out_duration", 0.3)
            style.setdefault("anim_out_easing", "ease_out")
            style.setdefault("fade_in_out_enabled", True)
            style.setdefault("fade_in_out_duration", 0.3)
            style.setdefault("tw_revealed_color", style.get("color_text", "#000000"))
            style.setdefault("tw_revealed_stroke_color", style.get("color_outline", "#000000"))
            style.setdefault("tw_revealed_opacity", 255)
            style.setdefault("tw_revealed_stroke_opacity", 255)
            style.setdefault("tw_unrevealed_color", "#808080")
            style.setdefault("tw_unrevealed_stroke_color", "#404040")
            style.setdefault("tw_unrevealed_opacity", 80)
            style.setdefault("bold", True)
            style.setdefault("italic", False)
            style.setdefault("line_spacing", 0)
            style.setdefault("char_bounce_height", 20)
            style.setdefault("char_bounce_stagger", 0.05)
            style.setdefault("dyn_box_anim", False)
            style.setdefault("dyn_box_anim_overshoot", 1.3)
            style.setdefault("dyn_box_anim_duration", 0.15)
            # ── Phase 2 defaults ──
            style.setdefault("floating_amplitude", 8)
            style.setdefault("floating_period", 2.0)
            style.setdefault("bullet_stagger", 0.15)
            style.setdefault("metronome_bpm", 120)
            style.setdefault("flash_color", "#FFFF00")
            style.setdefault("flash_duration", 0.1)
            style.setdefault("holy_glow_radius", 6)
            style.setdefault("holy_glow_color", "#FFFFCC")
            style.setdefault("holy_glow_period", 3.0)
            style.setdefault("letter_jump_scale", 1.5)
            style.setdefault("letter_jump_duration", 0.2)
            style.setdefault("blur_sharp_max", 20)
            style.setdefault("blur_sharp_clear_frac", 0.4)
            style.setdefault("stroke_expand_enabled", False)
            style.setdefault("stroke_expand_layers", 3)
            style.setdefault("stroke_expand_step", 4)
            style.setdefault("stroke_expand_feather", 8)
            style.setdefault("stroke_expand_colors", "#FF0000,#00FF00,#0000FF")
            style.setdefault("stroke_expand_layer_widths", [])
            style.setdefault("stroke_expand_layer_feathers", [])
            style.setdefault("bg_gradient_enabled", False)
            style.setdefault("bg_gradient_type", "linear_h")
            style.setdefault("bg_gradient_colors", "#e0c3fc,#8ec5fc")
            style.setdefault("bg_gradient_highlight", False)

            # Audio spectrum（独立开关）
            style.setdefault("spectrum_enabled", False)
            style.setdefault("spectrum_bands", 32)
            style.setdefault("spectrum_bar_width", 4)
            style.setdefault("spectrum_bar_gap", 2)
            style.setdefault("spectrum_max_height", 80)
            style.setdefault("spectrum_color_bottom", "#00AAFF")
            style.setdefault("spectrum_color_top", "#FF4444")
            style.setdefault("spectrum_position", "below")
            style.setdefault("spectrum_shape", "bars")
            style.setdefault("spectrum_dual", False)
            style.setdefault("spectrum_top_shape", "bars")
            style.setdefault("spectrum_opacity", 200)
            style.setdefault("spectrum_roundness", 2)
            # Metronome read/unread
            style.setdefault("metro_read_color", "#FFFFFF")
            style.setdefault("metro_read_stroke_color", "#000000")
            style.setdefault("metro_read_stroke_width", 2)
            style.setdefault("metro_read_opacity", 255)
            style.setdefault("metro_unread_color", "#808080")
            style.setdefault("metro_unread_stroke_color", "#404040")
            style.setdefault("metro_unread_stroke_width", 1)
            style.setdefault("metro_unread_opacity", 100)
            # Box color transition
            style.setdefault("box_transition_enabled", False)
            style.setdefault("box_transition_color_to", "#8ec5fc")
            # Advanced subtitle text-box
            style.setdefault("advanced_textbox_enabled", False)
            style.setdefault("advanced_textbox_x", 0)
            style.setdefault("advanced_textbox_y", 0)
            style.setdefault("advanced_textbox_w", 0)
            style.setdefault("advanced_textbox_h", 0)
            style.setdefault("advanced_textbox_align", "center")
            style.setdefault("adv_bg_enabled", False)
            style.setdefault("adv_bg_offset_x", 0)
            style.setdefault("adv_bg_offset_y", 0)
            style.setdefault("adv_bg_w", 1080)
            style.setdefault("adv_bg_h", 280)
            style.setdefault("adv_bg_radius", 20)
            style.setdefault("adv_bg_color", "#e0c3fc")
            style.setdefault("adv_bg_opacity", 191)
            style.setdefault("adv_bg_gradient_enabled", False)
            style.setdefault("adv_bg_gradient_type", "linear_h")
            style.setdefault("adv_bg_gradient_colors", "#e0c3fc,#8ec5fc")
            style.setdefault("adv_bg_gradient_highlight", False)
            style.setdefault("adv_line_spacing", 0)
            style.setdefault("adv_text_align", "center")
            style.setdefault("adv_stroke_enabled", False)
            style.setdefault("adv_stroke_width", 2)
            style.setdefault("adv_stroke_color", "#000000")
            style.setdefault("adv_stroke_opacity", 255)
            # Scrolling lyrics mode
            style.setdefault("lyrics_scroll_enabled", False)
            style.setdefault("lyrics_scroll_damping", 0.8)
            style.setdefault("lyrics_scroll_transition", 0.4)
            style.setdefault("lyrics_center_scale", 1.15)
            style.setdefault("lyrics_edge_feather", 40)
            style.setdefault("lyrics_alpha_decay", 30)
            style.setdefault("lyrics_line_gap", 0)
            style.setdefault("lyrics_center_offset", 0)
            style.setdefault("lyrics_center_color", "#FFD700")
        from cutora.subtitle.style_schema import migrate_task_styles
        migrate_task_styles(task)
        from cutora.model.offline import inspect_task
        inspect_task(task)
        from cutora.subtitle.stylepack import bind_default_paint_style
        bind_default_paint_style(task)
    from cutora.core.migrate_user_data import migrate_feather_to_fade
    migrate_feather_to_fade(data)

# ═══════════════════════════════════════════════════════════════════════════
#  [v6.1] 并行批量导出 —— 渲染任务的进程间传递
#
#  批量导出原本严格串行，整条流水线挤在一个 Python 线程里，24 核的机器只用得上
#  1 个核。不能改成多线程，因为渲染路径依赖 _SE_SUB_CACHE / _IMAGE_CACHE /
#  _DBI_VIDEO_CAPS 这些模块级全局缓存，而且 cv2.VideoCapture 完全非线程安全。
#
#  所以走多进程：每个子进程有自己独立的全局缓存和解码器，天然隔离。
#  下面这组函数负责把一个 task 序列化成 JSON 传给子进程，再在子进程里还原。
#
#  设计原则：**只做当前结构的忠实往返**。父进程内存里的 task 已经是完全规范化
#  的（所有旧版本迁移都在 apply_project_data 里做完了），所以这里不需要、
#  也刻意不做任何兼容性补齐逻辑 —— 保持简单就是保持正确。
# ═══════════════════════════════════════════════════════════════════════════

def _rj_json_safe(value, _depth=0):
    """递归地把任意值转成 JSON 可序列化的形式。

    - 下划线开头的 key 一律丢弃（运行时缓存，如 _rich_cache / _clip_obj），
      例外见 PERSIST_UNDERSCORE_KEYS（动图的 _match_target_dur 要落盘）
    - 元组转列表
    - 无法序列化的对象直接丢弃，绝不抛异常
    """
    if _depth > 24:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or (
                    k.startswith("_") and k not in PERSIST_UNDERSCORE_KEYS):
                continue
            sv = _rj_json_safe(v, _depth + 1)
            if sv is not None or v is None:
                out[k] = sv
        return out
    if isinstance(value, (list, tuple)):
        return [_rj_json_safe(v, _depth + 1) for v in value]
    return None

def _rj_serialize_clip(obj):
    data = {
        "source_id": getattr(obj, "source_id", None),
        "in_t": getattr(obj, "in_t", 0.0),
        "out_t": getattr(obj, "out_t", 0.0),
        "start_t": getattr(obj, "start_t", 0.0),
    }
    for k in _RJ_CLIP_KEYS:
        if hasattr(obj, k):
            val = getattr(obj, k)
            if k == "audio_proc" and not val:
                continue
            if k == "speed_curve" and not val:
                continue
            if k == "matte_for" and not val:
                continue
            if k in ("matte_invert", "seam_xfade") and not val:
                continue
            data[k] = _rj_json_safe(val)
    return data



def _serialize_track_props(obj):
    from cutora.render.track_transform import track_props_of
    return track_props_of(obj)


def _hydrate_track_props(track_obj, tr):
    from cutora.render.track_transform import normalize_track_props
    raw = tr.get("props")
    props = normalize_track_props(raw)
    if not isinstance(raw, dict) or "blend_mode" not in raw:
        props["blend_mode"] = str(tr.get("blend_mode") or getattr(track_obj, "blend_mode", "normal") or "normal")
    track_obj.props = props
    track_obj.blend_mode = str(props.get("blend_mode") or "normal")
    track_obj.name = str(tr.get("name") or "")
    track_obj.pinned = False
    cell = tr.get("split_cell")
    if cell in (None, "", "none", "None"):
        track_obj.split_cell = None
    else:
        track_obj.split_cell = str(cell)
    track_obj.bg_role = str(tr.get("bg_role") or "")
    track_obj.audio_role = str(tr.get("audio_role") or "")


def _rj_serialize_transition(trans):
    data = {
        "start_t": getattr(trans, "start_t", 0.0),
        "duration": getattr(trans, "duration", 0.0),
        "preset": getattr(trans, "preset", "none"),
        "zoom_scale_start": getattr(trans, "zoom_scale_start", 0.7),
        "motion_blur": getattr(trans, "motion_blur", 0.4),
        "intensity": getattr(trans, "intensity", 1.0),
        "easing": getattr(trans, "easing", "ease_in_out"),
    }
    direction = getattr(trans, "direction", None)
    if direction is not None:
        data["direction"] = direction
    try:
        angle = float(getattr(trans, "angle", 0.0) or 0.0)
    except (TypeError, ValueError):
        angle = 0.0
    if abs(angle) > 1e-6:
        data["angle"] = angle
    try:
        feather = float(getattr(trans, "feather", 0.0) or 0.0)
    except (TypeError, ValueError):
        feather = 0.0
    if feather > 1e-6:
        data["feather"] = feather
    domain = getattr(trans, "domain", "visual") or "visual"
    if domain == "audio":
        data["domain"] = "audio"
    return data

_SOURCE_FALLBACK = {
    "id": None,
    "path": None,
    "duration": 0.0,
    "has_video": True,
    "has_audio": True,
    "alpha": False,
    "alpha_mode": "straight",
    "preview_path": None,
    "source_path": None,
    "kind": None,
}


def _rj_serialize_track(obj):
    from cutora.model.timeline_model import clamp_track_height
    d = {
        "id": getattr(obj, "id", None),
        "type": getattr(obj, "type", None),
        "enable_batch": getattr(obj, "enable_batch", False),
        "is_main_video": getattr(obj, "is_main_video", False),
        "domain": getattr(obj, "domain", None),
        "visible": getattr(obj, "visible", True),
        "locked": getattr(obj, "locked", False),
        "mute": bool(getattr(obj, "mute", False)),
        "order": getattr(obj, "order", 0),
        "pinned": False,
        "name": getattr(obj, "name", "") or "",
        "blend_mode": getattr(obj, "blend_mode", "normal"),
        "props": _serialize_track_props(obj),
        "split_cell": getattr(obj, "split_cell", None) or None,
        "bg_role": str(getattr(obj, "bg_role", "") or ""),
        "audio_role": str(getattr(obj, "audio_role", "") or ""),
        "height": int(round(clamp_track_height(getattr(obj, "height", None)))),
        "clips": [_rj_serialize_clip(c) for c in getattr(obj, "clips", [])],
        "transitions": [_rj_serialize_transition(t) for t in getattr(obj, "transitions", [])],
    }
    missing = [k for k in _RJ_TRACK_KEYS if k not in d]
    if missing:
        raise RuntimeError("track field table not written: %s" % ", ".join(missing))
    extra = getattr(obj, "_extra", {}) or {}
    if extra:
        d.update(_rj_json_safe(extra))
    return d

def _rj_serialize_source(s):
    """Every SOURCE_KEYS entry is written, including defaults.

    Omitting alpha / preview_path when they were false is how a transparent
    mov reopened as an opaque file.
    """
    if isinstance(s, dict):
        raw = _rj_json_safe(s) or {}
        data = {}
        for k in _RJ_SOURCE_KEYS:
            data[k] = raw[k] if k in raw else _SOURCE_FALLBACK[k]
        for k, v in raw.items():
            if k not in data:
                data[k] = v
        return data
    data = {}
    for k in _RJ_SOURCE_KEYS:
        if hasattr(s, k):
            data[k] = _rj_json_safe(getattr(s, k))
        else:
            data[k] = _SOURCE_FALLBACK[k]
    return data


def restore_saved_alpha_modes(sources) -> None:
    """Put a saved alpha mode back on the decoder.

    Straight + no alpha must not override the file probe. A saved
    premultiplied mov, or any source flagged alpha, must.
    """
    if not isinstance(sources, dict):
        return
    from cutora.render.readers import set_path_alpha_mode
    for src in sources.values():
        if isinstance(src, dict):
            path = src.get("path") or ""
            if "alpha_mode" not in src and not src.get("alpha"):
                continue
            mode = src.get("alpha_mode") or "straight"
            alpha = bool(src.get("alpha"))
        else:
            path = getattr(src, "path", None) or ""
            mode = getattr(src, "alpha_mode", None) or "straight"
            alpha = bool(getattr(src, "alpha", False))
        if not path:
            continue
        if alpha or str(mode) != "straight":
            set_path_alpha_mode(str(path), str(mode))

def _rj_serialize_timeline(tl):
    if tl is None:
        return None
    if isinstance(tl, dict):
        d = dict(tl)
        d["sources"] = {sid: _rj_serialize_source(s)
                        for sid, s in (d.get("sources") or {}).items()}
        d["tracks"] = [_rj_serialize_track(tr) if not isinstance(tr, dict) else _rj_json_safe(tr)
                       for tr in (d.get("tracks") or [])]
        return _rj_json_safe(d)
    return {
        "width": getattr(tl, "width", DEFAULT_TIMELINE_WIDTH),
        "height": getattr(tl, "height", DEFAULT_TIMELINE_HEIGHT),
        "fps": getattr(tl, "fps", DEFAULT_TIMELINE_FPS),
        "sources": {sid: _rj_serialize_source(s)
                    for sid, s in (getattr(tl, "sources", {}) or {}).items()},
        "tracks": [_rj_serialize_track(tr) for tr in (getattr(tl, "tracks", []) or [])],
    }

def _rj_deserialize_timeline(d):
    """把 _rj_serialize_timeline 的输出还原成 Timeline 对象。

    注意 sources 保持为 dict 不还原成 MediaSource —— 这与 apply_project_data
    加载项目文件时的做法完全一致（那里也是 `tl_obj.sources = raw_tl["sources"]`），
    渲染路径的取值写法 `getattr(src,"path",None) or src.get("path")` 两种都支持。
    """
    if not isinstance(d, dict):
        return None
    from cutora.model.timeline import Timeline as _TL
    tl = _TL(d.get("width", DEFAULT_TIMELINE_WIDTH),
             d.get("height", DEFAULT_TIMELINE_HEIGHT),
             d.get("fps", DEFAULT_TIMELINE_FPS))
    tl.sources = d.get("sources", {}) or {}
    for tr in (d.get("tracks") or []):
        if not isinstance(tr, dict):
            continue
        track_obj = Track(tr.get("type"))
        if tr.get("id"):
            track_obj.id = tr.get("id")
        track_obj.enable_batch = bool(tr.get("enable_batch", False))
        track_obj.is_main_video = bool(tr.get("is_main_video", False))
        track_obj.domain = tr.get("domain", track_obj.domain)
        track_obj.visible = bool(tr.get("visible", True))
        track_obj.locked = bool(tr.get("locked", False))
        track_obj.mute = bool(tr.get("mute", False))
        track_obj.order = int(tr.get("order", 0) or 0)
        track_obj.blend_mode = str(tr.get("blend_mode", "normal"))
        _hydrate_track_props(track_obj, tr)
        _known = {"id", "type", "domain", "visible", "locked", "mute", "enable_batch",
                  "is_main_video", "order", "pinned", "blend_mode", "name", "props",
                  "split_cell", "height", "bg_role", "audio_role",
                  "clips", "transitions"}
        track_obj._extra = {k: v for k, v in tr.items() if k not in _known}
        from cutora.model.timeline_model import TRACK_HEIGHT_STANDARD
        try:
            h = int(tr.get("height") or TRACK_HEIGHT_STANDARD)
        except (TypeError, ValueError):
            h = TRACK_HEIGHT_STANDARD
        track_obj.height = max(16, min(160, h))
        from cutora.subtitle.track_source import hydrate_track
        hydrate_track(track_obj)

        for td in (tr.get("transitions") or []):
            if not isinstance(td, dict):
                continue
            track_obj.transitions.append(TransitionItem(
                td.get("start_t", 0.0),
                td.get("duration", 0.0),
                td.get("preset", "none"),
                zoom_scale_start=td.get("zoom_scale_start", 0.7),
                motion_blur=td.get("motion_blur", 0.4),
                domain=td.get("domain", "visual"),
                intensity=td.get("intensity", 1.0),
                easing=td.get("easing", "ease_in_out"),
                direction=td.get("direction", None),
                angle=td.get("angle", 0.0),
                feather=td.get("feather", 0.0),
            ))

        for c in (tr.get("clips") or []):
            if not isinstance(c, dict):
                continue
            clip_obj = Clip(c.get("source_id"), c.get("in_t", 0.0),
                            c.get("out_t", 0.0), c.get("start_t", 0.0))
            for k in _RJ_CLIP_KEYS:
                if k in c:
                    setattr(clip_obj, k, c.get(k))
            track_obj.clips.append(clip_obj)

        tl.tracks.append(track_obj)
    restore_saved_alpha_modes(tl.sources)
    return tl

def serialize_render_task(task):
    """把一个 task dict 转成可写入 JSON 的形式（timeline 单独处理）。"""
    out = {}
    for k, v in (task or {}).items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        if k == "timeline":
            continue
        out[k] = _rj_json_safe(v)
    out["timeline"] = _rj_serialize_timeline((task or {}).get("timeline"))
    return out

def deserialize_render_task(d):
    """serialize_render_task 的逆操作。"""
    task = dict(d or {})
    task["timeline"] = _rj_deserialize_timeline(task.get("timeline"))
    dims = task.get("dims")
    if isinstance(dims, list) and len(dims) == 2:
        task["dims"] = (dims[0], dims[1])
    from cutora.subtitle.stylepack import migrate_v1_bg_to_fx
    st = task.get("style")
    if isinstance(st, dict):
        migrate_v1_bg_to_fx(st)
    roles = task.get("subtitle_roles")
    if isinstance(roles, dict):
        for info in roles.values():
            if isinstance(info, dict) and isinstance(info.get("style"), dict):
                migrate_v1_bg_to_fx(info["style"])
    return task
