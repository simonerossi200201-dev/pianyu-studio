# -*- coding: utf-8 -*-
"""Q81: batch-load preview is the real VideoCanvas, same rect as the main canvas."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q81-batch"])


def _silence():
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)


def _png(path, w, h, rgb=(220, 24, 24)):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_RGB32)
    img.fill(QColor(int(rgb[0]), int(rgb[1]), int(rgb[2])))
    assert img.save(str(path), "PNG")
    return str(path)


def _task(tw=720, th=1280):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(int(tw), int(th), 30.0)
    v = Track("video")
    v.is_main_video = True
    tl.add_track(v)
    return {
        "timeline": tl,
        "dims": (int(tw), int(th)),
        "fps": 30.0,
        "duration": 10.0,
        "video": "",
        "segments": [],
        "image_overlays": [],
        "text_overlays": [],
        "style": {},
    }


def _host(task):
    from PySide6.QtWidgets import QWidget
    from cutora.ui.canvas import VideoCanvas
    host = QWidget()
    host.tasks = [task]
    host.curr_idx = 0
    host.video_canvas = VideoCanvas()
    host.timeline = QWidget()

    def refresh_ui():
        host.video_canvas.set_data(host.tasks[host.curr_idx], 0.2)
        host.video_canvas.repaint()

    host.refresh_ui = refresh_ui
    return host


def _dialog(task, png, folder):
    from PySide6.QtWidgets import QApplication
    from cutora.batch.batch_dialog import build_batch_dialog
    host = _host(task)
    dlg = build_batch_dialog(host)
    dlg.ed_dir.setText(folder)
    dlg.thumb_grid.set_folder(folder)
    dlg.ed_img.setText(png)
    dlg._host = host
    dlg.show()
    QApplication.processEvents()
    return dlg


def _sticker(canvas):
    canvas.repaint()
    sticks = [r for r in (canvas._hit_records or []) if r.get("type") == "sticker"]
    assert len(sticks) == 1, [r.get("type") for r in (canvas._hit_records or [])]
    return sticks[0]["rect"]


def _box(rect):
    return (
        int(round(rect.x())),
        int(round(rect.y())),
        int(round(rect.width())),
        int(round(rect.height())),
    )


def _wxy(canvas, vx, vy):
    tr = canvas._preview_target_rect()
    sx = tr.width() / float(canvas._video_dims[0])
    sy = tr.height() / float(canvas._video_dims[1])
    return tr.x() + float(vx) * sx, tr.y() + float(vy) * sy


def _mouse(typ, x, y, mods=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    if mods is None:
        mods = Qt.KeyboardModifier.NoModifier
    return QMouseEvent(
        typ, QPointF(float(x), float(y)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, mods,
    )


def _press_move(canvas, x0, y0, x1, y1, mods=None):
    from PySide6.QtCore import QEvent
    canvas.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x0, y0, mods))
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x1, y1, mods))


def _red_bbox(img):
    x0, y0, x1, y1 = img.width(), img.height(), -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() >= 160 and c.green() <= 80 and c.blue() <= 80:
                x0 = min(x0, x)
                y0 = min(y0, y)
                x1 = max(x1, x)
                y1 = max(y1, y)
    return x0, y0, x1, y1


def test_q81_preview_is_video_canvas(tmp_path):
    _app()
    from cutora.ui.canvas import VideoCanvas
    png = _png(tmp_path / "a.png", 80, 60)
    dlg = _dialog(_task(), png, str(tmp_path))
    # Q123：三栏从 QHBoxLayout 换成可拖的 QSplitter，取法跟着改，断言不变
    # Q191：每栏包在一张圆角卡片里，画布是第三张卡片里面那个
    right = dlg.column_layout.widget(2).inner
    assert isinstance(right, VideoCanvas)
    assert right is dlg.preview_canvas
    assert type(right) is VideoCanvas
    dlg.close()


def test_q81_same_position_as_main_canvas(tmp_path):
    """(120, 300) scale 0.6 on a 400x500 picture. Default fill.

    The picture does not cover the 720x1280 canvas, so a preview that ignores
    the offset or uses a different fit cannot share the main canvas's rect.
    """
    from PySide6.QtWidgets import QApplication
    _app()
    _silence()
    png = _png(tmp_path / "poster.png", 400, 500, (220, 24, 24))
    task = _task(720, 1280)
    dlg = _dialog(task, png, str(tmp_path))
    dlg.resize(1400, 900)
    QApplication.processEvents()
    dlg.sp_x.setValue(120)
    dlg.sp_y.setValue(300)
    dlg.sp_scale.setValue(0.6)
    assert (dlg.cb_fit.currentData() or "fill") == "fill"
    QApplication.processEvents()
    pre_canvas = dlg.preview_canvas
    pre = _sticker(pre_canvas)
    from cutora.batch.batch_load import compute_fit_rect
    ex, ey, ew, eh = compute_fit_rect(400, 500, 720, 1280, "fill", 0.6, 120, 300)
    assert abs(pre.x() - ex) < 1e-4, (pre.x(), ex)
    assert abs(pre.y() - ey) < 1e-4, (pre.y(), ey)
    assert abs(pre.width() - ew) < 1e-4, (pre.width(), ew)
    assert abs(pre.height() - eh) < 1e-4, (pre.height(), eh)
    # fill+0.6 of this poster is inside the canvas, and the +120 offset is visible
    assert pre.width() < 700
    assert pre.x() > 100

    dlg.btn_run.click()
    QApplication.processEvents()
    main = dlg._host.video_canvas
    main.resize(pre_canvas.width(), pre_canvas.height())
    main.set_data(task, 0.2)
    main.show()
    QApplication.processEvents()
    got = _sticker(main)
    assert _box(pre) == _box(got), (_box(pre), _box(got))
    assert abs(pre.x() - got.x()) < 1e-6
    assert abs(pre.y() - got.y()) < 1e-6
    assert abs(pre.width() - got.width()) < 1e-6
    assert abs(pre.height() - got.height()) < 1e-6

    # Same widget size: the painted red box is the same pixels.
    pre_canvas.resize(360, 640)
    main.resize(360, 640)
    pre_canvas.show()
    main.show()
    QApplication.processEvents()
    pre_canvas.repaint()
    main.repaint()
    pb = _red_bbox(pre_canvas.grab().toImage())
    mb = _red_bbox(main.grab().toImage())
    assert pb[2] > pb[0] and pb[3] > pb[1], pb
    assert pb == mb, (pb, mb)
    dlg.close()
    main.close()


def test_q81_drag_on_canvas_updates_params(tmp_path):
    from PySide6.QtWidgets import QApplication
    _app()
    png = _png(tmp_path / "drag.png", 400, 500, (220, 24, 24))
    dlg = _dialog(_task(), png, str(tmp_path))
    dlg.resize(1400, 900)
    QApplication.processEvents()
    dlg.sp_x.setValue(0)
    dlg.sp_y.setValue(0)
    dlg.sp_scale.setValue(1.0)
    QApplication.processEvents()
    c = dlg.preview_canvas
    rect = _sticker(c)
    cx, cy = rect.center().x(), rect.center().y()
    x0, y0 = _wxy(c, cx, cy)
    x1, y1 = _wxy(c, cx + 80, cy + 40)
    _press_move(c, x0, y0, x1, y1)
    assert abs(dlg.sp_x.value() - 80) < 2, dlg.sp_x.value()
    assert abs(dlg.sp_y.value() - 40) < 2, dlg.sp_y.value()
    dlg.close()


def test_q81_corner_drag_updates_scale(tmp_path):
    """Corner handle writes 缩放 and keeps aspect. Shift reverses the lock."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication
    _app()
    png = _png(tmp_path / "corner.png", 200, 160, (20, 180, 40))
    dlg = _dialog(_task(), png, str(tmp_path))
    dlg.resize(1400, 900)
    QApplication.processEvents()
    idx = dlg.cb_fit.findData("source")
    assert idx >= 0
    dlg.cb_fit.setCurrentIndex(idx)
    dlg.sp_x.setValue(0)
    dlg.sp_y.setValue(0)
    dlg.sp_scale.setValue(1.0)
    QApplication.processEvents()
    c = dlg.preview_canvas
    rect = _sticker(c)
    aspect = 200 / 160
    cx, cy = rect.center().x(), rect.center().y()
    x0, y0 = _wxy(c, cx, cy)
    c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x0, y0))
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x0, y0))
    rect = c._selected_rect
    assert rect is not None
    se = c._resize_handle_rects(rect)["se"].center()
    hx, hy = _wxy(c, se.x(), se.y())
    ox, oy = _wxy(c, se.x() + 70, se.y() + 20)
    _press_move(c, hx, hy, ox, oy)
    assert dlg.sp_scale.value() > 1.05, dlg.sp_scale.value()
    el = dlg._preview_task["image_overlays"][0]["elements"][0]
    got_aspect = float(el["w"]) / float(el["h"])
    assert abs(got_aspect - aspect) / aspect < 0.02, (got_aspect, aspect)

    # Shift inverts the lock: a non-uniform pull must change the ratio.
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, ox, oy))
    dlg.sp_scale.setValue(1.0)
    dlg.sp_x.setValue(0)
    dlg.sp_y.setValue(0)
    QApplication.processEvents()
    c.repaint()
    rect = _sticker(c)
    c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, *_wxy(c, rect.center().x(), rect.center().y())))
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, *_wxy(c, rect.center().x(), rect.center().y())))
    rect = c._selected_rect
    se = c._resize_handle_rects(rect)["se"].center()
    hx, hy = _wxy(c, se.x(), se.y())
    ox, oy = _wxy(c, se.x() + 80, se.y() + 10)
    _press_move(c, hx, hy, ox, oy, mods=Qt.KeyboardModifier.ShiftModifier)
    el = dlg._preview_task["image_overlays"][0]["elements"][0]
    shifted = float(el["w"]) / float(el["h"])
    assert abs(shifted - aspect) / aspect > 0.05, shifted
    dlg.close()


def test_q81_param_column_max_width(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = _app()
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import load_theme_qss
    load_embedded_fonts()
    qss = load_theme_qss()
    if qss:
        app.setStyleSheet(qss)
    folder = tmp_path / "pics"
    folder.mkdir()
    png = _png(folder / "one.png", 400, 500, (220, 24, 24))
    _png(folder / "two_long_filename_picture.png", 120, 80, (20, 80, 200))
    _png(folder / "three.png", 90, 140, (200, 180, 20))
    dlg = _dialog(_task(), png, str(folder))
    dlg.thumb_grid.set_folder(str(folder))
    dlg.resize(2000, 900)
    dlg.show()
    QApplication.processEvents()
    dlg.resize(2000, 900)
    QApplication.processEvents()
    assert dlg.video_column.width() == 260, dlg.video_column.width()
    assert dlg.param_column.width() <= 420, dlg.param_column.width()
    assert dlg.preview_canvas.width() > 1000, dlg.preview_canvas.width()
    assert dlg.multi_section.isExpanded() is False

    # Natural window for the report shot, not the 2000px probe.
    dlg.resize(1440, 900)
    dlg.sp_x.setValue(120)
    dlg.sp_y.setValue(300)
    dlg.sp_scale.setValue(0.6)
    QApplication.processEvents()
    dlg.preview_canvas.repaint()
    QApplication.processEvents()
    shot = dlg.grab().toImage()
    assert not shot.isNull() and shot.width() > 800
    out = os.path.join(REPO, "docs", "plan", "reports", "Q81_batch_dialog.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    assert shot.save(out, "PNG"), out
    dlg.close()
