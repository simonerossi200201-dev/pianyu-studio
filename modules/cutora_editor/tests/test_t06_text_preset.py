# -*- coding: utf-8 -*-
"""T06-1: relative text presets, new-layer apply, idempotent auto-credit."""
from __future__ import annotations

import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def preset_store(tmp_path, monkeypatch):
    path = tmp_path / "text_presets.json"
    legacy = tmp_path / "text_style_presets.json"
    monkeypatch.setattr(
        "cutora.subtitle.text_preset._user_path", lambda: str(path))
    monkeypatch.setattr(
        "cutora.subtitle.text_preset._legacy_path", lambda: str(legacy))
    from cutora.subtitle.text_preset import reset_runtime_state_for_tests
    reset_runtime_state_for_tests()
    return path


def _task(w, h, duration=8.0):
    return {
        "dims": [w, h],
        "duration": duration,
        "text_overlays": [],
        "segments": [{"start": 0, "end": duration, "text": "hi"}],
    }


def test_layout_uses_short_side(preset_store):
    from cutora.subtitle.text_preset import layout_to_box, short_side
    lay = {"anchor": "bottom_right", "margin_x": 0.2, "margin_y": 0.4, "size_pct": 0.035}
    x1, y1, w1, h1, fs1 = layout_to_box(lay, 1080, 1920, "Hello")
    x2, y2, w2, h2, fs2 = layout_to_box(lay, 1920, 1080, "Hello")
    s1, s2 = short_side(1080, 1920), short_side(1920, 1080)
    assert s1 == s2 == 1080
    assert abs(fs1 - fs2) < 0.01
    # Distance from right/bottom edge, divided by short side, matches margin.
    assert abs((1080 - x1 - w1) / s1 - 0.2) < 1e-6
    assert abs((1920 - y1 - h1) / s1 - 0.4) < 1e-6
    assert abs((1920 - x2 - w2) / s2 - 0.2) < 1e-6
    assert abs((1080 - y2 - h2) / s2 - 0.4) < 1e-6


def test_apply_creates_new_layer_each_time(preset_store):
    from cutora.subtitle.text_preset import apply_to_task, get_preset
    preset = get_preset("credit.default")
    assert preset is not None
    task = _task(1080, 1920)
    apply_to_task(task, preset, idempotent=False)
    apply_to_task(task, preset, idempotent=False)
    ovs = task["text_overlays"]
    assert len(ovs) == 2
    assert all(ov.get("source_preset_id") == "credit.default" for ov in ovs)


def test_auto_credit_idempotent_three_runs(preset_store):
    from cutora.subtitle.text_preset import apply_to_task, get_preset
    preset = get_preset("credit.default")
    task = _task(1080, 1920)
    apply_to_task(task, preset, idempotent=True)
    snap = json.dumps(task["text_overlays"], sort_keys=True)
    apply_to_task(task, preset, idempotent=True)
    apply_to_task(task, preset, idempotent=True)
    credits = [ov for ov in task["text_overlays"]
               if ov.get("source_preset_id") == "credit.default"]
    assert len(credits) == 1
    assert json.dumps(task["text_overlays"], sort_keys=True) == snap


def test_relative_coords_match_on_both_canvases(preset_store):
    from cutora.subtitle.text_preset import apply_to_task, get_preset, short_side
    preset = get_preset("credit.default")
    a = _task(1080, 1920)
    b = _task(1920, 1080)
    apply_to_task(a, preset, idempotent=True)
    apply_to_task(b, preset, idempotent=True)
    oa, ob = a["text_overlays"][0], b["text_overlays"][0]
    assert oa["layout"] == ob["layout"]
    sa, sb = short_side(1080, 1920), short_side(1920, 1080)
    assert abs(oa["fontsize"] / sa - ob["fontsize"] / sb) < 1e-9


def test_json_roundtrip_byte_identical(preset_store):
    from cutora.subtitle import text_preset as TP
    saved = TP.save_preset({
        "id": "p_round",
        "name": "往返",
        "text": "Hi",
        "layout": {"anchor": "top_left", "margin_x": 0.1, "margin_y": 0.1, "size_pct": 0.04},
        "style": {"color": "#ABC"},
        "created_at": "2026-09-15T00:00:00Z",
        "updated_at": "2026-09-15T00:00:00Z",
    })
    first = preset_store.read_text(encoding="utf-8")
    TP.save_preset(saved)
    second = preset_store.read_text(encoding="utf-8")
    assert first == second
    loaded = TP.get_preset("p_round")
    assert loaded["text"] == "Hi"
    assert loaded["layout"]["anchor"] == "top_left"


def test_commands_registered():
    from cutora.core.commands import get_registry
    names = {s.name for s in get_registry().list()}
    for n in ("text_preset.list", "text_preset.save", "text_preset.delete", "text_preset.apply"):
        assert n in names


def test_apply_command_headless(tmp_path, preset_store):
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    proj = {
        "tasks": [_task(1080, 1920), _task(1920, 1080)],
        "curr_idx": 0,
    }
    path = tmp_path / "p.asproj.json"
    path.write_text(json.dumps(proj), encoding="utf-8")
    bus = CommandBus(get_registry())
    out = bus.execute(
        "text_preset.apply",
        {"id": "credit.default", "idempotent": True, "project": str(path)},
        source="cli")
    assert out.get("ok") is True
    bus.execute(
        "text_preset.apply",
        {"id": "credit.default", "idempotent": True, "project": str(path)},
        source="cli")
    data = json.loads(path.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        credits = [ov for ov in task.get("text_overlays") or []
                   if ov.get("source_preset_id") == "credit.default"]
        assert len(credits) == 1


def test_three_projects_one_credit_each(tmp_path, preset_store):
    from cutora.subtitle.text_preset import apply_to_project_file
    paths = []
    for i in range(3):
        p = tmp_path / ("p%d.asproj.json" % i)
        p.write_text(json.dumps({"tasks": [_task(1080, 1920)]}), encoding="utf-8")
        paths.append(p)
    for p in paths:
        apply_to_project_file(str(p), "credit.default", idempotent=True)
        apply_to_project_file(str(p), "credit.default", idempotent=True)
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        credits = [ov for ov in data["tasks"][0].get("text_overlays") or []
                   if ov.get("source_preset_id") == "credit.default"]
        assert len(credits) == 1


def test_recipe_accepts_text_preset_apply():
    from cutora.batch.recipe import validate_recipe
    rec = {
        "schema_version": 1,
        "name": "credit",
        "description": "auto credit",
        "inputs": {},
        "steps": [
            {"command": "text_preset.apply",
             "params": {"id": "credit.default", "idempotent": True}},
        ],
    }
    assert validate_recipe(rec)["steps"][0]["command"] == "text_preset.apply"


def test_retarget_fixes_foreign_canvas_and_keeps_a_drag_on_the_authoring_one(preset_store):
    """批量套用把 1080 的像素印到 720 上。打开工程时按比例放回 720，
    同一套预设在 1080 上被拖过的 y 不动，署名和改过宽的框也不动。"""
    from cutora.subtitle.text_preset import (
        apply_layout_to_overlay, get_preset, layout_to_box, preset_from_overlay,
        retarget_project_text_boxes, save_preset,
    )
    authored = {
        "type": "text", "content": "Title",
        "x": 119, "y": 788, "w": 842, "h": 177.5625,
        "fontsize": 40, "scale": 1.2,
    }
    saved = save_preset(preset_from_overlay(authored, 1080, 1920, name="标题"))
    preset = get_preset(saved["id"])
    expect = layout_to_box(preset["layout"], 720, 1280, "Title")

    dragged = dict(authored)
    dragged["y"] = 1029.4375
    dragged["source_preset_id"] = saved["id"]
    stamped = dict(authored)
    stamped["source_preset_id"] = saved["id"]
    edited = dict(authored)
    edited["x"] = 200
    edited["source_preset_id"] = saved["id"]
    already = {"type": "text", "content": "Title", "source_preset_id": saved["id"],
               "scale": 1.2}
    apply_layout_to_overlay(already, preset["layout"], 720, 1280, "Title")
    already_snap = dict(already)
    credit = {"type": "text", "content": "Generated with AI",
              "source_preset_id": "credit.default",
              "x": 119, "y": 788, "w": 842, "h": 108, "fontsize": 17}
    plain = {"type": "text", "content": "note", "x": 119, "y": 788, "w": 842, "h": 40}

    data = {"tasks": [
        {"timeline": {"width": 1080, "height": 1920},
         "text_overlays": [dragged]},
        {"timeline": {"width": 720, "height": 1280},
         "text_overlays": [stamped, edited, already, credit, plain]},
    ]}
    n = retarget_project_text_boxes(data)
    assert n == 1
    assert dragged["y"] == 1029.4375
    assert dragged["x"] == 119 and dragged["w"] == 842 and dragged["fontsize"] == 40
    assert stamped["x"] == pytest.approx(expect[0])
    assert stamped["y"] == pytest.approx(expect[1])
    assert stamped["w"] == pytest.approx(expect[2])
    assert stamped["h"] == pytest.approx(expect[3])
    assert stamped["fontsize"] == pytest.approx(expect[4])
    assert stamped["scale"] == pytest.approx(1.2)
    assert stamped["x"] == pytest.approx(79.333333, abs=0.01)
    assert stamped["y"] == pytest.approx(525.333333, abs=0.01)
    assert stamped["w"] == pytest.approx(561.333333, abs=0.01)
    assert stamped["fontsize"] == pytest.approx(40 * 720 / 1080)
    assert edited["x"] == 200 and edited["w"] == 842
    assert already == already_snap
    assert credit["w"] == 842 and credit["fontsize"] == 17
    assert plain["w"] == 842
    assert retarget_project_text_boxes(data) == 0
