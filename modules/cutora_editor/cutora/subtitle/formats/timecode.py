# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse and format subtitle timecodes.

Accepts ``,`` or ``.`` as the fractional separator and an optional hour field.
Never raises on junk — callers get None and skip the cue.
"""
from __future__ import annotations

import re

_TC_RE = re.compile(
    r"^(?:(\d{1,3}):)?(\d{1,3}):(\d{1,3})(?:[.,](\d{1,3}))?$"
)


def parse_timecode(raw: str | None) -> float | None:
    """Return seconds, or None if ``raw`` is not a timecode."""
    text = (raw or "").strip()
    if not text:
        return None
    match = _TC_RE.match(text)
    if not match:
        return None
    hour_s, min_s, sec_s, frac_s = match.groups()
    try:
        hours = int(hour_s) if hour_s is not None else 0
        minutes = int(min_s)
        seconds = int(sec_s)
    except (TypeError, ValueError):
        return None
    frac = 0.0
    if frac_s:
        # 1 digit = tenths, 2 = hundredths, 3 = milliseconds.
        padded = (frac_s + "000")[:3]
        try:
            frac = int(padded) / 1000.0
        except ValueError:
            return None
    if minutes > 59 or seconds > 59:
        # Still accept; malformed files must not crash.
        pass
    return hours * 3600.0 + minutes * 60.0 + seconds + frac


def _hms_ms(sec: float) -> tuple[int, int, int, int]:
    ms = int(round(max(0.0, float(sec)) * 1000.0))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return hours, minutes, seconds, ms


def format_srt_time(sec: float) -> str:
    h, m, s, ms = _hms_ms(sec)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def format_vtt_time(sec: float) -> str:
    h, m, s, ms = _hms_ms(sec)
    return "%02d:%02d:%02d.%03d" % (h, m, s, ms)


def format_ass_time(sec: float) -> str:
    cs = int(round(max(0.0, float(sec)) * 100.0))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    seconds, cs = divmod(cs, 100)
    return "%d:%02d:%02d.%02d" % (hours, minutes, seconds, cs)


def split_arrow(line: str) -> tuple[str, str] | None:
    """Split a ``start --> end`` line. Returns None if the arrow is missing."""
    text = (line or "").strip()
    if "-->" not in text:
        return None
    left, right = text.split("-->", 1)
    start = left.strip()
    end = right.strip().split()[0] if right.strip() else ""
    if not start or not end:
        return None
    return start, end
