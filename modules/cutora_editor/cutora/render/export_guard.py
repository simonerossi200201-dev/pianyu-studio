# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""
export_guard.py —— 导出保活 / 资源守护

解决的问题
──────────────────────────────────────────────────────────────────────────
并行批量导出时，主窗口会长时间「未响应」并被 Windows 换成一张白板
（DWM ghost window）。此时后台的 render-job 子进程其实还在正常出片，
但用户看不到任何进度，容易误以为电脑挂了而重启，把本可完成的导出打断。

本模块提供三件事，全部**不依赖主窗口的绘制**：

  1. 资源体检 + 降级方案 (plan_parallel_export / ResourceWatchdog)
     导出前先看内存、显存、CPU，决定「开几个进程、几个能用 NVENC」；
     导出中后台线程持续采样，压力超线时写一个降级标记文件，
     子进程读到后会在下一个视频自动关掉 GPU 加速，改用 CPU 编码。
     —— 目标是在崩之前先自己退一步，而不是崩了再救。

  2. 心跳文件 (HeartbeatWriter)
     在输出目录写「导出进行中.lock」，资源管理器里肉眼可见，
     每完成一个视频更新一次；全部结束后改写成「导出完成.txt」。

  3. 独立保活进度窗 (GuardWindow，本文件可单独作为脚本运行)
     python export_guard.py --watch <state.json>
     一个极小的置顶窗口，独立进程、独立事件循环，只 import PySide6 + 标准库，
     不碰 torch / cv2 / 主程序。它同时读两个信息源：
       · 父进程写的 state.json（父进程卡死时会过期）
       · 子进程写的 w*.progress + 输出目录里的成品文件（父进程死了也照样更新）
     所以哪怕主界面整个白板，这个窗口依然会准确显示「还在导，已完成 37/80」。

本模块被设计成「加进去不会让原功能变坏」：任何一步失败都只打印日志并返回
安全的默认值，调用方无需处理异常。
"""

import json
import os
import subprocess
import sys
import threading
import time

# 这个文件也会被当脚本直接跑（保活进度窗：`python export_guard.py --watch …`，
# cwd 是 cutora/render）。那时 sys.path[0] 是脚本所在目录，`cutora` 包根本
# import 不到 —— Q110(1) 把下面这行 import 加到模块顶部之后，保活窗一启动就
# 静默死掉（stderr 接的是 DEVNULL），Luca 导出时「独立小窗口不见了」就是它。
# 先把仓库根目录补进 sys.path 再 import。
if __package__ in (None, ""):
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

IS_WINDOWS = (os.name == "nt")

# ───────────────────────────────────────────────────────────────────────────
#  文件名常量
# ───────────────────────────────────────────────────────────────────────────
LOCK_NAME = "导出进行中.lock"
DONE_NAME = "导出完成.txt"
STATE_NAME = "export_state.json"
DEGRADE_NAME = "degrade.flag"
STOP_NAME = "export_stop.flag"


def guard_should_close(state) -> bool:
    """队列要换下一个工程时，导出小窗该自己关掉。普通导出完成仍留着给用户看。"""
    if not isinstance(state, dict):
        return False
    if state.get("auto_close"):
        return True
    return str(state.get("status") or "") in ("handoff", "closed")


def guard_state_path() -> str:
    from cutora.core.paths import resolve_user_data
    return os.path.join(resolve_user_data(), STATE_NAME)


def mark_guard_auto_close(state_path=None) -> str:
    path = state_path or guard_state_path()
    data = {}
    try:
        if path and os.path.isfile(path):
            loaded = read_json_allow_replace(path)
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}
    data["auto_close"] = True
    data["status"] = "handoff"
    data["updated_at"] = time.time()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _atomic_write(path, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
    return path


def close_watch_window(proc=None, state_path=None) -> None:
    """先写上让小窗自己关，关不掉再结束那个进程。"""
    mark_guard_auto_close(state_path)
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def read_json_allow_replace(path):
    """Load JSON without blocking a writer that os.replace()s the same path.

    On Windows a normal open() handle does not share FILE_SHARE_DELETE, so the
    child's atomic rename raises PermissionError (WinError 5) and the worker
    dies. Open with READ|WRITE|DELETE sharing, then read the bytes.
    """
    if not IS_WINDOWS:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    import ctypes
    import msvcrt
    generic_read = 0x80000000
    share_all = 0x00000007
    open_existing = 3
    file_attribute_normal = 0x80
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    CreateFileW.restype = ctypes.c_void_p
    handle = CreateFileW(
        path, generic_read, share_all, None,
        open_existing, file_attribute_normal, None,
    )
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        raise OSError(ctypes.get_last_error() or 2, "CreateFileW", path)
    fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read()
    return json.loads(raw.decode("utf-8"))


def stop_flag_path(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path or "")), STOP_NAME)


def write_stop_flag(state_path):
    path = stop_flag_path(state_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1")
        return path
    except Exception:
        return path


def stop_flag_exists(state_path):
    try:
        return os.path.isfile(stop_flag_path(state_path))
    except Exception:
        return False


def clear_stop_flag(state_path):
    path = stop_flag_path(state_path)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def terminate_pids(pids):
    """Best-effort terminate of render child PIDs. Argument list, never a shell."""
    for raw in pids or []:
        try:
            pid = int(raw)
        except Exception:
            continue
        if pid <= 0:
            continue
        try:
            if IS_WINDOWS:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                _no_window_run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=8, creationflags=flags,
                )
            else:
                os.kill(pid, 15)
        except Exception:
            pass


def write_serial_export_state(ffmpeg_pid, output_path=None):
    """Minimal export_state.json for serial RenderWorker (adds child_pids)."""
    try:
        from cutora.core.paths import resolve_user_data
        state_dir = resolve_user_data()
        os.makedirs(state_dir, exist_ok=True)
        state_path = os.path.join(state_dir, STATE_NAME)
        pids = []
        try:
            if ffmpeg_pid:
                pids.append(int(ffmpeg_pid))
        except Exception:
            pass
        payload = {
            "status": "running",
            "done": 0,
            "total": 1,
            "pids": pids,
            "child_pids": pids,
            "parent_pid": os.getpid(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "out_dirs": [os.path.dirname(output_path)] if output_path else [],
        }
        _atomic_write(state_path, json.dumps(payload, ensure_ascii=False))
        return state_path
    except Exception:
        return None


def _guard_asset_paths():
    """Repo-relative QSS / icon, no cutora.ui import."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    return (
        os.path.join(repo, "resources", "theme", "theme_dark.qss"),
        os.path.join(repo, "resources", "brand", "app.ico"),
    )

def _log(msg):
    try:
        print(f"[Guard] {msg}", flush=True)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════
#  一、资源探测
# ═══════════════════════════════════════════════════════════════════════════

def system_memory_gb():
    """返回 (总内存 GB, 可用内存 GB)。取不到时返回 (0.0, 0.0)。"""
    try:
        if IS_WINDOWS:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTAT()
            st.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                g = 1024.0 ** 3
                return (st.ullTotalPhys / g, st.ullAvailPhys / g)
            return (0.0, 0.0)
        # 非 Windows：读 /proc/meminfo
        total = avail = 0
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / 1024.0 / 1024.0
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / 1024.0 / 1024.0
        return (total, avail)
    except Exception:
        return (0.0, 0.0)

_NVSMI_CACHE = {"t": 0.0, "val": None}

def gpu_memory_gb(max_age=2.0):
    """返回 (显存总量 GB, 已用 GB, 空闲 GB)。没有 N 卡 / 取不到时返回 None。

    走 nvidia-smi 而不是 torch，是因为这个函数要能在**不加载 torch**的
    子进程和保活窗口里用；而且它读的是全卡真实占用，包含 DWM、浏览器、
    其他实例，比 torch.cuda.memory_reserved() 更贴近「还能不能再开一路」。
    """
    now = time.time()
    if _NVSMI_CACHE["val"] is not None and (now - _NVSMI_CACHE["t"]) < max_age:
        return _NVSMI_CACHE["val"]
    val = None
    try:
        flags = 0
        if IS_WINDOWS:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = _no_window_run(
            ["nvidia-smi",
             "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=flags,
        )
        if out.returncode == 0:
            line = (out.stdout or "").strip().splitlines()[0]
            t, u, f = [float(x.strip()) / 1024.0 for x in line.split(",")[:3]]
            val = (t, u, f)
    except Exception:
        val = None
    _NVSMI_CACHE["t"] = now
    _NVSMI_CACHE["val"] = val
    return val

def pid_alive(pid):
    """进程是否还活着。跨平台，且不依赖 psutil。"""
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        if IS_WINDOWS:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == 259   # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def disable_console_quickedit():
    """关掉 Windows 控制台的「快速编辑模式」。

    这是一个非常隐蔽的假死来源：控制台开着 QuickEdit 时，用户只要在窗口里
    随手点一下（哪怕只是想复制一行日志），控制台就进入选择状态，
    此后程序的**下一次 print 会被无限期阻塞**。如果这次 print 恰好发生在
    Qt 主线程里，整个界面立刻变成「未响应」→ 白板，直到用户按下 Esc。

    与导出本身无关，但它和「白板」的表现一模一样，所以顺手一起关掉。
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        if not h or h == ctypes.c_void_p(-1).value:
            return False
        mode = ctypes.c_ulong()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False                    # 没有控制台（pythonw / 打包后）
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_INSERT_MODE = 0x0020
        ENABLE_EXTENDED_FLAGS = 0x0080
        new = (mode.value & ~ENABLE_QUICK_EDIT_MODE & ~ENABLE_INSERT_MODE) | ENABLE_EXTENDED_FLAGS
        if new != mode.value:
            k32.SetConsoleMode(h, new)
            _log("已关闭控制台快速编辑模式（避免误点终端导致界面假死）")
        return True
    except Exception:
        return False

def boost_main_process(on=True):
    """导出期间把主进程（GUI）提到 ABOVE_NORMAL 优先级。

    子进程是 BELOW_NORMAL，主进程是 ABOVE_NORMAL —— 中间隔了两档。
    渲染因此慢不了几个百分点，但 Qt 主线程能稳定抢到时间片把窗口画出来，
    而画得出来，Windows 就不会判定「未响应」，也就不会套上那层白板。
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        ABOVE_NORMAL = 0x00008000
        NORMAL = 0x00000020
        k32 = ctypes.windll.kernel32
        return bool(k32.SetPriorityClass(k32.GetCurrentProcess(),
                                         ABOVE_NORMAL if on else NORMAL))
    except Exception:
        return False

def keep_system_awake(on=True):
    """导出期间阻止系统休眠（屏幕仍可以关）。批量导出动辄十几分钟，
    中途睡过去会让用户以为「电脑死机了」。"""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED) if on else ES_CONTINUOUS
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
        return True
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════════════════════
#  二、导出前体检 / 降级方案
# ═══════════════════════════════════════════════════════════════════════════

# Fallback when no RSS history: 1080×1920 worker ≈ 3.0 GB (measured 2.7 + pad).
# Never use a hardcoded 1.2 — that over-admitted workers 2.24× and TDR'd NVENC.
MEM_PER_WORKER_GB = 3.0
# 实测（2026-09-18，720×1280 拼接时间线、27 个源全开）每 worker 峰值 2.8~3.06 GB；
# 解码器改按需开之后会降，但地板不许低于 2.0 —— 解释器 + Qt + 607 个字体
# + 编码器本身就有这么多。真实样本进来后以 p90 为准。
MEM_PER_WORKER_MIN_GB = 2.5
REF_CANVAS_PX = 1080 * 1920
WORKER_RSS_NAME = "worker_rss.json"
# 给系统 / DWM / 浏览器留的余量。低于这个数 Windows 会开始换页，
# 主进程的页被换出去之后，界面就再也画不出来了。
# 留给系统 + DWM + 主进程自身增长的余量。3.0 GB 时 5 个 worker 把 14.1 GB
# 可用吃到 1.5 GB，桌面黑屏两次（2026-09-18）。6.0 = 在那台 32 GB 机器上
# 只会派 2~3 个。
MEM_HEADROOM_GB = 6.0
# Per parallel worker: compose_parallel.COMPOSE_INFLIGHT_BYTES (256 MiB).
# Q11's overlay queue is on top of mem_per_worker_gb; omitting it is the
# same class of under-count that over-admitted workers on 2026-09-18.
COMPOSE_INFLIGHT_PER_WORKER_GB = 256 / 1024.0
# Q180：整帧并行合成时解码线程取好、还没画的源帧（compose_parallel.REPLAY_INFLIGHT_BYTES）
REPLAY_INFLIGHT_PER_WORKER_GB = 96 / 1024.0
# 在编期间可用内存低于这个数就「甩负载」：杀最晚启动的 worker，它的视频
# 退回串行重做。宁可慢，也不让系统换页到黑屏。
SHED_RAM_GB = 1.5
PAUSE_RAM_GB = 2.0
SERIAL_RAM_GB = 1.0

# 一路 NVENC 会话（编码器上下文 + 参考帧）大约要这么多显存
VRAM_PER_NVENC_GB = 0.25
# 显存余量。DWM 合成桌面本身需要显存，显存被吃干净时 Windows 会
# 无法为窗口分配重定向表面 —— 这正是「整窗变白板」的直接原因之一。
VRAM_HEADROOM_GB = 1.0
# 消费级 GeForce 驱动允许的并发 NVENC 会话上限：历史上 2 → 3 → 5 → 8，随驱动版本变。
# Q183：不再让用户猜 —— 启动后在后台用本机 ffmpeg 实测一次「能同时开几路」，按驱动版本 +
# 显卡型号缓存；设置里的数字 0 = 自动（实测），>0 才是手动覆盖（个别用户需要时才动）。
# 还没实测到结果之前按 3 算（老默认值，所有驱动都开得起）。
NVENC_MAX_SESSIONS = 3
NVENC_SESSIONS_KEY = "nvenc_max_sessions"
NVENC_MANUAL_KEY = "nvenc_manual"
NVENC_PROBE_KEY = "nvenc_probe"
NVENC_PROBE_MAX = 8
NVENC_PROBE_MAX_AGE_DAYS = 30
_NVENC_PROBE_LOCK = threading.Lock()
_NVENC_PROBE_STATE = {"running": False}


def nvenc_manual_override():
    """设置里手动指定的并发数；None = 自动。
    兼容老设置：以前设置对话框每次保存都会写入默认值 3，所以存着的 3 不算用户的选择。"""
    try:
        from cutora.core.history import _load_app_settings
        s = _load_app_settings() or {}
    except Exception:
        return None
    try:
        raw = int(s.get(NVENC_SESSIONS_KEY, 0) or 0)
    except Exception:
        raw = 0
    if raw <= 0:
        return None
    if s.get(NVENC_MANUAL_KEY) or raw != NVENC_MAX_SESSIONS:
        return max(1, min(8, raw))
    return None


def nvenc_probe_result():
    """缓存的实测结果 {"sessions", "driver", "gpu", "when"}；没有 / 过期 / 驱动或显卡换了 → None。"""
    try:
        from cutora.core.history import _load_app_settings
        rec = (_load_app_settings() or {}).get(NVENC_PROBE_KEY)
    except Exception:
        return None
    if not isinstance(rec, dict) or int(rec.get("sessions", 0) or 0) <= 0:
        return None
    ident = gpu_identity()
    if ident is not None and (rec.get("driver"), rec.get("gpu")) != ident:
        return None
    try:
        # 测出来很少（<5）多半是当时有别的程序占着会话（OBS、浏览器）—— 隔天再测一次；
        # 测到 5 路以上基本就是驱动上限了，30 天内不用再测
        max_days = NVENC_PROBE_MAX_AGE_DAYS if int(rec["sessions"]) >= 5 else 1
        if time.time() - float(rec.get("when", 0)) > max_days * 86400:
            return None
    except Exception:
        return None
    return rec


def nvenc_max_sessions() -> int:
    """规划并行导出时用的 NVENC 并发上限：手动覆盖 > 本机实测 > 保守默认 3。"""
    n = nvenc_manual_override()
    if n is not None:
        return n
    rec = nvenc_probe_result()
    if rec is not None:
        return max(1, min(8, int(rec["sessions"])))
    return NVENC_MAX_SESSIONS


def nvenc_sessions_source() -> str:
    """给日志 / 设置页看的一句话：这个数是哪来的。"""
    n = nvenc_manual_override()
    if n is not None:
        return f"手动设置 {n} 路"
    rec = nvenc_probe_result()
    if rec is not None:
        return f"本机实测 {int(rec['sessions'])} 路（驱动 {rec.get('driver') or '?'}）"
    return f"尚未实测，按 {NVENC_MAX_SESSIONS} 路"


def save_nvenc_max_sessions(n: int) -> None:
    """设置页保存：0 = 自动，>0 = 手动覆盖。"""
    from cutora.core.history import _load_app_settings, _save_app_settings
    s = _load_app_settings() or {}
    n = max(0, min(8, int(n)))
    s[NVENC_SESSIONS_KEY] = n
    s[NVENC_MANUAL_KEY] = bool(n > 0)
    _save_app_settings(s)


_GPU_IDENT_CACHE = {"t": 0.0, "val": None}


def gpu_identity(max_age=60.0):
    """(驱动版本, 显卡名) —— nvidia-smi 取；没有 N 卡返回 None。"""
    now = time.time()
    if (now - _GPU_IDENT_CACHE["t"]) < max_age and _GPU_IDENT_CACHE["t"] > 0:
        return _GPU_IDENT_CACHE["val"]
    val = None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        out = _no_window_run(
            ["nvidia-smi", "--query-gpu=driver_version,name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4, creationflags=flags)
        if out.returncode == 0:
            parts = [x.strip() for x in (out.stdout or "").strip().splitlines()[0].split(",", 1)]
            if len(parts) == 2 and parts[0]:
                val = (parts[0], parts[1])
    except Exception:
        val = None
    _GPU_IDENT_CACHE["t"] = now
    _GPU_IDENT_CACHE["val"] = val
    return val


def probe_nvenc_sessions(max_try: int = NVENC_PROBE_MAX, hold_seconds: float = 4.0, _spawn=None) -> int:
    """实测本机此刻能同时开几路 NVENC：一次拉起 max_try 个 ffmpeg，各自按实时速度编 hold_seconds 秒
    的合成画面（-re 保证它们的会话确实同时存在），数有几个正常结束。超出驱动上限的那几个会在
    打开会话时失败（OpenEncodeSessionEx: out of memory）。其他程序（OBS、浏览器）占着的会话
    也会被扣掉 —— 这正是我们要的「当下情况」。没有 ffmpeg / 没有 N 卡 → 0。"""
    if _spawn is None:
        from cutora.core.paths import FFMPEG
        if not os.path.isfile(FFMPEG):
            return 0
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-re", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30",
               "-t", str(float(hold_seconds)), "-c:v", "h264_nvenc", "-preset", "p1", "-f", "null", "-"]

        def _spawn(_i):
            return _no_window_popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, creationflags=flags)
    procs = []
    for i in range(max(1, int(max_try))):
        try:
            procs.append(_spawn(i))
        except Exception:
            break
    ok = 0
    deadline = time.time() + float(hold_seconds) + 20.0
    for p in procs:
        try:
            p.communicate(timeout=max(1.0, deadline - time.time()))
            if p.returncode == 0:
                ok += 1
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    return ok


def ensure_nvenc_probe(force: bool = False, log=print) -> int:
    """启动后台调用：缓存有效就直接返回；否则实测并写入设置。返回会话数（0 = 没测出来）。
    一次只跑一个；导出进行中不要调（会和导出抢会话，测出来偏小）。"""
    if not force:
        rec = nvenc_probe_result()
        if rec is not None:
            return int(rec["sessions"])
    if not _NVENC_PROBE_LOCK.acquire(blocking=False):
        return 0
    try:
        _NVENC_PROBE_STATE["running"] = True
        ident = gpu_identity()
        if ident is None:
            return 0
        n = probe_nvenc_sessions()
        if n <= 0:
            log("[NVENC] 实测失败：一路 NVENC 都开不起来（ffmpeg 缺失或显卡不支持），按 %d 路" % NVENC_MAX_SESSIONS)
            return 0
        try:
            from cutora.core.history import _load_app_settings, _save_app_settings
            s = _load_app_settings() or {}
            s[NVENC_PROBE_KEY] = {"sessions": int(n), "driver": ident[0], "gpu": ident[1], "when": time.time()}
            _save_app_settings(s)
        except Exception:
            pass
        log("[NVENC] 实测本机可同时开 %d 路 NVENC（%s，驱动 %s）%s" % (
            n, ident[1], ident[0], "，已到探测上限" if n >= NVENC_PROBE_MAX else ""))
        return int(n)
    finally:
        _NVENC_PROBE_STATE["running"] = False
        _NVENC_PROBE_LOCK.release()


def _rss_log_path() -> str:
    from cutora.core.paths import ensure_user_layout, user_logs_dir
    ensure_user_layout()
    folder = os.path.join(user_logs_dir(), "export")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, WORKER_RSS_NAME)


def load_measured_rss() -> dict:
    """Last recorded RSS samples. {samples: [{peak_gb, canvas_px, ...}], p90}."""
    path = _rss_log_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# 一个渲染子进程光是 Python + PySide6 + 232 个字体就远超这个数。
# 低于它的样本不是真实测量（BUG-41：以前量到的是 venv 启动壳，0.004 GB）。
RSS_SAMPLE_MIN_GB = 0.2
# Q130：同画布样本够这么多条才信它（一次批量导出就能攒够）
RSS_SAME_CANVAS_MIN = 6
RSS_EXACT_SAFETY = 1.25
RSS_EXACT_FLOOR_GB = 1.2


def _p90(vals):
    vals = sorted(v for v in vals if v > 0)
    if not vals:
        return None
    idx = max(0, min(len(vals) - 1, int(round(0.9 * (len(vals) - 1)))))
    return vals[idx]


def measured_rss_p90(canvas_px=None) -> tuple[float | None, int | None]:
    """(p90 GB, 样本画布像素数)。老接口：全体样本的 p90 + 最后一条样本的画布。"""
    p90, px, _exact = measured_rss_for_canvas(canvas_px)
    return p90, px


def measured_rss_for_canvas(canvas_px=None):
    """Q130：优先用**同画布尺寸**的样本。

    2026-09-25 实机：worker_rss.json 里 28 条 720×1280 拼接样本（峰值 2.8~3.0 GB，
    27 个源全开的老日子）+ 12 条 1080×1920 样本（今天，全是 1.59 GB）。老算法取
    全体 p90 = 2.08，再按像素比 ×2.25 → 「每进程 4.8 GB」→ 12 GB 可用只派 1 个进程
    → 22 条视频串行 43 分钟。RSS 的大头是解释器 + Qt + 字体 + 解码器，根本不随
    画布像素线性长，同画布实测才是准的。

    返回 (p90, 画布像素, exact)：exact=True 表示用的是 ≥ RSS_SAME_CANVAS_MIN 条
    同画布样本；False 走老逻辑（全体 p90 + 按像素缩放）。
    """
    data = load_measured_rss()
    rows = []
    last_px = None
    samples = [r for r in (data.get("samples") or []) if isinstance(r, dict)]
    # Q180：在途缓冲收小之后（gen 2）的样本够数就只用它们；不够时老样本减去收掉的那部分
    gen2 = [r for r in samples if int(r.get("gen") or 1) >= RSS_SAMPLE_GEN]
    gen2_same = [r for r in gen2 if canvas_px and str(r.get("canvas_px")) == str(int(canvas_px))]
    use_gen2 = len(gen2_same) >= RSS_SAME_CANVAS_MIN
    for row in samples:
        gen = int(row.get("gen") or 1)
        if use_gen2 and gen < RSS_SAMPLE_GEN:
            continue
        try:
            v = float(row.get("peak_gb") or 0.0)
        except (TypeError, ValueError):
            continue
        if v < RSS_SAMPLE_MIN_GB:
            continue
        if gen < RSS_SAMPLE_GEN and row.get("at") and "gen" not in row:
            # 老版本真实记下的样本（有时间戳、没有代数）：那时在途缓冲大，按收掉的量折算
            v = max(RSS_SAMPLE_MIN_GB, v - LEGACY_INFLIGHT_DISCOUNT_GB)
        px = None
        if row.get("canvas_px"):
            try:
                px = int(row["canvas_px"])
            except (TypeError, ValueError):
                px = None
        rows.append((v, px))
        if px:
            last_px = px
    if canvas_px:
        same = [v for v, px in rows if px == int(canvas_px)]
        if len(same) >= RSS_SAME_CANVAS_MIN:
            return _p90(same), int(canvas_px), True
    p90 = _p90([v for v, _px in rows])
    return p90, last_px, False


# Q180：样本代数。gen 2 = 合成在途缓冲收小后（成品 ≤ 3×线程、源帧 ≤ 96 MB、批量时每进程 ≤ 4 线程）量的；
# 之前的老样本（成品最多 ~260 MB + 源帧 192 MB + 6~8 线程的临时图）按收掉的量折算
RSS_SAMPLE_GEN = 2
LEGACY_INFLIGHT_DISCOUNT_GB = 0.35


def record_worker_rss(peak_gb: float, canvas_wh=None, pid: int | None = None) -> None:
    if peak_gb <= 0 or peak_gb < RSS_SAMPLE_MIN_GB:
        return
    path = _rss_log_path()
    data = load_measured_rss()
    samples = list(data.get("samples") or [])
    w, h = 1080, 1920
    if canvas_wh and len(canvas_wh) >= 2:
        w, h = int(canvas_wh[0] or 1080), int(canvas_wh[1] or 1920)
    samples.append({
        "peak_gb": round(float(peak_gb), 3),
        "canvas_px": int(w) * int(h),
        "canvas": [int(w), int(h)],
        "pid": int(pid or 0),
        "at": time.time(),
        "gen": RSS_SAMPLE_GEN,
    })
    samples = samples[-40:]
    payload = {"samples": samples}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def mem_per_worker_gb(canvas_wh=None, measured_gb=None, measured_canvas_wh=None) -> float:
    """Per-worker RAM. Prefer measured P90, else 3.0, scaled by canvas pixels."""
    w, h = 1080, 1920
    if canvas_wh and len(canvas_wh) >= 2:
        try:
            w, h = int(canvas_wh[0] or 1080), int(canvas_wh[1] or 1920)
        except (TypeError, ValueError):
            w, h = 1080, 1920
    px = max(1, w * h)
    gb = measured_gb
    src_px = None
    if gb is None:
        gb, src_px, exact = measured_rss_for_canvas(px)
        if exact and gb:
            # 同画布实测：p90 再加 25% 安全余量，地板 RSS_EXACT_FLOOR_GB。
            # 不再套 2.5 GB 的老地板 —— 那是 27 路解码器全开时代量出来的。
            return max(RSS_EXACT_FLOOR_GB, float(gb) * RSS_EXACT_SAFETY)
    if measured_canvas_wh and len(measured_canvas_wh) >= 2:
        try:
            src_px = int(measured_canvas_wh[0]) * int(measured_canvas_wh[1])
        except (TypeError, ValueError):
            src_px = src_px
    if gb is None or gb <= 0:
        gb = MEM_PER_WORKER_GB * (px / float(REF_CANVAS_PX))
    else:
        base_px = int(src_px or REF_CANVAS_PX)
        gb = float(gb) * (px / float(max(1, base_px)))
    return max(MEM_PER_WORKER_MIN_GB, float(gb))


def dominant_canvas(tasks):
    """这批视频里最多的画布尺寸 (w, h)；一样多取像素大的。读不到 → None。"""
    from collections import Counter
    cnt = Counter()
    for t in tasks or []:
        try:
            tl = t.get("timeline") if isinstance(t, dict) else None
            if isinstance(tl, dict):
                w, h = int(tl.get("width") or 0), int(tl.get("height") or 0)
            elif tl is not None:
                w, h = int(getattr(tl, "width", 0) or 0), int(getattr(tl, "height", 0) or 0)
            else:
                continue
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            cnt[(w, h)] += 1
    if not cnt:
        return None
    return max(cnt.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))[0]


def process_rss_gb(pid: int) -> float:
    """Working-set GB for pid. 0 if unreadable."""
    if not pid:
        return 0.0
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return 0.0
            try:
                pmc = _PMC()
                pmc.cb = ctypes.sizeof(_PMC)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                    return 0.0
                return float(pmc.PeakWorkingSetSize) / (1024.0 ** 3)
            finally:
                k32.CloseHandle(handle)
        st = os.stat("/proc/%d/statm" % int(pid))
        pages = int(open("/proc/%d/statm" % int(pid), encoding="utf-8").read().split()[1])
        return pages * float(os.sysconf("SC_PAGE_SIZE")) / (1024.0 ** 3)
    except Exception:
        return 0.0


def memory_hogs_note(top_n: int = 6) -> str:
    """「内存是谁占的」一句话：本进程（主界面）工作集 + 全机前几名。给导出前体检的日志用 ——
    用户问「31.8 GB 的机器怎么只剩 15 GB」时，答案应该已经在日志里。psutil 不在就用 tasklist。"""
    g = 1024.0 ** 3
    rows = []
    me = 0.0
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                rss = float(p.info["memory_info"].rss)
                name = p.info["name"] or "?"
                if p.info["pid"] == os.getpid():
                    me = rss
                    name = name + "(CUTORA 主界面)"
                rows.append((rss, name))
            except Exception:
                continue
    except Exception:
        rows = []
    if not rows and IS_WINDOWS:
        try:
            import csv
            import io as _io
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            # tasklist 在中文 Windows 上是 ANSI/GBK。text=True 会跟 PYTHONUTF8 走 UTF-8，读到一半就抛掉。
            out = _no_window_run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                 timeout=8, creationflags=flags)
            text = (out.stdout or b"").decode("mbcs", errors="replace")
            for rec in csv.reader(_io.StringIO(text)):
                if len(rec) < 5:
                    continue
                try:
                    kb = float(rec[4].replace(",", "").replace(".", "").split()[0])
                except Exception:
                    continue
                name = rec[0]
                if rec[1].strip() == str(os.getpid()):
                    me = kb * 1024.0
                    name = name + "(CUTORA 主界面)"
                rows.append((kb * 1024.0, name))
        except Exception:
            rows = []
    if not rows:
        return ""
    # 同名进程合并（浏览器几十个子进程）
    agg = {}
    for rss, name in rows:
        agg[name] = agg.get(name, 0.0) + rss
    top = sorted(agg.items(), key=lambda kv: -kv[1])[:top_n]
    parts = ["%s %.1f GB" % (n, v / g) for n, v in top]
    head = "CUTORA 主界面占 %.1f GB；" % (me / g) if me else ""
    return head + "全机占内存前 %d：%s" % (len(top), "、".join(parts))


def gui_memory_note() -> str:
    """主界面进程的内存都花在哪：RSS、Python 堆里 numpy 数组 / QImage 的总量（按持有者粗分）、帧缓存。
    走一遍 gc.get_objects() 大概几十到几百毫秒，只在导出前体检时做一次。"""
    import gc
    g = 1024.0 ** 3
    out = []
    try:
        import psutil
        rss = psutil.Process().memory_info().rss / g
        out.append("主界面 RSS %.2f GB" % rss)
    except Exception:
        pass
    np_bytes = 0
    np_n = 0
    qi_bytes = 0
    qi_n = 0
    big = []
    try:
        import numpy as _np
        try:
            from PySide6.QtGui import QImage as _QI
        except Exception:
            _QI = None
        seen = set()

        def _arr(a):
            nonlocal np_bytes, np_n
            if id(a) in seen:
                return
            seen.add(id(a))
            if a.base is not None and isinstance(a.base, _np.ndarray):
                return                                       # 视图不重复算
            nb = int(a.nbytes)
            np_bytes += nb
            np_n += 1
            if nb >= 64 << 20:
                big.append((nb, "ndarray %s %s" % (tuple(a.shape), a.dtype)))

        # numpy 数组本身不进 gc 跟踪，但装着它们的 dict / list / tuple 进 —— 从容器里找
        for o in gc.get_objects():
            try:
                if isinstance(o, dict):
                    vals = o.values()
                elif isinstance(o, (list, tuple, set)):
                    vals = o
                elif _QI is not None and isinstance(o, _QI):
                    nb = int(o.sizeInBytes())
                    qi_bytes += nb
                    qi_n += 1
                    if nb >= 64 << 20:
                        big.append((nb, "QImage %dx%d" % (o.width(), o.height())))
                    continue
                else:
                    continue
                for v in vals:
                    if isinstance(v, _np.ndarray):
                        _arr(v)
                    elif isinstance(v, (list, tuple)) and len(v) <= 8:
                        for w in v:
                            if isinstance(w, _np.ndarray):
                                _arr(w)
            except Exception:
                continue
    except Exception:
        pass
    out.append("Python 堆里 numpy 数组 %d 个共 %.2f GB、QImage %d 张共 %.2f GB" % (np_n, np_bytes / g, qi_n, qi_bytes / g))
    try:
        from cutora.media.frame_cache import cache as _fc
        c = _fc()
        out.append("预乘帧缓存 %.2f / %.2f GB" % (float(getattr(c, "_bytes", 0)) / g, float(getattr(c, "budget", 0)) / g))
    except Exception:
        pass
    try:
        import sys as _sys
        if "torch" in _sys.modules:
            import torch as _t
            out.append("torch 已加载（CUDA %s）" % ("已初始化" if _t.cuda.is_initialized() else "未初始化"))
    except Exception:
        pass
    if big:
        big.sort(reverse=True)
        out.append("≥64 MB 的大块：" + "、".join("%s %.0f MB" % (n, b / 1048576.0) for b, n in big[:5]))
    try:
        out.append(mapped_memory_note())
    except Exception:
        pass
    return "；".join(x for x in out if x)


def _mapped_memory_fallback(g: float, why: str) -> str:
    """psutil 不在 / memory_maps 不可用时（Luca 的运行时里没有 psutil）：纯 ctypes 走 Windows API ——
    工作集 / 私有提交（GetProcessMemoryInfo）+ 已加载 DLL 按家族的磁盘大小（EnumProcessModulesEx）。
    磁盘大小不等于常驻，但 torch / CUDA 的 DLL 一旦加载，常驻的大头通常就是它们。"""
    out = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _PMC_EX(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                            ("PrivateUsage", ctypes.c_size_t)]
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            h = k32.GetCurrentProcess()
            pmc = _PMC_EX()
            pmc.cb = ctypes.sizeof(_PMC_EX)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC_EX), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                ws = float(pmc.WorkingSetSize) / g
                priv = float(pmc.PrivateUsage) / g
                out.append("工作集 %.2f GB（峰值 %.2f GB），私有提交 %.2f GB" % (ws, float(pmc.PeakWorkingSetSize) / g, priv))
            n = 2048
            arr = (wintypes.HMODULE * n)()
            needed = wintypes.DWORD(0)
            psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                                   ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
            psapi.EnumProcessModulesEx.restype = wintypes.BOOL
            psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
            psapi.GetModuleFileNameExW.restype = wintypes.DWORD
            if psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(needed), 0x03):
                cnt = min(n, needed.value // ctypes.sizeof(wintypes.HMODULE))
                fam = {}
                total = 0.0
                buf = ctypes.create_unicode_buffer(32768)
                for i in range(cnt):
                    if psapi.GetModuleFileNameExW(h, arr[i], buf, 32768):
                        path = buf.value
                        try:
                            sz = float(os.path.getsize(path))
                        except Exception:
                            continue
                        total += sz
                        key = _dll_family(os.path.basename(path))
                        fam[key] = fam.get(key, 0.0) + sz
                top = sorted(fam.items(), key=lambda kv: -kv[1])[:10]
                out.append("已加载 %d 个模块共 %.2f GB（磁盘大小），按家族：%s" % (
                    cnt, total / g, "、".join("%s %.2f GB" % (k, v / g) for k, v in top if v >= 16 << 20)))
        except Exception as e:
            out.append("（Windows API 读取失败：%s）" % (repr(e)[:120],))
    else:
        try:
            import resource
            out.append("RSS 峰值 %.2f GB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024.0 / g))
        except Exception:
            pass
    try:
        mm = mimalloc_memory_note()
        if mm:
            out.append(mm)
    except Exception:
        pass
    try:
        tc = thread_census_note()
        if tc:
            out.append(tc)
    except Exception:
        pass
    return "；".join(out)


_THREAD_CPU_PREV = {"v": None, "t": None, "thr": None}


def python_hotspots_note(seconds: float = 2.0, samples: int = 40) -> str:
    """采样式剖析：2 秒里抓 40 次所有 Python 线程的栈，按最内层 cutora 代码位置计数 —— 空闲时谁在转。"""
    import time as _time
    import threading as _th
    counts = {}
    names = {t.ident: t.name for t in _th.enumerate()}
    me = _th.get_ident()
    gap = max(0.01, float(seconds) / max(1, int(samples)))
    for _ in range(int(samples)):
        try:
            frames = sys._current_frames()
        except Exception:
            break
        for tid, fr in frames.items():
            if tid == me:
                continue
            if fr.f_code.co_name in _WAIT_FUNCS:
                continue                                       # 最内层在等东西（sleep / wait / recv…）→ 不算热点
            f = fr
            loc = None
            depth = 0
            while f is not None and depth < 40:
                fn = f.f_code.co_filename.replace("\\", "/")
                if "/cutora/" in fn and "/site-packages/" not in fn:
                    loc = "%s:%d %s" % (fn.rsplit("/cutora/", 1)[1], f.f_lineno, f.f_code.co_name)
                    break
                f = f.f_back
                depth += 1
            if loc is None:
                fn = fr.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
                loc = "%s:%d %s" % (fn, fr.f_lineno, fr.f_code.co_name)
            key = "%s @ %s" % (loc, names.get(tid, str(tid)))
            counts[key] = counts.get(key, 0) + 1
        _time.sleep(gap)
    if not counts:
        return "Python 热点：无（所有 Python 线程都在等）"
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    return "Python 热点（%d 次采样）：" % samples + "、".join("%s ×%d" % (k, v) for k, v in top)


_WAIT_FUNCS = frozenset(("wait", "_wait_for_tstate_lock", "select", "poll", "sleep", "get", "recv", "wait_for",
                         "acquire", "readinto", "read", "exec", "exec_", "join", "_recv", "_recv_bytes", "msleep",
                         "usleep", "waitForReadyRead", "getch"))


def thread_census_note(top_n: int = 10) -> str:
    """Windows：进程里的线程按「起始地址所在模块」归类 —— 266 条线程到底是谁开的（FFmpeg 解码器、Qt、显卡驱动、Python）。
    Toolhelp 快照 + NtQueryInformationThread(ThreadQuerySetWin32StartAddress) + 模块地址范围。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        psapi = ctypes.WinDLL("psapi")

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                        ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                        ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]

        class MODULEINFO(ctypes.Structure):
            _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wintypes.DWORD), ("EntryPoint", ctypes.c_void_p)]

        pid = os.getpid()
        hproc = k32.GetCurrentProcess()
        # 模块地址范围
        n = 2048
        arr = (wintypes.HMODULE * n)()
        needed = wintypes.DWORD(0)
        psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, wintypes.HMODULE, ctypes.POINTER(MODULEINFO), wintypes.DWORD]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        ranges = []
        if psapi.EnumProcessModulesEx(hproc, arr, ctypes.sizeof(arr), ctypes.byref(needed), 0x03):
            cnt = min(n, needed.value // ctypes.sizeof(wintypes.HMODULE))
            buf = ctypes.create_unicode_buffer(32768)
            for i in range(cnt):
                mi = MODULEINFO()
                if psapi.GetModuleInformation(hproc, arr[i], ctypes.byref(mi), ctypes.sizeof(mi)) and \
                        psapi.GetModuleFileNameExW(hproc, arr[i], buf, 32768):
                    base = int(mi.lpBaseOfDll or 0)
                    ranges.append((base, base + int(mi.SizeOfImage), os.path.basename(buf.value)))
        ranges.sort()
        TH32CS_SNAPTHREAD = 0x4
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return ""
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
        k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
        k32.OpenThread.restype = wintypes.HANDLE
        k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        nt.NtQueryInformationThread.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                                ctypes.POINTER(wintypes.ULONG)]
        fam = {}
        cpu_by_mod = {}
        cpu_by_tid = {}
        total = 0
        k32.GetThreadTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                       ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        try:
            k32.GetThreadDescription.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
            k32.GetThreadDescription.restype = ctypes.c_long
            k32.LocalFree.argtypes = [ctypes.c_void_p]
            _has_desc = True
        except Exception:
            _has_desc = False
        ok = k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                total += 1
                name = "?"
                h = k32.OpenThread(0x0040, False, te.th32ThreadID)      # THREAD_QUERY_INFORMATION
                if h:
                    addr = ctypes.c_void_p(0)
                    if nt.NtQueryInformationThread(h, 9, ctypes.byref(addr), ctypes.sizeof(addr), None) == 0:
                        a = int(addr.value or 0)
                        for lo, hi, nm in ranges:
                            if lo <= a < hi:
                                name = nm
                                break
                    try:
                        ft = [wintypes.FILETIME() for _ in range(4)]
                        if k32.GetThreadTimes(h, *[ctypes.byref(f) for f in ft]):
                            sec = sum(((f.dwHighDateTime << 32) | f.dwLowDateTime) for f in ft[2:]) / 1e7
                            cpu_by_mod[name] = cpu_by_mod.get(name, 0.0) + sec
                            desc = ""
                            if _has_desc:
                                try:
                                    pdesc = wintypes.LPWSTR()
                                    if k32.GetThreadDescription(h, ctypes.byref(pdesc)) >= 0 and pdesc.value:
                                        desc = pdesc.value[:40]
                                        k32.LocalFree(pdesc)
                                except Exception:
                                    desc = ""
                            cpu_by_tid[int(te.th32ThreadID)] = (sec, name, desc)
                    except Exception:
                        pass
                    k32.CloseHandle(h)
                fam[name] = fam.get(name, 0) + 1
            ok = k32.Thread32Next(snap, ctypes.byref(te))
        k32.CloseHandle(snap)
        top = sorted(fam.items(), key=lambda kv: -kv[1])[:top_n]
        txt = "线程 %d 条，按起始模块：%s" % (total, "、".join("%s %d" % (k, v) for k, v in top))
        # 两次采样之间各模块 / 各线程吃了多少 CPU（累计秒的差）—— 空闲时还在烧 CPU 的是谁
        import time as _time
        now = _time.monotonic()
        prev = _THREAD_CPU_PREV.get("v")
        prev_t = _THREAD_CPU_PREV.get("t")
        prev_thr = _THREAD_CPU_PREV.get("thr") or {}
        _THREAD_CPU_PREV["v"] = cpu_by_mod
        _THREAD_CPU_PREV["thr"] = cpu_by_tid
        _THREAD_CPU_PREV["t"] = now
        if prev is not None and prev_t is not None and now > prev_t + 1.0:
            dt = now - prev_t
            delta = sorted(((k, cpu_by_mod.get(k, 0.0) - prev.get(k, 0.0)) for k in cpu_by_mod), key=lambda kv: -kv[1])
            delta = [(k, v) for k, v in delta if v > 0.5][:6]
            if delta:
                txt += "；期间线程 CPU 按模块：" + "、".join("%s %.2f 核" % (k, v / dt) for k, v in delta)
            dthr = sorted(((tid, cpu_by_tid[tid][0] - prev_thr.get(tid, (0.0,))[0], cpu_by_tid[tid][1], cpu_by_tid[tid][2])
                           for tid in cpu_by_tid), key=lambda kv: -kv[1])
            dthr = [x for x in dthr if x[1] > 0.3][:6]
            if dthr:
                import threading as _th
                pynames = {t.native_id: t.name for t in _th.enumerate() if getattr(t, "native_id", None)}
                txt += "；最忙的线程：" + "、".join(
                    "#%d %s(%s) %.2f 核" % (tid, pynames.get(tid) or desc or "-", mod, sec / dt) for tid, sec, mod, desc in dthr)
        return txt
    except Exception as e:
        return "（线程归类失败：%s）" % (repr(e)[:80],)


def mimalloc_memory_note(max_chars: int = 1800) -> str:
    """mimalloc 的视角：进程里所有 C 堆分配（Python、Qt、OpenCV、FFmpeg 都经它）现在真正活着的有多少、
    它自己攒着没还给系统的有多少。mi_process_info + mi_stats_print_out（只在 Windows 打过补丁的 python.exe 上有）。"""
    if os.name != "nt":
        return ""
    import ctypes
    try:
        lib = ctypes.WinDLL("mimalloc.dll")
    except Exception:
        return ""
    parts = []
    try:
        sz = ctypes.c_size_t
        vals = [sz() for _ in range(8)]
        lib.mi_process_info.argtypes = [ctypes.POINTER(sz)] * 8
        lib.mi_process_info.restype = None
        lib.mi_process_info(*[ctypes.byref(v) for v in vals])
        g = 1024.0 ** 3
        parts.append("mimalloc：RSS %.2f GB（峰 %.2f），已提交 %.2f GB（峰 %.2f），缺页 %d" % (
            vals[3].value / g, vals[4].value / g, vals[5].value / g, vals[6].value / g, vals[7].value))
    except Exception:
        pass
    try:
        buf = []
        CB = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)

        def _cb(msg, _arg):
            try:
                buf.append(msg.decode("utf-8", "replace") if msg else "")
            except Exception:
                pass
        cb = CB(_cb)
        lib.mi_stats_print_out.argtypes = [CB, ctypes.c_void_p]
        lib.mi_stats_print_out.restype = None
        lib.mi_stats_print_out(cb, None)
        text = "".join(buf)
        keep = []
        for ln in text.splitlines():
            low = ln.strip().lower()
            if not low:
                continue
            if low.startswith(("reserved", "committed", "reset", "purged", "touched", "segments", "pages", "arenas",
                               "malloc req", "normal", "huge", "large", "total", "threads", "searches", "numa",
                               "elapsed", "process", "peak", "mmaps", "commits", "slices")):
                keep.append(" ".join(ln.split()))
        txt = " | ".join(keep)
        if txt:
            parts.append("mimalloc 统计：" + txt[:max_chars])
    except Exception:
        pass
    return "；".join(parts)


def _dll_family(name: str) -> str:
    n = name.lower()
    if n.startswith(("torch", "c10", "cudnn", "cublas", "cufft", "curand", "cusparse", "cusolver", "nvrtc",
                     "nvjitlink", "cudart", "nvcuda", "nvfatbin", "libtorch", "libc10", "libcudnn", "libcublas")):
        return "torch/CUDA"
    if n.startswith(("qt6", "pyside6", "shiboken6", "libqt6")):
        return "Qt"
    if n.startswith(("opencv", "cv2", "libopencv")):
        return "OpenCV"
    if n.startswith(("python", "libpython")) or n.endswith(".pyd"):
        return "Python/扩展"
    if n.startswith(("ctranslate2", "libctranslate2", "onnxruntime", "libonnxruntime")):
        return "ctranslate2/onnx"
    if n.startswith(("mkl", "libiomp", "libopenblas", "openblas")):
        return "MKL/BLAS"
    return name


def mapped_memory_note(top_n: int = 12) -> str:
    """RSS 按「映射的文件」分：torch / CUDA 的 DLL、Qt、cv2、模型文件……各占多少，剩下的是匿名内存
    （Python 堆、解码帧、缓存）。这才是「主界面 3~5 GB 到底是什么」的答案。"""
    g = 1024.0 ** 3
    try:
        import psutil
        maps = psutil.Process().memory_maps(grouped=True)
    except Exception as e:
        return _mapped_memory_fallback(g, repr(e))
    rows = []
    anon = 0.0
    total = 0.0
    for m in maps:
        rss = float(getattr(m, "rss", 0) or 0)
        total += rss
        path = (getattr(m, "path", "") or "").strip()
        low = path.lower()
        if not path or low in ("[anon]", "[heap]", "[stack]") or low.startswith("["):
            anon += rss
            continue
        rows.append((rss, os.path.basename(path)))
    rows.sort(reverse=True)
    # 同一家族合并：torch 的 DLL / CUDA 的 DLL / Qt / cv2 / python
    fam = {}
    for rss, name in rows:
        n = name.lower()
        if n.endswith((".pt", ".pth", ".bin", ".safetensors", ".onnx", ".gguf")):
            key = "模型文件"
        elif n.endswith((".ttf", ".otf", ".ttc")):
            key = "字体文件"
        else:
            key = _dll_family(name)
        fam[key] = fam.get(key, 0.0) + rss
    top = sorted(fam.items(), key=lambda kv: -kv[1])[:top_n]
    parts = ["%s %.2f GB" % (k, v / g) for k, v in top if v >= 32 << 20]
    return "RSS 构成（共 %.2f GB）：匿名内存（堆 / 帧 / 缓存）%.2f GB；文件映射：%s" % (
        total / g, anon / g, "、".join(parts) if parts else "无 ≥32 MB 的")


def font_coverage_notes(tasks) -> list:
    """Warn when subtitle text needs a bundled fallback font."""
    notes = []
    try:
        from cutora.subtitle.font_coverage import coverage_note_for_text
    except Exception:
        return notes
    seen = set()
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        style = task.get("style") if isinstance(task.get("style"), dict) else {}
        family = style.get("font_family") or "Arimo"
        texts = []
        for seg in task.get("subtitles") or task.get("segments") or []:
            if isinstance(seg, dict):
                texts.append(str(seg.get("text") or ""))
        blob = " ".join(t for t in texts if t)
        if not blob:
            continue
        note = coverage_note_for_text(family, blob)
        if note and note not in seen:
            seen.add(note)
            notes.append(note)
    return notes


def plan_parallel_export(job_count, requested_workers, want_gpu, cpu_count=None,
                         avail_ram=None, canvas_wh=None, mem_per_worker=None):
    """导出前体检：返回一份「这台机器现在扛得住多少」的方案。

    返回 dict：
        workers      实际建议的并行进程数
        gpu_workers  其中允许使用 NVENC 的进程数（0 表示全部走 CPU）
        enc_threads  每个子进程给 FFmpeg 的 -threads 上限
        notes        [str]，写进日志 / 提示框给用户看的解释
    """
    notes = []
    try:
        cpu = int(cpu_count or os.cpu_count() or 8)
    except Exception:
        cpu = 8

    workers = max(1, min(int(requested_workers or 1), max(1, int(job_count or 1))))

    # ── 内存 ────────────────────────────────────────────────────────────
    total_ram, sampled_avail = system_memory_gb()
    if avail_ram is None:
        avail_ram = sampled_avail
    else:
        avail_ram = float(avail_ram)
    per = float(mem_per_worker) if mem_per_worker else mem_per_worker_gb(canvas_wh)
    inflight_per = float(COMPOSE_INFLIGHT_PER_WORKER_GB)
    # Q180：同画布的实测峰值 RSS 本来就含在途帧（合成队列 / 取好的源帧）—— 再加一遍是重复计算
    measured_exact = False
    if not mem_per_worker:
        try:
            _w, _h = (canvas_wh or (1080, 1920))[:2]
            measured_exact = bool(measured_rss_for_canvas(int(_w) * int(_h))[2])
        except Exception:
            measured_exact = False
    if avail_ram > 0:
        budget = max(0.0, avail_ram - MEM_HEADROOM_GB)
        cost_per = per if measured_exact else per + inflight_per + float(REPLAY_INFLIGHT_PER_WORKER_GB)
        mem_cap = int(budget // cost_per)
        if mem_cap < 1:
            mem_cap = 1
        if mem_cap < workers:
            if measured_exact:
                notes.append(
                    f"可用内存仅 {avail_ram:.1f} GB，并行进程数由 {workers} 降到 {mem_cap}"
                    f"（每进程按同尺寸实测峰值 ×1.25 ≈ {per:.1f} GB，已含在途缓冲，"
                    f"另留 {MEM_HEADROOM_GB} GB 给系统）")
            else:
                notes.append(
                    f"可用内存仅 {avail_ram:.1f} GB，并行进程数由 {workers} 降到 {mem_cap}"
                    f"（每进程约需 {per:.1f} GB + 在途缓冲 {inflight_per:.2f} GB，"
                    f"另留 {MEM_HEADROOM_GB} GB 给系统）")
            workers = mem_cap

    # ── CPU ────────────────────────────────────────────────────────────
    # 关键：给主界面留出至少 2 个逻辑核。主进程抢不到 CPU 时，
    # Qt 主线程处理不了 WM_PAINT，Windows 就会把窗口换成白板 ghost。
    cpu_cap = max(1, (cpu - 2) // 2)
    if cpu_cap < workers:
        notes.append(f"为主界面保留 CPU 余量，并行进程数由 {workers} 降到 {cpu_cap}")
        workers = cpu_cap

    # 每个子进程能给 FFmpeg 用几个线程。原来写死 -threads 0（= 用满所有核），
    # N 个子进程各开满 N 份线程池，24 核机器上会出现上百个编码线程互相抢核，
    # 主界面直接被饿死。这里按进程数均分。
    # 每个 worker 除了 FFmpeg 还有一个 Python 渲染线程要占核，所以先把
    # workers 个渲染线程和 2 个给主界面的核扣掉，剩下的才分给 FFmpeg。
    enc_threads = max(2, min(8, (cpu - 2 - workers) // max(1, workers)))

    # ── 显存 / NVENC ───────────────────────────────────────────────────
    gpu_workers = 0
    if want_gpu:
        vram = gpu_memory_gb()
        if vram is None:
            # 读不到显存信息（nvidia-smi 不在 PATH 等）。这时不去改变用户的
            # 选择——保持 GPU 可用，只把并发 NVENC 会话数压到安全值。
            gpu_workers = min(workers, nvenc_max_sessions())
            notes.append(
                f"读取不到显存信息，保守起见只允许 {gpu_workers} 个进程使用 GPU 编码")
        else:
            total_v, used_v, free_v = vram
            budget_v = max(0.0, free_v - VRAM_HEADROOM_GB)
            vram_cap = int(budget_v // VRAM_PER_NVENC_GB)
            nv_cap = nvenc_max_sessions()
            gpu_workers = max(0, min(workers, nv_cap, vram_cap))
            if gpu_workers == 0:
                notes.append(
                    f"显存空闲仅 {free_v:.1f} GB（共 {total_v:.1f} GB），"
                    f"本次导出全部改用 CPU 编码，以免显存耗尽导致桌面白屏")
            elif gpu_workers < workers:
                why = (f"NVENC 并发上限（{nvenc_sessions_source()}）" if nv_cap <= vram_cap
                       else f"显存空闲 {free_v:.1f} GB")
                notes.append(
                    f"{why}，只允许 {gpu_workers} 个进程使用 NVENC，"
                    f"其余 {workers - gpu_workers} 个用 CPU 编码")

    tier = "低" if workers <= 1 else ("高" if workers >= 5 else "中")
    n_workers = max(1, workers)
    notes.insert(0, (
        f"本机档位：{tier} —— 可用内存 {avail_ram:.1f} GB（留 {MEM_HEADROOM_GB:.0f} GB 给系统），"
        f"每个渲染进程按 {per:.1f} GB 算，另加在途缓冲 {inflight_per:.2f} GB × {n_workers}，"
        f"CPU {cpu} 核 → {n_workers} 个进程"))

    return {
        "workers": n_workers,
        "gpu_workers": int(gpu_workers),
        "enc_threads": int(enc_threads),
        "notes": notes,
        "ram_total": round(total_ram, 1),
        "ram_avail": round(avail_ram, 1),
        "cpu": cpu,
        "mem_per_worker": round(per, 2),
        "compose_inflight_gb": round(inflight_per * n_workers, 3),
        "compose_inflight_per_worker_gb": round(inflight_per, 3),
    }

# ── 降级标记：父进程写，子进程在每个视频开始前读 ──────────────────────────

def degrade_path(tmpdir):
    return os.path.join(tmpdir, DEGRADE_NAME)

def write_degrade(tmpdir, reason, level="gpu_off", inflight=0):
    """Ask workers to degrade. Does not claim in-flight encodes already stopped."""
    try:
        p = degrade_path(tmpdir)
        if os.path.exists(p):
            return False
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"level": level, "reason": reason, "at": time.time(),
                       "inflight": int(inflight or 0)}, f,
                      ensure_ascii=False)
        n = int(inflight or 0)
        if n > 0:
            _log("已请求降级（%s）：%s；%d 个视频正在编码中，本次不中途换编码器，"
                 "收尾后该片改 CPU 重做" % (level, reason, n))
        else:
            _log("已请求降级（%s）：%s" % (level, reason))
        return True
    except Exception:
        return False

def read_degrade(tmpdir):
    """子进程侧：返回降级信息 dict 或 None。这个调用必须极廉价。"""
    try:
        p = degrade_path(tmpdir)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"level": "gpu_off", "reason": "unknown"}

class ResourceWatchdog(threading.Thread):
    """后台采样内存 / 显存，压力超线时发降级指令。

    刻意做成普通 threading.Thread 而不是 QThread，也完全不碰任何 Qt 对象：
    主线程卡死（甚至白板）的时候，这个线程照样在跑，照样能救场。
    """

    def __init__(self, tmpdir, gpu_in_use, interval=3.0, on_degrade=None):
        super().__init__(daemon=True, name="CUTORAResourceWatchdog")
        self.tmpdir = tmpdir
        self.gpu_in_use = bool(gpu_in_use)
        self.interval = float(interval)
        self.on_degrade = on_degrade
        self._stop = threading.Event()
        self.last_sample = {}

    def stop(self):
        self._stop.set()

    def run(self):
        low_ram_hits = 0
        low_vram_hits = 0
        while not self._stop.wait(self.interval):
            try:
                _, avail = system_memory_gb()
                vram = gpu_memory_gb() if self.gpu_in_use else None
                self.last_sample = {
                    "ram_avail": round(avail, 2),
                    "vram_free": round(vram[2], 2) if vram else None,
                    "at": time.time(),
                }

                # 预防：2.0 GB 就开始请求降级并暂停派发；1.0 GB 未开始的改串行。
                # 不再等 1.5 GB 连续两次（那时已经 0.15 GB，是事后报警）。
                if 0 < avail < PAUSE_RAM_GB:
                    low_ram_hits += 1
                else:
                    low_ram_hits = 0

                if vram and vram[2] < 0.8:
                    low_vram_hits += 1
                else:
                    low_vram_hits = 0

                reason = None
                level = "gpu_off"
                if 0 < avail < SERIAL_RAM_GB:
                    reason = f"可用内存仅剩 {avail:.2f} GB，未开始的任务改串行"
                    level = "serial_rest"
                elif low_vram_hits >= 1:
                    reason = f"显存仅剩 {vram[2]:.2f} GB，已请求关掉后续 NVENC"
                elif low_ram_hits >= 1:
                    reason = f"可用内存仅剩 {avail:.2f} GB，已请求降级并暂停派发新 worker"
                    level = "pause_spawn"

                if reason and write_degrade(self.tmpdir, reason, level=level):
                    if callable(self.on_degrade):
                        try:
                            self.on_degrade(reason)
                        except Exception:
                            pass
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════════════
#  三、心跳文件 + 状态文件
# ═══════════════════════════════════════════════════════════════════════════

def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

class HeartbeatWriter:
    """把导出状态同时写到两个地方：

      · out_dirs/导出进行中.lock —— 给人看的。资源管理器里就能看到还在导。
      · state.json               —— 给保活窗口看的。

    所有写入都由后台线程调用，不占用 Qt 主线程。
    """

    def __init__(self, state_path, out_dirs, total, workers, gpu_workers, tmpdir):
        self.state_path = state_path
        self.out_dirs = [d for d in dict.fromkeys(out_dirs or []) if d]
        self.total = int(total or 0)
        self.workers = int(workers or 0)
        self.gpu_workers = int(gpu_workers or 0)
        self.tmpdir = tmpdir
        self.started_at = time.time()
        self.pids = []
        self._last_lock_write = 0.0

    # ── lock 文件 ────────────────────────────────────────────────────────
    def _lock_files(self):
        return [os.path.join(d, LOCK_NAME) for d in self.out_dirs]

    def _write_lock(self, done, latest, degraded):
        body = (
            "片语 Studio 正在导出，请不要关闭软件、不要重启电脑。\n"
            "─────────────────────────────────────────────\n"
            f"进度        ：{done} / {self.total}\n"
            f"最近完成    ：{latest or '（尚未产出）'}\n"
            f"开始时间    ：{time.strftime('%H:%M:%S', time.localtime(self.started_at))}\n"
            f"更新时间    ：{time.strftime('%H:%M:%S')}\n"
            f"渲染进程    ：{self.workers} 个（其中 {self.gpu_workers} 个使用 GPU 编码）\n"
            f"主程序 PID  ：{os.getpid()}\n"
            f"子进程 PID  ：{', '.join(str(p) for p in self.pids) or '—'}\n"
            + (f"降级提示    ：{degraded}\n" if degraded else "")
            + "─────────────────────────────────────────────\n"
            "如果软件主界面变成白板或提示「未响应」：那是 Windows 在主进程\n"
            "忙不过来时给窗口套的一层假图，导出并没有停止。只要本文件的\n"
            "「更新时间」还在往前走、或本目录里还在产出新的 _sub.mp4，\n"
            "就请继续等待，不要强制关闭，也不要重启电脑。\n"
            "全部导出完成后，本文件会自动变成「导出完成.txt」。\n"
        )
        for p in self._lock_files():
            try:
                _atomic_write(p, body)
            except Exception:
                pass

    # ── 对外接口 ────────────────────────────────────────────────────────
    def update(self, done, latest=None, status="running", degraded=None, extra=None):
        payload = {
            "status": status,
            "done": int(done),
            "total": self.total,
            "latest": latest or "",
            "workers": self.workers,
            "gpu_workers": self.gpu_workers,
            "tmpdir": self.tmpdir,
            "out_dirs": self.out_dirs,
            "pids": list(self.pids),
            "child_pids": list(self.pids),
            "parent_pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "degraded": degraded or "",
        }
        if extra:
            payload.update(extra)
        try:
            _atomic_write(self.state_path, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

        # lock 文件写得慢一点就够了（3 秒），减少磁盘打扰
        now = time.time()
        if status != "running" or (now - self._last_lock_write) > 3.0:
            self._last_lock_write = now
            self._write_lock(done, latest, degraded)

    def finish(self, ok_count, failed_count, latest=None, degraded=None):
        self.update(ok_count, latest=latest, status="finished", degraded=degraded,
                    extra={"failed": int(failed_count)})
        elapsed = time.time() - self.started_at
        body = (
            "片语 Studio 批量导出已全部结束。\n"
            "─────────────────────────────────────────────\n"
            f"成功        ：{ok_count} 个\n"
            f"失败/重试   ：{failed_count} 个\n"
            f"开始时间    ：{time.strftime('%H:%M:%S', time.localtime(self.started_at))}\n"
            f"结束时间    ：{time.strftime('%H:%M:%S')}\n"
            f"总耗时      ：{int(elapsed // 60)} 分 {int(elapsed % 60)} 秒\n"
        )
        for d in self.out_dirs:
            try:
                _atomic_write(os.path.join(d, DONE_NAME), body)
            except Exception:
                pass
            try:
                lp = os.path.join(d, LOCK_NAME)
                if os.path.exists(lp):
                    os.remove(lp)
            except Exception:
                pass

    def cleanup_locks(self):
        for p in self._lock_files():
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════════════════════
#  四、保活进度窗（独立进程）
# ═══════════════════════════════════════════════════════════════════════════

def launch_guard_window(state_path, python_exe=None):
    """拉起独立的保活进度窗进程。返回 Popen 或 None。

    刻意用 ABOVE_NORMAL 优先级：这个窗口小得可以忽略不计，但它必须在整台
    机器被渲染进程压满的时候依然能画得出来 —— 它是用户唯一的信息来源。
    """
    try:
        exe = python_exe or sys.executable
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_guard.py")
        if not os.path.exists(script):
            _log("找不到 export_guard.py，跳过保活窗口")
            return None
        flags = 0
        if IS_WINDOWS:
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0))
        env = dict(os.environ)
        from cutora.core.flags import clear_render_child; clear_render_child(env)
        proc = _no_window_popen(
            [exe, "-u", script, "--watch", state_path],
            cwd=os.path.dirname(script),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _log(f"保活进度窗已启动 (pid={proc.pid})")
        return proc
    except Exception as e:
        _log(f"保活进度窗启动失败（不影响导出）：{e}")
        return None

def _fmt_hms(sec):
    try:
        sec = int(max(0, sec))
    except Exception:
        return "—"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def guard_window_class():
    """返回保活窗口类。做成工厂函数是为了把 PySide6 的 import 延迟到真正要用时，
    这样 export_guard 在任何环境下 import 都不会失败。"""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QProgressBar,
        QPushButton,
        QMessageBox,
    )

    class GuardWindow(QWidget):
        def __init__(self, state_path):
            super().__init__()
            self.state_path = state_path
            self.state = {}
            self.finished_shown = False
            self.last_fs_done = 0
            self.last_fs_change = time.time()

            self.setWindowTitle("片语 Studio · 导出中")
            self.setObjectName("GuardWindow")
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.resize(420, 210)
            self.setMinimumSize(380, 190)
            self.setMaximumSize(560, 280)
            self._stop_requested = False
            try:
                from PySide6.QtGui import QIcon
                _qss, ico = _guard_asset_paths()
                if os.path.isfile(ico):
                    self.setWindowIcon(QIcon(ico))
            except Exception:
                pass

            lay = QVBoxLayout(self)
            lay.setContentsMargins(14, 12, 14, 10)
            lay.setSpacing(6)

            self.lbl_title = QLabel("导出中")
            self.lbl_title.setObjectName("title")
            lay.addWidget(self.lbl_title)

            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            self.bar.setTextVisible(False)
            lay.addWidget(self.bar)

            self.lbl_count = QLabel("已完成 0 / 0")
            lay.addWidget(self.lbl_count)

            self.lbl_latest = QLabel("")
            self.lbl_latest.setObjectName("sub")
            lay.addWidget(self.lbl_latest)

            self.lbl_time = QLabel("已用 00:00 · 剩余 —")
            self.lbl_time.setObjectName("sub")
            lay.addWidget(self.lbl_time)

            hint_row = QHBoxLayout()
            hint_row.setSpacing(4)
            self.lbl_hint = QLabel("主界面暂时无响应属正常，请勿强关")
            self.lbl_hint.setObjectName("hint")
            _full_hint = (
                "主界面若变成白板或显示「未响应」，属于 Windows 的正常表现，"
                "导出并没有停止。请以本窗口为准，不要强关软件、不要重启电脑。"
            )
            self.lbl_hint.setToolTip(_full_hint)
            info = QLabel("ⓘ")
            info.setObjectName("hint")
            info.setToolTip(_full_hint)
            hint_row.addWidget(self.lbl_hint)
            hint_row.addWidget(info)
            hint_row.addStretch(1)
            lay.addLayout(hint_row)

            row = QHBoxLayout()
            self.btn_open = QPushButton("打开输出文件夹")
            self.btn_open.clicked.connect(self._open_out)
            row.addWidget(self.btn_open)
            row.addStretch(1)
            self.btn_stop = QPushButton("停止导出")
            self.btn_stop.setObjectName("btnStopExport")
            self.btn_stop.setToolTip(
                "主界面暂时无响应属正常，请勿强关 ⓘ")
            self.btn_stop.clicked.connect(self._request_stop)
            row.addWidget(self.btn_stop)
            lay.addLayout(row)

            self.timer = QTimer(self)
            self.timer.setInterval(800)
            self.timer.timeout.connect(self._tick)
            self.timer.start()
            self._tick()

        # ── 数据来源 1：父进程写的 state.json ──────────────────────────
        def _read_state(self):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.state = d
            except Exception:
                pass

        # ── 数据来源 2：磁盘实况（父进程卡死也照样准） ─────────────────
        def _scan_disk(self):
            """直接数输出目录里的成品，以及子进程写的 w*.progress。

            这是整个方案的关键：它完全绕开父进程。主界面白板、甚至主进程
            被用户强杀，只要还有渲染子进程在出片，这里的数字就会继续走。
            """
            done = 0
            latest = ""
            latest_t = 0.0
            # 只数「本次导出开始之后才写出来的」成品。
            # 否则重复导出同一批时，目录里上一轮的 80 个文件会让进度
            # 一上来就显示 80/80。
            since = float(self.state.get("started_at") or 0) - 30.0
            for d in self.state.get("out_dirs", []) or []:
                try:
                    for name in os.listdir(d):
                        if not name.lower().endswith(".mp4"):
                            continue
                        p = os.path.join(d, name)
                        try:
                            mt = os.path.getmtime(p)
                        except Exception:
                            continue
                        if mt < since:
                            continue
                        done += 1
                        if mt > latest_t:
                            latest_t, latest = mt, name
                except Exception:
                    pass

            partial = 0.0
            tmpdir = self.state.get("tmpdir")
            if tmpdir and os.path.isdir(tmpdir):
                try:
                    for name in os.listdir(tmpdir):
                        if not name.endswith(".progress"):
                            continue
                        try:
                            d0 = read_json_allow_replace(os.path.join(tmpdir, name))
                            partial += float(d0.get("cur", 0)) / 100.0
                        except Exception:
                            pass
                except Exception:
                    pass
            return done, latest, latest_t, partial

        def _children_alive(self):
            pids = self.state.get("pids") or []
            return sum(1 for p in pids if pid_alive(p))

        def _tick(self):
            self._read_state()
            if guard_should_close(self.state):
                self.timer.stop()
                self.close()
                return
            total = int(self.state.get("total") or 0)
            fs_done, latest, latest_t, partial = self._scan_disk()
            rep_done = int(self.state.get("done") or 0)
            done = max(rep_done, fs_done)

            if fs_done != self.last_fs_done:
                self.last_fs_done = fs_done
                self.last_fs_change = time.time()

            alive = self._children_alive()
            parent_ok = pid_alive(self.state.get("parent_pid", 0))
            stale = time.time() - float(self.state.get("updated_at") or 0)
            status = self.state.get("status", "running")

            # Q180：进度条 / 剩余时间按各进程的实际进度（含正在画的那条）算；「已完成」只数写完的文件
            try:
                prog = float(self.state.get("progress") or 0.0)
            except (TypeError, ValueError):
                prog = 0.0
            prog = max(prog, done + partial)
            pct = int(min(100.0, prog / total * 100.0)) if total else 0
            self.bar.setValue(pct)
            current = self.state.get("current_name") or latest or ""
            running_n = max(0, min(total - done, int(self.state.get("workers") or 0)))
            if prog > done + 0.01 and total > done and running_n:
                self.lbl_count.setText(f"已完成 {done} / {total}（另有 {running_n} 个正在导出）")
            else:
                self.lbl_count.setText(f"已完成 {done} / {total}")
            self.lbl_latest.setText(str(current))

            elapsed = time.time() - float(self.state.get("started_at") or time.time())
            if prog > 0.05 and total > prog:
                eta = elapsed / prog * (total - prog)
                self.lbl_time.setText(
                    f"已用 {_fmt_hms(elapsed)} · 剩余 {_fmt_hms(eta)}")
            else:
                self.lbl_time.setText(f"已用 {_fmt_hms(elapsed)} · 剩余 —")

            degraded = self.state.get("degraded")
            # 主界面心跳：主窗口有一个 1 秒的轻量定时器只做「记一个时间戳」，
            # 它停多久没走，就说明 Qt 主线程被卡了多久。
            ui_tick = float(self.state.get("ui_tick") or 0)
            ui_lag = (time.time() - ui_tick) if ui_tick else -1

            # ── 状态判定 ───────────────────────────────────────────────
            if status == "finished" or (total and done >= total and alive == 0):
                if not self.finished_shown:
                    self.finished_shown = True
                    self._notify_done()
                self.lbl_title.setText("✅ 导出全部完成")
                self.bar.setValue(100)
                self.lbl_hint.setText(
                    f"共 {done} 个视频已写入输出目录，可以安全关闭软件了。\n"
                    "（主界面如果还是白板，直接关掉重开即可，成品不会丢。）")
                if not getattr(self, "_done_style_applied", False):
                    self._done_style_applied = True
                    self.setProperty("done", "true")
                    sty = self.style()
                    if sty is not None:
                        sty.unpolish(self)
                        sty.polish(self)
                self.timer.setInterval(3000)
            elif alive == 0 and not parent_ok:
                self.lbl_title.setText("⚠ 渲染进程已全部退出")
                self.lbl_hint.setText(
                    f"已产出 {done} / {total}。主程序和渲染进程都不在了，\n"
                    "剩余部分需要重新打开软件后继续导出。")
            elif alive > 0 and (stale > 12 or ui_lag > 8 or not parent_ok):
                self.lbl_title.setText("正在导出（主界面已无响应，属正常）")
                lag_txt = f"主界面已卡住约 {int(ui_lag)} 秒。" if ui_lag > 8 else ""
                self.lbl_hint.setText(
                    lag_txt + "后台渲染进程仍在正常出片 —— 上面的数字就是实时的\n"
                    "真实进度。请耐心等待，千万不要强关软件、更不要重启电脑。")
            elif time.time() - self.last_fs_change > 300 and alive > 0:
                self.lbl_title.setText("正在导出（当前这个视频较长）")
                self.lbl_hint.setText(
                    f"已有 5 分钟没有新成品产出，但 {alive} 个渲染进程仍在运行，\n"
                    "通常是当前视频时长较长。若超过 20 分钟没有变化再考虑中止。")
            else:
                self.lbl_title.setText("正在导出…")
                if degraded:
                    self.lbl_hint.setText(f"⚠ {degraded}\n导出会继续，只是速度略慢，成品质量不受影响。")

        def _notify_done(self):
            try:
                if IS_WINDOWS:
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
            try:
                self.showNormal()
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

        def _open_out(self):
            dirs = self.state.get("out_dirs") or []
            if not dirs:
                return
            try:
                if IS_WINDOWS:
                    os.startfile(dirs[0])
                else:
                    _no_window_popen(["xdg-open", dirs[0]])
            except Exception:
                pass

        def _request_stop(self):
            if self._stop_requested:
                return
            box = QMessageBox(self)
            box.setWindowTitle("停止导出")
            box.setText("已完成的文件保留，正在写的这个会作废")
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
            self._apply_stop()

        def _apply_stop(self):
            self._stop_requested = True
            write_stop_flag(self.state_path)
            self._read_state()
            pids = self.state.get("child_pids") or self.state.get("pids") or []
            terminate_pids(pids)
            self.btn_stop.setEnabled(False)
            self.btn_stop.setText("已请求停止…")

    return GuardWindow

def _run_guard_window(state_path):
    """保活窗口主体。只 import PySide6 + 标准库，秒开、极低负担。"""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QStyleFactory
    GuardWindow = guard_window_class()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    try:
        app.setStyle(QStyleFactory.create("Fusion"))
    except Exception:
        pass
    qss_path, ico_path = _guard_asset_paths()
    extra = (
        "QPushButton#btnStopExport {"
        " color: #B4544A; border: 1px solid #B4544A;"
        " background-color: #262019; }"
        "QPushButton#btnStopExport:hover { background-color: #332A22; }"
        "QPushButton#btnStopExport:disabled {"
        " color: #7A6F63; border: 1px solid #3A302A; }"
    )
    try:
        with open(qss_path, "r", encoding="utf-8") as fh:
            app.setStyleSheet(fh.read() + extra)
    except Exception:
        app.setStyleSheet(extra)
    if os.path.isfile(ico_path):
        try:
            app.setWindowIcon(QIcon(ico_path))
        except Exception:
            pass
    w = GuardWindow(state_path)
    w.show()
    # 放到屏幕右下角，尽量不挡住主界面
    try:
        scr = app.primaryScreen().availableGeometry()
        w.move(scr.right() - w.width() - 30, scr.bottom() - w.height() - 60)
    except Exception:
        pass
    return app.exec()

# ═══════════════════════════════════════════════════════════════════════════
#  脚本入口
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        if i + 1 >= len(sys.argv):
            print("用法: python export_guard.py --watch <state.json>")
            sys.exit(2)
        sys.exit(_run_guard_window(sys.argv[i + 1]))
    if "--check" in sys.argv:
        # 体检自测：python export_guard.py --check
        ram = system_memory_gb()
        vram = gpu_memory_gb()
        print(f"内存: 总 {ram[0]:.1f} GB / 可用 {ram[1]:.1f} GB")
        print(f"显存: {vram if vram else '未检测到 NVIDIA 显卡'}")
        print(f"CPU : {os.cpu_count()} 逻辑核")
        print("方案:", json.dumps(
            plan_parallel_export(80, 6, True), ensure_ascii=False, indent=2))
        sys.exit(0)
    print(__doc__)
