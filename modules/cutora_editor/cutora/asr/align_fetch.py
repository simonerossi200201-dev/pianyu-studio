# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""One download script per language that has a pinned, enabled aligner.

Stitch calls ``fetch_plan(code)`` for the language it is about to use.
The plan is that language only. Languages already on disk, frozen local
models (en/it), and rows the license gate keeps disabled (da/hr/gl/vi)
return nothing, so nothing is downloaded.
"""
from __future__ import annotations

import os

# Same pair as engine.ALIGN_MODEL_BY_LANG. Those two load the frozen
# local files, not the extra Hugging Face rows that share the code.
_FROZEN = ("en", "it")


def download_script(code: str) -> dict | None:
    """The pinned download recipe for this language, or None.

    Presence on disk does not matter. None means this language is not
    fetched from Hugging Face (no row, disabled, or local-only).
    """
    row = _row_for_stitch(code)
    if not _fetchable(row):
        return None
    return _script_from_row(row)


def all_download_scripts() -> list[dict]:
    """Every language whose stitch aligner can be fetched on demand."""
    from cutora.asr.languages import load_catalog
    out = []
    for lang in load_catalog():
        script = download_script(str(lang.get("code") or ""))
        if script is not None:
            out.append(script)
    return out


def fetch_plan(code: str) -> dict | None:
    """The script to run now, or None when this language needs no download."""
    script = download_script(code)
    if script is None:
        return None
    from cutora.asr.models import get_model
    row = get_model(script["id"])
    if row is None or weights_present(row):
        return None
    return script


def weights_present(row: dict) -> bool:
    """True when every pinned file is on disk at the registered size.

    Size is enough to decide "don't download". The full SHA256 still runs
    inside the downloader for files that are actually fetched.
    """
    from cutora.asr.models import iter_files, model_folder
    folder = model_folder(row)
    files = iter_files(row)
    if not files:
        return False
    for item in files:
        path = os.path.join(folder, str(item.get("filename") or ""))
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size <= 0:
            return False
        expect = item.get("size")
        if expect is not None and int(size) != int(expect):
            return False
    return True


def _row_for_stitch(code: str):
    raw = (code or "").strip().lower()
    if not raw or raw == "auto":
        return None
    from cutora.asr.models import align_row_for_language, get_model
    if raw in _FROZEN:
        return get_model("align-%s" % raw)
    return align_row_for_language(raw)


def _fetchable(row: dict | None) -> bool:
    if not row or not row.get("enabled"):
        return False
    if row.get("local_only") or row.get("gated"):
        return False
    if not str(row.get("repo") or "").strip():
        return False
    if not str(row.get("revision") or "").strip():
        return False
    files = list(row.get("files") or [])
    if not files:
        return False
    for item in files:
        if not isinstance(item, dict):
            return False
        name = str(item.get("filename") or "")
        digest = str(item.get("sha256") or "").strip().lower()
        if not name or len(digest) != 64:
            return False
        try:
            int(digest, 16)
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            return False
        if size <= 0:
            return False
    return True


def _script_from_row(row: dict) -> dict:
    from cutora.asr.languages import get_language
    code = str(row.get("language") or "").strip().lower()
    lang = get_language(code) or {}
    files = []
    total = 0
    for item in row.get("files") or []:
        size = int(item.get("size") or 0)
        total += size
        files.append({
            "filename": str(item.get("filename") or ""),
            "size": size,
            "sha256": str(item.get("sha256") or "").strip().lower(),
        })
    return {
        "id": str(row.get("id") or ""),
        "language": code,
        "name_zh": str(lang.get("name_zh") or row.get("title_zh") or code),
        "repo": str(row.get("repo") or ""),
        "revision": str(row.get("revision") or ""),
        "license": str(row.get("license") or ""),
        "size_bytes": total,
        "files": files,
    }
