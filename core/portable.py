"""片语工作室 (D:\\0_LTX) 便携环境重定位器 (core/portable.py)。

每次双击启动时，自动把 `D:\\0_LTX\\runtime\\venv\\pyvenv.cfg` 中的 `home =`
重写为当前解压目录下的 `<root>\\runtime\\python`。
确保用户把压缩包发给任何没装 Python 的新电脑、解压到任意盘符（C:/D:/E:/桌面）后，
一键双击即可直接运行，零外部 Python 依赖。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import LTX_ROOT


def rewrite_pyvenv_cfg(root: Path | str | None = None) -> Path:
    """将 runtime/venv/pyvenv.cfg 与 runtime/comfyui/venv/pyvenv.cfg 的 home 指向当前目录下的 runtime/python。"""
    base = Path(root).resolve() if root else LTX_ROOT
    py_home = base / "runtime" / "python"
    if not (py_home / "python.exe").is_file():
        return py_home

    for rel_cfg in (
        Path("runtime") / "venv" / "pyvenv.cfg",
        Path("runtime") / "comfyui" / "venv" / "pyvenv.cfg",
    ):
        cfg_path = base / rel_cfg
        if not cfg_path.is_file():
            continue
        lines = cfg_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        out: list[str] = []
        found = False
        for line in lines:
            if line.strip().lower().startswith("home") and "=" in line:
                out.append(f"home = {py_home}")
                found = True
            else:
                out.append(line)
        if not found:
            out.insert(0, f"home = {py_home}")
        cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return py_home

