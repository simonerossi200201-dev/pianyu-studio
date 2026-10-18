# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single outbound HTTP entry. Hostnames must be on the allow-list."""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cutora.core.log import get_logger

logger = get_logger("cutora.core.net")

# General spec §2.2 — product runtime allow-list.
# raw.githubusercontent.com is development/build only (see
# docs/DEV_NETWORK_ALLOWLIST.md); do not add it here.
ALLOWED_HOSTS = frozenset({
    "pypi.org",
    "files.pythonhosted.org",
    "download.pytorch.org",
    "huggingface.co",
    "github.com",
    "objects.githubusercontent.com",
    "fonts.google.com",
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.x.ai",
})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
AI_HOSTS = frozenset({
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.x.ai",
})


class NetPolicyError(RuntimeError):
    """Raised when a URL is blocked by the allow-list or the AI network switch."""


class NetCancelled(Exception):
    """用户取消了下载（.part 保留，可续传）。"""


def _hostname(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host


def _is_huggingface_host(host: str) -> bool:
    h = (host or "").lower().strip()
    return h == "huggingface.co" or h.endswith(".huggingface.co")


def _hf_co_redirect_host(host: str) -> bool:
    """Official Hugging Face LFS/Xet CDN — redirect target only."""
    h = (host or "").lower().strip()
    return h == "hf.co" or h.endswith(".hf.co")  # redirect target only


def is_allowed_host(host: str, *, allow_local: bool = True) -> bool:
    """Return True if ``host`` is on the allow-list.

    ``*.hf.co`` is not a direct host; only a huggingface.co 302 redirect
    target (see ``assert_url_allowed`` / ``_GuardRedirect``).
    Files fetched that way still require SHA256 verification.
    """
    h = (host or "").lower().strip()
    if not h:
        return False
    if allow_local and h in LOCAL_HOSTS:
        return True
    if h in ALLOWED_HOSTS:
        return True
    if _is_huggingface_host(h):
        return True
    return False


def assert_url_allowed(
    url: str,
    *,
    allow_local: bool = True,
    via_redirect_from: str | None = None,
) -> str:
    """Validate ``url`` and return its hostname. Raises ``NetPolicyError``."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise NetPolicyError("不允许的 URL scheme: %s" % (parsed.scheme or ""))
    host = (parsed.hostname or "").lower()
    if _hf_co_redirect_host(host):
        src_host = _hostname(via_redirect_from or "")
        if not via_redirect_from or not _is_huggingface_host(src_host):
            logger.warning("拒绝直接访问 hf.co（非 redirect 落点）: %s", host)
            raise NetPolicyError(
                "hf.co 只能作为 huggingface.co 的 redirect 落点: %s" % host)
        if parsed.scheme != "https":
            raise NetPolicyError("非本机端点必须使用 https")
        return host
    if not is_allowed_host(host, allow_local=allow_local):
        logger.warning("拒绝出站请求（主机不在白名单）: %s", host or url)
        raise NetPolicyError("主机不在白名单: %s" % (host or url))
    if host in LOCAL_HOSTS:
        # Ollama: host must stay on loopback; port is caller-controlled.
        if parsed.scheme != "http":
            raise NetPolicyError("本机端点只允许 http")
    elif parsed.scheme != "https":
        raise NetPolicyError("非本机端点必须使用 https")
    return host


def is_ai_network_enabled() -> bool:
    """Read the global 'allow AI networking' switch (default off)."""
    try:
        from cutora.ai.prefs import get_ai_prefs
        return bool(get_ai_prefs().get("ai_network_enabled"))
    except Exception:
        return False


class _GuardRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        src = getattr(req, "full_url", None) or req.get_full_url()
        assert_url_allowed(newurl, via_redirect_from=src)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 60,
    purpose: str = "ai",
    json_body: Any = None,
) -> tuple[int, bytes, dict]:
    """Perform an outbound request after the host allow-list check.

    ``purpose='ai'`` additionally requires the user-facing network switch.
    Returns ``(status, body, response_headers)``.
    """
    host = assert_url_allowed(url)
    if purpose == "ai":
        if host in AI_HOSTS or host in LOCAL_HOSTS:
            if not is_ai_network_enabled() and os.environ.get("CUTORA_NET_SKIP_AI_SWITCH") != "1":
                raise NetPolicyError("AI 功能联网已关闭")
    hdrs = {"User-Agent": "CUTORA/1.0"}
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(dict(headers))
    req = Request(url, data=body, headers=hdrs, method=method.upper())
    opener = build_opener(_GuardRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or resp.getcode() or 200
            resp_headers = dict(resp.headers.items()) if resp.headers else {}
            return int(status), raw, resp_headers
    except ConnectionError as exc:
        raise NetPolicyError("网络错误: %s" % exc) from exc
    except HTTPError as exc:
        detail = b""
        try:
            detail = exc.read()
        except Exception:
            pass
        raise NetPolicyError("HTTP %s: %s" % (exc.code, detail[:800].decode("utf-8", "replace"))) from exc
    except NetPolicyError:
        raise
    except URLError as exc:
        raise NetPolicyError("网络错误: %s" % exc.reason) from exc


def download_to_file(
    url: str,
    dest: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 600,
    resume: bool = True,
    purpose: str = "model",
    progress_cb=None,
    cancel_event=None,
) -> dict:
    """Stream ``url`` to ``dest`` after the host allow-list check.

    ``purpose='model'`` does not require the AI network switch. Resume uses
    ``dest + '.part'`` and an HTTP Range request when the part file exists.
    ``progress_cb(written_bytes, total_bytes_or_None)`` is called per chunk;
    ``cancel_event`` (threading.Event) stops the stream and keeps the .part
    file so a later call can resume. Cancel raises ``NetCancelled``.
    """
    host = assert_url_allowed(url)
    _ = host
    if purpose == "ai":
        raise NetPolicyError("模型下载不得走 purpose=ai")
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    part = dest + ".part"
    offset = os.path.getsize(part) if resume and os.path.isfile(part) else 0
    hdrs = {"User-Agent": "CUTORA/1.0"}
    if headers:
        hdrs.update(dict(headers))
    if offset:
        hdrs["Range"] = "bytes=%d-" % offset
    req = Request(url, headers=hdrs, method="GET")
    opener = build_opener(_GuardRedirect)
    mode = "ab" if offset else "wb"
    resumed = bool(offset)
    written = offset
    try:
        with opener.open(req, timeout=timeout) as resp, open(part, mode) as fh:
            total = None
            try:
                clen = resp.headers.get("Content-Length")
                if clen:
                    total = int(clen) + offset
            except (TypeError, ValueError):
                total = None
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise NetCancelled("下载已取消")
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
                if progress_cb is not None:
                    progress_cb(written, total)
    except HTTPError as exc:
        raise NetPolicyError("HTTP %s" % exc.code) from exc
    except URLError as exc:
        raise NetPolicyError("网络错误: %s" % exc.reason) from exc
    os.replace(part, dest)
    return {"bytes": written, "resumed": resumed}
