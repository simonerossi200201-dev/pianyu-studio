# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON load with size cap and optional duplicate-key rejection."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional, TextIO, Union, cast

MAX_BYTES = 64 * 1024 * 1024

PathLike = Union[str, os.PathLike]


class DuplicateKeyError(ValueError):
    """Raised when ``reject_duplicate_keys`` sees the same key twice."""


def _reject_dup_hook(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateKeyError("duplicate JSON key: %r" % (key,))
        out[key] = value
    return out


def loads(
    text: Union[str, bytes],
    *,
    max_bytes: int = MAX_BYTES,
    reject_duplicate_keys: bool = False,
) -> Any:
    """Parse a JSON string. Rejects payloads larger than ``max_bytes``."""
    if isinstance(text, bytes):
        if len(text) > max_bytes:
            raise ValueError("JSON payload exceeds %d bytes" % max_bytes)
        text = text.decode("utf-8")
    elif isinstance(text, str):
        # Cheap length check; UTF-8 JSON is ASCII-heavy so len ≈ bytes.
        if len(text) > max_bytes:
            raise ValueError("JSON payload exceeds %d bytes" % max_bytes)
    else:
        raise TypeError("JSON text must be str or bytes")
    hook: Optional[Callable] = _reject_dup_hook if reject_duplicate_keys else None
    return json.loads(text, object_pairs_hook=hook)


def load(
    fp: Union[TextIO, PathLike],
    *,
    max_bytes: int = MAX_BYTES,
    reject_duplicate_keys: bool = False,
) -> Any:
    """Parse JSON from a file object or path."""
    if isinstance(fp, (str, os.PathLike)):
        path = os.fspath(fp)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > max_bytes:
            raise ValueError("JSON file exceeds %d bytes: %s" % (max_bytes, path))
        with open(path, "r", encoding="utf-8") as fh:
            return loads(
                fh.read(),
                max_bytes=max_bytes,
                reject_duplicate_keys=reject_duplicate_keys,
            )
    data = fp.read()
    if isinstance(data, bytes):
        return loads(
            data, max_bytes=max_bytes, reject_duplicate_keys=reject_duplicate_keys
        )
    return loads(
        cast(str, data),
        max_bytes=max_bytes,
        reject_duplicate_keys=reject_duplicate_keys,
    )


class ProjectEncoder(json.JSONEncoder):
    """Shared by project save and autosave. Qt geometry → JSON arrays."""

    def default(self, obj: Any) -> Any:
        name = type(obj).__name__
        if name in ("QRectF", "QRect"):
            return [obj.x(), obj.y(), obj.width(), obj.height()]
        if name in ("QPointF", "QPoint"):
            return [obj.x(), obj.y()]
        if name == "QSize":
            return [obj.width(), obj.height()]
        return super().default(obj)


def dump(obj: Any, fp: TextIO, **kwargs: Any) -> None:
    kwargs.setdefault("ensure_ascii", False)
    json.dump(obj, fp, **kwargs)


def dumps(obj: Any, **kwargs: Any) -> str:
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(obj, **kwargs)


def dump_project(obj: Any, fp: TextIO, **kwargs: Any) -> None:
    """Write a project/autosave payload. Same encoder as Ctrl+S."""
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("indent", 2)
    kwargs["cls"] = ProjectEncoder
    json.dump(obj, fp, **kwargs)
