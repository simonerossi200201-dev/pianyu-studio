# SPDX-License-Identifier: GPL-3.0-or-later
"""音效库：目录、设置读写、按间隔/随机往时间线铺音效。

不依赖主窗口，避免循环导入。时间线操作只碰 audio 轨上
audio_role=='sfx' 的 clip，主音频和配乐原样保留。
"""
from __future__ import annotations

import json
import math
import os
import random
import uuid

from cutora.core import safe_json
from cutora.core.log import get_logger
from cutora.core.paths import AUDIO_SFX
from cutora.core.secrets import strip_secret_fields
from cutora.model.timeline_model import MediaSource, Clip, Track, probe_duration_sec

_log = get_logger("cutora.audio")

ROLE_TRACK_NAME = {"main": "原声", "bgm": "配乐", "sfx": "音效"}
NEW_BGM_LINEAR_VOL = 0.22
NEW_BGM_FADE_SEC = 0.30
_AUDIO_TYPES = frozenset(("audio", "bgm", "sfx"))
_AUDIO_ROLES = frozenset(("main", "bgm", "sfx"))

SFX_DIR = AUDIO_SFX
SETTINGS_KEY = "audio_fx"

# 默认给大型演讲/祷告现场用的三类；自然环境默认关闭，用户自行勾选。
SFX_CATALOG = [
    {
        "id": "crowd_applause",
        "label": "群众鼓掌",
        "group": "大型现场",
        "placement": "interval",
        "default_enabled": True,
        "files": ["crowd_applause_short.wav", "crowd_applause_long.wav", "crowd_applause_soft.wav"],
    },
    {
        "id": "crowd_amen",
        "label": "群众 Amen / 应和",
        "group": "大型现场",
        "placement": "interval",
        "default_enabled": True,
        "files": ["crowd_amen_soft.wav", "crowd_amen_strong.wav", "crowd_yes.wav"],
    },
    {
        "id": "venue_ambience",
        "label": "大厅现场同期声",
        "group": "大型现场",
        "placement": "bed",
        "default_enabled": True,
        "files": ["venue_ambience.wav", "crowd_murmur.wav"],
    },
    {
        "id": "birds",
        "label": "户外鸟叫",
        "group": "自然环境",
        "placement": "interval",
        "default_enabled": False,
        "files": ["birds_forest.wav"],
    },
    {
        "id": "ocean",
        "label": "海边海浪",
        "group": "自然环境",
        "placement": "bed",
        "default_enabled": False,
        "files": ["ocean_waves.wav"],
    },
    {
        "id": "river",
        "label": "河流流淌",
        "group": "自然环境",
        "placement": "bed",
        "default_enabled": False,
        "files": ["river_flow.wav"],
    },
    {
        "id": "rain",
        "label": "自然环境下雨",
        "group": "自然环境",
        "placement": "bed",
        "default_enabled": False,
        "files": ["rain_light.wav", "rain_heavy.wav"],
    },
    {
        "id": "wind",
        "label": "轻风",
        "group": "自然环境",
        "placement": "bed",
        "default_enabled": False,
        "files": ["wind_soft.wav"],
    },
]

SCENE_TO_SFX = {
    "church_sermon": ["venue_ambience", "crowd_applause", "crowd_amen"],
    "conference": ["venue_ambience", "crowd_applause"],
    "outdoor": ["birds", "wind"],
    "forest": ["birds", "wind"],
    "beach": ["ocean"],
    "river": ["river"],
    "rain": ["rain", "wind"],
    "other": ["venue_ambience"],
}

def default_fx_settings():
    enabled = {c["id"]: bool(c["default_enabled"]) for c in SFX_CATALOG}
    return {
        "spatial_enabled": True,
        # 0830: 空间立体声整个换成卷积混响(见 spatial_audio.py 顶部)。默认预设
        # 改成 stage —— 近距离话筒 + 大厅堂, 就是"话筒在舞台上"那个听感。
        # 湿度/宽度这两个数是实测标定的(空档混响 ≈ 语音下方 10 dB), 别随手改。
        # 已经保存过设置的人不受影响(读的是自己 json 里那份)。
        "spatial_preset": "stage",
        # 0830 二次标定: 0.34 实听"太强、说话不清晰", 收到 0.26。
        "spatial_wet": 0.26,
        "spatial_width": 0.62,
        # 话筒质感: 近讲效应 + 极轻的喷麦 + 电容麦空气感。默认关, 想要再开。
        "mic_texture": False,
        "mic_amount": 0.5,
        "sfx_volume": 0.18,
        "sfx_interval_sec": 30.0,
        "sfx_duration_sec": 5.0,
        "sfx_jitter_sec": 8.0,
        "sfx_start_offset_sec": 8.0,
        "sfx_auto_apply": True,
        "sfx_enabled": enabled,
        "ai_enabled": False,
        "ai_provider": "gemini",  # gemini | ollama | spacexai | openai | anthropic
        "gemini_model": "gemini-2.0-flash",
        "ollama_model": "llava",
        "xai_model": "grok-2-vision-1212",
        "openai_model": "gpt-4o-mini",
        "anthropic_model": "claude-sonnet-4-5",
    }

def _settings_path():
    from cutora.core.paths import settings_file
    return settings_file("app_settings.json")

def load_fx_settings():
    cfg = default_fx_settings()
    try:
        p = _settings_path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = safe_json.load(f)
            saved = data.get(SETTINGS_KEY) if isinstance(data, dict) else None
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in cfg})
                en = saved.get("sfx_enabled")
                if isinstance(en, dict):
                    cfg["sfx_enabled"] = {c["id"]: bool(en.get(c["id"], c["default_enabled"]))
                                          for c in SFX_CATALOG}
    except Exception:
        pass
    return cfg

def save_fx_settings(settings):
    try:
        p = _settings_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {}
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = safe_json.load(f) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        data = strip_secret_fields(data)
        data[SETTINGS_KEY] = strip_secret_fields(dict(settings or {}))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def catalog_by_id():
    return {c["id"]: c for c in SFX_CATALOG}

def resolve_sfx_path(filename):
    if not filename:
        return None
    p = filename if os.path.isabs(filename) else os.path.join(SFX_DIR, filename)
    return p if os.path.isfile(p) else None

def list_available_files(cat):
    files = []
    for name in cat.get("files") or []:
        p = resolve_sfx_path(name)
        if p:
            files.append(p)
    return files

def missing_sfx_files():
    missing = []
    for cat in SFX_CATALOG:
        if not list_available_files(cat):
            missing.append(cat["id"])
    return missing

def enabled_categories(settings):
    en = (settings or {}).get("sfx_enabled") or {}
    out = []
    for cat in SFX_CATALOG:
        if en.get(cat["id"], cat["default_enabled"]) and list_available_files(cat):
            out.append(cat)
    return out

def linear_to_gain_db(vol):
    vol = float(vol)
    if vol <= 0:
        return -120.0
    return 20.0 * math.log10(max(vol, 1e-6))

def gain_db_to_linear(gain_db):
    try:
        g = float(gain_db)
    except (TypeError, ValueError):
        return 1.0
    if abs(g) < 1e-9:
        return 1.0
    if g <= -120.0:
        return 0.0
    return 10.0 ** (g / 20.0)

def _clip_get(clip, key, default=None):
    if isinstance(clip, dict):
        return clip.get(key, default)
    return getattr(clip, key, default)


def _clip_set(clip, key, value):
    if isinstance(clip, dict):
        clip[key] = value
    else:
        setattr(clip, key, value)


def clip_audio_role(clip):
    role = _clip_get(clip, "audio_role", None)
    if role:
        return str(role)
    if _clip_get(clip, "is_main", False):
        return "main"
    return "bgm"

def is_bgm_clip(clip):
    return clip_audio_role(clip) == "bgm"

def is_sfx_clip(clip):
    return clip_audio_role(clip) == "sfx"

def is_main_clip(clip):
    if _clip_get(clip, "is_main", False):
        return True
    return clip_audio_role(clip) == "main"

def apply_new_bgm_defaults(clip):
    """New BGM only: linear 0.22 + 0.30 s fades. Do not rewrite existing projects."""
    vol = float(NEW_BGM_LINEAR_VOL)
    gain = 20.0 * math.log10(max(vol, 1e-6)) if vol > 0 else -120.0
    fade = float(NEW_BGM_FADE_SEC)
    if isinstance(clip, dict):
        clip["gain_db"] = gain
        clip["fade_in_dur"] = fade
        clip["fade_out_dur"] = fade
        clip["audio_role"] = "bgm"
        clip["spatial_enabled"] = False
        clip["loop"] = True
        clip["is_main"] = False
        clip["match_duration"] = True
        clip["seam_xfade"] = True
        return clip
    if clip is None:
        return clip
    clip.gain_db = gain
    clip.fade_in_dur = fade
    clip.fade_out_dur = fade
    clip.audio_role = "bgm"
    clip.spatial_enabled = False
    clip.loop = True
    clip.is_main = False
    clip.match_duration = True
    clip.seam_xfade = True
    return clip


def apply_default_spatial_to_clip(clip, settings=None):
    """导入时给 clip 写空间立体声勾选的初始值。渲染不再改这个勾。

    主音频跟随全局「新导入默认开启」；配乐 / 音效默认关。
    预设 / 湿声不钉到 clip 上，渲染跟全局走（见 resolve_spatial_for_track）。
    """
    if clip is None:
        return
    fx = settings if isinstance(settings, dict) else load_fx_settings()
    role = clip_audio_role(clip)
    if role == "sfx":
        _clip_set(clip, "spatial_enabled", False)
        return
    if role == "bgm" or not is_main_clip(clip):
        _clip_set(clip, "spatial_enabled", False)
        return
    _clip_set(clip, "audio_role", "main")
    _clip_set(clip, "spatial_enabled", bool(fx.get("spatial_enabled", True)))

def is_audio_domain_track(tr):
    domain = _clip_get(tr, "domain", None)
    ttype = _clip_get(tr, "type", None)
    return domain == "audio" or ttype in _AUDIO_TYPES


def _timeline_tracks(timeline):
    if timeline is None:
        return []
    if isinstance(timeline, dict):
        tracks = timeline.get("tracks")
        if tracks is None:
            timeline["tracks"] = []
            return timeline["tracks"]
        return tracks
    tracks = getattr(timeline, "tracks", None)
    if tracks is None:
        timeline.tracks = []
        return timeline.tracks
    return tracks


def unique_role_track_name(timeline, role):
    """原声 / 配乐 / 音效，重名则 配乐 2、配乐 3…"""
    base = ROLE_TRACK_NAME.get(str(role or "bgm"), "配乐")
    names = set()
    for tr in _timeline_tracks(timeline):
        name = _clip_get(tr, "name", "") or ""
        if name:
            names.add(str(name))
    if base not in names:
        return base
    n = 2
    while ("%s %d" % (base, n)) in names:
        n += 1
    return "%s %d" % (base, n)


def _track_roles(tr):
    clips = _clip_get(tr, "clips", None) or []
    return {clip_audio_role(c) for c in clips}


def _is_homogeneous_role_track(tr, role):
    clips = _clip_get(tr, "clips", None) or []
    if not clips:
        return str(_clip_get(tr, "audio_role", "") or "") == role
    return _track_roles(tr) == {role}


def _new_role_track(timeline, role, name):
    if isinstance(timeline, dict):
        tr = {
            "id": uuid.uuid4().hex,
            "type": "audio",
            "domain": "audio",
            "audio_role": role,
            "name": name,
            "clips": [],
            "transitions": [],
            "visible": True,
            "locked": False,
            "mute": False,
            "enable_batch": False,
            "is_main_video": False,
            "order": 0,
            "pinned": False,
            "blend_mode": "normal",
        }
        _timeline_tracks(timeline).append(tr)
        return tr
    tr = Track("audio")
    tr.audio_role = role
    tr.name = name
    add = getattr(timeline, "add_track", None)
    if callable(add):
        add(tr)
    else:
        _timeline_tracks(timeline).append(tr)
    return tr


def ensure_role_track(timeline, role: str):
    """返回只承载该角色的轨。绝不返回混装轨。没有则新建。"""
    role = str(role or "bgm")
    if role not in _AUDIO_ROLES:
        role = "bgm"
    audio_tracks = [tr for tr in _timeline_tracks(timeline) if is_audio_domain_track(tr)]
    for tr in audio_tracks:
        if str(_clip_get(tr, "audio_role", "") or "") == role and _is_homogeneous_role_track(tr, role):
            return tr
    for tr in audio_tracks:
        clips = _clip_get(tr, "clips", None) or []
        if not clips:
            continue
        if _track_roles(tr) == {role}:
            _clip_set(tr, "audio_role", role)
            if not (_clip_get(tr, "name", "") or ""):
                _clip_set(tr, "name", ROLE_TRACK_NAME[role])
            return tr
    return _new_role_track(timeline, role, ROLE_TRACK_NAME[role])


def get_or_create_bgm_track(timeline):
    return ensure_role_track(timeline, "bgm")


def get_or_create_sfx_track(timeline):
    return ensure_role_track(timeline, "sfx")


def log_bgm_assign(track, n_clips, vol, spatial=0):
    name = _clip_get(track, "name", "") or ROLE_TRACK_NAME["bgm"]
    _log.info("[Audio] bgm assign track=%s clips=%s vol=%s spatial=%s",
              name, n_clips, vol, spatial)

def clear_sfx_clips(timeline):
    if timeline is None:
        return
    for tr in getattr(timeline, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        tr.clips = [c for c in (tr.clips or []) if not is_sfx_clip(c)]

def task_timeline_duration(task):
    """扫描视频轨得到时间线总时长（含开场）。"""
    total = 0.0
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "video":
                continue
            for clip in getattr(tr, "clips", []) or []:
                try:
                    start_t = float(getattr(clip, "start_t", 0.0))
                    eff = float(getattr(clip, "effective_duration", 0.0))
                    if eff <= 0:
                        in_t = float(getattr(clip, "in_t", 0.0))
                        out_t = float(getattr(clip, "out_t", in_t))
                        speed = max(0.001, float(getattr(clip, "speed", 1.0)))
                        eff = max(0.0, (out_t - in_t) / speed)
                    total = max(total, start_t + eff)
                except Exception:
                    continue
    if total <= 0 and isinstance(task, dict):
        try:
            total = float(task.get("duration") or 0.0)
        except (TypeError, ValueError):
            total = 0.0
        if total <= 0:
            total = probe_duration_sec(task.get("video") or "") or 0.0
    return max(0.0, total)

def _pick_file(cat, rng):
    files = list_available_files(cat)
    if not files:
        return None
    return rng.choice(files)

def _add_sfx_clip(tl, track, path, start, dur, gain_db, fade_in=0.12, fade_out=0.45, loop=False):
    src = MediaSource(path)
    src.has_video = False
    src.has_audio = True
    src_dur = float(src.duration or 0.0)
    if src_dur <= 0:
        src.duration = dur
        src_dur = dur
    tl.sources[src.id] = src
    use_dur = max(0.15, float(dur))
    clip = Clip(src.id, 0.0, use_dur, float(start))
    clip.is_main = False
    clip.audio_role = "sfx"
    clip.gain_db = float(gain_db)
    clip.fade_in_dur = min(fade_in, use_dur * 0.4)
    clip.fade_out_dur = min(fade_out, use_dur * 0.5)
    clip.loop = bool(loop or use_dur > src_dur + 0.05)
    clip.spatial_enabled = False
    track.clips.append(clip)
    return clip

def place_sfx_on_task(task, settings=None, rng=None):
    """按设置把音效铺到任务时间线。先清旧 sfx clip，不动主音频和配乐。

    返回 {placed: int, skipped: str|None}
    """
    fx = settings if isinstance(settings, dict) else load_fx_settings()
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return {"placed": 0, "skipped": "no_timeline"}

    cats = enabled_categories(fx)
    if not cats:
        clear_sfx_clips(tl)
        return {"placed": 0, "skipped": "none_enabled"}

    duration = task_timeline_duration(task)
    if duration <= 0.5:
        return {"placed": 0, "skipped": "no_duration"}

    rng = rng or random.Random()
    interval = max(3.0, float(fx.get("sfx_interval_sec", 30.0)))
    burst = max(0.5, float(fx.get("sfx_duration_sec", 5.0)))
    jitter = max(0.0, float(fx.get("sfx_jitter_sec", 8.0)))
    start_off = max(0.0, float(fx.get("sfx_start_offset_sec", 8.0)))
    vol = max(0.0, float(fx.get("sfx_volume", 0.18)))
    gain_db = linear_to_gain_db(vol)

    clear_sfx_clips(tl)
    track = get_or_create_sfx_track(tl)
    # 清完再建，避免旧 clip 还在
    track.clips = [c for c in (track.clips or []) if not is_sfx_clip(c)]

    placed = 0
    beds = [c for c in cats if c.get("placement") == "bed"]
    bursts = [c for c in cats if c.get("placement") != "bed"]

    for cat in beds:
        path = _pick_file(cat, rng)
        if not path:
            continue
        # 现场同期声比爆发类再低一点，避免盖过人声
        bed_gain = gain_db - 4.0
        _add_sfx_clip(tl, track, path, 0.0, duration, bed_gain,
                      fade_in=0.8, fade_out=1.2, loop=True)
        placed += 1

    if bursts:
        t = start_off + rng.uniform(0.0, min(jitter, interval * 0.4))
        guard = 0
        while t + 0.4 < duration and guard < 4000:
            guard += 1
            cat = rng.choice(bursts)
            path = _pick_file(cat, rng)
            if not path:
                t += interval
                continue
            src_dur = probe_duration_sec(path) or burst
            use = min(burst, src_dur, max(0.4, duration - t))
            _add_sfx_clip(tl, track, path, t, use, gain_db,
                          fade_in=0.08, fade_out=0.5, loop=False)
            placed += 1
            delta = interval + rng.uniform(-jitter, jitter)
            t += max(use + 1.5, delta)

    if isinstance(task, dict):
        task["audio_fx"] = {
            "sfx_volume": vol,
            "sfx_interval_sec": interval,
            "sfx_duration_sec": burst,
        }
    return {"placed": placed, "skipped": None}

def apply_spatial_defaults_to_task(task, settings=None):
    fx = settings if isinstance(settings, dict) else load_fx_settings()
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return 0
    n = 0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            if is_main_clip(c):
                apply_default_spatial_to_clip(c, fx)
                n += 1
    if isinstance(task, dict):
        task["audio_fx"] = dict(fx)
    return n

def prepare_task_audio_for_export(task, settings=None):
    """导出前调用：按设置补音效。空间立体声只认 clip.spatial_enabled，这里不改勾。"""
    fx = settings if isinstance(settings, dict) else load_fx_settings()
    if not bool(fx.get("sfx_auto_apply", True)):
        return
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return
    has_sfx = False
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            if is_sfx_clip(c):
                has_sfx = True
                break
        if has_sfx:
            break
    if not has_sfx and enabled_categories(fx):
        place_sfx_on_task(task, fx)

def set_role_gain_on_task(task, role, linear_vol):
    """批量改某类音频 clip 的 gain_db。role: main|bgm|sfx。"""
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return 0
    gain_db = linear_to_gain_db(linear_vol)
    n = 0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        for c in getattr(tr, "clips", []) or []:
            if clip_audio_role(c) == role:
                c.gain_db = gain_db
                n += 1
    if role == "main" and isinstance(task, dict):
        task["src_volume"] = float(linear_vol)
    return n
