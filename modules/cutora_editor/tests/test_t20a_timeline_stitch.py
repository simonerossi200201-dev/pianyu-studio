# -*- coding: utf-8 -*-
"""T20a: cutlist → ClipPlacement, fill canvas, no stitched mp4."""
from __future__ import annotations

import json
import os

from cutora.batch.timeline_stitch import (
    ClipPlacement,
    placements_from_cutlist,
    write_placements,
)


def test_gui_start_goes_to_timeline_not_encode():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "cutora", "batch", "script_stitcher.py"),
               encoding="utf-8").read()
    assert "self._finish_as_timeline(cuts_map)" in src
    assert "生成时间线" in src
    assert 'self.btn_start = QPushButton("开始拼接")' in src
    assert "class EncodeWorker" not in src
    finish = src.split("def _finish_as_timeline", 1)[1]
    assert "EncodeWorker" not in finish
    assert "project_from_placements" in finish


def test_placements_hard_cut_and_fill():
    cuts = {
        "T040": [
            {"path": r"D:\c\a.mp4", "keep": True, "in_t": 0.5, "out_t": 2.5,
             "segs": [[0.5, 2.5]], "name": "a.mp4"},
            {"path": r"D:\c\b.mp4", "keep": False, "in_t": 0, "out_t": 8,
             "name": "b.mp4"},
            {"path": r"D:\c\c.mp4", "keep": True, "segs": [[1.0, 2.0], [3.0, 4.0]],
             "name": "c.mp4"},
        ]
    }
    got = placements_from_cutlist(cuts)
    assert [p.source_path for p in got] == [r"D:\c\a.mp4", r"D:\c\c.mp4", r"D:\c\c.mp4"]
    assert got[0].in_t == 0.5 and got[0].out_t == 2.5 and got[0].start_t == 0.0
    assert got[1].start_t == 2.0
    assert got[2].start_t == 3.0
    assert got[2].in_t == 3.0 and got[2].out_t == 4.0
    assert all(p.link_audio is True for p in got)
    assert all(p.fit_mode == "fill" for p in got)


def test_write_placements_does_not_create_mp4(tmp_path):
    p = ClipPlacement("a.mp4", 0.0, 1.0, 0.0)
    dest = write_placements(str(tmp_path), [p])
    assert dest.endswith("timeline_placements.json")
    data = json.loads(open(dest, encoding="utf-8").read())
    assert data["n"] == 1
    assert data["fit_mode"] == "fill"
    stitched = os.path.join(str(tmp_path), "_stitched")
    mp4s = [n for n in os.listdir(stitched) if n.lower().endswith(".mp4")]
    assert mp4s == []


def test_timeline_pairs_video_audio_fill():
    from cutora.batch.timeline_stitch import timeline_from_placements
    cuts = {
        "G": [
            {"path": __file__, "keep": True, "segs": [[0.0, 1.0]], "name": "a"},
            {"path": __file__, "keep": True, "segs": [[0.0, 2.0]], "name": "b"},
        ]
    }
    places = placements_from_cutlist(cuts)
    tl = timeline_from_placements(places, width=1080, height=1920)
    assert tl.width == 1080 and tl.height == 1920
    vtracks = [t for t in tl.tracks if t.type == "video"]
    atracks = [t for t in tl.tracks if t.type == "audio"]
    assert len(vtracks) == 1 and len(atracks) == 1
    assert len(vtracks[0].clips) == 2
    assert len(atracks[0].clips) == 2
    assert vtracks[0].clips[0].fit_mode == "fill"
    assert vtracks[0].clips[0].link_group_id == atracks[0].clips[0].link_group_id
    assert vtracks[0].clips[0].link_group_id != vtracks[0].clips[1].link_group_id
    assert abs(vtracks[0].clips[1].start_t - 1.0) < 1e-9


def test_stitch_to_timeline_command(tmp_path):
    folder = tmp_path / "_stitched"
    folder.mkdir()
    cuts = {"G": [{"path": "x.mp4", "keep": True, "segs": [[0.0, 1.5]], "name": "x.mp4"}]}
    (folder / "cutlist.json").write_text(json.dumps(cuts), encoding="utf-8")
    from cutora.core.commands import get_bus
    out = get_bus().execute(
        "stitch.to_timeline", {"task_dir": str(tmp_path)}, source="cli")
    assert out["ok"] is True
    assert out["n"] == 1
    assert out["mp4"] is None
    assert out["placements"][0]["fit_mode"] == "fill"
    assert out["placements"][0]["start_t"] == 0.0
    assert out["placements"][0]["out_t"] == 1.5
    assert out.get("asproj") and os.path.isfile(out["asproj"])
    assert not any(
        n.lower().endswith(".mp4")
        for n in os.listdir(os.path.dirname(out["asproj"])))


def test_asproj_roundtrip_keeps_link_group_and_fill(tmp_path):
    from cutora.batch.timeline_stitch import (
        placements_from_cutlist, project_from_placements, timeline_from_placements,
    )
    from cutora.model.project_io import deserialize_render_task
    cuts = {
        "G": [
            {"path": __file__, "keep": True, "segs": [[0.0, 1.0]], "name": "a"},
            {"path": __file__, "keep": True, "segs": [[0.5, 2.0]], "name": "b"},
        ]
    }
    places = placements_from_cutlist(cuts)
    dest = str(tmp_path / "stitched.asproj.json")
    project_from_placements(places, dest)
    data = json.loads(open(dest, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    tl = task["timeline"]
    vtracks = [t for t in tl.tracks if t.type == "video"]
    atracks = [t for t in tl.tracks if t.type == "audio"]
    assert vtracks[0].clips[0].fit_mode == "fill"
    assert vtracks[0].clips[0].link_group_id == atracks[0].clips[0].link_group_id
    assert vtracks[0].clips[0].link_group_id
    assert vtracks[0].clips[1].in_t == 0.5
    assert abs(vtracks[0].clips[1].start_t - 1.0) < 1e-9
    live = timeline_from_placements(places)
    assert live.tracks[0].clips[0].link_group_id == live.tracks[-1].clips[0].link_group_id


def test_multi_clip_timeline_detected():
    from cutora.asr.timeline_wav import is_multi_clip_timeline
    from cutora.batch.timeline_stitch import (
        placements_from_cutlist, timeline_from_placements,
    )
    cuts = {"G": [
        {"path": __file__, "keep": True, "segs": [[0.0, 1.0]], "name": "a"},
        {"path": __file__, "keep": True, "segs": [[0.0, 1.0]], "name": "b"},
    ]}
    tl = timeline_from_placements(placements_from_cutlist(cuts))
    task = {"timeline": tl, "video": __file__, "text": "hi", "segments": []}
    assert is_multi_clip_timeline(task) is True
    assert is_multi_clip_timeline({"video": __file__, "segments": []}) is False


def test_cli_stitch_no_longer_imports_encode_worker():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "cutora", "batch", "cli.py"),
               encoding="utf-8").read()
    assert "EncodeWorker" not in src
    assert "project_from_placements" in src


def test_g19_fixture_never_points_at_1_project():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    case = os.path.join(root, "tests", "golden", "g19_stitch_timeline")
    proj = os.path.join(case, "project.asproj.json")
    man = os.path.join(case, "manifest.json")
    assert os.path.isfile(proj) and os.path.isfile(man)
    blob = open(proj, encoding="utf-8").read()
    assert "1_PROJECT" not in blob
    data = json.loads(blob)
    task = data["tasks"][0]
    tl = task["timeline"]
    assert tl["width"] == 1080 and tl["height"] == 1920
    vclips = []
    for tr in tl["tracks"]:
        if tr.get("type") == "video" and tr.get("is_main_video"):
            vclips = tr.get("clips") or []
    assert len(vclips) == 5
    assert all(c.get("fit_mode") == "fill" for c in vclips)
    assert all(c.get("link_group_id") for c in vclips)
    man_data = json.loads(open(man, encoding="utf-8").read())
    assert man_data.get("asr_frozen") is True
    sha = os.path.join(case, "expected.sha256")
    if os.path.isfile(sha):
        line = open(sha, encoding="utf-8").read().strip().splitlines()[0]
        digest, name = line.split()
        assert name.endswith("expected.mp4")
        assert len(digest) == 64
