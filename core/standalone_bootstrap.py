# -*- coding: utf-8 -*-
"""片语工作室 (PianYu Studio v3.0) — 零模型依赖·单包即开即用引导器 (core/standalone_bootstrap.py)。

设计目标：
1. 让只下载了《02_代码主程序包》（未下载 17.5 GB《01_模型包》）的用户，解压后即可直接独立运行任意一款软件：
   - 『右键表格工具』：100% 依赖内置便携 Python 标准库 + 内置 rclone，0 秒等待、0 模型依赖、0 pip 安装直接打开独立窗口，并自动注册系统鼠标右键菜单！
   - 『Orante 2.0 制片台』：若未合并 01 包的 venv，自动使用内置 Python 极速拉起轻量 Web 依赖（约 15 秒），无需任何本地大模型即可使用云端 Flow / Grok 批量排片生片！
   - 『片语剪辑台 CUTORA』：若未合并 01 包的 venv，自动使用内置 Python 按需安装轻量桌面剪辑界面依赖（跳过 2.5GB Torch 与 17.5GB 本地模型），即可正常剪辑、加转场与导出视频！
   - 『片语工作室总控台』：若已安装 PySide6 则打开原生总控台；若仅下载了 02 小包尚未安装 PySide6，则 0.3 秒直接弹出同款黑金 9:16 独立桌面总控窗口！
2. 自动整理并隐藏根目录下的内部技术文件夹（config / core / licenses / modules / resources / runtime 等）与内部脚本，
   让用户打开解压目录时只看到清爽的 1~5 号功能入口与说明书，彻底告别杂乱无章。
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LTX_ROOT = Path(__file__).resolve().parent.parent
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# 根目录下允许对用户直接展示的白名单文件/文件夹；其余内部目录或技术脚本自动设为隐藏属性
VISIBLE_ROOT_ITEMS = {
    # Windows 清爽入口
    "1_片语工作室_总控台.bat",
    "2_右键表格工具_独立运行与注册右键.bat",
    "3_Orante制片台_独立运行.bat",
    "4_片语剪辑台_独立运行.bat",
    "5_安装与修复体检.bat",
    # macOS 清爽入口
    "1_启动片语工作室.command",
    "2_打开右键表格工具.command",
    "3_打开Orante制片台.command",
    "4_打开片语剪辑台.command",
    "5_安装与体检.command",
    # 说明书与扩展包存放夹
    "片语工作室_安装与使用说明书.pdf",
    "packs",
}

# 需要隐藏的内部目录与技术文件（仅在打包出的用户目录或启动时隐藏，保持物理路径 100% 不变，绝不破坏任何模块引用）
INTERNAL_HIDDEN_ROOT_ITEMS = {
    "config",
    "core",
    "data",
    "logs",
    "licenses",
    "modules",
    "resources",
    "runtime",
    "studio_launcher.py",
    "install_studio.py",
    "片语工作室.bat",
    "片语工作室.vbs",
    "打开表格工具.bat",
    "安装与体检.bat",
    "启动片语工作室.command",
    "安装与体检.command",
    "代码热更新_双击同步最新功能.bat",
    "收集卡死诊断信息.bat",
    "收集卡死诊断信息.ps1",
    "创建桌面快捷方式.bat",
    "启动本地算力网关.bat",
    "requirements.txt",
    "requirements-macos-arm64.lock",
    "README.md",
    "安装与扩展包使用指南.md",
    "README_macOS_AppleSilicon_安装与启动说明.txt",
    "README_模型与环境底座包.txt",
}

PIP_MIRRORS = [
    ("清华镜像源 (Tuna)", "https://pypi.tuna.tsinghua.edu.cn/simple", "pypi.tuna.tsinghua.edu.cn"),
    ("阿里云镜像源 (Aliyun)", "https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ("官方源 (PyPI)", "https://pypi.org/simple", "pypi.org"),
]

ORANTE_LIGHT_REQUIREMENTS = [
    ("fastapi", "fastapi==0.141.1"),
    ("uvicorn", "uvicorn==0.52.4"),
    ("requests", "requests==2.34.2"),
    ("urllib3", "urllib3==2.8.0"),
    ("PIL", "pillow==12.3.0"),
    ("cryptography", "cryptography==50.0.1"),
    ("imageio_ffmpeg", "imageio-ffmpeg==0.6.0"),
    ("webview", "pywebview==6.2.1"),
    ("playwright", "playwright==1.62.0"),
]

CUTORA_LIGHT_REQUIREMENTS = [
    ("PySide6", "PySide6==6.10.2"),
    ("yaml", "PyYAML==6.0.3"),
    ("pandas", "pandas==2.2.3"),
    ("openpyxl", "openpyxl==3.1.5"),
    ("numpy", "numpy==1.26.4"),
    ("PIL", "pillow==12.3.0"),
    ("cv2", "opencv-python-headless==4.9.0.80"),
    ("soundfile", "soundfile==0.13.1"),
    ("scipy", "scipy==1.11.4"),
    ("psutil", "psutil==6.1.1"),
    ("imageio_ffmpeg", "imageio-ffmpeg==0.6.0"),
    ("fastapi", "fastapi==0.141.1"),
    ("uvicorn", "uvicorn==0.52.4"),
    ("httpx", "httpx==0.28.1"),
    ("pydantic", "pydantic==2.13.5"),
    ("cryptography", "cryptography==50.0.1"),
]


def hide_internal_root_clutter(root: Path = LTX_ROOT) -> None:
    """将根目录下的内部技术文件夹与底层脚本设为隐藏属性，保持用户解压目录清爽整洁。
    注意：若当前目录包含 .git（开发者主仓库 D:\\0_LTX），则不隐藏源码文件夹以便日常开发。
    """
    if (root / ".git").exists():
        return
    FILE_ATTRIBUTE_HIDDEN = 0x02
    for name in INTERNAL_HIDDEN_ROOT_ITEMS:
        if name in VISIBLE_ROOT_ITEMS:
            continue
        p = root / name
        if not p.exists():
            continue
        try:
            if os.name == "nt":
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
                if attrs != -1 and not (attrs & FILE_ATTRIBUTE_HIDDEN):
                    ctypes.windll.kernel32.SetFileAttributesW(str(p), attrs | FILE_ATTRIBUTE_HIDDEN)
            elif sys.platform == "darwin":
                subprocess.run(["chflags", "hidden", str(p)], capture_output=True, timeout=2)
        except Exception:
            pass


def try_silent_merge_sibling_model_pack(root: Path = LTX_ROOT) -> bool:
    """如果用户同时下载了解压了《01_模型与环境底座包》在同级目录，静默合并进来；
    如果没有下载《01_模型包》，绝不报错、绝不拦截，直接进入单包轻量独立运行模式！
    """
    venv_py_win = root / "runtime" / "venv" / "Scripts" / "python.exe"
    venv_py_mac = root / "runtime" / "venv" / "bin" / "python3"
    models_asr = root / "runtime" / "models" / "asr_small"
    if (venv_py_win.is_file() or venv_py_mac.is_file()) and models_asr.is_dir():
        return True

    if not root.parent or not root.parent.exists():
        return False

    try:
        for cand in root.parent.iterdir():
            if not cand.is_dir() or cand.resolve() == root.resolve():
                continue
            cname = cand.name.lower()
            if "片语工作室" not in cname and "01_" not in cname and "模型" not in cname:
                continue
            cand_rt = cand / "runtime"
            if not cand_rt.is_dir():
                cand_sub = cand / "片语工作室" / "runtime"
                if cand_sub.is_dir():
                    cand_rt = cand_sub
                else:
                    continue
            has_models = (cand_rt / "models" / "asr_small").is_dir()
            if has_models:
                print(f"[自动合并] 检测到同级目录存在《01_模型与环境底座包》({cand.name})，正在无缝合并...", flush=True)
                dst_rt = root / "runtime"
                dst_rt.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    subprocess.run(
                        ["robocopy", str(cand_rt), str(dst_rt), "/E", "/XO", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                        capture_output=True,
                    )
                else:
                    subprocess.run(["rsync", "-a", f"{cand_rt}/", f"{dst_rt}/"], capture_output=True)
                print("[自动合并] 模型与运行底座已合并完成！", flush=True)
                return True
    except Exception:
        pass
    return False


def _repair_portable_venv_binaries(root: Path = LTX_ROOT) -> None:
    """修复 Windows 下 runtime/venv/Scripts/python*.exe 便携启动器与 pyvenv.cfg。"""
    try:
        from core.portable import rewrite_pyvenv_cfg
        rewrite_pyvenv_cfg(root)
    except Exception:
        pass
    if os.name == "nt":
        venv_scripts = root / "runtime" / "venv" / "Scripts"
        if venv_scripts.is_dir():
            for src_name, dst_name in (
                ("portable_venv_python.exe", "python.exe"),
                ("portable_venv_pythonw.exe", "pythonw.exe"),
            ):
                src = root / "runtime" / "bin" / src_name
                dst = venv_scripts / dst_name
                if src.is_file():
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass


def _find_missing_modules(py_exe: str, req_list: list[tuple[str, str]]) -> list[str]:
    """在目标 Python 解释器中检测缺失的 pip 包列表。"""
    mod_names = [m for m, _ in req_list]
    code = (
        "import importlib.util, json; "
        f"mods = {json.dumps(mod_names)}; "
        "missing = [m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(json.dumps(missing))"
    )
    try:
        r = subprocess.run([py_exe, "-c", code], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and (r.stdout or "").strip():
            import json as _json
            missing_mods = set(_json.loads(r.stdout.strip()))
            return [pip_spec for mod_name, pip_spec in req_list if mod_name in missing_mods]
    except Exception:
        pass
    # 如果目标解释器就是当前解释器，直接在进程内查
    if Path(py_exe).resolve() == Path(sys.executable).resolve():
        return [
            pip_spec
            for mod_name, pip_spec in req_list
            if importlib.util.find_spec(mod_name) is None
        ]
    return [pip_spec for _, pip_spec in req_list]


def ensure_lightweight_deps(py_exe: str, req_list: list[tuple[str, str]], app_title: str) -> bool:
    """按需为独立运行的软件安装轻量级依赖包（无需下载 17.5GB 模型包，自动切换国内高速镜像源）。"""
    import json
    missing_specs = _find_missing_modules(py_exe, req_list)
    if not missing_specs:
        return True

    print("================================================================", flush=True)
    print(f"  ⚡ 正在为【{app_title}】初始化轻量独立运行环境（无需下载大模型包）", flush=True)
    print(f"  · 待安装轻量组件 ({len(missing_specs)} 项): {', '.join(s.split('==')[0] for s in missing_specs)}", flush=True)
    print("================================================================", flush=True)

    # 确保 pip 可用
    subprocess.run([py_exe, "-m", "ensurepip", "--upgrade"], capture_output=True)

    for mirror_name, index_url, trusted_host in PIP_MIRRORS:
        print(f"\n[正在连接 {mirror_name}] 自动拉取轻量运行组件...", flush=True)
        cmd = [
            py_exe,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "-i",
            index_url,
            "--trusted-host",
            trusted_host,
            *missing_specs,
        ]
        rc = subprocess.call(cmd)
        if rc == 0:
            still_missing = _find_missing_modules(py_exe, req_list[:4])
            if not still_missing:
                print(f"✅ 【{app_title}】轻量独立运行环境就绪！正在启动界面...", flush=True)
                return True
    print(f"⚠ 部分组件安装未完成，将尝试以基础兼容模式启动【{app_title}】...", flush=True)
    return False


def _ensure_shortcuts_and_context_menu(quiet: bool = True) -> None:
    try:
        from core.shortcut import ensure_shortcuts, ensure_table_tool_registered
        ensure_table_tool_registered(LTX_ROOT, quiet=quiet)
        ensure_shortcuts(LTX_ROOT, force=(os.name == "nt"), quiet=quiet)
    except Exception:
        pass


def run_app(app_target: str, extra_args: list[str]) -> int:
    from core import resolve_python_exe

    hide_internal_root_clutter(LTX_ROOT)
    try_silent_merge_sibling_model_pack(LTX_ROOT)
    _repair_portable_venv_binaries(LTX_ROOT)
    _ensure_shortcuts_and_context_menu(quiet=(app_target != "install"))

    py_console = resolve_python_exe(gui=False, root=LTX_ROOT)
    py_gui = resolve_python_exe(gui=True, root=LTX_ROOT)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PATH"] = os.pathsep.join([
        str(LTX_ROOT / "runtime" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg"),
        env.get("PATH", ""),
    ])

    if app_target == "table_tool":
        # 右键表格工具：100% 零模型、零第三方强依赖，内置 Python 0.3 秒即开即用！
        script = LTX_ROOT / "modules" / "table_tool" / "app" / "表格工具.py"
        cmd = [py_gui, str(script), *extra_args]
        subprocess.Popen(cmd, cwd=str(script.parent), env=env)
        return 0

    if app_target == "orante":
        ensure_lightweight_deps(py_console, ORANTE_LIGHT_REQUIREMENTS, "Orante 2.0 制片台")
        py_gui = resolve_python_exe(gui=True, root=LTX_ROOT)
        orante_dir = LTX_ROOT / "modules" / "orante_desk"
        cmd = [py_gui, "-m", "orante", *extra_args]
        subprocess.Popen(cmd, cwd=str(orante_dir), env=env)
        return 0

    if app_target == "cutora":
        ensure_lightweight_deps(py_console, CUTORA_LIGHT_REQUIREMENTS, "片语剪辑台 CUTORA")
        py_gui = resolve_python_exe(gui=True, root=LTX_ROOT)
        cutora_dir = LTX_ROOT / "modules" / "cutora_editor"
        cmd = [py_gui, "-m", "cutora", *extra_args]
        subprocess.Popen(cmd, cwd=str(cutora_dir), env=env)
        return 0

    if app_target == "install":
        rc = subprocess.call([py_console, str(LTX_ROOT / "install_studio.py"), *extra_args], cwd=str(LTX_ROOT), env=env)
        hide_internal_root_clutter(LTX_ROOT)
        return rc

    # 默认：启动片语工作室总控台 (studio_launcher.py)
    subprocess.Popen([py_gui, str(LTX_ROOT / "studio_launcher.py"), *extra_args], cwd=str(LTX_ROOT), env=env)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="片语工作室单包独立引导器")
    parser.add_argument(
        "--app",
        default="launcher",
        choices=["launcher", "table_tool", "orante", "cutora", "install"],
        help="要独立启动的目标软件模块",
    )
    args, extra = parser.parse_known_args()
    return run_app(args.app, extra)


if __name__ == "__main__":
    raise SystemExit(main())
