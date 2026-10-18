# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Subtitle file formats: SRT, WebVTT, ASS/SSA, TXT, native JSON."""
from __future__ import annotations

import os

from cutora.core.log import get_logger
from cutora.subtitle.formats.ass import dump_ass, load_ass
from cutora.subtitle.formats.native_json import dump_json, load_json
from cutora.subtitle.formats.srt import dump_srt, load_srt
from cutora.subtitle.formats.txt import dump_txt, load_txt
from cutora.subtitle.formats.vtt import dump_vtt, load_vtt
from cutora.subtitle.model import Segment

logger = get_logger(__name__)

FORMATS = ("srt", "vtt", "ass", "ssa", "txt", "json")

_EXT = {
    ".srt": "srt",
    ".vtt": "vtt",
    ".webvtt": "vtt",
    ".ass": "ass",
    ".ssa": "ssa",
    ".txt": "txt",
    ".json": "json",
}


class SubtitleFormatError(ValueError):
    """Unknown format or missing path."""


def detect_format(path: str, explicit: str | None = None) -> str:
    if explicit:
        fmt = str(explicit).strip().lower().lstrip(".")
        if fmt in FORMATS:
            return "ass" if fmt == "ssa" else fmt
        raise SubtitleFormatError("不支持的字幕格式: %s" % explicit)
    ext = os.path.splitext(path or "")[1].lower()
    fmt = _EXT.get(ext)
    if not fmt:
        raise SubtitleFormatError("无法从扩展名判断字幕格式: %s" % path)
    return "ass" if fmt == "ssa" else fmt


def read_text(path: str) -> str:
    """UTF-8 with or without BOM. Binary junk is decoded with replacement."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def write_text(path: str, text: str, bom: bool = False) -> None:
    payload = (text or "").encode("utf-8")
    if bom:
        payload = b"\xef\xbb\xbf" + payload
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)


def load_subtitle(path: str, fmt: str | None = None) -> list[Segment]:
    """Load a subtitle file. Malformed content returns [] instead of raising."""
    kind = detect_format(path, fmt)
    try:
        text = read_text(path)
    except OSError:
        logger.warning("无法读取字幕文件: %s", path)
        return []
    try:
        return _parse(kind, text)
    except Exception:
        logger.warning("解析字幕失败: %s", path, exc_info=True)
        return []


def _parse(kind: str, text: str) -> list[Segment]:
    if kind == "srt":
        return load_srt(text)
    if kind == "vtt":
        return load_vtt(text)
    if kind in ("ass", "ssa"):
        return load_ass(text)
    if kind == "txt":
        return load_txt(text)
    if kind == "json":
        return load_json(text)
    return []


def dump_subtitle(
    segments: list[Segment],
    fmt: str,
    style: dict | None = None,
    play_w: int = 1920,
    play_h: int = 1080,
    karaoke: bool = True,
) -> str:
    kind = str(fmt or "").strip().lower()
    if kind == "ssa":
        kind = "ass"
    if kind == "srt":
        return dump_srt(segments)
    if kind == "vtt":
        return dump_vtt(segments, style=style, play_h=play_h)
    if kind == "ass":
        return dump_ass(
            segments, style=style, play_w=play_w, play_h=play_h, karaoke=karaoke)
    if kind == "txt":
        return dump_txt(segments)
    if kind == "json":
        return dump_json(segments)
    raise SubtitleFormatError("不支持的字幕格式: %s" % fmt)


def save_subtitle(
    path: str,
    segments: list[Segment],
    fmt: str | None = None,
    style: dict | None = None,
    play_w: int = 1920,
    play_h: int = 1080,
    karaoke: bool = True,
    bom: bool = False,
) -> str:
    kind = detect_format(path, fmt)
    text = dump_subtitle(
        segments, kind, style=style, play_w=play_w, play_h=play_h, karaoke=karaoke)
    write_text(path, text, bom=bool(bom) and kind == "srt")
    return os.path.abspath(path)
