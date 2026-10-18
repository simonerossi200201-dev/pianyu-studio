# -*- coding: utf-8 -*-
r"""桌面 / 开始菜单快捷方式：纯 Python 建，不用 powershell。

为什么不用 powershell：65 号把 `-ExecutionPolicy Bypass` 去掉之后，剩下的
`powershell -NoProfile -Command "... New-Object -ComObject WScript.Shell ..."`
本身还是杀毒软件最敏感的画像之一（一长串内联脚本 + 建 COM 对象 + 写桌面文件）。
真去干这件事的是 Windows 自己的 IShellLink，Python 用 ctypes 直接调就行，
不需要 pywin32、不需要任何编译产物。

快捷方式指向 `.venv\Scripts\pythonw.exe -m orante`——不指向 bat、更不指向 exe。
日常启动因此完全不碰脚本文件。

    python -m orante.shortcut            建桌面 + 开始菜单
    python -m orante.shortcut --quiet    安静模式（首次安装时调）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Orante"
NEW_WINDOW_NAME = "Orante（新窗口）"
DESCRIPTION = "Orante 制片台"


def pythonw_path(root: Path) -> Path:
    """优先用项目自己的 .venv；没有就用当前解释器旁边的 pythonw。"""
    venv = root / ".venv" / "Scripts" / "pythonw.exe"
    if venv.is_file():
        return venv
    here = Path(sys.executable)
    pyw = here.with_name("pythonw.exe")
    return pyw if pyw.is_file() else here


def desktop_dir() -> Path:
    """桌面。OneDrive 接管过桌面的机器上 USERPROFILE\\Desktop 是错的，先问系统。"""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(260)
        # SHGetFolderPathW(NULL, CSIDL_DESKTOPDIRECTORY=0x10, NULL, SHGFP_TYPE_CURRENT=0, buf)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            p = Path(buf.value)
            if p.is_dir():
                return p
    except Exception:  # noqa: BLE001
        pass
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for cand in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if cand.is_dir():
            return cand
    return home


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


def icon_path(root: Path) -> Path:
    """桌面图标：优先用 "Orante me.png" 转出来的那张（tools/build_me_icon.py 生成）。

    .ico 是图片，杀软不碰；exe 才是被误杀的那个，已经从项目里删掉了。
    """
    me = root / "ui" / "assets" / "orante-me.ico"
    return me if me.is_file() else root / "ui" / "assets" / "orante.ico"


def plan(root: Path | None = None, *, desktop: Path | None = None,
         start_menu: Path | None = None) -> list[dict]:
    """要建哪几个快捷方式。不碰系统，测试直接看这张表。"""
    from . import paths
    root = Path(root or paths.ROOT)
    pyw = pythonw_path(root)
    ico = icon_path(root)
    desk = Path(desktop) if desktop else desktop_dir()
    menu = Path(start_menu) if start_menu else start_menu_dir()
    out: list[dict] = []
    for folder in (desk, menu):
        out.append({"path": folder / f"{APP_NAME}.lnk", "target": pyw,
                    "args": "-m orante", "icon": ico, "workdir": root,
                    "desc": DESCRIPTION})
        out.append({"path": folder / f"{NEW_WINDOW_NAME}.lnk", "target": pyw,
                    "args": "-m orante --new", "icon": ico, "workdir": root,
                    "desc": DESCRIPTION + "（再开一个窗口）"})
    return out


# ---------------------------------------------------------------- COM
_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"

# IShellLinkW 虚表序号（前三个是 IUnknown）
_SET_DESCRIPTION = 7
_SET_WORKING_DIR = 9
_SET_ARGUMENTS = 11
_SET_SHOW_CMD = 15
_SET_ICON_LOCATION = 17
_SET_PATH = 20
# IPersistFile
_PERSIST_SAVE = 6


def _guid(s: str):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

    g = GUID()
    ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(g))
    return g


def _vcall(ptr, index: int, *args, argtypes=()):
    """调虚表上的第 index 个方法。HRESULT != 0 就抛。"""
    import ctypes
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    hr = fn(ptr, *args)
    if hr != 0:
        raise OSError(f"COM 调用失败（序号 {index}，HRESULT 0x{hr & 0xFFFFFFFF:08X}）")


def write_lnk(item: dict) -> None:
    """真正落一个 .lnk。只在 Windows 上能跑。"""
    import ctypes

    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass
    ptr = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_SHELLLINK)), None, 1,
                           ctypes.byref(_guid(_IID_ISHELLLINKW)), ctypes.byref(ptr))
    try:
        _vcall(ptr, _SET_PATH, ctypes.c_wchar_p(str(item["target"])),
               argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_ARGUMENTS, ctypes.c_wchar_p(str(item.get("args") or "")),
               argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_WORKING_DIR, ctypes.c_wchar_p(str(item["workdir"])),
               argtypes=(ctypes.c_wchar_p,))
        _vcall(ptr, _SET_DESCRIPTION, ctypes.c_wchar_p(str(item.get("desc") or "")),
               argtypes=(ctypes.c_wchar_p,))
        ico = item.get("icon")
        if ico and Path(ico).is_file():
            _vcall(ptr, _SET_ICON_LOCATION, ctypes.c_wchar_p(str(ico)), 0,
                   argtypes=(ctypes.c_wchar_p, ctypes.c_int))
        _vcall(ptr, _SET_SHOW_CMD, 1, argtypes=(ctypes.c_int,))   # SW_SHOWNORMAL

        persist = ctypes.c_void_p()
        _vcall(ptr, 0, ctypes.byref(_guid(_IID_IPERSISTFILE)), ctypes.byref(persist),
               argtypes=(ctypes.c_void_p, ctypes.c_void_p))       # QueryInterface
        try:
            dest = Path(item["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            _vcall(persist, _PERSIST_SAVE, ctypes.c_wchar_p(str(dest)), 1,
                   argtypes=(ctypes.c_wchar_p, ctypes.c_int))
        finally:
            _vcall_release(persist)
    finally:
        _vcall_release(ptr)


def _vcall_release(ptr) -> None:
    import ctypes
    try:
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        proto(vtbl[2])(ptr)          # IUnknown::Release
    except Exception:  # noqa: BLE001
        pass


def create(root: Path | None = None, *, quiet: bool = False) -> list[Path]:
    """建全部快捷方式，返回建成的那些。单个失败不影响别的。"""
    made: list[Path] = []
    if os.name != "nt":
        return made
    for item in plan(root):
        try:
            write_lnk(item)
            made.append(Path(item["path"]))
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"快捷方式没建成 {item['path'].name}：{e}", flush=True)
    return made


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in args
    if os.name != "nt":
        print("快捷方式只在 Windows 上建。", flush=True)
        return 0
    made = create(quiet=quiet)
    if not made:
        print("一个快捷方式都没建成。", flush=True)
        return 1
    if quiet:
        print("Desktop shortcut created.", flush=True)
    else:
        for p in made:
            print(f"✓ {p}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
