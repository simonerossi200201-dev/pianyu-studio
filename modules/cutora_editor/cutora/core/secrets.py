# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""API secrets stored in the OS credential store (Windows Credential Manager).

Never persist keys/tokens/secrets to JSON. If the keyring backend is missing,
saving is refused — there is no plaintext fallback.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from cutora.core.log import get_logger

SERVICE = "CUTORA"
SECRET_SUFFIXES = ("_key", "_token", "_secret")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

logger = get_logger("cutora.core.secrets")

_backend = None  # optional test injection


class SecretsError(RuntimeError):
    """Raised when the credential store cannot be used."""


def use_backend(backend: Any) -> None:
    """Install a test double. Pass ``None`` to restore the real keyring."""
    global _backend
    _backend = backend


def _is_secret_field(name: str) -> bool:
    lower = str(name).lower()
    return any(lower.endswith(suf) for suf in SECRET_SUFFIXES)


def _get_backend():
    if _backend is not None:
        return _backend
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except Exception as exc:
        raise SecretsError("keyring 不可用，拒绝保存密钥") from exc
    kr = keyring.get_keyring()
    if isinstance(kr, FailKeyring) or type(kr).__name__.lower() in ("failkeyring", "nullkeyring"):
        raise SecretsError("未找到凭据库后端，拒绝保存密钥（不回退明文）")
    # WinVaultKeyring.priority is 5; fail is -1.
    try:
        if float(getattr(kr, "priority", 0)) < 0:
            raise SecretsError("未找到凭据库后端，拒绝保存密钥（不回退明文）")
    except SecretsError:
        raise
    except Exception:
        pass
    return kr


def get(name: str) -> Optional[str]:
    """Return the stored secret or ``None``."""
    if not _NAME_RE.match(name):
        raise ValueError("invalid secret name")
    try:
        kr = _get_backend()
        value = kr.get_password(SERVICE, name)
    except SecretsError:
        return None
    except Exception as exc:
        logger.warning("读取凭据失败: %s", exc)
        return None
    if value is None or value == "":
        return None
    return str(value)


def set(name: str, value: str) -> None:
    """Store ``value`` under ``name``. Refuses if no keyring backend exists."""
    if not _NAME_RE.match(name):
        raise ValueError("invalid secret name")
    if value is None:
        raise ValueError("secret value is required")
    kr = _get_backend()
    try:
        kr.set_password(SERVICE, name, str(value))
    except SecretsError:
        raise
    except Exception as exc:
        raise SecretsError("写入凭据库失败，拒绝保存密钥") from exc


def delete(name: str) -> None:
    """Delete a stored secret. Missing names are ignored."""
    if not _NAME_RE.match(name):
        raise ValueError("invalid secret name")
    try:
        kr = _get_backend()
        kr.delete_password(SERVICE, name)
    except SecretsError:
        return
    except Exception:
        return


def strip_secret_fields(obj: Any) -> Any:
    """Return a copy of ``obj`` without keys named ``*_key`` / ``*_token`` / ``*_secret``."""
    if isinstance(obj, dict):
        return {
            k: strip_secret_fields(v)
            for k, v in obj.items()
            if not _is_secret_field(str(k))
        }
    if isinstance(obj, list):
        return [strip_secret_fields(x) for x in obj]
    return obj


def migrate_settings_dict(data: Any) -> tuple[Any, int]:
    """Move non-empty secret fields into keyring and drop the JSON keys.

    Returns ``(new_data, migrated_count)``. Empty secret fields are dropped
    without being written to the keyring.
    """
    if not isinstance(data, dict):
        return data, 0
    migrated = 0

    def walk(node: dict) -> dict:
        nonlocal migrated
        out: dict = {}
        for key, value in node.items():
            if isinstance(value, dict):
                out[key] = walk(value)
                continue
            if _is_secret_field(str(key)):
                text = "" if value is None else str(value).strip()
                if text:
                    set(str(key), text)
                    migrated += 1
                continue
            out[key] = value
        return out

    new_data = walk(data)
    return new_data, migrated
