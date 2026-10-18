# -*- coding: utf-8 -*-
"""T05f waveform: async, fingerprint cache, LOD, cancel."""
from __future__ import annotations

import os
import time

import numpy as np

from cutora.audio.waveform import (
    WaveformCache, downsample_peaks, file_fingerprint, fingerprint_key,
)


def test_fingerprint_changes_with_mtime(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"abcd")
    fp1 = file_fingerprint(str(p))
    os.utime(p, (time.time() + 10, time.time() + 10))
    p.write_bytes(b"abcdefgh")
    fp2 = file_fingerprint(str(p))
    assert fp1 != fp2
    assert fingerprint_key(fp1) != fingerprint_key(fp2)


def test_ensure_async_does_not_block_ui_thread(tmp_path):
    p = tmp_path / "b.wav"
    p.write_bytes(b"x" * 16)
    cache = WaveformCache()

    def slow(path, peaks_per_second=200, cancel_event=None, **k):
        time.sleep(0.4)
        return np.zeros((16, 2), dtype=np.float32)

    cache.generate_fn = slow
    t0 = time.perf_counter()
    cache.ensure_async("src1", str(p))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 50.0, elapsed_ms
    deadline = time.time() + 3
    while cache.is_pending("src1") and time.time() < deadline:
        time.sleep(0.02)
    assert cache.get_cached("src1") is not None


def test_second_request_skips_decoder(tmp_path):
    p = tmp_path / "c.wav"
    p.write_bytes(b"x" * 16)
    cache = WaveformCache()
    cache.generate_fn = lambda *a, **k: np.ones((12, 2), dtype=np.float32)
    cache.ensure_async("src2", str(p))
    deadline = time.time() + 3
    while cache.is_pending("src2") and time.time() < deadline:
        time.sleep(0.02)
    assert cache.decode_calls == 1
    cache.ensure_async("src2", str(p))
    time.sleep(0.05)
    assert cache.decode_calls == 1
    assert cache.get_cached("src2") is not None


def test_cancel_stops_worker(tmp_path):
    p = tmp_path / "d.wav"
    p.write_bytes(b"x" * 16)
    cache = WaveformCache()

    def slow(path, peaks_per_second=200, cancel_event=None, **k):
        for _ in range(80):
            if cancel_event is not None and cancel_event.is_set():
                return None
            time.sleep(0.01)
        return np.ones((8, 2), dtype=np.float32)

    cache.generate_fn = slow
    cache.ensure_async("src3", str(p))
    time.sleep(0.03)
    cache.cancel("src3")
    deadline = time.time() + 2
    while cache.is_pending("src3") and time.time() < deadline:
        time.sleep(0.02)
    assert cache.get_cached("src3") is None


def test_lod_downsample():
    peaks = np.column_stack((np.linspace(-1, 0, 200), np.linspace(0, 1, 200)))
    d = downsample_peaks(peaks, 10)
    assert len(d) == 20
    assert d.shape[1] == 2
