# SPDX-License-Identifier: GPL-3.0-or-later
"""
波形生成与缓存。

UI 线程只读已完成的峰值；解码在后台线程。缓存键 = 绝对路径 + mtime + size。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time

import numpy as np

from cutora.core.config import FFMPEG_PATH
from cutora.core.log import get_logger
from cutora.core.paths import user_cache_dir
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.audio.waveform")

# LOD: peaks per second. Finest is generated; coarser are downsampled.
LOD_RATES = (200, 20, 2)

# --- 波形生成并发控制 (防系统崩溃补丁) ---
# 控制 FFmpeg 最大并发数为 4，防止加载 70+ 个视频时瞬间打满 i9-13th Gen 
# CPU 所有 32 线程，从而引发主板过流保护 (OCP) / 瞬态掉压 (Vdroop) 导致重启。
_FFMPEG_SEMAPHORE = threading.Semaphore(4)

# --------------- 波形生成 ---------------


def file_fingerprint(media_path: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(media_path)
    except OSError:
        return None
    return (os.path.abspath(media_path), int(st.st_mtime_ns), int(st.st_size))


def fingerprint_key(fp: tuple[str, int, int]) -> str:
    raw = ("%s|%s|%s" % fp).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def downsample_peaks(peaks: np.ndarray, factor: int) -> np.ndarray:
    if peaks is None or len(peaks) == 0 or factor <= 1:
        return peaks
    n = len(peaks) // int(factor)
    if n <= 0:
        return peaks[:1].copy()
    chunk = peaks[: n * factor].reshape(n, factor, 2)
    mins = chunk[:, :, 0].min(axis=1)
    maxs = chunk[:, :, 1].max(axis=1)
    return np.column_stack((mins, maxs)).astype(np.float32)


def generate_peaks(media_path: str,
                   ffmpeg_path: str = None,
                   sample_rate: int = 8000,
                   peaks_per_second: int = 200,
                   cancel_event: threading.Event | None = None) -> np.ndarray | None:
    """从音频/视频文件生成波形 peaks 数组。

    返回 shape (N, 2) 的 float32 数组，每行是一对 (min, max)。
    N ≈ duration * peaks_per_second。
    失败时返回 None。cancel_event 置位时杀掉 ffmpeg。
    """
    if not media_path:
        return None
    ffmpeg = ffmpeg_path or FFMPEG_PATH
    try:
        cmd = [
            ffmpeg, "-v", "error",
            "-i", media_path,
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le",
            "-",
        ]
        chunk_size = max(1, sample_rate // peaks_per_second)
        block_bytes = chunk_size * 4
        with _FFMPEG_SEMAPHORE:
            proc = _no_window_popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            peak_blocks = []
            carry = b""
            saw_any = False
            deadline = time.time() + 120
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    return None
                if time.time() > deadline:
                    proc.kill()
                    proc.wait()
                    logger.warning("FFmpeg timeout for %s", media_path)
                    return None
                buf = proc.stdout.read(256 * 1024) if proc.stdout is not None else b""
                if not buf:
                    break
                saw_any = True
                data = carry + buf if carry else buf
                n_full = (len(data) // block_bytes) * block_bytes
                if n_full > 0:
                    samples = np.frombuffer(memoryview(data)[:n_full], dtype=np.float32)
                    reshaped = samples.reshape(-1, chunk_size)
                    peak_blocks.append(
                        np.column_stack((reshaped.min(axis=1), reshaped.max(axis=1))).astype(np.float32)
                    )
                    carry = data[n_full:]
                else:
                    carry = data
            stderr = proc.stderr.read() if proc.stderr is not None else b""
            rc = proc.wait()
        if cancel_event is not None and cancel_event.is_set():
            return None
        if rc != 0:
            logger.warning("FFmpeg failed for %s: %s", media_path, stderr[:200])
            return None
        if not saw_any and len(carry) < 4:
            return None
        if not peak_blocks:
            if len(carry) < 4:
                return None
            return np.zeros((1, 2), dtype=np.float32)
        return np.vstack(peak_blocks) if len(peak_blocks) > 1 else peak_blocks[0]
    except Exception as exc:
        logger.warning("Error generating peaks: %s", exc)
        return None


# --------------- 波形缓存 ---------------

class WaveformCache:
    """线程安全缓存：内存 + 落盘。键含路径/mtime/size。"""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._cancels: dict[str, threading.Event] = {}
        self._progress: dict[str, float] = {}
        self.decode_calls = 0
        self.generate_fn = generate_peaks

    @property
    def peaks_per_second(self) -> int:
        return LOD_RATES[0]

    def _disk_path(self, fp_key: str) -> str:
        root = os.path.join(user_cache_dir(), "waveform")
        os.makedirs(root, exist_ok=True)
        return os.path.join(root, fp_key + ".npz")

    def _pack_lod(self, peaks200: np.ndarray) -> dict:
        return {
            200: peaks200,
            20: downsample_peaks(peaks200, 10),
            2: downsample_peaks(peaks200, 100),
        }

    def get_peaks(self, source_id: str, media_path: str = None) -> np.ndarray | None:
        hit = self.get_cached(source_id)
        if hit is not None:
            return hit
        if media_path:
            self.ensure_async(source_id, media_path)
        return self.get_cached(source_id)

    def get_cached(self, source_id: str) -> np.ndarray | None:
        with self._lock:
            rec = self._cache.get(source_id)
            if not rec:
                return None
            lod = rec.get("lod") or {}
            return lod.get(200)

    def get_lod(self, source_id: str, pixels_per_second: float = 40.0) -> np.ndarray | None:
        with self._lock:
            rec = self._cache.get(source_id)
            if not rec:
                return None
            lod = rec.get("lod") or {}
        pps = float(pixels_per_second or 0)
        if pps >= 80:
            rate = 200
        elif pps >= 8:
            rate = 20
        else:
            rate = 2
        peaks = lod.get(rate)
        if peaks is None:
            peaks = lod.get(200)
        return peaks

    def progress(self, source_id: str) -> float:
        with self._lock:
            return float(self._progress.get(source_id, 0.0))

    def put(self, source_id: str, peaks: np.ndarray):
        with self._lock:
            self._cache[source_id] = {"fp": None, "lod": self._pack_lod(peaks)}

    def invalidate(self, source_id: str):
        self.cancel(source_id)
        with self._lock:
            self._cache.pop(source_id, None)
            self._progress.pop(source_id, None)
            self._failed.discard(source_id)

    def clear(self):
        self.cancel_all()
        with self._lock:
            self._cache.clear()
            self._progress.clear()
            self._failed.clear()

    def is_pending(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._pending

    def is_failed(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._failed

    def set_pending(self, source_id: str):
        with self._lock:
            self._pending.add(source_id)

    def clear_pending(self, source_id: str):
        with self._lock:
            self._pending.discard(source_id)
            self._cancels.pop(source_id, None)

    def cancel(self, source_id: str):
        with self._lock:
            ev = self._cancels.get(source_id)
        if ev is not None:
            ev.set()

    def cancel_all(self):
        with self._lock:
            events = list(self._cancels.values())
        for ev in events:
            ev.set()

    def ensure_async(self, source_id: str, media_path: str, on_done=None) -> bool:
        """Start background fill if needed. Returns True if already in memory."""
        fp = file_fingerprint(media_path) if media_path else None
        with self._lock:
            rec = self._cache.get(source_id)
            if rec and rec.get("fp") == fp and rec.get("lod"):
                return True
            if source_id in self._pending:
                return False
            self._pending.add(source_id)
            ev = threading.Event()
            self._cancels[source_id] = ev
            self._progress[source_id] = 0.05
        t = threading.Thread(
            target=self._fill, args=(source_id, media_path, fp, ev, on_done),
            daemon=True, name="waveform-%s" % source_id[:8],
        )
        t.start()
        return False

    def _fill(self, source_id, media_path, fp, cancel_event, on_done):
        try:
            peaks = None
            if fp is not None:
                disk = self._disk_path(fingerprint_key(fp))
                if os.path.isfile(disk):
                    try:
                        data = np.load(disk)
                        peaks = data["p200"]
                        try:
                            os.utime(disk, None)
                        except OSError:
                            pass
                    except Exception:
                        peaks = None
            if peaks is None:
                self.decode_calls += 1
                with self._lock:
                    self._progress[source_id] = 0.15
                peaks = self.generate_fn(
                    media_path, peaks_per_second=self.peaks_per_second,
                    cancel_event=cancel_event,
                )
            if peaks is None or (cancel_event is not None and cancel_event.is_set()):
                if peaks is None and (
                        cancel_event is None or not cancel_event.is_set()):
                    with self._lock:
                        self._failed.add(source_id)
                return
            lod = self._pack_lod(peaks)
            if fp is not None:
                try:
                    np.savez_compressed(
                        self._disk_path(fingerprint_key(fp)),
                        p200=lod[200], p20=lod[20], p2=lod[2],
                    )
                except Exception as exc:
                    logger.debug("waveform disk save failed: %s", exc)
            with self._lock:
                self._cache[source_id] = {"fp": fp, "lod": lod}
                self._progress[source_id] = 1.0
                self._failed.discard(source_id)
            if on_done:
                on_done(source_id)
        finally:
            self.clear_pending(source_id)


def silence_boundaries(peaks, peaks_per_second=200, threshold=0.05,
                       min_silence=0.12) -> list[float]:
    """Source-time edges where a silence region starts or ends.

    ``peaks`` is shape (N, 2) min/max. A run counts as silence when
    max(|min|, |max|) stays below ``threshold`` for at least ``min_silence``
    seconds. Empty / missing peaks → no edges.
    """
    if peaks is None:
        return []
    arr = np.asarray(peaks)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 2:
        return []
    pps = max(1.0, float(peaks_per_second or 200))
    amp = np.maximum(np.abs(arr[:, 0]), np.abs(arr[:, 1]))
    silent = amp < float(threshold)
    times = []
    prev = False
    start_i = 0
    run = 0
    n = int(silent.shape[0])
    for i in range(n):
        s = bool(silent[i])
        if s and not prev:
            start_i = i
            run = 1
        elif s and prev:
            run += 1
        elif (not s) and prev:
            if run / pps >= float(min_silence):
                times.append(start_i / pps)
                times.append(i / pps)
            run = 0
        prev = s
    if prev and run / pps >= float(min_silence):
        times.append(start_i / pps)
        times.append(n / pps)
    return times


MAX_WAVEFORM_CACHE_BYTES = 512 * 1024 * 1024


def waveform_disk_dir() -> str:
    root = os.path.join(user_cache_dir(), "waveform")
    os.makedirs(root, exist_ok=True)
    return root


def waveform_cache_bytes(root: str | None = None) -> int:
    d = root or waveform_disk_dir()
    total = 0
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                total += os.path.getsize(p)
    except OSError:
        return 0
    return total


def prune_waveform_cache(max_bytes: int = MAX_WAVEFORM_CACHE_BYTES) -> int:
    """Delete oldest files until under max_bytes. atime (touched on disk hit) then mtime."""
    d = waveform_disk_dir()
    entries = []
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            stamp = getattr(st, "st_atime", None) or st.st_mtime
            entries.append((stamp, st.st_size, p))
    except OSError:
        return 0
    total = sum(sz for _t, sz, _p in entries)
    if total <= max_bytes:
        return 0
    entries.sort()  # oldest atime/mtime first
    freed = 0
    for _t, sz, p in entries:
        if total <= max_bytes:
            break
        try:
            os.unlink(p)
            total -= sz
            freed += sz
        except OSError:
            continue
    return freed


def clear_waveform_cache() -> int:
    d = waveform_disk_dir()
    freed = 0
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                sz = os.path.getsize(p)
                os.unlink(p)
                freed += sz
            except OSError:
                continue
    except OSError:
        return 0
    get_waveform_cache().clear()
    return freed


# --------------- 全局单例 ---------------
_global_cache = WaveformCache()


def get_waveform_cache() -> WaveformCache:
    return _global_cache


# --------------- 频谱采样 (用于音谱动画) ---------------

def get_spectrum_at_time(source_id: str, current_time: float,
                         num_bars: int = 16, window_sec: float = 0.05) -> list[float]:
    """从 peaks 缓存中获取指定时间附近的频谱近似值 (0.0~1.0)。

    通过将 window_sec 时间窗口内的 peaks 分成 num_bars 个段，
    计算每段的最大绝对值来模拟频谱效果。

    Parameters
    ----------
    source_id   : 波形缓存的键
    current_time: 当前播放时间 (秒)
    num_bars    : 频谱条数量
    window_sec  : 采样窗口宽度 (秒)

    Returns
    -------
    list[float] 长度为 num_bars 的列表，每个值在 [0.0, 1.0]
    """
    cache = get_waveform_cache()
    peaks = cache.get_cached(source_id)
    if peaks is None or len(peaks) == 0:
        return [0.0] * num_bars

    pps = cache.peaks_per_second  # peaks per second
    # 计算 window 的 peaks 索引范围
    center_idx = int(current_time * pps)
    half_window = int(window_sec * pps / 2)
    start_idx = max(0, center_idx - half_window)
    end_idx = min(len(peaks), center_idx + half_window + 1)

    if start_idx >= end_idx:
        return [0.0] * num_bars

    window_peaks = peaks[start_idx:end_idx]  # shape (W, 2)
    # 取绝对值最大值
    amplitudes = np.maximum(np.abs(window_peaks[:, 0]), np.abs(window_peaks[:, 1]))

    # 分成 num_bars 段
    bars = []
    w = len(amplitudes)
    for i in range(num_bars):
        seg_start = i * w // num_bars
        seg_end = (i + 1) * w // num_bars
        if seg_start >= seg_end:
            seg_end = seg_start + 1
        seg = amplitudes[seg_start:min(seg_end, w)]
        if len(seg) > 0:
            bars.append(float(np.max(seg)))
        else:
            bars.append(0.0)

    # 归一化到 [0, 1]（clamp，peaks 通常在 -1~1 范围）
    max_val = max(bars) if bars else 1.0
    if max_val > 0:
        bars = [min(1.0, b / max_val) for b in bars]

    return bars


def _waveform_worker_class():
    from PySide6.QtCore import QThread, Signal

    class _WaveformWorker(QThread):
        """后台线程：为指定 source_id 生成波形 peaks 并放入缓存。"""
        finished_signal = Signal(str)

        def __init__(self, source_id, media_path, parent=None):
            super().__init__(parent)
            self._source_id = source_id
            self._media_path = media_path

        def run(self):
            try:
                cache = get_waveform_cache()
                fp = file_fingerprint(self._media_path)
                with cache._lock:
                    ev = cache._cancels.get(self._source_id) or threading.Event()
                    cache._cancels[self._source_id] = ev
                cache._fill(self._source_id, self._media_path, fp, ev, None)
            except Exception as exc:
                logger.warning("WaveformWorker error: %s", exc)
            finally:
                self.finished_signal.emit(self._source_id)

    return _WaveformWorker


def _WaveformWorker(source_id, media_path, parent=None):
    return _waveform_worker_class()(source_id, media_path, parent)
