# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""WebVTT reader / writer. STYLE / REGION blocks are ignored on read."""
from __future__ import annotations

from cutora.subtitle.formats.timecode import (
    format_vtt_time,
    parse_timecode,
    split_arrow,
)
from cutora.subtitle.model import Segment

_SKIP_HEAD = frozenset({"style", "region", "note"})


def load_vtt(text: str) -> list[Segment]:
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    i = 0
    # Optional WEBVTT header (malformed files without it still parse).
    if i < len(lines) and lines[i].lstrip("\ufeff").upper().startswith("WEBVTT"):
        i += 1
        while i < len(lines) and lines[i].strip() != "":
            i += 1
    out: list[Segment] = []
    n = len(lines)
    while i < n:
        raw = lines[i].strip()
        if raw == "":
            i += 1
            continue
        kind = raw.split(None, 1)[0].rstrip(":").lower()
        if kind in _SKIP_HEAD:
            i = _skip_block(lines, i)
            continue
        cue, i = _read_cue(lines, i)
        if cue is not None:
            out.append(cue)
    return out


def _skip_block(lines: list[str], i: int) -> int:
    i += 1
    while i < len(lines) and lines[i].strip() != "":
        i += 1
    return i


def _read_cue(lines: list[str], i: int) -> tuple[Segment | None, int]:
    line = lines[i].strip()
    times = split_arrow(line)
    if times is None:
        # Optional identifier line, then the timing line.
        i += 1
        if i >= len(lines):
            return None, i
        times = split_arrow(lines[i].strip())
        if times is None:
            return None, _skip_block(lines, i)
    start = parse_timecode(times[0])
    end = parse_timecode(times[1])
    i += 1
    payload: list[str] = []
    while i < len(lines) and lines[i].strip() != "":
        payload.append(lines[i])
        i += 1
    if start is None or end is None:
        return None, i
    text = "\n".join(payload).strip()
    return Segment(id="", start=start, end=end, text=text, words=[]), i


def dump_vtt(
    segments: list[Segment],
    style: dict | None = None,
    play_h: int = 1080,
) -> str:
    setting = _line_setting(style, play_h)
    lines = ["WEBVTT", ""]
    for seg in segments or []:
        arrow = "%s --> %s%s" % (
            format_vtt_time(seg.start),
            format_vtt_time(seg.end),
            setting,
        )
        lines.append(arrow)
        lines.append((seg.text or "").strip("\n"))
        lines.append("")
    return "\n".join(lines)


def _line_setting(style: dict | None, play_h: int) -> str:
    if not style or style.get("pos_y") is None:
        return ""
    height = max(1, int(play_h or 1080))
    try:
        pos_y = float(style.get("pos_y"))
    except (TypeError, ValueError):
        return ""
    pct = int(round(100.0 * pos_y / height))
    pct = max(0, min(100, pct))
    return " line:%d%%" % pct
