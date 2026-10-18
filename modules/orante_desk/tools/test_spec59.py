# -*- coding: utf-8 -*-
"""规格 59：空白窗口 / 单实例守卫 / 窗口等后端。先设 ORANTE_DATA。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = Path(tempfile.mkdtemp(prefix="orante-test-59-"))
os.environ["ORANTE_DATA"] = str(TEST_DATA)
sys.path.insert(0, str(ROOT))

from orante import db, paths  # noqa: E402
from orante import main as mainmod  # noqa: E402

db.init()
paths.ensure_dirs()

ORANTE_STATE = {
    "ok": True,
    "version": "2.0.0-dev",
    "accounts": [],
    "tasks": [],
    "settings": {},
    "jobs": [],
    "root": str(ROOT),
    "flow_models": [],
}


def _quiet(monkeypatch):
    monkeypatch.setattr(mainmod, "_hook_stdio", lambda: None)
    monkeypatch.setattr(mainmod, "_set_app_user_model_id", lambda: None)


def _http(payload, delay_s=0.0, status=200):
    """起一个只回答 /api/state 的小 HTTP 服务，返回 (port, stop)。"""
    t0 = [None]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            import time
            if t0[0] is None:
                t0[0] = time.time()
            if delay_s and (time.time() - t0[0]) < delay_s:
                self.send_error(503)
                return
            if self.path.startswith("/api/state") or self.path.startswith("/api/ping"):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    th = Thread(target=httpd.serve_forever, daemon=True)
    th.start()

    def stop():
        httpd.shutdown()
        httpd.server_close()

    return port, stop


def test_node_bootstrap_retry_and_timeout():
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "test_spec59_boot.js")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, f"node 失败\n{r.stdout}\n{r.stderr}"
    out = r.stdout or ""
    assert "attempt=4" in out
    assert "connecting then ok" in out
    assert "点重试" in out


def test_overlay_in_index_and_css():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "css" / "app.css").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "js" / "app.js").read_text(encoding="utf-8")
    store = (ROOT / "ui" / "js" / "store.js").read_text(encoding="utf-8")
    assert "正在连接后端…（第" in html
    assert "连不上后端" in html
    assert "重试" in html
    assert "打开日志文件夹" in html
    assert "复制诊断信息" in html
    assert "boot-mask" in css
    assert "await bootstrap()" in app
    assert "await refreshState();" not in app.split("app.mount")[-1]
    assert "window.bootstrap" in store
    assert "BOOT_TIMEOUT_MS" in store
    # 起动不要被 i18n 这一下卡住
    assert "store.i18n = await (await fetch('/api/i18n/zh'))" not in app


def test_js_syntax():
    for rel in ("ui/js/app.js", "ui/js/store.js", "ui/js/api.js"):
        r = subprocess.run(
            ["node", "--check", str(ROOT / rel)],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert r.returncode == 0, f"{rel}\n{r.stderr}"


def test_classify_free_orante_other():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert mainmod.classify_port(free_port, retries=1) == "free"

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        assert mainmod.classify_port(busy, retries=1, sleep=lambda _x: None) == "other"
    finally:
        holder.close()

    port, stop = _http(ORANTE_STATE)
    try:
        assert mainmod.classify_port(port, retries=3, sleep=lambda _x: None) == "orante"
    finally:
        stop()


def test_main_reuse_does_not_bind(monkeypatch):
    _quiet(monkeypatch)
    called = {"serve": False, "open_ui": False, "bring": False}

    monkeypatch.setattr(mainmod, "classify_port", lambda *a, **k: "orante")
    monkeypatch.setattr(mainmod, "serve", lambda *a, **k: called.__setitem__("serve", True))
    monkeypatch.setattr(mainmod, "open_ui", lambda *a, **k: called.__setitem__("open_ui", True) or 0)
    monkeypatch.setattr(
        mainmod, "bring_existing_orante_window",
        lambda: called.__setitem__("bring", True) or True,
    )
    with patch.object(mainmod.webbrowser, "open") as wb:
        rc = mainmod.main([])
    assert rc == 0
    assert called["bring"] is True
    assert called["serve"] is False
    assert called["open_ui"] is False
    wb.assert_not_called()


def test_main_occupied_shows_window_error(monkeypatch):
    _quiet(monkeypatch)
    seen = {}

    def show(url, error="", browser=False):
        seen["url"] = url
        seen["error"] = error
        seen["browser"] = browser
        seen["html"] = mainmod.boot_error_html(url, error=error)
        return 1

    monkeypatch.setattr(mainmod, "classify_port", lambda *a, **k: "other")
    monkeypatch.setattr(mainmod, "show_boot_error_window", show)

    def boom(*a, **k):
        raise AssertionError("occupied 不应再 bind/serve")

    monkeypatch.setattr(mainmod, "serve", boom)
    monkeypatch.setattr(mainmod, "open_ui", boom)
    rc = mainmod.main(["--port", "9400"])
    assert rc == 1
    assert "被别的程序占用" in seen["error"]
    html = seen["html"]
    assert "连不上后端" in html
    assert "重试" in html
    assert "打开日志文件夹" in html
    assert "复制诊断信息" in html


def test_load_url_after_state_200():
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += float(s)

    def fetch(url, timeout=1.0):
        if t[0] < 2:
            return None
        return dict(ORANTE_STATE)

    class W:
        def __init__(self):
            self.urls = []
            self.htmls = []

        def load_url(self, u):
            self.urls.append((t[0], u))

        def load_html(self, h):
            self.htmls.append((t[0], h))

    w = W()
    ok = mainmod.load_when_ready(
        w, "http://127.0.0.1:9400/", fetch=fetch, sleep=sleep, clock=clock,
    )
    assert ok is True
    assert w.urls, "load_url 没被调用"
    assert w.urls[0][0] >= 2, w.urls
    assert w.urls[0][1] == "http://127.0.0.1:9400/"
    assert not w.htmls


def test_load_timeout_goes_to_error_page():
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += float(s)

    class W:
        def __init__(self):
            self.urls = []
            self.htmls = []

        def load_url(self, u):
            self.urls.append(u)

        def load_html(self, h):
            self.htmls.append(h)

    w = W()
    ok = mainmod.load_when_ready(
        w, "http://127.0.0.1:9400/",
        timeout=20.0,
        fetch=lambda *a, **k: None,
        sleep=sleep, clock=clock,
    )
    assert ok is False
    assert not w.urls
    assert w.htmls
    html = w.htmls[0]
    assert "连不上后端" in html
    assert "重试" in html
    assert "打开日志文件夹" in html
    assert "复制诊断信息" in html
    assert t[0] >= 20.0


def test_boot_error_html_has_buttons():
    html = mainmod.boot_error_html("http://127.0.0.1:9400/", error="x")
    assert "连不上后端 http://127.0.0.1:9400" in html
    assert "重试" in html and "打开日志文件夹" in html and "复制诊断信息" in html
    assert "bootRetry" in html
    # 重试先探 /api/state，成功才跳走；一上来 location.href 会变成空壳
    assert "api/ping" in html
    assert html.find("fetch(") < html.find("location.href = BOOT_URL")


def test_debug_bat_and_launcher():
    bat = (ROOT / "tools" / "13-Orante-Debug.bat").read_text(encoding="utf-8", errors="replace")
    launcher = (ROOT / "Orante2.bat").read_text(encoding="utf-8", errors="replace")
    assert "探端口 9400" in bat
    assert "9400 已经被占用" in bat
    assert "13-Orante-Debug.bat" in launcher
    main_src = (ROOT / "orante" / "main.py").read_text(encoding="utf-8")
    assert "faulthandler" in main_src
    assert "while not _port_free(port):" not in main_src
    assert "Orante 已经在运行" in main_src
    assert "/api/system/open-logs" in (ROOT / "orante" / "server.py").read_text(encoding="utf-8")


def test_is_orante_state():
    assert mainmod.is_orante_state(ORANTE_STATE) is True
    assert mainmod.is_orante_state({"ok": True, "version": "x"}) is False
    assert mainmod.is_orante_state(None) is False
    assert mainmod.is_orante_state("no") is False
