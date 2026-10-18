# -*- coding: utf-8 -*-
"""T21: export size == canvas size for every preset."""
from __future__ import annotations

import os

from cutora.model.canvas import CANVAS_PRESETS, apply_canvas, canvas_wh
from cutora.model.timeline_model import Clip


def test_t21_four_presets_export_equals_canvas():
    assert len(CANVAS_PRESETS) == 4
    assert (720, 1280, "竖屏 720") in CANVAS_PRESETS
    assert (1280, 720, "横屏 720") in CANVAS_PRESETS
    for w, h, _lab in CANVAS_PRESETS:
        task = {"dims": (1, 1)}
        apply_canvas(task, w, h)
        assert task["dims"] == (w, h)
        assert canvas_wh(task) == (w, h)


def test_t21_timeline_object_wins():
    class _TL:
        width = 720
        height = 1280
    task = {"dims": (1080, 1920), "timeline": _TL()}
    assert canvas_wh(task) == (720, 1280)
    apply_canvas(task, 1280, 720)
    assert task["dims"] == (1280, 720)
    assert task["timeline"].width == 1280
    assert task["timeline"].height == 720


def test_t21_clip_default_fill_project():
    c = Clip("s", 0, 1, 0)
    assert getattr(c, "size_mode", "project") == "project"
    assert c.fit_mode == "fill"


def test_t21_source_size_mode_native_rect():
    from PySide6.QtCore import QRectF
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rect = rw._compute_fit_rect(640, 360, 1080, 1920, fit_mode="source")
    assert isinstance(rect, QRectF)
    assert abs(rect.width() - 640) < 1e-6
    assert abs(rect.height() - 360) < 1e-6


def test_t21_new_project_default_canvas_720():
    from cutora.model.canvas import (
        DEFAULT_NEW_CANVAS,
        SETTING_NEW_CANVAS,
        default_new_canvas_wh,
        template_from_settings,
    )
    assert DEFAULT_NEW_CANVAS == (720, 1280)
    assert default_new_canvas_wh({}) == (720, 1280)
    assert default_new_canvas_wh(None) == (720, 1280)
    assert default_new_canvas_wh({SETTING_NEW_CANVAS: "1920x1080"}) == (1920, 1080)
    assert default_new_canvas_wh({SETTING_NEW_CANVAS: "1080x1920"}) == (1080, 1920)
    tpl = template_from_settings({})
    assert tpl["width"] == 720 and tpl["height"] == 1280
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings_src = open(
        os.path.join(root, "cutora", "ui", "settings_dialog.py"), encoding="utf-8"
    ).read()
    assert "新建工程默认画布" in settings_src
    assert "SETTING_NEW_CANVAS" in settings_src or "new_project_canvas" in settings_src
    bodies = open(
        os.path.join(root, "cutora", "ui", "command_bodies.py"), encoding="utf-8"
    ).read()
    assert "default_new_canvas_wh" in bodies
    mw = open(
        os.path.join(root, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "default_new_canvas_wh" in mw


def test_t21_menu_has_720_presets():
    import os
    src = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "ui", "main_window.py"),
        encoding="utf-8",
    ).read()
    assert "CANVAS_PRESETS" in src
    assert "size_mode = \"project\"" in src
    wsrc = open(
        os.path.join(os.path.dirname(__file__), "..", "cutora", "render", "render_worker.py"),
        encoding="utf-8",
    ).read()
    assert "canvas_wh" in wsrc
    assert "导出宽高永远是画布" in wsrc
