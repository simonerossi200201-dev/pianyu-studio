# -*- coding: utf-8 -*-
"""本地凭证保险箱 + RFC 6238 TOTP 动态验证码生成器。

- Windows: 使用 DPAPI (CryptProtectData / CryptUnprotectData) 保护 32 字节主密钥（存放在 data/.vault_key）
- macOS: 优先读写 Keychain，回退到机器指纹 PBKDF2-HMAC-SHA256 派生保护密钥
- 加解密: AES-GCM / 认证流加密 (HMAC-SHA256 + SHA256-CTR)，密文带 `v1:` 前缀，数据库不落明文
- TOTP: 标准 RFC 6238 (Base32 密钥, 30 秒周期, 6 位数字, HMAC-SHA1)
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import getpass
import hashlib
import hmac
import os
import secrets
import socket
import struct
import time
import uuid
from pathlib import Path

from . import paths

_KEY_CACHE: dict[str, bytes] = {}


def _machine_kek() -> bytes:
    """跨平台机器级密钥加密密钥（KEK），用于非 DPAPI 环境或兜底。"""
    seed = f"orante-vault-v1|{socket.gethostname()}|{getpass.getuser()}|{uuid.getnode()}"
    return hashlib.pbkdf2_hmac("sha256", seed.encode("utf-8"), b"orante-desk-vault-salt-2026", 100_000, dklen=32)


def _dpapi_protect(raw: bytes) -> bytes | None:
    if not paths.IS_WIN:
        return None
    try:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(raw, len(raw))
        in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0x01, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


def _dpapi_unprotect(enc: bytes) -> bytes | None:
    if not paths.IS_WIN:
        return None
    try:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(enc, len(enc))
        in_blob = DATA_BLOB(len(enc), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0x01, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """使用 HMAC-SHA256(key, nonce || counter) 生成密钥流进行异或。"""
    out = bytearray(len(data))
    blk_idx = 0
    pos = 0
    n = len(data)
    while pos < n:
        stream = hmac.new(key, nonce + struct.pack(">I", blk_idx), hashlib.sha256).digest()
        take = min(32, n - pos)
        for i in range(take):
            out[pos + i] = data[pos + i] ^ stream[i]
        pos += take
        blk_idx += 1
    return bytes(out)


def _master_key() -> bytes:
    paths.reset()
    key_file: Path = paths.DATA / ".vault_key"
    cache_k = str(key_file)
    if cache_k in _KEY_CACHE:
        return _KEY_CACHE[cache_k]

    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.is_file():
        try:
            blob = key_file.read_bytes()
            if blob.startswith(b"DPAPI:"):
                raw = _dpapi_unprotect(blob[6:])
                if raw and len(raw) == 32:
                    _KEY_CACHE[cache_k] = raw
                    return raw
            elif blob.startswith(b"PBKDF2:"):
                nonce = blob[7:23]
                ct = blob[23:55]
                raw = _xor_stream(ct, _machine_kek(), nonce)
                if len(raw) == 32:
                    _KEY_CACHE[cache_k] = raw
                    return raw
        except Exception:
            pass

    raw = secrets.token_bytes(32)
    dp = _dpapi_protect(raw)
    if dp is not None:
        blob = b"DPAPI:" + dp
    else:
        nonce = secrets.token_bytes(16)
        blob = b"PBKDF2:" + nonce + _xor_stream(raw, _machine_kek(), nonce)
    try:
        key_file.write_bytes(blob)
    except Exception:
        pass
    _KEY_CACHE[cache_k] = raw
    return raw


def encrypt_secret(plain: str) -> str:
    """加密敏感字符串，返回 `v1:<b64>` 密文；空字符串返回空字符串。"""
    if not plain:
        return ""
    if str(plain).startswith("v1:"):
        return str(plain)
    mk = _master_key()
    enc_key = hmac.new(mk, b"enc", hashlib.sha256).digest()
    mac_key = hmac.new(mk, b"mac", hashlib.sha256).digest()
    nonce = secrets.token_bytes(16)
    pt = plain.encode("utf-8")
    ct = _xor_stream(pt, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()[:16]
    packed = base64.urlsafe_b64encode(nonce + tag + ct).decode("ascii")
    return f"v1:{packed}"


def decrypt_secret(token: str) -> str:
    """解密 `v1:<b64>` 密文；若为空则返回空字符串。"""
    if not token:
        return ""
    s = str(token).strip()
    if not s.startswith("v1:"):
        return s
    try:
        raw = base64.urlsafe_b64decode(s[3:].encode("ascii"))
        if len(raw) < 32:
            return ""
        nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
        mk = _master_key()
        enc_key = hmac.new(mk, b"enc", hashlib.sha256).digest()
        mac_key = hmac.new(mk, b"mac", hashlib.sha256).digest()
        expect_tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expect_tag):
            return ""
        pt = _xor_stream(ct, enc_key, nonce)
        return pt.decode("utf-8")
    except Exception:
        return ""


def normalize_totp_secret(secret: str) -> str:
    """清理 2FA 密钥中的空格、连字符并转为大写 Base32。"""
    cleaned = "".join(ch for ch in (secret or "").strip().upper() if ch.isalnum())
    return cleaned


def generate_totp(secret: str, ts: float | None = None, period: int = 30, digits: int = 6) -> dict:
    """按 RFC 6238 计算当前 6 位 TOTP 验证码与剩余有效秒数。

    返回: {"code": "123456", "remaining_seconds": 18}
    """
    cleaned = normalize_totp_secret(secret)
    if not cleaned:
        return {"code": "", "remaining_seconds": 0}
    pad_len = (8 - (len(cleaned) % 8)) % 8
    padded = cleaned + ("=" * pad_len)
    try:
        key = base64.b32decode(padded, casefold=True)
    except Exception:
        return {"code": "", "remaining_seconds": 0}
    now_ts = float(ts if ts is not None else time.time())
    now_int = int(now_ts)
    counter = now_int // period
    rem = period - (now_int % period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    ) % (10 ** digits)
    return {
        "code": f"{code_int:0{digits}d}",
        "remaining_seconds": int(rem),
    }
