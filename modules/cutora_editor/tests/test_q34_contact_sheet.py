# -*- coding: utf-8 -*-
"""Q34-3: with the contact-sheet setting on, verify_export writes a 3x3 PNG."""
from __future__ import annotations

import os
import subprocess

import pytest

from cutora.core.config import FFMPEG_PATH
from cutora.render.export_verify import (
    FLICKER_VAR_LIMIT,
    contact_sheet_flicker_var,
    contact_sheet_path,
    set_contact_sheet_enabled,
    verify_export,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ffmpeg():
    return FFMPEG_PATH


def _make_mp4(path, duration=1.0, fps=25, size="64x64", src="color=c=red"):
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", "%s:s=%s:d=%.2f:r=%d" % (src, size, duration, fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode test clip: %s" % (r.stderr or "")[-200:])
    assert os.path.isfile(path) and os.path.getsize(path) > 400


def _make_noise_mp4(path, duration=1.0, fps=25, size="64x64"):
    filt = (
        "nullsrc=s=%s:d=%.2f:r=%d,format=rgb24,"
        "geq=r='random(1)*255':g='random(1)*255':b='random(1)*255',"
        "format=yuv420p" % (size, duration, fps)
    )
    r = subprocess.run(
        [
            _ffmpeg(), "-y", "-f", "lavfi", "-i", filt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            path,
        ],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("ffmpeg cannot encode noise clip: %s" % (r.stderr or "")[-200:])
    assert os.path.isfile(path) and os.path.getsize(path) > 400


def _cells(png_path):
    import cv2
    img = cv2.imread(png_path, cv2.IMREAD_COLOR)
    assert img is not None, png_path
    h, w = img.shape[:2]
    assert w % 3 == 0 and h % 3 == 0, (w, h)
    cw, ch = w // 3, h // 3
    out = []
    for row in range(3):
        for col in range(3):
            cell = img[row * ch:(row + 1) * ch, col * cw:(col + 1) * cw]
            out.append(cell)
    return out


def test_q34_contact_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    set_contact_sheet_enabled(True)
    src = str(tmp_path / "good.mp4")
    _make_mp4(src, duration=1.0, fps=25, src="color=c=red")
    ok, why = verify_export(src, expected_duration=1.0, fps=25)
    assert ok is True, why
    png = contact_sheet_path(src)
    assert png == str(tmp_path / "_verify" / "good.png")
    assert os.path.isfile(png), png
    cells = _cells(png)
    assert len(cells) == 9
    for i, cell in enumerate(cells):
        mean = float(cell.mean())
        assert mean > 8.0, "cell %d is all-black mean=%.3f" % (i, mean)

    noisy = str(tmp_path / "flower.mp4")
    _make_noise_mp4(noisy, duration=1.0, fps=25)
    ok2, why2 = verify_export(noisy, expected_duration=1.0, fps=25)
    assert ok2 is True, why2
    flower_png = contact_sheet_path(noisy)
    assert os.path.isfile(flower_png), flower_png
    var = contact_sheet_flicker_var(flower_png)
    solid_var = contact_sheet_flicker_var(png)
    assert var > FLICKER_VAR_LIMIT, (
        "flower-screen contact sheet flicker var %.3f <= limit %.3f"
        % (var, FLICKER_VAR_LIMIT))
    assert solid_var < FLICKER_VAR_LIMIT, solid_var
    print("Q34_FLICKER_VAR", var, "SOLID", solid_var, "LIMIT", FLICKER_VAR_LIMIT)
