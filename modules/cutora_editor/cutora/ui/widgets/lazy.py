# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 103-137

# ═══════════════════════════════════════════════════════════════════════════
# [v6.1] torch / whisperx 惰性导入
#
# 原来这里是裸的 `import torch` / `import whisperx`。这两个库加起来要 3~5 秒
# 才能导入完，而且 torch 一旦被使用就会建立 CUDA 上下文（约 0.5~1GB 显存）。
#
# 并行导出会同时启动多个纯渲染子进程，它们只需要 PySide6 + OpenCV + FFmpeg，
# 完全用不到 AI。如果每个子进程都老老实实 import torch 并初始化 CUDA，
# 6 个进程光上下文就能吃掉 6GB 显存，16GB 的卡直接被挤爆。
#
# 下面的代理对象在**第一次访问属性时**才真正导入。对现有代码完全透明：
# torch.cuda.is_available() 这样的写法一字不用改，行为也完全一致。
# ═══════════════════════════════════════════════════════════════════════════

class _LazyModule:
    """按需导入的模块代理。第一次访问属性时才执行真正的 import。"""

    def __init__(self, name):
        self.__dict__["_lazy_name"] = name
        self.__dict__["_lazy_mod"] = None

    def _lazy_load(self):
        mod = self.__dict__["_lazy_mod"]
        if mod is None:
            import importlib
            mod = importlib.import_module(self.__dict__["_lazy_name"])
            self.__dict__["_lazy_mod"] = mod
        return mod

    def __getattr__(self, item):
        return getattr(self._lazy_load(), item)

    def __repr__(self):
        state = "已加载" if self.__dict__["_lazy_mod"] is not None else "未加载"
        return f"<LazyModule {self.__dict__['_lazy_name']} ({state})>"
