# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""AI provider preferences (no secrets). Stored in app_settings.json."""
from __future__ import annotations

from cutora.core import secrets as secrets
from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.log import get_logger

logger = get_logger("cutora.ai.prefs")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.0-flash",
    "xai": "grok-2-vision-1212",
    "ollama": "llava",
}

DEFAULT_PREFS = {
    "ai_network_enabled": False,
    "ai_default_provider": "gemini",
    "ai_models": dict(DEFAULT_MODELS),
    "ollama_port": 11434,
}


def _normalize(raw: dict) -> dict:
    prefs = dict(DEFAULT_PREFS)
    prefs["ai_network_enabled"] = bool(raw.get("ai_network_enabled", False))
    provider = str(raw.get("ai_default_provider") or "gemini").lower().strip()
    if provider in DEFAULT_MODELS:
        prefs["ai_default_provider"] = provider
    models = dict(DEFAULT_MODELS)
    incoming = raw.get("ai_models") if isinstance(raw.get("ai_models"), dict) else {}
    for key, fallback in DEFAULT_MODELS.items():
        val = incoming.get(key) or raw.get("%s_model" % key) or fallback
        models[key] = str(val).strip() or fallback
    prefs["ai_models"] = models
    try:
        port = int(raw.get("ollama_port") or 11434)
    except Exception:
        port = 11434
    if port < 1 or port > 65535:
        port = 11434
    prefs["ollama_port"] = port
    return prefs


def get_ai_prefs() -> dict:
    """Load AI prefs from app settings (secret fields already stripped)."""
    data = _load_app_settings() or {}
    return _normalize(data)


def save_ai_prefs(prefs: dict) -> None:
    """Persist AI prefs. Secret fields are never written."""
    data = _load_app_settings() or {}
    norm = _normalize(prefs)
    data["ai_network_enabled"] = bool(norm["ai_network_enabled"])
    data["ai_default_provider"] = norm["ai_default_provider"]
    data["ai_models"] = dict(norm["ai_models"])
    data["ollama_port"] = int(norm["ollama_port"])
    _save_app_settings(data)


def migrate_app_settings() -> int:
    """Move JSON secrets into keyring and drop ``*_key`` fields. Returns count."""
    import os
    from cutora.core import safe_json
    from cutora.core.history import _get_app_settings_path

    path = _get_app_settings_path()
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = safe_json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    if not data:
        return 0
    # Legacy ollama_host → port (host is always loopback).
    fx = data.get("audio_fx") if isinstance(data.get("audio_fx"), dict) else {}
    host = str((fx or {}).get("ollama_host") or data.get("ollama_host") or "")
    if host and "ollama_port" not in data:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(host if "://" in host else "http://%s" % host)
            if parsed.port:
                data["ollama_port"] = int(parsed.port)
        except Exception:
            pass
    new_data, count = secrets.migrate_settings_dict(data)
    new_data = secrets.strip_secret_fields(new_data)
    fx2 = new_data.get("audio_fx") if isinstance(new_data.get("audio_fx"), dict) else None
    if isinstance(fx2, dict) and "ollama_host" in fx2:
        fx2.pop("ollama_host", None)
    new_data.pop("ollama_host", None)
    if count or new_data != data:
        _save_app_settings(new_data)
        if count:
            logger.info("已迁移密钥")
    return count


def secret_name_for(provider_id: str) -> str:
    mapping = {
        "openai": "openai_api_key",
        "anthropic": "anthropic_api_key",
        "gemini": "gemini_api_key",
        "xai": "xai_api_key",
        "ollama": "",
    }
    return mapping.get(provider_id, "")
