# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Product identity. User-visible names live here; do not scatter string literals."""
from __future__ import annotations

import os

APP_NAME = "CUTORA"
APP_DISPLAY_NAME = "片语 Studio"
APP_VERSION = "7.0.0"
BUILD_DATE = "2026-09-08"
ORGANIZATION_NAME = "CUTORA"
APP_USER_MODEL_ID = "CUTORA.Studio"
TAGLINE_ZH = "让每一句话，找到它的位置。"
TAGLINE_EN = "Every word finds its place."
PREDECESSOR = "前身 AutoSub v6"

# Qt file-dialog filters. Save still writes asproj-shaped JSON; extension is .cutora.
PROJECT_FILTER = "片语工程 (*.cutora *.asproj *.asproj.json);;所有文件 (*)"
PROJECT_SAVE_FILTER = "片语工程 (*.cutora);;片语工程 (*.asproj *.asproj.json);;所有文件 (*)"
PROJECT_OPEN_FILTER = "片语工程 (*.cutora *.asproj *.asproj.json);;所有文件 (*)"
MEDIA_OR_PROJECT_FILTER = (
    "视频或工程 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.cutora *.asproj.json *.asproj);;"
    "片语工程 (*.cutora *.asproj.json *.asproj);;"
    "视频文件 (*.mp4 *.mov *.mkv *.avi);;所有文件 (*)"
)

PROJECT_EXTS = (".cutora", ".asproj.json", ".asproj")


def is_project_path(path: str) -> bool:
    n = os.path.normcase(str(path or ""))
    return n.endswith(".cutora") or n.endswith(".asproj.json") or n.endswith(".asproj")


def ensure_save_extension(path: str) -> str:
    """Default new saves to ``.cutora``; keep an explicit legacy extension."""
    if not path:
        return path
    n = os.path.normcase(path)
    if n.endswith(PROJECT_EXTS):
        return path
    return path + ".cutora"


def format_window_title(project_path: str | None = None, extra: str | None = None,
                        dirty: bool = False) -> str:
    """``片语 Studio`` / ``片语 Studio — <name>``; unsaved adds a leading ``*``."""
    if extra:
        title = "%s — %s" % (APP_DISPLAY_NAME, extra)
    elif project_path:
        title = "%s — %s" % (APP_DISPLAY_NAME, os.path.basename(project_path))
    else:
        title = APP_DISPLAY_NAME
    if dirty:
        title = "*" + title
    return title
