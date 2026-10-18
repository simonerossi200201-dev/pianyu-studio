# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q138 · 旧版（AutoSub v6）字幕预设 JSON 导入：读取后转成新版风格包。"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

LEGACY = {
    "border_width": 2, "box_blur": 0, "box_padding_x": 20, "box_padding_y": 20,
    "box_radius": 20, "color_bg": "#202040", "color_high": "#ffff00", "color_high_bg": "#FF0000",
    "color_outline": "#000000", "color_shadow": "#000000", "color_text": "#ffffff",
    "color_underline": "#000000", "dynamic_box": True, "dynamic_radius": 12,
    "font_family": "Arimo", "fontsize": 48, "high_offset_y": 0, "high_padding": 5,
    "opacity_bg": 186, "opacity_high_bg": 255, "opacity_outline": 255, "opacity_shadow": 100,
    "pos_x": 548, "pos_y": 524, "rotation": 0, "shadow_blur": 6, "use_box": True,
    "use_stroke": True, "use_underline": True, "wrap_left": 0, "wrap_lines": 2, "wrap_right": 0,
}


def test_convert_legacy_style_maps_paint_and_reports_leftovers():
    from cutora.subtitle.legacy_import import convert_legacy_style, looks_legacy_style
    assert looks_legacy_style(LEGACY)
    new, notes = convert_legacy_style(LEGACY)
    fx = new["fx"]
    assert [c.lower() for c in fx["fill"]["colors"]] == ["#ffffff"]
    assert fx["stroke"]["width_em"] > 0
    assert fx["bg"]["type"] == "box" and fx["bg"]["color"].lower() == "#202040"
    assert fx["shadow"]["enabled"] is True and 0 < fx["shadow"]["strength"] < 1
    assert fx["follow_word"]["box"]["enabled"] is True
    assert fx["follow_word"]["box"]["color"].lower() == "#ff0000"
    assert [c.lower() for c in fx["follow_word"]["tint"]["colors"]] == ["#ffff00"]
    assert any("下划线" in n for n in notes)
    assert LEGACY.get("fx") is None            # 不改原字典
    from cutora.render.textfx.spec import from_style
    spec = from_style(new)                     # 新版能解析
    assert spec.follow_word.box.enabled


def test_extract_presets_recognises_three_legacy_shapes():
    from cutora.subtitle.legacy_import import extract_presets
    rows, dflt = extract_presets({"default": LEGACY, "presets": {"甲": LEGACY, "乙": LEGACY},
                                  "default_preset_name": "乙"})
    assert [r[0] for r in rows] == ["甲", "乙"] and dflt == "乙"
    rows, _ = extract_presets({"style": LEGACY, "version": 1}, fallback_name="default_style")
    assert rows[0][0] == "default_style"
    rows, _ = extract_presets(LEGACY, fallback_name="bare")
    assert rows[0][0] == "bare"
    assert extract_presets([1, 2, 3])[0] == []


def test_import_legacy_file_writes_user_packs(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q138"])
    from cutora.subtitle import stylepack as sp
    monkeypatch.setattr(sp, "user_dir", lambda: str(tmp_path / "packs"))
    src = tmp_path / "subtitle_style_presets.json"
    src.write_text(json.dumps({"default": LEGACY, "presets": {"经典蓝字": LEGACY, "小童风景": dict(LEGACY, use_box=False)}},
                              ensure_ascii=False), encoding="utf-8")
    from cutora.subtitle.legacy_import import import_legacy_file
    rep = import_legacy_file(str(src))
    assert rep["imported"] == ["经典蓝字", "小童风景"]
    assert "经典蓝字" in rep["notes"]
    names = sorted(os.listdir(tmp_path / "packs"))
    assert len(names) == 2
    pack = sp.find_user_pack_by_name("经典蓝字")
    assert pack and pack["style"]["fx"]["bg"]["type"] == "box"
    # 再导一次不覆盖，改名
    rep2 = import_legacy_file(str(src))
    assert rep2["imported"] == ["经典蓝字 (2)", "小童风景 (2)"]


def test_import_dialog_accepts_json():
    import inspect
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    src = inspect.getsource(SubtitleStylePanel._preset_import)
    assert "*.json" in src and "import_legacy_file" in src
