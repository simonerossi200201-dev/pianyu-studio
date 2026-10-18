# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Marshal API work onto the Qt GUI thread (appendix D3)."""
from __future__ import annotations

import threading
from typing import Any, Callable

from cutora.core.log import get_logger

logger = get_logger("cutora.api.bridge")

_BRIDGE = None
EXECUTE_TIMEOUT = 30.0


def _make_bridge_class():
    from PySide6.QtCore import QObject, Qt, Signal

    class QtBridge(QObject):
        _sig = Signal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._sig.connect(self._run, Qt.ConnectionType.QueuedConnection)

        def submit(self, fn: Callable[[], Any], timeout: float = EXECUTE_TIMEOUT) -> Any:
            box: dict[str, Any] = {
                "fn": fn,
                "ev": threading.Event(),
                "result": None,
                "error": None,
                "cancelled": False,
            }
            self._sig.emit(box)
            if not box["ev"].wait(timeout):
                box["cancelled"] = True
                raise TimeoutError("命令在主线程执行超过 %.0f 秒" % timeout)
            if box["error"] is not None:
                raise box["error"]
            return box["result"]

        def _run(self, box: dict) -> None:
            if box.get("cancelled"):
                box["ev"].set()
                return
            try:
                box["result"] = box["fn"]()
            except Exception as exc:
                box["error"] = exc
            box["ev"].set()

    return QtBridge


def install_bridge(window) -> Any:
    """Create the bridge as a child of MainWindow (GUI thread)."""
    global _BRIDGE
    cls = _make_bridge_class()
    _BRIDGE = cls(window)
    window._api_bridge = _BRIDGE
    return _BRIDGE


def get_bridge():
    return _BRIDGE


def run_on_gui(fn: Callable[[], Any], timeout: float = EXECUTE_TIMEOUT) -> Any:
    """Run ``fn`` on the GUI thread when a bridge exists; otherwise inline."""
    bridge = get_bridge()
    if bridge is None:
        return fn()
    return bridge.submit(fn, timeout=timeout)
