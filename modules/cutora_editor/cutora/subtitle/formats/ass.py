# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ASS/SSA reader (Dialogue text + times only) and writer (style + optional \\k)."""
from __future__ import annotations

import re

from cutora.subtitle.formats.timecode import format_ass_time, parse_timecode
from cutora.subtitle.model import Segment

_TAG_RE = re.compile(r"\{[^}]*\}")
_DIALOGUE_RE = re.compile(r"^\s*(?:dialogue|comment)\s*:", re.I)


def load_ass(text: str) -> list[Segment]:
    """Read Dialogue events. Styles / comments / junk are ignored."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[Segment] = []
    for raw in body.split("\n"):
        if not _DIALOGUE_RE.match(raw):
            continue
        if raw.lstrip().lower().startswith("comment"):
            continue
        seg = _parse_dialogue(raw)
        if seg is not None:
            out.append(seg)
    return out


def _parse_dialogue(raw: str) -> Segment | None:
    try:
        payload = raw.split(":", 1)[1]
    except IndexError:
        return None
    # Layer/Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    parts = payload.split(",", 9)
    if len(parts) < 10:
        return None
    start = parse_timecode(parts[1].strip())
    end = parse_timecode(parts[2].strip())
    if start is None or end is None:
        return None
    text = _strip_tags(parts[9])
    return Segment(id="", start=start, end=end, text=text, words=[])


def _strip_tags(text: str) -> str:
    out = _TAG_RE.sub("", text or "")
    out = out.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    return out.strip()


def dump_ass(
    segments: list[Segment],
    style: dict | None = None,
    play_w: int = 1920,
    play_h: int = 1080,
    karaoke: bool = True,
) -> str:
    header = _header(style, int(play_w or 1920), int(play_h or 1080))
    events = ["[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
              "MarginV, Effect, Text"]
    for seg in segments or []:
        body = _event_text(seg, karaoke=karaoke)
        events.append(
            "Dialogue: 0,%s,%s,Default,,0,0,0,,%s"
            % (format_ass_time(seg.start), format_ass_time(seg.end), body)
        )
    return header + "\n".join(events) + "\n"


def _header(style: dict | None, play_w: int, play_h: int) -> str:
    sty = style or {}
    font = str(sty.get("font_family") or "Arimo")
    try:
        size = int(float(sty.get("fontsize") or 60))
    except (TypeError, ValueError):
        size = 60
    primary = html_to_ass_color(str(sty.get("color_text") or "#FFFFFF"))
    outline_c = html_to_ass_color(
        str(sty.get("color_outline") or "#000000"),
        opacity=_opacity(sty, "opacity_outline", 255),
    )
    back = html_to_ass_color(
        str(sty.get("color_shadow") or "#000000"),
        opacity=_opacity(sty, "opacity_shadow", 0),
    )
    bold = -1 if sty.get("bold") else 0
    italic = -1 if sty.get("italic") else 0
    outline = _outline_w(sty)
    shadow = 1 if sty.get("shadow_enabled") else 0
    align, margin_v = _align_margin(sty, play_w, play_h)
    style_line = (
        "Style: Default,%s,%d,%s,&H000000FF,%s,%s,%d,%d,0,0,100,100,0,0,1,"
        "%d,%d,%d,10,10,%d,1"
        % (font, size, primary, outline_c, back, bold, italic,
           outline, shadow, align, margin_v)
    )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\n"
        "PlayResY: %d\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "%s\n"
        "\n" % (play_w, play_h, style_line)
    )


def html_to_ass_color(html: str, opacity: int = 255) -> str:
    """``#RRGGBB`` → ``&HAABBGGRR`` (ASS 00 = opaque)."""
    raw = (html or "").strip().lstrip("#")
    if len(raw) == 8:
        aa, rr, gg, bb = raw[0:2], raw[2:4], raw[4:6], raw[6:8]
        return "&H%s%s%s%s" % (aa.upper(), bb.upper(), gg.upper(), rr.upper())
    if len(raw) != 6:
        raw = "FFFFFF"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    alpha = max(0, min(255, 255 - int(opacity)))
    return "&H%02X%s%s%s" % (alpha, bb.upper(), gg.upper(), rr.upper())


def _opacity(style: dict, key: str, default: int) -> int:
    try:
        return max(0, min(255, int(style.get(key, default))))
    except (TypeError, ValueError):
        return default


def _outline_w(style: dict) -> int:
    if not style.get("use_stroke"):
        return 0
    try:
        return max(0, int(float(style.get("border_width") or 0)))
    except (TypeError, ValueError):
        return 0


def _align_margin(style: dict, play_w: int, play_h: int) -> tuple[int, int]:
    w = max(1, int(play_w or 1920))
    h = max(1, int(play_h or 1080))
    try:
        pos_y = float(style.get("pos_y") if style.get("pos_y") is not None
                      else h * 0.85)
    except (TypeError, ValueError):
        pos_y = h * 0.85
    try:
        pos_x = float(style.get("pos_x") if style.get("pos_x") is not None
                      else w * 0.5)
    except (TypeError, ValueError):
        pos_x = w * 0.5
    col = 1 if pos_x < w * 0.33 else (3 if pos_x > w * 0.66 else 2)
    if pos_y < h * 0.33:
        return 6 + col, max(0, int(pos_y))
    if pos_y > h * 0.66:
        return col, max(0, int(h - pos_y))
    return 3 + col, 0


def _event_text(seg: Segment, karaoke: bool) -> str:
    if karaoke and seg.words:
        return _karaoke(seg)
    return _escape(seg.text or "")


def _karaoke(seg: Segment) -> str:
    parts: list[str] = []
    for i, word in enumerate(seg.words):
        dur = max(0.0, float(word.end) - float(word.start))
        cs = max(0, int(round(dur * 100.0)))
        piece = word.text or ""
        if i > 0 and piece and not piece[0].isspace():
            piece = " " + piece
        parts.append(r"{\k%d}%s" % (cs, _escape(piece)))
    return "".join(parts) or _escape(seg.text or "")


def _escape(text: str) -> str:
    out = (text or "").replace("\\", r"\\")
    out = out.replace("{", r"\{").replace("}", r"\}")
    return out.replace("\n", r"\N")
