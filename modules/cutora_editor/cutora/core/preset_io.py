# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T11d named-preset JSON I/O. Style packs stay directories; this is the JSON store.

Export profiles, text-layer templates, and any later named preset list must
call these helpers. Do not copy dump/read/write again.
"""
from __future__ import annotations

import json
import os


def dump_presets(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_preset_file(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {"version": 1, "presets": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"version": 1, "presets": []}
    if not isinstance(data, dict):
        return {"version": 1, "presets": []}
    if not isinstance(data.get("presets"), list):
        data["presets"] = []
    return data


def write_preset_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dump_presets(data))
    os.replace(tmp, path)
