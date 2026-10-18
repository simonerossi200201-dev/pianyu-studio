# -*- coding: utf-8 -*-
"""每个账号一个 Chrome：独立 profile + 独立调试端口。

这是全项目唯一允许出现 platform 分支的三处之一（另两处 paths.py / main.py）。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

from .. import paths
from ..errors import OranteError

# 防冻结：借鉴「其他人做的」扩展 —— Web Lock + 标题心跳，后台标签不被 Chrome 冻住
KEEP_AWAKE_JS = r"""
(() => {
  if (window.__oranteAwake) return 'already';
  window.__oranteAwake = true;
  try { navigator.locks && navigator.locks.request('orante-keep-awake', {mode:'shared'}, () => new Promise(()=>{})); } catch(e) {}
  let tick = 0; const t0 = document.title;
  setInterval(() => { tick++; document.title = t0 + (tick % 2 ? '\u200B' : ''); }, 5000);
  return 'ok';
})()
"""


def chrome_exe() -> str:
    env = os.environ.get("ORANTE_CHROME")
    if env and Path(env).is_file():
        return env
    cands = []
    if paths.IS_WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cands.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif paths.IS_MAC:
        cands.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        cands.append(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            w = shutil.which(n)
            if w:
                cands.append(Path(w))
    for c in cands:
        if c.is_file():
            return str(c)
    raise OranteError("找不到 Google Chrome", "请安装 Chrome，或在设置里指定 chrome 路径（环境变量 ORANTE_CHROME）。")


def port_alive(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port(start: int = 9300, taken: set[int] | None = None) -> int:
    taken = taken or set()
    for p in range(start, start + 400):
        if p in taken or port_alive(p):
            continue
        return p
    raise OranteError("找不到空闲端口")


def cdp_version(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def cdp_pages(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            return [p for p in json.loads(r.read().decode()) if p.get("type") == "page"]
    except Exception:
        return []


def open_tab(port: int, url: str) -> None:
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{url}", method="PUT"), timeout=3).read()
    except Exception:
        pass


def launch(port: int, profile_dir: Path, url: str, wait: float = 40,
           tab_if_alive: bool = True) -> bool:
    """起 Chrome。返回是否是这次新起的。

    tab_if_alive=False：Chrome 已经在跑就什么都不做（引擎自己会开专属标签页，
    不然每连一次就多一个标签，越攒越多）。
    """
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    if port_alive(port):
        if cdp_version(port) is None:
            raise OranteError(f"端口 {port} 被别的程序占着", "换个端口，或关掉占用它的程序。")
        if tab_if_alive:
            open_tab(port, url)
        return False
    args = [chrome_exe(), f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion",
            url]
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if paths.IS_WIN:
        kw["creationflags"] = 0x00000200 | 0x01000000  # NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)
    t0 = time.time()
    while time.time() - t0 < wait:
        if cdp_version(port):
            return True
        time.sleep(0.4)
    raise OranteError(f"Chrome 没有在端口 {port} 起来", "看看是不是同一个 profile 已经被另一个 Chrome 用着。")


class Browser:
    """Playwright 连到某个端口的 Chrome。同步 API，一个账号一个实例。"""

    def __init__(self, port: int):
        self.port = port
        self._pw = None
        self.browser = None
        self.ctx = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}", timeout=20000)
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        return self

    def __exit__(self, *a):
        try:
            if self.browser:
                self.browser.close()      # 只断连接，不关用户的 Chrome
        finally:
            if self._pw:
                self._pw.stop()

    def page_matching(self, host: str, create_url: str | None = None):
        for pg in self.ctx.pages:
            if host in (pg.url or ""):
                return pg
        if create_url:
            pg = self.ctx.new_page()
            pg.goto(create_url, wait_until="domcontentloaded", timeout=60000)
            return pg
        return None

    @staticmethod
    def keep_awake(page) -> None:
        try:
            page.evaluate(KEEP_AWAKE_JS)
        except Exception:
            pass


# ---- Windows DPAPI：Chrome cookie 密钥绑在当前 Windows 用户上 ----
# 平台分支只允许在这个文件（和 paths.py / main.py）。预设导出要先解成明文，
# 换机导入再用这台机器的 DPAPI 重新加密，否则是一堆解不开的乱码。

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_in(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob._keep = buf  # ctypes 指针必须把 buffer 挂住，否则会被回收
    return blob


def dpapi_unprotect(data: bytes) -> bytes:
    """CryptUnprotectData。优先 pywin32，没有就 ctypes。"""
    if not data:
        raise OranteError("Chrome 的加密密钥是空的")
    try:
        import win32crypt  # type: ignore
        out = win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return out[1]
    except ImportError:
        pass
    except Exception as e:
        raise OranteError("解不开 Chrome 的加密密钥", str(e))
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        raise OranteError("这台机器解不开 Chrome 的 cookie",
                          "登录态搬运目前只支持 Windows（需要 DPAPI）。")
    inp = _blob_in(data)
    out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OranteError("解不开 Chrome 的加密密钥",
                          "DPAPI 拒绝了解密。换了 Windows 用户或 cookie 已经坏了。")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData：用这台机器当前 Windows 用户重新包一层。"""
    if not data:
        raise OranteError("不能加密空密钥")
    try:
        import win32crypt  # type: ignore
        out = win32crypt.CryptProtectData(data, None, None, None, None, 0)
        return out
    except ImportError:
        pass
    except Exception as e:
        raise OranteError("没法用这台机器加密 cookie", str(e))
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        raise OranteError("这台机器没法重新加密 cookie",
                          "登录态搬运目前只支持 Windows（需要 DPAPI）。")
    inp = _blob_in(data)
    out = _DATA_BLOB()
    if not crypt32.CryptProtectData(
            ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OranteError("没法用这台机器加密 cookie", "DPAPI 拒绝了加密。")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
