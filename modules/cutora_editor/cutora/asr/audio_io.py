# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q133 · 不开黑框的 whisperx.load_audio。

whisperx.audio.load_audio 用裸 subprocess.run 起「ffmpeg」。主程序是 pythonw
（自己没控制台），Windows 就给每个 ffmpeg 现开一个控制台窗口 —— 自动字幕
每条视频闪一下黑框。这里是逐字节等价的替身：同一条 ffmpeg 命令、同样的
s16le → float32 换算，只是走 cutora.core.proc（CREATE_NO_WINDOW）并用内置
ffmpeg 的绝对路径。patch_whisperx() 把 whisperx 内部各模块里绑定的
load_audio 一并换掉（asr / alignment / transcribe / diarize 都是
``from whisperx.audio import load_audio``）。
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np

SAMPLE_RATE = 16000
_WX_MODULES = (
    "whisperx.audio", "whisperx.asr", "whisperx.alignment",
    "whisperx.transcribe", "whisperx.diarize",
)


def ffmpeg_decode_cmd(file: str, sr: int = SAMPLE_RATE) -> list:
    from cutora.core.config import FFMPEG_PATH
    return [
        FFMPEG_PATH, "-nostdin", "-threads", "0", "-i", str(file),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(int(sr)), "-",
    ]


def load_audio(file: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """与 whisperx.audio.load_audio 同样的输出，不开控制台窗口。"""
    from cutora.core.proc import run
    try:
        out = run(ffmpeg_decode_cmd(file, sr), capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Failed to load audio: %s" % (e.stderr or b"").decode("utf-8", "replace")) from e
    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0


load_audio._cutora_no_window = True  # type: ignore[attr-defined]


def patch_whisperx() -> int:
    """把已导入的 whisperx 各模块里的 load_audio 换成上面这个。返回换了几处。"""
    n = 0
    for name in _WX_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        cur = getattr(mod, "load_audio", None)
        if cur is None or getattr(cur, "_cutora_no_window", False):
            continue
        try:
            setattr(mod, "load_audio", load_audio)
            n += 1
        except Exception:
            pass
    return n
