# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""subprocess helpers: never use a shell, always hide the console on Windows."""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any


def creationflags(extra: int = 0) -> int:
    """Return CREATE_NO_WINDOW on Windows, combined with ``extra``."""
    flags = int(extra)
    if os.name == "nt":
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return flags


def run(cmd, **kwargs: Any):
    """``subprocess.run`` with a shell forbidden and no console window."""
    if kwargs.get("shell"):
        raise ValueError("subprocess shell=True is forbidden")
    if os.name == "nt" and "creationflags" not in kwargs:
        kwargs["creationflags"] = creationflags()
    return subprocess.run(cmd, **kwargs)


def run_filter_script(cmd_before, filter_graph: str, cmd_after, **kwargs: Any):
    """Run ffmpeg with the filter graph in a UTF-8 file.

    Windows CreateProcess rejects a command line longer than 32767 characters
    (WinError 206). The graph text is the same string ``-filter_complex`` would
    take. The file is UTF-8 without a BOM; a GBK script misreads a non-ASCII
    path inside the graph. The file is removed after ffmpeg returns.
    """
    fd, script = tempfile.mkstemp(prefix="cutora_fc_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(filter_graph)
        cmd = list(cmd_before) + ["-filter_complex_script", script] + list(cmd_after)
        return run(cmd, **kwargs)
    finally:
        try:
            os.remove(script)
        except OSError:
            pass


def popen(cmd, **kwargs: Any) -> subprocess.Popen:
    """``subprocess.Popen`` with a shell forbidden and no console window."""
    if kwargs.get("shell"):
        raise ValueError("subprocess shell=True is forbidden")
    if os.name == "nt" and "creationflags" not in kwargs:
        kwargs["creationflags"] = creationflags()
    return subprocess.Popen(cmd, **kwargs)
