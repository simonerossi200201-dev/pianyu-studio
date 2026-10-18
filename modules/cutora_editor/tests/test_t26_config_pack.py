# -*- coding: utf-8 -*-
"""T26: config zip export/import merges, does not overwrite same-name."""
from __future__ import annotations

import json
import os

from cutora.model.config_pack import PACK_KIND, export_config, import_config


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def test_t26_export_import_skip_same_name(tmp_path):
    src = tmp_path / "user_a"
    dst = tmp_path / "user_b"
    (src / "presets").mkdir(parents=True)
    _write(str(src / "presets" / "text_presets.json"), {
        "presets": [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}],
    })
    _write(str(src / "settings" / "app_settings.json"), {
        "theme_id": "dark", "shortcuts": {"razor": "Ctrl+B"},
    })
    (src / "fonts").mkdir()
    (src / "fonts" / "Demo.ttf").write_bytes(b"ttf")
    zpath = str(tmp_path / "cfg.zip")
    out = export_config(zpath, user_root=str(src))
    assert os.path.isfile(zpath)
    assert out["manifest"]["kind"] == PACK_KIND
    assert out["manifest"]["counts"]["presets"] >= 1

    (dst / "presets").mkdir(parents=True)
    _write(str(dst / "presets" / "text_presets.json"), {
        "presets": [{"id": "a", "name": "Alpha-local"}],
    })
    _write(str(dst / "settings" / "app_settings.json"), {"theme_id": "light"})
    report = import_config(zpath, user_root=str(dst), overwrite=False)
    merged = json.loads((dst / "presets" / "text_presets.json").read_text(encoding="utf-8"))
    ids = [p["id"] for p in merged["presets"]]
    assert ids.count("a") == 1
    assert "b" in ids
    names = {p["id"]: p["name"] for p in merged["presets"]}
    assert names["a"] == "Alpha-local"
    settings = json.loads((dst / "settings" / "app_settings.json").read_text(encoding="utf-8"))
    assert settings["theme_id"] == "light"
    assert settings.get("shortcuts") == {"razor": "Ctrl+B"}
    assert (dst / "fonts" / "Demo.ttf").is_file()
    assert "a" in report["sections"]["text_presets.json"]["skipped"]
    assert "theme_id" in report["sections"]["settings"]["skipped"]


def test_t26_roundtrip_keeps_user_assets(tmp_path):
    src = tmp_path / "user_a"
    dst = tmp_path / "user_b"
    (src / "assets" / "user" / "sticker").mkdir(parents=True)
    asset = src / "assets" / "user" / "sticker" / "star.png"
    asset.write_bytes(b"png-bytes")
    (src / "assets" / "user" / "sticker" / "item.json").write_text(
        '{"id": "star", "license": "self"}', encoding="utf-8")
    zpath = str(tmp_path / "cfg.zip")
    out = export_config(zpath, user_root=str(src))
    sections = out["manifest"]["sections"]
    assert "assets" in sections
    assert "shortcuts" not in sections
    assert "theme" not in sections
    assert set(sections) <= set(out["manifest"]["counts"])
    for name in sections:
        assert out["manifest"]["counts"].get(name, 0) > 0

    report = import_config(zpath, user_root=str(dst), overwrite=False)
    got = dst / "assets" / "user" / "sticker" / "star.png"
    assert got.is_file()
    assert got.read_bytes() == b"png-bytes"
    assert "star.png" in (report["sections"].get("assets") or {}).get("added", [])

    (dst / "assets" / "user" / "sticker" / "star.png").write_bytes(b"local")
    report2 = import_config(zpath, user_root=str(dst), overwrite=False)
    assert (dst / "assets" / "user" / "sticker" / "star.png").read_bytes() == b"local"
    assert "star.png" in (report2["sections"].get("assets") or {}).get("skipped", [])


def test_t26_overwrite_replaces(tmp_path):
    src = tmp_path / "a"
    dst = tmp_path / "b"
    (src / "settings").mkdir(parents=True)
    (dst / "settings").mkdir(parents=True)
    _write(str(src / "settings" / "app_settings.json"), {"theme_id": "dark"})
    _write(str(dst / "settings" / "app_settings.json"), {"theme_id": "light"})
    zpath = str(tmp_path / "c.zip")
    export_config(zpath, user_root=str(src))
    import_config(zpath, user_root=str(dst), overwrite=True)
    settings = json.loads((dst / "settings" / "app_settings.json").read_text(encoding="utf-8"))
    assert settings["theme_id"] == "dark"
