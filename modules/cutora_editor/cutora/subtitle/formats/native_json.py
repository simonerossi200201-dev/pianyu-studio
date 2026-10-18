# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native subtitle JSON: segments with words / speaker / lang."""
from __future__ import annotations

import json

from cutora.subtitle.model import (
    Segment,
    segment_from_dict,
    segment_to_native_dict,
)

KIND = "cutora.subtitle"


def load_json(text: str) -> list[Segment]:
    """Parse native JSON. Malformed documents return []."""
    try:
        data = json.loads(text or "")
    except Exception:
        return []
    items = _items(data)
    out: list[Segment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(segment_from_dict(item))
        except Exception:
            continue
    return out


def _items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        segs = data.get("segments")
        if isinstance(segs, list):
            return segs
    return []


def dump_json(segments: list[Segment]) -> str:
    payload = {
        "version": 1,
        "kind": KIND,
        "segments": [segment_to_native_dict(s) for s in (segments or [])],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
