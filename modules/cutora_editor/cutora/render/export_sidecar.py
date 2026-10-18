# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidecars next to a finished export: SRT/VTT/ASS, cover frame, .export.json."""
from __future__ import annotations

import json
import os
import subprocess

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.log import get_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.render.export_sidecar")

SETTING_SRT = "sidecar_srt"
SETTING_VTT = "sidecar_vtt"
SETTING_ASS = "sidecar_ass"
SETTING_COVER = "sidecar_cover"
SETTING_COVER_BURN = "sidecar_cover_burn"
SETTING_REPORT = "sidecar_report"
SETTING_CHAPTERS = "sidecar_chapters"


def sidecar_flags(settings: dict | None = None) -> dict:
    s = settings if isinstance(settings, dict) else (_load_app_settings() or {})
    return {
        "srt": bool(s.get(SETTING_SRT)),
        "vtt": bool(s.get(SETTING_VTT)),
        "ass": bool(s.get(SETTING_ASS)),
        "cover": bool(s.get(SETTING_COVER)),
        "cover_burn": bool(s.get(SETTING_COVER_BURN)),
        "report": bool(s.get(SETTING_REPORT)),
        "chapters": bool(s.get(SETTING_CHAPTERS)),
    }


def save_sidecar_flags(flags: dict) -> None:
    s = _load_app_settings() or {}
    s[SETTING_SRT] = bool(flags.get("srt", False))
    s[SETTING_VTT] = bool(flags.get("vtt", False))
    s[SETTING_ASS] = bool(flags.get("ass", False))
    s[SETTING_COVER] = bool(flags.get("cover", False))
    s[SETTING_COVER_BURN] = bool(flags.get("cover_burn", False))
    s[SETTING_REPORT] = bool(flags.get("report", False))
    s[SETTING_CHAPTERS] = bool(flags.get("chapters", False))
    _save_app_settings(s)


def _stem(path: str) -> str:
    return os.path.splitext(path)[0]


def write_subtitles(output_path: str, task: dict, flags: dict | None = None) -> list[str]:
    from cutora.subtitle.formats import dump_subtitle
    from cutora.subtitle.model import segments_from_dicts

    flags = flags or sidecar_flags()
    segs = segments_from_dicts(task.get("segments") or [])
    written = []
    if not segs:
        return written
    stem = _stem(output_path)
    mapping = (("srt", flags.get("srt")), ("vtt", flags.get("vtt")), ("ass", flags.get("ass")))
    for fmt, on in mapping:
        if not on:
            continue
        text = dump_subtitle(segs, fmt)
        dest = stem + "." + fmt
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
        written.append(dest)
    return written


def write_cover(output_path: str, task: dict, flags: dict | None = None, ffmpeg: str | None = None) -> str | None:
    flags = flags or sidecar_flags()
    if not flags.get("cover"):
        return None
    if ffmpeg is None:
        from cutora.core.config import FFMPEG_PATH
        ffmpeg = FFMPEG_PATH
    dest = _stem(output_path) + ".jpg"
    src = output_path if flags.get("cover_burn") else (task.get("video") or output_path)
    if not src or not os.path.isfile(src):
        src = output_path
    if not os.path.isfile(src):
        return None
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-ss", "1", "-i", src,
        "-frames:v", "1", "-q:v", "3", dest,
    ]
    r = _no_window_run(cmd, capture_output=True, timeout=30)
    if r.returncode != 0 or not os.path.isfile(dest):
        cmd[cmd.index("-ss") + 1] = "0"
        r = _no_window_run(cmd, capture_output=True, timeout=30)
    if r.returncode != 0 or not os.path.isfile(dest):
        logger.warning("cover frame failed: %s", (r.stderr or b"")[-200:])
        return None
    return dest


def _fmt_chapter_clock(seconds: float) -> str:
    t = max(0.0, float(seconds))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def chapters_from_task(task: dict, duration_s: float | None = None) -> list[dict]:
    from cutora.model.markers import sorted_markers
    rows = []
    markers = sorted_markers(task) if isinstance(task, dict) else []
    times = []
    for rec in markers:
        try:
            t = float(rec.get("t") or 0.0)
        except (TypeError, ValueError):
            continue
        name = str(rec.get("name") or "").strip() or "标记"
        times.append((t, name))
    if not times:
        return rows
    end_cap = float(duration_s) if duration_s is not None else None
    if end_cap is None:
        end_cap = times[-1][0] + 1.0
        segs = list((task or {}).get("segments") or [])
        for seg in segs:
            if isinstance(seg, dict):
                try:
                    end_cap = max(end_cap, float(seg.get("end") or 0.0))
                except (TypeError, ValueError):
                    pass
    for i, (t, name) in enumerate(times):
        nxt = times[i + 1][0] if i + 1 < len(times) else end_cap
        if nxt <= t:
            nxt = t + 0.04
        rows.append({"t": t, "end": nxt, "name": name})
    return rows


def write_chapters(output_path: str, task: dict, flags: dict | None = None,
                   duration_s: float | None = None) -> list[str]:
    flags = flags or sidecar_flags()
    if not flags.get("chapters"):
        return []
    rows = chapters_from_task(task, duration_s=duration_s)
    if not rows:
        logger.info("chapters flag on but no markers; skip")
        return []
    stem = _stem(output_path)
    written = []
    txt = stem + ".chapters.txt"
    lines = ["%s %s" % (_fmt_chapter_clock(r["t"]), r["name"]) for r in rows]
    with open(txt, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    written.append(txt)
    meta = stem + ".ffmeta"
    parts = [";FFMETADATA1"]
    for r in rows:
        start_ms = int(round(float(r["t"]) * 1000.0))
        end_ms = int(round(float(r["end"]) * 1000.0))
        title = str(r["name"]).replace("\\", "\\\\").replace("=", "\\=").replace(
            ";", "\\;").replace("#", "\\#").replace("\n", " ")
        parts.extend([
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            "START=%d" % start_ms,
            "END=%d" % max(start_ms + 1, end_ms),
            "title=%s" % title,
        ])
    with open(meta, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(parts) + "\n")
    written.append(meta)
    return written


def write_report(output_path: str, payload: dict) -> str:
    dest = _stem(output_path) + ".export.json"
    body = dict(payload)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, dest)
    return dest


def write_all(output_path: str, task: dict, meta: dict | None = None, flags: dict | None = None) -> dict:
    """Best-effort. Never raises into the export success path."""
    out = {"subtitles": [], "cover": None, "report": None, "chapters": []}
    try:
        flags = flags or sidecar_flags()
        sidecar_path = output_path
        dest = (task or {}).get("_export_dest_folder")
        if dest:
            from cutora.render.export_dest import sidecar_base_path
            sidecar_path = sidecar_base_path(task, output_path) + os.path.splitext(output_path)[1]
        out["subtitles"] = write_subtitles(sidecar_path, task, flags)
        out["cover"] = write_cover(sidecar_path, task, flags)
        dur = None
        try:
            dur = float((meta or {}).get("duration_s") or 0.0) or None
        except (TypeError, ValueError):
            dur = None
        out["chapters"] = write_chapters(
            sidecar_path, task, flags, duration_s=dur)
        payload = dict(meta or {})
        payload.setdefault("output", output_path)
        payload.setdefault("source", (task or {}).get("video"))
        payload.setdefault("subtitle_count", len((task or {}).get("segments") or []))
        payload["sidecars"] = list(out["subtitles"]) + list(out.get("chapters") or [])
        if out["cover"]:
            payload["cover"] = out["cover"]
        if flags.get("report"):
            out["report"] = write_report(sidecar_path, payload)
    except Exception as exc:
        logger.warning("sidecar write failed: %s", exc)
    return out
