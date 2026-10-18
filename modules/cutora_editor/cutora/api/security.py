# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host / Origin / rate-limit checks. No bind-all-interfaces literals."""
from __future__ import annotations

import collections
import time


def loopback_host() -> str:
    """Loopback IPv4. Bind this, never the hostname localhost."""
    return "127.0.0.1"


def host_header_ok(header: str | None, port: int) -> bool:
    raw = (header or "").strip().lower()
    if not raw:
        return False
    allowed = {
        loopback_host(),
        "%s:%d" % (loopback_host(), int(port)),
        "localhost",
        "localhost:%d" % int(port),
    }
    return raw in allowed


def has_origin(header: str | None) -> bool:
    return bool((header or "").strip())


class RateLimiter:
    def __init__(self, limit: int = 600, window: float = 60.0) -> None:
        self.limit = int(limit)
        self.window = float(window)
        self._hits: dict[str, collections.deque] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(key, collections.deque())
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
