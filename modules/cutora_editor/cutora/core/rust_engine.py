# -*- coding: utf-8 -*-
"""Zero-copy Python bridge to the native Rust engine (`cutora_rust_core.dll`).

Provides deterministic, low-overhead memory management and SIMD/multi-threaded media routines:
- `arena_put_bytes` / `arena_get_bytes` / `arena_clear`: Hard-capped 384 MB native heap arena.
- `waveform_peaks_f32`: Vectorized streaming min/max peak extraction.
- `blur_bgra_inplace`: Multi-threaded separable O(1) Gaussian box blur for glass/backdrop FX.
- `lcs_ratio`: Fast native sequence similarity ratio for Smart Stitch anchor matching.
- `trim_working_set` / `avail_phys_mb`: Direct Win32 kernel working-set & RAM governor.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Optional

import numpy as np

_LIB: Optional[ctypes.CDLL] = None
_LOAD_TRIED = False


def _find_dll() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "cutora_rust_core.dll",
        here.parent.parent / "rust_core" / "target" / "release" / "cutora_rust_core.dll",
        here.parent.parent.parent.parent / "runtime" / "bin" / "cutora_rust_core.dll",
        Path(r"D:\0_LTX\runtime\bin\cutora_rust_core.dll"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


class RustBlitOp(ctypes.Structure):
    _fields_ = [
        ("op_type", ctypes.c_int32),
        ("src_ptr", ctypes.POINTER(ctypes.c_uint8)),
        ("src_w", ctypes.c_uint32),
        ("src_h", ctypes.c_uint32),
        ("src_stride", ctypes.c_uint32),
        ("mask_ptr", ctypes.POINTER(ctypes.c_uint8)),
        ("mask_stride", ctypes.c_uint32),
        ("rects_ptr", ctypes.POINTER(ctypes.c_int32)),
        ("n_rects", ctypes.c_size_t),
        ("ox", ctypes.c_int32),
        ("oy", ctypes.c_int32),
    ]


def get_rust_lib() -> Optional[ctypes.CDLL]:
    global _LIB, _LOAD_TRIED
    if _LOAD_TRIED:
        return _LIB
    _LOAD_TRIED = True
    dll_path = _find_dll()
    if dll_path is None:
        return None
    try:
        lib = ctypes.CDLL(str(dll_path))

        lib.cutora_rust_version.argtypes = []
        lib.cutora_rust_version.restype = ctypes.c_uint32

        lib.cutora_rust_arena_set_limit.argtypes = [ctypes.c_size_t]
        lib.cutora_rust_arena_set_limit.restype = None

        lib.cutora_rust_arena_used_bytes.argtypes = []
        lib.cutora_rust_arena_used_bytes.restype = ctypes.c_size_t

        lib.cutora_rust_arena_clear.argtypes = []
        lib.cutora_rust_arena_clear.restype = ctypes.c_size_t

        lib.cutora_rust_arena_put.argtypes = [
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.cutora_rust_arena_put.restype = ctypes.c_int32

        lib.cutora_rust_arena_get.argtypes = [
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.cutora_rust_arena_get.restype = ctypes.c_size_t

        lib.cutora_rust_waveform_peaks_f32.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
        ]
        lib.cutora_rust_waveform_peaks_f32.restype = ctypes.c_size_t

        lib.cutora_rust_blur_bgra.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        lib.cutora_rust_blur_bgra.restype = ctypes.c_int32

        lib.cutora_rust_lcs_ratio_u32.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_size_t,
        ]
        lib.cutora_rust_lcs_ratio_u32.restype = ctypes.c_float

        lib.cutora_rust_avail_phys_mb.argtypes = []
        lib.cutora_rust_avail_phys_mb.restype = ctypes.c_uint64

        lib.cutora_rust_total_phys_mb.argtypes = []
        lib.cutora_rust_total_phys_mb.restype = ctypes.c_uint64

        lib.cutora_rust_trim_working_set.argtypes = []
        lib.cutora_rust_trim_working_set.restype = ctypes.c_uint64

        lib.cutora_rust_bgr_to_rgb32_resize.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.cutora_rust_bgr_to_rgb32_resize.restype = ctypes.c_int32

        lib.cutora_rust_blit_solid_rect_copy.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_blit_solid_rect_copy.restype = ctypes.c_int32

        lib.cutora_rust_blit_masked_band_plus.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_blit_masked_band_plus.restype = ctypes.c_int32

        lib.cutora_rust_zero_rect.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_zero_rect.restype = ctypes.c_int32

        lib.cutora_rust_quantize_partition.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_int32,
        ]
        lib.cutora_rust_quantize_partition.restype = ctypes.c_int32

        lib.cutora_rust_blit_bgr_solid_rect_copy.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_blit_bgr_solid_rect_copy.restype = ctypes.c_int32

        lib.cutora_rust_blit_bgr_masked_band_plus.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_blit_bgr_masked_band_plus.restype = ctypes.c_int32

        lib.cutora_rust_exec_blit_ops.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(RustBlitOp),
            ctypes.c_size_t,
        ]
        lib.cutora_rust_exec_blit_ops.restype = ctypes.c_int32

        lib.cutora_rust_premask_cell_qimg.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.cutora_rust_premask_cell_qimg.restype = ctypes.c_int32

        lib.cutora_rust_bgra_to_yuv420p.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        lib.cutora_rust_bgra_to_yuv420p.restype = ctypes.c_int32

        lib.cutora_rust_bgr_resize_crop_premask_qimg.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        lib.cutora_rust_bgr_resize_crop_premask_qimg.restype = ctypes.c_int32

        # Configure adaptive arena limit based on total physical RAM (256 MB on <=16GB PCs, 384 MB on 32GB+ PCs)
        total_mb = int(lib.cutora_rust_total_phys_mb())
        cap_mb = 256 if (0 < total_mb <= 16384) else 384
        lib.cutora_rust_arena_set_limit(cap_mb * 1024 * 1024)

        _LIB = lib
    except Exception:
        _LIB = None
    return _LIB


def is_rust_available() -> bool:
    return get_rust_lib() is not None


def rust_trim_working_set() -> int:
    """Trigger Rust arena pressure check + Win32 kernel working-set flush. Returns available RAM in MB."""
    lib = get_rust_lib()
    if lib is not None:
        return int(lib.cutora_rust_trim_working_set())
    return 0


def rust_avail_phys_mb() -> int:
    lib = get_rust_lib()
    if lib is not None:
        return int(lib.cutora_rust_avail_phys_mb())
    return 0


def rust_arena_clear() -> int:
    lib = get_rust_lib()
    if lib is not None:
        return int(lib.cutora_rust_arena_clear())
    return 0


def rust_arena_used_bytes() -> int:
    lib = get_rust_lib()
    if lib is not None:
        return int(lib.cutora_rust_arena_used_bytes())
    return 0


def rust_waveform_peaks_f32(samples: np.ndarray, bucket_size: int) -> Optional[np.ndarray]:
    """Compute (num_buckets, 2) min/max float32 peaks in native Rust."""
    lib = get_rust_lib()
    if lib is None or bucket_size <= 0:
        return None
    arr = np.ascontiguousarray(samples, dtype=np.float32)
    n_buckets = arr.size // bucket_size
    if n_buckets <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    out = np.empty((n_buckets, 2), dtype=np.float32)
    src_ptr = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    dst_ptr = out.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    written = lib.cutora_rust_waveform_peaks_f32(src_ptr, arr.size, int(bucket_size), dst_ptr, n_buckets)
    return out[:written]


def rust_blur_bgra(img_bgra: np.ndarray, sigma: float, passes: int = 2) -> Optional[np.ndarray]:
    """Multi-threaded O(1) separable box blur approximating Gaussian blur in native Rust."""
    lib = get_rust_lib()
    if lib is None:
        return None
    if img_bgra.ndim != 3 or img_bgra.shape[2] != 4 or img_bgra.dtype != np.uint8:
        return None
    h, w, _ = img_bgra.shape
    if h <= 1 or w <= 1:
        return img_bgra.copy()
    radius = max(1, int(round(float(sigma) * 0.85)))
    src = np.ascontiguousarray(img_bgra, dtype=np.uint8)
    dst = np.empty_like(src)
    src_ptr = src.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
    dst_ptr = dst.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
    ok = lib.cutora_rust_blur_bgra(src_ptr, dst_ptr, int(w), int(h), int(radius), int(passes))
    if ok == 1:
        return dst
    return None


def rust_lcs_ratio(s1: str, s2: str) -> Optional[float]:
    """Compute exact Longest Common Subsequence similarity ratio in native Rust."""
    lib = get_rust_lib()
    if lib is None:
        return None
    if not s1 or not s2:
        return 0.0
    a = np.fromiter((ord(c) for c in s1[:4096]), dtype=np.uint32, count=min(len(s1), 4096))
    b = np.fromiter((ord(c) for c in s2[:4096]), dtype=np.uint32, count=min(len(s2), 4096))
    a_ptr = a.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
    b_ptr = b.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
    return float(lib.cutora_rust_lcs_ratio_u32(a_ptr, a.size, b_ptr, b.size))


import threading

_QIMG_TLS = threading.local()


def rust_bgr_to_rgb32_qimage(src_bgr: np.ndarray, dst_w: int, dst_h: int, reuse: bool = False):
    """Fused 1-pass BGR888 (contiguous or strided slice) -> resized QImage(Format_RGB32) in native Rust.
    When reuse=True, reuses a thread-local QImage ring buffer to avoid per-frame VirtualAlloc/VirtualFree page faults.
    """
    lib = get_rust_lib()
    if lib is None or src_bgr is None or src_bgr.ndim != 3 or src_bgr.shape[2] != 3 or src_bgr.dtype != np.uint8:
        return None
    sh, sw, _ = src_bgr.shape
    if sw <= 0 or sh <= 0 or dst_w <= 0 or dst_h <= 0:
        return None
    if src_bgr.strides[1] != 3 or src_bgr.strides[2] != 1 or src_bgr.strides[0] < sw * 3:
        src_bgr = np.ascontiguousarray(src_bgr)
    from PySide6.QtGui import QImage
    dw_i, dh_i = int(dst_w), int(dst_h)
    qimg = None
    if reuse:
        pool = getattr(_QIMG_TLS, "rgb32_pool", None)
        if pool is None:
            pool = {}
            _QIMG_TLS.rgb32_pool = pool
        key = (dw_i, dh_i)
        slot = pool.get(key)
        if slot is None:
            if len(pool) >= 8:
                pool.clear()
            qimg = QImage(dw_i, dh_i, QImage.Format.Format_RGB32)
            pool[key] = qimg
        else:
            qimg = slot
    if qimg is None:
        qimg = QImage(dw_i, dh_i, QImage.Format.Format_RGB32)
    dst_stride = int(qimg.bytesPerLine())
    dst_buf = np.frombuffer(qimg.bits(), dtype=np.uint8)
    src_ptr = src_bgr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
    dst_ptr = dst_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
    rc = lib.cutora_rust_bgr_to_rgb32_resize(
        src_ptr,
        int(sw),
        int(sh),
        int(src_bgr.strides[0]),
        dst_ptr,
        dw_i,
        dh_i,
        dst_stride,
    )
    del dst_buf
    return qimg if rc == 0 else None


def rust_blit_masked_band_plus(src_qimg, mask_qimg, dst_qimg, band_rects, ox: int, oy: int) -> bool:
    """1-pass native Rust DestinationIn(src, mask) + Plus(dst) over band_rects without intermediate scratch QImage."""
    lib = get_rust_lib()
    if lib is None or src_qimg is None or dst_qimg is None:
        return False
    try:
        s_buf = np.frombuffer(src_qimg.constBits(), dtype=np.uint8)
        d_buf = np.frombuffer(dst_qimg.bits(), dtype=np.uint8)
        s_ptr = s_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        sw, sh, s_stride = int(src_qimg.width()), int(src_qimg.height()), int(src_qimg.bytesPerLine())
        dw, dh, d_stride = int(dst_qimg.width()), int(dst_qimg.height()), int(dst_qimg.bytesPerLine())
        if mask_qimg is not None:
            m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
            m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
            m_stride = int(mask_qimg.bytesPerLine())
        else:
            m_buf = None
            m_ptr = ctypes.POINTER(ctypes.c_uint8)()
            m_stride = 0
        for r in band_rects:
            lib.cutora_rust_blit_masked_band_plus(
                s_ptr, sw, sh, s_stride,
                m_ptr, m_stride,
                d_ptr, dw, dh, d_stride,
                int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                int(ox), int(oy),
            )
        del m_buf, d_buf, s_buf
        return True
    except Exception:
        return False


def rust_blit_cell_spans(
    src_qimg,
    mask_qimg,
    dst_qimg,
    solid_rects,
    band_rects,
    ox: int,
    oy: int,
    img_opaque: bool = False,
    premasked: bool = False,
) -> bool:
    """Fused native Rust cell span blit: copies solid_rects (when opaque) and masked-blits band_rects in one buffer lock."""
    lib = get_rust_lib()
    if lib is None or src_qimg is None or dst_qimg is None:
        return False
    try:
        s_buf = np.frombuffer(src_qimg.constBits(), dtype=np.uint8)
        d_buf = np.frombuffer(dst_qimg.bits(), dtype=np.uint8)
        s_ptr = s_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        sw, sh, s_stride = int(src_qimg.width()), int(src_qimg.height()), int(src_qimg.bytesPerLine())
        dw, dh, d_stride = int(dst_qimg.width()), int(dst_qimg.height()), int(dst_qimg.bytesPerLine())
        if solid_rects and img_opaque:
            for r in solid_rects:
                lib.cutora_rust_blit_solid_rect_copy(
                    s_ptr, sw, sh, s_stride,
                    d_ptr, dw, dh, d_stride,
                    int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                    int(ox), int(oy),
                )
        if band_rects:
            if premasked or mask_qimg is None:
                null_m = ctypes.POINTER(ctypes.c_uint8)()
                for r in band_rects:
                    lib.cutora_rust_blit_masked_band_plus(
                        s_ptr, sw, sh, s_stride,
                        null_m, 0,
                        d_ptr, dw, dh, d_stride,
                        int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                        int(ox), int(oy),
                    )
            else:
                m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
                m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
                m_stride = int(mask_qimg.bytesPerLine())
                for r in band_rects:
                    lib.cutora_rust_blit_masked_band_plus(
                        s_ptr, sw, sh, s_stride,
                        m_ptr, m_stride,
                        d_ptr, dw, dh, d_stride,
                        int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                        int(ox), int(oy),
                    )
                del m_buf
        del d_buf, s_buf
        return True
    except Exception:
        return False


def rust_zero_band_rects(dst_qimg, band_origins_rects) -> bool:
    """Zero only the feather band rectangles on dst_qimg in native Rust (avoids clearing the 97.5% solid interior)."""
    lib = get_rust_lib()
    if lib is None or dst_qimg is None:
        return False
    try:
        d_buf = np.frombuffer(dst_qimg.bits(), dtype=np.uint8)
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        dw, dh, d_stride = int(dst_qimg.width()), int(dst_qimg.height()), int(dst_qimg.bytesPerLine())
        for ox, oy, rects in band_origins_rects:
            ox_i, oy_i = int(ox), int(oy)
            for r in rects:
                lib.cutora_rust_zero_rect(
                    d_ptr, dw, dh, d_stride,
                    int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                    ox_i, oy_i,
                )
        del d_buf
        return True
    except Exception:
        return False


def rust_blit_bgr_cell_spans(
    src_bgr: np.ndarray,
    mask_qimg,
    dst_qimg,
    solid_rects,
    band_rects,
    ox: int,
    oy: int,
) -> bool:
    """Zero-allocation direct 1:1 BGR888 -> dst_qimg (Format_ARGB32_Premultiplied / RGB32) cell span blit in native Rust."""
    lib = get_rust_lib()
    if lib is None or src_bgr is None or dst_qimg is None or src_bgr.ndim != 3 or src_bgr.shape[2] != 3 or src_bgr.dtype != np.uint8:
        return False
    try:
        sh, sw, _ = src_bgr.shape
        if src_bgr.strides[1] != 3 or src_bgr.strides[2] != 1 or src_bgr.strides[0] < sw * 3:
            src_bgr = np.ascontiguousarray(src_bgr)
        s_stride = int(src_bgr.strides[0])
        s_ptr = src_bgr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_buf = np.frombuffer(dst_qimg.bits(), dtype=np.uint8)
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        dw, dh, d_stride = int(dst_qimg.width()), int(dst_qimg.height()), int(dst_qimg.bytesPerLine())
        if solid_rects:
            for r in solid_rects:
                lib.cutora_rust_blit_bgr_solid_rect_copy(
                    s_ptr, sw, sh, s_stride,
                    d_ptr, dw, dh, d_stride,
                    int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                    int(ox), int(oy),
                )
        if band_rects and mask_qimg is not None:
            m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
            m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
            m_stride = int(mask_qimg.bytesPerLine())
            for r in band_rects:
                lib.cutora_rust_blit_bgr_masked_band_plus(
                    s_ptr, sw, sh, s_stride,
                    m_ptr, m_stride,
                    d_ptr, dw, dh, d_stride,
                    int(r.x()), int(r.y()), int(r.width()), int(r.height()),
                    int(ox), int(oy),
                )
            del m_buf
        del d_buf
        return True
    except Exception:
        return False


def rust_quantize_partition(weights: list, hard: bool):
    """Quantize split-screen float32 cell masks to uint8 summing to 255 in 1-pass native Rust."""
    lib = get_rust_lib()
    if lib is None or not weights or len(weights) > 16:
        return None
    try:
        shape = weights[0].shape
        n_cells = len(weights)
        n_pixels = int(weights[0].size)
        stacked = np.ascontiguousarray(np.stack(weights, axis=0), dtype=np.float32)
        out = np.empty((n_cells, *shape), dtype=np.uint8)
        w_ptr = stacked.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        o_ptr = out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        rc = lib.cutora_rust_quantize_partition(w_ptr, o_ptr, n_cells, n_pixels, 1 if hard else 0)
        if rc == 0:
            return [out[i] for i in range(n_cells)]
    except Exception:
        pass
    return None


_RECT_C_CACHE: dict[int, tuple[object, ctypes.Array, int]] = {}


def _cached_rects_c_ptr(rects):
    rid = id(rects)
    hit = _RECT_C_CACHE.get(rid)
    if hit is not None and hit[0] is rects:
        return hit[1], hit[2]
    n = len(rects)
    if n == 0:
        return None, 0
    flat = []
    for r in rects:
        flat.extend((int(r.x()), int(r.y()), int(r.width()), int(r.height())))
    arr = (ctypes.c_int32 * (n * 4))(*flat)
    if len(_RECT_C_CACHE) > 2048:
        _RECT_C_CACHE.clear()
    _RECT_C_CACHE[rid] = (rects, arr, n)
    return arr, n


def rust_exec_split_blits(dst_qimg, band_jobs, cell_blits) -> bool:
    """Execute all split-screen band zeroing and multi-cell (BGR / QImage) blits in ONE single FFI call and ONE QImage.bits() lock."""
    lib = get_rust_lib()
    if lib is None or dst_qimg is None:
        return False
    try:
        null_p = ctypes.POINTER(ctypes.c_uint8)()
        ops_list = []
        keepalive = []
        if band_jobs:
            for ox, oy, rects in band_jobs:
                r_arr, n_r = _cached_rects_c_ptr(rects)
                if n_r > 0:
                    ops_list.append(
                        RustBlitOp(
                            0, null_p, 0, 0, 0, null_p, 0,
                            r_arr, n_r, int(ox), int(oy),
                        )
                    )
        for job in cell_blits:
            kind = job[0]
            if kind == "bgr":
                _, src_bgr, mask_qimg, srects, brects, ox, oy = job
                if src_bgr is None or src_bgr.ndim != 3 or src_bgr.shape[2] != 3 or src_bgr.dtype != np.uint8:
                    return False
                sh, sw, _ = src_bgr.shape
                if src_bgr.strides[1] != 3 or src_bgr.strides[2] != 1 or src_bgr.strides[0] < sw * 3:
                    src_bgr = np.ascontiguousarray(src_bgr)
                keepalive.append(src_bgr)
                s_stride = int(src_bgr.strides[0])
                s_ptr = src_bgr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
                ox_i, oy_i = int(ox), int(oy)
                if srects:
                    r_arr, n_r = _cached_rects_c_ptr(srects)
                    if n_r > 0:
                        ops_list.append(
                            RustBlitOp(
                                3, s_ptr, sw, sh, s_stride, null_p, 0,
                                r_arr, n_r, ox_i, oy_i,
                            )
                        )
                if brects and mask_qimg is not None:
                    r_arr, n_r = _cached_rects_c_ptr(brects)
                    if n_r > 0:
                        m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
                        keepalive.append(m_buf)
                        m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
                        m_stride = int(mask_qimg.bytesPerLine())
                        ops_list.append(
                            RustBlitOp(
                                4, s_ptr, sw, sh, s_stride, m_ptr, m_stride,
                                r_arr, n_r, ox_i, oy_i,
                            )
                        )
            elif kind == "qimg":
                _, src_qimg, mask_qimg, srects, brects, ox, oy, img_opaque, premasked = job
                if src_qimg is None:
                    return False
                s_buf = np.frombuffer(src_qimg.constBits(), dtype=np.uint8)
                keepalive.append(s_buf)
                s_ptr = s_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
                sw, sh, s_stride = int(src_qimg.width()), int(src_qimg.height()), int(src_qimg.bytesPerLine())
                ox_i, oy_i = int(ox), int(oy)
                if srects and img_opaque:
                    r_arr, n_r = _cached_rects_c_ptr(srects)
                    if n_r > 0:
                        ops_list.append(
                            RustBlitOp(
                                1, s_ptr, sw, sh, s_stride, null_p, 0,
                                r_arr, n_r, ox_i, oy_i,
                            )
                        )
                if brects:
                    r_arr, n_r = _cached_rects_c_ptr(brects)
                    if n_r > 0:
                        if premasked or mask_qimg is None:
                            ops_list.append(
                                RustBlitOp(
                                    2, s_ptr, sw, sh, s_stride, null_p, 0,
                                    r_arr, n_r, ox_i, oy_i,
                                )
                            )
                        else:
                            m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
                            keepalive.append(m_buf)
                            m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
                            m_stride = int(mask_qimg.bytesPerLine())
                            ops_list.append(
                                RustBlitOp(
                                    2, s_ptr, sw, sh, s_stride, m_ptr, m_stride,
                                    r_arr, n_r, ox_i, oy_i,
                                )
                            )
        if not ops_list:
            return True
        n_ops = len(ops_list)
        ops_arr = (RustBlitOp * n_ops)(*ops_list)
        d_buf = np.frombuffer(dst_qimg.bits(), dtype=np.uint8)
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        dw, dh, d_stride = int(dst_qimg.width()), int(dst_qimg.height()), int(dst_qimg.bytesPerLine())
        rc = lib.cutora_rust_exec_blit_ops(d_ptr, dw, dh, d_stride, ops_arr, n_ops)
        del d_buf, keepalive
        return rc == 0
    except Exception:
        return False


def rust_premask_cell_qimg(src_qimg, mask_qimg):
    """Bake src_qimg + mask_qimg (DestinationIn) into a fresh Format_ARGB32_Premultiplied QImage in 1 pass without QPainter."""
    lib = get_rust_lib()
    if lib is None or src_qimg is None:
        return None
    try:
        from PySide6.QtGui import QImage
        w, h = int(src_qimg.width()), int(src_qimg.height())
        baked = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        s_buf = np.frombuffer(src_qimg.constBits(), dtype=np.uint8)
        d_buf = np.frombuffer(baked.bits(), dtype=np.uint8)
        s_ptr = s_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        if mask_qimg is not None and not mask_qimg.isNull():
            m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
            m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
            m_stride = int(mask_qimg.bytesPerLine())
        else:
            m_buf = None
            m_ptr = ctypes.POINTER(ctypes.c_uint8)()
            m_stride = 0
        rc = lib.cutora_rust_premask_cell_qimg(
            s_ptr, m_ptr, d_ptr, w, h,
            int(src_qimg.bytesPerLine()), m_stride, int(baked.bytesPerLine()),
        )
        del d_buf, s_buf, m_buf
        if rc == 0:
            baked._is_premasked_cell = True
            return baked
    except Exception:
        pass
    return None


def rust_bgra_qimage_to_yuv420p(qimg, dst_buf: np.ndarray | None = None):
    """Convert BGRA / RGB32 QImage to planar YUV420p (tv, BT.601) in native Rust. Returns (dst_buf, memoryview)."""
    lib = get_rust_lib()
    if lib is None or qimg is None:
        return None, None
    try:
        w, h = int(qimg.width()), int(qimg.height())
        if (w & 1) != 0 or (h & 1) != 0 or w <= 0 or h <= 0:
            return None, None
        need = (w * h * 3) // 2
        if dst_buf is None or dst_buf.size != need:
            dst_buf = np.empty(need, dtype=np.uint8)
        s_buf = np.frombuffer(qimg.constBits(), dtype=np.uint8)
        s_ptr = s_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_ptr = dst_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        rc = lib.cutora_rust_bgra_to_yuv420p(s_ptr, w, h, int(qimg.bytesPerLine()), d_ptr)
        del s_buf
        if rc == 0:
            return dst_buf, memoryview(dst_buf)
    except Exception:
        pass
    return None, None


def rust_bgr_resize_crop_premask_qimg(
    src_bgr: np.ndarray,
    mask_qimg,
    dst_w: int,
    dst_h: int,
    full_w: int,
    full_h: int,
    off_x: int,
    off_y: int,
    reuse: bool = False,
):
    """Fused 1-pass BGR888 -> bilinear resize(full_w, full_h) -> crop(off_x, off_y, dst_w, dst_h) -> premask(mask_qimg) -> QImage."""
    lib = get_rust_lib()
    if lib is None or src_bgr is None or src_bgr.ndim != 3 or src_bgr.shape[2] != 3 or src_bgr.dtype != np.uint8:
        return None
    try:
        sh, sw, _ = src_bgr.shape
        dw_i, dh_i = int(dst_w), int(dst_h)
        fw_i, fh_i = int(full_w), int(full_h)
        if sw <= 0 or sh <= 0 or dw_i <= 0 or dh_i <= 0 or fw_i <= 0 or fh_i <= 0:
            return None
        if src_bgr.strides[1] != 3 or src_bgr.strides[2] != 1 or src_bgr.strides[0] < sw * 3:
            src_bgr = np.ascontiguousarray(src_bgr)
        from PySide6.QtGui import QImage
        has_mask = mask_qimg is not None and not mask_qimg.isNull()
        baked = None
        if reuse and not has_mask:
            pool = getattr(_QIMG_TLS, "rgb32_pool", None)
            if pool is None:
                pool = {}
                _QIMG_TLS.rgb32_pool = pool
            key = (dw_i, dh_i)
            slot = pool.get(key)
            if slot is None:
                if len(pool) >= 8:
                    pool.clear()
                baked = QImage(dw_i, dh_i, QImage.Format.Format_RGB32)
                pool[key] = baked
            else:
                baked = slot
        if baked is None:
            fmt = QImage.Format.Format_ARGB32_Premultiplied if has_mask else QImage.Format.Format_RGB32
            baked = QImage(dw_i, dh_i, fmt)
        d_buf = np.frombuffer(baked.bits(), dtype=np.uint8)
        s_ptr = src_bgr.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        d_ptr = d_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        if has_mask:
            m_buf = np.frombuffer(mask_qimg.constBits(), dtype=np.uint8)
            m_ptr = m_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
            m_stride = int(mask_qimg.bytesPerLine())
        else:
            m_buf = None
            m_ptr = ctypes.POINTER(ctypes.c_uint8)()
            m_stride = 0
        rc = lib.cutora_rust_bgr_resize_crop_premask_qimg(
            s_ptr, int(sw), int(sh), int(src_bgr.strides[0]),
            m_ptr, m_stride,
            d_ptr, dw_i, dh_i, int(baked.bytesPerLine()),
            fw_i, fh_i, int(off_x), int(off_y),
        )
        del d_buf, m_buf
        if rc == 0:
            if has_mask:
                baked._is_premasked_cell = True
            return baked
    except Exception:
        pass
    return None


