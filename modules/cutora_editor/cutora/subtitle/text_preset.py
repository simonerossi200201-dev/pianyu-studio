# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T06-1 text-layer templates with short-side-relative layout."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

from cutora.core.paths import RESOURCES, user_presets_dir
from cutora.core.preset_io import dump_presets as _dump
from cutora.core.preset_io import read_preset_file as _read_json
from cutora.core.preset_io import write_preset_file as _write_json
from cutora.subtitle.style_schema import (
    TEXT_LAYER_CANONICAL_KEYS,
    TEXT_LAYER_DEFAULTS,
    TEXT_LAYER_LAYOUT_KEYS,
    TEXT_LAYOUT_DEFAULT,
)

ANCHORS = (
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
)

# Inside 9:16 platform UI (TikTok right 0.18 / bottom 0.22 of that side).
# Margins are fractions of the canvas SHORT side (T06 1a).
DEFAULT_CREDIT_LAYOUT = {
    "anchor": "bottom_right",
    "margin_x": 0.20,
    "margin_y": 0.40,
    "size_pct": 0.035,
    "z": 20,
    "box": None,
    "scale": 1.0,
}
_BOX_KEYS = ("x", "y", "w", "h")

_STORE_NAME = "text_presets.json"
_LEGACY_STORE_NAME = "text_style_presets.json"
_BUILTIN_PATH = os.path.join(RESOURCES, "data", "presets", "text_presets.json")
DEFAULT_PRESET_ID = "credit.default"

_TEMPLATE_DROP_KEYS = {
    "id", "start", "end", "_track_id", "type", "track_index",
    "spans", "_preset_name", "_rich_cache", "source_preset_id",
}

_migrated_paths = None


def _user_path() -> str:
    return os.path.join(user_presets_dir(), _STORE_NAME)


def _legacy_path() -> str:
    return os.path.join(user_presets_dir(), _LEGACY_STORE_NAME)


def _load_user_store() -> dict:
    data = _read_json(_user_path())
    if not isinstance(data.get("presets"), list):
        data["presets"] = []
    data.setdefault("version", 1)
    return data


def _write_user_store(data: dict) -> None:
    out = dict(data) if isinstance(data, dict) else {}
    out.setdefault("version", 1)
    if not isinstance(out.get("presets"), list):
        out["presets"] = []
    _write_json(_user_path(), out)


def reset_runtime_state_for_tests() -> None:
    """Drop in-memory migrate flag so the next call re-reads disk (simulated restart)."""
    global _migrated_paths
    _migrated_paths = None


def _read_legacy_file() -> dict:
    """Read-only. T11d: never create or write text_style_presets.json."""
    path = _legacy_path()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _slug_legacy_id(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    slug = "_".join(p.lower() for p in parts) or "named"
    return "legacy." + slug


def _unique_legacy_id(name: str, used: set[str]) -> str:
    base = _slug_legacy_id(name)
    if base not in used:
        return base
    digest = hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:8]
    pid = base + "." + digest
    n = 1
    while pid in used:
        pid = "%s.%s%d" % (base, digest, n)
        n += 1
    return pid


def _row_from_legacy_named(name: str, style: Any, pid: str) -> dict[str, Any]:
    src = style if isinstance(style, dict) else {}
    text = src.get("content")
    if text is None:
        text = src.get("text") or ""
    layout = src.get("layout") if isinstance(src.get("layout"), dict) else src
    return _sanitize_preset({
        "id": pid,
        "name": str(name).strip() or pid,
        "text": text,
        "style": src,
        "layout": layout,
        "animation": src.get("anim_in_type") or src.get("animation") or "none",
        "builtin": False,
    }, builtin=False)


def ensure_legacy_migrated() -> dict:
    """Copy old named presets into text_presets.json once. Never write the old file."""
    global _migrated_paths
    key = (_user_path(), _legacy_path())
    store = _load_user_store()
    if store.get("legacy_migrated"):
        _migrated_paths = key
        return store
    if _migrated_paths == key:
        return store

    old = _read_legacy_file()
    old_presets = old.get("presets") if isinstance(old.get("presets"), dict) else {}
    users = [
        _sanitize_preset(p, builtin=False)
        for p in (store.get("presets") or [])
        if isinstance(p, dict)
    ]
    used_ids = {p["id"] for p in users} | {p["id"] for p in load_builtin()}
    used_names = {p["name"] for p in users}
    changed = False
    for name, style in old_presets.items():
        name = str(name)
        if not name or name in used_names:
            continue
        pid = _unique_legacy_id(name, used_ids)
        row = _row_from_legacy_named(name, style, pid)
        users.append(row)
        used_ids.add(row["id"])
        used_names.add(row["name"])
        changed = True

    default_name = str(old.get("default_preset_name") or "").strip()
    if default_name and not str(store.get("default_preset_id") or "").strip():
        match = next((p for p in users if p["name"] == default_name), None)
        if match:
            store["default_preset_id"] = match["id"]
            changed = True

    store["presets"] = users
    store["legacy_migrated"] = True
    _migrated_paths = key
    if changed or os.path.isfile(_user_path()):
        _write_user_store(store)
    return store


def short_side(canvas_w: float, canvas_h: float) -> float:
    return float(min(max(1.0, canvas_w), max(1.0, canvas_h)))


def normalize_anchor(raw: str | None) -> str:
    a = str(raw or "bottom_right").strip().lower().replace("-", "_").replace(" ", "_")
    return a if a in ANCHORS else "bottom_right"


def normalize_layout(raw: dict | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_CREDIT_LAYOUT)
    out["anchor"] = normalize_anchor(src.get("anchor") or out["anchor"])
    for key in ("margin_x", "margin_y", "size_pct"):
        try:
            out[key] = float(src.get(key, out[key]))
        except (TypeError, ValueError):
            pass
    try:
        out["z"] = int(src.get("z", out["z"]))
    except (TypeError, ValueError):
        pass
    out["box"] = _normalize_box(src.get("box"))
    try:
        sc = float(src.get("scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        sc = 1.0
    out["scale"] = sc if 0.05 <= sc <= 20.0 else 1.0
    return out


def _normalize_box(raw) -> dict | None:
    """{x,y,w,h} 都是画布比例（0..1 附近，允许略出界）。缺一个就当没有。"""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in _BOX_KEYS:
        try:
            v = float(raw.get(k))
        except (TypeError, ValueError):
            return None
        if v != v:   # NaN
            return None
        out[k] = v
    if out["w"] <= 0 or out["h"] <= 0:
        return None
    return out


def layout_to_box(
    layout: dict, canvas_w: float, canvas_h: float, text: str = "",
) -> tuple[float, float, float, float, float]:
    """Return (x, y, w, h, fontsize_px). Margins and size_pct use the short side."""
    lay = normalize_layout(layout)
    cw, ch = float(canvas_w), float(canvas_h)
    short = short_side(cw, ch)
    mx = float(lay["margin_x"]) * short
    my = float(lay["margin_y"]) * short
    fs = max(8.0, float(lay["size_pct"]) * short)
    box = lay.get("box")
    if isinstance(box, dict):
        # Q136：存过整个框就原样还原（按画布比例），不再按文字长度猜宽度。
        return (box["x"] * cw, box["y"] * ch,
                max(8.0, box["w"] * cw), max(8.0, box["h"] * ch), fs)
    # 老预设：没有框，按锚点 + 边距 + 文字长度估。以前空文本估出来的框只有
    # 2.4 em 宽，一句话进去就一列字（Luca：「揉成一团」）—— 至少给 8 em。
    nchar = max(1, len(text or "M"))
    tw = min(cw * 0.72, max(fs * 8.0, fs * 0.62 * nchar))
    th = fs * 1.55
    tw = min(tw, max(8.0, cw - 2.0 * mx))
    th = min(th, max(8.0, ch - 2.0 * my))
    anchor = lay["anchor"]
    if "left" in anchor:
        x = mx
    elif "right" in anchor:
        x = cw - tw - mx
    else:
        x = (cw - tw) / 2.0
    if anchor.startswith("top"):
        y = my
    elif anchor.startswith("bottom"):
        y = ch - th - my
    else:
        y = (ch - th) / 2.0
    return x, y, tw, th, fs


def box_to_layout(
    x: float, y: float, w: float, h: float, fontsize: float,
    canvas_w: float, canvas_h: float, anchor: str | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    cw, ch = float(canvas_w), float(canvas_h)
    short = short_side(cw, ch)
    anc = normalize_anchor(anchor) if anchor else _guess_anchor(x, y, w, h, cw, ch)
    if "left" in anc:
        mx = x / short
    elif "right" in anc:
        mx = (cw - x - w) / short
    else:
        mx = 0.0
    if anc.startswith("top"):
        my = y / short
    elif anc.startswith("bottom"):
        my = (ch - y - h) / short
    else:
        my = 0.0
    return normalize_layout({
        "anchor": anc,
        "margin_x": mx,
        "margin_y": my,
        "size_pct": float(fontsize) / short if short else 0.035,
        "box": {"x": float(x) / cw, "y": float(y) / ch,
                "w": float(w) / cw, "h": float(h) / ch} if cw > 0 and ch > 0 else None,
        "scale": scale,
    })


def _guess_anchor(x, y, w, h, cw, ch) -> str:
    cx, cy = x + w / 2.0, y + h / 2.0
    col = 0 if cx < cw / 3.0 else (2 if cx > 2.0 * cw / 3.0 else 1)
    row = 0 if cy < ch / 3.0 else (2 if cy > 2.0 * ch / 3.0 else 1)
    grid = (
        ("top_left", "top_center", "top_right"),
        ("center_left", "center", "center_right"),
        ("bottom_left", "bottom_center", "bottom_right"),
    )
    return grid[row][col]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fill_text_layer_style(src: dict | None) -> dict[str, Any]:
    """Return a style dict whose keys equal TEXT_LAYER_CANONICAL_KEYS.

    Missing keys get TEXT_LAYER_DEFAULTS. Extra keys are dropped (leftover=0).
    Overlay-namespace names only — do not fold subtitle aliases.
    """
    raw = src if isinstance(src, dict) else {}
    out: dict[str, Any] = {}
    for key in TEXT_LAYER_CANONICAL_KEYS:
        if key == "text_layout":
            lay = raw.get("text_layout") if isinstance(raw.get("text_layout"), dict) else {}
            filled = dict(TEXT_LAYOUT_DEFAULT)
            for lk, dv in TEXT_LAYOUT_DEFAULT.items():
                if lk in lay:
                    filled[lk] = copy.deepcopy(lay[lk])
            out[key] = filled
            continue
        if key in raw:
            out[key] = copy.deepcopy(raw[key])
        else:
            out[key] = copy.deepcopy(TEXT_LAYER_DEFAULTS[key])
    return out


def _sanitize_preset(raw: dict, *, builtin: bool = False) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    pid = str(src.get("id") or "").strip() or ("p_" + uuid.uuid4().hex[:12])
    name = str(src.get("name") or pid).strip() or pid
    text = src.get("text")
    if text is None:
        text = src.get("content") or ""
    style = src.get("style") if isinstance(src.get("style"), dict) else {}
    # Flat overlay dumps: lift from the overlay itself.
    if not style:
        style = src
    style = fill_text_layer_style(style)
    anim = src.get("animation", "none")
    _lay = normalize_layout(src.get("layout") if isinstance(src.get("layout"), dict) else src)
    out = {
        "id": pid,
        "name": name,
        "text": str(text),
        "style": style,
        "layout": {
            k: _lay[k] for k in TEXT_LAYER_LAYOUT_KEYS
        },
        "animation": copy.deepcopy(anim),
        "builtin": bool(src.get("builtin", builtin)),
        "created_at": str(src.get("created_at") or _now()),
        "updated_at": str(src.get("updated_at") or src.get("created_at") or _now()),
    }
    cat = str(src.get("category") or "").strip()
    if cat:
        out["category"] = cat
    return out


DEFAULT_BUILTIN_CATEGORY_1 = "01·艺术线框与通透质感"
DEFAULT_BUILTIN_CATEGORY_2 = "02·实心渐变与清晰底条"
DEFAULT_USER_CATEGORY = "03·我的自定义预设"


def preset_category_of(row: dict | None) -> str:
    if not isinstance(row, dict):
        return DEFAULT_BUILTIN_CATEGORY_1
    cat = str(row.get("category") or "").strip()
    if cat:
        return cat
    pid = str(row.get("id") or "")
    if pid.startswith("tp_solid_"):
        return DEFAULT_BUILTIN_CATEGORY_2
    if not row.get("builtin", True):
        return DEFAULT_USER_CATEGORY
    return DEFAULT_BUILTIN_CATEGORY_1


def list_preset_categories(rows: list[dict] | None = None) -> list[str]:
    items = rows if rows is not None else list_presets()
    seen: list[str] = []
    for r in items:
        c = preset_category_of(r)
        if c and c not in seen:
            seen.append(c)
    seen.sort()
    return seen


def load_builtin() -> list[dict]:
    data = _read_json(_BUILTIN_PATH)
    return [_sanitize_preset(p, builtin=True) for p in (data.get("presets") or []) if isinstance(p, dict)]


def load_user() -> list[dict]:
    ensure_legacy_migrated()
    data = _load_user_store()
    return [_sanitize_preset(p, builtin=False) for p in (data.get("presets") or []) if isinstance(p, dict)]


def list_presets() -> list[dict]:
    ensure_legacy_migrated()
    by_id: dict[str, dict] = {}
    for row in load_builtin():
        by_id[row["id"]] = row
    for row in load_user():
        by_id[row["id"]] = row
    rows = list(by_id.values())
    rows.sort(key=lambda r: (0 if r.get("builtin") else 1, preset_category_of(r), str(r.get("name") or "")))
    return rows


def get_preset(preset_id: str) -> dict | None:
    want = str(preset_id or "").strip()
    if not want:
        return None
    for row in list_presets():
        if row["id"] == want:
            return copy.deepcopy(row)
    return None


def resolve_preset(ref: str | None) -> dict | None:
    """Lookup by id, then display name. Old names exist only if migrate copied them."""
    ensure_legacy_migrated()
    want = str(ref or "").strip()
    if not want:
        return get_preset(DEFAULT_PRESET_ID)
    hit = get_preset(want)
    if hit is not None:
        return hit
    for row in list_presets():
        if str(row.get("name") or "") == want:
            return copy.deepcopy(row)
    return None


def apply_layout_to_overlay(ov: dict, layout: dict, canvas_w: float,
                            canvas_h: float, text: str = "") -> dict:
    """把预设的比例框落到这一张画布上。字号按短边重算，盖过样式里的绝对像素。"""
    if not isinstance(ov, dict) or not isinstance(layout, dict):
        return ov
    x, y, w, h, fs = layout_to_box(layout, canvas_w, canvas_h, str(text or ""))
    ov["x"] = float(x)
    ov["y"] = float(y)
    ov["w"] = float(w)
    ov["h"] = float(h)
    ov["fontsize"] = float(fs)
    try:
        sc = float(layout.get("scale") or 1.0)
    except (TypeError, ValueError):
        sc = 1.0
    if abs(sc - 1.0) > 1e-6:
        ov["scale"] = sc
    if h:
        ov["aspect_ratio"] = float(w) / float(h)
    return ov


# x、w 都要指向同一张「当时那张画布」，而且跟现在这张不同，才算被印死的像素。
_RETARGET_TOL_PX = 2.0
_RETARGET_MIN_FRAC = 0.02
_PRESET_UNSET = object()


def _implied_canvas_px(px, frac):
    try:
        px_f = float(px)
        frac_f = float(frac)
    except (TypeError, ValueError):
        return None
    if abs(frac_f) < _RETARGET_MIN_FRAC:
        return None
    return px_f / frac_f


def retarget_stamped_box(ov: dict, canvas_w: float, canvas_h: float,
                         preset=_PRESET_UNSET) -> bool:
    """框是另一张画布的绝对像素时，按来源预设的比例重放到本画布。

    x 和 w 推出来的宽度要一致，而且跟本画布不同，才改。
    推出来就是本画布（后来只拖过 y）则不动。没有比例框的预设不动。
    """
    if not isinstance(ov, dict):
        return False
    if preset is _PRESET_UNSET:
        pid = str(ov.get("source_preset_id") or "").strip()
        preset = get_preset(pid) if pid else None
    if not isinstance(preset, dict):
        return False
    layout = preset.get("layout") if isinstance(preset.get("layout"), dict) else None
    box = (layout or {}).get("box") if isinstance((layout or {}).get("box"), dict) else None
    if not isinstance(box, dict):
        return False
    try:
        cw = float(canvas_w)
        ch = float(canvas_h)
    except (TypeError, ValueError):
        return False
    if cw <= 0 or ch <= 0:
        return False
    implied_x = _implied_canvas_px(ov.get("x"), box.get("x"))
    implied_w = _implied_canvas_px(ov.get("w"), box.get("w"))
    if implied_x is None and implied_w is None:
        return False
    if (implied_x is not None and implied_w is not None
            and abs(implied_x - implied_w) > _RETARGET_TOL_PX):
        return False
    implied = implied_w if implied_x is None else (
        implied_x if implied_w is None else (implied_x + implied_w) / 2.0)
    if abs(implied - cw) <= _RETARGET_TOL_PX:
        return False
    apply_layout_to_overlay(
        ov, layout, cw, ch, str(ov.get("content") or preset.get("text") or ""))
    return True


def retarget_project_text_boxes(data) -> int:
    """打开工程时用。只改「比例框被写成了别的画布像素」的文本层。"""
    if not isinstance(data, dict):
        return 0
    from cutora.model.canvas import canvas_wh
    cache: dict[str, Any] = {}
    n = 0
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        try:
            cw, ch = canvas_wh(task)
        except Exception:
            continue
        for ov in task.get("text_overlays") or []:
            if not isinstance(ov, dict):
                continue
            pid = str(ov.get("source_preset_id") or "").strip()
            if not pid:
                continue
            if pid not in cache:
                try:
                    cache[pid] = get_preset(pid)
                except Exception:
                    cache[pid] = None
            if retarget_stamped_box(ov, cw, ch, preset=cache[pid]):
                n += 1
    return n


def get_default_preset_id() -> str:
    ensure_legacy_migrated()
    store = _load_user_store()
    pid = str(store.get("default_preset_id") or "").strip()
    if pid and get_preset(pid) is not None:
        return pid
    return DEFAULT_PRESET_ID


def set_default_preset_id(preset_id: str) -> bool:
    ensure_legacy_migrated()
    pid = str(preset_id or "").strip()
    if pid and get_preset(pid) is None:
        return False
    store = _load_user_store()
    store["default_preset_id"] = pid or DEFAULT_PRESET_ID
    store["legacy_migrated"] = True
    _write_user_store(store)
    return True


def template_from_preset(preset: dict) -> dict:
    """Style+text snapshot for applying onto an existing overlay (keep geometry)."""
    row = _sanitize_preset(preset)
    template = copy.deepcopy(row.get("style") or {})
    if not isinstance(template, dict):
        template = {}
    for k in _TEMPLATE_DROP_KEYS:
        template.pop(k, None)
    template["content"] = row.get("text") or ""
    # Q145：预设里「整段一个字体 / 字号」存成了覆盖全文的 run（用选中文字工具条设的）。
    # 套到别的文本上时 run 的范围对不上、面板也显示不出真正的字体 —— 先提升成样式本身。
    from cutora.subtitle.text_layer import promote_whole_runs
    promote_whole_runs(template, template["content"])
    template["source_preset_id"] = row["id"]
    template["_preset_name"] = row["name"]
    return template


def save_preset(raw: dict, *, overwrite: bool = True) -> dict:
    ensure_legacy_migrated()
    row = _sanitize_preset(raw, builtin=False)
    row["builtin"] = False
    if not str(raw.get("updated_at") or "").strip():
        row["updated_at"] = _now()
    if not str(raw.get("created_at") or "").strip():
        row["created_at"] = row.get("created_at") or row["updated_at"]
    store = _load_user_store()
    users = [
        _sanitize_preset(p, builtin=False)
        for p in (store.get("presets") or [])
        if isinstance(p, dict)
    ]
    idx = next((i for i, p in enumerate(users) if p["id"] == row["id"]), -1)
    builtin_ids = {p["id"] for p in load_builtin()}
    if row["id"] in builtin_ids and idx < 0 and not overwrite:
        raise ValueError("不能覆盖内置文本预设")
    if idx >= 0:
        if not overwrite:
            raise ValueError("预设已存在")
        row["created_at"] = users[idx].get("created_at") or row["created_at"]
        users[idx] = row
    else:
        users.append(row)
    store["presets"] = users
    store["legacy_migrated"] = True
    _write_user_store(store)
    return copy.deepcopy(row)


def delete_preset(preset_id: str) -> bool:
    ensure_legacy_migrated()
    want = str(preset_id or "").strip()
    if not want:
        return False
    if any(p["id"] == want and p.get("builtin") for p in load_builtin()):
        raise ValueError("不能删除内置文本预设")
    store = _load_user_store()
    users = [
        _sanitize_preset(p, builtin=False)
        for p in (store.get("presets") or [])
        if isinstance(p, dict)
    ]
    nxt = [p for p in users if p["id"] != want]
    if len(nxt) == len(users):
        return False
    store["presets"] = nxt
    store["legacy_migrated"] = True
    _write_user_store(store)
    return True


def export_presets(file_path: str) -> bool:
    ensure_legacy_migrated()
    try:
        payload = {
            "version": 1,
            "default_preset_id": get_default_preset_id(),
            "presets": list_presets(),
        }
        _write_json(file_path, payload)
        return True
    except Exception:
        return False


def import_presets(file_path: str) -> dict:
    """Import new-format list or old named-dict. Never writes text_style_presets.json."""
    ensure_legacy_migrated()
    result: dict[str, Any] = {"added": [], "conflicts": {}, "skipped": []}
    try:
        with open(file_path, encoding="utf-8") as fh:
            incoming = json.load(fh)
    except Exception:
        return result
    if not isinstance(incoming, dict):
        return result
    rows: list[dict] = []
    raw_presets = incoming.get("presets")
    if isinstance(raw_presets, list):
        rows = [p for p in raw_presets if isinstance(p, dict)]
    elif isinstance(raw_presets, dict):
        used = {p["id"] for p in list_presets()}
        for name, style in raw_presets.items():
            pid = _unique_legacy_id(str(name), used)
            used.add(pid)
            rows.append(_row_from_legacy_named(str(name), style, pid))
    existing_ids = {p["id"] for p in list_presets()}
    existing_names = {p["name"]: p["id"] for p in list_presets()}
    for raw in rows:
        row = _sanitize_preset(raw, builtin=False)
        row["builtin"] = False
        if row["id"] in existing_ids or row["name"] in existing_names:
            result["conflicts"][row["name"]] = row
            continue
        try:
            saved = save_preset(row, overwrite=False)
        except ValueError:
            result["skipped"].append(row["name"])
            continue
        result["added"].append(saved["name"])
        existing_ids.add(saved["id"])
        existing_names[saved["name"]] = saved["id"]
    return result


def overlay_from_preset(preset: dict, canvas_w: float, canvas_h: float, *,
                        duration: float = 5.0, overlay_id: str | None = None) -> dict:
    row = _sanitize_preset(preset)
    x, y, w, h, fs = layout_to_box(row["layout"], canvas_w, canvas_h, row["text"])
    ov = {
        "type": "text",
        "id": overlay_id or ("ov_tp_%s" % uuid.uuid4().hex[:10]),
        "content": row["text"],
        "content_format": "plain",
        "start": 0.0,
        "end": float(duration),
        "x": x, "y": y, "w": w, "h": h,
        "fontsize": fs,
        "source_preset_id": row["id"],
        "layout": copy.deepcopy(row["layout"]),
        "blend_mode": "normal",
        "opacity": 255,
        "rotation": 0,
        "lock_aspect": True,
        "aspect_ratio": (float(w) / float(h)) if h else 1.0,
    }
    style = row.get("style") or {}
    ov.update(copy.deepcopy(style))
    ov["fontsize"] = fs
    ov["content"] = row["text"]
    try:
        sc = float((row.get("layout") or {}).get("scale") or 1.0)
    except (TypeError, ValueError):
        sc = 1.0
    if abs(sc - 1.0) > 1e-6:
        ov["scale"] = sc
    anim = row.get("animation")
    if isinstance(anim, str) and anim and anim != "none":
        ov.setdefault("anim_in_type", anim)
    elif isinstance(anim, dict):
        ov.update(copy.deepcopy(anim))
    return ov


def _task_duration(task: dict) -> float:
    try:
        dur = float(task.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur > 0:
        return dur
    ends = []
    for seg in task.get("segments") or []:
        if isinstance(seg, dict):
            try:
                ends.append(float(seg.get("end") or 0.0))
            except (TypeError, ValueError):
                pass
    return max(ends) if ends else 5.0


def apply_to_project_file(path: str, preset_id: str, *, idempotent: bool = True) -> dict:
    """Headless: apply to every task in a .asproj.json. Returns overlay counts."""
    preset = resolve_preset(preset_id)
    if preset is None:
        raise ValueError("文本预设不存在: %s" % preset_id)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("工程文件不是 JSON 对象")
    tasks = list(data.get("tasks") or [])
    n = 0
    for task in tasks:
        if isinstance(task, dict):
            apply_to_task(task, preset, idempotent=idempotent)
            n += 1
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return {"ok": True, "tasks": n, "preset_id": preset["id"]}


def apply_to_task(task: dict, preset: dict, *, idempotent: bool = False) -> dict:
    """Create a text overlay from ``preset``. Idempotent path keys on source_preset_id."""
    from cutora.subtitle.io import canvas_size
    row = _sanitize_preset(preset)
    cw, ch = canvas_size(task)
    dur = _task_duration(task)
    ovs = list(task.get("text_overlays") or [])
    keep_id = None
    if idempotent:
        hits = [i for i, ov in enumerate(ovs)
                if isinstance(ov, dict) and str(ov.get("source_preset_id") or "") == row["id"]]
        if hits:
            keep_id = str(ovs[hits[0]].get("id") or "") or None
            ovs = [ov for i, ov in enumerate(ovs) if i not in set(hits)]
    ov = overlay_from_preset(row, cw, ch, duration=dur, overlay_id=keep_id)
    ovs.append(ov)
    task["text_overlays"] = ovs
    return {"ok": True, "overlay_id": ov["id"], "preset_id": row["id"],
            "count": sum(1 for x in ovs if str(x.get("source_preset_id") or "") == row["id"])}


def preset_from_overlay(ov: dict, canvas_w: float, canvas_h: float, *,
                        name: str = "", preset_id: str | None = None) -> dict:
    ov = ov if isinstance(ov, dict) else {}
    layout = ov.get("layout") if isinstance(ov.get("layout"), dict) else None
    # Q136：只要覆层带着自己的框，就按现在的框重算 —— 用户套完预设又拖过、
    # 改过宽度，存的时候要的是眼前这个成品，不是当初那个预设的旧框。
    if layout is None or all(ov.get(k) is not None for k in ("x", "y", "w", "h")):
        layout = box_to_layout(
            float(ov.get("x") or 0), float(ov.get("y") or 0),
            float(ov.get("w") or 100), float(ov.get("h") or 40),
            float(ov.get("fontsize") or 24),
            canvas_w, canvas_h,
            ov.get("anchor") or (layout or {}).get("anchor"),
            scale=float(ov.get("scale") or 1.0),
        )
    return _sanitize_preset({
        "id": preset_id or ov.get("source_preset_id") or ("p_" + uuid.uuid4().hex[:12]),
        "name": name or ov.get("name") or "文本预设",
        "text": ov.get("content") or ov.get("text") or "",
        "style": ov,
        "layout": layout,
        "animation": ov.get("anim_in_type") or "none",
    })
