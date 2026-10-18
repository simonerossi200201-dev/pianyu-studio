# -*- coding: utf-8 -*-
"""T13d handwritten stdio MCP: protocol + SDK client."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cutora.api.mcp_server import (
    API_OFF, DOCS_URI, handle_line, tool_name,
)
from cutora.core.commands import get_registry, reset_bus_for_tests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


@pytest.fixture(autouse=True)
def _reset():
    reset_bus_for_tests()
    yield
    reset_bus_for_tests()


def _rpc(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return handle_line(json.dumps(msg, ensure_ascii=False))


def test_initialize_ping_unknown_method():
    init = _rpc("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    })
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in init["result"]["capabilities"]
    assert "resources" in init["result"]["capabilities"]
    ping = _rpc("ping", msg_id=2)
    assert ping["result"] == {}
    note = handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }))
    assert note is None
    bad = _rpc("no/such", msg_id=3)
    assert bad["error"]["code"] == -32601


def test_tools_list_maps_commands_and_dangerous_hint():
    listed = _rpc("tools/list")["result"]["tools"]
    names = {t["name"] for t in listed}
    specs = list(get_registry().list())
    assert len(listed) == len(specs)
    assert "app_version" in names
    by_name = {t["name"]: t for t in listed}
    for spec in specs:
        item = by_name[tool_name(spec.name)]
        assert item["inputSchema"] == spec.params_schema
        if spec.dangerous:
            assert "confirm=true" in item["description"]


def test_tools_call_without_api_is_error():
    out = _rpc("tools/call", {"name": "app_version", "arguments": {}})
    assert out["result"]["isError"] is True
    text = out["result"]["content"][0]["text"]
    assert "请先打开片语 Studio" in text
    assert "代理接口" in text
    assert text.startswith(API_OFF) or API_OFF in text


def test_resources_read_docs_commands():
    out = _rpc("resources/read", {"uri": DOCS_URI})
    text = out["result"]["contents"][0]["text"]
    assert "app.version" in text
    assert out["result"]["contents"][0]["uri"] == DOCS_URI


def test_mcp_module_entry_stdio_roundtrip():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [VENV_PY, "-m", "cutora.api.mcp_server"],
        cwd=REPO, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    req = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t13d", "version": "0"},
        },
    }, ensure_ascii=False) + "\n"
    try:
        out, err = proc.communicate(req.encode("utf-8"), timeout=20)
    except Exception:
        proc.kill()
        raise
    assert proc.returncode == 0, err.decode("utf-8", "replace")
    line = out.decode("utf-8").splitlines()[0]
    payload = json.loads(line)
    assert payload["result"]["protocolVersion"] == "2025-06-18"
    assert b"torch" not in err.lower()


def _stdio_rpc(proc, method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line.decode("utf-8"))


def test_stdio_client_initialize_list_call_read(tmp_path, monkeypatch):
    """initialize → tools/list → tools/call(app_version) → resources/read over stdio."""
    from cutora.api.lifecycle import start_api, stop_api
    from cutora.api.token import ensure_token, rotate_token
    from cutora.core import secrets as secrets_mod
    from cutora.core.history import _save_app_settings
    from cutora.core.paths import refresh_cached_paths

    class _Mem:
        priority = 5

        def __init__(self):
            self._d = {}

        def get_password(self, service, name):
            return self._d.get((service, name))

        def set_password(self, service, name, value):
            self._d[(service, name)] = value

        def delete_password(self, service, name):
            self._d.pop((service, name), None)

    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    refresh_cached_paths()
    secrets_mod.use_backend(_Mem())
    rotate_token()
    token = ensure_token()
    srv = start_api(0)
    _save_app_settings({"api_enabled": True, "api_port": srv.port})
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["CUTORA_USER_DATA"] = str(tmp_path)
    env["CUTORA_API_TOKEN"] = token
    proc = subprocess.Popen(
        [VENV_PY, "-m", "cutora.api.mcp_server"],
        cwd=REPO, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        init = _stdio_rpc(proc, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t13d", "version": "0"},
        }, 1)
        assert init["result"]["protocolVersion"] == "2025-06-18"
        proc.stdin.write((json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n").encode("utf-8"))
        proc.stdin.flush()
        listed = _stdio_rpc(proc, "tools/list", msg_id=2)
        names = [t["name"] for t in listed["result"]["tools"]]
        assert "app_version" in names
        called = _stdio_rpc(proc, "tools/call", {
            "name": "app_version", "arguments": {},
        }, 3)
        assert called["result"].get("isError") is not True
        body = json.loads(called["result"]["content"][0]["text"])
        assert body.get("ok") is True
        assert body["result"]["version"]
        docs = _stdio_rpc(proc, "resources/read", {"uri": DOCS_URI}, 4)
        assert "app.version" in docs["result"]["contents"][0]["text"]
    finally:
        proc.stdin.close()
        proc.kill()
        proc.wait(timeout=5)
        stop_api()
        secrets_mod.use_backend(None)
        refresh_cached_paths()


SDK_PY = os.path.join(REPO, "build", "mcp-sdk-venv", "Scripts", "python.exe")


def test_sdk_client_initialize_list_call_read(tmp_path, monkeypatch):
    """Official mcp SDK client from build/mcp-sdk-venv, product python as server."""
    if not os.path.isfile(SDK_PY):
        pytest.skip("run tools/setup_dev_mcp.ps1 first")
    from cutora.api.lifecycle import start_api, stop_api
    from cutora.api.token import ensure_token, rotate_token
    from cutora.core import secrets as secrets_mod
    from cutora.core.history import _save_app_settings
    from cutora.core.paths import refresh_cached_paths

    class _Mem:
        priority = 5

        def __init__(self):
            self._d = {}

        def get_password(self, service, name):
            return self._d.get((service, name))

        def set_password(self, service, name, value):
            self._d[(service, name)] = value

        def delete_password(self, service, name):
            self._d.pop((service, name), None)

    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    refresh_cached_paths()
    secrets_mod.use_backend(_Mem())
    rotate_token()
    token = ensure_token()
    srv = start_api(0)
    _save_app_settings({"api_enabled": True, "api_port": srv.port})
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["CUTORA_USER_DATA"] = str(tmp_path)
    env["CUTORA_API_TOKEN"] = token
    env["CUTORA_PRODUCT_PY"] = VENV_PY
    try:
        proc = subprocess.run(
            [SDK_PY, os.path.join(REPO, "tools", "mcp_sdk_probe.py")],
            cwd=REPO, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        assert proc.returncode == 0, out
        assert "sdk-ok" in (proc.stdout or ""), out
    finally:
        stop_api()
        secrets_mod.use_backend(None)
        refresh_cached_paths()
