# -*- coding: utf-8 -*-
"""Q82: GUI save/open keeps every timeline field the render serializer keeps."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q82-persist"])


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None
    win._begin_open_progress = lambda *a, **k: None
    win._tick_open_progress = lambda *a, **k: None
    win._end_open_progress = lambda *a, **k: None
    win._ui_warning = lambda *a, **k: None


def _field(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key)


def _rich_task(mov_path, proxy_path):
    """One transparent mov, one looping still, one muted audio track."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    tl = Timeline(320, 180, 30.0)
    mov = MediaSource.__new__(MediaSource)
    mov.id = "q82mov"
    mov.path = mov_path
    mov.duration = 2.0
    mov.has_video = True
    mov.has_audio = False
    mov.alpha = True
    mov.alpha_mode = "premultiplied"
    mov.preview_path = proxy_path
    tl.sources[mov.id] = mov

    gif = MediaSource.__new__(MediaSource)
    gif.id = "q82gif"
    gif.path = mov_path + ".gif.mov"
    gif.duration = 0.4
    gif.has_video = True
    gif.has_audio = False
    gif.alpha = True
    gif.alpha_mode = "straight"
    gif.preview_path = None
    tl.sources[gif.id] = gif

    video = Track("video")
    video.is_main_video = True
    video.mute = False
    vclip = Clip(mov.id, 0.0, 2.0, 0.0)
    vclip.alpha = True
    vclip.alpha_mode = "premultiplied"
    vclip.size_mode = "source"
    vclip.fade_top = 0.15
    vclip.fade_bottom = 0.25
    vclip.fade_left = 0.35
    vclip.fade_right = 0.45
    video.clips.append(vclip)

    overlay = Track("video")
    overlay.is_main_video = False
    overlay.name = "叠加"
    overlay.mute = True
    gclip = Clip(gif.id, 0.0, 0.4, 0.2)
    gclip.loop = True
    gclip.match_duration = True
    gclip._match_target_dur = 3.5
    gclip.alpha = True
    gclip.size_mode = "project"
    overlay.clips.append(gclip)

    audio = Track("audio")
    audio.mute = True
    aclip = Clip(mov.id, 0.0, 2.0, 0.0)
    aclip.is_main = False
    aclip.audio_role = "bgm"
    audio.clips.append(aclip)

    tl.add_track(video)
    tl.add_track(overlay)
    tl.add_track(audio)
    return {
        "name": "q82",
        "video": mov_path,
        "path": mov_path,
        "text": "",
        "segments": [],
        "style": {},
        "duration": 4.0,
        "text_overlays": [],
        "image_overlays": [],
        "timeline": tl,
    }


def _open_saved(win, dest):
    from cutora.core.commands import reset_bus_for_tests
    reset_bus_for_tests()
    win.bus.execute("project.save_as", {"path": str(dest)}, source="ui")
    assert os.path.isfile(dest), dest
    win.bus.execute("project.open", {"path": str(dest)}, source="ui")
    tasks = list(getattr(win, "tasks", None) or [])
    assert tasks, "project.open left no tasks"
    return tasks[0]


def _tracks(task):
    tl = task.get("timeline")
    return list(getattr(tl, "tracks", None) or [])


def _clip_on(task, source_id):
    for tr in _tracks(task):
        for clip in getattr(tr, "clips", None) or []:
            if getattr(clip, "source_id", None) == source_id:
                return tr, clip
    return None, None


def test_q82_roundtrip_every_field(tmp_path, monkeypatch):
    """Real project.save_as then project.open keeps alpha, fades, mute, gif length."""
    _qapp()
    monkeypatch.setattr(
        "cutora.model.match_span.refresh_match_targets", lambda *a, **k: [])
    from cutora.ui.main_window import MainWindow

    mov = str(tmp_path / "alpha.mov")
    proxy = str(tmp_path / "alpha_proxy.png")
    task = _rich_task(mov, proxy)
    dest = str(tmp_path / "q82.asproj")
    win = MainWindow()
    try:
        _silence(win)
        win.tasks = [task]
        win.curr_idx = 0
        win.project_path = None
        got = _open_saved(win, dest)
    finally:
        win.close()

    tl = got.get("timeline")
    src = tl.sources["q82mov"]
    assert _field(src, "alpha") is True, _field(src, "alpha")
    assert _field(src, "alpha_mode") == "premultiplied", _field(src, "alpha_mode")
    assert _field(src, "preview_path") == proxy, _field(src, "preview_path")

    _vtr, vclip = _clip_on(got, "q82mov")
    assert vclip is not None
    assert vclip.alpha is True
    assert vclip.alpha_mode == "premultiplied"
    assert vclip.size_mode == "source", vclip.size_mode
    assert abs(float(vclip.fade_top) - 0.15) < 1e-6
    assert abs(float(vclip.fade_bottom) - 0.25) < 1e-6
    assert abs(float(vclip.fade_left) - 0.35) < 1e-6
    assert abs(float(vclip.fade_right) - 0.45) < 1e-6

    otr, gclip = _clip_on(got, "q82gif")
    assert gclip is not None
    assert gclip.loop is True and gclip.match_duration is True
    assert abs(float(gclip._match_target_dur) - 3.5) < 1e-6, gclip._match_target_dur
    # Lost target snaps the bar back to the 0.4s source. 3.5s is the saved length.
    assert abs(float(gclip.effective_duration) - 3.5) < 1e-6, gclip.effective_duration
    assert bool(getattr(otr, "mute", False)) is True

    muted = [tr for tr in _tracks(got) if getattr(tr, "type", None) == "audio"]
    assert muted and bool(muted[0].mute) is True, [getattr(tr, "mute", None) for tr in _tracks(got)]


def _saved_timeline(blob):
    tasks = (blob or {}).get("tasks") or []
    assert tasks, "saved project has no tasks"
    tl = tasks[0].get("timeline") or {}
    assert isinstance(tl, dict)
    return tl


def _keyset_map(rows, key_of):
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        out[key_of(row)] = set(row.keys())
    return out


def test_q82_gui_and_render_serializers_agree(tmp_path):
    """GUI save and serialize_render_task expose the same clip/source/track keys."""
    _qapp()
    import json
    from cutora.model.project_io import serialize_render_task
    from cutora.ui.main_window import MainWindow
    from cutora.core.commands import reset_bus_for_tests

    mov = str(tmp_path / "alpha.mov")
    proxy = str(tmp_path / "alpha_proxy.png")
    task = _rich_task(mov, proxy)
    dest = str(tmp_path / "q82-agree.asproj")
    win = MainWindow()
    try:
        _silence(win)
        win.tasks = [task]
        win.curr_idx = 0
        win.project_path = None
        rendered = serialize_render_task(win.tasks[0])
        reset_bus_for_tests()
        win.bus.execute("project.save_as", {"path": dest}, source="ui")
    finally:
        win.close()

    with open(dest, encoding="utf-8") as fh:
        saved = json.load(fh)
    gui_tl = _saved_timeline(saved)
    ren_tl = rendered.get("timeline") or {}
    assert isinstance(ren_tl, dict) and ren_tl.get("tracks"), ren_tl

    def _src_id(row):
        return str(row.get("id") or "")

    gui_src = _keyset_map((gui_tl.get("sources") or {}).values(), _src_id)
    ren_src = _keyset_map((ren_tl.get("sources") or {}).values(), _src_id)
    assert gui_src.keys() == ren_src.keys()
    for sid, keys in gui_src.items():
        assert keys == ren_src[sid], (sid, sorted(keys ^ ren_src[sid]))
        for need in ("alpha", "alpha_mode", "preview_path"):
            assert need in keys, (sid, need, sorted(keys))

    gui_tr = list(gui_tl.get("tracks") or [])
    ren_tr = list(ren_tl.get("tracks") or [])
    assert len(gui_tr) == len(ren_tr)
    for g, r in zip(gui_tr, ren_tr):
        assert set(g.keys()) == set(r.keys()), sorted(set(g.keys()) ^ set(r.keys()))
        assert "mute" in g
        gclips = list(g.get("clips") or [])
        rclips = list(r.get("clips") or [])
        assert len(gclips) == len(rclips)
        for gc, rc in zip(gclips, rclips):
            assert set(gc.keys()) == set(rc.keys()), sorted(set(gc.keys()) ^ set(rc.keys()))
            for need in (
                "alpha", "alpha_mode", "size_mode",
                "fade_top", "fade_bottom", "fade_left", "fade_right",
                "_match_target_dur",
            ):
                assert need in gc, (need, sorted(gc.keys()))
