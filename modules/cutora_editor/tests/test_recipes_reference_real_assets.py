# -*- coding: utf-8 -*-
"""Built-in recipes must name presets/style packs that actually exist."""
from __future__ import annotations

import os

from cutora.batch.recipe import builtin_recipes, load_recipe
from cutora.subtitle import presets as P

PRESET_FIELDS = {
    "style.apply_subtitle_preset": ("subtitle", "name"),
    "style.apply_text_preset": ("text", "name"),
    "style.apply_keyword_preset": ("keyword", "name"),
    "subtitle.apply_preset": ("subtitle", "name"),
}


def _catalog() -> dict[str, set]:
    from cutora.subtitle.stylepack import list_style_entries
    return {
        "subtitle": {row["name"] for row in list_style_entries()},
        "text": set((P.load_text_presets().get("presets") or {}).keys()),
        "keyword": set((P.load_keyword_presets().get("presets") or {}).keys()),
    }


def test_builtin_recipes_reference_real_presets():
    cat = _catalog()
    missing = []
    for path in builtin_recipes():
        data = load_recipe(path)
        for i, step in enumerate(data.get("steps") or [], 1):
            cmd = step.get("command")
            spec = PRESET_FIELDS.get(cmd)
            if not spec:
                continue
            kind, field = spec
            name = (step.get("params") or {}).get(field)
            if not name or str(name).startswith("{"):
                continue
            if str(name) not in cat[kind]:
                missing.append("%s step %d %s %r" % (
                    os.path.basename(path), i, cmd, name))
    assert missing == [], "unresolved recipe assets:\n" + "\n".join(missing)


def test_bundled_default_subtitle_preset_exists():
    """The old bundled preset name 默认 is a style pack now (glass-white)."""
    from cutora.subtitle.stylepack import apply_pack_to_task, find_pack_by_name
    pack = find_pack_by_name("毛玻璃·白")
    assert pack is not None and pack.get("id") == "glass-white"
    task = {"style": {}, "dims": (1080, 1920), "segments": []}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task.get("style")
    assert isinstance(style, dict) and style.get("font_family")
