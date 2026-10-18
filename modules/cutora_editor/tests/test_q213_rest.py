# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""图层文件夹、子蒙版划分、配乐接缝和少重复。不导入 PySide6。"""
from __future__ import annotations


def test_folder_without_key_is_identity():
    from cutora.batch.layer_folders import decorate, is_identity, opacity_mul_current
    rows = [{"kind": "text", "label": "字"}]
    task = {}
    assert decorate(task, rows) is rows
    assert is_identity({"id": "fd"})
    assert is_identity({"id": "fd", "opacity": 255, "blend": "normal"}) is True
    assert is_identity({"id": "fd", "hidden": True}) is False
    assert opacity_mul_current(object()) == 1.0


def test_folder_decorate_inserts_header():
    from cutora.batch.layer_folders import add_folder, decorate
    ov = {"id": "ov1", "type": "text"}
    task = {"text_overlays": [ov]}
    rows = [{
        "kind": "text",
        "label": "字",
        "locator": {"where": "overlay", "list": "text_overlays", "index": 0},
    }]
    add_folder(task, ["o:ov1"], "一组")
    out = decorate(task, rows)
    assert out is not rows
    assert out[0]["kind"] == "folder"
    assert out[0]["title"] == "一组"
    assert out[1]["indent"] == 1
    assert out[1]["locator"]["index"] == 0


def test_partition_same_list_until_a_child_matte_exists():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.child_matte import partition_clips, set_track_matte, video_above
    tl = Timeline(1080, 1920, 30)
    lower = Track("video")
    upper = Track("video")
    child = Clip("s", 0.0, 2.0, 0.0)
    parent = Clip("s", 0.0, 2.0, 0.0)
    lower.clips = [child]
    upper.clips = [parent]
    tl.tracks = [lower, upper]
    task = {"timeline": tl}
    active = [parent]
    own, mattes = partition_clips(task, upper, 0.5, active)
    assert own is active
    assert mattes == []
    assert video_above(task, lower) is upper
    assert video_above(task, upper) is None
    assert set_track_matte(lower, upper.id, True) == 1
    assert child.matte_for == upper.id
    assert child.matte_invert is True
    own2, mattes2 = partition_clips(task, upper, 0.5, [parent])
    assert parent in own2
    assert child in mattes2
    assert child not in own2


def test_seam_only_when_flagged_and_graph_has_no_trailing_semicolon():
    from cutora.audio.loop_seam import seam_prefix, seam_seconds, want_seam
    assert want_seam({}) is False
    assert want_seam({"_clip": {"seam_xfade": False}}) is False
    assert want_seam({"_clip": {"seam_xfade": True}}) is True
    assert seam_seconds(0.05) == 0.0
    graph = seam_prefix(
        "in0", "lp0", trim_in=0.0, target_sr=48000, speed=1.0,
        one_loop=2.0, trim_len=10.0, xf=0.3, atempo=lambda _s: "")
    assert "acrossfade" in graph
    assert graph.endswith("[lp0]")
    assert not graph.endswith(";")


def test_assign_least_repeat_uses_fresh_then_least_used():
    from cutora.audio.bgm_plan import assign_least_repeat
    out = assign_least_repeat(3, ["a.mp3", "b.mp3"], used={"b.mp3"})
    assert out == ["a.mp3", "a.mp3", "b.mp3"]
    assert assign_least_repeat(0, ["a.mp3"]) == []
    assert assign_least_repeat(2, []) == []


def test_new_bgm_defaults_opt_in_to_seam_and_match():
    from cutora.audio.sfx_library import NEW_BGM_FADE_SEC, apply_new_bgm_defaults
    clip = {}
    apply_new_bgm_defaults(clip)
    assert clip["seam_xfade"] is True
    assert clip["match_duration"] is True
    assert clip["loop"] is True
    assert clip["audio_role"] == "bgm"
    assert abs(float(clip["fade_in_dur"]) - float(NEW_BGM_FADE_SEC)) < 1e-9
    assert abs(float(clip["fade_out_dur"]) - float(NEW_BGM_FADE_SEC)) < 1e-9


def test_identity_grade_does_not_change_the_picture():
    from cutora.render.grade import grade_changes_picture, identity_grade
    assert grade_changes_picture(identity_grade()) is False
    assert grade_changes_picture(None) is False
    moved = identity_grade()
    moved["levels"] = dict(moved["levels"])
    moved["levels"]["gamma"] = 1.4
    assert grade_changes_picture(moved) is True
