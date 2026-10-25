# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""LRU cache of static ROI images. Cap 64. Key includes style + hold state.

ROI_CACHE is per-thread: compose workers must not share QImages.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

_TLS = threading.local()


class RoiCache:
    """Map key → (QImage, origin). Caller owns image lifetime after get()."""

    def __init__(self, maxsize: int = 64) -> None:
        self.maxsize = max(1, int(maxsize))
        self._data: OrderedDict[Any, tuple[Any, tuple[int, int]]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> tuple[Any, tuple[int, int]] | None:
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return None
        self.hits += 1
        self._data.move_to_end(key)
        return item

    def put(self, key: Any, image: Any, origin: tuple[int, int]) -> None:
        self._data[key] = (image, (int(origin[0]), int(origin[1])))
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
        self.reset_stats()

    def reset_stats(self) -> None:
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._data)


class _TlsRoiCache:
    """Proxy so existing `from cache import ROI_CACHE` stays valid."""

    def _c(self) -> RoiCache:
        c = getattr(_TLS, "roi", None)
        if c is None:
            c = RoiCache(28)
            _TLS.roi = c
        return c

    def get(self, key: Any) -> tuple[Any, tuple[int, int]] | None:
        return self._c().get(key)

    def put(self, key: Any, image: Any, origin: tuple[int, int]) -> None:
        return self._c().put(key, image, origin)

    def clear(self) -> None:
        return self._c().clear()

    def reset_stats(self) -> None:
        return self._c().reset_stats()

    def __len__(self) -> int:
        return len(self._c())

    @property
    def hits(self) -> int:
        return self._c().hits

    @property
    def misses(self) -> int:
        return self._c().misses

    @property
    def maxsize(self) -> int:
        return self._c().maxsize


ROI_CACHE = _TlsRoiCache()


def clear_roi_cache() -> None:
    ROI_CACHE.clear()
    try:
        from cutora.core.rust_engine import rust_arena_clear
        rust_arena_clear()
    except Exception:
        pass
