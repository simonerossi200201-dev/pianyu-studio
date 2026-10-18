"""Tiny shared HTTP helper (httpx) that turns connection failures into clear error dicts."""
from __future__ import annotations

from typing import Any

import httpx


class ServiceDown(Exception):
    pass


def request(method: str, url: str, *, json: Any = None, params: dict | None = None,
            timeout: float = 10.0, service: str = "service") -> Any:
    """Return parsed JSON (or text). Raises ServiceDown if the port is not listening,
    RuntimeError with the server's message on HTTP errors."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            r = c.request(method, url, json=json, params=params)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise ServiceDown(f"{service} is not running ({url.split('/')[2]} not reachable)") from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"{service} timed out after {timeout}s: {url}") from exc
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("error") or body.get("detail") or body
        except ValueError:
            msg = r.text[:500]
        raise RuntimeError(f"{service} HTTP {r.status_code}: {msg}")
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        return r.json()
    return r.text


def down(service: str, exc: Exception) -> dict:
    return {"ok": False, "running": False, "error": str(exc), "service": service}
