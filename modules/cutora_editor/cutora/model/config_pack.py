# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T26: export/import user config zip. Sibling of T06c project.pack."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone

from cutora.core.brand import APP_VERSION
from cutora.core.paths import resolve_user_data, settings_file, user_presets_dir

MANIFEST_NAME = "manifest.json"
PACK_KIND = "cutora.config"
SECTIONS = (
    "presets", "settings", "fonts", "assets", "shortcuts", "theme",
)


def _copytree_files(src: str, dest: str) -> int:
    n = 0
    if not os.path.isdir(src):
        return 0
    os.makedirs(dest, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out_root = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(out_root, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(root, name), os.path.join(out_root, name))
            n += 1
    return n


def export_config(zip_path: str, user_root: str | None = None) -> dict:
    """Write a zip with manifest + copied user presets/settings/fonts/assets."""
    root = os.path.abspath(user_root or resolve_user_data())
    zip_path = os.path.abspath(zip_path)
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    staging = tempfile.mkdtemp(prefix="cutora_cfg_")
    try:
        counts = {}
        presets_src = os.path.join(root, "presets") if os.path.isdir(os.path.join(root, "presets")) else user_presets_dir()
        counts["presets"] = _copytree_files(presets_src, os.path.join(staging, "presets"))
        settings_src = os.path.join(root, "settings", "app_settings.json")
        if not os.path.isfile(settings_src):
            settings_src = os.path.join(root, "app_settings.json")
        if not os.path.isfile(settings_src) and user_root is None:
            settings_src = settings_file("app_settings.json")
        os.makedirs(os.path.join(staging, "settings"), exist_ok=True)
        if os.path.isfile(settings_src):
            shutil.copy2(settings_src, os.path.join(staging, "settings", "app_settings.json"))
            counts["settings"] = 1
        else:
            counts["settings"] = 0
        counts["fonts"] = _copytree_files(os.path.join(root, "fonts"), os.path.join(staging, "fonts"))
        counts["assets"] = _copytree_files(os.path.join(root, "assets"), os.path.join(staging, "assets"))
        written = [name for name, n in counts.items() if int(n or 0) > 0]
        manifest = {
            "kind": PACK_KIND,
            "version": 1,
            "app_version": APP_VERSION,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sections": written,
            "counts": counts,
        }
        with open(os.path.join(staging, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _d, files in os.walk(staging):
                for name in files:
                    full = os.path.join(dirpath, name)
                    zf.write(full, os.path.relpath(full, staging).replace("\\", "/"))
        return {"path": zip_path, "manifest": manifest}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _merge_json_list(dest_path: str, incoming: list, key_fields=("id", "name")) -> dict:
    existing = []
    if os.path.isfile(dest_path):
        try:
            data = json.loads(open(dest_path, encoding="utf-8").read())
            if isinstance(data, dict) and isinstance(data.get("presets"), list):
                existing = list(data["presets"])
                wrapper = data
            elif isinstance(data, list):
                existing = list(data)
                wrapper = None
            else:
                wrapper = data if isinstance(data, dict) else {"presets": []}
                existing = list(wrapper.get("presets") or [])
        except Exception:
            wrapper = {"presets": []}
            existing = []
    else:
        wrapper = {"presets": []}
    seen = set()
    for row in existing:
        if not isinstance(row, dict):
            continue
        for k in key_fields:
            v = row.get(k)
            if v:
                seen.add(str(v))
    added, skipped = [], []
    for row in incoming or []:
        if not isinstance(row, dict):
            continue
        keys = [str(row.get(k)) for k in key_fields if row.get(k)]
        if any(k in seen for k in keys):
            skipped.append(keys[0] if keys else "?")
            continue
        existing.append(row)
        for k in keys:
            seen.add(k)
        added.append(keys[0] if keys else "?")
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if wrapper is None:
        payload = existing
    else:
        wrapper["presets"] = existing
        payload = wrapper
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return {"added": added, "skipped": skipped}


def _merge_settings(dest_path: str, incoming: dict, overwrite: bool = False) -> dict:
    current = {}
    if os.path.isfile(dest_path):
        try:
            current = json.loads(open(dest_path, encoding="utf-8").read()) or {}
        except Exception:
            current = {}
    if not isinstance(current, dict):
        current = {}
    from cutora.core.secrets import strip_secret_fields
    incoming = strip_secret_fields(incoming or {})
    if not isinstance(incoming, dict):
        incoming = {}
    added, skipped = [], []
    for k, v in incoming.items():
        if k in current and not overwrite:
            skipped.append(k)
            continue
        current[k] = v
        added.append(k)
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return {"added": added, "skipped": skipped}


def _copy_new_only(src_dir: str, dest_dir: str) -> dict:
    added, skipped = [], []
    if not os.path.isdir(src_dir):
        return {"added": added, "skipped": skipped}
    os.makedirs(dest_dir, exist_ok=True)
    for root, _d, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        out_root = dest_dir if rel == "." else os.path.join(dest_dir, rel)
        os.makedirs(out_root, exist_ok=True)
        for name in files:
            dest = os.path.join(out_root, name)
            src = os.path.join(root, name)
            if os.path.isfile(dest):
                skipped.append(name)
                continue
            shutil.copy2(src, dest)
            added.append(name)
    return {"added": added, "skipped": skipped}


def import_config(zip_path: str, user_root: str | None = None, overwrite: bool = False) -> dict:
    """Merge zip into user data. Same-name items skipped unless overwrite=True."""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(zip_path)
    root = os.path.abspath(user_root or resolve_user_data())
    staging = tempfile.mkdtemp(prefix="cutora_cfg_in_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            from cutora.core.zipsafe import safe_extractall
            safe_extractall(zf, staging)
        man_path = os.path.join(staging, MANIFEST_NAME)
        manifest = {}
        if os.path.isfile(man_path):
            manifest = json.loads(open(man_path, encoding="utf-8").read())
        if manifest.get("kind") not in (None, PACK_KIND):
            raise ValueError("不是配置包：kind=%s" % manifest.get("kind"))
        report = {"manifest": manifest, "sections": {}}
        presets_in = os.path.join(staging, "presets")
        presets_out = os.path.join(root, "presets")
        if os.path.isdir(presets_in):
            json_merged = False
            for dirpath, _d, files in os.walk(presets_in):
                for name in files:
                    if not name.endswith(".json"):
                        continue
                    src = os.path.join(dirpath, name)
                    rel = os.path.relpath(src, presets_in)
                    dest = os.path.join(presets_out, rel)
                    try:
                        data = json.loads(open(src, encoding="utf-8").read())
                    except Exception:
                        continue
                    incoming = data.get("presets") if isinstance(data, dict) else data
                    if isinstance(incoming, list):
                        report["sections"][rel] = _merge_json_list(dest, incoming)
                        json_merged = True
            files_report = _copy_new_only(presets_in, presets_out) if overwrite else None
            if not json_merged:
                report["sections"]["presets"] = _copy_new_only(presets_in, presets_out)
            elif files_report:
                report["sections"]["presets_files"] = files_report
        settings_in = os.path.join(staging, "settings", "app_settings.json")
        if os.path.isfile(settings_in):
            incoming = json.loads(open(settings_in, encoding="utf-8").read())
            dest = os.path.join(root, "settings", "app_settings.json")
            if not os.path.isdir(os.path.dirname(dest)):
                dest = os.path.join(root, "app_settings.json")
            report["sections"]["settings"] = _merge_settings(dest, incoming, overwrite=overwrite)
        report["sections"]["fonts"] = _copy_new_only(
            os.path.join(staging, "fonts"), os.path.join(root, "fonts"))
        report["sections"]["assets"] = _copy_new_only(
            os.path.join(staging, "assets"), os.path.join(root, "assets"))
        return report
    finally:
        shutil.rmtree(staging, ignore_errors=True)
