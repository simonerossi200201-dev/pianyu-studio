# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q204a: follow link_group_id on drag; razor rebind; audio clip menu."""
from __future__ import annotations

import inspect
import os
import sys

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _pair(offset=0.0, dur=2.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(1080, 1920, 30)
    vtr = Track("video")
    vtr.id = "v0"
    atr = Track("audio")
    atr.id = "a0"
    vc = Clip("src", 0.0, dur, 0.0)
    ac = Clip("src", 0.0, dur, offset)
    ac.is_main = True
    ac.audio_role = "main"
    gid = "gid-av"
    vc.link_group_id = gid
    ac.link_group_id = gid
    vtr.clips.append(vc)
    atr.clips.append(ac)
    tl.add_track(vtr)
    tl.add_track(atr)
    return tl, vtr, atr, vc, ac


class _DummyTimeline:
    def update(self):
        pass


from cutora.ui.command_bodies import CommandBodies  # noqa: E402


class _Win(CommandBodies):
    def __init__(self, tl):
        self.tasks = [{"timeline": tl, "duration": 8.0}]
        self.curr_idx = 0
        self.timeline = _DummyTimeline()


def test_index_skips_group_size_1():
    from cutora.model.av_link import index_link_groups, partners
    tl, vtr, atr, vc, ac = _pair()
    idx = index_link_groups(tl)
    assert list(idx) == ["gid-av"]
    assert {c for _t, c in idx["gid-av"]} == {vc, ac}
    assert partners(idx, vc)[0][1] is ac
    lone = type(vc)("x", 0.0, 1.0, 0.0)
    lone.link_group_id = "only-me"
    vtr.clips.append(lone)
    idx2 = index_link_groups(tl)
    assert "only-me" not in idx2
    assert "gid-av" in idx2


def test_no_second_mapping_table():
    import cutora.model.av_link as mod
    src = inspect.getsource(mod)
    assert "ClipLink" not in src
    assert "av_map" not in src
    assert "clip_links" not in src
    assert hasattr(mod, "index_link_groups")
    assert hasattr(mod, "partners")
    assert hasattr(mod, "rebind_link_groups_after_split")


def test_drag_same_delta_and_offset():
    from cutora.model.av_link import apply_edge_delta
    tl, vtr, atr, vc, ac = _pair(offset=0.0)
    ac.start_t = 0.35
    off = ac.start_t - vc.start_t
    vc.start_t += 0.4
    ac.start_t += 0.4
    assert ac.start_t - vc.start_t == off
    assert vc.start_t == 0.4
    apply_edge_delta(vc, "right", 0.4, audio=False)
    apply_edge_delta(ac, "right", 0.4, audio=True)
    assert ac.start_t - vc.start_t == off
    from cutora.model.av_link import clip_timeline_span
    _, ve = clip_timeline_span(vc, audio=False)
    _, ae = clip_timeline_span(ac, audio=True)
    assert ve == 2.8
    assert ae == 3.15


def test_split_at_rebind_and_join_keeps_left():
    from cutora.model.av_link import index_link_groups
    from cutora.model.seams import join_clips
    tl, vtr, atr, vc, ac = _pair()
    old = vc.link_group_id
    win = _Win(tl)
    win.command_split_at(1.0)
    assert len(vtr.clips) == 2 and len(atr.clips) == 2
    left_g = {vtr.clips[0].link_group_id, atr.clips[0].link_group_id}
    right_g = {vtr.clips[1].link_group_id, atr.clips[1].link_group_id}
    assert left_g == {old}
    assert len(right_g) == 1
    assert old not in right_g
    idx = index_link_groups(tl)
    assert len(idx) == 2
    join_clips(vtr, 0, 1, fps=30.0)
    join_clips(atr, 0, 1, fps=30.0)
    assert vtr.clips[0].link_group_id == old
    assert atr.clips[0].link_group_id == old
    assert index_link_groups(tl)[old]


def test_razor_pair_and_video_only():
    from cutora.model.av_link import index_link_groups
    tl, vtr, atr, vc, ac = _pair()
    old = vc.link_group_id
    win = _Win(tl)
    win.command_timeline_razor(1.0, clips=[
        {"track_id": "v0", "clip_index": 0},
        {"track_id": "a0", "clip_index": 0},
    ])
    assert len(vtr.clips) == 2 and len(atr.clips) == 2
    assert vtr.clips[0].link_group_id == old == atr.clips[0].link_group_id
    assert vtr.clips[1].link_group_id == atr.clips[1].link_group_id
    assert vtr.clips[1].link_group_id != old
    assert len(index_link_groups(tl)) == 2

    tl2, v2, a2, vc2, ac2 = _pair()
    old2 = vc2.link_group_id
    win2 = _Win(tl2)
    win2.command_timeline_razor(1.0, clips=[{"track_id": "v0", "clip_index": 0}])
    assert len(v2.clips) == 2 and len(a2.clips) == 1
    assert v2.clips[0].link_group_id == old2 == ac2.link_group_id
    assert v2.clips[1].link_group_id != old2
    idx = index_link_groups(tl2)
    assert old2 in idx and len(idx[old2]) == 2
    assert v2.clips[1].link_group_id not in idx


def test_alt_copy_new_gid_original_group_stays():
    from cutora.model.av_link import index_link_groups
    tl, vtr, atr, vc, ac = _pair()
    old = vc.link_group_id
    win = _Win(tl)
    win.command_duplicate_clips(
        clips=[{"track_id": "v0", "clip_index": 0}],
        time=3.0,
    )
    assert len(vtr.clips) == 2
    copy_c = vtr.clips[1]
    assert copy_c.link_group_id
    assert copy_c.link_group_id != old
    idx = index_link_groups(tl)
    assert old in idx and len(idx[old]) == 2
    assert copy_c.link_group_id not in idx


def test_alt_copy_pair_shares_new_gid():
    from cutora.model.av_link import index_link_groups
    tl, vtr, atr, vc, ac = _pair()
    old = vc.link_group_id
    win = _Win(tl)
    win.command_duplicate_clips(
        clips=[
            {"track_id": "v0", "clip_index": 0},
            {"track_id": "a0", "clip_index": 0},
        ],
        time=3.0,
    )
    copies = [vtr.clips[1], atr.clips[1]]
    assert copies[0].link_group_id == copies[1].link_group_id
    assert copies[0].link_group_id != old
    idx = index_link_groups(tl)
    assert old in idx and copies[0].link_group_id in idx


def test_trim_clips_command_and_alt_edge_single():
    from cutora.model.av_link import apply_edge_delta, clip_timeline_span
    tl, vtr, atr, vc, ac = _pair(offset=0.25)
    off = ac.start_t - vc.start_t
    v_end0 = clip_timeline_span(vc, audio=False)[1]
    a_end0 = clip_timeline_span(ac, audio=True)[1]
    apply_edge_delta(vc, "right", 0.4, audio=False)
    apply_edge_delta(ac, "right", 0.4, audio=True)
    win = _Win(tl)
    win.command_trim_clips(clips=[
        {
            "track_id": "v0", "clip_index": 0,
            "in_t": vc.in_t, "out_t": vc.out_t, "start_t": vc.start_t,
        },
        {
            "track_id": "a0", "clip_index": 0,
            "in_t": ac.in_t, "out_t": ac.out_t, "start_t": ac.start_t,
        },
    ])
    assert ac.start_t - vc.start_t == off
    assert clip_timeline_span(vc, audio=False)[1] == v_end0 + 0.4
    assert clip_timeline_span(ac, audio=True)[1] == a_end0 + 0.4

    tl2, v2, a2, vc2, ac2 = _pair()
    a_out = ac2.out_t
    win2 = _Win(tl2)
    apply_edge_delta(vc2, "right", -0.3, audio=False)
    win2.command_trim_clip(
        track_id="v0", clip_index=0,
        in_t=vc2.in_t, out_t=vc2.out_t, start_t=vc2.start_t,
    )
    assert a2.clips[0].out_t == a_out


def test_unlink_and_link_commands():
    from cutora.model.av_link import index_link_groups
    tl, vtr, atr, vc, ac = _pair()
    win = _Win(tl)
    win.command_unlink_clips(clips=[
        {"track_id": "v0", "clip_index": 0},
        {"track_id": "a0", "clip_index": 0},
    ])
    assert vc.link_group_id is None and ac.link_group_id is None
    assert index_link_groups(tl) == {}
    out = win.command_link_clips(clips=[
        {"track_id": "v0", "clip_index": 0},
        {"track_id": "a0", "clip_index": 0},
    ])
    assert vc.link_group_id == ac.link_group_id == out["link_group_id"]
    assert vc.link_group_id in index_link_groups(tl)


def _qt_env():
    from PySide6.QtWidgets import QApplication
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget

    if QApplication.instance() is None:
        QApplication([])
    tl, vtr, atr, vc, ac = _pair()
    task = {
        "timeline": tl, "duration": 8.0,
        "text_overlays": [], "image_overlays": [], "segments": [],
        "video": "x.mp4", "style": {},
    }
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    tlw = TimelineWidget()
    tlw.set_data(8000, task)
    tlw.setParent(win)
    win.timeline = tlw
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.show()
    tlw.resize(900, 300)
    return win, tlw, panel, tl, vtr, atr, vc, ac


def test_click_selects_linked_main_audio_and_alt_isolates():
    from PySide6.QtWidgets import QApplication
    win, tlw, panel, tl, vtr, atr, vc, ac = _qt_env()
    try:
        tlw._select_video_clip(vtr, vc)
        keys = set(tlw._clip_sel)
        assert ("v0", 0) in keys and ("a0", 0) in keys
        tlw._clip_group_begin(vc)
        assert tlw._clip_group_orig and tlw._clip_group_orig[0][1] is ac
        a0 = ac.start_t
        v0 = vc.start_t
        vc.start_t = v0 + 0.5
        tlw._clip_group_apply(0.5)
        assert ac.start_t == a0 + 0.5
        tlw._clip_group_apply(0.0)
        tlw._clip_group_orig = None
        vc.start_t = v0
        ac.start_t = a0

        tlw._select_video_clip(vtr, vc, linked=False)
        assert tlw._clip_sel == [("v0", 0)]
        tlw._clip_group_begin(vc)
        assert not tlw._clip_group_orig

        ac.start_t = 0.4
        off = ac.start_t - vc.start_t
        tlw._select_video_clip(vtr, vc)
        tlw._clip_group_begin(vc)
        vc.start_t = 0.3
        tlw._clip_group_apply(0.3)
        assert ac.start_t - vc.start_t == pytest.approx(off)

        tlw._select_video_clip(vtr, vc, linked=False)
        tlw._begin_av_linked_trim(vc, "right")
        assert not tlw._av_trim_partners

        panel.load_data("audio", ac)
        assert panel.audio_link_status.text() == "已链接"
        panel.load_data("video_clip", (vtr, vc))
        assert panel.vid_link_status.text() == "已链接"
        ac.link_group_id = None
        vc.link_group_id = None
        tlw._rebuild_av_link_index()
        panel.load_data("audio", ac)
        assert panel.audio_link_status.text() == "未链接"
    finally:
        win.close()
        QApplication.processEvents()


def test_audio_clip_menu_items():
    from cutora.ui.timeline.widget import TimelineWidget
    src = inspect.getsource(TimelineWidget._popup_audio_clip_menu)
    for label in ("解绑音视频", "重新绑定音视频", "淡化 0.3s", "清除淡化", "删除", "复制"):
        assert label in src
    assert "降噪" in src
    assert "变速" not in src


def test_commands_registered():
    from cutora.core.commands import get_registry
    names = {s.name for s in get_registry().list()}
    for n in ("timeline.trim_clips", "timeline.unlink_clips", "timeline.link_clips"):
        assert n in names
