# -*- coding: utf-8 -*-
"""右键表格工具 — 卸载（只删本工具，不动你的视频和工程文件）。"""
from __future__ import print_function

import os
import sys
import shutil

if os.name == "nt":
    import winreg
else:
    winreg = None

REG_KEY = "RightClickTableTool"
if sys.platform == "darwin":
    DEST = os.path.expanduser("~/Library/Application Support/RightClickTableTool")
elif os.name == "nt":
    DEST = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RightClickTableTool")
else:
    DEST = os.path.expanduser("~/.local/share/RightClickTableTool")


def _del_key(root, path):
    if winreg is None:
        return False
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
    if sys.platform == "darwin":
        for p in (
            os.path.expanduser("~/Library/Services/用表格工具打开.workflow"),
            os.path.expanduser("~/Applications/右键表格工具.app"),
        ):
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
                print("已移除", p)
    elif os.name == "nt" and winreg is not None:
        hkcu = winreg.HKEY_CURRENT_USER
        for kname in ("RightClickTableTool", "LTXStudioTableTool", "VisualRenamer"):
            _del_key(hkcu, r"Software\Classes\Directory\Background\shell\%s" % kname)
            _del_key(hkcu, r"Software\Classes\Directory\shell\%s" % kname)
            _del_key(hkcu, r"Software\Classes\Drive\shell\%s" % kname)
            _del_key(winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\%s" % kname)
            _del_key(winreg.HKEY_CLASSES_ROOT, r"Directory\shell\%s" % kname)
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
