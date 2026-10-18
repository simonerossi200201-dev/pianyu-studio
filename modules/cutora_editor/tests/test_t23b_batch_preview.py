# -*- coding: utf-8 -*-
"""Q12 / T23-b: batch preview keeps image aspect; offscreen paint + scale."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t23b-preview"])


def _solid_png(path, w, h, rgb=(220, 24, 24)):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_RGB32)
    img.fill(QColor(int(rgb[0]), int(rgb[1]), int(rgb[2])))
    ok = img.save(path, "PNG")
    assert ok, path
    return path


def _grab(w):
    w.show()
    w.repaint()
    img = w.grab().toImage()
    assert not img.isNull()
    return img


def _red_bbox(img, min_r=160, max_g=80, max_b=80):
    x0, y0, x1, y1 = img.width(), img.height(), -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() >= min_r and c.green() <= max_g and c.blue() <= max_b:
                if x < x0:
                    x0 = x
                if y < y0:
                    y0 = y
                if x > x1:
                    x1 = x
                if y > y1:
                    y1 = y
    return x0, y0, x1, y1


def _dialog(path, tw, th):
    """Product path: the batch dialog's VideoCanvas, not a private preview widget."""
    from PySide6.QtWidgets import QApplication, QWidget
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.batch.batch_dialog import build_batch_dialog
    from cutora.ui.canvas import VideoCanvas

    tl = Timeline(int(tw), int(th), 30.0)
    v = Track("video")
    v.is_main_video = True
    tl.add_track(v)
    task = {
        "timeline": tl, "dims": (int(tw), int(th)), "fps": 30.0,
        "duration": 8.0, "video": "", "segments": [],
        "image_overlays": [], "text_overlays": [], "style": {},
    }
    host = QWidget()
    host.tasks = [task]
    host.curr_idx = 0
    host.video_canvas = VideoCanvas()
    host.timeline = QWidget()
    dlg = build_batch_dialog(host)
    dlg.ed_img.setText(path)
    dlg.show()
    QApplication.processEvents()
    return dlg


def test_batch_preview_keeps_aspect():
    """4:5 poster on a 9:16 canvas stays 4:5. The old preview stretched it."""
    _qapp()
    td = tempfile.mkdtemp(prefix="t23b_aspect_")
    path = _solid_png(os.path.join(td, "poster.png"), 400, 500)
    dlg = _dialog(path, 1080, 1920)
    idx = dlg.cb_fit.findData("fit")
    dlg.cb_fit.setCurrentIndex(idx)
    dlg.sp_scale.setValue(1.0)
    dlg.sp_x.setValue(0)
    dlg.sp_y.setValue(0)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    canvas = dlg.preview_canvas
    canvas.resize(270, 480)
    canvas.repaint()
    sticks = [r for r in canvas._hit_records if r.get("type") == "sticker"]
    assert len(sticks) == 1
    rect = sticks[0]["rect"]
    assert rect.width() > 1 and rect.height() > 1
    aspect = float(rect.width()) / float(rect.height())
    assert abs(aspect - 0.8) / 0.8 <= 0.01, aspect
    img = _grab(canvas)
    x0, y0, x1, y1 = _red_bbox(img)
    assert x1 > x0 and y1 > y0, (x0, y0, x1, y1)
    vis = (x1 - x0 + 1) / float(y1 - y0 + 1)
    assert abs(vis - 0.8) / 0.8 <= 0.01, vis
    dlg.close()


def test_batch_preview_offscreen_paint():
    """Scale 1.5 of a small red square is red in the middle, not a cropped blow-up."""
    _qapp()
    td = tempfile.mkdtemp(prefix="t23b_paint_")
    path = _solid_png(os.path.join(td, "solid.png"), 40, 40, rgb=(220, 24, 24))
    dlg = _dialog(path, 1080, 1920)
    idx = dlg.cb_fit.findData("fit")
    dlg.cb_fit.setCurrentIndex(idx)
    dlg.sp_scale.setValue(1.5)
    dlg.sp_x.setValue(0)
    dlg.sp_y.setValue(0)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    canvas = dlg.preview_canvas
    canvas.resize(270, 480)
    img = _grab(canvas)
    sticks = [r for r in canvas._hit_records if r.get("type") == "sticker"]
    assert len(sticks) == 1
    rect = sticks[0]["rect"]
    # Widget point of the canvas-space center of the picture.
    target = canvas._preview_target_rect()
    sx = target.width() / float(canvas._video_dims[0])
    sy = target.height() / float(canvas._video_dims[1])
    cx = int(target.x() + rect.center().x() * sx)
    cy = int(target.y() + rect.center().y() * sy)
    mid = img.pixelColor(cx, cy)
    assert mid.red() >= 160 and mid.green() <= 80 and mid.blue() <= 80, (
        cx, cy, mid.red(), mid.green(), mid.blue())
    # Corners of the portrait canvas are the empty plate, not the picture.
    corners = (
        (int(target.x()) + 2, int(target.y()) + 2),
        (int(target.right()) - 3, int(target.y()) + 2),
        (int(target.x()) + 2, int(target.bottom()) - 3),
        (int(target.right()) - 3, int(target.bottom()) - 3),
    )
    for x, y in corners:
        c = img.pixelColor(x, y)
        assert c.red() < 80, (x, y, c.red(), c.green(), c.blue())
    dlg.close()
