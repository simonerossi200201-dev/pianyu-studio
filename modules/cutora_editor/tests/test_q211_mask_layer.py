# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""分屏上的全幅视频可以点中；作为蒙版 / 蒙版反相；跟踪能挂到不重叠的图层。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q211"])
    return app


def _split_task(scale=0.4):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(216, 384, 30)
    main = Track("video")
    main.is_main_video = True
    main.split_cell = "A"
    tl.add_track(main)
    main.clips.append(Clip("m", 0.0, 3.0, 0.0))
    ext = Track("video")
    ext.is_main_video = False
    tl.add_track(ext)
    over = Clip("e", 0.0, 3.0, 0.0)
    over.scale = scale
    ext.clips.append(over)
    task = {
        "timeline": tl,
        "dims": (216, 384),
        "split": {"enabled": True, "layout": "top_bottom"},
    }
    return task, main, ext


def test_split_overlay_hit_beats_the_cell():
    from cutora.batch import monitor_model as mm

    task, main, ext = _split_task(0.4)
    tr, cid, rect = mm.content_target(task, 108, 192, 1.0)
    assert tr is ext and cid is None
    assert rect[2] < 216 and rect[3] < 384          # 缩放过的画面框，不是整幅画布
    tr2, cid2, _r = mm.content_target(task, 5, 5, 1.0)
    assert tr2 is main and cid2 == "A"              # 框外仍是格子
    ext.locked = True
    assert mm.content_target(task, 108, 192, 1.0)[0] is main
    ext.locked = False
    ext.visible = False
    assert mm.content_target(task, 108, 192, 1.0)[0] is main


def test_sibling_keeps_external_slot_when_split():
    from cutora.batch import monitor_model as mm

    a, _ma, ea = _split_task()
    b, _mb, eb = _split_task()
    slot = mm.layer_slot(a, ea)
    assert slot == ("ext", 0)
    assert mm.sibling_video_track(b, None, slot) is eb
    assert mm.sibling_video_track(b, "A", None) is _mb
    assert mm.sibling_video_track(b, None, None) is None


def test_video_blend_items_add_matte_text_keeps_five():
    from cutora.ui.widgets.inspector import BLEND_ITEMS, VIDEO_BLEND_ITEMS

    assert [k for k, _l in BLEND_ITEMS] == ["normal", "multiply", "screen", "overlay", "additive"]
    keys = [k for k, _l in VIDEO_BLEND_ITEMS]
    assert keys[:5] == ["normal", "multiply", "screen", "overlay", "additive"]
    assert keys[-2:] == ["luma_matte", "luma_matte_inv"]
    assert dict(VIDEO_BLEND_ITEMS)["luma_matte"] == "作为蒙版"
    assert dict(VIDEO_BLEND_ITEMS)["luma_matte_inv"] == "蒙版反相"


def test_set_blend_writes_clip_track_and_props():
    from cutora.ui.monitor_layer_ops import set_blend

    _task, _main, ext = _split_task()
    n = set_blend(ext, "luma_matte")
    assert n == 1
    assert ext.clips[0].blend_mode == "luma_matte"
    assert ext.blend_mode == "luma_matte"
    assert ext.props["blend_mode"] == "luma_matte"
    assert set_blend(ext, "luma_matte") == 0


def _fill(img, color):
    from PySide6.QtGui import QColor
    img.fill(QColor(*color))


def _alpha(img, x=0, y=0):
    from PySide6.QtGui import QImage
    c = img.convertToFormat(QImage.Format.Format_ARGB32).pixelColor(x, y)
    return c.alpha(), c.red(), c.green(), c.blue()


def test_luma_coverage_white_keeps_black_punches_margin_does_not():
    _app()
    from PySide6.QtGui import QImage
    from cutora.render.frame_renderer import luma_coverage_image

    white = QImage(2, 2, QImage.Format.Format_ARGB32)
    _fill(white, (255, 255, 255, 255))
    assert _alpha(luma_coverage_image(white, invert=False))[0] == 255
    assert _alpha(luma_coverage_image(white, invert=True))[0] == 0

    black = QImage(2, 2, QImage.Format.Format_ARGB32)
    _fill(black, (0, 0, 0, 255))
    assert _alpha(luma_coverage_image(black, invert=False))[0] == 0
    assert _alpha(luma_coverage_image(black, invert=True))[0] == 255

    empty = QImage(2, 2, QImage.Format.Format_ARGB32)
    _fill(empty, (255, 255, 255, 0))
    assert _alpha(luma_coverage_image(empty, invert=False))[0] == 255
    assert _alpha(luma_coverage_image(empty, invert=True))[0] == 255


def test_luma_matte_punches_destination_classic_blend_does_not():
    _app()
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.frame_renderer import defer_luma_matte, paint_blended_video_clip, take_luma_note

    def _draw_black(p):
        from PySide6.QtGui import QColor
        p.fillRect(0, 0, 4, 4, QColor(0, 0, 0, 255))

    dest = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    _fill(dest, (255, 0, 0, 255))
    p = QPainter(dest)
    paint_blended_video_clip(p, "luma_matte", False, _draw_black, 4, 4)
    p.end()
    assert _alpha(dest)[0] == 0

    kept = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    _fill(kept, (255, 0, 0, 255))
    p = QPainter(kept)
    paint_blended_video_clip(p, "luma_matte_inv", False, _draw_black, 4, 4)
    p.end()
    assert _alpha(kept)[0] == 255
    assert _alpha(kept)[1] == 255

    over = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    _fill(over, (255, 0, 0, 255))
    p = QPainter(over)
    with defer_luma_matte():
        paint_blended_video_clip(p, "luma_matte", False, _draw_black, 4, 4)
    p.end()
    assert _alpha(over)[0] == 255
    assert _alpha(over)[2] == 0 and _alpha(over)[3] == 0   # 推迟时留下黑画面，不挖

    noted = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    _fill(noted, (0, 0, 0, 0))
    p = QPainter(noted)
    paint_blended_video_clip(p, "luma_matte", True, _draw_black, 4, 4)
    p.end()
    assert take_luma_note() == "luma_matte"
    assert _alpha(noted)[0] == 255

    normal = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    normal.fill(0)
    p = QPainter(normal)

    def _draw_blue(painter):
        from PySide6.QtGui import QColor
        painter.fillRect(0, 0, 4, 4, QColor(0, 0, 255, 255))

    paint_blended_video_clip(p, "normal", False, _draw_blue, 4, 4)
    p.end()
    a, r, g, b = _alpha(normal)
    assert a == 255 and b == 255 and r == 0


def test_follow_targets_include_nonoverlapping_mask_clip():
    from cutora.fx.track_bind import list_follow_targets
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(100, 100, 30)
    host_tr = Track("video")
    tl.add_track(host_tr)
    host = Clip("h", 0.0, 1.0, 0.0)
    host.name = "host"
    host_tr.clips.append(host)
    mask_tr = Track("video")
    tl.add_track(mask_tr)
    mask = Clip("k", 0.0, 1.0, 50.0)     # 和宿主没有时间重叠
    mask.name = "mask"
    mask.blend_mode = "luma_matte"
    mask_tr.clips.append(mask)
    task = {
        "timeline": tl,
        "text_overlays": [{"id": 7, "content": "标题", "start": 80.0, "end": 81.0}],
        "image_overlays": [{"id": 3, "type": "adjust", "name": "模糊", "start": 0.0, "end": 1.0}],
    }
    rows = list_follow_targets(task, host)
    roles = [(r["kind"], r["role"], r.get("name")) for r in rows]
    assert ("overlay", "text", "标题") in [(a, b, c) for a, b, c in roles]
    assert any(r["role"] == "adjust" for r in rows)
    assert any(r["kind"] == "clip" and r["name"] == "mask" for r in rows)
    assert all(not (r["kind"] == "clip" and r["name"] == "host") for r in rows)
