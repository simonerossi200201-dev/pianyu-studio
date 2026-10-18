# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run import of settings from previous user-data locations.

Sources (later entries are fallbacks; the first existing wins as the
primary, but the dialog lists all). Appendix: repo ``user_data`` is
preferred over the original v6 tree.

Secret fields (``*_key`` / ``*_token`` / ``*_secret``) are never copied.
"""
from __future__ import annotations

import os
import re
import shutil
from cutora.core import safe_json
from cutora.core.log import get_logger
from cutora.core.paths import (
    REPO_ROOT,
    ensure_user_layout,
    program_dir,
    resolve_user_data,
    settings_file,
    user_presets_dir,
    uses_override_layout,
)
from cutora.core.secrets import strip_secret_fields

logger = get_logger("cutora.core.migrate_user_data")

# Original v6 tree (read-only). This path is the documented import source.
V6_USER_DATA = r"D:\0_Dev\AutoSub_v6\core\user_data"

IMPORT_FILES = (
    "app_settings.json",
    "default_style.json",
    "recent_projects.json",
    "stitch_naming.json",
    "project_queue.json",
)
SKIP_NAMES = {
    "export_state.json",
    "jobs",
    "_preview_audio",
    "logs",
    "ir",
    "cache",
}

# Persisted keys that used a third-party product name. New code only reads
# the renamed keys; this table plus the generic davinci→dock rewrite keep
# old app_settings / imported v6 JSON readable.
SETTINGS_KEY_RENAMES: dict[str, str] = {}
_LEGACY_PRODUCT_IN_KEY = re.compile(r"davinci", re.IGNORECASE)


def _rename_settings_key(key: str) -> str:
    if key in SETTINGS_KEY_RENAMES:
        return SETTINGS_KEY_RENAMES[key]
    return _LEGACY_PRODUCT_IN_KEY.sub("dock", key)


FADE_KEYS = ("fade_top", "fade_bottom", "fade_left", "fade_right")
FADE_CAP = 0.5


def _item_short_side(item: dict, canvas_w: int, canvas_h: int) -> float:
    w = item.get("w")
    h = item.get("h")
    try:
        bw = float(w) if w is not None else 0.0
        bh = float(h) if h is not None else 0.0
    except (TypeError, ValueError):
        bw, bh = 0.0, 0.0
    if bw <= 1.0 or bh <= 1.0:
        bw, bh = float(max(1, canvas_w)), float(max(1, canvas_h))
    return max(1.0, min(bw, bh))


def _migrate_one_feather(item: dict, canvas_w: int, canvas_h: int) -> None:
    """Convert integer-pixel ``feather`` to four-edge ``fade_*`` ratios.

    Keeps the original in ``feather_legacy``. Does not delete ``feather``.
    Idempotent: a second pass sees ``feather_legacy`` and returns.
    """
    if "feather_legacy" in item:
        return
    if "feather" not in item:
        return
    try:
        px = float(item.get("feather") or 0)
    except (TypeError, ValueError):
        return
    item["feather_legacy"] = int(round(px))
    if px <= 0:
        return
    existing = 0.0
    for key in FADE_KEYS:
        try:
            existing = max(existing, float(item.get(key) or 0))
        except (TypeError, ValueError):
            pass
    if existing > 0:
        return
    frac = min(FADE_CAP, px / _item_short_side(item, canvas_w, canvas_h))
    for key in FADE_KEYS:
        item[key] = frac


def migrate_feather_to_fade(data):
    """Walk a project dict and convert overlay/clip ``feather`` to ``fade_*``.

    No-op on objects without a ``feather`` key. Used from
    ``_normalize_project_data`` so every load is migrated once.
    """
    if not isinstance(data, dict):
        return data
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        tl = task.get("timeline") if isinstance(task.get("timeline"), dict) else {}
        try:
            cw = int(tl.get("width") or 1080)
            ch = int(tl.get("height") or 1920)
        except (TypeError, ValueError):
            cw, ch = 1080, 1920
        for key in ("image_overlays", "text_overlays"):
            for ov in task.get(key) or []:
                if not isinstance(ov, dict):
                    continue
                _migrate_one_feather(ov, cw, ch)
                for el in ov.get("elements") or []:
                    if isinstance(el, dict):
                        _migrate_one_feather(el, cw, ch)
        vo = task.get("video_overlay")
        if isinstance(vo, dict):
            _migrate_one_feather(vo, cw, ch)
        style = task.get("style")
        if isinstance(style, dict):
            for item in style.get("dyn_box_images") or []:
                if isinstance(item, dict):
                    _migrate_one_feather(item, cw, ch)
        for tr in tl.get("tracks") or []:
            if not isinstance(tr, dict):
                continue
            for cl in tr.get("clips") or []:
                if isinstance(cl, dict):
                    _migrate_one_feather(cl, cw, ch)
    return data


def migrate_legacy_keys(obj):
    """Move old persisted keys onto their current names.

    If both the old and new key exist, the new key wins. Lists and nested
    dicts are walked. Non-containers are returned unchanged.
    """
    if isinstance(obj, dict):
        kept: dict = {}
        deferred: list[tuple[str, object]] = []
        for key, value in obj.items():
            migrated = migrate_legacy_keys(value)
            if isinstance(key, str):
                new_key = _rename_settings_key(key)
                if new_key != key:
                    deferred.append((new_key, migrated))
                else:
                    kept[key] = migrated
            else:
                kept[key] = migrated
        for new_key, migrated in deferred:
            if new_key not in kept:
                kept[new_key] = migrated
        return kept
    if isinstance(obj, list):
        return [migrate_legacy_keys(item) for item in obj]
    return obj


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def candidate_sources() -> list[str]:
    """Return existing source directories, repo user_data first."""
    dest = _norm(resolve_user_data())
    ordered = [
        os.path.join(REPO_ROOT, "user_data"),
        V6_USER_DATA,
        os.path.join(program_dir(), "core", "user_data"),
        os.path.join(os.path.expanduser("~"), ".autosub"),
    ]
    out: list[str] = []
    seen = set()
    for raw in ordered:
        if not raw or not os.path.isdir(raw):
            continue
        key = _norm(raw)
        if key == dest or key in seen:
            continue
        seen.add(key)
        out.append(os.path.abspath(raw))
    return out


def source_has_importable(src: str) -> bool:
    if os.path.isfile(os.path.join(src, "app_settings.json")):
        return True
    if os.path.isfile(os.path.join(src, "settings", "app_settings.json")):
        return True
    presets = os.path.join(src, "presets")
    if os.path.isdir(presets) and os.listdir(presets):
        return True
    # ~/.autosub/presets
    if os.path.basename(_norm(src).rstrip("\\/")) == ".autosub":
        p = os.path.join(src, "presets")
        return os.path.isdir(p) and bool(os.listdir(p))
    return False


def find_import_sources() -> list[str]:
    return [p for p in candidate_sources() if source_has_importable(p)]


def should_offer_import() -> bool:
    """First run: destination settings missing, a source exists, not golden/tests."""
    if uses_override_layout():
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.path.isfile(settings_file("app_settings.json")):
        return False
    return bool(find_import_sources())


def _copy_json_stripped(src: str, dest: str) -> bool:
    try:
        data = safe_json.load(src)
    except Exception as exc:
        logger.warning("skip unreadable %s: %s", src, exc)
        return False
    data = strip_secret_fields(data)
    data = migrate_legacy_keys(data)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        import json
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("failed writing %s: %s", dest, exc)
        return False


def _filter_recent(src_path: str, dest_path: str) -> bool:
    try:
        data = safe_json.load(src_path)
    except Exception:
        return False
    data = strip_secret_fields(data)
    data = migrate_legacy_keys(data)
    if isinstance(data, list):
        kept = []
        for item in data:
            if not isinstance(item, dict):
                continue
            p = item.get("path")
            if p and os.path.exists(p):
                kept.append(item)
        data = kept
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    try:
        import json
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _copy_presets(src_root: str) -> int:
    n = 0
    dest = user_presets_dir()
    os.makedirs(dest, exist_ok=True)
    candidates = [
        os.path.join(src_root, "presets"),
        os.path.join(src_root, "user_data", "presets"),
    ]
    if os.path.basename(_norm(src_root).rstrip("\\/")) == ".autosub":
        candidates.insert(0, os.path.join(src_root, "presets"))
    seen = set()
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        key = _norm(folder)
        if key in seen:
            continue
        seen.add(key)
        for name in os.listdir(folder):
            if name.startswith("."):
                continue
            sp = os.path.join(folder, name)
            dp = os.path.join(dest, name)
            if os.path.isfile(sp):
                if name.lower().endswith(".json"):
                    if _copy_json_stripped(sp, dp):
                        n += 1
                else:
                    try:
                        shutil.copy2(sp, dp)
                        n += 1
                    except Exception:
                        pass
    return n


def import_from(src: str) -> dict:
    """Copy allowed files from ``src`` into the current user-data tree."""
    ensure_user_layout()
    copied: list[str] = []
    src = os.path.abspath(src)
    for name in IMPORT_FILES:
        sp = os.path.join(src, name)
        if not os.path.isfile(sp):
            alt = os.path.join(src, "settings", name)
            sp = alt if os.path.isfile(alt) else ""
        if not sp:
            continue
        dest = settings_file(name)
        if name == "recent_projects.json":
            ok = _filter_recent(sp, dest)
        else:
            ok = _copy_json_stripped(sp, dest)
        if ok:
            copied.append(name)
    n_presets = _copy_presets(src)
    logger.info("imported user data from %s files=%s presets=%d", src, copied, n_presets)
    return {"source": src, "files": copied, "presets": n_presets}


def import_preferred() -> dict | None:
    sources = find_import_sources()
    if not sources:
        return None
    return import_from(sources[0])


def prepare_import_dialog(box) -> None:
    """Keep the modal above a splash (double insurance with startup order)."""
    from PySide6.QtCore import Qt
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    box.raise_()
    box.activateWindow()


def prompt_and_import(parent=None) -> dict | None:
    """Show the one-shot dialog. Returns the import report or None."""
    if not should_offer_import():
        return None
    sources = find_import_sources()
    if not sources:
        return None
    try:
        from PySide6.QtWidgets import QMessageBox
    except Exception:
        return import_from(sources[0])

    primary = sources[0]
    extra = ""
    if len(sources) > 1:
        extra = "\n\n同时发现：\n" + "\n".join(sources[1:])
    box = QMessageBox(parent)
    box.setWindowTitle("片语 Studio")
    box.setText("检测到 AutoSub v6 的设置与预设，是否导入？")
    box.setInformativeText("来源：\n%s%s\n\n密钥字段不会被复制。" % (primary, extra))
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    prepare_import_dialog(box)
    if box.exec() != QMessageBox.StandardButton.Yes:
        ensure_user_layout()
        return None
    return import_from(primary)
