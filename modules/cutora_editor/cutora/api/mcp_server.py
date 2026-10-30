# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hand-written MCP stdio server (JSON-RPC 2.0, protocol 2025-06-18).

Zero third-party deps. Forwards tools/call to the local API. Logs go to
stderr; stdout is JSON-RPC only (newline-delimited, no embedded newlines).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

PROTOCOL = "2025-06-18"
API_OFF = "请先打开片语 Studio 并在 设置 → 代理接口 里启用"
DOCS_URI = "cutora://docs/commands"
STATE_URI = "cutora://state"
PROJECT_URI = "cutora://project/current"


class ApiOff(RuntimeError):
    """Local API is not reachable."""


def _log(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


def _dump(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _reply(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def tool_name(command: str) -> str:
    return str(command).replace(".", "_")


def _tool_map() -> dict[str, Any]:
    from cutora.core.commands import get_registry
    out = {}
    for spec in get_registry().list():
        out[tool_name(spec.name)] = spec
    return out


def _tool_desc(spec) -> str:
    text = spec.doc_zh or ""
    if spec.dangerous and "confirm=true" not in text:
        text = text.rstrip() + " 需要 confirm=true。"
    return text


def handle_initialize(_params: dict) -> dict:
    from cutora import __version__
    return {
        "protocolVersion": PROTOCOL,
        "capabilities": {"tools": {}, "resources": {}},
        "serverInfo": {
            "name": "cutora",
            "title": "片语 Studio",
            "version": __version__,
        },
        "instructions": API_OFF,
    }


def handle_ping(_params: dict) -> dict:
    return {}


def handle_tools_list(_params: dict) -> dict:
    tools = []
    for spec in _tool_map().values():
        tools.append({
            "name": tool_name(spec.name),
            "description": _tool_desc(spec),
            "inputSchema": spec.params_schema,
        })
    tools.sort(key=lambda t: t["name"])
    return {"tools": tools}


def handle_tools_call(params: dict) -> dict:
    name = str((params or {}).get("name") or "")
    args = (params or {}).get("arguments") or {}
    if not isinstance(args, dict):
        args = {}
    spec = _tool_map().get(name)
    if spec is None:
        return {
            "content": [{"type": "text", "text": "未知工具 %s" % name}],
            "isError": True,
        }
    try:
        body = api_post("/v1/commands/" + spec.name, args)
    except ApiOff:
        if getattr(spec, "headless", False) and (
            os.environ.get("CUTORA_MCP_HEADLESS") == "1" or bool(args.get("_headless"))
        ):
            clean_args = {k: v for k, v in args.items() if k != "_headless"}
            try:
                from cutora.core.commands import get_bus
                res = get_bus().execute(spec.name, clean_args, source="mcp")
                body = {"ok": True, "command": spec.name, "result": res}
                return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False, indent=2)}], "isError": False}
            except Exception as exc:
                body = {"ok": False, "command": spec.name, "error": str(exc)}
                return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False, indent=2)}], "isError": True}
        return {
            "content": [{"type": "text", "text": API_OFF}],
            "isError": True,
        }
    except Exception as exc:
        return {
            "content": [{"type": "text", "text": API_OFF + "（%s）" % exc}],
            "isError": True,
        }
    text = json.dumps(body, ensure_ascii=False, indent=2)
    err = not bool(body.get("ok", True))
    return {"content": [{"type": "text", "text": text}], "isError": err}


def handle_resources_list(_params: dict) -> dict:
    return {"resources": [
        {
            "uri": STATE_URI, "name": "state",
            "description": "当前工程摘要、播放头、导出状态",
            "mimeType": "application/json",
        },
        {
            "uri": PROJECT_URI, "name": "project",
            "description": "当前打开的工程",
            "mimeType": "application/json",
        },
        {
            "uri": DOCS_URI, "name": "commands",
            "description": "命令参考 docs/api/commands.md",
            "mimeType": "text/markdown",
        },
    ]}


def handle_resources_read(params: dict) -> dict:
    uri = str((params or {}).get("uri") or "")
    if uri == DOCS_URI:
        return _read_docs()
    if uri in (STATE_URI, PROJECT_URI):
        return _read_state_resource(uri)
    raise KeyError(uri)


def _read_docs() -> dict:
    from cutora.core.paths import REPO_ROOT
    path = os.path.join(REPO_ROOT, "docs", "api", "commands.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return {"contents": [{
        "uri": DOCS_URI, "mimeType": "text/markdown", "text": text,
    }]}


def _read_state_resource(uri: str) -> dict:
    body = api_get("/v1/state")
    state = body.get("state") if isinstance(body, dict) else body
    if uri == PROJECT_URI:
        payload = (state or {}).get("project") if isinstance(state, dict) else {}
    else:
        payload = state
    return {"contents": [{
        "uri": uri, "mimeType": "application/json",
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
    }]}


def _token_and_port() -> tuple[str | None, int]:
    from cutora.api import DEFAULT_PORT
    from cutora.api.token import read_token
    from cutora.core.history import _load_app_settings
    settings = _load_app_settings() or {}
    try:
        port = int(settings.get("api_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    token = os.environ.get("CUTORA_API_TOKEN") or read_token()
    return token, port


def _http(method: str, path: str, body: dict | None = None) -> dict:
    import http.client
    token, port = _token_and_port()
    if not token:
        raise ApiOff(API_OFF)
    payload = None
    headers = {
        "Host": "127.0.0.1:%d" % port,
        "Authorization": "Bearer " + token,
    }
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=8)
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
    except OSError as exc:
        raise ApiOff(API_OFF) from exc
    finally:
        conn.close()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = {"ok": False, "error": raw.decode("utf-8", "replace")[:400]}
    if resp.status in (401, 404) and not (isinstance(data, dict) and data.get("ok")):
        if resp.status == 401:
            raise ApiOff(API_OFF)
    return data if isinstance(data, dict) else {"ok": False, "raw": data}


def api_get(path: str) -> dict:
    return _http("GET", path)


def api_post(path: str, body: dict) -> dict:
    return _http("POST", path, body)


HANDLERS = {
    "initialize": handle_initialize,
    "ping": handle_ping,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "resources/list": handle_resources_list,
    "resources/read": handle_resources_read,
}


def dispatch(message: dict) -> dict | None:
    """Handle one JSON-RPC object. Notifications return None."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(message.get("id") if isinstance(message, dict) else None,
                      -32600, "Invalid Request")
    method = message.get("method")
    msg_id = message.get("id", _MISSING)
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    if method == "notifications/initialized":
        return None
    if not isinstance(method, str):
        if msg_id is _MISSING:
            return None
        return _error(msg_id, -32600, "Invalid Request")
    handler = HANDLERS.get(method)
    if handler is None:
        if msg_id is _MISSING:
            return None
        return _error(msg_id, -32601, "Method not found: %s" % method)
    if msg_id is _MISSING:
        try:
            handler(params)
        except Exception:
            _log("notification %s failed" % method)
        return None
    try:
        result = handler(params)
        return _reply(msg_id, result)
    except ApiOff as exc:
        return _error(msg_id, -32603, str(exc) or API_OFF)
    except KeyError:
        return _error(msg_id, -32002, "Resource not found", {"uri": params.get("uri")})
    except OSError:
        return _error(msg_id, -32603, API_OFF)
    except Exception as exc:
        _log("handler %s failed: %s" % (method, exc))
        return _error(msg_id, -32603, "Internal error")


_MISSING = object()


def handle_line(line: str) -> dict | None:
    line = (line or "").strip()
    if not line:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return _error(None, -32700, "Parse error")
    if isinstance(message, dict):
        return dispatch(message)
    return _error(None, -32600, "Invalid Request")


def main() -> int:
    logging_to_stderr()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        raw = stdin.readline()
        if not raw:
            break
        try:
            line = raw.decode("utf-8")
        except Exception:
            out = _error(None, -32700, "Parse error")
            stdout.write(_dump(out))
            stdout.flush()
            continue
        reply = handle_line(line)
        if reply is not None:
            stdout.write(_dump(reply))
            stdout.flush()
    return 0


def logging_to_stderr() -> None:
    import logging
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
