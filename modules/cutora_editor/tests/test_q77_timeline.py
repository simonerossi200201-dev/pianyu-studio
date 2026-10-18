# -*- coding: utf-8 -*-
"""Q77: one timeline click owns the selection. The previous bar must let go.

Video highlight is fed by three mirrors (selected_video_clip, _clip_sel,
selection.has_clip). Those three naming the same clip count as the one
video channel. A second channel (a text bar, a picture bar, a stale
selection.clips entry) is the bug.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q77"])


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


def _primary_clip_keys(w):
    keys = set()
    if w.selected_video_clip is not None:
        tr, clip = w.selected_video_clip
        keys.add((str(getattr(tr, "id", "")), w._clip_index_of(tr, clip)))
    if w.selected_audio_idx is not None:
        ac = w._get_audio_clip_list()
        i = w.selected_audio_idx
        if 0 <= i < len(ac):
            clip, tr = ac[i]
            keys.add((str(getattr(tr, "id", "")), w._clip_index_of(tr, clip)))
    return keys


def _occupied(w):
    """Names of the seven channels that hold a choice of their own.

    _clip_sel and selection are the publish of the one selected clip or
    subtitle. They count only when they still name something that primary
    field does not — that is the stuck-video case.
    """
    names = []
    if w.selected_indices:
        names.append("segments")
    if w.selected_overlay is not None:
        names.append("overlay")
    if w.selected_video_clip is not None:
        names.append("video")
    if w.selected_audio_idx is not None:
        names.append("audio")
    if w.selected_transition is not None or w.selected_seam is not None:
        names.append("transition")
    primary = _primary_clip_keys(w)
    extra_ctrl = [tuple(k) for k in (w._clip_sel or []) if tuple(k) not in primary]
    if extra_ctrl or (
        w._clip_sel and w.selected_video_clip is None and w.selected_audio_idx is None
    ):
        names.append("clip_sel")
    sel_extra = [tuple(c) for c in (w.selection.clips or []) if tuple(c) not in primary]
    seg_ok = set(w.selection.segments or []) == set(w.selected_indices or [])
    if sel_extra or not seg_ok or (
        w.selection.clips and not primary and not w.selected_indices
    ):
        names.append("selection")
    return names


def _build_bars():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(320, 180, 30.0)
    video = Track("video")
    video.is_main_video = True
    clip = Clip("src-q77", 0.0, 4.0, 0.0)
    video.clips.append(clip)
    text_tr = Track("text")
    image_tr = Track("image")
    tl.tracks.extend([video, text_tr, image_tr])
    text = {
        "type": "text",
        "content": "你好",
        "start": 0.0,
        "end": 4.0,
        "x": 20, "y": 20, "w": 200, "h": 60,
        "fontsize": 40,
        "font_family": "Arimo",
        "color": "#FFFFFF",
        "opacity": 255,
        "rotation": 0,
        "_track_id": text_tr.id,
    }
    image = {
        "type": "image",
        "start": 0.0,
        "end": 4.0,
        "x": 0, "y": 0, "w": 320, "h": 180,
        "opacity": 255,
        "elements": [{
            "kind": "image",
            "path": "",
            "x": 0, "y": 0, "w": 320, "h": 180,
            "rotation": 0.0,
            "opacity": 255.0,
        }],
        "_track_id": image_tr.id,
    }
    task = {
        "timeline": tl,
        "segments": [],
        "style": {},
        "text_overlays": [text],
        "image_overlays": [image],
        "duration": 4.0,
        "dims": [320, 180],
    }
    return task, video, clip, text, image


def _arm_timeline(task):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.timeline.widget import TimelineWidget
    app = _app()
    w = TimelineWidget()
    w.resize(900, 420)
    w.pixels_per_second = 80.0
    w.scroll_x = 0
    w.scroll_y = 0
    w.set_data(4000, task)
    w.show()
    app.processEvents()
    return w


def _click_kind(w, kind):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    rows = w._track_positions()["rows"]
    target = None
    for track, y, h in rows:
        if getattr(track, "type", None) == kind:
            target = (y, h)
            break
    assert target is not None, kind
    y, h = target
    # 1.5s sits in the bar body, clear of the 14px trim handles.
    x = int(1.5 * w.pixels_per_second - w.scroll_x)
    QTest.mouseClick(
        w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        QPoint(x, int(y + h / 2)))
    _app().processEvents()


def _assert_exclusive(w, kind):
    got = _occupied(w)
    assert got == [kind], (
        "occupied %s, video=%s overlay=%s clip_sel=%s selection=%s indices=%s"
        % (got, w.selected_video_clip, w.selected_overlay, w._clip_sel,
           w.selection.clips, w.selected_indices)
    )


def test_q77_single_selection_video_text_image():
    """Video, then text, then picture. After each click only one channel is set.

    Before the fix, the text click leaves selected_video_clip set.
    """
    task, video, clip, _text, _image = _build_bars()
    w = _arm_timeline(task)
    _click_kind(w, "video")
    _assert_exclusive(w, "video")
    assert w.selected_video_clip[1] is clip
    assert w.selected_overlay is None
    tid = str(video.id)
    assert w.selection.has_clip(tid, 0)

    _click_kind(w, "text")
    _assert_exclusive(w, "overlay")
    assert w.selected_video_clip is None
    assert w._clip_sel == []
    assert w.selection.clips == []
    assert not w.selection.has_clip(tid, 0)
    assert w.selected_overlay == ("text_overlays", 0)
    assert w.selected_audio_idx is None
    assert not w.selected_indices
    assert w.selected_transition is None
    assert w.selected_seam is None

    _click_kind(w, "image")
    _assert_exclusive(w, "overlay")
    assert w.selected_video_clip is None
    assert w._clip_sel == []
    assert w.selection.clips == []
    assert w.selected_overlay == ("image_overlays", 0)
    w.close()


def test_q77_reverse_order():
    """Text first, then the video bar. The text bar must not stay selected."""
    task, _video, clip, _text, _image = _build_bars()
    w = _arm_timeline(task)
    _click_kind(w, "text")
    assert w.selected_overlay == ("text_overlays", 0)
    _click_kind(w, "video")
    assert w.selected_overlay is None
    assert w.selected_video_clip is not None
    assert w.selected_video_clip[1] is clip
    _assert_exclusive(w, "video")
    w.close()


def _arm_window(task):
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow
    app = _app()
    load_embedded_fonts()
    win = MainWindow()
    _silence(win)
    win.tasks = [task]
    win.curr_idx = 0
    win._task_data = task
    win.timeline.set_data(4000, task)
    win.timeline.pixels_per_second = 80.0
    win.timeline.scroll_x = 0
    win.timeline.scroll_y = 0
    win.timeline.resize(900, 420)
    win.show()
    app.processEvents()
    return win


def test_q77_panel_follows_last_click():
    """Property panel type matches the bar just clicked, including mouse-up.

    The last step plants a stale image selection and releases the mouse
    (on_canvas_drag). The panel must stay on the video.
    """
    from PySide6.QtCore import QRectF
    task, _video, _clip, text, image = _build_bars()
    win = _arm_window(task)
    panel = win.prop_panel
    # A full-canvas picture left selected on the preview must not survive
    # a click on the video bar.
    win.video_canvas._selected_item = {
        "type": "overlay", "list": "image_overlays", "idx": 0,
    }
    win.video_canvas._selected_rect = QRectF(0, 0, 320, 180)

    _click_kind(win.timeline, "video")
    assert panel.current_type == "video_clip"
    assert win.video_canvas._selected_item is None
    assert win.video_canvas._selected_rect is None
    _assert_exclusive(win.timeline, "video")

    _click_kind(win.timeline, "text")
    assert panel.current_type == "overlay"
    assert panel.current_data is text
    _assert_exclusive(win.timeline, "overlay")

    _click_kind(win.timeline, "image")
    assert panel.current_type == "sticker"
    assert panel.current_data is image["elements"][0]
    _assert_exclusive(win.timeline, "overlay")

    _click_kind(win.timeline, "video")
    assert panel.current_type == "video_clip", panel.current_type
    _assert_exclusive(win.timeline, "video")

    # Mouse-up used to reload whatever selected_overlay still pointed at.
    win.timeline.selected_overlay = ("image_overlays", 0)
    win.on_canvas_drag()
    assert panel.current_type == "video_clip", panel.current_type
    win.close()


def _transparent_png(path, tw, th):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(tw, th, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        painter.fillRect(0, 0, 8, 8, QColor(255, 0, 0, 255))
    finally:
        painter.end()
    assert img.save(path, "PNG")


def test_q77_canvas_click_through_transparent_image(tmp_path):
    """A full-canvas picture with a transparent middle must not eat the click.

    The opaque corner still selects the picture. The clear middle selects
    the video underneath, and the picture bar lets go.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from cutora.batch.script_insert_image import make_overlay
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.hitmask import pick_record

    tw, th = 160, 90
    png = str(tmp_path / "clear.png")
    _transparent_png(png, tw, th)
    tl = Timeline(tw, th, 30.0)
    video = Track("video")
    video.is_main_video = True

    class _Src:
        id = "vid"
        path = "q77-solid.mp4"
        has_video = True
        has_audio = False
        duration = 4.0

    clip = Clip("vid", 0.0, 4.0, 0.0)
    video.clips.append(clip)
    image_tr = Track("image")
    tl.sources["vid"] = _Src()
    tl.tracks.extend([video, image_tr])
    ov = make_overlay(png, 0.0, 4.0, tw, th, tw, th, "fill", 1.0, 0.0, 0.0,
                      image_tr.id, 0)
    task = {
        "timeline": tl,
        "segments": [],
        "style": {},
        "text_overlays": [],
        "image_overlays": [ov],
        "duration": 4.0,
        "dims": [tw, th],
        "video": _Src.path,
    }
    win = _arm_window(task)
    canvas = win.video_canvas
    solid = QImage(tw, th, QImage.Format.Format_ARGB32)
    solid.fill(QColor(20, 80, 200, 255))
    canvas._get_cv_frame_for_preview = lambda *a, **k: solid
    # The docked preview often has no size until the shell finishes layout.
    # A real click still goes through this same widget and its item_selected.
    canvas.setParent(None)
    canvas.setFixedSize(tw, th)
    canvas.set_data(task, 1.0)
    canvas.show()
    _app().processEvents()
    canvas.repaint()
    recs = list(canvas._hit_records or [])
    assert recs, "preview paint produced no hit records (%sx%s)" % (
        canvas.width(), canvas.height())
    covered = [
        r for r in recs
        if r.get("type") in ("overlay", "sticker")
        and r.get("rect") is not None and r["rect"].contains(80, 45)
    ]
    assert any(r.get("type") == "video_clip" for r in recs), [r.get("type") for r in recs]
    assert covered, "picture layer does not cover the canvas middle"
    mid = pick_record(recs, 80, 45)
    assert mid is not None and mid.get("type") == "video_clip", mid
    corner = pick_record(recs, 4, 4)
    assert corner is not None and corner.get("type") in ("overlay", "sticker"), corner

    win.timeline.selected_overlay = ("image_overlays", 0)
    QTest.mouseClick(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        QPoint(4, 4))
    _app().processEvents()
    assert win.timeline.selected_overlay == ("image_overlays", 0)
    assert win.timeline.selected_video_clip is None
    assert win.prop_panel.current_type in ("sticker", "overlay")

    QTest.mouseClick(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        QPoint(80, 45))
    _app().processEvents()
    assert win.timeline.selected_overlay is None
    assert win.timeline.selected_video_clip is not None
    assert win.timeline.selected_video_clip[1] is clip
    assert win.prop_panel.current_type == "video_clip"
    item = canvas._selected_item
    assert item is None or item.get("type") == "video_clip"
    _assert_exclusive(win.timeline, "video")
    win.close()
