# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export-bus loudness: dual-pass FFmpeg loudnorm. Product default -14 LUFS."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.log import get_logger
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.audio.loudness")

MODES = ("off", "lufs14", "lufs16", "custom")
MODE_OFF = "off"
MODE_14 = "lufs14"
MODE_16 = "lufs16"
MODE_CUSTOM = "custom"
SETTING_MODE = "loudness_mode"
SETTING_TARGET = "loudness_target_lufs"
DEFAULT_MODE = MODE_14
TARGET_14 = -14.0
TARGET_16 = -16.0
TRUE_PEAK = -1.0
LRA = 11.0
_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


@dataclass(frozen=True)
class LoudnessTarget:
    enabled: bool
    mode: str
    target_i: float
    target_tp: float = TRUE_PEAK


def _mode_of(raw) -> str:
    m = str(raw or DEFAULT_MODE).strip().lower()
    if m in ("-14", "14"):
        return MODE_14
    if m in ("-16", "16"):
        return MODE_16
    return m if m in MODES else DEFAULT_MODE


def target_i_for(mode: str, custom: float | None = None) -> float:
    if mode == MODE_14:
        return TARGET_14
    if mode == MODE_16:
        return TARGET_16
    if mode == MODE_CUSTOM:
        try:
            return float(custom if custom is not None else TARGET_14)
        except (TypeError, ValueError):
            return TARGET_14
    return TARGET_14


def get_loudness_settings(settings: dict | None = None) -> LoudnessTarget:
    s = settings if isinstance(settings, dict) else (_load_app_settings() or {})
    mode = _mode_of(s.get(SETTING_MODE))
    custom = s.get(SETTING_TARGET)
    target = target_i_for(mode, custom)
    return LoudnessTarget(enabled=(mode != MODE_OFF), mode=mode, target_i=target)


def pin_settings_loudness_off(settings_path: str) -> None:
    """Golden harness: write loudness_mode=off into a copied app_settings.json."""
    data = {}
    if settings_path and os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data[SETTING_MODE] = MODE_OFF
    os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def set_loudness_settings(mode: str, target_lufs=None) -> LoudnessTarget:
    mode = _mode_of(mode)
    s = _load_app_settings() or {}
    prev = get_loudness_settings(s)
    if target_lufs is not None and target_lufs != "":
        try:
            s[SETTING_TARGET] = float(target_lufs)
        except (TypeError, ValueError):
            pass
    elif mode in (MODE_14, MODE_16):
        s[SETTING_TARGET] = target_i_for(mode)
    s[SETTING_MODE] = mode
    _save_app_settings(s)
    logger.info("loudness %s -> %s I=%s", prev.mode, mode, s.get(SETTING_TARGET))
    return get_loudness_settings(s)


def measure_filter(target_i: float, target_tp: float = TRUE_PEAK, lra: float = LRA) -> str:
    return "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:print_format=json" % (
        float(target_i), float(target_tp), float(lra))


def apply_filter(measured: dict, target_i: float, target_tp: float = TRUE_PEAK, lra: float = LRA) -> str:
    def _f(key, default):
        try:
            return float(measured.get(key, default))
        except (TypeError, ValueError):
            return float(default)
    return (
        "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f:"
        "measured_I=%.2f:measured_LRA=%.2f:measured_TP=%.2f:"
        "measured_thresh=%.2f:offset=%.2f:linear=true"
        % (
            float(target_i), float(target_tp), float(lra),
            _f("input_i", target_i),
            _f("input_lra", lra),
            _f("input_tp", target_tp),
            _f("input_thresh", -30.0),
            _f("target_offset", 0.0),
        )
    )


def parse_measured(stderr: str) -> dict:
    blob = stderr or ""
    m = _JSON_RE.search(blob)
    if not m:
        raise ValueError("loudnorm produced no JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict) or "input_i" not in data:
        raise ValueError("loudnorm JSON missing input_i")
    return data


def measure_file(ffmpeg: str, path: str, target_i: float, target_tp: float = TRUE_PEAK) -> dict:
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", path,
        "-af", measure_filter(target_i, target_tp),
        "-f", "null", "-",
    ]
    r = _no_window_run(cmd, capture_output=True, timeout=120)
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("loudnorm measure failed: %s" % err[-400:])
    return parse_measured(err)


def two_pass_wav(ffmpeg: str, src: str, dst: str, target_i: float, target_tp: float = TRUE_PEAK) -> dict:
    """Measure then apply. Returns the measure dict. dst is rewritten."""
    measured = measure_file(ffmpeg, src, target_i, target_tp)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-i", src,
        "-af", apply_filter(measured, target_i, target_tp),
        "-c:a", "pcm_s16le", dst,
    ]
    r = _no_window_run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError("loudnorm apply failed: %s" % err[-400:])
    return measured


def parse_ebur128_i(stderr: str) -> float | None:
    """Integrated LUFS from ``-af ebur128`` summary (``I:`` line)."""
    found = None
    for line in (stderr or "").splitlines():
        s = line.strip()
        if s.startswith("I:"):
            try:
                found = float(s.split(":", 1)[1].replace("LUFS", "").strip())
            except (TypeError, ValueError):
                continue
    return found
