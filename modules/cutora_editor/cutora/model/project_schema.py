# SPDX-License-Identifier: GPL-3.0-or-later
"""Fields project save and the export subprocess both have to keep.

GUI collect/apply and project_io read these tuples. A second hand-written
list is how alpha, mute and the looping-still length got dropped.
"""

# Clip attributes written to the project file and the render task.
# `_match_target_dur` is the looping still's timeline length, not a cache.
CLIP_KEYS = (
    "fit_mode", "blend_mode", "x", "y", "scale", "scale_y", "rotation",
    "flip_x", "flip_y", "speed", "loop", "match_duration",
    "alpha", "alpha_mode", "size_mode",
    "transition_preset", "transition_duration",
    "fade_top", "fade_bottom", "fade_left", "fade_right",
    "gain_db", "pitch_semitones",
    "fade_in_dur", "fade_out_dur", "spatial_enabled", "spatial_amount",
    "is_main", "audio_role",
    "link_group_id",
    "is_thumbnail_leadin", "freeze_source_t", "freeze_frame_index",
    "thumbnail_text", "thumbnail_duration", "thumb_duration",
    "is_intro_video_leadin", "intro_video_path",
    "keyframes",
    "_match_target_dur",
    "lock_aspect",
    # Q117：画不满时背景怎么补（模糊 / 镜像 / 平铺 / 拉伸）
    "bg_fill",
    # Q152-G：图层面板的显隐 / 锁定 / 颜色标签 / 自定义名
    "hidden", "locked", "label_color", "name",
    # Q152-B/C：形状遮罩、蓝绿幕抠像
    "masks", "keyer",
    # Q152-D：AI 抠像
    "ai_matte",
    # Q152-E：AI 跟踪
    "tracking",
    # Q152-F：效果节点图
    "fx_graph",
    # Q154：片段稳定 id + 预合成组（原位分组）
    "uid", "group",
    # Q173：片段调色（以前没进存盘表 —— 调好的色一保存就丢）
    "grade",
    # Q206：降噪 / 分轨 sidecar。空字典不写出。
    "audio_proc",
    # Q208：区域速度曲线。空列表不写出，等于只看 clip.speed。
    "speed_curve",
    # 子蒙版：空字符串 = 不是蒙版。接缝淡化：假 = 老的 aloop。
    "matte_for", "matte_invert", "seam_xfade",
    # AI 卡牌组：每个片段专属的 AI 卡牌参数与素材记录，随工程保存
    "ai_cards",
)

# Q117 背景填充（画面没铺满时补什么）的默认值。放在这个没有 Qt 的模块里，
# 是为了让 Clip 的构造函数和 render/fill_bg 用同一份，而不把 Qt 拽进模型层。
BG_FILL_DEFAULTS = {
    "mode": "none",
    "blur": 45.0,       # 0–100，模糊程度
    "scale": 1.12,      # 背景比铺满再放大一点，转起来不露角
    "x": 0.0,
    "y": 0.0,
    "rotation": 0.0,
    "dim": 0.25,        # 压暗，让前景跳出来；0 = 不压
    # Q151：lens = 镜头模糊（六边形光圈 + 高光散景）；legacy = Q117 的缩图高斯。
    # 老工程里没有这个键：已经开着模糊背景的按 legacy 画（fill_bg.fill_spec）。
    "blur_style": "lens",
    "highlights": 0.35,  # 散景高光 0–1（达芬奇 Lens Blur 默认 0.35）
}

# Underscore keys that are real project data. Every other `_` key is a
# runtime cache (_rich_cache, _clip_obj) and stays out of the file.
PERSIST_UNDERSCORE_KEYS = frozenset({
    "_match_target_dur",
    # Q115：文本/图片 overlay 记在哪条轨道上。全仓都用这个 key 找轨道
    # （ripple.py / main_window 加载时都读它），以前被当成运行时缓存
    # 丢掉了 —— 存完工程重开，放在第 2 条文本轨上的字会掉回第 1 条。
    "_track_id",
})

# MediaSource. Object sources always emit the whole tuple, so a plain
# file and a transparent mov expose the same keys.
SOURCE_KEYS = (
    "id", "path", "duration", "has_video", "has_audio",
    "alpha", "alpha_mode", "preview_path",
    "source_path", "kind",
)

# Scalar track fields. props / clips / transitions are structural.
TRACK_KEYS = (
    "id", "type", "enable_batch", "is_main_video", "domain",
    "visible", "locked", "mute", "order", "name", "blend_mode",
    "split_cell", "height", "bg_role", "audio_role",
)


def lock_aspect_of(obj, fallback=None) -> bool:
    """Missing field means locked. ``keep_aspect`` is the old name."""
    for host in (obj, fallback):
        if host is None:
            continue
        if isinstance(host, dict):
            if "lock_aspect" in host:
                return bool(host.get("lock_aspect"))
            if "keep_aspect" in host:
                return bool(host.get("keep_aspect"))
            continue
        if hasattr(host, "lock_aspect"):
            return bool(getattr(host, "lock_aspect"))
        if hasattr(host, "keep_aspect"):
            return bool(getattr(host, "keep_aspect"))
    return True


def ensure_lock_aspect(obj, inherit=None) -> None:
    """Write ``lock_aspect`` onto a loaded dict. Old files omit it."""
    if not isinstance(obj, dict):
        if obj is not None and not hasattr(obj, "lock_aspect"):
            setattr(obj, "lock_aspect", True if inherit is None else bool(inherit))
        return
    if "lock_aspect" not in obj:
        if "keep_aspect" in obj:
            obj["lock_aspect"] = bool(obj.get("keep_aspect"))
        elif inherit is not None:
            obj["lock_aspect"] = bool(inherit)
        else:
            obj["lock_aspect"] = True
    for el in obj.get("elements") or []:
        if isinstance(el, dict):
            ensure_lock_aspect(el, inherit=bool(obj.get("lock_aspect", True)))


def clip_scale_y(clip) -> float:
    """Extra height scale. 1 means the uniform ``scale`` path, unchanged."""
    if clip is None:
        return 1.0
    raw = clip.get("scale_y", None) if isinstance(clip, dict) else getattr(clip, "scale_y", None)
    try:
        value = float(1.0 if raw is None else raw)
    except (TypeError, ValueError):
        return 1.0
    if value <= 0:
        return 1.0
    return value


def native_aspect_ratio(host, parent=None):
    """Original width/height. None when the material has no known ratio."""
    for obj in (host, parent):
        if not isinstance(obj, dict):
            continue
        try:
            ratio = float(obj.get("aspect_ratio") or 0.0)
        except (TypeError, ValueError):
            ratio = 0.0
        if ratio > 0:
            return ratio
        path = obj.get("path") or obj.get("content")
        if not isinstance(path, str) or not path:
            continue
        try:
            from cutora.assets.user import _image_wh
            iw, ih = _image_wh(path)
        except Exception:
            iw, ih = 0, 0
        if ih:
            return float(iw) / float(ih)
    return None
