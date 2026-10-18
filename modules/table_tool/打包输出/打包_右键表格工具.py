# -*- coding: utf-8 -*-
"""
右键表格工具 —— 内部分发打包脚本

只读取源文件夹，不修改源文件夹里的任何东西。
按白名单挑出需要的文件 → 复制到临时目录 → 去掉开发者本机路径 →
逐项自检 → 生成 zip + 版本说明 + SHA256。

用法：  python 打包_右键表格工具.py
        python 打包_右键表格工具.py "D:\\0_Dev\\右键表格工具" "D:\\0_Dev\\打包输出"
"""
import hashlib
import os
import py_compile
import shutil
import sys
import tempfile
import time
import zipfile

SRC = sys.argv[1] if len(sys.argv) > 1 else r"D:\0_Dev\右键表格工具"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:\0_Dev\打包输出"
TOP = "右键表格工具"                      # 压缩包里的顶层文件夹名
VERSION = time.strftime("%Y.%m.%d")

# 白名单：只打包这些。备份(.bak-*)、__pycache__、日志、工程文件一律不会进包。
INCLUDE = [
    "app/表格工具.py",
    "app/rclone.exe",
    "install.py",
    "uninstall.py",
    "安装.bat",
    "卸载.bat",
    "使用说明.md",
    "README.txt",
    "许可与第三方说明.txt",
    "licenses/rclone-README.txt",
    "说明文档/使用说明.html",
    "说明文档/使用说明.md",
] + ["说明文档/截图/%s.png" % n for n in (
    "01-main", "02-download", "03-mkdir", "05-drive-connected-dlg", "05b-drive-pill",
    "06-drive-intro-dlg", "07-drive-auth-step-dlg", "08-drive-switch-dlg", "09-drive-success-dlg")]
OPTIONAL = {"README.txt", "许可与第三方说明.txt", "licenses/rclone-README.txt"}

# 分发版里去掉开发者本机的路径（源文件夹里的原文件不动）
SCRUB = [
    ('    r"D:\\0_Dev\\下载图片\\Upload\\rclone.exe",\n', ""),
    ('"未找到 rclone.exe。请把它放到工具目录，或放到 D:\\\\0_Dev\\\\下载图片\\\\Upload\\\\"',
     '"未找到 rclone.exe。请重新运行「安装.bat」，它会把 rclone 一起装好。"'),
]

# 包里绝不能出现的东西（个人路径、账号、授权令牌）
FORBIDDEN = ["0_Dev", "@gmail.com", "client_secret_", "ya29.", "1//0", "[gdrive]"]

ok_all = True


def say(ok, msg):
    global ok_all
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        ok_all = False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("源文件夹：", SRC)
    print("输出到：  ", OUT)
    print("版本：    ", VERSION)
    if not os.path.isdir(SRC):
        print("✗ 源文件夹不存在，停止。")
        return 2

    print("\n[1/6] 检查文件是否齐全")
    files = []
    for rel in INCLUDE:
        p = os.path.join(SRC, *rel.split("/"))
        if os.path.isfile(p):
            files.append(rel)
            say(True, "%s（%s 字节）" % (rel, os.path.getsize(p)))
        elif rel in OPTIONAL:
            print("  - %s 不存在（可选，跳过）" % rel)
        else:
            say(False, "%s 缺失" % rel)
    if not ok_all:
        print("\n必需文件缺失，停止。")
        return 2

    stage = tempfile.mkdtemp(prefix="rctt_pack_")
    root = os.path.join(stage, TOP)
    try:
        print("\n[2/6] 复制到临时目录（源文件夹不做任何修改）")
        for rel in files:
            dst = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(SRC, *rel.split("/")), dst)
        say(True, "已复制 %d 个文件" % len(files))

        print("\n[3/6] 去掉开发者本机路径")
        py = os.path.join(root, "app", "表格工具.py")
        with open(py, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        for old, new in SCRUB:
            n = text.count(old)
            if n == 1:
                text = text.replace(old, new)
                say(True, "已替换：%s…" % old.strip()[:40])
            elif n == 0:
                print("  - 没找到（可能已经去掉过）：%s…" % old.strip()[:40])
            else:
                say(False, "出现了 %d 次，不确定该改哪一处：%s…" % (n, old.strip()[:40]))
        with open(py, "w", encoding="utf-8", newline="") as f:
            f.write(text)

        print("\n[4/6] 自检")
        for rel in ("app/表格工具.py", "install.py", "uninstall.py"):
            p = os.path.join(root, *rel.split("/"))
            if not os.path.isfile(p):
                continue
            try:
                py_compile.compile(p, cfile=os.path.join(stage, "_chk.pyc"), doraise=True)
                say(True, "%s 语法检查通过" % rel)
            except py_compile.PyCompileError as e:
                say(False, "%s 语法错误：%s" % (rel, e))
        for rel in ("安装.bat", "卸载.bat"):
            with open(os.path.join(root, rel), "rb") as f:
                raw = f.read()
            ascii_ok = all(b < 128 for b in raw)
            crlf_ok = raw.count(b"\n") == raw.count(b"\r\n")
            say(ascii_ok and crlf_ok,
                "%s 是纯英文 + Windows 换行（否则双击会乱码）%s" %
                (rel, "" if ascii_ok and crlf_ok else "：ASCII=%s CRLF=%s" % (ascii_ok, crlf_ok)))
        hits = []
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.lower().endswith((".exe", ".png", ".jpg")):
                    continue
                if os.path.relpath(dp, root).split(os.sep)[0] == "licenses":
                    continue      # 第三方许可文档原样附带（rclone 官方说明书里有示例配置和贡献者名单）
                p = os.path.join(dp, fn)
                with open(p, "rb") as f:
                    data = f.read().decode("utf-8", "ignore")
                for word in FORBIDDEN:
                    if word in data:
                        hits.append("%s 里有「%s」" % (os.path.relpath(p, root), word))
        say(not hits, "没有个人路径、账号或授权信息" + ("：" + "；".join(hits) if hits else ""))
        rc = os.path.join(root, "app", "rclone.exe")
        say(os.path.getsize(rc) > 10 * 1024 * 1024, "rclone.exe 大小正常（%.1f MB）" % (os.path.getsize(rc) / 1048576))
        if not ok_all:
            print("\n自检没通过，不生成压缩包。请把上面打 ✗ 的内容原样反馈。")
            return 1

        print("\n[5/6] 写版本说明")
        tool_sha = sha256(py)
        with open(os.path.join(root, "版本.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("右键表格工具  内部分发版 %s\n" % VERSION)
            f.write("打包时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("app\\表格工具.py SHA256：%s\n\n" % tool_sha)
            f.write("安装：把整个「右键表格工具」文件夹放到电脑上（不要拆开），双击「安装.bat」。\n")
            f.write("需要：Windows 10/11 + Python 3.10 或更高（安装 Python 时勾选 Add python.exe to PATH）。\n")
            f.write("上传 / 下载：第一次用时，点软件右上角「连接网盘」→「一键连接」，在浏览器里点「允许」即可，连的是你自己的 Google Drive。\n")
        say(True, "版本.txt")

        print("\n[6/6] 生成压缩包")
        os.makedirs(OUT, exist_ok=True)
        zpath = os.path.join(OUT, "%s_内部版_%s.zip" % (TOP, VERSION))
        if os.path.exists(zpath):
            zpath = zpath[:-4] + time.strftime("_%H%M%S") + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for dp, _, fns in os.walk(root):
                for fn in sorted(fns):
                    p = os.path.join(dp, fn)
                    arc = os.path.relpath(p, stage).replace(os.sep, "/")
                    z.write(p, arc)
        with zipfile.ZipFile(zpath) as z:
            bad = z.testzip()
            names = z.namelist()
        say(bad is None, "压缩包完整性检查")
        print("\n压缩包里的文件：")
        for n in names:
            print("   ", n)
        print("\n结果")
        print("  文件：", zpath)
        print("  大小：%.1f MB" % (os.path.getsize(zpath) / 1048576))
        print("  SHA256：", sha256(zpath))
        print("\n全部通过 ✓" if ok_all else "\n有项目没通过 ✗")
        return 0 if ok_all else 1
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
