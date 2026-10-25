# -*- coding: utf-8 -*-
"""Verification tests for the native Rust high-performance engine (`cutora_rust_core.dll`)."""
from __future__ import annotations

import numpy as np

from cutora.core.rust_engine import (
    is_rust_available,
    rust_arena_clear,
    rust_arena_used_bytes,
    rust_avail_phys_mb,
    rust_bgra_qimage_to_yuv420p,
    rust_bgr_resize_crop_premask_qimg,
    rust_blur_bgra,
    rust_exec_split_blits,
    rust_lcs_ratio,
    rust_premask_cell_qimg,
    rust_trim_working_set,
    rust_waveform_peaks_f32,
)


def test_rust_engine_loaded_and_watchdog():
    assert is_rust_available() is True
    avail_mb = rust_avail_phys_mb()
    assert avail_mb > 100
    after_trim = rust_trim_working_set()
    assert after_trim > 100
    rust_arena_clear()
    assert rust_arena_used_bytes() == 0


def test_rust_waveform_peaks_exact():
    samples = np.array([-0.75, 0.25, -0.1, 0.9, -0.5, -0.2, 0.0, 0.4], dtype=np.float32)
    peaks = rust_waveform_peaks_f32(samples, bucket_size=4)
    assert peaks is not None
    assert peaks.shape == (2, 2)
    np.testing.assert_allclose(peaks[0], [-0.75, 0.9], atol=1e-5)
    np.testing.assert_allclose(peaks[1], [-0.5, 0.4], atol=1e-5)


def test_rust_blur_bgra_and_lcs():
    img = np.zeros((64, 64, 4), dtype=np.uint8)
    img[16:48, 16:48, :] = 255
    blurred = rust_blur_bgra(img, sigma=6.0, passes=2)
    assert blurred is not None
    assert blurred.shape == (64, 64, 4)
    assert int(blurred[32, 32, 0]) > 200
    assert 0 < int(blurred[16, 16, 0]) < 255

    sim_same = rust_lcs_ratio("让每一句话找到它的位置", "让每一句话找到它的位置")
    sim_diff = rust_lcs_ratio("让每一句话找到它的位置", "完全不同的另一段文字")
    assert sim_same is not None and abs(sim_same - 1.0) < 1e-5
    assert sim_diff is not None and sim_diff < 0.3


def test_rust_avx2_bgra_to_yuv420p_and_split_blits():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage

    w, h = 64, 64
    qimg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    qimg.fill(QColor(255, 255, 255, 255))
    res = rust_bgra_qimage_to_yuv420p(qimg)
    assert res is not None
    yuv_arr, yuv_mv = res
    assert len(yuv_mv) == w * h * 3 // 2
    y_plane = yuv_arr[: w * h]
    # Pure white BT.601 limited-range luma must be 235 (never clamped by i16 overflow)
    assert int(y_plane.min()) == 235 and int(y_plane.max()) == 235

    src = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
    src.fill(QColor(40, 120, 220, 255))
    mask = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(QColor(255, 255, 255, 255))
    premasked = rust_premask_cell_qimg(src, mask)
    assert premasked is not None
    assert premasked.width() == 32 and premasked.height() == 32

    dst = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    dst.fill(0)
    ok = rust_exec_split_blits(
        dst,
        [],
        [("qimg", premasked, None, [QRect(0, 0, 32, 32)], [], 8, 8, True, True)],
    )
    assert ok is True
    px = dst.pixelColor(16, 16)
    assert (px.red(), px.green(), px.blue()) == (40, 120, 220)

    bgr = np.full((64, 64, 3), (220, 120, 40), dtype=np.uint8)
    fused = rust_bgr_resize_crop_premask_qimg(bgr, None, 32, 32, 32, 32, 0, 0)
    assert fused is not None
    assert fused.width() == 32 and fused.height() == 32
    fpx = fused.pixelColor(10, 10)
    assert (fpx.red(), fpx.green(), fpx.blue()) == (40, 120, 220)

