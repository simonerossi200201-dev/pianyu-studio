# -*- coding: utf-8 -*-
"""右键表格工具 — 一键安装（不需要管理员）。"""
from __future__ import print_function

import os
import sys
import shutil
import subprocess
import winreg

APP_NAME = "右键表格工具"
REG_KEY = "RightClickTableTool"
DEST = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RightClickTableTool")


def here():
    return os.path.dirname(os.path.abspath(__file__))


def find_pythonw():
    cands = []
    exe = sys.executable
    if exe:
        d = os.path.dirname(exe)
        cands += [os.path.join(d, "pythonw.exe"), exe]
    for p in (
        r"C:\Program Files\Python312\pythonw.exe",
        r"C:\Program Files\Python311\pythonw.exe",
        r"C:\Program Files\Python310\pythonw.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python312\pythonw.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python311\pythonw.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python310\pythonw.exe"),
    ):
        cands.append(p)
    try:
        r = subprocess.run(["where", "pythonw"], capture_output=True, text=True, timeout=8)
        cands += [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    except Exception:
        pass
    seen = set()
    for p in cands:
        p = os.path.normpath(p)
        if p.lower() in seen:
            continue
        seen.add(p.lower())
        if os.path.isfile(p) and "WindowsApps" not in p:
            return p
    return None


def copy_files(pythonw):
    os.makedirs(DEST, exist_ok=True)
    src_py = os.path.join(here(), "app", "表格工具.py")
    if not os.path.isfile(src_py):
        raise SystemExit("找不到 app\\表格工具.py，请不要拆开这个文件夹再安装。")
    shutil.copy2(src_py, os.path.join(DEST, "表格工具.py"))
    src_rclone = os.path.join(here(), "app", "rclone.exe")
    if os.path.isfile(src_rclone):
        shutil.copy2(src_rclone, os.path.join(DEST, "rclone.exe"))
        print("已附带 rclone.exe（上传功能可用，MIT 许可）")
    else:
        print("未附带 rclone.exe：重命名功能不受影响；上传需要自行放入 rclone.exe")
    launcher = os.path.join(DEST, "打开.bat")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write("start \"\" \"%s\" \"%%~dp0表格工具.py\" %%*\r\n" % pythonw)


def pip_webview(pythonw):
    py = pythonw.replace("pythonw.exe", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    print("正在安装界面库 pywebview（BSD 许可，开源）…")
    try:
        subprocess.run(
            [py, "-m", "pip", "install", "--user", "pywebview==6.2.1"],
            timeout=180, check=False,
        )
    except Exception as e:
        print("pip 安装 pywebview 失败（可稍后手动安装）：", e)


def _reg_write(root, path, command, pythonw, script):
    key = winreg.CreateKey(root, path)
    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, APP_NAME)
    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "imageres.dll,-113")
    winreg.CloseKey(key)
    cmd = winreg.CreateKey(root, path + r"\command")
    winreg.SetValueEx(cmd, "", 0, winreg.REG_SZ, '"%s" "%s" "%%V"' % (pythonw, script))
    winreg.CloseKey(cmd)


def register(pythonw):
    script = os.path.join(DEST, "表格工具.py")
    hkcu = winreg.HKEY_CURRENT_USER
    _reg_write(hkcu, r"Software\Classes\Directory\Background\shell\%s" % REG_KEY, None, pythonw, script)
    _reg_write(hkcu, r"Software\Classes\Directory\shell\%s" % REG_KEY, None, pythonw, script)
    print("已写入当前用户的右键菜单（不需要管理员）")
    # 用当前用户配置盖住旧菜单名，避免同时出现两条
    for path in (
        r"Software\Classes\Directory\Background\shell\VisualRenamer",
        r"Software\Classes\Directory\shell\VisualRenamer",
    ):
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
            winreg.SetValueEx(key, "LegacyDisable", 0, winreg.REG_SZ, "")
            winreg.CloseKey(key)
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\VisualRenamer\command")
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\VisualRenamer")
        print("已移除旧的「表格化批量重命名」菜单")
    except OSError:
        pass


def main():
    print("======== %s 安装 ========" % APP_NAME)
    pythonw = find_pythonw()
    if not pythonw:
        print("没有找到 Python。请先安装 Python 3.10 或更高版本：")
        print("  https://www.python.org/downloads/")
        print("安装时勾选 Add python.exe to PATH。")
        return 1
    print("Python：", pythonw)
    copy_files(pythonw)
    pip_webview(pythonw)
    register(pythonw)
    print()
    print("安装完成。")
    print("  程序目录：", DEST)
    print("  在任意文件夹空白处点右键，选择「%s」。" % APP_NAME)
    print("  也可以双击：", os.path.join(DEST, "打开.bat"))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        print("安装失败：", e)
        code = 1
    if sys.stdin and sys.stdin.isatty():
        try:
            input("按回车退出")
        except Exception:
            pass
    sys.exit(code)
