# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q115 · 加轨道快捷键 + 素材直接拖进时间线。

改之前每条为什么红，写在各自的 docstring 里。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QMimeData, QPointF, QUrl
from PySide6.QtWidgets import QApplication

from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Track


def _app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 快捷键

def test_alt_keys_are_registered():
    """改之前：快捷键表里根本没有加轨道这一组。"""
    from cutora.ui.shortcuts import shortcut_key
    assert shortcut_key("track_add_video") == "Alt+V"
    assert shortcut_key("track_add_audio") == "Alt+A"
    assert shortcut_key("track_add_text") == "Alt+T"
    assert shortcut_key("track_add_image") == "Alt+I"


def test_alt_keys_are_not_bound_twice():
    """同一个键既给 QAction 又 QShortcut 会变成歧义快捷键，两边都不响应。"""
    import inspect
    from cutora.ui import shortcuts
    src = inspect.getsource(shortcuts.install_shortcuts)
    for sid in ("track_add_video", "track_add_audio",
                "track_add_text", "track_add_image"):
        assert '"%s"' % sid in src, "%s 必须在 skip 里" % sid


def test_alt_keys_do_not_collide():
    from cutora.ui.shortcuts import SHORTCUTS
    keys = [k for _sid, k, _l, _s in SHORTCUTS]
    assert len(keys) == len(set(keys)), "快捷键有重复：%s" % (
        sorted(k for k in keys if keys.count(k) > 1),)


def test_shortcut_add_track_reports_the_new_track_name():
    """改之前：MainWindow 上根本没有 shortcut_add_track。"""
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    tl.add_track(Track("video"))
    task = {"timeline": tl}
    msgs = []

    class Host:
        curr_idx = 0
        tasks = [task]
        timeline = SimpleNamespace(update=lambda: None)

        def statusBar(self):
            return SimpleNamespace(showMessage=lambda m, *a: msgs.append(m))

        def add_timeline_track(self, t):
            return MainWindow.add_timeline_track(self, t)

        def push_undo(self, *a, **k):
            return None

        def _ensure_task_for_overlay(self):
            return task

    host = Host()
    track = MainWindow.shortcut_add_track(host, "video")
    assert track is not None and track.type == "video"
    assert len(tl.tracks) == 2
    assert msgs and "视频轨道 2" in msgs[-1]


# ------------------------------------------------------------ 认素材类型

def test_kind_for_path():
    from cutora.ui.timeline_drop import kind_for_path
    assert kind_for_path("a.mp4") == "video"
    assert kind_for_path("a.MOV") == "video"
    assert kind_for_path("a.wav") == "audio"
    assert kind_for_path("a.png") == "image"
    assert kind_for_path("a.gif") == "image"
    assert kind_for_path("a.txt") == "text"
    assert kind_for_path("a.srt") == "subs"
    assert kind_for_path("a.ass") == "subs"
    assert kind_for_path("a.exe") is None
    assert kind_for_path("") is None


def test_text_and_subtitle_exts_are_droppable():
    """改之前：_drop_supported_exts 只有视频/音频/图片，文本和字幕连拖都拖不进来。"""
    from cutora.ui.timeline_drop import supported_exts
    exts = supported_exts()
    for e in (".mp4", ".wav", ".png", ".txt", ".srt", ".ass", ".vtt"):
        assert e in exts


def test_sort_for_drop_is_natural():
    from cutora.ui.timeline_drop import sort_for_drop
    assert sort_for_drop(["b10.mp4", "b2.mp4", "a1.mp4"]) == [
        "a1.mp4", "b2.mp4", "b10.mp4"]


def test_accepts_matches_track_type():
    from cutora.ui.timeline_drop import accepts
    v, a = Track("video"), Track("audio")
    assert accepts(v, "video") and not accepts(v, "audio")
    assert accepts(a, "audio") and not accepts(a, "video")
    assert not accepts(None, "video")


# -------------------------------------------------------- 落点轨道（老 bug）

class _FakeMime:
    def __init__(self, paths):
        self._md = QMimeData()
        self._md.setUrls([QUrl.fromLocalFile(p) for p in paths])

    def hasUrls(self):
        return self._md.hasUrls()

    def hasFormat(self, fmt):
        return False

    def urls(self):
        return self._md.urls()


class _FakeDrop:
    def __init__(self, paths, x, y):
        self._mime = _FakeMime(paths)
        self._pos = QPointF(x, y)
        self.accepted = False
        self.ignored = False

    def mimeData(self):
        return self._mime

    def position(self):
        return self._pos

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def _timeline_widget(rows, sep_y=400.0):
    from cutora.ui.timeline.widget import TimelineWidget
    _app()
    w = TimelineWidget()
    w.pixels_per_second = 10.0
    w.scroll_x = 0
    w._track_positions = lambda: {"rows": rows, "domain_sep_y": sep_y}
    w._status = lambda *a, **k: None
    return w


def test_drop_payload_carries_the_track_under_the_cursor(tmp_path):
    """老 bug：_last_drop_target_track 是在 emit 之后才赋值的，

    handler 拿到的永远是上一次的落点（第一次是 None），所以「拖到第 2 条
    视频轨」从来没生效过，素材总是落回第一条。
    """
    v1, v2 = Track("video"), Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0), (v2, 60.0, 60.0)])
    got = []
    w.files_dropped_on_timeline.connect(got.append)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    ev = _FakeDrop([str(f)], x=100.0, y=80.0)   # y=80 → 第二条轨
    w.dropEvent(ev)
    assert ev.accepted and got, "没有发出批量拖放信号"
    assert got[0]["track"] is v2
    assert got[0]["new_track"] is False
    # Windows 上 QUrl.toLocalFile() 给的是正斜杠（D:/x/a.mp4），和工程里存的写法一致；
    # 比较前统一成同一种写法，别因为斜杠方向判错
    _norm = lambda p: os.path.normcase(os.path.normpath(p))  # noqa: E731
    assert [_norm(p) for p in got[0]["paths"]] == [_norm(str(f))]
    assert got[0]["time"] == pytest.approx(10.0)


def test_drop_below_all_tracks_asks_for_a_new_one(tmp_path):
    """改之前：落在空白处也只会往已有轨道上塞。"""
    v1 = Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0)])
    got = []
    w.files_dropped_on_timeline.connect(got.append)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    w.dropEvent(_FakeDrop([str(f)], x=0.0, y=300.0))
    assert got[0]["new_track"] is True
    assert got[0]["track"] is None


def test_drop_sorts_a_batch_and_skips_junk(tmp_path):
    v1 = Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0)])
    got = []
    w.files_dropped_on_timeline.connect(got.append)
    names = ["b10.mp4", "b2.mp4", "readme.exe"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    w.dropEvent(_FakeDrop([str(tmp_path / n) for n in names], 0.0, 10.0))
    assert [os.path.basename(p) for p in got[0]["paths"]] == ["b2.mp4", "b10.mp4"]


def test_drop_of_only_junk_is_refused(tmp_path):
    v1 = Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0)])
    got = []
    w.files_dropped_on_timeline.connect(got.append)
    junk = tmp_path / "setup.exe"
    junk.write_bytes(b"x")
    ev = _FakeDrop([str(junk)], 0.0, 10.0)
    w.dropEvent(ev)
    assert not got and ev.ignored and not ev.accepted


# --------------------------------------------------------------- 批量摆放

def _batch_host(tl, task, durations):
    from cutora.ui.main_window import MainWindow

    class Host:
        curr_idx = 0
        tasks = [task]
        _timeline_template = {"width": 720, "height": 1280, "fps": 30.0}
        timeline = SimpleNamespace(
            duration=10000, position=0, _last_drop_target_track=None,
            set_data=lambda *a, **k: None, update=lambda: None)
        video_canvas = SimpleNamespace(update=lambda: None)
        calls = []

        def push_undo(self, *a, **k):
            self.calls.append(("undo",))

        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

        def _ensure_task_for_overlay(self):
            return task

        def add_timeline_track(self, t):
            return MainWindow.add_timeline_track(self, t)

        def _handle_timeline_drop(self, path, time_sec, domain,
                                  push_undo=True, target_track=None):
            self.calls.append(("drop", os.path.basename(path), round(time_sec, 3),
                               target_track, push_undo))
            return durations.get(os.path.basename(path), 0.0)

    return Host()


def test_batch_lays_files_out_back_to_back(tmp_path):
    """改之前：一次拖 3 个视频，三条全部堆在同一秒上互相盖住。"""
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    v = Track("video")
    tl.add_track(v)
    task = {"timeline": tl}
    for n in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / n).write_bytes(b"x")
    host = _batch_host(tl, task, {"a.mp4": 2.0, "b.mp4": 3.0, "c.mp4": 1.0})
    MainWindow._handle_timeline_drop_batch(host, {
        "paths": [str(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4")],
        "time": 1.0, "domain": "visual", "track": v, "new_track": False,
    })
    drops = [c for c in host.calls if c[0] == "drop"]
    assert [d[1] for d in drops] == ["a.mp4", "b.mp4", "c.mp4"]
    assert [d[2] for d in drops] == [1.0, 3.0, 6.0]
    assert all(d[3] is v for d in drops), "落点轨道没传下去"


def test_batch_pushes_undo_once(tmp_path):
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    v = Track("video")
    tl.add_track(v)
    task = {"timeline": tl}
    for n in ("a.mp4", "b.mp4"):
        (tmp_path / n).write_bytes(b"x")
    host = _batch_host(tl, task, {"a.mp4": 1.0, "b.mp4": 1.0})
    MainWindow._handle_timeline_drop_batch(host, {
        "paths": [str(tmp_path / n) for n in ("a.mp4", "b.mp4")],
        "time": 0.0, "domain": "visual", "track": v, "new_track": False,
    })
    assert [c for c in host.calls if c[0] == "undo"] == [("undo",)]
    assert all(c[4] is False for c in host.calls if c[0] == "drop")


def test_batch_opens_one_new_track_for_the_whole_batch(tmp_path):
    """落在空白处：两条视频进同一条新轨，不是一个文件开一条。"""
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    tl.add_track(Track("video"))
    task = {"timeline": tl}
    for n in ("a.mp4", "b.mp4"):
        (tmp_path / n).write_bytes(b"x")
    host = _batch_host(tl, task, {"a.mp4": 1.0, "b.mp4": 1.0})
    MainWindow._handle_timeline_drop_batch(host, {
        "paths": [str(tmp_path / n) for n in ("a.mp4", "b.mp4")],
        "time": 0.0, "domain": "visual", "track": None, "new_track": True,
    })
    assert len(tl.tracks) == 2, "应该只新开一条轨道"
    drops = [c for c in host.calls if c[0] == "drop"]
    assert drops[0][3] is drops[1][3] is tl.tracks[-1]


def test_batch_ignores_files_that_are_gone(tmp_path):
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    v = Track("video")
    tl.add_track(v)
    task = {"timeline": tl}
    (tmp_path / "there.mp4").write_bytes(b"x")
    host = _batch_host(tl, task, {"there.mp4": 1.0})
    MainWindow._handle_timeline_drop_batch(host, {
        "paths": [str(tmp_path / "gone.mp4"), str(tmp_path / "there.mp4")],
        "time": 0.0, "domain": "visual", "track": v, "new_track": False,
    })
    drops = [c for c in host.calls if c[0] == "drop"]
    assert [d[1] for d in drops] == ["there.mp4"]


# ------------------------------------------------------------- 文本 / 字幕

def test_dropping_a_txt_makes_a_text_overlay(tmp_path):
    """改之前：.txt 拖进来什么都不会发生。"""
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    task = {"timeline": tl}
    f = tmp_path / "words.txt"
    f.write_text("愿你平安\n第二行", encoding="utf-8")

    class Host:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

    placed = MainWindow._drop_text_file(Host(), task, tl, str(f), 2.0)
    assert placed == pytest.approx(5.0)
    ovs = task["text_overlays"]
    assert len(ovs) == 1
    assert ovs[0]["content"] == "愿你平安\n第二行"
    assert ovs[0]["start"] == pytest.approx(2.0)
    text_tracks = [t for t in tl.tracks if t.type == "text"]
    assert len(text_tracks) == 1
    assert ovs[0]["_track_id"] == text_tracks[0].id


def test_dropping_a_txt_honours_the_target_text_track(tmp_path):
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    t1, t2 = Track("text"), Track("text")
    tl.add_track(t1)
    tl.add_track(t2)
    task = {"timeline": tl}
    f = tmp_path / "w.txt"
    f.write_text("hi", encoding="utf-8")

    class Host:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

    MainWindow._drop_text_file(Host(), task, tl, str(f), 0.0, target_track=t2)
    assert task["text_overlays"][0]["_track_id"] == t2.id


def test_empty_txt_is_reported_not_silently_dropped(tmp_path):
    from cutora.ui.main_window import MainWindow
    tl = Timeline(720, 1280, 30.0)
    task = {"timeline": tl}
    f = tmp_path / "empty.txt"
    f.write_text("   \n", encoding="utf-8")
    msgs = []

    class Host:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda m, *a: msgs.append(m))

    assert MainWindow._drop_text_file(Host(), task, tl, str(f), 0.0) == 0.0
    assert not task.get("text_overlays")
    assert msgs and "读不出来" in msgs[-1]


def test_dropping_a_srt_appends_instead_of_replacing(tmp_path):
    """字幕拖进来绝不能覆盖已有的字幕。"""
    from cutora.ui.main_window import MainWindow
    calls = []

    class Host:
        curr_idx = 3

        class bus:
            @staticmethod
            def execute(name, params, source=None):
                calls.append((name, params, source))

        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

    f = tmp_path / "x.srt"
    f.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    MainWindow._drop_subtitle_file(Host(), str(f))
    assert calls and calls[0][0] == "subtitle.import_file"
    assert calls[0][1]["mode"] == "append"
    assert calls[0][1]["media_id"] == 3


# ------------------------------------------------------------- 存盘往返

def test_overlay_track_binding_survives_a_save(tmp_path):
    """老 bug：_track_id 被当成运行时缓存丢掉，

    存完工程重开，放在第 2 条文本轨上的字会掉回第 1 条。
    """
    from cutora.model.project_io import deserialize_render_task, serialize_render_task
    tl = Timeline(720, 1280, 30.0)
    t1, t2 = Track("text"), Track("text")
    tl.add_track(t1)
    tl.add_track(t2)
    task = {
        "timeline": tl,
        "text_overlays": [{"type": "text", "content": "hi", "start": 0.0,
                           "end": 2.0, "_track_id": t2.id}],
    }
    back = deserialize_render_task(serialize_render_task(task))
    assert back["text_overlays"][0]["_track_id"] == t2.id
    ids = [t.id for t in back["timeline"].tracks]
    assert t2.id in ids


def test_runtime_caches_still_stay_out_of_the_file():
    from cutora.model.project_io import serialize_render_task
    tl = Timeline(720, 1280, 30.0)
    task = {"timeline": tl,
            "text_overlays": [{"content": "hi", "_rich_cache": {"x": 1},
                               "_clip_obj": object()}]}
    out = serialize_render_task(task)
    ov = out["text_overlays"][0]
    assert "_rich_cache" not in ov and "_clip_obj" not in ov


def test_dropped_video_clip_survives_a_save(tmp_path):
    """拖进来的视频必须能存进工程再读回来（轨道 / 片段 / 源都在）。"""
    from cutora.model.project_io import deserialize_render_task, serialize_render_task
    from cutora.model.timeline_model import Clip, MediaSource
    tl = Timeline(720, 1280, 30.0)
    tr = Track("video")
    tl.add_track(tr)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    src = MediaSource(str(f))
    src.duration = 4.0
    src.has_audio = False
    tl.sources[src.id] = src
    tr.clips.append(Clip(src.id, 0.0, 4.0, 1.5))
    task = {"timeline": tl}
    back = deserialize_render_task(serialize_render_task(task))
    tracks = [t for t in back["timeline"].tracks if t.type == "video"]
    assert len(tracks) == 1 and len(tracks[0].clips) == 1
    clip = tracks[0].clips[0]
    assert clip.start_t == pytest.approx(1.5)
    assert clip.out_t == pytest.approx(4.0)
    got = back["timeline"].sources[clip.source_id]
    got_path = got.get("path") if isinstance(got, dict) else got.path
    assert got_path == str(f)


# ---------------------------------------------------------- 落点提示 / 轨道头

def test_drop_hint_follows_the_cursor_and_clears(tmp_path):
    """拖进来时要看得见落点：竖线 + 目标轨高亮，松手/离开要收掉。"""
    v1, v2 = Track("video"), Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0), (v2, 60.0, 60.0)])
    assert getattr(w, "_drop_hint", None) is None
    w._set_drop_hint(120.0, 80.0)
    assert w._drop_hint[0] == 120.0
    assert w._drop_hint[1] == v2.id
    assert w._drop_hint[2] is False
    w._set_drop_hint(120.0, 300.0)        # 空白处
    assert w._drop_hint[2] is True
    w._clear_drop_hint()
    assert w._drop_hint is None


def test_painting_the_drop_hint_does_not_raise():
    from PySide6.QtGui import QPainter, QPixmap
    v1 = Track("video")
    w = _timeline_widget([(v1, 0.0, 60.0)])
    pm = QPixmap(200, 200)
    painter = QPainter(pm)
    try:
        w._set_drop_hint(50.0, 10.0)
        w._paint_drop_hint(painter)
        w._set_drop_hint(50.0, 150.0)
        w._paint_drop_hint(painter)
        w._clear_drop_hint()
        w._paint_drop_hint(painter)       # 没提示时什么都不画
    finally:
        painter.end()


def test_track_header_drop_targets_that_row(tmp_path):
    """改之前：轨道头根本不收拖放，只能点 + 号走文件对话框。"""
    from cutora.ui.timeline.header import TrackHeaderPanel
    _app()
    v1, v2 = Track("video"), Track("video")
    tl_widget = _timeline_widget([(v1, 0.0, 60.0), (v2, 60.0, 60.0)])
    tl_widget.position = 3000
    panel = TrackHeaderPanel(tl_widget)
    got = []
    panel.window = lambda: SimpleNamespace(
        _handle_timeline_drop_batch=got.append)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    ev = _FakeDrop([str(f)], x=10.0, y=80.0)
    panel.dropEvent(ev)
    assert ev.accepted and got
    assert got[0]["track"] is v2
    assert got[0]["time"] == pytest.approx(3.0)   # 播放头位置
    assert got[0]["new_track"] is False


def test_track_header_highlights_the_row_under_the_cursor():
    from cutora.ui.timeline.header import TrackHeaderPanel
    _app()
    v1, v2 = Track("video"), Track("video")
    tl_widget = _timeline_widget([(v1, 0.0, 60.0), (v2, 60.0, 60.0)])
    panel = TrackHeaderPanel(tl_widget)
    assert panel._drop_row_id is None
    panel._set_drop_row(v2)
    assert panel._drop_row_id == v2.id
    panel._set_drop_row(None)
    assert panel._drop_row_id is None


def test_window_level_drop_goes_to_the_media_library(tmp_path):
    """丢在时间线以外的地方 = 导进媒体库（以前整窗根本不收拖放）。"""
    from cutora.ui.main_window import MainWindow
    got = []

    class Host:
        on_drop = staticmethod(lambda paths: got.append(list(paths)))

        def _drop_paths_from(self, mime):
            return MainWindow._drop_paths_from(self, mime)

    for n in ("b2.mp4", "a1.mp4", "junk.exe"):
        (tmp_path / n).write_bytes(b"x")
    ev = _FakeDrop([str(tmp_path / n) for n in ("b2.mp4", "a1.mp4", "junk.exe")],
                   0.0, 0.0)
    MainWindow.dropEvent(Host(), ev)
    assert ev.accepted
    assert [os.path.basename(p) for p in got[0]] == ["a1.mp4", "b2.mp4"]
