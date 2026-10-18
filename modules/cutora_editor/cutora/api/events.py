# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process SSE hub. API thread publishes; request handlers subscribe."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[asyncio.Queue] = []
        self._sync: list = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def client_count(self) -> int:
        with self._lock:
            return len(self._queues)

    def subscribe_sync(self, cb) -> None:
        with self._lock:
            self._sync.append(cb)

    def unsubscribe_sync(self, cb) -> None:
        with self._lock:
            if cb in self._sync:
                self._sync.remove(cb)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {
            "type": event_type,
            "ts": time.time(),
            "data": data,
        }
        loop = self._loop
        with self._lock:
            queues = list(self._queues)
            sync = list(self._sync)
        for cb in sync:
            try:
                cb(payload)
            except Exception:
                pass
        for q in queues:
            if loop is None:
                continue
            try:
                loop.call_soon_threadsafe(self._put, q, payload)
            except Exception:
                pass

    @staticmethod
    def _put(q: asyncio.Queue, payload: dict) -> None:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(payload)
            except Exception:
                pass


def encode_sse(payload: dict) -> bytes:
    etype = str(payload.get("type") or "message")
    body = json.dumps(payload, ensure_ascii=False)
    return ("event: %s\ndata: %s\n\n" % (etype, body)).encode("utf-8")


HUB = EventHub()
