# -*- coding: utf-8 -*-
"""冒烟：把出包目录拷到临时目录，建全新 venv，起 Orante，打接口。

任何一步失败 → 打印失败在哪一步 + 最后 40 行日志，exit 1，不出 zip。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT_BLOCKS = ("desc", "camera", "tail", "problem", "expand", "refill_clause")
DEPS = ("fastapi", "uvicorn", "playwright", "PIL", "webview", "requests", "cryptography")


def _free_port(start: int = 9410) -> int:
    for p in range(start, start + 80):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit("找不到冒烟用的空闲端口")


def _get(url: str, timeout: float = 5.0) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)
    text = raw.decode("utf-8", "replace")
    try:
        return code, json.loads(text)
    except Exception:
        return code, text


def _wait_up(url: str, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, body = _get(url, timeout=2.0)
        if code == 200:
            return True
        time.sleep(0.4)
    return False


def _tail(path: Path, n: int = 40) -> str:
    if not path.is_file():
        return "（没有日志文件）"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "（日志读不出来）"
    return "\n".join(lines[-n:])


def _fail(step: str, log_path: Path | None, extra: str = "") -> int:
    print(f"冒烟失败：{step}")
    if extra:
        print(extra)
    if log_path:
        print("—— 最后 40 行日志 ——")
        print(_tail(log_path, 40))
    return 1


def smoke(src_pkg: Path) -> int:
    src_pkg = src_pkg.resolve()
    if not src_pkg.is_dir():
        print(f"找不到出包目录 {src_pkg}")
        return 1
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    tmp = Path(tempfile.gettempdir()) / f"orante-smoke-{stamp}"
    log_path = tmp / "smoke.log"
    proc = None
    try:
        print(f"冒烟临时目录 {tmp}")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src_pkg, tmp)

        py = tmp / ".venv" / "Scripts" / "python.exe"
        print("建全新 .venv …")
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(tmp / ".venv")],
            cwd=str(tmp), capture_output=True, text=True,
        )
        if r.returncode != 0 or not py.is_file():
            return _fail("建 venv", log_path, (r.stderr or r.stdout or "")[-2000:])

        print("pip install -r requirements.txt …")
        r = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(tmp), capture_output=True, text=True,
        )
        log_path.write_text(
            (r.stdout or "") + "\n" + (r.stderr or ""), encoding="utf-8"
        )
        if r.returncode != 0:
            return _fail("pip install", log_path)

        # 依赖自检
        chk = ";".join(f"import {n}" for n in DEPS)
        r = subprocess.run([str(py), "-c", chk], cwd=str(tmp),
                           capture_output=True, text=True)
        if r.returncode != 0:
            return _fail("依赖 import", log_path, r.stderr or r.stdout or "")

        port = _free_port()
        data_dir = tmp / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["ORANTE_DATA"] = str(data_dir)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # 不要去碰开发机正在用的 9400
        print(f"启动 Orante --browser --port {port} …")
        log_f = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [str(py), "-m", "orante", "--browser", "--port", str(port)],
            cwd=str(tmp), env=env,
            stdout=log_f, stderr=subprocess.STDOUT,
        )
        health = f"http://127.0.0.1:{port}/api/health"
        if not _wait_up(health, 60):
            # 兼容还没加 /api/health 的瞬间：试 /api/state
            if not _wait_up(f"http://127.0.0.1:{port}/api/state?probe=0", 5):
                log_f.close()
                return _fail("等端口通（60 秒）", log_path)

        code, body = _get(health)
        if code != 200:
            code, body = _get(f"http://127.0.0.1:{port}/api/state?probe=0")
        if code != 200:
            return _fail("健康/状态接口 200", log_path, f"{code} {body!r}")

        code, body = _get(f"http://127.0.0.1:{port}/api/accounts")
        if code != 200 or not isinstance(body, dict):
            return _fail("GET /api/accounts", log_path, f"{code} {body!r}")
        acc = body.get("accounts")
        if acc:
            return _fail("GET /api/accounts 应为空列表", log_path, repr(acc))

        code, body = _get(f"http://127.0.0.1:{port}/api/config")
        if code != 200 or not isinstance(body, dict):
            return _fail("GET /api/config", log_path, f"{code} {body!r}")
        cfg = body.get("settings") if isinstance(body.get("settings"), dict) else body
        key = cfg.get("gemini.api_key", "")
        if key:
            return _fail("gemini.api_key 应为空", log_path, repr(key))
        ws = str(cfg.get("workspace.root") or "")
        if "D:\\" in ws or "D:/" in ws:
            return _fail("workspace.root 含 D:\\", log_path, ws)
        if cfg.get("setup.done"):
            return _fail("setup.done 不应为真", log_path, repr(cfg.get("setup.done")))

        code, body = _get(f"http://127.0.0.1:{port}/api/prompts")
        if code != 200 or not isinstance(body, dict):
            return _fail("GET /api/prompts", log_path, f"{code} {body!r}")
        miss = [k for k in PROMPT_BLOCKS if k not in body or body.get(k) in (None, "", {}, [])]
        if miss:
            return _fail("提示词六块", log_path, f"缺 {miss} keys={list(body)}")

        print("冒烟通过")
        return 0
    except Exception:
        return _fail("未捕获异常", log_path, traceback.format_exc())
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", help="出包目录（已按白名单拷好的那份）")
    a = ap.parse_args(argv)
    return smoke(Path(a.dest))


if __name__ == "__main__":
    sys.exit(main())
