# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 331-345, 347-357, 360-369, 371-390, 392-396, 442-467, 469-518, 645-645, 646-646, 647-647, 675-680, 682-692, 694-705, 708-709, 712-713, 715-725, 727-734, 736-743, 745-770, 772-823, 825-834, 836-877, 879-883, 885-911, 913-925, 927-934, 936-943, 945-958, 960-1010, 1012-1038, 1040-1074, 1076-1105, 1107-1145, 1147-1185, 1188-1207, 1210-1240, 1243-1285, 1288-1457, 1460-1502, 1505-1550, 1553-1596, 1599-1638, 1640-1712, 1714-1742, 1744-1797, 1800-1885, 2022-2038, 2040-2052, 2150-10672

import os
import json
import math
from pathlib import Path
import hashlib
import subprocess
from cutora.asr.engine import (
    torch,
    whisperx,
    _WHISPERX_MODEL,
    get_align_model,
    get_whisperx_model,
    _cuda_mem_gb,
    release_gpu_models,
    _sync_device_globals,
    trim_gpu_cache,
)
import re
import cv2
import copy
import shutil
import time
import random
import uuid
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QTextEdit,
    QLabel,
    QSplitter,
    QMessageBox,
    QProgressBar,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QColorDialog,
    QSlider,
    QCheckBox,
    QScrollArea,
    QFrame,
    QTabWidget,
    QInputDialog,
    QFileDialog,
    QToolButton,
    QComboBox,
    QStackedWidget,
    QDoubleSpinBox,
    QDialogButtonBox,
    QLineEdit,
    QPlainTextEdit,
    QMenu,
    QDialog,
    QDockWidget,
    QToolBar,
    QSizePolicy,
)
from PySide6.QtCore import (
    Qt,
    QUrl,
    QTimer,
    QSize,
    QRectF,
    QSignalBlocker,
    QEvent,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QAction,
    QActionGroup,
    QIcon,
    QFontMetrics,
    QPainterPath,
    QImage,
    QPixmap,
    QKeySequence,
    QShortcut,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices, QAudioSink, QAudioFormat
from cutora.core.utils import clean_text
from cutora.model.timeline_model import MediaSource, Track, Clip, TransitionItem
from cutora.core.config import DEVICE
try:
    from cutora.audio import spatial as _SPATIAL
    from cutora.audio import sfx_library as _SFX
except Exception as _sfx_imp_err:
    _SPATIAL = None
    _SFX = None
    print(f"[AudioFx] 音效模块未加载（不影响原有导出）: {_sfx_imp_err}")
from cutora.render.parallel import (
    _default_parallel_workers,
    PARALLEL_EXPORT_ENABLED,
    PARALLEL_EXPORT_WORKERS,
    _save_parallel_settings,
    ParallelExportManager,
)
from cutora.subtitle import presets as preset_manager
try:
    from cutora.render import export_guard as _GUARD
except Exception as _e:
    _GUARD = None
    print(f"[Guard] export_guard 不可用，导出保活功能关闭（不影响导出本身）: {_e}")
from cutora.ui.panels.fonts import (
    DEFAULT_FONT_FAMILY,
    FONT_DIR,
    _iter_font_files,
    register_font_combo,
    refresh_all_font_combos,
    get_font_manager,
    load_embedded_fonts,
    sanitize_font_family,
    force_allowed_font,
    FontManagerDialog,
)
from cutora.ui.panels.subtitle_style import (
    copy_style_full,
    SubtitleEditDialog,
    SubtitleStylePanel,
)
from cutora.subtitle.text_utils import (
    ENABLE_RICH_TEXT,
    tokenize_for_wrap,
    wrap_tokens_by_pixel_width,
    wrap_text_by_pixel_width,
    _rich_plain_text,
    _rich_default_layout,
    _clear_rich_cache_in_task,
    resegment_words_by_style,
)
from cutora.asr.align_worker import (
    apply_time_offset,
    fix_segment_durations,
    merge_short_segments,
    AlignmentWorker,
)
from cutora.model.timeline import (
    DEFAULT_TIMELINE_WIDTH,
    DEFAULT_TIMELINE_HEIGHT,
    DEFAULT_TIMELINE_FPS,
    DEFAULT_TIMELINE_TEMPLATE,
    DEFAULT_VIDEO_FIT_MODE,
)
from cutora.model.project_io import (
    PROJECT_VERSION,
    PROJECT_VERSION_MIN,
    _normalize_project_data,
    serialize_render_task,
    deserialize_render_task,
)
from PySide6.QtWidgets import (
    QMenu,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt
from cutora.ui.panels.media_list import DraggableListWidget
from cutora.render.readers import (
    _open_video_capture,
    probe_duration_sec,
    probe_bitrates,
    has_audio_stream,
)
from cutora.ui.panels.text_editors import (
    RichTextEditorDialog,
    SimpleTextEditorDialog,
)
from cutora.ui.panels.role_mapping import RoleMappingPanel
from cutora.ui.panels.word_block import WordBlockPanel
from cutora.ui.panels.property import PropertyPanel
from cutora.asr.align_worker import AlignmentWorker, apply_time_offset, fix_segment_durations, merge_short_segments
from cutora.asr.engine import _WHISPERX_MODEL, _cuda_mem_gb, _sync_device_globals, get_align_model, get_whisperx_model, release_gpu_models, torch, trim_gpu_cache, whisperx
from cutora.model.project_io import PROJECT_VERSION, PROJECT_VERSION_MIN, _normalize_project_data, serialize_render_task, deserialize_render_task
from cutora.model.timeline import DEFAULT_TIMELINE_FPS, DEFAULT_TIMELINE_HEIGHT, DEFAULT_TIMELINE_TEMPLATE, DEFAULT_TIMELINE_WIDTH, DEFAULT_VIDEO_FIT_MODE
from cutora.render.parallel import PARALLEL_EXPORT_ENABLED, PARALLEL_EXPORT_WORKERS, ParallelExportManager, _default_parallel_workers, _save_parallel_settings, prepare_task_audio_for_export
from cutora.render.readers import _open_video_capture, has_audio_stream, probe_duration_sec, probe_bitrates
from cutora.subtitle.text_utils import ENABLE_RICH_TEXT, _clear_rich_cache_in_task, _rich_default_layout, _rich_plain_text, resegment_words_by_style, tokenize_for_wrap, wrap_text_by_pixel_width, wrap_tokens_by_pixel_width
from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, FONT_DIR, FontManagerDialog, _iter_font_files, force_allowed_font, get_font_manager, load_embedded_fonts, refresh_all_font_combos, register_font_combo, sanitize_font_family
from cutora.ui.panels.media_list import DraggableListWidget
from cutora.ui.panels.property import PropertyPanel
from cutora.ui.panels.role_mapping import RoleMappingPanel
from cutora.ui.panels.subtitle_style import SubtitleEditDialog, SubtitleStylePanel, copy_style_full
from cutora.ui.panels.text_editors import RichTextEditorDialog, SimpleTextEditorDialog
from cutora.ui.panels.word_block import WordBlockPanel
from cutora.ui.panels.transcript import TranscriptPanel
from cutora.core.history import (
    HistoryManager,
    _get_recent_projects,
    _load_app_settings,
    _load_gpu_enabled,
    _record_recent_project,
    _save_app_settings,
    _save_export_video_mode,
    _save_gpu_enabled,
)
from cutora.core import history as _t01_history
from cutora.core.brand import (
    PROJECT_FILTER,
    PROJECT_SAVE_FILTER,
    ensure_save_extension,
    format_window_title,
)
from cutora.core.paths import ICON_DIR, user_cache_dir
from cutora.core.seek_trace import emit as _seek_trace_emit
from cutora.audio.waveform import _WaveformWorker
from cutora.ui.command_bodies import CommandBodies

def get_or_create_track(timeline, track_type):
    """
    根据 track_type 获取已有轨道；不存在则创建并注册
    """
    from cutora.model.timeline_model import Track

    # 在现有轨道池中查找
    for tr in timeline.tracks:
        if tr.type == track_type:
            return tr

    # 不存在 → 创建
    tr = Track(track_type)
    timeline.add_track(tr)
    return tr


def ensure_subs_track_for_segments(task):
    """If task.segments is non-empty, keep/create a subs track.

    Stitch writes Track('subs'). Opening with empty segments lets
    prune_padded_tracks drop it; ASR later fills segments but never
    puts the row back. Heal here so canvas/transcript and the
    timeline stay in sync.
    """
    if not isinstance(task, dict):
        return None
    if not (task.get("segments") or []):
        return None
    tl = task.get("timeline")
    if tl is None:
        return None
    tracks = getattr(tl, "tracks", None)
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    for tr in tracks or []:
        ttype = getattr(tr, "type", None)
        if ttype is None and isinstance(tr, dict):
            ttype = tr.get("type")
        if ttype == "subs":
            return tr
    if hasattr(tl, "tracks") and not isinstance(tl, dict):
        return get_or_create_track(tl, "subs")
    try:
        tl_obj = _tl_of(task)
    except Exception:
        return None
    if tl_obj is None:
        return None
    return get_or_create_track(tl_obj, "subs")


def _overlay_time_overlap(ov, start, end):
    ov_s = float(ov.get("start") or 0.0)
    ov_e = float(ov.get("end") or 0.0)
    return not (end <= ov_s or start >= ov_e)


def _first_track_id_of_type(timeline, track_type):
    tracks = getattr(timeline, "tracks", None) or []
    for tr in tracks:
        if getattr(tr, "type", None) == track_type:
            return getattr(tr, "id", None)
    return None


def overlay_belongs_to_track(ov, track, first_id):
    tid = ov.get("_track_id") if isinstance(ov, dict) else None
    track_id = getattr(track, "id", None)
    if tid == track_id:
        return True
    return tid is None and track_id == first_id


def track_is_empty(track, task):
    """True when the track has no clips, overlays, or subtitle segments."""
    if getattr(track, "clips", None):
        return False
    ttype = getattr(track, "type", "")
    first_id = None
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is not None:
        first_id = _first_track_id_of_type(tl, ttype)
    if ttype == "text":
        for ov in (task.get("text_overlays") or []) if isinstance(task, dict) else []:
            if overlay_belongs_to_track(ov, track, first_id):
                return False
    elif ttype == "image":
        for ov in (task.get("image_overlays") or []) if isinstance(task, dict) else []:
            if overlay_belongs_to_track(ov, track, first_id):
                return False
    elif ttype == "adjust":
        tid = getattr(track, "id", None)
        for ov in (task.get("image_overlays") or []) if isinstance(task, dict) else []:
            if isinstance(ov, dict) and ov.get("type") == "adjust" and ov.get("_track_id") == tid:
                return False
    elif ttype == "subs":
        from cutora.subtitle.track_source import track_event_count
        if track_event_count(track) > 0:
            return False
        if isinstance(task, dict) and (task.get("segments") or []):
            return False
    return True


def pick_or_create_overlay_track(timeline, track_type, start, end, overlays):
    """Topmost unlocked track of type with no overlap; otherwise create one."""
    from cutora.model.timeline_model import Track
    first_id = _first_track_id_of_type(timeline, track_type)
    for tr in timeline.tracks:
        if getattr(tr, "type", None) != track_type:
            continue
        if getattr(tr, "locked", False):
            continue
        busy = False
        for ov in overlays or []:
            if not overlay_belongs_to_track(ov, tr, first_id):
                continue
            if _overlay_time_overlap(ov, start, end):
                busy = True
                break
        if not busy:
            return tr
    tr = Track(track_type)
    timeline.add_track(tr)
    return tr


def prune_padded_tracks(tl_obj, task):
    """Keep each type only up to the highest occupied track index.

    Empty tracks above that index are dropped. Types with no content
    are dropped entirely (no more blank Image / Text / Video 2–5).
    """
    groups = {}
    for tr in list(tl_obj.tracks):
        groups.setdefault(getattr(tr, "type", ""), []).append(tr)
    keep = set()
    for ttype, tracks in groups.items():
        last = -1
        for i, tr in enumerate(tracks):
            if not track_is_empty(tr, task):
                last = i
        if last >= 0:
            for tr in tracks[: last + 1]:
                keep.add(getattr(tr, "id", None))
    tl_obj.tracks = [tr for tr in tl_obj.tracks if getattr(tr, "id", None) in keep]
    vis = [t for t in tl_obj.tracks if getattr(t, "domain", "visual") == "visual"]
    aud = [t for t in tl_obj.tracks if getattr(t, "domain", "visual") == "audio"]
    tl_obj.tracks = vis + aud
    return tl_obj

def get_or_create_bgm_track(timeline):
    """Homogeneous 配乐轨. Never a mixed/main track."""
    if _SFX is not None:
        return _SFX.ensure_role_track(timeline, "bgm")
    from cutora.model.timeline_model import Track
    tr = Track("audio")
    tr.audio_role = "bgm"
    tr.name = "配乐"
    timeline.add_track(tr)
    return tr

def get_or_create_sfx_track(timeline):
    if _SFX is not None:
        return _SFX.ensure_role_track(timeline, "sfx")
    from cutora.model.timeline_model import Track
    tr = Track("audio")
    tr.audio_role = "sfx"
    tr.name = "音效"
    timeline.add_track(tr)
    return tr

def track_supports_batch(track):
    """轨道共享判定：仅允许非主视频、非字幕、非主音频轨道。"""
    if track is None:
        return False
    t_type = getattr(track, "type", None)
    if t_type == "subs":
        return False
    if t_type == "video" and getattr(track, "is_main_video", False):
        return False
    if t_type == "audio":
        for c in getattr(track, "clips", []) or []:
            if getattr(c, "is_main", False):
                return False
    # 双保险：任何包含缩略图前置片段或开场视频片段的轨道都不参与共享
    for c in getattr(track, "clips", []) or []:
        if bool(getattr(c, "is_thumbnail_leadin", False)):
            return False
        if bool(getattr(c, "is_intro_video_leadin", False)):
            return False
    return True

def get_batch_tracks(timeline):
    return [
        tr for tr in getattr(timeline, "tracks", [])
        if getattr(tr, "enable_batch", False) and track_supports_batch(tr)
    ]

def _host_stems_ready(host) -> bool:
    mixer = getattr(host, "_stem_mixer", None)
    fn = getattr(mixer, "stems_ready", None) if mixer is not None else None
    try:
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _tl_of(task_data):
    """获取 task_data 中的 Timeline 对象。
    如果 task["timeline"] 仍然是 dict（旧项目），则就地升级为 Timeline 对象。
    返回 Timeline 或 None。"""
    if not isinstance(task_data, dict):
        return None
    tl = task_data.get("timeline")
    if tl is None:
        return None
    from cutora.model.timeline import Timeline as _TL
    if isinstance(tl, _TL):
        return tl
    if isinstance(tl, dict):
        from cutora.model.timeline import DEFAULT_TIMELINE_WIDTH, DEFAULT_TIMELINE_HEIGHT, DEFAULT_TIMELINE_FPS
        obj = _TL(
            tl.get("width", DEFAULT_TIMELINE_WIDTH),
            tl.get("height", DEFAULT_TIMELINE_HEIGHT),
            tl.get("fps", DEFAULT_TIMELINE_FPS),
        )
        obj.sources = tl.get("sources", {})
        # 直接接管 tracks 列表（Track 对象已在 dict 中）
        raw_tracks = tl.get("tracks", [])
        obj.tracks = list(raw_tracks) if raw_tracks else []
        task_data["timeline"] = obj  # 就地替换，后续访问直接得到 Timeline 对象
        return obj
    return None


def _audio_clips_from_track(task_data):
    """兼容桥接：从 Track.clips + sources 构建 audio_clips 兼容列表。
    返回 list[dict]，格式与旧 audio_clips 一致，供 UI/prop_panel 使用。
    仅读取，不修改数据。"""
    tl = _tl_of(task_data)
    if tl is None:
        return []
    tracks = tl.tracks
    sources = tl.sources
    result = []
    for t in tracks:
        if not hasattr(t, "type") or t.type != "audio":
            continue
        for clip in (t.clips if hasattr(t, "clips") else []):
            sid = getattr(clip, "source_id", None)
            src = sources.get(sid) if sid else None
            path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
            in_t = float(getattr(clip, "in_t", 0.0))
            out_t = float(getattr(clip, "out_t", in_t))
            start_t = float(getattr(clip, "start_t", 0.0))
            gain_db = float(getattr(clip, "gain_db", 0.0))
            # 从 dB 转换回线性 volume（用于 UI 兼容）
            volume = 10.0 ** (gain_db / 20.0) if gain_db != 0.0 else 1.0
            audio_role = getattr(clip, "audio_role", None) or (
                "main" if getattr(clip, "is_main", False) else "bgm"
            )
            result.append({
                "path": path or "",
                "start": start_t,
                "end": start_t + max(0.0, out_t - in_t),
                "trim_in": in_t,
                "volume": round(volume, 4),
                "gain_db": gain_db,
                "pitch_semitones": float(getattr(clip, "pitch_semitones", 0.0)),
                "fade_in_dur": float(getattr(clip, "fade_in_dur", 0.0)),
                "fade_out_dur": float(getattr(clip, "fade_out_dur", 0.0)),
                "fade_in_enabled": float(getattr(clip, "fade_in_dur", 0.0)) > 0,
                "fade_out_enabled": float(getattr(clip, "fade_out_dur", 0.0)) > 0,
                "spatial_enabled": bool(getattr(clip, "spatial_enabled", False)),
                "spatial_amount": float(getattr(clip, "spatial_amount", 0.0)),
                "is_main": bool(getattr(clip, "is_main", False)),
                "audio_role": audio_role,
                "loop": bool(getattr(clip, "loop", False)),
                # 保留 Clip 对象引用，方便编辑时回写
                "_clip_obj": clip,
                "_source_id": sid,
            })
    return result

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}

VID_EXT = {".mp4", ".mov", ".mkv", ".webm"}

# Q115：区分「没传 target_track」和「明确传了 None」
_DROP_TARGET_FROM_WIDGET = object()

AUD_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"}

def match_intro_media(video_path, media_files):
    stem = Path(video_path).stem
    for f in media_files:
        if f.stem.startswith(stem):
            return f
    return None

def extract_first_two_sentences(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r'(?<=[。.!?])', text)
    out = []
    for p in parts:
        if p.strip():
            out.append(p.strip())
        if len(out) >= 2:
            break
    return "\n".join(out)

def _clip_audio_role(clip):
    if clip is None:
        return ""
    if isinstance(clip, dict):
        role = clip.get("audio_role")
        if role:
            return str(role)
        return "main" if clip.get("is_main") else "bgm"
    role = getattr(clip, "audio_role", None)
    if role:
        return str(role)
    return "main" if getattr(clip, "is_main", False) else "bgm"

def _is_bgm_clip(clip):
    return _clip_audio_role(clip) == "bgm"

def _is_sfx_clip(clip):
    return _clip_audio_role(clip) == "sfx"

def _find_main_video_track(task):
    tl = _tl_of(task)
    if tl is None:
        return None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video" and getattr(tr, "is_main_video", False):
            return tr
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            return tr
    return None

def _find_thumbnail_clip(task):
    tr = _find_main_video_track(task)
    if tr is None:
        return None, None
    for c in getattr(tr, "clips", []) or []:
        if bool(getattr(c, "is_thumbnail_leadin", False)):
            return tr, c
    return tr, None

def _find_main_content_clip(task):
    tr = _find_main_video_track(task)
    if tr is None:
        return None, None
    for c in getattr(tr, "clips", []) or []:
        if not bool(getattr(c, "is_thumbnail_leadin", False)):
            return tr, c
    return tr, None

def _get_main_video_duration(task):
    """获取主视频时长（仅主内容片段，不含其他素材）。"""
    task = task if isinstance(task, dict) else {}
    try:
        _tr, clip = _find_main_content_clip(task)
    except Exception:
        clip = None
    if clip is not None:
        try:
            in_t = float(getattr(clip, "in_t", 0.0))
            out_t = float(getattr(clip, "out_t", in_t))
            dur = max(0.0, out_t - in_t)
            if dur > 0:
                return dur
        except Exception:
            pass
    try:
        dur = float(task.get("duration", 0.0))
        if dur > 0:
            return dur
    except Exception:
        pass
    try:
        return float(probe_duration_sec(task.get("video")) or 0.0)
    except Exception:
        return 0.0

def _prepare_thumbnail_text_meta(task):
    if not isinstance(task, dict):
        return None
    tl = _tl_of(task)
    if tl is not None:
        tw = int(getattr(tl, "width", DEFAULT_TIMELINE_WIDTH) or DEFAULT_TIMELINE_WIDTH)
        th = int(getattr(tl, "height", DEFAULT_TIMELINE_HEIGHT) or DEFAULT_TIMELINE_HEIGHT)
    else:
        dims = task.get("dims", (DEFAULT_TIMELINE_WIDTH, DEFAULT_TIMELINE_HEIGHT))
        tw = int(dims[0] if isinstance(dims, (list, tuple)) and len(dims) > 0 else DEFAULT_TIMELINE_WIDTH)
        th = int(dims[1] if isinstance(dims, (list, tuple)) and len(dims) > 1 else DEFAULT_TIMELINE_HEIGHT)
    auto_text = ""
    segs = task.get("segments", []) or []
    if segs:
        auto_text = " ".join(str(seg.get("text", "")) for seg in segs)
    content = extract_first_two_sentences(auto_text)
    # 文本框目标：宽约 55%~65%，高约 20%~28%，位置中间偏下（并强制在画布内）
    box_w = int(max(200, min(tw - 8, tw * 0.60)))
    box_h = int(max(120, min(th - 8, th * 0.24)))
    box_x = int((tw - box_w) / 2)
    box_y = int(th * 0.62)
    box_x = max(0, min(box_x, max(0, tw - box_w)))
    box_y = max(0, min(box_y, max(0, th - box_h)))
    font_size = int(max(24, min(56, round(th * 0.055))))
    return {
        "content": content,
        "x": box_x, "y": box_y, "w": box_w, "h": box_h,
        "font_family": "Arimo",
        "fontsize": font_size,
        "bold": True,
        "italic": False,
        "color": "#FFFFFF",
        "use_stroke": True,
        "stroke_color": "#000000",
        "stroke_width": 3,
        "bg_enabled": True,
        "bg_color": "#000000",
        "bg_opacity": 165,
        "bg_padding": 12,
        "bg_radius": 12,
        "shadow_enabled": False,
        "shadow_opacity": 120,
        "shadow_blur": 0,
        "shadow_offset_x": 4,
        "shadow_offset_y": 4,
        "text_layout": {
            "left_pad": 12, "right_pad": 12,
            "top_pad": 10, "bottom_pad": 10,
            "line_spacing": 0, "letter_spacing": 0,
            "align": "left"
        }
    }

_THUMB_TEXT_STYLE_KEYS = [
    "x", "y", "w", "h",
    "rotation", "opacity", "blend_mode",
    "font_family", "fontsize", "bold", "italic", "color",
    "use_stroke", "stroke_color", "stroke_width",
    "bg_enabled", "bg_color", "bg_opacity", "bg_padding", "bg_radius",
    "shadow_enabled", "shadow_color", "shadow_opacity", "shadow_blur",
    "shadow_offset_x", "shadow_offset_y",
    "text_layout"
]

def _build_thumbnail_text_overlay(task, duration_sec, text_meta):
    if not isinstance(text_meta, dict):
        return None
    duration = float(duration_sec or 0.0)
    if duration <= 0:
        return None
    layout = _rich_default_layout(text_meta.get("text_layout"))
    return {
        "type": "text",
        "content": str(text_meta.get("content", "") or ""),
        "start": 0.0,
        "end": duration,
        "x": float(text_meta.get("x", 0.0)),
        "y": float(text_meta.get("y", 0.0)),
        "w": float(text_meta.get("w", 0.0)),
        "h": float(text_meta.get("h", 0.0)),
        "font_family": sanitize_font_family(text_meta.get("font_family", DEFAULT_FONT_FAMILY)),
        "fontsize": int(text_meta.get("fontsize", 42)),
        "bold": bool(text_meta.get("bold", True)),
        "italic": bool(text_meta.get("italic", False)),
        "color": text_meta.get("color", "#FFFFFF"),
        "use_stroke": bool(text_meta.get("use_stroke", True)),
        "stroke_color": text_meta.get("stroke_color", "#000000"),
        "stroke_width": int(text_meta.get("stroke_width", 3)),
        "bg_enabled": bool(text_meta.get("bg_enabled", True)),
        "bg_color": text_meta.get("bg_color", "#000000"),
        "bg_opacity": int(text_meta.get("bg_opacity", 191)),
        "bg_padding": int(text_meta.get("bg_padding", 12)),
        "bg_radius": int(text_meta.get("bg_radius", 12)),
        "shadow_enabled": bool(text_meta.get("shadow_enabled", False)),
        "shadow_color": text_meta.get("shadow_color", "#000000"),
        "shadow_opacity": int(text_meta.get("shadow_opacity", 120)),
        "shadow_blur": int(text_meta.get("shadow_blur", 0)),
        "shadow_offset_x": float(text_meta.get("shadow_offset_x", 4)),
        "shadow_offset_y": float(text_meta.get("shadow_offset_y", 4)),
        "rotation": float(text_meta.get("rotation", 0.0)),
        "opacity": int(text_meta.get("opacity", 255)),
        "blend_mode": text_meta.get("blend_mode", "normal"),
        "content_format": "plain",
        "text_layout": layout,
        "is_thumbnail_text": True
    }

def _find_thumbnail_text_overlay(task):
    for ov in task.get("text_overlays", []) or []:
        if isinstance(ov, dict) and ov.get("is_thumbnail_text"):
            return ov
    return None

def _ensure_thumbnail_text_overlay(task, duration_sec=None, text_meta=None):
    if not isinstance(task, dict):
        return None
    existing = _find_thumbnail_text_overlay(task)
    if existing is not None:
        return existing
    _tr, clip = _find_thumbnail_clip(task)
    if text_meta is None:
        text_meta = getattr(clip, "thumbnail_text", None) if clip is not None else None
    if not isinstance(text_meta, dict):
        text_meta = _prepare_thumbnail_text_meta(task)
    if duration_sec is None:
        state = task.get("_thumbnail_state") if isinstance(task.get("_thumbnail_state"), dict) else {}
        duration_sec = state.get("duration")
        if not duration_sec and clip is not None:
            duration_sec = getattr(clip, "thumbnail_duration", None) or getattr(clip, "thumb_duration", None)
    ov = _build_thumbnail_text_overlay(task, duration_sec, text_meta)
    if ov is None:
        return None
    task.setdefault("text_overlays", []).append(ov)
    if clip is not None:
        try:
            clip.thumbnail_text = None
            clip.thumbnail_text_as_overlay = True
        except Exception:
            pass
    return ov

def _remove_thumbnail_text_overlay(task):
    if not isinstance(task, dict):
        return 0
    ovs = task.get("text_overlays", []) or []
    removed = 0
    kept = []
    for ov in ovs:
        if isinstance(ov, dict) and ov.get("is_thumbnail_text"):
            removed += 1
            continue
        kept.append(ov)
    task["text_overlays"] = kept
    return removed

def _extract_thumbnail_text_style(ov):
    if not isinstance(ov, dict):
        return {}
    out = {}
    for k in _THUMB_TEXT_STYLE_KEYS:
        if k in ov:
            out[k] = copy.deepcopy(ov[k])
    return out

def _apply_thumbnail_text_style(ov, style):
    if not isinstance(ov, dict) or not isinstance(style, dict):
        return
    for k, v in style.items():
        ov[k] = copy.deepcopy(v)
    ov["font_family"] = sanitize_font_family(ov.get("font_family", DEFAULT_FONT_FAMILY))
    if "text_layout" in ov:
        ov["text_layout"] = _rich_default_layout(ov.get("text_layout"))

def _verify_first_frame_readable(video_path):
    """使用 OpenCV 验证首帧可读取，确保缩略图静帧源可用。"""
    try:
        if not video_path or not os.path.exists(video_path):
            return False
        cap = _open_video_capture(video_path)
        if cap is None:
            return False
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()
        cap.release()
        return bool(ret and frame is not None)
    except Exception:
        return False

def apply_thumbnail_leadin_to_task(task, duration_sec):
    """为任务添加主视频轨前置缩略图静帧片段。"""
    if not isinstance(task, dict):
        return False, "invalid_task"
    duration = float(duration_sec or 0.0)
    if duration <= 0:
        return False, "invalid_duration"
    tr, existing_thumb = _find_thumbnail_clip(task)
    if existing_thumb is not None:
        return False, "already_exists"
    tr_main, main_clip = _find_main_content_clip(task)
    if tr_main is None or main_clip is None:
        return False, "missing_main_clip"
    tl = _tl_of(task)
    sid = getattr(main_clip, "source_id", None)
    src = tl.sources.get(sid) if (tl is not None and sid) else None
    src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
    if not _verify_first_frame_readable(src_path):
        return False, "frame_unreadable"

    backup = _build_thumbnail_shift_backup(task)
    _shift_task_for_thumbnail(task, duration)

    thumb_clip = Clip(sid, 0.0, float(duration), 0.0)
    thumb_clip.is_thumbnail_leadin = True
    thumb_clip.thumbnail_duration = float(duration)
    thumb_clip.thumb_duration = float(duration)
    thumb_clip.freeze_source_t = 0.0
    thumb_clip.freeze_frame_index = 0
    thumb_clip.fit_mode = getattr(main_clip, "fit_mode", DEFAULT_VIDEO_FIT_MODE)
    thumb_clip.x = float(getattr(main_clip, "x", 0.0))
    thumb_clip.y = float(getattr(main_clip, "y", 0.0))
    thumb_clip.scale = float(getattr(main_clip, "scale", 1.0))
    thumb_clip.rotation = float(getattr(main_clip, "rotation", 0.0))
    thumb_clip.flip_x = bool(getattr(main_clip, "flip_x", False))
    thumb_clip.flip_y = bool(getattr(main_clip, "flip_y", False))
    thumb_clip.opacity = float(getattr(main_clip, "opacity", 255))
    thumb_clip.thumbnail_text = _prepare_thumbnail_text_meta(task)

    tr_main.clips.insert(0, thumb_clip)
    _ensure_thumbnail_text_overlay(task, duration, thumb_clip.thumbnail_text)
    task["_thumbnail_state"] = {
        "enabled": True,
        "duration": float(duration),
        "shift_applied": True,
        "backup_positions": backup,
    }
    task["video_time_offset"] = float(duration)
    task["intro_media"] = None
    remap_bgm_to_full_duration(task)
    return True, "applied"

def remove_thumbnail_leadin_from_task(task):
    """撤销任务的缩略图前置片段，并恢复应用前时间线位置。"""
    if not isinstance(task, dict):
        return False, "invalid_task"
    tr, thumb_clip = _find_thumbnail_clip(task)
    if tr is None or thumb_clip is None:
        return False, "not_found"
    state = task.get("_thumbnail_state") if isinstance(task.get("_thumbnail_state"), dict) else {}
    backup = state.get("backup_positions") if isinstance(state, dict) else None
    duration = float(state.get("duration", getattr(thumb_clip, "thumbnail_duration", 0.0)) if isinstance(state, dict) else 0.0)

    _remove_thumbnail_text_overlay(task)
    try:
        tr.clips.remove(thumb_clip)
    except Exception:
        pass

    if isinstance(backup, dict):
        _restore_task_from_thumbnail_backup(task, backup)
    elif duration > 0:
        _shift_task_for_thumbnail(task, -duration)

    task.pop("_thumbnail_state", None)
    task["video_time_offset"] = 0.0
    task["intro_media"] = None
    remap_bgm_to_full_duration(task)
    return True, "removed"

def update_thumbnail_leadin_duration(task, new_duration_sec):
    """修改已有缩略图片段时长（保留原始 backup，用于精准撤销）。"""
    if not isinstance(task, dict):
        return False, "invalid_task"
    tr, thumb_clip = _find_thumbnail_clip(task)
    if tr is None or thumb_clip is None:
        return False, "not_found"
    state = task.get("_thumbnail_state")
    if not isinstance(state, dict):
        state = {"enabled": True, "duration": float(getattr(thumb_clip, "thumbnail_duration", 0.0)), "shift_applied": True, "backup_positions": None}
        task["_thumbnail_state"] = state
    old_duration = float(state.get("duration", getattr(thumb_clip, "thumbnail_duration", 0.0)) or 0.0)
    new_duration = float(new_duration_sec or 0.0)
    if new_duration <= 0:
        return False, "invalid_duration"
    delta = new_duration - old_duration
    if abs(delta) <= 0.01:
        return True, "unchanged"

    _shift_task_for_thumbnail(task, delta)
    thumb_clip.in_t = 0.0
    thumb_clip.out_t = float(new_duration)
    thumb_clip.start_t = 0.0
    thumb_clip.thumbnail_duration = float(new_duration)
    thumb_clip.thumb_duration = float(new_duration)
    thumb_ov = _ensure_thumbnail_text_overlay(task, new_duration, getattr(thumb_clip, "thumbnail_text", None))
    if isinstance(thumb_ov, dict):
        thumb_ov["start"] = 0.0
        thumb_ov["end"] = float(new_duration)
    state["enabled"] = True
    state["duration"] = float(new_duration)
    state["shift_applied"] = True
    task["video_time_offset"] = float(new_duration)
    remap_bgm_to_full_duration(task)
    return True, "updated"

def _build_thumbnail_shift_backup(task):
    backup = {
        "segments": [],
        "segment_words": [],
        "text_overlays": [],
        "image_overlays": [],
        "track_clips": {}
    }
    for i, seg in enumerate(task.get("segments", []) or []):
        backup["segments"].append((i, seg.get("start"), seg.get("end")))
        words = []
        for wi, w in enumerate(seg.get("words", []) or []):
            words.append((wi, w.get("start"), w.get("end")))
        backup["segment_words"].append((i, words))
    for i, ov in enumerate(task.get("text_overlays", []) or []):
        backup["text_overlays"].append((i, ov.get("start"), ov.get("end")))
    for i, ov in enumerate(task.get("image_overlays", []) or []):
        backup["image_overlays"].append((i, ov.get("start"), ov.get("end")))
    tl = _tl_of(task)
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            tr_id = getattr(tr, "id", None) or uuid.uuid4().hex
            clips = []
            for ci, c in enumerate(getattr(tr, "clips", []) or []):
                clips.append((ci, float(getattr(c, "start_t", 0.0))))
            backup["track_clips"][tr_id] = clips
    # 保存播放尾位置，以便撤销时恢复
    backup["trim_end_sec"] = float(task.get("trim_end_sec", 0.0))
    backup["trim_start_sec"] = float(task.get("trim_start_sec", 0.0))
    return backup

def _shift_task_for_thumbnail(task, delta_sec):
    if not isinstance(delta_sec, (int, float)) or abs(delta_sec) <= 1e-9:
        return
    delta_sec = float(delta_sec)
    # Segments
    for seg in task.get("segments", []) or []:
        s = seg.get("start"); e = seg.get("end")
        if isinstance(s, (int, float)): seg["start"] = s + delta_sec
        if isinstance(e, (int, float)): seg["end"] = e + delta_sec
        for w in seg.get("words", []) or []:
            ws = w.get("start"); we = w.get("end")
            if isinstance(ws, (int, float)): w["start"] = ws + delta_sec
            if isinstance(we, (int, float)): w["end"] = we + delta_sec
    # Overlays
    for ov in task.get("text_overlays", []) or []:
        if isinstance(ov, dict) and ov.get("is_thumbnail_text"):
            continue
        s = ov.get("start"); e = ov.get("end")
        if isinstance(s, (int, float)): ov["start"] = s + delta_sec
        if isinstance(e, (int, float)): ov["end"] = e + delta_sec
    for ov in task.get("image_overlays", []) or []:
        s = ov.get("start"); e = ov.get("end")
        if isinstance(s, (int, float)): ov["start"] = s + delta_sec
        if isinstance(e, (int, float)): ov["end"] = e + delta_sec
    # Timeline clips: shift all tracks except BGM clips
    tl = _tl_of(task)
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            t_type = getattr(tr, "type", None)
            for c in getattr(tr, "clips", []) or []:
                if bool(getattr(c, "is_thumbnail_leadin", False)):
                    continue
                if t_type == "audio" and _is_bgm_clip(c):
                    continue
                c.start_t = float(getattr(c, "start_t", 0.0)) + delta_sec
    # 同步后移播放尾（trim_end_sec），确保渲染范围包含开场+主视频
    old_trim_end = float(task.get("trim_end_sec", 0.0))
    if old_trim_end > 0:
        task["trim_end_sec"] = old_trim_end + delta_sec

def _restore_task_from_thumbnail_backup(task, backup):
    if not isinstance(task, dict) or not isinstance(backup, dict):
        return
    for i, s, e in backup.get("segments", []):
        segs = task.get("segments", []) or []
        if 0 <= i < len(segs):
            if isinstance(s, (int, float)): segs[i]["start"] = float(s)
            if isinstance(e, (int, float)): segs[i]["end"] = float(e)
    for i, words in backup.get("segment_words", []):
        segs = task.get("segments", []) or []
        if 0 <= i < len(segs):
            wlist = segs[i].get("words", []) or []
            for wi, ws, we in words:
                if 0 <= wi < len(wlist):
                    if isinstance(ws, (int, float)): wlist[wi]["start"] = float(ws)
                    if isinstance(we, (int, float)): wlist[wi]["end"] = float(we)
    for i, s, e in backup.get("text_overlays", []):
        ovs = task.get("text_overlays", []) or []
        if 0 <= i < len(ovs):
            if isinstance(s, (int, float)): ovs[i]["start"] = float(s)
            if isinstance(e, (int, float)): ovs[i]["end"] = float(e)
    for i, s, e in backup.get("image_overlays", []):
        ovs = task.get("image_overlays", []) or []
        if 0 <= i < len(ovs):
            if isinstance(s, (int, float)): ovs[i]["start"] = float(s)
            if isinstance(e, (int, float)): ovs[i]["end"] = float(e)
    tl = _tl_of(task)
    if tl is not None:
        by_track = backup.get("track_clips", {})
        for tr in getattr(tl, "tracks", []) or []:
            tr_id = getattr(tr, "id", None)
            saved = by_track.get(tr_id, [])
            clips = getattr(tr, "clips", []) or []
            for ci, st in saved:
                if 0 <= ci < len(clips):
                    clips[ci].start_t = float(st)
    # 恢复播放尾（trim_end_sec）
    if "trim_end_sec" in backup:
        task["trim_end_sec"] = float(backup["trim_end_sec"])

# ───────────────────────────────────────────────────────
# 开场视频 (Intro Video Lead-in) — 纯函数层
# ───────────────────────────────────────────────────────

def _find_intro_video_clips(task):
    """查找主视频轨和主音频轨上标记为 is_intro_video_leadin 的 clip。
    返回 (video_track, video_clip, audio_track, audio_clip)。
    任意未找到则对应位返回 None。"""
    tl = _tl_of(task)
    if tl is None:
        return None, None, None, None
    v_tr, v_clip, a_tr, a_clip = None, None, None, None
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            if bool(getattr(c, "is_intro_video_leadin", False)):
                if getattr(tr, "type", None) == "video":
                    v_tr, v_clip = tr, c
                elif getattr(tr, "type", None) == "audio":
                    a_tr, a_clip = tr, c
    return v_tr, v_clip, a_tr, a_clip

def _build_intro_video_shift_backup(task):
    """与缩略图类似的备份机制，记录平移前所有时间戳。"""
    backup = {
        "segments": [],
        "segment_words": [],
        "text_overlays": [],
        "image_overlays": [],
        "track_clips": {}
    }
    for i, seg in enumerate(task.get("segments", []) or []):
        backup["segments"].append((i, seg.get("start"), seg.get("end")))
        words = []
        for wi, w in enumerate(seg.get("words", []) or []):
            words.append((wi, w.get("start"), w.get("end")))
        backup["segment_words"].append((i, words))
    for i, ov in enumerate(task.get("text_overlays", []) or []):
        backup["text_overlays"].append((i, ov.get("start"), ov.get("end")))
    for i, ov in enumerate(task.get("image_overlays", []) or []):
        backup["image_overlays"].append((i, ov.get("start"), ov.get("end")))
    tl = _tl_of(task)
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            tr_id = getattr(tr, "id", None) or uuid.uuid4().hex
            clips = []
            for ci, c in enumerate(getattr(tr, "clips", []) or []):
                clips.append((ci, float(getattr(c, "start_t", 0.0))))
            backup["track_clips"][tr_id] = clips
    # 保存播放尾位置
    backup["trim_end_sec"] = float(task.get("trim_end_sec", 0.0))
    backup["trim_start_sec"] = float(task.get("trim_start_sec", 0.0))
    return backup

def _shift_task_for_intro_video(task, delta_sec):
    """将字幕/overlay/非开场clip 整体后移 delta_sec 秒（与缩略图类似，但跳过开场视频标记的clip）。"""
    if not isinstance(delta_sec, (int, float)) or abs(delta_sec) <= 1e-9:
        return
    delta_sec = float(delta_sec)
    # Segments
    for seg in task.get("segments", []) or []:
        s = seg.get("start"); e = seg.get("end")
        if isinstance(s, (int, float)): seg["start"] = s + delta_sec
        if isinstance(e, (int, float)): seg["end"] = e + delta_sec
        for w in seg.get("words", []) or []:
            ws = w.get("start"); we = w.get("end")
            if isinstance(ws, (int, float)): w["start"] = ws + delta_sec
            if isinstance(we, (int, float)): w["end"] = we + delta_sec
    # Text overlays（跳过 thumbnail text）
    for ov in task.get("text_overlays", []) or []:
        if isinstance(ov, dict) and ov.get("is_thumbnail_text"):
            continue
        s = ov.get("start"); e = ov.get("end")
        if isinstance(s, (int, float)): ov["start"] = s + delta_sec
        if isinstance(e, (int, float)): ov["end"] = e + delta_sec
    # Image overlays
    for ov in task.get("image_overlays", []) or []:
        s = ov.get("start"); e = ov.get("end")
        if isinstance(s, (int, float)): ov["start"] = s + delta_sec
        if isinstance(e, (int, float)): ov["end"] = e + delta_sec
    # Timeline clips: 后移所有非开场视频、非 BGM 的 clip
    tl = _tl_of(task)
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            t_type = getattr(tr, "type", None)
            for c in getattr(tr, "clips", []) or []:
                if bool(getattr(c, "is_intro_video_leadin", False)):
                    continue
                if bool(getattr(c, "is_thumbnail_leadin", False)):
                    continue
                if t_type == "audio" and _is_bgm_clip(c):
                    continue
                c.start_t = float(getattr(c, "start_t", 0.0)) + delta_sec
    # 同步后移播放尾（trim_end_sec），确保渲染范围包含开场+主视频
    old_trim_end = float(task.get("trim_end_sec", 0.0))
    if old_trim_end > 0:
        task["trim_end_sec"] = old_trim_end + delta_sec

def apply_intro_video_to_task(task, intro_video_path,
                               transition_type="zoom_in",
                               transition_duration=1.5,
                               intro_duration=5.0,
                               zoom_scale_start=0.7,
                               motion_blur=0.4,
                               intro_in_t=0.0):
    """将开场视频插入到任务的主视频轨道和音频轨道前面。

    Parameters
    ----------
    task : dict
        任务字典
    intro_video_path : str
        开场视频文件路径
    transition_type : str
        转场类型 "zoom_in" 或 "cross_dissolve"
    transition_duration : float
        转场时长（秒）
    intro_duration : float
        开场视频最大保留时长（默认 5.0 秒）
    intro_in_t : float
        源素材起播点（秒），跳过前面的画面（默认 0.0）

    Returns
    -------
    (bool, str)  成功/失败 + 原因
    """
    if not isinstance(task, dict):
        return False, "invalid_task"
    if not intro_video_path or not os.path.isfile(intro_video_path):
        return False, "invalid_intro_path"

    # 检查是否已有开场视频
    _vtr, _vc, _atr, _ac = _find_intro_video_clips(task)
    if _vc is not None:
        return False, "already_exists"

    tl = _tl_of(task)
    if tl is None:
        return False, "no_timeline"

    # 获取开场视频时长
    intro_full_dur = probe_duration_sec(intro_video_path)
    if not isinstance(intro_full_dur, (int, float)) or intro_full_dur <= 0:
        return False, "cannot_probe_intro"

    intro_in_t = float(intro_in_t or 0.0)
    intro_in_t = max(0.0, min(intro_in_t, intro_full_dur - 0.5))  # 钳位
    intro_duration = float(intro_duration or 5.0)
    transition_duration = float(transition_duration or 1.5)
    # 可用区间 = 源总长 - 起播偏移
    available_dur = intro_full_dur - intro_in_t
    effective_intro_dur = min(available_dur, intro_duration)
    if effective_intro_dur <= 0.5:
        return False, "intro_too_short"

    # 查找主视频轨和主音频轨
    tr_main, main_clip = _find_main_content_clip(task)
    if tr_main is None or main_clip is None:
        return False, "missing_main_clip"

    # 获取主视频的 source 以继承 fit_mode
    main_fit = getattr(main_clip, "fit_mode", DEFAULT_VIDEO_FIT_MODE)

    # 创建 MediaSource 并注册
    intro_src = MediaSource(intro_video_path)
    intro_src.has_audio = has_audio_stream(intro_video_path)
    tl.sources[intro_src.id] = intro_src

    # 备份所有时间位置
    backup = _build_intro_video_shift_backup(task)

    # 后移所有现有内容（字幕、overlay、clip）以实现真正的物理重叠，重叠时长即转场时长
    overlap_shift = max(0.0, effective_intro_dur - transition_duration)
    _shift_task_for_intro_video(task, overlap_shift)

    # --- NLE Gen-2: 给开场视频生成专属的 Magnetic Link ---
    intro_link_group = uuid.uuid4().hex

    # --- 视频轨：创建开场视频 clip 并插入到主轨最前面 ---
    intro_vclip = Clip(intro_src.id, float(intro_in_t), float(intro_in_t + effective_intro_dur), 0.0)
    intro_vclip.link_group_id = intro_link_group
    intro_vclip.is_intro_video_leadin = True
    intro_vclip.intro_video_path = str(intro_video_path)
    intro_vclip.fit_mode = main_fit  # 继承主视频的适配模式，处理分辨率差异
    intro_vclip.blend_mode = "normal"
    intro_vclip.x = 0.0
    intro_vclip.y = 0.0
    intro_vclip.scale = 1.0
    intro_vclip.rotation = 0.0
    # 插入到主视频轨最前面（在缩略图之后，如果有的话）
    insert_pos = 0
    for i, c in enumerate(tr_main.clips):
        if bool(getattr(c, "is_thumbnail_leadin", False)):
            insert_pos = i + 1
            break
    tr_main.clips.insert(insert_pos, intro_vclip)

    # 真正的 NLE 独立转场：创建一个独立的 TransitionItem 覆盖重叠区域，不污染 Clip 自身的属性
    transition_dur = float(transition_duration)
    if transition_dur > 0:
        if transition_type.endswith("_out") or transition_type.endswith("_both"):
            t_preset = transition_type
        elif "cross_dissolve" in transition_type:
            t_preset = "cross_dissolve_both"
        else:
            t_preset = "zoom_in_both"
        
        # 物理重叠区间为 开场视频结尾处向前移 transition_dur
        # 实际上 intro_vclip 在 timeline 上的结束时间是 intro_vclip.start_t + eff_dur
        # NLE 中转场通常居中对齐或者在接缝处对齐，这里对齐到物理重叠的起始点
        t_start = intro_vclip.start_t + intro_vclip.effective_duration - transition_dur
        trans_item = TransitionItem(start_t=t_start, duration=transition_dur, preset=t_preset,
                                    zoom_scale_start=zoom_scale_start, motion_blur=motion_blur)
        if not hasattr(tr_main, "transitions"):
            tr_main.transitions = []
        tr_main.transitions.append(trans_item)

    # --- 音频轨：创建开场视频音频 clip ---
    intro_aclip = None
    if intro_src.has_audio:
        if _SFX is not None:
            main_audio_track = _SFX.ensure_role_track(tl, "main")
        else:
            main_audio_track = get_or_create_track(tl, "audio")
            main_audio_track.audio_role = "main"
            main_audio_track.name = "原声"

        intro_aclip = Clip(intro_src.id, float(intro_in_t), float(intro_in_t + effective_intro_dur), 0.0)
        intro_aclip.link_group_id = intro_link_group
        intro_aclip.is_intro_video_leadin = True
        intro_aclip.is_main = True
        intro_aclip.audio_role = "main"
        # 音频淡出效果：在转场区域淡出
        intro_aclip.fade_out_dur = float(transition_duration)

        # 插入到音频轨最前面
        a_insert_pos = 0
        for i, c in enumerate(main_audio_track.clips):
            if bool(getattr(c, "is_thumbnail_leadin", False)):
                a_insert_pos = i + 1
                break
        main_audio_track.clips.insert(a_insert_pos, intro_aclip)

    # 保存状态
    task["_intro_video_state"] = {
        "enabled": True,
        "intro_path": str(intro_video_path),
        "effective_duration": float(effective_intro_dur),
        "full_duration": float(intro_full_dur),
        "intro_duration_limit": float(intro_duration),
        "intro_in_t": float(intro_in_t),
        "transition_type": str(transition_type),
        "transition_duration": float(transition_duration),
        "zoom_scale_start": float(zoom_scale_start),
        "motion_blur": float(motion_blur),
        "overlap_shift": float(overlap_shift),
        "backup_positions": backup,
    }
    task["video_time_offset"] = float(task.get("video_time_offset", 0.0)) + float(overlap_shift)
    task["intro_media"] = None  # 与旧 intro_media 互斥
    remap_bgm_to_full_duration(task)
    return True, intro_src.id

def remove_intro_video_from_task(task):
    """撤销任务的开场视频插入，恢复原始时间线。"""
    if not isinstance(task, dict):
        return False, "invalid_task"

    v_tr, v_clip, a_tr, a_clip = _find_intro_video_clips(task)
    if v_clip is None:
        return False, "not_found"

    state = task.get("_intro_video_state") if isinstance(task.get("_intro_video_state"), dict) else {}
    backup = state.get("backup_positions") if isinstance(state, dict) else None
    effective_dur = float(state.get("effective_duration", 0.0) if isinstance(state, dict) else 0.0)

    # 移除视频 clip 和关联的独立转场
    if v_tr is not None and v_clip is not None:
        try:
            v_tr.clips.remove(v_clip)
        except Exception:
            pass
        if hasattr(v_tr, "transitions"):
            v_tr.transitions = []

    # 移除音频 clip
    if a_tr is not None and a_clip is not None:
        try:
            a_tr.clips.remove(a_clip)
        except Exception:
            pass

    # 恢复时间位置
    if isinstance(backup, dict):
        _restore_task_from_thumbnail_backup(task, backup)  # 复用缩略图的恢复函数
    elif effective_dur > 0:
        _shift_task_for_intro_video(task, -effective_dur)

    # 清除状态
    overlap_shift = float(state.get("overlap_shift", effective_dur))
    task.pop("_intro_video_state", None)
    offset = float(task.get("video_time_offset", 0.0))
    task["video_time_offset"] = max(0.0, offset - overlap_shift)
    task["intro_media"] = None
    remap_bgm_to_full_duration(task)
    return True, "removed"

def update_intro_video_duration(task, new_duration):
    """修改已插入开场视频的持续时长（remove → re-apply 策略）。

    Parameters
    ----------
    task : dict
    new_duration : float
        新的开场视频保留时长（秒）

    Returns
    -------
    (bool, str)
    """
    if not isinstance(task, dict):
        return False, "invalid_task"
    state = task.get("_intro_video_state")
    if not isinstance(state, dict) or not state.get("enabled"):
        return False, "no_intro_video"
    intro_path = state.get("intro_path")
    if not intro_path or not os.path.isfile(intro_path):
        return False, "intro_file_missing"
    new_duration = float(new_duration or 5.0)
    if new_duration <= 0.3:
        return False, "duration_too_short"

    # 保存现有参数
    t_type = state.get("transition_type", "zoom_in_both")
    t_dur = float(state.get("transition_duration", 1.5))
    zoom_start = float(state.get("zoom_scale_start", 0.7))
    m_blur = float(state.get("motion_blur", 0.4))
    src_in_t = float(state.get("intro_in_t", 0.0))

    # Remove → Re-apply
    ok, reason = remove_intro_video_from_task(task)
    if not ok:
        return False, f"remove_failed:{reason}"
    ok2, sid = apply_intro_video_to_task(
        task, intro_path,
        transition_type=t_type,
        transition_duration=t_dur,
        intro_duration=new_duration,
        zoom_scale_start=zoom_start,
        motion_blur=m_blur,
        intro_in_t=src_in_t,
    )
    return ok2, sid if ok2 else "reapply_failed"

def update_intro_video_settings(task, new_duration=None, transition_type=None,
                                 transition_duration=None, zoom_scale_start=None,
                                 motion_blur=None, intro_in_t=None):
    """修改已插入开场视频的全部参数（remove → re-apply 策略）。

    传入 None 的参数将保留原有值。

    Returns
    -------
    (bool, str)
    """
    if not isinstance(task, dict):
        return False, "invalid_task"
    state = task.get("_intro_video_state")
    if not isinstance(state, dict) or not state.get("enabled"):
        return False, "no_intro_video"
    intro_path = state.get("intro_path")
    if not intro_path or not os.path.isfile(intro_path):
        return False, "intro_file_missing"

    # 合并参数：未传入的使用原始值
    final_dur = float(new_duration) if new_duration is not None else float(state.get("intro_duration_limit", 5.0))
    final_t_type = str(transition_type) if transition_type is not None else state.get("transition_type", "zoom_in_both")
    final_t_dur = float(transition_duration) if transition_duration is not None else float(state.get("transition_duration", 1.5))
    final_zoom = float(zoom_scale_start) if zoom_scale_start is not None else float(state.get("zoom_scale_start", 0.7))
    final_blur = float(motion_blur) if motion_blur is not None else float(state.get("motion_blur", 0.4))
    final_in_t = float(intro_in_t) if intro_in_t is not None else float(state.get("intro_in_t", 0.0))

    if final_dur <= 0.3:
        return False, "duration_too_short"

    ok, reason = remove_intro_video_from_task(task)
    if not ok:
        return False, f"remove_failed:{reason}"
    ok2, sid = apply_intro_video_to_task(
        task, intro_path,
        transition_type=final_t_type,
        transition_duration=final_t_dur,
        intro_duration=final_dur,
        zoom_scale_start=final_zoom,
        motion_blur=final_blur,
        intro_in_t=final_in_t,
    )
    return ok2, sid if ok2 else "reapply_failed"

def shift_task_timeline(task, offset):
    if not isinstance(offset, (int, float)) or offset == 0:
        return
    # 字幕（段 + 单词）
    for seg in task.get("segments", []):
        s = seg.get("start")
        e = seg.get("end")
        if isinstance(s, (int, float)) and isinstance(e, (int, float)):
            seg["start"] = s + offset
            seg["end"] = e + offset
        for w in seg.get("words", []) or []:
            ws = w.get("start")
            we = w.get("end")
            if isinstance(ws, (int, float)):
                w["start"] = ws + offset
            if isinstance(we, (int, float)):
                w["end"] = we + offset

    # 文本 overlay
    for ov in task.get("text_overlays", []):
        s = ov.get("start")
        e = ov.get("end")
        if isinstance(s, (int, float)) and isinstance(e, (int, float)):
            ov["start"] = s + offset
            ov["end"] = e + offset

    # 图片 / overlay
    for ov in task.get("image_overlays", []):
        if ov.get("is_intro"):
            continue
        s = ov.get("start")
        e = ov.get("end")
        if isinstance(s, (int, float)) and isinstance(e, (int, float)):
            ov["start"] = s + offset
            ov["end"] = e + offset

    # 同步后移播放尾（trim_end_sec），确保渲染范围完整
    old_trim_end = float(task.get("trim_end_sec", 0.0))
    if old_trim_end > 0:
        task["trim_end_sec"] = old_trim_end + offset

def remap_bgm_to_full_duration(task):
    """
    强制让 BGM 覆盖整个最终视频时间线（开场+主视频全部）。
    通过扫描 NLE 时间轴的所有视频片段得到真实总时长，
    避免依赖已过时的 intro_media/video_time_offset 字段。
    """
    if not isinstance(task, dict):
        return

    # ── 扫描 NLE Video 轨道 start_t + effective_duration 的最大值 ──
    total_dur = 0.0
    tl = _tl_of(task)
    if tl is not None:
        for tr in getattr(tl, "tracks", []):
            if getattr(tr, "type", None) != "video":
                continue
            for clip in getattr(tr, "clips", []) or []:
                try:
                    start_t = float(getattr(clip, "start_t", 0.0))
                    eff = float(getattr(clip, "effective_duration", 0.0))
                    if eff <= 0:
                        in_t  = float(getattr(clip, "in_t", 0.0))
                        out_t = float(getattr(clip, "out_t", in_t))
                        speed = max(0.001, float(getattr(clip, "speed", 1.0)))
                        eff = max(0.0, (out_t - in_t) / speed)
                    total_dur = max(total_dur, start_t + eff)
                except Exception:
                    continue

    # 兜底：NLE 没有视频轨道时退化为旧逻辑
    if total_dur <= 0:
        video_path = task.get("video")
        base_dur = probe_duration_sec(video_path) or 0.0
        intro = task.get("intro_media")
        intro_dur = 0.0
        if isinstance(intro, dict):
            intro_dur = float(intro.get("duration", 0.0))
        thumb_state = task.get("_thumbnail_state")
        if isinstance(thumb_state, dict) and thumb_state.get("enabled"):
            intro_dur = max(intro_dur, float(thumb_state.get("duration", 0.0)))
        try:
            intro_dur = max(intro_dur, float(task.get("video_time_offset", 0.0)))
        except Exception:
            pass
        total_dur = base_dur + intro_dur

    if total_dur <= 0:
        return

    # ── 操作 Track.clips（NLE 数据模型）──
    # 只拉伸配乐，绝不改 sfx（音效按自己的起止时间插入）
    if tl is not None:
        for tr in getattr(tl, "tracks", []):
            if getattr(tr, "type", None) != "audio":
                continue
            for clip in getattr(tr, "clips", []) or []:
                if not _is_bgm_clip(clip):
                    continue
                clip.start_t = 0.0
                clip.loop = True
                if not getattr(clip, "audio_role", None):
                    clip.audio_role = "bgm"
                new_dur = max(clip.out_t - clip.in_t, total_dur)
                clip.out_t = clip.in_t + new_dur

    # ── 旧 audio_clips 兼容 ──
    audio_clips = task.get("audio_clips", [])
    for bgm in audio_clips:
        if bgm.get("audio_role") == "sfx" or bgm.get("is_main"):
            continue
        bgm["start"] = 0.0
        bgm["end"] = max(float(bgm.get("end", 0.0)), total_dur)
        bgm["loop"] = True

def place_shared_track(src_tl, src_tr, tgt_tl, new_tr, placed=None) -> int:
    """Q177 共享轨道保持叠压顺序：新轨道插在「源里它正下方那条轨道」在目标里对应的那条之上。

    对应关系：同一次共享里已经放好的 → 用放好的那条；否则 = 目标里同类型的第 N 条（N = 源里它是同类型第几条）。
    往下一直找不到对应的 → 放到同一域（画面 / 声音）最底下。返回插入位置。"""
    from cutora.model.timeline import domain_of
    placed = placed or {}
    tracks = list(getattr(src_tl, "tracks", None) or [])
    dom = domain_of(getattr(new_tr, "type", ""))
    new_tr.domain = dom
    tgt = tgt_tl.tracks
    if src_tr in tracks:
        i = tracks.index(src_tr)
        for j in range(i - 1, -1, -1):
            below = tracks[j]
            if domain_of(getattr(below, "type", "")) != dom:
                continue
            anchor = placed.get(id(below))
            if anchor is None:
                btype = getattr(below, "type", "")
                nth = sum(1 for t in tracks[:j] if getattr(t, "type", "") == btype)
                same = [t for t in tgt if getattr(t, "type", "") == btype]
                anchor = same[nth] if nth < len(same) else None
            if anchor is not None and anchor in tgt:
                pos = tgt.index(anchor) + 1
                tgt.insert(pos, new_tr)
                return pos
    # 源里它就在最底下（或下面的都对不上）：同一域的最底下
    pos = next((k for k, t in enumerate(tgt) if domain_of(getattr(t, "type", "")) == dom), None)
    if pos is None:
        if dom == "audio":
            pos = len(tgt)
        else:
            pos = next((k for k, t in enumerate(tgt) if domain_of(getattr(t, "type", "")) == "audio"), len(tgt))
    # 整幅背景轨永远垫最底：别插到它下面
    while pos < len(tgt) and str(getattr(tgt[pos], "bg_role", "") or "") and dom != "audio":
        pos += 1
    tgt.insert(pos, new_tr)
    return pos


def fit_track_to_duration(track, target_duration, task=None):
    """
    将轨道内容自动适配到目标视频时长（不影响源素材）。
    """
    if not isinstance(target_duration, (int, float)) or target_duration <= 0:
        return
    t_type = getattr(track, "type", None)
    if t_type in ("text", "image", "adjust") and isinstance(task, dict):
        list_name = "text_overlays" if t_type == "text" else "image_overlays"
        for ov in task.get(list_name, []) or []:
            if ov.get("_track_id") == getattr(track, "id", None):
                ov["start"] = 0.0
                ov["end"] = float(target_duration)
    elif t_type == "video":
        for clip in getattr(track, "clips", []) or []:
            if getattr(clip, "is_main", False):
                continue
            clip.start_t = 0.0
            clip.match_duration = True
            clip.loop = True
            clip._match_target_dur = float(target_duration)
            clip.out_t = max(clip.out_t, clip.in_t + 0.1)
    elif t_type == "audio":
        for clip in getattr(track, "clips", []) or []:
            if getattr(clip, "is_main", False):
                continue
            clip.start_t = 0.0
            orig_dur = max(0.01, float(clip.out_t - clip.in_t))
            clip.out_t = clip.in_t + max(orig_dur, float(target_duration))

def _scale_overlay_for_canvas(ov, src_w, src_h, tgt_w, tgt_h):
    """将 overlay 的空间属性按源/目标画布比例缩放。
    用于轨道共享时，确保不同分辨率视频间的 overlay 位置和大小一致。
    直接修改 ov dict（in-place）。"""
    if src_w <= 0 or src_h <= 0 or tgt_w <= 0 or tgt_h <= 0:
        return
    # 如果尺寸完全一致则跳过
    if abs(src_w - tgt_w) < 1 and abs(src_h - tgt_h) < 1:
        return
    sx = tgt_w / src_w
    sy = tgt_h / src_h
    # 字体 / 描边等使用统一缩放因子（取均值，保持视觉一致性）
    s_avg = (sx + sy) / 2.0

    # ── 顶层空间属性 ──
    for k in ("x", "w"):
        if k in ov:
            ov[k] = float(ov[k]) * sx
    for k in ("y", "h"):
        if k in ov:
            ov[k] = float(ov[k]) * sy
    # fontsize / stroke / padding 等按均值缩放
    for k in ("fontsize", "stroke_width", "border_width",
              "bg_padding", "bg_padding_x", "bg_padding_y", "bg_radius",
              "bg_expand_w", "bg_expand_h",
              "shadow_offset_x", "shadow_offset_y", "shadow_blur", "shadow_spread"):
        if k in ov and isinstance(ov[k], (int, float)):
            ov[k] = round(float(ov[k]) * s_avg)

    # Q175 调整条：边框宽度 / 羽化 / 画面变换的位移也是画布像素
    if ov.get("type") == "adjust":
        sol = ov.get("solid")
        if isinstance(sol, dict) and isinstance(sol.get("border_width"), (int, float)):
            sol["border_width"] = float(sol["border_width"]) * s_avg
        if isinstance(ov.get("feather"), (int, float)):
            ov["feather"] = float(ov["feather"]) * s_avg
        xf = ov.get("xform")
        if isinstance(xf, dict):
            if isinstance(xf.get("x"), (int, float)):
                xf["x"] = float(xf["x"]) * sx
            if isinstance(xf.get("y"), (int, float)):
                xf["y"] = float(xf["y"]) * sy

    # ── 子元素（image overlay 的 elements）──
    elements = ov.get("elements")
    if isinstance(elements, list):
        for el in elements:
            if not isinstance(el, dict):
                continue
            for k in ("x", "w"):
                if k in el:
                    el[k] = float(el[k]) * sx
            for k in ("y", "h"):
                if k in el:
                    el[k] = float(el[k]) * sy
            for k in ("fontsize", "stroke_width", "border_width"):
                if k in el and isinstance(el[k], (int, float)):
                    el[k] = round(float(el[k]) * s_avg)

    # ── text_layout padding ──
    tl_layout = ov.get("text_layout")
    if isinstance(tl_layout, dict):
        for k in ("left_pad", "right_pad"):
            if k in tl_layout:
                tl_layout[k] = round(float(tl_layout[k]) * sx)
        for k in ("top_pad", "bottom_pad"):
            if k in tl_layout:
                tl_layout[k] = round(float(tl_layout[k]) * sy)

def insert_intro_media(task, media_path, duration):
    if not isinstance(task, dict) or not media_path:
        return False
    video_path = task.get("video", "")
    if not video_path:
        return False
    if not Path(media_path).stem.startswith(Path(video_path).stem):
        return False

    ext = Path(media_path).suffix.lower()
    if ext in IMG_EXT:
        shift_task_timeline(task, duration)
        w, h = task.get("dims", (1920, 1080))
        auto_text = ""
        segments = task.get("segments", [])
        if segments:
            auto_text = " ".join(seg.get("text", "") for seg in segments)
        content = extract_first_two_sentences(auto_text)
        shape_w = 1600
        shape_h = 300
        shape_x = (w - shape_w) / 2
        shape_y = h * 0.65
        text_x = shape_x
        text_y = shape_y
        text_w = shape_w
        text_h = shape_h
        elements = [
            {
                "id": uuid.uuid4().hex,
                "kind": "shape",
                "shape": "round_rect",
                "x": shape_x,
                "y": shape_y,
                "w": shape_w,
                "h": shape_h,
                "fill_color": "#000000",
                "opacity": 160,
                "border_enabled": False,
                "z": 1
            },
            {
                "id": uuid.uuid4().hex,
                "kind": "text",
                "content": content,
                "x": text_x,
                "y": text_y,
                "w": text_w,
                "h": text_h,
                "font_family": "Arimo",
                "fontsize": 80,
                "bold": True,
                "color": "#FFFFFF",
                "opacity": 255,
                "z": 2
            }
        ]
        task.setdefault("image_overlays", [])
        task["image_overlays"] = [ov for ov in task["image_overlays"] if not ov.get("is_intro")]
        task["image_overlays"].insert(0, {
            "type": "image",
            "content": str(media_path),
            "start": 0.0,
            "end": float(duration),
            "x": 0,
            "y": 0,
            "w": w,
            "h": h,
            "scale": 1.11,
            "opacity": 255,
            "is_intro": True,
            "lock_aspect": True,
            "keep_aspect": True,
            "aspect_ratio": (w / h) if h else 1.0,
            "elements": elements
        })
        task["intro_media"] = {"type": "image", "path": str(media_path), "duration": float(duration)}
        task["video_time_offset"] = float(duration)
        remap_bgm_to_full_duration(task)
        return True

    if ext in VID_EXT:
        shift_task_timeline(task, duration)
        task["intro_media"] = {"type": "video", "path": str(media_path), "duration": float(duration)}
        task["video_time_offset"] = float(duration)
        return True

    return False

def load_svg_icon_hidpi(svg_path: str, logical_px: int, dpr: float = None) -> QIcon:
    from cutora.ui.icons import icon as lucide_icon
    return lucide_icon(svg_path, size=logical_px)

def load_svg_icon(svg_path: str, size: int = 20) -> QIcon:
    from cutora.ui.icons import icon as lucide_icon
    return lucide_icon(svg_path, size=size)

# Cyclic imports deferred until helpers (_tl_of, IMG_EXT, ...) exist.
# Leaf modules bind those names at import time (appendix C pit 1).
from cutora.ui.canvas import (
    MultiGridTextEdit,
    VideoCanvas,
)
from cutora.render.render_worker import RenderWorker, PreviewFxWorker
from cutora.ui.timeline.widget import TimelineWidget
from cutora.ui.timeline.container import TimelineContainer
from cutora.render.render_worker import PreviewFxWorker, RenderWorker
from cutora.ui.canvas import MultiGridTextEdit, VideoCanvas
from cutora.ui.timeline.container import TimelineContainer
from cutora.ui.timeline.widget import TimelineWidget

class _DockEdgeHandle(QWidget):
    """6px title-bar strip. Three dots hint that the dock can float; dragging resizes the dock vertically."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dockHandle")
        self.setFixedHeight(6)
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.SplitVCursor)
        self.setMouseTracking(True)
        self._hover = False
        self._dragging = False
        self._drag_start_gy = 0
        self._drag_start_h = 0

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            dock = self.parentWidget()
            if isinstance(dock, QDockWidget) and not dock.isFloating():
                self._dragging = True
                self._drag_start_gy = int(event.globalPosition().y())
                self._drag_start_h = int(dock.height())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            dock = self.parentWidget()
            win = dock.parentWidget() if isinstance(dock, QDockWidget) else None
            if isinstance(dock, QDockWidget) and isinstance(win, QMainWindow):
                dy = int(event.globalPosition().y()) - self._drag_start_gy
                area = win.dockWidgetArea(dock)
                if area == Qt.DockWidgetArea.BottomDockWidgetArea:
                    new_h = max(64, min(int(win.height() * 0.85), self._drag_start_h - dy))
                else:
                    new_h = max(64, min(int(win.height() * 0.85), self._drag_start_h + dy))
                try:
                    win.resizeDocks([dock], [new_h], Qt.Orientation.Vertical)
                except Exception:
                    pass
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        from cutora.ui.theme.tokens import current
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(current().fg2)
        c.setAlphaF(1.0 if (self._hover or self._dragging) else 0.35)
        p.setBrush(c)
        cy = self.height() / 2.0
        cx = self.width() / 2.0
        for dx in (-6.0, 0.0, 6.0):
            p.drawEllipse(QRectF(cx + dx - 1.2, cy - 1.2, 2.4, 2.4))



_SOURCE_WH_CACHE: dict = {}


def _source_wh_key(path):
    try:
        st = os.stat(str(path))
        return (os.path.abspath(str(path)), int(st.st_size), int(st.st_mtime))
    except (OSError, TypeError, ValueError):
        return None

class MainWindow(CommandBodies, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(format_window_title())
        from cutora.ui.splash import load_app_icon
        _icon = load_app_icon()
        if not _icon.isNull():
            self.setWindowIcon(_icon)
        self.resize(1700, 1000)
        self.setAcceptDrops(True)  # Q115：素材丢窗口任何位置都能导入
        self.reload_style()
        
        self.tasks = []
        self.curr_idx = -1
        self.history_mgr = HistoryManager(max_steps=80)
        from cutora.core.commands import get_bus
        self.bus = get_bus()
        self.bus.bind_window(self)
        self.bus.on_executed(self._on_command_executed)
        self._autosave_timer = QTimer(self)
        # Q159：定时自动保存也走后台写盘（不卡界面）；关窗 / 退出才同步 force
        self._autosave_timer.timeout.connect(lambda: self._autosave_now(force=False, throttle=False))
        self._restart_autosave_timer()
        # Q154：上一次没保存过的工程留下的快照 → 启动后问一句要不要恢复
        QTimer.singleShot(1200, lambda: self._offer_untitled_restore() if hasattr(self, "_offer_untitled_restore") else None)
        try:
            from cutora.api.bridge import install_bridge
            from cutora.api.events import HUB
            install_bridge(self)
            self.bus.on_executed(lambda ev: HUB.publish("command", ev))
        except Exception:
            pass
        self.is_batch_running = False
        self.is_batch_exporting = False
        self.batch_queue = []
        self._selected_sticker = None
        self._selected_image_overlay = None
        self._syncing_sticker_module = False
        self.global_intro_duration = 1.5
        # 默认关闭时间轴后处理，保证 forced alignment 精确性
        self.enable_time_postprocess = False
        self._project_dirty = False

        # === Batch export scheduling (must be stable/serial) ===
        # 用 run_id 防止旧的 singleShot/timeout 在新一轮批导出中误触发
        self._batch_export_run_id = 0
        self._batch_export_total = 0
        self._batch_export_current = 0
        self._batch_export_next_timer = QTimer(self)
        self._batch_export_next_timer.setSingleShot(True)
        self._batch_export_next_timer.timeout.connect(self.process_next_export)
        
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.video_canvas = VideoCanvas()
        self.video_canvas.sub_double_clicked.connect(self.edit_subtitle_text)
        self.video_canvas.fullscreen_toggle_requested.connect(self.toggle_preview_fullscreen)
        # T28: audio-only clock on the mixed proxy wav. Video comes from frame_at.
        from cutora.preview.engine import PreviewClock, PreviewEngine
        from cutora.preview.worker import PreviewWorker
        self._preview = PreviewEngine()
        self._preview_clock = PreviewClock()
        self._preview_worker = PreviewWorker(self)
        self._preview_worker.frame_ready.connect(self._on_preview_worker_frame)
        self._preview_worker.start()
        self._proxy_wav = ""
        self._proxy_ready = False
        self._proxy_job = None
        self._present_err_ts = 0.0
        self._proxy_timer = QTimer(self)
        self._proxy_timer.setSingleShot(True)
        self._proxy_timer.setInterval(400)
        self._proxy_timer.timeout.connect(lambda: self._rebuild_preview_wav(immediate=True))
        self._stem_mixer = None
        self._stem_sink = None
        self._stem_sink_dev_id = None
        self._stem_pull_io = None
        self._stem_sink_running = False
        self._batch_audition = False
        self._audition_wall0 = None
        self._audition_origin_ms = 0.0
        try:
            from cutora.audio.stem_cache import get_stem_mixer, make_pull_device
            self._stem_mixer = get_stem_mixer()
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
            self._stem_audio_fmt = fmt
            dev = QMediaDevices.defaultAudioOutput()
            if dev is not None and not dev.isNull():
                self._stem_sink = QAudioSink(dev, fmt, self)
                self._stem_sink_dev_id = dev.id()
            else:
                self._stem_sink = QAudioSink(fmt, self)
            try:
                self._stem_sink.setBufferSize(48000 * 2 * 4 // 5)
            except Exception:
                pass
            self._stem_pull_io = make_pull_device(self, self)
        except Exception:
            from cutora.core.log import get_logger as _stem_log
            _stem_log("cutora.audio").exception("[Audio] QAudioSink init failed")
        self._stem_rebuild_timer = QTimer(self)
        self._stem_rebuild_timer.setSingleShot(True)
        self._stem_rebuild_timer.setInterval(400)
        self._stem_rebuild_timer.timeout.connect(
            lambda: self._rebuild_stems_now())
        from cutora.audio.stem_cache import StemRebuildGate
        self._stem_gate = StemRebuildGate()
        self._fader_commit_timer = QTimer(self)
        self._fader_commit_timer.setSingleShot(True)
        self._fader_commit_timer.setInterval(400)
        self._fader_commit_timer.timeout.connect(self._commit_role_faders)
        
        self.intro_player = QMediaPlayer()
        self.intro_audio_output = QAudioOutput()
        self.intro_player.setAudioOutput(self.intro_audio_output)
        self.intro_audio_output.setVolume(1.0)
        self.bgm_player = QMediaPlayer()
        self.bgm_audio_output = QAudioOutput()
        self.bgm_player.setAudioOutput(self.bgm_audio_output)
        self.bgm_audio_output.setVolume(0.0)

        # ── 音频输出设备热插拔监听 (0829) ──
        # 详见下面 _rebind_audio_outputs 的注释。QMediaDevices 必须存成属性:
        # 存成局部变量会被 GC 掉, 信号跟着断, 而且不报任何错。
        self._audio_dev_watcher = QMediaDevices(self)
        self._audio_dev_timer = QTimer(self)
        self._audio_dev_timer.setSingleShot(True)
        self._audio_dev_timer.timeout.connect(self._rebind_audio_outputs)
        try:
            self._audio_dev_watcher.audioOutputsChanged.connect(
                self._on_audio_devices_changed)
        except Exception:
            pass
        
        self.video_canvas.item_selected.connect(self.on_canvas_select)
        self.video_canvas.item_dragged.connect(self.on_canvas_drag)
        self.video_canvas.gesture_began.connect(self._on_canvas_gesture_began)
        self.video_canvas.gesture_ended.connect(self._on_canvas_gesture_ended)
        self.video_canvas.structure_changed.connect(self._on_canvas_structure_changed)
        self.video_canvas.gesture_cancelled.connect(self._on_canvas_gesture_cancelled)
        self.video_canvas.item_edit_request.connect(self.on_canvas_edit_request)
        self.video_canvas.sub_text_selected.connect(self.on_sub_text_selected)
        self.video_canvas.sub_textbox_resized.connect(self.on_sub_textbox_resized)
        self.media_player.positionChanged.connect(self._sync_bgm_to_video)
        self.media_player.playbackStateChanged.connect(self._sync_bgm_state)
        # 音效实时预览 (0829)。详见 _sync_sfx_preview 上方注释。
        self._sfx_preview_on = True
        self._sfx_players = []
        self._sfx_slots = {}
        # 成品效果预览(代理音轨)。详见 _sync_preview_fx 上方注释。
        self._preview_fx_on = True
        self._preview_fx_key = ""        # 当前代理音轨对应的参数指纹
        self._preview_fx_path = ""       # 渲染好的文件; 空 = 还没有
        self._preview_fx_worker = None
        self._preview_fx_player = None
        self._preview_fx_out = None
        self._preview_fx_timer = QTimer(self)
        self._preview_fx_timer.setSingleShot(True)
        self._preview_fx_timer.timeout.connect(self._preview_fx_build)
        self.media_player.positionChanged.connect(self._sync_sfx_preview)
        self.media_player.playbackStateChanged.connect(self._sync_sfx_preview)
        self.media_player.playbackStateChanged.connect(self._sync_preview_fx)
        
        self.timer = QTimer()
        self.timer.setInterval(200)
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
        self.timer.timeout.connect(self.sync_ui)
        try:                                   # Q189h：空闲停帧被用户输入 / 编辑叫醒
            from cutora.ui.idle_wake import install as _install_idle_wake
            _install_idle_wake(self)
        except Exception:
            pass
        self._timeline_ms = 0
        self._timeline_last_ts = None
        self._timeline_playing = False
        self._using_media_clock = False
        self._seek_pending_target = None
        self._seek_pending_timeline = 0
        self._seek_pending_since = 0.0
        
        self._warned_render_no_stop = False
        self.is_exporting = False
        self.batch_export_running = False
        self.project_path = None
        self.project_glossary = []
        self._timeline_template = None
        self._current_fit_mode = DEFAULT_VIDEO_FIT_MODE  # 当前适配模式（fill/fit/stretch）
        self._last_adv_text_selection = None
        self._timeline_h_user_set = False
        self._applying_timeline_h = False
        self.init_ui()
        self.setWindowTitle(format_window_title(self.project_path))
        self.shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.shortcut_space.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_space.activated.connect(self.toggle_play_pause)
        from cutora.ui.shortcuts import install_shortcuts
        install_shortcuts(self)
        self._reload_default_subtitle_style()
        self.media_player.sourceChanged.connect(self.update_ui_state)
        self.media_player.durationChanged.connect(self.on_duration_changed)
        self.update_ui_state()

        # [v6.2 CLI] 占单实例锁。若命令行正在跑，只警告、不挡界面。
        # Q131：以前这里直接弹模态框 —— 构造主窗口时闪屏还开着「置顶」，框被压在
        # 闪屏底下看不见也点不到，模态框又把事件循环堵住，于是永远卡在
        # 「加载界面…」。现在只记下来，等 run_gui_startup 关掉闪屏、主窗口
        # 显示出来之后再由 show_pending_startup_warnings() 弹。
        self._cli_lock_owned = False
        self._pending_cli_lock_warning = ""
        try:
            from cutora.batch import cli as batch_cli
            owned, warn = batch_cli.gui_try_acquire()
            self._cli_lock_owned = bool(owned)
            if warn:
                self._pending_cli_lock_warning = str(warn)
                print("[CLI] " + str(warn))
        except Exception as _lk_err:
            print("[CLI] 图形界面加锁失败（忽略）: %s" % _lk_err)

        self._badge_transition = False
        self._console = None
        from cutora.ui.floating_badge import FloatingBadge
        self._badge = FloatingBadge()
        self._badge.restore_requested.connect(self._restore_from_badge)
        self._badge.quit_requested.connect(self.close)

    def show_pending_startup_warnings(self):
        """Q131：启动期间攒下的提示，主窗口显示出来之后再弹（见 run_gui_startup）。"""
        warn = getattr(self, "_pending_cli_lock_warning", "") or ""
        if not warn:
            return
        self._pending_cli_lock_warning = ""
        try:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("另一个片语正在运行")
            box.setText("无头进程占用中，同时改同一工程可能互相覆盖。")
            box.setInformativeText(warn)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setWindowModality(Qt.WindowModality.WindowModal)
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            box.open()          # 非阻塞：不堵事件循环，主窗口照样能画
            self._cli_lock_box = box
        except Exception:
            print("[CLI] " + warn)

    def _release_idle_video_handles(self):
        """当窗口切到后台或最小化且未在播放/导出时，立即释放所有缓存的 VideoCapture 文件句柄，
        确保用户在外部（如右键表格工具或资源管理器）对视频执行重命名/移动时绝不触发 WinError 32 文件占用。"""
        if bool(getattr(self, "_timeline_playing", False)):
            return
        if bool(getattr(self, "_is_exporting", False)):
            return
        try:
            canvas = getattr(self, "video_canvas", None)
            if canvas is not None and hasattr(canvas, "_stop_all_decoders"):
                canvas._stop_all_decoders()
        except Exception:
            pass
        try:
            from cutora.preview.engine import release_all_pools
            release_all_pools()
        except Exception:
            pass
        try:
            from cutora.render.frame_renderer import cleanup_dbi_video_caps
            cleanup_dbi_video_caps()
        except Exception:
            pass

    def changeEvent(self, event):
        super().changeEvent(event)
        try:
            et = event.type()
            if et in (QEvent.Type.ActivationChange, QEvent.Type.WindowDeactivate):
                if not self.isActiveWindow():
                    self._release_idle_video_handles()
        except Exception:
            pass
        if event.type() != QEvent.Type.WindowStateChange:
            return
        if self.isMinimized():
            self._release_idle_video_handles()
        if getattr(self, "_badge_transition", False):
            return
        if not self.isMinimized():
            return
        from cutora.ui.badge_prefs import badge_enabled
        if not badge_enabled():
            return
        if getattr(self, "_badge", None) is None:
            return
        QTimer.singleShot(0, self._melt_to_badge)

    def _melt_to_badge(self):
        if getattr(self, "_badge_transition", False):
            return
        badge = getattr(self, "_badge", None)
        if badge is None:
            return
        from cutora.ui.badge_prefs import badge_enabled
        if not badge_enabled() or not self.isMinimized():
            return
        self._badge_transition = True
        try:
            badge.present()
            self.hide()
        finally:
            self._badge_transition = False

    def _restore_from_badge(self):
        self._badge_transition = True
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            badge = getattr(self, "_badge", None)
            if badge is not None:
                badge.hide()
        finally:
            self._badge_transition = False

    def apply_badge_pref(self):
        """Master switch changed. Off hides a badge that is already up."""
        from cutora.ui.badge_prefs import badge_enabled
        badge = getattr(self, "_badge", None)
        if badge_enabled() or badge is None:
            return
        badge.hide()
        if self.isHidden():
            self._badge_transition = True
            try:
                self.showMinimized()
            finally:
                self._badge_transition = False

    def apply_badge_edge_pref(self):
        badge = getattr(self, "_badge", None)
        if badge is not None:
            badge.on_edge_setting_changed()

    def reset_floating_badge(self):
        badge = getattr(self, "_badge", None)
        if badge is None:
            from cutora.ui.badge_prefs import clear_badge_position
            clear_badge_position()
            return
        badge.reset_position()

    def open_console(self):
        from cutora.ui.panels.console_view import ConsoleView
        if getattr(self, "_console", None) is None:
            self._console = ConsoleView(self)
        from cutora.ui.inwindow import show_sheet      # Q192：主窗口里的侧滑面板，不挡旁边
        show_sheet(self._console, self)

    def open_log_privacy(self):
        from cutora.core.log import privacy_note
        from cutora.ui.i18n import tr
        QMessageBox.information(self, tr("关于日志与隐私"), privacy_note())

    # ------------------------------------------------------------------
    # Q115：整个窗口都能接素材 —— 丢在时间线/轨道头以外的地方就进媒体库
    # ------------------------------------------------------------------
    def _drop_paths_from(self, mime):
        from cutora.ui.timeline_drop import supported_exts
        if not mime.hasUrls():
            return []
        ok = supported_exts() | {".cutora", ".asproj", ".json"}
        out = []
        for url in mime.urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path) or os.path.splitext(path)[1].lower() in ok:
                out.append(path)
        return out

    def dragEnterEvent(self, event):
        if self._drop_paths_from(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drop_paths_from(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._drop_paths_from(event.mimeData())
        if not paths:
            event.ignore()
            return
        from cutora.ui.timeline_drop import sort_for_drop
        self.on_drop(sort_for_drop(paths))
        event.acceptProposedAction()

    def closeEvent(self, event):
        badge = getattr(self, "_badge", None)
        if badge is not None:
            try:
                badge.hide()
            except Exception:
                pass
        offscreen = (os.environ.get("QT_QPA_PLATFORM") or "").lower() == "offscreen"
        if (self.is_dirty()
                and not getattr(self, "_suppress_close_prompt", False)
                and not offscreen):
            r = QMessageBox.question(
                self, "未保存的修改",
                "工程有未保存的修改，是否保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if r == QMessageBox.StandardButton.Save:
                self.save_project()
                if self.is_dirty():
                    event.ignore()
                    return
            elif r == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        try:
            if getattr(self, "_queue_mode", False) and not getattr(self, "_queue_relaunching", False):
                from cutora.batch import cli as batch_cli
                batch_cli.pause_queue("window_closed")
        except Exception:
            pass
        try:
            from cutora.batch import cli as batch_cli
            batch_cli.release_lock_if_owner()
        except Exception:
            pass
        # 代理音轨还在渲就等它一下, 免得 QThread 带着活线程被销毁(Qt 会告警)
        try:
            self._stop_stem_sink()
            mixer = getattr(self, "_stem_mixer", None)
            if mixer is not None:
                mixer.clear()
        except Exception:
            pass
        try:
            w = getattr(self, "_preview_fx_worker", None)
            if w is not None and w.isRunning():
                w.wait(3000)
        except Exception:
            pass
        try:
            pw = getattr(self, "_preview_worker", None)
            if pw is not None and hasattr(pw, "shutdown"):
                pw.shutdown(2000)
        except Exception:
            pass
        # BUG-39: 导出线程同理 —— 先叫停，再等它把 VideoCapture 释放完，
        # 带着活线程退出就是 0xC0000409。
        for _attr in ("render", "worker_ai"):
            try:
                r = getattr(self, _attr, None)
                if r is not None and hasattr(r, "isRunning") and r.isRunning():
                    try:
                        r.stop()
                    except Exception:
                        pass
                    r.wait(15000)
            except Exception:
                pass
        try:                                   # Q191：在房间里关软件 → 先回剪辑页（面板布局按剪辑页存）
            rooms = getattr(self, "room_host", None)
            if rooms is not None:
                rooms.shutdown()
        except Exception:
            pass
        try:
            self._save_dock_state()
        except Exception:
            pass
        try:
            from cutora.asr.clip_worker import close_all_asr_clients
            close_all_asr_clients()
        except Exception:
            pass
        try:
            from cutora.core.autosave import clear_pointer, clear_snapshot, untitled_project_path
            clear_snapshot(getattr(self, "project_path", None))
            clear_snapshot(untitled_project_path())
            clear_pointer()   # 干净退出就别留指针，免得下次开机被当成崩溃
        except Exception:
            pass
        super().closeEvent(event)

    def _reload_default_subtitle_style(self):
        """带 default 标记的风格包写进 self.default_style。没有标记就保持现状。"""
        from cutora.subtitle.stylepack import default_pack_style
        loaded_style = default_pack_style()
        if loaded_style:
            self.default_style = force_allowed_font(copy.deepcopy(loaded_style))

    def init_ui(self):
        self._build_shell_menus()

        # [v6.1] 初始勾选状态改为读 config.DEVICE（即 detect_gpu_config 的结论），
        # 而不是裸的 torch.cuda.is_available() —— 后者在架构不受支持时仍返回 True，
        # 会让勾选框显示「已启用」但实际跑在 CPU 上。
        # GPU / 音量控件仍作状态源，但不再上工具栏（设置 → 性能 / 角色条推子）。
        self.chk_gpu = QCheckBox("GPU加速", self)
        self.chk_gpu.setObjectName("TopMiniChk")
        _saved_gpu = _load_gpu_enabled()
        _want_gpu = DEVICE == "cuda" if _saved_gpu is None else bool(_saved_gpu)
        self.chk_gpu.setChecked(_want_gpu)
        self.chk_gpu.hide()
        self.chk_gpu.toggled.connect(self.toggle_gpu)
        if _saved_gpu is False:
            from cutora.core import config as _cfg
            _cfg.DEVICE = "cpu"
            _cfg.COMPUTE_TYPE = "float32"
            _sync_device_globals()
        if self.chk_gpu.isChecked() and not os.environ.get("CUTORA_SKIP_ENCODER_PROBE"):
            def _boot_encoder_probe():
                try:
                    from cutora.render.encoder_probe import probe as _probe
                    _probe(force=False)
                except Exception:
                    pass
            QTimer.singleShot(800, _boot_encoder_probe)

        self.spin_src_vol = QDoubleSpinBox(self)
        self.spin_src_vol.setRange(0.0, 5.0)
        self.spin_src_vol.setValue(1.0)
        self.spin_src_vol.setSingleStep(0.01)
        self.spin_src_vol.setObjectName("TopVolSpin")
        self._fit_volume_spin(self.spin_src_vol)
        self.spin_src_vol.valueChanged.connect(self._on_src_vol_changed)
        self.spin_src_vol.hide()

        self.spin_bgm_vol = QDoubleSpinBox(self)
        self.spin_bgm_vol.setRange(0.0, 5.0)
        self.spin_bgm_vol.setValue(0.1)
        self.spin_bgm_vol.setSingleStep(0.01)
        self.spin_bgm_vol.setObjectName("TopVolSpin")
        self._fit_volume_spin(self.spin_bgm_vol)
        self.spin_bgm_vol.valueChanged.connect(self._on_bgm_vol_changed)
        self.spin_bgm_vol.hide()

        self.spin_sfx_vol = QDoubleSpinBox(self)
        _sfx_vol0 = 0.18
        try:
            if _SFX is not None:
                _sfx_vol0 = float(_SFX.load_fx_settings().get("sfx_volume", 0.18))
        except Exception:
            _sfx_vol0 = 0.18
        self.spin_sfx_vol.setRange(0.0, 5.0)
        self.spin_sfx_vol.setValue(_sfx_vol0)
        self.spin_sfx_vol.setSingleStep(0.01)
        self.spin_sfx_vol.setObjectName("TopVolSpin")
        self._fit_volume_spin(self.spin_sfx_vol)
        self.spin_sfx_vol.setToolTip("群众鼓掌 / Amen / 现场同期声的音量，建议 0.10–0.25，避免盖过人声")
        self.spin_sfx_vol.valueChanged.connect(self._on_sfx_vol_changed)
        self.spin_sfx_vol.hide()

        mid = QSplitter(Qt.Orientation.Horizontal)
    
        bin_w = QWidget(); bl = QVBoxLayout(bin_w)
        bin_top = QHBoxLayout(); bin_top.addStretch()
        self.btn_delete_task = QPushButton("删除"); self.btn_delete_task.clicked.connect(self.delete_selected_task); self.btn_delete_task.hide()
        bl.addLayout(bin_top)
        self.file_list = DraggableListWidget(); self.file_list.files_dropped.connect(self.on_drop); self.file_list.itemClicked.connect(self.on_click); self.file_list.order_changed.connect(self.on_task_order_changed); self.file_list.itemChanged.connect(self.update_batch_export_btn_text)
        self.file_list.itemChanged.connect(self.update_ui_state)
        self.file_list.itemSelectionChanged.connect(self.on_selection_changed)
        self.file_list.keyPressEvent = self.file_list_keypress
        bl.addWidget(self.file_list); mid.addWidget(bin_w)
    
        play_w = QWidget(); pl = QVBoxLayout(play_w); pl.setContentsMargins(0,0,0,0); pl.setSpacing(0)
        self._play_layout = pl
        self.video_wrap = QWidget(); self.video_wrap.setObjectName("previewWrap"); pl.addWidget(self.video_wrap, 1)
        vl = QVBoxLayout(self.video_wrap); vl.setContentsMargins(0,0,0,0); vl.setSpacing(0); vl.addWidget(self.video_canvas)
        self.video_canvas.setObjectName("VideoCanvas")
        mid.addWidget(play_w)
    
        insp_w = QWidget(); il = QVBoxLayout(insp_w)
        insp_w.setMinimumWidth(0)
        insp_w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tabs = QTabWidget()
        tab_txt = QWidget(); tl = QVBoxLayout(tab_txt)
        tl.setContentsMargins(0, 0, 0, 0)

        from cutora.ui.i18n import tr as _tr_tx
        self.transcript_inner = QTabWidget()
        self.transcript_inner.setMinimumWidth(160)
        self.transcript_panel = TranscriptPanel(self)
        self.transcript_inner.addTab(self.transcript_panel, _tr_tx("句子"))
        script_w = QWidget()
        script_l = QVBoxLayout(script_w)
        script_l.setContentsMargins(6, 6, 6, 6)
    
        # --- 批量粘贴网格 (Batch Paste Grid) ---
        self.stacked_text_area = QStackedWidget()
        script_l.addWidget(self.stacked_text_area, 10)
        self.lbl_approx_words = QLabel("")
        self.lbl_approx_words.setObjectName("hint")
        self.lbl_approx_words.hide()
        script_l.addWidget(self.lbl_approx_words)
        self.transcript_inner.addTab(script_w, _tr_tx("文案"))
        tl.addWidget(self.transcript_inner, 10)
    
        self.text_input = QTextEdit(); self.text_input.setObjectName("text_input"); self.text_input.setPlaceholderText("原文...")
        self.text_input.textChanged.connect(self.save_text)
        self.stacked_text_area.addWidget(self.text_input) # Index 0
    
        self.multi_text_scroll = QScrollArea()
        self.multi_text_scroll.setWidgetResizable(True)
        self.multi_text_scroll.setObjectName("multiTextScroll")
        self.multi_text_container = QWidget()
        self.multi_text_container.setObjectName("multiTextContainer")
        self.multi_text_layout = QVBoxLayout(self.multi_text_container)
        self.multi_text_layout.setContentsMargins(0, 0, 8, 0)
        self.multi_text_layout.setSpacing(10)
        self.multi_text_layout.addStretch(1)
        self.multi_text_scroll.setWidget(self.multi_text_container)
        self.stacked_text_area.addWidget(self.multi_text_scroll) # Index 1
        self._grid_widgets_pool = []
        # ----------------------------------------

        from cutora.ui.icons import lucide_path
        for f in ("btn_match.svg", "btn_export.svg", "btn_stop.svg",
                  "tool_text.svg", "tool_image.svg", "tool_audio.svg",
                  "Track_Sharing.svg", "ShareT.svg", "Batch_Add_Music.svg",
                  "play", "pause", "skip-back", "skip-forward"):
            assert os.path.isfile(lucide_path(f)), f"Missing Lucide icon for: {f}"

        from cutora.ui.i18n import tr as _tr_btn
        from cutora.ui.theme.fonts_ui import mono_qfont
        self.preview_tools = QWidget()
        self.preview_tools.setObjectName("previewTools")
        self.preview_tools.setFixedHeight(40)
        top_btn_row = QHBoxLayout(self.preview_tools)
        top_btn_row.setContentsMargins(8, 4, 8, 4)
        top_btn_row.setSpacing(4)

        self.btn_ai = QToolButton(); self.btn_ai.setObjectName("btn_ai"); self.btn_ai.clicked.connect(self._ui_auto_subtitle)
        self.btn_exp = QToolButton(); self.btn_exp.setObjectName("btn_exp"); self.btn_exp.clicked.connect(self.export_video)
        self.btn_exp_stop = QToolButton(); self.btn_exp_stop.setObjectName("btn_exp_stop"); self.btn_exp_stop.clicked.connect(self.stop_export)
        left_tools = QHBoxLayout()
        left_tools.setContentsMargins(0, 0, 0, 0)
        left_tools.setSpacing(4)
        left_tools.addWidget(self.btn_ai)
        left_tools.addWidget(self.btn_exp)
        left_tools.addWidget(self.btn_exp_stop)
        top_btn_row.addLayout(left_tools)
        top_btn_row.addStretch(1)

        mid_play = QHBoxLayout()
        mid_play.setContentsMargins(0, 0, 0, 0)
        mid_play.setSpacing(4)
        self.btn_skip_start = QToolButton()
        self.btn_skip_start.setObjectName("btn_skip_start")
        self.btn_skip_start.clicked.connect(self.shortcut_goto_start)
        self.btn_start = QToolButton()
        self.btn_start.setObjectName("btn_play")
        self.btn_start.clicked.connect(self.toggle_play)
        self.btn_skip_end = QToolButton()
        self.btn_skip_end.setObjectName("btn_skip_end")
        self.btn_skip_end.clicked.connect(self.shortcut_goto_end)
        self.lbl_timecode = QLabel("00:00:00.00 / 00:00.00")
        self.lbl_timecode.setObjectName("transportTimecode")
        self.lbl_timecode.setFont(mono_qfont(12))
        self.lbl_timecode.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        fm = self.lbl_timecode.fontMetrics()
        self.lbl_timecode.setMinimumWidth(
            fm.horizontalAdvance("00:00:00.00 / 00:00:00.00") + 8)
        mid_play.addWidget(self.btn_skip_start)
        mid_play.addWidget(self.btn_start)
        mid_play.addWidget(self.btn_skip_end)
        mid_play.addWidget(self.lbl_timecode)
        top_btn_row.addLayout(mid_play)
        top_btn_row.addStretch(1)

        right_tools = QHBoxLayout()
        right_tools.setContentsMargins(0, 0, 0, 0)
        right_tools.setSpacing(4)
        self.btn_split_guides = QToolButton()
        self.btn_split_guides.setObjectName("btn_split_guides")
        self.btn_split_guides.setCheckable(True)
        _sg = _load_app_settings() or {}
        _guides_on = True if "preview.split_guides" not in _sg else bool(_sg.get("preview.split_guides"))
        self.btn_split_guides.setChecked(_guides_on)
        self.btn_split_guides.toggled.connect(self._on_split_guides_toggled)
        if getattr(self, "video_canvas", None) is not None:
            self.video_canvas._show_split_guides = _guides_on
        self.btnAddText = QToolButton(); self.btnAddText.setObjectName("btnAddText"); self.btnAddText.clicked.connect(self.add_text_ov)
        self.btnAddImage = QToolButton(); self.btnAddImage.setObjectName("btnAddImage"); self.btnAddImage.clicked.connect(self.add_img_ov)
        right_tools.addWidget(self.btn_split_guides)
        right_tools.addWidget(self.btnAddText)
        right_tools.addWidget(self.btnAddImage)
        from cutora.ui.audio_mixer_bar import AudioMixerBar
        self.mixer_bar = AudioMixerBar(self.preview_tools)
        self.mixer_bar.fader_moved.connect(self._on_role_fader_moved)
        self.mixer_bar.fader_released.connect(self._on_role_fader_released)
        self.mixer_bar.mute_toggled.connect(self._on_mixer_mute)
        self.mixer_bar.spatial_toggled.connect(self._on_mixer_spatial)
        self.mixer_bar.preset_chosen.connect(self._on_mixer_preset)
        self.mixer_bar.add_bgm_requested.connect(self.add_audio_bgm)
        self.mixer_bar.place_sfx_requested.connect(self._jump_to_batch_audio)
        self.mixer_bar.sfx_preview_toggled.connect(self._on_toggle_sfx_preview)
        self.mixer_bar.set_sfx_preview(getattr(self, "_sfx_preview_on", True))
        right_tools.addWidget(self.mixer_bar)
        self._role_fader_sliders = self.mixer_bar.sliders
        top_btn_row.addLayout(right_tools)

        for b, tip in (
            (self.btn_ai, _tr_btn("匹配字幕")),
            (self.btn_exp, _tr_btn("导出")),
            (self.btn_exp_stop, _tr_btn("停止导出")),
            (self.btn_skip_start, _tr_btn("跳到开头")),
            (self.btn_start, _tr_btn("播放")),
            (self.btn_skip_end, _tr_btn("跳到结尾")),
            (self.btn_split_guides, _tr_btn("分屏参考线")),
            (self.btnAddText, _tr_btn("添加文本")),
            (self.btnAddImage, _tr_btn("添加图片")),
        ):
            b.setText("")
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setAutoRaise(True)
            b.setFixedSize(32, 32)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_exp_stop.hide()

        self._preview_tool_btns = (
            self.btn_ai, self.btn_exp, self.btn_exp_stop,
            self.btn_skip_start, self.btn_start, self.btn_skip_end,
            self.btn_split_guides,
            self.btnAddText, self.btnAddImage,
        )
        for b in self._preview_tool_btns:
            b.installEventFilter(self)
        self.preview_tools.installEventFilter(self)

        self.prog_frame = QFrame()
        prog_layout = QHBoxLayout(self.prog_frame)
        prog_layout.setContentsMargins(6, 2, 6, 2)
        prog_layout.setSpacing(8)
        self.prog_main = QProgressBar(); self.prog_main.setObjectName("progress_main"); self.prog_main.setFixedHeight(6); self.prog_main.setTextVisible(False); prog_layout.addWidget(self.prog_main, 1)
        self.prog_label = QLabel("正在识别… 0%"); self.prog_label.setObjectName("progLabel"); prog_layout.addWidget(self.prog_label, 0)
        from cutora.ui.i18n import tr as _tr_cancel
        self.btn_prog_cancel = QPushButton(_tr_cancel("取消"))
        self.btn_prog_cancel.setObjectName("btn_prog_cancel")
        self.btn_prog_cancel.setFixedHeight(22)
        self.btn_prog_cancel.hide()
        self.btn_prog_cancel.clicked.connect(self._cancel_detect)
        prog_layout.addWidget(self.btn_prog_cancel, 0)
        self.set_progress_mode("sub")

        self._play_layout.addWidget(self.preview_tools, 0)
        self._refresh_timecode()

        self.apply_top_icons()
        self._tab_ops = tab_txt

        self.sub_panel = SubtitleStylePanel()
        self.sub_panel.param_changed.connect(self.update_preview)
        self.sub_panel.font_manage_request.connect(self.open_font_manager)
        self.sub_panel.recalc_textbox_request.connect(self.on_recalc_textbox_global)
        self.sub_panel.relayout_request.connect(self._ui_relayout_subtitles)
        self.sub_panel.wrap_width_committed.connect(self._on_wrap_width_committed)
        from cutora.ui.panels.style_gallery import StyleGalleryPanel
        self.style_gallery = StyleGalleryPanel(self)
        self.tabs.addTab(self.style_gallery, _tr_tx("风格"))
        self.tabs.addTab(self.sub_panel, "字幕样式")
        self.role_panel = RoleMappingPanel()
        self.role_panel.style_changed.connect(self._on_role_style_changed)
        self.role_panel.bind_request.connect(self.apply_role_mapping)
        self.role_panel.smart_match_request.connect(self.smart_match_subtitle_range)
        self.role_panel.roles_updated.connect(self._on_roles_updated)
        self.role_panel.style_panel.font_manage_request.connect(self.open_font_manager)
        self.role_panel.style_panel.recalc_textbox_request.connect(self.on_recalc_textbox_role)
        self.prop_panel = PropertyPanel()
        self.prop_panel.param_changed.connect(self._group_sync_from_inspector)   # Q154：先同步整组，再刷预览
        self.prop_panel.param_changed.connect(self.update_preview)
        self.prop_panel.param_changed.connect(self._on_audio_param_changed)
        self.tabs.addTab(self.prop_panel, "属性")
        # Q126：选中文本就浮在预览上方的工具条。控件全是属性面板的代理，
        # 改哪边另一边立刻跟着变；不在画布里（是 video_wrap 的子控件），
        # 所以画布截图 / 导出不会把它带上。
        from cutora.ui.text_context_bar import TextContextBar
        self.text_bar = TextContextBar(self.prop_panel, self.video_wrap)
        self.prop_panel.data_loaded.connect(self.text_bar.show_for)
        self.prop_panel.param_changed.connect(
            lambda _p=None: self.text_bar.refresh() if self.text_bar.isVisible() else None)
        self.text_bar.more_requested.connect(self._focus_property_panel)
        # Q128：排列 / 样式刷走画布（多选也在画布上）
        self.text_bar.align_requested.connect(self.video_canvas.align_selection)
        self.text_bar.distribute_requested.connect(self.video_canvas.distribute_selection)
        self.text_bar.style_copy_requested.connect(self.shortcut_copy_text_style)
        self.text_bar.style_paste_requested.connect(self.shortcut_paste_text_style)
        self.video_canvas.multi_selection_changed.connect(self.text_bar.set_selection_count)
        self.text_bar.styles_requested.connect(self._open_text_style_gallery)
        self.text_gallery = None
        self.tabs.addTab(self.role_panel, "角色映射")

        self.word_block_panel = WordBlockPanel()
        self.word_block_panel.adv_block_request.connect(self.on_adv_block_request)
        self.word_block_panel.adv_block_edit_request.connect(self.on_adv_block_edit)
        self.word_block_panel.adv_block_delete_request.connect(self.on_adv_block_delete)
        self.word_block_panel.adv_block_modified_request.connect(self._on_adv_block_modified)
        self.tabs.addTab(self.word_block_panel, "字块样式")
        self.prop_panel.element_raise.connect(self.raise_sticker)
        self.prop_panel.element_lower.connect(self.lower_sticker)
        self.prop_panel.element_move_up.connect(self.move_up_sticker)
        self.prop_panel.sync_intro_style.connect(self.sync_intro_style_to_all)
        self.prop_panel.rich_text_edit_request.connect(self.open_rich_text_editor)
        self._bind_sticker_module_signals()  # stickers only; composer UI removed
        il.addWidget(self.tabs)

        self.timeline = TimelineWidget()
        self.timeline_container = TimelineContainer(self.timeline)
        self.timeline.seek_request.connect(self.on_seek_request)
        self.timeline.item_selected.connect(self.on_timeline_select)
        self.timeline.item_dragged.connect(self._on_timeline_item_dragged)
        self.timeline.segment_changed.connect(self.push_undo)
        self.timeline.text_edit_request.connect(self.edit_subtitle_text)
        # Q115：一次拖一批走 files_dropped_on_timeline；老的单文件信号留着兼容，
        # 但只接一个，两个都接会把同一个文件导两遍。
        self.timeline.files_dropped_on_timeline.connect(
            self._handle_timeline_drop_batch)
        self.timeline.import_to_track.connect(self._on_import_to_track)
        self.timeline_container.setMinimumHeight(80)
        from cutora.ui.widgets.error_banner import ErrorBanner
        self._error_banner = ErrorBanner()
        pl.addWidget(self._error_banner)

        self._assemble_shell(bin_w, play_w, insp_w, tab_txt)
        try:                                   # Q191：房间（批量加载 / 中央监视器…）+ 底部页面栏
            from cutora.ui.rooms import install as _install_rooms
            _install_rooms(self)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("rooms install")
        QTimer.singleShot(3000, self._announce_hw_profile)
        try:
            from cutora.ui.widgets.responsive import harden_docks
            harden_docks(self)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("harden_docks")
    
        self.default_style = {
            'pos_x': 960, 'pos_y': 1450, 
            'fontsize': 60, 'font_family': DEFAULT_FONT_FAMILY,
            'bold': True, 'italic': False,
            'color_text': '#000000', 'color_high': '#FF0000', 'color_bg': '#e0c3fc', 'color_shadow': '#000000',
            'opacity_bg': 191, 'opacity_shadow': 0, 'shadow_enabled': False, 'use_box': True, 
            'box_padding_x': 20, 'box_padding_y': 20,
            'box_radius': 20, 'box_blur': 0,
            'use_stroke': False, 'border_width': 2, 'opacity_outline': 255, 'color_outline': '#000000',
            'dynamic_box': False, 'color_high_bg': '#FF0000', 'opacity_high_bg': 180, 'dynamic_radius': 5,
            'shadow_blur': 0, 'rotation': 0, 'use_underline': False, 'color_underline': '#000000',
            'high_padding': 5, 'high_offset_y': 0,
            'wrap_width_percent': 86,
            'wrap_lines': 2,
            'line_spacing': 0,
            'advanced_textbox_enabled': False,
            'advanced_textbox_x': 0,
            'advanced_textbox_y': 0,
            'advanced_textbox_w': 0,
            'advanced_textbox_h': 0,
            'advanced_textbox_align': 'center',
            'adv_bg_enabled': False,
            'adv_bg_offset_x': 0,
            'adv_bg_offset_y': 0,
            'adv_bg_w': 1080,
            'adv_bg_h': 280,
            'adv_bg_radius': 20,
            'adv_bg_color': '#e0c3fc',
            'adv_bg_opacity': 191,
            'adv_bg_gradient_enabled': False,
            'adv_bg_gradient_type': 'linear_h',
            'adv_bg_gradient_colors': '#e0c3fc,#8ec5fc',
            'adv_bg_gradient_highlight': False,
            'adv_line_spacing': 0,
            'adv_text_align': 'center',
            'adv_stroke_enabled': False,
            'adv_stroke_width': 2,
            'adv_stroke_color': '#000000',
            'adv_stroke_opacity': 255,
            # ── Animation defaults ──
            'anim_in_type': 'none',
            'anim_in_duration': 0.3,
            'anim_in_easing': 'ease_out',
            'anim_out_type': 'none',
            'anim_out_duration': 0.3,
            'anim_out_easing': 'ease_out',
            # Typewriter
            'tw_revealed_color': '#000000',
            'tw_revealed_stroke_color': '#000000',
            'tw_revealed_opacity': 255,
            'tw_revealed_stroke_opacity': 255,
            'tw_unrevealed_color': '#808080',
            'tw_unrevealed_stroke_color': '#404040',
            'tw_unrevealed_opacity': 80,
            # Character bounce
            'char_bounce_height': 20,
            'char_bounce_stagger': 0.05,
            # Dynamic box animation
            'dyn_box_anim': False,
            'dyn_box_anim_overshoot': 1.3,
            'dyn_box_anim_duration': 0.15,
            # ── Phase 2 defaults ──
            'floating_amplitude': 8,
            'floating_period': 2.0,
            'bullet_stagger': 0.15,
            'metronome_bpm': 120,
            'flash_color': '#FFFF00',
            'flash_duration': 0.1,
            'holy_glow_radius': 6,
            'holy_glow_color': '#FFFFCC',
            'holy_glow_period': 3.0,
            'letter_jump_scale': 1.5,
            'letter_jump_duration': 0.2,
            'blur_sharp_max': 20,
            'blur_sharp_clear_frac': 0.4,
            # Multi-layer stroke expansion
            'stroke_expand_enabled': False,
            'stroke_expand_layers': 3,
            'stroke_expand_step': 4,
            'stroke_expand_feather': 8,
            'stroke_expand_colors': '#FF0000,#00FF00,#0000FF',
            'stroke_expand_layer_widths': [],
            'stroke_expand_layer_feathers': [],
            # Gradient background
            'bg_gradient_enabled': False,
            'bg_gradient_type': 'linear_h',
            'bg_gradient_colors': '#e0c3fc,#8ec5fc',
            'bg_gradient_highlight': False,
            # Metronome read/unread styles
            'metro_read_color': '#FFFFFF',
            'metro_read_stroke_color': '#000000',
            'metro_read_stroke_width': 2,
            'metro_read_opacity': 255,
            'metro_unread_color': '#808080',
            'metro_unread_stroke_color': '#404040',
            'metro_unread_stroke_width': 1,
            'metro_unread_opacity': 100,
            # Box color transition
            'box_transition_enabled': False,
            'box_transition_color_to': '#8ec5fc',
            # Audio spectrum (enhanced)
            'spectrum_bands': 32,
            'spectrum_bar_width': 4,
            'spectrum_bar_gap': 2,
            'spectrum_max_height': 80,
            'spectrum_color_bottom': '#00AAFF',
            'spectrum_color_top': '#FF4444',
            'spectrum_position': 'below',
            'spectrum_shape': 'bars',
            'spectrum_dual': False,
            'spectrum_top_shape': 'bars',
            'spectrum_opacity': 200,
            'spectrum_roundness': 2,
            # ── Scrolling Lyrics Mode (歌词滚动模式) ──
            'lyrics_scroll_enabled': False,
            'lyrics_scroll_damping': 0.8,
            'lyrics_scroll_transition': 0.4,
            'lyrics_center_scale': 1.15,
            'lyrics_edge_feather': 40,
            'lyrics_alpha_decay': 30,
            'lyrics_line_gap': 20,
        }
        self.render_mode = 'quality'
        self._batch_export_out_dir = None
        self.is_batch_running = False
        self.is_batch_exporting = False

        # 启动时自动加载默认关键词配色预设
        if hasattr(self, 'sub_panel') and hasattr(self.sub_panel, 'keyword_colors_ui'):
            self.sub_panel.keyword_colors_ui.load_default_preset_if_empty()
        self._finish_shell()

    def _build_shell_menus(self):
        """文件·编辑·视图·字幕·音频·批量·导出·性能·帮助. Keep every old action."""
        from cutora.ui.i18n import tr
        owned = []
        def own(obj):
            if obj is not None:
                owned.append(obj)
            return obj
        self._menu_owned = owned
        menu = self.menuBar()
        file_menu = menu.addMenu(tr("文件"))
        act_new = QAction(tr("新建项目"), self)
        act_new.setShortcut(QKeySequence.StandardKey.New)
        act_new.triggered.connect(self.new_project)
        file_menu.addAction(act_new)
        act_open = QAction(tr("打开项目"), self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self.open_project)
        file_menu.addAction(act_open)
        act_import_media = QAction(tr("导入媒体"), self)
        act_import_media.setShortcut(QKeySequence("Ctrl+I"))
        act_import_media.triggered.connect(self.import_media)
        file_menu.addAction(act_import_media)
        act_import_proj = QAction(tr("导入工程文件…"), self)
        act_import_proj.setToolTip(tr("把多个工程的视频和字幕合并进当前工程"))
        act_import_proj.triggered.connect(self.import_project_files)
        file_menu.addAction(act_import_proj)
        self.recent_menu = file_menu.addMenu(tr("打开最近项目"))
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)
        act_save = QAction(tr("保存项目"), self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self.save_project)
        file_menu.addAction(act_save)
        act_save_as = QAction(tr("另存为"), self)
        act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        act_save_as.triggered.connect(self.save_project_as)
        file_menu.addAction(act_save_as)
        act_pack = QAction(tr("打包工程…"), self)
        act_pack.triggered.connect(self.pack_project)
        file_menu.addAction(act_pack)
        # Q118：开机不再弹「上次未正常退出，是否恢复？」。真崩过想捞快照
        # 就来这里点一下；没有残留时这一条是灰的。
        self.act_autosave_restore = QAction(tr("恢复上次自动保存…"), self)
        self.act_autosave_restore.setEnabled(False)
        self.act_autosave_restore.triggered.connect(self.restore_autosave_from_menu)
        file_menu.addAction(self.act_autosave_restore)
        proj_menu = menu.addMenu(tr("项目"))
        _a = QAction(tr("术语表…"), self)
        _a.triggered.connect(self.open_glossary_dialog)
        proj_menu.addAction(_a)
        own(_a)
        edit_menu = menu.addMenu(tr("编辑"))
        act_undo = QAction(tr("撤销"), self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(self.undo)
        edit_menu.addAction(act_undo)
        act_redo = QAction(tr("重做"), self)
        act_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        act_redo.triggered.connect(self.redo)
        edit_menu.addAction(act_redo)
        self.history_menu = edit_menu.addMenu(tr("操作历史"))
        self.history_menu.aboutToShow.connect(self._populate_history_menu)
        act_del = QAction(tr("删除选中片段"), self)
        act_del.triggered.connect(self.delete_timeline_selection)
        edit_menu.addAction(act_del)
        _s = QAction(self)
        _s.setSeparator(True)
        edit_menu.addAction(_s)
        own(_s)
        self.act_add_text = QAction(tr("添加文本"), self)
        self.act_add_text.setShortcut(QKeySequence("Ctrl+T"))
        self.act_add_text.triggered.connect(self.add_text_ov)
        edit_menu.addAction(self.act_add_text)
        self.act_add_image = QAction(tr("添加图片"), self)
        self.act_add_image.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self.act_add_image.triggered.connect(self.add_img_ov)
        edit_menu.addAction(self.act_add_image)

        # Q115：加轨道也给一组快捷键，菜单里能看见按哪个键
        from cutora.ui.shortcuts import shortcut_key as _sc_key
        track_menu = edit_menu.addMenu(tr("添加轨道"))
        self.act_add_tracks = {}
        for _sid, _title, _ttype in (
            ("track_add_video", tr("视频轨道"), "video"),
            ("track_add_audio", tr("音频轨道"), "audio"),
            ("track_add_text", tr("文本轨道"), "text"),
            ("track_add_image", tr("图片轨道"), "image"),
            ("track_add_adjust", tr("调整轨道（纯色 / 局部效果）"), "adjust"),
        ):
            _act = QAction(_title, self)
            _seq = _sc_key(_sid)
            if _seq:
                _act.setShortcut(QKeySequence(_seq))
                _act.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            _act.triggered.connect(
                lambda _checked=False, t=_ttype: self.shortcut_add_track(t))
            track_menu.addAction(_act)
            self.act_add_tracks[_ttype] = _act

        view_menu = menu.addMenu(tr("视图"))
        act_reset = QAction(tr("重置布局"), self)
        act_reset.triggered.connect(self.reset_shell_layout)
        view_menu.addAction(act_reset)
        self.act_keyframes = QAction(tr("关键帧编辑器"), self, checkable=True)
        self.act_keyframes.setShortcut(QKeySequence("Alt+K"))
        self.act_keyframes.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_keyframes.setToolTip(tr("时间线下方的关键帧面板和运动曲线编辑器"))
        self.act_keyframes.triggered.connect(self.toggle_keyframe_editor)
        view_menu.addAction(self.act_keyframes)
        self.act_nodes = QAction(tr("节点编辑器"), self, checkable=True)
        self.act_nodes.setShortcut(QKeySequence("Alt+N"))
        self.act_nodes.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_nodes.setToolTip(tr("时间线下方的节点面板：选中图层的效果节点图（遮罩 / 抠像 / 调色串联）"))
        self.act_nodes.triggered.connect(self.toggle_node_editor)
        view_menu.addAction(self.act_nodes)
        self.act_full = QAction(tr("全屏预览"), self)
        self.act_full.setShortcut(QKeySequence("F"))
        self.act_full.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_full.triggered.connect(self.toggle_preview_fullscreen)
        view_menu.addAction(self.act_full)
        from cutora.render.safe_zones import disclaimer as _sz_disclaimer, presets as _sz_presets
        self.act_safe_zones = QAction(tr("安全区参考层"), self, checkable=True)
        self.act_safe_zones.setToolTip(tr("参考值，随平台版本变化"))
        from cutora.ui.shortcuts import shortcut_key as _sz_key
        _sz_seq = _sz_key("safe_zones")
        if _sz_seq:
            self.act_safe_zones.setShortcut(QKeySequence(_sz_seq))
        self.act_safe_zones.toggled.connect(self._on_safe_zones_master)
        view_menu.addAction(self.act_safe_zones)
        sz_menu = view_menu.addMenu(tr("安全区预设"))
        _sz_note = QAction(tr(_sz_disclaimer() or "参考值，随平台版本变化"), self)
        _sz_note.setEnabled(False)
        sz_menu.addAction(_sz_note)
        self.safe_zone_preset_actions = {}
        for _p in _sz_presets():
            _pid = str(_p.get("id") or "")
            _act = QAction(str(_p.get("name") or _pid), self, checkable=True)
            _act.setData(_pid)
            _act.toggled.connect(lambda on, i=_pid: self._on_safe_zone_preset(i, on))
            sz_menu.addAction(_act)
            self.safe_zone_preset_actions[_pid] = _act
        canvas_menu = view_menu.addMenu(tr("工程画布尺寸"))
        from cutora.model.canvas import CANVAS_PRESETS
        for _cw, _ch, _clab in CANVAS_PRESETS:
            _a = QAction(tr("%s（%s）" % ("%dx%d" % (_cw, _ch), _clab)), self)
            _a.triggered.connect(lambda _=False, w=_cw, h=_ch: self._set_canvas_size(w, h))
            canvas_menu.addAction(_a)
            own(_a)
        _a = QAction(tr("自定义尺寸..."), self)
        _a.triggered.connect(self._prompt_custom_canvas)
        canvas_menu.addAction(_a)
        own(_a)
        fit_menu = view_menu.addMenu(tr("视频适配模式"))
        fit_group = QActionGroup(self)
        fit_group.setExclusive(True)
        for label, mode in ((tr("适配"), "fit"), (tr("填满"), "fill"), (tr("拉伸"), "stretch")):
            act = QAction(label, self, checkable=True)
            act.setData(mode)
            if mode == self._current_fit_mode:
                act.setChecked(True)
            act.triggered.connect(lambda checked, m=mode: self._set_fit_mode(m))
            fit_group.addAction(act)
            fit_menu.addAction(act)
        self._fit_mode_actions = fit_group

        sub_menu = menu.addMenu(tr("字幕"))
        sub_menu.addAction(self.act_add_text)
        _a = QAction(tr("重新识别"), self)
        _a.triggered.connect(self._ui_auto_subtitle)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("按当前排版重新断句"), self)
        _a.triggered.connect(self._ui_relayout_subtitles)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("检查阅读速度"), self)
        _a.triggered.connect(self._ui_check_cps)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("导入字幕文件…"), self)
        _a.triggered.connect(self._ui_import_subtitle_file)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("导出字幕文件…"), self)
        _a.triggered.connect(self._ui_export_subtitle_file)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("一键全自动"), self)
        _a.triggered.connect(self.start_batch_ai)
        sub_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("字体管理"), self)
        _a.triggered.connect(self.open_font_manager)
        sub_menu.addAction(_a)
        own(_a)
        lang_menu = sub_menu.addMenu(tr("对齐/识别语言"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        from cutora.core import config as _cfg_lang
        from cutora.asr.languages import picker_entries
        current = getattr(_cfg_lang, "LANGUAGE", "en")
        last_section = None
        for ent in picker_entries():
            if last_section is not None and ent["section"] != last_section:
                _s = QAction(self)
                _s.setSeparator(True)
                lang_menu.addAction(_s)
                own(_s)
            last_section = ent["section"]
            act_l = QAction(ent["label"], self, checkable=True)
            act_l.setChecked(ent["code"] == current)
            if ent.get("tooltip"):
                act_l.setToolTip(ent["tooltip"])
            act_l.triggered.connect(
                lambda checked, c=ent["code"]: self._set_align_language(c))
            lang_group.addAction(act_l)
            lang_menu.addAction(act_l)

        batch_menu = menu.addMenu(tr("批量"))
        _a = QAction(tr("片段智能拼接…"), self)
        _a.triggered.connect(self.open_script_stitcher)
        batch_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("片段散落…"), self)
        _a.triggered.connect(self.open_script_broll_scatter)
        batch_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("批量加载…"), self)
        _a.triggered.connect(self.open_script_insert_image)
        batch_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("中央监视器…"), self)
        _a.setShortcut(QKeySequence("Ctrl+Shift+M"))
        _a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        _a.setToolTip(tr("一屏看全部视频，直接在画面上调分屏、平移缩放、搬文字、拖素材"))
        _a.triggered.connect(self.open_central_monitor)
        batch_menu.addAction(_a)
        own(_a)
        _s = QAction(self)
        _s.setSeparator(True)
        batch_menu.addAction(_s)
        own(_s)
        self.act_copy_sub_style = QAction(tr("从选中视频拷贝字幕样式"), self)
        self.act_copy_sub_style.triggered.connect(self.sync_style_to_selected)
        batch_menu.addAction(self.act_copy_sub_style)
        self.act_share_tracks = QAction(tr("从选中视频共享轨道…"), self)
        self.act_share_tracks.triggered.connect(self.on_batch_share_clicked)
        batch_menu.addAction(self.act_share_tracks)
        _s = QAction(self)
        _s.setSeparator(True)
        batch_menu.addAction(_s)
        own(_s)
        act_queue = QAction(tr("工程队列…"), self)
        act_queue.triggered.connect(lambda: self.open_project_queue_dialog())
        batch_menu.addAction(act_queue)
        self.act_credit_copy = QAction(tr("复制署名到队列每条视频"), self)
        self.act_credit_copy.triggered.connect(self.copy_credit_overlay_to_queue)
        batch_menu.addAction(self.act_credit_copy)
        self.act_auto_credit = QAction(tr("配方：工程队列署名…"), self)
        self.act_auto_credit.triggered.connect(self.open_auto_credit_dialog)
        batch_menu.addAction(self.act_auto_credit)
        act_split = QAction(tr("自动拆分工程…"), self)
        act_split.triggered.connect(self.split_current_project)
        batch_menu.addAction(act_split)
        _s = QAction(self)
        _s.setSeparator(True)
        batch_menu.addAction(_s)
        own(_s)
        act_rec = QAction(tr("开始录制配方"), self)
        act_rec.triggered.connect(self._recipe_record_start)
        batch_menu.addAction(act_rec)
        act_rec_stop = QAction(tr("停止录制配方"), self)
        act_rec_stop.triggered.connect(self._recipe_record_stop)
        batch_menu.addAction(act_rec_stop)
        act_apply = QAction(tr("套用配方到文件夹…"), self)
        act_apply.triggered.connect(self._recipe_apply_folder)
        batch_menu.addAction(act_apply)

        exp_menu = menu.addMenu(tr("导出"))
        act_exp = QAction(tr("导出"), self)
        act_exp.setShortcut(QKeySequence("Ctrl+E"))
        act_exp.triggered.connect(self.export_video)
        exp_menu.addAction(act_exp)
        self.act_bexp = QAction(tr("批量导出"), self)
        self.act_bexp.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_bexp.triggered.connect(self.toggle_batch_export)
        exp_menu.addAction(self.act_bexp)
        _a = QAction(tr("导出设置"), self)
        _a.triggered.connect(self.open_export_settings)
        exp_menu.addAction(_a)
        own(_a)
        perf_menu = menu.addMenu(tr("性能"))
        from cutora.render import segment_export as _seg
        self.act_segment_export = QAction(tr("单个视频分段并行导出"), self, checkable=True)
        self.act_segment_export.setChecked(bool(_seg.enabled()))
        self.act_segment_export.setToolTip(tr("导出一条视频时切成几段、多个进程同时渲染，再无损拼接。编码参数不变。"))
        self.act_segment_export.triggered.connect(self._on_toggle_segment_export)
        perf_menu.addAction(self.act_segment_export)
        self.act_parallel_export = QAction(tr("并行批量导出"), self, checkable=True)
        self.act_parallel_export.setChecked(bool(PARALLEL_EXPORT_ENABLED))
        self.act_parallel_export.triggered.connect(self._on_toggle_parallel_export)
        perf_menu.addAction(self.act_parallel_export)
        _a = QAction(tr("并行进程数..."), self)
        _a.triggered.connect(self._on_set_parallel_workers)
        perf_menu.addAction(_a)
        own(_a)
        _s = QAction(self)
        _s.setSeparator(True)
        perf_menu.addAction(_s)
        own(_s)
        _a = QAction(tr("释放显存 (卸载 AI 模型)"), self)
        _a.triggered.connect(self._on_release_vram)
        perf_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("显存占用情况"), self)
        _a.triggered.connect(self._on_show_vram_info)
        perf_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("性能设置…"), self)
        _a.triggered.connect(lambda: self.open_settings_dialog(2))
        perf_menu.addAction(_a)
        own(_a)
        settings_menu = menu.addMenu(tr("设置"))
        act_settings = QAction(tr("设置…"), self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self.open_settings_dialog)
        settings_menu.addAction(act_settings)
        act_ai = QAction(tr("AI 提供商..."), self)
        act_ai.triggered.connect(self.open_ai_providers_dialog)
        settings_menu.addAction(act_ai)

        help_menu = menu.addMenu(tr("帮助"))
        _a = QAction(tr("控制台"), self)
        _a.setShortcut(QKeySequence("Ctrl+Shift+L"))
        _a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        _a.triggered.connect(self.open_console)
        help_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("关于日志与隐私"), self)
        _a.triggered.connect(self.open_log_privacy)
        help_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("快捷键"), self)
        _a.triggered.connect(self.open_shortcut_help)
        help_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("语言支持情况"), self)
        _a.triggered.connect(self.open_language_support)
        help_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("字体许可"), self)
        _a.triggered.connect(self.open_font_licenses_dialog)
        help_menu.addAction(_a)
        own(_a)
        _a = QAction(tr("关于 片语 Studio"), self)
        _a.triggered.connect(self.open_about_dialog)
        help_menu.addAction(_a)
        own(_a)
        self._add_mode_corner()

    def _add_mode_corner(self):
        from cutora.ui.i18n import tr
        from cutora.ui.icons import bind_icon
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 0, 8, 0)
        lay.setSpacing(4)
        btn_set = QToolButton()
        bind_icon(btn_set, "settings", size=16, color_token="fg1")
        btn_set.setToolTip(tr("设置"))
        btn_set.clicked.connect(self.open_settings_dialog)
        btn_help = QToolButton()
        bind_icon(btn_help, "circle-help", size=16, color_token="fg1")
        btn_help.setToolTip(tr("帮助"))
        btn_help.clicked.connect(self.open_about_dialog)
        lay.addWidget(btn_set)
        lay.addWidget(btn_help)
        # PySide6: setCornerWidget does not keep the Python wrapper alive.
        # If `box` is collected, the C++ buttons are deleted and set_ui_mode
        # raises "Internal C++ object already deleted".
        self._mode_corner = box
        self.menuBar().setCornerWidget(self._mode_corner, Qt.Corner.TopRightCorner)

    def _assemble_shell(self, bin_w, play_w, insp_w, tab_txt):
        from cutora.ui.i18n import tr
        from cutora.ui.welcome import WelcomePage
        self.setDockNestingEnabled(True)
        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(bin_w, tr("媒体库"))
        self.left_tabs.addTab(tab_txt, tr("字幕文本"))
        from cutora.ui.panels.asset_library import AssetLibraryPanel
        self.asset_library = AssetLibraryPanel()
        self.left_tabs.addTab(self.asset_library, tr("资产库"))
        from cutora.ui.panels.sticker_panel import StickerPanel
        self.sticker_panel = StickerPanel()
        self.sticker_panel.insert_requested.connect(self._on_insert_sticker)
        self.left_tabs.addTab(self.sticker_panel, tr("贴纸"))
        from cutora.ui.panels.transition_panel import TransitionPanel
        self.transition_panel = TransitionPanel()
        self.transition_panel.apply_requested.connect(self._on_apply_transition)
        self.left_tabs.addTab(self.transition_panel, tr("转场"))
        from cutora.ui.asr_batch_results import AsrBatchResults
        self._asr_batch_results = AsrBatchResults(self)
        self._asr_batch_results.hide()
        self._asr_batch_results.rerun_yellow.connect(self._rerun_yellow_asr)
        self._asr_batch_results.rerun_corrected.connect(self._rerun_corrected_asr)
        self._batch_results_tab = -1
        self._batch_results_open = False
        self.dock_left = QDockWidget(tr("素材"), self)
        self.dock_left.setObjectName("dockLeft")
        self.dock_left.setWidget(self.left_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_left)

        self.dock_right = QDockWidget(tr("检查器"), self)
        self.dock_right.setObjectName("dockRight")
        from cutora.ui.theme.tokens import DOCK_RIGHT_DEFAULT, DOCK_RIGHT_MAX, DOCK_RIGHT_MIN
        self.dock_right.setMinimumWidth(DOCK_RIGHT_MIN)
        self.dock_right.setMaximumWidth(DOCK_RIGHT_MAX)
        from cutora.ui.widgets.responsive import mark_explicit
        mark_explicit(self.dock_right, "minimumWidth")
        mark_explicit(self.dock_right, "maximumWidth")
        self.dock_right.setWidget(insp_w)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_right)
        self.resizeDocks([self.dock_right], [DOCK_RIGHT_DEFAULT], Qt.Orientation.Horizontal)

        self.dock_timeline = QDockWidget(tr("时间线"), self)
        self.dock_timeline.setObjectName("dockTimeline")
        self.dock_timeline.setWidget(self.timeline_container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_timeline)
        for dock in (self.dock_left, self.dock_right, self.dock_timeline):
            self._thin_dock_handle(dock)
            dock.installEventFilter(self)
        for w_min in (self.dock_left, self.dock_right, self.left_tabs, self.tabs, insp_w, play_w, self.video_wrap, self.video_canvas):
            try:
                w_min.setMinimumHeight(60)
            except Exception:
                pass
        for w_tl in (self.timeline, self.timeline_container, self.dock_timeline):
            try:
                w_tl.setMinimumHeight(64)
            except Exception:
                pass
        # Q152-A：关键帧面板 + 运动曲线编辑器，时间线下方，默认收起（视图 → 关键帧编辑器 / Alt+K）
        from cutora.ui.keyframe_editor import KeyframeEditor
        self.keyframe_editor = KeyframeEditor(self)
        self.dock_keyframes = QDockWidget(tr("关键帧"), self)
        self.dock_keyframes.setObjectName("dockKeyframes")
        self.dock_keyframes.setWidget(self.keyframe_editor)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_keyframes)
        self.splitDockWidget(self.dock_timeline, self.dock_keyframes, Qt.Orientation.Vertical)
        self._thin_dock_handle(self.dock_keyframes)
        self.dock_keyframes.hide()
        self.dock_keyframes.visibilityChanged.connect(self._on_keyframe_dock_visible)
        # Q152-F：节点编辑器，和关键帧面板同一排 tab（视图 → 节点编辑器 / Alt+N）
        from cutora.ui.nodes.editor import NodeEditor
        self.node_editor = NodeEditor(self)
        self.dock_nodes = QDockWidget(tr("节点"), self)
        self.dock_nodes.setObjectName("dockNodes")
        self.dock_nodes.setWidget(self.node_editor)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_nodes)
        self.tabifyDockWidget(self.dock_keyframes, self.dock_nodes)
        self._thin_dock_handle(self.dock_nodes)
        self.dock_nodes.hide()
        self.dock_nodes.visibilityChanged.connect(self._on_node_dock_visible)

        self.welcome_page = WelcomePage()
        self.welcome_page.new_with_size.connect(self._welcome_new)
        self.welcome_page.new_custom.connect(self._welcome_new_custom)
        self.welcome_page.open_project.connect(self.open_project)
        self.welcome_page.open_recent.connect(lambda p: self.open_project(p))
        self.welcome_page.import_v6.connect(self._welcome_import_v6)
        self.welcome_page.files_dropped.connect(self._welcome_drop)
        self.editor_host = play_w
        self.shell_stack = QStackedWidget()
        self.shell_stack.setMinimumHeight(60)
        self.shell_stack.addWidget(self.welcome_page)
        self.shell_stack.addWidget(self.editor_host)
        self.setCentralWidget(self.shell_stack)

        self.quick_toolbar = QToolBar(tr("快速工具带"))
        self.quick_toolbar.setObjectName("quickToolbar")
        self.quick_toolbar.setMovable(False)
        self.addToolBar(self.quick_toolbar)
        b1 = QPushButton(tr("① 导入"))
        b1.clicked.connect(self.import_media)
        b2 = QPushButton(tr("② 重新识别"))
        b2.clicked.connect(self._ui_auto_subtitle)
        b3 = QPushButton(tr("③ 选风格"))
        b3.clicked.connect(self._focus_style_panel)
        b4 = QPushButton(tr("④ 导出"))
        b4.clicked.connect(self.export_video)
        self._quick_step_btns = (b1, b2, b3, b4)
        for b in self._quick_step_btns:
            self.quick_toolbar.addWidget(b)
        self._refresh_quick_steps()
        self._status_gpu = QLabel()
        self._status_task = QLabel()
        self._status_save = QLabel(tr("未自动保存"))
        from cutora.core.brand import APP_VERSION
        self._status_ver = QLabel("v" + APP_VERSION)
        sep_a = QLabel("│")
        sep_a.setObjectName("statusSep")
        sep_b = QLabel("│")
        sep_b.setObjectName("statusSep")
        from cutora.ui.widgets.fps_meter import FpsMeter
        self._fps_meter = FpsMeter()
        self.statusBar().addWidget(self._fps_meter, 0)
        self._fps_meter.clear()
        self.statusBar().addWidget(self.prog_frame, 1)
        self.statusBar().addPermanentWidget(self._status_gpu)
        self.statusBar().addPermanentWidget(sep_a)
        self.statusBar().addPermanentWidget(self._status_task)
        self.statusBar().addPermanentWidget(self._status_save)
        self._status_api = QLabel(tr("代理关"))
        self._status_api.setObjectName("hint")
        self._status_api.setToolTip(tr("本地 API（设置 → 代理接口）"))
        self.statusBar().addPermanentWidget(sep_b)
        self.statusBar().addPermanentWidget(self._status_api)
        self._status_doctor = QPushButton(tr("体检"))
        self._status_doctor.setObjectName("doctorBadge")
        self._status_doctor.setFlat(True)
        self._status_doctor.setCursor(Qt.CursorShape.PointingHandCursor)
        self._status_doctor.setToolTip(tr("启动体检"))
        self._status_doctor.hide()
        self._status_doctor.clicked.connect(self._show_doctor_report)
        self._doctor_report = ""
        self.statusBar().addPermanentWidget(self._status_doctor)
        self.statusBar().addPermanentWidget(self._status_ver)
        self.prog_frame.installEventFilter(self)
        self._dismiss_progress(force=True)
        self._refresh_status_gpu()
        self._api_led_timer = QTimer(self)
        self._api_led_timer.setInterval(1000)
        self._api_led_timer.timeout.connect(self._refresh_api_led)
        self._api_led_timer.start()
        self._refresh_api_led()

    def _finish_shell(self):
        from cutora.core.history import _load_app_settings
        s = _load_app_settings() or {}
        self._ui_mode = "pro"
        self.set_ui_mode("pro", persist=False)
        raw = s.get("dock_state")
        if raw:
            from PySide6.QtCore import QByteArray
            self.restoreState(QByteArray.fromHex(str(raw).encode("ascii")))
        self._restore_layout_sizes(s)
        self._sync_safe_zone_actions(s)
        if not self.project_path and not self.tasks:
            self.show_welcome()
        else:
            self.show_editor()
        if bool(s.get("api_enabled", True)):
            try:
                from cutora.api import DEFAULT_PORT
                from cutora.api.lifecycle import start_api
                start_api(int(s.get("api_port") or DEFAULT_PORT))
            except Exception:
                pass
        self._refresh_api_led()

    def set_ui_mode(self, mode, persist=True):
        """Always the full editor. Compact layout was removed."""
        self._apply_ui_mode("pro", persist=persist)

    def _apply_ui_mode(self, mode, persist=True):
        self._ui_mode = "pro"
        if hasattr(self, "quick_toolbar"):
            self.quick_toolbar.setVisible(False)
        self._dismiss_progress(force=True)
        if hasattr(self, "left_tabs"):
            self.left_tabs.setTabVisible(1, True)
            self.left_tabs.setTabVisible(2, True)
            # 批量结果 tab removed; language/confidence live on 媒体库 rows.
        if hasattr(self, "tabs"):
            for i in range(self.tabs.count()):
                self.tabs.setTabVisible(i, True)
        self._set_style_compact(False)
        self._apply_timeline_default_h(False)
        if persist:
            from cutora.core.history import _load_app_settings, _save_app_settings
            s = _load_app_settings()
            s.pop("ui_mode", None)
            _save_app_settings(s)
        self._refresh_status_gpu()

    def _set_style_compact(self, compact):
        from cutora.ui.widgets.dock_panels import CollapsibleSection
        if not hasattr(self, "sub_panel"):
            return
        keep = {"基本排版", "配色方案", "字幕排版"}
        sections = self.sub_panel.findChildren(CollapsibleSection)
        for sec in sections:
            title = sec._title_label.text() if hasattr(sec, "_title_label") else ""
            sec.setVisible((title in keep) if compact else True)
            if compact and title == "字幕排版":
                sec.set_collapsed(False)

    def toggle_ui_mode(self):
        return

    def _announce_hw_profile(self):
        """Q193：没有 N 卡的电脑，启动后状态栏说一次哪些功能会慢（用到时还会再问）。"""
        try:
            from cutora.ui.gpu_notice import has_gpu, startup_note
            from cutora.core.log import get_logger
            note = startup_note()
            get_logger("cutora.ui.main_window").info(
                "[HW] NVIDIA 显卡：%s；CPU %s 线程", "有" if has_gpu() else "没有", os.cpu_count())
            if note:
                self.statusBar().showMessage(note, 20000)
        except Exception:
            pass

    def _leave_rooms_for_shell(self):
        """Q191：新建 / 打开 / 导入 / 关工程都要回到剪辑页：房间先收掉（不然剪辑页的面板和快捷键状态会乱）。"""
        rooms = getattr(self, "room_host", None)
        if rooms is not None and rooms.current != "edit" and not getattr(rooms, "_switching", False):
            try:
                rooms.shutdown()
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").exception("leave rooms")

    def show_welcome(self):
        self._leave_rooms_for_shell()
        for d in (getattr(self, "dock_left", None), getattr(self, "dock_right", None),
                  getattr(self, "dock_timeline", None)):
            if d is not None:
                d.hide()
        if hasattr(self, "quick_toolbar"):
            self.quick_toolbar.hide()
        self._dismiss_progress(force=True)
        self.welcome_page.reload_recent()
        self.shell_stack.setCurrentIndex(0)

    def show_editor(self):
        if getattr(self, "_in_show_editor", False):
            return
        self._leave_rooms_for_shell()
        self._in_show_editor = True
        try:
            for d in (self.dock_left, self.dock_right, self.dock_timeline):
                d.show()
            self.shell_stack.setCurrentIndex(1)
            self.set_ui_mode(getattr(self, "_ui_mode", "quick"), persist=False)
            self._refresh_timecode_visibility()
        finally:
            self._in_show_editor = False

    def toggle_keyframe_editor(self, on=None):
        """Q152-A：显示 / 收起关键帧编辑器。"""
        dock = getattr(self, "dock_keyframes", None)
        if dock is None:
            return
        show = (not dock.isVisible()) if on is None else bool(on)
        dock.setVisible(show)
        if show:
            if dock.isFloating():
                dock.setFloating(False)
            self.keyframe_editor.refresh()
            self.keyframe_editor.fit_view()
            self.keyframe_editor.stack.currentWidget().setFocus()

    def toggle_node_editor(self, on=None):
        """Q152-F：显示 / 收起节点编辑器（和关键帧面板同一排 tab）。"""
        dock = getattr(self, "dock_nodes", None)
        if dock is None:
            return
        show = (not (dock.isVisible() and not dock.visibleRegion().isEmpty())) if on is None else bool(on)
        if show:
            dock.setVisible(True)
            if dock.isFloating():
                dock.setFloating(False)
            dock.raise_()
            self.node_editor.refresh(force=True)
            self.node_editor.view.setFocus()
        else:
            dock.setVisible(False)

    def _on_node_dock_visible(self, visible):
        act = getattr(self, "act_nodes", None)
        if act is not None and act.isChecked() != bool(visible):
            act.blockSignals(True)
            act.setChecked(bool(visible))
            act.blockSignals(False)

    def _on_keyframe_dock_visible(self, visible):
        act = getattr(self, "act_keyframes", None)
        if act is not None and act.isChecked() != bool(visible):
            act.blockSignals(True)
            act.setChecked(bool(visible))
            act.blockSignals(False)

    def reset_shell_layout(self):
        if getattr(self, "_in_reset_layout", False):
            return
        self._in_reset_layout = True
        try:
            self.dock_left.setFloating(False)
            self.dock_right.setFloating(False)
            self.dock_timeline.setFloating(False)
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_left)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_right)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_timeline)
            self.resizeDocks([self.dock_left, self.dock_right], [240, 320], Qt.Orientation.Horizontal)
            self.set_ui_mode(self._ui_mode)
        finally:
            self._in_reset_layout = False

    def open_settings_dialog(self, tab=0):
        if isinstance(tab, bool):
            tab = 0
        from cutora.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, initial_tab=int(tab or 0), parent=self)
        dlg.exec()
        self._refresh_status_gpu()

    def open_shortcut_help(self):
        from cutora.ui.shortcuts import ShortcutHelpDialog
        ShortcutHelpDialog.exec_dialog(self)

    def import_media(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入媒体", "",
            "视频 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;所有文件 (*)")
        if paths:
            self.bus.execute("media.import", {"paths": paths}, source="ui")

    def _ui_relayout_subtitles(self):
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        params = {"scope": "all"}
        tl = getattr(self, "timeline", None)
        sel = list(getattr(tl, "selected_indices", None) or [])
        if sel:
            params["scope"] = "selected"
            params["seg_ids"] = [int(i) for i in sel]
        try:
            out = self.bus.execute("subtitle.relayout", params, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))
            return
        n = int((out or {}).get("changed") or 0)
        self._ui_information(tr("重新断句"), tr("已处理 %s 句。") % n)

    def _on_wrap_width_committed(self, pct):
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        n_before = 0
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks or []):
            n_before = len(self.tasks[self.curr_idx].get("segments") or [])
        params = {"scope": "all"}
        try:
            out = self.bus.execute("subtitle.relayout", params, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))
            return
        n_after = int((out or {}).get("count") or n_before)
        self.statusBar().showMessage(
            tr("字幕宽度 %s%% → 重新断句，%s 条变 %s 条") % (
                int(pct), n_before, n_after),
            8000,
        )

    def _ui_check_cps(self):
        from PySide6.QtWidgets import QMessageBox
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        try:
            out = self.bus.execute("subtitle.check_cps", {}, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))
            return
        hits = list((out or {}).get("over") or [])
        if not hits:
            self._ui_information(tr("检查阅读速度"), tr("没有超过阈值的句子。"))
            return
        lines = []
        for hit in hits[:40]:
            lines.append("%.1f CPS  %s" % (hit.get("cps") or 0, (hit.get("text") or "")[:48]))
        extra = "" if len(hits) <= 40 else tr("\n…共 %s 句") % len(hits)
        box = QMessageBox(self)
        box.setWindowTitle(tr("检查阅读速度"))
        box.setText(tr("%s 句超过阅读速度。是否自动延长显示时间？") % len(hits))
        box.setInformativeText("\n".join(lines) + extra)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            ext = self.bus.execute("subtitle.extend_durations", {}, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))
            return
        self._ui_information(
            tr("检查阅读速度"),
            tr("已延长 %s 句。") % int((ext or {}).get("changed") or 0),
        )

    def _ui_auto_subtitle(self):
        """字幕菜单 / 工具栏 / 快速步骤：只走命令总线。"""
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "asr"):           # Q193：没有 N 卡先说清楚（会改用 CPU，很慢）
            return
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        if self.curr_idx >= 0 and hasattr(self, "text_input"):
            self.tasks[self.curr_idx]["text"] = self.text_input.toPlainText()
        has_subs = False
        if 0 <= self.curr_idx < len(self.tasks or []):
            has_subs = bool((self.tasks[self.curr_idx] or {}).get("segments"))
        elif any((t or {}).get("segments") for t in (self.tasks or [])):
            has_subs = True
        if has_subs:
            reply = QMessageBox.question(
                self,
                tr("重新识别"),
                tr("拼接已经带上字幕。再跑一次会覆盖时间线上现有的词级时间戳。\n\n确定重新识别？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        params = {"force": True}
        if self.curr_idx >= 0:
            params["media_id"] = int(self.curr_idx)
        try:
            self.bus.execute("subtitle.auto", params, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))

    def _ui_import_subtitle_file(self):
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入字幕文件"), "",
            "字幕 (*.srt *.vtt *.ass *.ssa *.txt *.json);;所有文件 (*)")
        if not path:
            return
        modes = ("replace", "append")
        labels = (tr("替换当前"), tr("追加"))
        chosen, ok = QInputDialog.getItem(
            self, tr("导入字幕"), tr("导入方式"), labels, 0, False)
        if not ok:
            return
        try:
            mode = modes[labels.index(chosen)]
        except ValueError:
            mode = "replace"
        params = {"path": path, "mode": mode}
        if self.curr_idx >= 0:
            params["media_id"] = int(self.curr_idx)
        try:
            self.bus.execute("subtitle.import_file", params, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))

    def _ui_export_subtitle_file(self):
        from cutora.core.commands import CommandError
        from cutora.ui.i18n import tr
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出字幕文件"), "",
            "SRT (*.srt);;WebVTT (*.vtt);;ASS (*.ass);;SSA (*.ssa);;"
            "文本 (*.txt);;JSON (*.json)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path = path + ".srt"
        params = {"path": path}
        if self.curr_idx >= 0:
            params["media_id"] = int(self.curr_idx)
        try:
            self.bus.execute("subtitle.export_file", params, source="ui")
        except CommandError as exc:
            self.on_align_error(str(exc))

    def _welcome_new(self, w, h):
        self._set_canvas_size(w, h)
        self.new_project()
        self.set_ui_mode("quick")
        self.show_editor()

    def _welcome_new_custom(self):
        self._prompt_custom_canvas()
        self.new_project()
        self.set_ui_mode("quick")
        self.show_editor()

    def _welcome_drop(self, paths):
        self.new_project()
        self.set_ui_mode("quick")
        self.show_editor()
        self.on_drop(paths)

    def _welcome_import_v6(self):
        from cutora.core.migrate_user_data import find_import_sources, import_from
        from cutora.ui.i18n import tr
        sources = find_import_sources()
        if not sources:
            QMessageBox.information(self, tr("导入"), tr("未找到 AutoSub v6 设置。"))
            return
        report = import_from(sources[0])
        QMessageBox.information(
            self, tr("导入"),
            tr("已从 {src} 导入 {n} 个文件。", src=report.get("source"), n=len(report.get("files") or [])))
        self.welcome_page.reload_recent()

    def _focus_style_panel(self):
        self.show_editor()
        if hasattr(self, "dock_right"):
            self.dock_right.show()
            self.dock_right.raise_()
        gallery = getattr(self, "style_gallery", None)
        if gallery is not None:
            self.tabs.setCurrentWidget(gallery)
            return
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "字幕样式":
                self.tabs.setCurrentIndex(i)
                break

    def toggle_preview_fullscreen(self):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return
        now = time.monotonic()
        if getattr(self, "_fs_toggling", False) or now - getattr(self, "_fs_toggle_at", 0) < 0.05:
            return
        self._fs_toggle_at = now
        self._fs_toggling = True
        try:
            canvas = self.video_canvas
            if canvas.isFullScreen() or getattr(canvas, "_preview_fullscreen", False):
                self._exit_preview_fullscreen()
            else:
                self._enter_preview_fullscreen()
        finally:
            self._fs_toggling = False

    def _enter_preview_fullscreen(self):
        canvas = self.video_canvas
        lay = getattr(self, "_play_layout", None)
        idx = lay.indexOf(self.video_wrap) if lay is not None else -1
        self._fs_wrap_stretch = lay.stretch(idx) if lay is not None and idx >= 0 else 1
        self._fs_was_playing = bool(getattr(self, "_timeline_playing", False))
        self._fs_pos = int(getattr(self, "_timeline_ms", 0) or 0)
        canvas.setWindowFlags(Qt.WindowType.Window)
        canvas.showFullScreen()
        canvas.activateWindow()
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.setActiveWindow(canvas)
        canvas.set_preview_fullscreen_mode(True)
        if hasattr(self, "act_full"):
            self.act_full.setShortcut(QKeySequence())

    def _exit_preview_fullscreen(self):
        canvas = self.video_canvas
        canvas.set_preview_fullscreen_mode(False)
        canvas.setWindowFlags(Qt.WindowType.Widget)
        self.video_wrap.layout().addWidget(canvas)
        canvas.show()
        if hasattr(self, "act_full"):
            self.act_full.setShortcut(QKeySequence("F"))
            self.act_full.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        lay = getattr(self, "_play_layout", None)
        if lay is not None:
            idx = lay.indexOf(self.video_wrap)
            if idx >= 0:
                lay.setStretch(idx, int(getattr(self, "_fs_wrap_stretch", 1)))
        pos = int(getattr(self, "_fs_pos", getattr(self, "_timeline_ms", 0)) or 0)
        if hasattr(self, "on_seek_request"):
            self.on_seek_request(pos)
        want_play = bool(getattr(self, "_fs_was_playing", False))
        playing = bool(getattr(self, "_timeline_playing", False))
        if want_play != playing:
            self.toggle_play()
        canvas.update()

    def shortcut_delete(self):
        """No global Delete. Timeline handles Delete; media list uses Shift+Delete."""
        return

    def delete_timeline_selection(self):
        tl = getattr(self, "timeline", None)
        if tl is None:
            return
        from PySide6.QtGui import QKeyEvent
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        tl.keyPressEvent(ev)

    def shortcut_stop(self):
        self._jkl_set_rate(0)
        if getattr(self, "_timeline_playing", False):
            self.toggle_play()

    def shortcut_play_fwd(self):
        from cutora.ui.jkl import next_rate
        self._jkl_set_rate(next_rate(getattr(self, "_jkl_rate", 0), True))

    def shortcut_play_rev(self):
        from cutora.ui.jkl import next_rate
        self._jkl_set_rate(next_rate(getattr(self, "_jkl_rate", 0), False))

    def _ensure_jkl_timer(self):
        if getattr(self, "_jkl_timer", None) is None:
            self._jkl_timer = QTimer(self)
            self._jkl_timer.timeout.connect(self._jkl_tick)
        return self._jkl_timer

    def _jkl_set_rate(self, rate: int):
        from cutora.ui.jkl import rate_label
        self._jkl_rate = int(rate or 0)
        timer = self._ensure_jkl_timer()
        if hasattr(self, "_apply_preview_track_mute"):
            self._apply_preview_track_mute()
        else:
            try:
                self.audio_output.setMuted(self._jkl_rate < 0)
            except Exception:
                pass
        if self._jkl_rate == 0:
            timer.stop()
            try:
                self.media_player.setPlaybackRate(1.0)
            except Exception:
                pass
            self.statusBar().showMessage(rate_label(0), 1500)
            return
        if self._jkl_rate < 0:
            if getattr(self, "_timeline_playing", False):
                self.toggle_play()
            timer.start(33)
        elif self._jkl_rate == 1:
            timer.stop()
            try:
                self.media_player.setPlaybackRate(1.0)
            except Exception:
                pass
            if not getattr(self, "_timeline_playing", False):
                self.toggle_play()
        else:
            timer.stop()
            try:
                self.media_player.setPlaybackRate(float(self._jkl_rate))
            except Exception:
                timer.start(33)
            if not getattr(self, "_timeline_playing", False):
                self.toggle_play()
        self.statusBar().showMessage(rate_label(self._jkl_rate), 2000)

    def _jkl_tick(self):
        rate = int(getattr(self, "_jkl_rate", 0) or 0)
        if rate == 0:
            self._ensure_jkl_timer().stop()
            return
        step = int(33 * abs(rate))
        now = int(getattr(self, "_timeline_ms", 0) or 0)
        if rate < 0:
            nxt = max(0, now - step)
            self.on_seek_request(nxt)
            if nxt <= 0:
                self._jkl_set_rate(0)
        else:
            self.on_seek_request(now + step)

    def _project_fps(self) -> float:
        try:
            task = self.tasks[self.curr_idx] if 0 <= self.curr_idx < len(self.tasks or []) else None
            tl = _tl_of(task) if task is not None else None
            fps = float(getattr(tl, "fps", 0) or (task or {}).get("fps") or 0)
        except Exception:
            fps = 0.0
        return fps if fps > 0 else 30.0

    def _step_frames(self, n: int) -> None:
        """Q145：逐帧按工程帧率（以前写死 33 ms，60 fps / 25 fps 工程会跳帧或走不动）。"""
        fps = self._project_fps()
        cur = int(getattr(self, "_timeline_ms", 0) or 0)
        idx = int(round(cur * fps / 1000.0)) + int(n)
        self.on_seek_request(max(0, int(round(idx * 1000.0 / fps))))

    def shortcut_frame_prev(self):
        self._step_frames(-1)

    def shortcut_frame_next(self):
        self._step_frames(1)

    def shortcut_jump_back(self):
        self.on_seek_request(max(0, int(getattr(self, "_timeline_ms", 0)) - 1000))

    def shortcut_jump_fwd(self):
        self.on_seek_request(int(getattr(self, "_timeline_ms", 0)) + 1000)

    def shortcut_goto_start(self):
        self.on_seek_request(0)

    def shortcut_mark_in(self):
        if self.curr_idx < 0:
            return
        t = float(getattr(self.timeline, "position", 0) or 0) / 1000.0
        from cutora.core.commands import get_bus
        get_bus().execute("timeline.set_in_point", {"t": t}, source="ui")

    def shortcut_mark_out(self):
        if self.curr_idx < 0:
            return
        t = float(getattr(self.timeline, "position", 0) or 0) / 1000.0
        from cutora.core.commands import get_bus
        get_bus().execute("timeline.set_out_point", {"t": t}, source="ui")

    def shortcut_clear_in(self):
        if self.curr_idx < 0:
            return
        from cutora.core.commands import get_bus
        get_bus().execute("timeline.clear_io_points", {"which": "in"}, source="ui")

    def shortcut_clear_out(self):
        if self.curr_idx < 0:
            return
        from cutora.core.commands import get_bus
        get_bus().execute("timeline.clear_io_points", {"which": "out"}, source="ui")

    def shortcut_marker_add(self):
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks or []):
            return
        t = float(getattr(self.timeline, "position", 0) or 0) / 1000.0
        from cutora.core.commands import get_bus
        get_bus().execute("marker.add", {"t": t}, source="ui")

    def shortcut_marker_prev(self):
        self._jump_marker(-1)

    def shortcut_marker_next(self):
        self._jump_marker(1)

    def _jump_marker(self, direction):
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks or []):
            return
        from cutora.model.markers import sorted_markers
        rows = sorted_markers(self.tasks[self.curr_idx])
        if not rows:
            return
        now = float(getattr(self.timeline, "position", 0) or 0) / 1000.0
        if direction < 0:
            cand = [m for m in rows if float(m.get("t") or 0.0) < now - 1e-4]
            rec = cand[-1] if cand else None
        else:
            cand = [m for m in rows if float(m.get("t") or 0.0) > now + 1e-4]
            rec = cand[0] if cand else None
        if rec is None:
            return
        self.on_seek_request(int(float(rec.get("t") or 0.0) * 1000))

    def shortcut_split(self):
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks or []):
            return
        t = float(getattr(self.timeline, "position", 0) or 0) / 1000.0
        from cutora.core.commands import get_bus
        bus = get_bus()
        bus.execute("timeline.split_at", {"time": t}, source="ui")
        task = self.tasks[self.curr_idx]
        segs = list(task.get("segments") or [])
        sel = getattr(self.timeline, "selection", None)
        ids = list(getattr(sel, "segments", None) or [])
        if not ids:
            ids = list(getattr(self.timeline, "selected_indices", None) or [])
        if not ids:
            ids = [
                i for i, s in enumerate(segs)
                if isinstance(s, dict)
                and float(s.get("start") or 0.0) < t < float(s.get("end") or 0.0)
            ]
        for sid in sorted({int(i) for i in ids}, reverse=True):
            segs = list(task.get("segments") or [])
            if sid < 0 or sid >= len(segs) or not isinstance(segs[sid], dict):
                continue
            s = segs[sid]
            if not (float(s.get("start") or 0.0) < t < float(s.get("end") or 0.0)):
                continue
            try:
                bus.execute(
                    "subtitle.split",
                    {"seg_id": sid, "time": t},
                    source="ui",
                )
            except Exception:
                pass
        self.timeline.update()

    def shortcut_goto_end(self):
        dur = int(getattr(self.timeline, "duration", 0) or 0)
        self.on_seek_request(max(0, dur))

    def shortcut_zoom_in(self):
        self._nudge_timeline_zoom(1.2)

    def shortcut_zoom_out(self):
        self._nudge_timeline_zoom(0.8)

    def shortcut_zoom_fit(self):
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit
        focus = self.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return
        tl = getattr(self, "timeline", None)
        fn = getattr(tl, "zoom_fit_all", None) if tl is not None else None
        if callable(fn):
            fn()

    def shortcut_zoom_selection(self):
        tl = getattr(self, "timeline", None)
        fn = getattr(tl, "zoom_to_selection", None) if tl is not None else None
        if callable(fn):
            fn()

    def _nudge_timeline_zoom(self, factor):
        tl = self.timeline
        zoom = getattr(tl, "zoom_at_playhead", None)
        if callable(zoom):
            zoom(factor)
            return
        tl.pixels_per_second = max(1, min(300, tl.pixels_per_second * factor))
        tl.update()

    def _fit_volume_spin(self, spin):
        """Min width = longest value '0.18' + spinner arrows. Do not rely on layout."""
        spin.setDecimals(2)
        fm = spin.fontMetrics()
        text_w = max(fm.horizontalAdvance("0.18"), fm.horizontalAdvance("5.00"))
        spin.setMinimumWidth(text_w + 18 + 24)
        spin.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def _thin_dock_handle(self, dock):
        """4px title strip. Floating hint only; height/width come from QMainWindow::separator."""
        handle = _DockEdgeHandle(dock)
        dock.setTitleBarWidget(handle)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

    def _apply_timeline_h(self, h):
        if not hasattr(self, "dock_timeline"):
            return
        self._applying_timeline_h = True
        try:
            self.resizeDocks(
                [self.dock_timeline], [max(80, int(h))], Qt.Orientation.Vertical)
        finally:
            self._applying_timeline_h = False

    def _apply_timeline_default_h(self, quick):
        if not hasattr(self, "dock_timeline"):
            return
        if getattr(self, "_timeline_h_user_set", False):
            return
        self._apply_timeline_h(140 if quick else 240)

    def _layout_dict(self, settings=None):
        s = settings if isinstance(settings, dict) else _load_app_settings()
        layout = s.get("layout")
        return layout if isinstance(layout, dict) else {}

    def _persist_layout_sizes(self):
        s = _load_app_settings()
        layout = self._layout_dict(s)
        if hasattr(self, "dock_timeline"):
            layout["timeline_h"] = int(self.dock_timeline.height())
        if hasattr(self, "dock_right"):
            layout["inspector_w"] = int(self.dock_right.width())
        if hasattr(self, "dock_left"):
            layout["bin_w"] = int(self.dock_left.width())
        s["layout"] = layout
        _save_app_settings(s)

    def _schedule_persist_layout(self):
        timer = getattr(self, "_layout_persist_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._persist_layout_sizes)
            self._layout_persist_timer = timer
        timer.start(200)

    def _restore_layout_sizes(self, settings=None):
        layout = self._layout_dict(settings)
        th = layout.get("timeline_h")
        if isinstance(th, (int, float)) and int(th) >= 80:
            self._timeline_h_user_set = True
            self._apply_timeline_h(int(th))
        iw = layout.get("inspector_w")
        if isinstance(iw, (int, float)) and hasattr(self, "dock_right"):
            w = max(260, min(800, int(iw)))
            self.resizeDocks([self.dock_right], [w], Qt.Orientation.Horizontal)
        bw = layout.get("bin_w")
        if isinstance(bw, (int, float)) and int(bw) > 0 and hasattr(self, "dock_left"):
            self.resizeDocks([self.dock_left], [int(bw)], Qt.Orientation.Horizontal)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_timecode_visibility()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "prog_frame", None) and event.type() == QEvent.Type.MouseButtonPress:
            if getattr(self, "_progress_error", False):
                self._dismiss_progress(force=True)
                return True
        if obj is getattr(self, "preview_tools", None) and event.type() == QEvent.Type.Resize:
            self._refresh_timecode_visibility()
        if obj in getattr(self, "_preview_tool_btns", ()):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
                self._recolor_preview_tool(obj, hover=event.type() == QEvent.Type.Enter)
        docks = (
            getattr(self, "dock_timeline", None),
            getattr(self, "dock_left", None),
            getattr(self, "dock_right", None),
        )
        if obj in docks and event.type() == QEvent.Type.Resize:
            if not getattr(self, "_applying_timeline_h", False):
                if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                    if obj is getattr(self, "dock_timeline", None):
                        self._timeline_h_user_set = True
                    self._schedule_persist_layout()
        return super().eventFilter(obj, event)

    def _refresh_quick_steps(self):
        btns = getattr(self, "_quick_step_btns", None)
        if not btns:
            return
        from cutora.ui.theme.apply import restyle
        has_media = bool(getattr(self, "tasks", None))
        has_subs = False
        for task in getattr(self, "tasks", None) or []:
            if task.get("segments"):
                has_subs = True
                break
        if not has_media:
            active = 0
        elif not has_subs:
            active = 1
        else:
            active = 2
        for i, btn in enumerate(btns):
            btn.setObjectName("btnPrimary" if i == active else "")
            restyle(btn)

    def _refresh_status_gpu(self):
        from cutora.core.config import gpu_status_text
        if hasattr(self, "_status_gpu"):
            self._status_gpu.setText(gpu_status_text())

    def _apply_doctor_findings(self, findings):
        from cutora.core.doctor import format_report
        from cutora.ui.i18n import tr
        items = list(findings or [])
        self._doctor_report = format_report(items)
        badge = getattr(self, "_status_doctor", None)
        if badge is None:
            return
        if not items:
            badge.hide()
            return
        n = len(items)
        badge.setText(tr("体检 %s") % n)
        badge.setToolTip(tr("点击查看启动体检"))
        badge.show()
        self.statusBar().showMessage(
            tr("体检发现 %s 项，点击右下角徽章查看") % n, 8000)

    def _show_doctor_report(self):
        from cutora.ui.i18n import tr
        text = getattr(self, "_doctor_report", "") or tr("体检无记录")
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("启动体检"))
        dlg.resize(560, 360)
        lay = QVBoxLayout(dlg)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(text)
        lay.addWidget(body, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(dlg.accept)
        lay.addWidget(box)
        dlg.exec()

    def _refresh_api_led(self):
        lab = getattr(self, "_status_api", None)
        if lab is None:
            return
        from cutora.ui.i18n import tr
        try:
            from cutora.api.lifecycle import get_server
            srv = get_server()
        except Exception:
            srv = None
        if srv is None or not srv.running:
            lab.setText(tr("代理关"))
            return
        n = 0
        try:
            n = int(srv.client_count())
        except Exception:
            n = 0
        if n > 0:
            lab.setText(tr("代理已连接"))
        else:
            lab.setText(tr("代理开启"))

    def _save_dock_state(self):
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings()
        s["dock_state"] = bytes(self.saveState().data()).hex()
        s["ui_mode"] = getattr(self, "_ui_mode", "quick")
        layout = self._layout_dict(s)
        if hasattr(self, "dock_timeline"):
            layout["timeline_h"] = int(self.dock_timeline.height())
        if hasattr(self, "dock_right"):
            layout["inspector_w"] = int(self.dock_right.width())
        if hasattr(self, "dock_left"):
            layout["bin_w"] = int(self.dock_left.width())
        s["layout"] = layout
        _save_app_settings(s)

    # --- Interaction Logic ---
    def update_ui_state(self):
        has_video = not self.media_player.source().isEmpty()
        reason = "请先导入视频"
        for btn in [self.btn_ai, self.btn_exp]:
            btn.setEnabled(has_video)
            btn.setToolTip("" if has_video else reason)
        self._set_batch_export_enabled(has_video, "" if has_video else reason)
        selected_count = len(self.file_list.selectedItems()) if has_video else 0
        src = getattr(self, "curr_idx", -1)
        targets = []
        if has_video and hasattr(self, "resolve_bin_targets"):
            targets = [i for i in self.resolve_bin_targets() if i != src]
        empty_tip = "请先勾选或选中目标视频"
        if hasattr(self, "act_copy_sub_style"):
            self.act_copy_sub_style.setEnabled(bool(targets))
            self.act_copy_sub_style.setToolTip("" if targets else empty_tip)
        if hasattr(self, "act_share_tracks"):
            self.act_share_tracks.setEnabled(bool(targets))
            self.act_share_tracks.setToolTip("" if targets else empty_tip)
        if hasattr(self, "menu_intro"):
            self.menu_intro.setEnabled(has_video and selected_count >= 1)
        if self.is_exporting:
            self.btn_exp_stop.setVisible(True)
            self.btn_exp_stop.setEnabled(True)
            self.btn_exp_stop.setToolTip("点击可立即停止当前导出任务")
        else:
            self.btn_exp_stop.setVisible(False)
            self.btn_exp_stop.setEnabled(False)
            self.btn_exp_stop.setToolTip("当前没有导出任务")
        self._refresh_quick_steps()

    def apply_top_icons(self):
        from cutora.ui.icons import bind_icon
        specs = (
            (self.btn_ai, "btn_match.svg", 18),
            (self.btn_exp, "btn_export.svg", 18),
            (self.btn_exp_stop, "btn_stop.svg", 18),
            (self.btn_skip_start, "skip-back", 18),
            (self.btn_start, "play", 18),
            (self.btn_skip_end, "skip-forward", 18),
            (self.btn_split_guides, "eye", 18),
            (self.btnAddText, "tool_text.svg", 18),
            (self.btnAddImage, "tool_image.svg", 18),
        )
        self._preview_icon_specs = {btn: (name, size) for btn, name, size in specs}
        for btn, name, size in specs:
            bind_icon(btn, name, size=size, color_token="fg1")
            btn.setFixedSize(32, 32)
            btn.setIconSize(QSize(18, 18))
        self._refresh_transport_play_icon()
        self._refresh_timecode()
        guides = getattr(self, "btn_split_guides", None)
        if guides is not None and guides.isChecked():
            self._recolor_preview_tool(guides, hover=False)

    def _format_transport_tc(self, ms, with_hours=False):
        """Format milliseconds as ``HH:MM:SS.cc`` or ``MM:SS.cc``."""
        ms = max(0, int(ms or 0))
        cs = (ms % 1000) // 10
        tot_s = ms // 1000
        s = tot_s % 60
        m = (tot_s // 60) % 60
        h = tot_s // 3600
        if with_hours or h > 0:
            return "%02d:%02d:%02d.%02d" % (h, m, s, cs)
        return "%02d:%02d.%02d" % (m, s, cs)

    def _transport_duration_ms(self):
        dur = 0
        tl = getattr(self, "timeline", None)
        if tl is not None:
            dur = int(getattr(tl, "duration", 0) or 0)
        if dur <= 0:
            mp = getattr(self, "media_player", None)
            if mp is not None:
                dur = int(mp.duration() or 0)
        return max(0, dur)

    def _refresh_timecode(self):
        lbl = getattr(self, "lbl_timecode", None)
        if lbl is None:
            return
        cur = int(getattr(self, "_timeline_ms", 0) or 0)
        tot = self._transport_duration_ms()
        lbl.setText("%s / %s" % (
            self._format_transport_tc(cur, with_hours=True),
            self._format_transport_tc(tot, with_hours=tot >= 3600000),
        ))
        self._refresh_timecode_visibility()

    def _refresh_timecode_visibility(self):
        lbl = getattr(self, "lbl_timecode", None)
        if lbl is None:
            return
        win_w = int(self.width() or 0)
        bar = getattr(self, "preview_tools", None)
        bar_w = int(bar.width() or 0) if bar is not None else win_w
        # 任务书：窗口 900px 藏时间码。左右 Dock 把最小窗口钉在 1128
        # （左 164 + 右 300 + 分隔），运输条在这一档是 648。旧阈值 640
        # 刚好低于这 648，分屏参考线按钮加上之后时间码再也藏不住。
        # 1600 宽时运输条是 1120，时间码仍显示。宽窗口上的最小宽度没改。
        lbl.setVisible(win_w > 900 and bar_w >= 660)

    def _refresh_transport_play_icon(self):
        from cutora.ui.i18n import tr as _tr
        from cutora.ui.icons import bind_icon
        btn = getattr(self, "btn_start", None)
        if btn is None:
            return
        playing = bool(getattr(self, "_timeline_playing", False))
        name = "pause" if playing else "play"
        specs = getattr(self, "_preview_icon_specs", None)
        if isinstance(specs, dict) and btn in specs:
            _, size = specs[btn]
            specs[btn] = (name, size)
        else:
            size = 18
        bind_icon(btn, name, size=size, color_token="fg1")
        btn.setToolTip(_tr("暂停") if playing else _tr("播放"))

    def _recolor_preview_tool(self, btn, hover: bool):
        from cutora.ui.icons import icon
        from cutora.ui.theme.tokens import current
        spec = getattr(self, "_preview_icon_specs", {}).get(btn)
        if not spec:
            return
        name, size = spec
        checked = bool(btn.isCheckable() and btn.isChecked())
        token = "accent" if (hover or checked) else "fg1"
        btn.setIcon(icon(name, color=getattr(current(), token), size=size))

    def _set_batch_export_text(self, text):
        act = getattr(self, "act_bexp", None)
        if act is not None:
            act.setText(text)

    def _set_batch_export_enabled(self, enabled, tip=None):
        act = getattr(self, "act_bexp", None)
        if act is None:
            return
        act.setEnabled(bool(enabled))
        if tip is not None:
            act.setToolTip(tip)

    def _cancel_progress_hide(self):
        timer = getattr(self, "_progress_hide_timer", None)
        if timer is not None:
            timer.stop()

    def _dismiss_progress(self, force=False):
        self._cancel_progress_hide()
        if force:
            self._progress_error = False
        elif getattr(self, "_progress_error", False):
            return
        if hasattr(self, "btn_prog_cancel"):
            self.btn_prog_cancel.hide()
        if hasattr(self, "prog_frame"):
            self.prog_frame.hide()

    def _begin_progress(self):
        self._cancel_progress_hide()
        self._progress_error = False
        if hasattr(self, "prog_frame"):
            self.prog_frame.show()

    def _style_progress(self, mode):
        from cutora.ui.theme.apply import restyle
        if hasattr(self, "prog_main"):
            self.prog_main.setProperty("mode", mode)
            restyle(self.prog_main)
        if hasattr(self, "prog_label"):
            self.prog_label.setProperty("mode", mode)
            restyle(self.prog_label)

    def show_progress_error(self, text):
        self._cancel_progress_hide()
        self._progress_error = True
        self._style_progress("err")
        if hasattr(self, "prog_label"):
            self.prog_label.setText(text)
        if hasattr(self, "prog_frame"):
            self.prog_frame.show()
            self.prog_frame.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_progress_mode(self, mode: str):
        if not hasattr(self, "prog_main"):
            return
        if mode == "export":
            self._progress_mode = "export"
        elif mode == "bgm":
            self._progress_mode = "bgm"
        elif mode == "open":
            self._progress_mode = "open"
        else:
            self._progress_mode = "sub"
        self._begin_progress()
        style = "sub" if self._progress_mode == "open" else self._progress_mode
        self._style_progress(style)
        if hasattr(self, "prog_frame"):
            self.prog_frame.setCursor(Qt.CursorShape.ArrowCursor)

    def _open_yield(self):
        if not getattr(self, "_opening_project", False):
            return
        from cutora.media.probe_cache import pump_ui
        pump_ui()

    def _begin_open_progress(self):
        self._opening_project = True
        self._open_frame_scheduled = False
        self.set_progress_mode("open")
        if hasattr(self, "prog_main"):
            self.prog_main.setValue(5)
        if hasattr(self, "prog_label"):
            self.prog_label.setText("正在打开工程…")
        self._open_yield()

    def _tick_open_progress(self, pct, msg=None):
        if not getattr(self, "_opening_project", False):
            return
        if hasattr(self, "prog_main"):
            self.prog_main.setValue(max(0, min(99, int(pct))))
        if msg and hasattr(self, "prog_label"):
            self.prog_label.setText(msg)
        self._open_yield()

    def _end_open_progress(self):
        was = bool(getattr(self, "_opening_project", False))
        self._opening_project = False
        if was and hasattr(self, "prog_main"):
            self.update_progress_value(100)
        if was:
            QTimer.singleShot(0, self._present_open_preview)
            QTimer.singleShot(0, self._flush_open_waveforms)
            QTimer.singleShot(0, self._fill_library_thumbs)
            QTimer.singleShot(0, self._queue_open_thumbs)

    def _queue_open_thumbs(self):
        tl = getattr(self, "timeline", None)
        queue = getattr(tl, "_queue_visible_thumbs", None) if tl is not None else None
        if callable(queue):
            queue()

    def _fill_library_thumbs(self):
        from PySide6.QtGui import QIcon
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        fl = getattr(self, "file_list", None)
        if fl is None:
            return
        for i, task in enumerate(self.tasks or []):
            item = fl.item(i)
            path = MainWindow._library_thumb_path(task)
            if item is None or not path or not os.path.isfile(path):
                continue

            def _apply(_k="", it=item, p=path):
                hit = peek_clip_thumb(p, 0.1, 64, 48)
                if not hit:
                    return
                try:
                    it.setIcon(0, QIcon(hit))
                except RuntimeError:
                    pass        # 缩略图回来时这一行已经被重建 / 删掉了（C++ 对象没了），新行会自己再要

            if peek_clip_thumb(path, 0.1, 64, 48):
                _apply()
            else:
                request_clip_thumb(path, 0.1, 64, 48, on_done=_apply)

    def _flush_open_waveforms(self):
        pending = list(getattr(self, "_pending_open_waveforms", None) or [])
        self._pending_open_waveforms = pending[4:]
        for sid, path in pending[:4]:
            self._trigger_waveform_generation(sid, path)
        if getattr(self, "_pending_open_waveforms", None):
            QTimer.singleShot(0, self._flush_open_waveforms)

    def _keep_bg_sig(self, sig):
        sigs = getattr(self, "_inflight_bg_sigs", None)
        if sigs is None:
            sigs = self._inflight_bg_sigs = []
        sigs.append(sig)

    def _drop_bg_sig(self, sig):
        sigs = getattr(self, "_inflight_bg_sigs", None)
        if sigs is not None:
            try:
                sigs.remove(sig)
            except ValueError:
                pass

    def _present_open_preview(self):
        """Show the canvas immediately; decode the first frame off the GUI thread."""
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return
        task = self.tasks[self.curr_idx]
        t = float(getattr(self, "_timeline_ms", 0) or 0) / 1000.0
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
        from PySide6.QtGui import QImage
        # 1×1 plate. A full-canvas empty QImage (1080×1920) is an 8 MB GUI-thread
        # blit and is the Q71/Q54 200 ms stall. The worker replaces this.
        placeholder = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        placeholder.fill(0)
        setter = getattr(self.video_canvas, "set_program_frame", None)
        if callable(setter):
            setter(placeholder)
        self.video_canvas.set_data(task, t)
        worker = getattr(self, "_preview_worker", None)
        if worker is not None and worker.isRunning():
            self._present_preview_frame()
            return

        class _Sig(QObject):
            done = Signal(object)

        class _Job(QRunnable):
            def __init__(self, snap, tt, sig):
                super().__init__()
                self._snap, self._t, self._sig = snap, tt, sig
                self.setAutoDelete(True)

            def run(self):
                img = None
                sig_ref = self._sig
                try:
                    from cutora.preview.engine import PreviewEngine
                    eng = PreviewEngine()
                    img = eng.video_frame_at(self._snap, self._t)
                    eng.close()
                except Exception:
                    img = None
                finally:
                    self._sig = None
                try:
                    if sig_ref is not None:
                        sig_ref.done.emit(img)
                except RuntimeError:
                    pass

        sig = _Sig()
        self._open_preview_sig = sig
        self._keep_bg_sig(sig)

        def _ok(img, _s=sig):
            self._drop_bg_sig(_s)
            if img is None:
                return
            if not (0 <= self.curr_idx < len(self.tasks or [])):
                return
            if self.tasks[self.curr_idx] is not task:
                return
            fn = getattr(self.video_canvas, "set_program_frame", None)
            if callable(fn):
                fn(img)

        sig.done.connect(_ok)
        QThreadPool.globalInstance().start(_Job(task, t, sig))

    def update_progress_value(self, value: int):
        value = int(value)
        if hasattr(self, "prog_main"):
            self.prog_main.setValue(max(0, min(100, value)))
        if value >= 100:
            self._begin_progress()
            if hasattr(self, "prog_label"):
                self.prog_label.setText("完成")
            self._cancel_progress_hide()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._dismiss_progress)
            self._progress_hide_timer = timer
            timer.start(1500)
            return
        self._begin_progress()
        if not hasattr(self, "prog_label"):
            return
        mode = getattr(self, "_progress_mode", "sub")
        if mode == "export" and getattr(self, "is_batch_exporting", False) and getattr(self, "_batch_export_total", 0) > 0:
            title = "正在导出… %d/%d" % (
                max(1, int(getattr(self, "_batch_export_current", 1))),
                int(self._batch_export_total),
            )
        elif mode == "export":
            title = "正在导出…"
        elif mode == "bgm":
            title = "正在导入配乐…"
        elif mode == "open":
            title = "正在打开工程…"
        else:
            title = "正在识别…"
        self.prog_label.setText("%s %d%%" % (title, value))

    def _preview_target_fps(self) -> float:
        try:
            if not (0 <= self.curr_idx < len(self.tasks or [])):
                return 30.0
            task = self.tasks[self.curr_idx]
            tl = task.get("timeline") if isinstance(task, dict) else None
            raw = getattr(tl, "fps", None) if tl is not None else None
            if not raw and isinstance(task, dict):
                raw = task.get("fps")
            fps = float(raw or 30.0)
            return fps if fps > 0 else 30.0
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("preview target fps")
            return 30.0

    def _fps_meter_clear(self):
        meter = getattr(self, "_fps_meter", None)
        if meter is not None:
            meter.clear()

    def _on_export_fps(self, fps: float, done: int, total: int):
        meter = getattr(self, "_fps_meter", None)
        if meter is None:
            return
        try:
            meter.set_export(float(fps), int(done), int(total))
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("export fps 刷新失败")

    def _refresh_fps_meter(self):
        from cutora.core.fps_meter import (
            preview_fps,
            preview_ring_count,
            set_preview_counting,
        )
        playing = bool(getattr(self, "_timeline_playing", False))
        set_preview_counting(playing)
        meter = getattr(self, "_fps_meter", None)
        if meter is None:
            return
        if getattr(self, "is_exporting", False):
            return
        if playing:
            fps = preview_fps()
            n = preview_ring_count()
            if fps > 0 and n >= 2:
                try:
                    meter.set_preview(fps, self._preview_target_fps(), n=n)
                except Exception:
                    from cutora.core.log import get_logger
                    get_logger("cutora.ui.main_window").exception("preview fps 刷新失败")
                return
        if getattr(meter, "_mode", "") == "preview":
            meter.clear()

    def set_mode(self, mode):
        # T08a: fast 模式无 UI/命令入口，只关抗锯齿、不改编码器。产品路径只保留 quality。
        self.render_mode = "quality"

    def _ensure_timeline_template(self):
        if self._timeline_template:
            return self._timeline_template
        from cutora.model.canvas import default_new_canvas_wh
        w, h = default_new_canvas_wh()
        self._timeline_template = {
            "width": int(w), "height": int(h), "fps": DEFAULT_TIMELINE_FPS,
        }
        return self._timeline_template

    def _probe_source_wh(self, path):
        """素材宽高（ffprobe）。Q170：按（路径, 大小, 修改时间）记住 —— 打开工程时不再每个素材都起一次 ffprobe。"""
        key = _source_wh_key(path)
        if key is not None and key in _SOURCE_WH_CACHE:
            return _SOURCE_WH_CACHE[key]
        wh = None
        try:
            from cutora.render.readers import probe_bitrates
            info = probe_bitrates(path) or {}
            w, h = int(info.get("width") or 0), int(info.get("height") or 0)
            if w > 0 and h > 0:
                wh = (w, h)
        except Exception:
            pass
        if key is not None and wh is not None:
            _SOURCE_WH_CACHE[key] = wh
        return wh

    def _probe_sources_wh(self, paths):
        """一批素材宽高：去重、并行 ffprobe，等的时候让界面照常刷新（Q170：以前串行、卡界面 1~2 秒）。"""
        uniq = list(dict.fromkeys(str(p) for p in paths if p))
        todo = [p for p in uniq if _source_wh_key(p) not in _SOURCE_WH_CACHE]
        if len(todo) > 1:
            import time as _time
            from concurrent.futures import ThreadPoolExecutor, wait
            from cutora.media.probe_cache import pump_ui
            with ThreadPoolExecutor(max_workers=min(6, len(todo)), thread_name_prefix="cutora-wh") as ex:
                futs = [ex.submit(self._probe_source_wh, p) for p in todo]
                while True:
                    done, pending = wait(futs, timeout=0.03)
                    if not pending:
                        break
                    pump_ui()
                    _time.sleep(0)
        return {p: self._probe_source_wh(p) for p in uniq}

    def _apply_canvas_from_first_source(self, path):
        wh = self._probe_source_wh(path)
        if not wh:
            return
        w, h = wh
        tpl = self._ensure_timeline_template()
        tpl["width"] = int(w)
        tpl["height"] = int(h)

    def _canvas_prompt_is_headless(self) -> bool:
        plat = (os.environ.get("QT_QPA_PLATFORM") or "").lower()
        if plat == "offscreen":
            return True
        src = str(getattr(self, "_bus_source", "") or "ui").lower()
        return src in ("api", "cli", "mcp", "recipe")

    def _prompt_canvas_if_sources_mismatch(self):
        if getattr(self, "_canvas_mismatch_asked", False):
            return
        paths = []
        for task in self.tasks or []:
            tl = task.get("timeline") if isinstance(task, dict) else None
            sources = getattr(tl, "sources", None) if tl is not None else None
            if isinstance(tl, dict):
                sources = tl.get("sources")
            if not sources:
                continue
            items = sources.values() if isinstance(sources, dict) else sources
            for src in items:
                path = getattr(src, "path", None) if not isinstance(src, dict) else src.get("path")
                if path:
                    paths.append(path)
        whs = self._probe_sources_wh(paths) if paths else {}
        sizes = [whs.get(str(p)) for p in paths]
        sizes = [wh for wh in sizes if wh]
        if not sizes:
            return
        if len(set(sizes)) != 1:
            return
        src_w, src_h = sizes[0]
        tpl = self._ensure_timeline_template()
        cw, ch = int(tpl.get("width") or 0), int(tpl.get("height") or 0)
        if (src_w, src_h) == (cw, ch):
            return
        self._canvas_mismatch_asked = True
        from cutora.core.log import get_logger
        log = get_logger("cutora.ui.main_window")
        if self._canvas_prompt_is_headless():
            log.info(
                "canvas mismatch source=%s platform=%s src=%dx%d canvas=%dx%d; keeping canvas",
                getattr(self, "_bus_source", "ui"),
                os.environ.get("QT_QPA_PLATFORM") or "",
                src_w, src_h, cw, ch,
            )
            return
        reply = QMessageBox.question(
            self,
            "画布尺寸",
            "素材是 %dx%d，当前画布是 %dx%d。要改成素材尺寸吗？" % (src_w, src_h, cw, ch),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._set_canvas_size(src_w, src_h)
        else:
            log.info(
                "canvas mismatch src=%dx%d canvas=%dx%d; user kept canvas",
                src_w, src_h, cw, ch,
            )
        
    def _set_canvas_size(self, w, h):
        from cutora.model.canvas import apply_canvas
        has_task = 0 <= getattr(self, "curr_idx", -1) < len(self.tasks or [])
        if has_task:
            self.push_undo(f"修改画布尺寸 {w}x{h}")
        if not self._timeline_template:
            self._timeline_template = {"width": w, "height": h, "fps": DEFAULT_TIMELINE_FPS}
        else:
            self._timeline_template["width"] = w
            self._timeline_template["height"] = h
        for task in self.tasks or []:
            apply_canvas(task, w, h)
        self.statusBar().showMessage(f"画布尺寸已修改为：{w}x{h}", 3000)
        if has_task:
            self.refresh_ui()

    def _prompt_custom_canvas(self):
        default_str = "1080x1920"
        if self._timeline_template:
            default_str = f"{self._timeline_template.get('width', 1080)}x{self._timeline_template.get('height', 1920)}"
        text, ok = QInputDialog.getText(self, "自定义画布", "请输入尺寸 (例如 1080x1920):", text=default_str)
        if ok and text:
            parts = text.lower().split('x')
            if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                w, h = int(parts[0].strip()), int(parts[1].strip())
                self._set_canvas_size(w, h)
            else:
                QMessageBox.warning(self, "格式错误", "请输入有效的尺寸格式，例如 1080x1920")

    def _set_fit_mode(self, mode):
        """切换视频适配模式（菜单回调）"""
        self._current_fit_mode = mode
        _LABEL_MAP = {"fit": "适配", "fill": "填满", "stretch": "拉伸"}
        self.statusBar().showMessage(f"视频适配模式已切换为：{_LABEL_MAP.get(mode, mode)}", 3000)

    def _trigger_waveform_generation(self, source_id, media_path):
        """异步触发波形生成（不阻塞 UI）。"""
        from cutora.audio.waveform import file_fingerprint, get_waveform_cache
        cache = get_waveform_cache()
        fp = file_fingerprint(media_path) if media_path else None
        rec = None
        with cache._lock:
            rec = cache._cache.get(source_id)
        if rec and rec.get("fp") == fp and rec.get("lod"):
            return
        if rec and rec.get("fp") != fp:
            cache.invalidate(source_id)
        if cache.is_pending(source_id):
            return
        cache.set_pending(source_id)
        worker = _WaveformWorker(source_id, media_path)
        worker.finished_signal.connect(self._on_waveform_ready)
        worker.start()
        # 保持引用防止被 GC
        if not hasattr(self, '_waveform_workers'):
            self._waveform_workers = []
        self._waveform_workers.append(worker)

    def _on_waveform_ready(self, source_id):
        """波形生成完成回调 — 刷新时间线 UI。"""
        from cutora.audio.waveform import get_waveform_cache
        cache = get_waveform_cache()
        cache.clear_pending(source_id)
        # 清理已完成的 worker
        if hasattr(self, '_waveform_workers'):
            self._waveform_workers = [w for w in self._waveform_workers if w.isRunning()]
        # 刷新时间线
        if hasattr(self, 'timeline') and self.timeline is not None:
            self.timeline.update()

    def place_asset_on_timeline(self, item):
        """Drop a library asset onto the current playhead."""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(self, "提示", "请先导入一个视频文件")
            return
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None:
            return
        start = max(0.0, float(self.timeline.position) / 1000.0) if hasattr(self, "timeline") else 0.0
        from cutora.assets.user import place_on_timeline
        from cutora.media.animated import (
            classify_animated, persistent_anim_dir,
            report_animated_import_error, start_animated_import,
        )
        item = dict(item)
        if getattr(self, "project_path", None):
            item["_project_dir"] = os.path.dirname(os.path.abspath(self.project_path))
        src_path = item.get("source_path") or item.get("stored_path") or ""
        if item.get("type") == "animated" and classify_animated(src_path) == "gif":
            def _placed(info, err, _self=self, _item=item, _start=start, _task=task, _tl=tl):
                if err:
                    report_animated_import_error(_self, err)
                    return
                try:
                    _self.push_undo()
                    place_on_timeline(_tl, _item, start_t=_start, task=_task)
                except Exception as exc:
                    from cutora.core.log import get_logger
                    get_logger("cutora.ui.main_window").exception("place gif asset failed")
                    try:
                        _self.statusBar().showMessage("无法把资产放到时间线：%s" % exc, 5000)
                    except Exception:
                        get_logger("cutora.ui.main_window").exception("statusBar failed")
                    return
                _self.timeline.set_data(_self.timeline.duration, _task)
                _self.timeline.update()
                _self.video_canvas.update()
            start_animated_import(
                src_path, fps=getattr(tl, "fps", None),
                on_done=_placed, parent=self if isinstance(self, QWidget) else None,
                dest_dir=persistent_anim_dir(
                    item.get("_project_dir"),
                    item.get("storage_mode") or "library"))
            return
        try:
            self.push_undo()
            place_on_timeline(tl, item, start_t=start, task=task)
        except Exception as exc:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("place asset failed")
            try:
                self.statusBar().showMessage("无法把资产放到时间线：%s" % exc, 5000)
            except Exception:
                get_logger("cutora.ui.main_window").exception("statusBar failed")
            return
        self.timeline.set_data(self.timeline.duration, task)
        self.timeline.update()
        self.video_canvas.update()

    def _handle_timeline_drop(self, path, time_sec, domain,
                              push_undo=True, target_track=_DROP_TARGET_FROM_WIDGET):
        """把一个外部文件放到时间线上，返回实际占用的时长（秒）。

        ``target_track`` 不传时沿用老行为：从 timeline 上取本次落点轨道。
        批量拖放会自己算好轨道和是否要压撤销，所以显式传进来。
        """
        """处理从文件系统拖拽到时间线上的素材。
        根据文件类型自动创建对应的 Track + Clip。"""
        if not os.path.exists(path):
            return

        # 必须先有项目（timeline template）—— 没有则直接弹尺寸选择
        if not self._timeline_template:
            if not self._ensure_timeline_template():
                return

        # 必须先有当前 task
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(self, "提示", "请先导入一个视频文件（拖到左侧文件列表）")
            return

        ext = os.path.splitext(path)[1].lower()
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None:
            return

        # 获取拖拽落点的目标 Track
        if target_track is _DROP_TARGET_FROM_WIDGET:
            target_track = getattr(self.timeline, '_last_drop_target_track', None)
            self.timeline._last_drop_target_track = None  # 清除

        if push_undo:
            self.push_undo()

        from cutora.ui.timeline_drop import SUB_EXT, TEXT_EXT
        if ext in SUB_EXT:
            return self._drop_subtitle_file(path)
        if ext in TEXT_EXT:
            placed = self._drop_text_file(task, tl, path, time_sec,
                                          target_track=target_track)
            if not getattr(self, "_suppress_drop_refresh", False):
                self.timeline.set_data(self.timeline.duration, task)
                self.timeline.update()
                self.video_canvas.update()
            return placed

        from cutora.media.animated import (
            classify_animated, report_animated_import_error, start_animated_import,
            try_import_animated,
        )
        fps = getattr(tl, "fps", None)
        kind = classify_animated(path)
        if kind == "gif":
            MainWindow._start_gif_timeline_import(
                self, path, time_sec, target_track, task, tl, fps)
            # GIF 是后台解码的，这里还不知道真时长；批量拖放先按 5 秒往后排，
            # 解完再落到回调里给的位置。
            return 5.0

        loop = False
        info, anim_err = try_import_animated(path, fps=fps)
        if anim_err:
            report_animated_import_error(self, anim_err)
            return
        if info:
            path = info["path"]
            ext = os.path.splitext(path)[1].lower()
            loop = True

        placed = 0.0
        if ext in AUD_EXT:
            placed = self._drop_audio_file(
                task, tl, path, time_sec, target_track=target_track)
        elif (not loop) and ext in IMG_EXT:
            placed = self._drop_image_file(
                task, tl, path, time_sec, target_track=target_track)
        elif ext in VID_EXT or loop:
            placed = self._drop_video_file(
                task, tl, path, time_sec, target_track=target_track, loop=loop)

        if not getattr(self, "_suppress_drop_refresh", False):
            self.timeline.set_data(self.timeline.duration, task)
            self.timeline.update()
            self.video_canvas.update()
        return float(placed or 0.0)

    def _start_gif_timeline_import(self, path, time_sec, target_track, task, tl, fps):
        """Decode a GIF off the UI thread, then drop it like a video clip."""
        from cutora.media.animated import report_animated_import_error, start_animated_import

        def _progress(done, total, _self=self, _name=os.path.basename(path)):
            try:
                bar = _self.statusBar()
                if total:
                    bar.showMessage("正在解码 GIF %s（%d/%d）" % (_name, done, total), 2000)
                else:
                    bar.showMessage("正在解码 GIF %s" % _name, 2000)
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").exception("gif progress statusBar")

        def _done(info, err, _self=self, _time=time_sec, _track=target_track,
                  _task=task, _tl=tl):
            from cutora.media.animated import report_animated_import_error
            if err:
                report_animated_import_error(_self, err)
                return
            if not info:
                _self._drop_image_file(_task, _tl, path, _time, target_track=_track)
            else:
                dest = info["path"]
                _self._drop_video_file(
                    _task, _tl, dest, _time, target_track=_track, loop=True)
            try:
                _self.timeline.set_data(_self.timeline.duration, _task)
                _self.timeline.update()
                _self.video_canvas.update()
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").exception("gif drop refresh")

        start_animated_import(
            path, fps=fps, on_done=_done, on_progress=_progress, parent=self
            if isinstance(self, QWidget) else None)

    def add_adjust_item(self, track=None, start=None, dur=5.0, new_track=False):
        """Q174：在调整轨上（播放头处）建一块纯色矩形。没给轨道就用 / 建一条调整轨（new_track=True：一定新建）。
        一步撤销。返回新覆层。"""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(self, "提示", "请先导入一个视频文件")
            return None
        from cutora.render.adjust_layer import adjust_defaults
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None:
            return None
        if track is None and not new_track:
            track = next((t for t in reversed(tl.tracks) if getattr(t, "type", "") == "adjust"
                          and not getattr(t, "locked", False)), None)
        start_t = max(0.0, (self.timeline.position / 1000.0) if start is None else float(start))
        tw = int(tl.width or task.get('dims', (1080, 1920))[0])
        th = int(tl.height or task.get('dims', (1080, 1920))[1])
        self.push_undo("添加调整图层")
        if track is None:
            track = Track("adjust")
            tl.add_track(track)
        ov = adjust_defaults(tw, th, start=start_t, end=start_t + float(dur), track_id=getattr(track, "id", None))
        ov["id"] = int(time.time() * 1000)
        task.setdefault("image_overlays", []).append(ov)
        self.capture_undo_after()
        self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        self._reveal_timeline_track(track)
        self.video_canvas.update()
        try:
            idx = len(task["image_overlays"]) - 1
            self.video_canvas.item_selected.emit("overlay", ("image_overlays", idx))
        except Exception:
            pass
        self.statusBar().showMessage(
            "已在调整轨道建了一块纯色矩形（%.1fs ~ %.1fs）。右侧「调整图层」里可以关掉填充、打开效果" % (
                start_t, start_t + float(dur)), 5000)
        return ov

    def _reveal_timeline_track(self, track):
        """新建的轨道在可见区外面（往下滚才看得到）→ 把时间线滚过去。"""
        cont = getattr(self, "timeline_container", None)
        tlw = getattr(self, "timeline", None)
        if cont is None or tlw is None:
            return
        try:
            cont.update_scroll_range()
            for tr_obj, row_y, row_h in tlw._track_positions()["rows"]:
                if tr_obj is not track:
                    continue
                top = tlw.RULER_H + tlw.TOP_PAD
                bottom = tlw.height() - tlw.BOTTOM_PAD
                if row_y < top or row_y + row_h > bottom:
                    sb = cont.v_scrollbar
                    want = int(tlw.scroll_y + (row_y + row_h - bottom) + 8) if row_y + row_h > bottom \
                        else int(tlw.scroll_y - (top - row_y))
                    sb.setValue(max(sb.minimum(), min(sb.maximum(), want)))
                    tlw.set_scroll_y(sb.value())
                break
        except Exception:
            pass

    def _on_import_to_track(self, track):
        """轨道头"+"按钮：根据轨道类型打开对应文件选择器，导入素材到指定 Track。"""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(self, "提示", "请先导入一个视频文件")
            return

        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None:
            return

        t_type = getattr(track, "type", "")
        # 按轨道类型决定文件过滤器
        _FILTERS = {
            "video": "视频文件 (*.mp4 *.mov *.mkv *.webm);;所有文件 (*)",
            "image": "图片文件 (*.png *.jpg *.jpeg *.webp *.svg *.gif);;所有文件 (*)",
            "audio": "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a *.wma);;所有文件 (*)",
            "text":  None,  # 文本轨道用输入框
            "subs":  None,  # 字幕轨道不支持
        }
        filt = _FILTERS.get(t_type)
        if t_type == "adjust":
            self.add_adjust_item(track)
            return
        if filt is None:
            if t_type == "text":
                # 文本轨道：在播放头位置建一条新文本。Q124：和 Ctrl+T 同一份默认
                # （框贴着文字、居中、不锁比例、高度自动），不再手写整画布的框。
                from cutora.model.factory_defaults import overlay_defaults
                start_t = max(0.0, self.timeline.position / 1000.0)
                dur = 5.0  # 默认 5 秒
                tl = _tl_of(task)
                tw = int(tl.width if tl else task.get('dims', (1080, 1920))[0])
                th = int(tl.height if tl else task.get('dims', (1080, 1920))[1])
                new_ov = overlay_defaults(
                    "text", canvas_w=tw, canvas_h=th,
                    start=start_t, end=start_t + dur,
                    track_id=getattr(track, "id", None))
                new_ov['font_family'] = task.get('style', {}).get(
                    'font_family', new_ov.get('font_family', 'Arimo'))
                new_ov['id'] = int(time.time() * 1000)
                from cutora.subtitle.text_layer import ensure_overlay_fx
                ensure_overlay_fx(new_ov)
                task.setdefault('text_overlays', []).append(new_ov)
                self.push_undo()
                self.timeline.update()
                self.video_canvas.update()
                self.statusBar().showMessage(f"已在文本轨道创建新文本（{start_t:.1f}s ~ {start_t+dur:.1f}s）", 3000)
            return

        path, _ = QFileDialog.getOpenFileName(
            self, f"导入素材到 {t_type.capitalize()} 轨道", "", filt
        )
        if not path or not os.path.exists(path):
            return

        # 使用播放头位置作为 start_t
        start_t = max(0.0, self.timeline.position / 1000.0)

        self.push_undo()

        if t_type == "video":
            self._drop_video_file(task, tl, path, start_t, target_track=track)
        elif t_type == "image":
            self._drop_image_file(task, tl, path, start_t, target_track=track)
        elif t_type == "audio":
            self._drop_audio_file(task, tl, path, start_t, target_track=track)

        # 刷新 UI
        self.timeline.set_data(self.timeline.duration, task)
        self.timeline.update()
        self.video_canvas.update()
        self.statusBar().showMessage(f"已导入素材到 {t_type.capitalize()} 轨道", 3000)

    # ------------------------------------------------------------------
    # Q115：文本 / 字幕也能直接拖进来
    # ------------------------------------------------------------------
    def _drop_text_file(self, task, tl, path, start_t, target_track=None):
        """拖 .txt / .md → 在文本轨道上生成一个文本 overlay，内容就是文件里的字。"""
        from cutora.model.factory_defaults import overlay_defaults
        from cutora.ui.timeline_drop import read_drop_text
        body = read_drop_text(path)
        if not body:
            self.statusBar().showMessage(
                "这个文本文件是空的或读不出来：%s" % os.path.basename(path), 4000)
            return 0.0
        dur = 5.0
        main_dur = _get_main_video_duration(task)
        if isinstance(main_dur, (int, float)) and main_dur > 0:
            remaining = max(0.0, float(main_dur) - float(start_t))
            if remaining <= 0.0:
                self.statusBar().showMessage("导入位置已超出主视频时长，未添加素材", 3000)
                return 0.0
            dur = min(dur, remaining)
        if target_track is None or getattr(target_track, "type", None) != "text":
            target_track = get_or_create_track(tl, "text")
        tw = int(getattr(tl, "width", 0) or (task.get('dims') or (1080, 1920))[0])
        th = int(getattr(tl, "height", 0) or (task.get('dims') or (1080, 1920))[1])
        # Q124：和 Ctrl+T 走同一份默认（框贴着文字、不锁比例、高度自动），
        # 不再在这里手写一份整画布的框。
        ov = overlay_defaults(
            "text", canvas_w=tw, canvas_h=th,
            start=float(start_t), end=float(start_t) + dur,
            track_id=getattr(target_track, "id", None))
        ov["content"] = body
        ov["font_family"] = (task.get('style') or {}).get('font_family', ov.get("font_family", "Arimo"))
        ov["id"] = int(time.time() * 1000)
        from cutora.subtitle.text_layer import ensure_overlay_fx
        ensure_overlay_fx(ov)
        task.setdefault('text_overlays', []).append(ov)
        self.statusBar().showMessage(
            "已添加文本: %s" % os.path.basename(path), 3000)
        return float(dur)

    def _drop_subtitle_file(self, path):
        """拖 .srt/.vtt/.ass/.ssa → 追加到当前视频的字幕里（不覆盖已有的）。"""
        from cutora.core.commands import CommandError
        params = {"path": path, "mode": "append"}
        if self.curr_idx >= 0:
            params["media_id"] = int(self.curr_idx)
        try:
            self.bus.execute("subtitle.import_file", params, source="ui")
        except CommandError as exc:
            self.statusBar().showMessage(
                "字幕导入失败：%s" % exc, 5000)
            return 0.0
        self.statusBar().showMessage(
            "已把字幕追加进来: %s" % os.path.basename(path), 3000)
        return 0.0

    def _handle_timeline_drop_batch(self, payload):
        """Q115：一次拖一批文件进时间线。

        规则：落在收得下这类素材的轨道上就用那条；落在所有轨道下面的空白处
        就新开一条；一批文件按文件名顺序、首尾相接往后排，不会叠在同一秒。
        """
        from cutora.ui.timeline_drop import (
            accepts, kind_for_path, track_display_name, track_type_for_kind,
        )
        payload = payload or {}
        paths = [p for p in (payload.get("paths") or []) if p]
        if not paths:
            return
        if not self._timeline_template:
            if not self._ensure_timeline_template():
                return
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(
                self, "提示", "请先导入一个视频文件（拖到左侧文件列表）")
            return
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None:
            return

        drop_track = payload.get("track")
        want_new = bool(payload.get("new_track"))
        start_t = max(0.0, float(payload.get("time") or 0.0))

        self.push_undo()
        self._active_drop_batch = {
            "origin": start_t,
            "task": task,
            "slots": [],
        }
        self._suppress_drop_refresh = True
        fresh = {}          # 轨道类型 -> 这一批里新开的那条轨道
        placed_names = []
        skipped = []
        try:
            for path in paths:
                if not os.path.exists(path):
                    skipped.append(os.path.basename(path))
                    continue
                kind = kind_for_path(path)
                if kind is None:
                    skipped.append(os.path.basename(path))
                    continue
                if kind == "subs":
                    self._handle_timeline_drop(
                        path, start_t, "visual", push_undo=False, target_track=None)
                    placed_names.append(os.path.basename(path))
                    continue
                t_type = track_type_for_kind(kind)
                track = drop_track if accepts(drop_track, kind) else None
                if track is None and (want_new or drop_track is not None):
                    track = fresh.get(t_type)
                    if track is None:
                        track = self.add_timeline_track(t_type)
                        fresh[t_type] = track
                domain = "audio" if kind == "audio" else "visual"
                dur = self._handle_timeline_drop(
                    path, start_t, domain, push_undo=False, target_track=track)
                if dur is None:
                    dur = 0.0
                placed_names.append(os.path.basename(path))
                start_t += max(0.0, float(dur))
        finally:
            self._suppress_drop_refresh = False
            self._active_drop_batch = None
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        # One refresh for the whole batch. refresh_ui already calls set_data.
        if hasattr(self, "refresh_ui"):
            self.refresh_ui()
        elif hasattr(self, "timeline"):
            self.timeline.set_data(self.timeline.duration, task)
            self.timeline.update()
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()

        if placed_names and not skipped:
            if len(placed_names) == 1:
                msg = "已导入 %s" % placed_names[0]
            else:
                msg = "已导入 %d 个素材，依次排在 %.1fs 之后" % (
                    len(placed_names), max(0.0, float(payload.get("time") or 0.0)))
            if fresh:
                msg += "（新开了 %s）" % "、".join(
                    track_display_name(self.timeline, tl, t)
                    for t in fresh.values() if t is not None)
            self.statusBar().showMessage(msg, 4000)
        elif skipped:
            self.statusBar().showMessage(
                "已导入 %d 个，跳过 %d 个（找不到或不支持）：%s"
                % (len(placed_names), len(skipped), "、".join(skipped[:3])), 5000)

    # Placeholder length while import_probe is still running. Real duration
    # replaces it, and later clips in the same drop shift to stay in order.
    _DROP_PENDING_SEC = 1.0
    _AUDIO_PROBE_OK = frozenset({"E_BAD_FORMAT", "E_NO_VIDEO_STREAM"})

    def _pending_drop_duration(self, task, start_t):
        """Seconds to reserve now, or None if the drop point is past the main video."""
        dur = float(self._DROP_PENDING_SEC)
        main_dur = _get_main_video_duration(task)
        if isinstance(main_dur, (int, float)) and main_dur > 0:
            remaining = max(0.0, float(main_dur) - float(start_t))
            if remaining <= 0.0:
                self.statusBar().showMessage("导入位置已超出主视频时长，未添加素材", 3000)
                return None
            dur = min(dur, remaining)
        return dur

    def _note_drop_slot(self, slot):
        batch = getattr(self, "_active_drop_batch", None)
        if batch is None:
            batch = {
                "origin": float(slot.get("origin") or 0.0),
                "task": slot.get("task"),
                "slots": [],
            }
            held = getattr(self, "_lone_drop_batches", None)
            if held is None:
                held = []
                self._lone_drop_batches = held
            held.append(batch)
        batch["slots"].append(slot)
        slot["batch"] = batch
        return batch

    def _queue_drop_probe(self, slot):
        from cutora.media.import_probe import queue_import_probe

        def _done(p, rec, _self=self, _slot=slot):
            _self._apply_drop_probe(_slot, p, rec)

        queue_import_probe(slot["path"], _done)

    def _drop_span_duration(self, task, start_t, known):
        """Clamp a known or pending duration to the main video, if there is one."""
        if known is None:
            dur = float(self._DROP_PENDING_SEC)
        else:
            try:
                dur = float(known)
            except (TypeError, ValueError):
                dur = 0.0
        main_dur = _get_main_video_duration(task)
        if isinstance(main_dur, (int, float)) and main_dur > 0:
            remaining = max(0.0, float(main_dur) - float(start_t))
            if remaining <= 0.0:
                return 0.0
            if dur <= 0.0:
                return remaining
            return min(dur, remaining)
        return max(0.0, dur)

    def _write_drop_span(self, slot, start_t, dur):
        for clip, fade in (
            (slot.get("vclip"), False),
            (slot.get("aclip"), slot.get("kind") == "audio"),
        ):
            if clip is None:
                continue
            clip.in_t = 0.0
            clip.out_t = float(dur)
            clip.start_t = float(start_t)
            if fade:
                role = str(getattr(clip, "audio_role", "") or "")
                if role == "bgm" and _SFX is not None:
                    _SFX.apply_new_bgm_defaults(clip)
                else:
                    clip.fade_in_dur = min(0.5, float(dur) / 4.0) if dur > 0 else 0.0
                    clip.fade_out_dur = min(0.5, float(dur) / 4.0) if dur > 0 else 0.0
        src = slot.get("source")
        known = slot.get("duration")
        if src is not None and known is not None:
            try:
                src.duration = float(known)
            except (TypeError, ValueError):
                src.duration = float(dur)

    def _relayout_drop_batch(self, batch):
        if not batch:
            return
        task = batch.get("task")
        cursor = float(batch.get("origin") or 0.0)
        for slot in batch.get("slots") or []:
            if slot.get("removed"):
                continue
            dur = self._drop_span_duration(task, cursor, slot.get("duration"))
            self._write_drop_span(slot, cursor, dur)
            cursor += dur

    def _remove_drop_slot(self, slot):
        slot["removed"] = True
        slot["duration"] = 0.0
        tl = slot.get("tl")
        for key, track_key in (("vclip", "vtrack"), ("aclip", "atrack")):
            clip = slot.get(key)
            track = slot.get(track_key)
            if clip is None or track is None:
                continue
            track.clips = [c for c in (track.clips or []) if c is not clip]
        src = slot.get("source")
        if tl is not None and src is not None:
            still = False
            for tr in getattr(tl, "tracks", []) or []:
                for clip in getattr(tr, "clips", []) or []:
                    if getattr(clip, "source_id", None) == getattr(src, "id", None):
                        still = True
                        break
            if not still:
                sources = getattr(tl, "sources", None)
                if isinstance(sources, dict):
                    sources.pop(getattr(src, "id", None), None)
        task = slot.get("task")
        if isinstance(task, dict):
            task["audio_clips"] = _audio_clips_from_track(task)

    def _mark_drop_rejected(self, path, error):
        marks = getattr(self, "_drop_guard_marks", None)
        if marks is None:
            marks = []
            self._drop_guard_marks = marks
        marks.append({"path": path, "error": str(error or "无法导入")})
        try:
            names = "、".join(
                os.path.basename(m.get("path") or "") for m in marks if m.get("path"))
            self.statusBar().showMessage("无法导入 %s" % names, 8000)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("drop guard status")

    def _drop_probe_accepted(self, rec, kind):
        rec = rec or {}
        info = rec.get("info") if isinstance(rec.get("info"), dict) else {}
        if rec.get("ok"):
            return True, info
        if kind != "audio":
            return False, info
        codes = set()
        for item in rec.get("fatal") or []:
            if isinstance(item, dict) and item.get("code"):
                codes.add(item.get("code"))
        if codes and codes <= self._AUDIO_PROBE_OK and (
                info.get("has_audio") or float(info.get("duration") or 0.0) > 0.0):
            return True, info
        return False, info

    def _ensure_drop_audio_clip(self, slot, dur, start_t):
        if slot.get("aclip") is not None:
            return slot["aclip"]
        src = slot.get("source")
        tl = slot.get("tl")
        if src is None or tl is None:
            return None
        track = slot.get("atrack")
        role = "bgm" if slot.get("kind") == "audio" else "main"
        if track is None:
            if _SFX is not None:
                track = _SFX.ensure_role_track(tl, role)
            elif role == "bgm":
                track = get_or_create_bgm_track(tl)
            else:
                track = get_or_create_track(tl, "audio")
                track.audio_role = "main"
                track.name = "原声"
            slot["atrack"] = track
        else:
            inherited = str(getattr(track, "audio_role", "") or "")
            if inherited:
                role = inherited
        clip = Clip(src.id, 0.0, float(dur), float(start_t))
        clip.is_main = bool(role == "main")
        clip.audio_role = role
        if role != "main":
            clip.spatial_enabled = False
        if slot.get("kind") == "audio":
            if role == "bgm" and _SFX is not None:
                _SFX.apply_new_bgm_defaults(clip)
            else:
                clip.fade_in_dur = min(0.5, float(dur) / 4.0) if dur > 0 else 0.0
                clip.fade_out_dur = min(0.5, float(dur) / 4.0) if dur > 0 else 0.0
        track.clips.append(clip)
        slot["aclip"] = clip
        return clip

    def _apply_drop_alpha(self, slot, info):
        from cutora.render.readers import alpha_mode_for_path, pix_fmt_has_alpha
        src = slot.get("source")
        clip = slot.get("vclip")
        path = slot.get("path") or ""
        if src is None or clip is None:
            return
        pix = str((info or {}).get("pix_fmt") or "")
        codec = str((info or {}).get("vcodec") or "")
        if not pix_fmt_has_alpha(pix, codec):
            return
        src.alpha = True
        clip.alpha = True
        mode = alpha_mode_for_path(path, None)
        src.alpha_mode = mode
        clip.alpha_mode = mode
        try:
            from cutora.media.alpha_proxy import ensure_alpha_proxy
            proxy = ensure_alpha_proxy(path, host=self, sync=False)
            if proxy:
                src.preview_path = proxy
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception(
                "alpha proxy on drop probe: %s", path)

    def _refresh_drop_timeline(self, task):
        if task is None or not hasattr(self, "timeline"):
            return
        tasks = getattr(self, "tasks", None) or []
        idx = int(getattr(self, "curr_idx", -1) or -1)
        if not (0 <= idx < len(tasks)) or tasks[idx] is not task:
            return
        try:
            from cutora.preview.engine import timeline_duration_sec
            self.timeline.set_data(int(timeline_duration_sec(task) * 1000), task)
            self.timeline.update()
            if hasattr(self, "video_canvas"):
                self.video_canvas.update()
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("drop probe refresh")

    def _apply_drop_probe(self, slot, path, rec):
        if slot.get("done"):
            return
        slot["done"] = True
        kind = slot.get("kind") or "video"
        ok, info = self._drop_probe_accepted(rec, kind)
        if not ok:
            err = (rec or {}).get("error") or "无法导入"
            self._remove_drop_slot(slot)
            self._mark_drop_rejected(path, err)
            self._relayout_drop_batch(slot.get("batch"))
            self._refresh_drop_timeline(slot.get("task"))
            return
        try:
            dur = float((info or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        slot["duration"] = dur
        src = slot.get("source")
        if src is not None:
            src.has_audio = bool((info or {}).get("has_audio"))
            if kind == "video":
                src.has_video = bool((info or {}).get("has_video", True))
            else:
                src.has_video = False
                src.has_audio = True
        if kind == "video":
            self._apply_drop_alpha(slot, info)
            if src is not None and src.has_audio:
                self._ensure_drop_audio_clip(slot, dur, float(slot.get("origin") or 0.0))
                self._trigger_waveform_generation(src.id, path)
            elif slot.get("aclip") is not None and src is not None and not src.has_audio:
                track = slot.get("atrack")
                clip = slot.get("aclip")
                if track is not None:
                    track.clips = [c for c in track.clips if c is not clip]
                slot["aclip"] = None
        elif src is not None:
            self._trigger_waveform_generation(src.id, path)
        task = slot.get("task")
        if isinstance(task, dict):
            task["audio_clips"] = _audio_clips_from_track(task)
        self._relayout_drop_batch(slot.get("batch"))
        self._refresh_drop_timeline(task)

    def _drop_audio_file(self, task, tl, path, start_t, target_track=None):
        """拖拽音频文件 → 先占位，时长由 import_probe 回来再填。"""
        dur = self._pending_drop_duration(task, start_t)
        if dur is None:
            return 0.0
        src = MediaSource(path, probe=False)
        src.has_video = False
        src.has_audio = True
        src.duration = None
        tl.sources[src.id] = src
        if target_track and getattr(target_track, "type", None) in ("audio", "bgm", "sfx"):
            audio_track = target_track
            role = str(getattr(target_track, "audio_role", "") or "") or "bgm"
        else:
            if _SFX is not None:
                audio_track = _SFX.ensure_role_track(tl, "bgm")
            else:
                audio_track = get_or_create_bgm_track(tl)
            role = "bgm"
        clip = Clip(src.id, 0.0, dur, start_t)
        clip.is_main = bool(role == "main")
        clip.audio_role = role
        if role == "bgm" and _SFX is not None:
            _SFX.apply_new_bgm_defaults(clip)
        else:
            if role != "main":
                clip.spatial_enabled = False
            clip.fade_in_dur = min(0.5, dur / 4)
            clip.fade_out_dur = min(0.5, dur / 4)
        audio_track.clips.append(clip)
        task['audio_clips'] = _audio_clips_from_track(task)
        slot = {
            "kind": "audio",
            "path": path,
            "task": task,
            "tl": tl,
            "source": src,
            "vclip": None,
            "aclip": clip,
            "vtrack": None,
            "atrack": audio_track,
            "origin": float(start_t),
            "duration": None,
            "removed": False,
            "done": False,
        }
        self._note_drop_slot(slot)
        self._queue_drop_probe(slot)
        self.statusBar().showMessage(f"已添加音频: {os.path.basename(path)}", 3000)
        return float(dur or 0.0)

    def _drop_image_file(self, task, tl, path, start_t, target_track=None):
        """拖拽图片文件 → 创建 image overlay（像素坐标，居中缩放适配画布）。"""
        from cutora.assets.user import build_image_overlay, _ensure_image_track
        default_dur = 5.0
        main_dur = _get_main_video_duration(task)
        if isinstance(main_dur, (int, float)) and main_dur > 0:
            remaining = max(0.0, float(main_dur) - float(start_t))
            if remaining <= 0.0:
                self.statusBar().showMessage("导入位置已超出主视频时长，未添加素材", 3000)
                return 0.0
            default_dur = min(default_dur, remaining)
        if target_track is None or getattr(target_track, "type", None) != "image":
            target_track = _ensure_image_track(tl)
        ov = build_image_overlay(
            path, start_t, tl.width, tl.height,
            track_id=getattr(target_track, "id", None),
            duration=default_dur,
            ov_id="ov_%s" % int(time.time() * 1000),
        )
        task.setdefault('image_overlays', []).append(ov)
        self.statusBar().showMessage(f"已添加图片: {os.path.basename(path)} (时长 {default_dur:.1f}s)", 3000)
        return float(default_dur or 0.0)

    def _drop_video_file(self, task, tl, path, start_t, target_track=None, loop=False):
        """拖拽视频文件 → 先占位。时长、音轨、alpha 由 import_probe 一次带回。"""
        dur = self._pending_drop_duration(task, start_t)
        if dur is None:
            return 0.0
        src = MediaSource(path, probe=False)
        src.has_audio = False
        src.duration = None
        tl.sources[src.id] = src
        fit_mode = getattr(self, '_current_fit_mode', DEFAULT_VIDEO_FIT_MODE)

        if target_track and getattr(target_track, "type", None) == "video":
            video_track = target_track
        else:
            video_track = get_or_create_track(tl, "video")
        vclip = Clip(src.id, 0.0, dur, start_t)
        vclip.loop = bool(loop)
        vclip.fit_mode = fit_mode
        vclip.blend_mode = "normal"
        vclip.x = 0.0
        vclip.y = 0.0
        vclip.scale = 1.0
        vclip.rotation = 0.0
        video_track.clips.append(vclip)
        slot = {
            "kind": "video",
            "path": path,
            "task": task,
            "tl": tl,
            "source": src,
            "vclip": vclip,
            "aclip": None,
            "vtrack": video_track,
            "atrack": None,
            "origin": float(start_t),
            "duration": None,
            "removed": False,
            "done": False,
        }
        self._note_drop_slot(slot)
        self._queue_drop_probe(slot)
        self.statusBar().showMessage(f"已添加视频: {os.path.basename(path)}", 3000)
        return float(dur or 0.0)

    def _add_bgm_to_track(self, task, apath, duration, fade_in=0.8, fade_out=1.0):
        """将 BGM 音频文件添加到 task 的 audio Track 中（NLE 数据模型）。"""
        tl = _tl_of(task)
        if tl is None:
            return
        bgm_source = MediaSource(apath)
        bgm_source.has_video = False
        bgm_source.has_audio = True
        src_dur = float(bgm_source.duration or 0.0)
        if src_dur <= 0:
            src_dur = probe_duration_sec(apath) or 0.0
            if src_dur > 0:
                bgm_source.duration = src_dur
        tl.sources[bgm_source.id] = bgm_source
        audio_track = get_or_create_bgm_track(tl)
        # 只替换旧配乐，绝不删主音频或音效（含 audio_role=main 且 is_main=False）
        keep = []
        for c in audio_track.clips:
            role = _clip_audio_role(c)
            if role == "main" or getattr(c, "is_main", False) or role == "sfx" or _is_sfx_clip(c):
                keep.append(c)
        audio_track.clips = keep
        # 创建 BGM Clip：时间线铺满视频；源比视频短则循环
        timeline_dur = float(duration) if duration and duration > 0 else src_dur
        if timeline_dur <= 0:
            timeline_dur = src_dur if src_dur > 0 else 1.0
        bgm_clip = Clip(bgm_source.id, 0.0, timeline_dur, 0.0)
        vol = 0.22
        if _SFX is not None:
            vol = float(getattr(_SFX, "NEW_BGM_LINEAR_VOL", 0.22))
            _SFX.apply_new_bgm_defaults(bgm_clip)
            try:
                _SFX.apply_default_spatial_to_clip(bgm_clip)
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").exception(
                    "apply_default_spatial_to_clip failed for bgm")
                bgm_clip.spatial_enabled = False
        else:
            bgm_clip.is_main = False
            bgm_clip.audio_role = "bgm"
            bgm_clip.gain_db = 20.0 * math.log10(max(vol, 1e-6)) if vol > 0 else -120.0
            bgm_clip.fade_in_dur = 0.30
            bgm_clip.fade_out_dur = 0.30
            bgm_clip.loop = True
            bgm_clip.spatial_enabled = False
        audio_track.clips.append(bgm_clip)
        if _SFX is not None:
            _SFX.log_bgm_assign(audio_track, 1, vol, 0)
        # 同步旧 audio_clips 兼容列表
        task['audio_clips'] = _audio_clips_from_track(task)
        # 异步波形生成
        self._trigger_waveform_generation(bgm_source.id, apath)

    def _build_default_timeline(self, override_width=None, override_height=None):
        """返回 Timeline 对象（不再是 dict）。
        如果提供了 override_width / override_height，则使用视频的实际分辨率作为画布尺寸，
        否则使用模板默认值。"""
        from cutora.model.timeline import Timeline as _TL
        tpl = self._ensure_timeline_template()
        if tpl and isinstance(tpl, dict):
            w = override_width or tpl.get("width", DEFAULT_TIMELINE_WIDTH)
            h = override_height or tpl.get("height", DEFAULT_TIMELINE_HEIGHT)
            return _TL(w, h, tpl.get("fps", DEFAULT_TIMELINE_FPS))
        w = override_width or DEFAULT_TIMELINE_WIDTH
        h = override_height or DEFAULT_TIMELINE_HEIGHT
        return _TL(w, h, DEFAULT_TIMELINE_FPS)

    def new_project(self):
        from cutora.audio.waveform import get_waveform_cache
        get_waveform_cache().cancel_all()
        return self.bus.execute("project.new", {}, source="ui")

    def _extract_video_audio(self, video_path):
        return None, None

    def on_drop(self, links):
        videos = []
        projects = []
        try:
            from cutora.batch.cli import collect_asproj_paths, is_asproj_path, VIDEO_EXTS as _VEXTS
        except Exception:
            collect_asproj_paths = None
            is_asproj_path = lambda p: str(p).lower().endswith(".asproj.json") or str(p).lower().endswith(".asproj")
            _VEXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
        for path in links or []:
            if not path:
                continue
            if os.path.isdir(path):
                projs = []
                if collect_asproj_paths:
                    try:
                        projs = collect_asproj_paths(path, sort="name")
                    except Exception:
                        projs = []
                else:
                    try:
                        for n in os.listdir(path):
                            fp = os.path.join(path, n)
                            if is_asproj_path(fp):
                                projs.append(fp)
                    except Exception:
                        projs = []
                if projs:
                    projects.extend(projs)
                else:
                    try:
                        for n in os.listdir(path):
                            fp = os.path.join(path, n)
                            if os.path.splitext(n)[1].lower() in _VEXTS:
                                videos.append(fp)
                    except Exception:
                        pass
            elif is_asproj_path(path):
                projects.append(path)
            else:
                videos.append(path)
        if projects:
            self.import_project_files(projects)
        if not videos:
            return
        links = videos
        if not self._timeline_template:
            if not self._ensure_timeline_template():
                return
        apply_canvas = not self.tasks
        # 确保使用最新的默认预设
        self._reload_default_subtitle_style()
        for path in links:
            idx = self._append_placeholder_task(path)
            self._queue_task_import_probe(idx, path, apply_canvas=apply_canvas and idx == 0)

    def _append_placeholder_task(self, path):
        """Create a list/timeline row immediately. Duration filled when probe returns."""
        from cutora.media.import_probe import library_item_text

        fit_mode = getattr(self, '_current_fit_mode', DEFAULT_VIDEO_FIT_MODE)
        tpl = self._ensure_timeline_template() or {}
        dims = (tpl.get("width", DEFAULT_TIMELINE_WIDTH), tpl.get("height", DEFAULT_TIMELINE_HEIGHT))
        s = force_allowed_font(copy.deepcopy(self.default_style))
        from cutora.subtitle.stylepack import set_fontsize_px, set_position_px
        set_position_px(s, dims[0] / 2.0, dims[1] * 0.755, dims[0], dims[1])
        timeline = self._build_default_timeline(
            override_width=dims[0], override_height=dims[1]
        )
        source = MediaSource(path, probe=False)
        source.has_audio = False
        timeline.sources[source.id] = source
        shared_link_id = uuid.uuid4().hex
        video_track = get_or_create_track(timeline, "video")
        video_track.is_main_video = True
        video_clip = Clip(source.id, 0.0, 0.0, 0.0)
        video_clip.link_group_id = shared_link_id
        video_clip.fit_mode = fit_mode
        video_clip.size_mode = "project"
        video_clip.x = 0.0
        video_clip.y = 0.0
        video_clip.scale = 1.0
        video_clip.rotation = 0.0
        video_track.clips.append(video_clip)
        get_or_create_track(timeline, "subs")
        audio_track = get_or_create_track(timeline, "audio")
        audio_track.audio_role = "main"
        audio_track.name = "原声"
        self.tasks.append({
            'video': path, 'text': '', 'segments': [], 'overlays': [],
            'text_overlays': [], 'image_overlays': [], 'audio_clips': [],
            'dims': dims, 'style': s, 'src_volume': 1.0, 'intro_media': None,
            'timeline': timeline, 'video_overlay': {},
            'audio_tracks': [], 'duration': None,
            'trim_end_sec': None,
            'probe_pending': True,
            'guard_error': None,
            '_link_group_id': shared_link_id,
        })
        from cutora.subtitle.stylepack import ensure_default_pack
        ensure_default_pack(self.tasks[-1])
        self.tasks[-1]["subtitle_roles"] = {
            "default": {
                "name": "默认",
                "style": copy.deepcopy(self.tasks[-1]["style"]),
                "color": "#CCCCCC"
            }
        }
        self.file_list.addItem(
            library_item_text(path, None), path, decode_thumb=False)
        self._relabel_library_rows()
        return len(self.tasks) - 1

    def _queue_task_import_probe(self, idx, path, apply_canvas=False):
        from cutora.media.import_probe import queue_import_probe

        def _done(p, rec, _self=self, _idx=idx, _canvas=apply_canvas):
            _self._apply_import_probe(_idx, p, rec, apply_canvas=_canvas)

        queue_import_probe(path, _done)

    def _apply_import_probe(self, idx, path, rec, apply_canvas=False):
        if idx < 0 or idx >= len(self.tasks or []):
            return
        task = self.tasks[idx]
        task_path = task.get("video") or ""
        try:
            same = os.path.normcase(os.path.abspath(task_path)) == os.path.normcase(os.path.abspath(path))
        except OSError:
            same = task_path == path
        if not same:
            return
        rec = rec or {}
        task["probe_pending"] = False
        if not rec.get("ok"):
            err = rec.get("error") or "无法导入"
            task["guard_error"] = err
            task["duration"] = None
            self._strip_task_media_clips(task)
            self._refresh_library_item(idx, error=err)
            return
        info = rec.get("info") or {}
        try:
            dur = float(info.get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        task["duration"] = dur
        task["trim_end_sec"] = dur
        task["guard_error"] = None
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if apply_canvas and w > 0 and h > 0:
            tpl = self._ensure_timeline_template()
            tpl["width"] = int(w)
            tpl["height"] = int(h)
            task["dims"] = (int(w), int(h))
            tl = task.get("timeline")
            if tl is not None:
                tl.width = int(w)
                tl.height = int(h)
        elif w > 0 and h > 0:
            task["source_wh"] = (int(w), int(h))
        self._fill_task_from_probe(task, info, dur)
        self._refresh_library_item(idx, duration=dur)
        if source_id := self._task_source_id(task):
            src = self._task_source(task, source_id)
            src_path = getattr(src, "path", None) if src is not None else path
            if info.get("has_audio") and src_path and (self.curr_idx == idx or len(self.tasks or []) <= 2):
                self._trigger_waveform_generation(source_id, src_path)
        if self.curr_idx == idx and hasattr(self, "timeline"):
            from cutora.preview.engine import timeline_duration_sec
            self.timeline.set_data(int(timeline_duration_sec(task) * 1000), task)
            self.timeline.update()

    def _task_source_id(self, task):
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return None
        sources = getattr(tl, "sources", None) or {}
        for sid in sources:
            return sid
        return None

    def _task_source(self, task, source_id):
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return None
        sources = getattr(tl, "sources", None) or {}
        return sources.get(source_id)

    def _strip_task_media_clips(self, task):
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) in ("video", "audio"):
                tr.clips = []

    def _fill_task_from_probe(self, task, info, dur):
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None:
            return
        sources = getattr(tl, "sources", None) or {}
        source = None
        source_id = None
        for sid, src in sources.items():
            source = src
            source_id = sid
            break
        if source is None:
            return
        source.duration = dur
        source.has_video = bool(info.get("has_video", True))
        source.has_audio = bool(info.get("has_audio"))
        link_id = task.get("_link_group_id") or uuid.uuid4().hex
        has_vclip = False
        main_vtrack = None
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "video":
                continue
            if main_vtrack is None or getattr(tr, "is_main_video", False):
                main_vtrack = tr
            for clip in getattr(tr, "clips", []) or []:
                if getattr(clip, "source_id", None) == source_id:
                    clip.out_t = dur
                    if not getattr(clip, "link_group_id", None):
                        clip.link_group_id = link_id
                    has_vclip = True
        if not has_vclip and main_vtrack is not None:
            vclip = Clip(source_id, 0.0, float(dur or 0.0), 0.0)
            vclip.link_group_id = link_id
            vclip.fit_mode = getattr(self, "_current_fit_mode", DEFAULT_VIDEO_FIT_MODE)
            vclip.size_mode = "project"
            vclip.x = 0.0
            vclip.y = 0.0
            vclip.scale = 1.0
            vclip.rotation = 0.0
            main_vtrack.clips.append(vclip)
        if _SFX is not None:
            audio_track = _SFX.ensure_role_track(tl, "main")
        else:
            audio_track = get_or_create_track(tl, "audio")
            audio_track.audio_role = "main"
            audio_track.name = "原声"
        existing = [
            c for c in (getattr(audio_track, "clips", None) or [])
            if getattr(c, "source_id", None) == source_id
        ]
        if source.has_audio:
            if existing:
                for clip in existing:
                    clip.out_t = dur
            else:
                audio_clip = Clip(source_id, 0.0, float(dur or 0.0), 0.0)
                audio_clip.link_group_id = link_id
                audio_clip.is_main = True
                audio_clip.audio_role = "main"
                if _SFX is not None:
                    try:
                        _SFX.apply_default_spatial_to_clip(audio_clip)
                    except Exception:
                        from cutora.core.log import get_logger
                        get_logger("cutora.ui.main_window").exception(
                            "apply_default_spatial_to_clip failed")
                        audio_clip.spatial_enabled = True
                else:
                    audio_clip.spatial_enabled = True
                audio_track.clips.append(audio_clip)
        elif existing:
            audio_track.clips = [
                c for c in audio_track.clips
                if getattr(c, "source_id", None) != source_id
            ]

    def _library_label_for(self, task):
        """一行媒体库该显示的字：文件名 + 时长。只要文件名，不加文件夹。"""
        from cutora.media.import_probe import library_item_text
        task = task or {}
        name = str(task.get("name") or "").strip()
        if name:
            # 拼接工程等自带名字的条目原样显示：名字是人起的，别替他加料
            return name
        base = (task.get("video") or task.get("path") or "") or "（未找到文件）"
        return library_item_text(
            base, task.get("duration"),
            error=task.get("guard_error") or None,
            pending=bool(task.get("probe_pending")))

    def _relabel_library_rows(self):
        """媒体库每一行的字只由 task 决定，导入时和重开工程后必须一模一样。

        以前是两套：导入走「文件名 时长」，打开工程走另一个只给文件名的
        函数，同一个工程前后长得不一样。现在只有这一条路。
        """
        fl = getattr(self, "file_list", None)
        if fl is None:
            return
        for i, task in enumerate(self.tasks or []):
            item = fl.item(i)
            if item is None:
                continue
            item.setText(0, self._library_label_for(task))

    def _refresh_library_item(self, idx, duration=None, error=None):
        from cutora.media.import_probe import library_item_text
        from PySide6.QtGui import QBrush, QColor, QIcon
        from cutora.ui.theme.tokens import ERR
        fl = getattr(self, "file_list", None)
        if fl is None:
            return
        item = fl.item(idx)
        if item is None:
            return
        task = self.tasks[idx] if 0 <= idx < len(self.tasks) else {}
        path = (task or {}).get("video") or ""
        # 只改这一行。探测是一个文件回来一次，整表重刷 N 次就是 N×N 次
        # setText，白白惹一堆重绘。
        item.setText(0, self._library_label_for(task))
        if error:
            item.setForeground(0, QBrush(QColor(ERR)))
            item.setToolTip(0, str(error))
            return
        item.setForeground(0, QBrush())
        item.setToolTip(0, path)
        from cutora.ui.timeline.thumbs import peek_clip_thumb, request_clip_thumb
        if path and os.path.isfile(path):
            def _apply(_k="", it=item, p=path):
                hit = peek_clip_thumb(p, 0.1, 64, 48)
                if not hit:
                    return
                try:
                    it.setIcon(0, QIcon(hit))
                except RuntimeError:
                    pass        # 缩略图回来时这一行已经被重建 / 删掉了（C++ 对象没了），新行会自己再要
            if peek_clip_thumb(path, 0.1, 64, 48):
                _apply()
            else:
                request_clip_thumb(path, 0.1, 64, 48, on_done=_apply)

    def _get_selected_indices(self):
        selected = self.file_list.selectedItems()
        return sorted({self.file_list.row(item) for item in selected})

    @staticmethod
    def _library_row_name(task):
        name = str((task or {}).get("name") or "").strip()
        if name:
            return name
        path = (task or {}).get("video") or (task or {}).get("path") or ""
        return os.path.basename(path) if path else "（未找到文件）"

    @staticmethod
    def _library_thumb_path(task):
        tl = (task or {}).get("timeline") if isinstance(task, dict) else None
        sources, tracks = {}, []
        if tl is not None and hasattr(tl, "tracks"):
            sources = getattr(tl, "sources", {}) or {}
            tracks = getattr(tl, "tracks", []) or []
        elif isinstance(tl, dict):
            sources = tl.get("sources") or {}
            tracks = tl.get("tracks") or []
        for tr in tracks:
            ttype = getattr(tr, "type", None) if not isinstance(tr, dict) else tr.get("type")
            if ttype != "video":
                continue
            clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
            if not clips:
                continue
            clip = clips[0]
            sid = getattr(clip, "source_id", None) if not isinstance(clip, dict) else clip.get("source_id")
            src = sources.get(sid) if sid else None
            if src is not None:
                p = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None)
                if p:
                    return str(p)
        return str((task or {}).get("video") or (task or {}).get("path") or "")

    def _populate_library(self, tasks):
        """One checkable row per task. Same path for stitch-import and open."""
        fl = getattr(self, "file_list", None)
        if fl is None:
            return
        fl.clear()
        rows = list(tasks or [])
        n = len(rows) or 1
        opening = bool(getattr(self, "_opening_project", False))
        for i, task in enumerate(rows):
            thumb = "" if opening else MainWindow._library_thumb_path(task)
            fl.addItem(MainWindow._library_row_name(task), thumb)
            if opening:
                self._tick_open_progress(
                    55 + int(20 * (i + 1) / n), "正在加载媒体库…")
        self._relabel_library_rows()

    def resolve_bin_targets(self):
        """Checked media-bin leaves; if none checked, current selection."""
        from PySide6.QtCore import Qt as _Qt
        leaves = self.file_list._get_all_leaves()
        checked = []
        for item in leaves:
            if item.checkState(0) != _Qt.CheckState.Checked:
                continue
            idx = self.file_list.row(item)
            if 0 <= idx < len(self.tasks):
                checked.append(idx)
        if checked:
            return checked
        selected = []
        for item in self.file_list.selectedItems():
            idx = self.file_list.row(item)
            if 0 <= idx < len(self.tasks):
                selected.append(idx)
        return selected

    def _bin_item_name(self, idx):
        item = self.file_list.item(idx) if idx is not None else None
        if item is not None:
            return (item.text(0) or "").strip()
        if 0 <= int(idx or -1) < len(self.tasks):
            return os.path.basename(self.tasks[idx].get("video") or "") or "未命名"
        return "未命名"

    def _confirm_bin_batch(self, title, source_name, n_targets, what):
        msg = (
            "源：%s\n目标：%d 个视频\n将要覆盖：%s\n\n确定执行？"
            % (source_name, int(n_targets), what)
        )
        return QMessageBox.question(
            self, title, msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def copy_subtitle_style(self, source_id, target_ids, options=None):
        """Apply source task subtitle style onto targets. Returns applied count."""
        if source_id is None or source_id < 0 or source_id >= len(self.tasks):
            return 0
        src_style = copy_style_full(self.tasks[source_id].get("style") or {})
        applied = 0
        for idx in list(target_ids or []):
            if idx == source_id or idx < 0 or idx >= len(self.tasks):
                continue
            task = self.tasks[idx]
            old_style = task.get("style") if isinstance(task.get("style"), dict) else {}
            task["style"] = copy.deepcopy(src_style)
            self._recalc_textbox_for_task(task)
            self._relayout_after_style_copy(task, old_style, task["style"])
            applied += 1
        return applied

    @staticmethod
    def _relayout_after_style_copy(task, old_style, new_style):
        """Q135：样式拷过去了，字幕条本身也要按新排版重切。

        「每条字幕：单字蹦」「行数」「宽度」这些不是画的时候读的，而是决定
        一条字幕里装几个词 —— 面板上改它们会触发 maybe_auto_relayout 重切；
        共享样式以前只换了 style，目标视频的字幕条还是一整行，得手动切一遍
        整句再切回单字蹦才生效。这里对变了的排版参数照面板的规矩重切一次。
        """
        if not isinstance(task, dict):
            return
        style = new_style if isinstance(new_style, dict) else {}
        if bool(style.get("advanced_textbox_enabled", False)):
            return          # TEXT 模式由 _recalc_textbox_for_task 负责
        from cutora.subtitle.layout import RELAYOUT_PARAM_KEYS, maybe_auto_relayout
        from cutora.subtitle.stylepack import is_single_word_mode
        old = old_style if isinstance(old_style, dict) else {}
        changed = [k for k in sorted(RELAYOUT_PARAM_KEYS) if old.get(k) != style.get(k)]
        if (old.get("layout_rules") or {}) != (style.get("layout_rules") or {}):
            changed.append("word_mode")
        if is_single_word_mode(old) != is_single_word_mode(style) and "word_mode" not in changed:
            changed.append("word_mode")
        if not changed:
            return
        key = "word_mode" if "word_mode" in changed else changed[0]
        try:
            maybe_auto_relayout(task, key)
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.style_copy").exception("共享样式后重排字幕失败")

    def add_bin_batch_menu_actions(self, menu, source_item):
        """Right-click group: copy style / share tracks, with live source/target text."""
        source_id = self.file_list.row(source_item)
        source_name = self._bin_item_name(source_id)
        targets = [i for i in self.resolve_bin_targets() if i != source_id]
        n = len(targets)
        # Q96: 这两条是最常用的，放菜单最顶上（顺序在 media_list 里定），
        # 名字写短 —— 源是谁放进 tooltip，标题只留「做什么 + 几次」。
        act_style = menu.addAction("共享字幕风格（%d 次共享）" % n)
        act_share = menu.addAction("共享轨道（%d 次共享）…" % n)
        act_style.setObjectName("act_bin_copy_style")
        act_share.setObjectName("act_bin_share_tracks")
        if n <= 0:
            tip = "请先勾选或选中目标视频"
            act_style.setEnabled(False)
            act_share.setEnabled(False)
            act_style.setToolTip(tip)
            act_share.setToolTip(tip)
        else:
            act_style.setToolTip(
                "把「%s」的字幕样式套到选中的 %d 个片段上" % (source_name, n))
            act_share.setToolTip(
                "把「%s」的轨道共享到选中的 %d 个片段上" % (source_name, n))
        return act_style, act_share

    def on_bin_copy_style(self, source_item):
        source_id = self.file_list.row(source_item)
        targets = [i for i in self.resolve_bin_targets() if i != source_id]
        if not targets:
            return
        name = self._bin_item_name(source_id)
        if not self._confirm_bin_batch(
            "拷贝字幕样式", name, len(targets), "目标视频的字幕样式"):
            return
        result = self.bus.execute(
            "batch.copy_subtitle_style",
            {"src_id": source_id, "target_ids": targets},
            source="ui",
        )
        n = (result or {}).get("applied", 0)
        self.sync_ui()
        self.statusBar().showMessage("已将「%s」的字幕样式套用到 %d 个视频" % (name, n), 5000)

    def on_bin_share_tracks(self, source_item):
        source_id = self.file_list.row(source_item)
        self._share_tracks_from_source(source_id)

    def on_selection_changed(self):
        indices = self._get_selected_indices()
        if not hasattr(self, 'stacked_text_area') or not indices:
            return
        if len(indices) <= 1:
            self.stacked_text_area.setCurrentIndex(0)
            # 恢复单文本框状态
            if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
                self.text_input.blockSignals(True)
                self.text_input.setText(self.tasks[self.curr_idx].get('text', ''))
                self.text_input.blockSignals(False)
        else:
            self.stacked_text_area.setCurrentIndex(1)
            self._sync_multi_grid(indices)

    def on_click(self, item, col=0):
        if item is None or self.file_list.row(item) == -1: return
        self.curr_idx = self.file_list.row(item)
        task = self.tasks[self.curr_idx]
        if task.get("guard_error") and not task.get("probe_pending"):
            vpath = task.get("video") or ""
            if vpath and os.path.isfile(vpath):
                task["probe_pending"] = True
                self._queue_task_import_probe(self.curr_idx, vpath)
        self._task_data = task
        self._timeline_ms = 0
        self._timeline_last_ts = None
        self._timeline_playing = False
        self._using_media_clock = False
        self.text_input.blockSignals(True); self.text_input.setText(task['text']); self.text_input.blockSignals(False)
        _sv = float(task.get('src_volume', 1.0))
        self.audio_output.setVolume(max(0.0, min(_sv, 1.0)))
        if hasattr(self, "spin_src_vol"):
            self.spin_src_vol.blockSignals(True)
            self.spin_src_vol.setValue(_sv)
            self.spin_src_vol.blockSignals(False)
        if hasattr(self, "spin_bgm_vol"):
            bgm_clip = self._get_current_bgm_clip()
            bgm_v = float(bgm_clip.get('volume', 0.1)) if bgm_clip else 0.1
            self.spin_bgm_vol.blockSignals(True)
            self.spin_bgm_vol.setValue(bgm_v)
            self.spin_bgm_vol.blockSignals(False)
        if hasattr(self, "spin_sfx_vol"):
            sfx_v = None
            for c in _audio_clips_from_track(task):
                if _is_sfx_clip(c):
                    sfx_v = float(c.get("volume", 0.18))
                    break
            if sfx_v is None and _SFX is not None:
                try:
                    sfx_v = float(_SFX.load_fx_settings().get("sfx_volume", 0.18))
                except Exception:
                    sfx_v = 0.18
            if sfx_v is None:
                sfx_v = 0.18
            self.spin_sfx_vol.blockSignals(True)
            self.spin_sfx_vol.setValue(sfx_v)
            self.spin_sfx_vol.blockSignals(False)
        self._sfx_stop_all()          # 换视频先把上一条的音效声停掉
        self._preview_fx_reset()      # 代理音轨也作废, 换完再按新片子重渲
        self._stop_stem_sink()
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is not None:
            mixer.switch_task(id(task))
        self._sync_role_faders_from_task()
        from cutora.preview.engine import timeline_duration_sec
        self._rebuild_preview_wav(immediate=not getattr(self, "_opening_project", False))
        dur_ms = int(timeline_duration_sec(task) * 1000)
        self.timeline.set_data(dur_ms, task)
        if getattr(self, "_opening_project", False):
            self._open_yield()
        self.timer.start()
        self.sub_panel.load_data(task['style'])
        if getattr(self, "_opening_project", False):
            self._open_yield()
        if hasattr(self, "role_panel"):
            self.role_panel.load_task(task)
        self._reset_sticker_ui_cache()
        self.prop_panel.load_data(None, None)
        self.media_player.pause()
        self.media_player.setPosition(0)
        self._timeline_ms = 0
        from cutora.subtitle.track_source import bind_segments, log_subtitle_source
        bind_segments(task)
        log_subtitle_source("on_click", task)
        self._refresh_task_view(self.curr_idx)
        self._sync_bgm_state(self.media_player.playbackState())
        self._preview_fx_request(400)
        opening = bool(getattr(self, "_opening_project", False))
        if not opening:
            # _refresh_task_view already presented via sync_ui. A second
            # present here was a duplicate full canvas update on every click.
            gal = getattr(self, "style_gallery", None)
            if gal is not None:
                # Rebuild the list now. Painting every visible card here was
                # the 400ms click. Those cards are filled in on the thread pool.
                gal._defer_thumb_paint = True
                try:
                    gal.reload()
                finally:
                    gal._defer_thumb_paint = False
                gal.schedule_visible_thumbs()

    def on_task_order_changed(self, old_leaves, new_leaves):
        try:
            old_indices = [old_leaves.index(item) for item in new_leaves]
            if len(old_indices) == len(self.tasks):
                self.tasks = [self.tasks[i] for i in old_indices]
            if getattr(self, '_task_data', None) in self.tasks:
                self.curr_idx = self.tasks.index(self._task_data)
        except Exception as e:
            print("[WARN] Order sync error:", e)

    def export_selected_first_frames(self):
        count = 0
        from PySide6.QtWidgets import QMessageBox
        for idx in range(self.file_list.count()):
            item = self.file_list.item(idx)
            if item and item.checkState(0) == Qt.CheckState.Checked:
                task = self.tasks[idx]
                video_path = task.get("video")
                if video_path and os.path.exists(video_path):
                    import cv2
                    cap = _open_video_capture(video_path) or cv2.VideoCapture(video_path)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret:
                            base_dir = os.path.dirname(video_path)
                            frames_dir = os.path.join(base_dir, "首帧图")
                            if not os.path.exists(frames_dir):
                                os.makedirs(frames_dir)
                            stem = os.path.splitext(os.path.basename(video_path))[0]
                            out_path = os.path.join(frames_dir, f"{stem}_cover.jpg")
                            cv2.imwrite(out_path, frame)
                            count += 1
                    cap.release()
        QMessageBox.information(self, "导出首帧图片", f"成功导出 {count} 张首帧图片。")

    def export_selected_last_frames(self):
        count = 0
        from PySide6.QtWidgets import QMessageBox
        for idx in range(self.file_list.count()):
            item = self.file_list.item(idx)
            if item and item.checkState(0) == Qt.CheckState.Checked:
                task = self.tasks[idx]
                video_path = task.get("video")
                if video_path and os.path.exists(video_path):
                    import cv2
                    cap = _open_video_capture(video_path) or cv2.VideoCapture(video_path)
                    if cap.isOpened():
                        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                        if total_frames > 0:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
                            ret, frame = cap.read()
                            if not ret and total_frames > 1:
                                cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 2)
                                ret, frame = cap.read()
                            if ret:
                                base_dir = os.path.dirname(video_path)
                                frames_dir = os.path.join(base_dir, "尾帧图")
                                if not os.path.exists(frames_dir):
                                    os.makedirs(frames_dir)
                                stem = os.path.splitext(os.path.basename(video_path))[0]
                                out_path = os.path.join(frames_dir, f"{stem}_tail.jpg")
                                cv2.imwrite(out_path, frame)
                                count += 1
                    cap.release()
        QMessageBox.information(self, "导出尾帧图片", f"成功导出 {count} 张尾帧图片。")

    def file_list_keypress(self, event):
        if event.key() == Qt.Key.Key_Delete:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._confirm_delete_tasks()
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_A):
            self.file_list.selectAll()
            return
        return DraggableListWidget.keyPressEvent(self.file_list, event)

    def _confirm_delete_tasks(self):
        n = len(self.file_list.selectedItems()) or (1 if self.file_list.currentRow() >= 0 else 0)
        if n <= 0:
            return
        ret = QMessageBox.question(
            self, "删除任务",
            "将从工程里删除 %d 条视频任务，无法用时间线 Delete 恢复。确定？" % n,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.delete_selected_task()

    def delete_selected_task(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            row = self.file_list.currentRow()
            if row < 0 or row >= len(self.tasks):
                return
            rows_to_delete = [row]
        else:
            rows_to_delete = sorted([self.file_list.row(item) for item in selected_items], reverse=True)

        self.push_undo("删除任务")
        self.media_player.stop()

        self.file_list.blockSignals(True)
        for row in rows_to_delete:
            if 0 <= row < len(self.tasks):
                del self.tasks[row]
                item = self.file_list.takeItem(row)
                del item
        self.file_list.blockSignals(False)

        if not self.tasks:
            self.curr_idx = -1
            self._task_data = None
            self.text_input.blockSignals(True); self.text_input.setText(""); self.text_input.blockSignals(False)
            self.timeline.set_data(0, None)
            self.sub_panel.load_data(self.default_style)
            if hasattr(self, "role_panel"):
                self.role_panel.load_task(None)
            self.prop_panel.load_data(None, None)
            self.update_ui_state()
            self.on_selection_changed()
            return
            
        new_row = min(rows_to_delete[-1], len(self.tasks) - 1)
        if new_row < 0:
            new_row = 0
            
        self.file_list.setCurrentRow(new_row)
        self.on_click(self.file_list.item(new_row))
        self.update_ui_state()
        self.on_selection_changed()

    def _regen_overlay_ids(self, overlays):
        for i, ov in enumerate(overlays):
            ov['id'] = f"ov_{int(time.time()*1000)}_{i}"

    def _get_video_duration_sec(self, video_path):
        return probe_duration_sec(video_path)

    def _probe_duration_sec(self, path):
        return probe_duration_sec(path)

    def _list_audio_files(self, folder):
        if not folder or not os.path.isdir(folder):
            return []
        exts = ('.mp3', '.wav', '.aac', '.m4a', '.flac', '.ogg')
        return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(exts)]

    def _ui_information(self, title, text):
        if (getattr(self, "_bus_source", None) or "ui") != "ui":
            return
        QMessageBox.information(self, title, text)

    def _ui_warning(self, title, text):
        if (getattr(self, "_bus_source", None) or "ui") != "ui":
            return
        QMessageBox.warning(self, title, text)

    def _warn_missing_media(self, missing):
        """UI save only. command_project_save stays dialog-free and calls this
        via getattr, so headless project.save does not pop a window."""
        lines = "\n".join(list(missing)[:20])
        extra = "" if len(missing) <= 20 else "\n…共 %d 个" % len(missing)
        self._ui_warning(
            "保存工程",
            "这些时间线上的文件找不到，工程保存后在别的电脑可能打不开：\n%s%s" % (
                lines, extra))

    def assign_bgm_for_tasks(self, task_indices, folder, mode='batch', push_undo=True):
        self.tabs.setCurrentIndex(0)
        self.set_progress_mode("bgm")
        if hasattr(self, "prog_label"):
            self.prog_label.setText("正在扫描音频库...")
        self.update_progress_value(5)
        audio_pool = self._list_audio_files(folder)
        if not audio_pool:
            self._ui_warning("错误", "未找到可用音频文件")
            self.set_progress_mode("sub")
            return

        if hasattr(self, "prog_label"):
            self.prog_label.setText(f"正在分析视频时长 (共{len(task_indices)}个)...")
        durations = {}
        def _dur(path):
            if path not in durations:
                durations[path] = self._probe_duration_sec(path)
            return durations[path]

        tasks = self.tasks
        video_durs = {}
        for idx in task_indices:
            task = tasks[idx]
            # 使用 NLE 全轨道扫描计算完整时长（含开场视频）
            vd = 0.0
            tl = _tl_of(task)
            if tl is not None:
                for _tr in tl.tracks:
                    if getattr(_tr, "type", None) != "video":
                        continue
                    for _cl in getattr(_tr, "clips", []) or []:
                        try:
                            _st = float(getattr(_cl, "start_t", 0.0))
                            _eff = float(getattr(_cl, "effective_duration", 0.0))
                            if _eff <= 0:
                                _i = float(getattr(_cl, "in_t", 0.0))
                                _o = float(getattr(_cl, "out_t", _i))
                                _sp = max(0.001, float(getattr(_cl, "speed", 1.0)))
                                _eff = max(0.0, (_o - _i) / _sp)
                            vd = max(vd, _st + _eff)
                        except Exception:
                            continue
            if not vd:
                vd = self._probe_duration_sec(task.get('video', ''))
            if not vd:
                self._ui_warning("错误", f"无法获取视频时长：{os.path.basename(task.get('video',''))}")
                self.set_progress_mode("sub")
                return
            video_durs[idx] = vd

        # 配乐短于视频时循环播放，不再要求「必须比视频长」（这是批量配乐失效的主因）
        playable = [a for a in audio_pool if os.path.isfile(a)]
        if not playable:
            self._ui_warning("错误", "未找到可用音频文件")
            self.set_progress_mode("sub")
            return

        used_paths = set()
        for t in tasks:
            # 从 Track.clips 获取已使用的配乐路径（不含主音频 / 音效）
            ac_compat = _audio_clips_from_track(t)
            for c in ac_compat:
                if c.get('path') and _is_bgm_clip(c):
                    used_paths.add(c['path'])
            # 旧 audio_clips 兼容
            for c in t.get('audio_clips', []):
                if isinstance(c, dict) and c.get('path') and c.get('audio_role') != 'sfx':
                    used_paths.add(c['path'])

        def _prefer_longer(cands, vd):
            longer = [a for a in cands if (_dur(a) or 0) > vd]
            return longer if longer else list(cands)

        plan = {}
        if mode == 'single':
            idx = task_indices[0]
            vd = video_durs[idx]
            unused = [a for a in playable if a not in used_paths]
            pool = _prefer_longer(unused if unused else playable, vd)
            plan[idx] = random.choice(pool)
        else:
            unused = playable[:]
            random.shuffle(unused)
            pending = []
            for idx in task_indices:
                vd = video_durs[idx]
                if unused:
                    pick = random.choice(_prefer_longer(unused, vd))
                    plan[idx] = pick
                    if pick in unused:
                        unused.remove(pick)
                else:
                    pending.append(idx)
            for idx in pending:
                vd = video_durs[idx]
                plan[idx] = random.choice(_prefer_longer(playable, vd))

        if push_undo and self.curr_idx in plan:
            self.push_undo()

        done = 0
        total = max(1, len(plan))
        for idx, apath in plan.items():
            if hasattr(self, "prog_label"):
                self.prog_label.setText(f"匹配中: {os.path.basename(apath)}")
            vd = video_durs[idx]
            clip_len = max(0.1, vd)
            fade_in = min(0.8, clip_len / 2)
            fade_out = min(1.0, clip_len / 2)
            # 创建 MediaSource + Clip 加入 audio Track（NLE 数据模型）
            self._add_bgm_to_track(tasks[idx], apath, vd, fade_in, fade_out)
            remap_bgm_to_full_duration(tasks[idx])
            done += 1
            pct = int(done * 100 / total)
            self.update_progress_value(pct)
            QApplication.processEvents()

        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()
        if hasattr(self, "prog_label"):
            self.prog_label.setText("配乐识别完成")
        self.update_progress_value(100)
        QTimer.singleShot(2000, lambda: self.set_progress_mode("sub"))
        self._ui_information("完成", "背景音乐已分配")

    def assign_bgm_links(self, mapping):
        """节点连线的结果：每条视频用指定的配乐（支持单首，或一行多首串接、多行叠轨）。短于时间线的仍循环铺满。"""
        plan = {}
        for key, val in (mapping or {}).items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(val, str):
                if val and os.path.isfile(val):
                    plan[idx] = [[val]]
            elif isinstance(val, (list, tuple)):
                groups = []
                if val and isinstance(val[0], (list, tuple)):
                    for row in val:
                        g = [str(p) for p in row if p and os.path.isfile(str(p))]
                        if g:
                            groups.append(g)
                else:
                    g = [str(p) for p in val if p and os.path.isfile(str(p))]
                    if g:
                        groups.append(g)
                if groups:
                    plan[idx] = groups
        tasks = getattr(self, "tasks", None) or []

        def _vd(task):
            vd = 0.0
            tl = _tl_of(task)
            if tl is not None:
                for _tr in tl.tracks:
                    if getattr(_tr, "type", None) != "video":
                        continue
                    for _cl in getattr(_tr, "clips", []) or []:
                        try:
                            _st = float(getattr(_cl, "start_t", 0.0))
                            _eff = float(getattr(_cl, "effective_duration", 0.0))
                            if _eff <= 0:
                                _i = float(getattr(_cl, "in_t", 0.0))
                                _o = float(getattr(_cl, "out_t", _i))
                                _sp = max(0.001, float(getattr(_cl, "speed", 1.0)))
                                _eff = max(0.0, (_o - _i) / _sp)
                            vd = max(vd, _st + _eff)
                        except Exception:
                            continue
            if not vd:
                vd = self._probe_duration_sec(task.get("video", ""))
            return vd

        video_durs = {}
        for idx in list(plan):
            if idx < 0 or idx >= len(tasks):
                plan.pop(idx, None)
                continue
            vd = _vd(tasks[idx])
            if not vd:
                self._ui_warning(
                    "错误",
                    "无法获取视频时长：%s" % os.path.basename(tasks[idx].get("video", "")))
                return
            video_durs[idx] = vd
        if not plan:
            self._ui_warning("错误", "没有可以配上的音频")
            return
        self.tabs.setCurrentIndex(0)
        self.set_progress_mode("bgm")
        if self.curr_idx in plan:
            self.push_undo()
        done = 0
        total = max(1, len(plan))
        for idx, groups in plan.items():
            first_path = groups[0][0]
            if hasattr(self, "prog_label"):
                self.prog_label.setText("匹配中: %s" % os.path.basename(first_path))
            vd = video_durs[idx]
            clip_len = max(0.1, vd)
            fade_in = min(0.8, clip_len / 2)
            fade_out = min(1.0, clip_len / 2)
            if len(groups) == 1 and len(groups[0]) == 1:
                self._add_bgm_to_track(tasks[idx], first_path, vd, fade_in, fade_out)
                remap_bgm_to_full_duration(tasks[idx])
            else:
                self._add_bgm_groups_to_task(tasks[idx], groups, vd, fade_in, fade_out)
            done += 1
            self.update_progress_value(int(done * 100 / total))
            QApplication.processEvents()
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()
        if hasattr(self, "prog_label"):
            self.prog_label.setText("配乐识别完成")
        self.update_progress_value(100)
        QTimer.singleShot(2000, lambda: self.set_progress_mode("sub"))
        self._ui_information("完成", "背景音乐已按连线分配")

    def _add_bgm_groups_to_task(self, task, groups, duration, fade_in=0.8, fade_out=1.0):
        """将一行多首串接或多行配乐组写入 task 的音频轨。"""
        tl = _tl_of(task)
        if tl is None:
            return
        from cutora.model.timeline_model import Track
        timeline_dur = max(0.1, float(duration or 0.0))
        for gi, paths in enumerate(groups):
            if not paths:
                continue
            if gi == 0 and len(paths) == 1:
                self._add_bgm_to_track(task, paths[0], timeline_dur, fade_in, fade_out)
                remap_bgm_to_full_duration(task)
                continue
            if gi == 0:
                audio_track = get_or_create_bgm_track(tl)
                keep = []
                for c in audio_track.clips:
                    role = _clip_audio_role(c)
                    if role == "main" or getattr(c, "is_main", False) or role == "sfx" or _is_sfx_clip(c):
                        keep.append(c)
                audio_track.clips = keep
            else:
                audio_track = Track("audio")
                audio_track.audio_role = "bgm"
                tl.add_track(audio_track)
            if len(paths) == 1:
                apath = paths[0]
                src = MediaSource(apath)
                src.has_video = False
                src.has_audio = True
                src_dur = float(src.duration or 0.0) or float(probe_duration_sec(apath) or 0.0)
                if src_dur > 0:
                    src.duration = src_dur
                tl.sources[src.id] = src
                cl = Clip(src.id, 0.0, timeline_dur, 0.0)
                if _SFX is not None:
                    _SFX.apply_new_bgm_defaults(cl)
                else:
                    cl.is_main = False
                    cl.audio_role = "bgm"
                    cl.loop = True
                    cl.match_duration = True
                audio_track.clips.append(cl)
                continue
            # 一组多首音频：按顺序首尾相接并循环铺满视频时长
            cursor = 0.0
            safety = 0
            while cursor < timeline_dur - 1e-3 and safety < 200:
                safety += 1
                progressed = False
                for apath in paths:
                    if cursor >= timeline_dur - 1e-3:
                        break
                    src = MediaSource(apath)
                    src.has_video = False
                    src.has_audio = True
                    src_dur = float(src.duration or 0.0) or float(probe_duration_sec(apath) or 0.0)
                    if src_dur <= 0:
                        src_dur = timeline_dur - cursor
                    src.duration = src_dur
                    tl.sources[src.id] = src
                    use_dur = min(src_dur, timeline_dur - cursor)
                    if use_dur <= 1e-3:
                        break
                    cl = Clip(src.id, 0.0, use_dur, cursor)
                    if _SFX is not None:
                        _SFX.apply_new_bgm_defaults(cl)
                    cl.is_main = False
                    cl.audio_role = "bgm"
                    cl.loop = False
                    cl.match_duration = False
                    audio_track.clips.append(cl)
                    cursor += use_dur
                    progressed = True
                if not progressed:
                    break
        _sync_audio_tracks_to_task(task)

    def _after_audio_volume_changed(self, role=None, linear_val=None, immediate=False):
        """统一同步角色推子、实时更新 StemMixer 增益，并触发后台音轨重混（解决配乐/音效调音量无反应）。"""
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is not None and role and linear_val is not None:
            try:
                mixer.set_live_linear(str(role), max(0.0, float(linear_val)))
            except Exception:
                pass
        sync_faders = getattr(self, "_sync_role_faders_from_task", None)
        if callable(sync_faders):
            try:
                sync_faders()
            except Exception:
                pass
        rebuild = getattr(self, "_rebuild_preview_wav", None)
        if callable(rebuild):
            try:
                rebuild(immediate=bool(immediate))
            except Exception:
                pass

    def _on_src_vol_changed(self, val):
        """原生音量 spinbox 实时变化 → 立刻影响播放音量 + 保存到 task + 同步主音频 Clip + 属性面板。"""
        val = float(val)
        # 代理 wav 已是全混：src_v 不是角色增益，喂扬声器会连带抬配乐且 >1 被钳死
        if not getattr(self, "_proxy_ready", False):
            self.audio_output.setVolume(max(0.0, val))
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task = self.tasks[self.curr_idx]
            task['src_volume'] = val
            # ── 同步写入主音频 Track.Clip 的 gain_db ──
            tl = _tl_of(task)
            if tl:
                gain_db = 20.0 * math.log10(max(val, 1e-6)) if val > 0 else -120.0
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "audio":
                        for c in getattr(tr, "clips", []):
                            if getattr(c, "is_main", False) or _clip_audio_role(c) == "main":
                                c.gain_db = gain_db
            # ── 如果属性面板当前正在显示主音频 Clip，同步更新面板 ──
            if (hasattr(self, 'prop_panel') and
                    getattr(self.prop_panel, 'current_type', None) == 'audio' and
                    hasattr(self.prop_panel, 'current_data') and
                    getattr(self.prop_panel.current_data, 'is_main', False)):
                self.prop_panel.audio_vol.blockSignals(True)
                self.prop_panel.audio_vol_sl.blockSignals(True)
                self.prop_panel.audio_vol.setValue(val)
                _s = max(1, int(round(1.0 / self.prop_panel.audio_vol.singleStep())))
                self.prop_panel.audio_vol_sl.setValue(int(round(val * _s)))
                self.prop_panel.audio_vol.blockSignals(False)
                self.prop_panel.audio_vol_sl.blockSignals(False)
            self._after_audio_volume_changed(role="main", linear_val=val, immediate=False)

    def _on_bgm_vol_changed(self, val):
        """BGM音量 spinbox 实时变化 → 立刻影响 BGM 播放音量 + 写入当前 BGM clip。"""
        val = float(val)
        self.bgm_audio_output.setVolume(max(0.0, min(val, 1.0)))
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task = self.tasks[self.curr_idx]
            tl = _tl_of(task)
            if tl:
                gain_db = 20.0 * math.log10(max(val, 1e-6)) if val > 0 else -120.0
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "audio":
                        for c in getattr(tr, "clips", []):
                            if _is_bgm_clip(c) or _clip_audio_role(c) == "bgm":
                                c.gain_db = gain_db
            for ac in (task.get('audio_clips') or []):
                if isinstance(ac, dict) and not ac.get("is_main") and ac.get("audio_role") != "sfx":
                    ac["volume"] = val
            # ── 如果属性面板当前正在显示 BGM Clip，同步更新面板 ──
            if (hasattr(self, 'prop_panel') and
                    getattr(self.prop_panel, 'current_type', None) == 'audio' and
                    hasattr(self.prop_panel, 'current_data') and
                    _is_bgm_clip(self.prop_panel.current_data)):
                self.prop_panel.audio_vol.blockSignals(True)
                self.prop_panel.audio_vol_sl.blockSignals(True)
                self.prop_panel.audio_vol.setValue(val)
                _s = max(1, int(round(1.0 / self.prop_panel.audio_vol.singleStep())))
                self.prop_panel.audio_vol_sl.setValue(int(round(val * _s)))
                self.prop_panel.audio_vol.blockSignals(False)
                self.prop_panel.audio_vol_sl.blockSignals(False)
            self._after_audio_volume_changed(role="bgm", linear_val=val, immediate=False)

    def _on_sfx_vol_changed(self, val):
        """音效音量：只改 sfx clip，并记住为后续铺音效用。"""
        val = float(val)
        if _SFX is not None:
            try:
                fx = _SFX.load_fx_settings()
                fx["sfx_volume"] = val
                _SFX.save_fx_settings(fx)
            except Exception:
                pass
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task = self.tasks[self.curr_idx]
            tl = _tl_of(task)
            if tl:
                gain_db = 20.0 * math.log10(max(val, 1e-6)) if val > 0 else -120.0
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "audio":
                        for c in getattr(tr, "clips", []):
                            if _is_sfx_clip(c) or _clip_audio_role(c) == "sfx":
                                c.gain_db = gain_db
            for ac in (task.get('audio_clips') or []):
                if isinstance(ac, dict) and ac.get("audio_role") == "sfx":
                    ac["volume"] = val
            if (hasattr(self, 'prop_panel') and
                    getattr(self.prop_panel, 'current_type', None) == 'audio' and
                    hasattr(self.prop_panel, 'current_data') and
                    _is_sfx_clip(self.prop_panel.current_data)):
                self.prop_panel.audio_vol.blockSignals(True)
                self.prop_panel.audio_vol_sl.blockSignals(True)
                self.prop_panel.audio_vol.setValue(val)
                _s = max(1, int(round(1.0 / self.prop_panel.audio_vol.singleStep())))
                self.prop_panel.audio_vol_sl.setValue(int(round(val * _s)))
                self.prop_panel.audio_vol.blockSignals(False)
                self.prop_panel.audio_vol_sl.blockSignals(False)
            self._after_audio_volume_changed(role="sfx", linear_val=val, immediate=False)

    def _on_audio_param_changed(self, clip_data):
        """音频属性面板参数变化 → 实时更新对应播放器音量 + 双向同步顶栏控件。"""
        if clip_data is None:
            return
        role = _clip_audio_role(clip_data)
        vol = None
        if hasattr(clip_data, 'gain_db'):
            gain = float(clip_data.gain_db)
            vol = 10.0 ** (gain / 20.0) if gain > -120.0 else 0.0
            if getattr(clip_data, 'is_main', False) or role == "main":
                # ── 主音频：同步到主播放器 + 顶栏"原生音量" + task['src_volume']；支持 >1 ──
                if not getattr(self, "_proxy_ready", False):
                    self.audio_output.setVolume(max(0.0, vol))
                if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
                    self.tasks[self.curr_idx]['src_volume'] = vol
                # 同步顶栏 spinbox（避免循环触发）
                if hasattr(self, 'spin_src_vol'):
                    self.spin_src_vol.blockSignals(True)
                    self.spin_src_vol.setValue(vol)
                    self.spin_src_vol.blockSignals(False)
                # 代理音轨在播时, 音量镜像过去(音量没烘进渲染, 见 _preview_fx_chain)
                self._preview_fx_apply_volume()
            elif role == "sfx":
                if hasattr(self, 'spin_sfx_vol'):
                    self.spin_sfx_vol.blockSignals(True)
                    self.spin_sfx_vol.setValue(vol)
                    self.spin_sfx_vol.blockSignals(False)
            else:
                # ── BGM：同步到 BGM 播放器 + 顶栏 spin_bgm_vol ──
                self.bgm_audio_output.setVolume(max(0.0, min(vol, 1.0)))
                if hasattr(self, 'spin_bgm_vol'):
                    self.spin_bgm_vol.blockSignals(True)
                    self.spin_bgm_vol.setValue(vol)
                    self.spin_bgm_vol.blockSignals(False)
        # 混响/变调/淡入淡出都烘在代理音轨里, 参数一动就得重渲(防抖 700ms)
        self._preview_fx_request()
        self._after_audio_volume_changed(role=role, linear_val=vol, immediate=False)

    def batch_apply_volume(self, *args, scope="auto"):
        if not self.tasks:
            return
        selected = self.file_list.selectedItems()
        if scope == "all":
            indices = list(range(len(self.tasks)))
        elif scope == "current":
            indices = [self.curr_idx] if self.curr_idx >= 0 else []
        elif scope == "selected":
            indices = sorted({self.file_list.row(item) for item in selected})
            if not indices:
                self._ui_information("提示", "请先选中视频")
                return
        elif selected:
            indices = sorted({self.file_list.row(item) for item in selected})
        else:
            indices = list(range(len(self.tasks)))
        src_v = float(self.spin_src_vol.value())
        bgm_v = float(self.spin_bgm_vol.value())
        sfx_v = float(self.spin_sfx_vol.value()) if hasattr(self, "spin_sfx_vol") else 0.18
        gain_db = 20.0 * math.log10(max(bgm_v, 1e-6)) if bgm_v > 0 else -120.0
        sfx_gain_db = 20.0 * math.log10(max(sfx_v, 1e-6)) if sfx_v > 0 else -120.0
        if _SFX is not None:
            try:
                fx = _SFX.load_fx_settings()
                fx["sfx_volume"] = sfx_v
                _SFX.save_fx_settings(fx)
            except Exception:
                pass
        for idx in indices:
            if idx < 0 or idx >= len(self.tasks):
                continue
            task = self.tasks[idx]
            task['src_volume'] = src_v
            src_gain_db = 20.0 * math.log10(max(src_v, 1e-6)) if src_v > 0 else -120.0
            # 更新 Track.clips：主音频 / 配乐 / 音效分开写，互不覆盖
            tl = _tl_of(task)
            if tl is not None:
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "audio":
                        for c in tr.clips:
                            role = _clip_audio_role(c)
                            if role == "main" or getattr(c, "is_main", False):
                                c.gain_db = src_gain_db
                            elif role == "sfx":
                                c.gain_db = sfx_gain_db
                            else:
                                c.gain_db = gain_db
            # 旧 audio_clips 兼容（只写配乐条目）
            for ac in (task.get('audio_clips') or []):
                if not isinstance(ac, dict):
                    continue
                if ac.get("audio_role") == "sfx":
                    ac["volume"] = sfx_v
                elif not ac.get("is_main"):
                    ac["volume"] = bgm_v
        if self.curr_idx >= 0:
            # 代理 wav 已含 gain_db。src_v 喂扬声器会把整条混音当角色推子。
            if not getattr(self, "_proxy_ready", False):
                self.audio_output.setVolume(max(0.0, min(float(src_v), 1.0)))
            clip = self._get_current_bgm_clip()
            if clip:
                clip['volume'] = bgm_v
                self._apply_bgm_volume(clip)
            else:
                self.bgm_audio_output.setVolume(max(0.0, min(float(bgm_v), 1.0)))
            mixer = getattr(self, "_stem_mixer", None)
            if mixer is not None:
                try:
                    mixer.set_live_linear("main", src_v)
                    mixer.set_live_linear("bgm", bgm_v)
                    mixer.set_live_linear("sfx", sfx_v)
                except Exception:
                    pass
        # ── 同步属性面板（如果当前正在显示音频 Clip） ──
        if (hasattr(self, 'prop_panel') and
                getattr(self.prop_panel, 'current_type', None) == 'audio' and
                hasattr(self.prop_panel, 'current_data')):
            cd = self.prop_panel.current_data
            _sync_val = None
            if getattr(cd, 'is_main', False) or _clip_audio_role(cd) == "main":
                _sync_val = src_v
            elif _clip_audio_role(cd) == "sfx":
                _sync_val = sfx_v
            elif hasattr(cd, 'gain_db'):
                _sync_val = bgm_v
            if _sync_val is not None:
                self.prop_panel.audio_vol.blockSignals(True)
                self.prop_panel.audio_vol_sl.blockSignals(True)
                self.prop_panel.audio_vol.setValue(_sync_val)
                _s = max(1, int(round(1.0 / self.prop_panel.audio_vol.singleStep())))
                self.prop_panel.audio_vol_sl.setValue(int(round(_sync_val * _s)))
                self.prop_panel.audio_vol.blockSignals(False)
                self.prop_panel.audio_vol_sl.blockSignals(False)
        self._after_audio_volume_changed(immediate=True)
        if scope != "current":
            _scope = f"{len(indices)} 个项目" if selected else "全部项目"
            self._ui_information("完成", f"音量已统一调整（{_scope}）")

    def _get_first_track_id_of_type(self, tl, track_type):
        if tl is None:
            return None
        for tr in tl.tracks:
            if getattr(tr, "type", None) == track_type:
                return getattr(tr, "id", None)
        return None

    def _collect_overlays_for_track(self, src_task, src_tl, src_track):
        t_type = getattr(src_track, "type", None)
        # Q175：调整轨的调整条也在 image_overlays 里（type == "adjust"）—— 以前这里只认文字 / 图片，
        # 共享调整轨只建了空轨道，调整条一条都没带过去
        list_name = "text_overlays" if t_type == "text" else (
            "image_overlays" if t_type in ("image", "adjust") else None
        )
        if not list_name:
            return None, []
        first_id = self._get_first_track_id_of_type(src_tl, t_type)
        out = []
        for ov in src_task.get(list_name, []) or []:
            if not isinstance(ov, dict):
                continue
            if list_name == "image_overlays" and (ov.get("type") == "adjust") != (t_type == "adjust"):
                continue
            ov_tid = ov.get("_track_id")
            if ov_tid == getattr(src_track, "id", None) or (ov_tid is None and first_id == getattr(src_track, "id", None)):
                out.append(copy.deepcopy(ov))
        return list_name, out

    def _track_display_name(self, track):
        t_type = getattr(track, "type", "")
        if t_type == "image":
            return "图片轨道"
        if t_type == "text":
            return "文本轨道"
        if t_type == "audio":
            return "BGM轨道"
        if t_type == "video":
            return "视频素材轨道"
        if t_type == "adjust":
            return "调整轨道"
        if t_type in ("subs", "subtitle"):
            return "字幕轨道"
        return str(t_type)

    def _track_icon_for_type(self, track):
        t_type = getattr(track, "type", "")
        if t_type == "text":
            return load_svg_icon("tool_text.svg", size=16)
        if t_type == "image":
            return load_svg_icon("tool_image.svg", size=16)
        if t_type == "audio":
            return load_svg_icon("tool_audio.svg", size=16)
        return load_svg_icon("btn_export.svg", size=16)

    def _build_batch_share_dialog(self, batch_tracks):
        dlg = QDialog(self)
        dlg.setWindowTitle("轨道共享")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        desc = QLabel("以下轨道已开启轨道共享，请确认要共享的轨道：")
        layout.addWidget(desc)

        rows_container = QWidget()
        rows_layout = QVBoxLayout(rows_container)
        rows_layout.setSpacing(6)

        items = []
        for tr in batch_tracks:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(True)
            icon_label = QLabel()
            icon = self._track_icon_for_type(tr)
            icon_label.setPixmap(icon.pixmap(16, 16))
            name_label = QLabel(self._track_display_name(tr))

            auto_fit = QCheckBox("自动匹配目标视频时长")
            auto_fit.setToolTip(
                "勾上：拉伸到每条目标视频的总时长。\n"
                "不勾：保持源视频里一模一样的起止时间和时长。")
            auto_fit.setChecked(self._share_auto_fit_pref(getattr(tr, "type", "")))

            row_layout.addWidget(chk)
            row_layout.addWidget(icon_label)
            row_layout.addWidget(name_label)
            row_layout.addStretch()
            row_layout.addWidget(auto_fit)

            rows_layout.addWidget(row)
            items.append({
                "track": tr,
                "enabled": True,
                "auto_fit_duration": True,
                "_chk": chk,
                "_auto": auto_fit
            })

        layout.addWidget(rows_container)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("确定")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        return dlg, items

    _SHARE_AUTO_FIT_KEY = "share_tracks.auto_fit_by_type"

    def _share_auto_fit_pref(self, track_type) -> bool:
        """上次这种轨道勾没勾「自动匹配目标视频时长」。没记过 = 勾上（老行为）。"""
        try:
            from cutora.core.history import _load_app_settings
            prefs = (_load_app_settings() or {}).get(self._SHARE_AUTO_FIT_KEY) or {}
            if isinstance(prefs, dict) and str(track_type) in prefs:
                return bool(prefs[str(track_type)])
        except Exception:
            pass
        return True

    def _save_share_auto_fit_pref(self, track_type, on) -> None:
        if not track_type:
            return
        try:
            from cutora.core.history import _load_app_settings, _save_app_settings
            data = _load_app_settings() or {}
            prefs = data.get(self._SHARE_AUTO_FIT_KEY)
            prefs = dict(prefs) if isinstance(prefs, dict) else {}
            prefs[str(track_type)] = bool(on)
            data[self._SHARE_AUTO_FIT_KEY] = prefs
            _save_app_settings(data)
        except Exception:
            pass

    def on_batch_share_clicked(self):
        self._share_tracks_from_source(self.curr_idx)

    def _share_tracks_from_source(self, source_id):
        if source_id is None or source_id < 0 or source_id >= len(self.tasks):
            return
        target_ids = [i for i in self.resolve_bin_targets() if i != source_id]
        if not target_ids:
            QMessageBox.information(self, "提示", "请先勾选或选中目标视频")
            return
        src = self.tasks[source_id]
        src_tl = _tl_of(src)
        if src_tl is None:
            return
        batch_tracks = get_batch_tracks(src_tl)
        if not batch_tracks:
            QMessageBox.information(self, "提示", "当前视频没有开启轨道共享的轨道")
            return
        dlg, items = self._build_batch_share_dialog(batch_tracks)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for it in items:
            it["enabled"] = it["_chk"].isChecked()
            it["auto_fit_duration"] = it["_auto"].isChecked()
            self._save_share_auto_fit_pref(
                getattr(it.get("track"), "type", ""), it["auto_fit_duration"])
        selected_items = [it for it in items if it["enabled"]]
        if not selected_items:
            return
        name = self._bin_item_name(source_id)
        if not self._confirm_bin_batch(
            "共享轨道", name, len(target_ids), "目标视频将新增所选共享轨道"):
            return
        types = []
        track_ids = []
        keep_ids = []
        for it in selected_items:
            t = getattr(it.get("track"), "type", None)
            if t:
                types.append(t)
            tid = str(getattr(it.get("track"), "id", "") or "")
            if tid:
                track_ids.append(tid)
                if not it["auto_fit_duration"]:
                    keep_ids.append(tid)
        # Q140：勾没勾「自动匹配目标视频时长」要逐条轨道传进命令，
        # 以前只传了类型，命令体里一律按勾上处理
        result = self.bus.execute(
            "batch.share_tracks",
            {"src_id": source_id, "target_ids": target_ids, "track_types": types,
             "track_ids": track_ids, "keep_duration_track_ids": keep_ids},
            source="ui",
        )
        n = (result or {}).get("applied", 0)
        if self.curr_idx >= 0:
            self.timeline.update()
            self.video_canvas.update()
        self.statusBar().showMessage("已将「%s」的轨道共享到 %d 个视频" % (name, n), 5000)

    def share_tracks(self, source_id, target_ids, options=None):
        """Share selected batch tracks from source onto targets. Returns count."""
        options = options or {}
        selected_items = options.get("items") or []
        if source_id is None or source_id < 0 or source_id >= len(self.tasks):
            return 0
        src = self.tasks[source_id]
        src_tl = _tl_of(src)
        if src_tl is None or not selected_items:
            return 0
        applied = 0
        for idx in list(target_ids or []):
            if idx == source_id or idx < 0 or idx >= len(self.tasks):
                continue
            tgt = self.tasks[idx]
            tgt_tl = _tl_of(tgt)
            if tgt_tl is None:
                continue
            applied += 1
            # 使用 NLE 全轨道扫描计算目标视频的完整时长（含开场）
            target_dur = 0.0
            if tgt_tl is not None:
                for _tr in tgt_tl.tracks:
                    if getattr(_tr, "type", None) != "video":
                        continue
                    for _cl in getattr(_tr, "clips", []) or []:
                        try:
                            _st = float(getattr(_cl, "start_t", 0.0))
                            _eff = float(getattr(_cl, "effective_duration", 0.0))
                            if _eff <= 0:
                                _i = float(getattr(_cl, "in_t", 0.0))
                                _o = float(getattr(_cl, "out_t", _i))
                                _sp = max(0.001, float(getattr(_cl, "speed", 1.0)))
                                _eff = max(0.0, (_o - _i) / _sp)
                            target_dur = max(target_dur, _st + _eff)
                        except Exception:
                            continue
            if target_dur <= 0:
                target_dur = probe_duration_sec(tgt.get("video")) or 0.0
            placed = {}
            src_order = {id(t): i for i, t in enumerate(src_tl.tracks)}
            selected_items = sorted(selected_items, key=lambda it: src_order.get(id(it.get("track")), 1 << 30))
            for it in selected_items:
                src_tr = it["track"]
                auto_fit = it["auto_fit_duration"]
                new_tr = copy.deepcopy(src_tr)
                # 双保险：共享时忽略任何缩略图前置片段
                new_tr.clips = [
                    c for c in (getattr(new_tr, "clips", []) or [])
                    if not bool(getattr(c, "is_thumbnail_leadin", False))
                ]
                new_tr.id = uuid.uuid4().hex
                new_tr.enable_batch = False
                # Q177：保持叠压顺序 —— 放到和源时间线里一样的位置（源里它下面那条轨道在目标里对应的那条之上），
                # 不再一律盖在最上面
                place_shared_track(src_tl, src_tr, tgt_tl, new_tr, placed)
                placed[id(src_tr)] = new_tr
                for c in getattr(new_tr, "clips", []) or []:
                    sid = getattr(c, "source_id", None)
                    if sid and sid in src_tl.sources and sid not in tgt_tl.sources:
                        tgt_tl.sources[sid] = copy.deepcopy(src_tl.sources[sid])
                list_name, ovs = self._collect_overlays_for_track(src, src_tl, src_tr)
                if list_name and ovs:
                    # ── 按画布比例缩放 overlay 空间属性 ──
                    _src_cw = float(getattr(src_tl, "width", 0) or 0)
                    _src_ch = float(getattr(src_tl, "height", 0) or 0)
                    _tgt_cw = float(getattr(tgt_tl, "width", 0) or 0)
                    _tgt_ch = float(getattr(tgt_tl, "height", 0) or 0)
                    tgt.setdefault(list_name, [])
                    base_idx = len(tgt[list_name])
                    for i, ov in enumerate(ovs):
                        _scale_overlay_for_canvas(ov, _src_cw, _src_ch, _tgt_cw, _tgt_ch)
                        ov["_track_id"] = new_tr.id
                        ov["id"] = f"ov_{int(time.time()*1000)}_{base_idx + i}"
                        tgt[list_name].append(ov)
                if auto_fit:
                    fit_track_to_duration(new_tr, target_dur, tgt)
        return applied

    def copy_layers_to_selected(self):
        # 兼容旧入口：统一走轨道共享确认弹窗
        self.on_batch_share_clicked()

    def _sanitize_segments(self, segs):
        clean = []
        for seg in segs or []:
            if not isinstance(seg, dict):
                continue
            s = seg.get('start')
            e = seg.get('end')
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                continue
            cc = copy.deepcopy(seg)
            for k in list(cc.keys()):
                if isinstance(k, str) and k.startswith("_"):
                    cc.pop(k, None)
            clean.append(cc)
        return clean

    def _sanitize_overlays(self, ov_list):
        clean = []
        for i, ov in enumerate(ov_list or []):
            if not isinstance(ov, dict):
                continue
            ov.pop("_rich_cache", None)
            s = ov.get('start')
            e = ov.get('end')
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                continue
            new_ov = copy.deepcopy(ov)
            if new_ov.get("type") == "text":
                new_ov["font_family"] = sanitize_font_family(new_ov.get("font_family"))
            if new_ov.get("type") == "image":
                elements = new_ov.get("elements", [])
                if isinstance(elements, list):
                    for el in elements:
                        if isinstance(el, dict) and el.get("kind") == "text":
                            el["font_family"] = sanitize_font_family(el.get("font_family"))
                        if isinstance(el, dict):
                            el.pop("_rich_cache", None)
            if 'id' not in new_ov:
                new_ov['id'] = f"ov_{int(time.time()*1000)}_{i}"
            clean.append(new_ov)
        return clean

    def _sanitize_audio_clips(self, clips):
        clean = []
        for i, c in enumerate(clips or []):
            if not isinstance(c, dict):
                continue
            s = c.get('start')
            e = c.get('end')
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                continue
            cc = copy.deepcopy(c)
            # 关键：运行时引用（Clip 对象等）不能写入项目文件，否则 json.dump 会失败
            cc.pop("_clip_obj", None)
            cc.pop("_source_id", None)
            # 未来扩展：任何内部字段都不落盘
            for k in list(cc.keys()):
                if isinstance(k, str) and k.startswith("_"):
                    cc.pop(k, None)
            clean.append(cc)
        return clean

    def collect_project_data(self):
        EXPORT_VIDEO_MODE = _t01_history.EXPORT_VIDEO_MODE
        tasks_data = []
        for t in self.tasks:
            # ── 透传模式：完整拷贝 task dict，只覆盖需要清洗/序列化的字段 ──
            # 这样任何新增字段都会自动保存，不需要在这里逐个枚举。
            td = copy.deepcopy(t)
            td["z_order_v2"] = True          # Q163：存下来的工程都是统一叠压的排法
            path = t.get('video')
            td["path"] = path                                            # 项目文件用 "path"
            td["segments"] = self._sanitize_segments(t.get('segments', []))
            td["text_overlays"] = self._sanitize_overlays(t.get('text_overlays', []))
            td["image_overlays"] = self._sanitize_overlays(t.get('image_overlays', []))
            td["audio_clips"] = self._sanitize_audio_clips(t.get('audio_clips', []))
            td["style"] = force_allowed_font(copy.deepcopy(t.get('style', {})))
            td["src_volume"] = float(t.get('src_volume') if t.get('src_volume') is not None else 1.0)
            td["trim_start_sec"] = float(t.get("trim_start_sec") or 0.0)
            td["trim_end_sec"] = float(t.get("trim_end_sec") or 0.0)
            # 角色字体清洗
            roles = td.get("subtitle_roles", {})
            if isinstance(roles, dict):
                for _rid, _info in roles.items():
                    if isinstance(_info, dict):
                        _info["style"] = force_allowed_font(copy.deepcopy(_info.get("style", {})))
            tasks_data.append(td)
        ui_state = {
            "timeline_zoom": self.timeline.pixels_per_second,
            "timeline_scroll_x": self.timeline_container.scrollbar.value(),
            "timeline_scroll_y": self.timeline_container.v_scrollbar.value()
        }
        def _serialize_timeline(tl_obj_or_dict):
            """Same field table as the export subprocess (project_schema)."""
            from cutora.model.project_io import _rj_serialize_timeline
            return _rj_serialize_timeline(tl_obj_or_dict) or {}

        return {
            "version": PROJECT_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": "CUTORA",
            "glossary": list(getattr(self, "project_glossary", None) or []),
            "export_video_mode": EXPORT_VIDEO_MODE,
            "tasks": [
                {
                    **t,
                    "timeline": _serialize_timeline(t.get("timeline"))
                }
                for t in tasks_data
            ],
            "curr_idx": self.curr_idx,
            "ui": ui_state
        }

    def apply_project_data(self, data):
        global EXPORT_VIDEO_MODE
        # export_video_mode 已改为 app-level 持久化设置 (app_settings.json),
        # 打开项目时不再覆盖用户的全局偏好，保持 EXPORT_VIDEO_MODE 不变。
        self.tasks = []
        self.file_list.clear()
        from cutora.asr.glossary import parse_terms
        self.project_glossary = parse_terms(data.get("glossary"))
        missing = []
        mismatch_rows = []
        tasks = data.get("tasks", [])
        try:
            from cutora.subtitle.text_preset import retarget_project_text_boxes
            n_retarget = retarget_project_text_boxes(data)
        except Exception:
            import logging
            logging.getLogger("cutora.ui.main_window").exception(
                "按各视频画布重算文本框失败")
            n_retarget = 0
        if n_retarget:
            from PySide6.QtCore import QTimer

            def _announce_retarget(n=n_retarget):
                try:
                    self.mark_dirty()
                    self.statusBar().showMessage(
                        "已按各视频的画布重算 %d 条文本的位置和字号，记得保存" % n, 8000)
                except Exception:
                    pass
            QTimer.singleShot(0, _announce_retarget)
        from cutora.model.project_media import (
            collect_missing as _collect_missing,
            rebuild_animated_missing,
            resolve_relative_paths,
        )
        proj = getattr(self, "project_path", None)
        if proj:
            resolve_relative_paths(tasks, proj)
        rebuild_animated_missing(tasks)
        for t in tasks:
            path = t.get("path") or t.get("video")
            if path and not os.path.exists(path):
                missing.append(path)
        for p in _collect_missing(tasks):
            if p not in missing:
                missing.append(p)
        for t in tasks:
            # Q119：这一行以前不在 —— path 是上面那个「查缺失文件」循环留下的
            # 变量，循环结束后它固定等于**最后一个** task 的路径。下面
            # task["video"] = path 就把所有条目的视频都写成了最后那一个：
            # 拖进 v01…v22 存盘重开，22 行全叫 v22.mp4。
            path = t.get("path") or t.get("video")
            style = force_allowed_font(t.get("style", copy.deepcopy(self.default_style)))
            text_overlays = t.get("text_overlays", [])
            image_overlays = t.get("image_overlays", [])
            from cutora.model.project_schema import ensure_lock_aspect
            for _ov in list(text_overlays or []) + list(image_overlays or []):
                ensure_lock_aspect(_ov)
            for ov in text_overlays:
                if isinstance(ov, dict):
                    ov["font_family"] = sanitize_font_family(ov.get("font_family"))
            # === 反序列化 timeline → Timeline 对象 ===
            from cutora.model.timeline import Timeline as _TL
            raw_tl = t.get("timeline")
            if isinstance(raw_tl, _TL):
                tl_obj = raw_tl
            elif isinstance(raw_tl, dict):
                tl_obj = _TL(
                    raw_tl.get("width", DEFAULT_TIMELINE_WIDTH),
                    raw_tl.get("height", DEFAULT_TIMELINE_HEIGHT),
                    raw_tl.get("fps", DEFAULT_TIMELINE_FPS),
                )
                tl_obj.sources = raw_tl.get("sources", {})
                # 反序列化 Track/Clip 对象
                for tr in raw_tl.get("tracks", []) or []:
                    if isinstance(tr, Track):
                        from cutora.subtitle.track_source import hydrate_track
                        hydrate_track(tr)
                        tl_obj.tracks.append(tr)
                        continue
                    if not isinstance(tr, dict):
                        continue
                    tr_type = tr.get("type")
                    track_obj = Track(tr_type)
                    if tr.get("id"):
                        track_obj.id = tr.get("id")
                    track_obj.enable_batch = bool(tr.get("enable_batch", False))
                    track_obj.is_main_video = bool(tr.get("is_main_video", False))
                    track_obj.domain = tr.get("domain", track_obj.domain)
                    track_obj.visible = bool(tr.get("visible", True))
                    track_obj.locked = bool(tr.get("locked", False))
                    track_obj.mute = bool(tr.get("mute", False))
                    track_obj.order = int(tr.get("order", 0))
                    track_obj.blend_mode = str(tr.get("blend_mode", "normal"))
                    from cutora.model.project_io import _hydrate_track_props
                    from cutora.model.timeline_model import TRACK_HEIGHT_STANDARD, clamp_track_height
                    _hydrate_track_props(track_obj, tr)
                    _known_track_keys = {"id", "type", "domain", "visible", "locked", "mute", "enable_batch",
                                         "is_main_video", "order", "pinned", "blend_mode", "name", "props",
                                         "split_cell", "height", "bg_role", "audio_role",
                                         "clips", "transitions"}
                    track_obj._extra = {k: v for k, v in tr.items() if k not in _known_track_keys}
                    raw_h = tr.get("height")
                    if raw_h is None:
                        raw_h = TRACK_HEIGHT_STANDARD
                    track_obj.height = clamp_track_height(raw_h)
                    from cutora.subtitle.track_source import hydrate_track
                    hydrate_track(track_obj)
                    
                    from cutora.model.timeline_model import TransitionItem
                    for trans_dict in tr.get("transitions", []) or []:
                        if not isinstance(trans_dict, dict):
                            continue
                        t_obj = TransitionItem(
                            trans_dict.get("start_t", 0.0),
                            trans_dict.get("duration", 0.0),
                            trans_dict.get("preset", "none"),
                            zoom_scale_start=trans_dict.get("zoom_scale_start", 0.7),
                            motion_blur=trans_dict.get("motion_blur", 0.4),
                            domain=trans_dict.get("domain", "visual"),
                            intensity=trans_dict.get("intensity", 1.0),
                            easing=trans_dict.get("easing", "ease_in_out"),
                            direction=trans_dict.get("direction", None),
                        )
                        track_obj.transitions.append(t_obj)

                    from cutora.model.project_io import _RJ_CLIP_KEYS
                    for c in tr.get("clips", []) or []:
                        if not isinstance(c, dict):
                            continue
                        clip_obj = Clip(c.get("source_id"), c.get("in_t", 0.0), c.get("out_t", 0.0), c.get("start_t", 0.0))
                        for k in _RJ_CLIP_KEYS:
                            if k in c:
                                setattr(clip_obj, k, c.get(k))
                        track_obj.clips.append(clip_obj)
                    tl_obj.tracks.append(track_obj)
            else:
                # 旧项目无 timeline：尝试用保存的 dims 还原画布尺寸
                old_dims = t.get("dims")
                ow = int(old_dims[0]) if isinstance(old_dims, (list, tuple)) and len(old_dims) > 0 and old_dims[0] else None
                oh = int(old_dims[1]) if isinstance(old_dims, (list, tuple)) and len(old_dims) > 1 and old_dims[1] else None
                tl_obj = self._build_default_timeline(override_width=ow, override_height=oh)

            from cutora.model.project_io import restore_saved_alpha_modes
            restore_saved_alpha_modes(getattr(tl_obj, "sources", None))

            # 旧项目无 source 时自动补建
            if not tl_obj.sources and (path and os.path.exists(path)):
                source_id = uuid.uuid4().hex
                dur = probe_duration_sec(path) or 0.0
                tl_obj.sources[source_id] = MediaSource.__new__(MediaSource)
                tl_obj.sources[source_id].id = source_id
                tl_obj.sources[source_id].path = path
                tl_obj.sources[source_id].duration = float(dur)
                tl_obj.sources[source_id].has_video = True
                tl_obj.sources[source_id].has_audio = True
                v_clip = Clip(source_id, 0.0, float(dur), 0.0)
                v_clip.fit_mode = "fill"
                v_clip.x = 0.0; v_clip.y = 0.0; v_clip.scale = 1.0; v_clip.rotation = 0.0
                v_track = Track("video"); v_track.is_main_video = True; v_track.clips.append(v_clip)
                a_clip = Clip(source_id, 0.0, float(dur), 0.0); a_clip.is_main = True; a_clip.audio_role = "main"
                a_track = Track("audio"); a_track.clips.append(a_clip)
                tl_obj.tracks.append(v_track)
                tl_obj.tracks.append(a_track)

            # 确保主视频轨道标记
            if not any(getattr(tr, "type", None) == "video" and getattr(tr, "is_main_video", False) for tr in tl_obj.tracks):
                main_path = path or t.get("video")
                if main_path:
                    norm_main = os.path.normcase(os.path.abspath(main_path))
                    for tr in tl_obj.tracks:
                        if getattr(tr, "type", None) != "video":
                            continue
                        for c in getattr(tr, "clips", []) or []:
                            sid = getattr(c, "source_id", None)
                            src = tl_obj.sources.get(sid) if sid else None
                            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                            if src_path and os.path.normcase(os.path.abspath(src_path)) == norm_main:
                                tr.is_main_video = True
                                break
                        if getattr(tr, "is_main_video", False):
                            break

            # 只保留「有内容的最高轨号」及以下，不再垫到 5/4/3 空轨
            prune_padded_tracks(tl_obj, {
                "timeline": tl_obj,
                "text_overlays": text_overlays,
                "image_overlays": image_overlays,
                "segments": t.get("segments") or [],
            })
            ensure_subs_track_for_segments({
                "timeline": tl_obj,
                "segments": t.get("segments") or [],
            })

            # ── 透传模式：以项目文件中保存的 dict 为基底完整拷贝 ──
            # 这样任何字段（trim_end_sec, subtitle_roles, video_time_offset,
            # 以及未来新增的任何字段）都会自动保留，不需要逐个枚举。
            task = copy.deepcopy(t)

            # 只覆盖需要特殊处理/反序列化的字段
            task["video"] = path or ""
            task["style"] = style                        # 已做 force_allowed_font
            from cutora.subtitle.stylepack import migrate_v1_bg_to_fx
            if isinstance(task.get("style"), dict):
                migrate_v1_bg_to_fx(task["style"])
            roles = task.get("subtitle_roles")
            if isinstance(roles, dict):
                for info in roles.values():
                    if isinstance(info, dict) and isinstance(info.get("style"), dict):
                        migrate_v1_bg_to_fx(info["style"])
            task["text_overlays"] = text_overlays         # 已做 font_family 清洗
            task["image_overlays"] = image_overlays
            task["timeline"] = tl_obj                    # Timeline 对象，不是 dict
            # Q163：老工程里视频一律压在文字 / 字幕下面 —— 统一叠压前把排在覆层后面的视频轨挪到覆层之前，
            # 画面和以前一样（只做一次，打 z_order_v2 标记；之后用户怎么排就怎么叠）
            try:
                from cutora.render.visual_stack import migrate_old_order
                migrate_old_order(task)
            except Exception:
                import logging
                logging.getLogger("cutora.ui.main_window").exception("轨道叠压迁移失败")
            task.setdefault("text", "")
            task.setdefault("segments", [])
            task.setdefault("audio_clips", [])
            task.setdefault("audio_tracks", [])
            task.setdefault("video_overlay", {})
            if "dims" not in task:
                task["dims"] = (
                    int(getattr(tl_obj, "width", 1080) or 1080),
                    int(getattr(tl_obj, "height", 1920) or 1920),
                )
            task.setdefault("src_volume", 1.0)
            task["src_volume"] = float(task.get("src_volume", 1.0))
            # === 角色映射（旧项目补齐）===
            if not isinstance(task.get("subtitle_roles"), dict):
                task["subtitle_roles"] = {}
            if "default" not in task["subtitle_roles"]:
                task["subtitle_roles"]["default"] = {
                    "name": "默认",
                    "style": copy.deepcopy(style),
                    "color": "#CCCCCC"
                }
            for rid, rinfo in list(task["subtitle_roles"].items()):
                if not isinstance(rinfo, dict):
                    task["subtitle_roles"].pop(rid, None)
                    continue
                rinfo.setdefault("name", "默认" if rid == "default" else rid)
                rinfo.setdefault("style", copy.deepcopy(style))
                rinfo.setdefault("color", "#CCCCCC")
            for seg in task.get("segments", []) or []:
                if isinstance(seg, dict) and "role" not in seg:
                    seg["role"] = "default"
            from cutora.subtitle.track_source import bind_segments
            bind_segments(task)
            from cutora.subtitle.seg_ids import ensure_segment_ids
            ensure_segment_ids(task)
            from cutora.model.offline import inspect_task
            mismatch_rows.extend(inspect_task(task).get("duration_mismatch") or [])
            if getattr(self, "_opening_project", False):
                self._tick_open_progress(50, "正在检查素材…")

            # === 旧 audio_clips → Track.clips 迁移 ===
            old_ac = task.get("audio_clips", [])
            if old_ac and isinstance(old_ac, list):
                has_any_audio_clip = any(
                    len(getattr(tr, "clips", [])) > 0 
                    for tr in tl_obj.tracks if getattr(tr, "type", None) == "audio"
                )
                if not has_any_audio_clip:
                    if _SFX is not None:
                        audio_track = _SFX.ensure_role_track(tl_obj, "bgm")
                    else:
                        audio_track = get_or_create_track(tl_obj, "audio")
                        audio_track.audio_role = "bgm"
                        audio_track.name = "配乐"
                    for ac in old_ac:
                        if not isinstance(ac, dict) or not ac.get("path"):
                            continue
                        bgm_src = MediaSource(ac["path"])
                        bgm_src.has_video = False
                        bgm_src.has_audio = True
                        clip_start = float(ac.get("start", 0.0))
                        clip_end = float(ac.get("end", clip_start))
                        clip_len = max(0.0, clip_end - clip_start)
                        trim_in = float(ac.get("trim_in", 0.0))
                        if bgm_src.duration is None:
                            bgm_src.duration = clip_len
                        tl_obj.sources[bgm_src.id] = bgm_src
                        bgm_clip = Clip(bgm_src.id, trim_in, trim_in + clip_len, clip_start)
                        volume = float(ac.get("volume", 0.1))
                        bgm_clip.gain_db = 20.0 * math.log10(max(volume, 1e-6))
                        bgm_clip.pitch_semitones = float(ac.get("pitch_semitones", 0.0))
                        bgm_clip.fade_in_dur = float(ac.get("fade_in_dur", 0.8)) if ac.get("fade_in_enabled", True) else 0.0
                        bgm_clip.fade_out_dur = float(ac.get("fade_out_dur", 1.0)) if ac.get("fade_out_enabled", True) else 0.0
                        bgm_clip.spatial_enabled = bool(ac.get("spatial_enabled", False))
                        bgm_clip.spatial_amount = float(ac.get("spatial_amount", 0.0))
                        bgm_clip.is_main = False
                        bgm_clip.audio_role = ac.get("audio_role") or "bgm"
                        bgm_clip.loop = bool(ac.get("loop", True))
                        audio_track.clips.append(bgm_clip)
                task["audio_clips"] = []
            if not task.get("video_overlay"):
                task["video_overlay"] = {
                    "type": "video",
                    "path": task.get("video", ""),
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0.0,
                    "fit_mode": "fill",
                    "keep_aspect": True,
                    "crop": None
                }
            if not task.get("audio_tracks"):
                task["audio_tracks"] = []
            if not isinstance(task.get("duration"), (int, float)) or task.get("duration") <= 0:
                task["duration"] = probe_duration_sec(task.get("video")) or 0.0
            self.tasks.append(task)
        self._populate_library(self.tasks)
        if getattr(self, "_opening_project", False):
            self._tick_open_progress(80, "正在打开工程…")

        # 项目加载完成后，为所有音频 source 触发波形生成
        pending_wav = []
        opening = bool(getattr(self, "_opening_project", False))
        for task in self.tasks:
            tl = _tl_of(task)
            if tl is None:
                continue
            for tr in tl.tracks:
                if getattr(tr, "type", None) == "audio":
                    for clip in getattr(tr, "clips", []):
                        sid = getattr(clip, "source_id", None)
                        if not sid:
                            continue
                        src = tl.sources.get(sid)
                        src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                        if src_path and os.path.exists(src_path):
                            if opening:
                                pending_wav.append((sid, src_path))
                            else:
                                self._trigger_waveform_generation(sid, src_path)
        if pending_wav:
            self._pending_open_waveforms = pending_wav

        # Q145：Q144 的中央监视器用「轨道变换」平移 / 缩放分屏格子，轨道变换先按格子
        # 大小裁图再移动 —— 原视频出格的像素变成黑边，导出也是黑的。打开工程时把这类
        # 轨道位移并进片段（画面等效），黑边处的原像素就回来了。
        try:
            from cutora.batch.monitor_model import bake_task_split_geometry
            fixed = sum(1 for task in self.tasks if bake_task_split_geometry(task))
        except Exception:
            import logging
            logging.getLogger("cutora.ui.main_window").exception("修复分屏黑边失败")
            fixed = 0
        if fixed:
            def _announce(n=fixed):
                try:
                    self.mark_dirty()
                    self.statusBar().showMessage(
                        "已修复 %d 条视频的分屏黑边（格子平移并入片段），记得保存" % n, 8000)
                except Exception:
                    pass
            QTimer.singleShot(0, _announce)

        self.curr_idx = data.get("curr_idx", -1)
        if self.tasks:
            if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
                self.curr_idx = 0
            self.file_list.setCurrentRow(self.curr_idx)
            self.on_click(self.file_list.item(self.curr_idx))
            first_tl = _tl_of(self.tasks[self.curr_idx])
            if first_tl is not None:
                self._timeline_template = {
                    "width": int(first_tl.width or DEFAULT_TIMELINE_WIDTH),
                    "height": int(first_tl.height or DEFAULT_TIMELINE_HEIGHT),
                    "fps": float(first_tl.fps or DEFAULT_TIMELINE_FPS)
                }
        else:
            self.curr_idx = -1
            self.text_input.blockSignals(True); self.text_input.setText(""); self.text_input.blockSignals(False)
            self.timeline.set_data(0, None)
            self.sub_panel.load_data(self.default_style)
            self.prop_panel.load_data(None, None)
            self.update_ui_state()

        ui = data.get("ui", {})
        zoom = ui.get("timeline_zoom")
        if isinstance(zoom, (int, float)) and zoom > 0:
            self.timeline.pixels_per_second = zoom
            self.timeline_container.update_scroll_range()
        if "timeline_scroll_x" in ui:
            self.timeline_container.scrollbar.setValue(int(ui.get("timeline_scroll_x", 0)))
        if "timeline_scroll_y" in ui:
            self.timeline_container.v_scrollbar.setValue(int(ui.get("timeline_scroll_y", 0)))

        if missing or mismatch_rows:
            lines = []
            if missing:
                lines.append("离线素材（文件不存在）：")
                lines.extend(missing)
            if mismatch_rows:
                if lines:
                    lines.append("")
                from cutora.model.offline import format_mismatch_lines
                lines.extend(format_mismatch_lines(mismatch_rows))
            self._ui_warning("素材检查", "\n".join(lines))

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目", self.project_path or "", PROJECT_SAVE_FILTER)
        if not path:
            return
        try:
            result = self.bus.execute(
                "project.save_as", {"path": ensure_save_extension(path)}, source="ui")
            QMessageBox.information(
                self, "保存成功", "已保存项目：%s" % os.path.basename(self.project_path or path))
            return result
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def is_dirty(self):
        return bool(getattr(self, "_project_dirty", False))

    def mark_dirty(self):
        self._project_dirty = True
        self._sync_idle_task = None            # Q189h：工程改了，空闲停帧作废
        self._refresh_window_title()

    def _refresh_window_title(self):
        if getattr(self, "_queue_mode", False):
            return
        try:
            self.setWindowTitle(format_window_title(
                getattr(self, "project_path", None),
                dirty=self.is_dirty()))
        except Exception:
            pass

    def pack_project(self):
        from PySide6.QtWidgets import QFileDialog
        out = QFileDialog.getExistingDirectory(self, "打包工程", "")
        if not out:
            return
        try:
            result = self.bus.execute(
                "project.pack", {"out_dir": out, "mode": "copy"}, source="ui")
            missing = (result or {}).get("missing") or []
            dest = (result or {}).get("project") or out
            if missing:
                QMessageBox.warning(
                    self, "打包工程",
                    "已写入 %s\n但这些文件找不到：\n%s" % (
                        dest, "\n".join(missing[:20])))
            else:
                QMessageBox.information(
                    self, "打包工程", "已打包到：%s" % dest)
            return result
        except Exception as e:
            QMessageBox.warning(self, "打包工程", str(e))

    def save_project(self):
        if not self.project_path:
            return self.save_project_as()
        try:
            result = self.bus.execute("project.save", {}, source="ui")
            self._project_dirty = False
            self._refresh_window_title()
            QMessageBox.information(
                self, "保存成功", "已保存项目：%s" % os.path.basename(self.project_path))
            return result
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def open_project(self, target_path=None):
        if getattr(self, "_opening_project", False):
            return None
        from cutora.audio.waveform import get_waveform_cache
        get_waveform_cache().cancel_all()
        if isinstance(target_path, str) and target_path:
            path = target_path
        else:
            path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", PROJECT_FILTER)
            if not path:
                return
        try:
            out = self.bus.execute("project.open", {"path": path}, source="ui")
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))
            return None
        return out

    def import_project_files(self, paths=None):
        """批量导入 .asproj.json：视频进当前列表，字幕/署名/配乐原样保留。"""
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "导入工程文件", "",
                PROJECT_FILTER)
            if not paths:
                return
        try:
            from cutora.batch.cli import collect_asproj_paths
            paths = collect_asproj_paths(paths, sort="name")
        except Exception as e:
            QMessageBox.warning(self, "导入工程", str(e))
            return
        incoming = []
        errors = []
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                file_ver = data.get("version", 1)
                if not (PROJECT_VERSION_MIN <= file_ver <= PROJECT_VERSION):
                    errors.append("%s 版本 v%s 不支持" % (os.path.basename(p), file_ver))
                    continue
                _normalize_project_data(data)
                self._announce_text_layer_migrate(data)
                incoming.extend(data.get("tasks") or [])
            except Exception as e:
                errors.append("%s: %s" % (os.path.basename(p), e))
        if not incoming:
            QMessageBox.warning(
                self, "导入工程",
                "这些文件里没有视频条目。\n" + ("\n".join(errors) if errors else ""))
            return
        existing = [serialize_render_task(t) for t in (self.tasks or [])]
        have = set()
        for t in existing:
            v = t.get("video") or t.get("path") or ""
            if v:
                try:
                    have.add(os.path.normcase(os.path.abspath(v)))
                except Exception:
                    have.add(os.path.normcase(v))
        added = []
        skipped = 0
        for t in incoming:
            v = t.get("video") or t.get("path") or ""
            key = ""
            if v:
                try:
                    key = os.path.normcase(os.path.abspath(v))
                except Exception:
                    key = os.path.normcase(v)
            if key and key in have:
                skipped += 1
                continue
            if key:
                have.add(key)
            added.append(t)
        if not added:
            QMessageBox.information(self, "导入工程", "没有新视频（可能都已在列表里）。")
            return
        merged = {
            "version": PROJECT_VERSION,
            "tasks": existing + added,
            "curr_idx": self.curr_idx if self.tasks else 0,
        }
        self.apply_project_data(merged)
        self.project_path = None
        n_seg = sum(1 for t in added if t.get("segments"))
        msg = "已导入 %d 个工程、%d 条视频，其中 %d 条带着字幕。" % (
            len(paths), len(added), n_seg)
        if skipped:
            msg += "\n跳过 %d 条已在列表中的重复视频。" % skipped
        if errors:
            msg += "\n\n未读入：\n" + "\n".join(errors)
        QMessageBox.information(self, "导入工程", msg)

    def _write_project_json(self, path, data):
        from cutora.core.safe_json import dump_project
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            dump_project(data, f)
        os.replace(tmp, path)

    def _recipe_record_start(self):
        from cutora.ui.panels.recipes import start_recording
        start_recording(self)

    def _recipe_record_stop(self):
        from cutora.ui.panels.recipes import stop_recording
        stop_recording(self)

    def _recipe_apply_folder(self):
        from cutora.ui.panels.recipes import open_apply_dialog
        open_apply_dialog(self)

    def split_current_project(self):
        n = len(self.tasks or [])
        if n <= 0:
            QMessageBox.warning(self, "自动拆分工程", "当前工程没有视频。先拖入视频或打开一个工程。")
            return
        from cutora.batch import cli as batch_cli
        from cutora.batch.queue_ui import SplitProjectDialog
        stem = "split"
        outdir = ""
        if self.project_path:
            outdir = os.path.dirname(os.path.abspath(self.project_path))
            stem = batch_cli.split_stem_from_path(self.project_path)
        else:
            v = (self.tasks[0].get("video") or self.tasks[0].get("path") or "") if self.tasks else ""
            if v:
                outdir = os.path.dirname(os.path.abspath(v))
                stem = os.path.splitext(os.path.basename(os.path.dirname(v)))[0] or "split"
            else:
                outdir = os.path.join(os.path.expanduser("~"), "Desktop")
        dlg = SplitProjectDialog(self, n, default_chunk=40, default_outdir=outdir, stem=stem)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.values()
        try:
            os.makedirs(opts["outdir"], exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, "自动拆分工程", "无法创建输出目录：%s" % e)
            return
        data = self.collect_project_data()
        tasks = data.get("tasks") or []
        try:
            plan = batch_cli.split_plan(len(tasks), opts["chunk"])
        except Exception as e:
            QMessageBox.warning(self, "自动拆分工程", str(e))
            return
        artifacts = []
        try:
            for part in plan:
                slice_data = dict(data)
                slice_data["tasks"] = tasks[part["start"] - 1:part["end"]]
                slice_data["curr_idx"] = 0 if slice_data["tasks"] else -1
                name = batch_cli.split_part_filename(
                    opts["stem"], part["start"], part["end"], part["index"])
                out = os.path.join(opts["outdir"], name)
                self._write_project_json(out, slice_data)
                artifacts.append(os.path.abspath(out))
        except Exception as e:
            QMessageBox.warning(self, "自动拆分工程", "写出失败：%s" % e)
            return
        sizes = " / ".join(str(p["count"]) for p in plan)
        msg = "已按顺序拆成 %d 个工程（%s）：\n\n%s" % (
            len(artifacts), sizes, "\n".join(artifacts))
        QMessageBox.information(self, "自动拆分工程", msg)
        if opts.get("add_to_queue"):
            self.open_project_queue_dialog(
                initial_paths=artifacts,
                auto_subtitle=opts.get("auto_subtitle", True))
        elif opts.get("open_first") and artifacts:
            self.open_project(artifacts[0])

    def copy_credit_overlay_to_queue(self):
        """Copy the selected text overlay onto every other task's text track."""
        import copy
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            QMessageBox.information(self, "复制署名", "请先选中一条带署名层的视频。")
            return
        src = self.tasks[self.curr_idx]
        ov = None
        sel = getattr(self.timeline, "selected_overlay", None)
        if sel and sel[0] == "text_overlays":
            lst = src.get("text_overlays") or []
            idx = sel[1]
            if 0 <= idx < len(lst):
                ov = lst[idx]
        if ov is None:
            lst = src.get("text_overlays") or []
            if len(lst) == 1:
                ov = lst[0]
        if ov is None:
            QMessageBox.information(self, "复制署名", "请在时间线选中一个文本层作为署名。")
            return
        n = 0
        for i, task in enumerate(self.tasks):
            if i == self.curr_idx:
                continue
            copies = task.setdefault("text_overlays", [])
            copies.append(copy.deepcopy(ov))
            n += 1
        QMessageBox.information(self, "复制署名", "已复制到 %d 条视频。" % n)

    def open_auto_credit_dialog(self):
        from cutora.batch.queue_ui import AutoCreditDialog
        dlg = AutoCreditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.want_queue():
            return
        self.open_project_queue_dialog(
            auto_credit=dlg.auto_credit(),
            credit_preset_id=dlg.credit_preset_id() or None,
            highlight_credit=True)

    def open_project_queue_dialog(self, initial_paths=None, auto_subtitle=None,
                                  auto_credit=None, credit_preset_id=None,
                                  highlight_credit=False):
        from cutora.batch.queue_ui import ProjectQueueDialog
        dlg = ProjectQueueDialog(
            self, initial_paths=initial_paths, auto_subtitle=auto_subtitle,
            auto_credit=auto_credit, credit_preset_id=credit_preset_id,
            highlight_credit=highlight_credit)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.want_start():
            return
        projects = dlg.projects()
        if not projects:
            return
        from cutora.batch import cli as batch_cli
        batch_cli.make_queue(
            projects,
            restart_between=dlg.restart_between(),
            auto_subtitle=dlg.auto_subtitle(),
            auto_credit=dlg.auto_credit(),
            credit_preset_id=dlg.credit_preset_id())
        batch_cli.mark_queue_item(0, "running")
        self._queue_mode = True
        first = projects[0]
        cur = ""
        if self.project_path:
            try:
                cur = os.path.normcase(os.path.abspath(self.project_path))
            except Exception:
                cur = ""
        if cur != os.path.normcase(os.path.abspath(first)):
            self.open_project(first)
        try:
            self.setWindowTitle(format_window_title(extra="工程队列 1/%d" % len(projects)))
        except Exception:
            pass
        QTimer.singleShot(600, self._start_queue_item)

    def _queue_select_all(self):
        try:
            n = self.file_list.count()
        except Exception:
            n = 0
        for i in range(n):
            it = self.file_list.item(i)
            if it is None:
                continue
            try:
                it.setCheckState(0, Qt.CheckState.Checked)
            except Exception:
                try:
                    it.setCheckState(Qt.CheckState.Checked)
                except Exception:
                    pass

    def _queue_auto_subtitle_enabled(self):
        try:
            from cutora.batch import cli as batch_cli
            q = batch_cli.load_queue() or {}
            return bool((q.get("options") or {}).get("auto_subtitle", True))
        except Exception:
            return True

    def _save_project_silent(self):
        if not self.project_path:
            return False
        try:
            data = self.collect_project_data()
            self._write_project_json(self.project_path, data)
            try:
                _record_recent_project(self.project_path)
            except Exception:
                pass
            print("[Queue] 已保存工程：%s" % self.project_path)
            return True
        except Exception as e:
            print("[Queue] 保存失败: %s" % e)
            return False

    def _start_queue_item(self):
        if self.batch_export_running or getattr(self, "is_batch_running", False):
            return
        self._queue_mode = True
        if self._queue_auto_subtitle_enabled():
            self._start_queue_auto_ai()
        else:
            self._start_queue_export()

    def _start_queue_auto_ai(self):
        self._queue_mode = True
        self._queue_ai_active = True
        self.batch_queue = []
        for i, t in enumerate(self.tasks or []):
            if not isinstance(t, dict):
                continue
            text = str(t.get("text") or "").strip()
            if text and not t.get("segments"):
                self.batch_queue.append(i)
        if not self.batch_queue:
            self._queue_ai_active = False
            self._queue_after_auto_ai()
            return
        try:
            from cutora.batch import cli as batch_cli
            q = batch_cli.load_queue() or {}
            idx = int(q.get("current_index") or 0) + 1
            total = len(q.get("items") or [])
            self.setWindowTitle(format_window_title(extra="工程队列 %d/%d（一键全自动）" % (idx, total or idx)))
        except Exception:
            pass
        self.is_batch_running = True
        self.process_next_batch()

    def _queue_after_auto_ai(self):
        if not getattr(self, "_queue_mode", False):
            return
        if not self._save_project_silent():
            self._advance_project_queue_after_export(failed=True, error="一键全自动后保存失败")
            return
        self._start_queue_export()

    def _start_queue_export(self):
        if self.batch_export_running:
            return
        self._queue_mode = True
        try:
            from cutora.batch import cli as batch_cli
            from cutora.batch.queue_credit import apply_auto_credit_from_queue_options
            q = batch_cli.load_queue() or {}
            opts = q.get("options") or {}
            if apply_auto_credit_from_queue_options(
                    self.bus, self.tasks, opts, source="ui"):
                self._save_project_silent()
        except Exception:
            pass
        self._queue_select_all()
        n = 0
        try:
            n = self.file_list.count()
        except Exception:
            n = len(self.tasks or [])
        if n <= 0:
            QMessageBox.warning(self, "工程队列", "这个工程里没有视频，已跳过。")
            self._advance_project_queue_after_export(failed=True, error="工程里没有视频")
            return
        try:
            from cutora.batch import cli as batch_cli
            q = batch_cli.load_queue() or {}
            idx = int(q.get("current_index") or 0) + 1
            total = len(q.get("items") or [])
            self.setWindowTitle(format_window_title(extra="工程队列 %d/%d（导出）" % (idx, total or idx)))
        except Exception:
            pass
        self.batch_export_running = True
        self.is_batch_exporting = True
        try:
            self._set_batch_export_text("停止批量导出")
        except Exception:
            pass
        self.start_batch_export()

    def _queue_quit_and_relaunch(self):
        """关掉导出小窗，存盘，退出，由等候进程重新打开并继续队列。"""
        from cutora.batch import cli as batch_cli
        self._queue_relaunching = True
        self._suppress_close_prompt = True
        try:
            self._save_project_silent()
        except Exception:
            pass
        try:
            from cutora.render.export_guard import close_watch_window
            from cutora.render.parallel import ParallelExportManager
            close_watch_window(getattr(ParallelExportManager, "_PREV_GUARD", None))
        except Exception as e:
            print("[Queue] 关闭导出小窗失败: %s" % e)
        try:
            batch_cli.spawn_gui_relaunch(["--queue-continue"])
        except Exception as e:
            self._queue_relaunching = False
            self._suppress_close_prompt = False
            QMessageBox.warning(self, "工程队列", "无法重启软件以继续队列：%s" % e)
            return False
        QTimer.singleShot(300, QApplication.instance().quit)
        return True

    def resume_project_queue(self):
        try:
            from cutora.batch import cli as batch_cli
        except Exception:
            return
        q = batch_cli.load_queue()
        if not q or not q.get("active") or q.get("paused"):
            return
        q["resume_on_launch"] = False
        batch_cli.save_queue(q)
        items = q.get("items") or []
        idx = int(q.get("current_index") or 0)
        while idx < len(items) and (items[idx].get("status") == "done"):
            idx += 1
        if idx >= len(items):
            batch_cli.deactivate_queue()
            return
        st = items[idx].get("status") or "pending"
        if st not in ("pending", "running"):
            nxt = batch_cli.next_pending_index(q, after=idx - 1)
            if nxt is None:
                batch_cli.deactivate_queue()
                return
            idx = nxt
        path = items[idx].get("project")
        if not path or not os.path.isfile(path):
            batch_cli.mark_queue_item(idx, "failed", error="找不到工程文件")
            q = batch_cli.load_queue()
            nxt = batch_cli.next_pending_index(q, after=idx)
            if nxt is None:
                batch_cli.deactivate_queue()
                QMessageBox.warning(self, "工程队列", "找不到工程文件：\n%s" % (path or ""))
                return
            q["current_index"] = nxt
            q["resume_on_launch"] = True
            batch_cli.save_queue(q)
            self._queue_quit_and_relaunch()
            return
        batch_cli.mark_queue_item(idx, "running")
        self._queue_mode = True
        try:
            self.setWindowTitle(format_window_title(extra="工程队列 %d/%d" % (idx + 1, len(items))))
        except Exception:
            pass
        self.open_project(path)
        QTimer.singleShot(1500, self._start_queue_item)

    def _advance_project_queue_after_export(self, failed=False, error=None):
        try:
            from cutora.batch import cli as batch_cli
        except Exception:
            return False
        q = batch_cli.load_queue()
        if not q or not q.get("active") or q.get("paused"):
            self._queue_mode = False
            return False
        items = q.get("items") or []
        idx = int(q.get("current_index") or 0)
        if 0 <= idx < len(items):
            if failed:
                batch_cli.mark_queue_item(idx, "failed", error=error or "导出失败")
            else:
                batch_cli.mark_queue_item(idx, "done")
            q = batch_cli.load_queue() or q
            items = q.get("items") or []
        nxt = batch_cli.next_pending_index(q, after=idx)
        if nxt is None:
            batch_cli.deactivate_queue()
            self._queue_mode = False
            try:
                if os.name == "nt":
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
            QMessageBox.information(self, "工程队列完成", "全部 %d 个工程已处理完毕。" % len(items))
            return True
        q["current_index"] = nxt
        restart = bool((q.get("options") or {}).get("restart_between", True))
        if restart:
            q["resume_on_launch"] = True
            q["paused"] = False
            batch_cli.save_queue(q)
            self._queue_quit_and_relaunch()
            return True
        batch_cli.save_queue(q)
        path = (items[nxt] or {}).get("project")
        batch_cli.mark_queue_item(nxt, "running")
        self.open_project(path)
        try:
            self.setWindowTitle(format_window_title(extra="工程队列 %d/%d" % (nxt + 1, len(items))))
        except Exception:
            pass
        QTimer.singleShot(800, self._start_queue_item)
        return True

    def _populate_recent_menu(self):
        self.recent_menu.clear()
        recent_list = _get_recent_projects()
        if not recent_list:
            act_empty = QAction("无最近项目", self)
            act_empty.setEnabled(False)
            self.recent_menu.addAction(act_empty)
            return

        for item in recent_list:
            path = item.get("path")
            if not path:
                continue
            name = os.path.basename(path)
            act = QAction(name, self)
            act.setToolTip(path)
            # Use default argument capture for lambda closure
            act.triggered.connect(lambda checked, p=path: self.open_project(p))
            self.recent_menu.addAction(act)

    def on_duration_changed(self, duration_ms):
        if self.curr_idx < 0: return
        if duration_ms > 0:
            offset_ms = self._get_video_time_offset_ms()
            total_ms = duration_ms + offset_ms if offset_ms > 0 else duration_ms
            self.timeline.set_data(total_ms, self.tasks[self.curr_idx])
            self.timeline.update()
            self._refresh_timecode()

    def on_intro_duration_changed(self, v):
        self.global_intro_duration = float(v)
        self.remap_selected_intro_duration()

    def remap_selected_intro_duration(self):
        selected = self.file_list.selectedItems()
        if not selected:
            return
        selected_indices = sorted({self.file_list.row(item) for item in selected})
        for idx in selected_indices:
            task = self.tasks[idx]
            state = task.get("_thumbnail_state")
            if not isinstance(state, dict) or not state.get("enabled"):
                continue
            new = float(self.global_intro_duration)
            update_thumbnail_leadin_duration(task, new)
        if self.curr_idx in selected_indices:
            offset_ms = self._get_video_time_offset_ms()
            base_ms = self.media_player.duration()
            total_ms = base_ms + offset_ms if offset_ms > 0 else base_ms
            self.timeline.set_data(total_ms, self.tasks[self.curr_idx])
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def _find_intro_image_overlay(self, task):
        for ov in task.get("image_overlays", []):
            if ov.get("is_intro"):
                return ov
        return None

    def copy_intro_style(self, src_ov, dst_ov):
        def copy_el(src_el, dst_el):
            for k, v in src_el.items():
                if k in ("content", "id"):
                    continue
                dst_el[k] = copy.deepcopy(v)
            if src_el.get("id") == dst_el.get("id"):
                dst_el["id"] = uuid.uuid4().hex

        for i, src_el in enumerate(src_ov.get("elements", [])):
            if i >= len(dst_ov.get("elements", [])):
                continue
            dst_el = dst_ov["elements"][i]
            if src_el.get("kind") == dst_el.get("kind"):
                copy_el(src_el, dst_el)

    def sync_intro_style_to_all(self):
        if self.curr_idx < 0:
            return
        src_task = self.tasks[self.curr_idx]
        src_ov = self._find_intro_image_overlay(src_task)
        if not src_ov:
            return
        for task in self.tasks:
            dst_ov = self._find_intro_image_overlay(task)
            if not dst_ov:
                continue
            self.copy_intro_style(src_ov, dst_ov)
        self._reset_sticker_ui_cache()
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def _clear_canvas_pick(self):
        """Drop the preview's hit selection so a timeline pick is not fighting a leftover image."""
        canvas = getattr(self, "video_canvas", None)
        if canvas is None:
            return
        canvas._selected_item = None
        canvas._selected_rect = None
        if hasattr(canvas, "_multi_clear"):
            canvas._multi_clear()
        canvas.update()

    def _sync_timeline_from_canvas(self, type_, idx):
        """Canvas hit uses the same exclusive selection as a timeline click."""
        tlw = getattr(self, "timeline", None)
        if tlw is None or not hasattr(tlw, "_reset_selection"):
            return
        task = None
        if 0 <= self.curr_idx < len(self.tasks or []):
            task = self.tasks[self.curr_idx]
        if type_ == "video_clip":
            clip = idx
            track = None
            tl = _tl_of(task) if isinstance(task, dict) else None
            if tl is not None and clip is not None:
                for tr in getattr(tl, "tracks", []) or []:
                    if getattr(tr, "type", None) == "video" and clip in (getattr(tr, "clips", []) or []):
                        track = tr
                        break
            if track is not None:
                tlw._select_video_clip(track, clip)
            else:
                tlw._reset_selection()
                tlw._publish_selection()
            tlw.update()
        elif type_ == "overlay":
            tlw._reset_selection()
            if isinstance(idx, tuple) and len(idx) == 2:
                tlw.selected_overlay = (idx[0], idx[1])
                tlw.selected_track = tlw._resolve_track_obj(
                    "text" if idx[0] == "text_overlays" else "images")
            tlw._publish_selection()
            tlw.update()
        elif type_ == "sticker":
            tlw._reset_selection()
            if isinstance(idx, tuple) and idx and isinstance(idx[0], int):
                tlw.selected_overlay = ("image_overlays", idx[0])
                tlw.selected_track = tlw._resolve_track_obj("images")
            tlw._publish_selection()
            tlw.update()
        elif type_ == "sub" and isinstance(idx, int):
            tlw._select_subtitle_index(idx)
            tlw.update()
        elif type_ in ("", None):
            tlw._reset_selection()
            tlw._publish_selection()
            tlw.update()

    def on_timeline_select(self, type_, idx):
        if self.curr_idx < 0: return
        task = self.tasks[self.curr_idx]
        self._task_data = task
        if type_ != 'sticker':
            self._selected_sticker = None
        if type_ != 'overlay':
            self._selected_image_overlay = None
        if not type_:
            panel = getattr(self, "prop_panel", None)
            if panel is not None and hasattr(panel, "load_data"):
                panel.load_data(None, None)
            return
        # [FIX-IMG] 拖入的图片其覆层容器 = 整块画布（x/y=0, w/h=画布尺寸），
        # 直接选中容器会让“变换”里的位置/尺寸作用在画布容器上而不是图片本身。
        # 若该覆层只承载一张图片元素，则改为选中这个元素。
        if type_ == 'overlay':
            _ln, _oi = (idx if isinstance(idx, tuple) else ('overlays', idx))
            if _ln == 'image_overlays' and isinstance(_oi, int):
                _ovs = task.get('image_overlays', []) or []
                if 0 <= _oi < len(_ovs) and isinstance(_ovs[_oi], dict):
                    _ov_c = _ovs[_oi]
                    _els_c = _ov_c.get('elements') or []
                    if (not _ov_c.get('content')) and len(_els_c) == 1 \
                            and isinstance(_els_c[0], dict) and _els_c[0].get('kind') == 'image':
                        type_, idx = 'sticker', (_oi, 0)
        if type_ == 'sub':
            self._clear_canvas_pick()
            self._reset_sticker_ui_cache()
            self.sub_panel.load_data(task['style'])
            if hasattr(self, "role_panel"):
                segs = task.get("segments", []) or []
                if isinstance(idx, int) and 0 <= idx < len(segs):
                    seg = segs[idx]
                    role_id = seg.get("role", "default")
                    if role_id != "default":
                        self.role_panel._select_role(role_id)
                        r_start, r_end = self._get_role_segments_range(task, role_id)
                        if r_start is not None and r_end is not None:
                            self.role_panel.set_time_range(r_start, r_end, True)
                        role_tab_idx = self.tabs.indexOf(self.role_panel)
                        if role_tab_idx >= 0:
                            self.tabs.setCurrentIndex(role_tab_idx)
                    else:
                        sub_tab_idx = self.tabs.indexOf(self.sub_panel)
                        if sub_tab_idx >= 0:
                            self.tabs.setCurrentIndex(sub_tab_idx)
                        self.role_panel.set_time_range(seg.get("start", 0.0), seg.get("end", 0.0), True)
        elif type_ == 'overlay':
            prop_tab_idx = self.tabs.indexOf(self.prop_panel)
            if prop_tab_idx >= 0:
                self.tabs.setCurrentIndex(prop_tab_idx)
            if isinstance(idx, tuple):
                list_name, ov_idx = idx
            else:
                list_name, ov_idx = 'overlays', idx
            if list_name in task and 0 <= ov_idx < len(task[list_name]):
                if list_name == 'image_overlays':
                    self._selected_image_overlay = task[list_name][ov_idx]
                # 查找 overlay 所属的 Track（用于混合模式）
                ov_data = task[list_name][ov_idx]
                parent_track = None
                tl = _tl_of(task)
                if tl is not None:
                    ov_track_id = ov_data.get('_track_id')
                    _track_type_for_list = {'image_overlays': 'image', 'text_overlays': 'text', 'overlays': 'text'}
                    seek_type = _track_type_for_list.get(list_name, 'image')
                    for tr in getattr(tl, 'tracks', []):
                        if ov_track_id and getattr(tr, "id", None) == ov_track_id:
                            parent_track = tr
                            break
                        if getattr(tr, 'type', '') == seek_type and parent_track is None:
                            parent_track = tr
                self.prop_panel._ov_parent_track = parent_track
                self.prop_panel.load_data('overlay', ov_data)
                if list_name == 'image_overlays':
                    self.refresh_sticker_panel()
                for record in (self.video_canvas._hit_records or []):
                    if record.get('type') == 'overlay' and record.get('list') == list_name and record.get('idx') == ov_idx:
                        self._apply_canvas_hit(record, ("overlay", list_name, ov_idx))
                        break
        elif type_ == 'sticker':
            prop_tab_idx = self.tabs.indexOf(self.prop_panel)
            if prop_tab_idx >= 0:
                self.tabs.setCurrentIndex(prop_tab_idx)
            if isinstance(idx, tuple):
                ov_idx, el_idx = idx
            else:
                ov_idx, el_idx = None, None
            image_ovs = task.get('image_overlays', [])
            if isinstance(ov_idx, int) and isinstance(el_idx, int) and 0 <= ov_idx < len(image_ovs):
                elements = image_ovs[ov_idx].get('elements', [])
                if 0 <= el_idx < len(elements):
                    self._selected_sticker = elements[el_idx]
                    self._selected_image_overlay = image_ovs[ov_idx]
                    # 设置父 overlay 和 track 引用（供属性面板读取缩放/混合/羽化）
                    self.prop_panel._ov_parent_overlay = image_ovs[ov_idx]
                    parent_track = None
                    tl = _tl_of(task)
                    if tl is not None:
                        ov_tid = image_ovs[ov_idx].get('_track_id')
                        for tr in getattr(tl, 'tracks', []):
                            if ov_tid and getattr(tr, "id", None) == ov_tid:
                                parent_track = tr
                                break
                            if getattr(tr, 'type', '') == 'image' and parent_track is None:
                                parent_track = tr
                    self.prop_panel._ov_parent_track = parent_track
                    self.prop_panel.load_data('sticker', elements[el_idx])
                    self.refresh_sticker_panel()
                    for record in (self.video_canvas._hit_records or []):
                        if record.get('type') == 'sticker' and record.get('ov_idx') == ov_idx and record.get('el_idx') == el_idx:
                            self._apply_canvas_hit(record, ("sticker", ov_idx, el_idx))
                            break
        elif type_ == 'audio':
            self._clear_canvas_pick()
            self._reset_sticker_ui_cache()
            self.tabs.setCurrentIndex(2)
            clip_obj = self._get_audio_clip_by_index(task, idx)
            if clip_obj is not None:
                self.prop_panel.load_data('audio', clip_obj)
                targets = [clip_obj]
                tlw = getattr(self, "timeline", None)
                getter = getattr(tlw, "selected_audio_clips", None) if tlw is not None else None
                if callable(getter):
                    extra = getter()
                    if extra:
                        targets = extra
                if hasattr(self.prop_panel, "set_audio_targets"):
                    self.prop_panel.set_audio_targets(targets)
        elif type_ == 'video_clip':
            self._reset_sticker_ui_cache()
            self.tabs.setCurrentIndex(2)
            # idx 是 Clip 对象（从 timeline item_selected 传来）
            clip_obj = idx
            # 找到所属 Track
            tl = _tl_of(task)
            track = None
            if tl is not None:
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "video" and clip_obj in getattr(tr, "clips", []):
                        track = tr
                        break
            self.prop_panel.load_data('video_clip', (track, clip_obj))
            self._reveal_video_clip(clip_obj)
        elif type_ == 'track':
            self._reset_sticker_ui_cache()
            prop_tab_idx = self.tabs.indexOf(self.prop_panel)
            if prop_tab_idx >= 0:
                self.tabs.setCurrentIndex(prop_tab_idx)
            track = idx
            if track is not None:
                self.prop_panel.load_data("track", track)
        elif type_ == 'transition':
            self._reset_sticker_ui_cache()
            self.tabs.setCurrentIndex(2)
            trans_obj = idx
            tl = _tl_of(task)
            track = None
            if tl is not None:
                for tr in tl.tracks:
                    if getattr(tr, "type", None) == "video" and trans_obj in getattr(tr, "transitions", []):
                        track = tr
                        break
            self.prop_panel.load_data('transition', (track, trans_obj))
            panel = getattr(self, "transition_panel", None)
            if panel is not None and hasattr(panel, "set_active_preset"):
                panel.set_active_preset(getattr(trans_obj, "preset", "") or "")

    # [v6.1] 此处原有一份 _resegment_role_range 的「placeholder implementation」，
    # 签名是 (task, role_id, start_ms, end_ms, role_raw_words)，函数体只是把
    # role_raw_words 去重后原样返回，注释里自己写着 "Actual logic ... would go here"。
    #
    # 它是**永远执行不到的死代码**：本类后面（原 23307 行）还有一份同名方法，
    # 签名为 (task, role_id, style, start, end)，Python 构建类字典时后者会覆盖前者。
    # 全部 4 处调用点传的也都是后者的参数形式。已移除，行为零变化。

    def _get_audio_clip_by_index(self, task, idx):
        """根据展开索引获取实际的 Clip 对象。"""
        if not isinstance(idx, int) or idx < 0:
            return None
        tl = _tl_of(task)
        if tl is None:
            return None
        flat_idx = 0
        for tr in tl.tracks:
            if getattr(tr, "type", None) != "audio":
                continue
            for clip in getattr(tr, "clips", []):
                if flat_idx == idx:
                    return clip
                flat_idx += 1
        return None

    def _reveal_video_clip(self, clip_obj):
        """Show the same selection box a picture gets. Do not drop an in-progress drag."""
        canvas = getattr(self, "video_canvas", None)
        if canvas is None or clip_obj is None:
            return
        for record in list(canvas._hit_records or []):
            if record.get("type") == "video_clip" and record.get("clip") is clip_obj:
                self._apply_canvas_hit(record, ("video_clip", clip_obj))
                return
        sel = getattr(canvas, "_selected_item", None)
        if isinstance(sel, dict) and sel.get("clip") is clip_obj:
            return
        self._clear_canvas_pick()

    def _apply_canvas_hit(self, record, ident):
        """Show the selection box. Do not cancel a drag that just started on it."""
        canvas = self.video_canvas
        canvas._selected_item = record
        canvas._selected_rect = record.get("rect")
        drag = getattr(canvas, "_dragging_item", None)
        same = False
        if isinstance(drag, dict) and ident and drag.get("type") == ident[0]:
            if ident[0] == "overlay":
                same = drag.get("list") == ident[1] and drag.get("idx") == ident[2]
            elif ident[0] == "sticker":
                same = drag.get("ov_idx") == ident[1] and drag.get("el_idx") == ident[2]
            elif ident[0] == "video_clip":
                same = drag.get("clip") is ident[1]
        if not same:
            canvas._dragging_item = None
            canvas._drag_mode = None
            canvas._resize_handle = None
        canvas.update()

    def _on_canvas_gesture_began(self):
        """Freeze the pre-move project. The first move pushes that copy once."""
        # Q159：只拍当前这条（画布拖的只会是这条视频的东西），不再深拷整个工程
        self._canvas_gesture_before = self._undo_snapshot("task")
        self._canvas_gesture_pushed = False

    def _push_canvas_gesture_once(self):
        before = getattr(self, "_canvas_gesture_before", None)
        if before is None or getattr(self, "_canvas_gesture_pushed", False):
            return
        # Q128：对齐 / 粘贴样式这类一次性操作带自己的标签，拖动还是「拖动」
        label = getattr(self.video_canvas, "_gesture_label", None) or "拖动"
        self.history_mgr.push(label, before)
        self._canvas_gesture_pushed = True

    def _on_canvas_gesture_ended(self):
        if getattr(self, "_canvas_gesture_pushed", False):
            self.capture_undo_after()
        self._canvas_gesture_before = None
        self._canvas_gesture_pushed = False

    def _on_canvas_structure_changed(self):
        """Q145：画布上删除 / 粘贴 / 复制了文字或图片 → 时间线重建、预览刷新。"""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks or []):
            return
        task = self.tasks[self.curr_idx]
        try:
            dur = float(task.get("duration") or 0)
            for name in ("text_overlays", "image_overlays"):
                for ov in task.get(name) or []:
                    try:
                        dur = max(dur, float(ov.get("end") or 0))
                    except (TypeError, ValueError):
                        pass
            tl_dur = int(getattr(self.timeline, "base_duration", 0) or 0)
            self.timeline.set_data(max(int(dur * 1000), tl_dur), task)
        except Exception:
            import logging
            logging.getLogger("cutora.ui.main_window").exception("画布增删后刷新时间线失败")
        self.mark_dirty()
        self.update_preview()

    def _on_canvas_gesture_cancelled(self):
        """Q145：Esc 取消正在拖的 → 回到按下之前（撤销栈里也不留这一步）。"""
        before = getattr(self, "_canvas_gesture_before", None)
        pushed = getattr(self, "_canvas_gesture_pushed", False)
        self._canvas_gesture_before = None
        self._canvas_gesture_pushed = False
        if before is None or not pushed:
            return
        try:
            self.history_mgr.pop_last()
            self._restore_snapshot(before)
        except Exception:
            import logging
            logging.getLogger("cutora.ui.main_window").exception("取消拖动恢复失败")

    def _focus_property_panel(self):
        """文本工具条上的「更多…」：把右侧切到属性面板。"""
        try:
            i = self.tabs.indexOf(self.prop_panel)
            if i >= 0:
                self.tabs.setCurrentIndex(i)
        except Exception:
            pass

    def on_canvas_select(self, type_, idx):
        if not type_ and hasattr(self, "text_bar"):
            self.text_bar.hide()
        self._sync_timeline_from_canvas(type_, idx)
        self.on_timeline_select(type_, idx)
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        if type_ == "sub_span":
            if isinstance(idx, tuple) and len(idx) == 2:
                seg_idx, span_idx = idx
            else:
                seg_idx = idx
                span_idx = None
            segs = task.get("segments", [])
            if 0 <= seg_idx < len(segs):
                seg = segs[seg_idx]
                self._last_adv_text_selection = {"seg_idx": seg_idx, "start_char": None, "end_char": None, "text": seg.get("text", "")}
                self._refresh_adv_block_list(seg)
                if hasattr(self, "word_block_panel"):
                    if span_idx is not None and 0 <= span_idx < self.word_block_panel.list_blocks.count():
                        self.word_block_panel.list_blocks.setCurrentRow(span_idx)
            return

        if type_ == "sub" and isinstance(idx, int):
            segs = task.get("segments", [])
            if 0 <= idx < len(segs):
                seg = segs[idx]
                self._last_adv_text_selection = {"seg_idx": idx, "start_char": None, "end_char": None, "text": seg.get("text", "")}
                self._refresh_adv_block_list(seg)
                if hasattr(self, "word_block_panel"):
                    self.word_block_panel.set_adv_block_label("字块选区：未选择")

    def on_canvas_edit_request(self, type_, payload):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        if type_ == "overlay":
            list_name, idx = payload
            if list_name in task and isinstance(idx, int) and 0 <= idx < len(task[list_name]):
                self.open_rich_text_editor("overlay", task[list_name][idx])
        elif type_ == "sticker":
            ov_idx, el_idx = payload
            image_ovs = task.get("image_overlays", [])
            if isinstance(ov_idx, int) and isinstance(el_idx, int) and 0 <= ov_idx < len(image_ovs):
                elements = image_ovs[ov_idx].get("elements", [])
                if 0 <= el_idx < len(elements):
                    self.open_rich_text_editor("sticker", elements[el_idx])

    def open_rich_text_editor(self, item_type, data):
        if not isinstance(data, dict):
            return
        if item_type == "overlay" and data.get("type") != "text":
            return
        if item_type == "sticker" and data.get("kind") != "text":
            return
        if not ENABLE_RICH_TEXT:
            dlg = SimpleTextEditorDialog(self, data.get("content", ""))
            if dlg.exec():
                data["content"] = dlg.get_text()
                data["content_format"] = "plain"
                data.pop("rich_content", None)
                if item_type == "overlay":
                    self.prop_panel.load_data("overlay", data)
                elif item_type == "sticker":
                    self.prop_panel.load_data("sticker", data)
                    self.refresh_sticker_panel()
                self.video_canvas.update()
                self.timeline.update()
            return
        base_style = {
            "font_family": sanitize_font_family(data.get("font_family", DEFAULT_FONT_FAMILY)),
            "fontsize": int(data.get("fontsize", 40)),
            "bold": bool(data.get("bold", False)),
            "italic": bool(data.get("italic", False)),
            "color": data.get("color", "#FFFFFF"),
            "bg_color": data.get("bg_color"),
            "bg_opacity": int(data.get("bg_opacity", 255)),
            "stroke_color": data.get("stroke_color", "#000000"),
            "stroke_width": int(data.get("stroke_width", 0)),
            "use_stroke": bool(data.get("use_stroke", False)),
            "shadow_color": data.get("shadow_color", "#000000"),
            "shadow_opacity": int(data.get("shadow_opacity", 120)),
            "shadow_blur": int(data.get("shadow_blur", 0)),
            "shadow_offset_x": int(data.get("shadow_offset_x", 4)),
            "shadow_offset_y": int(data.get("shadow_offset_y", 4)),
            "shadow_enabled": bool(data.get("shadow_enabled", False)),
        }
        spans = data.get("rich_content", []) if data.get("content_format") == "rich_spans" else []
        if not spans:
            spans = [{"text": data.get("content", ""), **base_style}]
        dlg = RichTextEditorDialog(self, base_style, spans=spans, layout=data.get("text_layout"))
        if dlg.exec():
            new_spans, new_layout = dlg.get_result()
            data["content_format"] = "rich_spans"
            data["rich_content"] = new_spans
            data["text_layout"] = new_layout
            data["content"] = _rich_plain_text(new_spans)
            for sp in new_spans:
                if isinstance(sp, dict) and sp.get("text") not in (None, "", "\n"):
                    from cutora.subtitle.io import canvas_size_for_window
                    from cutora.subtitle.stylepack import set_fontsize_px
                    data["font_family"] = sanitize_font_family(sp.get("font_family", base_style.get("font_family", DEFAULT_FONT_FAMILY)))
                    cw, ch = canvas_size_for_window(self)
                    set_fontsize_px(
                        data,
                        int(sp.get("fontsize", base_style.get("fontsize", 40))),
                        cw, ch)
                    data["bold"] = bool(sp.get("bold", base_style.get("bold", False)))
                    data["italic"] = bool(sp.get("italic", base_style.get("italic", False)))
                    data["color"] = sp.get("color", base_style.get("color", "#FFFFFF"))
                    break
            if item_type == "overlay":
                self.prop_panel.load_data("overlay", data)
            elif item_type == "sticker":
                self.prop_panel.load_data("sticker", data)
                self.refresh_sticker_panel()
            self.video_canvas.update()
            self.timeline.update()

    # ───────────────────────── Q154：预合成（原位分组） ─────────────────────────

    def _group_sync_from_inspector(self, _clip=None):
        """检查器改了锁住的组里的一条 → 外观参数抄给整组；再按循环铺满重算重复份。"""
        self.group_sync(getattr(self.prop_panel, "_video_clip", None)
                        if getattr(self.prop_panel, "current_type", None) == "video_clip"
                        else getattr(self.prop_panel, "current_data", None)
                        if getattr(self.prop_panel, "current_type", None) == "audio" else None)

    def group_sync(self, src_clip=None, *, resync=True) -> bool:
        from cutora.model import clip_groups as CG
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return False
        task = self.tasks[self.curr_idx]
        tl = task.get("timeline") if isinstance(task, dict) else None
        if tl is None or not hasattr(tl, "tracks"):
            return False
        changed = False
        try:
            if src_clip is not None and not isinstance(src_clip, dict) and CG.group_of(src_clip):
                changed = CG.sync_props(tl, src_clip) > 0
            if resync and CG.resync_task(task, self):
                changed = True
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception("预合成同步失败")
            return False
        if changed:
            tlw = getattr(self, "timeline", None)
            if tlw is not None:
                tlw.update()
        return changed

    def _on_timeline_item_dragged(self):
        """时间线松手 / 快捷操作以后刷新预览和面板。

        时间线的编辑都已经自己记了撤销（命令总线一条，或 Q145 的一次撤销），这里
        不能再让 update_preview 补一条「操作」—— 那条存的是改完以后的样子，
        Ctrl+Z 第一下会「没反应」，第二下又一次退两步。"""
        prev_flush = getattr(self, "_preview_flushing", False)
        prev_pushed = getattr(self, "_canvas_gesture_pushed", False)
        self._preview_flushing = True
        self._canvas_gesture_pushed = True
        try:
            sel = getattr(self.timeline, "selected_video_clip", None)
            self.group_sync(sel[1] if sel else None)
            self.on_canvas_drag()
        finally:
            self._preview_flushing = prev_flush
            self._canvas_gesture_pushed = prev_pushed

    def on_canvas_drag(self):
        self._push_canvas_gesture_once()
        self.update_preview()
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        # 如果 NLE 时间线当前有 video_clip / transition 选中，不允许 canvas _dragging_item
        # 覆盖属性面板（避免松开鼠标后属性面板从视频属性跳回文字/叠层属性）
        tl_video_sel = (getattr(self.timeline, "selected_video_clip", None) is not None or
                        getattr(self.timeline, "selected_transition", None) is not None)
        drag_item = getattr(self.video_canvas, "_dragging_item", None)
        if drag_item and drag_item.get('type') == 'sub':
            self.sub_panel.blockSignals(True)
            self.sub_panel.load_data(task['style'])
            self.sub_panel.blockSignals(False)
            seg_idx = drag_item.get('idx')
            segs = task.get('segments', [])
            if isinstance(seg_idx, int) and 0 <= seg_idx < len(segs):
                role_id = segs[seg_idx].get('role', 'default')
                if role_id == getattr(self.role_panel, "current_role_id", None):
                    style = task.get('style', {})
                    if role_id != "default":
                        role_info = (task.get("subtitle_roles", {}) or {}).get(role_id, {})
                        role_style = role_info.get("style") if isinstance(role_info, dict) else None
                        if isinstance(role_style, dict) and role_style:
                            style = role_style
                    self.role_panel.style_panel.blockSignals(True)
                    self.role_panel.style_panel.load_data(style)
                    self.role_panel.style_panel.blockSignals(False)
            return
        if drag_item and drag_item.get('type') == 'overlay':
            if tl_video_sel:
                return   # NLE 有视频选中 → 不覆盖属性面板
            list_name = drag_item.get('list')
            ov_idx = drag_item.get('idx')
            if list_name in task and isinstance(ov_idx, int) and 0 <= ov_idx < len(task[list_name]):
                self.prop_panel.blockSignals(True)
                self.prop_panel.load_data('overlay', task[list_name][ov_idx])
                self.prop_panel.blockSignals(False)
            return
        if drag_item and drag_item.get('type') == 'sticker':
            if tl_video_sel:
                return   # NLE 有视频选中 → 不覆盖属性面板
            ov_idx = drag_item.get('ov_idx')
            el_idx = drag_item.get('el_idx')
            image_ovs = task.get('image_overlays', [])
            if isinstance(ov_idx, int) and isinstance(el_idx, int) and 0 <= ov_idx < len(image_ovs):
                elements = image_ovs[ov_idx].get('elements', [])
                if 0 <= el_idx < len(elements):
                    self.prop_panel.blockSignals(True)
                    self.prop_panel.load_data('sticker', elements[el_idx])
                    self.prop_panel.blockSignals(False)
                    self.refresh_sticker_panel()
            return
        if drag_item and drag_item.get('type') == 'video_clip':
            clip_obj = drag_item.get('clip')
            if clip_obj is not None:
                tl = _tl_of(task)
                track = None
                if tl is not None:
                    for tr in tl.tracks:
                        if getattr(tr, "type", None) == "video" and clip_obj in getattr(tr, "clips", []):
                            track = tr
                            break
                self.prop_panel.blockSignals(True)
                self.prop_panel.load_data('video_clip', (track, clip_obj))
                self.prop_panel.blockSignals(False)
            return
        # Fallback refresh follows the panel that is already open.
        # A leftover timeline.selected_overlay must not steal it on mouse-up.
        ctype = getattr(self.prop_panel, "current_type", None)
        if ctype == "overlay":
            data = None
            ov_sel = getattr(self.timeline, "selected_overlay", None)
            if isinstance(ov_sel, tuple) and len(ov_sel) == 2:
                list_name, ov_idx = ov_sel
                rows = task.get(list_name) or []
                if isinstance(ov_idx, int) and 0 <= ov_idx < len(rows):
                    data = rows[ov_idx]
            if not isinstance(data, dict):
                data = getattr(self.prop_panel, "current_data", None)
            if isinstance(data, dict):
                self.prop_panel.blockSignals(True)
                self.prop_panel.load_data("overlay", data)
                self.prop_panel.blockSignals(False)
            return
        if ctype == "sticker":
            return
        if ctype == "audio":
            idx = self.timeline.selected_audio_idx
            clip_obj = self._get_audio_clip_by_index(task, idx) if idx is not None else None
            if clip_obj is not None:
                self.prop_panel.blockSignals(True)
                self.prop_panel.load_data("audio", clip_obj)
                self.prop_panel.blockSignals(False)
            return
        if ctype in (None, "sub") and len(self.timeline.selected_indices) == 1:
            self.sub_panel.blockSignals(True)
            self.sub_panel.load_data(task['style'])
            self.sub_panel.blockSignals(False)
            return

    def resegment_current_segments(self):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        segs = task.get('segments', [])
        if not segs:
            return
        from cutora.subtitle.layout import smart_split_enabled
        if smart_split_enabled(task.get('style')):
            # Q137：新断句法 —— 合并碎句、按意群切、量宽和渲染一致
            from cutora.subtitle.reflow import reflow_task
            reflow_task(task)
            return
        old_segs = segs
        all_words = []
        for seg in segs:
            for w in seg.get('words', []):
                if w.get('word'):
                    all_words.append(w)
        if not all_words:
            return
        style = task.get('style', self.default_style)
        video_w = None
        if hasattr(self.video_canvas, "_video_dims"):
            video_w = self.video_canvas._video_dims[0]
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            dims = task.get('dims') or [1920, 1080]
            video_w = dims[0] if isinstance(dims, (list, tuple)) and dims else 1920
        new_segs = resegment_words_by_style(all_words, style, video_w)
        # 角色映射：尽量按时间交集继承旧 role
        for ns in new_segs:
            ns.setdefault("role", "default")
            ns_s = ns.get("start")
            ns_e = ns.get("end")
            if not isinstance(ns_s, (int, float)) or not isinstance(ns_e, (int, float)):
                continue
            best_role = "default"
            best_overlap = 0.0
            for os in old_segs:
                os_s = os.get("start")
                os_e = os.get("end")
                if not isinstance(os_s, (int, float)) or not isinstance(os_e, (int, float)):
                    continue
                overlap = min(ns_e, os_e) - max(ns_s, os_s)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_role = os.get("role", "default")
            if best_overlap > 0:
                ns["role"] = best_role
        from cutora.subtitle.track_source import set_segments
        set_segments(task, new_segs)

    def _get_video_width(self, task):
        video_w = None
        if hasattr(self.video_canvas, "_video_dims"):
            video_w = self.video_canvas._video_dims[0]
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            dims = task.get('dims') if isinstance(task, dict) else None
            if isinstance(dims, (list, tuple)) and dims:
                video_w = dims[0]
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            video_w = 1920
        return video_w

    def _calc_textbox_visible_lines(self, style):
        if not isinstance(style, dict):
            return 1
        try:
            font_size = int(style.get('fontsize', 60))
        except Exception:
            font_size = 60
        font = get_font_manager().get_font(
            sanitize_font_family(style.get("font_family")),
            int(font_size),
            bold=bool(style.get('bold', True)),
            italic=bool(style.get('italic', False))
        )
        fm = QFontMetrics(font)
        line_h = fm.height()
        line_gap = int(style.get('adv_line_spacing', style.get('line_spacing', 0)) or 0) if bool(style.get('advanced_textbox_enabled', False)) else int(style.get('line_spacing', 0) or 0)
        line_step = max(1, line_h + line_gap)
        adv_h = float(style.get('advanced_textbox_h', 0) or 0)
        if adv_h <= 0:
            return max(1, int(style.get('wrap_lines', 2) or 2))
        return max(1, int(adv_h // line_step))

    def _pack_words_segment(self, words, role_id):
        if not words:
            return None
        text = " ".join([w.get('word', '') for w in words if w.get('word')]).strip()
        return {
            "start": words[0].get("start"),
            "end": words[-1].get("end"),
            "words": words,
            "text": text,
            "role": role_id
        }

    def _segment_words_by_textbox_capacity(self, words, style, video_w):
        if not words:
            return []
        if not isinstance(style, dict):
            return []
        if not bool(style.get('advanced_textbox_enabled', False)):
            return resegment_words_by_style(words, style, video_w)
        try:
            font_size = int(style.get('fontsize', 60))
        except Exception:
            font_size = 60
        if not isinstance(video_w, (int, float)) or video_w <= 0:
            video_w = 1920

        from cutora.subtitle.stylepack import line_max_width_px
        try:
            fs = float(style.get("fontsize") or font_size)
        except (TypeError, ValueError):
            fs = float(font_size)
        max_width_px = int(line_max_width_px(style, video_w, em=fs))
        max_width_px = max(80, int(style.get('advanced_textbox_w', max_width_px) or max_width_px))

        font = get_font_manager().get_font(
            sanitize_font_family(style.get("font_family")),
            int(font_size),
            bold=bool(style.get('bold', True)),
            italic=bool(style.get('italic', False))
        )
        fm = QFontMetrics(font)
        line_gap = int(style.get('line_spacing', 0) or 0)
        line_step = max(1, fm.height() + line_gap)
        adv_h = float(style.get('advanced_textbox_h', 0) or 0)
        if adv_h <= 0:
            max_lines = max(1, int(style.get('wrap_lines', 2) or 2))
        else:
            max_lines = max(1, int(adv_h // line_step))

        def pack_event_words(words_list):
            if not words_list:
                return None
            text = " ".join([w.get('word', '') for w in words_list if w.get('word')]).strip()
            return {
                "start": words_list[0].get("start"),
                "end": words_list[-1].get("end"),
                "words": words_list,
                "text": text
            }

        def would_fit(next_token):
            tokens = [w.get('word', '') for w in current_words if w.get('word')]
            if next_token:
                tokens.append(next_token)
            lines = wrap_tokens_by_pixel_width(tokens, fm, max_width_px, " ") if tokens else []
            return len(lines) <= max_lines

        events = []
        current_words = []
        for w in words:
            token = w.get('word', '')
            if not token:
                continue
            if current_words and not would_fit(token):
                packed = pack_event_words(current_words)
                if packed:
                    events.append(packed)
                current_words = []
            current_words.append(w)

        packed = pack_event_words(current_words)
        if packed:
            events.append(packed)
        return events

    def _seg_sort_key(self, seg):
        s = seg.get("start")
        if not isinstance(s, (int, float)):
            s = seg.get("end")
        if not isinstance(s, (int, float)):
            s = 0.0
        return s

    def _get_role_segments_range(self, task, role_id):
        segs = task.get("segments", []) if isinstance(task, dict) else []
        start = None
        end = None
        for seg in segs:
            if seg.get("role", "default") != role_id:
                continue
            s = seg.get("start")
            e = seg.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                continue
            start = s if start is None else min(start, s)
            end = e if end is None else max(end, e)
        return start, end

    def _recalc_role_by_id(self, task, role_id):
        role_info = (task.get("subtitle_roles", {}) or {}).get(role_id, {})
        role_style = role_info.get("style") if isinstance(role_info, dict) else None
        base_style = task.get("style", self.default_style)
        merged_style = copy.deepcopy(base_style if isinstance(base_style, dict) else {})
        if isinstance(role_style, dict):
            merged_style.update(role_style)
        if not bool(merged_style.get("advanced_textbox_enabled", False)):
            return False
        r_start, r_end = self._get_role_segments_range(task, role_id)
        if r_start is None or r_end is None:
            return False
        self._resegment_role_range(task, role_id, merged_style, r_start, r_end)
        return True

    def _build_words_from_text(self, text, start, end):
        tokens, _ = tokenize_for_wrap(text or "")
        words = [t for t in tokens if t and (not t.isspace() or t == "\n")]
        if not words:
            return []
        s = float(start) if isinstance(start, (int, float)) else 0.0
        e = float(end) if isinstance(end, (int, float)) else s
        duration = max(0.0, e - s)
        n = len(words)
        out = []
        for i, tok in enumerate(words):
            if duration > 0:
                t0 = s + duration * (i / n)
                t1 = s + duration * ((i + 1) / n)
            else:
                t0, t1 = s, e
            out.append({"word": tok, "start": t0, "end": t1, "wid": None})
        return out

    def _align_text_truth_to_words(self, text_truth, words_info, seg_start, seg_end):
        text_truth = str(text_truth or "").strip()
        if not text_truth:
            return []
            
        # Re-tokenize preserving \n
        import re
        tokens = re.findall(r'\S+|\n', text_truth)
        new_words = []
        for t in tokens:
            if t == "\n":
                new_words.append(t)
            else:
                new_words.append(t)
                
        if not new_words:
            return []
            
        # Fallback to simple division if no timing available
        if not words_info:
            duration = seg_end - seg_start
            if duration <= 0: duration = 1.0
            w_dur = duration / len(new_words)
            return [{"word": w, "start": seg_start + i*w_dur, "end": seg_start + (i+1)*w_dur, "wid": None} for i, w in enumerate(new_words)]

        # 1. Gather old characters and their exact times
        old_chars = []
        for w in words_info:
            w_str = str(w.get("word", "")).strip()
            if not w_str: continue
            w_st = w.get("start", seg_start)
            w_en = w.get("end", seg_end)
            if w_en <= w_st: w_en = w_st + 0.05
            t_per_c = (w_en - w_st) / len(w_str)
            for i in range(len(w_str)):
                old_chars.append((w_st + i * t_per_c, w_st + (i + 1) * t_per_c))
                
        if not old_chars:
            duration = seg_end - seg_start
            if duration <= 0: duration = 1.0
            w_dur = duration / len(new_words)
            return [{"word": w, "start": seg_start + i*w_dur, "end": seg_start + (i+1)*w_dur, "wid": None} for i, w in enumerate(new_words)]

        # 2. Assign new characters sequentially to these old times
        new_words_info = []
        c_idx = 0
        tot_c = sum(len(w) for w in new_words)
        
        for w in new_words:
            w_len = len(w)
            # Map start and end based on exact linear interpolation of character distributions
            idx_start = int(round((c_idx / tot_c) * len(old_chars)))
            idx_end_inclusive = int(round(((c_idx + w_len) / tot_c) * len(old_chars))) - 1
            
            if idx_end_inclusive < idx_start: idx_end_inclusive = idx_start
            idx_start = max(0, min(idx_start, len(old_chars) - 1))
            idx_end_inclusive = max(0, min(idx_end_inclusive, len(old_chars) - 1))
            
            st = float(old_chars[idx_start][0])
            en = float(old_chars[idx_end_inclusive][1])
            if en <= st: en = st + 0.05
            
            new_words_info.append({"word": w, "start": st, "end": en, "wid": None})
            c_idx += w_len
            
        return new_words_info

    def _deduplicate_words_list(self, w_list):
        if not w_list: return []
        w_list.sort(key=lambda x: x.get("start", 0))
        res = [w_list[0]]
        for w in w_list[1:]:
            prev = res[-1]
            t_cur = w.get("start", 0)
            t_prev_end = prev.get("end", 0)
            if t_cur < t_prev_end - 0.05:
                s1 = str(prev.get("word", "")).strip().lower()
                s2 = str(w.get("word", "")).strip().lower()
                
                import string
                s1_c = s1.translate(str.maketrans('', '', string.punctuation))
                s2_c = s2.translate(str.maketrans('', '', string.punctuation))
                
                if s1_c == s2_c or s1_c in s2_c or s2_c in s1_c:
                    # KEEP prev, SKIP w
                    prev["end"] = max(t_prev_end, w.get("end", t_cur))
                    continue
            res.append(w)
        return res

    def _resegment_role_range(self, task, role_id, style, start=None, end=None):
        if not task or not role_id or not isinstance(style, dict):
            return
        segs = task.get('segments', [])
        if not segs:
            return
        video_w = self._get_video_width(task)

        use_range = isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start
        kept = []
        target_words = []
        target_segs = []
        target_texts = ""
        target_start = 0.0
        target_end = 0.0
        
        for seg in segs:
            seg_role = seg.get("role", "default")
            seg_start = seg.get("start")
            seg_end = seg.get("end")
            in_range = True
            if use_range and isinstance(seg_start, (int, float)) and isinstance(seg_end, (int, float)):
                if seg_end <= start or seg_start >= end:
                    in_range = False
            if seg_role != role_id or not in_range:
                kept.append(seg)
                continue
            target_segs.append(seg)
            seg_words = seg.get("words", []) or []
            if not seg_words:
                seg_words = self._build_words_from_text(seg.get("text", ""), seg.get("start"), seg.get("end"))
            for w in seg_words:
                t = w.get("start")
                if not isinstance(t, (int, float)):
                    t = w.get("end")
                if use_range and isinstance(t, (int, float)) and (t < start or t > end):
                    continue
                if w.get("word"):
                    target_words.append(w)
                    
            text_truth = seg.get("edited_text") or seg.get("text", "")
            if text_truth:
                if not target_texts:
                    target_texts = text_truth
                    target_start = seg.get("start", 0.0)
                    target_end = seg.get("end", 0.0)
                else:
                    if not target_texts.endswith("\n") and not target_texts.endswith(" "):
                        target_texts += " "
                    target_texts += text_truth
                    target_end = max(target_end, seg.get("end", 0.0))

        new_segments = []
        if target_words:
            target_words = self._deduplicate_words_list(target_words)
            aligned = self._align_text_truth_to_words(target_texts, target_words, target_start, target_end)
            if not aligned:
                aligned = self._build_words_from_text(target_texts, target_start, target_end)
                
            new_segments = self._segment_words_by_textbox_capacity(aligned, style, video_w)
            for ns in new_segments:
                ns["role"] = role_id
        from cutora.subtitle.track_source import set_segments
        set_segments(task, sorted(kept + new_segments, key=self._seg_sort_key))

    def on_recalc_textbox_global(self):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        style = task.get("style", self.default_style)
        if not isinstance(style, dict) or not bool(style.get("advanced_textbox_enabled", False)):
            return
        segs = task.get("segments", []) or []
        if not segs:
            return
        self.push_undo()
        self._recalc_textbox_for_task(task)
        self.sync_ui()
        self.timeline.update()
        self.video_canvas.update()

    def _recalc_textbox_for_task(self, task):
        style = task.get("style", self.default_style)
        if not isinstance(style, dict) or not bool(style.get("advanced_textbox_enabled", False)):
            return
        segs = task.get("segments", []) or []
        if not segs:
            return
        video_w = self._get_video_width(task)
        # Gather all raw words and concatenate all text truths per role
        role_raw_words = {}
        role_texts = {}
        role_start_end = {}
        
        for seg in segs:
            role_id = seg.get("role", "default")
            seg_words = seg.get("words", []) or []
            if not seg_words:
                seg_words = self._build_words_from_text(seg.get("text", ""), seg.get("start"), seg.get("end"))
            for w in seg_words:
                if w.get("word"):
                    role_raw_words.setdefault(role_id, []).append(w)
                    
            text_truth = seg.get("edited_text") or seg.get("text", "")
            if text_truth:
                if role_id not in role_texts:
                    role_texts[role_id] = text_truth
                    role_start_end[role_id] = [seg.get("start", 0.0), seg.get("end", 0.0)]
                else:
                    if not role_texts[role_id].endswith("\n") and not role_texts[role_id].endswith(" "):
                        role_texts[role_id] += " "
                    role_texts[role_id] += text_truth
                    # extend the end boundary
                    role_start_end[role_id][1] = max(role_start_end[role_id][1], seg.get("end", 0.0))
        
        new_role_map = {}
        for role_id, w_list in role_raw_words.items():
            w_list = self._deduplicate_words_list(w_list)
            text_truth = role_texts.get(role_id, "")
            t_start, t_end = role_start_end.get(role_id, [0.0, 0.0])
            
            aligned = self._align_text_truth_to_words(text_truth, w_list, t_start, t_end)
            if not aligned:
                aligned = self._build_words_from_text(text_truth, t_start, t_end)
            new_role_map[role_id] = aligned

        new_segments = []
        roles = task.get("subtitle_roles", {}) or {}
        for role_id, words in new_role_map.items():
            merged_style = copy.deepcopy(style)
            if role_id != "default":
                role_info = roles.get(role_id, {})
                role_style = role_info.get("style") if isinstance(role_info, dict) else None
                if isinstance(role_style, dict):
                    merged_style.update(role_style)
            events = self._segment_words_by_textbox_capacity(words, merged_style, video_w)
            for ev in events:
                ev["role"] = role_id
            new_segments.extend(events)
        from cutora.subtitle.track_source import set_segments
        set_segments(task, sorted(new_segments, key=self._seg_sort_key))

    def on_recalc_textbox_role(self):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        role_id = getattr(self.role_panel, "current_role_id", None)
        if not role_id:
            return
        role_info = (task.get("subtitle_roles", {}) or {}).get(role_id, {})
        role_style = role_info.get("style") if isinstance(role_info, dict) else None
        base_style = task.get("style", self.default_style)
        merged_style = copy.deepcopy(base_style if isinstance(base_style, dict) else {})
        if isinstance(role_style, dict):
            merged_style.update(role_style)
        if not bool(merged_style.get("advanced_textbox_enabled", False)):
            return
        self.push_undo()
        use_range = bool(getattr(self.role_panel, "use_time_range", None) and self.role_panel.use_time_range.isChecked())
        start = self.role_panel.range_start.value() if use_range else None
        end = self.role_panel.range_end.value() if use_range else None
        if not use_range or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            r_start, r_end = self._get_role_segments_range(task, role_id)
            if r_start is not None and r_end is not None:
                start, end = r_start, r_end
                self.role_panel.set_time_range(start, end, True)
        self._resegment_role_range(task, role_id, merged_style, start, end)
        self.sync_ui()
        self.timeline.update()
        self.video_canvas.update()

    def on_sub_textbox_resized(self, payload):
        if self.curr_idx < 0 or not isinstance(payload, dict):
            return
        task = self.tasks[self.curr_idx]
        seg_idx = payload.get("seg_idx")
        segs = task.get("segments", [])
        if not isinstance(seg_idx, int) or seg_idx < 0 or seg_idx >= len(segs):
            return
        role_id = segs[seg_idx].get("role", "default")
        if role_id != "default":
            self.role_panel._select_role(role_id)
            self._recalc_role_by_id(task, role_id)
        else:
            style = task.get("style", self.default_style)
            if bool(style.get("advanced_textbox_enabled", False)):
                # 保存字块 → resegment → 恢复字块
                saved_blocks = self._save_adv_blocks_fulltext(task)
                self.on_recalc_textbox_global()
                if saved_blocks:
                    self._restore_adv_blocks_fulltext(task, saved_blocks)
        self.sync_ui()
        self.timeline.update()
        self.video_canvas.update()

    def on_sub_text_selected(self, payload):
        if not isinstance(payload, dict):
            return
        self._last_adv_text_selection = payload
        text = payload.get("text") or ""
        start = payload.get("start_char")
        end = payload.get("end_char")
        preview = "未选择"
        if isinstance(start, int) and isinstance(end, int) and text:
            lo = max(0, min(start, end))
            hi = max(0, max(start, end))
            preview = text[lo:hi].strip() or text[lo:hi]
        if hasattr(self.sub_panel, "set_adv_block_label"):
            self.sub_panel.set_adv_block_label(f"字块选区：{preview}")
        if hasattr(self.role_panel, "style_panel") and hasattr(self.role_panel.style_panel, "set_adv_block_label"):
            self.role_panel.style_panel.set_adv_block_label(f"字块选区：{preview}")
        seg_idx = payload.get("seg_idx")
        if self.curr_idx >= 0 and isinstance(seg_idx, int):
            task = self.tasks[self.curr_idx]
            segs = task.get("segments", [])
            if 0 <= seg_idx < len(segs):
                self._refresh_adv_block_list(segs[seg_idx])

    def _normalize_adv_spans(self, text, spans):
        if not text:
            return []
        if isinstance(spans, list) and spans:
            joined = "".join([str(s.get("text", "")) for s in spans if isinstance(s, dict)])
            if joined == text:
                return spans
        return [{"text": text}]

    def _apply_adv_spans_to_segment(self, seg, start_char, end_char, overrides):
        if not isinstance(seg, dict):
            return
        text = seg.get("text") or ""
        if not text:
            return
        if not isinstance(start_char, int) or not isinstance(end_char, int):
            return
        lo = max(0, min(start_char, end_char))
        hi = max(0, max(start_char, end_char))
        if lo >= hi:
            return
        spans = self._normalize_adv_spans(text, seg.get("adv_rich_spans"))
        new_spans = self._apply_adv_spans_to_text(text, spans, lo, hi, overrides)
        seg["adv_rich_spans"] = new_spans

    def _apply_adv_spans_to_text(self, text, spans, lo, hi, overrides):
        new_spans = []
        cursor = 0
        for sp in spans:
            if not isinstance(sp, dict):
                continue
            sp_text = str(sp.get("text", ""))
            sp_start = cursor
            sp_end = cursor + len(sp_text)
            cursor = sp_end
            if hi <= sp_start or lo >= sp_end:
                new_spans.append(sp)
                continue
            if lo > sp_start:
                pre_text = sp_text[:lo - sp_start]
                if pre_text:
                    pre_sp = dict(sp)
                    pre_sp["text"] = pre_text
                    new_spans.append(pre_sp)
            mid_start = max(0, lo - sp_start)
            mid_end = min(len(sp_text), hi - sp_start)
            mid_text = sp_text[mid_start:mid_end]
            if mid_text:
                mid_sp = dict(sp)
                mid_sp.update({k: v for k, v in overrides.items() if v is not None})
                mid_sp["text"] = mid_text
                new_spans.append(mid_sp)
            if hi < sp_end:
                post_text = sp_text[hi - sp_start:]
                if post_text:
                    post_sp = dict(sp)
                    post_sp["text"] = post_text
                    new_spans.append(post_sp)
        return new_spans

    def _rebuild_adv_spans_from_blocks(self, seg):
        if not isinstance(seg, dict):
            return
        text = seg.get("text") or ""
        if not text:
            seg.pop("adv_rich_spans", None)
            return
        blocks = seg.get("adv_blocks", [])
        if not blocks:
            seg.pop("adv_rich_spans", None)
            return
        spans = [{"text": text}]
        for b_idx, blk in enumerate(blocks):
            if not isinstance(blk, dict): continue
            s = blk.get("start_char")
            e = blk.get("end_char")
            if not isinstance(s, int) or not isinstance(e, int):
                continue
            lo = max(0, min(s, e))
            hi = max(0, max(s, e))
            if lo >= hi:
                continue
            overrides = blk.get("overrides", {}).copy() if isinstance(blk.get("overrides"), dict) else {}
            overrides["_block_idx"] = b_idx
            spans = self._apply_adv_spans_to_text(text, spans, lo, hi, overrides)
        seg["adv_rich_spans"] = spans if spans else [{"text": text}]

    # ══════════════════════════════════════════════════════════════
    # ── 跨 resegment 保存 / 恢复字块数据 ──
    # ══════════════════════════════════════════════════════════════

    def _save_adv_blocks_fulltext(self, task):
        """保存所有字块，将 char 索引转换为全文坐标（按角色分组）。"""
        segs = task.get('segments', [])
        saved = {}          # role_id → list of {block, full_start, full_end}
        role_offsets = {}    # role_id → 当前角色累计字符偏移
        for seg in segs:
            role = seg.get('role', 'default')
            offset = role_offsets.get(role, 0)
            text = seg.get('text', '')
            for blk in seg.get('adv_blocks', []):
                if blk.get('is_global'):
                    continue   # 全局字块不保存，恢复时自动生成
                entry = {
                    'block': copy.deepcopy(blk),
                    'full_start': offset + blk.get('start_char', 0),
                    'full_end': offset + blk.get('end_char', 0),
                }
                saved.setdefault(role, []).append(entry)
            role_offsets[role] = offset + len(text)
        return saved

    def _restore_adv_blocks_fulltext(self, task, saved):
        """resegment 后将用户字块映射回新 segments，并自动生成全局字块。"""
        segs = task.get('segments', [])

        # 构建每个新 segment 在全文中的位置（按角色分组）
        role_seg_ranges = {}   # role → [(seg_idx, full_start, full_end)]
        role_offsets = {}
        for si, seg in enumerate(segs):
            role = seg.get('role', 'default')
            offset = role_offsets.get(role, 0)
            text = seg.get('text', '')
            role_seg_ranges.setdefault(role, []).append((si, offset, offset + len(text)))
            role_offsets[role] = offset + len(text)

        # 清空所有新 segment 的字块
        for seg in segs:
            seg.pop('adv_blocks', None)
            seg.pop('adv_rich_spans', None)

        # 1. 为每个 segment 自动创建唯一的全局字块 [0, len(text)]
        for seg in segs:
            text = seg.get('text', '')
            if text:
                seg['adv_blocks'] = [{
                    "name": "全局字块",
                    "start_char": 0,
                    "end_char": len(text),
                    "is_global": True,
                    "z_order": 0,
                    "overrides": {}
                }]

        # 2. 将保存的用户字块映射到新 segment
        if saved:
            for role, entries in saved.items():
                ranges = role_seg_ranges.get(role, [])
                if not ranges:
                    continue
                for entry in entries:
                    blk = entry['block']
                    fs = entry['full_start']
                    fe = entry['full_end']

                    for si, ss, se in ranges:
                        if fe <= ss or fs >= se:
                            continue   # 无重叠
                        local_start = max(0, fs - ss)
                        local_end = min(se - ss, fe - ss)
                        if local_start >= local_end:
                            continue
                        new_blk = copy.deepcopy(blk)
                        new_blk['start_char'] = local_start
                        new_blk['end_char'] = local_end
                        segs[si].setdefault('adv_blocks', []).append(new_blk)
                        # 如果是分离字块，切割该 segment 的全局字块
                        if new_blk.get('overrides', {}).get('is_detached'):
                            self._split_global_blocks_on_detach(segs[si], local_start, local_end)
                        break   # 用户字块只映射到第一个重叠 segment

        # 3. 重建所有 segment 的 spans
        for seg in segs:
            if seg.get('adv_blocks'):
                self._rebuild_adv_spans_from_blocks(seg)

    def _refresh_adv_block_list(self, seg):
        """刷新字块列表 UI。全局字块是真实 adv_block（is_global=True）。
        安全网：如果 TEXT 模式已开启但全局字块不存在，自动创建。"""
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task_style = self.tasks[self.curr_idx].get('style', self.default_style) or self.default_style
        else:
            task_style = self.default_style

        if not isinstance(seg, dict):
            if hasattr(self, "word_block_panel"):
                self.word_block_panel.set_adv_block_list([], "", task_style)
            return

        blocks = seg.get("adv_blocks", [])
        if not isinstance(blocks, list):
            blocks = []

        # 安全网：TEXT 模式已开启但全局字块不存在 → 自动创建
        is_text_mode = False
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task = self.tasks[self.curr_idx]
            task_style = task.get('style', self.default_style) or self.default_style
            is_text_mode = bool(task_style.get('advanced_textbox_enabled', False))

        if is_text_mode and not any(b.get("is_global") for b in blocks):
            text = seg.get("text", "")
            global_block = {
                "name": "字块1",
                "start_char": 0,
                "end_char": len(text),
                "is_global": True,
                "z_order": 0,
                "overrides": {}
            }
            blocks.insert(0, global_block)
            seg["adv_blocks"] = blocks
            self._rebuild_adv_spans_from_blocks(seg)

        if hasattr(self, "word_block_panel"):
            self.word_block_panel.set_adv_block_list(
                blocks, seg.get("text", ""), task_style)

    def on_adv_block_request(self):
        if self.curr_idx < 0:
            return
        sel = self._last_adv_text_selection or {}
        seg_idx = sel.get("seg_idx")
        start_char = sel.get("start_char")
        end_char = sel.get("end_char")
        task = self.tasks[self.curr_idx]
        segs = task.get("segments", [])
        if not isinstance(seg_idx, int) or seg_idx < 0 or seg_idx >= len(segs) or start_char is None or end_char is None:
            QMessageBox.information(self, "提示", "请先在画布上按住 Shift 拖拽选择文字范围")
            return
        
        self.push_undo("添加新字块")
        seg = segs[seg_idx]
        blocks = seg.get("adv_blocks", [])
        if not isinstance(blocks, list):
            blocks = []
        name = f"字块{len(blocks) + 1}"
        
        # 默认一律不启用字块分离，保持为内联样式叠加
        overrides = {"is_detached": False, "advanced_textbox_enabled": False}
        if hasattr(self, "video_canvas"):
            sc_w = self.video_canvas.width()
            sc_h = self.video_canvas.height()
            overrides["advanced_textbox_x"] = float(sc_w * 0.2)
            overrides["advanced_textbox_w"] = float(sc_w * 0.6)
            overrides["advanced_textbox_y"] = float(sc_h * 0.4)
            overrides["advanced_textbox_h"] = float(sc_h * 0.2)
        
        blocks.append({
            "name": name,
            "start_char": int(min(start_char, end_char)),
            "end_char": int(max(start_char, end_char)),
            "z_order": len(blocks),  # 渲染层级预留（值越大越在上层）
            "overrides": overrides
        })
        seg["adv_blocks"] = blocks
        self._rebuild_adv_spans_from_blocks(seg)
        self._refresh_adv_block_list(seg)
        if hasattr(self, "word_block_panel") and self.word_block_panel:
            self.word_block_panel.list_blocks.setCurrentRow(len(blocks) - 1)
        
        # 1. 成功创建字块后，清理高亮选区，但保留关键的段落追踪(seg_idx)供接下来的参数修改使用
        if isinstance(self._last_adv_text_selection, dict):
            self._last_adv_text_selection["start_char"] = None
            self._last_adv_text_selection["end_char"] = None
        # 2. 强行抹除画布上的橙色高亮选区矩形框，避免遮挡视线
        if hasattr(self, 'video_canvas'):
            self.video_canvas._sub_text_sel_rects = []
            self.video_canvas.update()
            
        self.timeline.update()

    def on_adv_block_edit(self, idx):
        if hasattr(self, "word_block_panel") and self.word_block_panel:
            # 避免死循环：只在选中行不同时才设置
            if self.word_block_panel.list_blocks.currentRow() != idx:
                self.word_block_panel.list_blocks.setCurrentRow(idx)
        
        # 在画布上选中该字块（如果已分离）
        if self.curr_idx >= 0:
            task = self.tasks[self.curr_idx]
            seg_idx = None
            if isinstance(self._last_adv_text_selection, dict):
                seg_idx = self._last_adv_text_selection.get("seg_idx")
            if isinstance(seg_idx, int):
                segs = task.get("segments", [])
                if seg_idx < len(segs):
                    seg = segs[seg_idx]
                    blocks = seg.get("adv_blocks", [])
                    if 0 <= idx < len(blocks):
                        ov = blocks[idx].get("overrides", {})
                        if ov.get("is_detached") and ov.get("advanced_textbox_enabled"):
                            self._auto_select_detached_block(seg_idx, idx)
                        else:
                            # 没分离的字块，取消画布上的文本框选中状态
                            if self.video_canvas._selected_item and self.video_canvas._selected_item.get('type') == 'sub_span':
                                self.video_canvas._selected_item = None
                                self.video_canvas._selected_rect = None
                                self.video_canvas.update()

    # ══════════════════════════════════════════════════════════════
    # ── 字块图层分割 / 合并（方案 A：真实数据分割）──
    # ══════════════════════════════════════════════════════════════

    def _split_global_blocks_on_detach(self, seg, det_start, det_end):
        """将全局字块按 [det_start, det_end] 真实切割。

        例：全局字块 [0, 20]，分离字块 [5, 10]
        → 全局字块被一分为二：[0, 5] 和 [10, 20]
        
        这是"剪切并新建图层"的核心算法。
        分离后每个块的 start_char/end_char 精确反映实际内容，
        确保包围盒计算和居中对齐始终精确。
        """
        blocks = seg.get("adv_blocks", [])
        new_blocks = []
        for blk in blocks:
            if not blk.get("is_global"):
                new_blocks.append(blk)
                continue

            gs, ge = blk["start_char"], blk["end_char"]

            # 无重叠 → 保留原样
            if ge <= det_start or gs >= det_end:
                new_blocks.append(blk)
                continue

            # 有重叠 → 切割
            # 左段 [gs, det_start)
            if gs < det_start:
                left = dict(blk)
                left["start_char"] = gs
                left["end_char"] = det_start
                left["overrides"] = dict(blk.get("overrides", {}))
                
                # 动态收紧剩余全局字的宽度边界
                task_style = self.tasks[self.curr_idx].get('style', {})
                font_size = int(left["overrides"].get('fontsize', task_style.get('fontsize', 60)))
                est_w = int(max(1, det_start - gs) * font_size * 0.7) + 80
                left["overrides"]["advanced_textbox_w"] = min(est_w, task_style.get('advanced_textbox_w', 1080))
                
                new_blocks.append(left)

            # 右段 [det_end, ge)
            if ge > det_end:
                right = dict(blk)
                right["start_char"] = det_end
                right["end_char"] = ge
                right["overrides"] = dict(blk.get("overrides", {}))
                
                # 动态收紧剩余全局字的宽度边界
                task_style = self.tasks[self.curr_idx].get('style', {})
                font_size = int(right["overrides"].get('fontsize', task_style.get('fontsize', 60)))
                est_w = int(max(1, ge - det_end) * font_size * 0.7) + 80
                right["overrides"]["advanced_textbox_w"] = min(est_w, task_style.get('advanced_textbox_w', 1080))
                
                new_blocks.append(right)

            # 若 gs >= det_start and ge <= det_end → 全局块完全被覆盖 → 不保留

        # CRITICAL FIX: Ensure the block array ALWAYS stays temporally sorted!
        new_blocks.sort(key=lambda b: (min(b.get("start_char", 0), b.get("end_char", 0)), max(b.get("start_char", 0), b.get("end_char", 0))))
        seg["adv_blocks"] = new_blocks

    def _merge_adjacent_global_blocks(self, seg):
        """合并相邻 / 紧邻的全局子块。

        当字块取消分离或被删除后，相邻的全局子块应重新合并
        以恢复连续性（例 [0,5]+[5,10] → [0,10]）。
        """
        blocks = seg.get("adv_blocks", [])
        globals_ = sorted(
            [b for b in blocks if b.get("is_global")],
            key=lambda x: x["start_char"]
        )
        non_globals = [b for b in blocks if not b.get("is_global")]

        # 收集所有已分离字块的范围，合并时不得覆盖
        detached_ranges = []
        for b in non_globals:
            if b.get('overrides', {}).get('is_detached', False):
                detached_ranges.append((b['start_char'], b['end_char']))

        merged = []
        for g in globals_:
            if (merged
                    and merged[-1]["end_char"] >= g["start_char"]
                    and not any(ds <= merged[-1]["end_char"] and de >= g["start_char"]
                                for ds, de in detached_ranges)):
                # 紧邻且中间无已分离字块 → 合并
                merged[-1]["end_char"] = max(merged[-1]["end_char"], g["end_char"])
            else:
                merged.append(dict(g))
                merged[-1]["overrides"] = dict(g.get("overrides", {}))

        seg["adv_blocks"] = merged + non_globals

    def _clamp_block_indices(self, seg):
        """安全钳位：确保所有字块索引不越界。
        
        预留接口 —— 当原始字幕文本被修改后，字符索引可能错乱。
        此方法将所有 start_char / end_char 钳位到 [0, len(text)]，
        防止渲染崩溃。完整的文本联动策略（锁定 / 提醒重置）留待后续迭代。
        """
        text = seg.get("text", "")
        text_len = len(text)
        for blk in seg.get("adv_blocks", []):
            blk["start_char"] = max(0, min(blk.get("start_char", 0), text_len))
            blk["end_char"]   = max(0, min(blk.get("end_char",   0), text_len))
            if blk["start_char"] > blk["end_char"]:
                blk["start_char"], blk["end_char"] = blk["end_char"], blk["start_char"]

    def _on_adv_block_modified(self, idx, new_block_data):
        print(f"[DETACH_DBG] _on_adv_block_modified called: idx={idx}")
        if self.curr_idx < 0:
            print(f"[DETACH_DBG] BAIL: curr_idx < 0")
            return
        task = self.tasks[self.curr_idx]
        seg_idx = None
        if isinstance(self._last_adv_text_selection, dict):
            seg_idx = self._last_adv_text_selection.get("seg_idx")
        if not isinstance(seg_idx, int):
            print(f"[DETACH_DBG] BAIL: seg_idx={seg_idx} is not int")
            return
        segs = task.get("segments", [])
        if seg_idx < 0 or seg_idx >= len(segs):
            print(f"[DETACH_DBG] BAIL: seg_idx={seg_idx} out of range")
            return
        seg = segs[seg_idx]
        
        # ── 直接使用 idx，不再做伪条目偏移 ──
        blocks = seg.get("adv_blocks", [])
        if not isinstance(blocks, list) or idx < 0 or idx >= len(blocks):
            print(f"[DETACH_DBG] BAIL: idx={idx} out of range, blocks count={len(blocks) if isinstance(blocks, list) else 'N/A'}")
            return
            
        old_block = blocks[idx]
        was_det = old_block.get('overrides', {}).get('is_detached', False)
        is_det = new_block_data.get('overrides', {}).get('is_detached', False)
        print(f"[DETACH_DBG] was_det={was_det} is_det={is_det}")
        
        if is_det and not was_det:
            # ═══ 分离操作：剪切并新建图层 ═══
            det_start = old_block.get('start_char', 0)
            det_end = old_block.get('end_char', 0)

            # 0. 快照当前全局视觉属性，使分离字块完全独立
            task_style = task.get('style', {})
            if 'overrides' not in new_block_data:
                new_block_data['overrides'] = {}
            ov = new_block_data['overrides']
            _LAYOUT_KEYS = {'is_detached', 'advanced_textbox_enabled', 'advanced_textbox_x', 'advanced_textbox_y', 'advanced_textbox_w', 'advanced_textbox_h'}
            
            effective_style = dict(task_style)
            if 'adv_rich_spans' in seg:
                for sp in seg['adv_rich_spans']:
                    if sp.get('_block_idx') == idx:
                        effective_style.update(sp)
                        break

            for vk, vv in effective_style.items():
                if vk not in ov and vk not in _LAYOUT_KEYS and not vk.startswith('_'):
                    ov[vk] = vv
            ov['use_box'] = False
            ov['is_metronome'] = False
            ov['dyn_box_anim'] = False
            ov['anim_in_type'] = 'none'
            ov['anim_out_type'] = 'none'
            ov['fade_in_out_enabled'] = False

            # 1. 基于视频尺寸和文本内容，计算合理的初始文本框大小
            ov['advanced_textbox_enabled'] = True

            video_w, video_h = 1080, 1920
            try:
                dims = task.get('dims')
                if dims:
                    video_w, video_h = int(dims[0]), int(dims[1])
            except Exception:
                pass

            # 取字块文本
            block_text = (seg.get('text', '') or '')[det_start:det_end]
            font_size = int(ov.get('fontsize', task_style.get('fontsize', 45)) or 45)

            # 动态收紧宽度，紧密包裹文本，防止分离出来的字块具有汪洋大海般的空白边缘
            est_w = int(max(1, len(block_text)) * font_size * 0.7) + 80
            est_w = min(est_w, int(video_w * 0.85))
            # 高度：按字体大小和文字量估算行数
            chars_per_line = max(1, est_w // max(1, int(font_size * 0.6)))
            est_lines = max(1, (len(block_text) + chars_per_line - 1) // chars_per_line)
            est_h = int(est_lines * font_size * 1.4 + font_size)
            est_h = max(est_h, int(font_size * 2.5))  # 至少 2.5 行高
            box_x = int((video_w - est_w) / 2)

            # 尝试从 hit_records 获取原始 Y 位置作为参考
            ref_y = int(video_h * 0.08)  # 默认放在画面上方 8%
            for record in reversed(self.video_canvas._hit_records):
                if record.get('type') == 'sub_span' and record.get('seg_idx') == seg_idx:
                    sp_idx = record.get('span_idx')
                    b_idx = -1
                    if 'adv_rich_spans' in seg and isinstance(sp_idx, int) and 0 <= sp_idx < len(seg['adv_rich_spans']):
                        b_idx = seg['adv_rich_spans'][sp_idx].get('_block_idx', -1)
                    if b_idx == idx:
                        ref_y = max(0, int(record['rect'].y()))
                        break

            ov['advanced_textbox_x'] = box_x
            ov['advanced_textbox_y'] = ref_y
            ov['advanced_textbox_w'] = est_w
            ov['advanced_textbox_h'] = est_h

            print(f"[DETACH] video={video_w}x{video_h} text='{block_text[:40]}...' font={font_size}")
            print(f"[DETACH] box: x={box_x} y={ref_y} w={est_w} h={est_h}")
            print(f"[DETACH] ov keys: {list(ov.keys())}")

            # 2. 更新当前 block
            blocks[idx] = new_block_data
            seg["adv_blocks"] = blocks

            # 3. 真实切割全局字块
            self._split_global_blocks_on_detach(seg, det_start, det_end)

            # 4. 重建 spans 和 UI
            self._rebuild_adv_spans_from_blocks(seg)
            self._refresh_adv_block_list(seg)
            self.video_canvas.update()

            # 5. 自动选中：利用统一接口，自动关联底层 block_idx
            # 5. 自动选中找回新索引
            new_idx = -1
            for bi, blk in enumerate(seg.get("adv_blocks", [])):
                if not blk.get("is_global") and blk.get("start_char") == det_start and blk.get("end_char") == det_end:
                    new_idx = bi
                    break
            if new_idx >= 0:
                self._auto_select_detached_block(seg_idx, new_idx)
            return

        elif not is_det and was_det:
            # ═══ 取消分离：重新合并全局字块 ═══
            # 移除独立坐标属性，让字块回到文本流
            ov = new_block_data.get('overrides', {})
            for key in ('advanced_textbox_enabled', 'advanced_textbox_x',
                        'advanced_textbox_y', 'advanced_textbox_w', 'advanced_textbox_h'):
                ov.pop(key, None)

            blocks[idx] = new_block_data
            seg["adv_blocks"] = blocks

            # 尝试恢复全局字块的连续性
            self._merge_adjacent_global_blocks(seg)
            self._rebuild_adv_spans_from_blocks(seg)
            self._refresh_adv_block_list(seg)
            self.video_canvas.update()
            return

        else:
            # ═══ 普通样式修改（颜色/字体/大小等）═══
            
            # ── 已分离字块自动修复：旧数据可能保存了极小或不存在的文本框 ──
            ov = new_block_data.get('overrides', {})
            if ov.get('is_detached'):
                # 确保 advanced_textbox_enabled 被显式开启
                ov['advanced_textbox_enabled'] = True
                
                cur_w = float(ov.get('advanced_textbox_w', 0) or 0)
                cur_h = float(ov.get('advanced_textbox_h', 0) or 0)
                # 仅对极其极其异常的 0 数据进行修复，不再干涉用户的自行缩小动作
                if cur_w <= 81 or cur_h <= 41:
                    video_w, video_h = 1080, 1920
                    det_start = new_block_data.get('start_char', 0)
                    det_end = new_block_data.get('end_char', 0)
                    block_text = (seg.get('text', '') or '')[det_start:det_end]
                    _task_style = task.get('style', {})
                    font_size = int(ov.get('fontsize', _task_style.get('fontsize', 45)) or 45)
                    
                    est_w = int(max(1, len(block_text)) * font_size * 0.7) + 80
                    est_w = min(est_w, int(video_w * 0.85))
                    est_h = max(80, int(font_size * 2.5))
                    
                    ov['advanced_textbox_w'] = est_w
                    ov['advanced_textbox_h'] = est_h
                    if float(ov.get('advanced_textbox_x', 0)) == 0:
                        ov['advanced_textbox_x'] = int((video_w - est_w) / 2)
                    if float(ov.get('advanced_textbox_y', 0)) == 0:
                        ov['advanced_textbox_y'] = int(video_h * 0.08)

            blocks[idx] = new_block_data
            seg["adv_blocks"] = blocks
            self._rebuild_adv_spans_from_blocks(seg)
            # Note: 不调用 _refresh_adv_block_list 以免拖拽滑块时丢失焦点
            self.video_canvas.update()

            # ── 已分离字块：自动在画布上显示选中框 ──
            if ov.get('is_detached') and ov.get('advanced_textbox_enabled'):
                self._auto_select_detached_block(seg_idx, idx)

    def _auto_select_detached_block(self, seg_idx, block_idx):
        """分离字块后自动选中它，在画布上显示选框和拖拽手柄。"""
        try:
            # 自动修复缺失坐标的遗留数据，防止初次点击拖动时飞出屏幕
            if self.curr_idx >= 0:
                task = self.tasks[self.curr_idx]
                segs = task.get('segments', [])
                if seg_idx < len(segs):
                    seg = segs[seg_idx]
                    blocks = seg.get('adv_blocks', [])
                    if block_idx < len(blocks):
                        blk = blocks[block_idx]
                        ov = blk.get('overrides', {})
                        if ov.get('is_detached'):
                            cur_w = float(ov.get('advanced_textbox_w', 0))
                            cur_x = float(ov.get('advanced_textbox_x', 0))
                            if cur_w < 100 or cur_x == 0:
                                ov['advanced_textbox_enabled'] = True
                                video_w, video_h = 1080, 1920
                                try:
                                    dims = task.get('dims')
                                    if dims:
                                        video_w, video_h = int(dims[0]), int(dims[1])
                                except Exception:
                                    pass
                                font_size = int(ov.get('fontsize', task.get('style', {}).get('fontsize', 45)) or 45)
                                est_w = int(video_w * 0.85)
                                ov['advanced_textbox_x'] = int((video_w - est_w) / 2)
                                ov['advanced_textbox_y'] = int(video_h * 0.08)
                                ov['advanced_textbox_w'] = est_w
                                ov['advanced_textbox_h'] = int(font_size * 2.5)
                                # 写回持久层，防止丢失
                                blk['overrides'] = ov
                                blocks[block_idx] = blk
                                seg['adv_blocks'] = blocks
                                self._rebuild_adv_spans_from_blocks(seg)

            canvas = self.video_canvas
            # 强制重绘以生成新的 hit_records
            canvas.update()
            canvas.repaint()
            # 在 hit_records 中查找刚分离的 sub_span
            for record in reversed(canvas._hit_records):
                if record.get('type') == 'sub_span' and record.get('seg_idx') == seg_idx:
                    # 验证 span 对应的 block idx
                    task = self.tasks[self.curr_idx]
                    segs = task.get('segments', [])
                    if seg_idx < len(segs):
                        seg = segs[seg_idx]
                        spans = seg.get('adv_rich_spans', [])
                        sp_idx = record.get('span_idx')
                        if isinstance(sp_idx, int) and 0 <= sp_idx < len(spans):
                            if spans[sp_idx].get('_block_idx') == block_idx:
                                canvas._selected_item = record
                                canvas._selected_rect = record.get('rect')
                                canvas.update()
                                return
            # 兜底：即使没找到精确的 hit_record，也尝试用 block 的坐标构造选中态
            if self.curr_idx >= 0:
                task = self.tasks[self.curr_idx]
                segs = task.get('segments', [])
                if seg_idx < len(segs):
                    seg = segs[seg_idx]
                    blocks = seg.get('adv_blocks', [])
                    if block_idx < len(blocks):
                        blk = blocks[block_idx]
                        ov = blk.get('overrides', {})
                        if ov.get('advanced_textbox_enabled'):
                            fake_rect = QRectF(
                                float(ov.get('advanced_textbox_x', 0)),
                                float(ov.get('advanced_textbox_y', 0)),
                                float(ov.get('advanced_textbox_w', 200)),
                                float(ov.get('advanced_textbox_h', 100))
                            )
                            fake_record = {
                                'type': 'sub_span',
                                'seg_idx': seg_idx,
                                'span_idx': -1,
                                'block_idx': block_idx,
                                'rect': fake_rect
                            }
                            canvas._selected_item = fake_record
                            canvas._selected_rect = fake_rect
                            canvas.update()
        except Exception:
            pass

    def on_adv_block_delete(self, idx):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        seg_idx = None
        if isinstance(self._last_adv_text_selection, dict):
            seg_idx = self._last_adv_text_selection.get("seg_idx")
        if not isinstance(seg_idx, int):
            QMessageBox.information(self, "提示", "请先选中包含字块的字幕")
            return
        segs = task.get("segments", [])
        if seg_idx < 0 or seg_idx >= len(segs):
            return
        seg = segs[seg_idx]

        # ── 直接使用 idx，不再做伪条目偏移 ──
        blocks = seg.get("adv_blocks", [])
        if not isinstance(blocks, list) or idx < 0 or idx >= len(blocks):
            return

        target = blocks[idx]
        if target.get("is_global"):
            QMessageBox.information(self, "提示", "全局字块不能被删除。")
            return

        self.push_undo()
        was_detached = target.get('overrides', {}).get('is_detached', False)
        blocks.pop(idx)
        seg["adv_blocks"] = blocks

        # 如果删除的是已分离字块 → 尝试恢复全局字块连续性
        if was_detached:
            self._merge_adjacent_global_blocks(seg)

        self._rebuild_adv_spans_from_blocks(seg)
        self._refresh_adv_block_list(seg)
        self.video_canvas.update()
        self.timeline.update()

    def _flush_coalesced_preview(self):
        self._preview_flushing = True
        try:
            self.update_preview(getattr(self, "_preview_pending_key", None))
        finally:
            self._preview_flushing = False
            self._preview_pending_key = None

    def update_preview(self, key=None):
        canvas = getattr(self, "video_canvas", None)
        dragging = bool(canvas is not None and getattr(canvas, "_dragging_item", None))
        if not getattr(self, "_preview_flushing", False) and not dragging:
            # T04f-1d-1 §1.5: slider ticks coalesce to 50 ms; mouse-up waits the timer.
            self._preview_pending_key = key
            timer = getattr(self, "_preview_coalesce", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(50)
                timer.timeout.connect(self._flush_coalesced_preview)
                self._preview_coalesce = timer
            if not timer.isActive():
                timer.start()
            return
        self._sync_idle_task = None            # Q189h：参数改了 → 下面的 sync_ui 要重新出帧
        if key != "rich_content" and not getattr(self, "_canvas_gesture_pushed", False):
            self.push_undo()
        if key == 'adv_textbox_toggle':
            if self.curr_idx >= 0:
                task = self.tasks[self.curr_idx]
                style = task.get('style', self.default_style)
                if bool(style.get('advanced_textbox_enabled', False)):
                    # ═══ TEXT 模式刚启用 → 计算居中位置，创建全局字块 ═══
                    # 根据实际视频尺寸计算文本框居中位置
                    video_w, video_h = 1080, 1920
                    try:
                        dims = task.get('dims')
                        if dims:
                            video_w, video_h = int(dims[0]), int(dims[1])
                    except Exception:
                        pass
                    # 每次启用时重新居中文本框 + 强制居中对齐
                    box_w = int(video_w * 0.85)
                    box_h = int(video_h * 0.16)
                    box_x = int((video_w - box_w) / 2)
                    box_y = int((video_h - box_h) / 2)
                    style['advanced_textbox_x'] = box_x
                    style['advanced_textbox_y'] = box_y
                    style['advanced_textbox_w'] = box_w
                    style['advanced_textbox_h'] = box_h
                    style['advanced_textbox_align'] = 'center'
                    # 同步到 UI
                    if hasattr(self, 'sub_panel'):
                        self.sub_panel.load_data(style)
                    segs = task.get("segments", [])
                    for seg in segs:
                        blocks = seg.get("adv_blocks", [])
                        if not isinstance(blocks, list):
                            blocks = []
                        has_global = any(b.get("is_global") for b in blocks)
                        if not has_global:
                            text = seg.get("text", "")
                            global_block = {
                                "name": "全局字块",
                                "start_char": 0,
                                "end_char": len(text),
                                "is_global": True,
                                "z_order": 0,
                                "overrides": {}
                            }
                            blocks.insert(0, global_block)
                            seg["adv_blocks"] = blocks
                            self._rebuild_adv_spans_from_blocks(seg)
                    # 刷新当前选中 segment 的字块列表 UI
                    sel = self._last_adv_text_selection or {}
                    seg_idx = sel.get("seg_idx")
                    if isinstance(seg_idx, int) and 0 <= seg_idx < len(segs):
                        self._refresh_adv_block_list(segs[seg_idx])
                else:
                    self.resegment_current_segments()
        elif key in ('wrap_lines', 'wrap_width_percent', 'wrap_bounds', 'fontsize', 'font_family', 'word_mode'):
            is_text_mode = False
            if self.curr_idx >= 0:
                _ts = self.tasks[self.curr_idx].get('style', {})
                is_text_mode = bool(_ts.get('advanced_textbox_enabled', False))
            if is_text_mode:
                # TEXT 模式：用 _recalc_textbox_for_task 重排，保留字块数据
                task = self.tasks[self.curr_idx]
                saved_blocks = self._save_adv_blocks_fulltext(task)
                self._recalc_textbox_for_task(task)
                if saved_blocks:
                    self._restore_adv_blocks_fulltext(task, saved_blocks)
            else:
                from cutora.subtitle.layout import maybe_auto_relayout
                try:
                    maybe_auto_relayout(self.tasks[self.curr_idx], key)
                except Exception:
                    pass
        elif self.curr_idx >= 0:
            _ts = self.tasks[self.curr_idx].get("style", {})
            if not bool(_ts.get("advanced_textbox_enabled", False)):
                from cutora.subtitle.layout import maybe_auto_relayout
                try:
                    maybe_auto_relayout(self.tasks[self.curr_idx], key)
                except Exception:
                    pass
        clip = self._get_current_bgm_clip()
        if clip:
            self._apply_bgm_volume(clip)
        self.sync_ui()
        self.timeline.update()
        self.video_canvas.update()

    def open_export_settings(self):
        global EXPORT_VIDEO_MODE
        EXPORT_VIDEO_MODE = _t01_history.EXPORT_VIDEO_MODE
        dlg = QDialog(self)
        dlg.setWindowTitle("导出设置")
        dlg.setMinimumWidth(380)
        from cutora.ui.theme.tokens import SPACE_3
        layout = QVBoxLayout(dlg)
        layout.setSpacing(SPACE_3)

        layout.addWidget(QLabel("导出预设："))

        from cutora.render.export_profile import (
            list_profiles, resolve_profile_id, set_active_profile_id,
        )
        combo = QComboBox()
        rows = list_profiles()
        active = resolve_profile_id()
        for row in rows:
            combo.addItem(row.get("name") or row["id"], row["id"])
        idx = combo.findData(active)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addWidget(combo)

        desc_label = QLabel()
        desc_label.setWordWrap(True)
        desc_label.setObjectName("hint")
        by_id = {r["id"]: r for r in rows}
        def _update_desc(index):
            pid = combo.itemData(index)
            desc_label.setText((by_id.get(pid) or {}).get("note") or "")
        combo.currentIndexChanged.connect(_update_desc)
        _update_desc(combo.currentIndex())
        layout.addWidget(desc_label)

        layout.addSpacing(6)
        layout.addWidget(QLabel("平台："))
        from cutora.render.export_platform import (
            hint_text as _plat_hint_text,
            list_platforms,
            resolve_platform_id as resolve_platform_id_,
            set_active_platform_id,
        )
        plat_combo = QComboBox()
        plat_combo.setObjectName("export_platform")
        plat_rows = list_platforms()
        plat_active = resolve_platform_id_()
        for row in plat_rows:
            plat_combo.addItem(row.get("name") or row["id"], row["id"])
        plat_idx = plat_combo.findData(plat_active)
        plat_combo.setCurrentIndex(plat_idx if plat_idx >= 0 else 0)
        layout.addWidget(plat_combo)

        plat_hint = QLabel()
        plat_hint.setWordWrap(True)
        plat_hint.setObjectName("hint")
        def _update_plat_hint(index):
            # Suggestions only — do not apply canvas here.
            pid = plat_combo.itemData(index)
            wh = (1080, 1920)
            dur = 0.0
            if 0 <= getattr(self, "curr_idx", -1) < len(self.tasks or []):
                from cutora.model.canvas import canvas_wh as _cwh
                _t = self.tasks[self.curr_idx]
                wh = _cwh(_t)
                dur = float(_t.get("duration") or 0.0)
            plat_hint.setText(_plat_hint_text(pid, wh, dur))
        plat_combo.currentIndexChanged.connect(_update_plat_hint)
        _update_plat_hint(plat_combo.currentIndex())
        layout.addWidget(plat_hint)

        layout.addSpacing(6)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        from cutora.ui.i18n import localize_button_box
        localize_button_box(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec():
            pid = combo.currentData()
            try:
                set_active_profile_id(pid)
            except Exception:
                _t01_history.EXPORT_VIDEO_MODE = "compressed"
                _save_export_video_mode("compressed")
            EXPORT_VIDEO_MODE = _t01_history.EXPORT_VIDEO_MODE
            plat_id = plat_combo.currentData()
            try:
                set_active_platform_id(plat_id)
            except Exception as exc:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").exception(
                    "save export platform failed: %s", exc)

    def open_font_manager(self):
        dlg = FontManagerDialog(self, refresh_callback=self.refresh_font_registry)
        dlg.exec()

    def refresh_font_registry(self):
        load_embedded_fonts()
        refresh_all_font_combos()
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks):
            task = self.tasks[self.curr_idx]
            self.sub_panel.load_data(task.get("style", self.default_style))
            self.role_panel.style_panel.load_data(task.get("style", self.default_style))

    def _on_role_style_changed(self, payload):
        if self.curr_idx < 0 or not isinstance(payload, dict):
            return
        role_id = payload.get("role_id")
        key = payload.get("key")
        if role_id == "default":
            self.update_preview(key)
        else:
            if key == "adv_textbox_toggle":
                task = self.tasks[self.curr_idx]
                role_info = (task.get("subtitle_roles", {}) or {}).get(role_id, {})
                role_style = role_info.get("style") if isinstance(role_info, dict) else None
                base_style = task.get("style", self.default_style)
                merged_style = copy.deepcopy(base_style if isinstance(base_style, dict) else {})
                if isinstance(role_style, dict):
                    merged_style.update(role_style)
                if not bool(merged_style.get("advanced_textbox_enabled", False)):
                    use_range = bool(getattr(self.role_panel, "use_time_range", None) and self.role_panel.use_time_range.isChecked())
                    start = self.role_panel.range_start.value() if use_range else None
                    end = self.role_panel.range_end.value() if use_range else None
                    self._resegment_role_range(task, role_id, merged_style, start, end)
            # 非默认角色只刷新渲染，不触发重分段
            self.update_preview(None)

    def _on_roles_updated(self):
        self.timeline.update()
        self.video_canvas.update()

    def apply_role_mapping(self, payload):
        if self.curr_idx < 0 or not isinstance(payload, dict):
            return
        task = self.tasks[self.curr_idx]
        role_id = payload.get("role_id") or "default"
        query_raw = payload.get("text") or ""
        query = clean_text(query_raw).lower()
        # 剥离标点符号，避免用户输入的引号/逗号/括号导致匹配失败
        query = re.sub(r'[^\w\s]', ' ', query)
        import unicodedata
        query = unicodedata.normalize('NFC', query)
        if not query:
            QMessageBox.information(self, "提示", "请输入要匹配的文本内容")
            return
        use_range = bool(payload.get("use_range", True))
        start = float(payload.get("start", 0.0))
        end = float(payload.get("end", 0.0))
        if use_range and start > end:
            start, end = end, start

        segs = task.get("segments", []) or []
        if not segs:
            QMessageBox.information(self, "提示", "当前没有可绑定的字幕段")
            return

        # 按空格/换行拆分用户输入为短句/关键词
        query_tokens = [t for t in re.split(r"[\s]+", query) if t]
        if not query_tokens:
            QMessageBox.information(self, "提示", "请输入要匹配的文本内容")
            return

        # 时间范围优先过滤候选
        candidates = []
        if use_range:
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                s = seg.get("start")
                e = seg.get("end")
                if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                    continue
                if e < start or s > end:
                    continue
                candidates.append(seg)
        else:
            candidates = [seg for seg in segs if isinstance(seg, dict)]

        query_nospace = "".join(query_tokens)

        def _is_text_match(seg_text: str) -> bool:
            seg_clean = clean_text(seg_text or "").lower()
            seg_clean = re.sub(r'[^\w\s]', ' ', seg_clean)
            seg_clean = unicodedata.normalize('NFC', seg_clean)
            if not seg_clean.strip():
                return False
                
            seg_nospace = seg_clean.replace(' ', '')
            if not seg_nospace:
                return False
                
            # 1. 连续匹配（子串包含）：搜短词或贴长文案
            if seg_nospace in query_nospace or query_nospace in seg_nospace:
                return True
                
            # 2. 词级别高重合度判定
            seg_tokens = [t for t in re.split(r"[\s]+", seg_clean) if t]
            if seg_tokens:
                inter = set(query_tokens) & set(seg_tokens)
                # 段落大部分词在搜索词中 (长文案覆盖当前字幕)
                if len(inter) / max(1, len(seg_tokens)) >= 0.6:
                    return True
                # 搜索词大部分在段落中 (短搜索词匹配当前字幕)
                if len(inter) / max(1, len(query_tokens)) >= 0.6:
                    return True

            # 3. 单 token 兜底（适配无空格语言的容错匹配）
            if len(query_tokens) == 1 and len(query_tokens[0]) >= 2:
                import difflib
                matcher = difflib.SequenceMatcher(None, query_tokens[0], seg_nospace)
                match = matcher.find_longest_match(0, len(query_tokens[0]), 0, len(seg_nospace))
                if match.size / len(query_tokens[0]) > 0.7:
                    return True

            return False

        self.push_undo()
        conflicts = 0
        applied = 0
        for seg in candidates:
            if not isinstance(seg, dict):
                continue
            seg.setdefault("role", "default")
            if not _is_text_match(seg.get("text", "")):
                continue
            if seg.get("role") not in ("default", role_id):
                conflicts += 1
                continue
            seg["role"] = role_id
            applied += 1

        if conflicts:
            QMessageBox.information(self, "提示", "部分字幕已属于其他角色，未覆盖")
        if applied == 0:
            QMessageBox.information(self, "提示", "未在所选时间范围内找到匹配字幕")
        else:
            QMessageBox.information(self, "提示", f"已匹配 {applied} 条字幕")
            role_info = (task.get("subtitle_roles", {}) or {}).get(role_id, {})
            role_style = role_info.get("style") if isinstance(role_info, dict) else None
            base_style = task.get("style", self.default_style)
            merged_style = copy.deepcopy(base_style if isinstance(base_style, dict) else {})
            if isinstance(role_style, dict):
                merged_style.update(role_style)
            if bool(merged_style.get("advanced_textbox_enabled", False)):
                self._resegment_role_range(
                    task, role_id, merged_style,
                    start if use_range else None,
                    end if use_range else None
                )
        self.timeline.update()
        self.video_canvas.update()

    def smart_match_subtitle_range(self, payload):
        if self.curr_idx < 0 or not isinstance(payload, dict):
            return
        task = self.tasks[self.curr_idx]
        query_raw = payload.get("text") or ""
        query = clean_text(query_raw).lower()
        # 剥离标点符号，避免用户输入的引号/逗号/括号导致匹配失败
        query = re.sub(r'[^\w\s]', ' ', query)
        # Unicode 规范化，避免 NFC/NFD 差异导致重音字符不匹配
        import unicodedata
        query = unicodedata.normalize('NFC', query)
        if not query:
            QMessageBox.information(self, "提示", "请输入要匹配的文本内容")
            return

        tokens = [t for t in re.split(r"[\s]+", query) if t]
        if not tokens:
            QMessageBox.information(self, "提示", "请输入要匹配的文本内容")
            return

        print(f"[SMART_MATCH] query after clean: {repr(query)}")
        print(f"[SMART_MATCH] tokens ({len(tokens)}): {tokens[:20]}")

        words_seq = []
        segments = task.get("segments", []) or []
        for seg_idx, seg in enumerate(segments):
            for w in seg.get("words", []) or []:
                w_txt = re.sub(r'[^\w\s]', '', clean_text(w.get("word", "")).lower())
                w_txt = unicodedata.normalize('NFC', w_txt).strip()
                if not w_txt:
                    continue
                words_seq.append({
                    "text": w_txt,
                    "start": w.get("start"),
                    "end": w.get("end"),
                    "seg_idx": seg_idx
                })

        print(f"[SMART_MATCH] words_seq ({len(words_seq)}): {[w['text'] for w in words_seq[:30]]}")

        if not words_seq:
            QMessageBox.information(self, "提示", "当前字幕缺少词级时间信息")
            return

        # ═══ 模糊匹配算法 ═══
        # 将查询和字幕词拼接为连续字符串进行子串搜索，
        # 这样可以避免 WhisperX 分词差异导致的滑动窗口错位问题
        # (例如用户输入 "41 10" 但 WhisperX 合并为 "4110")
        query_joined = ' '.join(tokens)

        # 为每个 word 维护字符偏移映射
        word_char_starts = []  # word_idx -> 在拼接字符串中的起始位置
        word_char_ends = []
        concat_parts = []
        pos = 0
        for wi, wd in enumerate(words_seq):
            word_char_starts.append(pos)
            concat_parts.append(wd["text"])
            pos += len(wd["text"])
            word_char_ends.append(pos)
            if wi < len(words_seq) - 1:
                concat_parts.append(' ')
                pos += 1
        words_joined = ''.join(concat_parts)

        print(f"[SMART_MATCH] query_joined ({len(query_joined)} chars): {query_joined[:80]}")
        print(f"[SMART_MATCH] words_joined ({len(words_joined)} chars): {words_joined[:80]}...")

        # 方法1：尝试精确子串匹配（去掉空格后的连续匹配）
        query_nospace = query_joined.replace(' ', '')
        words_nospace = words_joined.replace(' ', '')

        # 建立 words_nospace 中每个字符→原始 words_joined 位置的映射
        nospace_to_orig = []
        for ci, ch in enumerate(words_joined):
            if ch != ' ':
                nospace_to_orig.append(ci)

        best_start_ci = -1
        best_end_ci = -1
        best_score = 0.0

        # 尝试在 words_nospace 中找到 query_nospace 的最佳匹配
        # 使用滑动窗口 + 字符级比较
        qlen = len(query_nospace)
        if qlen > 0 and qlen <= len(words_nospace):
            for i in range(len(words_nospace) - qlen + 1):
                matches = 0
                for j in range(qlen):
                    if words_nospace[i + j] == query_nospace[j]:
                        matches += 1
                score = matches / qlen
                if score > best_score:
                    best_score = score
                    best_start_ci = i
                    best_end_ci = i + qlen - 1
                if score >= 0.85:
                    break  # 足够好，提前退出

        print(f"[SMART_MATCH] best_score={best_score:.3f}")

        if best_score < 0.6:
            QMessageBox.information(self, "提示", "未能在字幕中找到该文本，请确认文本内容或语言一致")
            return

        # 将最佳匹配的字符范围映射回 word 索引
        orig_start = nospace_to_orig[best_start_ci] if best_start_ci < len(nospace_to_orig) else 0
        orig_end = nospace_to_orig[best_end_ci] if best_end_ci < len(nospace_to_orig) else len(words_joined) - 1

        # 找到覆盖 [orig_start, orig_end] 范围的 word 索引
        s_idx = 0
        e_idx = len(words_seq) - 1
        for wi in range(len(words_seq)):
            if word_char_ends[wi] > orig_start:
                s_idx = wi
                break
        for wi in range(len(words_seq) - 1, -1, -1):
            if word_char_starts[wi] < orig_end:
                e_idx = wi
                break

        print(f"[SMART_MATCH] matched word range: [{s_idx}, {e_idx}]")
        print(f"[SMART_MATCH] matched words: {[words_seq[k]['text'] for k in range(s_idx, min(e_idx+1, len(words_seq)))]}")

        start_word = words_seq[s_idx]
        end_word = words_seq[e_idx]
        if hasattr(self, "role_panel"):
            self.role_panel.set_time_range(start_word.get("start", 0.0), end_word.get("end", 0.0), True)

        hit_seg_ids = set()
        for k in range(s_idx, e_idx + 1):
            hit_seg_ids.add(words_seq[k]["seg_idx"])
        self.timeline.selected_indices = set(hit_seg_ids)
        self.timeline.update()
        self.video_canvas.update()

    def _get_video_time_offset_ms(self):
        if self.curr_idx < 0:
            return 0
        task = self.tasks[self.curr_idx]
        st = task.get("_thumbnail_state")
        if isinstance(st, dict) and st.get("enabled"):
            try:
                return int(float(st.get("duration", 0.0)) * 1000)
            except Exception:
                pass
        try:
            return int(float(task.get("video_time_offset", 0.0)) * 1000)
        except Exception:
            return 0

    def _timeline_to_video_ms(self, timeline_ms: int):
        offset_ms = self._get_video_time_offset_ms()
        return max(0, int(timeline_ms - offset_ms))

    def sync_ui(self):
        if self.curr_idx < 0:
            self._refresh_timecode()
            return
        offset_ms = self._get_video_time_offset_ms()
        now = time.time()
        if self._timeline_last_ts is None:
            self._timeline_last_ts = now

        clk = getattr(self, "_preview_clock", None)
        if clk is not None:
            clk.end_ms = self._timeline_end_ms()
            media_pos = None
            if self._proxy_ready and self._using_media_clock:
                media_pos = int(self.media_player.position())
                clk.use_media = True
                # Q157：预览混音比时间线短（后面是没声音的片段 / 渲染范围拉长了）。音频一放完
                # 播放器就停、位置归零 —— 以前播放头跟着跳回开头。这时改按墙钟接着走到时间线结尾。
                if (self._timeline_playing
                        and self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
                        and int(clk.ms) < int(clk.end_ms) - 50):
                    self._using_media_clock = False
                    clk.use_media = False
                    clk.last_ts = now
                    media_pos = None
            else:
                clk.use_media = False
            if not clk.playing and self._timeline_playing:
                clk.playing = True
                clk.last_ts = now
            clk.tick(now, media_pos)
            self._timeline_ms = int(clk.ms)
            if clk.playing != self._timeline_playing:
                self._timeline_playing = clk.playing
                if not clk.playing:
                    self.media_player.pause()
                    self.timer.setInterval(200)
                    self.timer.setTimerType(Qt.TimerType.CoarseTimer)
                    self._refresh_transport_play_icon()
        elif self._timeline_playing:
            if self._proxy_ready and self._using_media_clock:
                self._timeline_ms = int(self.media_player.position())
            else:
                self._timeline_ms = int(self._timeline_ms or 0) + int(
                    (now - (self._timeline_last_ts or now)) * 1000)
            self._timeline_last_ts = now
            end_ms = self._timeline_end_ms()
            if end_ms > 0 and int(self._timeline_ms or 0) >= end_ms - 20:
                self._timeline_ms = end_ms
                self.media_player.pause()
                self._timeline_playing = False
                self.timer.setInterval(200)
                self.timer.setTimerType(Qt.TimerType.CoarseTimer)
                self._refresh_transport_play_icon()
        from cutora.core.fps_meter import set_preview_counting
        set_preview_counting(bool(getattr(self, "_timeline_playing", False)))
        idle = self._idle_preview_hold()
        if getattr(self, "_opening_project", False):
            if not getattr(self, "_open_frame_scheduled", False):
                self._open_frame_scheduled = True
        elif not idle:
            self._present_preview_frame()
        self._refresh_fps_meter()
        # Q189g：暂停且播放头 / 工程没变 → 心跳不再要预览帧、不再重画时间线 / 画布。
        # 空工程本来就在上面 curr_idx < 0 处返回（0.01 核）；开着工程空转才是 0.4 核的源头。
        if idle:
            from cutora.core.counters import bump as _bump
            _bump("preview.idle_skip")
            d = self.media_player.duration()
            duration_ms = d + offset_ms if offset_ms > 0 else d
            if duration_ms > 0 and duration_ms != getattr(self.timeline, "base_duration", -1):
                self.timeline.set_data(duration_ms, self.tasks[self.curr_idx])
            self._timeline_last_ts = now
            self._refresh_timecode()
            return
        panel = getattr(self, "transcript_panel", None)
        if panel is not None:
            panel.set_playhead_ms(int(getattr(self, "_timeline_ms", 0) or 0))

        # 音效预览也跟着这个 33ms 的心跳走(见 _sync_sfx_preview)。
        # 光靠 positionChanged 不够: 那个信号由后端决定快慢, 慢的时候一秒才一次,
        # 音效会晚进、淡入淡出也会一格一格地跳。这里只是多调一次, 函数是幂等的。
        self._sync_sfx_preview()
        self._sync_preview_fx()
        self._sync_intro_preview_audio(offset_ms)

        self._timeline_last_ts = now
        pos = self._timeline_ms
        _seek_trace_emit(
            "heartbeat",
            timeline_ms=int(pos),
            player_ms=int(self.media_player.position()),
            using_media_clock=bool(self._using_media_clock),
            playing=bool(self._timeline_playing),
        )
        self.timeline.set_position(pos)
        d = self.media_player.duration()
        duration_ms = d + offset_ms if offset_ms > 0 else d
        if duration_ms > 0 and duration_ms != getattr(self.timeline, "base_duration", -1):
            self.timeline.set_data(duration_ms, self.tasks[self.curr_idx])
        self.tasks[self.curr_idx]['_player_ms'] = self.media_player.position()
        if not self._playback_full_frames():
            self.video_canvas.set_data(self.tasks[self.curr_idx], pos / 1000.0)

        # === 主视频变速 → 同步 QMediaPlayer 播放速率 ===
        task = self.tasks[self.curr_idx] if self.curr_idx >= 0 else None
        if task:
            tl = _tl_of(task)
            if tl is not None:
                main_clip = self.video_canvas._get_active_video_clip_preview(task, pos / 1000.0)
                clip_speed = float(getattr(main_clip, "speed", 1.0)) if main_clip else 1.0
                from cutora.audio.stem_cache import preview_clock_rate
                jkl = int(getattr(self, "_jkl_rate", 0) or 0)
                target_rate = preview_clock_rate(
                    stems_ready=_host_stems_ready(self),
                    jkl_rate=jkl,
                    clip_speed=clip_speed,
                )
                current_rate = self.media_player.playbackRate()
                if jkl == 0 and abs(current_rate - target_rate) > 0.01:
                    self.media_player.setPlaybackRate(target_rate)
                if not bool(getattr(self, "_timeline_playing", False)):
                    # 循环+匹配时长：跟时间线结尾，不跟播放器时长（播放器经常是短代理）
                    from cutora.model.match_span import refresh_match_targets
                    changed = refresh_match_targets(task)
                    if changed:
                        from cutora.core.log import get_logger
                        log = get_logger("cutora.ui.preview")
                        for vc, old, target in changed:
                            log.info(
                                "[Match] target %.3f -> %.3f start=%.3f",
                                old, target, float(getattr(vc, "start_t", 0.0) or 0.0))
                        self.mark_dirty()
                        self._schedule_stem_rebuild(immediate=False)
        self._note_idle_preview_sync()
        self._refresh_timecode()

    def _get_current_bgm_clip(self):
        """获取当前任务的 BGM clip（兼容 dict 格式，供 UI 播放使用）。"""
        if self.curr_idx < 0:
            return None
        task = self.tasks[self.curr_idx]
        ac_compat = _audio_clips_from_track(task)
        try:
            live_ids = self._preview_live_audio_ids()
        except Exception:
            live_ids = None
        # 优先找真正的配乐，跳过主音频和音效
        saw_bgm = False
        for c in ac_compat:
            if _is_bgm_clip(c) and not c.get("is_intro_video_leadin", False) and c.get("path"):
                saw_bgm = True
                obj = c.get("_clip_obj")
                if live_ids is not None and obj is not None and id(obj) not in live_ids:
                    continue
                return c
        if saw_bgm:
            return None
        # 回退到旧 audio_clips
        old_clips = task.get("audio_clips", [])
        if old_clips:
            c = old_clips[0]
            if isinstance(c, dict) and c.get("path"):
                return c
        return None

    def _ensure_bgm_source(self, clip):
        path = clip.get("path")
        if not path:
            return
        url = QUrl.fromLocalFile(path)
        if self.bgm_player.source() != url:
            self.bgm_player.setSource(url)

    def _apply_bgm_volume(self, clip):
        vol = float(clip.get("volume", 0.1))
        self.bgm_audio_output.setVolume(max(0.0, min(1.0, vol)))

    def _sync_intro_preview_audio(self, offset_ms):
        """Intro player + master duck. Proxy mix already has intro fades — skip both."""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks or []):
            return
        intro_state = self.tasks[self.curr_idx].get("_intro_video_state", {}) or {}
        intro_path = intro_state.get("intro_path")
        intro_end_ms = int(intro_state.get("effective_duration", 0.0) * 1000)
        t_dur_ms = int(intro_state.get("transition_duration", 0.0) * 1000)
        intro_in_t_ms = int(float(intro_state.get("intro_in_t", 0.0)) * 1000)
        proxy_mix = bool(getattr(self, "_proxy_ready", False)) or _host_stems_ready(self)
        if intro_path and hasattr(self, "intro_player"):
            if proxy_mix:
                if self.intro_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self.intro_player.pause()
            else:
                current_src = self.intro_player.source().toLocalFile()
                if os.path.normpath(current_src) != os.path.normpath(intro_path):
                    self.intro_player.setSource(QUrl.fromLocalFile(intro_path))
                if self._timeline_ms < intro_end_ms:
                    intro_src_pos_ms = self._timeline_ms + intro_in_t_ms
                    if self._timeline_playing:
                        if self.intro_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                            self.intro_player.play()
                        if abs(self.intro_player.position() - intro_src_pos_ms) > 200:
                            self.intro_player.setPosition(intro_src_pos_ms)
                    else:
                        if self.intro_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                            self.intro_player.pause()
                        if abs(self.intro_player.position() - intro_src_pos_ms) > 200:
                            self.intro_player.setPosition(intro_src_pos_ms)
                    if offset_ms > 0 and self._timeline_ms >= offset_ms and t_dur_ms > 0:
                        fade_prog = (self._timeline_ms - offset_ms) / float(t_dur_ms)
                        self.intro_audio_output.setVolume(max(0.0, 1.0 - fade_prog))
                    else:
                        self.intro_audio_output.setVolume(1.0)
                else:
                    if self.intro_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                        self.intro_player.pause()
        if proxy_mix:
            return
        if offset_ms > 0 and self._timeline_ms >= offset_ms and t_dur_ms > 0:
            fade_prog = (self._timeline_ms - offset_ms) / float(t_dur_ms)
            self.audio_output.setVolume(min(1.0, fade_prog))
        elif offset_ms > 0 and self._timeline_ms < offset_ms:
            self.audio_output.setVolume(0.0)
        else:
            if hasattr(self, "audio_output"):
                self.audio_output.setVolume(1.0)

    def _sync_bgm_state(self, state):
        if getattr(self, "_proxy_ready", False) or _host_stems_ready(self):
            try:
                self.bgm_player.pause()
            except Exception:
                pass
            return
        clip = self._get_current_bgm_clip()
        if not clip:
            self.bgm_player.pause()
            self.bgm_audio_output.setVolume(0.0)
            return
        self._ensure_bgm_source(clip)
        self._apply_bgm_volume(clip)
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.bgm_player.play()
        else:
            self.bgm_player.pause()

    def _sync_bgm_to_video(self, pos_ms: int):
        if getattr(self, "_proxy_ready", False) or _host_stems_ready(self):
            return
        clip = self._get_current_bgm_clip()
        if not clip or self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        start = float(clip.get("start", 0.0))
        trim_in = float(clip.get("trim_in", 0.0))
        target_bgm_ms = int((pos_ms / 1000.0 - start + trim_in) * 1000)
        current_bgm_ms = self.bgm_player.position()
        if abs(current_bgm_ms - target_bgm_ms) > 500:
            self.bgm_player.setPosition(target_bgm_ms)

    def run_align(self, is_batch=False):
        if self.curr_idx < 0:
            return
        self.tasks[self.curr_idx]['text'] = self.text_input.toPlainText()
        path = self.tasks[self.curr_idx].get("video") or ""
        try:
            from cutora.batch.media_guard import (
                PreflightError,
                preflight_for_auto_subtitle,
            )
            preflight_for_auto_subtitle(path)
        except PreflightError as exc:
            self.on_align_error(str(exc))
            return
        if not self._ensure_asr_model():
            return
        from cutora.asr.detect_plan import needs_detect
        from cutora.asr.languages import whisper_language_arg
        from cutora.core import config as cfg
        requested = getattr(cfg, "LANGUAGE", "en")
        task = self.tasks[self.curr_idx]
        forced = task.get("asr_force_language")
        if forced:
            self._begin_alignment(str(forced), is_batch)
            return
        if not needs_detect(requested):
            self._begin_alignment(whisper_language_arg(requested), is_batch)
            return
        stored = task.get("asr_language") or (task.get("asr_detect") or {}).get("language")
        if is_batch and stored:
            self._begin_alignment(stored, is_batch)
            return
        path = task.get("video") or ""
        self._start_detect_worker(
            [(self.curr_idx, path)],
            on_all=lambda pairs: self._on_single_detect_done(pairs, is_batch),
        )

    def open_glossary_dialog(self):
        from cutora.ui.glossary_dialog import GlossaryDialog
        dlg = GlossaryDialog(self)
        dlg.exec()

    def _begin_alignment(self, lang, is_batch=False):
        if not lang:
            return
        self._end_detect_progress()
        self.set_progress_mode("sub")
        style = self.tasks[self.curr_idx].get('style', self.default_style)
        video_w = self.video_canvas._video_dims[0] if hasattr(self.video_canvas, "_video_dims") else None
        from cutora.asr.glossary import prompt_and_hotwords
        from cutora.asr.models import get_model, model_folder
        prompt, hot = prompt_and_hotwords(
            getattr(self, "project_glossary", None),
            getattr(self, "_run_glossary_extra", None),
        )
        asr_dir = None
        rid = getattr(self, "_run_asr_id", None)
        if rid:
            row = get_model(rid)
            if row:
                asr_dir = model_folder(row)
        audio_path = self.tasks[self.curr_idx]['video']
        from cutora.asr.timeline_wav import is_multi_clip_timeline, render_timeline_wav
        if is_multi_clip_timeline(self.tasks[self.curr_idx]):
            try:
                audio_path = render_timeline_wav(self.tasks[self.curr_idx])
                self._timeline_asr_wav = audio_path
            except Exception as exc:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, "自动字幕",
                    "跨片段字幕还不能在这条时间线上跑：%s\n"
                    "（拼接时间线的整轨字幕会把音频渲成一条 wav 再识别。）" % exc)
                return
        self._retire_thread_attr("worker_ai")
        self.worker_ai = AlignmentWorker(
            audio_path,
            self.tasks[self.curr_idx]['text'],
            style=style, video_w=video_w, language=lang,
            initial_prompt=prompt, hotwords=hot, asr_dir=asr_dir)
        self.worker_ai.finished.connect(lambda s: self.on_align_done(s, is_batch))
        self.worker_ai.progress.connect(self.update_progress_value)
        self.worker_ai.error.connect(self.on_align_error)
        _q = getattr(self.worker_ai, "queued", None)
        if _q is not None:
            _q.connect(lambda who: self.statusBar().showMessage(
                "AI 字幕排队中：显卡正被「%s」使用，结束后自动开始" % (who or "其他 AI 任务"), 8000))
        self.worker_ai.start()

    def _ensure_asr_model(self) -> bool:
        from cutora.asr.models import get_model, status_row
        from cutora.ui.i18n import tr
        mid = getattr(self, "_run_asr_id", None) or "asr-small"
        row = get_model(mid)
        if row is None:
            return True
        st = status_row(row)
        if st.get("status") == "ready":
            return True
        size = st.get("size_mb") or "?"
        reply = QMessageBox.question(
            self, tr("需要下载模型"),
            tr("需要下载 {name}（{size} MB）。现在下载吗？",
               name=st.get("title_zh") or mid, size=size),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self.bus.execute("asr.models.download", {"id": mid}, source="ui")
        return True

    def _get_detect_fn(self):
        from cutora.asr.detect import detect_for_media
        return getattr(self, "_detect_fn", None) or detect_for_media

    def _begin_detect_progress(self):
        from cutora.ui.i18n import tr
        self.set_progress_mode("sub")
        if hasattr(self, "prog_main"):
            self.prog_main.setRange(0, 0)
        if hasattr(self, "prog_label"):
            self.prog_label.setText(tr("正在检测语言…"))
        if hasattr(self, "btn_prog_cancel"):
            self.btn_prog_cancel.show()

    def _end_detect_progress(self):
        if hasattr(self, "btn_prog_cancel"):
            self.btn_prog_cancel.hide()
        if hasattr(self, "prog_main"):
            self.prog_main.setRange(0, 100)

    def _cancel_detect(self):
        worker = getattr(self, "_detect_worker", None)
        if worker is not None:
            worker.cancel()
        self._end_detect_progress()
        self._dismiss_progress(force=True)
        self.is_batch_running = False

    def _start_detect_worker(self, jobs, on_all):
        from cutora.asr.detect_worker import DetectWorker
        old = getattr(self, "_detect_worker", None)
        if old is not None and old.isRunning():
            old.cancel()
            old.wait(100)
        self._begin_detect_progress()
        worker = DetectWorker(jobs, self._get_detect_fn(), parent=self)
        self._detect_worker = worker
        worker.all_done.connect(on_all)
        worker.failed.connect(self.on_align_error)
        worker.start()

    def _on_single_detect_done(self, pairs, is_batch):
        from cutora.asr.detect_plan import run_single_auto
        self._end_detect_progress()
        if not pairs:
            return
        media_id, det = pairs[0]
        task = self.tasks[media_id]
        chosen = []

        def confirm(detection):
            from cutora.ui.detect_confirm import ask_low_confidence
            return ask_low_confidence(self, detection)

        out = run_single_auto(
            media=task.get("video") or "",
            requested="auto",
            interactive=not is_batch,
            detect_fn=lambda _p: det,
            start_fn=chosen.append,
            ask_confirm_fn=confirm,
        )
        task["asr_detect"] = out.get("detection") or det
        if not out.get("started"):
            return
        task["asr_language"] = chosen[0]
        if is_batch:
            self._record_batch_detect_row(media_id, det)
        self._begin_alignment(chosen[0], is_batch)

    def _record_batch_detect_row(self, media_id, det):
        panel = getattr(self, "_asr_batch_results", None)
        if panel is None:
            return
        rec = {
            "media_id": media_id,
            "path": (self.tasks[media_id].get("video") if 0 <= media_id < len(self.tasks) else "") or "",
            "language": (det or {}).get("language"),
            "confidence": (det or {}).get("confidence"),
            "warn": bool((det or {}).get("low_confidence")),
        }
        rows = list(getattr(panel, "_rows", []) or [])
        replaced = False
        for i, old in enumerate(rows):
            if old.get("media_id") == media_id:
                rows[i] = rec
                replaced = True
                break
        if not replaced:
            rows.append(rec)
        panel.set_rows(rows)

    def _show_asr_batch_results(self, rows):
        panel = getattr(self, "_asr_batch_results", None)
        if panel is None:
            return
        panel.set_rows(rows)
        self._batch_results_open = True
        self._annotate_library_asr(rows)
        if hasattr(self, "left_tabs"):
            self.left_tabs.setCurrentIndex(0)

    def _hide_asr_batch_results(self):
        self._batch_results_open = False

    def _annotate_library_asr(self, rows):
        from PySide6.QtGui import QBrush, QColor
        fl = getattr(self, "file_list", None)
        if fl is None:
            return
        by_id = {}
        for rec in rows or []:
            try:
                by_id[int(rec.get("media_id"))] = rec
            except (TypeError, ValueError):
                continue
        for i, rec in by_id.items():
            item = fl.item(i)
            if item is None:
                continue
            path = rec.get("path") or ""
            name = os.path.basename(path) or item.text(0)
            lang = rec.get("language") or ""
            conf = rec.get("confidence")
            conf_txt = "" if conf is None else " %.0f%%" % (float(conf) * 100.0)
            extra = (("  " + str(lang)) if lang else "") + conf_txt
            item.setText(0, (name + extra).strip())
            if rec.get("warn"):
                from cutora.ui.theme.tokens import WARN
                item.setBackground(0, QBrush(QColor(WARN)))

    def _rerun_yellow_asr(self, ids):
        ids = [int(i) for i in (ids or [])]
        if not ids:
            return
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "asr"):
            return
        self.batch_queue = []
        for i in ids:
            if 0 <= i < len(self.tasks):
                self.batch_queue.append(i)
        if not self.batch_queue:
            return
        self.is_batch_running = True
        self.process_next_batch()

    def _rerun_corrected_asr(self, ids):
        ids = [int(i) for i in (ids or [])]
        if not ids:
            return
        from cutora.ui.gpu_notice import confirm as _gpu_ok
        if not _gpu_ok(self, "asr"):
            return
        panel = getattr(self, "_asr_batch_results", None)
        self.batch_queue = []
        for i in ids:
            if not (0 <= i < len(self.tasks)):
                continue
            lang = None
            if panel is not None:
                lang = panel.language_for(i)
            if lang:
                self.tasks[i]["asr_language"] = lang
                self.tasks[i]["asr_force_language"] = lang
                self.tasks[i]["asr_detect"] = {
                    "language": lang,
                    "confidence": 1.0,
                    "low_confidence": False,
                }
            self.batch_queue.append(i)
        if not self.batch_queue:
            return
        self.is_batch_running = True
        self.process_next_batch()

    def _prepare_batch_detect_async(self, indices):
        from cutora.asr.detect_plan import needs_detect
        from cutora.core import config as cfg
        requested = getattr(cfg, "LANGUAGE", "en")
        self._show_asr_batch_results([])
        if not needs_detect(requested):
            self.process_next_batch()
            return
        jobs = [(i, self.tasks[i].get("video") or "") for i in indices]
        self._start_detect_worker(
            jobs, on_all=lambda pairs: self._on_batch_detect_done(indices, pairs))

    def _on_batch_detect_done(self, indices, pairs):
        self._end_detect_progress()
        by_id = {int(mid): det for mid, det in (pairs or [])}
        rows = []
        for idx in indices:
            det = by_id.get(int(idx)) or {}
            rec = {
                "media_id": idx,
                "path": self.tasks[idx].get("video") or "",
                "language": det.get("language"),
                "confidence": det.get("confidence"),
                "warn": bool(det.get("low_confidence")),
                "detection": det,
            }
            self.tasks[idx]["asr_detect"] = det
            self.tasks[idx]["asr_language"] = det.get("language")
            rows.append(rec)
        self._show_asr_batch_results(rows)
        self.process_next_batch()

    def on_align_error(self, msg):
        from cutora.ui.i18n import tr
        text = tr("自动字幕失败：{msg}", msg=msg)
        self.set_progress_mode("sub")
        self.show_progress_error(text)
        self.statusBar().showMessage(text, 12000)
        banner = getattr(self, "_error_banner", None)
        if banner is not None:
            banner.show_error(text)

    def on_align_done(self, segs, is_batch):
        print(f"[SEG] count={len(segs)}")
        for i, seg in enumerate(segs[:3]):
            s = seg.get('start')
            e = seg.get('end')
            if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                d = e - s
                s_txt = f"{s:.3f}"
                e_txt = f"{e:.3f}"
                d_txt = f"{d:.3f}"
            else:
                s_txt = e_txt = d_txt = "NA"
            t = seg.get('text', '')
            print(f"[SEG] i={i} start={s_txt} end={e_txt} dur={d_txt} text={t}")
        for seg in segs:
            if isinstance(seg, dict) and "role" not in seg:
                seg["role"] = "default"
        from cutora.subtitle.seg_ids import ensure_segment_ids
        tmp = {"segments": segs}
        ensure_segment_ids(tmp)

        def _percentile(vals, p):
            if not vals:
                return None
            vals = sorted(vals)
            idx = int((len(vals) - 1) * p / 100)
            return vals[idx]

        def _stats(tag, seg_list):
            durs = []
            gaps = []
            prev_end = None
            for seg in sorted(seg_list, key=lambda s: s.get('start', 0)):
                s = seg.get('start')
                e = seg.get('end')
                if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
                    continue
                durs.append(e - s)
                if prev_end is not None:
                    gaps.append(s - prev_end)
                prev_end = e

            def _fmt(v):
                return f"{v:.3f}" if v is not None else "NA"

            print(f"[SEG] {tag} dur min={_fmt(min(durs) if durs else None)} p50={_fmt(_percentile(durs,50))} max={_fmt(max(durs) if durs else None)}")
            print(f"[SEG] {tag} gap min={_fmt(min(gaps) if gaps else None)} p50={_fmt(_percentile(gaps,50))}")
            print(f"[SEG] {tag} dur<0.35={sum(1 for d in durs if d < 0.35)}")

        _stats("before", segs)
        if self.enable_time_postprocess:
            segs = apply_time_offset(segs)
            segs = merge_short_segments(segs)
            _stats("after", segs)
        # 关键：无论是否开启 time_postprocess，都必须最后修复 segment 的 start/end
        # 不信任 whisperx 的 seg.end，按 word-level end / next.start 做多重兜底
        segs = fix_segment_durations(segs, min_dur=0.6, next_gap_pad=0.05, rebuild_words_first_only=True)
        self.push_undo()
        self._project_dirty = True
        if hasattr(self, "_refresh_window_title"):
            self._refresh_window_title()
        task = self.tasks[self.curr_idx]
        from cutora.subtitle.stylepack import ensure_default_pack
        # A pack applied here must not replace a position the project already
        # had, and must not invent an anchor that would paint somewhere else.
        _pre_style = task.get("style") if isinstance(task.get("style"), dict) else {}
        _pos_keys = (
            "pos_x", "pos_y", "anchor", "offset_x_pct", "offset_y_pct",
            "pos_x_pct", "pos_y_pct",
        )
        _had_pos = "pos_x" in _pre_style or "pos_y" in _pre_style
        _saved_pos = {k: copy.deepcopy(_pre_style[k]) for k in _pos_keys if k in _pre_style}
        ensure_default_pack(task)
        if _had_pos:
            _style_now = task.get("style") if isinstance(task.get("style"), dict) else None
            if isinstance(_style_now, dict):
                for _k in _pos_keys:
                    if _k in _saved_pos:
                        _style_now[_k] = _saved_pos[_k]
                    else:
                        _style_now.pop(_k, None)
        from cutora.subtitle.layout import (
            auto_relayout_enabled, fingerprints, relayout_new_segments,
        )
        prev = fingerprints(task.get("segments"))
        from cutora.subtitle.track_source import set_segments
        set_segments(task, segs)
        laid = 0
        if auto_relayout_enabled():
            laid = int((relayout_new_segments(task, prev) or {}).get("changed") or 0)
            segs = task.get("segments") or segs
            set_segments(task, segs)
        lang_used = task.get("asr_language")
        if lang_used:
            by_lang = task.setdefault("segments_by_language", {})
            by_lang[str(lang_used)] = segs
        rest = list(task.get("asr_lang_queue") or [])
        if rest:
            if rest[0] == lang_used:
                rest = rest[1:]
            task["asr_lang_queue"] = rest
            if rest:
                task["asr_language"] = rest[0]
                if is_batch:
                    self.batch_queue.insert(0, self.curr_idx)
        task_now = self.tasks[self.curr_idx]
        ensure_subs_track_for_segments(task_now)
        from cutora.subtitle.track_source import log_subtitle_source, set_segments
        set_segments(task_now, task_now.get("segments") or segs)
        log_subtitle_source("on_align_done", task_now)
        dur_ms = int(float(task_now.get("duration") or 0) * 1000)
        if dur_ms <= 0:
            dur_ms = int(self.media_player.duration() or 0)
        self.timeline.set_data(dur_ms, task_now)
        panel = getattr(self, "transcript_panel", None)
        if panel is not None:
            panel._reload_force_once = True
        self._refresh_task_view(self.curr_idx)
        try:
            self._autosave_now(force=True)
        except Exception:
            pass
        worker_ai = getattr(self, "worker_ai", None)
        note = getattr(worker_ai, "align_note", None) if worker_ai is not None else None
        approx = bool(getattr(worker_ai, "approximate_words", False)) if worker_ai is not None else False
        self.tasks[self.curr_idx]["word_timing"] = (
            "approximate" if approx else "aligned")
        if hasattr(self, "lbl_approx_words"):
            if approx:
                from cutora.ui.i18n import tr
                from cutora.asr.models import ALIGN_IT_SENTENCE_NOTE, ALIGN_NOT_BUNDLED_NOTE
                if note in (ALIGN_NOT_BUNDLED_NOTE, ALIGN_IT_SENTENCE_NOTE):
                    self.lbl_approx_words.setText(tr(note))
                else:
                    self.lbl_approx_words.setText(tr("近似词级"))
                self.lbl_approx_words.show()
            else:
                self.lbl_approx_words.hide()
        if laid:
            from cutora.ui.i18n import tr as _tr_lay
            lay_msg = _tr_lay("已按排版规则断行")
            panel = getattr(self, "transcript_panel", None)
            if panel is not None and hasattr(panel, "show_relayout_banner"):
                panel.show_relayout_banner()
            self.statusBar().showMessage(lay_msg, 8000)
        if note:
            if is_batch:
                print(note)
            elif note != "近似词级":
                QMessageBox.information(self, "提示", note)
            else:
                self.statusBar().showMessage(note, 8000)
        if is_batch: QTimer.singleShot(500, self.process_next_batch)

    def toggle_gpu(self):
        """切换 GPU 开关时，同步修正 DEVICE 与 COMPUTE_TYPE。

        [v6.1 修复] 原实现在这里写死了 `if 70 <= cc <= 90`，只认 RTX 20/30/40 系。
        而 config.py 的启动检测用的是 `60 <= cc <= 120`（含 Pascal 与 Blackwell）。
        两套白名单互相矛盾，导致 RTX 50 系 (sm_120) 用户只要手动点一下这个勾选框，
        就会弹出「不受支持」并被永久踢回 CPU 软件编码，而且再也勾不上。

        现在改为调用 config.detect_gpu_config() —— 与启动时完全同一套判断逻辑，
        单一事实源，不可能再出现分歧。
        """
        is_gpu_on = self.chk_gpu.isChecked()

        from cutora.core import config as config

        if not is_gpu_on:
            # 用户主动关闭 GPU
            config.DEVICE = "cpu"
            config.COMPUTE_TYPE = "float32"
            _sync_device_globals()
            _save_gpu_enabled(False)
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").info("[UI Switch] Switched to CPU (float32)")
            return

        device, compute_type, reason = config.detect_gpu_config()

        if device == "cuda":
            config.DEVICE = "cuda"
            config.COMPUTE_TYPE = compute_type
            _sync_device_globals()
            _save_gpu_enabled(True)
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").info(
                "[UI Switch] GPU 已启用: %s (compute_type=%s)", reason, compute_type)
            return

        # 检测未通过 → 安全回退到 CPU，并告知具体原因
        self.chk_gpu.blockSignals(True)
        self.chk_gpu.setChecked(False)
        self.chk_gpu.blockSignals(False)
        config.DEVICE = "cpu"
        config.COMPUTE_TYPE = "float32"
        _sync_device_globals()
        _save_gpu_enabled(False)
        QMessageBox.warning(self, "提示", f"已回退到 CPU。\n原因：{reason}")
        from cutora.core.log import get_logger
        get_logger("cutora.ui.main_window").info("[UI Switch] fallback to CPU: %s", reason)

    # ------------------------------------------------------------------
    # 脚本菜单：片段智能拼接（外挂 script_stitcher.py）
    # ------------------------------------------------------------------
    def _set_align_language(self, code):
        """运行时切换对齐/识别语言（含 auto 与全语种）。"""
        from cutora.asr.languages import EXPERIMENTAL_HINT, is_experimental, set_asr_language
        from cutora.ui.i18n import tr
        set_asr_language(code)
        if is_experimental(code):
            self.statusBar().showMessage(tr(EXPERIMENTAL_HINT), 8000)

    def open_script_stitcher(self):
        host = getattr(self, "room_host", None)
        if host is not None:                   # Q192：片段智能拼接是主窗口里的房间
            host.enter("stitch")
            return
        try:
            from cutora.batch import script_stitcher as script_stitcher
        except Exception as e:
            QMessageBox.warning(self, "外挂脚本加载失败",
                                f"无法加载 script_stitcher.py:\n{e}")
            return
        script_stitcher.open_stitcher_dialog(
            self, get_whisperx_model, get_align_model, self._on_stitcher_done)

    def open_script_broll_scatter(self):
        host = getattr(self, "room_host", None)
        if host is not None:                   # Q201：散落是批量加载最后一个 tab，不是房间
            host.enter("batch")
            dlg = host.widget("batch")
            tab = getattr(dlg, "scatter_tab", None) if dlg is not None else None
            tabs = getattr(dlg, "tabs", None) if dlg is not None else None
            if tabs is not None and tab is not None:
                tabs.setCurrentWidget(tab)
            return
        try:
            from cutora.batch import script_broll_scatter as script_broll_scatter
        except Exception as e:
            QMessageBox.warning(self, "外挂脚本加载失败",
                                f"无法加载 script_broll_scatter.py:\n{e}")
            return
        script_broll_scatter.open_dialog(self)

    def open_central_monitor(self):
        """Q144：中央监视器。媒体库里多选了就看选中的，否则看全部。Q191：主窗口里的一个房间。"""
        host = getattr(self, "room_host", None)
        if host is not None:
            from cutora.ui.rooms import monitor_tasks_from_selection
            host.enter("monitor", tasks=monitor_tasks_from_selection(self))
            return
        from cutora.ui.central_monitor import open_central_monitor
        tasks = list(self.tasks or [])
        try:
            rows = sorted({self.file_list.row(it) for it in self.file_list.selectedItems()})
        except Exception:
            rows = []
        if len(rows) > 1:
            tasks = [self.tasks[r] for r in rows if 0 <= r < len(self.tasks)]
        from cutora.ui.widgets.busy_bar import finish, report, start_busy
        start_busy(self, "正在打开中央监视器（%d 条）" % len(tasks))
        try:
            report(0.3, "排格子")
            open_central_monitor(self, tasks)
        finally:
            finish()

    def open_script_insert_image(self):
        host = getattr(self, "room_host", None)
        if host is not None:                   # Q191：批量加载是主窗口里的一个房间，不再是外置窗口
            host.enter("batch")
            return
        try:
            from cutora.batch import script_insert_image as script_insert_image
        except Exception as e:
            QMessageBox.warning(self, "外挂脚本加载失败",
                                f"无法加载 script_insert_image.py:\n{e}")
            return
        script_insert_image.open_dialog(self)

    def _on_stitcher_done(self, results, auto_ai):
        """拼接产出时间线工程（T20a），不再导入 _stitched mp4。"""
        if isinstance(results, dict) and results.get("kind") == "timeline":
            asprojs = [p for p in (results.get("asprojs") or []) if p]
            one = results.get("asproj")
            if one and one not in asprojs:
                asprojs.append(one)
            asprojs = [p for p in asprojs if os.path.isfile(p)]
            if asprojs:
                self._import_stitch_timeline_projects(asprojs)
            if auto_ai:
                self.start_batch_ai()
            return
        paths = [p for p, _ in results]
        if not paths:
            return
        self.on_drop(paths)
        text_map = {os.path.normpath(p): txt for p, txt in results}
        for t in self.tasks:
            key = os.path.normpath(t.get('video', ''))
            if key in text_map and not t.get('text'):
                t['text'] = text_map[key]
        if 0 <= self.curr_idx < len(self.tasks):
            cur = self.tasks[self.curr_idx]
            if os.path.normpath(cur.get('video', '')) in text_map:
                self.text_input.blockSignals(True)
                self.text_input.setText(cur.get('text', ''))
                self.text_input.blockSignals(False)
        if auto_ai:
            self.start_batch_ai()

    def _import_stitch_timeline_projects(self, asprojs):
        """N asproj → N library rows via the same populate path as open."""
        incoming = []
        errors = []
        for p in asprojs:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _normalize_project_data(data)
                self._announce_text_layer_migrate(data)
            except Exception as e:
                errors.append("%s: %s" % (os.path.basename(p), e))
                continue
            tasks = data.get("tasks") or []
            group = os.path.basename(
                os.path.dirname(os.path.dirname(os.path.abspath(p))))
            if (not group) or group in (".", "_stitched"):
                group = os.path.splitext(os.path.basename(p))[0]
            for t in tasks:
                if isinstance(t, dict) and not str(t.get("name") or "").strip():
                    t["name"] = group
                incoming.append(t)
        if not incoming:
            if errors:
                QMessageBox.warning(self, "导入工程", "\n".join(errors))
            return
        n_old = len(self.tasks or [])
        existing = []
        for t in (self.tasks or []):
            try:
                existing.append(serialize_render_task(t))
            except Exception:
                existing.append(t if isinstance(t, dict) else {})
        merged = {
            "version": PROJECT_VERSION,
            "tasks": existing + incoming,
            "curr_idx": n_old if incoming else (self.curr_idx if self.tasks else 0),
        }
        self.apply_project_data(merged)
        from cutora.subtitle.track_source import log_subtitle_source
        tasks = getattr(self, "tasks", None) or []
        idx = int(getattr(self, "curr_idx", -1) or -1)
        if 0 <= idx < len(tasks):
            log_subtitle_source("stitch_import", tasks[idx])
        self.project_path = None
        try:
            import re as _re
            first_p = str(asprojs[0]) if asprojs else ""
            m_dt = _re.search(r"(20\d{2}-\d{2}-\d{2})", first_p)
            if m_dt:
                day_str = m_dt.group(1)
                idx_dt = first_p.find(day_str)
                day_dir = first_p[: idx_dt + len(day_str)]
                if os.path.isdir(day_dir):
                    auto_proj = os.path.join(day_dir, f"{day_str}_智能拼接.cutora")
                    self.bus.execute("project.save_as", {"path": auto_proj}, source="api")
        except Exception:
            pass
        self._canvas_mismatch_asked = True

    def start_batch_ai(self):
        unaligned_count = sum(
            1 for t in self.tasks if not t['segments'] and t['text'].strip())
        force = False
        if unaligned_count == 0:
            has_text_count = sum(1 for t in self.tasks if t['text'].strip())
            if has_text_count <= 0:
                return
            reply = QMessageBox.question(
                self,
                '重新排版确认',
                '检测到所有视频已生成字幕。\n\n是否根据当前最新的字体格式参数，对所有视频进行一次强力的【全局换行与排版矫正】？\n\n警告：这将会重新打轴并覆盖所有视频现有的时间轴切片数据！',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            force = True
        try:
            self.bus.execute("subtitle.auto", {"force": force}, source="ui")
        except Exception as exc:
            from cutora.core.commands import CommandError
            if isinstance(exc, CommandError):
                self.on_align_error(str(exc))
                return
            raise

    def process_next_batch(self):
        if not getattr(self, "is_batch_running", False):
            return
        if not self.batch_queue:
            self.is_batch_running = False
            if getattr(self, "_queue_ai_active", False):
                self._queue_ai_active = False
                if getattr(self, "_queue_mode", False):
                    self._queue_after_auto_ai()
                return
            self._ui_information("Done", "Batch AI Finished")
            return
        idx = self.batch_queue.pop(0)
        self.file_list.setCurrentRow(idx)
        self.on_click(self.file_list.item(idx))
        self.run_align(is_batch=True)
    
    def update_batch_export_btn_text(self, item=None):
        if getattr(self, "is_batch_exporting", False):
            return
        checked = 0
        total = 0
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it:
                total += 1
                if it.checkState(0) == Qt.CheckState.Checked:
                    checked += 1
        if total == 0:
            self._set_batch_export_text("批量导出")
        elif checked == total:
            self._set_batch_export_text("导出全部")
        elif checked > 0:
            self._set_batch_export_text(f"导出选中项 ({checked})")
        else:
            self._set_batch_export_text("无可导出项")

    def toggle_batch_export(self):
        if self.batch_export_running:
            self.stop_batch_export()
            return
        from cutora.render.export_dest import prompt_export_folder
        folder = prompt_export_folder(self)
        if folder is False:
            return
        self._batch_export_out_dir = folder
        self.batch_export_running = True
        self.is_batch_exporting = True
        self._set_batch_export_text("停止批量导出")
        self.start_batch_export()

    def stop_batch_export(self):
        if getattr(self, "_queue_mode", False) and not getattr(self, "_queue_relaunching", False):
            try:
                from cutora.batch import cli as batch_cli
                batch_cli.pause_queue("user_stop")
            except Exception:
                pass
            self._queue_mode = False
        self._queue_ai_active = False
        self.is_batch_running = False
        self.batch_export_running = False
        self.is_batch_exporting = False
        self._batch_export_out_dir = None
        self.batch_queue = []
        # [v6.1] 并行模式下先终止所有渲染子进程
        mgr = getattr(self, "_parallel_mgr", None)
        if mgr is not None:
            try:
                mgr.stop()
            except Exception:
                pass
            self._parallel_mgr = None
            self.is_exporting = False
            self._resume_preview_timers()
        # 取消下一条导出的定时触发，避免旧回调串入导致“数量蹦掉/进度乱跳”
        if hasattr(self, "_batch_export_next_timer") and self._batch_export_next_timer.isActive():
            self._batch_export_next_timer.stop()
        self.update_batch_export_btn_text()
        self._set_batch_export_enabled(True)
        self.update_progress_value(0)
        self.stop_export()

    def start_batch_export(self):
        # 新一轮批量导出 token（防止上一轮遗留回调触发）
        self._batch_export_run_id = int(getattr(self, "_batch_export_run_id", 0)) + 1
        # 这一轮里每条视频的成品路径只算一次。并行失败后的串行重试沿用它，
        # 覆盖同一个文件。再算一次会看到文件已在，另存成 _2。
        self._batch_export_paths = {}
        self.batch_queue = [i for i in range(self.file_list.count()) if self.file_list.item(i) and self.file_list.item(i).checkState(0) == Qt.CheckState.Checked]
        self._batch_export_total = len(self.batch_queue)
        self._batch_export_current = 0
        if hasattr(self, "_batch_export_next_timer") and self._batch_export_next_timer.isActive():
            self._batch_export_next_timer.stop()
        if not self.batch_queue:
            self.batch_export_running = False
            self.is_batch_exporting = False
            self.update_batch_export_btn_text()
            return

        # ── [v6.1] 并行分支 ──
        # 只有在「性能」菜单里显式开启、且待导出数量 > 1 时才走并行。
        # 任何一步出问题都会 return False，自动落回下面原本的串行逻辑，
        # 所以这个分支不可能让导出功能变得不可用。
        # Q160：只有两三条视频时，整批并行（一条一个进程）用不满机器 —— 改成一条一条来、
        # 每条切段多进程（segment_export）。条数 ≥ 并行进程数才按整批并行。
        # Q187b：门槛改成「不到 4 条」。以前是「少于并行进程数（6）」：5 条视频被逐条切 3 段导，
        # 同时只有 3 个进程在跑、没有保活进度窗，比整批并行（5 个进程 + LPT 配平）慢了一半。
        _few = False
        try:
            from cutora.render import segment_export as _seg
            _few = bool(_seg.enabled() and len(self.batch_queue) < min(4, int(PARALLEL_EXPORT_WORKERS or 1))
                        and (os.cpu_count() or 4) >= _seg.MIN_CPU)
        except Exception:
            _few = False
        if _few and len(self.batch_queue) > 1:
            print("[Parallel] 视频只有 %d 条，改为逐条分段并行导出" % len(self.batch_queue))
        if PARALLEL_EXPORT_ENABLED and len(self.batch_queue) > 1 and not _few:
            self._parallel_cancelled = False
            if self._start_parallel_batch_export():
                return
            if getattr(self, "_parallel_cancelled", False):
                # [v6.2] 用户在前置提示框里点了「取消」——
                # 整个批量导出都不该开始，绝不能悄悄退化成串行导出。
                self.batch_queue = []
                self.batch_export_running = False
                self.is_batch_exporting = False
                self.update_batch_export_btn_text()
                print("[Parallel] 用户取消了本次批量导出")
                return
            print("[Parallel] 并行导出未能启动，已回退到串行模式")

        self.process_next_export()

    # ══════════════════════════════════════════════════════════════════════
    # [v6.1] 并行批量导出
    # ══════════════════════════════════════════════════════════════════════

    def _batch_output_path(self, task):
        """计算某个 task 的批量导出输出路径（与串行逻辑完全一致）。"""
        from cutora.render.export_params import batch_output_path
        dest = getattr(self, "_batch_export_out_dir", None)
        if dest:
            task["_export_dest_folder"] = dest
        return batch_output_path(task, dest)

    def _start_parallel_batch_export(self):
        """尝试以多进程方式执行当前 batch_queue。成功返回 True。"""
        EXPORT_VIDEO_MODE = _t01_history.EXPORT_VIDEO_MODE
        try:
            indices = list(self.batch_queue)
            jobs = []
            saved = getattr(self, "_batch_export_paths", None)
            if not isinstance(saved, dict):
                saved = {}
                self._batch_export_paths = saved
            for idx in indices:
                task = self.tasks[idx]
                out = saved.get(idx) or self._batch_output_path(task)
                saved[idx] = out
                jobs.append((task, out))
            if not jobs:
                return False

            workers = min(PARALLEL_EXPORT_WORKERS, len(jobs))
            want_gpu = bool(self.chk_gpu.isChecked())

            # ── [v6.2] 导出前体检 ────────────────────────────────────
            # 先把 AI 模型占的显存还给驱动，再按当时的内存/显存/CPU
            # 现状决定「开几个进程、几路能用 NVENC」。
            # 这一步就是「在崩之前先自己退一步」——
            # 显存不够时宁可全程 CPU 编码慢一点，也不要把桌面搞白屏。
            gpu_workers = None
            enc_threads = 0
            plan_notes = []
            if _GUARD is not None:
                try:
                    if want_gpu:
                        try:
                            freed = release_gpu_models()
                            if freed > 0.05:
                                plan_notes.append(f"已先释放 AI 模型占用的 {freed:.1f} GB 显存")
                        except Exception:
                            pass
                    canvas_wh = None
                    try:
                        # Q180：按这批里最多的画布尺寸估内存（以前只看第 1 个视频：41 个里唯一一个
                        # 1080×1920 排在第一 → 每进程按 3.7 GB 算 → 只派 3 个进程）
                        from cutora.render.export_guard import dominant_canvas
                        canvas_wh = dominant_canvas([t for t, _p in jobs])
                    except Exception:
                        canvas_wh = None
                    plan = _GUARD.plan_parallel_export(
                        len(jobs), workers, want_gpu, canvas_wh=canvas_wh)
                    workers = plan["workers"]
                    gpu_workers = plan["gpu_workers"]
                    enc_threads = plan["enc_threads"]
                    plan_notes.extend(plan.get("notes") or [])
                    try:
                        _mem_note = _GUARD.memory_hogs_note()
                        if _mem_note:
                            plan_notes.append(_mem_note)
                        _mem_note2 = _GUARD.gui_memory_note()
                        if _mem_note2:
                            plan_notes.append(_mem_note2)
                    except Exception:
                        pass
                    try:
                        plan_notes.extend(
                            _GUARD.font_coverage_notes([t for t, _p in jobs]))
                    except Exception:
                        pass
                    print(f"[Guard] 导出前体检：内存可用 {plan['ram_avail']}/{plan['ram_total']} GB，"
                          f"CPU {plan['cpu']} 核 → {workers} 个进程 / {gpu_workers} 路 GPU 编码 / "
                          f"每进程 FFmpeg {enc_threads} 线程")
                    for _n in plan_notes:
                        print(f"[Guard]   · {_n}")
                except Exception as e:
                    print(f"[Guard] 体检失败，按原设置导出: {e}")

            # ── [v6.2] 前置提示（可勾选不再显示） ────────────────────
            if not self._maybe_warn_before_parallel(len(jobs), workers, gpu_workers, plan_notes):
                self._parallel_cancelled = True
                return False

            # ── [v6.2] 主界面存活探针 ────────────────────────────────
            # 一个 1 秒的极轻定时器，回调里只记一个时间戳，别的什么都不做。
            # 它停了多久，就等于 Qt 主线程被卡了多久 —— 后台线程把这个数字
            # 写进心跳，保活窗口就能直接告诉用户「主界面卡了 37 秒，但在导」。
            self._start_ui_alive_probe()

            self._parallel_indices = indices
            self._parallel_mgr = ParallelExportManager(
                jobs,
                mode=self.render_mode,
                use_gpu=want_gpu,
                export_mode=EXPORT_VIDEO_MODE,
                workers=workers,
                parent=self,
                gpu_workers=gpu_workers,
                enc_threads=enc_threads,
            )
            self._parallel_mgr.progress.connect(self.update_progress_value)
            self._parallel_mgr.export_stats.connect(self._on_export_fps)
            self._parallel_mgr.stage.connect(self._on_parallel_stage)
            self._parallel_mgr.finished.connect(self._on_parallel_finished)
            self._parallel_mgr.degraded.connect(self._on_parallel_degraded)

            # 并行期间队列交由 manager 管理，清空串行队列避免重入
            self.batch_queue = []
            self.is_exporting = True
            self.set_progress_mode("export")
            self.update_progress_value(0)
            self.btn_exp.setEnabled(False)
            self._suspend_preview_timers()
            self.update_ui_state()

            self._parallel_mgr.start()
            return True

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Parallel] 启动失败: {e}")
            self._parallel_mgr = None
            return False

    def _on_parallel_stage(self, done, total):
        self._batch_export_current = max(1, min(total, done + 1))
        self._batch_export_total = total

    # ══════════════════════════════════════════════════════════════════
    # [v6.2] 导出保活相关的辅助方法
    # ══════════════════════════════════════════════════════════════════

    def _start_ui_alive_probe(self):
        self._ui_last_tick = time.time()
        if getattr(self, "_ui_alive_timer", None) is None:
            self._ui_alive_timer = QTimer(self)
            self._ui_alive_timer.setInterval(1000)
            self._ui_alive_timer.timeout.connect(self._on_ui_alive_tick)
        if not self._ui_alive_timer.isActive():
            self._ui_alive_timer.start()

    def _on_ui_alive_tick(self):
        self._ui_last_tick = time.time()
        self._check_export_stop_flag()

    def _export_state_path(self):
        from cutora.core.paths import resolve_user_data
        from cutora.render.export_guard import STATE_NAME
        return os.path.join(resolve_user_data(), STATE_NAME)

    def _check_export_stop_flag(self):
        try:
            from cutora.render.export_guard import stop_flag_exists
            path = self._export_state_path()
            if not stop_flag_exists(path):
                return
        except Exception:
            return
        self._honor_export_stop_flag()

    def _honor_export_stop_flag(self):
        try:
            from cutora.render.export_guard import clear_stop_flag
            clear_stop_flag(self._export_state_path())
        except Exception:
            pass
        mgr = getattr(self, "_parallel_mgr", None)
        if mgr is not None:
            try:
                mgr.stop()
            except Exception:
                pass
            self._parallel_mgr = None
        self.stop_export()
        try:
            self.statusBar().showMessage("导出已停止", 5000)
        except Exception:
            pass

    def _stop_ui_alive_probe(self):
        try:
            t = getattr(self, "_ui_alive_timer", None)
            if t is not None and t.isActive():
                t.stop()
        except Exception:
            pass

    def _on_parallel_degraded(self, reason):
        """看门狗触发降级时的界面反馈。刻意只改窗口标题 + 打印日志，
        不弹任何模态框 —— 此刻主线程可能已经很吃力，再弹窗只会更糟。"""
        print(f"[Guard] 自动降级：{reason}")
        try:
            self.setWindowTitle(format_window_title(extra=f"导出中（已自动降级：{reason}）"))
        except Exception:
            pass

    def _maybe_warn_before_parallel(self, n_jobs, workers, gpu_workers, notes):
        """并行导出前的一次性提示。返回 False 表示用户取消了本次导出。"""
        if getattr(self, "_queue_mode", False):
            return True
        try:
            s = _load_app_settings()
            if s.get("parallel_warn_shown"):
                return True
        except Exception:
            s = {}
        try:
            extra = ("\n\n本次体检结论：\n· " + "\n· ".join(notes)) if notes else ""
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("并行导出即将开始")
            box.setText(
                f"即将用 {workers} 个渲染进程同时导出 {n_jobs} 个视频"
                + (f"（其中 {gpu_workers} 路使用 GPU 编码）" if gpu_workers else "（全部使用 CPU 编码）")
                + "。\n\n"
                "⚠ 导出期间主界面可能会「未响应」甚至变成一块白板。\n"
                "这是 Windows 在主进程忙不过来时给窗口套的假图，\n"
                "**导出并没有停止**。\n\n"
                "屏幕右下角会出现一个独立的「导出监视器」小窗，\n"
                "它是单独的进程，主界面白板时它照样准确显示进度。\n"
                "输出目录里也会有一个「导出进行中.lock」文件同步更新。\n\n"
                "请以监视器为准：只要还在出新的 _sub.mp4，就不要强关软件、\n"
                "更不要重启电脑。" + extra)
            chk = QCheckBox("我知道了，以后不再提示")
            box.setCheckBox(chk)
            box.setStandardButtons(QMessageBox.StandardButton.Ok
                                   | QMessageBox.StandardButton.Cancel)
            from cutora.ui.i18n import localize_message_box
            localize_message_box(box)
            ret = box.exec()
            if chk.isChecked():
                try:
                    s["parallel_warn_shown"] = True
                    _save_app_settings(s)
                except Exception:
                    pass
            return ret == QMessageBox.StandardButton.Ok
        except Exception as e:
            print(f"[Guard] 前置提示失败（已跳过）: {e}")
            return True

    def _on_parallel_finished(self, failed_indices):
        """并行阶段结束。失败的视频退回串行路径重做，保证不丢任何一个。"""
        mgr = getattr(self, "_parallel_mgr", None)
        _reason = str(getattr(mgr, "_degrade_reason", "") or "") if mgr is not None else ""
        # 只有显存类降级才把串行补做改成 CPU；内存不够、甩负载和 NVENC 无关，
        # 强制 CPU 只会把 167 s 的片子拖到 2 分钟（2026-09-18 实测）。
        if _reason and ("显存" in _reason or "NVENC" in _reason or "GPU" in _reason):
            self._force_cpu_export = True
        self._parallel_mgr = None
        self._stop_ui_alive_probe()
        self.is_exporting = False
        self._resume_preview_timers()
        self._fps_meter_clear()
        self.btn_exp.setEnabled(True)
        self.update_ui_state()

        if not self.batch_export_running:
            # 用户中途按了「停止批量导出」
            return

        failed_rows = []
        try:
            for j in (failed_indices or []):
                if 0 <= int(j) < len(self._parallel_indices):
                    failed_rows.append(self._parallel_indices[int(j)])
        except Exception:
            failed_rows = []

        if failed_rows:
            names = []
            saved = getattr(self, "_batch_export_paths", None) or {}
            for i in failed_rows:
                try:
                    names.append(os.path.basename(
                        saved.get(i) or self._batch_output_path(self.tasks[i])))
                except Exception:
                    names.append("#%s" % i)
            self._last_export_failed_names = names
            if getattr(self, "_force_cpu_export", False):
                print(f"[Parallel] {len(failed_rows)} 个视频将改用串行 CPU 重做（不中途换编码器）: {', '.join(names)}")
            else:
                print(f"[Parallel] {len(failed_rows)} 个视频将改用串行方式重新导出: {', '.join(names)}")
            self.batch_queue = failed_rows
            self._batch_export_total = len(failed_rows)
            self._batch_export_current = 0
            self._serial_retry_n = len(failed_rows)
            self._serial_retry_t0 = time.time()
            self.update_progress_value(0)
            self.process_next_export()
            return

        self._finish_batch_export()

    def _finish_batch_export(self):
        """串行/并行批量导出都走这里，便于工程队列接到「这一份已经导完」。"""
        self.batch_export_running = False
        self.is_batch_exporting = False
        out_dir = self._batch_export_out_dir
        self._batch_export_out_dir = None
        # 并行阶段结束时「导出完成.txt」就写了「已全部结束 / 成功 0」，
        # 而串行重试还要跑好几分钟 —— 文件在撒谎。这里把真实收尾补上去。
        n_retry = int(getattr(self, "_serial_retry_n", 0) or 0)
        if n_retry and out_dir:
            try:
                from cutora.render.export_guard import DONE_NAME
                bad = list(getattr(self, "_last_export_failed_names", None) or [])
                t0 = float(getattr(self, "_serial_retry_t0", 0) or 0)
                lines = [
                    "─────────────────────────────────────────────",
                    "串行重试阶段（并行阶段退回的 %d 个）已结束：%s" % (
                        n_retry, time.strftime("%H:%M:%S")),
                ]
                if t0:
                    el = time.time() - t0
                    lines.append("重试耗时    ：%d 分 %d 秒" % (int(el // 60), int(el % 60)))
                if bad:
                    lines.append("重试的文件  ：" + "、".join(bad))
                lines.append("每个文件是否真的产出，以本目录里的 .mp4 为准；"
                             "上面「成功 0」是并行阶段的数字，不是最终结果。")
                with open(os.path.join(out_dir, DONE_NAME), "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            except Exception:
                pass
        self._serial_retry_n = 0
        self._force_cpu_export = False
        self.update_batch_export_btn_text()
        try:
            self.update_progress_value(100)
        except Exception:
            pass
        try:
            self.setWindowTitle(format_window_title(self.project_path))
        except Exception:
            pass
        if getattr(self, "_queue_mode", False):
            if self._advance_project_queue_after_export():
                return
        # [v6.2] 完成信号不依赖主窗口：
        # 输出目录已经有「导出完成.txt」，右下角的导出监视器也会变绿并响一声。
        # 即使这个 QMessageBox 因为主界面白板而弹不出来，用户照样知道结束了。
        try:
            if os.name == "nt":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        msg = "批量导出完成！"
        names = getattr(self, "_last_export_failed_names", None) or []
        if names:
            msg += "\n\n以下文件验收未过、已改串行重试：\n- " + "\n- ".join(names)
            self._last_export_failed_names = []
        QMessageBox.information(self, "完成", msg)

    def process_next_export(self):
        # 稳定性：严格串行——导出未结束时绝不启动下一条
        if getattr(self, "is_exporting", False):
            return
        if not self.batch_export_running:
            self.is_batch_exporting = False
            self.update_batch_export_btn_text()
            return
        if not self.batch_queue:
            self._finish_batch_export()
            return

        # 当前是第几个（用于进度标题稳定显示）
        try:
            self._batch_export_current = int(self._batch_export_total) - len(self.batch_queue) + 1
        except Exception:
            self._batch_export_current = 1

        idx = self.batch_queue.pop(0)
        self.file_list.setCurrentRow(idx)
        self.curr_idx = idx
        task = self.tasks[idx]

        saved = getattr(self, "_batch_export_paths", None)
        if not isinstance(saved, dict):
            saved = {}
            self._batch_export_paths = saved
        out = saved.get(idx) or self._batch_output_path(task)
        saved[idx] = out
        self.run_export_worker(task, out, is_batch=True)

    def run_export_worker(self, task, out_path, is_batch=False):
        prepare_task_audio_for_export(task)
        self.btn_exp.setEnabled(False)
        if not is_batch:
            self._set_batch_export_enabled(False)
        self.set_progress_mode("export")
        # 每个视频开始前先把进度归零，避免视觉“闪回/跳变”
        self.update_progress_value(0)
        # 导出走 ExportRenderer (QImage 离屏渲染)，与 UI painter 完全隔离，
        # 不需要禁用 UI 重绘，用户可以在导出期间继续预览。
        use_gpu = bool(self.chk_gpu.isChecked()) and not getattr(self, "_force_cpu_export", False)
        self._retire_render_thread()
        self.render = None
        # Q160：只有一两条视频在导出时，整台机器就一个渲染进程在干活 —— 把这条视频切成几段
        # 多进程同时渲染，再无损拼接（编码参数不变；任何一段不对自动退回整条串行）
        try:
            from cutora.render import segment_export as _seg
            if _seg.should_use(task, is_batch=is_batch, use_gpu=use_gpu):
                self.render = _seg.SegmentExportWorker(
                    task, out_path, self.render_mode, use_gpu, export_logs=(not is_batch))
        except Exception:
            import logging
            logging.getLogger("cutora.ui.export").exception("分段并行导出不可用，按整条导出")
            self.render = None
        if self.render is None:
            self.render = RenderWorker(task, out_path, self.render_mode, use_gpu, export_logs=(not is_batch))
        self.render.progress.connect(self.update_progress_value)
        self.render.export_stats.connect(self._on_export_fps)
        
        # [v6.1] 导出期间挂起预览定时器。
        # 整条导出流水线的瓶颈是那唯一一个 Python 渲染线程，
        # 主线程每 100ms/200ms 醒来做一次 UI 同步就会抢一次 GIL，直接拖慢渲染。
        # 这些定时器只负责刷新预览显示，暂停它们不影响任何导出结果。
        self._suspend_preview_timers()

        self._export_notify_src = getattr(self, "_bus_source", None) or "ui"
        self._export_notify_batch = bool(is_batch)
        self._export_notify_out = out_path
        self._render_seq = int(getattr(self, "_render_seq", 0) or 0) + 1
        _seq = self._render_seq
        self.render.finished.connect(lambda _s=_seq: self._on_export_finished(_s))
        self.render.error.connect(lambda msg, _s=_seq: self._on_export_error(msg, _s))
        self.is_exporting = True
        self.update_ui_state()
        self.render.start()

    def _retire_render_thread(self, timeout_ms: int = 15000) -> None:
        """BUG-39: never drop a RenderWorker that is still running.

        RenderWorker emits its own ``finished`` from inside run() *before* the
        finally-block releases 20-30 VideoCaptures and joins the decode
        prefetch threads. On a multi-clip timeline that cleanup takes longer
        than the 500 ms batch timer, so the next ``self.render = RenderWorker``
        used to destroy a live QThread → Qt abort() → exit 0xC0000409.
        Wait for it; if it is truly stuck, park it instead of freeing it.
        """
        self._retire_thread_attr("render", timeout_ms)

    def _retire_thread_attr(self, attr: str, timeout_ms: int = 15000) -> None:
        """Wait for the QThread held in ``self.<attr>`` before it gets replaced.

        Works for every worker that shadows ``finished`` and emits it before
        its run() returns (RenderWorker, AlignmentWorker): the Python wrapper
        stays alive through the thread's own run() frame, so the object is
        freed the moment run() returns — from inside the still-running
        thread — and Qt aborts. A thread that will not stop is parked, never
        freed.
        """
        old = getattr(self, attr, None)
        if old is None or not hasattr(old, "isRunning"):
            return
        try:
            if old.isRunning():
                old.wait(int(timeout_ms))
            if old.isRunning():
                zombies = getattr(self, "_zombie_render_threads", None)
                if zombies is None:
                    zombies = self._zombie_render_threads = []
                zombies.append(old)
                print("[Thread] %s 在 %d ms 内没退出，已挂起不释放（不崩溃）"
                      % (attr, int(timeout_ms)), flush=True)
        except Exception:
            pass

    def _export_outcome_once(self, seq, outcome: str) -> bool:
        """RenderWorker.finished is delivered twice (own signal + QThread's),
        and error + finished both arrive for a failed run.

        ``seq`` is the run number bound at connect time. Accept the first
        outcome for the current run; drop duplicates and anything from an
        earlier run (a stale QThread.finished that lands after the next
        worker already started). ``seq is None`` = direct call, always accepted.
        """
        if seq is None:
            return True
        cur = int(getattr(self, "_render_seq", 0) or 0)
        if int(seq) != cur:
            return False
        if int(getattr(self, "_render_done_seq", -1)) == cur:
            return False
        self._render_done_seq = cur
        self._render_outcome = outcome
        return True

    def _export_show_modal(self) -> bool:
        src = getattr(self, "_export_notify_src", None) or "ui"
        return src == "ui" and not bool(getattr(self, "_export_notify_batch", False))

    def _on_export_finished(self, seq=None):
        if not self._export_outcome_once(seq, "ok"):
            return
        self.is_exporting = False
        self._resume_preview_timers()
        self._fps_meter_clear()
        self.update_ui_state()
        self.btn_exp.setEnabled(True)
        out_path = getattr(self, "_export_notify_out", "")
        paths = getattr(self.render, "produced_paths", None) or [out_path]
        src = getattr(self, "_export_notify_src", None) or "ui"
        if self._export_show_modal():
            self._set_batch_export_enabled(True)
            QMessageBox.information(self, "成功", "导出完成:\n" + "\n".join(str(p) for p in paths))
            return
        if not getattr(self, "_export_notify_batch", False):
            self._set_batch_export_enabled(True)
            from cutora.api.events import HUB
            HUB.publish("export", {"ok": True, "out": out_path, "paths": list(paths), "source": src})
            return
        if not self.batch_export_running:
            return
        if hasattr(self, "_batch_export_next_timer"):
            if self._batch_export_next_timer.isActive():
                self._batch_export_next_timer.stop()
            self._batch_export_next_timer.start(500)

    def _on_export_error(self, e, seq=None):
        if not self._export_outcome_once(seq, "error"):
            return
        self.is_exporting = False
        self._resume_preview_timers()
        self._fps_meter_clear()
        self.update_ui_state()
        self.btn_exp.setEnabled(True)
        src = getattr(self, "_export_notify_src", None) or "ui"
        if self._export_show_modal():
            QMessageBox.critical(self, "错误", f"导出失败: {e}")
            return
        if not getattr(self, "_export_notify_batch", False):
            from cutora.api.events import HUB
            HUB.publish("export", {"ok": False, "error": str(e), "source": src})
            return
        print(f"Batch export failed: {e}")
        if not self.batch_export_running:
            return
        if hasattr(self, "_batch_export_next_timer"):
            if self._batch_export_next_timer.isActive():
                self._batch_export_next_timer.stop()
            self._batch_export_next_timer.start(500)

    # ══════════════════════════════════════════════════════════════════════
    # [v6.1] 导出期间的预览定时器挂起 / 恢复
    #
    # self.timer            —— 200ms，sync_ui()，同步播放头与各面板
    # self.timeline.timer   —— 100ms，update_scroll_range()
    #
    # 两者都只服务于「预览显示」，与导出渲染的正确性完全无关。
    # 用计数器保护，支持批量导出中反复调用而不会提前恢复。
    # ══════════════════════════════════════════════════════════════════════

    def _suspend_preview_timers(self):
        try:
            depth = int(getattr(self, "_preview_timer_suspend_depth", 0))
        except Exception:
            depth = 0
        if depth == 0:
            self._preview_timers_were_active = []
            for owner, attr in ((self, "timer"), (getattr(self, "timeline", None), "timer")):
                if owner is None:
                    continue
                t = getattr(owner, attr, None)
                try:
                    if t is not None and t.isActive():
                        t.stop()
                        self._preview_timers_were_active.append(t)
                except Exception:
                    pass
        self._preview_timer_suspend_depth = depth + 1

    def _resume_preview_timers(self):
        depth = int(getattr(self, "_preview_timer_suspend_depth", 0)) - 1
        if depth > 0:
            self._preview_timer_suspend_depth = depth
            return
        self._preview_timer_suspend_depth = 0
        for t in getattr(self, "_preview_timers_were_active", []) or []:
            try:
                t.start()
            except Exception:
                pass
        self._preview_timers_were_active = []

    # ══════════════════════════════════════════════════════════════════════
    # [v6.1] 性能菜单处理函数
    # ══════════════════════════════════════════════════════════════════════

    def _on_release_vram(self):
        """手动卸载 AI 模型并归还显存。"""
        if getattr(self, "is_ai_running", False) or getattr(self, "is_batch_running", False):
            QMessageBox.warning(self, "提示", "AI 字幕任务正在运行，请等待结束后再释放显存。")
            return
        try:
            freed = release_gpu_models()
        except Exception as e:
            QMessageBox.warning(self, "提示", f"释放显存时出错（软件功能不受影响）：\n{e}")
            return
        alloc, reserved = _cuda_mem_gb()
        if freed > 0.01:
            QMessageBox.information(
                self, "完成",
                f"已释放约 {freed:.2f} GB 显存。\n当前占用：{reserved:.2f} GB\n\n"
                f"下次运行自动字幕时会自动重新加载模型。")
        else:
            QMessageBox.information(
                self, "完成",
                f"当前没有可释放的显存。\n当前占用：{reserved:.2f} GB")

    def _on_show_vram_info(self):
        try:
            import sys as _sys
            if "torch" not in _sys.modules:
                # Q187：主界面没加载 torch 时用 nvidia-smi 看全卡占用（含 GPU 服务进程、渲染进程）
                from cutora.render.export_guard import gpu_identity, gpu_memory_gb
                info = gpu_memory_gb(max_age=0.0)
                ident = gpu_identity()
                if info is None:
                    QMessageBox.information(self, "显存占用", "当前未使用 GPU（CPU 模式）。")
                    return
                total, used, free = info
                QMessageBox.information(
                    self, "显存占用",
                    f"显卡：{(ident or ('', '?'))[1]}\n"
                    f"总显存：{total:.1f} GB\n已用：{used:.2f} GB（全机，含 GPU 服务进程）\n空闲：{free:.2f} GB\n\n"
                    f"主界面进程没有加载 AI 运行时；ASR / AI 抠像模型在用到时才加载。")
                return
            if not torch.cuda.is_available():
                QMessageBox.information(self, "显存占用", "当前未使用 GPU（CPU 模式）。")
                return
            alloc, reserved = _cuda_mem_gb()
            total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            name = torch.cuda.get_device_name(0)
            loaded = "已加载" if _WHISPERX_MODEL is not None else "未加载"
            QMessageBox.information(
                self, "显存占用",
                f"显卡：{name}\n"
                f"总显存：{total:.1f} GB\n\n"
                f"模型实际占用：{alloc:.2f} GB\n"
                f"PyTorch 保留：{reserved:.2f} GB\n"
                f"ASR 模型状态：{loaded}\n\n"
                f"若准备同时打开第二个软件实例，建议先执行\n"
                f"「性能 → 释放显存」。")
        except Exception as e:
            QMessageBox.information(self, "显存占用", f"无法读取显存信息：{e}")

    def _on_toggle_segment_export(self, checked):
        from cutora.render import segment_export as _seg
        _seg.set_enabled(bool(checked))
        if checked:
            k = _seg.segment_count(30 * 600, 30.0)
            QMessageBox.information(
                self, "单个视频分段并行导出已开启",
                f"导出一条视频时，会把它按时间切成约 {k} 段，{k} 个渲染进程同时干活，"
                f"最后无损拼接、只混一次音频。\n\n"
                f"· 编码参数（CRF / preset）和原来完全一样，只是每段开头多一个关键帧\n"
                f"· 任何一段出错会自动退回原来的整条导出，不会丢视频\n"
                f"· 视频太短（不够每段 4 秒）或 CPU 少于 6 线程时自动不分段")
        else:
            QMessageBox.information(self, "提示", "已切换回整条导出。")

    def _on_toggle_parallel_export(self, checked):
        global PARALLEL_EXPORT_ENABLED
        PARALLEL_EXPORT_ENABLED = bool(checked)
        _save_parallel_settings(PARALLEL_EXPORT_ENABLED, PARALLEL_EXPORT_WORKERS)
        if checked:
            QMessageBox.information(
                self, "并行批量导出已开启",
                f"批量导出时将同时运行 {PARALLEL_EXPORT_WORKERS} 个渲染进程。\n\n"
                f"· 输出结果与串行导出完全一致\n"
                f"· 若任何一个子进程失败，该视频会自动改用原串行方式重试\n"
                f"· 随时可以从本菜单关闭，回到原来的逐个导出")
        else:
            QMessageBox.information(self, "提示", "已切换回逐个串行导出。")

    def _on_set_parallel_workers(self):
        global PARALLEL_EXPORT_WORKERS
        rec = _default_parallel_workers()
        n, ok = QInputDialog.getInt(
            self, "并行进程数",
            f"同时渲染的视频数量：\n\n"
            f"本机 CPU 线程数：{os.cpu_count()}\n"
            f"推荐值：{rec}\n\n"
            f"数值越大越快，但过大时磁盘和显卡编码器会成为新瓶颈。",
            PARALLEL_EXPORT_WORKERS, 1, 12, 1)
        if ok:
            PARALLEL_EXPORT_WORKERS = int(n)
            _save_parallel_settings(PARALLEL_EXPORT_ENABLED, PARALLEL_EXPORT_WORKERS)

    def stop_ai(self):
        if hasattr(self, 'worker_ai'): self.worker_ai.stop()

    def _undo_snapshot(self, scope="all"):
        """Q159：按范围拍快照 —— "all" 整工程；"task" 只有当前这条；int / [int] 指定下标。"""
        from cutora.core.undo_snapshot import UndoSnapshot
        if self.curr_idx >= 0 and self.curr_idx < len(self.tasks or []):
            _clear_rich_cache_in_task(self.tasks[self.curr_idx])
        fl = self.file_list
        names = fl.leaf_texts() if hasattr(fl, "leaf_texts") else [fl.item(i).text() for i in range(fl.count())]
        return UndoSnapshot(self.tasks, self.curr_idx, names, scope=scope)

    def push_undo(self, action_name="操作", command=None, params=None, source=None, scope=None):
        """记一步撤销。scope 不给：有命令名就按命令族判断只拷当前这条还是整工程；没有就整工程（安全）。"""
        from cutora.core.undo_snapshot import scope_for_command
        if scope is None:
            scope = scope_for_command(command, params) if command else "all"
        snapshot = self._undo_snapshot(scope)
        extra = None
        if command:
            extra = {"command": command, "params": params, "source": source}
        self.history_mgr.push(action_name, snapshot, extra=extra)
        self._sync_idle_task = None            # Q189h：要改东西了，空闲停帧作废
        from cutora.subtitle.track_source import log_subtitle_source
        tasks = getattr(self, "tasks", None) or []
        idx = int(getattr(self, "curr_idx", -1) or -1)
        if 0 <= idx < len(tasks):
            log_subtitle_source("push_undo", tasks[idx])

    def capture_undo_after(self):
        """Fill the tail history entry's post-change image for redo."""
        mgr = getattr(self, "history_mgr", None)
        if mgr is None or not hasattr(mgr, "set_after"):
            return
        scope = mgr.tail_scope() if hasattr(mgr, "tail_scope") else "all"
        mgr.set_after(self._undo_snapshot(scope))

    def undo(self):
        state = self.history_mgr.undo()
        if state:
            self._restore_snapshot(state)

    def redo(self):
        state = self.history_mgr.redo()
        if state:
            self._restore_snapshot(state)

    def _restore_snapshot(self, state):
        self._sync_idle_task = None            # Q189h：撤销 / 重做后重新出帧
        from cutora.core.undo_snapshot import UndoSnapshot
        if isinstance(state, UndoSnapshot):
            # Q159：只动过的那几条原地写回，任务列表对象不换
            if isinstance(self.tasks, list):
                state.restore_into(self.tasks)
            else:
                self.tasks = state.restore_into(None)
        else:
            self.tasks = copy.deepcopy(state['tasks'])
        new_idx = state['curr_idx']
        fl = self.file_list
        fl.blockSignals(True)
        want_names = list(state.get('file_names', []) or [])
        try:
            cur_names = fl.leaf_texts() if hasattr(fl, "leaf_texts") else [fl.item(i).text() for i in range(fl.count())]
        except Exception:
            cur_names = None
        if cur_names != want_names or fl.count() != len(self.tasks):
            # Q159：媒体列表只在名单真的变了（增删 / 重排视频）才重建，而且复用已有缩略图 ——
            # 以前每次撤销都清空重建、每一条重新打开视频解一帧缩略图（上百条就是好几秒 + 显卡硬解风暴）
            old_icons = {}
            try:
                for it in fl._get_all_leaves():
                    old_icons[str(it.data(0, Qt.ItemDataRole.UserRole) or "")] = it.icon(0)
            except Exception:
                old_icons = {}
            fl.clear()
            for i, name in enumerate(want_names):
                vpath = self.tasks[i].get("video", "") if i < len(self.tasks) else ""
                fl.addItem(name, vpath, decode_thumb=False)
                icon = old_icons.get(str(vpath or ""))
                try:
                    it = fl.item(fl.count() - 1)
                    if it is not None and icon is not None and not icon.isNull():
                        it.setIcon(0, icon)
                except Exception:
                    pass

        if self.tasks and 0 <= new_idx < len(self.tasks):
            fl.setCurrentRow(new_idx)
            self.curr_idx = new_idx
        else:
            self.curr_idx = -1
        fl.blockSignals(False)
        
        if self.curr_idx >= 0:
            self.on_click(self.file_list.item(self.curr_idx))
        else:
            self.refresh_ui()

    def _populate_history_menu(self):
        self.history_menu.clear()
        if not self.history_mgr.history:
            dummy = QAction("暂无操作记录", self)
            dummy.setEnabled(False)
            self.history_menu.addAction(dummy)
            return
            
        for idx, entry in enumerate(self.history_mgr.history):
            desc = entry.get('desc', '未命名操作')
            cmd = entry.get("command")
            if cmd:
                try:
                    spec = self.bus.registry.get(cmd)
                    if spec is not None and getattr(spec, "label", None):
                        desc = spec.label
                except Exception:
                    pass
            when = entry.get("at") or ""
            prefix = "▶ " if idx == self.history_mgr.current_index else "  "
            text = f"{prefix}{idx+1}: {desc}"
            if when:
                text = f"{text}  {when}"
            act = QAction(text, self)
            # lambda closure bound parameter 
            act.triggered.connect(lambda checked, i=idx: self._jump_history(i))
            if idx > self.history_mgr.current_index:
                f = act.font()
                f.setItalic(True)
                act.setFont(f)
            self.history_menu.addAction(act)

    def _jump_history(self, index):
        """Q159：按视频存的快照只能一步步走 —— 跳到第 N 条 = 连续撤销 / 重做到那里。"""
        mgr = self.history_mgr
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        guard = 0
        while mgr.current_index > index and guard < 10000:
            guard += 1
            state = mgr.undo()
            if state is None:
                break
            self._restore_snapshot(state)
        while mgr.current_index < index and guard < 10000:
            guard += 1
            state = mgr.redo()
            if state is None:
                break
            self._restore_snapshot(state)

    def refresh_ui(self):
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            return
        task = self.tasks[self.curr_idx]
        self._task_data = task
        self.text_input.setText(task['text'])
        self.timeline.set_data(self.media_player.duration(), task)
        self.sub_panel.load_data(task['style'])
        self._reset_sticker_ui_cache()

    def _announce_text_layer_migrate(self, data):
        from cutora.subtitle.text_layer import migrate_open_project
        _n, msg = migrate_open_project(data)
        if msg:
            try:
                self.statusBar().showMessage(msg, 8000)
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.ui.main_window").error("status bar missing: %s", msg)

    def _reset_sticker_ui_cache(self):
        self.prop_panel._active_text_editor = None
        self.prop_panel.text_element_widgets.clear()
        self.prop_panel.shape_element_widgets.clear()
        for name in ("text_items_layout", "shape_items_layout"):
            layout = getattr(self.prop_panel, name, None)
            if layout is None:
                continue
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)

    def _get_intro_duration_for_insert(self, task):
        if not isinstance(task, dict):
            return None
        sel = self.timeline.selected_overlay
        if not sel or sel[0] != 'image_overlays':
            return None
        idx = sel[1]
        if not isinstance(idx, int):
            return None
        ovs = task.get("image_overlays", [])
        if idx < 0 or idx >= len(ovs):
            return None
        ov = ovs[idx]
        if not isinstance(ov, dict) or not ov.get("is_intro"):
            return None
        intro = task.get("intro_media")
        if isinstance(intro, dict):
            try:
                return float(intro.get("duration", 0.0))
            except Exception:
                return None
        return None

    def _get_current_image_overlay_for_elements(self):
        ov = getattr(self.prop_panel, "current_data", None)
        # [FIX-IMG] 选中的是图片元素时，返回它的父覆层，保证"插入文本/形状"等功能不受影响
        if self.prop_panel.current_type == 'sticker':
            parent_ov = getattr(self.prop_panel, "_ov_parent_overlay", None) or self.get_selected_image_overlay()
            if isinstance(parent_ov, dict) and parent_ov.get('type') == 'image':
                return parent_ov
            return None
        if self.prop_panel.current_type != 'overlay':
            return None
        if not isinstance(ov, dict) or ov.get('type') != 'image':
            return None
        return ov

    def get_selected_image_overlay(self):
        return self._selected_image_overlay

    def get_selected_sticker(self):
        return self._selected_sticker

    def _get_selected_image_overlay_index(self):
        ov = self.get_selected_image_overlay()
        if not ov or not isinstance(self._task_data, dict):
            return None
        image_ovs = self._task_data.get("image_overlays", [])
        try:
            return image_ovs.index(ov)
        except ValueError:
            return None

    def extract_first_two_sentences(self, text: str) -> str:
        if not text:
            return ""
        parts = re.split(r'(?<=[。.!?])', text)
        out = []
        for p in parts:
            if p.strip():
                out.append(p.strip())
            if len(out) >= 2:
                break
        return "\n".join(out)

    def on_insert_text(self):
        from cutora.core.log import get_logger
        get_logger("cutora.ui.main_window").info("nested text insert removed (Q27)")

    def on_insert_shape(self):
        from cutora.core.log import get_logger
        get_logger("cutora.ui.main_window").info("nested shape insert removed (Q27)")

    def _set_button_color(self, btn, color):
        if not color:
            return
        btn.setProperty("color", color)
        from cutora.ui.theme.apply import apply_swatch
        apply_swatch(btn, color)

    def _get_button_color(self, btn, fallback):
        val = btn.property("color")
        return val if val else fallback

    def _bind_sticker_module_signals(self):
        return

    def _pick_color_for_module(self, key):
        el = self.get_selected_sticker()
        if not el:
            return
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        color = c.name()
        p = self.prop_panel
        if key == "text_color":
            el["color"] = color
            self._set_button_color(p.text_color, color)
        elif key == "text_stroke_color":
            el["stroke_color"] = color
            self._set_button_color(p.text_stroke_color, color)
        elif key == "text_bg_color":
            el["bg_color"] = color
            self._set_button_color(p.text_bg_color, color)
        elif key == "shape_color":
            el["fill_color"] = color
            self._set_button_color(p.shape_color, color)
        elif key == "shape_border_color":
            el["border_color"] = color
            self._set_button_color(p.shape_border_color, color)
        self.video_canvas.update()

    def _set_prop_panel_parent_track(self, ov):
        """根据 overlay 的 _track_id 查找所属 Track 并设置到属性面板。"""
        task = self.tasks[self.curr_idx] if 0 <= self.curr_idx < len(self.tasks) else None
        parent_track = None
        if task:
            tl = _tl_of(task)
            if tl is not None:
                ov_tid = ov.get('_track_id') if isinstance(ov, dict) else None
                for tr in getattr(tl, 'tracks', []):
                    if ov_tid and getattr(tr, "id", None) == ov_tid:
                        parent_track = tr
                        break
                    if getattr(tr, 'type', '') == 'image' and parent_track is None:
                        parent_track = tr
        self.prop_panel._ov_parent_track = parent_track

    def refresh_sticker_panel(self):
        if not isinstance(self._task_data, dict):
            return
        ov = self.get_selected_image_overlay()
        if not ov:
            return
        self._ensure_sticker_ui(ov)
        el = self.get_selected_sticker()
        if el and (self.prop_panel.current_type != 'sticker' or self.prop_panel.current_data is not el):
            # 设置父 overlay 引用（供缩放/羽化/混合模式读取）
            self.prop_panel._ov_parent_overlay = ov
            self._set_prop_panel_parent_track(ov)
            self.prop_panel.load_data('sticker', el)
        if self.prop_panel._active_text_editor:
            self.prop_panel._active_text_editor.setFocus()

    def _ensure_sticker_ui(self, ov):
        self._reset_sticker_ui_cache()
        elements = ov.get("elements", [])
        if not isinstance(elements, list):
            return
        self.prop_panel._active_text_editor = None
        for el in elements:
            if not isinstance(el, dict):
                continue
            if "id" not in el:
                el["id"] = uuid.uuid4().hex
            if el.get("kind") in ("text", "shape"):
                continue

    def _ensure_text_widget(self, el):
        el_id = el.get("id")
        box = QGroupBox("文本")
        form = QFormLayout(box)
        form.setSpacing(12)
        form.setContentsMargins(4, 8, 4, 8)

        edit = QPlainTextEdit()
        edit.setMinimumHeight(120)
        edit.setPlaceholderText("输入多行文字，支持换行")
        form.addRow("文本内容", edit)
        rich_btn = QPushButton("富文本编辑")
        form.addRow(rich_btn)

        font = QComboBox()
        register_font_combo(font)
        form.addRow("字体", font)

        size = QSpinBox()
        size.setRange(10, 300)
        form.addRow("大小", size)

        bold = QCheckBox("粗体")
        form.addRow(bold)

        color = QPushButton("字体颜色")
        form.addRow(color)

        opacity = QSlider(Qt.Orientation.Horizontal)
        opacity.setRange(0, 255)
        form.addRow("透明度", opacity)

        stroke = QCheckBox("描边")
        stroke_width = QSpinBox()
        stroke_color = QPushButton("描边颜色")
        form.addRow(stroke)
        form.addRow("描边粗细", stroke_width)
        form.addRow("描边颜色", stroke_color)

        bg = QCheckBox("背景")
        bg_color = QPushButton("背景色")
        bg_radius = QSpinBox()
        bg_opacity = QSlider(Qt.Orientation.Horizontal)
        bg_opacity.setRange(0, 255)
        form.addRow(bg)
        form.addRow("背景颜色", bg_color)
        form.addRow("圆角", bg_radius)
        form.addRow("背景透明度", bg_opacity)

        pos_x = QSpinBox(); pos_x.setRange(-10000, 10000); form.addRow("X", pos_x)
        pos_y = QSpinBox(); pos_y.setRange(-10000, 10000); form.addRow("Y", pos_y)
        pos_w = QSpinBox(); pos_w.setRange(10, 10000); form.addRow("W", pos_w)
        pos_h = QSpinBox(); pos_h.setRange(10, 10000); form.addRow("H", pos_h)

        rotate = QSlider(Qt.Orientation.Horizontal)
        rotate.setRange(-180, 180)
        form.addRow("旋转", rotate)

        z_top = QPushButton("置顶")
        z_up = QPushButton("上移")
        z_down = QPushButton("下移")
        z_row = QHBoxLayout()
        z_row.addWidget(z_top)
        z_row.addWidget(z_up)
        z_row.addWidget(z_down)
        form.addRow("层级", z_row)

        delete_btn = QPushButton("删除此文本框")
        form.addRow(delete_btn)

        box._text_fields = {
            "edit": edit, "rich_btn": rich_btn, "font": font, "size": size, "bold": bold, "color": color,
            "opacity": opacity, "stroke": stroke, "stroke_width": stroke_width,
            "stroke_color": stroke_color, "bg": bg, "bg_color": bg_color,
            "bg_radius": bg_radius, "bg_opacity": bg_opacity,
            "pos_x": pos_x, "pos_y": pos_y, "pos_w": pos_w, "pos_h": pos_h,
            "rotate": rotate, "z_top": z_top, "z_up": z_up, "z_down": z_down, "delete_btn": delete_btn
        }

        def set_selected():
            if self._selected_sticker is not el:
                self._selected_sticker = el
            if self.prop_panel.current_type != 'sticker' or self.prop_panel.current_data is not el:
                self.prop_panel.load_data('sticker', el)

        def update(key, value):
            set_selected()
            self.update_selected_element(key, value)

        edit.textChanged.connect(lambda: update("content", edit.toPlainText()))
        font.currentTextChanged.connect(lambda: update("font_family", sanitize_font_family(font.currentText())))
        size.valueChanged.connect(lambda v: update("fontsize", int(v)))
        bold.toggled.connect(lambda v: update("bold", bool(v)))
        opacity.valueChanged.connect(lambda v: update("opacity", int(v)))
        stroke.toggled.connect(lambda v: update("use_stroke", bool(v)))
        stroke_width.valueChanged.connect(lambda v: update("stroke_width", int(v)))
        bg.toggled.connect(lambda v: update("bg_enabled", bool(v)))
        bg_radius.valueChanged.connect(lambda v: update("bg_radius", int(v)))
        bg_opacity.valueChanged.connect(lambda v: update("bg_opacity", int(v)))
        pos_x.valueChanged.connect(lambda v: update("x", float(v)))
        pos_y.valueChanged.connect(lambda v: update("y", float(v)))
        pos_w.valueChanged.connect(lambda v: update("w", float(v)))
        pos_h.valueChanged.connect(lambda v: update("h", float(v)))
        rotate.valueChanged.connect(lambda v: update("rotation", int(v)))
        z_top.clicked.connect(lambda: (set_selected(), self.bring_element_to_top(el)))
        z_up.clicked.connect(lambda: (set_selected(), self.move_element_z(el, 1)))
        z_down.clicked.connect(lambda: (set_selected(), self.move_element_z(el, -1)))
        rich_btn.clicked.connect(lambda: self.open_rich_text_editor("sticker", el))
        color.clicked.connect(lambda: (set_selected(), self._pick_color_for_element("color", color)))
        stroke_color.clicked.connect(lambda: (set_selected(), self._pick_color_for_element("stroke_color", stroke_color)))
        bg_color.clicked.connect(lambda: (set_selected(), self._pick_color_for_element("bg_color", bg_color)))
        delete_btn.clicked.connect(lambda: self._delete_text_element(el))

        self.prop_panel.text_items_layout.addWidget(box)
        self.prop_panel.text_element_widgets[el_id] = box
        self._update_text_widget(box, el)

    def _update_text_widget(self, box, el):
        f = box._text_fields
        content_format = el.get("content_format", "plain")
        if not ENABLE_RICH_TEXT and content_format == "rich_spans":
            el["content"] = _rich_plain_text(el.get("rich_content", [])) or el.get("content", "")
            el["content_format"] = "plain"
            el.pop("rich_content", None)
            content_format = "plain"
        f["edit"].setReadOnly(False)
        text_val = el.get("content", "")
        f["edit"].blockSignals(True); f["edit"].setPlainText(text_val); f["edit"].blockSignals(False)
        f["font"].blockSignals(True); f["font"].setCurrentText(sanitize_font_family(el.get("font_family"))); f["font"].blockSignals(False)
        f["size"].blockSignals(True); f["size"].setValue(int(el.get("fontsize", 48))); f["size"].blockSignals(False)
        f["bold"].blockSignals(True); f["bold"].setChecked(bool(el.get("bold", False))); f["bold"].blockSignals(False)
        self._set_button_color(f["color"], el.get("color", "#FFFFFF"))
        f["opacity"].blockSignals(True); f["opacity"].setValue(int(el.get("opacity", 255))); f["opacity"].blockSignals(False)
        f["stroke"].blockSignals(True); f["stroke"].setChecked(bool(el.get("use_stroke", False))); f["stroke"].blockSignals(False)
        f["stroke_width"].blockSignals(True); f["stroke_width"].setValue(int(el.get("stroke_width", 4))); f["stroke_width"].blockSignals(False)
        self._set_button_color(f["stroke_color"], el.get("stroke_color", "#000000"))
        f["bg"].blockSignals(True); f["bg"].setChecked(bool(el.get("bg_enabled", False))); f["bg"].blockSignals(False)
        self._set_button_color(f["bg_color"], el.get("bg_color", "#000000"))
        f["bg_radius"].blockSignals(True); f["bg_radius"].setValue(int(el.get("bg_radius", 16))); f["bg_radius"].blockSignals(False)
        f["bg_opacity"].blockSignals(True); f["bg_opacity"].setValue(int(el.get("bg_opacity", 180))); f["bg_opacity"].blockSignals(False)
        f["pos_x"].blockSignals(True); f["pos_x"].setValue(int(el.get("x", 0))); f["pos_x"].blockSignals(False)
        f["pos_y"].blockSignals(True); f["pos_y"].setValue(int(el.get("y", 0))); f["pos_y"].blockSignals(False)
        f["pos_w"].blockSignals(True); f["pos_w"].setValue(int(el.get("w", 600))); f["pos_w"].blockSignals(False)
        f["pos_h"].blockSignals(True); f["pos_h"].setValue(int(el.get("h", 300))); f["pos_h"].blockSignals(False)
        f["rotate"].blockSignals(True); f["rotate"].setValue(int(el.get("rotation", 0))); f["rotate"].blockSignals(False)
        if self._selected_sticker is el:
            self.prop_panel._active_text_editor = f["edit"]

    def _ensure_shape_widget(self, el):
        el_id = el.get("id")
        box = QGroupBox("图形")
        form = QFormLayout(box)
        form.setSpacing(12)
        form.setContentsMargins(4, 8, 4, 8)

        shape_type = QComboBox()
        shape_type.addItems(["round_rect", "circle", "rect", "diamond", "star", "heart", "cross"])
        form.addRow("形状", shape_type)

        color = QPushButton("填充颜色")
        form.addRow("颜色", color)

        opacity = QSlider(Qt.Orientation.Horizontal)
        opacity.setRange(0, 255)
        form.addRow("透明度", opacity)

        radius = QSpinBox()
        radius.setRange(0, 200)
        form.addRow("圆角", radius)

        blur = QSpinBox()
        form.addRow("边缘模糊", blur)

        border = QCheckBox("边框")
        border_width = QSpinBox()
        border_color = QPushButton("边框颜色")
        form.addRow(border)
        form.addRow("边框粗细", border_width)
        form.addRow("边框颜色", border_color)

        rotate = QSlider(Qt.Orientation.Horizontal)
        rotate.setRange(-180, 180)
        form.addRow("旋转", rotate)

        z_top = QPushButton("置顶")
        z_up = QPushButton("上移")
        z_down = QPushButton("下移")
        z_row = QHBoxLayout()
        z_row.addWidget(z_top)
        z_row.addWidget(z_up)
        z_row.addWidget(z_down)
        form.addRow("层级", z_row)

        pos_x = QSpinBox(); pos_x.setRange(-10000, 10000); form.addRow("X", pos_x)
        pos_y = QSpinBox(); pos_y.setRange(-10000, 10000); form.addRow("Y", pos_y)
        pos_w = QSpinBox(); pos_w.setRange(10, 10000); form.addRow("W", pos_w)
        pos_h = QSpinBox(); pos_h.setRange(10, 10000); form.addRow("H", pos_h)

        delete_btn = QPushButton("删除此图形")
        form.addRow(delete_btn)

        box._shape_fields = {
            "shape_type": shape_type, "color": color, "opacity": opacity,
            "radius": radius, "blur": blur, "border": border,
            "border_width": border_width, "border_color": border_color,
            "rotate": rotate, "pos_x": pos_x, "pos_y": pos_y,
            "pos_w": pos_w, "pos_h": pos_h, "delete_btn": delete_btn
        }

        def set_selected():
            if self._selected_sticker is not el:
                self._selected_sticker = el
            if self.prop_panel.current_type != 'sticker' or self.prop_panel.current_data is not el:
                self.prop_panel.load_data('sticker', el)

        def update(key, value):
            set_selected()
            self.update_selected_element(key, value)

        shape_type.currentTextChanged.connect(lambda: update("shape", shape_type.currentText()))
        opacity.valueChanged.connect(lambda v: update("opacity", int(v)))
        radius.valueChanged.connect(lambda v: update("radius", int(v)))
        blur.valueChanged.connect(lambda v: update("blur", int(v)))
        border.toggled.connect(lambda v: update("border_enabled", bool(v)))
        border_width.valueChanged.connect(lambda v: update("border_width", int(v)))
        rotate.valueChanged.connect(lambda v: update("rotation", int(v)))
        z_top.clicked.connect(lambda: (set_selected(), self.bring_element_to_top(el)))
        z_up.clicked.connect(lambda: (set_selected(), self.move_element_z(el, 1)))
        z_down.clicked.connect(lambda: (set_selected(), self.move_element_z(el, -1)))
        pos_x.valueChanged.connect(lambda v: update("x", float(v)))
        pos_y.valueChanged.connect(lambda v: update("y", float(v)))
        pos_w.valueChanged.connect(lambda v: update("w", float(v)))
        pos_h.valueChanged.connect(lambda v: update("h", float(v)))
        color.clicked.connect(lambda: (set_selected(), self._pick_color_for_element("fill_color", color)))
        border_color.clicked.connect(lambda: (set_selected(), self._pick_color_for_element("border_color", border_color)))
        delete_btn.clicked.connect(lambda: self._delete_shape_element(el))

        self.prop_panel.shape_items_layout.addWidget(box)
        self.prop_panel.shape_element_widgets[el_id] = box
        self._update_shape_widget(box, el)

    def _update_shape_widget(self, box, el):
        f = box._shape_fields
        f["shape_type"].blockSignals(True); f["shape_type"].setCurrentText(el.get("shape", "round_rect")); f["shape_type"].blockSignals(False)
        self._set_button_color(f["color"], el.get("fill_color", ""))
        f["opacity"].blockSignals(True); f["opacity"].setValue(int(el.get("opacity", 160))); f["opacity"].blockSignals(False)
        f["radius"].blockSignals(True); f["radius"].setValue(int(el.get("radius", 30))); f["radius"].blockSignals(False)
        f["blur"].blockSignals(True); f["blur"].setValue(int(el.get("blur", 0))); f["blur"].blockSignals(False)
        f["border"].blockSignals(True); f["border"].setChecked(bool(el.get("border_enabled", False))); f["border"].blockSignals(False)
        f["border_width"].blockSignals(True); f["border_width"].setValue(int(el.get("border_width", 4))); f["border_width"].blockSignals(False)
        self._set_button_color(f["border_color"], el.get("border_color", "#FFFFFF"))
        f["rotate"].blockSignals(True); f["rotate"].setValue(int(el.get("rotation", 0))); f["rotate"].blockSignals(False)
        f["pos_x"].blockSignals(True); f["pos_x"].setValue(int(el.get("x", 0))); f["pos_x"].blockSignals(False)
        f["pos_y"].blockSignals(True); f["pos_y"].setValue(int(el.get("y", 0))); f["pos_y"].blockSignals(False)
        f["pos_w"].blockSignals(True); f["pos_w"].setValue(int(el.get("w", 600))); f["pos_w"].blockSignals(False)
        f["pos_h"].blockSignals(True); f["pos_h"].setValue(int(el.get("h", 300))); f["pos_h"].blockSignals(False)

    def _delete_text_element(self, el):
        ov = self.get_selected_image_overlay()
        if not ov:
            return
        elements = ov.get("elements", [])
        if el not in elements:
            return
        elements.remove(el)
        el_id = el.get("id")
        box = self.prop_panel.text_element_widgets.pop(el_id, None)
        if box:
            self.prop_panel.text_items_layout.removeWidget(box)
            box.setParent(None)
        self._selected_sticker = None
        self.video_canvas.update()

    def _delete_shape_element(self, el):
        ov = self.get_selected_image_overlay()
        if not ov:
            return
        elements = ov.get("elements", [])
        if el not in elements:
            return
        elements.remove(el)
        el_id = el.get("id")
        box = self.prop_panel.shape_element_widgets.pop(el_id, None)
        if box:
            self.prop_panel.shape_items_layout.removeWidget(box)
            box.setParent(None)
        self._selected_sticker = None
        self.video_canvas.update()

    def _pick_color_for_element(self, key, btn):
        el = self.get_selected_sticker()
        if not el:
            return
        c = QColorDialog.getColor()
        if not c.isValid():
            return
        self.update_selected_element(key, c.name())
        self._set_button_color(btn, el.get(key))

    def update_selected_element(self, key, value):
        el = self.get_selected_sticker()
        if not el:
            return
        el[key] = value
        self.video_canvas.update()

    def normalize_all_elements_z(self, elements):
        if not isinstance(elements, list):
            return
        ordered = [e for e in elements if isinstance(e, dict)]
        ordered.sort(key=lambda e: e.get("z", 0))
        for idx, e in enumerate(ordered):
            e["z"] = idx

    def move_element_z(self, el, delta):
        ov = self.get_selected_image_overlay()
        if not ov or not el:
            return
        el["z"] = int(el.get("z", 0)) + int(delta)
        self.normalize_all_elements_z(ov.get("elements", []))
        self.refresh_sticker_panel()
        self.video_canvas.update()

    def bring_element_to_top(self, el):
        ov = self.get_selected_image_overlay()
        if not ov or not el:
            return
        elements = ov.get("elements", [])
        max_z = max((e.get("z", 0) for e in elements if isinstance(e, dict)), default=0)
        el["z"] = max_z + 1
        self.normalize_all_elements_z(elements)
        self.refresh_sticker_panel()
        self.video_canvas.update()

    def _update_selected_text_element_from_module(self):
        if self._syncing_sticker_module:
            return
        el = self.get_selected_sticker()
        if not el or el.get("kind") != "text":
            return
        p = self.prop_panel
        el["content"] = p.text_content.toPlainText()
        el["font_family"] = sanitize_font_family(p.text_font.currentText())
        from cutora.subtitle.io import canvas_size_for_window
        from cutora.subtitle.stylepack import set_fontsize_px
        cw, ch = canvas_size_for_window(self)
        set_fontsize_px(el, p.text_size.value(), cw, ch)
        el["bold"] = p.text_bold.isChecked()
        el["color"] = self._get_button_color(p.text_color, el.get("color", "#FFFFFF"))
        el["opacity"] = p.text_opacity.value()
        el["use_stroke"] = p.text_stroke.isChecked()
        el["stroke_width"] = p.text_stroke_width.value()
        el["stroke_color"] = self._get_button_color(p.text_stroke_color, el.get("stroke_color", "#000000"))
        el["bg_enabled"] = p.text_bg.isChecked()
        el["bg_color"] = self._get_button_color(p.text_bg_color, el.get("bg_color", "#000000"))
        el["bg_radius"] = p.text_bg_radius.value()
        el["bg_opacity"] = p.text_bg_opacity.value()
        el["rotation"] = p.text_rotate.value()
        el["shadow_enabled"] = p.text_shadow.isChecked()
        if p.text_z_top.isChecked():
            ov = self.get_selected_image_overlay()
            if ov:
                max_z = max([e.get("z", 0) for e in ov.get("elements", [])] or [0])
                el["z"] = max_z + 1
        else:
            el["z"] = 0
        self.video_canvas.update()

    def _update_selected_shape_element_from_module(self):
        if self._syncing_sticker_module:
            return
        el = self.get_selected_sticker()
        if not el or el.get("kind") != "shape":
            return
        p = self.prop_panel
        el["shape"] = p.shape_type.currentText()
        el["fill_color"] = self._get_button_color(p.shape_color, el.get("fill_color", "#000000"))
        el["opacity"] = p.shape_opacity.value()
        el["radius"] = p.shape_radius.value()
        el["blur"] = p.shape_blur.value()
        el["border_enabled"] = p.shape_border.isChecked()
        el["border_width"] = p.shape_border_width.value()
        el["border_color"] = self._get_button_color(p.shape_border_color, el.get("border_color", "#FFFFFF"))
        el["rotation"] = p.shape_rotate.value()
        self.video_canvas.update()

    def raise_sticker(self):
        ov = self.get_selected_image_overlay()
        el = self.get_selected_sticker()
        if not el or not ov:
            return
        max_z = max([e.get("z", 0) for e in ov.get("elements", [])] or [0])
        el["z"] = max_z + 1
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def lower_sticker(self):
        el = self.get_selected_sticker()
        if not el:
            return
        el["z"] = float(el.get("z", 0)) - 1
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def move_up_sticker(self):
        el = self.get_selected_sticker()
        if not el:
            return
        el["z"] = float(el.get("z", 0)) + 1
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def _ensure_task_for_overlay(self):
        """Return the current task, creating a 10s blank one if the bin is empty."""
        from cutora.ui.i18n import tr
        if self.tasks:
            if not (0 <= self.curr_idx < len(self.tasks)):
                item = self.file_list.currentItem() if hasattr(self, "file_list") else None
                row = self.file_list.row(item) if item is not None else -1
                self.curr_idx = row if 0 <= row < len(self.tasks) else 0
            task = self.tasks[self.curr_idx]
            if _tl_of(task) is None:
                task["timeline"] = self._build_default_timeline()
            task.setdefault("text_overlays", [])
            task.setdefault("image_overlays", [])
            return task
        self._reload_default_subtitle_style()
        tl = self._build_default_timeline()
        style = copy.deepcopy(getattr(self, "default_style", {}) or {})
        task = {
            "video": "", "text": "", "segments": [], "overlays": [],
            "text_overlays": [], "image_overlays": [], "audio_clips": [],
            "dims": (tl.width, tl.height), "style": style, "src_volume": 1.0,
            "intro_media": None, "timeline": tl, "video_overlay": {},
            "audio_tracks": [], "duration": 10.0, "trim_end_sec": 10.0,
        }
        from cutora.subtitle.stylepack import ensure_default_pack
        ensure_default_pack(task)
        self.tasks.append(task)
        self.file_list.addItem(tr("文本"), "")
        self.curr_idx = len(self.tasks) - 1
        self._task_data = task
        self.file_list.blockSignals(True)
        self.file_list.setCurrentRow(self.curr_idx)
        self.file_list.blockSignals(False)
        self.timeline.set_data(10000, task)
        if hasattr(self, "show_editor"):
            self.show_editor()
        return task

    def _overlay_end_sec(self, task):
        intro_dur = self._get_intro_duration_for_insert(task)
        if isinstance(intro_dur, (int, float)) and intro_dur > 0:
            return float(intro_dur)
        duration_ms = self.media_player.duration()
        if duration_ms and duration_ms > 0:
            return duration_ms / 1000.0
        dur = task.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            return float(dur)
        return 10.0

    def _bind_overlay_track(self, task, track_type, start, end, overlays):
        tl = _tl_of(task)
        if tl is None:
            tl = self._build_default_timeline()
            task["timeline"] = tl
        track = pick_or_create_overlay_track(tl, track_type, start, end, overlays)
        return track

    def add_text_ov(self):
        task = self._ensure_task_for_overlay()
        if task is None:
            return
        self.push_undo("添加文本")
        end_sec = self._overlay_end_sec(task)
        from cutora.model.factory_defaults import overlay_defaults
        dims = task.get("dims") or (1080, 1920)
        cw, ch = int(dims[0]), int(dims[1])
        new_ov = overlay_defaults("text", canvas_w=cw, canvas_h=ch, end=end_sec)
        default_preset_name = preset_manager.get_default_text_preset_name()
        base_style = None
        if default_preset_name:
            base_style = preset_manager.apply_text_preset(default_preset_name)
        if isinstance(base_style, dict) and base_style:
            # Preset paints over style keys. Geometry stays the factory box,
            # same as the old update() that ran after the preset copy.
            skip = {
                "type", "start", "end", "x", "y", "w", "h",
                "aspect_ratio", "lock_aspect", "id", "_track_id",
            }
            for key, val in base_style.items():
                if key in skip:
                    continue
                new_ov[key] = copy.deepcopy(val)
        # Q124：框是文字自己的框（overlay_defaults 已经算好位置、不锁比例、
        # auto_height），这里不再把它锁成画布的长宽比。
        if not new_ov.get("content"):
            new_ov["content"] = "Generato da IA"
        if not isinstance(new_ov.get("text_layout"), dict):
            new_ov["text_layout"] = {
                "left_pad": 0, "right_pad": 0,
                "top_pad": 0, "bottom_pad": 0,
                "line_spacing": 0, "letter_spacing": 0,
            }
        if default_preset_name:
            new_ov['_preset_name'] = default_preset_name
        # Q127：从第一帧起就走 textfx 路径（框 = 折行宽度，不限行数）
        from cutora.subtitle.text_layer import ensure_overlay_fx
        ensure_overlay_fx(new_ov)
        ovs = task.setdefault("text_overlays", [])
        track = self._bind_overlay_track(task, "text", 0.0, end_sec, ovs)
        new_ov["_track_id"] = getattr(track, "id", None)
        ovs.append(new_ov)
        dur_ms = int(max(end_sec, float(task.get("duration") or 0)) * 1000)
        self.timeline.set_data(dur_ms, task)
        self.video_canvas.update()

    def _sync_safe_zone_actions(self, settings=None):
        from cutora.core.history import _load_app_settings
        from cutora.render.safe_zones import read_state
        s = settings if settings is not None else (_load_app_settings() or {})
        on, flags = read_state(s)
        if hasattr(self, "act_safe_zones"):
            self.act_safe_zones.blockSignals(True)
            self.act_safe_zones.setChecked(on)
            self.act_safe_zones.blockSignals(False)
        for pid, act in (getattr(self, "safe_zone_preset_actions", None) or {}).items():
            act.blockSignals(True)
            act.setChecked(bool(flags.get(pid, False)))
            act.blockSignals(False)

    def toggle_safe_zones(self):
        if hasattr(self, "act_safe_zones"):
            self.act_safe_zones.toggle()

    def _on_split_guides_toggled(self, on):
        """Preview-only divider guide. Stored in app settings, not the project."""
        from cutora.core.commands import get_bus
        get_bus().execute(
            "app.settings.set",
            {"key": "preview.split_guides", "value": bool(on)},
            source="ui",
        )
        canvas = getattr(self, "video_canvas", None)
        if canvas is not None:
            canvas._show_split_guides = bool(on)
            canvas.update()
        self._recolor_preview_tool(self.btn_split_guides, hover=False)

    def _on_safe_zones_master(self, on):
        from cutora.core.commands import get_bus
        get_bus().execute(
            "app.settings.set",
            {"key": "safe_zones.enabled", "value": bool(on)},
            source="ui",
        )
        if hasattr(self, "canvas") and self.canvas is not None:
            self.canvas.update()
        panel = getattr(self, "sub_panel", None)
        if panel is not None and hasattr(panel, "_refresh_safe_zone_hint"):
            panel._refresh_safe_zone_hint()

    def _on_safe_zone_preset(self, pid, on):
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings() or {}
        flags = dict(s.get("safe_zones.presets") or {})
        flags[str(pid)] = bool(on)
        s["safe_zones.presets"] = flags
        _save_app_settings(s)
        if hasattr(self, "canvas") and self.canvas is not None:
            self.canvas.update()
        panel = getattr(self, "sub_panel", None)
        if panel is not None and hasattr(panel, "_refresh_safe_zone_hint"):
            panel._refresh_safe_zone_hint()

    def _on_insert_sticker(self, sid):
        from cutora.core.commands import get_bus
        get_bus().execute(
            "overlay.insert_sticker",
            {"sticker_id": str(sid or "")},
            source="ui",
        )
        if hasattr(self, "canvas") and self.canvas is not None:
            self.canvas.update()

    def _on_apply_transition(self, preset, domain, duration):
        from cutora.core.commands import CommandError, get_bus
        from cutora.model.seams import clip_span
        seam = getattr(self.timeline, "selected_seam", None)
        if not seam:
            self.statusBar().showMessage("请先选中一条接缝再添加转场", 4000)
            return
        track, _left_i, right_i = seam[0], int(seam[1]), int(seam[2])
        clips = getattr(track, "clips", []) or []
        if right_i < 0 or right_i >= len(clips):
            self.statusBar().showMessage("不能添加转场：接缝已失效", 4000)
            return
        track_domain = getattr(track, "domain", "visual")
        want = "audio" if str(domain or "visual") == "audio" else "visual"
        if (track_domain == "audio") != (want == "audio"):
            self.statusBar().showMessage(
                "不能添加转场：视频转场请放到视频接缝，音频转场请放到音频接缝", 4000)
            return
        seam_t = float(getattr(clips[right_i], "start_t", 0.0) or 0.0)
        if _left_i >= 0 and _left_i < len(clips):
            _s, left_end = clip_span(clips[_left_i])
            seam_t = left_end
        dur = float(duration or 0.35)
        start_t = seam_t - dur / 2.0
        try:
            get_bus().execute("timeline.add_transition", {
                "track_id": str(getattr(track, "id", "") or ""),
                "start_t": start_t,
                "duration": dur,
                "preset": str(preset or ""),
                "domain": want,
            }, source="ui")
        except CommandError as exc:
            self.statusBar().showMessage(str(exc), 4000)
            return
        trans = None
        for item in getattr(track, "transitions", []) or []:
            if abs(float(getattr(item, "start_t", 0.0) or 0.0) - start_t) < 1e-6:
                trans = item
                break
        if trans is not None:
            self.timeline.selected_seam = None
            self.timeline.selected_transition = (track, trans)
            self.timeline.update()

    def add_img_ov(self):
        path, _ = QFileDialog.getOpenFileName(self, "Img", "", "Images (*.png *.jpg)")
        if not path:
            return
        task = self._ensure_task_for_overlay()
        if task is None:
            return
        self.push_undo("添加图片")
        end_sec = self._overlay_end_sec(task)
        from cutora.assets.user import _image_wh
        from cutora.model.factory_defaults import overlay_defaults
        ovs = task.setdefault("image_overlays", [])
        track = self._bind_overlay_track(task, "image", 0.0, end_sec, ovs)
        dims = task.get("dims") or (1080, 1920)
        iw, ih = _image_wh(path)
        ovs.append(overlay_defaults(
            "image",
            canvas_w=int(dims[0]),
            canvas_h=int(dims[1]),
            image_wh=(iw, ih),
            path=path,
            start=0.0,
            end=end_sec,
            track_id=getattr(track, "id", None),
        ))
        dur_ms = int(max(end_sec, float(task.get("duration") or 0)) * 1000)
        self.timeline.set_data(dur_ms, task)
        self.video_canvas.update()

    def add_timeline_track(self, track_type):
        """加一条轨道，返回新加的那条（菜单、快捷键、拖放都走这里）。"""
        from cutora.ui.i18n import tr
        task = self._ensure_task_for_overlay()
        if task is None:
            return None
        tl = _tl_of(task)
        if tl is None:
            tl = self._build_default_timeline()
            task["timeline"] = tl
        before = list(getattr(tl, "tracks", None) or [])
        self.push_undo(tr("添加轨道"))
        new_tr = Track(track_type)
        if str(track_type) in ("audio", "bgm", "sfx"):
            role = "bgm" if str(track_type) == "audio" else str(track_type)
            new_tr.audio_role = role
            if _SFX is not None:
                new_tr.name = _SFX.unique_role_track_name(tl, role)
            else:
                new_tr.name = {"main": "原声", "bgm": "配乐", "sfx": "音效"}.get(role, "配乐")
        tl.add_track(new_tr)
        after = list(getattr(tl, "tracks", None) or [])
        added = [t for t in after if t not in before]
        self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
        return added[-1] if added else None

    # ---------------------------------------------------------------
    # Q115：键盘加轨道（Alt+V 视频 / Alt+A 音频 / Alt+T 文本 / Alt+I 图片）
    # ---------------------------------------------------------------
    def shortcut_add_track(self, track_type):
        """快捷键加轨道：没工程就提示，加完在状态栏报出轨道名字。"""
        from cutora.ui.i18n import tr
        from cutora.ui.timeline_drop import track_display_name
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            self.statusBar().showMessage(tr("先导入一个视频，再加轨道"), 3000)
            return None
        if track_type == "adjust":
            # Q174：空的调整轨什么也不干 —— 新建时顺手在播放头放一块纯色矩形（一步撤销）
            ov = self.add_adjust_item(new_track=True)
            if ov is None:
                return None
            tl = _tl_of(self.tasks[self.curr_idx])
            return next((t for t in tl.tracks if getattr(t, "id", None) == ov.get("_track_id")), None)
        track = self.add_timeline_track(track_type)
        if track is None:
            return None
        tl = _tl_of(self.tasks[self.curr_idx])
        self.statusBar().showMessage(
            tr("已新建 {name}",
               name=track_display_name(self.timeline, tl, track)), 3000)
        return track

    def shortcut_add_video_track(self):
        return self.shortcut_add_track("video")

    def shortcut_add_adjust_track(self):
        return self.shortcut_add_track("adjust")

    # ── Q128：样式画廊 ──
    def _open_text_style_gallery(self, anchor=None):
        from PySide6.QtCore import QPoint
        from cutora.ui.text_style_gallery import TextStyleGallery
        if getattr(self, "text_gallery", None) is None:
            self.text_gallery = TextStyleGallery(self)
            self.text_gallery.style_picked.connect(self._apply_text_style_preset)
            self.text_gallery.save_requested.connect(self._save_text_style_from_canvas)
        cur = ""
        data = getattr(self.prop_panel, "current_data", None)
        if isinstance(data, dict):
            cur = str(data.get("source_preset_id") or "")
        self.text_gallery.reload(cur)
        if anchor is None:
            anchor = self.video_wrap.mapToGlobal(QPoint(8, 40))
        self.text_gallery.popup_at(anchor)
        return self.text_gallery

    def _apply_text_style_preset(self, preset_id):
        """画廊点了一格：样式刷到画布上所有选中的文本层，正文不动。一步撤销。"""
        from cutora.subtitle import text_preset as store
        from cutora.ui.text_style_gallery import apply_preset_style
        row = store.resolve_preset(preset_id)
        if row is None:
            return 0
        canvas = self.video_canvas
        targets = canvas.selected_text_overlays()
        if not targets:
            data = getattr(self.prop_panel, "current_data", None)
            if isinstance(data, dict) and str(data.get("type") or "text") == "text" \
                    and getattr(self.prop_panel, "current_type", "") == "overlay":
                targets = [data]
        if not targets:
            self.statusBar().showMessage("先在画布上选中要套样式的文本", 3000)
            return 0
        n = {"v": 0}

        def _go():
            for ov in targets:
                if apply_preset_style(ov, row):
                    n["v"] += 1
            return n["v"] > 0
        canvas._discrete("应用样式「%s」" % (row.get("name") or ""), _go)
        sel = canvas._selected_item
        if sel is not None and sel.get("type") == "overlay":
            canvas.item_selected.emit("overlay", (sel.get("list"), sel.get("idx")))
        elif isinstance(getattr(self.prop_panel, "current_data", None), dict):
            self.prop_panel.load_data(self.prop_panel.current_type, self.prop_panel.current_data)
        self.update_preview()
        self.statusBar().showMessage("样式「%s」已套到 %d 条文本" % (row.get("name") or "", n["v"]), 3000)
        return n["v"]

    def _save_text_style_from_canvas(self, name):
        """画廊里的「把当前文本存为样式」：和属性面板存预设同一条路。"""
        from cutora.ui.text_style_gallery import invalidate_thumbs
        data = getattr(self.prop_panel, "current_data", None)
        if not (isinstance(data, dict) and str(data.get("type") or "text") == "text"
                and getattr(self.prop_panel, "current_type", "") == "overlay"):
            self.statusBar().showMessage("先选中一条文本再存样式", 3000)
            return None
        saved = self.prop_panel._ov_save_current_as(name)
        invalidate_thumbs()
        try:
            self.prop_panel._refresh_ov_text_presets()
        except Exception:
            pass
        if getattr(self, "text_gallery", None) is not None:
            self.text_gallery.reload(str((saved or {}).get("id") or ""))
        self.statusBar().showMessage("已存为样式「%s」" % name, 3000)
        return saved

    # ── Q128：样式刷 ──
    def shortcut_copy_text_style(self):
        canvas = getattr(self, "video_canvas", None)
        if canvas is None:
            return None
        style = canvas.copy_text_style()
        if style:
            self.statusBar().showMessage("已复制文本样式（Ctrl+Alt+V 粘贴到选中的文本）", 3000)
        else:
            self.statusBar().showMessage("先在画布上选中一条文本再复制样式", 3000)
        return style

    def shortcut_paste_text_style(self):
        canvas = getattr(self, "video_canvas", None)
        if canvas is None:
            return 0
        from cutora.ui.text_style_clipboard import has_clipboard
        if not has_clipboard():
            self.statusBar().showMessage("样式剪贴板是空的：先 Ctrl+Alt+C 复制一条文本的样式", 3000)
            return 0
        n = canvas.paste_text_style()
        if n:
            self.statusBar().showMessage("样式已刷到 %d 条文本" % n, 3000)
            self.update_preview()
        else:
            self.statusBar().showMessage("没有选中的文本可以刷样式", 3000)
        return n

    def shortcut_add_audio_track(self):
        return self.shortcut_add_track("audio")

    def shortcut_add_text_track(self):
        return self.shortcut_add_track("text")

    def shortcut_add_image_track(self):
        return self.shortcut_add_track("image")

    def remove_timeline_track(self, track):
        from cutora.ui.i18n import tr
        if self.curr_idx < 0 or track is None:
            return
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None or track not in tl.tracks:
            return
        from cutora.model import track_cleanup as TC
        if TC.track_item_count(task, track) > 0:
            # Q176：有素材 → 走「连同素材整条删除」（先确认）
            self.delete_track_with_content(track)
            return
        self.push_undo(tr("删除轨道"))
        tl.remove_track(track)
        self.capture_undo_after()
        self.timeline.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()

    # ── Q176：删空轨道（当前视频 / 全部视频）、整条删除（连素材） ──

    def _add_empty_track_menu(self, menu):
        from cutora.model import track_cleanup as TC
        from cutora.ui.i18n import tr
        cur = self.tasks[self.curr_idx]
        n_cur = len(TC.empty_tracks(cur))
        n_all, n_vid = 0, 0
        for task in self.tasks or []:
            _tl_of(task)                       # 老工程的 dict 时间线就地升级，和打开时一样
            k = len(TC.empty_tracks(task))
            n_all += k
            n_vid += 1 if k else 0
        sub = menu.addMenu(tr("删除空轨道"))
        sub.setToolTipsVisible(True)
        a1 = sub.addAction(tr("仅当前视频（%d 条）") % n_cur, lambda: self.remove_empty_tracks(all_videos=False))
        a1.setEnabled(n_cur > 0)
        a2 = sub.addAction(tr("项目内全部视频（%d 个视频，共 %d 条）") % (n_vid, n_all),
                           lambda: self.remove_empty_tracks(all_videos=True))
        a2.setEnabled(n_all > 0)
        tip = tr("只删上面什么都没有的轨道。主视频轨、分屏格子轨、整幅背景轨、锁住 / 钉住的轨道不删。可以撤销")
        a1.setToolTip(tip)
        a2.setToolTip(tip)
        return sub

    def _after_track_removal(self):
        tlw = getattr(self, "timeline", None)
        if tlw is not None:
            try:
                tlw._reset_selection()
                tlw._ov_multi = []
            except Exception:
                pass
            tlw.update()
        if hasattr(self, "timeline_container"):
            self.timeline_container.track_header.update()
            try:
                self.timeline_container.update_scroll_range()
            except Exception:
                pass
        if hasattr(self, "video_canvas"):
            self.video_canvas.update()
        self.update_preview()

    def remove_empty_tracks(self, all_videos=False) -> int:
        """批量删空轨道。all_videos=True：项目里每一条视频的时间线。一步撤销。"""
        from cutora.model import track_cleanup as TC
        from cutora.ui.i18n import tr
        if not self.tasks or not (0 <= self.curr_idx < len(self.tasks)):
            return 0
        tasks = list(self.tasks) if all_videos else [self.tasks[self.curr_idx]]
        if not any(TC.empty_tracks(t) for t in tasks):
            self.statusBar().showMessage(tr("没有空轨道"), 3000)
            return 0
        if all_videos:
            n = sum(len(TC.empty_tracks(t)) for t in tasks)
            ret = QMessageBox.question(
                self, tr("删除空轨道"),
                tr("要删除项目内 %d 个视频里的 %d 条空轨道吗？\n只删上面什么都没有的轨道，可以撤销。")
                % (sum(1 for t in tasks if TC.empty_tracks(t)), n))
            if ret != QMessageBox.StandardButton.Yes:
                return 0
        self.push_undo(tr("删除空轨道"))
        removed = 0
        for t in tasks:
            removed += len(TC.remove_empty_tracks(t))
        self.capture_undo_after()
        self._after_track_removal()
        self.mark_dirty()
        self.statusBar().showMessage(tr("已删除 %d 条空轨道") % removed, 4000)
        return removed

    def delete_track_with_content(self, track, confirm=True) -> bool:
        """整条删除：轨道和上面的素材一起删（先确认）。一步撤销。"""
        from cutora.model import track_cleanup as TC
        from cutora.ui.i18n import tr
        if not (0 <= self.curr_idx < len(self.tasks)) or track is None:
            return False
        task = self.tasks[self.curr_idx]
        tl = _tl_of(task)
        if tl is None or track not in tl.tracks:
            return False
        block = TC.force_delete_block(track)
        if block:
            QMessageBox.information(self, tr("删除轨道"), block)
            return False
        what = TC.describe_items(task, track) or tr("没有素材")
        name = self._track_display_name(track)
        if confirm:
            ret = QMessageBox.question(
                self, tr("删除轨道"),
                tr("确定删除「%s」以及上面的 %s 吗？\n删掉以后可以用撤销（Ctrl+Z）找回。") % (name, what))
            if ret != QMessageBox.StandardButton.Yes:
                return False
        self.push_undo(tr("删除轨道（连同素材）"))
        out = TC.delete_track_with_content(task, track)
        self.capture_undo_after()
        if out.get("removed"):
            self._after_track_removal()
            self.mark_dirty()
            self.statusBar().showMessage(tr("已删除「%s」和上面的 %s") % (name, what), 4000)
        return bool(out.get("removed"))

    def popup_track_menu(self, global_pos, target_track=None):
        from cutora.ui.i18n import tr
        menu = QMenu(self)
        act_snap = menu.addAction(tr("磁吸"))
        act_snap.setCheckable(True)
        act_snap.setChecked(bool(getattr(self.timeline, "snap_enabled", True)))
        act_snap.triggered.connect(
            lambda on: self.timeline.set_snap_enabled(bool(on)))
        menu.addSeparator()
        add_menu = menu.addMenu(tr("添加轨道"))
        from cutora.ui.shortcuts import shortcut_key as _sc_key
        for title, ttype, sid in (
            (tr("视频"), "video", "track_add_video"),
            (tr("文本"), "text", "track_add_text"),
            (tr("图片"), "image", "track_add_image"),
            (tr("音频"), "audio", "track_add_audio"),
            (tr("调整（纯色 / 局部效果）"), "adjust", "track_add_adjust"),
        ):
            _seq = _sc_key(sid)
            _act = add_menu.addAction(
                title, lambda t=ttype: self.shortcut_add_track(t))
            if _seq:
                # 只作提示：真正的绑定在「编辑 → 添加轨道」那组 QAction 上
                _act.setShortcut(QKeySequence(_seq))
                _act.setShortcutVisibleInContextMenu(True)
                _act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        if target_track is not None:
            blend_menu = menu.addMenu(tr("混合模式"))
            current_blend = getattr(target_track, "blend_mode", "normal")
            for mode_name in ("normal", "multiply", "screen", "overlay", "additive"):
                action = blend_menu.addAction(mode_name)
                action.setCheckable(True)
                action.setChecked(mode_name == current_blend)
                action.setData(mode_name)
            from cutora.model.timeline_model import (
                TRACK_HEIGHT_COMPACT, TRACK_HEIGHT_HIGH, TRACK_HEIGHT_STANDARD,
            )
            height_id = str(getattr(target_track, "id", "") or "")
            h_menu = menu.addMenu(tr("所有轨道高度"))
            for title, hh in (
                (tr("紧凑"), TRACK_HEIGHT_COMPACT),
                (tr("标准"), TRACK_HEIGHT_STANDARD),
                (tr("高"), TRACK_HEIGHT_HIGH),
            ):
                h_menu.addAction(
                    title,
                    lambda h=hh, tid=height_id: self.bus.execute(
                        "timeline.set_track_height",
                        {"track_id": tid, "height": h, "all": True},
                        source="ui",
                    ),
                )
            menu.addAction(
                tr("将此高度应用到所有轨道"),
                lambda tid=height_id, h=float(getattr(target_track, "height", TRACK_HEIGHT_STANDARD) or TRACK_HEIGHT_STANDARD): self.bus.execute(
                    "timeline.set_track_height",
                    {"track_id": tid, "height": h, "all": True},
                    source="ui",
                ),
            )
            menu.addSeparator()
            from cutora.model import track_cleanup as TC
            task = self.tasks[self.curr_idx] if 0 <= self.curr_idx < len(self.tasks) else {}
            n_items = TC.track_item_count(task, target_track)
            if n_items == 0:
                act_del = menu.addAction(tr("删除此轨"))
                act_del.triggered.connect(lambda: self.remove_timeline_track(target_track))
            else:
                # Q176：有素材的轨道也能整条删（先确认）
                act_del = menu.addAction(tr("删除此轨（连同上面的素材）…"))
                block = TC.force_delete_block(target_track)
                act_del.setEnabled(not block)
                if block:
                    act_del.setToolTip(block)
                else:
                    act_del.triggered.connect(lambda: self.delete_track_with_content(target_track))
        if 0 <= self.curr_idx < len(self.tasks):
            self._add_empty_track_menu(menu)
        if (target_track is not None
                and str(getattr(target_track, "audio_role", "") or "") == "bgm"):
            task = self.tasks[self.curr_idx] if 0 <= self.curr_idx < len(self.tasks) else {}
            duck = task.get("audio_duck") if isinstance(task, dict) and isinstance(task.get("audio_duck"), dict) else {}
            duck_menu = menu.addMenu(tr("人声闪避"))
            act_on = duck_menu.addAction(tr("开启"))
            act_on.setCheckable(True)
            act_on.setChecked(bool(duck.get("enabled")))
            act_on.triggered.connect(lambda on: self.bus.execute(
                "audio.duck_set",
                {"enabled": bool(on), "scope": "current"},
                source="ui"))
            for _lab, _db in (("浅 -6 dB", -6), ("中 -12 dB", -12), ("深 -18 dB", -18)):
                duck_menu.addAction(_lab, lambda d=_db: self.bus.execute(
                    "audio.duck_set",
                    {"enabled": True, "depth_db": d, "scope": "current"},
                    source="ui"))
        menu.setToolTipsVisible(True)
        chosen = menu.exec(global_pos)
        if target_track is not None and chosen is not None and chosen.data():
            target_track.blend_mode = chosen.data()
            self.timeline.update()
            if hasattr(self, "timeline_container"):
                self.timeline_container.track_header.update()
            self.video_canvas.update()

    def add_audio_bgm(self):
        if self.curr_idx < 0: return
        folder = QFileDialog.getExistingDirectory(self, "选择音频文件夹", "")
        if not folder: return
        self.bus.execute(
            "batch.bgm_assign",
            {"dir": folder, "ids": [self.curr_idx]},
            source="ui",
        )

    def batch_assign_bgm(self):
        if not self.tasks:
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择音频文件夹", "")
        if not folder: return
        self.bus.execute("batch.bgm_assign", {"dir": folder}, source="ui")

    def _ensure_sfx_library(self):
        if _SFX is None:
            return False
        try:
            missing = _SFX.missing_sfx_files()
        except Exception:
            return False
        if not missing:
            return True
        try:
            from cutora.audio.sfx_synth import generate_library
            generate_library()
            return not _SFX.missing_sfx_files()
        except Exception as e:
            print("[AudioFx] 生成音效库失败:", e)
            return False

    def open_ai_providers_dialog(self):
        from cutora.ui.panels.ai_settings import AIProvidersDialog
        dlg = AIProvidersDialog(self)
        dlg.exec()

    def open_language_support(self):
        from cutora.ui.language_support import LanguageSupportDialog
        dlg = getattr(self, "_lang_support", None)
        if dlg is None:
            dlg = LanguageSupportDialog(self)
            self._lang_support = dlg
        from cutora.ui.inwindow import show_sheet      # Q192：主窗口里的侧滑面板
        show_sheet(dlg, self)

    def open_font_licenses_dialog(self):
        from cutora.ui.panels.about import FontLicenseDialog
        dlg = FontLicenseDialog(self)
        dlg.exec()

    def open_about_dialog(self):
        from cutora.ui.panels.about import AboutDialog
        dlg = AboutDialog(self)
        dlg.exec()

    def open_audio_fx_dialog(self):
        if _SFX is None:
            QMessageBox.warning(self, "音效", "音效模块未加载")
            return
        self._ensure_sfx_library()
        try:
            from cutora.ui.panels.audio_fx import AudioFxDialog
            dlg = AudioFxDialog(self)
            if dlg.exec():
                if hasattr(self, "spin_sfx_vol"):
                    fx = _SFX.load_fx_settings()
                    self.spin_sfx_vol.blockSignals(True)
                    self.spin_sfx_vol.setValue(float(fx.get("sfx_volume", 0.18)))
                    self.spin_sfx_vol.blockSignals(False)
                self._preview_fx_request(200)   # 空间立体声参数可能改了 → 重渲代理
                if dlg.apply_all_requested:
                    self.apply_sfx_to_tasks(current_only=False)
                elif dlg.apply_current_requested:
                    self.apply_sfx_to_tasks(current_only=True)
        except Exception as e:
            QMessageBox.warning(self, "音效", str(e))

    def apply_sfx_to_tasks(self, current_only=True):
        if _SFX is None:
            QMessageBox.warning(self, "音效", "音效模块未加载")
            return
        if not self.tasks:
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        self._ensure_sfx_library()
        fx = _SFX.load_fx_settings()
        if current_only:
            if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
                QMessageBox.information(self, "提示", "请先选中一条视频")
                return
            indices = [self.curr_idx]
        else:
            indices = list(range(len(self.tasks)))
        placed = 0
        for idx in indices:
            try:
                _SFX.apply_spatial_defaults_to_task(self.tasks[idx], fx)
                r = _SFX.place_sfx_on_task(self.tasks[idx], fx)
                placed += int((r or {}).get("placed") or 0)
            except Exception as e:
                print(f"[AudioFx] task {idx} sfx failed: {e}")
        if hasattr(self, "spin_sfx_vol"):
            self.spin_sfx_vol.blockSignals(True)
            self.spin_sfx_vol.setValue(float(fx.get("sfx_volume", 0.18)))
            self.spin_sfx_vol.blockSignals(False)
        try:
            self.timeline.update()
            self.sync_ui()
        except Exception:
            pass
        self._preview_fx_request(200)
        QMessageBox.information(self, "音效", f"已写入 {placed} 段音效（{len(indices)} 条视频）")

    def on_batch_add_intro(self):
        if not self.tasks:
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        selected = self.file_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先选择要处理的视频（可多选）")
            return
        selected_indices = sorted({self.file_list.row(item) for item in selected})
        if self.curr_idx in selected_indices:
            self.push_undo()

        applied = 0
        skipped = 0
        failed = 0
        current_updated = False

        for idx in selected_indices:
            task = self.tasks[idx]
            _tr, thumb = _find_thumbnail_clip(task)
            if thumb is not None:
                skipped += 1
                continue
            ok, _reason = apply_thumbnail_leadin_to_task(task, self.global_intro_duration)
            if ok:
                applied += 1
                if idx == self.curr_idx:
                    current_updated = True
            else:
                failed += 1

        if self.curr_idx in selected_indices:
            self.resegment_current_segments()
            if current_updated:
                _tr, thumb_clip = _find_thumbnail_clip(self.tasks[self.curr_idx])
                if thumb_clip is not None:
                    self.timeline.selected_video_clip = (_tr, thumb_clip)
                    self.timeline.item_selected.emit('video_clip', thumb_clip)
            offset_ms = self._get_video_time_offset_ms()
            total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
            self.timeline.set_data(total_ms, self.tasks[self.curr_idx])
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

        total = len(selected_indices)
        msg = (
            f"处理完成：{applied}/{total}\n"
            f"已跳过（已有缩略图）：{skipped}\n"
            f"失败：{failed}"
        )
        QMessageBox.information(self, "完成", msg)

    def remove_thumbnail_for_current_task(self):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        _tr, thumb = _find_thumbnail_clip(task)
        if thumb is None:
            QMessageBox.information(self, "提示", "当前任务没有可撤销的缩略图")
            return
        self.push_undo()
        ok, _reason = remove_thumbnail_leadin_from_task(task)
        if not ok:
            QMessageBox.warning(self, "失败", "撤销缩略图失败")
            return
        self.timeline.selected_video_clip = None
        self.timeline.selected_track = None
        offset_ms = self._get_video_time_offset_ms()
        total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
        self.timeline.set_data(total_ms, task)
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def edit_thumbnail_duration_for_current_task(self):
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        state = task.get("_thumbnail_state")
        if not isinstance(state, dict) or not state.get("enabled"):
            QMessageBox.information(self, "提示", "当前任务没有缩略图")
            return
        current = float(state.get("duration", self.global_intro_duration) or self.global_intro_duration)
        val, ok = QInputDialog.getDouble(
            self, "修改缩略图时长", "缩略图时长（秒）", current, 0.1, 60.0, 2
        )
        if not ok:
            return
        self.push_undo()
        changed, reason = update_thumbnail_leadin_duration(task, float(val))
        if not changed and reason != "unchanged":
            QMessageBox.warning(self, "失败", "修改缩略图时长失败")
            return
        offset_ms = self._get_video_time_offset_ms()
        total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
        self.timeline.set_data(total_ms, task)
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def apply_thumbnail_text_style_to_all(self):
        if self.curr_idx < 0:
            return
        src_task = self.tasks[self.curr_idx]
        src_ov = _ensure_thumbnail_text_overlay(src_task)
        if not isinstance(src_ov, dict):
            QMessageBox.information(self, "提示", "当前任务没有缩略图文本")
            return
        style = _extract_thumbnail_text_style(src_ov)
        if not style:
            QMessageBox.information(self, "提示", "没有可应用的缩略图文本样式")
            return
        self.push_undo()
        applied = 0
        for i, task in enumerate(self.tasks):
            if i == self.curr_idx:
                continue
            _tr, thumb = _find_thumbnail_clip(task)
            if thumb is None:
                continue
            dst_ov = _ensure_thumbnail_text_overlay(task)
            if not isinstance(dst_ov, dict):
                continue
            _apply_thumbnail_text_style(dst_ov, style)
            state = task.get("_thumbnail_state") if isinstance(task.get("_thumbnail_state"), dict) else {}
            dur = state.get("duration")
            if not dur:
                dur = getattr(thumb, "thumbnail_duration", None) or getattr(thumb, "thumb_duration", None)
            if isinstance(dur, (int, float)) and dur > 0:
                dst_ov["start"] = 0.0
                dst_ov["end"] = float(dur)
            applied += 1
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()
        QMessageBox.information(self, "完成", f"已应用到 {applied} 个缩略图")

    # ─────────────────────────────────────────────────────
    # 开场视频 — UI 方法
    # ─────────────────────────────────────────────────────

    def on_batch_add_intro_video(self):
        """批量为选中任务插入开场视频。"""
        if not self.tasks:
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        selected = self.file_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先选择要处理的视频（可多选）")
            return
        selected_indices = sorted({self.file_list.row(item) for item in selected})
        n_selected = len(selected_indices)

        # ── 设置对话框：转场参数 ──
        dlg = QDialog(self)
        dlg.setWindowTitle("插入开场视频 — 设置")
        dlg.setMinimumWidth(360)
        layout = QVBoxLayout(dlg)

        form = QFormLayout()
        combo_transition = QComboBox()
        combo_transition.addItem("Zoom In（缩放进入）", "zoom_in")
        combo_transition.addItem("交叉溶解（Cross Dissolve）", "cross_dissolve")
        combo_transition.setCurrentIndex(0)
        form.addRow("转场方式:", combo_transition)

        spin_transition = QDoubleSpinBox()
        spin_transition.setRange(0.3, 5.0)
        spin_transition.setSingleStep(0.1)
        spin_transition.setValue(1.5)
        spin_transition.setSuffix(" s")
        form.addRow("转场时长:", spin_transition)

        spin_duration = QDoubleSpinBox()
        spin_duration.setRange(1.0, 30.0)
        spin_duration.setSingleStep(0.5)
        spin_duration.setValue(5.0)
        spin_duration.setSuffix(" s")
        form.addRow("开场时长:", spin_duration)

        spin_zoom_start = QDoubleSpinBox()
        spin_zoom_start.setRange(0.3, 1.0)
        spin_zoom_start.setSingleStep(0.05)
        spin_zoom_start.setDecimals(2)
        spin_zoom_start.setValue(0.7)
        spin_zoom_start.setToolTip("进场缩放起始比例（0.7=默认，越小动感越强）")
        form.addRow("缩放起始:", spin_zoom_start)

        spin_motion_blur = QDoubleSpinBox()
        spin_motion_blur.setRange(0.0, 1.0)
        spin_motion_blur.setSingleStep(0.1)
        spin_motion_blur.setDecimals(2)
        spin_motion_blur.setValue(0.4)
        spin_motion_blur.setToolTip("运动模糊强度（0=无, 1=最强）")
        form.addRow("运动模糊:", spin_motion_blur)

        spin_in_point = QDoubleSpinBox()
        spin_in_point.setRange(0.0, 300.0)
        spin_in_point.setSingleStep(0.5)
        spin_in_point.setDecimals(2)
        spin_in_point.setValue(0.0)
        spin_in_point.setSuffix(" s")
        spin_in_point.setToolTip("源素材起播点：跳过前面的画面，从该秒开始截取")
        form.addRow("开场起播点:", spin_in_point)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("选择开场视频文件夹")
        btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        t_type = combo_transition.currentData() or "zoom_in"
        t_dur = float(spin_transition.value())
        intro_dur_limit = float(spin_duration.value())
        t_zoom_start = float(spin_zoom_start.value())
        t_motion_blur = float(spin_motion_blur.value())
        t_in_point = float(spin_in_point.value())

        # ── 选择文件夹 ──
        folder = QFileDialog.getExistingDirectory(self, "选择开场视频文件夹", "")
        if not folder:
            return

        # 扫描文件夹内视频文件（排除"已导入"子文件夹内的文件）
        imported_subdir = Path(folder) / "已导入"
        intro_files = []
        for f in Path(folder).iterdir():
            if f.is_file() and f.suffix.lower() in VID_EXT:
                intro_files.append(f)
        if not intro_files:
            QMessageBox.warning(self, "提示", "所选文件夹内没有视频文件")
            return
        if len(intro_files) < n_selected:
            QMessageBox.warning(self, "提示",
                f"文件夹内视频数量不足（需要 {n_selected} 个，仅找到 {len(intro_files)} 个）")
            return

        # 随机抽取 N 个
        chosen = random.sample(intro_files, n_selected)

        # ── 阶段 1：先移动文件到"已导入"文件夹并重命名 ──
        # 必须先移动文件获取最终路径，再创建 MediaSource，否则路径会失效
        imported_dir = Path(folder) / "已导入"
        move_errors = []
        final_paths = []  # 与 selected_indices 一一对应的最终文件路径
        try:
            imported_dir.mkdir(exist_ok=True)
        except Exception as e:
            move_errors.append(f"创建'已导入'文件夹失败: {e}")
            # 无法创建文件夹时，直接使用原始路径（不移动文件）
            final_paths = [str(p) for p in chosen]

        if not final_paths:
            for idx, intro_path in zip(selected_indices, chosen):
                task = self.tasks[idx]
                orig_video = task.get("video", "")
                orig_stem = Path(orig_video).stem if orig_video else "video"
                intro_p = Path(intro_path)
                new_name = f"{orig_stem}KC{intro_p.suffix}"
                dest = imported_dir / new_name
                # 避免重名
                counter = 1
                while dest.exists():
                    new_name = f"{orig_stem}KC_{counter}{intro_p.suffix}"
                    dest = imported_dir / new_name
                    counter += 1
                try:
                    shutil.move(str(intro_p), str(dest))
                    final_paths.append(str(dest))
                except Exception as e:
                    move_errors.append(f"{intro_p.name}: {e}")
                    # 移动失败时使用原始路径
                    final_paths.append(str(intro_p))

        # ── 阶段 2：批量应用（使用最终路径创建 MediaSource）──
        if self.curr_idx in selected_indices:
            self.push_undo()

        applied = 0
        skipped = 0
        failed = 0

        for idx, final_path in zip(selected_indices, final_paths):
            task = self.tasks[idx]
            # 跳过已有开场视频的任务
            _vtr, _vc, _atr, _ac = _find_intro_video_clips(task)
            if _vc is not None:
                skipped += 1
                continue
            ok, rs_or_sid = apply_intro_video_to_task(
                task, final_path,
                transition_type=t_type,
                transition_duration=t_dur,
                intro_duration=intro_dur_limit,
                zoom_scale_start=t_zoom_start,
                motion_blur=t_motion_blur,
                intro_in_t=t_in_point,
            )
            if ok:
                applied += 1
                self._trigger_waveform_generation(rs_or_sid, final_path)
            else:
                failed += 1
                print(f"[INTRO] apply failed for {final_path}: {rs_or_sid}")

        # ── 阶段 3：刷新 UI ──
        if self.curr_idx in selected_indices:
            self.resegment_current_segments()
            offset_ms = self._get_video_time_offset_ms()
            total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
            self.timeline.set_data(total_ms, self.tasks[self.curr_idx])
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

        # ── 结果提示 ──
        msg = (
            f"处理完成：{applied}/{n_selected}\n"
            f"已跳过（已有开场视频）：{skipped}\n"
            f"失败：{failed}\n"
            f"文件已移动到'已导入'：{len(final_paths) - len(move_errors)}/{n_selected}"
        )
        if move_errors:
            msg += f"\n文件移动错误：{'; '.join(move_errors[:3])}"
        QMessageBox.information(self, "完成", msg)

    def remove_intro_video_for_current_task(self):
        """撤销当前任务的开场视频。"""
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        _vtr, _vc, _atr, _ac = _find_intro_video_clips(task)
        if _vc is None:
            QMessageBox.information(self, "提示", "当前任务没有可撤销的开场视频")
            return
        self.push_undo()
        ok, reason = remove_intro_video_from_task(task)
        if not ok:
            QMessageBox.warning(self, "失败", "撤销开场视频失败")
            return
        self.timeline.selected_video_clip = None
        self.timeline.selected_track = None
        offset_ms = self._get_video_time_offset_ms()
        total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
        self.timeline.set_data(total_ms, task)
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def edit_intro_video_duration(self):
        """右键菜单 → 修改开场视频时长。"""
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        state = task.get("_intro_video_state")
        if not isinstance(state, dict) or not state.get("enabled"):
            QMessageBox.information(self, "提示", "当前任务没有开场视频")
            return
        current_dur = float(state.get("effective_duration", 5.0))
        full_dur = float(state.get("full_duration", 30.0))
        val, ok = QInputDialog.getDouble(
            self, "修改开场视频时长", f"开场视频时长（秒）\n源素材总长：{full_dur:.1f}s",
            current_dur, 0.5, full_dur, 2
        )
        if not ok:
            return
        self.push_undo()
        changed, result = update_intro_video_duration(task, float(val))
        if not changed:
            QMessageBox.warning(self, "失败", f"修改开场视频时长失败：{result}")
            return
        self._trigger_waveform_generation(result, state.get("intro_path", ""))
        offset_ms = self._get_video_time_offset_ms()
        total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
        self.timeline.set_data(total_ms, task)
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def edit_intro_video_settings(self):
        """右键菜单 → 修改转场设置（完整对话框，预填充当前参数）。"""
        if self.curr_idx < 0:
            return
        task = self.tasks[self.curr_idx]
        state = task.get("_intro_video_state")
        if not isinstance(state, dict) or not state.get("enabled"):
            QMessageBox.information(self, "提示", "当前任务没有开场视频")
            return

        # ── 构建设置对话框（预填充当前值）──
        dlg = QDialog(self)
        dlg.setWindowTitle("修改开场视频转场设置")
        dlg.setMinimumWidth(360)
        layout = QVBoxLayout(dlg)

        form = QFormLayout()
        combo_transition = QComboBox()
        combo_transition.addItem("Zoom In（缩放进入）", "zoom_in")
        combo_transition.addItem("交叉溶解（Cross Dissolve）", "cross_dissolve")
        # 预设当前值
        current_t_type = state.get("transition_type", "zoom_in_both")
        if "cross_dissolve" in current_t_type:
            combo_transition.setCurrentIndex(1)
        else:
            combo_transition.setCurrentIndex(0)
        form.addRow("转场方式:", combo_transition)

        spin_transition = QDoubleSpinBox()
        spin_transition.setRange(0.3, 5.0)
        spin_transition.setSingleStep(0.1)
        spin_transition.setValue(float(state.get("transition_duration", 1.5)))
        spin_transition.setSuffix(" s")
        form.addRow("转场时长:", spin_transition)

        full_dur = float(state.get("full_duration", 30.0))
        spin_duration = QDoubleSpinBox()
        spin_duration.setRange(0.5, max(30.0, full_dur))
        spin_duration.setSingleStep(0.5)
        spin_duration.setValue(float(state.get("effective_duration", 5.0)))
        spin_duration.setSuffix(" s")
        spin_duration.setToolTip(f"源素材总长：{full_dur:.1f}s")
        form.addRow("开场时长:", spin_duration)

        spin_zoom_start = QDoubleSpinBox()
        spin_zoom_start.setRange(0.3, 1.0)
        spin_zoom_start.setSingleStep(0.05)
        spin_zoom_start.setDecimals(2)
        spin_zoom_start.setValue(float(state.get("zoom_scale_start", 0.7)))
        spin_zoom_start.setToolTip("进场缩放起始比例（0.7=默认，越小动感越强）")
        form.addRow("缩放起始:", spin_zoom_start)

        spin_motion_blur = QDoubleSpinBox()
        spin_motion_blur.setRange(0.0, 1.0)
        spin_motion_blur.setSingleStep(0.1)
        spin_motion_blur.setDecimals(2)
        spin_motion_blur.setValue(float(state.get("motion_blur", 0.4)))
        spin_motion_blur.setToolTip("运动模糊强度（0=无, 1=最强）")
        form.addRow("运动模糊:", spin_motion_blur)

        spin_in_point = QDoubleSpinBox()
        spin_in_point.setRange(0.0, max(300.0, full_dur))
        spin_in_point.setSingleStep(0.5)
        spin_in_point.setDecimals(2)
        spin_in_point.setValue(float(state.get("intro_in_t", 0.0)))
        spin_in_point.setSuffix(" s")
        spin_in_point.setToolTip(f"源素材起播点：跳过前面的画面（源总长 {full_dur:.1f}s）")
        form.addRow("开场起播点:", spin_in_point)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("应用修改")
        btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_t_type = combo_transition.currentData() or "zoom_in"
        new_t_dur = float(spin_transition.value())
        new_intro_dur = float(spin_duration.value())
        new_zoom = float(spin_zoom_start.value())
        new_blur = float(spin_motion_blur.value())
        new_in_point = float(spin_in_point.value())

        self.push_undo()
        changed, result = update_intro_video_settings(
            task,
            new_duration=new_intro_dur,
            transition_type=new_t_type,
            transition_duration=new_t_dur,
            zoom_scale_start=new_zoom,
            motion_blur=new_blur,
            intro_in_t=new_in_point,
        )
        if not changed:
            QMessageBox.warning(self, "失败", f"修改转场设置失败：{result}")
            return
        self._trigger_waveform_generation(result, state.get("intro_path", ""))
        offset_ms = self._get_video_time_offset_ms()
        total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
        self.timeline.set_data(total_ms, task)
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

    def apply_intro_video_settings_to_all(self):
        """将当前任务的开场视频设置（时长、转场参数）批量应用到所有其他已有开场视频的任务。"""
        if self.curr_idx < 0:
            return
        src_task = self.tasks[self.curr_idx]
        src_state = src_task.get("_intro_video_state")
        if not isinstance(src_state, dict) or not src_state.get("enabled"):
            QMessageBox.information(self, "提示", "当前任务没有开场视频，无法作为源")
            return

        # 提取当前任务参数
        target_dur = float(src_state.get("intro_duration_limit", src_state.get("effective_duration", 5.0)))
        target_t_type = src_state.get("transition_type", "zoom_in_both")
        target_t_dur = float(src_state.get("transition_duration", 1.5))
        target_zoom = float(src_state.get("zoom_scale_start", 0.7))
        target_blur = float(src_state.get("motion_blur", 0.4))
        target_in_t = float(src_state.get("intro_in_t", 0.0))

        applied = 0
        skipped = 0
        failed = 0
        self.push_undo()

        for i, task in enumerate(self.tasks):
            if i == self.curr_idx:
                continue
            dst_state = task.get("_intro_video_state")
            if not isinstance(dst_state, dict) or not dst_state.get("enabled"):
                skipped += 1
                continue
            ok, _result = update_intro_video_settings(
                task,
                new_duration=target_dur,
                transition_type=target_t_type,
                transition_duration=target_t_dur,
                zoom_scale_start=target_zoom,
                motion_blur=target_blur,
                intro_in_t=target_in_t,
            )
            if ok:
                applied += 1
            else:
                failed += 1

        # 刷新当前视图
        if self.curr_idx >= 0:
            offset_ms = self._get_video_time_offset_ms()
            total_ms = self.media_player.duration() + offset_ms if offset_ms > 0 else self.media_player.duration()
            self.timeline.set_data(total_ms, self.tasks[self.curr_idx])
        self.timeline.update()
        self.video_canvas.update()
        self.sync_ui()

        total_others = len(self.tasks) - 1
        QMessageBox.information(self, "完成",
            f"已应用设置到 {applied} 个任务\n"
            f"跳过（无开场视频）：{skipped}\n"
            f"失败：{failed}")

    def edit_subtitle_text(self, idx):
        if self.curr_idx < 0: return
        task = self.tasks[self.curr_idx]
        seg = task['segments'][idx]
        
        dlg = SubtitleEditDialog(seg.get('text', ''), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            txt = dlg.text_edit.toPlainText()
            # 入库前清洗换行符，防止渲染断层
            txt = txt.replace('\r\n', '\n').replace('\r', '\n')
            self.push_undo()

            style = task.get('style', self.default_style) or self.default_style

            # 处理高级文本框："Text模式" 下直接保存为单段，避免按换行符拆分
            if style.get('advanced_textbox_enabled', False):
                old_text = seg.get('text', '')
                seg['text'] = txt
                seg['edited_text'] = txt

                # ── 文本修改联动保护（预留接口） ──
                # 文本长度变化时，字块的 start_char/end_char 索引可能越界。
                # 当前策略：自动钳位索引 + 弹出提示。
                # 后续迭代可升级为：锁定文本 / 智能重映射 / 重置排版。
                blocks = seg.get("adv_blocks", [])
                has_detached = any(
                    b.get('overrides', {}).get('is_detached', False) for b in blocks
                )
                if blocks and len(txt) != len(old_text):
                    self._clamp_block_indices(seg)
                    # 如果存在全局字块，更新其 end_char 为新文本长度
                    for blk in blocks:
                        if blk.get("is_global") and blk.get("end_char") == len(old_text):
                            blk["end_char"] = len(txt)
                    self._rebuild_adv_spans_from_blocks(seg)
                    if has_detached:
                        QMessageBox.information(
                            self, "提示",
                            "文本长度已改变，已自动调整字块边界。\n"
                            "分离字块的选区可能需要手动重新确认。"
                        )

                self.video_canvas.update()
                self.timeline.update()
                self.sync_ui()
                return

            video_w = 1920
            try:
                video_w = int((task.get('dims') or (1920, 1080))[0])
            except Exception:
                video_w = 1920

            # 按当前样式的「字幕宽度(%) / 行数」把编辑后的文本切成多个字幕条，
            # 彻底避免“渲染时省略号截断”。
            def split_text_to_segments(text: str):
                wrap_lines = max(1, int(style.get('wrap_lines', 2) or 2))
                from cutora.subtitle.stylepack import line_max_width_px
                try:
                    fs = float(style.get("fontsize") or 60)
                except (TypeError, ValueError):
                    fs = 60.0
                max_width = int(line_max_width_px(style, video_w, em=fs))

                font = get_font_manager().get_font(
                    sanitize_font_family(style.get("font_family")),
                    int(style.get('fontsize', 60) or 60),
                    bold=bool(style.get('bold', True)),
                    italic=bool(style.get('italic', False))
                )
                fm = QFontMetrics(font)

                # 先按像素宽度换行，再按 wrap_lines 分“页”
                lines = wrap_text_by_pixel_width(text or "", fm, max_width)
                pages = []
                curr = []
                for ln in lines:
                    curr.append(ln)
                    if len(curr) >= wrap_lines:
                        pages.append("\n".join(curr).strip())
                        curr = []
                if curr:
                    pages.append("\n".join(curr).strip())
                if not pages:
                    pages = ["" ]
                return pages

            pages = split_text_to_segments(txt)

            # 依据段落时长把页分配到时间线上（不改变字号；尽量保持与音频匹配）
            start = seg.get('start')
            end = seg.get('end')
            duration = 0.0
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
                duration = float(end - start)
            n = max(1, len(pages))

            new_segments = []
            for i, page_text in enumerate(pages):
                if duration > 0:
                    s0 = start + duration * (i / n)
                    s1 = start + duration * ((i + 1) / n)
                else:
                    s0, s1 = start, end
                new_seg = copy.deepcopy(seg)
                new_seg['start'] = s0
                new_seg['end'] = s1
                new_seg['text'] = page_text
                new_seg['edited_text'] = page_text
                new_segments.append(new_seg)

            # 替换当前字幕条，并插入额外的字幕条到后面
            task['segments'][idx:idx+1] = new_segments

            self.video_canvas.update()
            self.timeline.update()
            self.sync_ui()

    def reload_style(self):
        from cutora.core.history import _load_app_settings
        from cutora.ui.theme.apply import apply_theme
        s = _load_app_settings() or {}
        apply_theme(s.get("theme_id") or "roma-gold", s.get("theme_accent"))

    def sync_style_to_selected(self):
        source_id = self.curr_idx
        if source_id is None or source_id < 0:
            return
        targets = [i for i in self.resolve_bin_targets() if i != source_id]
        if not targets:
            QMessageBox.information(self, "提示", "请先勾选或选中目标视频")
            return
        name = self._bin_item_name(source_id)
        if not self._confirm_bin_batch(
            "拷贝字幕样式", name, len(targets), "目标视频的字幕样式"):
            return
        result = self.bus.execute(
            "batch.copy_subtitle_style",
            {"src_id": source_id, "target_ids": targets},
            source="ui",
        )
        n = (result or {}).get("applied", 0)
        self.sync_ui()
        self.statusBar().showMessage("已将「%s」的字幕样式套用到 %d 个视频" % (name, n), 5000)

    def _prompt_export_platform(self, current_wh, duration_sec):
        """Export-dialog platform combo. Suggestions only; Cancel aborts export."""
        from cutora.render.export_platform import (
            hint_text, list_platforms, resolve_platform_id, set_active_platform_id,
        )
        if self._canvas_prompt_is_headless():
            return resolve_platform_id()
        dlg = QDialog(self)
        dlg.setWindowTitle("导出")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("平台："))
        combo = QComboBox()
        combo.setObjectName("export_platform")
        for row in list_platforms():
            combo.addItem(row.get("name") or row["id"], row["id"])
        idx = combo.findData(resolve_platform_id())
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addWidget(combo)
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        def _upd(i):
            hint.setText(hint_text(combo.itemData(i), current_wh, duration_sec))
        combo.currentIndexChanged.connect(_upd)
        _upd(combo.currentIndex())
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        from cutora.ui.i18n import localize_button_box
        localize_button_box(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        pid = combo.currentData()
        try:
            set_active_platform_id(pid)
        except Exception as exc:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").exception(
                "save export platform failed: %s", exc)
        return pid

    def _prompt_canvas_if_platform_mismatch(self, suggestions):
        """Same tone as ``_prompt_canvas_if_sources_mismatch``. Never auto-applies."""
        if not suggestions or not suggestions.get("canvas_mismatch"):
            return
        from cutora.render.export_platform import canvas_mismatch_message
        msg = suggestions.get("canvas_prompt") or canvas_mismatch_message(
            suggestions.get("id"), suggestions.get("current_wh"))
        if not msg:
            return
        from cutora.core.log import get_logger
        log = get_logger("cutora.ui.main_window")
        if self._canvas_prompt_is_headless():
            log.info(
                "platform canvas mismatch platform=%s suggested=%s; keeping canvas",
                suggestions.get("id"), suggestions.get("canvas_wh"),
            )
            return
        reply = QMessageBox.question(
            self,
            "画布尺寸",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            w, h = suggestions["canvas_wh"]
            self._set_canvas_size(int(w), int(h))
        else:
            log.info(
                "platform canvas mismatch platform=%s suggested=%s; user kept canvas",
                suggestions.get("id"), suggestions.get("canvas_wh"),
            )

    def export_video(self):
        if self.curr_idx < 0: return
        task = self.tasks[self.curr_idx]
        from cutora.model.canvas import canvas_wh
        from cutora.render.export_platform import select_platform
        current_wh = canvas_wh(task)
        duration = float(task.get("duration") or 0.0)
        pid = self._prompt_export_platform(current_wh, duration)
        if pid is None:
            return
        sug = select_platform(pid, current_wh=current_wh, duration_sec=duration)
        warn = sug.get("duration_warning") or ""
        if warn:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.main_window").warning(warn)
            self.statusBar().showMessage(warn, 8000)
            if not self._canvas_prompt_is_headless():
                QMessageBox.information(self, "平台时长", warn)
        if sug.get("canvas_mismatch"):
            self._prompt_canvas_if_platform_mismatch(sug)
        suffix = sug.get("filename_suffix") or "_FINAL"
        from cutora.render.export_dest import prompt_export_folder, unique_mp4_path
        folder = prompt_export_folder(self)
        if folder is False:
            return
        if folder:
            task["_export_dest_folder"] = folder
            out = unique_mp4_path(
                folder, task.get("video") or "out.mp4",
                suffix=suffix,
                name=task.get("name"))
            from cutora.render.export_params import honor_io_dialog_line, should_honor_io
            self.bus.execute(
                "export.run",
                {"out": out, "honor_io": should_honor_io(task)},
                source="ui",
            )
            return
        video = task['video']
        if str(video).lower().endswith(".mp4"):
            default_name = str(video)[:-4] + suffix + ".mp4"
        else:
            default_name = str(video).replace(".mp4", suffix + ".mp4")
        try:
            from cutora.batch import script_insert_image as _sii
            default_name = _sii.resolve_export_output_path(task, default_name)
        except Exception:
            pass
        from cutora.render.export_params import honor_io_dialog_line, should_honor_io
        title = "导出视频"
        io_line = honor_io_dialog_line(task)
        if io_line:
            title = "导出视频 — " + io_line
        out, _ = QFileDialog.getSaveFileName(self, title, default_name, "Video (*.mp4)")
        if not out: return
        try:
            from cutora.batch import script_insert_image as _sii
            out = _sii.resolve_export_output_path(task, out)
        except Exception:
            pass
        self.bus.execute(
            "export.run",
            {"out": out, "honor_io": should_honor_io(task)},
            source="ui",
        )

    def stop_export(self):
        if hasattr(self, 'render') and hasattr(self.render, "stop") and callable(self.render.stop):
            self.render.stop()
        elif hasattr(self, 'render'):
            if not self._warned_render_no_stop:
                print("[WARN] render has no stop()")
                self._warned_render_no_stop = True
        self.is_exporting = False
        self._fps_meter_clear()
        self.update_ui_state()
        self.btn_exp.setEnabled(True)
        try:
            from cutora.render.export_guard import clear_stop_flag
            clear_stop_flag(self._export_state_path())
        except Exception:
            pass
    
    def on_seek_request(self, pos_ms):
        now = time.time()
        clk = getattr(self, "_preview_clock", None)
        if clk is None:
            from cutora.preview.engine import PreviewClock
            clk = PreviewClock()
            self._preview_clock = clk
        clk.end_ms = self._timeline_end_ms()
        clk.seek(int(pos_ms), now=now)
        self._timeline_ms = int(clk.ms)
        self._timeline_last_ts = now
        offset_ms = self._get_video_time_offset_ms()
        video_ms = self._timeline_to_video_ms(self._timeline_ms)
        _seek_trace_emit(
            "seek_request",
            pos_ms=int(pos_ms),
            video_ms=int(video_ms),
            timeline_ms=int(self._timeline_ms),
            player_ms=int(self.media_player.position()),
            using_media_clock=bool(self._using_media_clock),
            playing=bool(self._timeline_playing),
        )
        self._using_media_clock = True
        # Q157：按住播放头拖的时候（擦洗）不让音频播放器每一步都 seek —— 那一步在后端里最慢，
        # 鼠标跟不上手；松手那一下（_scrub_end）再 seek 一次就够了
        scrubbing = bool(getattr(getattr(self, "timeline", None), "_scrubbing", False))
        if not (scrubbing and not self._timeline_playing):
            self.media_player.setPosition(int(self._timeline_ms))
        io = getattr(self, "_stem_pull_io", None)
        if io is not None and hasattr(io, "reset_cursor"):
            io.reset_cursor()
        if self._timeline_playing:
            self.media_player.play()
        self._seek_pending_target = int(self._timeline_ms)
        self._seek_pending_timeline = int(self._timeline_ms)
        self._seek_pending_since = now
        self._present_preview_frame()
        self.timeline.set_position(int(self._timeline_ms))
        if self.curr_idx >= 0:
            self.video_canvas.set_data(
                self.tasks[self.curr_idx], self._timeline_ms / 1000.0)

    def toggle_play_pause(self):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return
        self.toggle_play()

    # ══════════════ 音频输出设备热插拔 (0829) ══════════════
    #
    # 病症: 先开软件、后连蓝牙耳机 → 耳机里没声音, 必须重启软件。
    # 原因: 上面那三个 QAudioOutput 都是不带参数构造的, Qt 只在【构造那一刻】
    #       取一次系统默认输出设备, 之后不跟随系统切换 (Qt6 的设计: 跟不跟随
    #       由应用自己决定)。于是音频一直往开机时那个端点(笔记本扬声器)送,
    #       重启软件等于让构造函数重新取一次默认设备, 所以重启就好了。
    #
    # 两道保险, 缺一不可:
    #   ① audioOutputsChanged —— 设备【列表】变了就重绑(插拔蓝牙/USB 声卡)。
    #      带 800ms 防抖: Windows 连上蓝牙之后音频端点要一两秒才建出来,
    #      而且这个信号在连接过程中会连发好几次, 不防抖会重绑一串。
    #   ② 每次开始播放前比一次设备 id —— 覆盖"两个设备都在线, 只是在系统里
    #      把默认从 A 改成 B"这种情况: 那时列表没变, ① 根本不会触发。
    #
    # 全程 try/except 包住: 拿不到设备、后端不支持 setDevice 之类的情况一律
    # 静默跳过, 退化成改之前的老行为(重启软件), 绝不因为这个功能弹错误。
    def _on_audio_devices_changed(self):
        try:
            self._audio_dev_timer.start(800)
        except Exception:
            pass

    def _audio_output_pairs(self):
        """[(输出对象, 对应播放器)]。构造顺序的关系, 用 getattr 兜住还没建好的。"""
        pairs = []
        for a, p in (("audio_output", "media_player"),
                     ("intro_audio_output", "intro_player"),
                     ("bgm_audio_output", "bgm_player")):
            out = getattr(self, a, None)
            if out is not None:
                pairs.append((out, getattr(self, p, None)))
        # 音效预览那几路也要跟着换设备, 否则插上耳机之后只有音效还留在扬声器
        for e in (getattr(self, "_sfx_players", None) or []):
            if e.get("out") is not None:
                pairs.append((e["out"], e.get("player")))
        # 代理音轨这一路【必须】在名单里。漏了它的后果最严重:
        # 代理在播时主音频是静音的, 代理却还留在旧设备上 —— 插上耳机之后
        # 两边都听不到, 表现就是"改完参数一播放就没声音了"。
        o = getattr(self, "_preview_fx_out", None)
        if o is not None:
            pairs.append((o, getattr(self, "_preview_fx_player", None)))
        return pairs

    def _default_audio_device(self):
        try:
            dev = QMediaDevices.defaultAudioOutput()
            return None if (dev is None or dev.isNull()) else dev
        except Exception:
            return None

    def _rebind_stem_sink(self, dev=None) -> bool:
        """当系统默认音频输出设备变化（如打开软件后连接蓝牙耳机）时，重建 QAudioSink 绑定到新设备。"""
        if dev is None:
            dev = self._default_audio_device()
        if dev is None:
            return False
        cur_id = getattr(self, "_stem_sink_dev_id", None)
        if cur_id is not None and cur_id == dev.id() and getattr(self, "_stem_sink", None) is not None:
            return False
        was_running = bool(getattr(self, "_stem_sink_running", False))
        old_sink = getattr(self, "_stem_sink", None)
        if old_sink is not None:
            try:
                old_sink.stop()
            except Exception:
                pass
            try:
                old_sink.deleteLater()
            except Exception:
                pass
        self._stem_sink_running = False
        try:
            fmt = getattr(self, "_stem_audio_fmt", None)
            if fmt is None:
                fmt = QAudioFormat()
                fmt.setSampleRate(48000)
                fmt.setChannelCount(2)
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
                self._stem_audio_fmt = fmt
            new_sink = QAudioSink(dev, fmt, self)
            try:
                new_sink.setBufferSize(48000 * 2 * 4 // 5)
            except Exception:
                pass
            self._stem_sink = new_sink
            self._stem_sink_dev_id = dev.id()
            if was_running:
                self._sync_stem_sink()
            return True
        except Exception:
            from cutora.core.log import get_logger as _stem_log
            _stem_log("cutora.audio").exception("[Audio] QAudioSink rebind failed")
            return False

    def _rebind_audio_outputs(self):
        """把三个输出及 QAudioSink 重新绑到当前系统默认设备。已经绑对了就一个字都不动。"""
        dev = self._default_audio_device()
        if dev is None:
            return
        # 先把三个都切过去, 再统一"踢"一次正在播的 —— 不要边切边 pause/play:
        # 那会在循环中途触发 playbackStateChanged → _sync_bgm_state, 自己咬自己。
        kicked, changed = [], 0
        if self._rebind_stem_sink(dev):
            changed += 1
        for out, player in self._audio_output_pairs():
            try:
                cur = out.device()
                if cur is not None and not cur.isNull() and cur.id() == dev.id():
                    continue
                vol, muted = out.volume(), out.isMuted()
                if player is not None and (player.playbackState()
                                           == QMediaPlayer.PlaybackState.PlayingState):
                    kicked.append((player, player.position()))
                out.setDevice(dev)
                # 音量/静音是输出对象自己的属性, 换设备后 Qt 不保证保留 —— 原样搬回去。
                # bgm 的音量常年是 0.0(靠 _sync_bgm_to_video 调), 丢了会突然炸出背景音乐。
                out.setVolume(vol)
                out.setMuted(muted)
                changed += 1
            except Exception:
                continue
        if not changed:
            return
        # 有些后端换完设备 sink 不会自己重开, 正在播的要 pause→seek→play 踢一下才出声。
        for player, pos in kicked:
            try:
                player.pause()
                if pos is not None:
                    player.setPosition(int(pos))
                player.play()
            except Exception:
                continue
        try:
            name = dev.description() or "默认设备"
            self.statusBar().showMessage(f"音频输出已切换到：{name}", 3000)
        except Exception:
            pass

    def _ensure_audio_device(self):
        """开始播放前的兜底: 系统默认设备已经换了就先重绑再播。"""
        try:
            dev = self._default_audio_device()
            if dev is None:
                return
            out = getattr(self, "audio_output", None)
            cur = out.device() if out is not None else None
            sink_id = getattr(self, "_stem_sink_dev_id", None)
            if (cur is None or cur.isNull() or cur.id() != dev.id()
                    or sink_id != dev.id()):
                self._rebind_audio_outputs()
        except Exception:
            pass

    # ══════════════ 音效实时预览 (0829) ══════════════
    #
    # 音效(鼓掌 / Amen / 大厅同期声)本来只在【导出】时才混进去, 预览里听不见 ——
    # 铺得合不合适, 只能导出一条完整视频才知道。这里让预览也把它们放出来。
    #
    # 做法照搬现成的 BGM 那一套 (_sync_bgm_state / _sync_bgm_to_video):
    # 主播放器位置一变, 就看当前时间点上哪几条 sfx clip 该响, 给它分配一路播放器、
    # 对齐位置、按 clip 自己的淡入淡出算音量。和 BGM 唯一的区别是数量:
    # BGM 同一时刻只有一条, 音效可能好几条叠在一起(垫底的同期声 + 爆发的鼓掌),
    # 所以这里是一个最多 SFX_PREVIEW_MAX 路的小播放器池, 用完即还。
    #
    # 三条自我约束, 都是为了不碰坏现在能用的东西:
    #   · 只【读】时间线, 一个字都不写回 —— 预览开不开, 导出结果完全一样;
    #   · 主音频 / BGM / 开场那三个播放器一律不碰;
    #   · 出任何岔子(文件缺失、池子用完、后端不支持)一律静默跳过,
    #     顶多这一声没响, 绝不影响画面和主音频。
    #
    # 注意: 这里只解决【音效】。空间立体声(大厅堂混响)是导出时的 ffmpeg 滤镜,
    # Qt 的播放器插不进 DSP, 想实时听得走"预渲染代理音轨"那条路, 是另一件事。
    SFX_PREVIEW_MAX = 4

    def _on_toggle_sfx_preview(self, on):
        self._sfx_preview_on = bool(on)
        if not on:
            self._sfx_stop_all()
        rebuild = getattr(self, "_rebuild_preview_wav", None)
        if callable(rebuild):
            rebuild(immediate=False)

    def _sfx_acquire(self):
        """从池子里要一路空闲播放器; 池子没满就新建, 满了返回 None(这一声就不响)。"""
        pool = getattr(self, "_sfx_players", None)
        if pool is None:
            pool = self._sfx_players = []
        for e in pool:
            if not e.get("busy"):
                return e
        if len(pool) >= self.SFX_PREVIEW_MAX:
            return None
        try:
            p = QMediaPlayer()
            o = QAudioOutput()
            p.setAudioOutput(o)
            o.setVolume(0.0)
        except Exception:
            return None
        e = {"player": p, "out": o, "busy": False, "src": ""}
        pool.append(e)
        return e

    def _sfx_release(self, e):
        if not e:
            return
        try:
            e["out"].setVolume(0.0)
            e["player"].pause()
        except Exception:
            pass
        e["busy"] = False

    def _sfx_stop_all(self):
        slots = getattr(self, "_sfx_slots", None)
        if not slots:
            return
        for k in list(slots.keys()):
            self._sfx_release(slots.pop(k, None))

    def _preview_live_audio_ids(self):
        """Clip object ids that currently output (mute/solo via track_outputs)."""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            return set()
        from cutora.model.track_guard import compute_audio_tracks
        solo_ids = getattr(getattr(self, "timeline", None), "_solo_ids", set()) or set()
        return {id(c) for c in compute_audio_tracks(
            self.tasks[self.curr_idx], solo_ids=solo_ids)}

    def _get_current_sfx_clips(self):
        """当前任务时间线上的音效 clip(只读)。文件不在就跳过, 不报错。"""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            return []
        try:
            rows = _audio_clips_from_track(self.tasks[self.curr_idx])
        except Exception:
            return []
        try:
            live_ids = self._preview_live_audio_ids()
        except Exception:
            live_ids = None
        out = []
        for c in rows:
            try:
                if c.get("audio_role") != "sfx":
                    continue
                obj = c.get("_clip_obj")
                if live_ids is not None and obj is not None and id(obj) not in live_ids:
                    continue
                path = c.get("path") or ""
                if not path or not os.path.isfile(path):
                    continue
                d = dict(c)
                d["_key"] = id(c.get("_clip_obj")) or (path, c.get("start"))
                out.append(d)
            except Exception:
                continue
        return out

    def _sync_sfx_preview(self, *_args):
        try:
            if getattr(self, "_proxy_ready", False) or _host_stems_ready(self):
                self._sfx_stop_all()
                return
            if not getattr(self, "_sfx_preview_on", True):
                self._sfx_stop_all()
                return
            # 时间线在走就算在播。不能只看 media_player: 有开场视频时,
            # 时间线跑在开场那一段, 主播放器是暂停的, 但音效该响还是要响。
            playing = bool(getattr(self, "_timeline_playing", False)) or (
                self.media_player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)
            if not playing or self.curr_idx < 0:
                self._sfx_stop_all()
                return
            # 用时间线时钟而不是 media_player.position(): 有开场视频时两者差一个偏移,
            # 而 sfx clip 的 start 记的是时间线时间。
            t = float(getattr(self, "_timeline_ms", 0) or 0) / 1000.0
            slots = getattr(self, "_sfx_slots", None)
            if slots is None:
                slots = self._sfx_slots = {}
            seen = set()
            for c in self._get_current_sfx_clips():
                start = float(c.get("start") or 0.0)
                end = float(c.get("end") or 0.0)
                if not (start <= t < end):
                    continue
                key = c["_key"]
                seen.add(key)
                e = slots.get(key)
                if e is None:
                    e = self._sfx_acquire()
                    if e is None:
                        continue        # 同时响的超过池子容量, 这一声让掉
                    e["busy"] = True
                    slots[key] = e
                    if e.get("src") != c["path"]:
                        e["player"].setSource(QUrl.fromLocalFile(c["path"]))
                        e["src"] = c["path"]
                    try:
                        # 垫底的同期声铺满全片, 素材本身短, 要循环
                        e["player"].setLoops(QMediaPlayer.Loops.Infinite
                                             if c.get("loop") else 1)
                    except Exception:
                        pass
                p = e["player"]
                # 音量 = clip 增益 × 淡入淡出。和导出用的是同一组数值。
                vol = float(c.get("volume") or 1.0)
                fi = float(c.get("fade_in_dur") or 0.0)
                fo = float(c.get("fade_out_dur") or 0.0)
                if fi > 0 and (t - start) < fi:
                    vol *= max(0.0, (t - start) / fi)
                if fo > 0 and (end - t) < fo:
                    vol *= max(0.0, (end - t) / fo)
                e["out"].setVolume(max(0.0, min(1.0, vol)))
                target = (t - start + float(c.get("trim_in") or 0.0)) * 1000.0
                if c.get("loop"):
                    d = p.duration()
                    if d and d > 200:
                        target = target % d
                if abs(p.position() - target) > 250:
                    p.setPosition(int(max(0.0, target)))
                if p.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                    p.play()
            for k in [k for k in slots.keys() if k not in seen]:
                self._sfx_release(slots.pop(k, None))
        except Exception:
            # 预览音效不值得为它弹任何错误, 更不该影响主播放
            try:
                self._sfx_stop_all()
            except Exception:
                pass

    # ══════════════ 成品效果预览 · 代理音轨 (0829) ══════════════
    #
    # 要解决的事: 空间立体声(大厅堂混响)、变调、淡入淡出这三样都是导出时的
    # ffmpeg 滤镜, 而 Qt 的播放器没有 DSP 插入点 —— 预览永远听的是原始音轨,
    # 铺得对不对只能导出完才知道。
    #
    # 做法: 后台用【和导出逐字相同】的滤镜把当前视频的主音频渲成一条代理音轨
    # (m4a/aac), 播放时把主播放器的声音静音、改放这条代理, 位置 1:1 跟着走。
    # 渲染几秒钟, 结果按参数指纹缓存, 参数不变就不再渲。
    #
    # 几条关键取舍:
    #   · 音量【不】烘进渲染。它是拖得最频繁的旋钮, 烘进去等于每拖一下重渲一次;
    #     而音量本来就能实时改, 播的时候把主输出的音量镜像给代理播放器就行。
    #   · 变调用 asetrate+atempo, 时长不变, 所以代理和视频严格 1:1, 不用做时间映射。
    #   · 渲染没好之前【继续放原声】, 好了再无缝切过去 —— 任何时候都不会没声音。
    #   · 渲染失败/关掉开关/换视频, 一律先把主音频解除静音再说。
    #   · 只读时间线和参数, 不写回任何东西, 导出结果完全不受影响。
    PREVIEW_FX_CACHE_KEEP = 20

    def _preview_fx_dir(self):
        d = os.path.join(user_cache_dir(), "_preview_audio")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return ""
        return d

    def _preview_main_clip(self):
        """当前任务的主音频 clip(只读)。没有就返回 None。"""
        if self.curr_idx < 0 or self.curr_idx >= len(self.tasks):
            return None
        try:
            for c in _audio_clips_from_track(self.tasks[self.curr_idx]):
                if c.get("is_main") or c.get("audio_role") == "main":
                    return c
        except Exception:
            pass
        return None

    def _preview_fx_chain(self):
        """返回要烘进代理音轨的滤镜串。空串 = 没有需要预渲染的效果。

        写法和顺序照抄导出那条链路(_mix_audio_tracks 里主音频那一段):
        变调 → 淡入 → 淡出 → 大厅堂混响。音量故意不在这里(见上方注释)。
        """
        c = self._preview_main_clip()
        if not c:
            return ""
        sr = 48000
        parts = []
        try:
            pitch = float(c.get("pitch_semitones") or 0.0)
        except Exception:
            pitch = 0.0
        if pitch:
            rate = pow(2.0, pitch / 12.0)
            parts.append(f"asetrate={sr}*{rate},atempo={1.0 / rate},"
                         f"aresample={sr}")
        try:
            fi = float(c.get("fade_in_dur") or 0.0)
        except Exception:
            fi = 0.0
        if fi > 0:
            parts.append(f"afade=t=in:st=0:d={fi}")
        try:
            fo = float(c.get("fade_out_dur") or 0.0)
            dur = float(self.media_player.duration() or 0) / 1000.0
        except Exception:
            fo, dur = 0.0, 0.0
        if fo > 0 and dur > 0:
            parts.append(f"afade=t=out:st={max(0.0, dur - fo)}:d={fo}")
        hall = ""
        try:
            fx = _SFX.load_fx_settings() if _SFX is not None else {}
            if _SPATIAL is not None:
                on, preset, wet, width = _SPATIAL.resolve_spatial_for_track(c, fx)
                if on and wet > 0:
                    _psrc = ""
                    try:
                        _psrc = self.tasks[self.curr_idx].get("video") or ""
                    except Exception:
                        _psrc = ""
                    hall = _SPATIAL.build_hall_reverb_filter(
                        preset, wet, width, uid=0, src_path=_psrc,
                        mic=_SPATIAL.mic_amount(c, fx))
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.ui.preview").exception(
                "preview fx hall filter failed")
            hall = ""
        if hall:
            parts.append(hall)
        return ",".join(p for p in parts if p)

    def _preview_fx_request(self, delay_ms=700):
        """参数动了 → 防抖之后重算。拖滑块时不会一路狂渲。"""
        try:
            if not getattr(self, "_preview_fx_on", True):
                return
            self._preview_fx_timer.start(int(delay_ms))
        except Exception:
            pass

    def _preview_fx_build(self):
        """Old first-clip proxy. Unused: transport plays timeline_wav / original."""
        return

    def _on_preview_fx_done(self, key, path):
        if key != getattr(self, "_preview_fx_key", ""):
            return                          # 渲的是上一套参数, 丢掉
        self._preview_fx_path = path
        self._preview_fx_prune()
        try:
            self.statusBar().showMessage("预览已套用成品音效（混响/变调/淡入淡出）", 3000)
        except Exception:
            pass
        self._sync_preview_fx()

    def _on_preview_fx_failed(self, key, msg):
        print("[PreviewFx] 渲染失败:", msg)
        if key == getattr(self, "_preview_fx_key", ""):
            self._preview_fx_path = ""
        self._preview_fx_unmute()

    def _preview_fx_prune(self):
        """缓存只留最近 N 个, 免得 user_data 越堆越大。"""
        try:
            d = self._preview_fx_dir()
            if not d:
                return
            fs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".m4a")]
            if len(fs) <= self.PREVIEW_FX_CACHE_KEEP:
                return
            fs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for p in fs[self.PREVIEW_FX_CACHE_KEEP:]:
                if p == getattr(self, "_preview_fx_path", ""):
                    continue
                try:
                    os.remove(p)
                except OSError:
                    pass
        except Exception:
            pass

    def _preview_fx_unmute(self):
        """代理不工作时恢复主音频；仍尊重轨道 mute / solo / JKL 反向。"""
        if hasattr(self, "_apply_preview_track_mute"):
            self._apply_preview_track_mute()
            return
        try:
            self.audio_output.setMuted(False)
        except Exception:
            pass

    def _preview_fx_reset(self):
        """停代理 + 解除静音 + 清指纹。换视频/关开关/没效果时用。"""
        try:
            p = getattr(self, "_preview_fx_player", None)
            if p is not None:
                p.pause()
        except Exception:
            pass
        self._preview_fx_key = ""
        self._preview_fx_path = ""
        self._preview_fx_last_pos = None
        self._preview_fx_last_ts = None
        self._preview_fx_unmute()

    def _preview_fx_ensure_player(self):
        if getattr(self, "_preview_fx_player", None) is not None:
            return self._preview_fx_player
        try:
            p = QMediaPlayer()
            o = QAudioOutput()
            p.setAudioOutput(o)
            self._preview_fx_player = p
            self._preview_fx_out = o
        except Exception:
            self._preview_fx_player = None
            self._preview_fx_out = None
        return self._preview_fx_player

    def _preview_fx_apply_volume(self):
        """代理播放器的音量 = 主输出的音量(音量没烘进渲染)。"""
        try:
            o = getattr(self, "_preview_fx_out", None)
            if o is not None:
                o.setVolume(max(0.0, min(1.0, self.audio_output.volume())))
        except Exception:
            pass

    def _sync_preview_fx(self, *_args):
        """Heartbeat: mixed preview wav when ready, otherwise original source.

        Never falls back to the first-clip _preview_fx_chain proxy (that path
        only hears clip 0 — Luca's "第二段没有立体声").
        """
        try:
            from cutora.core.log import get_logger
            from cutora.preview.engine import preview_audio_mode
            _plog = get_logger("cutora.ui.preview")
            mode = preview_audio_mode(bool(getattr(self, "_proxy_ready", False)))
            self._preview_fx_unmute()
            p = getattr(self, "_preview_fx_player", None)
            if p is not None and p.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                p.pause()
            if getattr(self, "_preview_audio_src_logged", "") == mode:
                return
            self._preview_audio_src_logged = mode
            if mode == "timeline_wav":
                _plog.info("preview audio source=timeline_wav path=%s",
                           getattr(self, "_proxy_wav", ""))
            else:
                _plog.info("preview audio source=preparing (original, not first-clip fx)")
                try:
                    self.statusBar().showMessage("预览音频准备中", 2500)
                except Exception:
                    pass
        except Exception:
            self._preview_fx_unmute()

    def _rebuild_preview_wav(self, immediate=False):
        self._schedule_stem_rebuild(immediate=immediate)
        if not immediate:
            self._proxy_timer.start()
            try:
                self.statusBar().showMessage("音频代理更新中…", 2500)
            except Exception:
                pass
            self._proxy_ready = False
            self._using_media_clock = False
            clk = getattr(self, "_preview_clock", None)
            if clk is not None:
                clk.use_media = False
            return
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return
        task = self.tasks[self.curr_idx]
        if task.get("guard_error"):
            return
        solo_ids = getattr(getattr(self, "timeline", None), "_solo_ids", None) or None
        preview_sfx = bool(getattr(self, "_sfx_preview_on", True))
        from cutora.asr.timeline_wav import render_preview_wav
        from cutora.preview.engine import preview_wav_path
        dest = preview_wav_path(task, solo_ids=solo_ids, preview_sfx=preview_sfx)
        if os.path.isfile(dest) and os.path.getsize(dest) >= 44:
            self._apply_proxy_wav(dest)
            return
        self._proxy_ready = False
        self._using_media_clock = False
        clk = getattr(self, "_preview_clock", None)
        if clk is not None:
            clk.use_media = False
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        class _Sig(QObject):
            done = Signal(str)
            fail = Signal(str)

        class _Job(QRunnable):
            def __init__(self, snap, path, sig, solo, sfx_on):
                super().__init__()
                self._snap, self._path, self._sig = snap, path, sig
                self._solo, self._sfx_on = solo, sfx_on
                self.setAutoDelete(True)

            def run(self):
                sig_ref = self._sig
                try:
                    render_preview_wav(
                        self._snap, self._path,
                        solo_ids=self._solo, preview_sfx=self._sfx_on)
                    if sig_ref is not None:
                        sig_ref.done.emit(self._path)
                except RuntimeError:
                    pass
                except Exception as exc:
                    try:
                        if sig_ref is not None:
                            sig_ref.fail.emit(str(exc))
                    except RuntimeError:
                        pass
                finally:
                    self._sig = None

        sig = _Sig()
        self._proxy_sig = sig
        self._keep_bg_sig(sig)

        def _ok(path, _s=sig):
            self._drop_bg_sig(_s)
            self._apply_proxy_wav(path)

        def _fail(msg, _s=sig):
            self._drop_bg_sig(_s)
            from cutora.core.log import get_logger
            get_logger("cutora.ui.preview").error("preview wav failed: %s", msg)
            try:
                self.statusBar().showMessage("音频代理失败，画面继续（墙钟）: %s" % msg, 6000)
            except Exception:
                pass
            self._proxy_wav = ""
            self._proxy_ready = False
            self._using_media_clock = False
            if clk is not None:
                clk.use_media = False

        sig.done.connect(_ok)
        sig.fail.connect(_fail)
        QThreadPool.globalInstance().start(
            _Job(task, dest, sig, solo_ids, preview_sfx))

    def _apply_proxy_wav(self, dest):
        self._proxy_wav = dest
        if dest and os.path.isfile(dest):
            pos = int(getattr(self, "_timeline_ms", 0) or 0)
            playing = bool(self._timeline_playing)
            self.media_player.setSource(QUrl.fromLocalFile(dest))
            self.media_player.setPosition(pos)
            self._proxy_ready = True
            self._using_media_clock = True
            clk = getattr(self, "_preview_clock", None)
            if clk is not None:
                clk.use_media = True
                clk.ms = pos
            try:
                self.bgm_player.pause()
            except Exception:
                pass
            try:
                self._sfx_stop_all()
            except Exception:
                pass
            try:
                if hasattr(self, "intro_player"):
                    self.intro_player.pause()
            except Exception:
                pass
            if playing:
                self.media_player.play()
            else:
                self.media_player.pause()
            self._sync_stem_sink()

    def _preview_interaction_scale(self) -> float:
        """视频层合成倍率 = min(拖动 / 擦洗时的半分辨率, 画布实际显示像素)。"""
        drag = 1.0
        canvas = getattr(self, "video_canvas", None)
        if canvas is not None and (
            getattr(canvas, "_dragging_item", None) is not None
            or getattr(canvas, "_split_drag", None) is not None
        ):
            drag = 0.5
        tl = getattr(self, "timeline", None)
        if tl is not None and bool(getattr(tl, "_scrubbing", False) or getattr(tl, "_dragging", False)):
            drag = 0.5
        if not bool(getattr(self, "_timeline_playing", False)):
            return drag
        return min(drag, self._preview_display_scale())

    def _preview_display_scale(self) -> float:
        """Q145 第二期：**播放时**视频层只按屏幕上真正显示的像素合成（专业剪辑软件
        「播放分辨率 / 暂停分辨率」分开设的思路）。

        1080×1920 的工程放在 540 px 宽的画布里，播放时直接合成 540 宽（HiDPI 按
        设备像素算），几何不变；多轨叠加时每帧省三成多。**暂停时仍按满分辨率**，
        停下来看到的画面和以前一模一样（画布发现手上是小图会自动补一张满分辨率）。
        放大查看、玻璃背景（要满分辨率取样）、画布不可见时一律满分辨率。导出永远不走这里。"""
        canvas = getattr(self, "video_canvas", None)
        if canvas is None or not (0 <= self.curr_idx < len(self.tasks or [])):
            return 1.0
        try:
            if not canvas.isVisible() or canvas.width() <= 1 or canvas.height() <= 1:
                return 1.0
            from cutora.model.canvas import canvas_wh
            task = self.tasks[self.curr_idx]
            w, h = canvas_wh(task)
            if w <= 0 or h <= 0:
                return 1.0
            glass = getattr(canvas, "_task_needs_glass_backdrop", None)
            if callable(glass) and glass(task) and not bool(getattr(self, "_timeline_playing", False)):
                return 1.0
            dpr = float(canvas.devicePixelRatioF() or 1.0)
            zoom = float(getattr(canvas, "_view_zoom", 1.0) or 1.0)
            fit = min(canvas.width() / float(w), canvas.height() / float(h)) * dpr * max(1.0, zoom)
        except Exception:
            return 1.0
        from cutora.preview.engine import display_scale_step
        sc = display_scale_step(fit)
        if bool(getattr(self, "_timeline_playing", False)):
            return min(sc, 0.5)
        return sc

    def _playback_full_frames(self) -> bool:
        """Q175：播放中、没在拖画布上的东西、没在画布上打字 → 整幅画面交给工作线程合成。"""
        if not bool(getattr(self, "_timeline_playing", False)):
            return False
        canvas = getattr(self, "video_canvas", None)
        if canvas is None:
            return False
        if getattr(canvas, "_dragging_item", None) is not None or getattr(canvas, "_wysiwyg", None) is not None:
            return False
        return True

    def _on_preview_worker_frame(self, img, t, gen):
        if img is None:
            return
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return
        setter = getattr(self.video_canvas, "set_program_frame", None)
        if callable(setter):
            setter(img)
        try:
            self.video_canvas.set_data(self.tasks[self.curr_idx], float(t))
        except Exception:
            pass

    def _idle_preview_hold(self) -> bool:
        """Paused, same playhead/task, and the canvas has left playback-full mode.

        Q189g：200 ms 心跳以前每拍都 `_present_preview_frame`。工程开着人不动时
        8 分钟仍要 2304 帧（约 5 Hz），主线程 + PreviewWorker 合起来 0.4 核。
        停下来那一拍还要一张普通帧（清掉 `_playback_full`，画布回到可编辑）。
        """
        if bool(getattr(self, "_timeline_playing", False)):
            return False
        from cutora.ui.idle_wake import hold_expired
        if hold_expired(self):                 # Q189h：兜底，停帧最多几秒就刷一张
            return False
        canvas = getattr(self, "video_canvas", None)
        if canvas is not None and bool(getattr(canvas, "_playback_full", False)):
            return False
        if int(getattr(self, "_timeline_ms", 0) or 0) != int(getattr(self, "_sync_idle_ms", -1)):
            return False
        if int(getattr(self, "curr_idx", -1)) != int(getattr(self, "_sync_idle_idx", -2)):
            return False
        task = None
        idx = int(getattr(self, "curr_idx", -1))
        if 0 <= idx < len(self.tasks or []):
            task = self.tasks[idx]
        return task is getattr(self, "_sync_idle_task", None)

    def _note_idle_preview_sync(self) -> None:
        self._sync_idle_ts = time.monotonic()
        self._sync_idle_ms = int(getattr(self, "_timeline_ms", 0) or 0)
        self._sync_idle_idx = int(getattr(self, "curr_idx", -1))
        idx = self._sync_idle_idx
        self._sync_idle_task = (
            self.tasks[idx] if 0 <= idx < len(self.tasks or []) else None
        )

    def _present_preview_frame(self):
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return
        task = self.tasks[self.curr_idx]
        t = float(getattr(self, "_timeline_ms", 0) or 0) / 1000.0
        worker = getattr(self, "_preview_worker", None)
        if worker is not None and worker.isRunning():
            full = self._playback_full_frames()
            try:
                if full:
                    # 停下来以后这个标记由画布收到第一张普通帧时自己清掉（中间那几十毫秒还贴最后一张整幅帧，
                    # 不会闪回播放前的旧画面）
                    self.video_canvas._playback_full = True
                else:
                    self.video_canvas.set_data(task, t)
            except Exception:
                pass
            if full:
                # Q175：播放中整幅画面在工作线程合成；按它最近的用时往前多要一点，送到时正好对上声音
                lead = min(0.25, max(0.0, float(getattr(worker, "cost", 0.0) or 0.0)))
                try:
                    from cutora.ui.canvas import _preview_solo_ids
                    worker.solo_ids = _preview_solo_ids(self.video_canvas)
                except Exception:
                    worker.solo_ids = None
                worker.request(task, t + lead, scale=self._preview_display_scale(), full=True)
            else:
                worker.request(task, t, scale=self._preview_interaction_scale())
            self._note_idle_preview_sync()
            return
        eng = getattr(self, "_preview", None)
        if eng is None:
            return
        try:
            from cutora.render.visual_stack import build_stack, needs_stack
            if needs_stack(task, stack=build_stack(task)):
                img = eng.video_bands_at(task, t, scale=self._preview_interaction_scale())   # Q163
            else:
                img = eng.video_frame_at(task, t, scale=self._preview_interaction_scale())
            setter = getattr(self.video_canvas, "set_program_frame", None)
            if callable(setter):
                setter(img)
            self.video_canvas.set_data(task, t)
        except Exception:
            import time as _t
            now = _t.time()
            if now - float(getattr(self, "_present_err_ts", 0) or 0) > 2.0:
                self._present_err_ts = now
                from cutora.core.log import get_logger
                get_logger("cutora.ui.preview").error(
                    "present preview frame failed", exc_info=True)
            self.video_canvas.set_data(task, t)
        self._note_idle_preview_sync()

    def _timeline_end_ms(self) -> int:
        if not (0 <= self.curr_idx < len(self.tasks or [])):
            return 0
        from cutora.preview.engine import playback_end_sec
        return int(playback_end_sec(self.tasks[self.curr_idx]) * 1000)

    def _stems_are_ready(self):
        return _host_stems_ready(self)

    def _clock_only_mix(self):
        return bool(getattr(self, "_proxy_ready", False)) or _host_stems_ready(self)

    def _stem_clock_ms(self):
        if getattr(self, "_batch_audition", False):
            t0 = getattr(self, "_audition_wall0", None)
            origin = float(getattr(self, "_audition_origin_ms", 0) or 0)
            if t0 is None:
                return origin
            return origin + (time.time() - float(t0)) * 1000.0
        clk = getattr(self, "_preview_clock", None)
        if clk is not None and getattr(clk, "playing", False):
            return float(clk.ms)
        if getattr(self, "_using_media_clock", False):
            try:
                return float(self.media_player.position())
            except Exception:
                pass
        return float(getattr(self, "_timeline_ms", 0) or 0)

    def _stop_stem_sink(self):
        io = getattr(self, "_stem_pull_io", None)
        if io is not None and hasattr(io, "reset_cursor"):
            io.reset_cursor()
        sink = getattr(self, "_stem_sink", None)
        if sink is not None and getattr(self, "_stem_sink_running", False):
            try:
                sink.stop()
            except Exception:
                pass
        self._stem_sink_running = False

    def _sync_stem_sink(self):
        """Architecture A: sink runs only while playing/audition and stems are ready."""
        wanted = (
            (bool(getattr(self, "_timeline_playing", False))
             or bool(getattr(self, "_batch_audition", False)))
            and _host_stems_ready(self)
            and int(getattr(self, "_jkl_rate", 0) or 0) >= 0
        )
        sink = getattr(self, "_stem_sink", None)
        if not wanted:
            was = bool(getattr(self, "_stem_sink_running", False))
            self._stop_stem_sink()
            if was:
                apply = getattr(self, "_apply_preview_track_mute", None)
                if callable(apply):
                    apply()
            return
        if sink is None:
            self._stem_sink_running = False
            return
        if not getattr(self, "_stem_sink_running", False):
            try:
                io = getattr(self, "_stem_pull_io", None)
                if io is not None:
                    if hasattr(io, "reset_cursor"):
                        io.reset_cursor()
                    sink.start(io)
                else:
                    sink.start()
                self._stem_sink_running = True
            except Exception:
                self._stem_sink_running = False
                from cutora.core.log import get_logger
                get_logger("cutora.audio").exception("[Audio] sink.start failed")
                return
        try:
            self.audio_output.setMuted(True)
        except Exception:
            pass

    def _schedule_stem_rebuild(self, immediate=False):
        timer = getattr(self, "_stem_rebuild_timer", None)
        if not immediate:
            if timer is not None:
                timer.start()
            return
        self._rebuild_stems_now()

    def _can_rebuild_stems(self) -> bool:
        if not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return False
        return getattr(self, "_stem_mixer", None) is not None

    def _rebuild_stems_now(self):
        if not self._can_rebuild_stems():
            return
        from cutora.audio.stem_cache import StemRebuildGate
        gate = getattr(self, "_stem_gate", None)
        if gate is None:
            gate = self._stem_gate = StemRebuildGate()
        if not gate.begin():
            from cutora.core.log import get_logger
            get_logger("cutora.audio").info("[Audio] stem rebuild coalesced")
            return
        self._launch_stem_job(gate)

    def _launch_stem_job(self, gate):
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is None or not self._can_rebuild_stems():
            if gate.finish():
                self._launch_stem_job(gate)
            return
        task = self.tasks[self.curr_idx]
        solo_ids = getattr(getattr(self, "timeline", None), "_solo_ids", None) or None
        preview_sfx = bool(getattr(self, "_sfx_preview_on", True))
        mixer.preview_sfx = preview_sfx
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

        class _Sig(QObject):
            done = Signal()

        class _Job(QRunnable):
            def __init__(self, mix, snap, solo, sfx_on, key, sig):
                super().__init__()
                self._mix, self._snap = mix, snap
                self._solo, self._sfx_on = solo, sfx_on
                self._key, self._sig = key, sig
                self.setAutoDelete(True)

            def run(self):
                sig_ref = self._sig
                try:
                    self._mix.build(
                        self._snap, solo_ids=self._solo,
                        preview_sfx=self._sfx_on, task_key=self._key)
                except Exception:
                    from cutora.core.log import get_logger
                    get_logger("cutora.audio").exception("[Audio] stem rebuild failed")
                finally:
                    self._sig = None
                try:
                    if sig_ref is not None:
                        sig_ref.done.emit()
                except RuntimeError:
                    pass

        sig = _Sig()
        self._stem_sig = sig
        self._keep_bg_sig(sig)

        def _ok(_s=sig):
            self._drop_bg_sig(_s)
            try:
                self._on_stems_ready()
            finally:
                if gate.finish():
                    self._launch_stem_job(gate)

        sig.done.connect(_ok)
        try:
            QThreadPool.globalInstance().start(
                _Job(mixer, task, solo_ids, preview_sfx, id(task), sig))
        except Exception:
            self._drop_bg_sig(sig)
            from cutora.core.log import get_logger
            get_logger("cutora.audio").exception("[Audio] stem job start failed")
            if gate.finish():
                self._launch_stem_job(gate)

    def _on_stems_ready(self):
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is not None:
            c_baked = getattr(getattr(mixer, "cache", None), "baked_gain", None) or {}
            for role, sl in (getattr(self, "_role_fader_sliders", None) or {}).items():
                if role not in c_baked:
                    mixer.baked_gain[role] = max(0.0, sl.value() / 100.0)
                if sl.isEnabled():
                    mixer.set_live_linear(role, max(0.0, sl.value() / 100.0))
                else:
                    mixer.live_gain[role] = 1.0
            mixer.preview_sfx = bool(getattr(self, "_sfx_preview_on", True))
        apply = getattr(self, "_apply_preview_track_mute", None)
        if callable(apply):
            apply()
        self._sync_stem_sink()

    def _fader_linear(self, role):
        sl = (getattr(self, "_role_fader_sliders", None) or {}).get(role)
        if sl is None:
            return 1.0
        return max(0.0, sl.value() / 100.0)

    def _on_role_fader_moved(self, role, value):
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is not None:
            mixer.set_live_linear(role, max(0.0, int(value) / 100.0))
        linear = max(0.0, int(value) / 100.0)
        spin = {
            "main": getattr(self, "spin_src_vol", None),
            "bgm": getattr(self, "spin_bgm_vol", None),
            "sfx": getattr(self, "spin_sfx_vol", None),
        }.get(role)
        if spin is not None:
            spin.blockSignals(True)
            try:
                spin.setValue(linear)
            except Exception:
                pass
            spin.blockSignals(False)
        sync = getattr(self, "_sync_inspector_gain", None)
        if callable(sync):
            sync(role, linear)

    def _on_role_fader_released(self):
        timer = getattr(self, "_fader_commit_timer", None)
        if timer is not None:
            timer.start()

    def _commit_role_faders(self):
        bus = getattr(self, "bus", None)
        if bus is None or not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return
        sliders = getattr(self, "_role_fader_sliders", None) or {}
        def _role_val(role, spin_attr, default_v):
            sl = sliders.get(role)
            if sl is not None and sl.isEnabled():
                return sl.value() / 100.0
            sp = getattr(self, spin_attr, None)
            if sp is not None:
                try:
                    return float(sp.value())
                except Exception:
                    pass
            if sl is not None:
                return sl.value() / 100.0
            return default_v
        params = {
            "src": _role_val("main", "spin_src_vol", 1.0),
            "bgm": _role_val("bgm", "spin_bgm_vol", 0.22),
            "sfx": _role_val("sfx", "spin_sfx_vol", 0.18),
            "scope": "current",
        }
        try:
            bus.execute("audio.volume_master", params, source="ui")
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.audio").exception("[Audio] role fader commit failed")

    def _sync_role_faders_from_task(self):
        if not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return
        task = self.tasks[self.curr_idx]
        bar = getattr(self, "mixer_bar", None)
        if bar is not None:
            bar.set_from_task(task)
            self._role_fader_sliders = bar.sliders
            return
        sliders = getattr(self, "_role_fader_sliders", None) or {}
        if not sliders:
            return
        from cutora.audio.sfx_library import clip_audio_role, gain_db_to_linear
        found = {r: [] for r in ("main", "bgm", "sfx")}
        tl = _tl_of(task)
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "audio":
                continue
            for c in getattr(tr, "clips", []) or []:
                role = clip_audio_role(c)
                if role in found:
                    found[role].append(gain_db_to_linear(getattr(c, "gain_db", 0.0)))
        spin_fallback = {
            "main": getattr(self, "spin_src_vol", None),
            "bgm": getattr(self, "spin_bgm_vol", None),
            "sfx": getattr(self, "spin_sfx_vol", None),
        }
        for role, sl in sliders.items():
            vals = found.get(role) or []
            sl.setEnabled(bool(vals))
            if vals:
                linear = float(vals[0])
            else:
                sp = spin_fallback.get(role)
                linear = float(sp.value()) if sp is not None else (1.0 if role == "main" else 0.22)
            sl.blockSignals(True)
            sl.setValue(int(round(max(0.0, min(2.0, linear)) * 100.0)))
            sl.blockSignals(False)

    def _sync_inspector_gain(self, role, linear):
        panel = getattr(self, "prop_panel", None)
        if panel is None or getattr(panel, "current_type", None) != "audio":
            return
        clip = getattr(panel, "current_data", None)
        if clip is None:
            return
        from cutora.audio.sfx_library import clip_audio_role
        if clip_audio_role(clip) != role:
            return
        fn = getattr(panel, "sync_audio_volume_linear", None)
        if callable(fn):
            fn(linear)

    def _role_track_id(self, role):
        if not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return ""
        tl = _tl_of(self.tasks[self.curr_idx])
        from cutora.audio.sfx_library import clip_audio_role
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            if str(getattr(tr, "audio_role", "") or "") == role:
                return str(getattr(tr, "id", "") or "")
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            clips = getattr(tr, "clips", None) or []
            if clips and {clip_audio_role(c) for c in clips} == {role}:
                return str(getattr(tr, "id", "") or "")
        return ""

    def _on_mixer_mute(self, role, on):
        mixer = getattr(self, "_stem_mixer", None)
        if mixer is not None:
            mixer.mute[role] = bool(on)
        tid = self._role_track_id(role)
        bus = getattr(self, "bus", None)
        if tid and bus is not None:
            try:
                bus.execute(
                    "timeline.set_track_mute",
                    {"track_id": tid, "on": bool(on)},
                    source="ui",
                )
            except Exception:
                from cutora.core.log import get_logger
                get_logger("cutora.audio").exception("[Audio] mixer mute failed")

    def _on_mixer_spatial(self, on):
        if not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return
        from cutora.audio.sfx_library import clip_audio_role
        tl = _tl_of(self.tasks[self.curr_idx])
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
                continue
            for c in getattr(tr, "clips", []) or []:
                if clip_audio_role(c) != "main":
                    continue
                try:
                    c.spatial_enabled = bool(on)
                except Exception:
                    pass
        self._schedule_stem_rebuild(immediate=False)
        rebuild = getattr(self, "_rebuild_preview_wav", None)
        if callable(rebuild):
            rebuild(immediate=False)

    def _on_mixer_preset(self, key):
        bus = getattr(self, "bus", None)
        if bus is None:
            return
        try:
            bus.execute("audio.spatial.set", {"preset": str(key)}, source="ui")
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.audio").exception("[Audio] mixer preset failed")

    def _jump_to_batch_audio(self):
        host = getattr(self, "room_host", None)
        if host is None:
            return
        try:
            host.enter("batch")
        except Exception:
            return
        dlg = host.widget("batch") if hasattr(host, "widget") else None
        tab = getattr(dlg, "audio_tab", None) if dlg is not None else None
        tabs = getattr(dlg, "tabs", None) if dlg is not None else None
        if tab is not None and tabs is not None:
            tabs.setCurrentWidget(tab)

    def start_batch_audition(self):
        """Wall-clock sink only. Editor media_player stays paused; no preview engine."""
        if not (0 <= getattr(self, "curr_idx", -1) < len(getattr(self, "tasks", None) or [])):
            return False
        self._batch_audition = True
        self._audition_origin_ms = float(getattr(self, "_timeline_ms", 0) or 0)
        self._audition_wall0 = time.time()
        if not _host_stems_ready(self):
            rebuild = getattr(self, "_rebuild_stems_now", None)
            if callable(rebuild):
                rebuild()
        self._sync_stem_sink()
        return True

    def stop_batch_audition(self):
        self._batch_audition = False
        self._audition_wall0 = None
        if not bool(getattr(self, "_timeline_playing", False)):
            self._stop_stem_sink()
        else:
            self._sync_stem_sink()

    def toggle_play(self):
        clk = getattr(self, "_preview_clock", None)
        now = time.time()
        if clk is None:
            clk = None
        else:
            clk.end_ms = self._timeline_end_ms()
            clk.ms = int(self._timeline_ms or 0)
        if self._timeline_playing:
            self.media_player.pause()
            if hasattr(self, 'intro_player'):
                self.intro_player.pause()
            self._timeline_playing = False
            self._timeline_last_ts = None
            if clk is not None:
                clk.playing = False
                clk.last_ts = None
            self.timer.setInterval(200)
            self.timer.setTimerType(Qt.TimerType.CoarseTimer)
            self._sync_stem_sink()
        else:
            self._ensure_audio_device()
            end_ms = self._timeline_end_ms()
            if end_ms > 0 and int(self._timeline_ms or 0) >= end_ms - 30:
                self._timeline_ms = 0
                self.media_player.setPosition(0)
                if clk is not None:
                    clk.ms = 0
            self._timeline_playing = True
            self._timeline_last_ts = now
            if clk is not None:
                clk.playing = True
                clk.last_ts = now
                clk.end_ms = end_ms
            if self._proxy_ready:
                self._using_media_clock = True
                if clk is not None:
                    clk.use_media = True
                if abs(self.media_player.position() - int(self._timeline_ms or 0)) > 80:
                    self.media_player.setPosition(int(self._timeline_ms or 0))
                self.media_player.play()
            else:
                self._using_media_clock = False
                if clk is not None:
                    clk.use_media = False
            self.timer.setInterval(12)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._sync_stem_sink()
        self._refresh_transport_play_icon()
        self._refresh_fps_meter()

    def save_text(self):
        if self.curr_idx >= 0: self.tasks[self.curr_idx]['text'] = self.text_input.toPlainText()

    def handle_batch_grid_paste(self, start_grid_idx, lines):
        selected_indices = self._get_selected_indices()
        if not selected_indices: return
        total_grids = len(selected_indices)
        available_slots = total_grids - start_grid_idx
        if len(lines) > available_slots:
            QMessageBox.warning(self, "批量粘贴", f"剪贴板有 {len(lines)} 行数据，但从当前格子算起只剩 {available_slots} 个可用空位。\n多余的数据将被自动舍弃。")
        for i, line in enumerate(lines[:available_slots]):
            grid_ui_idx = start_grid_idx + i
            if grid_ui_idx < len(self._grid_widgets_pool):
                wrap = self._grid_widgets_pool[grid_ui_idx]
                tedit = wrap.findChild(MultiGridTextEdit)
                if tedit:
                    tedit.setPlainText(line)
                    
    def _sync_multi_grid(self, indices):
        # 动态池扩容：懒汉式复用
        while len(self._grid_widgets_pool) < len(indices):
            idx = len(self._grid_widgets_pool)
            wrap = QWidget()
            lay = QVBoxLayout(wrap)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lbl = QLabel()
            lbl.setObjectName("gridCaption")
            lay.addWidget(lbl)
            tedit = MultiGridTextEdit(self)
            tedit.grid_idx = idx
            tedit.textChanged.connect(lambda t=tedit: self._on_grid_text_changed(t))
            lay.addWidget(tedit)
            self.multi_text_layout.insertWidget(self.multi_text_layout.count() - 1, wrap)
            self._grid_widgets_pool.append(wrap)
            
        # 设置数据和可见性
        for i, wrap in enumerate(self._grid_widgets_pool):
            if i < len(indices):
                task_idx = indices[i]
                task = self.tasks[task_idx]
                lbl = wrap.findChild(QLabel)
                tedit = wrap.findChild(MultiGridTextEdit)
                lbl.setText(f"🎬 {os.path.basename(task.get('video', '未命名'))}")
                tedit.blockSignals(True)
                tedit.task_idx = task_idx
                tedit.setPlainText(task.get('text', ''))
                tedit.blockSignals(False)
                wrap.show()
            else:
                wrap.hide()

    def _on_grid_text_changed(self, text_edit):
        task_idx = text_edit.task_idx
        if 0 <= task_idx < len(self.tasks):
            new_text = text_edit.toPlainText()
            self.tasks[task_idx]['text'] = new_text
            self._changes_made = True
            if task_idx == self.curr_idx:
                self.update_preview()
