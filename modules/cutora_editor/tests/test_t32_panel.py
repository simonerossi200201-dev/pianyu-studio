# -*- coding: utf-8 -*-
"""T32 / Q26: panel writes fx + layout flats; no hidden leftovers; pack roundtrip."""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GONE = (
    "sub_rad", "sub_op", "sub_pad_x", "sub_pad_y", "sub_box_blur",
    "sub_box_trans", "sub_box_trans_c",
    "sub_bgg_en", "sub_bgg_type", "sub_bgg_colors", "sub_bgg_hl",
    "sub_bgg_en_host",
    "panel_bg",
)

# Layout / position flats collect() may keep. Everything else must be under fx.
PANEL_FLAT_WHITELIST = frozenset({
    "font_family", "font_style", "fontsize", "bold", "italic",
    "pos_x", "pos_y", "rotation",
    "wrap_width_percent", "wrap_lines", "line_spacing",
    "word_mode",
    "layout_engine", "safe_margin_pct", "max_words_per_line", "pause_break_sec",
    "geom_user_set", "anchor",
    "offset_x_pct", "offset_y_pct", "fontsize_pct",
    "pos_x_pct", "pos_y_pct",
    "layout_rules", "preset_name",
    "opacity_text_global",
})


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t32-panel"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    return SubtitleStylePanel()


def test_panel_writes_only_fx_keys(qapp):
    from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
    panel = _panel()
    style = {
        "fontsize": 72, "font_family": "Arimo",
        "bold": True, "italic": False,
        "pos_x": 360, "pos_y": 900, "rotation": 0,
        "wrap_width_percent": 86,
        "color_text": "#111111", "border_width": 4,
        "fx": {"bg": {"type": "glass", "alpha": 0.45}, "anim": {"loop": "none"}},
    }
    panel.load_data(style)
    panel.block_signals = False
    panel.current_data = style
    got = panel.collect()
    extra = set(got) - PANEL_FLAT_WHITELIST - {"fx"}
    assert extra == set(), extra
    dead = set(got) & DEPRECATED_UNPAINTED_KEYS
    assert dead == set(), dead
    for key in ("font_family", "fontsize", "pos_x", "pos_y", "rotation",
                "wrap_width_percent", "bold", "italic"):
        assert key in got, key
    assert isinstance(got.get("fx"), dict)
    panel.close()


def test_no_hidden_widgets_in_panel(qapp):
    panel = _panel()
    panel.load_data({"fontsize": 72, "fx": {"bg": {"type": "glass"}}})
    for name in GONE:
        widget = getattr(panel, name, None)
        assert widget is None, name
    src = open(
        os.path.join(REPO, "cutora", "ui", "panels", "subtitle_style.py"),
        encoding="utf-8").read()
    for name in ("self.sub_rad", "self.sub_box_blur", "self.sub_bgg_en",
                 "self.sub_pad_x", "self.sub_op =", "self.panel_bg"):
        assert name not in src, name
    hidden = _permanently_hidden_attrs(src)
    assert hidden == set(), sorted(hidden)
    panel.close()


def _permanently_hidden_attrs(src: str) -> set[str]:
    """Value-control attrs that are hide()/setVisible(False) with no show path.

    Hints (font_fallback_hint, safe_zone_hint) and pack-edit chrome can hide;
    dead leftover spins/combos/color panels cannot.
    """
    hide = set(re.findall(r"self\.(\w+)\.hide\(\)", src))
    vis_false = set(re.findall(r"self\.(\w+)\.setVisible\(\s*False\s*\)", src))
    show = set(re.findall(r"self\.(\w+)\.show\(\)", src))
    vis_other = set()
    for attr, arg in re.findall(r"self\.(\w+)\.setVisible\(([^)]*)\)", src):
        if arg.strip() != "False":
            vis_other.add(attr)
    dead = (hide | vis_false) - show - vis_other
    return {
        a for a in dead
        if a.startswith(("sub_", "fx_", "panel_", "chk_"))
    }


def test_dead_control_allowlist_is_empty():
    path = os.path.join(REPO, "tests", "test_t18_no_dead_controls.py")
    text = open(path, encoding="utf-8").read()
    assert re.search(
        r"^DEAD_CONTROL_ALLOWLIST:\s*list\[str\]\s*=\s*\[\]\s*$",
        text, re.M,
    ), "DEAD_CONTROL_ALLOWLIST must be an empty list"


def test_silent_failure_catalog_18_19_exist():
    reports = os.path.join(REPO, "docs", "plan", "reports")
    p18 = os.path.join(reports, "静默失败_18_引用不存在的控件帮手静默跳过.md")
    p19 = os.path.join(reports, "静默失败_19_面板写的键和渲染读的键各写各的.md")
    assert os.path.isfile(p18), p18
    assert os.path.isfile(p19), p19
    text19 = open(p19, encoding="utf-8").read()
    assert "adv_bar_" in text19
    assert "line_bar" in text19
    assert "DEPRECATED" in text19


def test_pack_roundtrip_panel(qapp):
    from dataclasses import asdict
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import (
        apply_pack_to_task, builtin_dir, list_pack_dirs, load_pack,
    )
    from cutora.ui.panels.fx_fields import get_widget_value, nested_get
    from cutora.ui.panels.fx_schema import json_to_asdict_path, schema_paths

    def _norm(value):
        if isinstance(value, tuple):
            return [_norm(x) for x in value]
        if isinstance(value, list):
            return [_norm(x) for x in value]
        if isinstance(value, str) and value.startswith("#"):
            return value.upper()
        if isinstance(value, float):
            return round(float(value), 3)
        return value

    def _close(a, b) -> bool:
        if isinstance(a, float) or isinstance(b, float):
            try:
                # 1-decimal spins (shadow.angle_deg) cannot hold atan2 defaults.
                return abs(float(a) - float(b)) < 0.1
            except (TypeError, ValueError):
                return False
        return _norm(a) == _norm(b)

    builtin = builtin_dir()
    packs = [p for p in list_pack_dirs() if os.path.normcase(p).startswith(
        os.path.normcase(builtin))]
    assert len(packs) == 14, [os.path.basename(p) for p in packs]
    panel = _panel()
    paths = list(schema_paths())
    for path in packs:
        pack = load_pack(path)
        task = {"style": {}, "segments": []}
        apply_pack_to_task(task, pack, apply_animation=True)
        panel.load_data(task["style"])
        spec = from_style(task["style"])
        payload = asdict(spec)
        for pth in paths:
            want = nested_get(payload, json_to_asdict_path(pth))
            got = get_widget_value(panel, pth)
            assert _close(got, want), "%s %s: got=%r want=%r" % (
                os.path.basename(path), pth, got, want)
    panel._fx_dirty = True
    panel.block_signals = False
    if panel.current_data is None:
        panel.current_data = task["style"]
    from cutora.ui.panels.fx_fields import apply_widget_sample
    apply_widget_sample(panel, "bg.alpha", 0.33)
    collected = panel.collect()
    got_a = nested_get(collected.get("fx") or {}, "bg.alpha")
    assert abs(float(got_a) - 0.33) < 0.03, got_a
    panel.close()


def test_load_data_writes_nothing(qapp):
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    panel = _panel()
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    task = {"style": {}, "segments": []}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    n = {"c": 0}
    orig = panel.on_change

    def wrapped(*a, **k):
        if not getattr(panel, "block_signals", False):
            n["c"] += 1
        return orig(*a, **k)

    panel.on_change = wrapped
    panel.load_data(style)
    assert n["c"] == 0, n["c"]
    panel.on_change = orig
    a = panel.collect()
    n["c"] = 0
    panel.on_change = wrapped
    panel.load_data(style)
    assert n["c"] == 0, n["c"]
    panel.on_change = orig
    b = panel.collect()
    assert a == b, (a, b)
    panel.close()
