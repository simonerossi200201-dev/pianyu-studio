# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T27 .cutorapack format: pack.json + items/<id>/item.json + source files."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile

from cutora.core.paths import RESOURCES, resolve_user_data

PACK_EXT = ".cutorapack"
ITEM_TYPES = (
    "element", "frame", "lower_third", "card", "effect", "animation",
    "sticker", "animated", "video", "audio",
    "sfx", "music", "template", "stylepack",
)
ALLOWED_LICENSE = ("CUTORA-original", "CC0-1.0", "OFL-1.1", "Apache-2.0")


def builtin_packs_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")


def legacy_builtin_packs_dir() -> str:
    return os.path.join(RESOURCES, "packs")


def user_packs_dir() -> str:
    d = os.path.join(resolve_user_data(), "packs")
    os.makedirs(d, exist_ok=True)
    return d


def load_pack_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("pack.json 必须是对象")
    if not data.get("id") or not data.get("name"):
        raise ValueError("pack.json 缺 id/name")
    lic = str(data.get("license") or "")
    if lic not in ALLOWED_LICENSE:
        raise ValueError("许可必须是 %s，得到 %s" % ("/".join(ALLOWED_LICENSE), lic))
    return data


def load_item_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("item.json 必须是对象")
    if data.get("type") not in ITEM_TYPES:
        raise ValueError("未知 item type: %s" % data.get("type"))
    if not data.get("id") or not data.get("name"):
        raise ValueError("item.json 缺 id/name")
    lic = str(data.get("license") or "CUTORA-original")
    if lic not in ALLOWED_LICENSE:
        raise ValueError("item 许可非法: %s" % lic)
    data["license"] = lic
    return data


def iter_pack_items(pack_dir: str) -> list[dict]:
    items_root = os.path.join(pack_dir, "items")
    out = []
    if not os.path.isdir(items_root):
        return out
    for name in sorted(os.listdir(items_root)):
        item_dir = os.path.join(items_root, name)
        meta = os.path.join(item_dir, "item.json")
        if not os.path.isfile(meta):
            continue
        rec = load_item_json(meta)
        rec["_dir"] = item_dir
        rec["_pack_dir"] = pack_dir
        out.append(rec)
    return out


def discover_packs(extra_dirs: list[str] | None = None) -> list[dict]:
    roots = [builtin_packs_dir(), legacy_builtin_packs_dir(), user_packs_dir()]
    for d in extra_dirs or []:
        if d:
            roots.append(d)
    packs = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            pdir = os.path.join(root, name)
            meta = os.path.join(pdir, "pack.json")
            if not os.path.isfile(meta):
                continue
            pack = load_pack_json(meta)
            if pack["id"] in seen:
                continue
            seen.add(pack["id"])
            pack["_dir"] = pdir
            pack["items"] = iter_pack_items(pdir)
            packs.append(pack)
    return packs


def export_cutorapack(pack_dir: str, zip_path: str) -> str:
    pack_dir = os.path.abspath(pack_dir)
    load_pack_json(os.path.join(pack_dir, "pack.json"))
    zip_path = os.path.abspath(zip_path)
    if not zip_path.endswith(PACK_EXT):
        zip_path += PACK_EXT
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _d, files in os.walk(pack_dir):
            for name in files:
                full = os.path.join(dirpath, name)
                zf.write(full, os.path.relpath(full, pack_dir).replace("\\", "/"))
    return zip_path


def import_cutorapack(zip_path: str, dest_root: str | None = None) -> dict:
    dest_root = dest_root or user_packs_dir()
    os.makedirs(dest_root, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="cutorapack_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            from cutora.core.zipsafe import safe_extractall
            safe_extractall(zf, staging)
        meta = os.path.join(staging, "pack.json")
        if not os.path.isfile(meta):
            raise ValueError("包内没有 pack.json")
        pack = load_pack_json(meta)
        pack_id = str(pack.get("id") or "")
        if (
            not pack_id
            or pack_id in (".", "..")
            or pack_id != os.path.basename(pack_id)
            or any(c in pack_id for c in "/\\:\x00")
        ):
            raise ValueError("包 id 不能包含路径")
        dest = os.path.abspath(os.path.join(dest_root, pack_id))
        if os.path.dirname(dest) != os.path.abspath(dest_root):
            raise ValueError("包 id 不能包含路径")
        if os.path.isdir(dest):
            raise FileExistsError("已有同名包：%s" % pack["id"])
        shutil.copytree(staging, dest)
        pack["_dir"] = dest
        pack["items"] = iter_pack_items(dest)
        return pack
    finally:
        shutil.rmtree(staging, ignore_errors=True)
