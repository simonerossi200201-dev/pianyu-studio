"""片语工作室便携环境重定位器 (core/portable.py)。

每次双击启动时，自动把 `<root>\\runtime\\venv\\pyvenv.cfg` 中的 `home =`
重写为当前解压目录下的 `<root>\\runtime\\python`，并同步确保自愈型便携启动器
(`runtime/venv/Scripts/python.exe` 与 `pythonw.exe`) 就位。
确保用户把压缩包发给任何没装 Python 的新电脑、解压到任意盘符（C:/D:/E:/桌面）后，
一键双击即可直接运行，零外部 Python 依赖。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import LTX_ROOT


def rewrite_pyvenv_cfg(root: Path | str | None = None) -> Path:
    """将 runtime/venv/pyvenv.cfg 与 runtime/comfyui/venv/pyvenv.cfg 的 home 指向当前目录下的便携 Python。"""
    base = Path(str(root).rstrip("\\/")).resolve() if root else LTX_ROOT
    py_home = base / "runtime" / "python"
    comfy_py_home = base / "runtime" / "comfyui" / "python312"

    targets = [
        (base / "runtime" / "venv" / "pyvenv.cfg", py_home),
        (
            base / "runtime" / "comfyui" / "venv" / "pyvenv.cfg",
            comfy_py_home if (comfy_py_home / "python.exe").is_file() else py_home,
        ),
    ]

    for cfg_path, target_home in targets:
        if not cfg_path.parent.is_dir() or not (target_home / "python.exe").is_file():
            continue
        ver = "3.12.7" if "python312" in str(target_home).lower() else "3.10.11"
        clean_cfg = (
            f"home = {target_home}\n"
            "include-system-site-packages = false\n"
            f"version = {ver}\n"
        )
        cfg_path.write_text(clean_cfg, encoding="utf-8")

    # 若存在便携式自愈启动器模板，确保 venv/Scripts 与 comfyui/venv/Scripts 同步升级为相对路径自愈启动器
    bin_dir = base / "runtime" / "bin"
    for venv_scripts in (
        base / "runtime" / "venv" / "Scripts",
        base / "runtime" / "comfyui" / "venv" / "Scripts",
    ):
        if not venv_scripts.is_dir():
            continue
        for tpl_name, dst_name in (
            ("portable_venv_python.exe", "python.exe"),
            ("portable_venv_pythonw.exe", "pythonw.exe"),
        ):
            tpl = bin_dir / tpl_name
            dst = venv_scripts / dst_name
            if tpl.is_file():
                try:
                    if not dst.is_file() or dst.read_bytes() != tpl.read_bytes():
                        shutil.copy2(tpl, dst)
                except OSError:
                    pass
    return py_home


if __name__ == "__main__":
    arg_root = sys.argv[1] if len(sys.argv) > 1 else None
    rewrite_pyvenv_cfg(arg_root)
