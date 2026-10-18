# -*- coding: utf-8 -*-
"""Q93: dragging a subtitle after ASR must not jump, and adds the mouse delta."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

W, H = 360, 640


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q93-drag"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _task(style):
    from cutora.model.timeline import Timeline
    return {
        "video": "",
        "text": "Hello",
        "segments": [],
        "style": style,
        "duration": 4.0,
        "timeline": Timeline(W, H, 30),
        "dims": (W, H),
        "text_overlays": [],
        "image_overlays": [],
    }


def _finish_asr(task):
    from cutora.ui.main_window import MainWindow
    host = type("H", (), {})()
    host.enable_time_postprocess = False
    host.tasks = [task]
    host.curr_idx = 0
    host.media_player = type("P", (), {"duration": lambda self: 4000})()
    host.timeline = type("T", (), {"set_data": lambda *a, **k: None})()
    host.push_undo = lambda *a, **k: None
    host._refresh_task_view = lambda *a, **k: None
    host._autosave_now = lambda *a, **k: None
    host.worker_ai = type("W", (), {"align_note": None, "approximate_words": False})()
    host.batch_queue = []
    host.statusBar = lambda: type("B", (), {"showMessage": lambda *a, **k: None})()
    host._project_dirty = False
    segs = [{
        "start": 0.2, "end": 1.8, "text": "Hello",
        "words": [{"word": "Hello", "start": 0.2, "end": 1.8}],
    }]
    MainWindow.on_align_done(host, segs, False)
    return task


def _center(style, text="Hello"):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    ev = {
        "text": text, "start": 0.0, "end": 2.0,
        "words": [{"word": text, "start": 0.0, "end": 1.0}],
    }
    lay = layout_event(ev, style, from_style(style), W, H)
    return float(lay.cx), float(lay.cy)


def _paint(task):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#101010"))
    p = QPainter(img)
    try:
        FrameRenderer.draw_layers(
            p, task, W, H, 0.6, render_order=["subs"])
    finally:
        p.end()
    return img


def _export(task):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, W, H, QColor("#101010"))

    return ExportRenderer.render_frame(task, W, H, 0.6, draw_background=_bg)


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4]
    return arr.reshape((h, w, 4)).copy()


def _ink_center(img):
    """Center of the light glyphs. Glass / shadow stay out of the average."""
    arr = _bgra(img)
    r = arr[:, :, 2]
    g = arr[:, :, 1]
    b = arr[:, :, 0]
    ys, xs = np.nonzero((r > 200) & (g > 180) & (b > 160))
    assert xs.size > 20, "subtitle glyphs missing"
    return float(xs.mean()), float(ys.mean())


def _canvas(task):
    from PySide6.QtWidgets import QApplication
    from cutora.ui.canvas import VideoCanvas
    canvas = VideoCanvas()
    canvas.resize(W, H)
    canvas.show()
    canvas.set_data(task, 0.6)
    QApplication.processEvents()
    canvas.repaint()
    QApplication.processEvents()
    return canvas


def _video_to_widget(canvas, vx, vy):
    from PySide6.QtCore import QPoint
    target = canvas._preview_target_rect()
    sx = target.width() / float(canvas._video_dims[0])
    sy = target.height() / float(canvas._video_dims[1])
    return QPoint(
        int(round(target.x() + vx * sx)),
        int(round(target.y() + vy * sy)),
    )


def _drag(canvas, dx, dy, moves=1):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    hit = None
    for rec in canvas._hit_records or []:
        if rec.get("type") == "sub":
            hit = rec.get("rect")
    assert hit is not None, "no subtitle hit after ASR"
    start = _video_to_widget(canvas, hit.center().x(), hit.center().y())
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    step_x = dx / float(moves)
    step_y = dy / float(moves)
    for i in range(1, moves + 1):
        pt = _video_to_widget(
            canvas,
            hit.center().x() + step_x * i,
            hit.center().y() + step_y * i,
        )
        QTest.mouseMove(canvas, pt)
    QTest.mouseRelease(
        canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        _video_to_widget(canvas, hit.center().x() + dx, hit.center().y() + dy),
    )


def test_q93_sub_drag_zero_delta_is_noop():
    """After ASR, a press-move(0,0)-release must not move the painted subtitle."""
    _qapp()
    _fonts()
    style = {
        "font_family": "Arimo",
        "fontsize": 48,
        "bold": True,
        "fx": {"fill": {"type": "solid", "colors": ["#F4EBD8"]}},
    }
    task = _finish_asr(_task(style))
    before = _center(task["style"])
    canvas = _canvas(task)
    _drag(canvas, 0, 0)
    after = _center(task["style"])
    canvas.close()
    assert abs(after[0] - before[0]) < 0.05, (before, after)
    assert abs(after[1] - before[1]) < 0.05, (before, after)
    # Missing keys are committed as the rendered center, not the top edge.
    cy = task["style"].get("pos_y")
    assert cy is not None
    assert abs(float(cy) - before[1]) < 0.05, (cy, before)


def test_q93_sub_drag_after_asr_keeps_position():
    """Existing pos survives ASR. A (30, 20) drag adds exactly that. Export matches."""
    _qapp()
    _fonts()
    from cutora.ui.main_window import MainWindow
    style = {
        "font_family": "Arimo",
        "fontsize": 48,
        "bold": True,
        "pos_x": 120.0,
        "pos_y": 500.0,
    }
    task = _task(style)
    task = _finish_asr(task)
    assert abs(float(task["style"]["pos_x"]) - 120.0) < 0.05
    assert abs(float(task["style"]["pos_y"]) - 500.0) < 0.05
    before = _center(task["style"])
    before_ink = _ink_center(_paint(task))
    canvas = _canvas(task)
    pushes = []

    class Hist:
        def push(self, name, before_snap):
            pushes.append(name)
        def set_after(self, after):
            pass

    host = type("H", (), {})()
    host.history_mgr = Hist()
    host.video_canvas = canvas
    host._undo_snapshot = lambda *_a, **_k: {"style": dict(task["style"])}
    host.capture_undo_after = lambda: None
    canvas.gesture_began.connect(lambda: MainWindow._on_canvas_gesture_began(host))
    canvas.item_dragged.connect(lambda: MainWindow._push_canvas_gesture_once(host))
    canvas.gesture_ended.connect(lambda: MainWindow._on_canvas_gesture_ended(host))
    _drag(canvas, 30, 20, moves=3)
    canvas.close()
    after = _center(task["style"])
    assert abs(after[0] - (before[0] + 30)) < 0.5, (before, after)
    assert abs(after[1] - (before[1] + 20)) < 0.5, (before, after)
    assert pushes == ["拖动"], pushes
    preview = _paint(task)
    export = _export(task)
    moved = _ink_center(preview)
    exported = _ink_center(export)
    assert abs(moved[0] - exported[0]) < 0.5, (moved, exported)
    assert abs(moved[1] - exported[1]) < 0.5, (moved, exported)
    assert abs((moved[0] - before_ink[0]) - 30) < 1.5, (before_ink, moved)
    assert abs((moved[1] - before_ink[1]) - 20) < 1.5, (before_ink, moved)
    assert abs((exported[0] - before_ink[0]) - 30) < 1.5, (before_ink, exported)
    assert abs((exported[1] - before_ink[1]) - 20) < 1.5, (before_ink, exported)


def _plain_sub_task(pos_x, pos_y):
    style = {
        "font_family": "Arimo",
        "fontsize": 36,
        "bold": True,
        "pos_x": float(pos_x),
        "pos_y": float(pos_y),
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "bg": {"type": "none", "enabled": False},
            "stroke": {"width_em": 0, "enabled": False},
            "glow": {"enabled": False, "sigma_em": 0},
            "shadow": {"enabled": False, "strength": 0},
        },
    }
    from cutora.model.timeline import Timeline
    return {
        "video": "",
        "text": "Hello",
        "segments": [{
            "start": 0.0, "end": 2.0, "text": "Hello", "role": "default",
            "words": [{"word": "Hello", "start": 0.0, "end": 1.5}],
        }],
        "style": style,
        "duration": 4.0,
        "timeline": Timeline(W, H, 30),
        "dims": (W, H),
        "text_overlays": [],
        "image_overlays": [],
    }


def _sub_hit(canvas):
    for rec in canvas._hit_records or []:
        if rec.get("type") == "sub":
            return rec.get("rect")
    return None


def test_q93_sub_snaps_to_center():
    """Drag the subtitle to 8px from the canvas center. Release lands on center.
    Holding N leaves it 8px short."""
    _qapp()
    _fonts()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    task = _plain_sub_task(140, 220)
    canvas = _canvas(task)
    hit = _sub_hit(canvas)
    assert hit is not None
    dx = (W / 2.0 - 8.0) - hit.center().x()
    dy = (H / 2.0 - 8.0) - hit.center().y()
    _drag(canvas, dx, dy, moves=1)
    from PySide6.QtWidgets import QApplication
    canvas.repaint()
    QApplication.processEvents()
    landed = _sub_hit(canvas)
    canvas.close()
    assert landed is not None
    assert abs(landed.center().x() - W / 2.0) <= 0.5, (
        landed.center().x(), landed.center().y())
    assert abs(landed.center().y() - H / 2.0) <= 0.5, (
        landed.center().x(), landed.center().y())

    task2 = _plain_sub_task(140, 220)
    canvas2 = _canvas(task2)
    hit2 = _sub_hit(canvas2)
    dx2 = (W / 2.0 - 8.0) - hit2.center().x()
    dy2 = (H / 2.0 - 8.0) - hit2.center().y()
    QTest.keyPress(canvas2, Qt.Key.Key_N)
    _drag(canvas2, dx2, dy2, moves=1)
    canvas2.repaint()
    QApplication.processEvents()
    QTest.keyRelease(canvas2, Qt.Key.Key_N)
    raw = _sub_hit(canvas2)
    canvas2.close()
    assert raw is not None
    assert abs(raw.center().x() - (W / 2.0 - 8.0)) <= 0.5, raw.center()
    assert abs(raw.center().y() - (H / 2.0 - 8.0)) <= 0.5, raw.center()
    assert abs(raw.center().x() - W / 2.0) > 4.0


def test_q93_sub_snap_guides_drawn():
    """While the subtitle is snapping, the canvas paints guide pixels."""
    _qapp()
    _fonts()
    task = _plain_sub_task(140, 220)
    canvas = _canvas(task)
    hit = _sub_hit(canvas)
    dx = (W / 2.0 - 8.0) - hit.center().x()
    dy = (H / 2.0 - 8.0) - hit.center().y()
    shots = []

    def _grab():
        shots.append(canvas.grab().toImage())

    canvas.item_dragged.connect(_grab)
    _drag(canvas, dx, dy, moves=1)
    canvas.close()
    assert shots, "drag did not paint"
    img = shots[-1]
    found = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.blue() > 180 and c.green() > 150 and c.red() < 80:
                found += 1
                if found > 8:
                    break
        if found > 8:
            break
    assert found > 8, found
