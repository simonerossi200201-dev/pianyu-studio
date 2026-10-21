# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q199: 3-role PCM cache. Builder = mix_rows_for_task grouped by role.

Clock contract lives here too: idx = round(clock_ms * 48). Never a free-running
device counter. Spatial / hall IR only on the main stem. Intro clips fold into
main. 96 MB budget; kick order is fixed.
"""
from __future__ import annotations

import hashlib
import threading
import time

import numpy as np

from cutora.core.log import get_logger

logger = get_logger("cutora.audio")

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = 8  # stereo float32
BUDGET_BYTES = 96 * 1024 * 1024
ROLES = ("main", "bgm", "sfx")


def clock_index(clock_ms) -> int:
    """Sample index at 48 kHz from a millisecond clock. Not a device counter."""
    return int(round(float(clock_ms) * 48.0))


def row_stem_role(rec) -> str:
    """Intro audio folds into main; otherwise clip_audio_role / row.audio_role."""
    clip = rec.get("_clip") if isinstance(rec, dict) else None
    intro = False
    if clip is not None:
        if isinstance(clip, dict):
            intro = bool(clip.get("is_intro_video_leadin"))
        else:
            intro = bool(getattr(clip, "is_intro_video_leadin", False))
    if intro:
        return "main"
    role = ""
    if isinstance(rec, dict):
        role = str(rec.get("audio_role") or "")
    if not role and clip is not None:
        from cutora.audio.sfx_library import clip_audio_role
        role = clip_audio_role(clip)
    if role not in ROLES:
        role = "bgm"
    return role


def group_rows_by_role(rows) -> dict:
    bags = {r: [] for r in ROLES}
    for rec in rows or []:
        bags[row_stem_role(rec)].append(rec)
    return bags


def _pcm_bytes(arr) -> int:
    if arr is None:
        return 0
    try:
        return int(arr.nbytes)
    except Exception:
        return 0


def _silence(n_frames) -> np.ndarray:
    n = max(0, int(n_frames or 0))
    return np.zeros((n, CHANNELS), dtype=np.float32)


def preview_clock_rate(*, stems_ready, jkl_rate, clip_speed) -> float:
    """Stem-ready playback stays 1×. clip.speed lives in the graph, not the clock.

    JKL (non-zero) still owns media_player.playbackRate.
    """
    try:
        jkl = int(jkl_rate or 0)
    except (TypeError, ValueError):
        jkl = 0
    if jkl != 0:
        return float(jkl)
    if stems_ready:
        return 1.0
    try:
        return float(clip_speed or 1.0)
    except (TypeError, ValueError):
        return 1.0


def speed_drag_allowed(src_span, *, sr=SAMPLE_RATE, budget=BUDGET_BYTES) -> bool:
    n = int(round(max(0.0, float(src_span or 0.0)) * sr))
    return n * BYTES_PER_FRAME <= int(budget)


def speed_drag_src(stem, start_t, src_span, speed_baked, *, sr=SAMPLE_RATE):
    """Invert one baked stem window back to source-clock PCM. No ffmpeg."""
    n = int(round(max(0.0, float(src_span or 0.0)) * sr))
    if n <= 0 or stem is None or len(stem) == 0:
        return _silence(n)
    baked = max(0.1, float(speed_baked or 1.0))
    idx = np.arange(n, dtype=np.float64) / baked
    src_i = np.rint(float(start_t or 0.0) * sr + idx).astype(np.int64)
    out = _silence(n)
    valid = (src_i >= 0) & (src_i < len(stem))
    if np.any(valid):
        out[valid] = np.asarray(stem)[src_i[valid]]
    return out


def speed_drag_live(drag_src, src_span, speed_live, *, sr=SAMPLE_RATE):
    """Map source-clock PCM to a live speed. Window is src_span/speed_live, not baked*live."""
    live = max(0.1, float(speed_live or 1.0))
    n = int(round(max(0.0, float(src_span or 0.0)) / live * sr))
    if n <= 0 or drag_src is None or len(drag_src) == 0:
        return _silence(n)
    src_i = np.rint(np.arange(n, dtype=np.float64) * live).astype(np.int64)
    src_i = np.clip(src_i, 0, len(drag_src) - 1)
    return np.array(np.asarray(drag_src)[src_i], dtype=np.float32, copy=True)


def _row_as_track(rec, role: str) -> dict:
    in_t = float(rec.get("in_t") or 0.0)
    out_t = float(rec.get("out_t") or 0.0)
    try:
        speed = float(rec.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = min(10.0, max(0.1, speed))
    src_span = max(0.0, out_t - in_t)
    out_dur = src_span if abs(speed - 1.0) < 1e-9 else src_span / speed
    spatial = bool(rec.get("spatial_enabled")) if role == "main" else False
    return {
        "path": rec.get("path"),
        "start": float(rec.get("start_t") or 0.0),
        "duration": out_dur,
        "src_span": src_span,
        "speed": speed,
        "trim_in": in_t,
        "gain_db": float(rec.get("gain_db") or 0.0),
        "_clip": rec.get("_clip"),
        "pitch_semitones": float(rec.get("pitch_semitones") or 0.0),
        "fade_in_dur": float(rec.get("fade_in_dur") or 0.0),
        "fade_out_dur": float(rec.get("fade_out_dur") or 0.0),
        "fade_curve": rec.get("fade_curve") or "tri",
        "spatial_enabled": spatial,
        "spatial_amount": float(rec.get("spatial_amount") or 0.0) if spatial else 0.0,
        "is_main": role == "main" or bool(rec.get("is_main")),
        "audio_role": role,
        "loop": bool(rec.get("loop")),
        "source_duration": float(rec.get("source_duration") or 0.0),
        "keyframes": rec.get("keyframes"),
    }


def _role_fingerprint(role, rows, task, pad_to) -> str:
    items = []
    for rec in rows or []:
        items.append((
            rec.get("path"), rec.get("in_t"), rec.get("out_t"), rec.get("start_t"),
            rec.get("gain_db"), rec.get("speed"), rec.get("fade_in_dur"),
            rec.get("fade_out_dur"), rec.get("pitch_semitones"),
            rec.get("fade_curve"), rec.get("loop"), rec.get("keyframes"),
        ))
        if role == "main":
            items.append((rec.get("spatial_enabled"), rec.get("spatial_amount")))
    extra = ()
    if role == "main":
        fx = (task or {}).get("audio_fx") if isinstance((task or {}).get("audio_fx"), dict) else {}
        extra = (fx.get("spatial_preset"), fx.get("spatial_width"), fx.get("spatial_wet"))
    payload = (tuple(items), extra, round(float(pad_to or 0.0), 2), role)
    return hashlib.sha1(repr(payload).encode("utf-8")).hexdigest()[:20]


def render_role_pcm(task, rows, *, role="main", pad_to=0.0) -> np.ndarray:
    """One role through export_audio_filter_complex. Tests patch this (no ffmpeg)."""
    n_pad = max(0, int(round(float(pad_to or 0.0) * SAMPLE_RATE)))
    if not rows:
        return _silence(n_pad)
    from cutora.core.config import FFMPEG_PATH
    from cutora.core.proc import run_filter_script
    from cutora.render.render_worker import RenderWorker
    tracks = [_row_as_track(rec, role) for rec in rows]
    fake = {
        "audio_tracks": tracks,
        "audio_fx": ((task or {}).get("audio_fx") if role == "main" else None) or {},
        "src_volume": 1.0,
        "trim_start_sec": float((task or {}).get("trim_start_sec") or 0.0) if role == "main" else 0.0,
        "trim_end_sec": float((task or {}).get("trim_end_sec") or 0.0) if role == "main" else 0.0,
    }
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = SAMPLE_RATE
    fc, built = rw.export_audio_filter_complex(fake, has_video=False)
    if not built or not fc:
        return _silence(n_pad)
    if pad_to and float(pad_to) > 0 and fc.endswith("[aout]"):
        fc = fc[:-len("[aout]")] + ",apad=whole_dur=%.3f[aout]" % float(pad_to)
    cmd_before = [FFMPEG_PATH, "-y", "-v", "error"]
    for rec in built:
        cmd_before += ["-i", rec["path"]]
    from cutora.audio.waveform import _FFMPEG_SEMAPHORE
    with _FFMPEG_SEMAPHORE:
        r = run_filter_script(
            cmd_before, fc,
            ["-map", "[aout]", "-f", "f32le", "-ac", "2",
             "-ar", str(SAMPLE_RATE), "pipe:1"],
            capture_output=True)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-300:]
        logger.warning("[Audio] stem build role=%s failed %s", role, err)
        return _silence(n_pad)
    raw = r.stdout or b""
    pcm = np.frombuffer(raw, dtype=np.float32)
    if pcm.size % CHANNELS:
        pcm = pcm[: pcm.size - (pcm.size % CHANNELS)]
    pcm = np.array(pcm.reshape(-1, CHANNELS), dtype=np.float32, copy=True)
    if n_pad and len(pcm) < n_pad:
        out = _silence(n_pad)
        out[: len(pcm)] = pcm
        return out
    return pcm


def mix_frames(stems, idx, n, gains, mutes, *, preview_sfx=True, duck=None) -> np.ndarray:
    """Sum role stems at idx for n frames. gains already include live fader ratio."""
    n = max(0, int(n or 0))
    out = _silence(n)
    if n <= 0:
        return out
    idx = int(idx)
    from cutora.audio.ducking import normalize_duck, preview_bgm_gain
    duck_on = normalize_duck(duck)
    for role in ROLES:
        if mutes.get(role):
            continue
        if role == "sfx" and not preview_sfx:
            continue
        pcm = (stems or {}).get(role)
        if pcm is None or len(pcm) == 0:
            continue
        g = float(gains.get(role, 1.0) or 0.0)
        if g == 0.0:
            continue
        if role == "bgm" and duck_on is not None:
            main_pcm = (stems or {}).get("main")
            if main_pcm is not None and len(main_pcm):
                ma = max(0, idx)
                mb = min(len(main_pcm), idx + n)
                if mb > ma:
                    g *= preview_bgm_gain(main_pcm[ma:mb], duck_on["depth_db"])
        start = idx
        if start >= len(pcm) or start + n <= 0:
            continue
        a = max(0, start)
        b = min(len(pcm), start + n)
        dst_a = a - start
        dst_b = dst_a + (b - a)
        chunk = pcm[a:b]
        if g != 1.0:
            out[dst_a:dst_b] += chunk * np.float32(g)
        else:
            out[dst_a:dst_b] += chunk
    return out


class StemCache:
    """Current-task 3-stem float32 cache + source-PCM LRU. 96 MB budget."""

    def __init__(self, budget_bytes=BUDGET_BYTES):
        self.budget_bytes = int(budget_bytes)
        self.task_key = None
        self.stems = {r: None for r in ROLES}
        self.stem_fp = {r: None for r in ROLES}
        self.dry_main = None
        self._sources = []
        self.last_build_roles = []
        self._build_lock = threading.Lock()

    def nbytes(self) -> int:
        n = _pcm_bytes(self.dry_main)
        for pcm in self.stems.values():
            n += _pcm_bytes(pcm)
        for rec in self._sources:
            n += _pcm_bytes(rec.get("pcm"))
        return n

    def stems_ready(self) -> bool:
        return any(self.stems.get(r) is not None for r in ROLES)

    def clear(self):
        self.stems = {r: None for r in ROLES}
        self.stem_fp = {r: None for r in ROLES}
        self.dry_main = None
        self._sources = []
        self.task_key = None
        self.last_build_roles = []

    def switch_task(self, task_key):
        self.task_key = task_key
        self.stems = {r: None for r in ROLES}
        self.stem_fp = {r: None for r in ROLES}
        self.dry_main = None
        self.last_build_roles = []
        self.kick_to_fit(0)

    def kick_to_fit(self, extra=0) -> bool:
        """Kick order: non-current source PCM → dry backup → sfx stem.

        Current main/bgm stems are last. Returns True if nbytes+extra fits.
        """
        extra = int(extra or 0)

        def over():
            return self.nbytes() + extra > self.budget_bytes

        if over():
            self._sources = [
                rec for rec in self._sources
                if rec.get("task_key") == self.task_key
            ]
        if over() and self.dry_main is not None:
            self.dry_main = None
        if over() and self.stems.get("sfx") is not None:
            self.stems["sfx"] = None
            self.stem_fp["sfx"] = None
        return not over()

    def cache_source_pcm(self, key, pcm, *, task_key, role=""):
        """LRU-ish source PCM. BGM longer than remaining budget is refused."""
        if pcm is None:
            return False
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.ndim == 1:
            pcm = np.column_stack((pcm, pcm))
        extra = int(pcm.nbytes)
        self._sources = [rec for rec in self._sources if rec.get("key") != key]
        self.kick_to_fit(extra)
        if self.nbytes() + extra > self.budget_bytes:
            if role == "bgm":
                logger.info(
                    "[Audio] stem budget role=bgm src_sec=%.0f kept=0",
                    pcm.shape[0] / float(SAMPLE_RATE))
            return False
        self._sources.append({
            "key": key, "pcm": pcm, "task_key": task_key, "role": role,
        })
        return True

    def build(self, task, *, solo_ids=None, preview_sfx=True, task_key=None):
        """Rebuild dirty role stems. One build at a time (ffmpeg + the silence alloc)."""
        with self._build_lock:
            return self._build_locked(
                task, solo_ids=solo_ids, preview_sfx=preview_sfx, task_key=task_key)

    def _build_locked(self, task, *, solo_ids=None, preview_sfx=True, task_key=None):
        from cutora.asr.timeline_wav import mix_rows_for_task
        from cutora.preview.engine import playback_end_sec

        if task_key is not None:
            if self.task_key not in (None, task_key):
                self.switch_task(task_key)
            else:
                self.task_key = task_key
        pad_to = 0.0
        try:
            pad_to = float(playback_end_sec(task) or 0.0)
        except Exception:
            pad_to = 0.0
        rows = mix_rows_for_task(
            task, solo_ids=solo_ids, preview_sfx=preview_sfx)
        bags = group_rows_by_role(rows)
        rebuilt = []
        n_pad = max(0, int(round(pad_to * SAMPLE_RATE)))
        for role in ROLES:
            fp = _role_fingerprint(role, bags[role], task, pad_to)
            if fp == self.stem_fp.get(role) and self.stems.get(role) is not None:
                continue
            t0 = time.perf_counter()
            spatial = 0
            if role == "main":
                spatial = int(any(bool(r.get("spatial_enabled")) for r in bags[role]))
            pcm = render_role_pcm(
                task, bags[role], role=role, pad_to=pad_to)
            if pcm is None:
                pcm = _silence(n_pad)
            self.stems[role] = pcm
            self.stem_fp[role] = fp
            if not hasattr(self, "baked_gain") or not isinstance(self.baked_gain, dict):
                self.baked_gain = {r: 1.0 for r in ROLES}
            if bags[role]:
                from cutora.audio.sfx_library import gain_db_to_linear
                lin = float(gain_db_to_linear(bags[role][0].get("gain_db", 0.0)))
                self.baked_gain[role] = max(1e-6, lin) if lin > 0 else 0.0
            else:
                self.baked_gain[role] = 1.0
            rebuilt.append(role)
            ms = (time.perf_counter() - t0) * 1000.0
            sec = pcm.shape[0] / float(SAMPLE_RATE) if pcm is not None else 0.0
            logger.info(
                "[Audio] stem build role=%s sec=%.1f ms=%.0f spatial=%s",
                role, sec, ms, spatial)
        self.last_build_roles = rebuilt
        self.kick_to_fit(0)
        return rebuilt


class StemMixer:
    """Process-wide mixer: cache + live fader ratios + clock-indexed pull."""

    def __init__(self, budget_bytes=BUDGET_BYTES):
        self.cache = StemCache(budget_bytes=budget_bytes)
        self.live_gain = {r: 1.0 for r in ROLES}
        self.baked_gain = {r: 1.0 for r in ROLES}
        self.mute = {r: False for r in ROLES}
        self.preview_sfx = True
        self.duck = None
        self.last_idx = 0
        self.last_idxs = []

    def stems_ready(self) -> bool:
        return self.cache.stems_ready()

    def switch_task(self, task_key):
        self.cache.switch_task(task_key)
        self.live_gain = {r: 1.0 for r in ROLES}
        self.last_idxs = []

    def clear(self):
        self.cache.clear()
        self.live_gain = {r: 1.0 for r in ROLES}
        self.baked_gain = {r: 1.0 for r in ROLES}
        self.mute = {r: False for r in ROLES}
        self.duck = None
        self.last_idxs = []

    def set_live_linear(self, role, linear):
        """Drag: stem × new/old. old≈0 cannot be recovered from a silent bake."""
        if role not in ROLES:
            return
        new = max(0.0, float(linear))
        if new <= 1e-6:
            self.live_gain[role] = 0.0
            return
        old = float(self.baked_gain.get(role) or 0.0)
        if old < 1e-6:
            self.live_gain[role] = 1.0
            return
        self.live_gain[role] = new / old

    def pull_frames(self, clock_ms, n_frames, *, sample_idx=None) -> np.ndarray:
        idx = clock_index(clock_ms) if sample_idx is None else max(0, int(sample_idx))
        self.last_idx = idx
        self.last_idxs.append(idx)
        t0 = time.perf_counter()
        out = mix_frames(
            self.cache.stems, idx, n_frames, self.live_gain, self.mute,
            preview_sfx=self.preview_sfx, duck=getattr(self, "duck", None))
        tick_ms = (time.perf_counter() - t0) * 1000.0
        if tick_ms > 50:
            logger.warning("[Audio] stem mix tick_ms=%.0f", tick_ms)
        return out

    def build(self, task, **kwargs):
        raw = (task or {}).get("audio_duck") if isinstance(task, dict) else None
        self.duck = raw if isinstance(raw, dict) and raw.get("enabled") else None
        rebuilt = self.cache.build(task, **kwargs)
        c_baked = getattr(self.cache, "baked_gain", None) or {}
        for r in rebuilt:
            if r in c_baked:
                self.baked_gain[r] = c_baked[r]
            self.live_gain[r] = 1.0
        return rebuilt


class StemRebuildGate:
    """GUI-side single flight. A request during a build runs once when it ends."""

    def __init__(self):
        self.running = False
        self.dirty = False

    def begin(self) -> bool:
        """True = this caller should start the job."""
        if self.running:
            self.dirty = True
            return False
        self.running = True
        self.dirty = False
        return True

    def finish(self) -> bool:
        """True = start one more job. running stays True across that handoff."""
        if self.dirty:
            self.dirty = False
            self.running = True
            return True
        self.running = False
        return False


_MIXER = None


def get_stem_mixer() -> StemMixer:
    global _MIXER
    if _MIXER is None:
        _MIXER = StemMixer()
    return _MIXER


def reset_stem_mixer() -> StemMixer:
    global _MIXER
    _MIXER = StemMixer()
    return _MIXER


def make_pull_device(host, parent=None):
    """QIODevice that pulls from the clock every buffer. Qt imported here only."""
    from PySide6.QtCore import QIODevice

    class _StemPullDevice(QIODevice):
        def __init__(self):
            super().__init__(parent)
            self._host = host
            self._cursor_idx: int | None = None
            self.open(QIODevice.OpenModeFlag.ReadOnly)

        def reset_cursor(self):
            self._cursor_idx = None

        def isSequential(self):
            return True

        def bytesAvailable(self):
            return SAMPLE_RATE * BYTES_PER_FRAME // 5 + int(super().bytesAvailable())

        def writeData(self, data):
            return 0

        def readData(self, maxlen):
            maxlen = int(maxlen or 0)
            if maxlen <= 0:
                return b""
            n = max(1, maxlen // BYTES_PER_FRAME)
            clock_fn = getattr(self._host, "_stem_clock_ms", None)
            try:
                ms = float(clock_fn()) if callable(clock_fn) else 0.0
            except Exception:
                ms = 0.0
            mixer = get_stem_mixer()
            jkl = int(getattr(self._host, "_jkl_rate", 0) or 0)
            if jkl < 0:
                self._cursor_idx = None
                frames = mixer.pull_frames(ms, n)
                return np.zeros_like(frames).tobytes()
            if jkl == 0:
                target_idx = clock_index(ms)
                if self._cursor_idx is None or abs(self._cursor_idx - target_idx) > int(SAMPLE_RATE * 0.18):
                    self._cursor_idx = target_idx
                frames = mixer.pull_frames(ms, n, sample_idx=self._cursor_idx)
                self._cursor_idx += n
            else:
                self._cursor_idx = None
                frames = mixer.pull_frames(ms, n)
            return frames.tobytes()

    return _StemPullDevice()
