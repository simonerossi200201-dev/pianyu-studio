# -*- coding: utf-8 -*-
"""右键表格工具 — 卸载（只删本工具，不动你的视频和工程文件）。"""
from __future__ import print_function

import os
import sys
import shutil
import winreg

REG_KEY = "RightClickTableTool"
DEST = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RightClickTableTool")


def _del_key(root, path):
    try:
        winreg.DeleteKey(root, path + r"\command")
    except OSError:
        pass
    try:
        winreg.DeleteKey(root, path)
        return True
    except OSError:
        return False


def main():
    print("======== 右键表格工具 卸载 ========")
    hkcu = winreg.HKEY_CURRENT_USER
    _del_key(hkcu, r"Software\Classes\Directory\Background\shell\%s" % REG_KEY)
    _del_key(hkcu, r"Software\Classes\Directory\shell\%s" % REG_KEY)
    _del_key(hkcu, r"Software\Classes\Directory\Background\shell\VisualRenamer")
    _del_key(hkcu, r"Software\Classes\Directory\shell\VisualRenamer")
    try:
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\VisualRenamer\command")
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\VisualRenamer")
    except OSError:
        pass
    if os.path.isdir(DEST):
        shutil.rmtree(DEST, ignore_errors=True)
        print("已删除", DEST)
    else:
        print("安装目录本来就不存在。")
    print("右键菜单已移除。视频文件、.renamer_project.json 工程文件都还在原来的文件夹里，没有动。")
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("卸载失败：", e)
    if sys.stdin and sys.stdin.isatty():
        try:
            input("按回车退出")
        except Exception:
            pass
