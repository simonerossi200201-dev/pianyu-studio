# -*- coding: utf-8 -*-
"""BUG-10: thumbnails generated off the paint path."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug10-thumbs"])


def test_set_data_queues_thumb_request(qapp, tmp_path, monkeypatch):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.timeline import thumbs as T
    from cutora.ui.timeline.widget import TimelineWidget

    monkeypatch.setattr(T, "user_cache_dir", lambda: str(tmp_path))
    T._request_log.clear()
    tl = Timeline(1080, 1920, 30)
    src = MediaSource(G01) if os.path.isfile(G01) else None
    tr = Track("video")
    clip = Clip("src", 0.0, 2.0, 0.0)
    if src is not None:
        clip.source_id = src.id
        tl.sources[src.id] = src
        clip.source_id = src.id
    tr.clips.append(clip)
    tl.tracks.append(tr)
    if src is not None:
        clip.source_id = src.id
    w = TimelineWidget()
    w.resize(640, 200)
    w.set_data(2000, {"timeline": tl, "segments": []})
    w.show()
    qapp.processEvents()
    log = T.take_request_log()
    w.close()
    if src is None:
        pytest.skip("g01 media missing")
    assert log, "set_data did not queue get_clip_thumb request"
    assert log[0][0] == G01


def test_peek_paint_uses_cached_jpeg(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.timeline import thumbs as T
    from cutora.ui.timeline.widget import TimelineWidget

    monkeypatch.setattr(T, "user_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(T, "request_clip_thumb", lambda *a, **k: None)
    monkeypatch.setattr(T, "extract_frame", lambda *a, **k: False)
    if not os.path.isfile(G01):
        pytest.skip("g01 media missing")
    src = MediaSource(G01)
    thumb_h = max(16, int(TimelineWidget.TRACK_H) - 6)
    key = T.thumb_key(G01, 0.1, 64, thumb_h)
    assert key
    dest = T.thumb_path(key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    img = QImage(64, thumb_h, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 0, 0))
    assert img.save(dest, "JPG")
    tl = Timeline(1080, 1920, 30)
    tr = Track("video")
    clip = Clip("src", 0.0, 4.0, 0.0)
    clip.source_id = src.id
    tl.sources[src.id] = src
    tr.clips.append(clip)
    tl.tracks.append(tr)
    w = TimelineWidget()
    w.resize(800, 200)
    w.pixels_per_second = 80.0
    w.set_data(4000, {"timeline": tl, "segments": []})
    w.show()
    qapp.processEvents()
    pix = w.grab()
    w.close()
    qimg = pix.toImage()
    # clip starts at x≈0 in content; look for a red-ish pixel in the first row of clips
    found = False
    for y in range(28, min(60, qimg.height())):
        for x in range(4, min(80, qimg.width())):
            c = qimg.pixelColor(x, y)
            if c.red() > 180 and c.green() < 80 and c.blue() < 80:
                found = True
                break
        if found:
            break
    assert found, "cached thumb pixels not painted"


def test_prune_thumb_cache_deletes_oldest(tmp_path, monkeypatch):
    from cutora.ui.timeline import thumbs as T
    monkeypatch.setattr(T, "user_cache_dir", lambda: str(tmp_path))
    d = T.thumbs_dir()
    a = os.path.join(d, "old.jpg")
    b = os.path.join(d, "new.jpg")
    open(a, "wb").write(b"x" * 200)
    time.sleep(0.05)
    open(b, "wb").write(b"y" * 200)
    os.utime(a, (1, 1))
    removed = T.prune_thumb_cache(max_bytes=250)
    assert removed >= 1
    assert not os.path.isfile(a)
    assert os.path.isfile(b)
