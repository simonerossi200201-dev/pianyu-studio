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
