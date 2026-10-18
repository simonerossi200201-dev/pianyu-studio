# -*- coding: utf-8 -*-
"""T04f-0: FxSpec, pure-black check, v2 pack apply. No Qt."""
from __future__ import annotations

import json
import os

from cutora.render.textfx.anim import rng_for, stable_seed
from cutora.render.textfx.spec import (
    collect_pure_black,
    from_style,
    is_pure_black,
    parse_color,
    rgba_tuple,
    task_has_glass_bg,
)
from cutora.subtitle.stylepack import (
    PACK_OFFSET_Y_PCT,
    apply_pack_to_task,
    restore_pack_geometry,
    user_has_custom_animation,
    gallery_card_size,
    gallery_column_count,
    gallery_column_width,
    gallery_content_band,
    gallery_crop_band,
    gallery_grid_width,
    gallery_hover_t,
    gallery_shared_band_h,
    load_pack,
    max_chars_from_width,
    resolve_style_geometry,
    sample_event,
    set_fontsize_px,
    set_position_px,
    subtitle_bbox,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(REPO, "resources", "stylepacks")


def test_stable_seed_is_process_stable():
    a = stable_seed("seg0", 1, 0)
    b = stable_seed("seg0", 1, 0)
    c = stable_seed("seg0", 1, 1)
    assert a == b
    assert a != c
    r1 = rng_for("seg0", 2, 0)
    r2 = rng_for("seg0", 2, 0)
    assert r1.random() == r2.random()


def test_cache_hash_is_blake2s():
    a = from_style({"fx": {"fill": {"type": "solid"}}})
    h = a.cache_hash()
    assert len(h) == 16
    int(h, 16)
    b = from_style({"fx": {"fill": {"type": "solid"}}})
    assert a.cache_hash() == b.cache_hash()
    c = from_style({"fx": {"fill": {"colors": ["#22D3EE"]}}})
    assert a.cache_hash() != c.cache_hash()


def test_parse_color_accepts_rgba():
    assert parse_color("#22D3EE", "#FFFFFF") == "#22D3EE"
    assert parse_color("#22D3EE80", "#FFFFFF") == "#22D3EE80"
    r, g, b, a = rgba_tuple("#FF000080")
    assert (r, g, b) == (255, 0, 0)
    assert abs(a - 128 / 255.0) < 0.01
    assert rgba_tuple("#FFFFFF")[3] == 1.0
    spec = from_style({"fx": {"fill": {"colors": ["#22D3EE99"]}}})
    assert spec.fill.colors[0] == "#22D3EE99"
    fade = spec.bg.fade
    assert fade.top == fade.bottom == fade.left == fade.right == 0.0
    spec2 = from_style({"fx": {"bg": {"fade": {"left": 0.3, "right": 9}}}})
    assert abs(spec2.bg.fade.left - 0.3) < 1e-6
    assert spec2.bg.fade.right == 1.0
    assert spec2.bg.fade.top_offset == 0.0


def test_is_pure_black_threshold():
    assert is_pure_black("#000000")
    assert is_pure_black("#101010")
    assert is_pure_black("#0F0000")
    assert not is_pure_black("#0B1A33")
    assert not is_pure_black("#141A26")
    assert not is_pure_black("#3A0A14")
    assert not is_pure_black("#FFFFFF")
    assert not is_pure_black("#F4F7FB")
    assert not is_pure_black("not-a-color")
    assert not is_pure_black(None)


def test_from_style_defaults_and_unknown_key(caplog):
    spec = from_style({"fx": {"nope": 1, "fill": {"type": "solid"}}})
    assert spec.fill.type == "solid"
    assert spec.fill.colors == ["#F4EBD8"]
    assert spec.bg.type == "none"
    assert spec.glow.sigma_em == 0
    assert "unknown fx key" in caplog.text


def test_from_style_clamps_and_bad_color():
    spec = from_style({
        "fx": {
            "stroke": {"width_em": 9, "color": "zzzz"},
            "glow": {"sigma_em": -1, "strength": 9},
            "tracking_em": 4,
        }
    })
    assert spec.stroke.width_em == 0.3
    assert spec.stroke.color == "#141A26"
    assert spec.glow.sigma_em == 0
    assert spec.glow.strength == 1.5
    assert spec.tracking_em == 0.5


def test_glass_white_pack_has_no_pure_black():
    path = os.path.join(PACK_DIR, "glass-white", "style.json")
    body = json.loads(open(path, encoding="utf-8").read())
    hits = collect_pure_black(body)
    assert hits == [], hits
    spec = from_style(body["style"])
    assert spec.bg.type == "glass"
    assert spec.fill.colors[0] == "#F4F7FB"
    assert spec.shadow.sigma_em > 0
    assert spec.stroke.width_em == 0
    assert not spec.has_pure_black


def test_v2_builtin_packs_reject_pure_black():
    for name in os.listdir(PACK_DIR):
        style_path = os.path.join(PACK_DIR, name, "style.json")
        if not os.path.isfile(style_path):
            continue
        body = json.loads(open(style_path, encoding="utf-8").read())
        if int(body.get("version") or 1) < 2:
            continue
        hits = collect_pure_black(body)
        assert hits == [], (name, hits)


def test_sample_event_builds_timed_words():
    ev = sample_event("Find its place", 0.0, 8.0)
    assert len(ev["words"]) == 3
    assert ev["words"][0]["word"] == "Find"
    assert ev["words"][-1]["word"] == "place"
    assert ev["words"][0]["start"] == 0.0
    assert abs(ev["words"][-1]["end"] - 8.0) < 1e-9


def test_gallery_card_aspect_and_crop_band():
    target = 1080 / 5.6
    x, y, bw, bh = gallery_crop_band(1080, 1920, bbox_cy=1114)
    assert x == 0 and abs(bw - 1080) < 1
    assert abs(bh - target) < 1
    assert abs((y + bh / 2.0) - 1114) < 1
    w, h = gallery_card_size(1080, 1920, bbox_cy=1114, col_w=180)
    assert w == 180
    assert abs(h / float(w) - (1.0 / 5.6)) < 0.02
    x2, y2, _, bh2 = gallery_crop_band(1080, 1920, bbox_cy=400)
    assert abs((y2 + bh2 / 2.0) - 400) < 1
    xl, yl, bwl, bhl = gallery_crop_band(
        1920, 1080, bbox_cy=626, bbox_cx=960)
    assert abs(bwl - 1920) < 1
    assert abs(bhl - 1920 / 5.6) < 2
    assert xl == 0
    wl, hl = gallery_card_size(
        1920, 1080, bbox_cx=960, bbox_cy=626, col_w=200)
    assert wl == 200
    assert abs(hl / float(wl) - (1.0 / 5.6)) < 0.02
    tall = gallery_crop_band(1080, 1920, bbox_cy=960, bbox_h=400)
    assert abs(tall[2] - 1080) < 1
    assert abs(tall[3] - 1080 / 5.6) < 1
    shared = gallery_shared_band_h(1080, 1920, [100, 400])
    assert abs(shared - 1080 / 5.6) < 1
    mild = gallery_shared_band_h(1080, 1920, [100, 140])
    assert abs(mild - 1080 / 5.6) < 1
    w2, h2 = gallery_card_size(1080, 1920, col_w=180, bbox_h=400)
    w3, h3 = gallery_card_size(1080, 1920, col_w=180, bbox_h=80)
    assert (w2, h2) == (w3, h3)
    assert abs(w2 / float(h2) - 5.6) < 0.05
    band, overflow = gallery_content_band(1080, 1920, bbox_cy=960, bbox_h=400)
    assert overflow is True
    assert abs(band[3] - 400) < 1
    band2, overflow2 = gallery_content_band(1080, 1920, bbox_cy=1114, bbox_h=80)
    assert overflow2 is False
    assert abs(band2[3] - 1080 / 5.6) < 1
    assert gallery_column_count(280) == 1
    assert gallery_column_count(319) == 1
    assert gallery_column_count(320) == 2
    assert gallery_column_width(288, 10) == 288
    assert gallery_column_width(400, 10) == 195
    assert gallery_grid_width(400) == 200
    assert gallery_grid_width(400) * 2 <= 400
    assert gallery_column_width(400, 10) > 80


def test_max_chars_from_width_uses_cap_as_ceiling():
    assert max_chars_from_width(1080, 20.0, 32) == 32
    assert max_chars_from_width(1080, 40.0, 32) == 22
    assert max_chars_from_width(1080, 40.0, 16) == 16
    assert max_chars_from_width(1080, 0.0, 32) == 32


def test_gallery_hover_t_loops_in_then_hold():
    assert gallery_hover_t(0.0, 0.0) == 0.4
    assert abs(gallery_hover_t(0.1, 0.35) - 0.1) < 1e-9
    assert abs(gallery_hover_t(0.35, 0.35) - 0.35) < 1e-9
    assert abs(gallery_hover_t(0.5, 0.35) - 0.35) < 1e-9
    assert abs(gallery_hover_t(0.95, 0.35) - 0.0) < 1e-9


def test_holy_glow_kept_in_engine_removed_from_ui_list():
    from cutora.render.anim_engine import ANIM_TYPE_LABELS, UI_ANIM_TYPE_LABELS
    assert any(n == "holy_glow" for _, n in ANIM_TYPE_LABELS)
    assert all(n != "holy_glow" for _, n in UI_ANIM_TYPE_LABELS)


def test_all_builtin_packs_reject_pure_black():
    for name in os.listdir(PACK_DIR):
        style_path = os.path.join(PACK_DIR, name, "style.json")
        if not os.path.isfile(style_path):
            continue
        body = json.loads(open(style_path, encoding="utf-8").read())
        hits = collect_pure_black(body)
        assert hits == [], (name, hits)


def test_task_has_glass_bg():
    assert task_has_glass_bg({"style": {"fx": {"bg": {"type": "glass"}}}})
    assert not task_has_glass_bg({"style": {"fx": {"bg": {"type": "box"}}}})
    assert not task_has_glass_bg({"style": {"color_text": "#fff"}})


def test_pop_word_to_paper_torn_drops_impact_pop():
    pop = load_pack(os.path.join(PACK_DIR, "pop-word"))
    paper = load_pack(os.path.join(PACK_DIR, "paper-torn"))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {
        "holy_glow_radius": 12,
        "fx": {"anim": {"in": "stomp"}},
    }}
    apply_pack_to_task(task, pop, apply_animation=True)
    assert task["style"]["fx"]["anim"]["in"] == "impact_pop"
    apply_pack_to_task(task, paper, apply_animation=True)
    assert task["style"]["fx"]["anim"]["in"] != "impact_pop"
    assert task["style"]["fx"]["anim"]["in"] == "none"
    assert task["style"].get("holy_glow_radius") == 6


def test_apply_animation_false_keeps_fx_anim_only():
    pop = load_pack(os.path.join(PACK_DIR, "pop-word"))
    paper = load_pack(os.path.join(PACK_DIR, "paper-torn"))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    apply_pack_to_task(task, pop, apply_animation=True)
    apply_pack_to_task(task, paper, apply_animation=False)
    assert task["style"]["fx"]["anim"]["in"] == "impact_pop"
    assert task["style"]["fx"]["bg"]["type"] == "texture"
    assert task["style"]["fx"]["bg"]["texture"] == "paper"


def test_geom_user_set_survives_pack_and_restore():
    pack = load_pack(os.path.join(PACK_DIR, "paper-torn"))
    other = load_pack(os.path.join(PACK_DIR, "glass-white"))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    pack_y = float(style["pos_y"])
    set_position_px(style, 400.0, 800.0, 1080, 1920)
    set_fontsize_px(style, 88, 1080, 1920)
    assert style.get("geom_user_set") is True
    apply_pack_to_task(task, other, apply_animation=True)
    assert abs(float(task["style"]["pos_y"]) - 800.0) < 1.0
    assert int(task["style"]["fontsize"]) == 88
    restore_pack_geometry(task, 1080, 1920)
    assert task["style"].get("geom_user_set") is False
    assert abs(float(task["style"]["pos_y"]) - pack_y) > 20 or task["stylepack_id"] == "glass-white"
    # restore uses last stylepack_id (glass-white), not paper-torn
    gw_task = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    apply_pack_to_task(gw_task, other, apply_animation=True)
    assert abs(float(task["style"]["pos_y"]) - float(gw_task["style"]["pos_y"])) < 1.0


def test_ten_pack_cycle_no_residue():
    ids = sorted(
        n for n in os.listdir(PACK_DIR)
        if os.path.isfile(os.path.join(PACK_DIR, n, "style.json"))
    )
    assert len(ids) >= 4
    first = load_pack(os.path.join(PACK_DIR, ids[0]))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {
        "holy_glow_radius": 99, "anim_in_type": "stomp",
    }}
    apply_pack_to_task(task, first, apply_animation=True)
    snap = json.dumps(task["style"], sort_keys=True, default=str)
    for i in range(10):
        pack = load_pack(os.path.join(PACK_DIR, ids[i % len(ids)]))
        apply_pack_to_task(task, pack, apply_animation=True)
    apply_pack_to_task(task, first, apply_animation=True)
    again = json.dumps(task["style"], sort_keys=True, default=str)
    assert snap == again


def test_user_has_custom_animation_reads_v2_fx():
    assert user_has_custom_animation({"fx": {"anim": {"in": "impact_pop"}}}) is True
    assert user_has_custom_animation({"fx": {"anim": {"in": "none", "loop": "none"}}}) is False


def test_apply_v2_writes_fx_block_and_keeps_anim():
    pack = load_pack(os.path.join(PACK_DIR, "glass-white"))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {
        "fx": {"anim": {"in": "fade", "seed": 7}},
    }}
    apply_pack_to_task(task, pack, apply_animation=False)
    assert isinstance(task["style"].get("fx"), dict)
    assert task["style"]["fx"]["bg"]["type"] == "glass"
    assert task["style"]["fx"]["anim"]["in"] == "fade"
    apply_pack_to_task(task, pack, apply_animation=True)
    assert task["style"]["fx"]["anim"]["in"] == "none"


def test_apply_v1_pack_clears_fx():
    v2 = load_pack(os.path.join(PACK_DIR, "glass-white"))
    v1 = {
        "id": "legacy-v1",
        "version": 1,
        "style": {"font_family": "Arimo", "color_text": "#F4F7FB", "fontsize": 48},
        "layout_rules": {"max_lines": 2},
        "animation": {},
        "keyword_rules": [],
    }
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    apply_pack_to_task(task, v2)
    assert "fx" in task["style"]
    apply_pack_to_task(task, v1)
    assert "fx" not in task["style"]


def test_glass_white_bbox_on_three_canvases():
    pack = load_pack(os.path.join(PACK_DIR, "glass-white"))
    rel = []
    for w, h in ((1080, 1920), (1920, 1080), (1080, 1080)):
        task = {"timeline": {"width": w, "height": h}, "style": {}}
        apply_pack_to_task(task, pack, apply_animation=True)
        x, y, bw, bh = subtitle_bbox(task["style"], w, h, ["Find its place"])
        assert x >= -0.5, (w, h, x, y, bw, bh)
        assert y >= -0.5, (w, h, x, y, bw, bh)
        assert x + bw <= w + 0.5, (w, h, x, y, bw, bh)
        assert y + bh <= h + 0.5, (w, h, x, y, bw, bh)
        cy = (y + bh / 2.0) / float(h)
        rel.append(cy)
        assert 0.56 <= cy <= 0.60, (w, h, cy)
    assert max(rel) - min(rel) < 0.04, rel


def test_builtin_packs_center_and_doubled_fontsize():
    for name in os.listdir(PACK_DIR):
        style_path = os.path.join(PACK_DIR, name, "style.json")
        if not os.path.isfile(style_path):
            continue
        pack = load_pack(os.path.join(PACK_DIR, name))
        body = json.loads(open(style_path, encoding="utf-8").read())
        pct = float((body.get("style") or {}).get("fontsize_pct") or 0)
        assert pct >= 0.05, (name, pct)
        off = (body.get("style") or {}).get("offset_y_pct")
        assert off is not None, name
        for w, h in ((1080, 1920), (1920, 1080), (1080, 1080)):
            task = {"timeline": {"width": w, "height": h}, "style": {}}
            apply_pack_to_task(task, pack, apply_animation=True)
            x, y, bw, bh = subtitle_bbox(
                task["style"], w, h, ["Find its place"])
            cy = (y + bh / 2.0) / float(h)
            assert 0.56 <= cy <= 0.60, (name, w, h, cy)


def test_fontsize_pct_uses_min_canvas_side():
    """Portrait and square stay on 1080; landscape must not use 1920."""
    style = {
        "fontsize_pct": 0.062962,
        "anchor": "bottom-center",
        "offset_y_pct": -0.42,
    }
    portrait = resolve_style_geometry(style, 1080, 1920)["fontsize"]
    landscape = resolve_style_geometry(style, 1920, 1080)["fontsize"]
    square = resolve_style_geometry(style, 1080, 1080)["fontsize"]
    assert portrait == square
    assert abs(portrait - landscape) <= 1
    assert abs(portrait - round(0.062962 * 1080)) <= 1
    assert landscape < round(0.062962 * 1920) * 0.7


def test_draw_subtitle_has_single_fx_branch():
    src = open(
        os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
        encoding="utf-8",
    ).read()
    assert "ensure_style_has_fx" in src
    assert "from cutora.render.textfx import draw as textfx_draw" in src
    layers = open(
        os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
        encoding="utf-8",
    ).read()
    assert "backdrop=None" in layers
    assert "backdrop=backdrop" in layers


def test_draw_layers_and_export_accept_backdrop():
    fr = open(os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
              encoding="utf-8").read()
    er = open(os.path.join(REPO, "cutora", "render", "export_renderer.py"),
              encoding="utf-8").read()
    cv = open(os.path.join(REPO, "cutora", "ui", "canvas.py"), encoding="utf-8").read()
    rw = open(os.path.join(REPO, "cutora", "render", "render_worker.py"),
              encoding="utf-8").read()
    assert "backdrop=None" in fr
    assert "backdrop=None" in er
    assert "_preview_backdrop" in cv
    assert "frame_has_glass_bg" in rw
    assert "backdrop = video" in rw


def test_geom_setters_roundtrip():
    """200 random (px, py, fs) × 3 canvases; set then resolve ≤ 0.5 px."""
    import random
    rng = random.Random(20260911)
    canvases = ((1080, 1920), (1920, 1080), (1080, 1080))
    n = 0
    for w, h in canvases:
        for _ in range(200):
            fs = rng.randint(12, 72)
            px = rng.uniform(200.0, w - 200.0)
            py = rng.uniform(220.0, h - 220.0)
            style = {
                "anchor": "bottom-center",
                "offset_x_pct": 0.0,
                "offset_y_pct": -0.42,
                "fontsize_pct": 0.06,
            }
            set_fontsize_px(style, fs, w, h)
            set_position_px(style, px, py, w, h)
            got = resolve_style_geometry(style, w, h)
            assert abs(float(got["pos_x"]) - px) <= 0.5, (w, h, px, got["pos_x"])
            assert abs(float(got["pos_y"]) - py) <= 0.5, (w, h, py, got["pos_y"])
            assert abs(int(got["fontsize"]) - fs) <= 0.5, (w, h, fs, got["fontsize"])
            n += 1
    assert n == 600


def test_set_position_survives_pack_relative_fields():
    pack = load_pack(os.path.join(PACK_DIR, "glass-white"))
    task = {"timeline": {"width": 1920, "height": 1080}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    assert style.get("anchor")
    assert style.get("fontsize_pct") is not None
    set_position_px(style, 900.0, 700.0, 1920, 1080)
    set_fontsize_px(style, 48, 1920, 1080)
    got = resolve_style_geometry(style, 1920, 1080)
    assert abs(float(got["pos_x"]) - 900.0) <= 0.5
    assert abs(float(got["pos_y"]) - 700.0) <= 0.5
    assert int(got["fontsize"]) == 48


def _fresh_empty_dict(func, name: str) -> bool:
    """True when ``name`` is only ever assigned ``{}`` inside ``func``."""
    import ast
    params = {a.arg for a in (
        *func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs)}
    if func.args.vararg is not None:
        params.add(func.args.vararg.arg)
    if func.args.kwarg is not None:
        params.add(func.args.kwarg.arg)
    if name in params:
        return False
    found = False
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name:
                found = True
                val = node.value
                if not (isinstance(val, ast.Dict) and len(val.keys) == 0):
                    return False
    return found


def test_ui_does_not_assign_geom_keys():
    """UI must not subscript-assign style geometry keys.

    ``style["fontsize"] =`` / ``style["pos_x"] =`` skips ``set_fontsize_px`` /
    ``set_position_px``, so ``fontsize_pct`` and ``offset_*_pct`` drift.
    A fresh ``{}`` in the same function is a rich-text run override
    (``text_rich._overrides_from_format``), not the style dict. Only
    ``fontsize`` is allowed on that fresh dict; ``pos_x`` / ``pos_y`` never are.
    """
    import ast
    banned = {"pos_x", "pos_y", "fontsize"}

    class _Scan(ast.NodeVisitor):
        def __init__(self):
            self.funcs = []
            self.hits = []

        def visit_FunctionDef(self, node):
            self.funcs.append(node)
            self.generic_visit(node)
            self.funcs.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def _targets(self, node):
            if isinstance(node, ast.Assign):
                return list(node.targets)
            if isinstance(node, ast.AnnAssign) and node.target is not None:
                return [node.target]
            if isinstance(node, ast.AugAssign):
                return [node.target]
            return []

        def visit_Assign(self, node):
            self._check(node)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self._check(node)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self._check(node)
            self.generic_visit(node)

        def _check(self, node):
            for tgt in self._targets(node):
                if not isinstance(tgt, ast.Subscript):
                    continue
                sl = tgt.slice
                if not (isinstance(sl, ast.Constant) and sl.value in banned):
                    continue
                base = tgt.value
                if (
                    sl.value == "fontsize"
                    and isinstance(base, ast.Name)
                    and self.funcs
                    and _fresh_empty_dict(self.funcs[-1], base.id)
                ):
                    continue
                self.hits.append(node.lineno)

    hits = []
    ui_root = os.path.join(REPO, "cutora", "ui")
    for dirpath, _, files in os.walk(ui_root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            scan = _Scan()
            scan.visit(tree)
            rel = os.path.relpath(path, REPO)
            hits.extend("%s:%s" % (rel, line) for line in scan.hits)
    assert hits == [], hits


def test_preview_coalesces_slider_repaint():
    src = open(os.path.join(REPO, "cutora", "ui", "main_window.py"),
               encoding="utf-8").read()
    assert "setInterval(50)" in src
    assert "_flush_coalesced_preview" in src
    assert "_dragging_item" in src


def test_v2_draw_passes_seg_idx():
    fr = open(os.path.join(REPO, "cutora", "render", "frame_renderer.py"),
              encoding="utf-8").read()
    assert "seg_idx=seg_idx" in fr
    init = open(os.path.join(REPO, "cutora", "render", "textfx", "__init__.py"),
                encoding="utf-8").read()
    assert "seg_idx=seg_idx" in init
    pipe = open(os.path.join(REPO, "cutora", "render", "textfx", "pipeline.py"),
                encoding="utf-8").read()
    assert "_layout_fingerprint" in pipe
    assert "_fill_word_hits" in pipe


def test_glass_white_uses_bundled_font():
    pack = load_pack(os.path.join(PACK_DIR, "glass-white"))
    assert pack["style"]["font_family"] == "Arimo"
    assert pack["meta"]["author"] == "CUTORA"
    assert pack["version"] == 2
    assert pack["meta"].get("license") == "OFL-1.1"
    assert pack.get("preview") and os.path.isfile(pack["preview"])
