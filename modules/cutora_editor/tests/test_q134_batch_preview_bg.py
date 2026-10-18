# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q134 · 批量加载右栏全黑：拼接工程的 task["video"] 只是第一段切片（十几秒），
起点选「1 分钟」就去切片里找第 60 秒 → 读不到帧。背景改成按时间线合成。"""
from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q134"])


def _clip(tmp_path):
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    out = str(tmp_path / "c.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0xC08040:s=108x192:r=30:d=4", "-pix_fmt", "yuv420p", out],
                   check=True)
    return out


def _mean(img):
    if img is None:
        return None
    return float(np.frombuffer(bytes(img.constBits()), np.uint8).mean())


def _stitched_task(path, n=5, seg=4.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(108, 192, 30)
    tr = Track("video")
    tl.add_track(tr)
    tl.sources["s1"] = MediaSource(path)
    for i in range(n):
        tr.clips.append(Clip("s1", 0.0, seg, i * seg))
    return {"timeline": tl, "video": path, "path": path}


def test_stitched_task_background_past_first_clip(tmp_path):
    _app()
    from cutora.batch.batch_preview import fitted_background, task_background
    path = _clip(tmp_path)
    task = _stitched_task(path)
    assert fitted_background(path, 12.0, 108, 192) is None   # 老办法：切片里没有第 12 秒
    img = task_background(task, 12.0, 108, 192)
    assert img is not None and _mean(img) > 60, _mean(img)
    late = task_background(task, 999.0, 108, 192)            # 超出时间线：取最后一帧
    assert late is not None and _mean(late) > 60


def test_plain_task_falls_back_to_file(tmp_path):
    _app()
    from cutora.batch.batch_preview import task_background
    path = _clip(tmp_path)
    img = task_background({"video": path}, 1.0, 108, 192)
    assert img is not None and _mean(img) > 60


def test_dialog_uses_task_background():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "cutora", "batch", "batch_dialog.py"), encoding="utf-8").read()
    body = src[src.index("def _paint_background"):]
    body = body[:body.index("def _media_path")]
    assert "task_background(" in body
