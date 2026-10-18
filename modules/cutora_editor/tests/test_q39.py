# -*- coding: utf-8 -*-
"""Q39: T04 leftover locks, readable asset dirs, no third-party UI names."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil

from q14_media import make_circle_png

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BANNED = (
    ("davinci", re.compile(r"\bdavinci\b", re.IGNORECASE)),
    ("davinci-resolve", re.compile(r"davinci[\s_-]*resolve", re.IGNORECASE)),
    ("capcut", re.compile(r"\bcapcut\b", re.IGNORECASE)),
    ("jianying", re.compile(r"剪映")),
    ("davinci-zh", re.compile(r"达芬奇")),
    ("premiere", re.compile(r"\bpremiere\b", re.IGNORECASE)),
    ("final-cut", re.compile(r"final[\s_-]*cut", re.IGNORECASE)),
    ("videokit", re.compile(r"\bvideokit\b", re.IGNORECASE)),
    ("wav2lip", re.compile(r"\bwav2lip\b", re.IGNORECASE)),
)

# Task: tr(...) args, setText / setToolTip / addItem literals. Comments allowed.
_UI_CALLS = frozenset({"tr", "setText", "setToolTip", "addItem"})


def _call_attr(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _string_bits(node: ast.AST) -> list[str]:
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        out.extend(_string_bits(node.left))
        out.extend(_string_bits(node.right))
    elif isinstance(node, ast.Call):
        for arg in node.args:
            out.extend(_string_bits(arg))
    return out


def test_q39_no_third_party_names():
    hits = []
    cutora = os.path.join(REPO, "cutora")
    for dirpath, dirnames, filenames in os.walk(cutora):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            src = open(path, encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            rel = os.path.relpath(path, REPO).replace("\\", "/")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_attr(node.func) not in _UI_CALLS:
                    continue
                if not node.args:
                    continue
                text = " ".join(_string_bits(node.args[0]))
                if not text:
                    continue
                for label, cre in BANNED:
                    if cre.search(text):
                        hits.append("%s:%d: [%s] %s" % (
                            rel, node.lineno, label, text[:160]))
    assert hits == [], "third-party names in user-visible strings:\n" + "\n".join(hits)


def test_q39_rename_keeps_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from cutora.assets import user as user_mod
    from cutora.model.offline import inspect_project_data, list_offline
    from cutora.model.project_io import _normalize_project_data

    old_id = "deadbeefcafe"
    png = make_circle_png(str(tmp_path / "logo.png"), w=16, h=16, radius=6, alpha=200)
    cat_dir = os.path.join(user_mod.user_assets_root(), "sticker")
    old_dir = os.path.join(cat_dir, old_id)
    os.makedirs(old_dir, exist_ok=True)
    dest_file = os.path.join(old_dir, "logo.png")
    shutil.copy2(png, dest_file)
    rec = {
        "id": old_id,
        "type": "sticker",
        "name": "logo",
        "license": "自有",
        "file": "logo.png",
        "alpha": True,
    }
    with open(os.path.join(old_dir, "item.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)

    loaded = user_mod.iter_user_assets("sticker")
    assert any(it["id"] == old_id for it in loaded), loaded
    hit = next(it for it in loaded if it["id"] == old_id)
    assert os.path.isfile(hit["stored_path"])
    assert os.path.normcase(hit["stored_path"]) == os.path.normcase(dest_file)

    project = {
        "version": 2,
        "tasks": [{
            "video": dest_file,
            "path": dest_file,
            "duration": 2.0,
            "timeline": {
                "width": 720, "height": 1280, "fps": 30,
                "sources": {
                    old_id: {"id": old_id, "path": dest_file, "duration": 2.0},
                },
                "tracks": [{
                    "id": "v1", "type": "video",
                    "clips": [{
                        "source_id": old_id, "in_t": 0.0, "out_t": 2.0, "start_t": 0.0,
                    }],
                }],
            },
        }],
    }
    _normalize_project_data(project)
    report = inspect_project_data(project)
    assert report["offline_paths"] == [], report
    assert list_offline(project["tasks"]) == []
    assert os.path.isdir(old_dir)
    assert os.path.basename(old_dir) == old_id

    fresh = make_circle_png(
        str(tmp_path / "My Logo.png"), w=16, h=16, radius=6, alpha=200)
    new_rec = user_mod.import_user_asset(fresh)
    leaf = os.path.basename(new_rec["_dir"])
    assert new_rec["id"][:8] in leaf, leaf
    assert "My_Logo" in leaf, leaf
    assert leaf != new_rec["id"], leaf
    # Old folder must still be the id-named directory.
    assert os.path.isdir(old_dir)
    assert os.path.basename(old_dir) == old_id
    assert os.path.isfile(dest_file)


def test_q39_sticker_pop_in_scales():
    from cutora.render.stickers import sticker_transform

    ov = {"start": 0.0, "end": 2.0}
    el = {"anim_in": "pop_in", "anim_in_s": 0.5, "anim_out": "none"}
    # ease_out_back peaks near u=0.58 → t = 0.58 * 0.5 = 0.29.
    # Clamp-to-none (the T04 leftover) yields scale_x=1.0 at every t.
    xf = sticker_transform(el, ov, 0.29)
    assert xf.scale_x > 1.08, (
        "pop_in at overshoot must scale past 1.08; got scale_x=%s (clamped to none?)"
        % xf.scale_x)


def test_q39_safe_zones_cache_is_copy():
    from cutora.render import safe_zones

    safe_zones._CACHE = None
    first = safe_zones.load_safe_zones()
    assert isinstance(first, dict)
    first["q39_poison"] = True
    second = safe_zones.load_safe_zones()
    assert "q39_poison" not in second, (
        "load_safe_zones handed out the cached dict; mutation leaked")
