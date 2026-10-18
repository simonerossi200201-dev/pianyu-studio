# -*- coding: utf-8 -*-
"""T20c: standalone transition library actually composes; export uses start_t."""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage, QPainter

from cutora.render import anim_engine
from cutora.model.timeline_model import Clip, TransitionItem


def _solid(w, h, rgb):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rgb))
    return img


def test_t20c_library_all_compose():
    names = [p for _lab, p in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    assert "cross_dissolve" in names
    assert "fade_dark" in names
    assert "wipe_left" in names
    out_img = _solid(64, 64, (255, 0, 0))
    in_img = _solid(64, 64, (0, 0, 255))
    for preset in names:
        comp = anim_engine.compose_standalone_transition(
            0.5, preset, out_img, in_img, 64, 64)
        assert comp is not None and not comp.isNull(), preset
        assert comp.width() == 64 and comp.height() == 64


def test_t20c_fade_dark_is_not_pure_black():
    assert anim_engine.FADE_DARK_RGB == (0x0B, 0x0B, 0x0E)


def test_t20c_export_uses_start_t_not_start_time():
    src = open(
        __file__.replace("tests\\test_t20c_transitions.py", "cutora/render/render_worker.py")
        .replace("tests/test_t20c_transitions.py", "cutora/render/render_worker.py"),
        encoding="utf-8",
    ).read()
    assert 'getattr(clip, "start_t"' in src
    assert "_paint_export_transition" in src
    assert "compose_standalone_transition" in src or "paint_standalone_transition" in src


def test_t20c_transition_item_window():
    t = TransitionItem(start_t=2.0, duration=0.35, preset="cross_dissolve")
    assert abs(t.end_t - 2.35) < 1e-9
    c = Clip("s", 0, 4, 0)
    assert abs(c.start_t) < 1e-9
