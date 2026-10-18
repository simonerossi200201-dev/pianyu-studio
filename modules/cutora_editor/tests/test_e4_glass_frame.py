# -*- coding: utf-8 -*-
"""E4: glass backdrop copy only when a glass subtitle is on screen."""
from __future__ import annotations

from cutora.render.textfx.spec import frame_has_glass_bg, task_has_glass_bg


def _task():
    return {
        "style": {"fx": {"bg": {"type": "glass"}}},
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 2.0, "end": 3.0, "text": "b"},
        ],
    }


def test_task_has_glass_but_frame_without_seg_is_false():
    task = _task()
    assert task_has_glass_bg(task) is True
    assert frame_has_glass_bg(task, 0.5) is True
    assert frame_has_glass_bg(task, 1.5) is False
    assert frame_has_glass_bg(task, 2.5) is True


def test_no_glass_style_never():
    task = {"style": {"fx": {"bg": {"type": "box"}}}, "segments": [
        {"start": 0.0, "end": 1.0, "text": "a"},
    ]}
    assert task_has_glass_bg(task) is False
    assert frame_has_glass_bg(task, 0.2) is False
