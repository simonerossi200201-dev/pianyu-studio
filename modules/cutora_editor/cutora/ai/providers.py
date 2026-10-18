# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""AI provider adapters. Hosts are hardcoded; callers cannot pass arbitrary URLs."""
from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Optional, Protocol, Sequence

from cutora.ai import prefs as ai_prefs
from cutora.core import secrets as secrets
from cutora.core.log import get_logger
from cutora.core.net import NetPolicyError, request

logger = get_logger("cutora.ai.providers")

# Hardcoded API roots. Never accept a caller-supplied host.
OPENAI_CHAT = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELS = "https://api.openai.com/v1/models"
ANTHROPIC_CHAT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS = "https://api.anthropic.com/v1/models"
GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta"
XAI_CHAT = "https://api.x.ai/v1/chat/completions"
XAI_MODELS = "https://api.x.ai/v1/models"

FALLBACK_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4o"],
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-5"],
    "gemini": ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.0-flash-lite"],
    "xai": ["grok-2-vision-1212", "grok-2-latest", "grok-3"],
    "ollama": ["llava", "qwen2.5vl", "llama3.2-vision", "gemma3"],
}


class Provider(Protocol):
    id: str
    name: str
    supports_images: bool

    def chat(
        self,
        messages: Sequence[dict],
        images: Optional[Iterable[bytes]] = None,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> str: ...

    def list_models(self) -> list[str]: ...


def _b64(frames: Optional[Iterable[bytes]]) -> list[str]:
    return [base64.b64encode(f).decode("ascii") for f in (frames or []) if f]


def _text_of(messages: Sequence[dict]) -> str:
    parts = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
    return "\n".join(p for p in parts if p)


def _json_body(raw: bytes) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        raise RuntimeError("提供商返回无法解析") from exc
    return obj if isinstance(obj, dict) else {}


class _Base:
    id: str = ""
    name: str = ""
    supports_images: bool = False
    secret_name: str = ""

    def _key(self) -> str:
        if not self.secret_name:
            return ""
        return secrets.get(self.secret_name) or ""

    def _model(self, model: Optional[str]) -> str:
        if model and str(model).strip():
            return str(model).strip()
        return ai_prefs.get_ai_prefs()["ai_models"].get(self.id) or FALLBACK_MODELS[self.id][0]


class OpenAIProvider(_Base):
    id = "openai"
    name = "OpenAI"
    supports_images = True
    secret_name = "openai_api_key"

    def chat(self, messages, images=None, model=None, timeout=60) -> str:
        key = self._key()
        if not key:
            raise RuntimeError("未配置 OpenAI API Key")
        frames = _b64(images)
        oai_messages = []
        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content") or ""
            if frames and role == "user":
                blocks: list[dict] = [{"type": "text", "text": content if isinstance(content, str) else _text_of([msg])}]
                for b64 in frames:
                    blocks.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}})
                oai_messages.append({"role": role, "content": blocks})
                frames = []
            else:
                oai_messages.append({"role": role, "content": content if isinstance(content, str) else _text_of([msg])})
        status, raw, _hdrs = request(
            OPENAI_CHAT,
            method="POST",
            headers={"Authorization": "Bearer %s" % key},
            json_body={"model": self._model(model), "messages": oai_messages, "temperature": 0.2},
            timeout=timeout,
        )
        if status >= 400:
            raise RuntimeError("OpenAI HTTP %s" % status)
        data = _json_body(raw)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise RuntimeError("OpenAI 返回无法解析") from exc

    def list_models(self) -> list[str]:
        key = self._key()
        if not key:
            return list(FALLBACK_MODELS["openai"])
        try:
            _status, raw, _h = request(
                OPENAI_MODELS,
                method="GET",
                headers={"Authorization": "Bearer %s" % key},
                timeout=30,
                purpose="ai",
            )
            data = _json_body(raw)
            ids = [str(x.get("id")) for x in (data.get("data") or []) if x.get("id")]
            return ids or list(FALLBACK_MODELS["openai"])
        except (NetPolicyError, Exception):
            return list(FALLBACK_MODELS["openai"])


class AnthropicProvider(_Base):
    id = "anthropic"
    name = "Anthropic"
    supports_images = True
    secret_name = "anthropic_api_key"

    def chat(self, messages, images=None, model=None, timeout=60) -> str:
        key = self._key()
        if not key:
            raise RuntimeError("未配置 Anthropic API Key")
        frames = _b64(images)
        body_messages = []
        system = None
        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content") or ""
            if role == "system":
                system = content if isinstance(content, str) else _text_of([msg])
                continue
            if role not in ("user", "assistant"):
                role = "user"
            if frames and role == "user":
                blocks: list[dict] = [{"type": "text", "text": content if isinstance(content, str) else _text_of([msg])}]
                for b64 in frames:
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    })
                body_messages.append({"role": role, "content": blocks})
                frames = []
            else:
                body_messages.append({"role": role, "content": content if isinstance(content, str) else _text_of([msg])})
        payload: dict[str, Any] = {
            "model": self._model(model),
            "max_tokens": 1024,
            "messages": body_messages,
        }
        if system:
            payload["system"] = system
        _status, raw, _h = request(
            ANTHROPIC_CHAT,
            method="POST",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            json_body=payload,
            timeout=timeout,
        )
        data = _json_body(raw)
        try:
            return str(data["content"][0]["text"] or "")
        except Exception as exc:
            raise RuntimeError("Anthropic 返回无法解析") from exc

    def list_models(self) -> list[str]:
        key = self._key()
        if not key:
            return list(FALLBACK_MODELS["anthropic"])
        try:
            _status, raw, _h = request(
                ANTHROPIC_MODELS,
                method="GET",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                timeout=30,
            )
            data = _json_body(raw)
            ids = [str(x.get("id")) for x in (data.get("data") or []) if x.get("id")]
            return ids or list(FALLBACK_MODELS["anthropic"])
        except (NetPolicyError, Exception):
            return list(FALLBACK_MODELS["anthropic"])


class GeminiProvider(_Base):
    id = "gemini"
    name = "Gemini"
    supports_images = True
    secret_name = "gemini_api_key"

    def chat(self, messages, images=None, model=None, timeout=60) -> str:
        key = self._key()
        if not key:
            raise RuntimeError("未配置 Gemini API Key")
        model_id = self._model(model)
        url = "%s/models/%s:generateContent" % (GEMINI_ROOT, model_id)
        parts: list[dict] = [{"text": _text_of(messages)}]
        for b64 in _b64(images):
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }
        _status, raw, _h = request(
            url, method="POST", json_body=payload, timeout=timeout,
            headers={"x-goog-api-key": key},
        )
        data = _json_body(raw)
        try:
            return str(data["candidates"][0]["content"]["parts"][0]["text"] or "")
        except Exception as exc:
            raise RuntimeError("Gemini 返回无法解析") from exc

    def list_models(self) -> list[str]:
        key = self._key()
        if not key:
            return list(FALLBACK_MODELS["gemini"])
        try:
            url = "%s/models" % GEMINI_ROOT
            _status, raw, _h = request(
                url, method="GET", timeout=30, headers={"x-goog-api-key": key},
            )
            data = _json_body(raw)
            ids = []
            for item in data.get("models") or []:
                name = str(item.get("name") or "")
                if name.startswith("models/"):
                    name = name.split("/", 1)[1]
                if name:
                    ids.append(name)
            return ids or list(FALLBACK_MODELS["gemini"])
        except (NetPolicyError, Exception):
            return list(FALLBACK_MODELS["gemini"])


class XAIProvider(_Base):
    id = "xai"
    name = "SpaceXAI / xAI"
    supports_images = True
    secret_name = "xai_api_key"

    def chat(self, messages, images=None, model=None, timeout=60) -> str:
        key = self._key()
        if not key:
            raise RuntimeError("未配置 SpaceXAI / xAI API Key")
        frames = _b64(images)
        oai_messages = []
        for msg in messages:
            role = str(msg.get("role") or "user")
            content = msg.get("content") or ""
            if frames and role == "user":
                blocks: list[dict] = [{"type": "text", "text": content if isinstance(content, str) else _text_of([msg])}]
                for b64 in frames:
                    blocks.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}})
                oai_messages.append({"role": role, "content": blocks})
                frames = []
            else:
                oai_messages.append({"role": role, "content": content if isinstance(content, str) else _text_of([msg])})
        _status, raw, _h = request(
            XAI_CHAT,
            method="POST",
            headers={"Authorization": "Bearer %s" % key},
            json_body={"model": self._model(model), "temperature": 0.2, "messages": oai_messages},
            timeout=timeout,
        )
        data = _json_body(raw)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise RuntimeError("SpaceXAI 返回无法解析") from exc

    def list_models(self) -> list[str]:
        key = self._key()
        if not key:
            return list(FALLBACK_MODELS["xai"])
        try:
            _status, raw, _h = request(
                XAI_MODELS,
                method="GET",
                headers={"Authorization": "Bearer %s" % key},
                timeout=30,
            )
            data = _json_body(raw)
            ids = [str(x.get("id")) for x in (data.get("data") or []) if x.get("id")]
            return ids or list(FALLBACK_MODELS["xai"])
        except (NetPolicyError, Exception):
            return list(FALLBACK_MODELS["xai"])


class OllamaProvider(_Base):
    id = "ollama"
    name = "Ollama（本机）"
    supports_images = True
    secret_name = ""

    def _root(self) -> str:
        port = int(ai_prefs.get_ai_prefs().get("ollama_port") or 11434)
        if port < 1 or port > 65535:
            port = 11434
        return "http://127.0.0.1:%d" % port

    def chat(self, messages, images=None, model=None, timeout=60) -> str:
        frames = _b64(images)
        payload_messages = []
        for msg in messages:
            role = str(msg.get("role") or "user")
            item: dict[str, Any] = {
                "role": role,
                "content": msg.get("content") if isinstance(msg.get("content"), str) else _text_of([msg]),
            }
            if frames and role == "user":
                item["images"] = frames
                frames = []
            payload_messages.append(item)
        url = self._root() + "/api/chat"
        _status, raw, _h = request(
            url,
            method="POST",
            json_body={
                "model": self._model(model),
                "stream": False,
                "messages": payload_messages,
                "options": {"temperature": 0.2},
            },
            timeout=timeout,
        )
        data = _json_body(raw)
        msg = data.get("message") or {}
        return str(msg.get("content") or data.get("response") or "")

    def list_models(self) -> list[str]:
        try:
            _status, raw, _h = request(self._root() + "/api/tags", method="GET", timeout=10)
            data = _json_body(raw)
            names = []
            for item in data.get("models") or []:
                name = item.get("name") or item.get("model")
                if name:
                    names.append(str(name))
            return names or list(FALLBACK_MODELS["ollama"])
        except (NetPolicyError, Exception):
            return list(FALLBACK_MODELS["ollama"])


_REGISTRY = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "xai": XAIProvider,
    "spacexai": XAIProvider,
    "grok": XAIProvider,
    "ollama": OllamaProvider,
}


def get_provider(provider_id: str) -> Provider:
    """Return a provider instance. Unknown ids fall back to Gemini."""
    cls = _REGISTRY.get(str(provider_id or "").lower().strip()) or GeminiProvider
    return cls()


def all_providers() -> list[Provider]:
    """Canonical five providers (unique ids)."""
    return [
        OpenAIProvider(),
        AnthropicProvider(),
        GeminiProvider(),
        XAIProvider(),
        OllamaProvider(),
    ]
