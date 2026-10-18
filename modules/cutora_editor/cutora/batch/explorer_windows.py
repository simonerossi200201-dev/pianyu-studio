# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T25: list folders currently open in Explorer. Injectable for tests."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlparse


def location_url_to_path(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("file:"):
        parsed = urlparse(raw)
        path = unquote(parsed.path or "")
        if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
            path = path[1:]
        path = path.replace("/", os.sep)
        return os.path.normpath(path)
    if os.path.isdir(raw):
        return os.path.normpath(raw)
    return None


def list_open_explorer_folders(windows_factory=None) -> list[str]:
    """Return unique local paths of open Explorer windows.

    ``windows_factory`` if given must return an iterable of objects with
    ``LocationURL`` (string or callable).
    """
    rows = []
    if windows_factory is not None:
        windows = windows_factory()
    else:
        windows = _com_windows()
    for w in windows or []:
        url = getattr(w, "LocationURL", None)
        if callable(url):
            url = url()
        path = location_url_to_path(str(url or ""))
        if path and os.path.isdir(path) and path not in rows:
            rows.append(path)
    return rows


def _com_windows():
    try:
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("Shell.Application")
        return list(shell.Windows())
    except Exception:
        return []
