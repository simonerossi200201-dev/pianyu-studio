# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable-pack helpers. No torch / PySide6 imports.

Windows venv launchers read ``runtime/venv/pyvenv.cfg`` ``home =`` to find
``python310.dll``. Internal packs rewrite that line to the bundled
``runtime/python`` on every launch so testers do not need a system Python.
"""
from __future__ import annotations

import os

PYVENV_REL = os.path.join("runtime", "venv", "pyvenv.cfg")
PYTHON_HOME_REL = os.path.join("runtime", "python")


def bundled_python_home(root: str) -> str:
    return os.path.abspath(os.path.join(root, PYTHON_HOME_REL))


def pyvenv_cfg_path(root: str) -> str:
    return os.path.abspath(os.path.join(root, PYVENV_REL))


def read_pyvenv_home(cfg_path: str) -> str | None:
    if not os.path.isfile(cfg_path):
        return None
    with open(cfg_path, encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if raw.lower().startswith("home") and "=" in raw:
                return raw.split("=", 1)[1].strip()
    return None


def rewrite_pyvenv_cfg(
    root: str, home: str | None = None, require_home_exists: bool = True,
) -> str:
    """Set ``home =`` to ``<root>/runtime/python`` (absolute, idempotent).

    Returns the home path written. Raises FileNotFoundError if the venv
    cfg is missing, or (when ``require_home_exists``) if the bundled
    interpreter is missing. Pack self-test writes a bogus home first
    with ``require_home_exists=False`` to prove the launcher actually
    consults this file.
    """
    root = os.path.abspath(root)
    dest_home = os.path.abspath(home) if home else bundled_python_home(root)
    cfg = pyvenv_cfg_path(root)
    if not os.path.isfile(cfg):
        raise FileNotFoundError(cfg)
    exe = os.path.join(dest_home, "python.exe")
    if require_home_exists and not os.path.isfile(exe):
        raise FileNotFoundError(exe)
    with open(cfg, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    found = False
    for line in lines:
        if line.strip().lower().startswith("home") and "=" in line:
            out.append("home = %s" % dest_home)
            found = True
        else:
            out.append(line)
    if not found:
        out.insert(0, "home = %s" % dest_home)
    text = "\n".join(out) + "\n"
    with open(cfg, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return dest_home
