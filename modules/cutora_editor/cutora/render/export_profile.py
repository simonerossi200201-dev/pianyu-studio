# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export profiles. Same user-presets I/O as text presets. Default = current encode."""
from __future__ import annotations

import copy
import os
import re

from cutora.core.history import _load_app_settings, _save_app_settings
from cutora.core.paths import user_presets_dir
from cutora.core.preset_io import read_preset_file, write_preset_file

SETTING_ID = "export_profile_id"
DEFAULT_ID = "social-vertical"
_STORE = "export_profiles.json"
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

# quality source = old export_video_mode=original (CRF 0 / QP 0). Not lossless.
BUILTIN = (
    {
        "id": "social-vertical",
        "name": "社交竖版",
        "container": "mp4",
        "vcodec": "h264",
        "quality": "compressed",
        "audio_codec": "aac",
        "note": "H.264 / mp4 / CRF 18（或 NVENC CQ 18）。和现在的压缩导出相同。",
    },
    {
        "id": "hevc-high",
        "name": "高质量 HEVC",
        "container": "mp4",
        "vcodec": "hevc",
        "quality": "high",
        "audio_codec": "aac",
        "note": "HEVC。CPU 走 libx265 CRF 20；GPU 走 hevc_nvenc CQ 18。",
    },
    {
        "id": "archive-prores",
        "name": "存档 ProRes",
        "container": "mov",
        "vcodec": "prores",
        "quality": "archive",
        "audio_codec": "pcm_s16le",
        "pix_fmt": "yuv422p10le",
        "note": "ProRes 422 HQ。给剪辑师，不是给平台。",
    },
    {
        "id": "audio-m4a",
        "name": "仅音频",
        "container": "m4a",
        "vcodec": "none",
        "quality": "compressed",
        "audio_codec": "aac",
        "note": "只混音，不渲画面。",
    },
    {
        "id": "source-size",
        "name": "跟源尺寸 / 帧率 / 采样率（画质仍是有损）",
        "container": "mp4",
        "vcodec": "h264",
        "quality": "source",
        "audio_codec": "aac",
        "note": "跟的是宽高、帧率和采样率，不是画质。画质用 CRF 15（高画质，不是无损）。真存档走 ProRes。",
    },
)
_BUILTIN_BY_ID = {p["id"]: p for p in BUILTIN}


def _user_path() -> str:
    return os.path.join(user_presets_dir(), _STORE)


def _read_user() -> dict:
    return read_preset_file(_user_path())


def _write_user(data: dict) -> None:
    write_preset_file(_user_path(), data)


def _norm_id(raw) -> str:
    text = str(raw or "").strip()
    if not _ID_RE.match(text):
        raise ValueError("预设 id 只能是字母数字、点、下划线、短横，收到 %r" % raw)
    return text


def is_builtin(pid: str) -> bool:
    return pid in _BUILTIN_BY_ID


def get_profile(pid: str) -> dict:
    pid = str(pid or "").strip()
    if pid in _BUILTIN_BY_ID:
        return copy.deepcopy(_BUILTIN_BY_ID[pid])
    for row in _read_user().get("presets") or []:
        if isinstance(row, dict) and row.get("id") == pid:
            return copy.deepcopy(row)
    raise KeyError("没有导出预设 %r" % pid)


def list_profiles() -> list[dict]:
    out = [copy.deepcopy(p) for p in BUILTIN]
    seen = {p["id"] for p in out}
    for row in _read_user().get("presets") or []:
        if not isinstance(row, dict) or not row.get("id") or row["id"] in seen:
            continue
        item = copy.deepcopy(row)
        item["builtin"] = False
        out.append(item)
        seen.add(row["id"])
    for p in out:
        p.setdefault("builtin", p["id"] in _BUILTIN_BY_ID)
    return out


def save_profile(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("预设必须是对象")
    pid = _norm_id(body.get("id"))
    if is_builtin(pid):
        raise ValueError("内置预设不能覆盖，请另存为新 id")
    row = {
        "id": pid,
        "name": str(body.get("name") or pid),
        "container": str(body.get("container") or "mp4"),
        "vcodec": str(body.get("vcodec") or "h264"),
        "quality": str(body.get("quality") or "compressed"),
        "audio_codec": str(body.get("audio_codec") or "aac"),
    }
    if body.get("pix_fmt"):
        row["pix_fmt"] = str(body["pix_fmt"])
    if body.get("note"):
        row["note"] = str(body["note"])
    data = _read_user()
    rows = [r for r in (data.get("presets") or []) if not (isinstance(r, dict) and r.get("id") == pid)]
    rows.append(row)
    data["version"] = 1
    data["presets"] = rows
    _write_user(data)
    return copy.deepcopy(row)


def delete_profile(pid: str) -> None:
    pid = _norm_id(pid)
    if is_builtin(pid):
        raise ValueError("内置预设不能删")
    data = _read_user()
    before = list(data.get("presets") or [])
    data["presets"] = [r for r in before if not (isinstance(r, dict) and r.get("id") == pid)]
    if len(data["presets"]) == len(before):
        raise KeyError("没有导出预设 %r" % pid)
    _write_user(data)
    s = _load_app_settings() or {}
    if s.get(SETTING_ID) == pid:
        s[SETTING_ID] = DEFAULT_ID
        _save_app_settings(s)


def resolve_profile_id(explicit=None, task=None) -> str:
    if explicit:
        return str(explicit).strip()
    if isinstance(task, dict) and task.get("_export_profile_id"):
        return str(task["_export_profile_id"]).strip()
    s = _load_app_settings() or {}
    if s.get(SETTING_ID):
        return str(s[SETTING_ID]).strip()
    if s.get("export_video_mode") == "original":
        return "source-size"
    return DEFAULT_ID


def resolve_profile(explicit=None, task=None) -> dict:
    pid = resolve_profile_id(explicit, task)
    try:
        return get_profile(pid)
    except KeyError:
        return copy.deepcopy(_BUILTIN_BY_ID[DEFAULT_ID])


def set_active_profile_id(pid: str) -> dict:
    prof = get_profile(pid)
    s = _load_app_settings() or {}
    s[SETTING_ID] = prof["id"]
    s["export_video_mode"] = "original" if prof.get("quality") == "source" else "compressed"
    _save_app_settings(s)
    try:
        from cutora.core import history as hist
        hist.EXPORT_VIDEO_MODE = s["export_video_mode"]
    except Exception:
        pass
    return prof


def encode_args(profile: dict | None, use_gpu: bool):
    """Return ``(vcodec, preset, extra_args, pix_fmt)``. vcodec None = audio only."""
    prof = profile if isinstance(profile, dict) else {}
    kind = str(prof.get("vcodec") or "h264")
    quality = str(prof.get("quality") or "compressed")
    pix = str(prof.get("pix_fmt") or "yuv420p")
    if kind in ("none", "null", ""):
        return None, None, [], pix
    if kind == "hevc":
        from cutora.render.encoder_probe import pick_hevc
        enc, _reason = pick_hevc(bool(use_gpu))
        if enc == "hevc_nvenc":
            return "hevc_nvenc", "p4", ["-rc", "vbr", "-cq", "18", "-b:v", "0"], pix
        return "libx265", "medium", ["-crf", "20"], pix
    if kind == "prores":
        return "prores_ks", None, ["-profile:v", "3"], pix or "yuv422p10le"
    # source-size: same geometry, high quality — CRF 15, not CRF 0.
    # CRF 0 + yuv420p is huge and still not lossless. Archive is ProRes.
    if quality == "source":
        from cutora.render.encoder_probe import pick_h264
        enc, _reason = pick_h264(bool(use_gpu))
        if enc == "h264_nvenc":
            return "h264_nvenc", "p4", ["-rc", "vbr", "-cq", "15", "-b:v", "0"], pix
        return "libx264", "medium", ["-crf", "15"], pix
    from cutora.render.export_params import vcodec_quality_args
    vcodec, preset, extra = vcodec_quality_args(use_gpu, "compressed")
    return vcodec, preset, extra, pix
