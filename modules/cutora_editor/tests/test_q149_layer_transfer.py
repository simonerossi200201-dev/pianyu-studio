# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""跨视频搬图层：原时间、轨道不够就新开、属性按种类和序号对应。不导入 Qt。"""
from __future__ import annotations

import copy
import os

from cutora.batch import layer_transfer as lt
from cutora.batch import monitor_model as mm
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, MediaSource, Track


def _box(w=1080, h=1920):
    tl = Timeline(w, h, 30)
    task = {"timeline": tl, "dims": (w, h), "text_overlays": [], "image_overlays": []}
    return task, tl


def _track(tl, kind, path, dur=4.0, main=False):
    tr = Track(kind)
    tr.is_main_video = bool(main)
    tl.add_track(tr)
    src = MediaSource(path, duration=dur, probe=False)
    tl.sources[src.id] = src
    return tr, src


def _put(tr, src, start, inn, out, **kw):
    clip = Clip(src.id, inn, out, start)
    for key, val in kw.items():
        setattr(clip, key, val)
    tr.clips.append(clip)
    return clip


def _videos(task):
    return [tr for tr in task["timeline"].tracks if tr.type == "video"]


def test_material_video_keeps_time_and_shares_one_new_track():
    src, tl = _box()
    tr, media = _track(tl, "video", r"D:\media\LayerA.mp4", main=True)
    tr.split_cell = "A"
    _put(tr, media, 0, 0, 2, x=30, is_main=True)
    _put(tr, media, 2, 2, 4, x=8)
    dst, dl = _box()
    main, other = _track(dl, "video", r"D:\media\LayerB.mp4", main=True)
    main.split_cell = "B"
    _put(main, other, 0, 0, 4)
    layers = [row for row in lt.list_layers(src) if row["kind"] == "video"]
    assert [row["index"] for row in layers] == [0, 1]
    out = lt.apply_pack(dst, lt.pack_layers(src, layers, "material"))
    assert out == {"added": 2, "styled": 0, "missed": 0}
    videos = _videos(dst)
    assert len(videos) == 2 and videos[0] is main and len(main.clips) == 1
    new = videos[1]
    assert new.is_main_video is False and new.split_cell is None
    assert new.name == "复制的视频"
    assert [(c.start_t, c.out_t, c.x, c.is_main) for c in new.clips] == [
        (0.0, 2.0, 30.0, False), (2.0, 4.0, 8.0, False),
    ]
    assert main.split_cell == "B"


def test_overlapping_clips_open_separate_tracks():
    src, tl = _box()
    tr, media = _track(tl, "video", r"D:\media\LayerA.mp4", main=True)
    _put(tr, media, 0, 0, 3)
    _put(tr, media, 1, 1, 4)
    dst, dl = _box()
    main, other = _track(dl, "video", r"D:\media\LayerB.mp4", main=True)
    _put(main, other, 0, 0, 4)
    layers = [row for row in lt.list_layers(src) if row["kind"] == "video"]
    out = lt.apply_pack(dst, lt.pack_layers(src, layers, "material"))
    assert out["added"] == 2
    assert len(_videos(dst)) == 3
    assert [len(tr.clips) for tr in _videos(dst)] == [1, 1, 1]


def test_source_reused_by_normalized_path_and_time_order():
    src, tl = _box()
    tr, _media = _track(tl, "video", r"D:\media\LayerA.mp4", main=True)
    late = _put(tr, _media, 3, 0, 1, gain_db=1.0)
    early = _put(tr, _media, 0, 0, 1, gain_db=9.0)
    assert tr.clips[0] is late
    dst, dl = _box()
    main, kept = _track(dl, "video", r"d:/media/LayerA.mp4", main=True)
    first = _put(main, kept, 5, 0, 1, gain_db=0.0, is_main=True)
    second = _put(main, kept, 0, 0, 1, gain_db=4.0)
    before = len(dl.sources)
    layers = [row for row in lt.list_layers(src) if row["kind"] == "video"]
    assert layers[0]["locator"]["clip_index"] == tr.clips.index(early)
    style = lt.pack_layers(src, [layers[0]], "style")
    out = lt.apply_pack(dst, style)
    assert out["styled"] == 1 and out["added"] == 0
    assert second.gain_db == 9.0 and second.start_t == 0.0 and second.source_id == kept.id
    assert first.gain_db == 0.0 and first.is_main is True
    assert len(dl.sources) == before
    assert len(dst["timeline"].tracks) == 1
    lt.apply_pack(dst, lt.pack_layers(src, [layers[0]], "material"))
    added = [tr for tr in dst["timeline"].tracks if tr.type == "video" and tr is not main]
    assert len(added) == 1 and added[0].clips[0].source_id == kept.id
    assert len(dl.sources) == before


def test_text_material_keeps_original_time_and_opens_a_track():
    src, tl = _box()
    tt = Track("text")
    tl.add_track(tt)
    src["text_overlays"].append({
        "id": 4, "content": "标题", "x": 200, "y": 40, "w": 80, "h": 20,
        "fontsize": 40, "start": 1.5, "end": 9.0, "_track_id": tt.id,
        "fade_top_offset": 0.2,
    })
    dst, dl = _box()
    busy = Track("text")
    dl.add_track(busy)
    dst["text_overlays"].append({
        "id": 1, "content": "已有", "start": 0.0, "end": 3.0, "_track_id": busy.id,
    })
    out = lt.apply_pack(dst, lt.pack_layers(src, lt.list_layers(src), "material"))
    assert out["added"] == 1
    texts = [tr for tr in dl.tracks if tr.type == "text"]
    assert len(texts) == 2 and texts[1].name == "复制的文本"
    new = dst["text_overlays"][-1]
    assert new["start"] == 1.5 and new["end"] == 9.0
    assert new["content"] == "标题" and new["id"] != 4
    assert new["_track_id"] == texts[1].id
    assert new["fade_top_offset"] == 0.2
    assert dst["text_overlays"][0]["content"] == "已有"


def test_locked_track_is_not_a_free_slot():
    src, tl = _box()
    src["text_overlays"].append({"id": 1, "content": "甲", "start": 5.0, "end": 6.0, "x": 1})
    dst, dl = _box()
    busy = Track("text")
    busy.locked = True
    dl.add_track(busy)
    dst["text_overlays"].append({"id": 2, "content": "乙", "start": 0.0, "end": 1.0, "_track_id": busy.id})
    lt.apply_pack(dst, lt.pack_layers(src, lt.list_layers(src), "material"))
    texts = [tr for tr in dl.tracks if tr.type == "text"]
    assert len(texts) == 2
    assert dst["text_overlays"][-1]["_track_id"] == texts[1].id


def test_text_style_keeps_words_and_all_keeps_identity():
    src, _tl = _box()
    src["text_overlays"].append({
        "id": 1, "content": "甲", "x": 100, "y": 10, "font_family": "Arimo",
        "fontsize": 40, "start": 0.0, "end": 2.0,
    })
    dst, _dl = _box()
    dest = {
        "id": 9, "content": "乙", "x": 20, "y": 30, "font_family": "Other",
        "fontsize": 40, "start": 1.0, "end": 4.0, "_track_id": "keep",
    }
    dst["text_overlays"].append(dest)
    style = lt.pack_layers(src, lt.list_layers(src), "style")
    assert lt.apply_pack(dst, style)["styled"] == 1
    assert dest["content"] == "乙" and dest["x"] == 20 and dest["start"] == 1.0
    assert dest["font_family"] == "Arimo" and dest["id"] == 9 and dest["_track_id"] == "keep"
    fresh, _fl = _box()
    fresh["text_overlays"].append({
        "id": 9, "content": "乙", "x": 20, "font_family": "Other",
        "fontsize": 40, "start": 1.0, "end": 4.0, "_track_id": "keep",
    })
    whole = lt.pack_layers(src, lt.list_layers(src), "all")
    assert lt.apply_pack(fresh, whole)["styled"] == 1
    got = fresh["text_overlays"][0]
    assert got["content"] == "甲" and got["x"] == 100 and got["start"] == 0.0
    assert got["id"] == 9 and got["_track_id"] == "keep"
    assert len(fresh["text_overlays"]) == 1


def test_missing_layer_is_a_miss_and_identical_style_is_nothing():
    src, _tl = _box()
    src["text_overlays"].append({"id": 1, "content": "甲", "font_family": "Arimo", "start": 0, "end": 1})
    src["text_overlays"].append({"id": 2, "content": "乙", "font_family": "Arimo", "start": 1, "end": 2})
    dst, _dl = _box()
    only = {"id": 3, "content": "丙", "font_family": "Other", "start": 0, "end": 1}
    dst["text_overlays"].append(only)
    layers = lt.list_layers(src)
    second = [row for row in layers if row["kind"] == "text" and row["index"] == 1]
    out = lt.apply_pack(dst, lt.pack_layers(src, second, "style"))
    assert out == {"added": 0, "styled": 0, "missed": 1}
    assert only["font_family"] == "Other" and len(dst["text_overlays"]) == 1
    assert "没有对应图层" in lt.describe_apply(1, out)
    same, _sl = _box()
    same["text_overlays"].append(copy.deepcopy(src["text_overlays"][0]))
    same["text_overlays"][0]["id"] = 8
    first = [row for row in layers if row["kind"] == "text" and row["index"] == 0]
    again = lt.apply_pack(same, lt.pack_layers(src, first, "style"))
    assert again["styled"] == 0 and again["missed"] == 0


def test_audio_style_all_and_bgm_are_separate():
    src, tl = _box()
    audio, voice = _track(tl, "audio", r"D:\media\voice.wav", dur=3)
    _put(audio, voice, 0.0, 0.0, 2.0, gain_db=-8.0, is_main=True)
    bgm, music = _track(tl, "bgm", r"D:\media\music.wav", dur=3)
    _put(bgm, music, 0.0, 0.0, 2.0, gain_db=-6.0)
    dst, dl = _box()
    da, dv = _track(dl, "audio", r"D:\media\other.wav", dur=3)
    spoken = _put(da, dv, 1.5, 0.0, 2.0, gain_db=0.0, is_main=False)
    db, dm = _track(dl, "bgm", r"D:\media\bed.wav", dur=3)
    bed = _put(db, dm, 0.0, 0.0, 2.0, gain_db=2.0)
    kinds = [row["kind"] for row in lt.list_layers(src)]
    assert kinds == ["audio", "bgm"]
    voice_layer = [row for row in lt.list_layers(src) if row["kind"] == "audio"]
    out = lt.apply_pack(dst, lt.pack_layers(src, voice_layer, "style"))
    assert out["styled"] == 1
    assert spoken.gain_db == -8.0 and spoken.start_t == 1.5 and spoken.is_main is False
    assert bed.gain_db == 2.0
    music_layer = [row for row in lt.list_layers(src) if row["kind"] == "bgm"]
    lt.apply_pack(dst, lt.pack_layers(src, music_layer, "all"))
    assert bed.gain_db == -6.0 and bed.start_t == 0.0 and bed is db.clips[0]
    assert spoken.source_id == dv.id and spoken.start_t == 1.5
    assert len([tr for tr in dl.tracks if tr.type == "bgm"]) == 1


def test_image_style_keeps_picture_and_all_replaces_it():
    src, _tl = _box()
    src["image_overlays"].append({
        "id": 1, "x": 100, "y": 20, "w": 40, "h": 40, "opacity": 0.5,
        "start": 0.0, "end": 2.0,
        "elements": [{"path": r"D:\pic\a.png", "x": 0, "y": 0, "w": 40, "h": 40}],
    })
    dst, _dl = _box(540, 960)
    dest = {
        "id": 3, "x": 10, "y": 0, "w": 20, "h": 20, "opacity": 1.0,
        "start": 0.2, "end": 1.0, "_track_id": "img",
        "elements": [{"path": r"D:\pic\b.png", "x": 1, "y": 1, "w": 8, "h": 8}],
    }
    dst["image_overlays"].append(dest)
    out = lt.apply_pack(dst, lt.pack_layers(src, lt.list_layers(src), "style"))
    assert out["styled"] == 1 and out["added"] == 0
    assert dest["elements"][0]["path"].endswith("b.png")
    assert dest["x"] == 50 and dest["start"] == 0.2 and dest["id"] == 3
    whole, wl = _box(540, 960)
    kept = copy.deepcopy(dest)
    kept["x"] = 10
    whole["image_overlays"].append(kept)
    assert lt.apply_pack(whole, lt.pack_layers(src, lt.list_layers(src), "all"))["styled"] == 1
    got = whole["image_overlays"][0]
    assert got["id"] == 3 and got["_track_id"] == "img"
    assert got["elements"][0]["path"].endswith("a.png")
    assert got["x"] == 50 and got["start"] == 0.0
    assert len(wl.tracks) == 0


def test_history_undo_redo_clip_fields_and_new_tracks():
    src, tl = _box()
    audio, voice = _track(tl, "audio", r"D:\media\voice.wav", dur=3)
    _put(audio, voice, 0.2, 0.0, 1.0, gain_db=-8.0)
    src["text_overlays"].append({"id": 4, "content": "标题", "start": 1.0, "end": 2.0, "x": 12})
    dst, dl = _box()
    da, dv = _track(dl, "audio", r"D:\media\other.wav", dur=3)
    spoken = _put(da, dv, 1.5, 0.0, 1.0, gain_db=0.0)
    h = mm.History()
    tok = h.begin([dst], snap=mm.snapshot_layers)
    assert tok[0][1]["kind"] == "layers"
    audio_layer = [row for row in lt.list_layers(src) if row["kind"] == "audio"]
    lt.apply_pack(dst, lt.pack_layers(src, audio_layer, "all"))
    assert spoken.gain_db == -8.0 and spoken.start_t == 0.2 and spoken.source_id != dv.id
    h.commit(tok, "粘贴全部属性")
    h.undo()
    assert spoken.gain_db == 0.0 and spoken.start_t == 1.5 and spoken.source_id == dv.id
    h.redo()
    assert spoken.gain_db == -8.0 and spoken.start_t == 0.2

    bare, bl = _box()
    main, other = _track(bl, "video", r"D:\media\LayerB.mp4", main=True)
    _put(main, other, 0, 0, 4)
    tok = h.begin([bare], snap=mm.snapshot_layers)
    lt.apply_pack(bare, lt.pack_layers(src, lt.list_layers(src), "material"))
    assert len(bare["text_overlays"]) == 1
    assert any(tr.type == "audio" for tr in bl.tracks)
    h.commit(tok, "粘贴素材")
    h.undo()
    assert bare["text_overlays"] == []
    assert [tr.type for tr in bl.tracks] == ["video"]
    h.redo()
    assert bare["text_overlays"][0]["content"] == "标题"
    assert bare["text_overlays"][0]["start"] == 1.0


def test_shared_clipboard_and_describe():
    src, _tl = _box()
    src["text_overlays"].append({"id": 1, "content": "甲", "start": 0, "end": 1})
    pack = lt.pack_layers(src, lt.list_layers(src), "material")
    mm.set_clipboard(None)
    try:
        mm.set_clipboard(pack)
        assert mm.get_clipboard()["kind"] == "layers"
        assert "素材" in lt.describe_copy(pack)
    finally:
        mm.set_clipboard(None)
    assert lt.describe_apply(2, {"added": 3, "styled": 0, "missed": 1}).startswith("已把素材贴到 2 条")
