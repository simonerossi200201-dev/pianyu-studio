# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export platform presets. Suggest canvas / duration / filename suffix; never lock CRF."""
from __future__ import annotations

import copy

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.log import get_logger

log = get_logger("cutora.render.export_platform")

SETTING_ID = "export_platform_id"
DEFAULT_ID = "tiktok"

# One table. UI must iterate this; do not duplicate names/sizes in widgets.
PLATFORMS: dict[str, dict] = {
    "tiktok": {
        "id": "tiktok",
        "name": "TikTok",
        "canvases": ((1080, 1920), (720, 1280)),
        "max_duration_sec": 600.0,
        "filename_suffix": "_tiktok",
    },
    "reels": {
        "id": "reels",
        "name": "Instagram Reels",
        "canvases": ((1080, 1920),),
        "max_duration_sec": 90.0,
        "filename_suffix": "_reels",
    },
    "shorts": {
        "id": "shorts",
        "name": "YouTube Shorts",
        "canvases": ((1080, 1920),),
        "max_duration_sec": 180.0,
        "filename_suffix": "_shorts",
    },
    "youtube": {
        "id": "youtube",
        "name": "YouTube 横屏",
        "canvases": ((1920, 1080),),
        "max_duration_sec": None,
        "filename_suffix": "_youtube",
    },
    "custom": {
        "id": "custom",
        "name": "自定义",
        "canvases": (),
        "max_duration_sec": None,
        "filename_suffix": None,
    },
}
_ORDER = ("tiktok", "reels", "shorts", "youtube", "custom")


def list_platforms() -> list[dict]:
    return [copy.deepcopy(PLATFORMS[pid]) for pid in _ORDER if pid in PLATFORMS]


def get_platform(pid) -> dict:
    key = str(pid or "").strip()
    if key not in PLATFORMS:
        raise KeyError("没有导出平台预设 %r" % pid)
    return copy.deepcopy(PLATFORMS[key])


def resolve_platform_id(explicit=None) -> str:
    if explicit:
        key = str(explicit).strip()
        if key in PLATFORMS:
            return key
        log.warning("unknown export platform %r; falling back to %s", explicit, DEFAULT_ID)
        return DEFAULT_ID
    s = _load_app_settings() or {}
    stored = str(s.get(SETTING_ID) or "").strip()
    if stored in PLATFORMS:
        return stored
    if stored:
        log.warning("stored export platform %r missing; falling back to %s", stored, DEFAULT_ID)
    return DEFAULT_ID


def set_active_platform_id(pid: str) -> dict:
    row = get_platform(pid)
    s = _load_app_settings() or {}
    s[SETTING_ID] = row["id"]
    _save_app_settings(s)
    return row


def _as_platform(platform) -> dict:
    if isinstance(platform, dict):
        return platform
    return get_platform(platform)


def _wh(pair) -> tuple[int, int]:
    return int(pair[0]), int(pair[1])


def suggest_canvas(platform, current_wh) -> tuple[int, int]:
    """Suggested canvas. Custom / already-matching sizes stay as-is. Never mutates."""
    plat = _as_platform(platform)
    cur = _wh(current_wh) if current_wh else (0, 0)
    canvases = tuple(_wh(c) for c in (plat.get("canvases") or ()))
    if not canvases:
        return cur
    if cur in canvases:
        return cur
    return canvases[0]


def canvas_mismatch(platform, current_wh) -> bool:
    plat = _as_platform(platform)
    canvases = tuple(_wh(c) for c in (plat.get("canvases") or ()))
    if not canvases:
        return False
    cur = _wh(current_wh) if current_wh else (0, 0)
    return cur not in canvases


def canvas_mismatch_message(platform, current_wh) -> str:
    """Same tone as ``_prompt_canvas_if_sources_mismatch``. Empty if no mismatch."""
    plat = _as_platform(platform)
    if not canvas_mismatch(plat, current_wh):
        return ""
    sw, sh = suggest_canvas(plat, current_wh)
    cw, ch = _wh(current_wh) if current_wh else (0, 0)
    return "平台建议是 %dx%d，当前画布是 %dx%d。要改成平台尺寸吗？" % (sw, sh, cw, ch)


def _format_limit(sec: float) -> str:
    t = float(sec)
    if t >= 60 and abs(t - round(t / 60.0) * 60.0) < 1e-6:
        minutes = int(round(t / 60.0))
        if minutes >= 1:
            return "%d 分钟" % minutes
    if abs(t - round(t)) < 1e-6:
        return "%d 秒" % int(round(t))
    return "%.1f 秒" % t


def duration_warning(platform, duration_sec) -> str:
    """Warning text when the project is longer than the platform cap. Empty otherwise.

    Never blocks export — callers must keep going after showing this.
    """
    plat = _as_platform(platform)
    limit = plat.get("max_duration_sec")
    if limit in (None, "", 0, 0.0):
        return ""
    dur = float(duration_sec or 0.0)
    cap = float(limit)
    if dur <= cap:
        return ""
    name = plat.get("name") or plat.get("id") or "该平台"
    return "%s 建议不超过 %s，当前工程约 %s。仍可继续导出。" % (
        name, _format_limit(cap), _format_limit(dur),
    )


def filename_suffix_for(platform, current_suffix: str = "_FINAL") -> str:
    plat = _as_platform(platform)
    suf = plat.get("filename_suffix")
    if suf in (None, ""):
        return str(current_suffix or "_FINAL")
    return str(suf)


def select_platform(
    platform_id,
    *,
    current_wh,
    duration_sec: float = 0.0,
    current_suffix: str = "_FINAL",
) -> dict:
    """Return suggestions only. Does not mutate ``current_wh`` or encode settings."""
    plat = get_platform(platform_id)
    suggested = suggest_canvas(plat, current_wh)
    mismatch = canvas_mismatch(plat, current_wh)
    warn = duration_warning(plat, duration_sec)
    suffix = filename_suffix_for(plat, current_suffix)
    custom = plat["id"] == "custom"
    return {
        "id": plat["id"],
        "name": plat["name"],
        "canvas_wh": suggested,
        "current_wh": _wh(current_wh) if current_wh else (0, 0),
        "canvas_mismatch": mismatch,
        "canvas_prompt": canvas_mismatch_message(plat, current_wh),
        "duration_warning": warn,
        "filename_suffix": suffix,
        "block_export": False,
        "changed": False if custom else bool(
            mismatch or warn or suffix != (current_suffix or "_FINAL")
        ),
    }


def hint_text(platform, current_wh, duration_sec: float = 0.0) -> str:
    """One-line hint for the export dialog combo."""
    plat = _as_platform(platform)
    parts = []
    canvases = tuple(_wh(c) for c in (plat.get("canvases") or ()))
    if canvases:
        parts.append("建议画布 " + " / ".join("%dx%d" % c for c in canvases))
    else:
        cw, ch = _wh(current_wh) if current_wh else (0, 0)
        if cw > 0 and ch > 0:
            parts.append("保持当前画布 %dx%d" % (cw, ch))
        else:
            parts.append("保持当前画布")
    limit = plat.get("max_duration_sec")
    if limit not in (None, "", 0, 0.0):
        parts.append("时长上限 " + _format_limit(float(limit)))
    warn = duration_warning(plat, duration_sec)
    if warn:
        parts.append(warn)
    mismatch = canvas_mismatch_message(plat, current_wh)
    if mismatch:
        parts.append(mismatch)
    return "；".join(parts)
