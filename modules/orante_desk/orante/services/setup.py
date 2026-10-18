# -*- coding: utf-8 -*-
"""第一次运行向导：环境自检、工作区、账号、多模态 Key。

向导不碰描述语（prompt_defaults.json / coverage_library.json 原样打包）。
"""
from __future__ import annotations

import importlib
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .. import accounts, config, db, paths
from ..engines import chrome
from ..errors import OranteError

DEPS = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("playwright", "playwright"),
    ("PIL", "PIL"),
    ("webview", "webview"),
    ("requests", "requests"),
    ("cryptography", "cryptography"),
)


def legacy_available() -> bool:
    return paths.find_legacy_root() is not None


def _truthy(v) -> bool:
    if v is True or v == 1:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
        return True
    return False


def state() -> dict:
    row = db.one("SELECT COUNT(*) n FROM accounts")
    n = int((row or {}).get("n") or 0)
    done = _truthy(config.get("setup.done"))
    # 老装机补登记：向导是 64 号才加的，在它之前配好的机器 settings 里没有
    # setup.done，但 accounts 里明明有账号。不补的话每次启动都会弹向导，
    # 而且默认落在「导入 .orpreset」那一步——老用户根本没有那个文件。
    # 有账号 = 这台机器早就配过了，直接记上，只补这一次。
    if n > 0 and not done:
        config.set("setup.done", True)
        done = True
    return {
        "need_setup": not done,
        "setup_done": done,
        "accounts_n": n,
        "legacy_available": legacy_available(),
        "workspace_root": str(config.get("workspace.root") or paths.DEFAULT_WORKSPACE),
        "ui_port": int(config.get("ui.port") or 9400),
        "flow_model": config.get("engine.flow.model") or "",
        "allow_paid": bool(config.get("flow.allow_paid")),
        "gemini_key_set": bool(str(config.get("gemini.api_key") or "").strip()),
    }


def _port_status(port: int) -> tuple[bool, str]:
    """端口空闲，或占用者就是本进程的 Orante → 算过。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return True, "free"
    except OSError:
        pass
    finally:
        s.close()
    # 已经被占：是不是我们自己？
    for path in (f"/api/health", f"/api/state?probe=0"):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{int(port)}{path}", timeout=1.2
            ) as r:
                import json
                data = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get("ok") and (
                "version" in data or data.get("status") == "ok"
            ):
                return True, "self"
        except Exception:
            continue
    return False, "busy"


def env_check(port: int | None = None, workspace: str | None = None) -> dict:
    """五项环境自检。不过时 hint 是给人看的。"""
    checks = []

    py_ok = sys.version_info >= (3, 10)
    checks.append({
        "id": "python",
        "ok": py_ok,
        "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "hint": "" if py_ok else "去 python.org 装 3.10 以上，安装时勾上 Add to PATH",
    })

    chrome_path = ""
    chrome_ok = False
    chrome_hint = "请先装 Google Chrome；装在别处的话，设置里填 chrome 路径"
    try:
        chrome_path = chrome.chrome_exe()
        chrome_ok = bool(chrome_path)
        chrome_hint = ""
    except OranteError as e:
        chrome_hint = e.hint or str(e) or chrome_hint
    except Exception:
        pass
    checks.append({
        "id": "chrome",
        "ok": chrome_ok,
        "detail": chrome_path,
        "hint": chrome_hint if not chrome_ok else "",
    })

    port = int(port if port is not None else (config.get("ui.port") or 9400))
    p_ok, p_why = _port_status(port)
    checks.append({
        "id": "port",
        "ok": p_ok,
        "detail": str(port),
        "why": p_why,
        "hint": "" if p_ok else f"{port} 被别的程序占了，这里可以改成 {port + 1}",
    })

    ws = Path(workspace or config.get("workspace.root") or paths.DEFAULT_WORKSPACE)
    ws_ok = False
    ws_hint = "这个盘写不进去，换一个目录"
    try:
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".orante-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ws_ok = True
        ws_hint = ""
    except Exception as e:
        ws_hint = f"这个盘写不进去，换一个目录（{e}）"
    checks.append({
        "id": "workspace",
        "ok": ws_ok,
        "detail": str(ws),
        "hint": ws_hint,
    })

    missing = []
    for label, mod in DEPS:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(label)
    dep_ok = not missing
    checks.append({
        "id": "deps",
        "ok": dep_ok,
        "detail": ",".join(missing),
        "hint": "" if dep_ok else "关掉重新双击 Orante2.bat，它会自己装",
    })

    # 规格 95：ffmpeg。软项——没有也能进下一步，但尾帧接龙 / 锚点接片要靠它，自检页给一键装
    from . import checks as chk
    fs = chk.ffmpeg_status()
    checks.append({
        "id": "ffmpeg",
        "ok": bool(fs.get("ok")),
        "soft": True,
        "detail": (fs.get("path") or "") + ((" · " + fs.get("how")) if fs.get("how") else ""),
        "hint": "" if fs.get("ok") else "尾帧接龙 / 锚点接片要用它抽尾帧。点「一键安装」（约 30 MB），或把 ffmpeg.exe 放进 Orante\\bin",
        "installable": not bool(fs.get("ok")),
    })

    return {"checks": checks, "ok": all(c["ok"] for c in checks if not c.get("soft")),
            "port": port, "workspace": str(ws)}


def complete(workspace: str | None = None, port: int | None = None,
             gemini_key: str | None = None) -> dict:
    if workspace:
        config.set("workspace.root", str(workspace))
    if port is not None:
        config.set("ui.port", int(port))
    if gemini_key is not None:
        config.set("gemini.api_key", str(gemini_key).strip())
    config.set("setup.done", True)
    return state()


def test_gemini(api_key: str, model: str | None = None) -> dict:
    """真发一次最小请求。不把 Key 写进 settings（点完成才写）。"""
    key = (api_key or "").strip()
    if not key:
        raise OranteError("还没填 Gemini API Key", "去 aistudio.google.com 免费申请")
    from .gemini import Gemini
    g = Gemini(api_key=key, model=model, retries=1)
    try:
        text = g.ask_text("Reply with the single word OK.", temperature=0)
    except OranteError:
        raise
    except Exception as e:
        raise OranteError("Gemini 请求失败", str(e)[:200])
    return {"ok": True, "reply": (text or "")[:80], "model": g.model}
