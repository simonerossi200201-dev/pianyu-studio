# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local aiohttp API. Bind loopback IPv4 only. Own thread + event loop."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from aiohttp import web

from cutora.api import DEFAULT_PORT
from cutora.api.events import HUB, encode_sse
from cutora.api.jobs import create_job, read_job, update_job
from cutora.api.openapi import build_openapi, docs_html
from cutora.api.security import RateLimiter, has_origin, host_header_ok, loopback_host
from cutora.api.token import token_matches
from cutora.core.command_schema import SchemaError
from cutora.core.commands import (
    CommandError,
    NeedConfirm,
    NeedsGui,
    get_bus,
    get_registry,
)
from cutora.core.log import get_logger

logger = get_logger("cutora.api.server")


class ApiServer:
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = DEFAULT_PORT if port is None else int(port)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self.limiter = RateLimiter(600, 60.0)
        self.clients: set[str] = set()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._thread_main, name="CUTORA-API", daemon=True)
        self._thread.start()
        if not self._ready.wait(8):
            raise RuntimeError(self._error or "本地 API 启动超时")
        if self._error:
            raise RuntimeError(self._error)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._async_stop(), loop).result(5)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None
        with self._lock:
            self.clients.clear()

    def client_count(self) -> int:
        with self._lock:
            return len(self.clients)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        HUB.bind_loop(loop)
        try:
            loop.run_until_complete(self._async_start())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
            logger.exception("本地 API 线程失败")
        finally:
            try:
                loop.run_until_complete(self._async_stop())
            except Exception:
                pass
            loop.close()

    async def _async_start(self) -> None:
        app = self._make_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, loopback_host(), self.port)
        await site.start()
        self._runner = runner
        self._site = site
        try:
            socks = getattr(site, "_server", None)
            socks = getattr(socks, "sockets", None) or []
            if socks:
                self.port = int(socks[0].getsockname()[1])
        except Exception:
            pass
        logger.info("本地 API 听在 %s:%s", loopback_host(), self.port)

    async def _async_stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _make_app(self) -> web.Application:
        app = web.Application(middlewares=[self._security_mw])
        app["api"] = self
        app.router.add_get("/v1/commands", _list_commands)
        app.router.add_post("/v1/commands/{name:.+}", _run_command)
        app.router.add_post("/v1/undo", _undo)
        app.router.add_post("/v1/redo", _redo)
        app.router.add_get("/v1/state", _state)
        app.router.add_get("/v1/events", _events)
        app.router.add_get("/v1/jobs/{job_id}", _job)
        app.router.add_get("/openapi.json", _openapi)
        app.router.add_get("/docs", _docs)
        return app

    @web.middleware
    async def _security_mw(self, request: web.Request, handler):
        if has_origin(request.headers.get("Origin")):
            return _json({"ok": False, "error": "浏览器页面不得调用本地 API"}, 403)
        if not host_header_ok(request.headers.get("Host"), self.port):
            return _json({"ok": False, "error": "Host 不合法"}, 421)
        peer = request.remote or "?"
        if not self.limiter.allow(peer):
            return _json({"ok": False, "error": "速率限制"}, 429)
        if not token_matches(request.headers.get("Authorization")):
            return _json({"ok": False, "error": "需要 Authorization: Bearer <token>"}, 401)
        with self._lock:
            self.clients.add(peer)
        try:
            resp = await handler(request)
        finally:
            pass
        # Never emit CORS headers.
        if "Access-Control-Allow-Origin" in resp.headers:
            del resp.headers["Access-Control-Allow-Origin"]
        return resp


def _json(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def _gui(fn):
    from cutora.api.bridge import run_on_gui
    return run_on_gui(fn)


async def _list_commands(request: web.Request) -> web.Response:
    items = get_registry().public_list()
    return _json({"ok": True, "commands": items, "count": len(items)})


async def _run_command(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        params = await request.json()
    except Exception:
        params = {}
    if not isinstance(params, dict):
        return _json({"ok": False, "error": "body 必须是 JSON 对象"}, 400)
    try:
        result = _gui(lambda n=name, p=dict(params): _execute(n, p))
    except TimeoutError as exc:
        return _json({"ok": False, "error": str(exc)}, 504)
    except NeedConfirm as exc:
        return _json({"ok": False, "error": exc.message, "code": exc.code}, 409)
    except NeedsGui as exc:
        return _json({"ok": False, "error": exc.message, "code": exc.code}, 400)
    except SchemaError as exc:
        return _json({"ok": False, "error": str(exc), "code": "schema"}, 400)
    except CommandError as exc:
        status = 404 if exc.code == "unknown" else 400
        return _json({"ok": False, "error": exc.message, "code": exc.code}, status)
    except Exception as exc:
        logger.exception("API 命令 %s 失败", name)
        return _json({"ok": False, "error": str(exc)}, 500)
    HUB.publish("command", {"name": name, "ok": True})
    body = dict(result or {})
    undo_id = body.pop("_undo_id", None)
    return _json({"ok": True, "result": body, "undo_id": undo_id})


def _execute(name: str, params: dict) -> dict:
    spec = get_registry().get(name)
    bus = get_bus()
    if spec.async_job:
        job = create_job(name, params)
        try:
            out = bus.execute(name, params, source="api")
            update_job(job["job_id"], status="running", result=out)
            result = dict(out or {})
            result["job_id"] = job["job_id"]
            return _with_undo(result)
        except Exception as exc:
            update_job(job["job_id"], status="failed", error=str(exc))
            raise
    out = bus.execute(name, params, source="api")
    return _with_undo(dict(out or {}))


def _with_undo(result: dict) -> dict:
    window = get_bus()._window
    mgr = getattr(window, "history_mgr", None) if window is not None else None
    if mgr is not None:
        result["_undo_id"] = int(getattr(mgr, "current_index", -1))
    return result


async def _undo(request: web.Request) -> web.Response:
    try:
        _gui(_do_undo)
    except TimeoutError as exc:
        return _json({"ok": False, "error": str(exc)}, 504)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    HUB.publish("undo", {})
    return _json({"ok": True})


async def _redo(request: web.Request) -> web.Response:
    try:
        _gui(_do_redo)
    except TimeoutError as exc:
        return _json({"ok": False, "error": str(exc)}, 504)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    HUB.publish("redo", {})
    return _json({"ok": True})


def _do_undo() -> None:
    window = get_bus()._window
    if window is None or not hasattr(window, "undo"):
        raise NeedsGui()
    window.undo()


def _do_redo() -> None:
    window = get_bus()._window
    if window is None or not hasattr(window, "redo"):
        raise NeedsGui()
    window.redo()


async def _state(request: web.Request) -> web.Response:
    try:
        data = _gui(_read_state)
    except TimeoutError as exc:
        return _json({"ok": False, "error": str(exc)}, 504)
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)}, 400)
    return _json({"ok": True, "state": data})


def _read_state() -> dict[str, Any]:
    window = get_bus()._window
    api: ApiServer | None = None
    # filled by handler via request; keep process singleton
    from cutora.api import lifecycle as _life
    srv = _life.get_server()
    info: dict[str, Any] = {
        "api": {
            "port": srv.port if srv else None,
            "clients": srv.client_count() if srv else 0,
        }
    }
    if window is None:
        info["project"] = {}
        return info
    try:
        info["project"] = window.command_project_info()
    except Exception:
        info["project"] = {}
    try:
        info["export"] = window.command_export_status()
    except Exception:
        info["export"] = {}
    info["playhead_ms"] = int(getattr(window, "_timeline_ms", 0) or 0)
    info["curr_idx"] = int(getattr(window, "curr_idx", -1))
    return info


async def _events(request: web.Request) -> web.StreamResponse:
    q = HUB.subscribe()
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    try:
        await resp.write(encode_sse({"type": "hello", "data": {}}))
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            await resp.write(encode_sse(payload))
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        HUB.unsubscribe(q)
    return resp


async def _job(request: web.Request) -> web.Response:
    info = read_job(request.match_info["job_id"])
    if info is None:
        return _json({"ok": False, "error": "找不到作业"}, 404)
    return _json({"ok": True, "job": info})


async def _openapi(request: web.Request) -> web.Response:
    port = request.app["api"].port
    return _json(build_openapi(port))


async def _docs(request: web.Request) -> web.Response:
    port = request.app["api"].port
    return web.Response(text=docs_html(port), content_type="text/html", charset="utf-8")
