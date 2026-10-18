"""片语工作室 (D:\\0_LTX) 跨模块共享核心底座 (ltx_core)。

统一提供：
1. 全局配置加载与离线环境强制注入 (HF_HUB_OFFLINE=1, IMAGEIO_FFMPEG_EXE)，同时兼容 Python 3.10 / 3.11 / 3.12
2. 三级硬件自适应网关 (HardwareGuard: 32GB+CPU 保障与 16GB GPU 识别)
3. 单卡显存互斥仲裁器 (VRAMBroker: 重型模型运行前自动卸载 Ollama/语音/特效缓存)
4. Windows 凭据管理器密钥保险箱 (SecretsVault: 零明文 API Key)
5. 一表到底同步器与骨架守恒分词 (TableSync: 表格工具 ⇄ Orante ⇄ CUTORA 智能拼接)
6. 统一媒体体检签名 (MediaInspect: MP4 Box + 响度 + 首帧 aHash/梯度校验一次性签发)
7. 跨窗口实时信号总线与共享数据库 (SpineDB & EventBus)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__version__ = "3.0.0"

LTX_ROOT = Path(r"D:\0_LTX")
CONFIG_PATH = LTX_ROOT / "config" / "studio.toml"
MODELS_REGISTRY_PATH = LTX_ROOT / "config" / "models_registry.json"
BIN_DIR = LTX_ROOT / "runtime" / "bin"
MODELS_DIR = LTX_ROOT / "runtime" / "models"
DATA_DIR = LTX_ROOT / "data"


def enforce_offline_and_runtime_env() -> None:
    """在任何子模块导入前强制注入离线与统一二进制路径环境变量。"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "False"

    ffmpeg_exe = BIN_DIR / "ffmpeg.exe"
    if ffmpeg_exe.is_file():
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg_exe)
        os.environ["FFMPEG_BINARY"] = str(ffmpeg_exe)

    bin_str = str(BIN_DIR)
    cur_path = os.environ.get("PATH", "")
    if bin_str.lower() not in cur_path.lower():
        os.environ["PATH"] = f"{bin_str};{cur_path}"


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
    """读取 D:\\0_LTX\\config\\studio.toml。"""
    if not CONFIG_PATH.is_file():
        return {}
    return _parse_simple_toml(CONFIG_PATH.read_text(encoding="utf-8"))


def load_models_registry() -> dict[str, Any]:
    """读取 D:\\0_LTX\\config\\models_registry.json。"""
    if not MODELS_REGISTRY_PATH.is_file():
        return {"models": []}
    return json.loads(MODELS_REGISTRY_PATH.read_text(encoding="utf-8"))
