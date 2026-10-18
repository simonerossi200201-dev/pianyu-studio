# -*- coding: utf-8 -*-
"""启动：建库 → 起 uvicorn（后台线程）→ pywebview 窗口（没有就开浏览器）。

    python -m orante            默认
    python -m orante --browser  强制浏览器模式
    python -m orante --port 9401
"""
from __future__ import annotations

import argparse
import atexit
import faulthandler
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

from . import paths, db, config

_window = None
APP_USER_MODEL_ID = "Orante.Studio"

# /api/state 里用来认「这是 Orante」的键。少一个就当别人占着端口。
ORANTE_STATE_KEYS = (
    "ok", "version", "accounts", "tasks", "settings", "jobs", "root", "flow_models",
)
BOOT_TIMEOUT = 20.0
BOOT_POLL = 0.2


def _port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def fetch_json(url: str, timeout: float = 1.0):
    """GET JSON。连不上 / 非 200 / 不是 JSON 都返回 None，不抛。"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    except Exception:  # noqa: BLE001
        return None


def is_orante_state(data) -> bool:
    """返回的是不是 Orante：/api/ping 的 {app: "orante"}，或者老的 /api/state 那一套键。

    2026-10-05 起 /api/state 要会话 Cookie（本机网页不能随便读账号和设置），启动器拿不到 Cookie，
    所以启动器和多开探测改问不带数据的 /api/ping。"""
    if not isinstance(data, dict):
        return False
    if data.get("ok") and data.get("app") == "orante":
        return True
    return all(k in data for k in ORANTE_STATE_KEYS)


PORT_SPAN = 20  # 从 ui.port 起连试 20 个，默认 9400..9419


class PortBusy(Exception):
    """用户指定的端口被占，不许自作主张换。"""

    def __init__(self, message: str, port: int = 0, kind: str = "orante"):
        super().__init__(message)
        self.message = message
        self.port = int(port or 0)
        self.kind = kind


class PortFull(Exception):
    """20 个窗口都开满了。"""

    def __init__(self, message: str = "已经开了 20 个 Orante 窗口，先关掉几个"):
        super().__init__(message)
        self.message = message


def pick_port(prefer=None, classify=None, base=None) -> int:
    """选一个空端口。

    prefer 有值 → 就用它；被占（Orante 或别人）都报错，不换。
    prefer 空 → 从 ui.port（默认 9400）起试 20 个，第一个 classify==free 的。
    20 个都满 → PortFull。
    """
    classify = classify or classify_port
    if prefer is not None:
        port = int(prefer)
        kind = classify(port)
        if kind == "free":
            return port
        if kind == "orante":
            raise PortBusy(f"端口 {port} 上已经有一个 Orante 窗口在跑",
                           port=port, kind="orante")
        raise PortBusy(f"端口 {port} 被别的程序占用了，不是 Orante",
                       port=port, kind="other")
    start = int(base if base is not None else (config.get("ui.port") or 9400))
    for i in range(PORT_SPAN):
        p = start + i
        if classify(p) == "free":
            return p
    raise PortFull("已经开了 20 个 Orante 窗口，先关掉几个")


def spawn_new_window() -> None:
    """顶栏「＋ 新窗口」：另起一个进程，自己挑端口。立刻返回。"""
    kw = {
        "args": [sys.executable, "-m", "orante", "--new"],
        "cwd": str(paths.ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kw["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        kw["close_fds"] = False
    else:
        kw["start_new_session"] = True
        kw["close_fds"] = True
    subprocess.Popen(**kw)


def classify_port(port: int, retries: int = 5, sleep=time.sleep, fetch=None,
                  linger: float = 0.0) -> str:
    """探端口：free / orante / other。

    能 bind → free。bind 不上再 GET /api/state：键对得上就是已经在跑的 Orante，
    否则是别的程序占着。retries 是为了「刚 bind 上、HTTP 还没起来」那一小会儿。
    """
    if _port_free(port):
        return "free"
    fetch = fetch or fetch_json
    url = f"http://127.0.0.1:{port}/api/ping"
    for i in range(max(1, retries)):
        data = fetch(url)
        if is_orante_state(data):
            return "orante"
        if i + 1 < retries:
            sleep(0.2)
    # 0922：刚关掉的 Orante 进程可能还在收尾（后台线程 / 浏览器连接没退干净），
    # 端口还占着但已经不回 /api/state。再等它最多 linger 秒，放掉了就算空闲，别急着报"别的程序"。
    for _ in range(int(max(0.0, linger) / 0.5)):
        sleep(0.5)
        if _port_free(port):
            return "free"
        if is_orante_state(fetch(url)):
            return "orante"
    return "other"


def wait_api_ready(api_url: str, timeout: float = BOOT_TIMEOUT,
                   fetch=None, sleep=time.sleep, clock=time.time,
                   interval: float = BOOT_POLL) -> bool:
    """轮询 /api/state 直到 200 且键对得上，或超时。"""
    fetch = fetch or fetch_json
    deadline = clock() + timeout
    while clock() < deadline:
        if is_orante_state(fetch(api_url)):
            return True
        remain = deadline - clock()
        if remain <= 0:
            break
        sleep(min(interval, remain))
    return False


def _window_load_html(window, html: str) -> None:
    if hasattr(window, "load_html"):
        window.load_html(html)
        return
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    p = paths.LOGS / "boot-error.html"
    p.write_text(html, encoding="utf-8")
    window.load_url(p.as_uri())


def load_when_ready(window, url: str, timeout: float = BOOT_TIMEOUT,
                    fetch=None, sleep=time.sleep, clock=time.time) -> bool:
    """先等 /api/state 200，再 load_url；超时就把窗口换成内置错误页。

    和前端 bootstrap() 是两道独立保险：这边保证「后端没起来就别加载页面」。
    """
    api = url.rstrip("/") + "/api/ping"
    print(f"等待后端就绪 {api}（最多 {int(timeout)}s）", flush=True)
    ok = wait_api_ready(api, timeout=timeout, fetch=fetch, sleep=sleep, clock=clock)
    if ok:
        print(f"后端就绪，load_url {url}", flush=True)
        window.load_url(url)
        return True
    print("等待后端超时，窗口改走错误页", flush=True)
    _window_load_html(window, boot_error_html(url, error="等待后端超时"))
    return False


def bring_existing_orante_window() -> bool:
    """把已经在跑的原生 Orante 独立桌面窗口拉到前台。找不到就返回 False（调用方直接拉起独立 pywebview 窗口）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        found: list[int] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @WNDENUMPROC
        def _cb(hwnd, _lp):
            proc = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
            if proc.value == pid:
                return True
            if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            cls_name = (cls_buf.value or "").lower()
            # 排除浏览器窗口（Chrome / Edge / Firefox），只认原生 pywebview 桌面窗口
            if "chrome_widgetwin" in cls_name or "mozillawindowclass" in cls_name:
                return True
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value or ""
            if "Orante" in title:
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(_cb, 0)
        if not found:
            return False
        hwnd = found[0]
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"拉现有窗口到前台失败：{e}", flush=True)
        return False


def open_logs_folder() -> None:
    """打开 data/logs。错误页按钮和 /api/system/open-logs 共用。"""
    p = paths.LOGS
    p.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(p)])  # noqa: S603


class JsBridge:
    """pywebview 前端调：后端没起来时错误页也能打开日志目录。"""

    def open_logs(self) -> bool:
        try:
            open_logs_folder()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"打开日志文件夹失败：{e}", flush=True)
            return False


def _boot_css() -> str:
    return (
        "html,body{height:100%;margin:0;background:#121110;color:#EFEAE0;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif}"
        ".boot-mask{position:fixed;inset:0;display:flex;align-items:center;justify-content:center}"
        ".boot-box{width:min(440px,92vw);background:#191714;border:1px solid #3D372E;"
        "border-radius:10px;padding:22px 22px 18px;box-shadow:0 18px 50px rgba(0,0,0,.55)}"
        ".boot-box p{margin:0 0 10px;font-size:15px;line-height:1.55}"
        ".boot-err{color:#8A8274;font-size:13px;word-break:break-all}"
        ".boot-acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}"
        ".btn{padding:7px 13px;border-radius:6px;background:#2A261F;color:#EFEAE0;"
        "border:1px solid #3D372E;cursor:pointer;font:inherit}"
        ".btn.primary{background:#D9A34F;color:#1b1610;border-color:transparent;font-weight:600}"
    )


def boot_connecting_html() -> str:
    """窗口先画这个，等 /api/state 200 再 load_url 正式页面。"""
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>Orante</title><style>{_boot_css()}</style></head><body>"
        "<div class=\"boot-mask\"><div class=\"boot-box\">"
        "<p>正在连接后端…</p>"
        "</div></div></body></html>"
    )


def boot_error_html(url: str, error: str = "", logs_dir: str | None = None) -> str:
    """内置错误页，文案和前端 A 节那三个按钮一致。不依赖后端静态文件。"""
    logs = logs_dir or str(paths.LOGS)
    url_js = json.dumps(url)
    err_js = json.dumps(error or "")
    logs_js = json.dumps(logs)
    err_block = f"<p class=\"boot-err\">{_html_esc(error)}</p>" if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Orante</title>
<style>{_boot_css()}</style>
</head>
<body>
<div class="boot-mask">
  <div class="boot-box">
    <p>连不上后端 {_html_esc(url.rstrip('/'))}</p>
    {err_block}
    <div class="boot-acts">
      <button class="btn primary" type="button" onclick="bootRetry()">重试</button>
      <button class="btn" type="button" onclick="bootOpenLogs()">打开日志文件夹</button>
      <button class="btn" type="button" onclick="bootCopyDiag()">复制诊断信息</button>
    </div>
  </div>
</div>
<script>
var BOOT_URL = {url_js};
var BOOT_ERR = {err_js};
var BOOT_LOGS = {logs_js};
function bootRetry() {{
  var api = BOOT_URL.replace(/\\/?$/, '/') + 'api/ping';
  fetch(api).then(function(r) {{
    if (!r.ok) throw new Error('not ok');
    return r.json();
  }}).then(function(d) {{
    if (d && d.ok && d.version) location.href = BOOT_URL;
    else location.reload();
  }}).catch(function() {{ location.reload(); }});
}}
function bootOpenLogs() {{
  try {{
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_logs) {{
      window.pywebview.api.open_logs();
      return;
    }}
  }} catch (e) {{}}
  bootCopy(BOOT_LOGS);
}}
function bootCopyDiag() {{
  var text = [location.href, BOOT_ERR, navigator.userAgent, new Date().toISOString()].join('\\n');
  bootCopy(text);
}}
function bootCopy(text) {{
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).catch(function(){{ bootCopyTa(text); }});
  }} else bootCopyTa(text);
}}
function bootCopyTa(text) {{
  var ta = document.createElement('textarea');
  ta.value = text; document.body.appendChild(ta); ta.select();
  try {{ document.execCommand('copy'); }} catch (e) {{}}
  ta.remove();
}}
</script>
</body>
</html>
"""


def _html_esc(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def show_boot_error_window(url: str, error: str = "", browser: bool = False) -> int:
    """端口被占 / 后端起不来：窗口里必须有字，不能只写进日志。"""
    html = boot_error_html(url, error=error)
    print(error or f"连不上后端 {url}", flush=True)
    if browser:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        p = paths.LOGS / "boot-error.html"
        p.write_text(html, encoding="utf-8")
        webbrowser.open(p.as_uri())
        return 1
    try:
        import webview
        global _window
        _window = webview.create_window(
            "Orante 制片台", html=html, width=720, height=420, min_size=(480, 320),
            js_api=JsBridge(),
        )
        _bind_win_icon(_window)
        webview.start()
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"错误窗口也打不开：{e}", flush=True)
        traceback.print_exc()
        try:
            paths.LOGS.mkdir(parents=True, exist_ok=True)
            p = paths.LOGS / "boot-error.html"
            p.write_text(html, encoding="utf-8")
            webbrowser.open(p.as_uri())
        except Exception:
            pass
        return 1


def _set_app_user_model_id() -> None:
    """Windows：进程一启动就登记 AppUserModelID，任务栏按此分组、认快捷方式图标。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as e:  # noqa: BLE001
        print(f"AppUserModelID 未设置：{e}", flush=True)


def _install_crash_hooks(logf) -> None:
    """pythonw 没有控制台：未捕获异常和 C 层崩溃都要进 orante.log。"""
    if logf is not None:
        try:
            faulthandler.enable(file=logf, all_threads=True)
        except Exception:  # noqa: BLE001
            try:
                faulthandler.enable(all_threads=True)
            except Exception:
                pass
    else:
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            pass

    def _excepthook(typ, val, tb):
        try:
            traceback.print_exception(typ, val, tb)
        except Exception:
            pass

    sys.excepthook = _excepthook
    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            try:
                print(
                    f"线程异常 {args.thread}: {args.exc_type.__name__}: {args.exc_value}",
                    flush=True,
                )
                traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
            except Exception:
                pass
        threading.excepthook = _thread_hook


def _hook_stdio() -> None:
    """pythonw 下 sys.stdout 为 None；print 同时落到 data/logs/orante.log，不要崩。"""
    try:
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        logf = open(paths.LOGS / "orante.log", "a", encoding="utf-8", buffering=1, errors="replace")
        try:
            logf.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 ====\n")
            logf.flush()
        except Exception:
            pass
    except Exception:  # noqa: BLE001
        logf = None
    _install_crash_hooks(logf)

    class _Tee:
        def __init__(self, stream, extra):
            self._s = stream
            self._e = extra

        def write(self, data):
            if self._s is not None:
                try:
                    self._s.write(data)
                    self._s.flush()
                except Exception:
                    pass
            if self._e is not None:
                try:
                    self._e.write(data)
                    self._e.flush()
                except Exception:
                    pass

        def flush(self):
            for x in (self._s, self._e):
                if x is None:
                    continue
                try:
                    x.flush()
                except Exception:
                    pass

        def isatty(self):
            return False

        encoding = "utf-8"
        errors = "replace"

        def fileno(self):
            for x in (self._s, self._e):
                if x is None or not hasattr(x, "fileno"):
                    continue
                try:
                    return x.fileno()
                except Exception:
                    continue
            raise OSError("no fileno")

    sys.stdout = _Tee(getattr(sys, "stdout", None), logf)
    sys.stderr = _Tee(getattr(sys, "stderr", None), logf)


def pick_folder(title: str | None = None) -> str | None:
    """本地文件夹选择框：优先 pywebview，其次 tkinter，都没有就 None（前端退回手填）。"""
    title = title or "Orante：这批数据存到哪？"
    if _window is not None:
        try:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if kind is None:
                kind = webview.FOLDER_DIALOG
            res = _window.create_file_dialog(kind)
            if isinstance(res, (list, tuple)):
                res = res[0] if res else None
            return str(res) if res else None
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        d = filedialog.askdirectory(title=title)
        root.destroy()
        return d or None
    except Exception:
        return None


def _ft_webview(filetypes: list) -> tuple:
    """tkinter 的 [('Orante 预设','*.orpreset')] → pywebview 的 '名 (*.ext)|*.ext'。"""
    out = []
    for name, pat in filetypes or []:
        glob = pat.replace(" ", ";")
        out.append(f"{name} ({pat})|{glob}")
    return tuple(out or ("全部 (*.*)|*.*",))


def pick_file(title: str | None = None, filetypes: list | None = None) -> str | None:
    """本地文件打开框。默认仍是查漏补缺的文案清单。"""
    title = title or "Orante：选缺失文案清单"
    filetypes = filetypes or [("文本", "*.txt *.md"), ("全部", "*.*")]
    if _window is not None:
        try:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
            if kind is None:
                kind = webview.OPEN_DIALOG
            res = _window.create_file_dialog(kind, file_types=_ft_webview(filetypes))
            if isinstance(res, (list, tuple)):
                res = res[0] if res else None
            return str(res) if res else None
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askopenfilename(title=title, filetypes=filetypes)
        root.destroy()
        return p or None
    except Exception:
        return None


def pick_files(title: str | None = None, filetypes: list | None = None) -> list[str]:
    """多选本地文件（导入工程：一次选多个 .orproj.json）。"""
    title = title or "Orante：导入工程"
    filetypes = filetypes or [("Orante 工程", "*.orproj.json"), ("JSON", "*.json"), ("全部", "*.*")]
    if _window is not None:
        try:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
            if kind is None:
                kind = webview.OPEN_DIALOG
            res = _window.create_file_dialog(
                kind, allow_multiple=True, file_types=_ft_webview(filetypes))
            if not res:
                return []
            if isinstance(res, (list, tuple)):
                return [str(x) for x in res if x]
            return [str(res)]
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askopenfilenames(title=title, filetypes=filetypes)
        root.destroy()
        return [str(x) for x in p] if p else []
    except Exception:
        return []


def pick_save(title: str | None = None, filename: str = "",
              directory: str = "", filetypes: list | None = None) -> str | None:
    """本地文件保存框（导出预设）。"""
    title = title or "Orante：导出预设"
    filetypes = filetypes or [("Orante 预设", "*.orpreset"), ("全部", "*.*")]
    if _window is not None:
        try:
            import webview
            kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
            if kind is None:
                kind = webview.SAVE_DIALOG
            kw = {"file_types": _ft_webview(filetypes)}
            if filename:
                kw["save_filename"] = filename
            if directory:
                kw["directory"] = directory
            res = _window.create_file_dialog(kind, **kw)
            if isinstance(res, (list, tuple)):
                res = res[0] if res else None
            return str(res) if res else None
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.asksavesasfilename(
            title=title, initialfile=filename or "",
            initialdir=directory or "", filetypes=filetypes,
            defaultextension=".orpreset")
        root.destroy()
        return p or None
    except Exception:
        return None


def serve(port: int) -> None:
    """后台线程跑 uvicorn。任何异常都打进 orante.log，pythonw 下不能吞。"""
    try:
        import uvicorn
        from . import server, jobs
        print(f"正在加载服务（绑定 127.0.0.1:{port}）…", flush=True)
        paths.add_legacy_to_sys_path()
        jobs.load_all_services()
        from .scheduler import start_ticker
        start_ticker()
        print(f"uvicorn 启动 127.0.0.1:{port}", flush=True)
        uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")
        print("uvicorn 已退出", flush=True)
    except Exception:
        traceback.print_exc()
        raise


def _hwnd_from_native(window) -> int:
    native = getattr(window, "native", None)
    if isinstance(native, int) and native:
        return native
    if native is None:
        return 0
    for name in ("Handle", "handle", "Hwnd", "hwnd"):
        v = getattr(native, name, None)
        if v is not None:
            for conv in ("ToInt64", "ToInt32"):
                fn = getattr(v, conv, None)
                if callable(fn):
                    try:
                        n = int(fn())
                        if n:
                            return n
                    except Exception:
                        pass
            try:
                n = int(v)
                if n:
                    return n
            except (TypeError, ValueError):
                pass
    return 0


def _enum_orante_hwnd() -> int:
    """EnumWindows：当前进程、标题含 Orante 的顶层窗口（兼容父进程传了 SW_HIDE 的情况）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    found: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def _cb(hwnd, _lp):
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if "Orante" in (buf.value or ""):
            found.append(int(hwnd))
            return False
        return True

    # 回调必须在 EnumWindows 期间保持引用，否则会被 GC
    user32.EnumWindows(_cb, 0)
    return found[0] if found else 0


def _apply_win_icons(hwnd: int, ico: str) -> bool:
    """LoadImageW 分别加载大小图标，WM_SETICON 贴到窗口。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040
    WM_SETICON = 0x0080
    ICON_SMALL, ICON_BIG = 0, 1
    SM_CXICON, SM_CYICON = 11, 12
    SM_CXSMICON, SM_CYSMICON = 49, 50

    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
    ]

    flags = LR_LOADFROMFILE | LR_DEFAULTSIZE
    cx_big = user32.GetSystemMetrics(SM_CXICON)
    cy_big = user32.GetSystemMetrics(SM_CYICON)
    cx_sm = user32.GetSystemMetrics(SM_CXSMICON)
    cy_sm = user32.GetSystemMetrics(SM_CYSMICON)

    h_big = user32.LoadImageW(None, ico, IMAGE_ICON, cx_big, cy_big, flags)
    h_sm = user32.LoadImageW(None, ico, IMAGE_ICON, cx_sm, cy_sm, flags)
    if not h_big and not h_sm:
        return False
    if h_big:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
    if h_sm:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_sm)
    elif h_big:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_big)
    return True


def _bind_closing(window) -> None:
    """关窗口前先让前端 flush。最多等 2 秒，超时也放行，别把用户卡死。

    不在 closing 回调里同步 evaluate_js：WebView 的 JS 回程要走 UI 消息循环，
    回调里死等会把关窗卡死。先拦下这次关闭，后台 flush，完了再 destroy。
    """
    state = {"ok": False, "busy": False}

    def on_closing():
        if state["ok"]:
            return True
        if state["busy"]:
            return False
        state["busy"] = True

        def worker():
            try:
                window.evaluate_js(
                    "(function(){try{if(window.__flushAll)window.__flushAll();return true;}"
                    "catch(e){return false;}})()"
                )
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        done = window.evaluate_js("!!window.__flushDone")
                    except Exception:
                        break
                    if done is True or str(done).lower() in ("true", "1"):
                        break
                    time.sleep(0.05)
            except Exception as e:  # noqa: BLE001
                print(f"关闭前 flush：{e}", flush=True)
            finally:
                state["ok"] = True
                try:
                    window.destroy()
                except Exception as e:  # noqa: BLE001
                    print(f"flush 后关窗：{e}", flush=True)

        threading.Thread(target=worker, daemon=True).start()
        return False

    try:
        ev = getattr(window, "events", None)
        closing = getattr(ev, "closing", None) if ev is not None else None
        if closing is not None:
            closing += on_closing
        elif hasattr(window, "closing"):
            window.closing += on_closing
        else:
            print("窗口 closing 事件挂不上，关窗前无法 flush", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"窗口 closing 未挂上：{e}", flush=True)


def _bind_win_icon(window) -> None:
    """Windows：窗口出现后强制显式显示（防父进程 SW_HIDE）并贴上 orante.ico。"""
    if os.name != "nt":
        return

    ico = paths.ROOT / "ui" / "assets" / "orante.ico"
    ico_s = str(ico) if ico.is_file() else ""

    def apply(_=None):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = _hwnd_from_native(window) or _enum_orante_hwnd()
            if not hwnd:
                print("窗口图标：找不到 Orante 窗口句柄，已跳过", flush=True)
                return
            if not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            if ico_s and not _apply_win_icons(hwnd, ico_s):
                print("窗口图标：LoadImageW 失败，已跳过", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"窗口图标未贴上：{e}", flush=True)

    try:
        ev = getattr(window, "events", None)
        shown = getattr(ev, "shown", None) if ev is not None else None
        if shown is not None:
            shown += apply
        else:
            apply()
        # shown 时标题有时还没写上，再补一次
        threading.Timer(0.4, apply).start()
    except Exception as e:  # noqa: BLE001
        print(f"窗口图标未贴上：{e}", flush=True)


def _bind_float_icon(window, port: int) -> None:
    """规格 89：把本窗口登记进窗口表，并按需拉起悬浮图标进程。

    登记的是窗口句柄——图标进程靠它判断「是不是所有窗口都最小化了」。
    心跳 8 秒一次；进程被强杀也不会留下假记录（心跳过期就不算数）。
    """
    if os.name != "nt":
        return
    from . import winlist, floaticon, instance

    state = {"hwnd": 0}

    def note(_=None):
        try:
            hwnd = _hwnd_from_native(window) or _enum_orante_hwnd()
            if hwnd:
                state["hwnd"] = int(hwnd)
            winlist.write(port, hwnd=state["hwnd"], title=instance.title())
        except Exception as e:  # noqa: BLE001
            print(f"窗口登记：{e}", flush=True)

    def beat():
        """心跳 + 看家：图标进程万一挂了（或者被用户结束了），下一拍把它拉回来。"""
        last_spawn = 0.0
        while True:
            time.sleep(winlist.BEAT_EVERY)
            note()
            try:
                if (time.time() - last_spawn >= 30.0
                        and bool(config.get("ui.float_icon", True))
                        and not floaticon.running()):
                    if floaticon.spawn():
                        last_spawn = time.time()
            except Exception:  # noqa: BLE001
                pass

    try:
        ev = getattr(window, "events", None)
        shown = getattr(ev, "shown", None) if ev is not None else None
        if shown is not None:
            shown += note
        else:
            note()
        threading.Timer(0.6, note).start()
        threading.Thread(target=beat, daemon=True).start()
        atexit.register(lambda: winlist.remove(port))
        # 窗口先露脸，图标进程晚一点再起，别抢启动那几百毫秒
        threading.Timer(1.5, floaticon.spawn).start()
    except Exception as e:  # noqa: BLE001
        print(f"悬浮图标没接上：{e}", flush=True)


def open_ui(url: str, port: int, browser: bool = False) -> int:
    """窗口模式：先画「正在连接」，等 /api/state 200 再 load_url。浏览器模式同理。"""
    if browser:
        api = url.rstrip("/") + "/api/ping"
        print("等待后端 /api/ping …", flush=True)
        if wait_api_ready(api):
            webbrowser.open(url)
            print("关掉这个窗口即停止服务。", flush=True)
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return 0
            return 0
        return show_boot_error_window(url, error="等待后端超时", browser=True)

    try:
        import webview  # pywebview
        global _window
        from . import instance
        _window = webview.create_window(
            instance.title(), html=boot_connecting_html(),
            width=1500, height=940, min_size=(1100, 700),
            js_api=JsBridge(),
        )
        _bind_win_icon(_window)
        _bind_closing(_window)
        _bind_float_icon(_window, port)

        def _go():
            # webview.start(func) 会在 GUI 循环起来之后、独立线程里调 func，
            # 这里 load_url 才是官方支持的；自己另开线程有的后端会静默丢掉。
            try:
                load_when_ready(_window, url)
            except Exception:
                traceback.print_exc()

        webview.start(_go)
        return 0
    except ImportError:
        print("（没装 pywebview，改用浏览器打开；pip install pywebview 可得到独立窗口）", flush=True)
        return open_ui(url, port, browser=True)
    except Exception as e:  # noqa: BLE001
        print(f"（窗口模式失败 {e}，改用浏览器）", flush=True)
        traceback.print_exc()
        return open_ui(url, port, browser=True)


def main(argv=None) -> int:
    """入口。启动期任何异常都进 orante.log，pythonw 下也落盘。"""
    try:
        return _run(argv)
    except Exception:
        traceback.print_exc()
        return 1


def _run(argv=None) -> int:
    # pythonw 下 stdout 为 None，先接上日志再登记 ID（都在创建窗口之前）
    _hook_stdio()
    _set_app_user_model_id()

    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--new", action="store_true",
                    help="新开一个窗口（自己挑空端口），不要聚焦已有的")
    ap.add_argument("--autostart-task", type=str, default=None,
                    help="启动后自动开始生成指定任务（例如 T0127）")
    a = ap.parse_args(argv)

    paths.ensure_dirs()
    db.init()
    try:
        # 老版本只有一条 settings['gemini.api_key']；接口名册是空的就把它搬进来，
        # 升级完不用重填，行为跟以前一样（一条 key、慢慢跑）。
        from .services import aikeys
        aikeys.migrate_from_settings()
    except Exception:
        pass

    from . import instance
    if not a.browser and config.get("ui.open_mode") != "window":
        try:
            config.set("ui.open_mode", "window")
        except Exception:
            pass
    browser = bool(a.browser)

    if a.port is not None:
        # 用户点名端口：被占就报错退出，不许自作主张换
        kind = classify_port(a.port, linger=15.0)
        if kind == "orante":
            if bring_existing_orante_window():
                return 0
            instance.configure(a.port)
            return open_ui(f"http://127.0.0.1:{a.port}/", a.port, browser=browser)
        if kind == "other":
            msg = f"端口 {a.port} 被别的程序占用了，不是 Orante"
            print(msg, flush=True)
            return show_boot_error_window(
                f"http://127.0.0.1:{a.port}/", error=msg, browser=browser)
        port = a.port
    elif a.new:
        try:
            port = pick_port(None)
        except PortFull as e:
            print(e.message, flush=True)
            return show_boot_error_window(
                "http://127.0.0.1:9400/", error=e.message, browser=browser)
    else:
        # 双击快捷方式 / bat：已有原生独立窗口就聚焦；若只有后台服务无可见窗口，则直接拉起原生 pywebview 独立窗口（绝不用浏览器打开）
        port = int(config.get("ui.port") or 9400)
        kind = classify_port(port, linger=15.0)
        if kind == "orante":
            print("Orante 后端已在运行，优先聚焦或拉起独立桌面窗口", flush=True)
            if bring_existing_orante_window():
                return 0
            instance.configure(port)
            return open_ui(f"http://127.0.0.1:{port}/", port, browser=browser)
        if kind == "other":
            msg = f"端口 {port} 被别的程序占用了，不是 Orante"
            print(msg, flush=True)
            return show_boot_error_window(
                f"http://127.0.0.1:{port}/", error=msg, browser=browser)

    instance.configure(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Orante {__import__('orante').__version__}  →  {url}", flush=True)

    try:
        # 可选：把做完超过 N 天的 finished 任务导出并从库里移出（默认关）
        # 多开：只有真正起服务的窗口跑；5 分钟内第二个会跳过
        from .services import project as projectmod
        from .scheduler import reap_orphans
        reap_orphans()
        projectmod.auto_archive_on_startup()
    except Exception as e:  # noqa: BLE001
        print(f"自动归档跳过：{e}", flush=True)

    threading.Thread(target=serve, args=(port,), daemon=True).start()
    print("uvicorn 线程已启动", flush=True)

    if a.autostart_task:
        def _auto_start_task(tid: str):
            time.sleep(2.5)
            try:
                from . import jobs as jobmod
                jobmod.start("generate", tid, redo=True)
                print(f"已自动触发任务 {tid} 开始生成", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"自动触发任务 {tid} 失败：{exc}", flush=True)
        threading.Thread(target=_auto_start_task, args=(a.autostart_task,), daemon=True).start()

    return open_ui(url, port, browser=browser)


if __name__ == "__main__":
    sys.exit(main())
