# -*- coding: utf-8 -*-
"""本窗口的身份：端口、是不是「第一个」（配置端口那个）。

多开之后每个进程自己记一份。账号锁的 owner_port 从这里抄。
"""
from __future__ import annotations

PORT = 0
PRIMARY = True


def configure(port: int) -> None:
    """起服务之前调一次。PRIMARY = 绑的就是配置里的 ui.port。"""
    global PORT, PRIMARY
    from . import config, locks
    PORT = int(port or 0)
    base = int(config.get("ui.port") or 9400)
    PRIMARY = PORT == base
    locks.OWNER_PORT = PORT


def title() -> str:
    """窗口标题。第一个窗口不带端口，其余带 · :9401。不放任务名。"""
    if PRIMARY or not PORT:
        return "Orante 制片台"
    return f"Orante 制片台 · :{PORT}"
