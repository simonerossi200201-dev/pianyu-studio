# -*- coding: utf-8 -*-
"""Local API: bind, auth, Host/Origin, confirm, command list."""
from __future__ import annotations

import http.client
import json
import socket

import pytest

from cutora.api import DEFAULT_PORT
from cutora.api.lifecycle import get_server, start_api, stop_api
from cutora.api.security import host_header_ok, loopback_host
from cutora.api.token import ensure_token, rotate_token
from cutora.core import secrets as secrets_mod
from cutora.core.commands import reset_bus_for_tests


class _MemKeyring:
    priority = 5

    def __init__(self):
        self._d = {}

    def get_password(self, service, name):
        return self._d.get((service, name))

    def set_password(self, service, name, value):
        self._d[(service, name)] = value

    def delete_password(self, service, name):
        self._d.pop((service, name), None)


@pytest.fixture
def api_server(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    secrets_mod.use_backend(_MemKeyring())
    reset_bus_for_tests()
    rotate_token()
    srv = start_api(0)
    try:
        yield srv
    finally:
        stop_api()
        secrets_mod.use_backend(None)
        reset_bus_for_tests()


def _call(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection(loopback_host(), int(port), timeout=8)
    hdrs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    if host:
        hdrs["Host"] = host
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = {"_raw": raw.decode("utf-8", "replace")}
    return resp.status, data, dict(resp.headers)


def _auth(port):
    tok = ensure_token()
    return {"Authorization": "Bearer " + tok}


def test_loopback_bind_and_default_port():
    assert loopback_host() == "127.0.0.1"
    assert DEFAULT_PORT == 47321
    assert host_header_ok("127.0.0.1:47321", 47321)
    assert host_header_ok("localhost:47321", 47321)
    assert not host_header_ok("example.com", 47321)


def test_no_token_401(api_server):
    st, data, _ = _call(api_server.port, "GET", "/v1/commands")
    assert st == 401
    assert data.get("ok") is False


def test_bad_host_421(api_server):
    st, data, _ = _call(
        api_server.port, "GET", "/v1/commands",
        headers=_auth(api_server.port),
        host="evil.example",
    )
    assert st == 421


def test_origin_403(api_server):
    hdrs = _auth(api_server.port)
    hdrs["Origin"] = "http://evil.example"
    st, data, _ = _call(
        api_server.port, "GET", "/v1/commands", headers=hdrs)
    assert st == 403


def test_list_commands(api_server):
    st, data, hdrs = _call(
        api_server.port, "GET", "/v1/commands",
        headers=_auth(api_server.port))
    assert st == 200
    assert data["count"] >= 45
    assert "Access-Control-Allow-Origin" not in {k.title(): v for k, v in hdrs.items()}
    for item in data["commands"]:
        assert len(item["doc_zh"]) >= 40


def test_app_version(api_server):
    st, data, _ = _call(
        api_server.port, "POST", "/v1/commands/app.version",
        body={}, headers=_auth(api_server.port))
    assert st == 200
    assert data["ok"] is True
    assert data["result"]["app"] == "CUTORA"


def test_dangerous_without_confirm_409(api_server):
    st, data, _ = _call(
        api_server.port, "POST", "/v1/commands/media.remove",
        body={"ids": [0]}, headers=_auth(api_server.port))
    assert st == 409
    assert data.get("code") == "need_confirm"


def test_docs_and_openapi(api_server):
    st, data, _ = _call(
        api_server.port, "GET", "/openapi.json",
        headers=_auth(api_server.port))
    assert st == 200
    assert data.get("openapi", "").startswith("3.1")
    conn = http.client.HTTPConnection(loopback_host(), api_server.port, timeout=8)
    conn.request("GET", "/docs", headers=_auth(api_server.port))
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    conn.close()
    assert resp.status == 200
    assert "text/html" in (resp.getheader("Content-Type") or "")
    assert "片语 Studio" in body
    assert "新建工程" in body
    from cutora.api.openapi import _DOCS_JS
    js_lines = _DOCS_JS.splitlines()
    assert len(js_lines) <= 80, "inline JS is %d lines" % len(js_lines)


def test_lan_ip_refused(api_server):
    lan = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                lan.append(ip)
    except Exception:
        lan = []
    if not lan:
        pytest.skip("no non-loopback IPv4")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect((lan[0], int(api_server.port)))
        connected = True
    except OSError:
        connected = False
    finally:
        sock.close()
    assert connected is False


def test_stop_releases_port(api_server):
    port = api_server.port
    stop_api()
    assert get_server() is None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect((loopback_host(), int(port)))
        alive = True
    except OSError:
        alive = False
    finally:
        sock.close()
    assert alive is False
