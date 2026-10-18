# -*- coding: utf-8 -*-
"""T09 security: net allow-list, secrets, providers (mock HTTP), settings keys."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MemoryKeyring:
    def __init__(self):
        self._d = {}
        self.priority = 5

    def get_password(self, service, name):
        return self._d.get((service, name))

    def set_password(self, service, name, value):
        self._d[(service, name)] = value

    def delete_password(self, service, name):
        self._d.pop((service, name), None)


@pytest.fixture
def mem_keyring():
    from cutora.core import secrets as secrets
    backend = MemoryKeyring()
    secrets.use_backend(backend)
    yield backend
    secrets.use_backend(None)


def test_safe_json_size_and_dup():
    from cutora.core.safe_json import DuplicateKeyError, load, loads
    assert loads('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        loads("x" * 10, max_bytes=4)
    with pytest.raises(DuplicateKeyError):
        loads('{"a":1,"a":2}', reject_duplicate_keys=True)
    assert loads('{"a":1,"a":2}', reject_duplicate_keys=False)["a"] == 2


def test_net_rejects_blacklist_host():
    from cutora.core.net import NetPolicyError, assert_url_allowed
    with pytest.raises(NetPolicyError):
        assert_url_allowed("https://www.baidu.com/foo")
    with pytest.raises(NetPolicyError):
        assert_url_allowed("https://hf-mirror.com/models")
    with pytest.raises(NetPolicyError):
        assert_url_allowed("http://api.openai.com/v1")  # non-local must be https
    host = assert_url_allowed("https://api.x.ai/v1/chat/completions")
    assert host == "api.x.ai"
    assert assert_url_allowed("http://127.0.0.1:11434/api/chat") == "127.0.0.1"


def test_net_rejects_arbitrary_user_url():
    from cutora.core.net import NetPolicyError, assert_url_allowed
    with pytest.raises(NetPolicyError):
        assert_url_allowed("https://evil.example.com/steal")


def test_net_rejects_raw_githubusercontent_at_runtime():
    """Dev-period host; must not be on the product runtime allow-list."""
    from cutora.core.net import NetPolicyError, assert_url_allowed
    with pytest.raises(NetPolicyError):
        assert_url_allowed(
            "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/OFL.txt")


def test_secrets_roundtrip(mem_keyring):
    from cutora.core import secrets as secrets
    secrets.set("gemini_api_key", "AIza-test-value-not-real")
    assert secrets.get("gemini_api_key") == "AIza-test-value-not-real"
    secrets.delete("gemini_api_key")
    assert secrets.get("gemini_api_key") is None


def test_secrets_refuse_without_backend():
    from cutora.core import secrets as secrets

    class Fail:
        priority = -1

        def set_password(self, *a, **k):
            raise RuntimeError("nope")

    secrets.use_backend(Fail())
    try:
        with pytest.raises(secrets.SecretsError):
            secrets.set("openai_api_key", "sk-test")
    finally:
        secrets.use_backend(None)


def test_migrate_strips_key_fields(mem_keyring):
    from cutora.core.secrets import migrate_settings_dict, strip_secret_fields
    data = {
        "audio_fx": {"gemini_api_key": "AIza-from-json", "gemini_model": "gemini-2.0-flash"},
        "xai_api_key": "",
        "export_video_mode": "compressed",
    }
    new, count = migrate_settings_dict(data)
    assert count == 1
    assert "gemini_api_key" not in new["audio_fx"]
    assert "xai_api_key" not in new
    assert new["audio_fx"]["gemini_model"] == "gemini-2.0-flash"
    dumped = json.dumps(strip_secret_fields(new))
    assert "api_key" not in dumped
    from cutora.core import secrets as secrets
    assert secrets.get("gemini_api_key") == "AIza-from-json"


def test_log_redacts_bearer():
    from cutora.core.log import redact_text
    assert "***" in redact_text("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redact_text("Bearer sk-abcdefghijklmnopqrstuvwxyz")
    assert "***" in redact_text("https://x?key=AIzaSyDummyKeyValue12")


def test_strip_secret_fields_keeps_group_keys():
    from cutora.core.secrets import strip_secret_fields
    src = {"group_keys": ["01"], "keyword": "foo", "audio_fx": {"gemini_api_key": "x"}}
    out = strip_secret_fields(src)
    assert out["group_keys"] == ["01"]
    assert out["keyword"] == "foo"
    assert "gemini_api_key" not in out["audio_fx"]


class _ProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n:
            self.rfile.read(n)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/models") or path.endswith("/api/tags") or "models" in path:
            if "generativelanguage" in (self.headers.get("Host") or "") or path.startswith("/v1beta"):
                self._send({"models": [{"name": "models/gemini-2.0-flash"}]})
                return
            if path == "/api/tags":
                self._send({"models": [{"name": "llava"}]})
                return
            self._send({"data": [{"id": "mock-model"}]})
            return
        self._send({"error": "nope"}, 404)

    def do_POST(self):
        self._read_body()
        path = urlparse(self.path).path
        if "generateContent" in path:
            self._send({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
            return
        if path.endswith("/messages"):
            self._send({"content": [{"type": "text", "text": "OK"}]})
            return
        if path.endswith("/chat/completions") or path.endswith("/api/chat"):
            if path.endswith("/api/chat"):
                self._send({"message": {"content": "OK"}})
            else:
                self._send({"choices": [{"message": {"content": "OK"}}]})
            return
        self._send({"error": "nope"}, 404)


@pytest.fixture
def mock_http():
    httpd = HTTPServer(("127.0.0.1", 0), _ProviderHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()


def test_five_providers_chat_ok(mem_keyring, mock_http, monkeypatch):
    monkeypatch.setenv("CUTORA_NET_SKIP_AI_SWITCH", "1")
    from cutora.core import secrets as secrets
    from cutora.ai import providers as prov
    from cutora.ai import prefs as prefs

    port = mock_http
    root = "http://127.0.0.1:%d" % port
    monkeypatch.setattr(prov, "OPENAI_CHAT", root + "/v1/chat/completions")
    monkeypatch.setattr(prov, "OPENAI_MODELS", root + "/v1/models")
    monkeypatch.setattr(prov, "ANTHROPIC_CHAT", root + "/v1/messages")
    monkeypatch.setattr(prov, "ANTHROPIC_MODELS", root + "/v1/models")
    monkeypatch.setattr(prov, "GEMINI_ROOT", root + "/v1beta")
    monkeypatch.setattr(prov, "XAI_CHAT", root + "/v1/chat/completions")
    monkeypatch.setattr(prov, "XAI_MODELS", root + "/v1/models")
    monkeypatch.setattr(prefs, "get_ai_prefs", lambda: {
        "ai_network_enabled": True,
        "ai_default_provider": "gemini",
        "ai_models": {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-4-5",
            "gemini": "gemini-2.0-flash",
            "xai": "grok-2-vision-1212",
            "ollama": "llava",
        },
        "ollama_port": port,
    })
    for name in ("openai_api_key", "anthropic_api_key", "gemini_api_key", "xai_api_key"):
        secrets.set(name, "test-key-value-16ch")

    for pid in ("openai", "anthropic", "gemini", "xai", "ollama"):
        text = prov.get_provider(pid).chat([{"role": "user", "content": "回复 OK"}])
        assert "OK" in text, pid


def test_sfx_library_defaults_have_no_secret_fields():
    from cutora.audio.sfx_library import default_fx_settings
    fx = default_fx_settings()
    for k in fx:
        lk = str(k).lower()
        assert not lk.endswith("_key")
        assert not lk.endswith("_token")
        assert not lk.endswith("_secret")


def test_proc_forbids_shell():
    from cutora.core.proc import run
    with pytest.raises(ValueError):
        run("echo hi", shell=True)


def test_render_job_rejects_escaped_sidecar(tmp_path):
    from cutora.render.parallel import _load_and_validate_job
    job = tmp_path / "w0.json"
    outside = tmp_path.parent / "evil.progress"
    job.write_text(json.dumps({
        "items": [{"task": {}, "output_path": str(tmp_path / "out.mp4")}],
        "progress_path": str(outside),
        "result_path": str(tmp_path / "w0.result"),
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        _load_and_validate_job(str(job))
