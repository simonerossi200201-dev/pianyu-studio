# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q169 · 时间线：实色色块按轨道种类分色 / 轨道头带把手可拖 / 视频条缩略图真的显示。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402


def _app():
    app = QApplication.instance() or QApplication(["q169"])
    app.setStyle(QStyleFactory.create("Fusion"))
    from cutora.ui.theme.apply import apply_theme
    apply_theme("roma-gold")
    return app


def _hsv(c: QColor):
    return c.hueF() * 360.0 if c.hueF() >= 0 else 0.0, c.saturationF(), c.valueF()


def _hue_dist(a, b):
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def _widget_and_tracks():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget
    tl = Timeline(1080, 1920, 30)
    video, image, text, subs, audio = (Track("video"), Track("image"), Track("text"), Track("subs"), Track("audio"))
    video.clips.append(Clip("s1", 0.0, 6.0, 0.5))
    audio.clips.append(Clip("s1", 0.0, 6.0, 0.5))
    tl.tracks.extend([video, image, text, subs, audio])
    w = TimelineWidget()
    w.resize(760, 360)
    w.pixels_per_second = 40.0
    w.scroll_x = 0
    w.task_data = {
        "timeline": tl, "duration": 12.0,
        "segments": [{"start": 0.5, "end": 6.5, "text": "subtitle"}],
        "text_overlays": [{"start": 0.5, "end": 6.5, "content": "T", "_track_id": text.id}],
        "image_overlays": [{"start": 0.5, "end": 6.5, "path": "", "_track_id": image.id}],
    }
    w.duration = 12000
    return w, tl, {"video": video, "image": image, "text": text, "subs": subs, "audio": audio}


def _centre_color(w, img, track):
    rows = w._track_positions()["rows"]
    for t, ry, rh in rows:
        if t is track:
            # 片段从 0.5 s 起，取片段中间偏右一块的平均（避开左边的文件名 / 缩略图）
            x0 = int(3.5 * w.pixels_per_second)
            x1 = int(6.0 * w.pixels_per_second)
            y0, y1 = int(ry + 8), int(ry + rh - 8)
            acc = np.zeros(3)
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1, 3):
                    c = img.pixelColor(x, y)
                    acc += (c.red(), c.green(), c.blue())
                    n += 1
            m = acc / max(1, n)
            return QColor(int(m[0]), int(m[1]), int(m[2]))
    raise AssertionError("track row not found")


def test_clip_blocks_take_the_track_colour():
    """五种轨道五种实色：中心色 ≈ 对应 token，色相互不相同；字是 fg0 不是纯白。"""
    from cutora.ui.theme.tokens import current
    _app()
    w, tl, tr = _widget_and_tracks()
    w.show()
    QApplication.processEvents()
    w.repaint()
    QApplication.processEvents()
    img = w.grab().toImage()
    t = current()
    want = {"video": t.track_video, "image": t.track_image, "text": t.track_text,
            "subs": t.track_subtitle, "audio": t.track_audio}
    hues = {}
    for kind, track in tr.items():
        got = _centre_color(w, img, track)
        exp = QColor(want[kind])
        dist = ((got.red() - exp.red()) ** 2 + (got.green() - exp.green()) ** 2 + (got.blue() - exp.blue()) ** 2) ** 0.5
        assert dist < 40, (kind, got.name(), exp.name(), dist)
        h, s, v = _hsv(exp)
        assert 0.25 <= s <= 0.66 and 0.28 <= v <= 0.60, ("中等偏低饱和 / 亮度", kind, exp.name(), s, v)
        hues[kind] = h
    ks = list(hues)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            assert _hue_dist(hues[ks[i]], hues[ks[j]]) >= 25, (ks[i], ks[j], hues)
    # 白字对比度（fg0 压在五个色上都 ≥ 4.5）
    from cutora.ui.theme.contrast import contrast_ratio
    for kind, hx in want.items():
        assert contrast_ratio(t.fg0, hx) >= 4.5, (kind, hx, contrast_ratio(t.fg0, hx))
    # 没有纯白
    white = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixel(x, y) & 0xFFFFFF == 0xFFFFFF:
                white += 1
    assert white == 0
    w.close()


def test_selected_clip_is_lifted_and_outlined():
    from cutora.ui.theme.tokens import current
    _app()
    w, tl, tr = _widget_and_tracks()
    w.show()
    QApplication.processEvents()
    w.repaint()
    img0 = w.grab().toImage()
    c0 = _centre_color(w, img0, tr["video"])
    w.selected_video_clip = (tr["video"], tr["video"].clips[0])
    w.repaint()
    img1 = w.grab().toImage()
    c1 = _centre_color(w, img1, tr["video"])
    assert c1.valueF() > c0.valueF() + 0.05, (c0.name(), c1.name())
    # 描边是主题高亮色
    rows = w._track_positions()["rows"]
    ry = [r for t, r, h in rows if t is tr["video"]][0]
    x = int(0.5 * w.pixels_per_second)
    edge = img1.pixelColor(x, int(ry + 10))
    acc = QColor(current().accent_hi)
    assert _hue_dist(_hsv(edge)[0], _hsv(acc)[0]) < 25 and edge.saturationF() > 0.3, edge.name()
    w.close()


def test_video_thumb_queue_key_matches_paint_key(tmp_path, monkeypatch):
    """以前排队用写死的 28 行高、绘制用真实行高 → 缓存键永远对不上，视频条从来不显示缩略图。"""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.timeline import thumbs as T
    from cutora.ui.timeline.widget import TimelineWidget
    _app()
    if not os.path.isfile(G01):
        pytest.skip("g01 media missing")
    monkeypatch.setattr(T, "user_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(T, "extract_frame", lambda *a, **k: False)
    T._request_log.clear()
    tl = Timeline(1080, 1920, 30)
    src = MediaSource(G01, probe=False)
    tl.sources[src.id] = src
    tr = Track("video")
    tr.height = 64.0                                   # 不是默认行高
    clip = Clip(src.id, 0.0, 6.0, 0.0)
    tr.clips.append(clip)
    tl.tracks.append(tr)
    w = TimelineWidget()
    w.resize(800, 200)
    w.pixels_per_second = 60.0
    w.set_data(6000, {"timeline": tl, "segments": []})
    w.show()
    QApplication.processEvents()
    log = T.take_request_log()
    assert log, "set_data 没排缩略图"
    tw, th = TimelineWidget.video_thumb_size(64.0)
    assert (tw, th) == (64, 58)
    assert all((r[2], r[3]) == (tw, th) for r in log), log
    # 够宽的片段：片头 + 片尾两张
    ts = sorted({round(r[1], 2) for r in log})
    assert ts == [0.1, 5.9], ts
    # 把这两个键的缓存放好 → 画出来（片头红、片尾蓝）
    for t_sec, rgb in ((0.1, (255, 0, 0)), (5.9, (0, 0, 255))):
        dest = T.thumb_path(T.thumb_key(G01, t_sec, tw, th))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        im = QImage(tw, th, QImage.Format.Format_RGB32)
        im.fill(QColor(*rgb))
        assert im.save(dest, "JPG")
    w.repaint()
    QApplication.processEvents()
    img = w.grab().toImage()
    rows = w._track_positions()["rows"]
    ry = [r for t, r, h in rows if t is tr][0]
    y = int(ry + 20)
    head = img.pixelColor(6 + 20, y)
    tail = img.pixelColor(int(6.0 * 60.0) - 6 - 20, y)
    assert head.red() > 180 and head.green() < 80, head.name()
    assert tail.blue() > 180 and tail.red() < 80, tail.name()
    w.close()


def _mouse(typ, x, y, btn=Qt.MouseButton.LeftButton):
    return QMouseEvent(typ, QPointF(x, y), QPointF(x, y), btn,
                       btn if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)


def test_header_grip_drag_reorders_with_insertion_line():
    from types import SimpleNamespace
    from cutora.ui.timeline.header import TrackHeaderPanel
    _app()
    w, tl, tr = _widget_and_tracks()
    w.show()
    QApplication.processEvents()
    panel = TrackHeaderPanel(w)
    panel.resize(panel.PANEL_W, 360)
    panel.show()
    QApplication.processEvents()
    calls = []
    panel.window = lambda: SimpleNamespace(reorder_timeline_track=lambda s, d, after=False: calls.append((s, d, after)))
    rows = {t: (ry, rh) for t, ry, rh in w._track_positions()["rows"]}
    ty, th = rows[tr["text"]]
    # 按在把手上（最左 14px）
    panel.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 6, ty + th / 2))
    assert panel._drag_track is tr["text"] and not panel._drag_live
    # 小于阈值不算拖
    panel.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 6, ty + th / 2 + 2))
    assert not panel._drag_live
    # 拖到「图片」轨的上半 → 插到它前面，插入线在两行之间，时间线也画
    iy, ih = rows[tr["image"]]
    panel.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 6, iy + 3))
    assert panel._drag_live and panel._drag_target == (tr["image"], False)
    assert panel._drag_line_y is not None and panel._drag_line_y < iy
    assert w._track_drag_line_y == panel._drag_line_y
    panel.repaint()
    panel.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 6, iy + 3))
    assert calls == [(tr["text"], tr["image"], False)]
    assert panel._drag_track is None and w._track_drag_line_y is None
    # 拖到「字幕」轨下半 → 插到它后面
    calls.clear()
    panel.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 6, ty + th / 2))
    sy, sh = rows[tr["subs"]]
    panel.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 6, sy + sh - 3))
    assert panel._drag_target == (tr["subs"], True)
    panel.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 6, sy + sh - 3))
    assert calls == [(tr["text"], tr["subs"], True)]
    # 拖过了视觉域（到音频轨上）= 贴到视觉域最底：目标仍是最后一条视觉轨之后，不会插进音频域
    calls.clear()
    panel.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 6, ty + th / 2))
    ay, ah = rows[tr["audio"]]
    panel.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 6, ay + ah / 2))
    assert panel._drag_live and panel._drag_target == (tr["subs"], True)
    panel.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 6, ay + ah / 2))
    assert calls == [(tr["text"], tr["subs"], True)]
    # 放回原位（自己正下方那一行的上半）→ 不发命令
    calls.clear()
    panel.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 6, ty + th / 2))
    panel.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 6, sy + 3))
    assert panel._drag_target is None
    panel.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 6, sy + 3))
    assert calls == []
    panel.close()
    w.close()


def test_reorder_after_flag_and_noop():
    from test_q62_keyframes import _win_for_task
    from cutora.ui.command_bodies import CommandBodies
    _app()
    w, tl, tr = _widget_and_tracks()
    task = w.task_data
    win = _win_for_task(task)
    win.mark_dirty = lambda: None
    win.timeline = w
    v, i, t_, s, a = tr["video"], tr["image"], tr["text"], tr["subs"], tr["audio"]
    CommandBodies.reorder_timeline_track(win, t_, i, after=False)
    assert tl.tracks == [v, t_, i, s, a]
    n = len(win.history_mgr.history)
    CommandBodies.reorder_timeline_track(win, t_, s, after=True)
    assert tl.tracks == [v, i, s, t_, a] and len(win.history_mgr.history) == n + 1
    # 落回原位：不改、不记撤销
    CommandBodies.reorder_timeline_track(win, t_, s, after=True)
    assert tl.tracks == [v, i, s, t_, a] and len(win.history_mgr.history) == n + 1
    # 跨域拒绝
    CommandBodies.reorder_timeline_track(win, t_, a, after=True)
    assert tl.tracks == [v, i, s, t_, a]
    win.close()


def test_header_layout_has_grip_before_name():
    from cutora.ui.timeline.header import TrackHeaderPanel
    assert TrackHeaderPanel.GRIP_W >= 10
    assert TrackHeaderPanel.NAME_X >= TrackHeaderPanel.GRIP_W
    assert TrackHeaderPanel.BTN_X0 >= TrackHeaderPanel.NAME_X + TrackHeaderPanel.NAME_W
    # 7 个按钮放得下
    n = len(TrackHeaderPanel.BTN_DEFS)
    assert TrackHeaderPanel.BTN_X0 + n * (TrackHeaderPanel.BTN_SIZE + TrackHeaderPanel.BTN_GAP) <= TrackHeaderPanel.PANEL_W
