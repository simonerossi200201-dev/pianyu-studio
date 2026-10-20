# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-start torch / CUDA initialization shared by the app and tests."""
from __future__ import annotations

import os
import sys

from cutora.core.log import get_logger

logger = get_logger("cutora.core.runtime")

_ENV_DEFAULTS = (
    ("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1"),
    ("HF_HUB_OFFLINE", "1"),
    ("TRANSFORMERS_OFFLINE", "1"),
)

_INITIALIZED = False
_PYSIDE6_MSVC_WARNED = False
CV2_NUM_THREADS = 2
_CUDA_WARMUP_OK: bool | None = None
_CUDA_DEVICE_NAME: str | None = None


def _pyside6_already_imported() -> bool:
    return any(m == "PySide6" or m.startswith("PySide6.") for m in sys.modules)


def loaded_msvc_runtime_path() -> str | None:
    """Return the path of msvcp140.dll mapped into this process, if any."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleFileNameW.argtypes = [
        wintypes.HMODULE,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD

    handle = kernel32.GetModuleHandleW("msvcp140.dll")
    if not handle:
        return None
    n = 32768
    buf = ctypes.create_unicode_buffer(n)
    copied = kernel32.GetModuleFileNameW(handle, buf, n)
    if copied == 0:
        return None
    return buf.value


def cuda_warmup_status() -> tuple[bool | None, str | None]:
    """Return ``(warmup_ok, device_name)``. Both None if init has not run.

    ``warmup_ok`` is True on a successful CUDA ``zeros``, False on exception,
    and None when the warm-up was skipped (Q187: the GUI no longer initialises
    CUDA in-process; the GPU service does and reports via ``note_cuda_probe``).
    """
    return _CUDA_WARMUP_OK, _CUDA_DEVICE_NAME


def note_cuda_probe(ok: bool, device_name: str | None = None) -> None:
    """GPU 服务进程（或别的子进程）体检完 CUDA 后回填：status 行和状态栏用。"""
    global _CUDA_WARMUP_OK, _CUDA_DEVICE_NAME
    _CUDA_WARMUP_OK = bool(ok)
    if device_name:
        _CUDA_DEVICE_NAME = device_name


def _warmup_cuda(torch_mod) -> tuple[bool | None, str | None]:
    """Establish a CUDA context. Returns (ok, device_name)."""
    try:
        if torch_mod.cuda.is_available():
            torch_mod.zeros(1, device="cuda")
            return True, torch_mod.cuda.get_device_name(0)
        return None, "cpu"
    except Exception:
        logger.exception("CUDA 上下文预热失败，继续启动")
        try:
            return False, torch_mod.cuda.get_device_name(0)
        except Exception:
            return False, None


def msvc_runtime_from_pyside6(path: str | None) -> bool:
    """True if the mapped msvcp140.dll lives under site-packages\\PySide6\\."""
    if not path:
        return False
    norm = os.path.normcase(os.path.normpath(path))
    marker = os.path.normcase(os.path.join("site-packages", "PySide6") + os.sep)
    return marker in (norm + os.sep)


def _warn_pyside6_msvc_runtime(path: str) -> None:
    global _PYSIDE6_MSVC_WARNED
    logger.error(
        "运行库加载顺序错误，自动字幕将崩溃，请把日志发给开发者: %s",
        path,
    )
    if _PYSIDE6_MSVC_WARNED:
        return
    _PYSIDE6_MSVC_WARNED = True
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "运行库加载顺序错误，自动字幕将崩溃，请把日志发给开发者",
            "CUTORA",
            0x10,
        )
    except Exception:
        logger.exception("无法弹出运行库警告框")


def pin_cv2_num_threads(n: int = CV2_NUM_THREADS) -> None:
    """Pin OpenCV threads for the process. Do not restore later.

    Small remaps oversubscribe at the default 32. Changing the count
    mid-call races the preview decoder's cv2.resize (T04f-5).
    """
    import cv2
    cv2.setNumThreads(int(n))


def mimalloc_status() -> str:
    """Q180：这个进程的 C 堆是不是 mimalloc 在管（SETUP_MIMALLOC.bat 给 python.exe 打过补丁）。
    Windows 看 mimalloc.dll 有没有被加载；别的平台看 LD_PRELOAD。日志用。"""
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.GetModuleHandleW("mimalloc.dll")
            if not h:
                return "mimalloc 未启用（运行 SETUP_MIMALLOC.bat 可让导出 / 预览少做一大半内存页分配）"
            try:
                v = int(ctypes.WinDLL("mimalloc.dll").mi_version())
                return "mimalloc v%d.%d.%d 已接管内存分配" % (v // 10000, (v // 100) % 100, v % 100)
            except Exception:
                return "mimalloc 已加载"
        pre = os.environ.get("LD_PRELOAD") or ""
        return "mimalloc 已接管内存分配（LD_PRELOAD）" if "mimalloc" in pre else "mimalloc 未启用"
    except Exception:
        return "mimalloc 状态未知"


# Q187：主界面进程不再常驻 torch / CUDA。
#
# 以前启动时先 import torch + 在显卡上建一个上下文（为了让 msvcp140.dll 先于 PySide6 自带的那份
# 被加载，顺便做 CUDA 体检）。代价是主界面进程一开机就背着 2～3 GB 的 torch / cuDNN / cuBLAS，
# 导出时规划器只能按剩下的内存开 4～5 个渲染进程。专业剪辑软件的做法是界面进程里不放 ML 运行时：
# 抠像 / 调整层算在 GPU 服务进程里（Q181 已有），AI 功能按需起子进程。
#
# 运行库加载顺序的问题换个办法解决：启动时直接从 System32 把 msvcp140.dll / vcruntime140*.dll
# 映射进来（和 import torch 时实际发生的事一样），PySide6 之后再 import 时发现同名 DLL 已在，
# 就不会去加载它自带的旧版。CUTORA_EAGER_TORCH=1 可切回老行为（排查用）。
_MSVC_PRELOAD = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
_MSVC_PRELOADED = None


def preload_system_msvc_runtime() -> str | None:
    """Windows：从 System32 预加载 MSVC 运行库；返回映射到的 msvcp140.dll 路径（非 Windows / 失败 → None）。"""
    global _MSVC_PRELOADED
    if os.name != "nt":
        return None
    if _MSVC_PRELOADED is not None:
        return _MSVC_PRELOADED or None
    import ctypes
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    ok = True
    for name in _MSVC_PRELOAD:
        path = os.path.join(sysdir, name)
        if not os.path.isfile(path):
            if name == "msvcp140.dll":
                ok = False
            continue
        try:
            ctypes.WinDLL(path)
        except Exception:
            logger.warning("预加载 %s 失败", path, exc_info=True)
            if name == "msvcp140.dll":
                ok = False
    mapped = loaded_msvc_runtime_path() if ok else None
    _MSVC_PRELOADED = mapped or ""
    return mapped


def _nvidia_gpu_name() -> str | None:
    """不碰 torch 的显卡名（nvidia-smi / nvcuda.dll）。没有 N 卡 → None。"""
    if _CUDA_DEVICE_NAME and _CUDA_DEVICE_NAME != "cpu":
        return _CUDA_DEVICE_NAME
    try:
        import subprocess
        from cutora.core.proc import run as _run
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        smi_exe = r"C:\Windows\System32\nvidia-smi.exe" if (os.name == "nt" and os.path.isfile(r"C:\Windows\System32\nvidia-smi.exe")) else "nvidia-smi"
        r = _run([smi_exe, "--query-gpu=name", "--format=csv,noheader"],
                 capture_output=True, text=True, timeout=8, creationflags=flags)
        if r.returncode == 0:
            name = (r.stdout or "").strip().splitlines()[0].strip()
            if name:
                return name
    except Exception:
        pass
    if os.name == "nt" and os.path.isfile(r"C:\Windows\System32\nvcuda.dll"):
        return "NVIDIA GeForce RTX GPU"
    return None


def init_torch_runtime(render_child: bool = False) -> None:
    """进程启动期的运行时初始化。app.main 与测试必须调同一个函数。

    ``render_child=True`` 时立即返回，一行 torch 都不碰——渲染子进程
    不建 CUDA 上下文（并行导出时 6 个子进程各吃约 1 GB 显存）。

    Q187 起主界面也不再 import torch：只预加载 MSVC 运行库（保住加载顺序）、设环境变量、
    记一下显卡名。torch 在真正用到 AI 功能的那一刻才进来；抠像 / 调整层走 GPU 服务进程。
    必须在任何 ``PySide6`` import 之前调用。重复调用幂等。
    """
    global _INITIALIZED, _CUDA_WARMUP_OK, _CUDA_DEVICE_NAME
    pin_cv2_num_threads()
    if render_child:
        return
    if _INITIALIZED:
        return
    if _pyside6_already_imported():
        raise RuntimeError(
            "init_torch_runtime 必须在任何 PySide6 import 之前调用（MSVCP140 加载顺序）"
        )

    for key, value in _ENV_DEFAULTS:
        os.environ.setdefault(key, value)
    from cutora.core.paths import pin_hf_home
    pin_hf_home()

    eager = (os.environ.get("CUTORA_EAGER_TORCH") or "") == "1"
    path = preload_system_msvc_runtime()
    if os.name == "nt" and not path and not eager:
        # System32 里没有可用的运行库（没装 VC 运行库？）：退回老办法，让 torch 把它该用的那份带进来
        logger.warning("System32 里没找到 msvcp140.dll，退回由 torch 先加载运行库")
        eager = True

    if eager:
        import torch
        torch.set_num_threads(4)
        _CUDA_WARMUP_OK, _CUDA_DEVICE_NAME = _warmup_cuda(torch)
    else:
        _CUDA_WARMUP_OK, _CUDA_DEVICE_NAME = None, (_nvidia_gpu_name() or "cpu")

    path = loaded_msvc_runtime_path()
    if path:
        logger.info("MSVCP140 loaded from %s", path)
    else:
        logger.info("MSVCP140 not mapped after runtime init")
    if msvc_runtime_from_pyside6(path):
        _warn_pyside6_msvc_runtime(path)

    _INITIALIZED = True


def shutdown_qapplication() -> None:
    """Quit a live QApplication before the interpreter finishes.

    On Windows, destroying PySide6 during CPython finalization fastfails
    with 0xC0000409. ``app.exec()`` returns only after quit. A test
    subprocess that builds a window and then returns has to quit the same
    way, while the interpreter is still fully alive.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    app.closeAllWindows()
    app.processEvents()
    app.quit()
    app.processEvents()
    # After quit, Qt's static teardown still fastfails with 0xC0000409 on
    # this machine. tests/qt_isolate.py already ignores that code when the
    # junit result is passed. Hard-exit so a hand-rolled subprocess test
    # sees 0 instead of the finalizer. Call only after the assertions.
    # os._exit skips stdio flush, so a child that printed "ok" would look
    # like it produced nothing.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
