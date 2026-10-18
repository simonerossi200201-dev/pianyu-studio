# -*- coding: utf-8 -*-
"""T04a: GUI auto-subtitle must preflight media before ASR."""
from __future__ import annotations

import inspect
import os
import subprocess

import pytest

from cutora.batch.media_guard import PreflightError, preflight_for_auto_subtitle

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def test_preflight_accepts_golden_video():
    info = preflight_for_auto_subtitle(G01)
    assert info is not None
    assert info.get("has_audio") is True


def test_preflight_rejects_missing_and_empty(tmp_path):
    missing = tmp_path / "nope.mp4"
    with pytest.raises(PreflightError):
        preflight_for_auto_subtitle(str(missing))
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(PreflightError) as exc:
        preflight_for_auto_subtitle(str(empty))
    assert "0 字节" in str(exc.value) or "空" in str(exc.value)


def test_preflight_rejects_jpeg_named_mp4(tmp_path):
    fake = tmp_path / "still.mp4"
    # Minimal JPEG SOI so sniff_type reports a still image.
    fake.write_bytes(b"\xff\xd8\xff\xe0" + b"JFIF" + b"\x00" * 16)
    with pytest.raises(PreflightError) as exc:
        preflight_for_auto_subtitle(str(fake))
    msg = str(exc.value)
    assert "JPEG" in msg or "图片" in msg or "不是视频" in msg or "视频" in msg


def test_preflight_rejects_video_without_audio(tmp_path):
    if not os.path.isfile(FFMPEG):
        pytest.skip("runtime ffmpeg missing")
    dest = tmp_path / "silent.mp4"
    cmd = [
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
        "-an", "-pix_fmt", "yuv420p", str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    with pytest.raises(PreflightError) as exc:
        preflight_for_auto_subtitle(str(dest))
    assert "音轨" in str(exc.value)


def test_run_align_calls_preflight():
    path = os.path.join(REPO, "cutora", "ui", "main_window.py")
    src = open(path, encoding="utf-8").read()
    assert "def run_align" in src
    assert "preflight_for_auto_subtitle" in src


def test_command_subtitle_auto_calls_preflight():
    from cutora.ui.command_bodies import CommandBodies
    auto = inspect.getsource(CommandBodies.command_subtitle_auto)
    helper = inspect.getsource(CommandBodies._preflight_auto_queue)
    assert "_preflight_auto_queue" in auto
    assert "preflight_for_auto_subtitle" in helper
