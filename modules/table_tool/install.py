# -*- coding: utf-8 -*-
"""右键表格工具 — 跨平台一键安装（Windows 右键菜单 + macOS Finder 快速操作 & .app，不需要管理员）。"""
from __future__ import print_function

import os
import stat
import sys
import shutil
import subprocess
import uuid

if os.name == "nt":
    import winreg
else:
    winreg = None

APP_NAME = "右键表格工具"
REG_KEY = "RightClickTableTool"
if sys.platform == "darwin":
    DEST = os.path.expanduser("~/Library/Application Support/RightClickTableTool")
elif os.name == "nt":
    DEST = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RightClickTableTool")
else:
    DEST = os.path.expanduser("~/.local/share/RightClickTableTool")


def here():
    return os.path.dirname(os.path.abspath(__file__))


def find_pythonw():
    if os.name != "nt":
        ltx_root = os.path.abspath(os.path.join(here(), "..", ".."))
        for rel in (
            os.path.join("runtime", "venv", "bin", "python3"),
            os.path.join("runtime", "venv", "bin", "python"),
            os.path.join("runtime", "python", "bin", "python3"),
            os.path.join("runtime", "python", "bin", "python"),
        ):
            cand = os.path.join(ltx_root, rel)
            if os.path.isfile(cand):
                return cand
        return sys.executable or shutil.which("python3") or "python3"

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
        raise SystemExit("找不到 app/表格工具.py，请不要拆开这个文件夹再安装。")
    shutil.copy2(src_py, os.path.join(DEST, "表格工具.py"))
    rclone_name = "rclone.exe" if os.name == "nt" else "rclone"
    src_rclone = os.path.join(here(), "app", rclone_name)
    if os.path.isfile(src_rclone):
        dst_rclone = os.path.join(DEST, rclone_name)
        shutil.copy2(src_rclone, dst_rclone)
        if os.name != "nt":
            try:
                os.chmod(dst_rclone, os.stat(dst_rclone).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass
        print("已附带 %s（上传功能可用，MIT 许可）" % rclone_name)
    else:
        print("未附带 %s：重命名功能不受影响；上传可使用 runtime/bin/%s 或系统 rclone" % (rclone_name, rclone_name))
    if os.name == "nt":
        launcher = os.path.join(DEST, "打开.bat")
        with open(launcher, "w", encoding="utf-8") as f:
            f.write("@echo off\r\n")
            f.write('start "" "%s" "%%~dp0表格工具.py" %%*\r\n' % pythonw)
    else:
        launcher = os.path.join(DEST, "打开表格工具.command")
        with open(launcher, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write('DIR="$(cd "$(dirname "$0")" && pwd)"\n')
            f.write('exec "%s" "$DIR/表格工具.py" "$@"\n' % pythonw)
        try:
            os.chmod(launcher, os.stat(launcher).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass


def pip_webview(pythonw):
    py = pythonw.replace("pythonw.exe", "python.exe") if os.name == "nt" else pythonw
    if not os.path.isfile(py):
        py = sys.executable
    try:
        r = subprocess.run([py, "-c", "import webview"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return
    except Exception:
        pass
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


def install_macos_finder_integration(
    script_path,
    python_exe=None,
    services_dir=None,
    apps_dir=None,
):
    """在 macOS 下自动生成：
    1. ~/Library/Services/用表格工具打开.workflow (Finder 右键服务 / 快速操作)
    2. ~/Applications/右键表格工具.app (原生 .app 应用，支持拖入文件夹或直接打开当前最前 Finder 窗口)
    """
    script_abs = os.path.abspath(script_path)
    py_exe = python_exe or find_pythonw() or "python3"
    services_root = services_dir or os.path.expanduser("~/Library/Services")
    apps_root = apps_dir or os.path.expanduser("~/Applications")

    # 1. 创建 Finder Quick Action: 用表格工具打开.workflow
    wf_dir = os.path.join(services_root, "用表格工具打开.workflow", "Contents")
    os.makedirs(wf_dir, exist_ok=True)
    wf_info_plist = os.path.join(wf_dir, "Info.plist")
    wf_doc_wflow = os.path.join(wf_dir, "document.wflow")

    info_plist_xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSServices</key>
    <array>
        <dict>
            <key>NSBackgroundColorName</key>
            <string>background</string>
            <key>NSIconName</key>
            <string>NSActionTemplate</string>
            <key>NSMenuItem</key>
            <dict>
                <key>default</key>
                <string>用表格工具打开</string>
            </dict>
            <key>NSMessage</key>
            <string>runWorkflowAsService</string>
            <key>NSRequiredContext</key>
            <dict>
                <key>NSApplicationIdentifier</key>
                <string>com.apple.finder</string>
            </dict>
            <key>NSSendFileTypes</key>
            <array>
                <string>public.folder</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""
    shell_cmd = (
        f'for f in "$@"; do\n'
        f'  if [ -d "$f" ]; then\n'
        f'    nohup "{py_exe}" "{script_abs}" "$f" >/dev/null 2>&1 &\n'
        f'  else\n'
        f'    nohup "{py_exe}" "{script_abs}" "$(dirname "$f")" >/dev/null 2>&1 &\n'
        f'  fi\n'
        f'done\n'
    )
    act_uuid = str(uuid.uuid4()).upper()
    in_uuid = str(uuid.uuid4()).upper()
    out_uuid = str(uuid.uuid4()).upper()
    doc_wflow_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>AMApplicationBuild</key>
    <string>523</string>
    <key>AMApplicationVersion</key>
    <string>2.10</string>
    <key>AMDocumentVersion</key>
    <string>2</string>
    <key>actions</key>
    <array>
        <dict>
            <key>action</key>
            <dict>
                <key>AMAccepts</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Optional</key>
                    <true/>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.path</string>
                    </array>
                </dict>
                <key>AMActionVersion</key>
                <string>2.0.3</string>
                <key>AMApplication</key>
                <array>
                    <string>Automator</string>
                </array>
                <key>AMParameterProperties</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <dict/>
                    <key>CheckedForUserDefaultShell</key>
                    <dict/>
                    <key>inputMethod</key>
                    <dict/>
                    <key>shell</key>
                    <dict/>
                    <key>source</key>
                    <dict/>
                </dict>
                <key>AMProvides</key>
                <dict>
                    <key>Container</key>
                    <string>List</string>
                    <key>Types</key>
                    <array>
                        <string>com.apple.cocoa.path</string>
                    </array>
                </dict>
                <key>ActionBundlePath</key>
                <string>/System/Library/Automator/Run Shell Script.action</string>
                <key>ActionName</key>
                <string>Run Shell Script</string>
                <key>ActionParameters</key>
                <dict>
                    <key>COMMAND_STRING</key>
                    <string>{shell_cmd}</string>
                    <key>CheckedForUserDefaultShell</key>
                    <true/>
                    <key>inputMethod</key>
                    <integer>1</integer>
                    <key>shell</key>
                    <string>/bin/bash</string>
                    <key>source</key>
                    <string></string>
                </dict>
                <key>BundleIdentifier</key>
                <string>com.apple.RunShellScript</string>
                <key>CFBundleVersion</key>
                <string>2.0.3</string>
                <key>CanShowSelectedItemsWhenRun</key>
                <false/>
                <key>CanShowWhenRun</key>
                <true/>
                <key>Category</key>
                <array>
                    <string>AMCategoryUtilities</string>
                </array>
                <key>Class Name</key>
                <string>RunShellScriptAction</string>
                <key>InputUUID</key>
                <string>{in_uuid}</string>
                <key>Keywords</key>
                <array>
                    <string>Shell</string>
                    <string>Script</string>
                </array>
                <key>OutputUUID</key>
                <string>{out_uuid}</string>
                <key>UUID</key>
                <string>{act_uuid}</string>
                <key>UnlocalizedApplications</key>
                <array>
                    <string>Automator</string>
                </array>
            </dict>
        </dict>
    </array>
    <key>connectors</key>
    <dict/>
    <key>workflowMetaData</key>
    <dict>
        <key>applicationBundleID</key>
        <string>com.apple.finder</string>
        <key>applicationBundleIDsByPath</key>
        <dict>
            <key>/System/Library/CoreServices/Finder.app</key>
            <string>com.apple.finder</string>
        </dict>
        <key>applicationPath</key>
        <string>/System/Library/CoreServices/Finder.app</string>
        <key>applicationPaths</key>
        <array>
            <string>/System/Library/CoreServices/Finder.app</string>
        </array>
        <key>inputTypeIdentifier</key>
        <string>com.apple.Automator.fileSystemObject.folder</string>
        <key>outputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>presentationMode</key>
        <integer>15</integer>
        <key>processesInput</key>
        <integer>0</integer>
        <key>serviceApplicationBundleID</key>
        <string>com.apple.finder</string>
        <key>serviceApplicationPath</key>
        <string>/System/Library/CoreServices/Finder.app</string>
        <key>serviceInputTypeIdentifier</key>
        <string>com.apple.Automator.fileSystemObject.folder</string>
        <key>serviceOutputTypeIdentifier</key>
        <string>com.apple.Automator.nothing</string>
        <key>serviceProcessesInput</key>
        <integer>0</integer>
        <key>systemImageName</key>
        <string>NSActionTemplate</string>
        <key>useAutomaticInputType</key>
        <integer>0</integer>
        <key>workflowTypeIdentifier</key>
        <string>com.apple.Automator.servicesMenu</string>
    </dict>
</dict>
</plist>
"""
    with open(wf_info_plist, "w", encoding="utf-8") as f:
        f.write(info_plist_xml)
    with open(wf_doc_wflow, "w", encoding="utf-8") as f:
        f.write(doc_wflow_xml)

    # 2. 创建 ~/Applications/右键表格工具.app
    app_bundle = os.path.join(apps_root, "右键表格工具.app")
    app_contents = os.path.join(app_bundle, "Contents")
    app_macos = os.path.join(app_contents, "MacOS")
    os.makedirs(app_macos, exist_ok=True)
    app_plist = os.path.join(app_contents, "Info.plist")
    app_exec = os.path.join(app_macos, "RightClickTableTool")

    app_plist_xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>右键表格工具</string>
    <key>CFBundleExecutable</key>
    <string>RightClickTableTool</string>
    <key>CFBundleIdentifier</key>
    <string>com.cutora.rightclicktabletool</string>
    <key>CFBundleName</key>
    <string>右键表格工具</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>3.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key>
            <string>Folder</string>
            <key>CFBundleTypeRole</key>
            <string>Viewer</string>
            <key>LSHandlerRank</key>
            <string>Alternate</string>
            <key>LSItemContentTypes</key>
            <array>
                <string>public.folder</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""
    app_exec_sh = f"""#!/bin/bash
TARGET="${{1:-}}"
if [ -z "$TARGET" ]; then
    TARGET="$(osascript -e 'tell application "Finder" to if (count of Finder windows) > 0 then get POSIX path of (target of front Finder window as alias)' 2>/dev/null || true)"
fi
if [ -z "$TARGET" ] || [ ! -d "$TARGET" ]; then
    TARGET="$HOME/Desktop"
fi
exec "{py_exe}" "{script_abs}" "$TARGET"
"""
    with open(app_plist, "w", encoding="utf-8") as f:
        f.write(app_plist_xml)
    with open(app_exec, "w", encoding="utf-8") as f:
        f.write(app_exec_sh)
    try:
        os.chmod(app_exec, os.stat(app_exec).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            subprocess.run(["/System/Library/CoreServices/pbs", "-update"], timeout=5, capture_output=True)
        except Exception:
            pass
    return {
        "workflow": os.path.join(services_root, "用表格工具打开.workflow"),
        "app": app_bundle,
    }


def register(pythonw):
    script = os.path.join(DEST, "表格工具.py")
    if sys.platform == "darwin":
        res = install_macos_finder_integration(script, python_exe=pythonw)
        print("已创建 macOS Finder 右键快速操作：", res["workflow"])
        print("已创建 macOS 应用程序包：", res["app"])
        return
    if os.name != "nt" or winreg is None:
        return
    hkcu = winreg.HKEY_CURRENT_USER
    _reg_write(hkcu, r"Software\Classes\Directory\Background\shell\%s" % REG_KEY, None, pythonw, script)
    _reg_write(hkcu, r"Software\Classes\Directory\shell\%s" % REG_KEY, None, pythonw, script)
    print("已写入当前用户的右键菜单（不需要管理员）")
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
        return 1
    print("Python：", pythonw)
    copy_files(pythonw)
    pip_webview(pythonw)
    register(pythonw)
    print()
    print("安装完成。")
    print("  程序目录：", DEST)
    if sys.platform == "darwin":
        print("  在 Finder 中对任意文件夹点右键 → 快速操作 → 选择「用表格工具打开」。")
        print("  也可以从 ~/Applications/右键表格工具.app 直接启动。")
    else:
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
