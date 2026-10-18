# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical filesystem locations.

Every on-disk asset path in the app should come from here. Do not compute
``dirname(__file__)`` in feature modules.

User-data resolution (highest wins):

    CUTORA_USER_DATA  >  <program>/UserData  (portable.flag)  >  %LOCALAPPDATA%\\CUTORA
"""
from __future__ import annotations

import json
import os
import sys

from cutora.core.log import get_logger

logger = get_logger("cutora.core.paths")

# cutora/core/paths.py → repo root is three levels up.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# OPS-03: worktrees must not symlink runtime/. Point CUTORA_RUNTIME at the
# main-repo runtime instead.
_RUNTIME_ENV = (os.environ.get("CUTORA_RUNTIME") or "").strip()
_LOCAL_RT = os.path.join(REPO_ROOT, "runtime")
_STUDIO_RT = os.path.abspath(os.path.join(REPO_ROOT, "..", "..", "runtime"))
RUNTIME_DIR = (
    os.path.abspath(_RUNTIME_ENV) if _RUNTIME_ENV
    else (_LOCAL_RT if os.path.isdir(_LOCAL_RT) else _STUDIO_RT)
)
FFMPEG_DIR = os.path.join(RUNTIME_DIR, "ffmpeg")
FFMPEG = os.path.join(FFMPEG_DIR, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
FFPROBE = os.path.join(FFMPEG_DIR, "ffprobe.exe" if os.name == "nt" else "ffprobe")
MODELS = os.path.join(RUNTIME_DIR, "models")
RESOURCES = os.path.join(REPO_ROOT, "resources")
BRAND_DIR = os.path.join(RESOURCES, "brand")
ENV_USER_DATA = "CUTORA_USER_DATA"
PORTABLE_FLAG_NAME = "portable.flag"
PORTABLE_USER_DIR = "UserData"
LOCAL_APPDATA_LEAF = "CUTORA"

_MODEL_DEFAULTS = {
    "asr_path": "asr_small",
    "align_path": "align_wav2vec2",
    "vad_path": None,
}


def program_dir() -> str:
    """Directory that contains the executable / repo (portable.flag lives here)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return REPO_ROOT


def is_portable() -> bool:
    return os.path.isfile(os.path.join(program_dir(), PORTABLE_FLAG_NAME))


def default_local_appdata() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, LOCAL_APPDATA_LEAF)


def resolve_user_data() -> str:
    """Return the user-data directory.

    ``CUTORA_USER_DATA`` is highest priority so golden_check / golden_gen can
    pin renders at ``tests/golden/_fixture_user_data`` (copied to a temp dir).
    Subprocesses inherit the same variable.
    """
    override = (os.environ.get(ENV_USER_DATA) or "").strip()
    if override:
        return os.path.abspath(override)
    if is_portable():
        return os.path.join(program_dir(), PORTABLE_USER_DIR)
    return default_local_appdata()


def uses_override_layout() -> bool:
    """True when CUTORA_USER_DATA is set: keep a flat directory (golden fixture)."""
    return bool((os.environ.get(ENV_USER_DATA) or "").strip())


def _legacy_flat_settings(root: str) -> bool:
    return os.path.isfile(os.path.join(root, "app_settings.json"))


def user_settings_dir() -> str:
    root = resolve_user_data()
    if uses_override_layout() or _legacy_flat_settings(root):
        return root
    return os.path.join(root, "settings")


def user_presets_dir() -> str:
    return os.path.join(resolve_user_data(), "presets")


def user_autosave_dir() -> str:
    return os.path.join(resolve_user_data(), "autosave")


def user_logs_dir() -> str:
    return os.path.join(resolve_user_data(), "logs")


def user_cache_dir() -> str:
    root = resolve_user_data()
    if uses_override_layout() or _legacy_flat_settings(root):
        return root
    return os.path.join(root, "cache")


def settings_file(name: str) -> str:
    """Resolve a settings JSON, preferring an existing file so golden stays flat.

    Read order: ``<USER_DATA>/<name>`` then ``<USER_DATA>/settings/<name>``.
    Writes go to whichever already exists; otherwise the override/legacy-flat
    layout stays at the root and the default LOCALAPPDATA layout uses
    ``settings/``.
    """
    root = resolve_user_data()
    flat = os.path.join(root, name)
    nested = os.path.join(root, "settings", name)
    if os.path.isfile(flat):
        return flat
    if os.path.isfile(nested):
        return nested
    if uses_override_layout() or _legacy_flat_settings(root):
        return flat
    return nested


def ensure_user_layout() -> str:
    """Create the user-data tree. Override/fixture stays flat plus logs/."""
    root = resolve_user_data()
    os.makedirs(root, exist_ok=True)
    os.makedirs(user_logs_dir(), exist_ok=True)
    if uses_override_layout() or _legacy_flat_settings(root):
        return root
    for fn in (user_settings_dir, user_presets_dir, user_autosave_dir, user_cache_dir):
        os.makedirs(fn(), exist_ok=True)
    return root


def refresh_cached_paths() -> str:
    """Re-bind module-level ``USER_DATA`` after tests change CUTORA_USER_DATA."""
    global USER_DATA
    USER_DATA = resolve_user_data()
    return USER_DATA


def models_root() -> str:
    """Directory that holds ASR / align weights.

    ``asr.models_dir`` in app settings overrides ``runtime/models``.
    """
    try:
        from cutora.core.history import _load_app_settings
        raw = ((_load_app_settings() or {}).get("asr.models_dir") or "").strip()
        if raw:
            return os.path.abspath(raw)
    except Exception:
        pass
    return MODELS


def pin_hf_home() -> str:
    """Force Hugging Face caches under the active models root."""
    root = models_root()
    os.environ["HF_HOME"] = root
    return root


def _rebase_model_entry(raw, default_leaf: str | None) -> str | None:
    root = models_root()
    if raw is None or raw == "":
        if default_leaf is None:
            return None
        return os.path.join(root, default_leaf)
    leaf = os.path.basename(str(raw).rstrip("\\/"))
    if not leaf:
        leaf = default_leaf or ""
    if not leaf:
        return None
    return os.path.join(root, leaf)


def get_model_paths():
    """ASR / align / VAD directories, rooted at ``models_root()``.

    ``model_paths.json`` may still contain leftover absolute paths from v6;
    only the last path component is kept.
    """
    pin_hf_home()
    root = models_root()
    out = {
        "asr_path": os.path.join(root, _MODEL_DEFAULTS["asr_path"]),
        "align_path": os.path.join(root, _MODEL_DEFAULTS["align_path"]),
        "vad_path": None,
    }
    json_path = os.path.join(root, "model_paths.json")
    try:
        if os.path.isfile(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out["asr_path"] = _rebase_model_entry(
                    data.get("asr_path"), _MODEL_DEFAULTS["asr_path"])
                out["align_path"] = _rebase_model_entry(
                    data.get("align_path"), _MODEL_DEFAULTS["align_path"])
                vad_raw = data.get("vad_path", _MODEL_DEFAULTS["vad_path"])
                out["vad_path"] = _rebase_model_entry(vad_raw, None) if vad_raw else None
    except Exception as exc:
        logger.warning("Failed to load model_paths.json: %s", exc)
    return out


def resource_path(*parts: str) -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = RESOURCES
    return os.path.join(base, *parts)


USER_DATA = resolve_user_data()
FONT_DIR = os.path.join(RESOURCES, "fonts")
TEXTURE_DIR = os.path.join(RESOURCES, "textures")
ICON_DIR = os.path.join(RESOURCES, "icons")
AUDIO_IR = os.path.join(RESOURCES, "audio", "ir")
AUDIO_SFX = os.path.join(RESOURCES, "audio", "sfx")
THEME_QSS = os.path.join(RESOURCES, "theme", "theme_dark.qss")
FONT_PENDING_DIR = os.path.join(RESOURCES, "fonts_pending")
LUCIDE_DIR = os.path.join(RESOURCES, "icons", "lucide")
STICKER_DIR = os.path.join(RESOURCES, "stickers")
SAFE_ZONES_JSON = os.path.join(RESOURCES, "safe_zones.json")
WENAN_WORDS = os.path.join(RESOURCES, "data", "wenan_words.txt")
VENDOR_WHISPERX = os.path.join(REPO_ROOT, "vendor", "whisperX")
APP_ICON = os.path.join(BRAND_DIR, "app.ico")
APP_ICON_PNG = os.path.join(BRAND_DIR, "icon_256.png")
SPLASH_PNG = os.path.join(BRAND_DIR, "splash_bg.png")
MARK_PNG = os.path.join(BRAND_DIR, "mark_1024.png")
FILE_ICON = APP_ICON  # dedicated .cutora document icon is T10

# Historical name: used to mean core/. After T01c it is the repo root.
BASE_DIR = REPO_ROOT
