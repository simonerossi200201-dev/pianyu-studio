# -*- coding: utf-8 -*-
"""Procedural seamless textures for fx.bg.texture (T04f-2). numpy only."""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from cutora.core.paths import TEXTURE_DIR

SIZE = 1024
SEEDS = {"paper": 4, "linen": 5, "grid": 6}
# 1024/410 ≈ 2.498 px — 2–3 px @1080p (integer cycles keep the torus seamless).
LINEN_CYCLES = 410
LINEN_CONTRAST = 0.06
LINEN_SIGMA_PX = 0.32


def torus_fbm(h: int, w: int, seed: int, octaves: int = 4) -> np.ndarray:
    """Integer-frequency sinusoids → periodic on the 1024 torus."""
    rng = np.random.RandomState(seed)
    ys = np.arange(h, dtype=np.float64)[:, None] * (2.0 * np.pi / h)
    xs = np.arange(w, dtype=np.float64)[None, :] * (2.0 * np.pi / w)
    acc = np.zeros((h, w), dtype=np.float64)
    amp = 1.0
    for o in range(octaves):
        freq = 2 ** (o + 1)
        layer = np.zeros((h, w), dtype=np.float64)
        for _ in range(5):
            kx = int(rng.randint(0, freq + 1))
            ky = int(rng.randint(0, freq + 1))
            if kx == 0 and ky == 0:
                continue
            ph = rng.rand() * 2.0 * np.pi
            layer += rng.randn() * np.sin(kx * xs + ky * ys + ph)
        acc += amp * layer
        amp *= 0.5
    acc = acc.astype(np.float32)
    lo, hi = float(acc.min()), float(acc.max())
    return (acc - lo) / (hi - lo + 1e-8)


def wrap_value_noise(
    h: int, w: int, seed: int, cell_y: int, cell_x: int | None = None,
) -> np.ndarray:
    """Periodic bilinear value-noise. cell_* must divide h/w (1024: 2,4,8,…)."""
    cell_x = cell_y if cell_x is None else int(cell_x)
    cell_y = int(cell_y)
    rng = np.random.RandomState(seed)
    ny = max(1, h // max(1, cell_y))
    nx = max(1, w // max(1, cell_x))
    grid = rng.randn(ny, nx).astype(np.float32)
    ys = np.arange(h, dtype=np.float32) * (ny / float(h))
    xs = np.arange(w, dtype=np.float32) * (nx / float(w))
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    fy = ys - y0.astype(np.float32)
    fx = xs - x0.astype(np.float32)
    fy = fy * fy * (3.0 - 2.0 * fy)
    fx = fx * fx * (3.0 - 2.0 * fx)
    y0 %= ny
    x0 %= nx
    y1 = (y0 + 1) % ny
    x1 = (x0 + 1) % nx
    g00 = grid[y0[:, None], x0[None, :]]
    g01 = grid[y0[:, None], x1[None, :]]
    g10 = grid[y1[:, None], x0[None, :]]
    g11 = grid[y1[:, None], x1[None, :]]
    fy2, fx2 = fy[:, None], fx[None, :]
    out = (
        g00 * (1.0 - fy2) * (1.0 - fx2) + g01 * (1.0 - fy2) * fx2
        + g10 * fy2 * (1.0 - fx2) + g11 * fy2 * fx2
    )
    out -= float(out.mean())
    std = float(out.std()) or 1.0
    return (out / std).astype(np.float32)


def paper_gray(size: int = SIZE, seed: int = SEEDS["paper"]) -> np.ndarray:
    blotch = wrap_value_noise(size, size, seed, 64)
    grain = wrap_value_noise(size, size, seed + 17, 8)
    speckle = wrap_value_noise(size, size, seed + 23, 2)
    fiber = wrap_value_noise(size, size, seed + 31, 2, 32)
    gray = (
        0.80
        + 0.035 * blotch
        + 0.040 * grain
        + 0.028 * speckle
        + 0.016 * fiber
    )
    return np.clip(gray.astype(np.float32), 0.0, 1.0)


def _pulse_train(
    coord: np.ndarray, size: int, cycles: int, seed: int,
    sigma_px: float = LINEN_SIGMA_PX,
) -> np.ndarray:
    """Periodic narrow Gaussian pulses with per-line thickness/amp/phase jitter."""
    rng = np.random.RandomState(seed)
    n = int(cycles)
    amp = (0.70 + 0.60 * rng.rand(n)).astype(np.float32)
    thick = (0.65 + 0.70 * rng.rand(n)).astype(np.float32)
    phase = ((rng.rand(n) - 0.5) * 0.36).astype(np.float32)
    t = coord.astype(np.float32) * (n / float(size))
    t_mod = np.mod(t, n)
    idx = np.floor(t_mod + 0.5).astype(np.int32) % n
    nearest = idx.astype(np.float32) + phase[idx]
    delta = t_mod - nearest
    delta = np.mod(delta + n * 0.5, n) - n * 0.5
    period = size / float(n)
    dist_px = np.abs(delta) * period
    sig = np.maximum(0.16, sigma_px * thick[idx])
    return amp[idx] * np.exp(-0.5 * (dist_px / sig) ** 2)


def linen_gray(size: int = SIZE, seed: int = SEEDS["linen"]) -> np.ndarray:
    xs = np.arange(size, dtype=np.float32)[None, :]
    ys = np.arange(size, dtype=np.float32)[:, None]
    warp = torus_fbm(size, size, seed + 9, octaves=3)
    wx = (warp - 0.5) * 0.8
    wy = (np.roll(warp, size // 3, axis=0) - 0.5) * 0.8
    weave = (
        _pulse_train(xs + wx, size, LINEN_CYCLES, seed)
        + _pulse_train(ys + wy, size, 394, seed + 3)
        + 0.55 * _pulse_train(xs + wx * 0.6, size, 402, seed + 7)
        + 0.55 * _pulse_train(ys + wy * 0.6, size, 386, seed + 11)
    )
    fluff = wrap_value_noise(size, size, seed + 19, 4)
    weave = weave - float(weave.mean())
    p98 = float(np.percentile(np.abs(weave), 98))
    gray = 0.58 + LINEN_CONTRAST * weave / (p98 + 1e-8) + 0.018 * fluff
    return np.clip(gray.astype(np.float32), 0.0, 1.0)


def grid_layers(size: int = SIZE, seed: int = SEEDS["grid"]) -> tuple[np.ndarray, np.ndarray]:
    paper = paper_gray(size, seed)
    gray = 0.88 + 0.10 * paper
    alpha = np.ones((size, size), dtype=np.float32)
    step = 32
    line_a = 0.18
    gray[np.arange(0, size, step), :] = gray[np.arange(0, size, step), :] * (1.0 - line_a)
    gray[:, np.arange(0, size, step)] = gray[:, np.arange(0, size, step)] * (1.0 - line_a)
    return np.clip(gray, 0.0, 1.0), alpha


def to_bgra(gray: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
    g = np.clip(gray * 255.0, 0, 255).astype(np.uint8)
    a = np.full(gray.shape, 255, dtype=np.uint8) if alpha is None else (
        np.clip(alpha * 255.0, 0, 255).astype(np.uint8))
    out = np.empty(gray.shape + (4,), dtype=np.uint8)
    out[..., 0] = g
    out[..., 1] = g
    out[..., 2] = g
    out[..., 3] = a
    return out


def seam_delta(gray: np.ndarray) -> float:
    """Wrap-step minus interior-step; periodic textures stay ≤ 2/255 extra."""
    g = gray.astype(np.float32)
    ix = float(np.abs(g[:, 1:] - g[:, :-1]).max())
    iy = float(np.abs(g[1:, :] - g[:-1, :]).max())
    wx = float(np.abs(g[:, 0] - g[:, -1]).max())
    wy = float(np.abs(g[0, :] - g[-1, :]).max())
    return float(max(wx - ix, wy - iy, 0.0))


def generate_all(out_dir: str | None = None) -> dict[str, str]:
    dest = out_dir or TEXTURE_DIR
    os.makedirs(dest, exist_ok=True)
    paper = paper_gray()
    linen = linen_gray()
    grid, grid_a = grid_layers()
    names = {"paper": paper, "linen": linen, "grid": grid}
    alphas = {"paper": None, "linen": None, "grid": grid_a}
    written = {}
    for name, gray in names.items():
        path = os.path.join(dest, "%s.png" % name)
        cv2.imwrite(path, to_bgra(gray, alphas[name]))
        written[name] = path
    return written


def main() -> int:
    written = generate_all()
    for name, path in written.items():
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        gray = img[:, :, 0].astype(np.float32) / 255.0
        delta = seam_delta(gray)
        print("%s %s seam=%.6f" % (name, path, delta), flush=True)
        if delta >= 2.0 / 255.0:
            raise SystemExit("texture %s not seamless: %s" % (name, delta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
