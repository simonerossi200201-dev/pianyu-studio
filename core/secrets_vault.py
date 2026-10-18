"""Windows 凭据管理器密钥保险箱 (SecretsVault) 与敏感信息/大陆节点扫描器。

确保：
1. 所有 API Key / Token 统一存入 Windows 系统凭据管理器 (WinVaultKeyring / ctypes CredWriteW)，
   绝不在任何 .json / .toml / SQLite 数据库中落盘明文密钥。
2. 提供日志与配置文件脱敏清洗器 (scrub_secrets)。
3. 提供全仓安全合规扫描，拦截任何明文密钥或禁止的大陆服务器节点。
"""

from __future__ import annotations

import re
from typing import Any

SERVICE_NAME = "LTX_STUDIO_VAULT"

# 敏感 Token 正则脱敏规则
_SECRET_PATTERNS = [
    (re.compile(r"ya29\.[0-9A-Za-z\-_]+"), "ya29.***REDACTED***"),
    (re.compile(r"1//0[0-9A-Za-z\-_]+"), "1//0***REDACTED***"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{30,}"), "AIza***REDACTED***"),
    (re.compile(r"sk-[0-9A-Za-z\-_]{20,}"), "sk-***REDACTED***"),
    (re.compile(r"xai-[0-9A-Za-z\-_]{20,}"), "xai-***REDACTED***"),
    (re.compile(r'"access_token"\s*:\s*"[^"]+"'), '"access_token":"***"'),
    (re.compile(r'"refresh_token"\s*:\s*"[^"]+"'), '"refresh_token":"***"'),
]

# 严禁出现的境外/大陆镜像或受限云端节点域名
FORBIDDEN_CN_DOMAINS = (
    ".cn/",
    "aliyun.com",
    "aliyuncs.com",
    "tsinghua.edu.cn",
    "ustc.edu.cn",
    "hf-mirror.com",
    "modelscope.cn",
    "baidu.com",
    "qq.com",
    "tencent.com",
    "bytedance.com",
    "volcengine.com",
    "api.deepseek.com",
)


def scrub_secrets(text: str) -> str:
    """将文本或日志中的 OAuth Token 和 API Key 全部掩码脱敏。"""
    if not text:
        return ""
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def is_url_allowed(url: str) -> bool:
    """检查 URL 是否违反安全防线（禁止任何大陆镜像或受限域名）。"""
    low = (url or "").lower()
    for bad in FORBIDDEN_CN_DOMAINS:
        if bad in low:
            return False
    return True


def store_secret(key_name: str, secret_value: str) -> bool:
    """将密钥存入 Windows 凭据管理器（优先使用 keyring，回退 ctypes WinCred）。"""
    if not secret_value:
        return False
    try:
        import keyring  # type: ignore
        keyring.set_password(SERVICE_NAME, key_name, secret_value)
        return True
    except Exception:
        return _wincred_write(f"{SERVICE_NAME}:{key_name}", secret_value)


def get_secret(key_name: str) -> str | None:
    """从 Windows 凭据管理器读取密钥。"""
    try:
        import keyring  # type: ignore
        val = keyring.get_password(SERVICE_NAME, key_name)
        if val:
            return val
    except Exception:
        pass
    return _wincred_read(f"{SERVICE_NAME}:{key_name}")


def strip_secret_fields(obj: Any) -> Any:
    """递归剥离字典中以 _key, _token, _secret, api_key 结尾的明文字段，转存至 Windows Vault。"""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, str) and v and not v.startswith("vault://") and (
                kl in ("api_key", "apikey", "client_secret", "access_token", "refresh_token")
                or kl.endswith(("_key", "_token", "_secret"))
            ):
                import hashlib
                digest = hashlib.sha256(v.encode("utf-8", errors="ignore")).hexdigest()[:12]
                vault_id = f"auto_{kl}_{digest}"
                store_secret(vault_id, v)
                cleaned[k] = f"vault://{vault_id}"


            else:
                cleaned[k] = strip_secret_fields(v)
        return cleaned
    if isinstance(obj, list):
        return [strip_secret_fields(x) for x in obj]
    return obj


# --- 纯标准库 ctypes Win32 CredWriteW / CredReadW 兜底实现 ---
import ctypes
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _wincred_write(target: str, secret: str) -> bool:
    try:
        advapi32 = ctypes.windll.advapi32
        blob = secret.encode("utf-16-le")
        cred = _CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = ctypes.c_wchar_p(target)
        cred.CredentialBlobSize = len(blob)
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = ctypes.c_wchar_p("LTXStudioUser")
        return bool(advapi32.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def _wincred_read(target: str) -> str | None:
    try:
        advapi32 = ctypes.windll.advapi32
        pcred = ctypes.POINTER(_CREDENTIALW)()
        if not advapi32.CredReadW(ctypes.c_wchar_p(target), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return None
        try:
            cred = pcred.contents
            size = int(cred.CredentialBlobSize)
            raw = ctypes.string_at(cred.CredentialBlob, size)
            return raw.decode("utf-16-le")
        finally:
            advapi32.CredFree(pcred)
    except Exception:
        return None
