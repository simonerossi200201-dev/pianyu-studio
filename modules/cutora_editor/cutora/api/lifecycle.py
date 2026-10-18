# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start / stop the process-wide local API server."""
from __future__ import annotations

from cutora.api import DEFAULT_PORT
from cutora.api.token import ensure_token
from cutora.core.log import get_logger

logger = get_logger("cutora.api.lifecycle")

_SERVER = None


def get_server():
    return _SERVER


def start_api(port: int = DEFAULT_PORT):
    global _SERVER
    stop_api()
    ensure_token()
    from cutora.api.server import ApiServer
    srv = ApiServer(DEFAULT_PORT if port is None else int(port))
    srv.start()
    _SERVER = srv
    logger.info("本地 API 已开启，端口 %s", srv.port)
    return srv


def stop_api() -> None:
    global _SERVER
    if _SERVER is None:
        return
    try:
        _SERVER.stop()
    except Exception:
        logger.exception("关闭本地 API 失败")
    _SERVER = None
