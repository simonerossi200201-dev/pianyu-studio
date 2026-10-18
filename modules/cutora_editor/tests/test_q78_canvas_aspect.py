# -*- coding: utf-8 -*-
"""Q78: left-drag an imported picture on the canvas, and lock aspect.

The picture is an image overlay whose pixels live on a child element, while
the overlay box is the whole canvas. Selecting it used to cancel the drag
the press had just started, so the element never moved and the export frame
stayed put. A keyed element that only writes the static x bounces back.
"""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q78"])


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


def _mouse(typ, x, y, mods=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    if mods is None:
        mods = Qt.KeyboardModifier.NoModifier
    return QMouseEvent(
        typ, QPointF(float(x), float(y)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, mods,
    )


def _white_png(path, w, h):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32)
    img.fill(QColor(240, 240, 240, 255))
    assert img.save(str(path), "PNG")


def _rgb(img):
    from PySide6.QtGui import QImage
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    raw = img.bits()
    buf = np.frombuffer(raw, dtype=np.uint8, count=h * img.bytesPerLine())
    return buf.reshape(h, img.bytesPerLine())[:, : w * 3].reshape(h, w, 3).copy()


def _mad(a, b):
    return float(np.abs(a.astype(np.float64) - b.astype(np.float64)).mean()) / 255.0


def _export(task, t):
    from cutora.model.canvas import canvas_wh
    from cutora.render.export_renderer import ExportRenderer
    w, h = canvas_wh(task)
    return ExportRenderer.render_frame(task, int(w), int(h), float(t))


def _picture_task(png, tw=320, th=180):
    from cutora.assets.user import build_image_overlay
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track

    tl = Timeline(tw, th, 30.0)
    image_tr = Track("image")
    tl.tracks.append(image_tr)
    ov = build_image_overlay(
        png, 0.0, tw, th, track_id=image_tr.id, duration=2.0)
    task = {
        "timeline": tl,
        "segments": [],
        "style": {},
        "text_overlays": [],
        "image_overlays": [ov],
        "duration": 2.0,
        "dims": [tw, th],
    }
    return task, ov


def _arm(task):
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow
    load_embedded_fonts()
    win = MainWindow()
    _silence(win)
    win.tasks = [task]
    win.curr_idx = 0
    win._task_data = task
    win.timeline.set_data(2000, task)
    return win


def _prep_canvas(win, task, tw, th, t=0.5):
    from PySide6.QtWidgets import QApplication
    canvas = win.video_canvas
    canvas.setParent(None)
    canvas.setFixedSize(tw, th)
    canvas.set_data(task, t)
    canvas.show()
    QApplication.processEvents()
    canvas.repaint()
    return canvas


def test_q78_canvas_drag_moves_image(tmp_path):
    """Press the picture, move (100, 50), release: the element moves and
    the exported frame moves with it. One gesture is one undo step.
    """
    _app()
    from PySide6.QtCore import QEvent

    tw, th = 320, 180
    png = str(tmp_path / "pic.png")
    _white_png(png, 80, 40)
    task, ov = _picture_task(png, tw, th)
    el = ov["elements"][0]
    x0, y0 = float(el["x"]), float(el["y"])
    # Center of the fitted picture. Canvas is 1:1 with the video, so widget
    # pixels and video pixels are the same.
    cx, cy = x0 + float(el["w"]) / 2.0, y0 + float(el["h"]) / 2.0
    before = _rgb(_export(task, 0.5))

    win = _arm(task)
    try:
        canvas = _prep_canvas(win, task, tw, th, 0.5)
        recs = list(canvas._hit_records or [])
        assert any(r.get("type") == "sticker" for r in recs), [r.get("type") for r in recs]
        n0 = len(win.history_mgr.history)
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx, cy))
        # Two moves, one gesture. A push on every move would leave two entries.
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx + 40, cy + 20))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx + 100, cy + 50))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cx + 100, cy + 50))
    finally:
        win.close()

    dx = float(el["x"]) - x0
    dy = float(el["y"]) - y0
    assert abs(dx - 100.0) < 2.0 and abs(dy - 50.0) < 2.0, (
        "left-drag did not move the picture (dx=%s dy=%s from %s,%s)"
        % (dx, dy, x0, y0)
    )
    after = _rgb(_export(task, 0.5))
    mad = _mad(before, after)
    assert mad > 0.02, "export frame did not move with the picture (mad=%.5f)" % mad
    added = len(win.history_mgr.history) - n0
    assert added == 1, "one drag must be one undo step, got %s" % added
    snap = win.history_mgr.history[win.history_mgr.current_index]["state"]
    old = snap["tasks"][0]["image_overlays"][0]["elements"][0]
    assert abs(float(old["x"]) - x0) < 1.0, old["x"]

    # Keyed element: writing only the static field bounces. The playhead
    # value and the export frame both have to follow the drag.
    from cutora.render.keyframe import set_point, value_at
    task2, ov2 = _picture_task(png, tw, th)
    el2 = ov2["elements"][0]
    x2 = float(el2["x"])
    set_point(el2, "x", 0.0, x2)
    set_point(el2, "x", 1.0, x2 + 40.0)
    el2["x"] = x2
    t = 0.5
    v0 = float(value_at(el2, "x", t, -999.0))
    assert abs(v0 - (x2 + 20.0)) < 1.0, v0
    before2 = _rgb(_export(task2, t))
    win2 = _arm(task2)
    try:
        canvas = _prep_canvas(win2, task2, tw, th, t)
        cx2 = x2 + float(el2["w"]) / 2.0
        cy2 = float(el2["y"]) + float(el2["h"]) / 2.0
        canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, cx2, cy2))
        canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, cx2 + 100, cy2))
        canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, cx2 + 100, cy2))
    finally:
        win2.close()
    v1 = float(value_at(el2, "x", t, -999.0))
    assert abs(v1 - v0) > 50.0, (
        "keyed drag wrote static x=%s but value_at stayed %s" % (el2.get("x"), v1)
    )
    mad2 = _mad(before2, _rgb(_export(task2, t)))
    assert mad2 > 0.02, "keyed picture bounced back in the export (mad=%.5f)" % mad2


def _select_picture(canvas):
    from PySide6.QtCore import QRectF
    rec = next(r for r in canvas._hit_records if r.get("type") == "sticker")
    canvas._selected_item = rec
    canvas._selected_rect = QRectF(rec["rect"])
    return rec


def _bare_canvas(task, tw, th):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.canvas import VideoCanvas
    canvas = VideoCanvas()
    canvas.setFixedSize(tw, th)
    canvas.set_data(task, 0.5)
    canvas.show()
    QApplication.processEvents()
    canvas.repaint()
    return canvas


def _drag_se(canvas, rect, dx, dy, mods=None):
    from PySide6.QtCore import QEvent
    hx, hy = float(rect.right()), float(rect.bottom())
    canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, hx, hy))
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, hx + dx, hy + dy, mods))
    canvas.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, hx + dx, hy + dy, mods))


def test_q78_corner_resize_keeps_aspect(tmp_path):
    """Locked corner drag changes size but not the width/height ratio."""
    _app()
    tw, th = 320, 180
    png = str(tmp_path / "pic.png")
    _white_png(png, 80, 40)
    task, ov = _picture_task(png, tw, th)
    el = ov["elements"][0]
    w0, h0 = float(el["w"]), float(el["h"])
    ratio0 = w0 / h0
    canvas = _bare_canvas(task, tw, th)
    try:
        rec = _select_picture(canvas)
        # +80,+10 would make 160x50 (ratio 3.2) if the axes were free.
        _drag_se(canvas, rec["rect"], 80, 10)
    finally:
        canvas.close()
    assert abs(float(el["w"]) - w0) > 20.0, (el["w"], el["h"])
    ratio1 = float(el["w"]) / float(el["h"])
    assert abs(ratio1 / ratio0 - 1.0) < 0.005, (ratio0, ratio1, el["w"], el["h"])


def test_q78_shift_inverts_lock(tmp_path):
    """Shift while dragging a corner changes the ratio. The lock stays on."""
    _app()
    from PySide6.QtCore import Qt
    from cutora.model.project_schema import lock_aspect_of

    tw, th = 320, 180
    png = str(tmp_path / "pic.png")
    _white_png(png, 80, 40)
    task, ov = _picture_task(png, tw, th)
    el = ov["elements"][0]
    ratio0 = float(el["w"]) / float(el["h"])
    canvas = _bare_canvas(task, tw, th)
    try:
        rec = _select_picture(canvas)
        _drag_se(canvas, rec["rect"], 80, 10, Qt.KeyboardModifier.ShiftModifier)
    finally:
        canvas.close()
    ratio1 = float(el["w"]) / float(el["h"])
    assert abs(ratio1 / ratio0 - 1.0) > 0.005, (ratio0, ratio1, el["w"], el["h"])
    assert lock_aspect_of(el, ov) is True


def test_q78_restore_aspect(tmp_path):
    """Stretch off-ratio, then 还原宽高比 keeps the width and restores the file ratio."""
    _app()
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.property import PropertyPanel

    png = str(tmp_path / "pic.png")
    _white_png(png, 80, 40)
    _task, ov = _picture_task(png)
    el = ov["elements"][0]
    el["w"] = 100
    el["h"] = 10
    panel = PropertyPanel()
    panel._ov_parent_overlay = ov
    panel.load_data("sticker", el)
    panel.show()
    QApplication.processEvents()
    try:
        panel.ov_restore_btn.click()
        QApplication.processEvents()
    finally:
        panel.close()
    assert abs(float(el["w"]) - 100.0) < 1.0, el["w"]
    ratio = float(el["w"]) / float(el["h"])
    assert abs(ratio - 2.0) < 0.005, (ratio, el["w"], el["h"])


def test_q78_default_locked(tmp_path):
    """New picture, text, sticker and clip lock aspect. Old files without the field do too."""
    _app()
    from cutora.assets.user import build_image_overlay
    from cutora.batch.batch_load import make_text_overlay
    from cutora.core.cmds.light import build_sticker_overlay
    from cutora.model.timeline_model import Clip
    from cutora.ui.main_window import MainWindow

    png = str(tmp_path / "pic.png")
    _white_png(png, 80, 40)
    ov = build_image_overlay(png, 0.0, 320, 180)
    assert ov["lock_aspect"] is True
    assert ov["elements"][0]["lock_aspect"] is True
    text = make_text_overlay(content="标题")
    # Q124/Q125：文本框不锁比例 —— 角柄本来就是等比缩字号，边柄只改折行宽，
    # 「锁比例」对文本只会让拖右边时高度跟着乱动。图片/贴纸/片段照旧锁。
    assert text["lock_aspect"] is False
    sticker = build_sticker_overlay("heart", 320, 180, 0.0, 2.0)
    assert sticker["lock_aspect"] is True
    assert sticker["elements"][0]["lock_aspect"] is True
    assert Clip("src", 0.0, 1.0, 0.0).lock_aspect is True

    win = MainWindow()
    _silence(win)
    try:
        win.apply_project_data({
            "tasks": [{
                "text_overlays": [{
                    "type": "text", "start": 0.0, "end": 1.0,
                    "content": "旧", "x": 0, "y": 0, "w": 20, "h": 10,
                }],
                "image_overlays": [{
                    "type": "image", "start": 0.0, "end": 1.0,
                    "keep_aspect": False,
                    "elements": [{"kind": "image", "w": 8, "h": 2}],
                }],
                "segments": [],
                "style": {},
                "timeline": {
                    "width": 320, "height": 180, "fps": 30,
                    "sources": {},
                    "tracks": [{
                        "type": "video",
                        "clips": [{
                            "source_id": "old", "in_t": 0.0,
                            "out_t": 1.0, "start_t": 0.0,
                        }],
                    }],
                },
            }],
        })
        task = win.tasks[0]
    finally:
        win.close()
    assert task["text_overlays"][0]["lock_aspect"] is True
    assert task["image_overlays"][0]["lock_aspect"] is False
    assert task["image_overlays"][0]["elements"][0]["lock_aspect"] is False
    clip = task["timeline"].tracks[0].clips[0]
    assert clip.lock_aspect is True
