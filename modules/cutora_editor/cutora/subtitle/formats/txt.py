# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain-text subtitles: sentence split on read, stitcher ``NN|text`` on write."""
from __future__ import annotations

import re

from cutora.subtitle.model import Segment

# Same numbered-block idea as script_stitcher.parse_scripts (format A).
_NUM_RE = re.compile(
    r"^(\d+|[^\W\d_]+\s*\d+)\s*[|:：\t]\s*(.+)$",
    re.S | re.I | re.UNICODE,
)
_SENT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def load_txt(text: str) -> list[Segment]:
    """Split into sentences for dump/load. Import-file path does not store these."""
    sentences = split_sentences(text)
    out: list[Segment] = []
    for i, sentence in enumerate(sentences):
        out.append(Segment(
            id="",
            start=float(i),
            end=float(i + 1),
            text=sentence,
            words=[],
        ))
    return out


def split_sentences(text: str) -> list[str]:
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return []
    numbered = _numbered_blocks(body)
    if numbered is not None:
        return numbered
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paras) > 1:
        out: list[str] = []
        for para in paras:
            out.extend(_split_one(para))
        return out
    return _split_one(body)


def _numbered_blocks(body: str) -> list[str] | None:
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    from_lines = _match_all_numbered(lines)
    if from_lines is not None:
        return from_lines
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return _match_all_numbered(paras)


def _match_all_numbered(blocks: list[str]) -> list[str] | None:
    if not blocks:
        return None
    found: list[str] = []
    for block in blocks:
        match = _NUM_RE.match(block)
        if not match:
            return None
        found.append(match.group(2).strip())
    return found or None


def _split_one(text: str) -> list[str]:
    parts = _SENT_RE.split((text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def dump_txt(segments: list[Segment]) -> str:
    lines = []
    for i, seg in enumerate(segments or [], 1):
        payload = (seg.text or "").replace("\n", " ").strip()
        lines.append("%02d|%s" % (i, payload))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
