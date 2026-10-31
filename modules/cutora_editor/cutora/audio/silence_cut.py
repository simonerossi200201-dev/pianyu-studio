# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""剪气口（停顿剪切）：检测 + 计划 + 复核。不导入 Qt / torch。

一处停顿要剪，必须三路信号都说“这里安静”：
  1. 词级时间戳：task["segments"][i]["words"]（识别 / 对齐已有的就用）
  2. 语音活动检测：faster-whisper 自带的 Silero onnx；不可用时退回
     能量 + 谱平坦度（只用来分类，能量那一路仍然兜底）
  3. 10 ms RMS 能量包络，自适应噪底

任何一路说“有声音”（呼吸、笑、拖长的元音、词），那一段都不剪；
停顿被这种声音撑长时，标成黄色让人工复核。

时间全部是时间线时间（秒）。剪切点吸附到时间线帧边界。
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import asdict, dataclass

import numpy as np

SR = 16000
HOP = 160                     # 10 ms 一格
HOP_S = HOP / float(SR)
UNITS = ("frames", "ms", "s", "min")
UNIT_LABELS = {"frames": "帧", "ms": "毫秒", "s": "秒", "min": "分钟"}
_NON_VOICE = ("bgm", "sfx")
TRANSIENT_S = 0.04            # 比这短、又不是语音的孤立声响（咔哒）当作安静
BREATH_MAX_S = 0.8


# ---------------------------------------------------------------------------
# 单位
# ---------------------------------------------------------------------------

def normalize_unit(unit) -> str:
    u = str(unit or "s").strip().lower()
    alias = {
        "f": "frames", "frame": "frames", "帧": "frames",
        "毫秒": "ms", "millisecond": "ms", "milliseconds": "ms",
        "sec": "s", "second": "s", "seconds": "s", "秒": "s",
        "m": "min", "minute": "min", "minutes": "min", "分钟": "min", "分": "min",
    }
    u = alias.get(u, u)
    if u not in UNITS:
        raise ValueError("单位只能是 frames / ms / s / min，收到 %r" % unit)
    return u


def to_seconds(value, unit, fps) -> float:
    v = float(value)
    u = normalize_unit(unit)
    if u == "frames":
        return v / max(1e-6, float(fps or 30.0))
    if u == "ms":
        return v / 1000.0
    if u == "min":
        return v * 60.0
    return v


def from_seconds(sec, unit, fps) -> float:
    s = float(sec)
    u = normalize_unit(unit)
    if u == "frames":
        return s * float(fps or 30.0)
    if u == "ms":
        return s * 1000.0
    if u == "min":
        return s / 60.0
    return s


@dataclass
class SilenceParams:
    threshold: float = 1.0          # 允许的最长停顿
    unit: str = "s"
    keep: float = 0.25              # 剪完后保留多长的停顿（0 = 只留保护帧）
    keep_unit: str = "s"
    head_frames: int = 2            # 上一句话结束后至少保留几帧
    tail_frames: int = 2            # 下一句话开始前至少保留几帧
    keep_breaths: bool = True       # True：呼吸声当作声音，不剪
    use_vad: bool = True
    trim_edges: bool = True         # 片头片尾的长静音也剪
    xfade_ms: float = 5.0           # 接缝处音频微淡入淡出

    @classmethod
    def from_dict(cls, d=None) -> "SilenceParams":
        d = dict(d or {})
        p = cls()
        for k in asdict(p):
            if k in d and d[k] is not None:
                setattr(p, k, d[k])
        p.threshold = float(p.threshold)
        p.keep = float(p.keep)
        p.unit = normalize_unit(p.unit)
        p.keep_unit = normalize_unit(p.keep_unit)
        p.head_frames = max(0, int(p.head_frames))
        p.tail_frames = max(0, int(p.tail_frames))
        p.keep_breaths = bool(p.keep_breaths)
        p.use_vad = bool(p.use_vad)
        p.trim_edges = bool(p.trim_edges)
        p.xfade_ms = max(0.0, min(50.0, float(p.xfade_ms)))
        if p.threshold <= 0:
            raise ValueError("停顿阈值必须大于 0")
        if p.keep < 0:
            raise ValueError("保留时长不能小于 0")
        return p

    def to_dict(self) -> dict:
        return asdict(self)

    def threshold_s(self, fps) -> float:
        return to_seconds(self.threshold, self.unit, fps)

    def keep_s(self, fps) -> float:
        return to_seconds(self.keep, self.keep_unit, fps)


# ---------------------------------------------------------------------------
# 时间线读取
# ---------------------------------------------------------------------------

def _clip_span(clip):
    from cutora.ui.timeline.snap import clip_span
    return clip_span(clip)


def timeline_fps(task) -> float:
    tl = (task or {}).get("timeline")
    try:
        fps = float(getattr(tl, "fps", 30.0) or 30.0)
    except (TypeError, ValueError):
        fps = 30.0
    return fps if fps > 0 else 30.0


def timeline_end(task) -> float:
    tl = (task or {}).get("timeline")
    end = 0.0
    for tr in getattr(tl, "tracks", []) or []:
        for clip in getattr(tr, "clips", []) or []:
            _s, e = _clip_span(clip)
            end = max(end, e)
    if end <= 0:
        for seg in (task or {}).get("segments") or []:
            if isinstance(seg, dict):
                try:
                    end = max(end, float(seg.get("end") or 0.0))
                except (TypeError, ValueError):
                    pass
    return end


def _src_path(tl, source_id, base_dir=None) -> str:
    src = (getattr(tl, "sources", {}) or {}).get(source_id)
    if src is None:
        return ""
    path = src.get("path") if isinstance(src, dict) else getattr(src, "path", "")
    path = str(path or "")
    if path and not os.path.isabs(path) and base_dir:
        path = os.path.normpath(os.path.join(base_dir, path))
    return path


def _src_has_audio(tl, source_id) -> bool:
    src = (getattr(tl, "sources", {}) or {}).get(source_id)
    if src is None:
        return False
    if isinstance(src, dict):
        return bool(src.get("has_audio", True))
    return bool(getattr(src, "has_audio", True))


def clean_orphan_duplicate_audio(task) -> int:
    """清理因旧版未绑定 link_group_id 遗留的孤立重复视频原声片段，并对齐配乐/音效轨片段角色。"""
    tl = (task or {}).get("timeline")
    if tl is None:
        return 0
    sources = getattr(tl, "sources", {}) or {}
    video_sids = set()
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            for c in getattr(tr, "clips", []) or []:
                sid = getattr(c, "source_id", None)
                if sid:
                    video_sids.add(sid)

    removed = 0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "domain", "visual") != "audio" and getattr(tr, "type", None) not in ("audio", "bgm", "sfx"):
            continue
        t_role = str(getattr(tr, "audio_role", "") or "")
        clips = list(getattr(tr, "clips", []) or [])
        if t_role in _NON_VOICE:
            for c in clips:
                if str(getattr(c, "audio_role", "") or "") == "main" or getattr(c, "is_main", False):
                    c.audio_role = t_role
                    c.is_main = False
            continue
        # 在原声轨上：如果已存在与主视频绑定的主音频片段，清理无 link_group_id 且对应视频轨已被删除的孤立视频源音频片段
        has_linked_main = any(
            getattr(c, "link_group_id", None) and getattr(c, "source_id", None) in video_sids
            for c in clips
        )
        if has_linked_main and len(clips) > 1:
            kept = []
            for c in clips:
                sid = getattr(c, "source_id", None)
                src = sources.get(sid) if sid else None
                has_vid = bool(src.get("has_video", False) if isinstance(src, dict) else getattr(src, "has_video", False))
                if not getattr(c, "link_group_id", None) and has_vid and sid not in video_sids:
                    removed += 1
                    continue
                kept.append(c)
            if len(kept) != len(clips):
                tr.clips = kept
    return removed


def voice_clips(task) -> list:
    """人声片段 [(track, clip)]。配乐 / 音效轨不算。没有音频轨时退回视频片段的原声。"""
    clean_orphan_duplicate_audio(task)
    tl = (task or {}).get("timeline")
    rows = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "domain", "visual") != "audio":
            continue
        if str(getattr(tr, "audio_role", "") or "") in _NON_VOICE:
            continue
        for c in getattr(tr, "clips", []) or []:
            if str(getattr(c, "type", "media") or "media") != "media":
                continue
            if str(getattr(c, "audio_role", "") or "") in _NON_VOICE:
                continue
            rows.append((tr, c))
    if rows:
        return rows
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "video":
            continue
        for c in getattr(tr, "clips", []) or []:
            if _src_has_audio(tl, getattr(c, "source_id", None)):
                rows.append((tr, c))
    return rows


def voice_linked_ids(task) -> set:
    """人声片段及其音视频编组伙伴的 id()。这些在停顿里被剪是正常的。"""
    tl = (task or {}).get("timeline")
    voice = voice_clips(task)
    gids = {getattr(c, "link_group_id", None) for _t, c in voice}
    gids.discard(None)
    out = {id(c) for _t, c in voice}
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            if getattr(c, "link_group_id", None) in gids:
                out.add(id(c))
            elif getattr(tr, "is_main_video", False) and getattr(tr, "type", None) == "video":
                out.add(id(c))
    return out


class AudioCache:
    """按路径缓存 16 kHz 单声道解码结果。一次批处理内复用。"""

    def __init__(self, decoder=None):
        self._data = {}
        self._decoder = decoder or decode_audio

    def get(self, path):
        key = os.path.normcase(os.path.abspath(path))
        if key not in self._data:
            self._data[key] = self._decoder(path)
        return self._data[key]


def decode_audio(path, sr=SR) -> np.ndarray:
    from cutora.core.config import FFMPEG_PATH
    from cutora.core.proc import run
    cmd = [
        FFMPEG_PATH, "-v", "error", "-nostdin", "-i", str(path), "-vn",
        "-ac", "1", "-ar", str(int(sr)), "-f", "f32le", "pipe:1",
    ]
    proc = run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
        raise RuntimeError("解码音频失败 %s: %s" % (path, err))
    return np.frombuffer(proc.stdout or b"", dtype=np.float32).copy()


def mix_voice(task, cache=None, base_dir=None):
    """把人声片段按时间线位置混成一条 16 kHz 单声道。返回 (signal, covered_samples)。"""
    cache = cache or AudioCache()
    tl = (task or {}).get("timeline")
    end = timeline_end(task)
    n = int(math.ceil(end * SR))
    sig = np.zeros(n, dtype=np.float32)
    cov = np.zeros(n, dtype=bool)
    missing = []
    for _tr, c in voice_clips(task):
        path = _src_path(tl, getattr(c, "source_id", None), base_dir)
        if not path or not os.path.isfile(path):
            missing.append(path)
            continue
        data = cache.get(path)
        s, e = _clip_span(c)
        i0 = int(round(s * SR))
        i1 = min(n, int(round(e * SR)))
        length = i1 - i0
        if length <= 0 or i0 >= n:
            continue
        speed = float(getattr(c, "speed", 1.0) or 1.0)
        in_t = float(getattr(c, "in_t", 0.0) or 0.0)
        if abs(speed - 1.0) < 1e-9 and not getattr(c, "speed_curve", None):
            j0 = int(round(in_t * SR))
            seg = data[j0:j0 + length]
            if len(seg) < length and getattr(c, "loop", False) and len(data):
                reps = int(math.ceil(length / max(1, len(data)))) + 1
                seg = np.tile(data, reps)[j0:j0 + length]
        else:
            idx = in_t * SR + np.arange(length, dtype=np.float64) * speed
            idx = idx[idx < len(data) - 1]
            seg = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        m = len(seg)
        if m <= 0:
            continue
        sig[i0:i0 + m] += seg[: n - i0]
        cov[i0:i0 + m] = True
    return sig, cov, missing


# ---------------------------------------------------------------------------
# 特征
# ---------------------------------------------------------------------------

def frame_rms_db(sig) -> np.ndarray:
    sig = np.asarray(sig, dtype=np.float32)
    n = int(math.ceil(len(sig) / HOP))
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    pad = np.zeros(n * HOP, dtype=np.float32)
    pad[: len(sig)] = sig
    x = pad.reshape(n, HOP).astype(np.float64)
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-12)
    return (20.0 * np.log10(rms)).astype(np.float32)


def frame_flatness(sig, i0, i1) -> float:
    """谱平坦度（0 = 纯音 / 元音，1 = 白噪声 / 气声）。i0..i1 是 10 ms 格子下标。"""
    seg = np.asarray(sig[i0 * HOP:i1 * HOP], dtype=np.float64)
    if len(seg) < 256:
        return 0.0
    n = len(seg) // 256
    x = seg[: n * 256].reshape(n, 256) * np.hanning(256)
    p = np.abs(np.fft.rfft(x, axis=1)) ** 2 + 1e-12
    geo = np.exp(np.mean(np.log(p), axis=1))
    ari = np.mean(p, axis=1)
    return float(np.median(geo / ari))


def frames_covered(cov_samples, n_frames) -> np.ndarray:
    out = np.zeros(n_frames, dtype=bool)
    if len(cov_samples) == 0:
        return out
    m = int(math.ceil(len(cov_samples) / HOP))
    pad = np.zeros(m * HOP, dtype=bool)
    pad[: len(cov_samples)] = cov_samples
    frac = pad.reshape(m, HOP).mean(axis=1)
    k = min(n_frames, m)
    out[:k] = frac[:k] >= 0.5
    return out


def energy_levels(db, covered):
    vals = db[covered] if covered is not None and covered.any() else db
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return -120.0, -120.0, -60.0
    floor = float(np.percentile(vals, 5))
    speech = float(np.percentile(vals, 95))
    thr = max(floor + 10.0, speech - 40.0, -65.0)
    if speech - floor > 15.0:
        thr = min(thr, speech - 10.0)
    return floor, speech, float(thr)


def runs(mask):
    """True 的连续段 [(i0, i1)]，i1 不含。"""
    m = np.asarray(mask, dtype=np.int8)
    if len(m) == 0:
        return []
    d = np.diff(np.concatenate(([0], m, [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def silero_speech_frames(sig, n_frames):
    """Silero 语音帧掩码；VAD 不可用时返回 None。"""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except Exception:
        return None
    try:
        opts = VadOptions(
            threshold=0.5, min_speech_duration_ms=60,
            min_silence_duration_ms=100, speech_pad_ms=30)
        chunks = get_speech_timestamps(
            np.asarray(sig, dtype=np.float32), vad_options=opts, sampling_rate=SR)
    except Exception:
        return None
    mask = np.zeros(n_frames, dtype=bool)
    for ch in chunks or []:
        a = int(ch["start"]) // HOP
        b = int(math.ceil(int(ch["end"]) / HOP))
        mask[max(0, a):min(n_frames, b)] = True
    return mask


def _task_subtitle_segments(task) -> list:
    if not isinstance(task, dict):
        return []
    try:
        from cutora.subtitle.track_source import get_segments
        segs = get_segments(task)
        if segs:
            return segs
    except Exception:
        pass
    return list(task.get("segments") or [])


def collect_words(task) -> list:
    words = []
    for seg in _task_subtitle_segments(task):
        if not isinstance(seg, dict):
            continue
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                s = float(w.get("start"))
                e = float(w.get("end"))
            except (TypeError, ValueError):
                continue
            words.append({"start": s, "end": max(s, e),
                          "text": str(w.get("word") or w.get("text") or "")})
    if not words:
        loc = (task or {}).get("_title_locate")
        if isinstance(loc, dict):
            for w in loc.get("segments") or []:
                if not isinstance(w, dict):
                    continue
                try:
                    s = float(w.get("start"))
                    e = float(w.get("end"))
                except (TypeError, ValueError):
                    continue
                words.append({"start": s, "end": max(s, e),
                              "text": str(w.get("word") or w.get("text") or "")})
    words.sort(key=lambda r: (r["start"], r["end"]))
    return words


def word_frames(task, n_frames):
    """词 / 无词字幕所占的 10 ms 格子。返回 (mask, n_words)。"""
    mask = np.zeros(n_frames, dtype=bool)
    n_words = 0
    segs = _task_subtitle_segments(task)
    if not segs:
        loc = (task or {}).get("_title_locate")
        if isinstance(loc, dict) and isinstance(loc.get("segments"), list):
            segs = loc["segments"]
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        spans = []
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                spans.append((float(w.get("start")), float(w.get("end"))))
            except (TypeError, ValueError):
                continue
        n_words += len(spans)
        if not spans and str(seg.get("text") or seg.get("word") or "").strip():
            try:
                spans.append((float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)))
                if isinstance((task or {}).get("_title_locate"), dict) and not (task or {}).get("segments"):
                    n_words += 1
            except (TypeError, ValueError):
                pass
        for s, e in spans:
            a = int(math.floor(s / HOP_S))
            b = int(math.ceil(max(s, e) / HOP_S))
            mask[max(0, a):min(n_frames, max(b, a + 1))] = True
    return mask, n_words


# ---------------------------------------------------------------------------
# 融合
# ---------------------------------------------------------------------------

@dataclass
class Analysis:
    sig: np.ndarray
    db: np.ndarray
    covered: np.ndarray
    energy: np.ndarray        # 过滤后的“有声音”
    vad: np.ndarray | None
    words: np.ndarray
    silent: np.ndarray        # 三路都说安静（且有音频覆盖）
    floor_db: float
    speech_db: float
    thr_db: float
    vad_backend: str
    n_words: int
    end: float
    missing: list


def analyze(task, params=None, cache=None, base_dir=None, sig_cov=None) -> Analysis:
    params = params if isinstance(params, SilenceParams) else SilenceParams.from_dict(params)
    if sig_cov is None:
        sig, cov, missing = mix_voice(task, cache=cache, base_dir=base_dir)
    else:
        sig, cov = sig_cov[0], sig_cov[1]
        missing = []
    end = timeline_end(task)
    db = frame_rms_db(sig)
    n = len(db)
    covered = frames_covered(cov, n)
    floor, speech, thr = energy_levels(db, covered)
    energy = db > thr
    vad = silero_speech_frames(sig, n) if params.use_vad and n else None
    backend = "silero" if vad is not None else "energy+flatness"
    words, n_words = word_frames(task, n)
    speechy = words.copy()
    if vad is not None:
        speechy |= vad
    # 孤立声响：既不在词里、VAD 也不认是话。很短的当作安静，呼吸按开关决定。
    eff = energy.copy()
    for i0, i1 in runs(energy):
        if speechy[i0:i1].any():
            continue
        dur = (i1 - i0) * HOP_S
        if dur < TRANSIENT_S:
            eff[i0:i1] = False
            continue
        if not params.keep_breaths and vad is not None and dur <= BREATH_MAX_S:
            if float(db[i0:i1].max()) < speech - 12.0:
                eff[i0:i1] = False
    silent = covered & ~eff & ~speechy
    return Analysis(sig=sig, db=db, covered=covered, energy=eff, vad=vad,
                    words=words, silent=silent, floor_db=floor, speech_db=speech,
                    thr_db=thr, vad_backend=backend, n_words=n_words, end=end,
                    missing=missing)


def silent_runs(an: Analysis):
    """[(a, b)] 秒。最后一段截到时间线终点。"""
    out = []
    for i0, i1 in runs(an.silent):
        a = i0 * HOP_S
        b = min(i1 * HOP_S, an.end)
        if b > a:
            out.append((a, b))
    return out


def timeline_fingerprint(task) -> str:
    tl = (task or {}).get("timeline")
    rows = []
    for tr in getattr(tl, "tracks", []) or []:
        clips = []
        for c in getattr(tr, "clips", []) or []:
            s, e = _clip_span(c)
            clips.append((round(s, 4), round(e, 4),
                          round(float(getattr(c, "in_t", 0.0) or 0.0), 4)))
        rows.append((str(getattr(tr, "id", "")), tuple(sorted(clips))))
    words = tuple((round(w["start"], 3), round(w["end"], 3)) for w in collect_words(task))
    raw = repr((tuple(rows), words, len((task or {}).get("segments") or [])))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _level(an: Analysis, t) -> float:
    i = int(t / HOP_S)
    lo = max(0, i - 1)
    hi = min(len(an.db), i + 2)
    if hi <= lo:
        return -120.0
    return float(np.mean(an.db[lo:hi]))


def _contained_items(task, c0, c1, voice_ids):
    """[c0, c1) 内完整包住的、不是人声的东西（独立素材、文字、无词字幕）。"""
    eps = 1e-6
    out = []
    tl = (task or {}).get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        for c in getattr(tr, "clips", []) or []:
            if id(c) in voice_ids:
                continue
            s, e = _clip_span(c)
            if s >= c0 - eps and e <= c1 + eps:
                out.append("%s 片段" % (getattr(tr, "type", "") or "轨道"))
    for key, label in (("text_overlays", "文字"), ("image_overlays", "图片")):
        for ov in (task or {}).get(key) or []:
            if not isinstance(ov, dict):
                continue
            try:
                s = float(ov.get("start") or 0.0)
                e = float(ov.get("end") or 0.0)
            except (TypeError, ValueError):
                continue
            if s >= c0 - eps and e <= c1 + eps:
                out.append(label)
    for seg in (task or {}).get("segments") or []:
        if not isinstance(seg, dict):
            continue
        try:
            s = float(seg.get("start") or 0.0)
            e = float(seg.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if s >= c0 - eps and e <= c1 + eps and str(seg.get("text") or "").strip():
            out.append("字幕")
    return out


def plan_task(task, params=None, analysis=None, cache=None, base_dir=None,
              task_index=0) -> dict:
    """返回剪切计划（纯 JSON）。不改 task。"""
    params = params if isinstance(params, SilenceParams) else SilenceParams.from_dict(params)
    fps = timeline_fps(task)
    an = analysis or analyze(task, params, cache=cache, base_dir=base_dir)
    thr_s = params.threshold_s(fps)
    keep = min(params.keep_s(fps), max(0.0, thr_s - 0.02))
    head = params.head_frames / fps
    tail = params.tail_frames / fps
    end = an.end
    voice_ids = voice_linked_ids(task)
    word_list = collect_words(task)

    def _neighbor_words(pa: float, pb: float) -> tuple[str, str]:
        prev_txt = ""
        next_txt = ""
        for w in word_list:
            if w["end"] <= pa + 0.08:
                prev_txt = str(w.get("text") or "").strip()
            elif w["start"] >= pb - 0.08 and not next_txt:
                next_txt = str(w.get("text") or "").strip()
                break
        return prev_txt, next_txt

    cuts = []
    flags = []
    pauses = silent_runs(an)
    for a, b in pauses:
        L = b - a
        if L <= thr_s + 1e-9:
            continue
        at_start = a <= 1e-6
        at_end = b >= end - 1e-6
        if (at_start or at_end) and not params.trim_edges:
            continue
        h = 0.0 if at_start else head
        t = 0.0 if at_end else tail
        k_eff = min(max(keep, h + t), thr_s)
        lo = int(math.ceil((a + h) * fps - 1e-6))
        hi = int(math.floor((b - t) * fps + 1e-6))
        n_max = hi - lo
        need = int(math.ceil((L - k_eff) * fps - 1e-6))
        if n_max <= 0 or need <= 0:
            flags.append({
                "kind": "protect", "start": a, "end": b, "gap_s": L,
                "note": "保护帧太多，这段停顿没法再剪短",
            })
            continue
        n = min(need, n_max)
        if at_start:
            target_kh = 0.0
        elif at_end:
            target_kh = L - n / fps
        else:
            target_kh = (L - n / fps) / 2.0
        best = None
        for j in range(lo, hi - n + 1):
            c0 = j / fps
            c1 = (j + n) / fps
            kh = c0 - a
            score = _level(an, c0) + _level(an, c1) + 20.0 * abs(kh - target_kh)
            if best is None or score < best[0] - 1e-9:
                best = (score, j)
        j = best[1]
        c0 = j / fps
        c1 = (j + n) / fps
        result = L - n / fps
        pw, nw = _neighbor_words(a, b)
        cut = {
            "id": "c%d_%d" % (int(task_index), len(cuts)),
            "start": c0, "end": c1,
            "frame_start": j, "frame_end": j + n,
            "remove_s": n / fps, "remove_frames": n,
            "pause_start": a, "pause_end": b, "pause_s": L,
            "result_s": result,
            "prev_word": pw, "next_word": nw,
            "kind": "lead" if at_start else ("tail" if at_end else "inner"),
            "enabled": True, "status": "ok", "note": "",
        }
        if result > thr_s + 1e-9:
            cut["status"] = "warn"
            cut["note"] = "保护帧限制，剪后仍有 %.2f 秒" % result
        items = _contained_items(task, c0, c1, voice_ids)
        if items:
            cut["enabled"] = False
            cut["status"] = "warn"
            cut["note"] = "停顿里有独立素材（%s），默认不剪，确认后可勾选" % "、".join(sorted(set(items)))
        cuts.append(cut)

    # 黄色：停顿里有非语音声音（呼吸 / 笑 / 拖音 / 环境声），没有被剪
    if an.vad is not None or an.n_words:
        speech = an.words.copy()
        if an.vad is not None:
            speech |= an.vad
        for i0, i1 in runs(~speech):
            a = i0 * HOP_S
            b = min(i1 * HOP_S, end)
            gap = b - a
            if gap <= thr_s + 1e-9:
                continue
            seg_energy = an.energy[i0:i1] & an.covered[i0:i1]
            if not seg_energy.any():
                continue
            removed = sum(c["remove_s"] for c in cuts
                          if c["enabled"] and c["start"] >= a - 1e-6 and c["end"] <= b + 1e-6)
            remain = gap - removed
            if remain <= thr_s + 1e-9:
                continue
            sounds = []
            for k0, k1 in runs(seg_energy):
                flat = frame_flatness(an.sig, i0 + k0, i0 + k1)
                sounds.append({
                    "start": a + k0 * HOP_S, "end": a + k1 * HOP_S,
                    "kind": "breath" if flat > 0.3 else "voiced",
                })
            kinds = {s["kind"] for s in sounds}
            what = "呼吸/气声" if kinds == {"breath"} else "笑声/拖音/哼声等"
            flags.append({
                "kind": "sound", "start": a, "end": b, "gap_s": gap,
                "remain_s": remain, "sounds": sounds,
                "note": "停顿里有非语音声音（%s），这部分没剪；剪后间隔约 %.2f 秒，请试听确认" % (what, remain),
            })
    for i0, i1 in runs(~an.covered):
        a = i0 * HOP_S
        b = min(i1 * HOP_S, end)
        if b - a > thr_s + 1e-9:
            flags.append({
                "kind": "no_audio", "start": a, "end": b, "gap_s": b - a,
                "note": "这一段没有人声音频（片头 / 图片 / 空隙），不剪",
            })
    flags.sort(key=lambda r: r["start"])

    enabled = [c for c in cuts if c["enabled"]]
    est = [c["result_s"] if c in enabled else c["pause_s"] for c in cuts]
    longest_before = max([b - a for a, b in pauses] or [0.0])
    untouched = [b - a for a, b in pauses if b - a <= thr_s + 1e-9]
    longest_after = max(est + untouched or [0.0])
    return {
        "task_index": int(task_index),
        "video": str((task or {}).get("video") or ""),
        "fps": fps,
        "duration": end,
        "threshold_s": thr_s,
        "keep_s": keep,
        "head_s": head,
        "tail_s": tail,
        "params": params.to_dict(),
        "signals": {
            "words": an.n_words,
            "vad": an.vad_backend,
            "floor_db": round(an.floor_db, 1),
            "speech_db": round(an.speech_db, 1),
            "threshold_db": round(an.thr_db, 1),
            "missing_media": [m for m in an.missing if m],
        },
        "fingerprint": timeline_fingerprint(task),
        "cuts": cuts,
        "flags": flags,
        "summary": {
            "n_cuts": len(enabled),
            "remove_s": round(sum(c["remove_s"] for c in enabled), 4),
            "longest_pause_before_s": round(longest_before, 3),
            "longest_pause_after_s": round(longest_after, 3),
            "n_flags": len(flags),
        },
    }


# ---------------------------------------------------------------------------
# 复核辅助
# ---------------------------------------------------------------------------

def map_time(t, applied) -> float:
    """原时间线 t → 剪后时间。applied = 已执行的剪切 [{start, end}]。"""
    t = float(t)
    shift = 0.0
    for c in applied:
        s, e = float(c["start"]), float(c["end"])
        if t >= e:
            shift += e - s
        elif t > s:
            shift += t - s
    return t - shift


def remove_ranges(sig, applied) -> np.ndarray:
    keep = np.ones(len(sig), dtype=bool)
    for c in applied:
        i0 = int(round(float(c["start"]) * SR))
        i1 = int(round(float(c["end"]) * SR))
        keep[max(0, i0):max(0, min(len(sig), i1))] = False
    return np.asarray(sig)[keep]


def compare_audio(expected, actual, joins, floor_db) -> dict:
    """10 ms 包络比较。接缝两侧各 20 ms 不比（微淡入淡出）。"""
    da = frame_rms_db(expected)
    db = frame_rms_db(actual)
    n = min(len(da), len(db))
    if n == 0:
        return {"ok": True, "max_db_diff": 0.0, "frames": 0, "length_diff_s": 0.0}
    mask = np.ones(n, dtype=bool)
    for t in joins:
        i = int(t / HOP_S)
        mask[max(0, i - 2):min(n, i + 3)] = False
    loud = np.maximum(da[:n], db[:n]) > floor_db + 6.0
    sel = mask & loud
    diff = np.abs(da[:n] - db[:n])[sel]
    worst = float(diff.max()) if len(diff) else 0.0
    return {
        "ok": bool(worst <= 1.5 and abs(len(da) - len(db)) <= 2),
        "max_db_diff": round(worst, 3),
        "frames": int(sel.sum()),
        "length_diff_s": round((len(db) - len(da)) * HOP_S, 3),
    }


def compare_words(before, after, applied) -> dict:
    missing = []
    moved = []
    if len(before) != len(after):
        return {
            "ok": False, "before": len(before), "after": len(after),
            "missing": [w["text"] for w in before][:20], "moved": [],
        }
    for wb, wa in zip(before, after):
        if wb["text"] != wa["text"]:
            missing.append(wb["text"])
            continue
        es = map_time(wb["start"], applied)
        ee = map_time(wb["end"], applied)
        if abs(es - wa["start"]) > 0.002 or abs(ee - wa["end"]) > 0.002:
            moved.append({"text": wb["text"], "expected": round(es, 3),
                          "got": round(wa["start"], 3)})
    return {"ok": not missing and not moved, "before": len(before),
            "after": len(after), "missing": missing[:20], "moved": moved[:20]}


def preview_wave(sig, t0, t1, applied_cut=None, pad=2.0) -> np.ndarray:
    """试听片段：剪切前 pad 秒 + 剪切后 pad 秒（applied_cut=None 时取原样 t0-pad..t1+pad）。"""
    sig = np.asarray(sig, dtype=np.float32)
    a = max(0, int(round((t0 - pad) * SR)))
    b = min(len(sig), int(round((t1 + pad) * SR)))
    if applied_cut is None:
        return sig[a:b].copy()
    i0 = int(round(float(applied_cut["start"]) * SR))
    i1 = int(round(float(applied_cut["end"]) * SR))
    left = sig[a:max(a, i0)].copy()
    right = sig[min(b, i1):b].copy()
    f = int(0.005 * SR)
    if len(left) > f and len(right) > f:
        left[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
        right[:f] *= np.linspace(0.0, 1.0, f, dtype=np.float32)
    return np.concatenate([left, right])


def write_wav(path, wave, sr=SR) -> str:
    import wave as _wave
    x = np.clip(np.asarray(wave, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2").tobytes()
    with _wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return path


def envelope(sig, t0, t1, bins=120) -> list:
    """波形缩略图：[t0, t1] 内 bins 个峰值（0~1）。"""
    sig = np.asarray(sig, dtype=np.float32)
    a = max(0, int(t0 * SR))
    b = min(len(sig), int(t1 * SR))
    if b <= a:
        return [0.0] * bins
    seg = np.abs(sig[a:b])
    edges = np.linspace(0, len(seg), bins + 1).astype(int)
    out = []
    for k in range(bins):
        part = seg[edges[k]:max(edges[k] + 1, edges[k + 1])]
        out.append(float(part.max()) if len(part) else 0.0)
    return out


# ---------------------------------------------------------------------------
# 执行涟漪剪切 (Ripple Cut) 与自动自检 (Self-Check Verification)
# ---------------------------------------------------------------------------

def _is_loop_bed_clip(tr, clip, old_end: float) -> bool:
    """判断是否为铺底循环轨（如全程循环的粒子特效覆盖轨、分屏循环副片或全程 BGM）。
    这类轨道不应在每个气口处被切碎产生画面跳切，而是保持连续并随总时长自然缩短。
    局部定点放置的短音效/配乐片段不视为全程铺底，会随时间线波纹左移对齐。
    """
    if getattr(tr, "is_main_video", False):
        return False
    s, e = _clip_span(clip)
    span = max(0.0, e - s)
    if (
        getattr(clip, "loop", False)
        or getattr(clip, "match_duration", False)
        or float(getattr(clip, "_match_target_dur", 0.0) or 0.0) > 0
    ):
        if s <= 0.25 and e >= max(0.5, old_end * 0.85):
            return True
    role = str(getattr(tr, "audio_role", "") or getattr(clip, "audio_role", "") or "")
    if role in _NON_VOICE:
        if s <= 0.25 and span >= max(0.5, old_end * 0.85):
            return True
    return False


def _map_subtitle_seg_list(segs: list, enabled_cuts: list[dict]) -> list[dict]:
    """将字幕段落列表（含词级 words）按切口列表精确涟漪映射，完全落在切口内的段/词自动剔除。"""
    new_segs: list[dict] = []
    for seg in segs or []:
        if not isinstance(seg, dict):
            continue
        try:
            s0 = float(seg.get("start") or 0.0)
            e0 = float(seg.get("end") or s0)
        except (TypeError, ValueError):
            continue
        ns = map_time(s0, enabled_cuts)
        ne = map_time(e0, enabled_cuts)
        if ne <= ns + 1e-4 and e0 > s0 + 1e-4:
            continue
        seg_copy = dict(seg)
        seg_copy["start"] = round(ns, 4)
        seg_copy["end"] = round(max(ns, ne), 4)
        if isinstance(seg.get("words"), list):
            new_words = []
            for w in seg["words"]:
                if not isinstance(w, dict):
                    continue
                try:
                    ws = float(w.get("start") or s0)
                    we = float(w.get("end") or ws)
                except (TypeError, ValueError):
                    continue
                nws = map_time(ws, enabled_cuts)
                nwe = map_time(we, enabled_cuts)
                if nwe <= nws + 1e-4 and we > ws + 1e-4:
                    continue
                w_copy = dict(w)
                w_copy["start"] = round(max(ns, nws), 4)
                w_copy["end"] = round(min(max(ns, ne), max(nws, nwe)), 4)
                new_words.append(w_copy)
            seg_copy["words"] = new_words
        new_segs.append(seg_copy)
    return new_segs


def _cut_clip_interval(clip, c0: float, c1: float, xfade_s: float) -> list:
    """对单个 Clip 执行区间 [c0, c1) 的涟漪剪切，返回剪切后保留下来的 Clip 列表（0、1 或 2 个）。"""
    eps = 1e-5
    d = c1 - c0
    if d <= eps:
        return [clip]
    s, e = _clip_span(clip)
    # 1. 完全在切口左侧
    if e <= c0 + eps:
        return [clip]
    # 2. 完全在切口右侧 → 整体左移 d
    if s >= c1 - eps:
        clip.start_t = max(0.0, float(clip.start_t) - d)
        return [clip]
    # 3. 完全落在切口内 → 删除
    if s >= c0 - eps and e <= c1 + eps:
        return []
    # 4. 横跨整个切口 (s < c0 < c1 < e) → 切成左、右两段并合拢
    if s < c0 - eps and e > c1 + eps:
        mid_right = clip.split_at(c0)
        if mid_right is None:
            return [clip]
        right = mid_right.split_at(c1)
        if right is None:
            return [clip]
        right.start_t = float(c0)
        if xfade_s > 0:
            clip.fade_out_dur = max(float(getattr(clip, "fade_out_dur", 0.0) or 0.0), xfade_s)
            right.fade_in_dur = max(float(getattr(right, "fade_in_dur", 0.0) or 0.0), xfade_s)
        return [clip, right]
    # 5. 切口削掉尾部 (s < c0 < e <= c1)
    if s < c0 - eps and e > c0 + eps:
        clip.split_at(c0)
        if xfade_s > 0:
            clip.fade_out_dur = max(float(getattr(clip, "fade_out_dur", 0.0) or 0.0), xfade_s)
        return [clip]
    # 6. 切口削掉头部 (c0 <= s < c1 < e)
    if s < c1 - eps and e > c1 + eps:
        right = clip.split_at(c1)
        if right is None:
            return []
        right.start_t = float(c0)
        if xfade_s > 0:
            right.fade_in_dur = max(float(getattr(right, "fade_in_dur", 0.0) or 0.0), xfade_s)
        return [right]
    return [clip]


def apply_plan(task: dict, plan: dict, *, force: bool = False) -> dict:
    """按 ``plan_task`` 生成的剪切计划对 ``task`` 原地执行无损涟漪剪切（Ripple Cut）。

    同步联动：
    - 主视频与人声轨道（在切点处精确分割、左移合拢，并施加微淡入淡出避免爆音咔哒）
    - 循环铺底轨 / 分屏副轨 / 全程 BGM（保持画面与音乐连续不跳切，自动收尾对齐新总时长）
    - 字幕段 ``task["segments"]`` 及词级时间戳 ``words``
    - 标题与贴纸 ``task["text_overlays"]`` / ``task["image_overlays"]`` / ``task["batch_load"]``
    """
    if not isinstance(task, dict) or not isinstance(plan, dict):
        raise ValueError("task 与 plan 必须为字典")
    clean_orphan_duplicate_audio(task)
    fp_now = timeline_fingerprint(task)
    fp_plan = str(plan.get("fingerprint") or "")
    if fp_plan and fp_plan != fp_now and not force:
        raise RuntimeError("时间线在生成计划后已被修改，请重新检测后再执行剪气口")

    raw_cuts = plan.get("cuts") or []
    enabled_cuts = [
        {"start": float(c["start"]), "end": float(c["end"]), "id": c.get("id", "")}
        for c in raw_cuts
        if isinstance(c, dict)
        and c.get("enabled", True)
        and float(c.get("end") or 0.0) > float(c.get("start") or 0.0) + 1e-5
    ]
    enabled_cuts.sort(key=lambda c: c["start"])

    old_end = timeline_end(task)
    if not enabled_cuts:
        return {
            "ok": True,
            "applied_cuts": [],
            "n_cuts": 0,
            "removed_s": 0.0,
            "old_duration": round(old_end, 4),
            "new_duration": round(old_end, 4),
        }

    p_dict = plan.get("params") or {}
    xfade_ms = float(p_dict.get("xfade_ms", 5.0) if p_dict.get("xfade_ms") is not None else 5.0)
    xfade_s = max(0.0, min(0.05, xfade_ms / 1000.0))
    total_remove = sum(c["end"] - c["start"] for c in enabled_cuts)
    new_target_end = max(0.1, old_end - total_remove)

    tl = task.get("timeline")
    loop_bed_clips = []
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            for c in getattr(tr, "clips", []) or []:
                if _is_loop_bed_clip(tr, c, old_end):
                    loop_bed_clips.append(c)
        loop_bed_ids = {id(c) for c in loop_bed_clips}

        # 从后往前逐刀执行涟漪剪切，前面刀的坐标保持不变
        for cut in reversed(enabled_cuts):
            c0, c1 = cut["start"], cut["end"]
            for tr in getattr(tl, "tracks", []) or []:
                old_clips = list(getattr(tr, "clips", []) or [])
                if not old_clips:
                    continue
                new_clips = []
                for c in old_clips:
                    if id(c) in loop_bed_ids:
                        new_clips.append(c)
                    else:
                        new_clips.extend(_cut_clip_interval(c, c0, c1, xfade_s))
                tr.clips = new_clips

        # 更新铺底循环轨的目标时长以对齐新时间线终点
        for c in loop_bed_clips:
            if getattr(c, "match_duration", False) or getattr(c, "_match_target_dur", 0.0) > 0:
                c._match_target_dur = new_target_end
            else:
                s, e = _clip_span(c)
                if e > new_target_end:
                    speed = max(0.01, float(getattr(c, "speed", 1.0) or 1.0))
                    c.out_t = float(c.in_t) + max(0.1, (new_target_end - s) * speed)

    # 同步映射字幕轨 (Track("subs") subtitle_events) 与 task["segments"] 及词级 words
    mapped_task_segs = None
    if isinstance(task.get("segments"), list):
        mapped_task_segs = _map_subtitle_seg_list(task["segments"], enabled_cuts)
        task["segments"] = mapped_task_segs

    try:
        from cutora.subtitle.track_source import (
            events_of,
            iter_subs_tracks,
            set_segments,
            write_events,
        )
        subs_tracks = list(iter_subs_tracks(task))
        if subs_tracks:
            combined_subs: list[dict] = []
            for idx_st, st in enumerate(subs_tracks):
                ev = events_of(st)
                if isinstance(ev, list) and ev:
                    m_ev = _map_subtitle_seg_list(ev, enabled_cuts)
                    write_events(st, m_ev)
                    combined_subs.extend(m_ev)
                elif idx_st == 0 and mapped_task_segs is not None:
                    write_events(st, list(mapped_task_segs))
                    combined_subs.extend(mapped_task_segs)
            if len(subs_tracks) == 1:
                ev0 = events_of(subs_tracks[0])
                if isinstance(ev0, list):
                    task["segments"] = ev0
            elif combined_subs:
                combined_subs.sort(key=lambda s: float(s.get("start") or 0.0))
                task["segments"] = combined_subs
        elif mapped_task_segs is not None:
            set_segments(task, mapped_task_segs)
    except Exception:
        pass

    # 同步映射 _title_locate 词级缓存
    if isinstance(task.get("_title_locate"), dict):
        loc = dict(task["_title_locate"])
        for sub_key in ("segments", "sentences"):
            if isinstance(loc.get(sub_key), list):
                new_items = []
                for it in loc[sub_key]:
                    if not isinstance(it, dict):
                        continue
                    try:
                        s0 = float(it.get("start") or 0.0)
                        e0 = float(it.get("end") or s0)
                    except (TypeError, ValueError):
                        continue
                    ns = map_time(s0, enabled_cuts)
                    ne = map_time(e0, enabled_cuts)
                    ic = dict(it)
                    ic["start"] = round(ns, 4)
                    ic["end"] = round(max(ns, ne), 4)
                    new_items.append(ic)
                loc[sub_key] = new_items
        task["_title_locate"] = loc

    # 同步映射轨道级自定义 tr.segments（若有）
    if tl is not None:
        for tr in getattr(tl, "tracks", []) or []:
            t_segs = getattr(tr, "segments", None)
            if isinstance(t_segs, list) and t_segs:
                tr.segments = _map_subtitle_seg_list(t_segs, enabled_cuts)

    # 同步映射时间线标记点 task["markers"]
    if isinstance(task.get("markers"), list):
        new_markers = []
        for mk in task["markers"]:
            if isinstance(mk, (int, float)):
                new_markers.append(round(map_time(float(mk), enabled_cuts), 4))
            elif isinstance(mk, dict):
                mk_copy = dict(mk)
                for tk in ("t", "time", "sec", "start"):
                    if isinstance(mk_copy.get(tk), (int, float)):
                        mk_copy[tk] = round(map_time(float(mk_copy[tk]), enabled_cuts), 4)
                new_markers.append(mk_copy)
            else:
                new_markers.append(mk)
        task["markers"] = new_markers

    # 同步映射 text_overlays 和 image_overlays
    for key in ("text_overlays", "image_overlays"):
        if isinstance(task.get(key), list):
            new_ovs = []
            for ov in task[key]:
                if not isinstance(ov, dict):
                    continue
                try:
                    s0 = float(ov.get("start") or 0.0)
                    e0 = float(ov.get("end") or s0)
                except (TypeError, ValueError):
                    new_ovs.append(ov)
                    continue
                ns = map_time(s0, enabled_cuts)
                ne = map_time(e0, enabled_cuts)
                if ne <= ns + 1e-4 and e0 > s0 + 1e-4:
                    continue
                ov_copy = dict(ov)
                ov_copy["start"] = round(ns, 4)
                ov_copy["end"] = round(max(ns + 0.05, ne), 4)
                new_ovs.append(ov_copy)
            task[key] = new_ovs

    # 同步映射 batch_load 记录
    if isinstance(task.get("batch_load"), list):
        for bl in task["batch_load"]:
            if isinstance(bl, dict):
                if bl.get("start_sec") is not None:
                    bl["start_sec"] = round(map_time(float(bl["start_sec"]), enabled_cuts), 4)
                if bl.get("end_sec") is not None:
                    bl["end_sec"] = round(map_time(float(bl["end_sec"]), enabled_cuts), 4)

    new_end = timeline_end(task)
    task["duration"] = round(new_end, 4)
    if task.get("trim_end_sec") is not None:
        task["trim_end_sec"] = round(new_end, 4)

    return {
        "ok": True,
        "applied_cuts": enabled_cuts,
        "n_cuts": len(enabled_cuts),
        "removed_s": round(old_end - new_end, 4),
        "old_duration": round(old_end, 4),
        "new_duration": round(new_end, 4),
    }


def verify_cut(
    task_after: dict,
    applied_cuts: list[dict],
    *,
    words_before: list[dict] | None = None,
    sig_before: np.ndarray | None = None,
    floor_db: float = -70.0,
    params: SilenceParams | dict | None = None,
    cache: AudioCache | None = None,
    base_dir: str | None = None,
) -> dict:
    """剪气口后的自检验证报告（供界面展示与自动化断言）：
    1. 词级完整性核验（0 误删词、0 时间错位）
    2. 音频包络一致性核验（除 5ms 接缝外，保留语音包络与原声完全一致）
    3. 剩余停顿核验（剪后最长停顿 <= 设定阈值）
    """
    params_obj = params if isinstance(params, SilenceParams) else SilenceParams.from_dict(params)
    fps = timeline_fps(task_after)
    thr_s = params_obj.threshold_s(fps)

    words_after = collect_words(task_after)
    word_check = (
        compare_words(words_before, words_after, applied_cuts)
        if words_before is not None
        else {"ok": True, "before": len(words_after), "after": len(words_after), "missing": [], "moved": []}
    )

    an_after = analyze(task_after, params_obj, cache=cache, base_dir=base_dir)
    pauses_after = silent_runs(an_after)
    longest_after = max([b - a for a, b in pauses_after] or [0.0])

    audio_check = {"ok": True, "max_db_diff": 0.0, "frames": 0, "length_diff_s": 0.0}
    if sig_before is not None and len(sig_before) > 0:
        expected_sig = remove_ranges(sig_before, applied_cuts)
        joins = [map_time(c["start"], applied_cuts) for c in applied_cuts]
        audio_check = compare_audio(expected_sig, an_after.sig, joins, floor_db)

    ok_all = bool(word_check["ok"] and audio_check["ok"])
    return {
        "ok": ok_all,
        "words_check": word_check,
        "audio_check": audio_check,
        "longest_pause_after_s": round(longest_after, 3),
        "threshold_s": round(thr_s, 3),
        "within_threshold": bool(longest_after <= thr_s + 0.05),
        "duration_after_s": round(an_after.end, 3),
    }


def cut_task_silences(
    task: dict,
    params: SilenceParams | dict | None = None,
    *,
    cache: AudioCache | None = None,
    base_dir: str | None = None,
    task_index: int = 0,
    verify: bool = True,
) -> dict:
    """对单条视频执行「检测 → 规划 → 涟漪剪切 → 自检复核」全流程。"""
    cache = cache or AudioCache()
    params_obj = params if isinstance(params, SilenceParams) else SilenceParams.from_dict(params)
    words_before = collect_words(task)
    an_before = analyze(task, params_obj, cache=cache, base_dir=base_dir)
    plan = plan_task(task, params_obj, analysis=an_before, cache=cache, base_dir=base_dir, task_index=task_index)
    apply_res = apply_plan(task, plan, force=True)
    ver_res = None
    if verify:
        ver_res = verify_cut(
            task,
            apply_res["applied_cuts"],
            words_before=words_before,
            sig_before=an_before.sig,
            floor_db=an_before.floor_db,
            params=params_obj,
            cache=cache,
            base_dir=base_dir,
        )
    return {
        "ok": True,
        "task_index": int(task_index),
        "plan": plan,
        "apply": apply_res,
        "verify": ver_res,
    }


def batch_silence_cut(
    tasks: list[dict],
    params: SilenceParams | dict | None = None,
    *,
    task_indices: list[int] | None = None,
    apply: bool = True,
    verify: bool = True,
    cache: AudioCache | None = None,
    base_dir: str | None = None,
    progress_cb=None,
) -> dict:
    """批量剪气口：支持对全部或指定下标的视频任务批量检测并执行无损剪气口。"""
    cache = cache or AudioCache()
    params_obj = params if isinstance(params, SilenceParams) else SilenceParams.from_dict(params)
    indices = list(range(len(tasks or []))) if task_indices is None else [int(i) for i in task_indices]
    results = []
    total_cuts = 0
    total_removed_s = 0.0
    total_flags = 0

    for pos, idx in enumerate(indices):
        if not (0 <= idx < len(tasks or [])):
            continue
        t = tasks[idx]
        if callable(progress_cb):
            progress_cb(pos, len(indices), t)
        if apply:
            res = cut_task_silences(t, params_obj, cache=cache, base_dir=base_dir, task_index=idx, verify=verify)
            total_cuts += int(res["apply"]["n_cuts"])
            total_removed_s += float(res["apply"]["removed_s"])
            total_flags += int(res["plan"]["summary"]["n_flags"])
            results.append(res)
        else:
            plan = plan_task(t, params_obj, cache=cache, base_dir=base_dir, task_index=idx)
            total_cuts += int(plan["summary"]["n_cuts"])
            total_removed_s += float(plan["summary"]["remove_s"])
            total_flags += int(plan["summary"]["n_flags"])
            results.append({"ok": True, "task_index": idx, "plan": plan, "apply": None, "verify": None})

    return {
        "ok": True,
        "count": len(results),
        "total_cuts": total_cuts,
        "total_removed_s": round(total_removed_s, 3),
        "total_flags": total_flags,
        "results": results,
    }

