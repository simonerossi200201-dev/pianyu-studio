# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import os
import re

from cutora.assets.pack import (
    ALLOWED_LICENSE, discover_packs, export_cutorapack, import_cutorapack,
    load_item_json, load_pack_json,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_t27_builtin_pack_schema():
    packs = discover_packs()
    core = next(p for p in packs if p["id"] == "cutora_core")
    assert core["license"] in ALLOWED_LICENSE
    assert core["author"] == "CUTORA"
    assert len(core["items"]) == 36
    types = {it["type"] for it in core["items"]}
    assert types == {"element", "frame", "lower_third"}
    by_type = {t: 0 for t in types}
    allowed_tags = {"svg", "path", "rect", "circle", "lineargradient", "defs", "stop"}
    for it in core["items"]:
        assert it["license"] in ALLOWED_LICENSE
        src = os.path.join(it["_dir"], it.get("file") or "")
        assert os.path.isfile(src)
        by_type[it["type"]] += 1
        if src.lower().endswith(".svg"):
            text = open(src, encoding="utf-8").read().lower()
            assert "#000" not in text and "#fff" not in text
            assert 'fill="black"' not in text and 'fill="white"' not in text
            tags = set(re.findall(r"<([a-z]+)", text))
            extra = tags - allowed_tags
            assert not extra, extra
    assert by_type == {"element": 12, "frame": 12, "lower_third": 12}


def test_gen_core_pack_deterministic(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "gen_core_pack", os.path.join(REPO, "tools", "gen_core_pack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = mod.generate(str(tmp_path / "a"))
    b = mod.generate(str(tmp_path / "b"))
    files_a = []
    files_b = []
    for root, _d, names in os.walk(a):
        for n in names:
            files_a.append(os.path.relpath(os.path.join(root, n), a).replace("\\", "/"))
    for root, _d, names in os.walk(b):
        for n in names:
            files_b.append(os.path.relpath(os.path.join(root, n), b).replace("\\", "/"))
    assert sorted(files_a) == sorted(files_b)
    for rel in files_a:
        pa = os.path.join(a, rel)
        pb = os.path.join(b, rel)
        assert open(pa, "rb").read() == open(pb, "rb").read(), rel
    from cutora.assets.pack import load_item_json, load_pack_json
    load_pack_json(os.path.join(a, "pack.json"))
    for root, _d, names in os.walk(os.path.join(a, "items")):
        if "item.json" in names:
            load_item_json(os.path.join(root, "item.json"))


def test_t27_roundtrip_zip(tmp_path):
    packs = discover_packs()
    core = next(p for p in packs if p["id"] == "cutora_core")
    zpath = str(tmp_path / "core.cutorapack")
    export_cutorapack(core["_dir"], zpath)
    assert os.path.isfile(zpath)
    dest = str(tmp_path / "packs")
    os.makedirs(dest)
    imported = import_cutorapack(zpath, dest_root=dest)
    assert imported["id"] == "cutora_core"
    assert len(imported["items"]) == len(core["items"])
    load_pack_json(os.path.join(imported["_dir"], "pack.json"))
    load_item_json(os.path.join(imported["items"][0]["_dir"], "item.json"))
