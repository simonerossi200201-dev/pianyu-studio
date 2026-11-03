# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""短视频音效包（30 条）：由本地 Stable Audio Open 1.0 模型生成并完成后期峰值对齐与响度标准化。

* 许可证：Stability AI Community License（年营收低于 100 万美元免费商用，详见 resources/audio/sfx_social/LICENSE.txt）。
* 规格：48 kHz / 立体声 / 16 bit WAV，峰值 ≤ -1.2 dBFS，无直流，首尾平滑淡入淡出。
* 响度：按 ITU-R BS.1770-4 K 计权将最响 400 ms 窗口瞬时响度对齐至 -14.0 LUFS（受峰值上限约束的短促 UI 音效如实记入 PROVENANCE.json）。
* ``CATALOG`` 的 id 被转场元数据 ``cutora.render.transition_catalog`` 引用，保持固定。
* ``attach_transition_sfx`` 把音效放到 audio_role='sfx' 的音效轨上，峰值点对齐转场中点。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import wave

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import butter, lfilter, sosfilt

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES

_log = get_logger("cutora.audio.sfx_social")

SR = 48000
PARAMS_VERSION = "2-sao-1.0"
LICENSE = "Stability AI Community License (Stable Audio Open 1.0, commercial use permitted < $1M USD revenue)"
GROUP_LABEL = "短视频音效"
SOCIAL_DIR = os.path.join(RESOURCES, "audio", "sfx_social")
PROVENANCE_NAME = "PROVENANCE.json"
LICENSE_FILE_NAME = "LICENSE.txt"

TARGET_MMAX_LUFS = -14.0      # 最响 400 ms 窗口的目标响度
PEAK_CEIL_DB = -1.2           # 采样峰值上限（留 0.2 dB 给 16 bit 取整）
MAX_LIMIT_DB = 4.0            # 限幅器最多压 4 dB

TRANSITION_SFX_GAIN_DB = -10.0
TRANSITION_SFX_SETTING = "transition_sfx_enabled"
TRANSITION_SFX_NAME_PREFIX = "转场音效·"


# ═══════════════════════════════════════════════════════════════════════════
# 响度与音频读写工具（ITU-R BS.1770-4，48 kHz K 计权系数）
# ═══════════════════════════════════════════════════════════════════════════

_K1_B = [1.53512485958697, -2.69169618940638, 1.19839281085285]
_K1_A = [1.0, -1.69065929318241, 0.73248077421585]
_K2_B = [1.0, -2.0, 1.0]
_K2_A = [1.0, -1.99004745483398, 0.99007225036621]


def _n(sec: float) -> int:
    return int(round(float(sec) * SR))


def _sos(kind: str, freq, order: int = 2):
    nyq = SR * 0.5
    if isinstance(freq, (tuple, list)):
        lo = min(max(5.0, float(freq[0])), nyq * 0.95)
        hi = min(max(lo * 1.01, float(freq[1])), nyq * 0.95)
        return butter(order, [lo / nyq, hi / nyq], btype=kind, output="sos")
    f = min(max(5.0, float(freq)), nyq * 0.95)
    return butter(order, f / nyq, btype=kind, output="sos")


def lowpass(x, f, order=2):
    return sosfilt(_sos("lowpass", f, order), x, axis=0)


def highpass(x, f, order=2):
    return sosfilt(_sos("highpass", f, order), x, axis=0)


def bandpass(x, lo, hi, order=2):
    return sosfilt(_sos("bandpass", (lo, hi), order), x, axis=0)


def raised_fade(n, fade_in, fade_out):
    env = np.ones(n, dtype=np.float64)
    ai = min(n // 2, max(1, _n(fade_in)))
    ao = min(n // 2, max(1, _n(fade_out)))
    env[:ai] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, ai))
    env[-ao:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, math.pi, ao))
    return env


def _k_power(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    y = lfilter(_K2_B, _K2_A, lfilter(_K1_B, _K1_A, x, axis=0), axis=0)
    return np.sum(y ** 2, axis=1)


def _block_loudness(x, win=0.4, hop=0.1):
    p = _k_power(x)
    nw, nh = _n(win), _n(hop)
    if len(p) < nw:
        p = np.concatenate([p, np.zeros(nw - len(p))])
    c = np.concatenate([[0.0], np.cumsum(p)])
    starts = np.arange(0, len(p) - nw + 1, nh)
    ms = (c[starts + nw] - c[starts]) / nw
    return ms


def _lufs(ms):
    return -0.691 + 10.0 * np.log10(np.maximum(ms, 1e-20))


def integrated_lufs(x) -> float:
    """BS.1770-4 门限积分响度（-70 绝对门限 + -10 相对门限）。短于 400 ms 时补零。"""
    ms = _block_loudness(x)
    lk = _lufs(ms)
    g1 = ms[lk > -70.0]
    if len(g1) == 0:
        return -120.0
    rel = _lufs(np.mean(g1)) - 10.0
    g2 = ms[(lk > -70.0) & (lk > rel)]
    if len(g2) == 0:
        return -120.0
    return float(_lufs(np.mean(g2)))


def momentary_max_lufs(x) -> float:
    ms = _block_loudness(x, 0.4, 0.01)
    return float(_lufs(np.max(ms))) if len(ms) else -120.0


def peak_dbfs(x) -> float:
    m = float(np.max(np.abs(x))) if np.size(x) else 0.0
    return 20.0 * math.log10(m) if m > 1e-12 else -120.0


def limiter(x, ceil_lin, lookahead=0.0015, release=0.06):
    """前瞻峰值限幅：保持 + 释放 + 平滑。保证输出采样峰值 ≤ ceil_lin。"""
    st = x if x.ndim == 2 else x[:, None]
    pk = np.max(np.abs(st), axis=1)
    g = np.minimum(1.0, ceil_lin / np.maximum(pk, 1e-12))
    if float(np.min(g)) >= 1.0:
        return x
    la = max(1, _n(lookahead))
    h = minimum_filter1d(g, size=2 * la + 1, mode="nearest")
    a = math.exp(-1.0 / max(1.0, release * SR))
    hl = h.tolist()
    e = 1.0
    out = [0.0] * len(hl)
    for i, v in enumerate(hl):
        if v < e:
            e = v
        else:
            e = v + (e - v) * a
        out[i] = e
    env = uniform_filter1d(np.asarray(out), size=la + 1, mode="nearest")
    env = np.minimum(env, h)
    y = st * env[:, None]
    y = np.clip(y, -ceil_lin, ceil_lin)
    return y if x.ndim == 2 else y[:, 0]


def to_pcm16(st) -> bytes:
    pcm = np.clip(np.round(np.asarray(st) * 32767.0), -32767, 32767).astype("<i2")
    return pcm.tobytes()


def write_wav16(path, st):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(to_pcm16(st))


def read_wav16(path):
    with wave.open(path, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    return data.reshape(-1, nch), sr, nch, sw


# ═══════════════════════════════════════════════════════════════════════════
# 目录。id 与文件名固定（转场元数据在引用）。
# peak_offset：音效的「冲击点 / 中心」距文件开头的秒数，挂到转场时对齐转场中点。
# peak_check：测试里是否校验能量最大点 ≈ peak_offset（节奏型 / 弥散型音效不校验）。
# ═══════════════════════════════════════════════════════════════════════════

_T, _I, _U, _A, _R = "转场", "冲击", "界面", "氛围", "节奏"

_SPECS = [
    # id,              label,        group, dur,  peak,  check
    ("whoosh_light",    "轻呼啸",      _T, 0.70, 0.35, True),
    ("whoosh_heavy",    "重呼啸",      _T, 1.10, 0.50, True),
    ("whoosh_rise",     "上扬呼啸",    _T, 1.20, 1.00, True),
    ("whoosh_drop",     "下沉呼啸",    _T, 1.00, 0.12, True),
    ("swish_fast",      "快速嗖声",    _T, 0.35, 0.16, True),
    ("impact_low",      "低频冲击",    _I, 1.60, 0.01, True),
    ("impact_punch",    "鼓点重击",    _I, 0.60, 0.01, True),
    ("impact_metal",    "金属撞击",    _I, 2.00, 0.01, True),
    ("shutter_click",   "相机快门",    _U, 0.30, 0.01, True),
    ("riser_short",     "短上升音",    _T, 2.00, 1.92, True),
    ("riser_long",      "长上升音",    _T, 4.00, 3.90, True),
    ("downlifter",      "下降音",      _T, 2.00, 0.05, True),
    ("glitch_burst",    "故障噪声",    _T, 0.60, 0.05, False),
    ("pop_bubble",      "气泡弹出",    _U, 0.18, 0.01, True),
    ("click_soft",      "轻点击",      _U, 0.15, 0.005, True),
    ("page_flip",       "翻页",        _U, 0.60, 0.30, True),
    ("heartbeat",       "心跳",        _R, 1.60, 0.06, True),
    ("zoom_in_swell",   "镜头推近",    _T, 1.00, 0.88, True),
    ("zoom_out_swell",  "镜头拉远",    _T, 1.00, 0.12, True),
    ("tape_rewind",     "磁带倒带",    _T, 1.20, 1.05, False),
    ("film_projector",  "胶片放映机",  _A, 2.50, 1.25, False),
    ("sparkle_shimmer", "闪光点缀",    _A, 1.50, 0.50, False),
    ("bass_drop",       "低音下坠",    _I, 2.00, 0.02, True),
    ("snap_finger",     "响指",        _R, 0.25, 0.01, True),
    ("cam_focus",       "相机对焦",    _U, 0.60, 0.40, False),
    ("air_whoosh",      "空气掠过",    _T, 0.90, 0.45, True),
    ("boom_cinematic",  "电影轰鸣",    _I, 2.50, 0.02, True),
    ("tick_tock",       "时钟滴答",    _R, 2.00, 1.02, False),
    ("reverse_cymbal",  "反向镲",      _T, 2.00, 1.95, True),
    ("ding_bright",     "清脆叮",      _U, 1.50, 0.005, True),
]


def _seed_of(sfx_id: str) -> int:
    return int(hashlib.sha256(("cutora-sao-1.0/" + sfx_id).encode("utf-8")).hexdigest()[:8], 16)


CATALOG = [
    {
        "id": sid,
        "label": label,
        "group": group,
        "duration": float(dur),
        "file": sid + ".wav",
        "license": LICENSE,
        "peak_offset": float(peak),
        "peak_check": bool(check),
        "seed": _seed_of(sid),
        "model": "stabilityai/stable-audio-open-1.0",
    }
    for sid, label, group, dur, peak, check in _SPECS
]

GROUPS = [_T, _I, _U, _A, _R]


def catalog_by_id() -> dict:
    return {c["id"]: c for c in CATALOG}


def public_catalog() -> list[dict]:
    """返回公开目录副本，给 UI / JSON 用。"""
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in CATALOG]


def sfx_path(sfx_id: str, base_dir: str | None = None) -> str | None:
    c = catalog_by_id().get(str(sfx_id or ""))
    if c is None:
        return None
    return os.path.join(base_dir or SOCIAL_DIR, c["file"])


def available_path(sfx_id: str, base_dir: str | None = None) -> str | None:
    p = sfx_path(sfx_id, base_dir=base_dir)
    return p if p and os.path.isfile(p) else None


def missing_files(base_dir: str | None = None) -> list[str]:
    return [c["id"] for c in CATALOG if not os.path.isfile(os.path.join(base_dir or SOCIAL_DIR, c["file"]))]


def render(sfx_id: str, base_dir: str | None = None) -> np.ndarray:
    """读取由 Stable Audio Open 1.0 生成并持久化的 48kHz 立体声 float 数组（-1..1）。"""
    p = available_path(sfx_id, base_dir=base_dir)
    if p is None:
        raise FileNotFoundError(f"SFX file missing for {sfx_id}: {sfx_path(sfx_id, base_dir)}")
    st, _sr, _nch, _sw = read_wav16(p)
    return st


def render_pcm(sfx_id: str, base_dir: str | None = None) -> bytes:
    return to_pcm16(render(sfx_id, base_dir=base_dir))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_provenance(base_dir: str | None = None) -> dict:
    p = os.path.join(base_dir or SOCIAL_DIR, PROVENANCE_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def verify_provenance(base_dir: str | None = None) -> tuple[bool, list[str]]:
    """校验 30 条音效 WAV、LICENSE.txt 与 PROVENANCE.json 的 SHA256 一致性。"""
    root = base_dir or SOCIAL_DIR
    errors: list[str] = []
    if not os.path.isfile(os.path.join(root, LICENSE_FILE_NAME)):
        errors.append("missing LICENSE.txt")
    prov = load_provenance(root)
    items = {it.get("id"): it for it in (prov.get("items") or []) if isinstance(it, dict)}
    for c in CATALOG:
        sid = c["id"]
        wav_p = os.path.join(root, c["file"])
        if not os.path.isfile(wav_p):
            errors.append(f"missing {c['file']}")
            continue
        it = items.get(sid)
        if not it:
            errors.append(f"missing provenance entry for {sid}")
            continue
        if _sha256_file(wav_p) != it.get("sha256"):
            errors.append(f"sha256 mismatch for {sid}")
    return len(errors) == 0, errors


def ensure_generated(base_dir: str | None = None) -> bool:
    """检查 30 条预生成音效文件是否全部就绪。"""
    return not missing_files(base_dir)


# ═══════════════════════════════════════════════════════════════════════════
# 设置开关：转场自动配音效（默认关）
# ═══════════════════════════════════════════════════════════════════════════

def is_enabled() -> bool:
    try:
        from cutora.audio import sfx_library as _lib
        from cutora.core import safe_json
        p = _lib._settings_path()
        if not os.path.exists(p):
            return False
        with open(p, "r", encoding="utf-8") as f:
            data = safe_json.load(f)
        return bool(isinstance(data, dict) and data.get(TRANSITION_SFX_SETTING, False))
    except Exception:
        _log.exception("[SfxSocial] read %s failed", TRANSITION_SFX_SETTING)
        return False


def set_enabled(on: bool) -> bool:
    try:
        from cutora.audio import sfx_library as _lib
        from cutora.core import safe_json
        from cutora.core.secrets import strip_secret_fields
        p = _lib._settings_path()
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
        data[TRANSITION_SFX_SETTING] = bool(on)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        _log.exception("[SfxSocial] write %s failed", TRANSITION_SFX_SETTING)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 转场挂音效
# ═══════════════════════════════════════════════════════════════════════════

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _resolve_timeline(project_or_timeline):
    obj = project_or_timeline
    for _ in range(3):
        if obj is None:
            return None
        if not isinstance(obj, dict) and hasattr(obj, "tracks") and hasattr(obj, "sources"):
            return obj
        nxt = _get(obj, "timeline", None)
        if nxt is None or nxt is obj:
            return None
        obj = nxt
    return None


def transition_window(transition):
    """(start, end) 秒；拿不到返回 None。支持 TransitionItem / dict / 独立转场块 Clip。"""
    try:
        start = float(_get(transition, "start_t", None))
    except (TypeError, ValueError):
        return None
    dur = _get(transition, "duration", None)
    if callable(dur):
        dur = None
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    if dur is None:
        try:
            from cutora.render.transition_runtime import clip_timeline_span
            s, e = clip_timeline_span(transition)
            dur = e - s
        except Exception:
            return None
    if not (math.isfinite(start) and math.isfinite(dur)) or dur < 0 or start < 0:
        return None
    return start, start + dur


def transition_key(track, transition) -> str:
    tid = _get(transition, "id", None)
    if tid:
        return str(tid)
    win = transition_window(transition)
    tr_id = _get(track, "id", "") if track is not None else ""
    if win is None:
        return ""
    return "%s@%.3f+%.3f" % (tr_id, win[0], win[1] - win[0])


def _source_path(tl, clip):
    src = (getattr(tl, "sources", {}) or {}).get(_get(clip, "source_id", None))
    if src is None:
        return ""
    return str(_get(src, "path", "") or "")


def is_transition_sfx_clip(tl, clip) -> bool:
    if _get(clip, "transition_sfx_for", None):
        return True
    name = str(_get(clip, "name", "") or "")
    if not name.startswith(TRANSITION_SFX_NAME_PREFIX):
        return False
    p = _source_path(tl, clip)
    try:
        return bool(p) and os.path.normcase(os.path.dirname(os.path.abspath(p))) == \
            os.path.normcase(os.path.abspath(SOCIAL_DIR))
    except Exception:
        return False


def _clip_span(clip):
    try:
        from cutora.render.transition_runtime import clip_timeline_span
        return clip_timeline_span(clip)
    except Exception:
        s = float(_get(clip, "start_t", 0.0) or 0.0)
        return s, s + max(0.0, float(_get(clip, "out_t", 0.0) or 0.0) - float(_get(clip, "in_t", 0.0) or 0.0))


def _find_existing(tl, key, mid):
    hits = []
    for tr in getattr(tl, "tracks", []) or []:
        if _get(tr, "domain", None) != "audio" and _get(tr, "type", None) not in ("audio", "bgm", "sfx"):
            continue
        for c in list(_get(tr, "clips", []) or []):
            if not is_transition_sfx_clip(tl, c):
                continue
            tag = _get(c, "transition_sfx_for", None)
            if tag:
                if tag == key:
                    hits.append((tr, c))
                continue
            s, e = _clip_span(c)
            if s - 1e-3 <= mid <= e + 1e-3:
                hits.append((tr, c))
    return hits


def attach_transition_sfx(project_or_timeline, track, transition, sfx_id):
    """把 sfx_id 挂到转场上：音效轨（audio_role='sfx'，没有就建），冲击点对齐转场中点。

    幂等：同一个转场再调一次不会叠第二条——同音效只重新对齐，换音效就替换。
    任何输入不对都返回 None 并记日志，不抛异常。
    """
    try:
        tl = _resolve_timeline(project_or_timeline)
        if tl is None:
            _log.warning("[SfxSocial] attach: no timeline object (%r)", type(project_or_timeline).__name__)
            return None
        cat = catalog_by_id().get(str(sfx_id or ""))
        if cat is None:
            _log.warning("[SfxSocial] attach: unknown sfx id %r", sfx_id)
            return None
        win = transition_window(transition)
        if win is None:
            _log.warning("[SfxSocial] attach: transition has no usable start/duration")
            return None
        path = available_path(cat["id"])
        if path is None:
            _log.warning("[SfxSocial] attach: file missing for %s", cat["id"])
            return None
        mid = (win[0] + win[1]) * 0.5
        key = transition_key(track, transition)
        if not key:
            return None
        from cutora.audio import sfx_library as _lib
        from cutora.model.timeline_model import Clip, MediaSource

        existing = _find_existing(tl, key, mid)
        start = mid - cat["peak_offset"]
        in_t = 0.0
        if start < 0.0:
            in_t = -start
            start = 0.0
        dur = float(cat["duration"])
        if dur - in_t < 0.05:
            _log.info("[SfxSocial] attach: %s would start before 0 s and be empty", cat["id"])
            return None
        keep = None
        for tr, c in existing:
            if keep is None and _source_path(tl, c) and os.path.normcase(os.path.abspath(_source_path(tl, c))) \
                    == os.path.normcase(os.path.abspath(path)):
                keep = c
                continue
            try:
                tr.clips.remove(c)
            except (ValueError, AttributeError):
                pass
        if keep is not None:
            keep.start_t = float(start)
            keep.in_t = float(in_t)
            keep.out_t = dur
            keep.transition_sfx_for = key
            return keep

        sfx_track = _lib.get_or_create_sfx_track(tl)
        if sfx_track is None or isinstance(sfx_track, dict):
            _log.warning("[SfxSocial] attach: could not get an sfx track")
            return None
        src = MediaSource(path, duration=dur, probe=False)
        src.has_video = False
        src.has_audio = True
        tl.sources[src.id] = src
        clip = Clip(src.id, float(in_t), dur, float(start))
        clip.is_main = False
        clip.audio_role = "sfx"
        clip.gain_db = float(TRANSITION_SFX_GAIN_DB)
        clip.fade_in_dur = 0.01 if in_t > 0 else 0.0
        clip.fade_out_dur = 0.0
        clip.loop = False
        clip.spatial_enabled = False
        clip.name = TRANSITION_SFX_NAME_PREFIX + cat["label"]
        clip.transition_sfx_for = key
        clip.transition_sfx_id = cat["id"]
        sfx_track.clips.append(clip)
        _log.info("[SfxSocial] attach %s → transition %s mid=%.3fs start=%.3fs", cat["id"], key, mid, start)
        return clip
    except Exception:
        _log.exception("[SfxSocial] attach_transition_sfx failed")
        return None


def detach_transition_sfx(project_or_timeline, track, transition) -> int:
    """删掉挂在该转场上的音效，返回删了几条。"""
    try:
        tl = _resolve_timeline(project_or_timeline)
        win = transition_window(transition)
        if tl is None or win is None:
            return 0
        hits = _find_existing(tl, transition_key(track, transition), (win[0] + win[1]) * 0.5)
        for tr, c in hits:
            try:
                tr.clips.remove(c)
            except (ValueError, AttributeError):
                pass
        return len(hits)
    except Exception:
        _log.exception("[SfxSocial] detach failed")
        return 0


def attach_recommended_for_track(project_or_timeline, track, force=False) -> int:
    """按转场元数据里的推荐音效，给一条轨道上的全部转场挂音效。开关关闭且未 force 时什么都不做。"""
    if not force and not is_enabled():
        return 0
    n = 0
    try:
        from cutora.render.transition_catalog import recommended_sfx
    except Exception:
        _log.exception("[SfxSocial] transition_catalog unavailable")
        return 0
    for trans in list(_get(track, "transitions", []) or []):
        sid = recommended_sfx(str(_get(trans, "preset", "") or ""))
        if sid and attach_transition_sfx(project_or_timeline, track, trans, sid) is not None:
            n += 1
    return n


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m cutora.audio.sfx_social",
                                 description="短视频音效包（Stable Audio Open 1.0 生成）")
    ap.add_argument("--verify", action="store_true", help="校验 WAV 文件与 PROVENANCE.json")
    ap.add_argument("--list", action="store_true", help="只列出目录")
    args = ap.parse_args(argv)
    if args.list:
        for c in CATALOG:
            print("%-16s %-6s %-4s %.2fs peak@%.3fs" % (c["id"], c["label"], c["group"], c["duration"], c["peak_offset"]))
        return 0
    ok, errs = verify_provenance()
    if not ok:
        for e in errs:
            print("ERROR:", e)
        return 1
    print("[sfx_social] verified %d files in %s" % (len(CATALOG), SOCIAL_DIR))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
