# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

from cutora.core.paths import ensure_user_layout, settings_file, user_settings_dir

DEFAULT_STYLE_DIR = user_settings_dir()
DEFAULT_STYLE_PATH = settings_file("default_style.json")

def ensure_user_data_dir():
    ensure_user_layout()
    os.makedirs(os.path.dirname(settings_file("default_style.json")) or ".", exist_ok=True)

def load_default_style():
    try:
        ensure_user_data_dir()
        path = settings_file("default_style.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if data.get("version") != 1:
            return None
        style = data.get("style")
        if not isinstance(style, dict):
            return None
        return style
    except Exception:
        return None

def save_default_style(style_dict):
    try:
        if not isinstance(style_dict, dict):
            return False
        ensure_user_data_dir()
        payload = {"version": 1, "style": style_dict}
        path = settings_file("default_style.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception:
        return False
