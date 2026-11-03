"""片语工作室便携环境重定位器 (core/portable.py)。

每次双击启动时，自动把 `<root>/runtime/venv/pyvenv.cfg` 中的 `home =`
重写为当前解压目录下的 `<root>/runtime/python`（Windows）或 `<root>/runtime/python/bin`（macOS/POSIX），
并同步确保自愈型便携启动器 (`runtime/venv/Scripts/python.exe` 或 `runtime/venv/bin/python3`) 就位。
确保用户把压缩包发给任何没装 Python 的新电脑（Windows 或 macOS Apple Silicon）、解压到任意位置后，
一键双击即可直接运行，零外部 Python 依赖。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import LTX_ROOT, resolve_python_exe, resolve_runtime_bin


def _has_python_binary(home_dir: Path) -> tuple[bool, Path]:
    """检查目标便携 Python 目录是否存在可执行文件，返回 (exists, home_for_pyvenv_cfg)。"""
    if os.name == "nt":
        exe = home_dir / "python.exe"
        return exe.is_file(), home_dir
    for sub in (home_dir / "bin" / "python3", home_dir / "bin" / "python", home_dir / "python3"):
        if sub.is_file():
            return True, sub.parent
    return False, home_dir


def rewrite_pyvenv_cfg(root: Path | str | None = None) -> Path:
    """将 runtime/venv/pyvenv.cfg 与 runtime/comfyui/venv/pyvenv.cfg 的 home 指向当前目录下的便携 Python。"""
    base = Path(str(root).rstrip("\\/")).resolve() if root else LTX_ROOT
    py_home = base / "runtime" / "python"
    comfy_py_home = base / "runtime" / "comfyui" / "python312"

    has_comfy_py, _ = _has_python_binary(comfy_py_home)
    targets = [
        (base / "runtime" / "venv" / "pyvenv.cfg", py_home),
        (
            base / "runtime" / "comfyui" / "venv" / "pyvenv.cfg",
            comfy_py_home if has_comfy_py else py_home,
        ),
    ]

    for cfg_path, target_root in targets:
        has_py, effective_home = _has_python_binary(target_root)
        if not cfg_path.parent.is_dir() or not has_py:
            continue
        if os.name == "nt":
            ver = "3.12.7" if "python312" in str(target_root).lower() else "3.10.11"
            clean_cfg = (
                f"home = {effective_home}\n"
                "include-system-site-packages = false\n"
                f"version = {ver}\n"
            )
            cfg_path.write_text(clean_cfg, encoding="utf-8")
        else:
            # macOS / POSIX: 绝不粗暴覆盖 uv venv 生成的 version_info / 3.11.x 元信息！
            # 仅当用户移动了文件夹导致旧 home 目录不存在时，才原位替换 home = 行并修复断裂软链
            if cfg_path.is_file():
                try:
                    lines = cfg_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    old_home = ""
                    for ln in lines:
                        if ln.strip().startswith("home"):
                            parts = ln.split("=", 1)
                            if len(parts) == 2:
                                old_home = parts[1].strip()
                            break
                    if old_home and not Path(old_home).is_dir():
                        new_lines = [
                            f"home = {effective_home}" if ln.strip().startswith("home") else ln
                            for ln in lines
                        ]
                        cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                except OSError:
                    pass
            # 若文件夹移动导致 runtime/venv/bin/python* 软链指向失效路径，自动重链到 effective_home/python3
            venv_bin = cfg_path.parent / "bin"
            target_py = effective_home / "python3"
            if venv_bin.is_dir() and target_py.is_file():
                for py_name in ("python", "python3", "python3.11", "python3.10"):
                    p_link = venv_bin / py_name
                    if p_link.is_symlink() and not p_link.exists():
                        try:
                            p_link.unlink()
                            p_link.symlink_to(target_py)
                        except OSError:
                            pass

    # Windows: 若存在便携式自愈启动器模板，确保 venv/Scripts 与 comfyui/venv/Scripts 同步升级为相对路径自愈启动器
    if os.name == "nt":
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
    else:
        # macOS / POSIX: 确保 runtime/venv/bin/python3 与 runtime/bin/* 具备可执行权限
        for exec_dir in (
            base / "runtime" / "venv" / "bin",
            base / "runtime" / "python" / "bin",
            base / "runtime" / "comfyui" / "venv" / "bin",
            base / "runtime" / "bin",
            base / "runtime" / "ffmpeg",
        ):
            if not exec_dir.is_dir():
                continue
            for item in exec_dir.iterdir():
                if item.is_file() and not item.is_symlink():
                    try:
                        item.chmod(item.stat().st_mode | 0o111)
                    except OSError:
                        pass
    return py_home


__all__ = ["rewrite_pyvenv_cfg", "resolve_python_exe", "resolve_runtime_bin"]


if __name__ == "__main__":
    arg_root = sys.argv[1] if len(sys.argv) > 1 else None
    rewrite_pyvenv_cfg(arg_root)
