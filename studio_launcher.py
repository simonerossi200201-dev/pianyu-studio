# -*- coding: utf-8 -*-
"""片语工作室 (Cutora LTX Studio v3.0) — 9:16 竖屏海报级总控台 (D:\\0_LTX\\studio_launcher.py)。

设计规范：
1. 9:16 竖屏黄金比例 (540 × 960)，遵循 CUTORA Roma-Gold 古典黑金视觉体系。
2. 完整内嵌四大核心软件与模块的专属高清徽章图标：
   - 片语工作室主徽章 (`resources/brand/app.png`)
   - Orante 2.0 制片台 (`resources/brand/orante_me.png`)
   - 片语 Studio · CUTORA (`resources/brand/cutora_me.png`)
   - 右键表格工具 (`resources/brand/table_tool_me.png`)
   - 本地算力网关 · 7870 (`resources/brand/ltx_gateway_me.png`)
3. 全圆角渐变金边卡片、平滑悬停/按压交互动画 (`QVariantAnimation` + `OutCubic`)、内置字体渲染。
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
from pathlib import Path

LTX_ROOT = Path(__file__).resolve().parent
if str(LTX_ROOT) not in sys.path:
    sys.path.insert(0, str(LTX_ROOT))

from core import resolve_python_exe
from core.portable import rewrite_pyvenv_cfg
from core.shortcut import ensure_shortcuts, ensure_table_tool_registered
from core.hardware_guard import get_hardware_guard
from core.spine_db import get_spine_db, _resolve_workspace_root
from core.launch_state import read_launch_state, write_launch_state
from core.table_sync import (
    call_cutora_api,
    is_cutora_running as _table_sync_is_cutora_running,
    launch_cutora_room,
    launch_cutora_room_detailed,
    prepare_cutora_stitch_handoff,
)
from core.delivery_worker import get_delivery_worker


def _bootstrap_studio_env() -> None:
    """在受保护上下文中修正便携路径、自动静默合并同级模型包、隐藏内部杂乱目录、更新快捷方式与右键表格工具注册。"""
    try:
        write_launch_state("BOOTSTRAP_START", app_name="launcher")
    except Exception:
        pass
    try:
        from core.standalone_bootstrap import hide_internal_root_clutter, try_silent_merge_sibling_model_pack
        try_silent_merge_sibling_model_pack(LTX_ROOT)
        hide_internal_root_clutter(LTX_ROOT)
    except Exception:
        pass
    try:
        rewrite_pyvenv_cfg(LTX_ROOT)
    except Exception:
        pass
    try:
        ensure_table_tool_registered(LTX_ROOT, quiet=True)
    except Exception:
        pass
    try:
        ensure_shortcuts(LTX_ROOT, force=(os.name == "nt"), quiet=True)
    except Exception:
        pass

# ─────────────────────── CUTORA Roma-Gold 色彩令牌 ───────────────────────
BG0 = "#141210"         # 暖玄武岩黑
BG1 = "#1C1815"         # 面板底色
BG2 = "#262019"         # 卡片底色
BG3 = "#332A22"         # 悬停底色
LINE = "#524944"        # 分隔线
BORDER = "#5D5550"      # 基础边框
FG0 = "#EFE7DA"         # 洞石暖白（主标题）
FG1 = "#B3A695"         # 次级石材灰（正文说明）
FG2 = "#92897F"         # 辅助说明文字
ACCENT = "#C8A24A"      # 古典金
ACCENT_HI = "#E0BE68"   # 香槟高光金
ACCENT_DIM = "#8A6E2E"  # 暗古铜金
INFO = "#8FA89B"        # 青铜绿（标语点缀）
OK = "#7D9B6A"          # 橄榄绿（在线状态）
WARN = "#C8913A"        # 赭金

WIN_W = 315
WIN_H = 560


def _python_exe(gui: bool = False) -> str:
    return resolve_python_exe(gui=gui, root=LTX_ROOT)


import time

_CUTORA_LAUNCH_ACCEPTED = False
_MODULE_CLICK_ACCEPTED: set[str] = set()
_CUTORA_PROC: subprocess.Popen | None = None
_CUTORA_SESSION_ID: str = ""
_CUTORA_START_TS: float = 0.0
_MODULE_PROCS: dict[str, subprocess.Popen] = {}
_MODULE_START_TS: dict[str, float] = {}


def is_cutora_running() -> bool:
    """检查系统内是否已有正在运行的 CUTORA 实例（支持 Windows 命名互斥体与 macOS flock）。"""
    return _table_sync_is_cutora_running()


def is_cutora_active() -> bool:
    """判断 CUTORA 是否正在启动中或正在运行中。
    若子进程已经退出（如缺少依赖报错退出），立即解除锁定，绝不死等 8 秒防抖。
    """
    global _CUTORA_LAUNCH_ACCEPTED
    if is_cutora_running():
        return True
    if _CUTORA_PROC is not None:
        if _CUTORA_PROC.poll() is None:
            return True
        # 子进程已退出且未持有全局单实例锁 -> 立即解锁
        _CUTORA_LAUNCH_ACCEPTED = False
        _MODULE_CLICK_ACCEPTED.discard("cutora")
        return False
    if _CUTORA_START_TS > 0.0 and (time.monotonic() - _CUTORA_START_TS) < 4.0:
        return True
    _CUTORA_LAUNCH_ACCEPTED = False
    _MODULE_CLICK_ACCEPTED.discard("cutora")
    return False


def is_module_active(mod_id: str) -> bool:
    """判断模块是否处于启动防抖窗口或进程仍在运行。"""
    if mod_id == "cutora":
        return is_cutora_active()
    proc = _MODULE_PROCS.get(mod_id)
    if proc is not None and proc.poll() is None:
        return True
    ts = _MODULE_START_TS.get(mod_id, 0.0)
    if ts > 0.0 and (time.monotonic() - ts) < 6.0:
        return True
    _MODULE_CLICK_ACCEPTED.discard(mod_id)
    return False


ORANTE_RELAUNCH_COOLDOWN_SEC = 5.0
_ORANTE_PROCS: list[subprocess.Popen] = []


def orante_cooldown_left() -> float:
    """Orante 允许多开：仅在上次启动后的 5 秒内忽略重复点击（防止一次手抖连开）。"""
    ts = _MODULE_START_TS.get("orante", 0.0)
    if ts <= 0.0:
        return 0.0
    return max(0.0, ORANTE_RELAUNCH_COOLDOWN_SEC - (time.monotonic() - ts))


def orante_instance_count() -> int:
    alive = [p for p in _ORANTE_PROCS if p.poll() is None]
    _ORANTE_PROCS[:] = alive
    return len(alive)


def _detached_popen_kwargs() -> dict:
    """在 macOS / POSIX 下将子进程完全脱离当前 .app 会话组，防止关闭总控台后子进程卡住 LaunchServices 重启。"""
    if os.name == "nt":
        return {}
    return {"start_new_session": True, "close_fds": True}


def _has_python_module(mod_name: str) -> bool:
    """检查当前激活的 Python 环境是否已安装指定模块。"""
    import importlib.util
    try:
        if importlib.util.find_spec(mod_name) is not None:
            return True
    except Exception:
        pass
    py = _python_exe(gui=False)
    try:
        r = subprocess.run(
            [py, "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({mod_name!r}) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _spawn_bootstrap_console(app_target: str, extra_env: dict | None = None) -> subprocess.Popen:
    """当用户仅下载了 02 轻量代码包、首次启动 Orante 或 CUTORA 需补齐轻量依赖时，弹出可见终端执行按需引导。"""
    py_console = _python_exe(gui=False)
    bootstrap_py = LTX_ROOT / "core" / "standalone_bootstrap.py"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    cmd = [py_console, str(bootstrap_py), "--app", app_target]
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        return subprocess.Popen(cmd, cwd=str(LTX_ROOT), env=env, creationflags=creationflags)
    return subprocess.Popen(cmd, cwd=str(LTX_ROOT), env=env, **_detached_popen_kwargs())


def launch_orante(start_node: str = "") -> bool:
    """启动 Orante 2.0。不限制多开数量；仅在两次启动间隔小于 5 秒时忽略（防误触只对 CUTORA 做单实例锁定）。"""
    if orante_cooldown_left() > 0.0:
        return False
    _MODULE_START_TS["orante"] = time.monotonic()
    orante_dir = LTX_ROOT / "modules" / "orante_desk"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PATH"] = os.pathsep.join(
        [
            str(LTX_ROOT / "runtime" / "bin"),
            str(LTX_ROOT / "runtime" / "ffmpeg" / "bin"),
            str(LTX_ROOT / "runtime" / "ffmpeg"),
            env.get("PATH", ""),
        ]
    )
    if start_node:
        env["ORANTE_START_NODE"] = start_node

    if not _has_python_module("fastapi") or not _has_python_module("uvicorn"):
        proc = _spawn_bootstrap_console("orante", extra_env={"ORANTE_START_NODE": start_node} if start_node else None)
        _MODULE_PROCS["orante"] = proc
        _ORANTE_PROCS.append(proc)
        get_spine_db().emit_event("launcher.orante_bootstrap_started", "launcher", "orante", {"start_node": start_node})
        return True

    cmd = [_python_exe(gui=True), "-m", "orante"]
    if orante_instance_count() > 0:
        cmd.append("--new")
    log_dir = LTX_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_fh = open(log_dir / "orante_stdout.log", "ab")
    err_fh = open(log_dir / "orante_stderr.log", "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(orante_dir),
            env=env,
            stdout=out_fh,
            stderr=err_fh,
            **_detached_popen_kwargs(),
        )
    finally:
        try:
            out_fh.close()
            err_fh.close()
        except Exception:
            pass
    _MODULE_PROCS["orante"] = proc
    _ORANTE_PROCS.append(proc)
    get_spine_db().emit_event("launcher.orante_started", "launcher", "orante", {"start_node": start_node})
    return True


def launch_cutora(room: str = "") -> bool:
    """启动 CUTORA（保存真实 Popen 句柄与 session_id；仅在收到 MAIN_WINDOW_READY 后才上报 started 事件）。"""
    global _CUTORA_LAUNCH_ACCEPTED, _CUTORA_PROC, _CUTORA_SESSION_ID, _CUTORA_START_TS
    if is_cutora_active():
        _CUTORA_LAUNCH_ACCEPTED = True
        _MODULE_CLICK_ACCEPTED.add("cutora")
        return False
    _CUTORA_LAUNCH_ACCEPTED = True
    _MODULE_CLICK_ACCEPTED.add("cutora")
    _CUTORA_START_TS = time.monotonic()

    if not _has_python_module("PySide6") or not _has_python_module("pandas"):
        _CUTORA_SESSION_ID = f"cutora-{int(time.time() * 1000)}"
        extra = {"CUTORA_LAUNCH_SESSION_ID": _CUTORA_SESSION_ID}
        if room:
            extra["CUTORA_START_ROOM"] = room
        _CUTORA_PROC = _spawn_bootstrap_console("cutora", extra_env=extra)
        get_spine_db().emit_event("launcher.cutora_bootstrap_started", "launcher", "cutora", {"room": room})
        return True

    try:
        if room == "stitch":
            prepare_cutora_stitch_handoff(auto_start=True, auto_ai=True, source="launcher")
        res = launch_cutora_room_detailed(room=room or "preview")
        _CUTORA_PROC = res.get("proc")
        _CUTORA_SESSION_ID = str(res.get("session_id") or "")
        if res.get("already_running"):
            get_spine_db().emit_event("launcher.cutora_focused", "launcher", "cutora", {"room": room})
        return bool(res.get("ok", True))
    except Exception:
        if is_cutora_running():
            return False
        cutora_dir = LTX_ROOT / "modules" / "cutora_editor"
        _CUTORA_SESSION_ID = f"cutora-{int(time.time() * 1000)}"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["CUTORA_RUNTIME"] = str(LTX_ROOT / "runtime")
        env["CUTORA_LAUNCH_SESSION_ID"] = _CUTORA_SESSION_ID
        env["PATH"] = os.pathsep.join(
            [
                str(LTX_ROOT / "runtime" / "bin"),
                str(LTX_ROOT / "runtime" / "ffmpeg" / "bin"),
                str(LTX_ROOT / "runtime" / "ffmpeg"),
                env.get("PATH", ""),
            ]
        )
        args = [_python_exe(gui=True), "-m", "cutora"]
        if room:
            env["CUTORA_START_ROOM"] = room
        _CUTORA_PROC = subprocess.Popen(
            args,
            cwd=str(cutora_dir),
            env=env,
            **_detached_popen_kwargs(),
        )
        return True


def launch_table_tool(target_dir: str | None = None) -> bool:
    """启动右键表格工具（防误触保护，关闭后可再次打开）。"""
    if is_module_active("table"):
        return False
    _MODULE_CLICK_ACCEPTED.add("table")
    _MODULE_START_TS["table"] = time.monotonic()
    script = LTX_ROOT / "modules" / "table_tool" / "app" / "表格工具.py"
    cmd = [_python_exe(gui=True), str(script)]
    if target_dir:
        cmd.append(target_dir)
    else:
        ws_root = _resolve_workspace_root()
        if ws_root.is_dir():
            cmd.append(str(ws_root))
    log_dir = LTX_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_fh = open(log_dir / "table_tool_stdout.log", "ab")
    err_fh = open(log_dir / "table_tool_stderr.log", "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(script.parent),
            stdout=out_fh,
            stderr=err_fh,
            **_detached_popen_kwargs(),
        )
    finally:
        try:
            out_fh.close()
            err_fh.close()
        except Exception:
            pass
    _MODULE_PROCS["table"] = proc
    get_spine_db().emit_event("launcher.table_tool_started", "launcher", "table_tool", {})
    return True


def launch_studio_gateway() -> bool:
    """启动本地算力网关（单次点击防误触保护）。"""
    if is_module_active("gateway"):
        return False
    _MODULE_CLICK_ACCEPTED.add("gateway")
    _MODULE_START_TS["gateway"] = time.monotonic()
    bat = LTX_ROOT / "启动本地算力网关.bat"
    if os.name == "nt" and bat.is_file():
        proc = subprocess.Popen(["cmd.exe", "/c", "start", "", str(bat)], cwd=str(LTX_ROOT))
    else:
        proc = subprocess.Popen(
            [_python_exe(gui=False), "-m", "modules.studio_gateway.server"],
            cwd=str(LTX_ROOT),
            **_detached_popen_kwargs(),
        )
    _MODULE_PROCS["gateway"] = proc
    get_spine_db().emit_event("launcher.gateway_started", "launcher", "gateway", {"port": 7870})
    return True


def run_standalone_web_launcher() -> None:
    """当用户仅下载了 02 轻量代码包、尚未安装 PySide6 时，使用纯 Python 标准库 + Edge/Chrome --app 独立窗口提供零依赖总控台。"""
    import base64
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import shutil
    import threading
    import webbrowser

    brand_dir = LTX_ROOT / "resources" / "brand"

    def _b64_img(name: str) -> str:
        p = brand_dir / name
        if not p.is_file():
            return ""
        try:
            return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        except Exception:
            return ""

    prof = get_hardware_guard().probe_system()
    gpu_short = (prof.gpu_name or "CPU 模式").replace("NVIDIA GeForce ", "")
    vram_gb = int(round(prof.gpu_vram_total_gb))
    ram_gb = int(round(prof.ram_total_gb))
    tier_cn = "旗舰工作站" if vram_gb >= 12 else ("主流独显档" if vram_gb >= 6 else "高内存 CPU 档")
    hw_text = f"✦ {tier_cn} · {gpu_short} ({vram_gb}GB) · {prof.cpu_cores}核 CPU · {ram_gb}GB 内存"

    app_icon_uri = _b64_img("app.png")
    orante_icon_uri = _b64_img("orante_me.png")
    cutora_icon_uri = _b64_img("cutora_me.png")
    table_icon_uri = _b64_img("table_tool_me.png")

    html_page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>片语工作室 v3.0</title>
<style>
*{{box-sizing:border-box;user-select:none;font-family:"Inter","Noto Sans SC","Microsoft YaHei UI",sans-serif;}}
body{{margin:0;background:radial-gradient(circle at 50% 14%, #2B231B 0%, #141210 72%);color:#EFE7DA;height:100vh;display:flex;flex-direction:column;padding:14px 16px;overflow:hidden;}}
.hdr{{font-size:10px;color:#C8A24A;letter-spacing:1.6px;font-weight:600;margin-bottom:6px;}}
.hero{{text-align:center;margin:4px 0 8px 0;}}
.hero img{{width:56px;height:56px;border-radius:50%;box-shadow:0 0 24px rgba(224,190,104,0.35);border:1px solid rgba(224,190,104,0.55);}}
.hero h1{{margin:6px 0 4px 0;font-size:18px;letter-spacing:3px;color:#EFE7DA;}}
.hw{{background:rgba(28,24,21,0.85);border:1px solid rgba(200,162,74,0.4);border-radius:12px;padding:4px 8px;font-size:10px;color:#D8CBB8;text-align:center;margin-bottom:10px;}}
.cards{{display:flex;flex-direction:column;gap:9px;flex:1;}}
.card{{background:linear-gradient(135deg, rgba(34,28,22,0.92), rgba(22,19,16,0.96));border:1px solid rgba(142,118,72,0.55);border-radius:12px;padding:10px 12px;display:flex;align-items:center;gap:12px;cursor:pointer;transition:all .16s ease;position:relative;}}
.card:hover{{border-color:#E0BE68;transform:translateY(-1px);box-shadow:0 4px 14px rgba(0,0,0,0.45);}}
.card img{{width:48px;height:48px;border-radius:10px;flex-shrink:0;}}
.card-body{{flex:1;}}
.card-idx{{position:absolute;top:8px;right:12px;font-size:10px;color:#8A6E2E;font-weight:700;}}
.card-title{{font-size:14px;font-weight:700;color:#EFE7DA;margin-bottom:6px;}}
.btn-row{{display:flex;justify-content:flex-end;gap:6px;}}
.btn{{background:linear-gradient(135deg,#D6B056,#9E7D34);color:#16120E;border:1px solid #E5C675;border-radius:7px;padding:4px 12px;font-size:11px;font-weight:700;cursor:pointer;}}
.btn.sec{{background:rgba(38,32,25,0.9);color:#D8CBB8;border:1px solid rgba(142,118,72,0.6);}}
.ftr{{display:flex;justify-content:center;gap:8px;margin-top:8px;}}
.toast{{position:fixed;bottom:44px;left:16px;right:16px;background:rgba(24,20,16,0.96);border:1px solid #E0BE68;color:#EFE7DA;padding:7px 10px;border-radius:8px;font-size:11px;text-align:center;display:none;}}
</style></head><body>
<div class="hdr">◆ ORANTE · CUTORA · TABLE</div>
<div class="hero"><img src="{app_icon_uri}"/><h1>片语工作室</h1></div>
<div class="hw">{hw_text}</div>
<div class="cards">
  <div class="card" onclick="act('orante')">
    <span class="card-idx">01</span><img src="{orante_icon_uri}"/>
    <div class="card-body"><div class="card-title">制片台 Orante</div>
      <div class="btn-row"><button class="btn">进入制片台 ▸</button></div>
    </div>
  </div>
  <div class="card" onclick="act('cutora')">
    <span class="card-idx">02</span><img src="{cutora_icon_uri}"/>
    <div class="card-body"><div class="card-title">片语剪辑 Cutora</div>
      <div class="btn-row"><button class="btn">打开剪辑台 ▸</button></div>
    </div>
  </div>
  <div class="card" onclick="act('table')">
    <span class="card-idx">03</span><img src="{table_icon_uri}"/>
    <div class="card-body"><div class="card-title">右键表格工具</div>
      <div class="btn-row"><button class="btn">打开表格工具 ▸</button></div>
    </div>
  </div>
</div>
<div class="ftr">
  <button class="btn sec" onclick="act('purge')">🧹 释放系统内存</button>
  <button class="btn sec" onclick="act('blackbox')">📦 导出黑匣子</button>
</div>
<div id="toast" class="toast"></div>
<script>
function showToast(msg){{const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(()=>{{t.style.display='none';}},3500);}}
async function act(cmd){{
  showToast('正在执行请求，请稍候…');
  try{{
    const r=await fetch('/api/act?cmd='+encodeURIComponent(cmd),{{method:'POST'}});
    const d=await r.json();
    showToast(d.msg||'已执行');
  }}catch(e){{showToast('请求失败: '+e);}}
}}
</script></body></html>""".encode("utf-8")

    class _LauncherHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_page)))
            self.end_headers()
            self.wfile.write(html_page)

        def do_POST(self):
            cmd = ""
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if kv.startswith("cmd="):
                        cmd = kv.split("=", 1)[1]
            msg = "已执行"
            if cmd == "orante":
                launch_orante()
                msg = "✓ 已启动 Orante 2.0 制片台（首次运行若需补齐轻量组件会自动弹出终端）"
            elif cmd == "cutora":
                launch_cutora()
                msg = "✓ 已启动片语剪辑台 CUTORA（首次运行若需补齐界面组件会自动弹出终端）"
            elif cmd == "table":
                launch_table_tool()
                msg = "✓ 已启动右键表格工具"
            elif cmd == "purge":
                import gc
                gc.collect()
                msg = f"✓ 已执行系统内存回收 · 当前总内存 {ram_gb}GB 守护中"
            elif cmd == "blackbox":
                try:
                    cutora_dir = LTX_ROOT / "modules" / "cutora_editor"
                    if str(cutora_dir) not in sys.path:
                        sys.path.insert(0, str(cutora_dir))
                    from cutora.core.blackbox import export_blackbox_zip
                    info = export_blackbox_zip(include_win_events=True)
                    zp = Path(str(info.get("zip_path") or "")).name
                    msg = f"✓ 已导出黑匣子至桌面：{zp}"
                except Exception as e:
                    msg = f"导出黑匣子失败：{e}"
            payload = json.dumps({"ok": True, "msg": msg}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LauncherHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    write_launch_state("MAIN_WINDOW_READY", app_name="launcher", detail={"mode": "web_standalone", "port": port})

    # 优先使用 Edge/Chrome --app 独立小窗口打开
    candidates = []
    if os.name == "nt":
        for p in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ):
            if os.path.isfile(p):
                candidates.append(p)
    elif sys.platform == "darwin":
        for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ):
            if os.path.isfile(p):
                candidates.append(p)
    if candidates:
        prof_dir = LTX_ROOT / "data" / "launcher_app_profile"
        prof_dir.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(
                [
                    candidates[0],
                    f"--app={url}",
                    "--window-size=350,600",
                    f"--user-data-dir={prof_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc.wait()
        except Exception:
            webbrowser.open(url)
    else:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def run_gui(screenshot_path: str | None = None) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LTXStudio.PianYu.3.0")
        except Exception:
            pass

    if "--web-launcher" in sys.argv:
        run_standalone_web_launcher()
        return

    try:
        from PySide6.QtCore import (
            QEasingCurve,
            QPoint,
            QPointF,
            QRectF,
            QSize,
            Qt,
            QTimer,
            QVariantAnimation,
            Signal,
        )
        from PySide6.QtGui import (
            QColor,
            QFont,
            QFontDatabase,
            QIcon,
            QLinearGradient,
            QPainter,
            QPainterPath,
            QPen,
            QPixmap,
            QRadialGradient,
        )
        from PySide6.QtWidgets import (
            QApplication,
            QHBoxLayout,
            QLabel,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        run_standalone_web_launcher()
        return

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("片语工作室")
    app.setOrganizationName("CUTORA")

    ico_path = LTX_ROOT / "resources" / "brand" / "app.ico"
    if ico_path.is_file():
        app.setWindowIcon(QIcon(str(ico_path)))

    # 注册 CUTORA 内置字体 (Inter / Noto Sans SC / JetBrains Mono)
    cutora_res = LTX_ROOT / "modules" / "cutora_editor" / "resources"
    for rel_font in (
        Path("fonts_pending") / "Inter" / "Interopsz_wght.ttf",
        Path("fonts_pending") / "JetBrains_Mono" / "JetBrainsMonowght.ttf",
        Path("fonts") / "Noto_Sans_SC" / "NotoSansSCwght.ttf",
    ):
        fp = cutora_res / rel_font
        if fp.is_file():
            QFontDatabase.addApplicationFont(str(fp))

    def make_font(px: int, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False, letter_spacing: float = 0.0) -> QFont:
        f = QFont()
        if mono:
            f.setFamilies(["JetBrains Mono", "Menlo", "SF Mono", "Consolas", "PingFang SC", "Microsoft YaHei UI"])
        else:
            f.setFamilies(["Inter", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Heiti SC", "Microsoft YaHei UI", "Microsoft YaHei"])
        f.setPixelSize(px)
        f.setWeight(weight)
        if letter_spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
        return f

    def blend_color(c1: QColor, c2: QColor, t: float) -> QColor:
        t = max(0.0, min(1.0, float(t)))
        return QColor(
            int(c1.red() + (c2.red() - c1.red()) * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue() + (c2.blue() - c1.blue()) * t),
            int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
        )

    def load_smooth_pixmap(path: Path, target_px: int) -> QPixmap:
        if not path.is_file():
            return QPixmap()
        pm = QPixmap(str(path))
        if pm.isNull():
            return pm
        dpr = 2.0
        scaled = pm.scaled(
            int(target_px * dpr),
            int(target_px * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        return scaled

    # ─────────────────────── 顶部窗口控制圆钮 ───────────────────────
    class WindowControlBtn(QWidget):
        clicked = Signal()

        def __init__(self, symbol: str, is_close: bool = False, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._symbol = symbol
            self._is_close = is_close
            self._hover_t = 0.0
            self.setFixedSize(22, 22)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(150)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._hover_t)
            self._anim.setEndValue(1.0)
            self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._hover_t)
            self._anim.setEndValue(0.0)
            self._anim.start()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

            bg_idle = QColor(28, 24, 21, 140)
            bg_hover = QColor("#B4544A") if self._is_close else QColor(200, 162, 74, 65)
            border_idle = QColor(93, 85, 80, 110)
            border_hover = QColor("#E0BE68") if not self._is_close else QColor("#D87A70")

            p.setBrush(blend_color(bg_idle, bg_hover, self._hover_t))
            p.setPen(QPen(blend_color(border_idle, border_hover, self._hover_t), 1.0))
            p.drawRoundedRect(r, 7.0, 7.0)

            fg = blend_color(QColor(FG1), QColor(FG0), self._hover_t)
            p.setPen(QPen(fg, 1.4))
            p.setFont(make_font(13, QFont.Weight.DemiBold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._symbol)
            p.end()

    # ─────────────────────── 圆角金韵交互按钮 ───────────────────────
    class AnimatedGoldButton(QWidget):
        clicked = Signal()

        def __init__(
            self,
            text: str,
            primary: bool = True,
            compact: bool = False,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._text = text
            self._orig_text = text
            self._primary = primary
            self._compact = compact
            self._locked = False
            self._hover_t = 0.0
            self._press_t = 0.0
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            f = make_font(11 if compact else 12, QFont.Weight.DemiBold)
            self.setFont(f)
            self._recalc_size()

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(160)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

        def _recalc_size(self) -> None:
            fm = self.fontMetrics()
            pad_x = 18 if self._compact else 24
            h = 26 if self._compact else 30
            self.setFixedSize(fm.horizontalAdvance(self._text) + pad_x, h)

        def set_text(self, text: str) -> None:
            self._text = text
            self._recalc_size()
            self.update()

        def set_locked(self, locked: bool, text: str | None = None) -> None:
            self._locked = locked
            if locked:
                if text is not None:
                    self._text = text
                    self._recalc_size()
            else:
                self._text = text if text is not None else self._orig_text
                self._recalc_size()
            self._anim.stop()
            self._hover_t = 0.0
            self._press_t = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
            self.update()

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(1.0)
                self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            self._press_t = 0.0
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(0.0)
                self._anim.start()
            super().leaveEvent(event)

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_t = 1.0
                if not self._locked:
                    self.update()
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if self._press_t > 0:
                self._press_t = 0.0
                self.update()
                if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
                    self.clicked.emit()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            if self._press_t > 0 and not self._locked:
                rect = rect.adjusted(0.5, 1.0, -0.5, 0.5)

            radius = 7.5 if self._compact else 8.5

            if self._locked:
                p.setBrush(QColor(34, 30, 26, 210))
                p.setPen(QPen(QColor(115, 102, 78, 130), 1.0))
                p.drawRoundedRect(rect, radius, radius)
                p.setPen(QColor("#8E8477"))
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
                p.end()
                return

            if self._primary:
                grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                c_top = blend_color(QColor("#D6B056"), QColor("#EAD084"), self._hover_t)
                c_bot = blend_color(QColor("#9E7D34"), QColor("#C8A24A"), self._hover_t)
                if self._press_t > 0:
                    c_top = QColor("#8A6E2E")
                    c_bot = QColor("#6E5622")
                grad.setColorAt(0.0, c_top)
                grad.setColorAt(1.0, c_bot)
                p.setBrush(grad)
                border_col = blend_color(QColor("#E5C675"), QColor("#FFF0B8"), self._hover_t)
                p.setPen(QPen(border_col, 1.1))
                p.drawRoundedRect(rect, radius, radius)

                p.setPen(QColor("#16120E"))
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            else:
                bg_idle = QColor(38, 32, 25, 195)
                bg_hover = QColor(72, 58, 36, 235)
                p.setBrush(blend_color(bg_idle, bg_hover, self._hover_t))
                b_idle = QColor(142, 118, 72, 150)
                b_hover = QColor("#E0BE68")
                p.setPen(QPen(blend_color(b_idle, b_hover, self._hover_t), 1.1))
                p.drawRoundedRect(rect, radius, radius)

                fg_col = blend_color(QColor("#D8CBB8"), QColor("#F6E7C4"), self._hover_t)
                p.setPen(fg_col)
                p.setFont(self.font())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            p.end()

    # ─────────────────────── 海报级软件模块卡片（精炼版：仅保留图标与启动入口） ───────────────────────
    class PosterModuleCard(QWidget):
        def __init__(
            self,
            index_str: str,
            icon_path: Path,
            title: str,
            badge_text: str,
            desc: str,
            actions: list[tuple[str, bool, callable]],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._index_str = index_str
            self._pixmap = load_smooth_pixmap(icon_path, 54)
            self._title = title
            self._badge_text = badge_text
            self._orig_badge_text = badge_text
            self._desc = desc
            self._locked = False
            self._hover_t = 0.0
            self._primary_callback = actions[-1][2] if actions else None

            self.setFixedHeight(86)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(190)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

            # 右下角启动入口按钮
            self._buttons: list[AnimatedGoldButton] = []
            btn_row = QHBoxLayout(self)
            btn_row.setContentsMargins(74, 44, 14, 10)
            btn_row.setSpacing(6)
            btn_row.addStretch(1)
            for label, is_prim, cb in actions:
                btn = AnimatedGoldButton(label, primary=is_prim, compact=True, parent=self)
                btn.clicked.connect(cb)
                btn_row.addWidget(btn)
                self._buttons.append(btn)

        def set_locked(
            self,
            locked: bool,
            primary_text: str = "✓ 已启动 · 再次点击无效",
            lock_all_buttons: bool = True,
        ) -> None:
            self._locked = locked
            self._anim.stop()
            self._hover_t = 0.0
            self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
            self._badge_text = "已接受启动 · 防误触锁定" if locked else self._orig_badge_text
            for idx, btn in enumerate(self._buttons):
                is_last = idx == len(self._buttons) - 1
                if is_last:
                    btn.set_locked(locked, primary_text if locked else None)
                elif lock_all_buttons:
                    btn.set_locked(locked)
            self.update()

        def _on_anim(self, val: float) -> None:
            self._hover_t = float(val)
            self.update()

        def enterEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(1.0)
                self._anim.start()
            super().enterEvent(event)

        def leaveEvent(self, event) -> None:
            if not self._locked:
                self._anim.stop()
                self._anim.setStartValue(self._hover_t)
                self._anim.setEndValue(0.0)
                self._anim.start()
            super().leaveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                if not any(b.geometry().contains(pos) for b in self._buttons):
                    if self.rect().contains(pos) and callable(self._primary_callback):
                        self._primary_callback()
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            lift = self._hover_t * 1.5
            rect = QRectF(self.rect()).adjusted(1.5, 2.0 - lift, -1.5, -1.5 - lift)
            radius = 12.0

            # 卡片玄武岩半透明渐变底色
            bg_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            c0 = blend_color(QColor(30, 25, 21, 216), QColor(48, 39, 30, 240), self._hover_t)
            c1 = blend_color(QColor(22, 19, 16, 228), QColor(34, 28, 22, 245), self._hover_t)
            bg_grad.setColorAt(0.0, c0)
            bg_grad.setColorAt(1.0, c1)

            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, bg_grad)

            # 悬停时软件图标背后的暖金柔光晕
            icon_cx = rect.left() + 39.0
            icon_cy = rect.center().y()
            if self._hover_t > 0.01:
                glow = QRadialGradient(QPointF(icon_cx, icon_cy), 50.0)
                glow.setColorAt(0.0, QColor(224, 190, 104, int(72 * self._hover_t)))
                glow.setColorAt(1.0, QColor(224, 190, 104, 0))
                p.save()
                p.setClipPath(path)
                p.fillRect(rect, glow)
                p.restore()

            # 顶部微光流线与圆角渐变金边
            border_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            b_top = blend_color(QColor(112, 96, 74, 155), QColor("#E5C778"), self._hover_t)
            b_bot = blend_color(QColor(68, 59, 51, 130), QColor("#8A6E2E"), self._hover_t)
            border_grad.setColorAt(0.0, b_top)
            border_grad.setColorAt(1.0, b_bot)
            p.setPen(QPen(border_grad, 1.2 + 0.4 * self._hover_t))
            p.drawPath(path)

            # 右上角古典序号暗纹 (01 / 02 / 03)
            p.setFont(make_font(10, QFont.Weight.Bold, mono=True, letter_spacing=1.0))
            idx_col = blend_color(QColor(146, 137, 127, 105), QColor(224, 190, 104, 215), self._hover_t)
            p.setPen(idx_col)
            p.drawText(
                QRectF(rect.right() - 44, rect.top() + 12, 30, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._index_str,
            )

            # 绘制对应软件的真实高清徽章图标（带轻微悬停缩放）
            if not self._pixmap.isNull():
                scale = 1.0 + 0.05 * self._hover_t
                p.save()
                p.translate(icon_cx, icon_cy)
                p.scale(scale, scale)
                p.drawPixmap(int(-27), int(-27), self._pixmap)
                p.restore()

            # 模块主名称（精炼无冗余说明）
            text_x = rect.left() + 76.0
            title_font = make_font(14, QFont.Weight.Bold, letter_spacing=0.4)
            p.setFont(title_font)
            title_col = blend_color(QColor(FG0), QColor("#FFF7E8"), self._hover_t)
            p.setPen(title_col)
            p.drawText(QPointF(text_x, rect.top() + 29), self._title)
            p.end()

    # ─────────────────────── 底部工作室状态与内存卫士总控卡 ───────────────────────
    class SpineFooterCard(QWidget):
        guide_requested = Signal()
        patrol_requested = Signal()
        shortcut_requested = Signal()
        refresh_requested = Signal()

        def __init__(self, badge_icon: Path, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._badge_pm = load_smooth_pixmap(badge_icon, 38)
            self._summary = {"tasks_total": 0, "gaps_pending": 0, "uploads_pending": 0, "unacked_events": 0}
            self._latest_signal = "Rust 高性能内存引擎在线 · 智能拼接/自动字幕/制片全流程就绪"
            self._pulse_phase = 0.0
            self.setFixedHeight(186)

            lay = QVBoxLayout(self)
            lay.setContentsMargins(14, 142, 14, 12)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)

            self.btn_guide = AnimatedGoldButton("🧹 一键释放系统内存", primary=True, compact=False, parent=self)
            self.btn_patrol = AnimatedGoldButton("⚡ 检查热更新", primary=False, compact=False, parent=self)
            self.btn_shortcut = AnimatedGoldButton("桌面图标", primary=False, compact=False, parent=self)
            self.btn_refresh = AnimatedGoldButton("刷新", primary=False, compact=False, parent=self)

            self.btn_guide.clicked.connect(self.guide_requested.emit)
            self.btn_patrol.clicked.connect(self.patrol_requested.emit)
            self.btn_shortcut.clicked.connect(self.shortcut_requested.emit)
            self.btn_refresh.clicked.connect(self.refresh_requested.emit)

            btn_row.addWidget(self.btn_guide)
            btn_row.addWidget(self.btn_patrol)
            btn_row.addWidget(self.btn_shortcut)
            btn_row.addStretch(1)
            btn_row.addWidget(self.btn_refresh)
            lay.addLayout(btn_row)

        def update_data(self, summary: dict, latest_signal: str) -> None:
            self._summary = dict(summary)
            if latest_signal:
                self._latest_signal = latest_signal
            self.update()

        def set_pulse(self, phase: float) -> None:
            self._pulse_phase = phase
            self.update()

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            radius = 14.0

            bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
            bg.setColorAt(0.0, QColor(28, 24, 20, 228))
            bg.setColorAt(1.0, QColor(19, 16, 14, 240))
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, bg)

            b_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            b_grad.setColorAt(0.0, QColor(200, 162, 74, 135))
            b_grad.setColorAt(1.0, QColor(82, 73, 68, 135))
            p.setPen(QPen(b_grad, 1.15))
            p.drawPath(path)

            # 1. 顶部：工作室徽章 + 标题 + 在线呼吸灯
            if not self._badge_pm.isNull():
                p.drawPixmap(int(rect.left() + 14), int(rect.top() + 11), self._badge_pm)

            p.setFont(make_font(13, QFont.Weight.Bold))
            p.setPen(QColor(FG0))
            p.drawText(QPointF(rect.left() + 60, rect.top() + 26), "工作室状态与内存卫士")

            p.setFont(make_font(10, QFont.Weight.Normal))
            p.setPen(QColor(FG2))
            p.drawText(QPointF(rect.left() + 60, rect.top() + 43), "Rust 原生内存护航 · 自动字幕/智能拼接/制片全流程闭环")

            # 右上角在线胶囊：显示当前活跃日期与推进批次（替代旧『三分身在线』）
            active_day_s = str(self._summary.get("active_day", ""))[-5:]
            active_batch_s = str(self._summary.get("active_batch", "A"))
            pill_label = f"{active_day_s} · {active_batch_s}批次" if active_day_s else "多子代理就绪"

            pill_r = QRectF(rect.right() - 124, rect.top() + 14, 110, 22)
            p.setBrush(QColor(125, 155, 106, 32))
            p.setPen(QPen(QColor(125, 155, 106, 120), 1.0))
            p.drawRoundedRect(pill_r, 11.0, 11.0)

            pulse_alpha = int(160 + 90 * (0.5 + 0.5 * math.sin(self._pulse_phase)))
            p.setBrush(QColor(142, 184, 116, pulse_alpha))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(pill_r.left() + 13, pill_r.center().y()), 3.6, 3.6)

            p.setFont(make_font(10, QFont.Weight.Medium))
            p.setPen(QColor("#A5C490"))
            p.drawText(pill_r.adjusted(22, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, pill_label)

            # 2. 中部：四项实时指标圆角小卡
            tasks_cnt = int(self._summary.get("tasks_total", 0))
            gaps_cnt = int(self._summary.get("gaps_pending", 0))
            clips_cnt = int(self._summary.get("clips_count", 0))
            up_ok_cnt = int(self._summary.get("uploaded_ok_count", 0))
            prev_up_cnt = int(self._summary.get("prev_uploaded_ok", 0))
            prev_day_s = str(self._summary.get("prev_completed_day", ""))[-2:].lstrip("0")

            first_metric = ("当期任务", str(tasks_cnt)) if tasks_cnt > 0 else ("当期批次", f"{active_batch_s}批")
            second_metric = ("待补片段", str(gaps_cnt)) if gaps_cnt > 0 else ("生成片段", str(clips_cnt))
            if up_ok_cnt > 0:
                third_metric = ("已传闭环", f"{up_ok_cnt}✓")
            elif prev_up_cnt > 0:
                third_metric = (f"{prev_day_s or '前'}号已传", f"{prev_up_cnt}✓")
            else:
                third_metric = ("已导成品", str(self._summary.get("uploads_pending", 0)))

            metrics = [
                first_metric,
                second_metric,
                third_metric,
                ("今日信号", str(self._summary.get("unacked_events", 0))),
            ]
            m_y = rect.top() + 55
            m_w = (rect.width() - 28 - 24) / 4.0
            for i, (m_label, m_val) in enumerate(metrics):
                mx = rect.left() + 14 + i * (m_w + 8)
                mr = QRectF(mx, m_y, m_w, 34)
                p.setBrush(QColor(38, 32, 25, 190))
                p.setPen(QPen(QColor(93, 85, 80, 120), 0.9))
                p.drawRoundedRect(mr, 7.0, 7.0)

                p.setFont(make_font(10, QFont.Weight.Normal))
                p.setPen(QColor(FG1))
                p.drawText(mr.adjusted(9, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, m_label)

                p.setFont(make_font(14, QFont.Weight.Bold, mono=True))
                p.setPen(QColor(ACCENT_HI))
                p.drawText(mr.adjusted(4, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, m_val)

            # 底部实时信号流水行
            sig_r = QRectF(rect.left() + 14, rect.top() + 97, rect.width() - 28, 32)
            p.setBrush(QColor(20, 18, 16, 195))
            p.setPen(QPen(QColor(82, 73, 68, 125), 0.9))
            p.drawRoundedRect(sig_r, 7.0, 7.0)

            p.setFont(make_font(10, QFont.Weight.Medium))
            p.setPen(QColor(ACCENT))
            p.drawText(sig_r.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "◆ 闭环矩阵")

            p.setFont(make_font(10, QFont.Weight.Normal))
            p.setPen(QColor(FG1))
            fm_s = p.fontMetrics()
            elided = fm_s.elidedText(self._latest_signal, Qt.TextElideMode.ElideRight, int(sig_r.width() - 88))
            p.drawText(sig_r.adjusted(78, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
            p.end()

    # ─────────────────────── 顶部轻提示横幅 (Toast) ───────────────────────
    class GoldToastBanner(QWidget):
        def __init__(self, parent: QWidget) -> None:
            super().__init__(parent)
            self._text = ""
            self._opacity = 0.0
            self.setFixedSize(WIN_W - 24, 34)
            self.move(12, 38)
            self.hide()

            self._anim = QVariantAnimation(self)
            self._anim.setDuration(220)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._on_anim)

            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(self._fade_out)

        def show_toast(self, text: str, duration_ms: int = 2800) -> None:
            self._text = text
            self.show()
            self.raise_()
            self._anim.stop()
            self._anim.setStartValue(self._opacity)
            self._anim.setEndValue(1.0)
            self._anim.start()
            self._hide_timer.start(duration_ms)

        def _fade_out(self) -> None:
            self._anim.stop()
            self._anim.setStartValue(self._opacity)
            self._anim.setEndValue(0.0)
            self._anim.start()

        def _on_anim(self, val: float) -> None:
            self._opacity = float(val)
            if self._opacity <= 0.01 and not self._hide_timer.isActive():
                self.hide()
            self.update()

        def paintEvent(self, event) -> None:
            if self._opacity <= 0.01:
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setOpacity(self._opacity)
            r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            p.setBrush(QColor(36, 30, 23, 245))
            p.setPen(QPen(QColor(ACCENT_HI), 1.2))
            p.drawRoundedRect(r, 9.0, 9.0)

            p.setFont(make_font(10, QFont.Weight.DemiBold))
            p.setPen(QColor(FG0))
            fm = p.fontMetrics()
            elided = fm.elidedText(f"◆ {self._text}", Qt.TextElideMode.ElideRight, int(r.width() - 16))
            p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, elided)
            p.end()

    # ─────────────────────── 9:16 竖屏海报主窗口（精炼版 315×560） ───────────────────────
    class PosterLauncherWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("片语工作室")
            self.setFixedSize(WIN_W, WIN_H)
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Window
                | Qt.WindowType.WindowMinimizeButtonHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            self._drag_offset: QPoint | None = None
            self._pulse_phase = 0.0

            brand_dir = LTX_ROOT / "resources" / "brand"
            self._bg_pm = QPixmap(str(brand_dir / "poster_bg_9x16.png"))
            self._hero_pm = load_smooth_pixmap(brand_dir / "app.png", 62)

            guard = get_hardware_guard()
            self._profile = guard.probe_system()
            self._spine = get_spine_db()

            self._build_ui(brand_dir)
            self._toast = GoldToastBanner(self)

            # 呼吸光效与实时状态定时器
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(45)
            self._pulse_timer.timeout.connect(self._tick_pulse)
            self._pulse_timer.start()

            self._status_timer = QTimer(self)
            self._status_timer.setInterval(6000)
            self._status_timer.timeout.connect(lambda: self.refresh_status(silent=True))
            self._status_timer.start()

            self.refresh_status(silent=True)
            try:
                write_launch_state("MAIN_WINDOW_READY", app_name="launcher")
            except Exception:
                pass

        def _build_ui(self, brand_dir: Path) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(16, 10, 16, 16)
            root.setSpacing(0)

            # 1. 顶部栏（最小化 / 关闭按钮）
            top_bar = QHBoxLayout()
            top_bar.setContentsMargins(4, 0, 0, 0)
            top_bar.setSpacing(5)
            top_bar.addStretch(1)

            btn_min = WindowControlBtn("—", is_close=False, parent=self)
            btn_close = WindowControlBtn("×", is_close=True, parent=self)
            btn_min.clicked.connect(self.showMinimized)
            btn_close.clicked.connect(self.close)
            top_bar.addWidget(btn_min)
            top_bar.addWidget(btn_close)
            root.addLayout(top_bar)

            # 2. 预留顶部海报主视觉区高度 (Hero Poster Header + 硬件配置条 由 paintEvent 精绘)
            root.addSpacing(154)

            # 3. 三大核心软件内嵌卡片区（仅保留图片与启动入口，保留防误触限制）
            cards_layout = QVBoxLayout()
            cards_layout.setContentsMargins(0, 0, 0, 0)
            cards_layout.setSpacing(12)

            self._accepted_clicks: set[str] = set()

            self._card_orante = PosterModuleCard(
                index_str="01",
                icon_path=brand_dir / "orante_medallion.png",
                title="制片台 Orante",
                badge_text="",
                desc="",
                actions=[
                    ("启动制片台  ▸", True, lambda: self._on_open_orante("")),
                ],
                parent=self,
            )

            self._card_cutora = PosterModuleCard(
                index_str="02",
                icon_path=brand_dir / "cutora_me.png",
                title="片语剪辑 Cutora",
                badge_text="",
                desc="",
                actions=[
                    ("进入剪辑台  ▸", True, lambda: self._on_open_cutora("")),
                ],
                parent=self,
            )

            self._card_table = PosterModuleCard(
                index_str="03",
                icon_path=brand_dir / "table_tool_me.png",
                title="右键表格工具",
                badge_text="",
                desc="",
                actions=[
                    ("打开表格工具  ▸", True, self._on_open_table),
                ],
                parent=self,
            )

            # 本地算力网关 (7870) 通道与实例在底层完整保留，不在首页展示
            self._card_gateway = PosterModuleCard(
                index_str="04",
                icon_path=brand_dir / "ltx_gateway_me.png",
                title="本地算力网关 · 7870",
                badge_text="",
                desc="",
                actions=[
                    ("🧩 扩展包", False, self._on_open_pack_manager),
                    ("唤醒算力网关  ▸", True, self._on_open_gateway),
                ],
                parent=self,
            )
            self._card_gateway.hide()

            cards_layout.addWidget(self._card_orante)
            cards_layout.addWidget(self._card_cutora)
            cards_layout.addWidget(self._card_table)
            root.addLayout(cards_layout)

            if is_cutora_running():
                self._accepted_clicks.add("cutora")
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")

            root.addStretch(1)

            # 4. 底部小按钮：释放系统内存 + 一键导出全工作室黑匣子
            bottom_bar = QHBoxLayout()
            bottom_bar.setContentsMargins(0, 8, 0, 0)
            bottom_bar.setSpacing(8)
            bottom_bar.addStretch(1)
            self._btn_purge_mem = AnimatedGoldButton("🧹 释放系统内存", primary=False, compact=True, parent=self)
            self._btn_purge_mem.clicked.connect(self._on_purge_system_memory)
            bottom_bar.addWidget(self._btn_purge_mem)
            self._btn_export_bb = AnimatedGoldButton("📦 导出黑匣子", primary=False, compact=True, parent=self)
            self._btn_export_bb.clicked.connect(self._on_export_blackbox)
            bottom_bar.addWidget(self._btn_export_bb)
            bottom_bar.addStretch(1)
            root.addLayout(bottom_bar)

            # 保留后台状态总控卡实例供内部信号更新调用（不挂载到首页视图）
            self._footer_card = SpineFooterCard(brand_dir / "app.png", parent=self)
            self._footer_card.hide()

        def _sync_module_locks(self) -> None:
            """同步模块运行与防误触状态：正在启动或运行中保持锁定，关闭或退出后自动恢复可点击。"""
            if is_cutora_active():
                if "cutora" not in self._accepted_clicks or not self._card_cutora._locked:
                    self._accepted_clicks.add("cutora")
                    self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
            else:
                if "cutora" in self._accepted_clicks or self._card_cutora._locked:
                    self._accepted_clicks.discard("cutora")
                    self._card_cutora.set_locked(False)

            # Orante 允许多开：5 秒冷却结束后自动解锁卡片
            if orante_cooldown_left() <= 0.0 and ("orante" in self._accepted_clicks or self._card_orante._locked):
                self._accepted_clicks.discard("orante")
                self._card_orante.set_locked(False)

            for mod_id, card in (
                ("table", self._card_table),
            ):
                if not is_module_active(mod_id):
                    if mod_id in self._accepted_clicks or card._locked:
                        self._accepted_clicks.discard(mod_id)
                        card.set_locked(False)

        def _tick_pulse(self) -> None:
            self._pulse_phase = (self._pulse_phase + 0.09) % (math.pi * 2.0)
            self._footer_card.set_pulse(self._pulse_phase)
            self._pulse_ticks = getattr(self, "_pulse_ticks", 0) + 1
            if self._pulse_ticks % 20 == 0:
                self._sync_module_locks()
            # 仅重绘顶部英雄区光环
            self.update(0, 0, WIN_W, 195)

        def _on_purge_system_memory(self) -> None:
            import gc
            gc.collect()
            try:
                if os.name == "nt":
                    hproc = ctypes.windll.kernel32.GetCurrentProcess()
                    try:
                        ctypes.windll.psapi.EmptyWorkingSet(hproc)
                    except Exception:
                        ctypes.windll.kernel32.K32EmptyWorkingSet(hproc)
                guard = get_hardware_guard()
                prof = guard.probe_system()
                self._profile = prof
                self._toast.show_toast(f"已执行系统内存深度回收 · 当前总内存 {int(round(prof.ram_total_gb))}GB 守护中")
            except Exception as e:
                self._toast.show_toast(f"内存整理完成：{e}")

        def _on_export_blackbox(self) -> None:
            self._toast.show_toast("⏳ 正在汇总全工作室三款软件运行记录并生成脱敏黑匣子诊断包…")
            try:
                cutora_dir = LTX_ROOT / "modules" / "cutora_editor"
                if str(cutora_dir) not in sys.path:
                    sys.path.insert(0, str(cutora_dir))
                from cutora.core.blackbox import export_blackbox_zip
                info = export_blackbox_zip(include_win_events=True)
                zp = Path(str(info.get("zip_path") or "")).name
                self._toast.show_toast(f"✓ 已导出全工作室黑匣子至桌面：{zp}")
            except Exception as exc:
                self._toast.show_toast(f"导出黑匣子异常：{exc}")

        def _on_open_orante(self, start_node: str = "") -> None:
            self._sync_module_locks()
            left = orante_cooldown_left()
            if left > 0.0:
                self._toast.show_toast(f"Orante 2.0 刚刚启动，{left:.0f} 秒后可再开一个窗口（支持多开）")
                return
            from core.pack_manager import is_module_installed
            orante_main = LTX_ROOT / "modules" / "orante_desk" / "orante" / "main.py"
            if not orante_main.is_file() and not is_module_installed("pack-app-orante"):
                self._toast.show_toast("当前版本尚未安装 Orante 2.0 制片台扩展包，可一键无缝安装！")
                self._on_open_pack_manager(highlight_pack_id="pack-app-orante")
                return
            if not launch_orante(start_node):
                return
            self._accepted_clicks.add("orante")
            self._card_orante.set_locked(True, "✓ 已启动 · 5 秒后可再开")
            QTimer.singleShot(int(ORANTE_RELAUNCH_COOLDOWN_SEC * 1000) + 100, self._sync_module_locks)
            n = orante_instance_count()
            mode_label = " · 🎨 制图工坊" if start_node == "imgstudio" else ""
            self._toast.show_toast(f"已启动 Orante 2.0 制片台{mode_label}（当前 {n} 个窗口，5 秒后可继续多开）")
            self.refresh_status(silent=True)

        def _on_open_cutora(self, room: str = "") -> None:
            self._sync_module_locks()
            if is_cutora_running():
                self._accepted_clicks.add("cutora")
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
                try:
                    call_cutora_api("ui.focus", {"panel": room or "preview"})
                except Exception:
                    pass
                self._toast.show_toast("CUTORA 已在运行中，已为您尝试唤醒聚焦已有剪辑台窗口")
                return
            if "cutora" in self._accepted_clicks:
                self._toast.show_toast("正在启动 CUTORA 剪辑台，请稍候…")
                return
            self._accepted_clicks.add("cutora")
            self._card_cutora.set_locked(True, "⏳ 正在启动剪辑台…")
            ok = launch_cutora(room)
            if not ok:
                self._accepted_clicks.discard("cutora")
                self._card_cutora.set_locked(False)
                self._toast.show_toast("CUTORA 启动请求未能发起，请重试")
                return
            label = {"stitch": "片段智能拼接", "ai_studio": "AI 工坊"}.get(room, "片语剪辑台")
            self._toast.show_toast(f"正在加载 CUTORA · {label}，等待主面板就绪…")
            self._start_cutora_supervisor(room=room)
            self.refresh_status(silent=True)

        def _start_cutora_supervisor(self, room: str = "") -> None:
            if not hasattr(self, "_cutora_watch_timer"):
                self._cutora_watch_timer = QTimer(self)
                self._cutora_watch_timer.setInterval(300)
                self._cutora_watch_timer.timeout.connect(self._poll_cutora_startup)
            self._cutora_watch_room = room
            self._cutora_watch_started_at = time.monotonic()
            self._cutora_watch_timer.start()

        def _poll_cutora_startup(self) -> None:
            global _CUTORA_LAUNCH_ACCEPTED
            state = read_launch_state("cutora")
            same_sess = (not _CUTORA_SESSION_ID) or (state.get("session_id") == _CUTORA_SESSION_ID)
            stage = str(state.get("stage") or "") if same_sess else ""

            if stage == "MAIN_WINDOW_READY" or (same_sess and state.get("main_window_ready")):
                self._cutora_watch_timer.stop()
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
                get_spine_db().emit_event("launcher.cutora_started", "launcher", "cutora", {"room": getattr(self, "_cutora_watch_room", "")})
                self._toast.show_toast("✓ 片语剪辑台 (CUTORA) 主面板已就绪！")
                self.refresh_status(silent=True)
                return

            if stage == "EXISTING_INSTANCE_FOCUSED":
                self._cutora_watch_timer.stop()
                self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
                self._toast.show_toast("✓ 已切换到正在运行的片语剪辑台窗口")
                self.refresh_status(silent=True)
                return

            proc = _CUTORA_PROC
            if proc is not None and proc.poll() is not None:
                rc = proc.returncode
                self._cutora_watch_timer.stop()
                _CUTORA_LAUNCH_ACCEPTED = False
                self._accepted_clicks.discard("cutora")
                self._card_cutora.set_locked(False)
                err_msg = str(state.get("error") or "") if same_sess else ""
                if not err_msg:
                    try:
                        out_log = LTX_ROOT / "data" / "logs" / "cutora_stdout.log"
                        err_log = LTX_ROOT / "data" / "logs" / "cutora_stderr.log"
                        tail_lines = []
                        for lp in (out_log, err_log):
                            if lp.is_file():
                                tail_lines.extend(lp.read_text(encoding="utf-8", errors="ignore").splitlines()[-8:])
                        err_msg = " | ".join(ln.strip() for ln in tail_lines if ln.strip())[-200:]
                    except Exception:
                        err_msg = f"进程提前退出 (退出码 {rc})"
                repair_script = "安装与体检.command" if sys.platform == "darwin" else "安装与体检.bat"
                get_spine_db().emit_event(
                    "launcher.cutora_startup_failed",
                    "launcher",
                    "cutora",
                    {"returncode": rc, "stage": stage, "error": err_msg},
                )
                self._toast.show_toast(
                    f"❌ 剪辑台未能启动 (码 {rc}): {err_msg or stage} · 请运行《{repair_script}》或查看 data/logs/cutora_stdout.log"
                )
                return

            # 超过 45 秒仍未收到就绪信号则停止高频轮询，保持进程继续加载但允许用户查看状态
            if time.monotonic() - getattr(self, "_cutora_watch_started_at", 0.0) > 45.0:
                self._cutora_watch_timer.stop()
                if is_cutora_running():
                    self._card_cutora.set_locked(True, "✓ 已在运行 · 再次点击无效")
                else:
                    self._accepted_clicks.discard("cutora")
                    self._card_cutora.set_locked(False)

        def _on_open_table(self) -> None:
            self._sync_module_locks()
            if "table" in self._accepted_clicks or is_module_active("table"):
                self._toast.show_toast("右键表格工具已接受过启动点击，第二次点击不再被接受（防误触锁定）")
                return
            self._accepted_clicks.add("table")
            self._card_table.set_locked(True, "✓ 已启动 · 再次点击无效")
            launch_table_tool()
            self._toast.show_toast("已接受点击并启动右键表格工具（后续重复点击已锁定忽略）")
            self.refresh_status(silent=True)

        def _on_open_gateway(self) -> None:
            if not is_module_active("gateway"):
                self._accepted_clicks.discard("gateway")
                self._card_gateway.set_locked(False, lock_all_buttons=False)
            if "gateway" in self._accepted_clicks:
                self._toast.show_toast("算力网关已接受过唤醒点击，第二次点击不再被接受（防误触锁定）")
                return
            self._accepted_clicks.add("gateway")
            self._card_gateway.set_locked(True, "✓ 已唤醒 · 再次点击无效", lock_all_buttons=False)
            launch_studio_gateway()
            self._toast.show_toast("已接受点击并唤醒本地算力网关 (127.0.0.1:7870)")
            self.refresh_status(silent=True)

        def _on_open_pack_manager(self, highlight_pack_id: str = "") -> None:
            from PySide6.QtWidgets import QDialog, QFileDialog, QPushButton, QTextBrowser
            from core.pack_manager import (
                evaluate_all_packs,
                find_inbox_archive,
                install_pack_archive,
                scan_and_auto_install_inbox,
            )

            dlg = QDialog(self)
            dlg.setWindowTitle("片语工作室 · 乐高模块化扩展包中心 (Pack Manager)")
            dlg.resize(560, 680)
            dlg.setStyleSheet(
                f"QDialog {{ background: {BG1}; color: {FG0}; }}"
                f"QTextBrowser {{ background: {BG2}; color: {FG0}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px; font-size: 12px; }}"
                f"QPushButton {{ background: {BG3}; color: {ACCENT_HI}; border: 1px solid {ACCENT}; border-radius: 6px; padding: 8px 14px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {ACCENT}; color: {BG0}; }}"
            )
            lay = QVBoxLayout(dlg)
            browser = QTextBrowser(dlg)
            lay.addWidget(browser, 1)

            def _render_info() -> None:
                info = evaluate_all_packs(self._profile)
                summ = info.get("summary", {})
                lines = [
                    f"<h3 style='color:{ACCENT_HI};margin:0;'>🧩 {summ.get('edition_mode', '')}</h3>",
                    f"<p style='color:{FG1};margin:4px 0 10px 0;'><b>内置标准核心底座 (~25GB)</b>：统一总控台 · 右键表格工具 · CUTORA 剪辑台 · 自动字幕 (Whisper) · 智能拼接 · AI视频抠像 (SAM2.1/RMBG2) · 立体声与降噪（支持 i7 + 32GB 内存无独显流畅运行）</p>",
                ]
                if highlight_pack_id:
                    lines.append(
                        f"<div style='background:#332618;border:1px solid {ACCENT_HI};padding:8px;border-radius:6px;margin-bottom:8px;'>"
                        f"💡 <b>提示</b>：您刚刚点击的功能模块需要扩展包 <code>{highlight_pack_id}.ltxpack</code>。请将其放入 <code>packs/</code> 目录或点击下方按钮一键无缝安装！"
                        f"</div>"
                    )
                lines.append("<hr/>")
                for pk in info.get("packs", []):
                    is_hi = pk["id"] == highlight_pack_id
                    border_style = f"border-left:3px solid {ACCENT_HI};padding-left:8px;" if is_hi else ""
                    lines.append(
                        f"<p style='margin:6px 0;{border_style}'><b>{pk['name']}</b> "
                        f"<span style='color:{ACCENT_HI};'>({pk['approx_size_gb']} GB · <code>{pk['id']}.ltxpack</code>)</span><br/>"
                        f"状态：{pk['status_badge']}<br/>"
                        f"<span style='color:{FG1};'>{pk['description']}</span></p>"
                    )
                browser.setHtml("".join(lines))

            _render_info()
            btn_row = QHBoxLayout()
            btn_auto = QPushButton("⚡ 自动扫描 packs/ 目录一键安装", dlg)
            btn_pick = QPushButton("📂 选择 .ltxpack 文件一键安装…", dlg)
            btn_close = QPushButton("关闭", dlg)

            def _do_auto() -> None:
                if highlight_pack_id:
                    cand = find_inbox_archive(highlight_pack_id)
                    if cand:
                        r = install_pack_archive(cand)
                        _render_info()
                        if r.get("ok"):
                            self._toast.show_toast(f"已一键安装：{highlight_pack_id}！")
                            return
                res = scan_and_auto_install_inbox()
                _render_info()
                if res:
                    self._toast.show_toast(f"已成功自动安装 {len(res)} 个扩展包！")
                else:
                    self._toast.show_toast("packs/ 目录下暂无未安装的新扩展包")

            def _do_pick() -> None:
                fn, _ = QFileDialog.getOpenFileName(
                    dlg,
                    "选择要安装的片语工作室扩展包 (.ltxpack / .zip)",
                    str(LTX_ROOT),
                    "片语扩展包 (*.ltxpack *.zip)",
                )
                if fn:
                    r = install_pack_archive(fn)
                    _render_info()
                    if r.get("ok"):
                        self._toast.show_toast(f"安装完成：{r.get('pack_id')} ({r.get('installed_size_gb')} GB)")
                    else:
                        self._toast.show_toast(f"安装失败：{r.get('error')}")

            btn_auto.clicked.connect(_do_auto)
            btn_pick.clicked.connect(_do_pick)
            btn_close.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_auto)
            btn_row.addWidget(btn_pick)
            btn_row.addWidget(btn_close)
            lay.addLayout(btn_row)
            dlg.exec()


        def _on_run_patrol(self) -> None:
            bat = LTX_ROOT / "代码热更新_双击同步最新功能.bat"
            if os.name == "nt" and bat.is_file():
                subprocess.Popen(["cmd.exe", "/c", "start", "", str(bat)], cwd=str(LTX_ROOT))
                self._toast.show_toast("已启动云端代码热更新窗口 · 更新后重启即可生效")
            elif sys.platform == "darwin":
                from core.studio_updater import check_and_apply_hot_update
                res = check_and_apply_hot_update()
                if res.get("ok"):
                    self._toast.show_toast(f"热更新检查完成：{res.get('message', '已是最新版本')}")
                else:
                    up_res = get_delivery_worker().sync_pending_uploads_to_drive()
                    self.refresh_status(silent=True)
                    self._toast.show_toast(f"状态已同步 · 上传队列处理 {up_res.get('uploaded', 0)} 个文件")
            else:
                up_res = get_delivery_worker().sync_pending_uploads_to_drive()
                self.refresh_status(silent=True)
                self._toast.show_toast(f"状态已同步 · 上传队列处理 {up_res.get('uploaded', 0)} 个文件")

        def _on_create_shortcut(self) -> None:
            ensure_shortcuts(LTX_ROOT, force=True, quiet=True)
            self._toast.show_toast("已在桌面与开始菜单生成『片语工作室』快捷方式")

        def refresh_status(self, silent: bool = False) -> None:
            self._sync_module_locks()
            st = self._spine.get_dashboard_summary()
            ev = self._spine.get_latest_event()
            active_day = st.get("active_day", "")
            active_batch = st.get("active_batch", "A")
            stage_lbl = st.get("stage_label", "待启动")
            prev_day = st.get("prev_completed_day", "")
            prev_up = st.get("prev_uploaded_ok", 0)
            ws_brief = f"[{active_day} · {active_batch}批次] {stage_lbl}"
            if prev_day and prev_up > 0:
                latest_str = f"{ws_brief} ｜ ✅ {prev_day[-5:]} 已全部上传完结 ({prev_up}/{prev_up})"
            elif ev:
                latest_str = f"{ws_brief} ｜ 信号 #{ev['id']} [{ev['topic']}]"
            else:
                latest_str = f"{ws_brief} ｜ Rust 高性能内存引擎在线 · 全流程就绪"
            self._footer_card.update_data(st, latest_str)
            if not silent:
                self._toast.show_toast("工作室实时指标与信号总线已刷新")

        # 窗口任意空白处拖拽移动
        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                self.move(event.globalPosition().toPoint() - self._drag_offset)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            self._drag_offset = None
            super().mouseReleaseEvent(event)

        def paintEvent(self, event) -> None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            outer = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            win_radius = 18.0
            clip_path = QPainterPath()
            clip_path.addRoundedRect(outer, win_radius, win_radius)

            # 1. 绘制 9:16 罗马石材与古典金光影海报底图
            p.save()
            p.setClipPath(clip_path)
            if not self._bg_pm.isNull():
                p.drawPixmap(outer.toRect(), self._bg_pm)
            else:
                p.fillRect(outer, QColor(BG0))
            p.restore()

            # 2. 顶部题头纪年铭文
            p.setFont(make_font(9, QFont.Weight.DemiBold, mono=True, letter_spacing=1.8))
            p.setPen(QColor(ACCENT))
            p.drawText(
                QRectF(18, 12, 230, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "◆  ORANTE · CUTORA · TABLE",
            )

            # 3. 英雄区主徽章呼吸金环与《片语工作室》海报排版
            cx = WIN_W / 2.0
            cy = 72.0
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)

            # 呼吸光晕
            halo = QRadialGradient(QPointF(cx, cy), 48.0)
            halo.setColorAt(0.0, QColor(224, 190, 104, int(55 + 30 * pulse)))
            halo.setColorAt(0.65, QColor(200, 162, 74, int(20 + 14 * pulse)))
            halo.setColorAt(1.0, QColor(200, 162, 74, 0))
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 48.0, 48.0)

            # 外侧古典细金环与两侧金线
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(224, 190, 104, int(90 + 55 * pulse)), 1.0))
            p.drawEllipse(QPointF(cx, cy), 35.0 + 1.2 * pulse, 35.0 + 1.2 * pulse)

            line_Grad_l = QLinearGradient(28, cy, cx - 44, cy)
            line_Grad_l.setColorAt(0.0, QColor(200, 162, 74, 0))
            line_Grad_l.setColorAt(1.0, QColor(200, 162, 74, 130))
            p.setPen(QPen(line_Grad_l, 1.0))
            p.drawLine(QPointF(28, cy), QPointF(cx - 44, cy))

            line_Grad_r = QLinearGradient(cx + 44, cy, WIN_W - 28, cy)
            line_Grad_r.setColorAt(0.0, QColor(200, 162, 74, 130))
            line_Grad_r.setColorAt(1.0, QColor(200, 162, 74, 0))
            p.setPen(QPen(line_Grad_r, 1.0))
            p.drawLine(QPointF(cx + 44, cy), QPointF(WIN_W - 28, cy))

            if not self._hero_pm.isNull():
                p.drawPixmap(int(cx - 31), int(cy - 31), self._hero_pm)

            # 海报主标题：片 语 工 作 室
            p.setFont(make_font(19, QFont.Weight.Bold, letter_spacing=3.5))
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(QRectF(0, 113, WIN_W, 26), Qt.AlignmentFlag.AlignCenter, "片语工作室")
            p.setPen(QColor(FG0))
            p.drawText(QRectF(0, 112, WIN_W, 26), Qt.AlignmentFlag.AlignCenter, "片语工作室")

            # 顶部硬件自适应状态胶囊条（完整保留检测到的配置信息）
            prof = self._profile
            gpu_short = (prof.gpu_name or "CPU 模式").replace("NVIDIA GeForce ", "")
            vram_gb = int(round(prof.gpu_vram_total_gb))
            ram_gb = int(round(prof.ram_total_gb))
            tier_cn = "旗舰工作站" if vram_gb >= 12 else ("主流独显档" if vram_gb >= 6 else "高内存 CPU 档")
            hw_text = f"✦ {tier_cn} · {gpu_short} ({vram_gb}GB) · {prof.cpu_cores}核 CPU · {ram_gb}GB 内存"
            hw_rect = QRectF(16, 146, WIN_W - 32, 22)
            p.setBrush(QColor(28, 24, 21, 195))
            p.setPen(QPen(QColor(200, 162, 74, 95), 1.0))
            p.drawRoundedRect(hw_rect, 11.0, 11.0)

            hw_font = make_font(9, QFont.Weight.Medium)
            p.setFont(hw_font)
            p.setPen(QColor("#D8CBB8"))
            p.drawText(hw_rect, Qt.AlignmentFlag.AlignCenter, hw_text)

            # 4. 窗口外框双层古典金圆角描边
            frame_grad = QLinearGradient(outer.topLeft(), outer.bottomRight())
            frame_grad.setColorAt(0.0, QColor("#E0BE68"))
            frame_grad.setColorAt(0.45, QColor("#9C7B34"))
            frame_grad.setColorAt(1.0, QColor("#5D5043"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(frame_grad, 1.6))
            p.drawRoundedRect(outer, win_radius, win_radius)

            inner_frame = outer.adjusted(1.5, 1.5, -1.5, -1.5)
            p.setPen(QPen(QColor(239, 231, 218, 26), 1.0))
            p.drawRoundedRect(inner_frame, win_radius - 1.5, win_radius - 1.5)
            p.end()

        def closeEvent(self, event) -> None:
            try:
                write_launch_state("PROCESS_EXIT", app_name="launcher")
            except Exception:
                pass
            try:
                if hasattr(self, "_pulse_timer") and self._pulse_timer.isActive():
                    self._pulse_timer.stop()
                if hasattr(self, "_status_timer") and self._status_timer.isActive():
                    self._status_timer.stop()
            except Exception:
                pass
            event.accept()
            app_inst = QApplication.instance()
            if app_inst is not None:
                app_inst.quit()

    app.setQuitOnLastWindowClosed(True)
    win = PosterLauncherWindow()
    win.show()

    # macOS: 当用户在 Dock 或桌面再次点击《片语工作室.app》时，若窗口被最小化或隐藏，立即恢复并置顶
    from PySide6.QtCore import QEvent, QObject

    class _AppActivateFilter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.ApplicationActivate:
                try:
                    if win.isMinimized():
                        win.showNormal()
                    elif not win.isVisible():
                        win.show()
                    win.raise_()
                    win.activateWindow()
                except Exception:
                    pass
            return super().eventFilter(obj, event)

    _act_filter = _AppActivateFilter(app)
    app.installEventFilter(_act_filter)

    if screenshot_path:
        def _capture_and_quit() -> None:
            app.processEvents()
            pm = win.grab()
            out_p = Path(screenshot_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            pm.save(str(out_p), "PNG")
            app.quit()

        QTimer.singleShot(350, _capture_and_quit)

    app.exec()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    try:
        _bootstrap_studio_env()
        if "--patrol-once" in sys.argv:
            print(json.dumps({"ok": True, "spine": get_spine_db().get_dashboard_summary()}, ensure_ascii=False, indent=2))
        elif "--status" in sys.argv:
            print(json.dumps({
                "hardware": get_hardware_guard().probe_system().to_dict(),
                "spine": get_spine_db().get_dashboard_summary(),
            }, ensure_ascii=False, indent=2))
        elif "--screenshot" in sys.argv:
            idx = sys.argv.index("--screenshot")
            shot_out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else str(LTX_ROOT / "launcher_preview.png")
            run_gui(screenshot_path=shot_out)
        else:
            run_gui()
    except Exception as _exc:
        import traceback
        _err_txt = traceback.format_exc()
        try:
            _log_dir = LTX_ROOT / "logs"
            _log_dir.mkdir(parents=True, exist_ok=True)
            (_log_dir / "launcher_error.log").write_text(_err_txt, encoding="utf-8")
        except Exception:
            pass
        if os.name == "nt":
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"片语工作室启动异常:\n{_exc}\n\n详细日志已保存至 logs\\launcher_error.log",
                    "片语工作室 - 启动诊断",
                    0x10,
                )
            except Exception:
                pass
        raise
