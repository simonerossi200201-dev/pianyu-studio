# -*- coding: utf-8 -*-
"""T13c recipes: schema, recording, SRT export, skip-on-error, no render-job."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from cutora.batch import recipe as recipe_mod
from cutora.core.commands import reset_bus_for_tests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
G01_PROJ = os.path.join(REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")
BUILTIN = os.path.join(REPO, "resources", "data", "recipes")


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_bus_for_tests()
    yield
    rec = recipe_mod.get_recorder()
    rec.stop()
    reset_bus_for_tests()


def test_builtin_recipes_validate():
    paths = recipe_mod.builtin_recipes()
    names = {recipe_mod.load_recipe(p)["name"] for p in paths}
    assert "竖屏字幕快出" in names
    assert "横竖双版本" in names
    assert "只出 SRT 不渲染" in names


def test_load_recipe_rejects_index_steps(tmp_path):
    data = {
        "schema_version": 1,
        "name": "old-bomb",
        "description": "",
        "inputs": {},
        "steps": [
            {"command": "subtitle.auto", "params": {}, "on_error": "stop"},
            {
                "command": "subtitle.delete_segments",
                "params": {"seg_ids": [5, 12]},
                "on_error": "stop",
            },
        ],
    }
    path = tmp_path / "bomb.cutorarecipe"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(recipe_mod.RecipeError) as exc:
        recipe_mod.load_recipe(str(path))
    msg = str(exc.value)
    assert "请重新录制这份配方" in msg
    assert "subtitle.delete_segments" in msg
    assert "步骤 2" in msg


def test_apply_to_folder_refuses_non_portable(tmp_path):
    recipe = {
        "schema_version": 1,
        "name": "old-bomb",
        "description": "",
        "inputs": {},
        "steps": [
            {
                "command": "timeline.trim_clip",
                "params": {
                    "track_id": "x", "clip_index": 0,
                    "in_t": 0, "out_t": 1, "start_t": 0,
                },
                "on_error": "stop",
            },
        ],
    }
    folder = tmp_path / "in"
    folder.mkdir()
    with pytest.raises(recipe_mod.RecipeError) as exc:
        recipe_mod.apply_to_folder(recipe, str(folder), str(tmp_path / "out"))
    assert "请重新录制这份配方" in str(exc.value)
    assert "timeline.trim_clip" in str(exc.value)


def test_invalid_recipe_names_step_and_field(tmp_path):
    bad = {
        "schema_version": 1,
        "name": "x",
        "description": "",
        "inputs": {},
        "steps": [{"command": "media.probe", "params": {}, "on_error": "explode"}],
    }
    path = tmp_path / "bad.cutorarecipe"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(recipe_mod.RecipeError) as exc:
        recipe_mod.load_recipe(str(path))
    msg = str(exc.value)
    assert "步骤" in msg or "on_error" in msg


def test_record_ui_undoable_only():
    rec = recipe_mod.get_recorder()
    rec.start()
    rec._on_executed({
        "name": "media.import", "source": "ui",
        "params": {"paths": [r"D:\media\a.mp4"]},
    })
    rec._on_executed({
        "name": "app.version", "source": "ui", "params": {},
    })
    rec._on_executed({
        "name": "media.select", "source": "ui", "params": {"ids": [0]},
    })
    rec._on_executed({
        "name": "media.import", "source": "cli",
        "params": {"paths": [r"D:\media\b.mp4"]},
    })
    rec._on_executed({
        "name": "project.new", "source": "ui", "params": {},
    })
    rec._on_executed({
        "name": "subtitle.delete", "source": "ui",
        "params": {"seg_id": 5},
    })
    rec._on_executed({
        "name": "subtitle.trim_segment", "source": "ui",
        "params": {"seg_id": 0, "start": 0.0, "end": 1.0},
    })
    rec._on_executed({
        "name": "subtitle.auto", "source": "ui",
        "params": {"language": "auto"},
    })
    steps = rec.stop()
    assert [s["command"] for s in steps] == ["media.import", "subtitle.auto"]
    assert steps[0]["params"]["paths"] == ["{media}"]
    assert "subtitle.delete" in rec.skipped
    assert "subtitle.trim_segment" in rec.skipped
    assert "project.new" in rec.skipped


def test_srt_only_recipe_steps_auto_then_export():
    recipe = recipe_mod.load_recipe(os.path.join(BUILTIN, "srt_only.cutorarecipe"))
    cmds = [s["command"] for s in recipe["steps"]]
    assert cmds == ["subtitle.auto", "subtitle.export_srt"]
    skip_if = recipe["steps"][0].get("skip_if") or {}
    assert skip_if.get("command") == "subtitle.status"
    assert skip_if.get("field") == "has_segments"
    assert skip_if.get("equals") is True


def test_srt_only_writes_srt_without_render_job(tmp_path, monkeypatch):
    spawned = []
    auto_results = []
    real = subprocess.Popen

    def watch(cmd, *a, **k):
        spawned.append(list(cmd) if not isinstance(cmd, str) else [cmd])
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", watch)
    from cutora.core.commands import get_bus
    bus = get_bus()
    real_exec = bus.execute

    def wrap(name, params=None, source="ui", ctx=None):
        out = real_exec(name, params, source=source, ctx=ctx)
        if name == "subtitle.auto":
            auto_results.append(out)
        return out

    monkeypatch.setattr(bus, "execute", wrap)
    media_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    media_dir.mkdir()
    out_dir.mkdir()
    video = media_dir / "video.mp4"
    shutil.copy2(G01, video)
    shutil.copy2(G01_PROJ, media_dir / "project.asproj.json")
    recipe = recipe_mod.load_recipe(os.path.join(BUILTIN, "srt_only.cutorarecipe"))
    report = recipe_mod.apply_to_folder(recipe, str(media_dir), str(out_dir))
    assert report["counts"]["ok"] == 1
    assert auto_results == []
    assert report["files"][0].get("skipped_steps") == ["subtitle.auto"]
    srt = out_dir / "video.srt"
    assert srt.is_file()
    text = srt.read_text(encoding="utf-8")
    assert "Hello" in text or "friends" in text.lower() or "-->" in text
    flat = " ".join(str(x) for row in spawned for x in row)
    assert "--render-job" not in flat


def test_srt_only_without_subs_invokes_auto(tmp_path):
    """No project/segments: recipe still runs subtitle.auto (would identify)."""
    called = []

    def execute(cmd, params):
        called.append((cmd, dict(params)))
        if cmd == "subtitle.status":
            return {"has_segments": False, "segment_count": 0, "language": None}
        if cmd == "subtitle.auto":
            return {"queued": 1}
        if cmd == "subtitle.export_srt":
            dest = os.path.join(params["out_dir"], "ok.srt")
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
            return {"path": dest}
        return {}

    folder = tmp_path / "in"
    out = tmp_path / "out"
    folder.mkdir()
    out.mkdir()
    shutil.copy2(G01, folder / "ok.mp4")
    recipe = recipe_mod.load_recipe(os.path.join(BUILTIN, "srt_only.cutorarecipe"))
    report = recipe_mod.apply_to_folder(
        recipe, str(folder), str(out), execute=execute)
    assert report["counts"]["ok"] == 1
    names = [c[0] for c in called]
    assert names[0] == "subtitle.status"
    assert "subtitle.auto" in names


def test_subtitle_status_headless_from_project(tmp_path):
    from cutora.core.cmds.light import cmd_subtitle_status
    from cutora.core.commands import Context

    media_dir = tmp_path / "in"
    media_dir.mkdir()
    video = media_dir / "video.mp4"
    shutil.copy2(G01, video)
    shutil.copy2(G01_PROJ, media_dir / "project.asproj.json")
    out = cmd_subtitle_status(
        Context(window=None), {"media": str(video)})
    assert out["has_segments"] is True
    assert out["segment_count"] > 0

    empty = tmp_path / "bare.mp4"
    shutil.copy2(G01, empty)
    out2 = cmd_subtitle_status(Context(window=None), {"media": str(empty)})
    assert out2["has_segments"] is False
    assert out2["segment_count"] == 0


def _five_step_probe_recipe():
    steps = [
        {"command": "media.probe", "params": {"path": "{media}"}, "on_error": "skip"}
        for _ in range(5)
    ]
    return {
        "schema_version": 1,
        "name": "五步探测",
        "description": "录制五步套用跳过坏文件",
        "inputs": {"media": {"description": "视频"}, "out_dir": {"description": "输出"}},
        "steps": steps,
    }


def test_apply_six_files_skip_one(tmp_path):
    folder = tmp_path / "six"
    out = tmp_path / "out"
    folder.mkdir()
    out.mkdir()
    for i in range(5):
        shutil.copy2(G01, folder / ("ok%d.mp4" % i))
    (folder / "bad.mp4").write_text("not a video", encoding="utf-8")
    recipe = _five_step_probe_recipe()
    report = recipe_mod.apply_to_folder(recipe, str(folder), str(out))
    assert report["counts"]["ok"] == 5
    assert report["counts"]["skip"] == 1
    assert report["counts"]["fail"] == 0
    assert (out / "_cutora_batch_report.json").is_file()


def test_vertical_and_dual_recipes_load():
    for name in ("vertical_subs.cutorarecipe", "hv_dual.cutorarecipe"):
        data = recipe_mod.load_recipe(os.path.join(BUILTIN, name))
        assert data["steps"]
        assert any(s["command"] == "export.run" for s in data["steps"])


def test_sse_progress_events(tmp_path):
    from cutora.api.events import HUB
    seen = []

    def _cb(p):
        seen.append(p)

    HUB.subscribe_sync(_cb)
    folder = tmp_path / "one"
    out = tmp_path / "out"
    folder.mkdir()
    out.mkdir()
    shutil.copy2(G01, folder / "ok.mp4")
    recipe_mod.apply_to_folder(_five_step_probe_recipe(), str(folder), str(out))
    types = [p.get("type") for p in seen]
    assert "progress" in types
    HUB.unsubscribe_sync(_cb)
