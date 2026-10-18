# -*- coding: utf-8 -*-
"""Per-thread mappings for export compose workers.

The render path used to keep module-level caches (_SE_* / _IMAGE_CACHE /
_DBI_VIDEO_CAPS / ROI / layout). Those are not safe to share across
threads: VideoCapture is not, and a cache hit on another thread's QImage
is a data race. Each compose worker gets its own dict/cache.
"""
from __future__ import annotations

import threading
from collections.abc import MutableMapping

_TLS = threading.local()


class ThreadLocalMapping(MutableMapping):
    """dict-like object whose storage is ``threading.local``."""

    def __init__(self, slot: str, factory):
        self._slot = str(slot)
        self._factory = factory

    def _data(self):
        d = getattr(_TLS, self._slot, None)
        if d is None:
            d = self._factory()
            setattr(_TLS, self._slot, d)
        return d

    def __getitem__(self, key):
        return self._data()[key]

    def __setitem__(self, key, value):
        self._data()[key] = value

    def __delitem__(self, key):
        del self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __contains__(self, key):
        return key in self._data()

    def get(self, key, default=None):
        return self._data().get(key, default)

    def pop(self, key, *rest):
        return self._data().pop(key, *rest)

    def clear(self):
        self._data().clear()

    def values(self):
        return self._data().values()

    def items(self):
        return self._data().items()

    def keys(self):
        return self._data().keys()

    def setdefault(self, key, default=None):
        return self._data().setdefault(key, default)
