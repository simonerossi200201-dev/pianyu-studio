# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q193 · 没有 NVIDIA 显卡的电脑：要显卡才流畅的功能，先说清楚再跑。

以前在没有 N 卡的电脑上，这些功能会悄悄改用 CPU：点了以后半天没反应，用户以为软件卡死了。
现在第一次用到时弹一个明确的提示（这次会话里同一个功能只问一次），选「继续（用 CPU）」照样能跑完。

不需要提示的（CPU 跑也正常）：剪辑、预览、字幕排版、抠像 / 调整图层（CPU 计算，稍慢）、
导出（改用 CPU 编码 libx264，画质一样，速度慢一些）。这些在启动时状态栏说一次。
"""
from __future__ import annotations

from cutora.core.log import get_logger

_LOG = get_logger("cutora.ui.gpu_notice")

FEATURES = {
    "asr": ("自动字幕（语音识别）", "改用 CPU 识别，大约慢 5–10 倍：一条 3 分钟的视频可能要好几分钟"),
    "locate": ("识别开头时间戳", "改用 CPU 识别，每条视频大约十几秒到一分钟"),
    "batch_asr": ("语音转文本", "改用 CPU 识别，大约慢 5–10 倍"),
    "stitch": ("片段智能拼接（要识别每个片段的语音）", "改用 CPU 识别，大约慢 5–10 倍"),
    "ai_matte": ("AI 抠像", "改用 CPU 计算，大约慢 10–20 倍：几十秒的素材可能要十几分钟以上"),
    "tracking": ("运动跟踪", "改用 CPU 计算，大约慢 10–20 倍"),
    "stem_split": ("只留说话 / 只留伴奏", "改用 CPU 分离，一条口播可能要几分钟"),
}

_STATE = {"ok": set(), "no": set(), "gpu": None, "asking": False}


def has_gpu() -> bool:
    """这台电脑有没有能用的 NVIDIA 显卡（nvidia-smi / nvcuda.dll 能看到）。``CUTORA_FAKE_NO_GPU=1`` 模拟没有。"""
    import os
    if os.environ.get("CUTORA_FAKE_NO_GPU") == "1":
        return False
    if os.environ.get("CUTORA_FAKE_GPU") == "1":       # 测试机没 N 卡也别弹提示把测试卡住
        return True
    if not _STATE["gpu"]:
        try:
            from cutora.fx.gpu import has_nvidia_gpu
            _STATE["gpu"] = bool(has_nvidia_gpu())
        except Exception:
            _STATE["gpu"] = bool(os.name == "nt" and os.path.isfile(r"C:\Windows\System32\nvcuda.dll"))
    return bool(_STATE["gpu"])


def reset() -> None:
    """测试用。"""
    _STATE["ok"].clear()
    _STATE["no"].clear()
    _STATE["gpu"] = None
    _STATE["asking"] = False


def confirm(parent, key: str) -> bool:
    """要显卡才流畅的功能开跑前调一次。有 N 卡 → True；没有 → 问一次（本次会话记住答案）。"""
    if has_gpu():
        return True
    if key in _STATE["ok"]:
        return True
    if _STATE["asking"]:
        # 提示框开着时它的事件循环还在跑：自动识别的定时器可能再来问一次 → 不叠第二个框，这次先不跑
        return False
    name, slow = FEATURES.get(key, (key, "改用 CPU 计算，会很慢"))
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("需要 NVIDIA 显卡")
    box.setText("这台电脑没有检测到 NVIDIA 显卡。")
    box.setInformativeText(
        "「%s」需要 NVIDIA 显卡才能流畅使用。\n没有的话会%s。\n\n要继续吗？" % (name, slow))
    go = box.addButton("继续（用 CPU）", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(go)
    _STATE["asking"] = True
    try:
        box.exec()
    finally:
        _STATE["asking"] = False
    ok = box.clickedButton() is go
    (_STATE["ok"] if ok else _STATE["no"]).add(key)
    _LOG.info("[HW] 没有 N 卡，「%s」用户选择：%s", name, "继续用 CPU" if ok else "取消")
    return ok


def declined(key: str) -> bool:
    """这次会话里用户对这个功能点过「取消」（自动触发的地方别再问）。"""
    return (not has_gpu()) and key in _STATE["no"]


def startup_note() -> str:
    """启动时状态栏说一次（没有 N 卡才有）。"""
    if has_gpu():
        return ""
    return ("没有检测到 NVIDIA 显卡：剪辑 / 预览 / 导出照常（导出用 CPU 编码，慢一些）；"
            "自动字幕、AI 抠像、运动跟踪会改用 CPU，很慢，用到时会先提示")
