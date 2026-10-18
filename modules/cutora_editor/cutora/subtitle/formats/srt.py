# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""SRT reader / writer. UTF-8 with or without BOM on read; write no BOM by default."""
from __future__ import annotations

from cutora.subtitle.formats.timecode import (
    format_srt_time,
    parse_timecode,
    split_arrow,
)
from cutora.subtitle.model import Segment


def load_srt(text: str) -> list[Segment]:
    """Parse SRT. Malformed cues are skipped; never raises."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks = re_split_blocks(body)
    out: list[Segment] = []
    for block in chunks:
        seg = _parse_block(block)
        if seg is not None:
            out.append(seg)
    return out


def re_split_blocks(body: str) -> list[str]:
    parts = []
    buf: list[str] = []
    for line in body.split("\n"):
        if line.strip() == "":
            if buf:
                parts.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        parts.append("\n".join(buf))
    return parts


def _parse_block(block: str) -> Segment | None:
    lines = [ln for ln in (block or "").split("\n") if ln is not None]
    if not lines:
        return None
    # Optional numeric index on the first line.
    if lines[0].strip().isdigit() and len(lines) >= 2:
        lines = lines[1:]
    if not lines:
        return None
    times = split_arrow(lines[0])
    if times is None:
        return None
    start = parse_timecode(times[0])
    end = parse_timecode(times[1])
    if start is None or end is None:
        return None
    payload = "\n".join(lines[1:]).strip()
    return Segment(id="", start=start, end=end, text=payload, words=[])


def dump_srt(segments: list[Segment]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments or [], 1):
        text = (seg.text or "").strip("\n")
        lines.append(str(i))
        lines.append("%s --> %s" % (format_srt_time(seg.start), format_srt_time(seg.end)))
        lines.append(text)
        lines.append("")
    return "\n".join(lines)
