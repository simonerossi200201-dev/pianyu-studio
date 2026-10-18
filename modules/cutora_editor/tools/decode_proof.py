# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which decoder is right? Compare cv2 software / cv2 hardware against ffmpeg.

2026-09-19: all twelve golden cases drifted when export switched from hardware
to software decoding (the fix for BUG-42, the display-driver reset that blacked
Luca's screen). Fonts were ruled out (digest identical) and single-source cases
drifted too (so it is not the capture pool). Before re-recording any baseline we
prove which decoder produces the correct pixels.

The reference is ffmpeg's own CLI decode to rawvideo — the same libavcodec that
both OpenCV paths sit on, with no GPU transfer and no OpenCV colour conversion
in between. A conformant H.264 decoder is bit-exact, so whichever path matches
the reference is the right one.

Run:  runtime\\venv\\Scripts\\python.exe tools\\decode_proof.py
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")
if not os.path.isfile(FFMPEG):
    FFMPEG = "ffmpeg"

SOURCES = [
    os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4"),
    os.path.join(REPO, "tests", "golden", "_media", "g02", "video.mp4"),
    os.path.join(REPO, "tests", "golden", "_media", "g03", "video.mp4"),
]
FRAMES = [0, 30, 90, 150]


def ffmpeg_frame(path: str, n: int):
    """Reference decode: libavcodec -> bgr24, no GPU, no OpenCV in the middle."""
    cmd = [FFMPEG, "-v", "error", "-i", path,
           "-vf", r"select=eq(n\,%d)" % n, "-vsync", "0", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0 or not p.stdout:
        return None
    cap = cv2.VideoCapture(path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    need = w * h * 3
    if len(p.stdout) < need:
        return None
    return np.frombuffer(p.stdout[:need], np.uint8).reshape(h, w, 3)


def cv2_frame(path: str, n: int, accel):
    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_HW_ACCELERATION, accel])
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def stats(a, b):
    if a is None or b is None or a.shape != b.shape:
        return None
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(d.mean()), int(d.max()), float((d > 0).mean() * 100.0)


def which_tier(path: str, hw: bool) -> str:
    from cutora.render.readers import (_FFmpegFrameReader, _RawARGBMOVReader,
                                       _open_video_capture)
    cap = _open_video_capture(path, hw=hw)
    if cap is None:
        return "None"
    try:
        if isinstance(cap, _RawARGBMOVReader):
            return "T3 rawARGB"
        if isinstance(cap, _FFmpegFrameReader):
            return "T4 ffmpeg-pipe"
        return "T1/T2 cv2"
    finally:
        try:
            cap.release()
        except Exception:
            pass


def main() -> int:
    print("ffmpeg:", FFMPEG)
    print("cv2   :", cv2.__version__)
    print()
    print("MAD = 与 ffmpeg 参考帧的平均绝对差；max = 最大通道差；diff%% = 有差异的像素占比")
    print("MAD 0.000 / max 0 = 与参考逐字节相同 = 这条路是对的")
    print()
    hdr = "%-26s %5s | %-22s | %-22s" % ("source", "frame", "software (cv2 NONE)", "hardware (cv2 ANY)")
    print(hdr)
    print("-" * len(hdr))
    worst_sw = 0.0
    worst_hw = 0.0
    for src in SOURCES:
        if not os.path.isfile(src):
            print("%-26s  (缺文件，跳过)" % os.path.basename(os.path.dirname(src)))
            continue
        name = os.path.basename(os.path.dirname(src))
        for n in FRAMES:
            ref = ffmpeg_frame(src, n)
            sw = stats(cv2_frame(src, n, cv2.VIDEO_ACCELERATION_NONE), ref)
            hw = stats(cv2_frame(src, n, cv2.VIDEO_ACCELERATION_ANY), ref)
            def fmt(s):
                if s is None:
                    return "%-22s" % "(取不到)"
                return "MAD %.3f max %3d %4.1f%%" % (s[0], s[1], s[2])
            print("%-26s %5d | %-22s | %-22s" % (name, n, fmt(sw), fmt(hw)))
            if sw:
                worst_sw = max(worst_sw, sw[0])
            if hw:
                worst_hw = max(worst_hw, hw[0])
    print()
    print("最差 MAD：软件解码 %.4f   硬件解码 %.4f" % (worst_sw, worst_hw))
    print()
    print("解码器分层（_open_video_capture 实际选了哪一层）")
    for src in SOURCES:
        if not os.path.isfile(src):
            continue
        name = os.path.basename(os.path.dirname(src))
        print("  %-10s hw=False -> %-16s hw=True -> %s"
              % (name, which_tier(src, False), which_tier(src, True)))
    print()
    if worst_sw < worst_hw:
        print("结论：软件解码更接近参考。旧基线是用硬件解码录的，那条路才是偏的。")
    elif worst_hw < worst_sw:
        print("结论：硬件解码更接近参考。这和预期相反 —— 不要重录，报给 Claude。")
    else:
        print("结论：两条路一样。那么漂移另有原因 —— 不要重录，报给 Claude。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
