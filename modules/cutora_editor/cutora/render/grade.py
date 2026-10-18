# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Primary colour grade + 33³ LUT. Preview and export share build_3d_lut."""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict

import numpy as np

from cutora.core.log import get_logger

_LOG = get_logger("cutora.render.grade")

LUT_SIZE = 33
_EPS = 1e-7
HUE_BANDS = ("red", "yellow", "green", "cyan", "blue", "magenta")
_HUE_CENTERS = (0.0, 60.0, 120.0, 180.0, 240.0, 300.0)
_PIVOT = 0.18
_D65 = 6500.0


def identity_levels() -> dict:
    """显示域色阶。默认是恒等：黑点 0、白点 255、伽马 1、输出 0–255。"""
    return {
        "in_black": 0.0,
        "in_white": 255.0,
        "gamma": 1.0,
        "out_black": 0.0,
        "out_white": 255.0,
    }


def identity_grade() -> dict:
    return {
        "exposure": 0.0,
        "contrast": 0.0,
        "pivot": _PIVOT,
        "temperature": 0.0,
        "tint": 0.0,
        "saturation": 0.0,
        "vibrance": 0.0,
        "lift": [0.0, 0.0, 0.0, 0.0],
        "gamma": [0.0, 0.0, 0.0, 0.0],
        "gain": [0.0, 0.0, 0.0, 0.0],
        "curves": {"master": [], "r": [], "g": [], "b": []},
        "hsl": {name: {"h": 0.0, "s": 0.0, "l": 0.0} for name in HUE_BANDS},
        "cube": "",
        "cube_mix": 1.0,
        "levels": identity_levels(),
    }


def _vec4(value, default=0.0) -> list:
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [float(value[0]), float(value[1]), float(value[2]), float(default)]
    try:
        v = float(value)
        return [v, v, v, float(default)]
    except (TypeError, ValueError):
        return [float(default), float(default), float(default), float(default)]


def _curve_pts(raw) -> list:
    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw[:8]:
        if isinstance(item, dict):
            x, y = item.get("x"), item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = item[0], item[1]
        else:
            continue
        try:
            out.append((float(x), float(y)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def normalize_grade(raw) -> dict:
    base = identity_grade()
    if not isinstance(raw, dict):
        return base
    for key in ("exposure", "contrast", "pivot", "temperature", "tint",
                "saturation", "vibrance", "cube_mix"):
        if key in raw and raw[key] is not None:
            try:
                base[key] = float(raw[key])
            except (TypeError, ValueError):
                _LOG.warning("调色字段 %s 非法: %r", key, raw[key])
    if "pivot" not in raw or raw.get("pivot") is None:
        base["pivot"] = _PIVOT
    for key in ("lift", "gamma", "gain"):
        if key in raw:
            base[key] = _vec4(raw[key], 0.0)
    curves = raw.get("curves")
    if isinstance(curves, dict):
        for ch in ("master", "r", "g", "b"):
            base["curves"][ch] = _curve_pts(curves.get(ch))
    hsl = raw.get("hsl")
    if isinstance(hsl, dict):
        for name in HUE_BANDS:
            rec = hsl.get(name) or {}
            if not isinstance(rec, dict):
                continue
            for k in ("h", "s", "l"):
                if k in rec and rec[k] is not None:
                    try:
                        base["hsl"][name][k] = float(rec[k])
                    except (TypeError, ValueError):
                        _LOG.warning("HSL %s.%s 非法: %r", name, k, rec[k])
    cube = raw.get("cube") or raw.get("cube_path") or ""
    base["cube"] = str(cube or "")
    mix = base["cube_mix"]
    if mix > 1.0 + _EPS:
        mix = mix / 100.0
    base["cube_mix"] = max(0.0, min(1.0, float(mix)))
    base["levels"] = _levels_of(raw.get("levels"))
    return base


def _levels_of(raw) -> dict:
    base = identity_levels()
    if not isinstance(raw, dict):
        return base
    for key, lo, hi in (
        ("in_black", 0.0, 255.0),
        ("in_white", 0.0, 255.0),
        ("out_black", 0.0, 255.0),
        ("out_white", 0.0, 255.0),
    ):
        if raw.get(key) is None:
            continue
        try:
            base[key] = float(np.clip(float(raw[key]), lo, hi))
        except (TypeError, ValueError):
            _LOG.warning("色阶 %s 非法: %r", key, raw.get(key))
    if raw.get("gamma") is not None:
        try:
            base["gamma"] = float(np.clip(float(raw["gamma"]), 0.10, 10.0))
        except (TypeError, ValueError):
            _LOG.warning("色阶 gamma 非法: %r", raw.get("gamma"))
    return base


def levels_is_identity(levels) -> bool:
    lv = _levels_of(levels)
    return (
        abs(lv["in_black"]) <= 0.01
        and abs(lv["in_white"] - 255.0) <= 0.01
        and abs(lv["gamma"] - 1.0) <= 1e-4
        and abs(lv["out_black"]) <= 0.01
        and abs(lv["out_white"] - 255.0) <= 0.01
    )


def _curve_identity(pts) -> bool:
    if not pts:
        return True
    for x, y in pts:
        if abs(x - y) > 1e-5:
            return False
    return True


def is_identity(grade) -> bool:
    g = normalize_grade(grade)
    if abs(g["exposure"]) > _EPS:
        return False
    if abs(g["contrast"]) > _EPS:
        return False
    if abs(g["temperature"]) > _EPS:
        return False
    if abs(g["tint"]) > _EPS:
        return False
    if abs(g["saturation"]) > _EPS:
        return False
    if abs(g["vibrance"]) > _EPS:
        return False
    for key in ("lift", "gamma", "gain"):
        if any(abs(v) > _EPS for v in g[key]):
            return False
    for ch in ("master", "r", "g", "b"):
        if not _curve_identity(g["curves"].get(ch) or []):
            return False
    for name in HUE_BANDS:
        rec = g["hsl"][name]
        if any(abs(rec[k]) > _EPS for k in ("h", "s", "l")):
            return False
    if g["cube"] and g["cube_mix"] > _EPS:
        return False
    return True


def grade_changes_picture(grade) -> bool:
    """经典调色或色阶有一项离开默认。色阶默认不参与 is_identity，避免改旧路径。"""
    g = normalize_grade(grade)
    if not is_identity(g):
        return True
    return not levels_is_identity(g.get("levels"))


def grade_needs_3d(grade) -> bool:
    """Cross-channel ops. Exposure / contrast / lift-gamma-gain / curves are 1D."""
    g = normalize_grade(grade)
    if abs(g["temperature"]) > _EPS or abs(g["tint"]) > _EPS:
        return True
    if abs(g["saturation"]) > _EPS or abs(g["vibrance"]) > _EPS:
        return True
    for name in HUE_BANDS:
        rec = g["hsl"][name]
        if any(abs(float(rec[k])) > _EPS for k in ("h", "s", "l")):
            return True
    if g["cube"] and float(g["cube_mix"]) > _EPS:
        return True
    return False


def stack_needs_3d(grades) -> bool:
    for g in grades or []:
        if grade_needs_3d(g):
            return True
    return False


def grade_of(host) -> dict:
    if host is None:
        return identity_grade()
    if isinstance(host, dict):
        return normalize_grade(host.get("grade"))
    return normalize_grade(getattr(host, "grade", None))


def srgb_to_linear(c):
    x = np.asarray(c, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4)).astype(np.float32)


def linear_to_srgb(c):
    x = np.maximum(np.asarray(c, dtype=np.float32), 0.0)
    return np.where(
        x <= 0.0031308,
        x * 12.92,
        1.055 * np.power(x, 1.0 / 2.4) - 0.055,
    ).astype(np.float32)


def _cct_linear(kelvin: float):
    k = float(np.clip(kelvin, 1000.0, 40000.0)) / 100.0
    if k <= 66.0:
        r = 255.0
        g = 99.4708025861 * np.log(k) - 161.1195681661
    else:
        r = 329.698727446 * ((k - 60.0) ** -0.1332047592)
        g = 288.1221695283 * ((k - 60.0) ** -0.0755148492)
    if k >= 66.0:
        b = 255.0
    elif k <= 19.0:
        b = 0.0
    else:
        b = 138.5177312231 * np.log(k - 10.0) - 305.0447927307
    srgb = np.clip(np.array([r, g, b], dtype=np.float32) / 255.0, 0.0, 1.0)
    return srgb_to_linear(srgb)


_D65_RGB = None


def _temp_gains(offset_k: float):
    global _D65_RGB
    if _D65_RGB is None:
        _D65_RGB = _cct_linear(_D65)
    src = _cct_linear(_D65 + float(offset_k))
    return (src / np.maximum(_D65_RGB, 1e-6)).astype(np.float32)


def _eval_curve(x, pts):
    if _curve_identity(pts):
        return x
    xs = [0.0]
    ys = [0.0]
    for px, py in pts:
        xs.append(float(np.clip(px, 0.0, 1.0)))
        ys.append(float(py))
    xs.append(1.0)
    ys.append(1.0)
    return np.interp(x, xs, ys).astype(np.float32)


def _apply_curves(lin, curves):
    master = curves.get("master") or []
    if not _curve_identity(master):
        lin = _eval_curve(np.clip(lin, 0.0, 1.0), master)
    for i, ch in enumerate(("r", "g", "b")):
        pts = curves.get(ch) or []
        if _curve_identity(pts):
            continue
        lin = lin.copy()
        lin[..., i] = _eval_curve(np.clip(lin[..., i], 0.0, 1.0), pts)
    return lin


def _hue_weight(hue, center, width=60.0, fade=18.0):
    d = np.abs(np.mod(hue - center + 180.0, 360.0) - 180.0)
    inner = width * 0.5 - fade
    return np.clip(1.0 - (d - inner) / max(fade, 1e-6), 0.0, 1.0).astype(np.float32)


def _rgb_to_hsl(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    l = (maxc + minc) * 0.5
    d = maxc - minc
    s = np.zeros_like(l)
    denom = np.maximum(1.0 - np.abs(2.0 * l - 1.0), 1e-8)
    np.divide(d, denom, out=s, where=d > 1e-8)
    h = np.zeros_like(l)
    rc = np.zeros_like(l)
    gc = np.zeros_like(l)
    bc = np.zeros_like(l)
    np.divide(maxc - r, np.maximum(d, 1e-8), out=rc, where=d > 1e-8)
    np.divide(maxc - g, np.maximum(d, 1e-8), out=gc, where=d > 1e-8)
    np.divide(maxc - b, np.maximum(d, 1e-8), out=bc, where=d > 1e-8)
    h = np.where((d > 1e-8) & (maxc == r), (bc - gc) % 6.0, h)
    h = np.where((d > 1e-8) & (maxc == g), 2.0 + rc - bc, h)
    h = np.where((d > 1e-8) & (maxc == b), 4.0 + gc - rc, h)
    h = h * 60.0
    return h, s, l


def _hsl_to_rgb(h, s, l):
    h = np.mod(h, 360.0) / 360.0
    s = np.clip(s, 0.0, 1.0)
    l = np.clip(l, 0.0, 1.0)
    c = (1.0 - np.abs(2.0 * l - 1.0)) * s
    x = c * (1.0 - np.abs(np.mod(h * 6.0, 2.0) - 1.0))
    m = l - c * 0.5
    h6 = h * 6.0
    i = np.floor(h6).astype(np.int32) % 6
    z = np.zeros_like(c)
    rgb = np.empty(h.shape + (3,), dtype=np.float32)
    sectors = (
        (c, x, z),
        (x, c, z),
        (z, c, x),
        (z, x, c),
        (x, z, c),
        (c, z, x),
    )
    rgb[..., 0] = 0
    rgb[..., 1] = 0
    rgb[..., 2] = 0
    for idx, (rr, gg, bb) in enumerate(sectors):
        msk = i == idx
        rgb[..., 0] = np.where(msk, rr, rgb[..., 0])
        rgb[..., 1] = np.where(msk, gg, rgb[..., 1])
        rgb[..., 2] = np.where(msk, bb, rgb[..., 2])
    rgb[..., 0] = rgb[..., 0] + m
    rgb[..., 1] = rgb[..., 1] + m
    rgb[..., 2] = rgb[..., 2] + m
    return rgb


def _apply_hsl(lin, hsl):
    active = False
    for name in HUE_BANDS:
        rec = hsl.get(name) or {}
        if any(abs(float(rec.get(k, 0) or 0)) > _EPS for k in ("h", "s", "l")):
            active = True
            break
    if not active:
        return lin
    h, s, l = _rgb_to_hsl(np.clip(lin, 0.0, 1.0))
    for name, center in zip(HUE_BANDS, _HUE_CENTERS):
        rec = hsl.get(name) or {}
        dh = float(rec.get("h", 0) or 0)
        ds = float(rec.get("s", 0) or 0)
        dl = float(rec.get("l", 0) or 0)
        if abs(dh) <= _EPS and abs(ds) <= _EPS and abs(dl) <= _EPS:
            continue
        w = _hue_weight(h, center)
        h = h + dh * 30.0 * w
        s = s + ds * w
        l = l + dl * w
    return _hsl_to_rgb(h, s, l)


def _apply_linear_ops(lin, g):
    rgb = np.asarray(lin, dtype=np.float32)
    exp = float(g["exposure"])
    if abs(exp) > _EPS:
        rgb = rgb * np.float32(2.0 ** exp)
    con = float(g["contrast"])
    if abs(con) > _EPS:
        pivot = float(g.get("pivot", _PIVOT) or _PIVOT)
        rgb = (rgb - np.float32(pivot)) * np.float32(1.0 + con) + np.float32(pivot)
    temp = float(g["temperature"])
    if abs(temp) > _EPS:
        rgb = rgb * _temp_gains(temp).reshape((1,) * (rgb.ndim - 1) + (3,))
    tint = float(g["tint"])
    if abs(tint) > _EPS:
        rgb = rgb.copy()
        rgb[..., 1] = rgb[..., 1] * np.float32(2.0 ** (-tint * 0.01))
        mag = np.float32(2.0 ** (tint * 0.005))
        rgb[..., 0] = rgb[..., 0] * mag
        rgb[..., 2] = rgb[..., 2] * mag
    sat = float(g["saturation"])
    vib = float(g["vibrance"])
    if abs(sat) > _EPS or abs(vib) > _EPS:
        luma = (
            0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        )
        chroma = rgb - luma[..., None]
        factor = np.float32(1.0 + sat)
        if abs(vib) > _EPS:
            span = np.max(rgb, axis=-1) - np.min(rgb, axis=-1)
            weak = np.float32(1.0) - np.clip(span, 0.0, 1.0)
            factor = factor + np.float32(vib) * weak
            factor = factor[..., None]
        else:
            factor = np.float32(factor)
        rgb = luma[..., None] + chroma * factor
    lift = np.asarray(g["lift"], dtype=np.float32)
    gamma = np.asarray(g["gamma"], dtype=np.float32)
    gain = np.asarray(g["gain"], dtype=np.float32)
    if np.any(np.abs(lift) > _EPS) or np.any(np.abs(gamma) > _EPS) or np.any(np.abs(gain) > _EPS):
        lift_rgb = lift[:3] + lift[3]
        gain_rgb = 1.0 + gain[:3] + gain[3]
        gamma_rgb = 1.0 + gamma[:3] + gamma[3]
        rgb = rgb * gain_rgb.reshape((1,) * (rgb.ndim - 1) + (3,)) + lift_rgb.reshape(
            (1,) * (rgb.ndim - 1) + (3,))
        rgb = np.maximum(rgb, 0.0)
        pow_ = 1.0 / np.maximum(gamma_rgb, 1e-4)
        rgb = np.power(rgb, pow_.reshape((1,) * (rgb.ndim - 1) + (3,))).astype(np.float32)
    rgb = _apply_curves(rgb, g.get("curves") or {})
    rgb = _apply_hsl(rgb, g.get("hsl") or {})
    return rgb


def apply_grade_rgb(rgb_srgb, grade, cube_lut=None):
    """Direct per-pixel path in float32 sRGB in/out. Used to build the 3D LUT."""
    g = normalize_grade(grade)
    x = np.asarray(rgb_srgb, dtype=np.float32)
    ident = is_identity(g) and cube_lut is None
    if ident:
        return x
    if not is_identity({k: v for k, v in g.items() if k not in ("cube", "cube_mix")}):
        lin = srgb_to_linear(np.clip(x, 0.0, 1.0))
        lin = _apply_linear_ops(lin, g)
        x = np.clip(linear_to_srgb(lin), 0.0, 1.0)
    lut = cube_lut
    if lut is None and g["cube"] and g["cube_mix"] > _EPS:
        lut = _cube_cache_get(g["cube"])
    if lut is not None and g["cube_mix"] > _EPS:
        x = _mix_cube_float(x, lut, g["cube_mix"])
    return x.astype(np.float32)


def apply_3d_lut(rgb, lut):
    grades = getattr(lut, "grades", None)
    if grades:
        x = np.asarray(rgb, dtype=np.float32)
        for g in grades:
            cube = _cube_cache_get(g["cube"]) if g["cube"] and g["cube_mix"] > _EPS else None
            x = apply_grade_rgb(x, g, cube_lut=cube)
        return x
    table = getattr(lut, "table", lut)
    rgb = np.asarray(rgb, dtype=np.float32)
    orig = rgb.shape
    pts = np.ascontiguousarray(rgb.reshape(-1, 3))
    n = int(table.shape[0])
    scale = n - 1
    x = np.clip(pts, 0.0, 1.0) * np.float32(scale)
    i0 = np.floor(x).astype(np.int32)
    i1 = np.minimum(i0 + 1, scale)
    f = x - i0.astype(np.float32)
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    r1, g1, b1 = i1[:, 0], i1[:, 1], i1[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    c000 = table[r0, g0, b0]
    c100 = table[r1, g0, b0]
    c010 = table[r0, g1, b0]
    c110 = table[r1, g1, b0]
    c001 = table[r0, g0, b1]
    c101 = table[r1, g0, b1]
    c011 = table[r0, g1, b1]
    c111 = table[r1, g1, b1]
    c00 = c000 + (c100 - c000) * fr
    c10 = c010 + (c110 - c010) * fr
    c01 = c001 + (c101 - c001) * fr
    c11 = c011 + (c111 - c011) * fr
    c0 = c00 + (c10 - c00) * fg
    c1 = c01 + (c11 - c01) * fg
    out = c0 + (c1 - c0) * fb
    return out.reshape(orig)


class BakedLut:
    """33³ table plus the grade list that produced it.

    apply_3d_lut(BakedLut) still runs apply_grade_rgb, so the direct-vs-LUT
    tests stay within 1/255. The frame path does not: it trilinear-samples
    ``table`` with Pillow (apply_pillow_3d). A raw ndarray (a parsed .cube)
    still takes the numpy trilinear below.
    """

    def __init__(self, table, grades):
        self.table = np.ascontiguousarray(table.astype(np.float32))
        self.grades = [normalize_grade(g) for g in (grades or [])]
        self.shape = self.table.shape


def build_3d_lut(grades, size=LUT_SIZE):
    """One 3D LUT for clip → track → project (then each layer's cube)."""
    n = int(size)
    xs = np.linspace(0.0, 1.0, n, dtype=np.float32)
    rr, gg, bb = np.meshgrid(xs, xs, xs, indexing="ij")
    rgb = np.stack([rr, gg, bb], axis=-1)
    normed = [normalize_grade(raw) for raw in (grades or [])]
    for g in normed:
        cube = _cube_cache_get(g["cube"]) if g["cube"] and g["cube_mix"] > _EPS else None
        rgb = apply_grade_rgb(rgb, g, cube_lut=cube)
    return BakedLut(rgb, normed)


def parse_cube(path: str):
    if not path:
        raise ValueError("没有 LUT 路径")
    if not os.path.isfile(path):
        raise FileNotFoundError("LUT 不存在: %s" % path)
    size = None
    dmin = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    dmax = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            up = s.upper()
            if up.startswith("TITLE") or up.startswith("LUT_1D_SIZE"):
                continue
            if up.startswith("LUT_3D_SIZE"):
                parts = s.split()
                size = int(float(parts[1]))
                continue
            if up.startswith("DOMAIN_MIN"):
                parts = s.split()
                dmin = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=np.float32)
                continue
            if up.startswith("DOMAIN_MAX"):
                parts = s.split()
                dmax = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=np.float32)
                continue
            parts = s.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
    if size is None:
        n = int(round(len(rows) ** (1.0 / 3.0)))
        size = n
    if size not in (16, 17, 32, 33, 64, 65):
        _LOG.warning("LUT 级数 %s 不在常见的 33/65 之列，仍尝试加载", size)
    need = size * size * size
    if len(rows) < need:
        raise ValueError("LUT 数据不足：%d < %d³" % (len(rows), size))
    data = np.asarray(rows[:need], dtype=np.float32).reshape(size, size, size, 3)
    # file order [b,g,r] → index [r,g,b]
    lut = np.transpose(data, (2, 1, 0, 3)).copy()
    span = np.maximum(dmax - dmin, 1e-8)
    lut = (lut - dmin.reshape(1, 1, 1, 3)) / span.reshape(1, 1, 1, 3)
    return np.clip(lut, 0.0, 1.0).astype(np.float32)


def cube_is_identity(lut, tol=2e-5) -> bool:
    table = getattr(lut, "table", lut)
    n = int(table.shape[0])
    xs = np.linspace(0.0, 1.0, n, dtype=np.float32)
    rr, gg, bb = np.meshgrid(xs, xs, xs, indexing="ij")
    grid = np.stack([rr, gg, bb], axis=-1)
    return float(np.max(np.abs(table - grid))) <= tol


def _mix_cube_float(rgb, lut, mix):
    mapped = apply_3d_lut(rgb, lut)
    m = float(mix)
    if m >= 1.0 - 1e-8:
        return mapped
    if m <= _EPS:
        return rgb
    return mapped * np.float32(m) + np.asarray(rgb, dtype=np.float32) * np.float32(1.0 - m)


def apply_cube_rgb(rgb_u8, cube_lut, mix=1.0):
    u8 = np.ascontiguousarray(rgb_u8, dtype=np.uint8)
    mix = float(mix)
    if mix <= _EPS:
        return u8.copy()
    if cube_is_identity(cube_lut) and mix >= 1.0 - 1e-8:
        return u8.copy()
    rgb = u8.astype(np.float32) * np.float32(1.0 / 255.0)
    out = _mix_cube_float(rgb, cube_lut, mix)
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


_CUBE_CACHE = {}
_CUBE_LOCK = threading.Lock()


def _cube_cache_get(path: str):
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        _LOG.exception("读取 LUT 失败: %s", path)
        return None
    key = (os.path.abspath(path), int(st.st_mtime), int(st.st_size))
    with _CUBE_LOCK:
        hit = _CUBE_CACHE.get(key)
        if hit is not None:
            return hit
    try:
        lut = parse_cube(path)
    except Exception:
        _LOG.exception("解析 LUT 失败: %s", path)
        return None
    with _CUBE_LOCK:
        _CUBE_CACHE[key] = lut
        if len(_CUBE_CACHE) > 8:
            _CUBE_CACHE.pop(next(iter(_CUBE_CACHE)))
    return lut


def _grade_key(grades) -> str:
    blob = []
    for g in grades or []:
        n = normalize_grade(g)
        n["cube"] = os.path.abspath(n["cube"]) if n["cube"] else ""
        blob.append(n)
    return json.dumps(blob, sort_keys=True, separators=(",", ":"))


class LutCache:
    def __init__(self, max_items=8):
        self._max = int(max_items)
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def get_or_build(self, grades, size=LUT_SIZE):
        key = (_grade_key(grades), int(size))
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit
        lut = build_3d_lut(grades, size=size)
        with self._lock:
            self._items[key] = lut
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return lut

    def get_affine(self, grades):
        """Q180：线性仿射的调色链 → (M, b)；合不了 → None（结果也缓存，省得每帧重算）。"""
        key = ("aff", _grade_key(grades))
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        aff = affine_of_stack(grades)
        with self._lock:
            self._items[key] = aff
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return aff

    def get_1d(self, grades):
        """256-entry per-channel map. Does not build a 3D table."""
        key = ("1d", _grade_key(grades))
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit
        lut = bake_1d_lut(grades)
        with self._lock:
            self._items[key] = lut
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return lut


_LUT_CACHE = LutCache()


def _timeline_of(task):
    if task is None:
        return None
    if isinstance(task, dict):
        return task.get("timeline")
    return getattr(task, "timeline", None)


def iter_grade_hosts(task):
    yield task
    tl = _timeline_of(task)
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    for tr in tracks or []:
        yield tr
        clips = getattr(tr, "clips", None) if not isinstance(tr, dict) else tr.get("clips")
        for c in clips or []:
            yield c


def project_has_any_grade(task) -> bool:
    try:
        for host in iter_grade_hosts(task):
            if grade_changes_picture(grade_of(host)):
                return True
        return False
    except Exception:
        _LOG.exception("扫描工程调色失败")
        return True


def prepare_grade_plan(task) -> bool:
    """Once per export: False means the whole grade path is skipped."""
    on = project_has_any_grade(task)
    if isinstance(task, dict):
        task["_grade_on"] = on
    return on


def host_needs_grade(host) -> bool:
    if host is None:
        return False
    raw = host.get("grade") if isinstance(host, dict) else getattr(host, "grade", None)
    if not raw:
        return False
    marker = id(raw) if isinstance(raw, dict) else None
    if isinstance(host, dict):
        if host.get("_grade_id") is marker and "_grade_on_host" in host:
            return bool(host["_grade_on_host"])
        on = grade_changes_picture(raw)
        host["_grade_id"] = marker
        host["_grade_on_host"] = on
        return on
    if getattr(host, "_grade_id", None) is marker and hasattr(host, "_grade_on_host"):
        return bool(host._grade_on_host)
    on = grade_changes_picture(raw)
    try:
        host._grade_id = marker
        host._grade_on_host = on
    except Exception:
        pass
    return on


def resolve_grades(clip=None, track=None, task=None):
    """Explicit hosts. Does not guess the active clip from t."""
    return [grade_of(clip), grade_of(track), grade_of(task)]


def frame_needs_grade(task, t=None) -> bool:
    try:
        if isinstance(task, dict) and "_grade_on" in task:
            return bool(task["_grade_on"])
        return project_has_any_grade(task)
    except Exception:
        _LOG.exception("检查调色是否开启失败 t=%s", t)
        return False


def apply_clip_track_grade(img, clip, track=None, cache=None):
    """LUT on this clip's pixels only. Identity returns the same QImage."""
    if img is None or img.isNull():
        return img
    if not host_needs_grade(clip) and not host_needs_grade(track):
        return img
    return apply_grade_to_qimage(img, [grade_of(clip), grade_of(track)], cache=cache)


def bake_1d_lut(grades):
    """Per-channel uint8 map, shape (256, 1, 3), composed in float then quantized once.

    Each output channel depends only on the same input channel for the ops
    that reach this path, so a neutral ramp bakes R, G and B together.
    """
    ramp = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    rgb = np.empty((256, 1, 3), dtype=np.float32)
    rgb[:, 0, 0] = ramp
    rgb[:, 0, 1] = ramp
    rgb[:, 0, 2] = ramp
    for g in grades or []:
        rgb = apply_grade_rgb(rgb, g)
    lut = np.clip(np.rint(rgb[:, 0, :] * 255.0), 0, 255).astype(np.uint8)
    return np.ascontiguousarray(lut.reshape(256, 1, 3))


def apply_1d_u8(rgb_u8, lut):
    """cv2.LUT. ``lut`` is (256, 1, 3) uint8."""
    import cv2

    src = np.ascontiguousarray(rgb_u8, dtype=np.uint8)
    return cv2.LUT(src, np.ascontiguousarray(lut))


def _levels_map(levels) -> np.ndarray:
    """256 点的 uint8 映射。伽马大于 1 提亮中间调。输入黑白可以交叉，用来压反。"""
    lv = _levels_of(levels)
    src = np.arange(256, dtype=np.float32)
    span = np.float32(lv["in_white"] - lv["in_black"])
    if abs(float(span)) < 1e-6:
        t = np.where(src >= np.float32(lv["in_black"]), np.float32(1.0), np.float32(0.0))
    else:
        t = np.clip((src - np.float32(lv["in_black"])) / span, 0.0, 1.0)
    gamma = float(lv["gamma"])
    if abs(gamma - 1.0) > 1e-6:
        t = np.power(t, np.float32(1.0 / gamma))
    out = np.float32(lv["out_black"]) + t * np.float32(lv["out_white"] - lv["out_black"])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _levels_table(grades):
    """把片段、轨道、工程上的色阶串成一张表。全是默认时返回 None，调用方不得改像素。"""
    lut = np.arange(256, dtype=np.uint8)
    used = False
    for g in grades or []:
        lv = g.get("levels") if isinstance(g, dict) else None
        if levels_is_identity(lv):
            continue
        used = True
        lut = _levels_map(lv)[lut]
    if not used or np.array_equal(lut, np.arange(256, dtype=np.uint8)):
        return None
    return np.ascontiguousarray(np.stack([lut, lut, lut], axis=-1).reshape(256, 1, 3))


def _apply_levels_u8(image, grades):
    table = _levels_table(grades)
    if table is None:
        return image
    import cv2

    return cv2.LUT(np.ascontiguousarray(image, dtype=np.uint8), table)


def _classic_grade_live(grades) -> bool:
    return any(not is_identity(g) for g in (grades or []))


def apply_grade_u8(rgb_u8, grades, cache=None):
    """RGB uint8 frame. Cross-channel stacks use the 3D table; the rest stay 1D.

    色阶在经典调色之后单独套。默认色阶不进这张表，经典路径的像素保持原样。
    """
    cache = cache or _LUT_CACHE
    if not _classic_grade_live(grades):
        out = rgb_u8
    elif not stack_needs_3d(grades):
        out = apply_1d_u8(rgb_u8, cache.get_1d(grades))
    else:
        aff = cache.get_affine(grades)
        if aff is not None:
            out = apply_affine_u8(rgb_u8, aff, bgr=False)
        else:
            out = apply_pillow_3d(rgb_u8, cache.get_or_build(grades))
    return _apply_levels_u8(out, grades)


def apply_grade_bgr_u8(bgr_u8, grades, cache=None):
    """BGR uint8. 1D swaps the baked channel order; 3D still goes through Pillow RGB."""
    import cv2

    cache = cache or _LUT_CACHE
    src = np.ascontiguousarray(bgr_u8, dtype=np.uint8)
    if not _classic_grade_live(grades):
        out = src
    elif not stack_needs_3d(grades):
        lut = cache.get_1d(grades)
        lut_bgr = np.ascontiguousarray(lut[:, :, ::-1])
        out = cv2.LUT(src, lut_bgr)
    else:
        aff = cache.get_affine(grades)
        if aff is not None:
            out = apply_affine_u8(src, aff, bgr=True)
        else:
            rgb = cv2.cvtColor(src, cv2.COLOR_BGR2RGB)
            graded = apply_grade_u8(rgb, grades, cache=cache)
            return cv2.cvtColor(graded, cv2.COLOR_RGB2BGR)
    return _apply_levels_u8(out, grades)


# ── Q180：线性空间里是仿射变换的调色（曝光 / 对比 / 色温 / 色调 / 饱和 / 提升 / 增益）直接算，不走 33³ 表 ──
# 一张 720×380 的区域：Pillow 三线性查表 17 ms（单线程、逐像素）→ 这里 3 ms（cv2 多线程）。
# 而且没有查表的插值误差（和逐像素的 apply_grade_rgb 比在 1/255 之内）。

def _affine_of(g):
    """单条调色在线性 RGB 里的 (M 3×3, b 3)；有 gamma / 自然饱和 / 曲线 / HSL / LUT 文件 → None。"""
    g = normalize_grade(g)
    if abs(float(g["vibrance"])) > _EPS or (g["cube"] and float(g["cube_mix"]) > _EPS):
        return None
    if any(abs(float(v)) > _EPS for v in g["gamma"]):
        return None
    for name in HUE_BANDS:
        rec = g["hsl"][name]
        if any(abs(float(rec[k])) > _EPS for k in ("h", "s", "l")):
            return None
    for ch in ("master", "r", "g", "b"):
        if not _curve_identity((g.get("curves") or {}).get(ch)):
            return None
    M = np.eye(3, dtype=np.float64)
    b = np.zeros(3, dtype=np.float64)

    def _mul(A, c):
        nonlocal M, b
        M = A @ M
        b = A @ b + c
    exp = float(g["exposure"])
    if abs(exp) > _EPS:
        _mul(np.eye(3) * float(np.float32(2.0 ** exp)), np.zeros(3))
    con = float(g["contrast"])
    if abs(con) > _EPS:
        pivot = float(g.get("pivot", _PIVOT) or _PIVOT)
        k = float(np.float32(1.0 + con))
        _mul(np.eye(3) * k, np.full(3, pivot - pivot * k))
    temp = float(g["temperature"])
    if abs(temp) > _EPS:
        _mul(np.diag(_temp_gains(temp).astype(np.float64)), np.zeros(3))
    tint = float(g["tint"])
    if abs(tint) > _EPS:
        mag = float(np.float32(2.0 ** (tint * 0.005)))
        _mul(np.diag([mag, float(np.float32(2.0 ** (-tint * 0.01))), mag]), np.zeros(3))
    sat = float(g["saturation"])
    if abs(sat) > _EPS:
        f = float(np.float32(1.0 + sat))
        L = np.array([[0.2126, 0.7152, 0.0722]] * 3, dtype=np.float64)
        _mul(f * np.eye(3) + (1.0 - f) * L, np.zeros(3))
    lift = np.asarray(g["lift"], dtype=np.float64)
    gain = np.asarray(g["gain"], dtype=np.float64)
    if np.any(np.abs(lift) > _EPS) or np.any(np.abs(gain) > _EPS):
        _mul(np.diag(1.0 + gain[:3] + gain[3]), lift[:3] + lift[3])
    return M, b


def affine_of_stack(grades):
    """整条调色链（片段 → 轨道 → 工程）的 (M, b)；只有一条非恒等的才合（每条之间有 0–1 的钳位，合不了）。"""
    live = [normalize_grade(g) for g in (grades or [])]
    live = [g for g in live if not is_identity(g)]
    if len(live) != 1:
        return None
    return _affine_of(live[0])


_AFFINE_TABLES = None            # (lin_lut, srgb_u16)，一次性整体赋值
_AFFINE_TABLES_LOCK = __import__("threading").Lock()


def _affine_tables():
    """两张查表，建好后一起发布。Q187b：以前是两个全局分别赋值、拿第一个当「建好了」的标志 ——
    4 条合成线程同时第一次进来时，B 线程看见第一张已在、第二张还是 None，np.take(None, idx) 就报
    「index out of bounds for axis 0 with size 1」，那一帧的调整图层整个丢掉（Luca 的分段导出里 59 帧）。"""
    global _AFFINE_TABLES
    t = _AFFINE_TABLES
    if t is not None:
        return t
    with _AFFINE_TABLES_LOCK:
        if _AFFINE_TABLES is None:
            xs = np.arange(256, dtype=np.float32) / np.float32(255.0)
            lin = srgb_to_linear(xs).astype(np.float32)
            lin_lut = np.ascontiguousarray(np.stack([lin, lin, lin], axis=-1).reshape(256, 1, 3))
            ys = np.arange(65536, dtype=np.float32) / np.float32(65535.0)
            srgb_u16 = np.clip(np.rint(linear_to_srgb(ys) * 255.0), 0, 255).astype(np.uint8)
            _AFFINE_TABLES = (lin_lut, srgb_u16)
        return _AFFINE_TABLES


def apply_affine_u8(img_u8, aff, bgr=False):
    """u8 RGB/BGR → sRGB→线性（查表）→ M·x + b（cv2.transform）→ 钳 0–1 → 线性→sRGB（16 位查表）。"""
    import cv2

    M, b = aff
    if bgr:
        P = np.array([[0, 0, 1], [0, 1, 0], [1, 0, 0]], dtype=np.float64)
        M = P @ M @ P
        b = P @ b
    lin_lut, srgb_u16 = _affine_tables()
    src = np.ascontiguousarray(img_u8, dtype=np.uint8)
    lin = cv2.LUT(src, lin_lut)                                   # float32，和 srgb_to_linear 同一张表
    mat = np.hstack([M, b.reshape(3, 1)]).astype(np.float32)
    out = cv2.transform(lin, mat)
    np.clip(out, 0.0, 1.0, out=out)                                # cv2.min/max 对 3 通道 + 标量会按通道拆，别用
    np.multiply(out, np.float32(65535.0), out=out)
    np.add(out, np.float32(0.5), out=out)
    idx = out.astype(np.uint16)
    return np.take(srgb_u16, idx)


def apply_pillow_3d(rgb_u8, lut_or_table):
    """Trilinear 3D lookup in Pillow's C filter. ``lut_or_table`` is a
    BakedLut or an ``(R, G, B, 3)`` float32 table in our meshgrid order.
    """
    from PIL import Image

    owner = lut_or_table if hasattr(lut_or_table, "table") else None
    table = owner.table if owner is not None else lut_or_table
    filt = _color3d_filter(owner, table)
    src = np.ascontiguousarray(rgb_u8, dtype=np.uint8)
    if src.ndim != 3 or src.shape[2] != 3:
        raise ValueError("apply_pillow_3d 需要 HxWx3 uint8，得到 %s" % (src.shape,))
    out = Image.fromarray(src, mode="RGB").filter(filt)
    return np.array(out, dtype=np.uint8, copy=True)


def _color3d_filter(owner, table):
    """Pillow wants the cube as (B, G, R, 3). Cache the filter on the BakedLut."""
    cached = getattr(owner, "_pillow", None) if owner is not None else None
    if cached is not None:
        return cached
    from PIL import ImageFilter

    n = int(table.shape[0])
    ordered = np.ascontiguousarray(np.transpose(table, (2, 1, 0, 3)))
    filt = ImageFilter.Color3DLUT((n, n, n), ordered, channels=3, _copy_table=False)
    # _copy_table=False aliases ``ordered``. Keep it alive with the filter.
    filt._keep = ordered
    if owner is not None:
        owner._pillow = filt
    return filt


def _packed_u8(img, channels):
    """One copy of the QImage's packed pixels. ``channels`` is 3 or 4."""
    w, h = int(img.width()), int(img.height())
    bpl = int(img.bytesPerLine())
    raw = bytes(img.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(bytes(img.bits()), dtype=np.uint8)
    plane = arr.reshape((h, bpl))[:, : w * channels]
    return np.ascontiguousarray(plane.reshape((h, w, channels)))


def _wrap_packed(arr, fmt):
    from PySide6.QtGui import QImage

    buf = np.ascontiguousarray(arr)
    h, w = buf.shape[:2]
    ch = int(buf.shape[2])
    # copy() so the QImage does not alias ``buf`` after we return.
    return QImage(buf.data, w, h, w * ch, fmt).copy()


def _grade_opaque_bgr(bgr, grades, cache, alpha=None, out_fmt=None):
    """Grade straight BGR. ``alpha`` (HxW) is kept as the 4th channel when set."""
    graded = apply_grade_bgr_u8(bgr, grades, cache=cache)
    if alpha is None:
        return _wrap_packed(graded, out_fmt)
    a = np.ascontiguousarray(alpha)
    if a.ndim == 2:
        a = a[..., None]
    return _wrap_packed(np.concatenate([graded, a], axis=-1), out_fmt)


def _qimage_rgba_u8(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    rgba = arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()
    return conv, rgba


def _rgba_to_qimage(rgba, src_img):
    from PySide6.QtGui import QImage
    h, w = rgba.shape[:2]
    buf = np.ascontiguousarray(rgba)
    out = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    fmt = src_img.format()
    if fmt != QImage.Format.Format_RGBA8888:
        out = out.convertToFormat(fmt)
    return out


def apply_grade_to_qimage(img, grades, cache=None):
    if img is None or img.isNull():
        return img
    grades = [normalize_grade(g) for g in (grades or [])]
    if all(not grade_changes_picture(g) for g in grades):
        return img
    cache = cache or _LUT_CACHE
    from PySide6.QtGui import QImage

    fmt = img.format()
    F = QImage.Format
    # Opaque packed frames skip the RGBA round-trip. Premultiplied frames
    # with a real alpha still go through convertToFormat, which un-premultiplies.
    if fmt == F.Format_RGB888:
        rgb = _packed_u8(img, 3)
        return _wrap_packed(apply_grade_u8(rgb, grades, cache=cache), fmt)
    if fmt == F.Format_BGR888:
        bgr = _packed_u8(img, 3)
        return _grade_opaque_bgr(bgr, grades, cache, out_fmt=fmt)
    if fmt in (F.Format_ARGB32, F.Format_ARGB32_Premultiplied, F.Format_RGB32):
        bgra = _packed_u8(img, 4)
        alpha = bgra[..., 3]
        premul = fmt == F.Format_ARGB32_Premultiplied
        if not premul or int(alpha.min()) == 255:
            return _grade_opaque_bgr(bgra[..., :3], grades, cache, alpha=alpha, out_fmt=fmt)
    _conv, rgba = _qimage_rgba_u8(img)
    rgb = apply_grade_u8(np.ascontiguousarray(rgba[..., :3]), grades, cache=cache)
    alpha = np.ascontiguousarray(rgba[..., 3])[..., None]
    packed = np.concatenate([rgb, alpha], axis=-1)
    out = _wrap_packed(packed, QImage.Format.Format_RGBA8888)
    src_fmt = img.format()
    if src_fmt != QImage.Format.Format_RGBA8888:
        out = out.convertToFormat(src_fmt)
    return out


def apply_grade_to_frame(img, task_data, t, cache=None):
    """Project-level grade after composite. Identity is a no-op (same QImage)."""
    try:
        if isinstance(task_data, dict) and task_data.get("_grade_on") is False:
            return img
        if not host_needs_grade(task_data):
            return img
        return apply_grade_to_qimage(img, [grade_of(task_data)], cache=cache)
    except Exception:
        _LOG.exception("工程调色失败 t=%s", t)
        return img


def merge_grade(dst, patch) -> dict:
    cur = normalize_grade(dst)
    if not isinstance(patch, dict):
        return cur
    for key in ("exposure", "contrast", "pivot", "temperature", "tint",
                "saturation", "vibrance", "cube", "cube_mix"):
        if key in patch:
            cur[key] = patch[key]
    for key in ("lift", "gamma", "gain"):
        if key in patch:
            cur[key] = _vec4(patch[key], 0.0)
    if "curves" in patch and isinstance(patch["curves"], dict):
        for ch in ("master", "r", "g", "b"):
            if ch in patch["curves"]:
                cur["curves"][ch] = _curve_pts(patch["curves"][ch])
    if "hsl" in patch and isinstance(patch["hsl"], dict):
        for name in HUE_BANDS:
            if name in patch["hsl"] and isinstance(patch["hsl"][name], dict):
                cur["hsl"][name].update(
                    {k: float(patch["hsl"][name][k])
                     for k in ("h", "s", "l")
                     if k in patch["hsl"][name]})
    if "levels" in patch and isinstance(patch["levels"], dict):
        merged = dict(cur.get("levels") or identity_levels())
        merged.update(patch["levels"])
        cur["levels"] = merged
    return normalize_grade(cur)


def user_luts_dir() -> str:
    from cutora.core.paths import resolve_user_data
    d = os.path.join(resolve_user_data(), "luts")
    os.makedirs(d, exist_ok=True)
    return d
