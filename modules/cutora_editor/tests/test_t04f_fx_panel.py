# -*- coding: utf-8 -*-
"""T04f-1d-2: fx schema ↔ right-panel widgets, including word_mode.

Qt widget I/O runs in a child process so the pytest parent never imports PySide6.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from cutora.subtitle.layout import RELAYOUT_PARAM_KEYS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def test_fx_registry_covers_schema():
    from cutora.ui.panels.fx_schema import (
        FX_FIELD_WIDGETS,
        T04F2_DISABLED_PATHS,
        registry_expected,
        schema_paths,
    )
    expected = registry_expected()
    assert set(FX_FIELD_WIDGETS) == expected, (
        "missing=%s extra=%s" % (
            sorted(expected - set(FX_FIELD_WIDGETS)),
            sorted(set(FX_FIELD_WIDGETS) - expected),
        )
    )
    assert "word_mode" in FX_FIELD_WIDGETS
    assert schema_paths().isdisjoint({"has_pure_black"})
    for path in T04F2_DISABLED_PATHS:
        assert path in FX_FIELD_WIDGETS, path


def test_word_mode_triggers_relayout():
    assert "word_mode" in RELAYOUT_PARAM_KEYS


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_fx_panel_widgets_write_and_roundtrip():
    env = _child_env()
    cmd = [PY, os.path.join(REPO, "tests", "test_t04f_fx_panel.py")]
    r = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    if r.returncode != 0 or "T04F_1D2_PANEL_OK" not in (r.stdout or ""):
        pytest.fail("rc=%s\n%s" % (r.returncode, out[-6000:]))


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
            return abs(float(a) - float(b)) < 0.03
        except (TypeError, ValueError):
            return False
    return _norm(a) == _norm(b)


SAMPLES = {
    "fill.type": "gradient",
    "fill.colors": ["#22D3EE", "#7C3AED"],
    "fill.angle": 180.0,
    "fill.metal": "chrome",
    "stroke.width_em": 0.12,
    "stroke.color": "#3A0A14",
    "stroke.join": "miter",
    "glow.sigma_em": 0.3,
    "glow.spread_em": 0.1,
    "glow.color": "#FDE68A",
    "glow.strength": 1.2,
    "glow.mode": "neon",
    "shadow.light": "point",
    "shadow.angle_deg": 210.0,
    "shadow.distance_em": 0.8,
    "shadow.size_em": 0.2,
    "shadow.falloff": 0.4,
    "shadow.color": "#062E2E",
    "shadow.strength": 0.7,
    "extrude.depth_em": 0.2,
    "extrude.angle": 90.0,
    "extrude.color": "#3A0A14",
    "bg.type": "glass",
    "bg.color": "#0B1A33",
    "bg.alpha": 0.4,
    "bg.radius_em": 0.6,
    "bg.pad_em": [0.8, 0.3],
    "bg.size_mode": "fixed",
    "bg.width_px": 640,
    "bg.height_px": 180,
    "bg.offset_x": 12,
    "bg.offset_y": -8,
    "bg.hug_pad_em": 0.35,
    "bg.hug_join_em": 0.12,
    "bg.glass_sigma_em": 1.1,
    "bg.glass_border": False,
    "bg.glass_thickness_em": 0.2,
    "bg.glass_refraction": 0.4,
    "bg.glass_bevel": 0.8,
    "bg.glass_sheen": 0.5,
    "bg.glass_light_deg": 180.0,
    "bg.grad_type": "linear_h",
    "bg.grad_colors": ["#E0C3FC", "#C084FC"],
    "bg.grad_highlight": True,
    "bg.blend": "multiply",
    "bg.texture": "linen",
    "bg.texture_tint": "#F1E9D8",
    "bg.align": "alternate",
    "bg.line_gap_em": 0.4,
    "bg.fade.top": 0.2,
    "bg.fade.bottom": 0.3,
    "bg.fade.left": 0.1,
    "bg.fade.right": 0.4,
    "bg.fade.top_offset": 0.1,
    "bg.fade.bottom_offset": -0.2,
    "bg.fade.left_offset": 0.0,
    "bg.fade.right_offset": 0.25,
    "fonts.body": "Arimo",
    "fonts.emphasis": "Arimo",
    "emphasis.auto": ["numbers", "caps"],
    "emphasis.words": ["CUTORA"],
    "emphasis.type": "gradient",
    "emphasis.colors": ["#7C3AED", "#22D3EE"],
    "emphasis.angle": 0.0,
    "emphasis.palette": ["#7C3AED", "#22D3EE"],
    "emphasis.scale": 1.5,
    "emphasis.font": "body",
    "emphasis.anim": "pop",
    "emphasis.max_per_cue": 2,
    "anim.in": "fade",
    "anim.loop": "float_loop",
    "anim.out": "scale_in",
    "anim.unit": "word",
    "anim.stagger_s": 0.12,
    "anim.in_s": 0.5,
    "anim.out_s": 0.4,
    "anim.easing": "bounce",
    "anim.seed": 7,
    "scatter.enabled": True,
    "scatter.size_jitter": 0.4,
    "scatter.pos_jitter_em": [2.0, 1.0],
    "scatter.rot_jitter_deg": 10.0,
    "tracking_em": 0.1,
    "letter_case": "upper",
    "line_bar.color": "#F2C14E80",
    "line_bar.height_em": 0.3,
    "line_bar.pad_em": 0.12,
    "line_bar.offset_y_em": -0.04,
    "line_bar.radius_em": 0.2,
    "line_bar.glow_em": 0.1,
    "line_bar.style": "dashed",
    "line_bar.anim": "draw",
    "word_mode": "single",
}


def run_panel_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    app = QApplication.instance() or QApplication(["cutora-t04f-1d2"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    import cutora.ui.panels.property as _property_bind
    _ = _property_bind
    from cutora.ui.panels.fx_fields import (
        apply_widget_sample,
        get_widget_value,
        nested_get,
    )
    from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS, T04F2_DISABLED_PATHS
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    from dataclasses import asdict
    from cutora.ui.panels.fx_schema import json_to_asdict_path, schema_paths

    panel = SubtitleStylePanel()
    missing = [a for a in FX_FIELD_WIDGETS.values() if getattr(panel, a, None) is None]
    assert missing == [], missing
    assert panel.fx_bg_fade_top.maximum() == 100
    assert panel.fx_bg_fade_left_offset.minimum() == -100
    assert panel.sub_font_style.count() >= 2
    follow = panel.sub_font_style.itemData(0)
    assert follow in ("", None)
    names = [panel.sub_font_style.itemData(i) for i in range(1, panel.sub_font_style.count())]
    assert all(isinstance(n, str) and n for n in names)
    panel.sub_font_style.setCurrentIndex(1)
    assert not panel.sub_bold.isEnabled()
    assert not panel.sub_italic.isEnabled()
    panel.sub_font_style.setCurrentIndex(0)
    assert panel.sub_bold.isEnabled()
    assert panel.sub_italic.isEnabled()
    from cutora.ui.panels.fonts import style_weight_args
    bold, italic, name = style_weight_args(
        {"bold": True, "italic": True, "font_style": "Bold Italic"})
    assert name == "Bold Italic"
    assert bold is False and italic is False
    bold, italic, name = style_weight_args({"bold": True, "italic": True})
    assert name is None and bold is True and italic is True
    tip = panel.sub_word_mode.toolTip()
    assert "逐词出现" in tip and "单字蹦" in tip, tip
    assert T04F2_DISABLED_PATHS == frozenset()
    for _sw in (
        getattr(panel, "sub_fill_en", None),
        getattr(panel, "sub_extrude_en", None),
        getattr(panel, "sub_texture_en", None),
        getattr(panel, "sub_fonts_en", None),
    ):
        if _sw is not None:
            _sw.setChecked(True)
    assert panel.fx_fill_metal.isEnabled()
    assert panel.fx_extrude_depth_em.isEnabled()
    assert panel.fx_bg_texture.isEnabled()
    assert panel.fx_fonts_emphasis.isEnabled()
    neon_idx = panel.fx_glow_mode.findData("neon")
    assert neon_idx >= 0
    assert panel.fx_glow_mode.model().item(neon_idx).isEnabled()

    style = {"font_family": "Arimo", "fontsize": 48, "fx": {}}
    panel.load_data(style)
    panel.block_signals = False
    panel.sub_strk.setChecked(True)
    panel.sub_glow_en.setChecked(True)
    panel.sub_shadow_en.setChecked(True)
    panel.sub_extrude_en.setChecked(True)
    panel.sub_texture_en.setChecked(True)
    panel.sub_fonts_en.setChecked(True)
    panel.sub_emphasis_en.setChecked(True)
    panel.fx_scatter_enabled.setChecked(True)
    panel._fx_dirty = True
    for path, sample in SAMPLES.items():
        apply_widget_sample(panel, path, sample)
        got = nested_get(panel.current_data.get("fx") or {}, path)
        if path == "word_mode":
            got = panel.current_data.get("word_mode")
        assert _close(got, sample), "%s: got=%r want=%r" % (path, got, sample)
        assert _close(get_widget_value(panel, path), sample), path

    pack_dir = os.path.join(builtin_dir(), "glass-white")
    pack = load_pack(pack_dir)
    task = {"style": {}, "segments": []}
    apply_pack_to_task(task, pack, apply_animation=True)
    panel.load_data(task["style"])
    spec = from_style(task["style"])
    payload = asdict(spec)
    for path in schema_paths():
        want = nested_get(payload, json_to_asdict_path(path))
        got = get_widget_value(panel, path)
        assert _close(got, want), "pack %s: got=%r want=%r" % (path, got, want)
    v1 = {"font_family": "Arimo", "fontsize": 40}
    panel2 = SubtitleStylePanel()
    panel2.load_data(v1)
    panel2.on_change()
    assert "fx" not in (panel2.current_data or {}), panel2.current_data
    panel2.sub_size.setValue(44)
    panel2.on_change()
    assert "fx" not in (panel2.current_data or {}), panel2.current_data
    assert panel2.fx_bg_glass_sigma_em.isHidden()
    idx_glass = panel2.fx_bg_type.findData("glass")
    assert idx_glass >= 0
    panel2.fx_bg_type.setCurrentIndex(idx_glass)
    assert not panel2.fx_bg_glass_sigma_em.isHidden()
    panel3 = SubtitleStylePanel()
    pack_style = task["style"]
    panel3.load_data(pack_style)
    assert panel3.fx_bg_type.currentData() == "glass"
    return 0


if __name__ == "__main__":
    try:
        rc = run_panel_suite()
        print("T04F_1D2_PANEL_OK", flush=True)
        raise SystemExit(rc)
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
