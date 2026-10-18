# -*- coding: utf-8 -*-
"""导出的解码器必须与 ffmpeg 参考逐字节一致（2026-09-19 事故的永久保险）。

背景：修 BUG-42（硬件解码把显卡驱动干重置 → 黑屏 + 花屏）时，导出从硬件解码
改成了软件解码，结果 golden 十二例全部哈希漂移。查下来不是新代码错了，而是
**旧基线本来就是错的**：

    源              帧    软件解码(cv2 NONE)        硬件解码(cv2 ANY)
    g01/g02         0    MAD 0.000 max   0  0.0%   MAD 0.851 max  80 40.4%
    g03             0    MAD 0.000 max   0  0.0%   MAD 1.218 max 255 40.4%

H.264 按规范逐比特确定，任何合规解码器出来的像素必须一模一样。软件解码做到了，
硬件解码在 40% 的像素上偏离、最大偏差 255。旧的十二例基线是用后者录的。

这个文件锁两件事，谁再把硬件解码放回导出路径都会当场红：
1. 软件解码与 ffmpeg 参考逐字节相同
2. 导出路径默认走软件解码
完整证据：tools/decode_proof.py、docs/plan/queue/_log/_decode_proof.log
"""
from __future__ import annotations

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"

pytestmark = pytest.mark.skipif(
    not os.path.isfile(SRC),
    reason="golden fixture media not on disk (tests/golden/_media is gitignored)",
)


def _ffmpeg_ref(path, n, w, h):
    cmd = [FFMPEG, "-v", "error", "-i", path,
           "-vf", r"select=eq(n\,%d)" % n, "-vsync", "0", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    need = w * h * 3
    if p.returncode != 0 or len(p.stdout) < need:
        pytest.skip("ffmpeg reference decode unavailable")
    import numpy as np
    return np.frombuffer(p.stdout[:need], np.uint8).reshape(h, w, 3)


@pytest.mark.parametrize("frame_no", [0, 30, 90])
def test_software_decode_is_bit_exact_with_ffmpeg(frame_no):
    """软件解码 = 参考。这是「新像素是对的」的直接证据，不是「看起来差不多」。"""
    import cv2
    import numpy as np
    probe = cv2.VideoCapture(SRC)
    w = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
    probe.release()
    ref = _ffmpeg_ref(SRC, frame_no, w, h)

    cap = cv2.VideoCapture(SRC, cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_HW_ACCELERATION,
                            cv2.VIDEO_ACCELERATION_NONE])
    assert cap.isOpened()
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, got = cap.read()
    finally:
        cap.release()
    assert ok and got is not None
    assert got.shape == ref.shape
    diff = np.abs(got.astype(np.int16) - ref.astype(np.int16))
    assert int(diff.max()) == 0, (
        "软件解码和 ffmpeg 参考不一致了（max=%d, MAD=%.4f）。"
        "这说明解码链有变化 —— 停下来查，不要重录 golden。"
        % (int(diff.max()), float(diff.mean()))
    )


def test_export_path_decodes_in_software_by_default(monkeypatch):
    """不变量 #1：导出一律软件解码。硬件解码会重置显卡驱动（BUG-42）。"""
    from cutora.render.render_worker import export_hw_decode
    monkeypatch.delenv("CUTORA_EXPORT_HW_DECODE", raising=False)
    assert export_hw_decode() is False

    src = open(os.path.join(REPO, "cutora", "render", "render_worker.py"),
               encoding="utf-8").read()
    body = src[src.index("class RenderWorker"):]
    assert "_open_video_capture(src_path)" not in body
    assert "_open_video_capture(overlay_path)" not in body
    assert src.count("hw=export_hw_decode()") == 2
