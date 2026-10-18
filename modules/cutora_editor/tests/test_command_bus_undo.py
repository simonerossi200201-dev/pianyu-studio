# -*- coding: utf-8 -*-
"""True undo/redo for CommandBus (T13a-fix). Offscreen MainWindow + golden media.

Must run the GUI half in a subprocess so init_torch_runtime() precedes PySide6.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from cutora.core.history import HistoryManager
from cutora.subtitle.track_source import set_segments
VENV_PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")
G01_MP4 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def test_history_pop_last_only_at_tail():
    mgr = HistoryManager(max_steps=40)
    assert mgr.pop_last() is False
    mgr.push("a", {"n": 1})
    mgr.push("b", {"n": 2})
    assert mgr.current_index == len(mgr.history) - 1
    mgr.undo()
    assert mgr.current_index != len(mgr.history) - 1
    assert mgr.pop_last() is False
    mgr.redo()
    assert mgr.current_index == len(mgr.history) - 1
    assert mgr.pop_last() is True
    assert len(mgr.history) == 1
    assert mgr.current_index == 0
    assert mgr.pop_last() is True
    assert mgr.history == []
    assert mgr.current_index == -1


def test_history_undo_restores_current_then_steps_back():
    """BEFORE-push convention: undo restores the tail snapshot (pre-change)."""
    mgr = HistoryManager(max_steps=40)
    mgr.push("before-A", {"tracks": 1})
    mgr.set_after({"tracks": 2})
    state = mgr.undo()
    assert state == {"tracks": 1}
    assert mgr.current_index == -1
    state = mgr.redo()
    assert state == {"tracks": 2}
    assert mgr.current_index == 0


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def _pump(app, ms: int = 40) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _track_count(win) -> int:
    idx = getattr(win, "curr_idx", -1)
    tasks = getattr(win, "tasks", None) or []
    if idx < 0 or idx >= len(tasks):
        return 0
    tl = tasks[idx].get("timeline")
    tracks = getattr(tl, "tracks", None) if tl is not None else None
    return len(tracks or [])


def _hist_len(win) -> int:
    return len(win.history_mgr.history)


def run_undo_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_bus_undo_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import CommandError, get_bus

    app = QApplication.instance() or QApplication(["cutora-bus-undo"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    win = MainWindow()
    win.show()
    _pump(app, 80)
    win.show_editor()
    win._ensure_timeline_template()
    assert os.path.isfile(G01_MP4), G01_MP4
    win.on_drop([G01_MP4])
    _pump(app, 200)
    assert win.tasks, "golden media did not import"
    win.file_list.setCurrentRow(0)
    win.curr_idx = 0
    if win.file_list.item(0) is not None:
        win.on_click(win.file_list.item(0))
    _pump(app, 80)

    bus = get_bus()
    bus.bind_window(win)

    # timeline.add_track: one snapshot, undo, redo
    n = _hist_len(win)
    tracks_before = _track_count(win)
    bus.execute("timeline.add_track", {"type": "text"}, source="ui")
    _pump(app, 40)
    assert _hist_len(win) == n + 1, "history %s after add_track, expected %s" % (
        _hist_len(win), n + 1)
    assert _track_count(win) == tracks_before + 1
    win.undo()
    _pump(app, 40)
    assert _track_count(win) == tracks_before, "undo did not restore track count"
    win.redo()
    _pump(app, 40)
    assert _track_count(win) == tracks_before + 1, "redo did not restore track"

    # subtitle.set_text
    win.tasks[0].setdefault("segments", [])
    if not win.tasks[0]["segments"]:
        # Q29 起字幕的唯一真源是 subs 轨。直接给 task["segments"] 赋值会造出
        # 一个「segments 有内容、轨道是空的」的不一致状态，撤销后 bind_segments
        # 会把轨道补出来，序列化形状就和 before 对不上了。用写端 API。
        set_segments(win.tasks[0], [{"start": 0.0, "end": 1.0, "text": "old"}])
    n = _hist_len(win)
    bus.execute(
        "subtitle.set_text",
        {"seg_id": 0, "text": "new", "media_id": 0},
        source="ui",
    )
    _pump(app, 40)
    assert _hist_len(win) == n + 1
    assert win.tasks[0]["segments"][0]["text"] == "new"
    win.undo()
    _pump(app, 40)
    assert win.tasks[0]["segments"][0]["text"] == "old"
    win.redo()
    _pump(app, 40)
    assert win.tasks[0]["segments"][0]["text"] == "new"

    # media.select is not undoable
    n = _hist_len(win)
    bus.execute("media.select", {"ids": [0]}, source="ui")
    _pump(app, 40)
    assert _hist_len(win) == n, "media.select must not push history"

    # app.theme.set is undoable=False
    n = _hist_len(win)
    bus.execute("app.theme.set", {"theme_id": "roma-gold"}, source="ui")
    _pump(app, 40)
    assert _hist_len(win) == n, "app.theme.set must not push history"

    # failed command must not leave a snapshot
    n = _hist_len(win)
    with pytest.raises(CommandError):
        bus.execute(
            "timeline.remove_track",
            {"track_id": "does-not-exist"},
            source="ui",
        )
    assert _hist_len(win) == n, "failed command left a history entry"

    from cutora.model.project_io import serialize_render_task

    def _task_json():
        return json.dumps(
            serialize_render_task(win.tasks[win.curr_idx]),
            sort_keys=True, default=str)

    def _first_video():
        tl = win.tasks[win.curr_idx].get("timeline")
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "video":
                continue
            clips = getattr(tr, "clips", []) or []
            if clips:
                return tr, 0, clips[0]
        return None, None, None

    tr, ci, clip = _first_video()
    assert tr is not None and clip is not None, "imported media has no video clip"
    before = _task_json()
    bus.execute("timeline.trim_clip", {
        "track_id": tr.id, "clip_index": ci,
        "in_t": float(clip.in_t) + 0.2,
        "out_t": float(clip.out_t),
        "start_t": float(clip.start_t) + 0.2,
        "speed": float(clip.speed),
    }, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "trim_clip undo JSON mismatch"

    tr, ci, clip = _first_video()
    before = _task_json()
    bus.execute("timeline.move_clips", {
        "clips": [{
            "track_id": tr.id, "clip_index": ci,
            "start": float(clip.start_t) + 1.5,
        }],
    }, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "move_clips undo JSON mismatch"

    before = _task_json()
    bus.execute("timeline.add_track", {"type": "video"}, source="ui")
    _pump(app, 40)
    dest = None
    src = None
    src_i = 0
    tl = win.tasks[win.curr_idx].get("timeline")
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "video":
            continue
        clips = getattr(tr, "clips", []) or []
        if clips and src is None:
            src, src_i = tr, 0
        elif not clips and dest is None:
            dest = tr
    assert src is not None and dest is not None
    bus.execute("timeline.move_clip_to_track", {
        "track_id": src.id, "clip_index": src_i,
        "dest_track_id": dest.id,
        "start": float(src.clips[src_i].start_t),
    }, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "move_clip_to_track undo JSON mismatch"

    before = _task_json()
    with pytest.raises(CommandError):
        bus.execute("timeline.ripple_intro", {
            "in_t": 0.0, "out_t": 1.0, "start_t": 0.0,
        }, source="ui")
    assert _task_json() == before, "ripple_intro without intro mutated the project"

    set_segments(win.tasks[win.curr_idx], [
        {
            "start": 0.0, "end": 1.0, "text": "one",
            "words": [{"word": "one", "start": 0.0, "end": 1.0}],
        },
        {
            "start": 1.0, "end": 2.0, "text": "two",
            "words": [{"word": "two", "start": 1.0, "end": 2.0}],
        },
    ])
    before = _task_json()
    bus.execute("subtitle.trim_segment", {
        "seg_id": 0, "start": 0.0, "end": 1.5,
    }, source="ui")
    _pump(app, 40)
    assert abs(float(win.tasks[win.curr_idx]["segments"][0]["end"]) - 1.5) < 1e-6
    assert abs(float(
        win.tasks[win.curr_idx]["segments"][0]["words"][0]["end"]) - 1.5) < 1e-6
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "trim_segment undo JSON mismatch"

    before = _task_json()
    bus.execute("subtitle.move_segments", {
        "segments": [{"seg_id": 1, "start": 3.0}],
    }, source="ui")
    _pump(app, 40)
    assert abs(float(win.tasks[win.curr_idx]["segments"][1]["start"]) - 3.0) < 1e-6
    assert abs(float(
        win.tasks[win.curr_idx]["segments"][1]["words"][0]["start"]) - 3.0) < 1e-6
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "move_segments undo JSON mismatch"

    before = _task_json()
    bus.execute("subtitle.retime_segments", {
        "seg_ids": [0], "pivot": 0.0, "scale": 2.0,
    }, source="ui")
    _pump(app, 40)
    assert abs(float(win.tasks[win.curr_idx]["segments"][0]["end"]) - 2.0) < 1e-6
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "retime_segments undo JSON mismatch"

    before = _task_json()
    bus.execute("subtitle.delete_segments", {"seg_ids": [1, 0]}, source="ui")
    _pump(app, 40)
    assert win.tasks[win.curr_idx]["segments"] == []
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "delete_segments undo JSON mismatch"

    n = _hist_len(win)
    sel = win.timeline.selection
    for i in range(10):
        sel.set_segments([i % 2])
        sel.toggle_clip("t", i % 3)
    assert _hist_len(win) == n, "selection must not push undo"

    before = _task_json()
    bus.execute("subtitle.duplicate_segments", {
        "seg_ids": [0], "time": 8.0,
    }, source="ui")
    _pump(app, 40)
    assert len(win.tasks[win.curr_idx]["segments"]) >= 3
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "duplicate_segments undo JSON mismatch"

    before = _task_json()
    added = bus.execute("marker.add", {"t": 3.5, "name": "副歌"}, source="ui")
    _pump(app, 40)
    mid = added.get("id")
    assert mid
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "marker.add undo JSON mismatch"

    bus.execute("marker.add", {"t": 3.5, "name": "副歌"}, source="ui")
    _pump(app, 40)
    mid = (win.tasks[win.curr_idx].get("markers") or [])[0]["id"]
    before = _task_json()
    bus.execute("marker.rename", {"id": mid, "name": "间奏"}, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "marker.rename undo JSON mismatch"

    before = _task_json()
    bus.execute("marker.move", {"id": mid, "t": 8.0}, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "marker.move undo JSON mismatch"

    before = _task_json()
    bus.execute("marker.set_color", {"id": mid, "color": "#112233"}, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "marker.set_color undo JSON mismatch"

    before = _task_json()
    bus.execute("marker.remove", {"ids": [mid]}, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "marker.remove undo JSON mismatch"

    tl = win.tasks[win.curr_idx].get("timeline")
    vtr = None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video" and getattr(tr, "clips", None):
            vtr = tr
            break
    before = _task_json()
    bus.execute("timeline.set_in_point", {"t": 1.25}, source="ui")
    _pump(app, 40)
    bus.execute("timeline.set_out_point", {"t": 4.5}, source="ui")
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    win.undo()
    _pump(app, 40)
    assert _task_json() == before, "I/O points undo JSON mismatch"
    assert win.tasks[win.curr_idx].get("in_point") is None

    if vtr is not None and len(vtr.clips) >= 1:
        before = _task_json()
        bus.execute("timeline.ripple_insert", {
            "t": 0.5, "duration": 1.0, "track_id": vtr.id,
        }, source="ui")
        _pump(app, 40)
        win.undo()
        _pump(app, 40)
        assert _task_json() == before, "ripple_insert undo JSON mismatch"
        before = _task_json()
        n_hist = len(win.history_mgr.history)
        bus.execute("timeline.close_gaps", {"track_id": vtr.id}, source="ui")
        _pump(app, 40)
        # Q161：没有间隙可闭合 = 什么都没改，这一步不进历史；这时 Ctrl+Z 撤的是更早那一步，所以只在记了历史时才撤
        if len(win.history_mgr.history) > n_hist:
            win.undo()
            _pump(app, 40)
        assert _task_json() == before, "close_gaps undo JSON mismatch"

    if vtr is not None and len(vtr.clips) >= 1:
        vid = getattr(vtr, "id", None)
        tl = win.tasks[win.curr_idx].get("timeline")
        live = None
        for tr in getattr(tl, "tracks", []) or []:
            if str(getattr(tr, "id", "")) == str(vid):
                live = tr
                break
        if live is not None:
            vtr = live
        task = win.tasks[win.curr_idx]
        clip0 = vtr.clips[0]
        setattr(clip0, "is_intro_video_leadin", True)
        task["_intro_video_state"] = {
            "enabled": True,
            "intro_path": G01_MP4,
            "effective_duration": float(
                getattr(clip0, "effective_duration", 1.0) or 1.0),
        }
        bus.execute("timeline.set_track_locked", {
            "track_id": vtr.id, "on": True,
        }, source="ui")
        _pump(app, 40)
        before = _task_json()
        lock_cmds = [
            ("timeline.trim_clip", {
                "track_id": vtr.id, "clip_index": 0,
                "in_t": 0.0, "out_t": 1.0, "start_t": 0.0,
            }),
            ("timeline.move_clips", {
                "clips": [{"track_id": vtr.id, "clip_index": 0, "start": 1.0}],
            }),
            ("timeline.delete_clip", {"track_id": vtr.id, "clip_index": 0}),
            ("timeline.duplicate_clips", {
                "clips": [{"track_id": vtr.id, "clip_index": 0}],
                "time": 2.0,
            }),
            ("timeline.split_at", {"time": 0.5}),
            ("overlay.split", {"kind": "image", "idx": 0, "t": 0.5}),
            ("timeline.razor", {"time": 0.5, "clips": [
                {"track_id": vtr.id, "clip_index": 0}]}),
            ("timeline.ripple_insert", {
                "t": 0.2, "duration": 0.4, "track_id": vtr.id,
            }),
            ("timeline.close_gaps", {"track_id": vtr.id}),
            ("timeline.ripple_delete", {
                "targets": [{"kind": "clip", "track_id": vtr.id, "clip_index": 0}],
            }),
            ("timeline.move_clip_to_track", {
                "track_id": vtr.id, "clip_index": 0,
                "dest_track_id": vtr.id, "start": 0.0,
            }),
            ("timeline.ripple_intro", {
                "in_t": 0.0, "out_t": 1.0, "start_t": 0.0,
            }),
        ]
        ran = set()
        for name, params in lock_cmds:
            with pytest.raises(CommandError) as exc:
                bus.execute(name, params, source="ui")
            assert "锁定" in str(exc.value), name
            assert _task_json() == before, name
            ran.add(name)
        missing = {n for n, _ in lock_cmds} - ran
        assert not missing, missing
        bus.execute("timeline.set_track_locked", {
            "track_id": vtr.id, "on": False,
        }, source="ui")
        _pump(app, 40)

        task = win.tasks[win.curr_idx]
        task["text_overlays"] = [
            {"start": 0.0, "end": 2.0, "content": "hi", "type": "text"}]
        task["image_overlays"] = [
            {"start": 0.0, "end": 2.0, "type": "image", "elements": []}]
        # Subs track is the subtitle source. Writing task["segments"] alone
        # leaves the track on the old cues; undo then on_click lets the track win.
        set_segments(task, [{
            "start": 0.0, "end": 2.0, "text": "hello world",
            "words": [
                {"word": "hello", "start": 0.0, "end": 1.0},
                {"word": "world", "start": 1.0, "end": 2.0},
            ],
        }])
        audio_ref = None
        tl = task.get("timeline")
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) == "audio" and getattr(tr, "clips", None):
                audio_ref = {"track_id": tr.id, "clip_index": 0}
                break
        razor_clips = [{"track_id": vtr.id, "clip_index": 0}]
        if audio_ref:
            razor_clips.append(audio_ref)
        before = _task_json()
        bus.execute("timeline.razor", {
            "time": 1.0,
            "clips": razor_clips,
            "overlays": [
                {"kind": "text", "idx": 0},
                {"kind": "image", "idx": 0},
            ],
            "subtitles": [{"idx": 0}],
        }, source="ui")
        _pump(app, 40)
        assert _task_json() != before, "razor did not change the project"
        win.undo()
        _pump(app, 40)
        assert _task_json() == before, "razor undo JSON mismatch"

        dest = None
        bus.execute("timeline.add_track", {"type": "video"}, source="ui")
        _pump(app, 40)
        tl = win.tasks[win.curr_idx].get("timeline")
        for tr in getattr(tl, "tracks", []) or []:
            if (getattr(tr, "type", None) == "video"
                    and str(getattr(tr, "id", "")) != str(vtr.id)):
                dest = tr
                break
        assert dest is not None, "need a second video track"
        bus.execute("timeline.set_track_locked", {
            "track_id": dest.id, "on": True,
        }, source="ui")
        _pump(app, 40)
        before = _task_json()
        with pytest.raises(CommandError) as exc:
            bus.execute("timeline.move_clip_to_track", {
                "track_id": vtr.id, "clip_index": 0,
                "dest_track_id": dest.id, "start": 0.0,
            }, source="ui")
        assert "锁定" in str(exc.value)
        assert _task_json() == before, "move to locked dest mutated project"
        bus.execute("timeline.set_track_locked", {
            "track_id": dest.id, "on": False,
        }, source="ui")
        _pump(app, 40)

        before = _task_json()
        bus.execute("timeline.set_track_mute", {
            "track_id": vtr.id, "on": True,
        }, source="ui")
        _pump(app, 40)
        win.undo()
        _pump(app, 40)
        assert _task_json() == before, "set_track_mute undo JSON mismatch"
        before = _task_json()
        bus.execute("timeline.set_track_locked", {
            "track_id": vtr.id, "on": True,
        }, source="ui")
        _pump(app, 40)
        win.undo()
        _pump(app, 40)
        assert _task_json() == before, "set_track_locked undo JSON mismatch"

        subs = None
        tl = win.tasks[win.curr_idx].get("timeline")
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) == "subs":
                subs = tr
                break
        segs = win.tasks[win.curr_idx].get("segments") or []
        if subs is None:
            bus.execute("timeline.add_track", {"type": "subs"}, source="ui")
            _pump(app, 40)
            tl = win.tasks[win.curr_idx].get("timeline")
            for tr in getattr(tl, "tracks", []) or []:
                if getattr(tr, "type", None) == "subs":
                    subs = tr
                    break
        if not segs:
            segs = set_segments(
                win.tasks[win.curr_idx],
                [{"start": 0.0, "end": 1.0, "text": "one"}])
        assert subs is not None, "subs track missing; lock tests would be skipped"
        assert segs, "segments missing; lock tests would be skipped"
        bus.execute("timeline.set_track_locked", {
            "track_id": vtr.id, "on": False,
        }, source="ui")
        _pump(app, 40)
        bus.execute("timeline.set_track_locked", {
            "track_id": subs.id, "on": True,
        }, source="ui")
        _pump(app, 40)
        before = _task_json()
        with pytest.raises(CommandError) as exc:
            bus.execute("timeline.ripple_delete", {
                "targets": [{"kind": "clip", "track_id": vtr.id, "clip_index": 0}],
            }, source="ui")
        assert "锁定" in str(exc.value)
        assert _task_json() == before, "ripple onto locked subs mutated project"
        with pytest.raises(CommandError) as exc:
            bus.execute("timeline.ripple_insert", {
                "t": 0.2, "duration": 0.4, "track_id": vtr.id,
            }, source="ui")
        assert "锁定" in str(exc.value)
        assert _task_json() == before, "ripple_insert onto locked subs mutated project"
        with pytest.raises(CommandError) as exc:
            bus.execute("timeline.close_gaps", {"track_id": vtr.id}, source="ui")
        assert "锁定" in str(exc.value)
        assert _task_json() == before, "close_gaps onto locked subs mutated project"
        sub_cmds = [
            ("subtitle.trim_segment", {
                "seg_id": 0, "start": 0.0, "end": 0.4,
            }),
            ("subtitle.move_segments", {
                "segments": [{"seg_id": 0, "start": 1.0}],
            }),
            ("subtitle.retime_segments", {
                "seg_ids": [0], "pivot": 0.0, "scale": 1.1,
            }),
            ("subtitle.delete_segments", {"seg_ids": [0]}),
            ("subtitle.duplicate_segments", {
                "seg_ids": [0], "time": 3.0,
            }),
        ]
        ran = set()
        for name, params in sub_cmds:
            with pytest.raises(CommandError) as exc:
                bus.execute(name, params, source="ui")
            assert "锁定" in str(exc.value), name
            assert _task_json() == before, name
            ran.add(name)
        missing = {n for n, _ in sub_cmds} - ran
        assert not missing, missing
        bus.execute("timeline.set_track_locked", {
            "track_id": subs.id, "on": False,
        }, source="ui")
        _pump(app, 40)

    print("UNDO_OK", flush=True)
    try:
        win.media_player.stop()
        win.close()
    except Exception:
        pass
    os._exit(0)


def test_command_bus_undo_offscreen():
    proc = subprocess.run(
        [VENV_PY, os.path.join(REPO, "tests", "test_command_bus_undo.py")],
        cwd=REPO,
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "UNDO_OK" in (proc.stdout or ""), out


if __name__ == "__main__":
    try:
        raise SystemExit(run_undo_suite())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
