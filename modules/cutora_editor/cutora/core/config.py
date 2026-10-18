# SPDX-License-Identifier: GPL-3.0-or-later
import shutil
import os

from cutora.core.flags import is_render_child

# ========================================================
# 1. Device Auto Detection & Safe Guard
#    (Synced with Installer v4.0 Logic)
# ========================================================

# [v6.1] 纯渲染子进程不需要 torch / CUDA。
# 并行导出时会启动多个渲染子进程，如果每个都 import torch 并初始化 CUDA 上下文，
# 每个进程要白白吃掉 0.5~1GB 显存，6 个进程就能把 16GB 显卡挤爆。
# 渲染只依赖 PySide6 + OpenCV + FFmpeg，与 AI 部分完全无关。
_IS_RENDER_CHILD = is_render_child()

# 默认安全回退配置
DEVICE = "cpu"
COMPUTE_TYPE = "float32"

def detect_gpu_config(verbose=True):
    """检测当前显卡是否可用于 AI 推理。

    返回 (device, compute_type, reason)：
        device       —— "cuda" 或 "cpu"
        compute_type —— "float16" / "int8" / "float32"
        reason       —— 人类可读的判断依据，用于日志和 UI 提示

    [v6.1] 这是全项目**唯一**的 GPU 架构判断入口。
    此前 config.py 用 60~120、主程序 toggle_gpu 用 70~90，
    两套白名单互相矛盾，RTX 50 系 (sm_120) 会被 UI 开关误判为「不受支持」。
    现在两边都调用本函数，不可能再分歧。

    架构安全区间说明：
      - 60~69   Pascal (GTX 10 系)          允许，但强制 int8（Windows 下 FP16 极慢且易崩）
      - 70~89   Turing / Ampere / Ada       完美支持，float16
      - 90      Hopper                      支持，float16
      - 100~120 Blackwell (RTX 50 系)       支持，float16（int8 在 sm_120 上有已知对齐问题）
    """
    if _IS_RENDER_CHILD:
        return ("cpu", "float32", "渲染子进程，不加载 AI 模型")
    # 空字符串表示调用方把显卡藏起来。nvidia-smi 不看这个变量，仍会报出本机的卡。
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return ("cpu", "float32", "CUDA_VISIBLE_DEVICES 为空，不用显卡")

    # Q187：主界面不再为了这一步 import torch（那是 2～3 GB 内存）。torch 已经在内存里时照用它；
    # 否则问 nvidia-smi 要算力版本（compute_cap），判断规则完全一样。GPU 服务进程起来后会用
    # 真正的 torch 结果回填（note_torch_cuda），两边不一致时以 torch 为准。
    import sys as _sys
    if "torch" not in _sys.modules:
        import importlib.util as _ilu
        if _ilu.find_spec("torch") is None:
            return ("cpu", "float32", "PyTorch 不可用 (未安装)")
        if _TORCH_CUDA_PROBE is not None and _TORCH_CUDA_PROBE[0] is False:
            return ("cpu", "float32", _TORCH_CUDA_PROBE[1] or "GPU 服务进程报告 CUDA 不可用")
        cc = _nvidia_compute_cap()
        if cc is None:
            return ("cpu", "float32", "未检测到 NVIDIA 显卡驱动")
        major, minor = cc // 10, cc % 10
        return _classify_cc(major, minor)

    try:
        import torch
    except Exception as e:
        return ("cpu", "float32", f"PyTorch 不可用 ({e})")

    if not torch.cuda.is_available():
        return ("cpu", "float32", "未检测到 NVIDIA 显卡驱动")

    try:
        major, minor = torch.cuda.get_device_capability()
        return _classify_cc(major, minor)
    except Exception as e:
        return ("cpu", "float32", f"GPU 硬件检测失败 ({e})")


_TORCH_CUDA_PROBE = None          # (ok, reason) —— GPU 服务进程用 torch 真测过之后回填


def note_torch_cuda(ok: bool, reason: str = "") -> None:
    """GPU 服务进程（真 import 了 torch 的那个）报告 CUDA 可不可用；不可用就把全局设备改回 cpu。"""
    global _TORCH_CUDA_PROBE, DEVICE, COMPUTE_TYPE, _GPU_REASON
    _TORCH_CUDA_PROBE = (bool(ok), reason)
    if not ok and DEVICE != "cpu":
        DEVICE, COMPUTE_TYPE, _GPU_REASON = ("cpu", "float32", reason or "GPU 服务进程报告 CUDA 不可用")
        _get_logger("cutora.core.config").warning("[Config] torch 报告 CUDA 不可用 → device=cpu（%s）", reason)


_CC_CACHE = {"v": None, "done": False}


def _nvidia_compute_cap():
    """nvidia-smi 的 compute_cap（如 "12.0" → 120）。没有 N 卡 / 老驱动不支持这个字段 → None。"""
    if _CC_CACHE["done"]:
        return _CC_CACHE["v"]
    val = None
    try:
        import subprocess
        from cutora.core.proc import run as _run
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        r = _run(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
                 capture_output=True, text=True, timeout=4, creationflags=flags)
        if r.returncode == 0:
            txt = (r.stdout or "").strip().splitlines()[0].strip()
            major, minor = txt.split(".")[:2]
            val = int(major) * 10 + int(minor)
    except Exception:
        val = None
    _CC_CACHE["v"] = val
    _CC_CACHE["done"] = True
    return val


def _classify_cc(major, minor):
    try:
        cc = major * 10 + minor  # 例如: 6.1 -> 61, 8.6 -> 86, 12.0 -> 120

        if not (60 <= cc <= 120):
            return ("cpu", "float32",
                    f"GPU 架构 sm_{cc} 不在安全区间 (60-120)，已回退 CPU 以防崩溃")

        if major < 7:
            return ("cuda", "int8", f"Pascal 架构 (sm_{cc})，强制 int8 模式")
        elif major >= 10:
            return ("cuda", "float16", f"Blackwell 架构 (sm_{cc})，float16 模式")
        else:
            return ("cuda", "float16", f"高性能 GPU (sm_{cc})，float16 模式")

    except Exception as e:
        return ("cpu", "float32", f"GPU 硬件检测失败 ({e})")

DEVICE, COMPUTE_TYPE, _GPU_REASON = detect_gpu_config()
from cutora.core.log import get_logger as _get_logger
_get_logger("cutora.core.config").info(
    "[Config] %s → device=%s, compute_type=%s",
    _GPU_REASON, DEVICE, COMPUTE_TYPE,
)


def gpu_status_text() -> str:
    """Status-bar GPU line. Device / compute_type come only from detect_gpu_config."""
    if DEVICE != "cuda":
        return "CPU · %s" % COMPUTE_TYPE
    name = "CUDA"
    try:
        import sys as _sys
        if "torch" in _sys.modules:
            import torch
            name = torch.cuda.get_device_name(0)
        else:
            from cutora.core.runtime import _nvidia_gpu_name
            name = _nvidia_gpu_name() or name
        for prefix in ("NVIDIA GeForce ", "NVIDIA ", "GeForce "):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
    except Exception:
        pass
    return "GPU: %s · %s" % (name, COMPUTE_TYPE)

# ========================================================
# 2. Model & Language Settings
# ========================================================

MODEL_SIZE = "small"
# 默认对齐/识别语言。可在软件内「字幕 → 对齐/识别语言」菜单切换（含 auto）。
# en → WAV2VEC2_ASR_LARGE_LV60K_960H 大模型 (需先运行 4_Download_EN_Align_Model.bat)
# it → VOXPOPULI_ASR_BASE_10K_IT (本地已有)
# 默认保持 en，golden 规则 A：en/it 旧路径不变。
LANGUAGE = "en"

# ========================================================
# 3. External Tools (FFmpeg) Auto Detect
# ========================================================

from cutora.core.paths import (
    REPO_ROOT,
    RUNTIME_DIR,
    MODELS as _RUNTIME_MODELS_DIR,
)
_REPO_ROOT = REPO_ROOT
_RUNTIME_FFMPEG_DIR = os.path.join(RUNTIME_DIR, "ffmpeg")

def _first_existing(*candidates):
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

# 优先 runtime/ffmpeg，再查 PATH
local_ffmpeg = _first_existing(
    os.path.join(_RUNTIME_FFMPEG_DIR, "ffmpeg.exe"),
    os.path.join(_RUNTIME_FFMPEG_DIR, "ffmpeg"),
)
local_ffprobe = _first_existing(
    os.path.join(_RUNTIME_FFMPEG_DIR, "ffprobe.exe"),
    os.path.join(_RUNTIME_FFMPEG_DIR, "ffprobe"),
)

if local_ffmpeg:
    FFMPEG_PATH = local_ffmpeg
else:
    FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"


def ensure_ffmpeg_on_path() -> None:
    """把内置 runtime/ffmpeg 放到 PATH 最前面（幂等）。

    Q132：whisperx.load_audio() 直接 spawn 裸名「ffmpeg」，不读 FFMPEG_PATH。
    CUTORA.bat 会 set PATH，但 Q106 之后桌面图标走 CUTORA.vbs →
    ``pythonw -m cutora``，根本不经过 bat —— 机器上没装系统 ffmpeg 时，
    自动字幕就是「[WinError 2] 系统找不到指定的文件」。放在 config 导入时
    做，GUI / CLI / 渲染子进程 / 队列重启，任何入口都一样。
    """
    if not local_ffmpeg:
        return
    d = os.path.dirname(local_ffmpeg)
    cur = os.environ.get("PATH") or ""
    parts = [p for p in cur.split(os.pathsep) if p]
    if parts and os.path.normcase(os.path.normpath(parts[0])) == os.path.normcase(os.path.normpath(d)):
        return
    parts = [p for p in parts
             if os.path.normcase(os.path.normpath(p)) != os.path.normcase(os.path.normpath(d))]
    os.environ["PATH"] = os.pathsep.join([d] + parts)


ensure_ffmpeg_on_path()

if local_ffprobe:
    FFPROBE_PATH = local_ffprobe
else:
    FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"

MODELS_DIR = _RUNTIME_MODELS_DIR

# ========================================================
# 4. Global Preference Defaults
# ========================================================

DEFAULT_TIMELINE_TEMPLATE = "1080x1920"  # 竖屏；横屏请设为 "1920x1080"
DEFAULT_VIDEO_FIT_MODE = "fill"          # "fit"=适配黑边 | "fill"=裁切填满 | "stretch"=拉伸
