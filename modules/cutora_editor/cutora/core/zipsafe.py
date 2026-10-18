# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Zip extract that rejects absolute paths and .. (zip-slip)."""
from __future__ import annotations

import os
import shutil
import zipfile


class ZipSlipError(ValueError):
    """A zip member would extract outside the destination directory."""


def _is_unsafe_name(name: str) -> bool:
    raw = (name or "").replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("//"):
        return True
    if len(raw) >= 2 and raw[1] == ":":
        return True
    parts = raw.split("/")
    return any(p == ".." for p in parts)


def member_dest(dest: str, name: str) -> str:
    """Return the real path for ``name`` under ``dest``, or raise ZipSlipError."""
    dest_real = os.path.realpath(dest)
    dest_prefix = dest_real if dest_real.endswith(os.sep) else dest_real + os.sep
    if _is_unsafe_name(name):
        raise ZipSlipError("zip path escapes dest: %s" % name)
    rel = (name or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p and p != "."]
    target = os.path.realpath(os.path.join(dest_real, *parts) if parts else dest_real)
    if target != dest_real and not target.startswith(dest_prefix):
        raise ZipSlipError("zip path escapes dest: %s" % name)
    return target


def safe_extractall(zf: zipfile.ZipFile, dest: str) -> None:
    """Like ZipFile.extractall but refuse members that escape ``dest``."""
    dest_real = os.path.realpath(dest)
    os.makedirs(dest_real, exist_ok=True)
    for info in zf.infolist():
        name = info.filename
        target = member_dest(dest_real, name)
        is_dir = info.is_dir() if hasattr(info, "is_dir") else str(name).endswith("/")
        if is_dir or str(name).replace("\\", "/").endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target) or dest_real, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
