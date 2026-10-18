# SPDX-License-Identifier: GPL-3.0-or-later
"""
preset_manager.py  –  Text templates, keyword colors, and loose preset files.

Subtitle style presets live in style packs (cutora.subtitle.stylepack).
Max 50 still applies to keyword-color presets and loose preset files.
"""

import os
import json
import copy
import time
import shutil

def _presets_dir():
    from cutora.core.paths import user_presets_dir
    return user_presets_dir()


PRESETS_DIR = _presets_dir()
MAX_PRESETS = 50
_DEFAULT_MARKER = os.path.join(PRESETS_DIR, "_default_preset.txt")
TEXT_STYLE_PRESETS_PATH = os.path.join(PRESETS_DIR, "text_style_presets.json")
KEYWORD_COLOR_PRESETS_PATH = os.path.join(PRESETS_DIR, "keyword_color_presets.json")
_TEXT_STYLE_KEYS = None
_COPY_STYLE_FULL = None
_TEXT_TEMPLATE_EXTRA_KEYS = None


def _ensure_dir():
    global PRESETS_DIR, _DEFAULT_MARKER, TEXT_STYLE_PRESETS_PATH
    global KEYWORD_COLOR_PRESETS_PATH
    PRESETS_DIR = _presets_dir()
    _DEFAULT_MARKER = os.path.join(PRESETS_DIR, "_default_preset.txt")
    TEXT_STYLE_PRESETS_PATH = os.path.join(PRESETS_DIR, "text_style_presets.json")
    KEYWORD_COLOR_PRESETS_PATH = os.path.join(PRESETS_DIR, "keyword_color_presets.json")
    os.makedirs(PRESETS_DIR, exist_ok=True)


def configure_text_style_helpers(style_keys, copy_style_full_fn, extra_template_keys=None):
    global _TEXT_STYLE_KEYS, _COPY_STYLE_FULL, _TEXT_TEMPLATE_EXTRA_KEYS
    _TEXT_STYLE_KEYS = list(style_keys) if style_keys else None
    _COPY_STYLE_FULL = copy_style_full_fn
    _TEXT_TEMPLATE_EXTRA_KEYS = list(extra_template_keys) if extra_template_keys else None


def _sanitize_text_style(style):
    if not isinstance(style, dict):
        style = {}
    template = {}
    # 内容字段
    if "content" in style:
        template["content"] = copy.deepcopy(style.get("content"))
    elif "text" in style:
        template["content"] = copy.deepcopy(style.get("text"))
    # 布局与位置字段
    for k in (_TEXT_TEMPLATE_EXTRA_KEYS or []):
        if k in ("content", "text"):
            continue
        if k in style:
            template[k] = copy.deepcopy(style[k])
    # 样式字段（强制使用 copy_style_full 深拷贝）
    if _COPY_STYLE_FULL:
        template.update(_COPY_STYLE_FULL(style or {}))
    elif _TEXT_STYLE_KEYS:
        for k in _TEXT_STYLE_KEYS:
            if k in style:
                template[k] = copy.deepcopy(style[k])
    # ── 删除不应保存到预设中的 overlay 结构/元数据字段 ──
    _NON_PRESET_KEYS = {
        "id", "start", "end", "_track_id", "type", "track_index",
        "spans", "_preset_name", "_rich_cache",
    }
    for k in _NON_PRESET_KEYS:
        template.pop(k, None)
    return template


def _load_text_preset_file():
    """Read-only leftover. T11d: never create text_style_presets.json."""
    _ensure_dir()
    if not os.path.exists(TEXT_STYLE_PRESETS_PATH):
        return {"default": {}, "presets": {}}
    try:
        with open(TEXT_STYLE_PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"default": {}, "presets": {}}
        data.setdefault("default", {})
        data.setdefault("presets", {})
        if not isinstance(data.get("presets"), dict):
            data["presets"] = {}
        return data
    except Exception:
        return {"default": {}, "presets": {}}


def _save_text_preset_file(data):
    """Retired. T11d: text presets persist only via text_preset.text_presets.json."""
    return False


def load_text_presets():
    """Adapter: named-dict view of text_preset.list_presets(). Does not write the old file."""
    from cutora.subtitle import text_preset as TP
    TP.ensure_legacy_migrated()
    presets = {}
    default_name = ""
    default_style = {}
    default_id = TP.get_default_preset_id()
    for p in TP.list_presets():
        tmpl = TP.template_from_preset(p)
        presets[p["name"]] = tmpl
        if p["id"] == default_id:
            default_name = p["name"]
            default_style = tmpl
    return {
        "default": default_style,
        "presets": presets,
        "default_preset_name": default_name,
    }


def save_default_text_style(style):
    from cutora.subtitle import text_preset as TP
    pid = TP.get_default_preset_id()
    row = TP.preset_from_overlay(style if isinstance(style, dict) else {}, 1080, 1920,
                                 name="", preset_id=pid)
    if not row.get("name"):
        existing = TP.get_preset(pid)
        row["name"] = (existing or {}).get("name") or "文本预设"
    TP.save_preset(row)
    return True

def get_default_text_preset_name():
    from cutora.subtitle import text_preset as TP
    pid = TP.get_default_preset_id()
    row = TP.get_preset(pid)
    return str((row or {}).get("name") or "")

def set_default_text_preset_name(name):
    from cutora.subtitle import text_preset as TP
    row = TP.resolve_preset(name)
    if row is None:
        return False
    return TP.set_default_preset_id(row["id"])



def save_named_text_preset(name, style):
    if not name or not isinstance(name, str):
        return False
    from cutora.subtitle import text_preset as TP
    existing = next((p for p in TP.list_presets() if p.get("name") == name), None)
    pid = existing["id"] if existing else None
    try:
        row = TP.preset_from_overlay(
            style if isinstance(style, dict) else {}, 1080, 1920,
            name=name, preset_id=pid)
        TP.save_preset(row)
        return True
    except ValueError:
        return False


def delete_text_preset(name):
    from cutora.subtitle import text_preset as TP
    row = TP.resolve_preset(name)
    if row is None:
        return False
    try:
        return TP.delete_preset(row["id"])
    except ValueError:
        return False


def export_text_presets(file_path):
    from cutora.subtitle import text_preset as TP
    return TP.export_presets(file_path)


def import_text_presets(file_path):
    """Import presets file. Returns dict with added/conflicts/skipped."""
    from cutora.subtitle import text_preset as TP
    return TP.import_presets(file_path)


def apply_text_preset(name):
    from cutora.subtitle import text_preset as TP
    row = TP.resolve_preset(name)
    if row is None:
        return {}
    return TP.template_from_preset(row)



def save_preset(name, style_dict):
    """Save style dict as a named preset. Returns filepath or None."""
    _ensure_dir()
    existing = list_presets()
    if len(existing) >= MAX_PRESETS:
        return None  # limit reached
    # Sanitize name for filesystem
    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name).strip()
    if not safe_name:
        safe_name = f"preset_{int(time.time())}"
    filename = f"{safe_name}.json"
    filepath = os.path.join(PRESETS_DIR, filename)
    # Avoid overwriting
    idx = 1
    while os.path.exists(filepath):
        filepath = os.path.join(PRESETS_DIR, f"{safe_name}_{idx}.json")
        idx += 1
    data = {
        "preset_version": 1,
        "name": name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "style": copy.deepcopy(style_dict),
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filepath
    except Exception as e:
        print(f"[PresetManager] Save failed: {e}")
        return None


def load_preset(filepath):
    """Load a preset from a JSON file. Returns style dict or None."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("style", {})
    except Exception as e:
        print(f"[PresetManager] Load failed: {e}")
        return None


def list_presets():
    """Return list of preset info dicts: [{name, path, created_at}, ...]"""
    _ensure_dir()
    result = []
    for fn in sorted(os.listdir(PRESETS_DIR)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(PRESETS_DIR, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            result.append({
                "name": data.get("name", fn.replace(".json", "")),
                "path": fp,
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            continue
    return result


def delete_preset(filepath):
    """Delete a preset file."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
    except Exception as e:
        print(f"[PresetManager] Delete failed: {e}")
    return False


def export_preset(filepath, dest_path):
    """Copy preset to a user-chosen location."""
    try:
        shutil.copy2(filepath, dest_path)
        return True
    except Exception as e:
        print(f"[PresetManager] Export failed: {e}")
        return False


def import_preset(src_path):
    """Import an external preset JSON into the presets directory. Returns filepath or None."""
    _ensure_dir()
    if len(list_presets()) >= MAX_PRESETS:
        return None
    try:
        # Validate it's a valid preset
        with open(src_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "style" not in data:
            return None
        fn = os.path.basename(src_path)
        dest = os.path.join(PRESETS_DIR, fn)
        idx = 1
        while os.path.exists(dest):
            base, ext = os.path.splitext(fn)
            dest = os.path.join(PRESETS_DIR, f"{base}_{idx}{ext}")
            idx += 1
        shutil.copy2(src_path, dest)
        return dest
    except Exception as e:
        print(f"[PresetManager] Import failed: {e}")
        return None


def get_default_preset_path():
    """Return the path of the default preset, or None."""
    try:
        if os.path.exists(_DEFAULT_MARKER):
            with open(_DEFAULT_MARKER, "r", encoding="utf-8") as f:
                path = f.read().strip()
            if os.path.exists(path):
                return path
    except Exception:
        pass
    return None


def set_default_preset(filepath):
    """Mark a preset as the default."""
    _ensure_dir()
    try:
        with open(_DEFAULT_MARKER, "w", encoding="utf-8") as f:
            f.write(filepath)
        return True
    except Exception as e:
        print(f"[PresetManager] Set default failed: {e}")
        return False


def load_default_preset():
    """Load the default preset's style dict, or return None."""
    path = get_default_preset_path()
    if path:
        return load_preset(path)
    return None


# ═══════════════════════════════════════════════════════════
#  Keyword Color Presets  (自动着色关键词配色预设)
# ═══════════════════════════════════════════════════════════

def _load_keyword_preset_file():
    """Load keyword color presets JSON. Returns dict with 'default_preset_name' and 'presets'."""
    _ensure_dir()
    if not os.path.exists(KEYWORD_COLOR_PRESETS_PATH):
        data = {"default_preset_name": "", "presets": {}}
        try:
            with open(KEYWORD_COLOR_PRESETS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return data
    try:
        with open(KEYWORD_COLOR_PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"default_preset_name": "", "presets": {}}
        data.setdefault("default_preset_name", "")
        data.setdefault("presets", {})
        if not isinstance(data.get("presets"), dict):
            data["presets"] = {}
        # Ensure every value is a list
        for k, v in list(data["presets"].items()):
            if not isinstance(v, list):
                data["presets"][k] = []
        return data
    except Exception:
        return {"default_preset_name": "", "presets": {}}


def _save_keyword_preset_file(data):
    try:
        _ensure_dir()
        with open(KEYWORD_COLOR_PRESETS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_keyword_presets():
    """Return all keyword color presets: {default_preset_name, presets: {name: [rules]}}."""
    return _load_keyword_preset_file()


def save_named_keyword_preset(name, rules):
    """Save a keyword color preset. rules is a list of keyword rule dicts."""
    if not name or not isinstance(name, str):
        return False
    data = _load_keyword_preset_file()
    presets = data.get("presets") or {}
    if name not in presets and len(presets) >= MAX_PRESETS:
        return False
    presets[name] = copy.deepcopy(rules) if isinstance(rules, list) else []
    data["presets"] = presets
    return _save_keyword_preset_file(data)


def delete_keyword_preset(name):
    """Delete a keyword color preset by name."""
    data = _load_keyword_preset_file()
    presets = data.get("presets") or {}
    if name in presets:
        presets.pop(name, None)
        data["presets"] = presets
        # Clear default if it was the deleted preset
        if data.get("default_preset_name") == name:
            data["default_preset_name"] = ""
        return _save_keyword_preset_file(data)
    return False


def get_default_keyword_preset_name():
    """Return the name of the default keyword color preset, or empty string."""
    return _load_keyword_preset_file().get("default_preset_name", "")


def set_default_keyword_preset_name(name):
    """Mark a keyword color preset as the default."""
    data = _load_keyword_preset_file()
    data["default_preset_name"] = name
    return _save_keyword_preset_file(data)


def load_default_keyword_preset():
    """Load the default keyword color preset's rules list, or return None."""
    data = _load_keyword_preset_file()
    default_name = data.get("default_preset_name", "")
    if default_name:
        presets = data.get("presets") or {}
        rules = presets.get(default_name)
        if isinstance(rules, list):
            return copy.deepcopy(rules)
    return None


def export_keyword_presets(file_path):
    """Export all keyword color presets to a JSON file."""
    data = load_keyword_presets()
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def import_keyword_presets(file_path):
    """Import keyword color presets from a JSON file.
    Returns dict with added/conflicts/skipped."""
    result = {"added": [], "conflicts": [], "skipped": []}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            incoming = json.load(f)
        if not isinstance(incoming, dict):
            return result
        incoming_presets = incoming.get("presets") or {}
        data = _load_keyword_preset_file()
        presets = data.get("presets") or {}
        for name, rules in incoming_presets.items():
            if not isinstance(rules, list):
                result["skipped"].append(name)
                continue
            if len(presets) >= MAX_PRESETS and name not in presets:
                result["skipped"].append(name)
                continue
            if name in presets:
                # Overwrite on conflict
                presets[name] = copy.deepcopy(rules)
                result["conflicts"].append(name)
                continue
            presets[str(name)] = copy.deepcopy(rules)
            result["added"].append(str(name))
        data["presets"] = presets
        _save_keyword_preset_file(data)
        return result
    except Exception:
        return result
