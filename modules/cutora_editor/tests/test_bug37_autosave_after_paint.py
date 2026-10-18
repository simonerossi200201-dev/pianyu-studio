# -*- coding: utf-8 -*-
"""BUG-37: autosave must survive a painted subtitle (QRectF on the segment)."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug37-autosave"])


def _paint_one_frame(event):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    w, h = 640, 360
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#1E3A5F"))
    style = {
        "font_family": "Arimo", "fontsize": 36,
        "pos_x": w // 2, "pos_y": h // 2,
        "color_text": "#FFFFFF",
        "fx": {
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "bg": {"type": "none"},
        },
    }
    painter = QPainter(img)
    try:
        FrameRenderer.draw_subtitle(
            painter, event, style, 0.5, w, h, seg_idx=0)
    finally:
        painter.end()
    return event


def test_autosave_after_paint(tmp_path, monkeypatch, qapp):
    """Load-like task → offscreen paint (creates word hits) → autosave writes."""
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    from cutora.core.autosave import reset_throttle_for_tests, sidecar_path
    reset_throttle_for_tests()

    event = {
        "start": 0.0, "end": 2.0, "text": "Hello world",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 1.0},
            {"word": "world", "start": 1.0, "end": 2.0},
        ],
        "role": "default",
    }
    _paint_one_frame(event)

    proj = str(tmp_path / "film.asproj.json")
    open(proj, "w", encoding="utf-8").write("{}")

    from cutora.ui.command_bodies import CommandBodies
    host = CommandBodies.__new__(CommandBodies)
    host.project_path = proj
    host.tasks = [{"video": "a.mp4", "duration": 2.0, "segments": [event],
                   "text": "Hello world"}]
    host.collect_project_data = lambda: {
        "version": 2,
        "tasks": [{"video": "a.mp4", "duration": 2.0,
                   "segments": host.tasks[0]["segments"],
                   "text": "Hello world"}],
    }
    side = host._autosave_now(force=True)
    assert side and os.path.isfile(side), side
    data = json.loads(open(side, encoding="utf-8").read())
    segs = (data.get("tasks") or [{}])[0].get("segments") or []
    assert len(segs) == 1, segs
    assert segs[0].get("text") == "Hello world"
    assert sidecar_path(proj) == side


def _rect_key(hit):
    rect = hit.get("rect")
    assert rect is not None, hit
    return (
        round(float(rect.x()), 1),
        round(float(rect.y()), 1),
        round(float(rect.width()), 1),
        round(float(rect.height()), 1),
        int(hit.get("word_idx", -1)),
        int(hit.get("char_start", -1)),
        int(hit.get("char_end", -1)),
    )


def test_sub_word_hits_still_readable_after_paint(qapp):
    """BUG-37 sidecar must still feed shift-click word selection after a paint."""
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.pipeline import _WORD_HITS, _fill_word_hits
    from cutora.render.textfx.spec import from_style
    from cutora.ui.canvas import VideoCanvas

    w, h = 640, 360
    event = {
        "start": 0.0, "end": 2.0, "text": "Hello world",
        "words": [
            {"word": "Hello", "start": 0.0, "end": 1.0},
            {"word": "world", "start": 1.0, "end": 2.0},
        ],
        "role": "default",
    }
    style = {
        "font_family": "Arimo", "fontsize": 36,
        "pos_x": w // 2, "pos_y": h // 2,
        "color_text": "#FFFFFF",
        "fx": {
            "fill": {"type": "solid", "colors": ["#FFFFFF"]},
            "bg": {"type": "none"},
        },
    }
    spec = from_style(style)
    layout = layout_event(event, style, spec, w, h)
    before = _fill_word_hits(event, layout, 0, seg_idx=0)
    assert len(before) >= 2, before
    before_keys = [_rect_key(h) for h in before]
    _WORD_HITS.clear()

    task = {
        "duration": 2.0,
        "segments": [event],
        "text": "Hello world",
        "style": style,
        "dims": (w, h),
    }
    bg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bg.fill(QColor("#1E3A5F"))
    canvas = VideoCanvas()
    try:
        canvas.resize(w, h)
        canvas._video_dims = (w, h)
        canvas.set_program_frame(bg)
        canvas.set_data(task, 0.5)
        canvas.show()
        qapp.processEvents()
        canvas.repaint()
        qapp.processEvents()
        item = {"type": "sub", "idx": 0}
        hits = canvas._get_sub_word_hits(item)
        assert len(hits) == len(before), (len(hits), len(before), hits)
        assert [_rect_key(h) for h in hits] == before_keys
        canvas.repaint()
        qapp.processEvents()
        again = canvas._get_sub_word_hits(item)
        assert [_rect_key(h) for h in again] == before_keys
    finally:
        canvas.close()
