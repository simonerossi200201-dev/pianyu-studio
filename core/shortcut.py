"""片语工作室 (D:\\0_LTX) 纯 Python COM 桌面与开始菜单快捷方式生成器 (core/shortcut.py)。

特性：
1. 采用 Windows 原生 `ctypes` + `IShellLinkW` + `IPersistFile` COM 接口直接落盘 `.lnk`，
   绝不调用 `powershell -Command`（彻底杜绝黑框闪现与杀毒软件误报）。
2. 自动识别 OneDrive 接管的桌面路径（通过 `SHGetFolderPathW(CSIDL_DESKTOPDIRECTORY)`）。
3. 桌面一键生成 `片语工作室.lnk`（挂载 7 尺寸多分辨率图标 `resources/brand/app.ico`），
   开始菜单同步生成「片语工作室」、「片语剪辑台 CUTORA」、「Orante 制片台」三枚快捷方式。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import LTX_ROOT

APP_NAME = "片语工作室"
DESCRIPTION = "片语工作室 (Cutora LTX Studio v3.0) — 全链路 AI 制片与剪辑工作台"

_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"

_SET_DESCRIPTION = 7
_SET_WORKING_DIR = 9
_SET_ARGUMENTS = 11
_SET_SHOW_CMD = 15
_SET_ICON_LOCATION = 17
_SET_PATH = 20
_PERSIST_SAVE = 6


def desktop_dir() -> Path:
    """获取真实桌面路径（兼容 OneDrive 桌面重定向）。"""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            pass
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for cand in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if cand.is_dir():
            return cand
    return home


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


def _guid(s: str):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    g = GUID()
    ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


def _vcall(ptr, index: int, *args, argtypes=()):
    import ctypes
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    hr = fn(ptr, *args)
    if hr != 0:
        raise OSError(f"COM 调用失败 (index={index}, HRESULT=0x{hr & 0xFFFFFFFF:08X})")


def _vcall_release(ptr) -> None:
    import ctypes
    try:
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        proto(vtbl[2])(ptr)
    except Exception:
        pass


def write_lnk(item: dict) -> None:
    """使用 Windows 原生 IShellLinkW 写入 .lnk 文件（无黑框、零杀软误报）。"""
    import ctypes

    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass
    ptr = ctypes.c_void_p()
    ole32.CoCreateInstance(
        ctypes.byref(_guid(_CLSID_SHELLLINK)),
        None,
        1,
        ctypes.byref(_guid(_IID_ISHELLLINKW)),
        ctypes.byref(ptr),
    )
    try:
        _vcall(ptr, _SET_PATH, ctypes.c_wchar_p(str(item["target"])), argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_ARGUMENTS, ctypes.c_wchar_p(str(item.get("args") or "")), argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_WORKING_DIR, ctypes.c_wchar_p(str(item["workdir"])), argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_DESCRIPTION, ctypes.c_wchar_p(str(item.get("desc") or "")), argtypes=(ctypes.c_wchar_p,))
        ico = item.get("icon")
        if ico and Path(ico).is_file():
            _vcall(ptr, _SET_ICON_LOCATION, ctypes.c_wchar_p(str(ico)), 0, argtypes=(ctypes.c_wchar_p, ctypes.c_int))
        _vcall(ptr, _SET_SHOW_CMD, 1, argtypes=(ctypes.c_int,))

        persist = ctypes.c_void_p()
        _vcall(
            ptr,
            0,
            ctypes.byref(_guid(_IID_IPERSISTFILE)),
            ctypes.byref(persist),
            argtypes=(ctypes.c_void_p, ctypes.c_void_p),
        )
        try:
            dest = Path(item["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            _vcall(persist, _PERSIST_SAVE, ctypes.c_wchar_p(str(dest)), 1, argtypes=(ctypes.c_wchar_p, ctypes.c_int))
        finally:
            _vcall_release(persist)
    finally:
        _vcall_release(ptr)


def write_macos_app_bundle(
    app_path: Path,
    app_name: str,
    bundle_id: str,
    exec_cmd: str,
    workdir: Path,
) -> Path:
    """在 macOS 上生成标准 .app 应用包 (Contents/Info.plist + Contents/MacOS/Launcher)。
    支持过滤 Finder 传入的 -psn_* 参数、自动清理隔离属性并执行 ad-hoc 重签名，确保关掉后可无限次重新双击打开。
    """
    import stat
    import subprocess

    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)
    plist_path = contents_dir / "Info.plist"
    exec_path = macos_dir / "Launcher"

    plist_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>{app_name}</string>
    <key>CFBundleExecutable</key>
    <string>Launcher</string>
    <key>CFBundleIdentifier</key>
    <string>{bundle_id}</string>
    <key>CFBundleName</key>
    <string>{app_name}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>3.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    script_txt = f"""#!/bin/bash
TARGET_DIR="{workdir}"
if [ ! -d "$TARGET_DIR" ]; then
    for CAND in "$HOME/Downloads/片语/片语工作室" "$HOME/Downloads/片语工作室" "$HOME/Desktop/片语工作室" "$HOME/Applications/片语工作室" "$HOME/PianyuStudio"; do
        if [ -d "$CAND" ] && [ -f "$CAND/studio_launcher.py" ]; then
            TARGET_DIR="$CAND"
            break
        fi
    done
fi
if [ ! -d "$TARGET_DIR" ]; then
    osascript -e 'display alert "片语工作室目录已移动" message "未找到原安装目录：{workdir}\\n\\n请在新位置打开文件夹并双击运行一次《安装与体检.command》以自动更新桌面图标。" as warning' >/dev/null 2>&1 || true
    exit 1
fi
cd "$TARGET_DIR" || exit 1
export PYTHONUTF8=1
export PATH="$TARGET_DIR/runtime/bin:$TARGET_DIR/runtime/ffmpeg/bin:$TARGET_DIR/runtime/ffmpeg:/opt/homebrew/bin:/usr/local/bin:$PATH"
ARGS=()
for arg in "$@"; do
    case "$arg" in
        -psn_*) ;;
        *) ARGS+=("$arg") ;;
    esac
done
mkdir -p "$TARGET_DIR/logs" "$TARGET_DIR/data/logs" 2>/dev/null || true
exec {exec_cmd} "${{ARGS[@]}}" >> "$TARGET_DIR/logs/macos_app_launch.log" 2>&1
"""
    changed = False
    if not plist_path.is_file() or plist_path.read_text(encoding="utf-8", errors="ignore") != plist_xml:
        plist_path.write_text(plist_xml, encoding="utf-8")
        changed = True
    if not exec_path.is_file() or exec_path.read_text(encoding="utf-8", errors="ignore") != script_txt:
        exec_path.write_text(script_txt, encoding="utf-8")
        changed = True
    try:
        os.chmod(exec_path, os.stat(exec_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass
    if changed and sys.platform == "darwin":
        try:
            subprocess.run(["xattr", "-cr", str(app_path)], capture_output=True, timeout=5, check=False)
            subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_path)], capture_output=True, timeout=10, check=False)
        except Exception:
            pass
    return app_path


def ensure_shortcuts(root: Path | None = None, *, force: bool = False, quiet: bool = False) -> list[Path]:
    """创建桌面与开始菜单快捷方式（Windows 生成 .lnk，macOS 生成 .app 应用包）。"""
    from . import resolve_python_exe

    base = Path(root).resolve() if root else LTX_ROOT
    stamp = base / "data" / "desktop_shortcut.created"
    desk = desktop_dir()

    if sys.platform == "darwin":
        apps_dir = Path.home() / "Applications"
        main_app = desk / f"{APP_NAME}.app"
        if not force and stamp.is_file() and main_app.is_dir():
            return [main_app]
        py_exe = resolve_python_exe(gui=True, root=base)
        launcher_sh = base / "启动片语工作室.command"
        studio_cmd = f'/bin/bash "{launcher_sh}"' if launcher_sh.is_file() else f'"{py_exe}" "{base / "studio_launcher.py"}"'
        created_mac: list[Path] = []
        mac_targets = [
            (desk / f"{APP_NAME}.app", APP_NAME, "com.cutora.ltxstudio", studio_cmd, base),
            (apps_dir / f"{APP_NAME}.app", APP_NAME, "com.cutora.ltxstudio", studio_cmd, base),
            (apps_dir / "片语剪辑台 CUTORA.app", "片语剪辑台 CUTORA", "com.cutora.editor", f'"{py_exe}" -m cutora', base / "modules" / "cutora_editor"),
            (apps_dir / "Orante 2.0 制片台.app", "Orante 2.0 制片台", "com.cutora.orante", f'"{py_exe}" -m orante', base / "modules" / "orante_desk"),
        ]
        for app_p, name_s, bid, cmd_s, wdir in mac_targets:
            try:
                write_macos_app_bundle(app_p, name_s, bid, cmd_s, wdir)
                created_mac.append(app_p)
            except Exception as e:
                if not quiet:
                    print(f"[WARN] 创建 macOS .app 失败 ({app_p}): {e}")
        if created_mac:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text("created\n", encoding="utf-8")
        return created_mac

    if os.name != "nt":
        return []

    menu = start_menu_dir()
    main_lnk = desk / f"{APP_NAME}.lnk"

    if not force and stamp.is_file() and main_lnk.is_file():
        return [main_lnk]

    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    wscript = Path(sysroot) / "System32" / "wscript.exe"
    vbs_path = base / "片语工作室.vbs"
    pyw = base / "runtime" / "venv" / "Scripts" / "pythonw.exe"
    if not pyw.is_file():
        pyw = base / "runtime" / "python" / "pythonw.exe"
    target = pyw if pyw.is_file() else wscript
    args = f'"{base / "studio_launcher.py"}"' if target == pyw else f'"{vbs_path}"'

    ico_studio = base / "resources" / "brand" / "app.ico"
    ico_cutora = base / "resources" / "brand" / "cutora.ico"
    ico_orante = base / "resources" / "brand" / "orante.ico"

    items = [
        {
            "path": desk / f"{APP_NAME}.lnk",
            "target": target,
            "args": args,
            "workdir": base,
            "icon": ico_studio,
            "desc": DESCRIPTION,
        },
        {
            "path": menu / f"{APP_NAME}.lnk",
            "target": target,
            "args": args,
            "workdir": base,
            "icon": ico_studio,
            "desc": DESCRIPTION,
        },
        {
            "path": menu / "片语剪辑台 (CUTORA).lnk",
            "target": pyw,
            "args": "-m cutora",
            "workdir": base / "modules" / "cutora_editor",
            "icon": ico_cutora if ico_cutora.is_file() else ico_studio,
            "desc": "直接打开片语剪辑台 (CUTORA)",
        },
        {
            "path": menu / "Orante 2.0 制片台.lnk",
            "target": pyw,
            "args": "-m orante",
            "workdir": base / "modules" / "orante_desk",
            "icon": ico_orante if ico_orante.is_file() else ico_studio,
            "desc": "直接打开 Orante 2.0 批量制片台",
        },
    ]

    created: list[Path] = []
    for it in items:
        try:
            write_lnk(it)
            created.append(Path(it["path"]))
        except Exception as e:
            if not quiet:
                print(f"[WARN] 创建快捷方式失败 ({it['path']}): {e}")

    if created:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("created\n", encoding="utf-8")
    return created


if __name__ == "__main__":
    made = ensure_shortcuts(force=True)
    for p in made:
        print(f"[OK] Shortcut created: {p}")
