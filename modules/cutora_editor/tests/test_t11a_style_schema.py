# -*- coding: utf-8 -*-
from cutora.subtitle.style_schema import (
    CANONICAL_STYLE_KEYS,
    DEAD_STYLE_KEYS,
    DEPRECATED_UNPAINTED_KEYS,
    STYLE_KEY_ALIASES,
    aliases_acyclic,
    migrate_subtitle_style,
    migrate_task_styles,
)
def test_canonical_unique():
    assert len(CANONICAL_STYLE_KEYS) == len(set(CANONICAL_STYLE_KEYS))
    assert "shadow_blur" not in CANONICAL_STYLE_KEYS
    assert "shadow_blur" in DEPRECATED_UNPAINTED_KEYS
    for dead in DEAD_STYLE_KEYS:
        assert dead not in CANONICAL_STYLE_KEYS


def test_panel_style_keys_bind_canonical():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "cutora" / "ui" / "panels" / "subtitle_style.py"
    text = src.read_text(encoding="utf-8")
    assert "STYLE_KEYS = list(CANONICAL_STYLE_KEYS)" in text


def test_alias_table_acyclic():
    assert aliases_acyclic()
    for src, dst in STYLE_KEY_ALIASES.items():
        assert dst in CANONICAL_STYLE_KEYS or dst in DEPRECATED_UNPAINTED_KEYS
        assert src not in CANONICAL_STYLE_KEYS
        assert src != dst
        assert src not in STYLE_KEY_ALIASES.values()


def test_migrate_subtitle_aliases():
    style = {"color": "#ABCDEF", "fontsize": 40, "stroke": 1, "shadow_offset": 3}
    migrate_subtitle_style(style)
    assert "color" not in style
    assert "color_text" not in style
    assert "stroke" not in style
    assert "shadow_offset" not in style
    assert style["fontsize"] == 40


def test_migrate_does_not_overwrite_canonical():
    style = {"color": "#111111", "color_text": "#222222", "fontsize": 40}
    migrate_subtitle_style(style)
    assert "color" not in style
    assert "color_text" not in style
    assert style["fontsize"] == 40


def test_migrate_pops_deprecated_unpainted():
    style = {k: 1 for k in DEPRECATED_UNPAINTED_KEYS}
    style["fontsize"] = 40
    style["font_family"] = "Arimo"
    style["fx"] = {"fill": {"type": "solid"}}
    migrate_subtitle_style(style)
    hits = sorted(k for k in style if k in DEPRECATED_UNPAINTED_KEYS)
    assert hits == []
    assert style["fontsize"] == 40
    assert style["font_family"] == "Arimo"
    assert style["fx"]["fill"]["type"] == "solid"


def test_alias_apply_sites_skip_overlays():
    """STYLE_KEY_ALIASES is only folded by migrate_subtitle_style.

    Callers: migrate_task_styles (task.style + roles.*.style),
    apply_pack_to_task (rebuilt subtitle style), copy_style_full
    (subtitle copy). Subtitle presets are style packs and do not call this.
    None of those walk text_overlays.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "cutora"
    schema = (root / "subtitle" / "style_schema.py").read_text(encoding="utf-8")
    fn = schema.split("def migrate_task_styles", 1)[1].split("\ndef ", 1)[0]
    assert "text_overlays" not in fn
    assert "migrate_subtitle_style(style)" in fn
    assert "migrate_subtitle_style(info[\"style\"])" in fn
    hits = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "migrate_subtitle_style(" not in text:
            continue
        hits.append(path.relative_to(root).as_posix())
    assert hits == [
        "subtitle/style_schema.py",
        "subtitle/stylepack.py",
        "ui/panels/subtitle_style.py",
    ]
    pio = (root / "model" / "project_io.py").read_text(encoding="utf-8")
    assert "migrate_task_styles(task)" in pio
    assert "migrate_subtitle_style(" not in pio


def test_migrate_task_skips_overlays():
    task = {
        "style": {"color": "#FFFFFF", "bg_enabled": True, "bg_padding": 9},
        "text_overlays": [{"color": "#ABCABC", "bg_enabled": False}],
        "subtitle_roles": {"default": {"style": {"stroke_width": 4}}},
    }
    migrate_task_styles(task)
    assert "color_text" not in task["style"]
    assert "color" not in task["style"]
    assert task["style"]["use_box"] is True
    assert task["style"]["box_padding_x"] == 9
    assert task["style"]["box_padding_y"] == 9
    assert task["text_overlays"][0]["color"] == "#ABCABC"
    assert task["text_overlays"][0]["bg_enabled"] is False
    assert "border_width" not in task["subtitle_roles"]["default"]["style"]
    assert "stroke_width" not in task["subtitle_roles"]["default"]["style"]
