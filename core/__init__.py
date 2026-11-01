"""片语工作室 (D:\\0_LTX) 跨模块共享核心底座 (ltx_core)。

统一提供：
1. 全局配置加载与离线环境强制注入 (HF_HUB_OFFLINE=1, IMAGEIO_FFMPEG_EXE)，同时兼容 Windows 与 macOS 及 Python 3.10 / 3.11 / 3.12
2. 三级硬件自适应网关 (HardwareGuard: 32GB+CPU 保障、16GB NVIDIA GPU 与 Apple Silicon Metal 统一内存识别)
3. 单卡显存/统一内存互斥仲裁器 (VRAMBroker: 重型模型运行前自动卸载 Ollama/特效缓存与释放 MPS 缓存)
4. 系统凭据保险箱 (SecretsVault: Windows CredMan / macOS Keychain 零明文 API Key)
5. 一表到底同步器与骨架守恒分词 (TableSync: 表格工具 ⇄ Orante ⇄ CUTORA 智能拼接)
6. 统一媒体体检签名 (MediaInspect: MP4 Box + 响度 + 首帧 aHash/梯度校验一次性签发)
7. 跨窗口实时信号总线与共享数据库 (SpineDB & EventBus)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

__version__ = "3.0.0"

LTX_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = LTX_ROOT / "config" / "studio.toml"
MODELS_REGISTRY_PATH = LTX_ROOT / "config" / "models_registry.json"
BIN_DIR = LTX_ROOT / "runtime" / "bin"
MODELS_DIR = LTX_ROOT / "runtime" / "models"
DATA_DIR = LTX_ROOT / "data"


def resolve_runtime_bin(name: str) -> Path:
    """跨平台解析二进制工具路径 (ffmpeg / ffprobe / rclone)，兼容 Windows (.exe) 与 macOS/POSIX。"""
    stem = name[:-4] if name.lower().endswith(".exe") else name
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend([
            BIN_DIR / f"{stem}.exe",
            LTX_ROOT / "runtime" / "ffmpeg" / f"{stem}.exe",
            LTX_ROOT / "modules" / "table_tool" / "app" / f"{stem}.exe",
        ])
    else:
        candidates.extend([
            BIN_DIR / stem,
            LTX_ROOT / "runtime" / "ffmpeg" / stem,
            LTX_ROOT / "modules" / "table_tool" / "app" / stem,
            Path("/opt/homebrew/bin") / stem,
            Path("/usr/local/bin") / stem,
        ])
    for c in candidates:
        if c.is_file():
            return c
    which = shutil.which(stem)
    if which:
        return Path(which)
    return BIN_DIR / (f"{stem}.exe" if os.name == "nt" else stem)


def resolve_python_exe(gui: bool = False, root: Path | None = None) -> str:
    """跨平台解析工作室虚拟环境或便携式 Python 可执行文件路径。"""
    base = root or LTX_ROOT
    if os.name == "nt":
        exe_name = "pythonw.exe" if gui else "python.exe"
        for cand in (
            base / "runtime" / "venv" / "Scripts" / exe_name,
            base / "runtime" / "venv" / "Scripts" / "python.exe",
            base / "runtime" / "python" / exe_name,
            base / "runtime" / "python" / "python.exe",
        ):
            if cand.is_file():
                return str(cand)
    else:
        for cand in (
            base / "runtime" / "venv" / "bin" / "python3",
            base / "runtime" / "venv" / "bin" / "python",
            base / "runtime" / "python" / "bin" / "python3",
            base / "runtime" / "python" / "bin" / "python",
        ):
            if cand.is_file():
                return str(cand)
    return sys.executable


def enforce_offline_and_runtime_env() -> None:
    """在任何子模块导入前强制注入离线与统一二进制路径环境变量。"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"

    ffmpeg_exe = resolve_runtime_bin("ffmpeg")
    if ffmpeg_exe.is_file():
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg_exe)
        os.environ["FFMPEG_BINARY"] = str(ffmpeg_exe)

    extra_dirs = [str(BIN_DIR), str(LTX_ROOT / "runtime" / "ffmpeg")]
    if sys.platform == "darwin":
        for hb in ("/opt/homebrew/bin", "/usr/local/bin"):
            if os.path.isdir(hb):
                extra_dirs.append(hb)
    cur_path = os.environ.get("PATH", "")
    cur_lower = cur_path.lower()
    prepend = [d for d in extra_dirs if d.lower() not in cur_lower]
    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + ([cur_path] if cur_path else []))


enforce_offline_and_runtime_env()


def _parse_simple_toml(text: str) -> dict[str, Any]:
    """零依赖 TOML 解析器（兼容 Python 3.10 与 3.11+）。"""
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ImportError:
        pass
    out: dict[str, Any] = {}
    cur_sec: dict[str, Any] = out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec_name = line[1:-1].strip()
            cur_sec = out.setdefault(sec_name, {})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.split("#", 1)[0].strip()
            if v.startswith('"') and v.endswith('"'):
                cur_sec[k] = bytes(v[1:-1], "utf-8").decode("unicode_escape")
            elif v.lower() in ("true", "false"):
                cur_sec[k] = v.lower() == "true"
            else:
                try:
                    cur_sec[k] = float(v) if "." in v else int(v)
                except ValueError:
                    cur_sec[k] = v
    return out


def load_studio_config() -> dict[str, Any]:
    """读取 config/studio.toml 并将相对路径自动解析为当前解压目录绝对路径。"""
    if not CONFIG_PATH.is_file():
        return {}
    cfg = _parse_simple_toml(CONFIG_PATH.read_text(encoding="utf-8"))
    suite_sec = cfg.get("suite")
    if isinstance(suite_sec, dict):
        rd = str(suite_sec.get("root_dir") or ".").strip()
        if not os.path.isabs(rd) or not Path(rd).exists():
            suite_sec["root_dir"] = str(LTX_ROOT)
    paths_sec = cfg.get("paths")
    if isinstance(paths_sec, dict):
        for k, v in list(paths_sec.items()):
            if isinstance(v, str) and v.strip():
                p = Path(v)
                if not p.is_absolute():
                    paths_sec[k] = str((LTX_ROOT / p).resolve())
                elif not p.exists():
                    # 自动重定位旧盘符绝对路径到当前 LTX_ROOT
                    norm = v.replace("\\", "/")
                    for marker in ("runtime/", "data/", "config/", "modules/"):
                        if marker in norm:
                            rel_part = norm[norm.index(marker):]
                            paths_sec[k] = str((LTX_ROOT / rel_part).resolve())
                            break
    return cfg


def load_models_registry() -> dict[str, Any]:
    """读取 D:\\0_LTX\\config\\models_registry.json。"""
    if not MODELS_REGISTRY_PATH.is_file():
        return {"models": []}
    return json.loads(MODELS_REGISTRY_PATH.read_text(encoding="utf-8"))
