# -*- coding: utf-8 -*-
"""T05g mute/solo/locked defaults, prune, command-layer lock."""
from __future__ import annotations

import os

from cutora.model.timeline_model import Track
from cutora.model.track_guard import (
    assert_ripple_unlocked, assert_type_unlocked, assert_unlocked,
    find_track, track_is_muted,
)
from cutora.audio.waveform import (
    MAX_WAVEFORM_CACHE_BYTES, clear_waveform_cache, prune_waveform_cache,
    waveform_disk_dir,
)


def test_track_defaults_and_old_project():
    tr = Track("video")
    assert tr.mute is False
    assert tr.locked is False
    task = {"timeline": type("T", (), {"tracks": [tr]})()}
    assert_unlocked(task, track_id=tr.id)
    tr.locked = True
    try:
        assert_unlocked(task, track=tr)
        raise AssertionError("expected lock error")
    except ValueError as exc:
        assert "锁定" in str(exc)


def test_old_project_json_without_mute_locked():
    from cutora.model.project_io import (
        _normalize_project_data, _rj_deserialize_timeline,
    )
    data = {
        "version": 1,
        "tasks": [{
            "timeline": {
                "width": 1920, "height": 1080, "fps": 30,
                "sources": {},
                "tracks": [{"type": "video", "clips": []}],
            },
        }],
    }
    _normalize_project_data(data)
    raw = data["tasks"][0]["timeline"]["tracks"][0]
    assert raw["mute"] is False
    assert raw["locked"] is False
    tl = _rj_deserialize_timeline(data["tasks"][0]["timeline"])
    assert tl.tracks[0].mute is False
    assert tl.tracks[0].locked is False


def test_assert_unlocked_unknown_id_raises():
    tr = Track("video")
    task = {"timeline": type("T", (), {"tracks": [tr]})()}
    try:
        assert_unlocked(task, track_id="does-not-exist")
        raise AssertionError("missing track_id must raise")
    except ValueError as exc:
        assert "找不到" in str(exc)


def test_find_track_id_first_exactly_one():
    a = Track("video")
    b = Track("video")
    task = {"timeline": type("T", (), {"tracks": [a, b]})()}
    assert find_track(task, track_id=b.id) is b
    assert find_track(task, track_type="video") is a
    try:
        find_track(task, track_id=b.id, track_type="video")
        raise AssertionError("both selectors must raise")
    except ValueError as exc:
        assert "只能传一个" in str(exc)


def test_ripple_guard_uses_link_group_id():
    from cutora.model.timeline_model import Clip
    v = Track("video")
    a = Track("audio")
    a.domain = "audio"
    vc = Clip("src-v", 0.0, 2.0, 0.0)
    ac = Clip("src-a", 0.0, 2.0, 0.0)
    vc.link_group_id = ac.link_group_id = "grp-1"
    v.clips.append(vc)
    a.clips.append(ac)
    a.locked = True
    task = {"timeline": type("T", (), {"tracks": [v, a]})()}
    try:
        assert_ripple_unlocked(task, track_id=v.id)
        raise AssertionError("linked locked audio must block")
    except ValueError as exc:
        assert "锁定" in str(exc)


def test_ripple_guard_checks_every_subs_track():
    v = Track("video")
    s1 = Track("subs")
    s2 = Track("subs")
    s2.locked = True
    from cutora.model.timeline_model import Clip
    v.clips.append(Clip("src", 0.0, 1.0, 0.0))
    task = {"timeline": type("T", (), {"tracks": [v, s1, s2]})()}
    try:
        assert_ripple_unlocked(task, track_id=v.id)
        raise AssertionError("second locked subs must block")
    except ValueError as exc:
        assert "锁定" in str(exc)
    try:
        assert_type_unlocked(task, "subs")
        raise AssertionError("type unlock must see second subs")
    except ValueError as exc:
        assert "锁定" in str(exc)


def test_solo_does_not_persist_and_mutes_peers():
    a = Track("audio")
    b = Track("audio")
    a.domain = b.domain = "audio"
    task = {"timeline": type("T", (), {"tracks": [a, b]})()}
    assert track_is_muted(a, task=task, solo_ids=set()) is False
    assert track_is_muted(b, task=task, solo_ids={str(a.id)}) is True
    assert track_is_muted(a, task=task, solo_ids={str(a.id)}) is False
    vis = Track("video")
    assert vis.domain == "visual"
    mixed = {"timeline": type("T", (), {"tracks": [a, vis]})()}
    assert track_is_muted(vis, task=mixed, solo_ids={str(a.id)}) is False


def test_visual_solo_preview_drops_peers_export_ignores_solo():
    import inspect
    from cutora.render.frame_renderer import (
        compute_export_render_order, compute_render_order,
    )
    assert "solo_ids" not in inspect.signature(
        compute_export_render_order).parameters
    from cutora.model.timeline import Timeline
    tl = Timeline(1920, 1080, 30)
    subs = Track("subs")
    text = Track("text")
    tl.tracks.extend([Track("video"), subs, text])
    task = {"timeline": tl}
    export = compute_export_render_order(task)
    assert "subs" in export and "text" in export
    preview = compute_render_order(task, solo_ids={str(subs.id)})
    assert "subs" in preview
    assert "text" not in preview
    assert compute_export_render_order(task) == export
    assert compute_render_order(task) == export


def test_muted_overlay_not_restored_by_default_order():
    from cutora.render.frame_renderer import (
        _DEFAULT_RENDER_ORDER, compute_render_order,
    )
    from cutora.model.timeline import Timeline
    tl = Timeline(1920, 1080, 30)
    subs = Track("subs")
    subs.mute = True
    tl.tracks.append(subs)
    order = compute_render_order({"timeline": tl})
    assert "subs" not in order
    video_only = Timeline(1920, 1080, 30)
    video_only.tracks.append(Track("video"))
    assert compute_render_order({"timeline": video_only}) == list(
        _DEFAULT_RENDER_ORDER)


def test_prune_deletes_oldest(tmp_path, monkeypatch):
    d = tmp_path / "waveform"
    d.mkdir()
    monkeypatch.setattr("cutora.audio.waveform.waveform_disk_dir", lambda: str(d))
    old = d / "old.npz"
    new = d / "new.npz"
    old.write_bytes(b"x" * 1000)
    new.write_bytes(b"y" * 1000)
    os.utime(old, (1, 1))
    os.utime(new, (100000, 100000))
    freed = prune_waveform_cache(max_bytes=1500)
    assert freed >= 1000
    assert not old.exists()
    assert new.exists()
    assert MAX_WAVEFORM_CACHE_BYTES == 512 * 1024 * 1024
    leftover = d / "keep.npz"
    leftover.write_bytes(b"z" * 100)
    cleared = clear_waveform_cache()
    assert cleared >= 100
    assert not leftover.exists()
