# -*- coding: utf-8 -*-
"""T11g-B: default pack entries, v1→fx.bg migrate, karaoke formula, setOpacity sites."""
from __future__ import annotations

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def test_paint_without_fx_logs_error(caplog):
    import logging
    from cutora.subtitle.stylepack import (
        ensure_style_has_fx, set_paint_task_hint,
    )
    set_paint_task_hint("clip.mp4")
    with caplog.at_level(logging.ERROR, logger="cutora.subtitle.stylepack"):
        out = ensure_style_has_fx(
            {"color_text": "#ffffff", "fontsize": 40},
            seg_idx=3, cue="Hello friends.",
        )
    set_paint_task_hint("")
    assert isinstance(out.get("fx"), dict)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "no fx" in text
    assert "clip.mp4" in text
    assert "3" in text


def test_draw_subtitle_uses_ensure_style_has_fx():
    src = _read("cutora/render/frame_renderer.py")
    body = src.split("def draw_subtitle", 1)[1].split("\n    def ", 1)[0]
    assert "ensure_style_has_fx" in body
    assert "migrate_v1_bg_to_fx" not in body


def test_v1_draw_subtitle_gone():
    src = _read("cutora/render/frame_renderer.py")
    assert "from cutora.render.textfx import draw as textfx_draw" in src
    # The old v1 paint body lived inside draw_subtitle; it is gone.
    body = src.split("def draw_subtitle", 1)[1].split("\n    def ", 1)[0]
    assert "textfx_draw" in body
    assert "anim_in_type" not in body
    assert "QPainter.setOpacity" not in body


def test_tint_run_ltr_shares_true_premul():
    src = _read("cutora/render/textfx/word_fx.py")
    blend = src.split("def _blend_premul_rgb", 1)[1].split("\ndef ", 1)[0]
    ltr = src.split("def tint_run_ltr", 1)[1].split("\ndef ", 1)[0]
    solid = src.split("def tint_run_solid", 1)[1].split("\ndef ", 1)[0]
    assert "_blend_premul_rgb(sl, r, g, b, amount)" in ltr
    assert "_blend_premul_rgb" in solid
    # Unique definition: channel * alpha, never * 255 a second time.
    assert "* 255" not in blend.replace("1.0 / 255.0", "")
    assert "a * 255" not in blend


def _setopacity_calls(src: str) -> int:
    return len(re.findall(r"\.setOpacity\s*\(", src))


def test_fx_setopacity_sites_converted():
    raster = _read("cutora/render/textfx/raster.py")
    assert "def mul_premul_alpha" in raster
    assert _setopacity_calls(raster) == 0
    warp = raster.split("def warp_layer_xf", 1)[1].split("\ndef ", 1)[0]
    assert "mul_premul_alpha" in warp
    paint = raster.split("def paint_stroke_fill", 1)[1].split("\ndef ", 1)[0]
    assert "mul_premul_alpha" in paint


def test_remaining_setopacity_not_in_fx_paint():
    """Census leftover: transitions / overlays / UI. Do not touch this batch."""
    leftover = {
        "cutora/ui/canvas.py": 3,
        "cutora/ui/timeline/header.py": 2,
        "cutora/ui/panels/style_gallery.py": 2,
        "cutora/render/render_worker.py": 2,
        "cutora/render/frame_renderer.py": 4,
        "cutora/render/anim_engine.py": 6,
    }
    for rel, nmin in leftover.items():
        n = _setopacity_calls(_read(rel))
        assert n >= nmin, "%s has %d setOpacity, expected ≥%d" % (rel, n, nmin)
    assert _setopacity_calls(_read("cutora/render/textfx/raster.py")) == 0
    assert _setopacity_calls(_read("cutora/render/textfx/word_fx.py")) == 0


def test_ensure_default_pack_glass_white_and_skips_fx():
    from cutora.subtitle.stylepack import ensure_default_pack, DEFAULT_PACK_ID

    bare = {"style": {"font_family": "Arimo", "fontsize": 60}, "segments": []}
    ensure_default_pack(bare)
    assert bare.get("stylepack_id") == DEFAULT_PACK_ID
    assert isinstance((bare.get("style") or {}).get("fx"), dict)

    fx = {"anim": {"loop": "karaoke"}, "fill": {"type": "solid", "colors": ["#F4EBD8"]}}
    kept = {"style": {"fx": fx}, "stylepack_id": "", "segments": []}
    ensure_default_pack(kept)
    assert kept["style"]["fx"] is fx
    assert kept.get("stylepack_id") in ("", None)

    tagged = {"style": {}, "stylepack_id": "amber-karaoke"}
    ensure_default_pack(tagged)
    assert tagged["stylepack_id"] == "amber-karaoke"
    assert tagged.get("style") == {}


def test_default_pack_entry_points():
    """ASR complete / drop-video / new-text-task / batch queue all call ensure."""
    main = _read("cutora/ui/main_window.py")
    cli = _read("cutora/batch/cli.py")
    assert main.count("ensure_default_pack") >= 3
    assert "ensure_default_pack(task)" in main  # ASR + overlay
    assert "ensure_default_pack(self.tasks[-1])" in main  # drop-video
    assert "ensure_default_pack(task)" in cli  # make_task_from_video
    # Import of .asproj migrates v1 box → fx.bg; does not overwrite existing fx.
    assert "migrate_v1_bg_to_fx" in main
    assert "migrate_v1_bg_to_fx" in _read("cutora/model/project_io.py")


def test_migrate_v1_bg_to_fx_maps_and_lists_unmapped():
    from cutora.subtitle.stylepack import (
        migrate_v1_bg_to_fx, v1_bg_unmapped_fields,
    )

    glass = {
        "use_box": True, "color_bg": "#ff0000",
        "fx": {"bg": {"type": "glass", "color": "#0B1A33"}},
    }
    migrate_v1_bg_to_fx(glass)
    assert glass["fx"]["bg"]["type"] == "glass"
    assert glass["fx"]["bg"]["color"] == "#0B1A33"

    none_fx = {
        "use_box": False, "bg_gradient_enabled": True,
        "bg_gradient_type": "fresnel",
        "bg_gradient_colors": "#0081bd,#007cb6",
        "fx": {"bg": {"type": "none", "color": "#550000"}},
    }
    migrate_v1_bg_to_fx(none_fx)
    assert none_fx["fx"]["bg"]["type"] == "none"

    # Any existing fx: do not touch fx.bg, regardless of type.
    for existing in (
        {"type": "none", "color": "#111111"},
        {"type": "box", "color": "#222222"},
        {"type": "glass", "color": "#333333"},
        {"color": "#444444"},
    ):
        before = {"use_box": True, "bg_gradient_enabled": True,
                  "color_bg": "#ff00ff", "fx": {"bg": dict(existing)}}
        snap = dict(before["fx"]["bg"])
        migrate_v1_bg_to_fx(before)
        assert before["fx"]["bg"] == snap, existing

    grad = {
        "use_box": True, "color_bg": "#e0c3fc", "opacity_bg": 200,
        "box_radius": 12, "box_padding_x": 10, "box_padding_y": 8,
        "bg_gradient_enabled": True, "bg_gradient_type": "linear_v",
        "bg_gradient_colors": "#111111,#EEEEEE", "bg_gradient_highlight": True,
        "box_blur": 12, "box_transition": True,
    }
    assert "box_blur" in v1_bg_unmapped_fields(grad)
    assert "box_transition" in v1_bg_unmapped_fields(grad)
    migrate_v1_bg_to_fx(grad)
    bg = grad["fx"]["bg"]
    assert bg["type"] == "box"
    assert bg["grad_type"] == "linear_v"
    assert len(bg["grad_colors"]) >= 2
    assert bg["grad_highlight"] is True
    assert grad["box_blur"] == 12  # not dropped

    black = {"use_box": True, "color_bg": "#000000"}
    migrate_v1_bg_to_fx(black)
    assert str(black["fx"]["bg"]["color"]).lower() != "#000000"


def test_dock_style_reparents_all_fx_widgets():
    """INSPECTOR_LAYOUT must name every registered fx widget.

    a6f1f29 (Q60(1)) bound fx.fonts.body to the 基本排版「字体」combo instead
    of a second row. mount_fonts_emphasis sets panel.fx_fonts_body = sub_font,
    and the layout lists sub_font, so dock reparent still reaches that widget.
    A name that is neither listed nor that alias is still a miss.
    """
    import inspect

    from cutora.ui.panels.fx_fields import mount_fonts_emphasis
    from cutora.ui.panels.fx_schema import FX_FIELD_WIDGETS
    from cutora.ui.panels.subtitle_layout import INSPECTOR_LAYOUT
    from cutora.ui.widgets.inspector import layout_attr_names
    names = set(layout_attr_names(INSPECTOR_LAYOUT))
    aliases = {"fx_fonts_body": "sub_font"}
    missing = [
        a for a in FX_FIELD_WIDGETS.values()
        if a not in names and aliases.get(a) not in names
    ]
    assert missing == [], missing
    assert "sub_font" in names
    src = inspect.getsource(mount_fonts_emphasis)
    assert "panel.fx_fonts_body = existing" in src
    assert 'getattr(panel, "sub_font", None)' in src


def test_retired_v1_goldens_not_in_all():
    src = _read("tools/golden_check.py")
    assert "RETIRED_V1_CASES" in src
    assert "g01_vertical_basic" in src
    assert "g09_linebreak_latin" in src
    notes = _read("tests/golden/RERECORD_NOTES.md")
    assert "退役原因：v1 draw_subtitle 路径删除" in notes
    assert "退役，不是失败" in notes
    assert "接替者：g11_vertical_basic_fx" in notes
