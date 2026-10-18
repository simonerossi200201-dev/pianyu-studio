# -*- coding: utf-8 -*-
"""子进程入口：Windows 下绝不弹控制台窗口。

pythonw 起的进程没有控制台，每 subprocess.run 一个控制台程序，Windows 就会
新建一个黑窗口闪一下。creationflags 和 startupinfo 两条路径在不同 Windows
版本 / 不同父进程（pythonw / python / .bat）下表现不一样，一起给才稳。
"""
from __future__ import annotations

import os
import subprocess


def _hidden_kw() -> dict:
    """Windows 下让子进程完全不弹控制台窗口。非 Windows 返回空。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE          # 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": si,
    }


# 给 Popen 用（jobs 等长活子进程）
hidden_kw = _hidden_kw


def run_quiet(cmd, timeout=None, **kw):
    """跑一个命令行工具，绝不弹窗。capture_output 默认开，stdin 掐死
    （ffmpeg 偶尔会等键盘输入，pythonw 下没有 stdin 会直接挂住）。"""
    kw.setdefault("capture_output", True)
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.update(_hidden_kw())
    return subprocess.run(cmd, timeout=timeout, **kw)
