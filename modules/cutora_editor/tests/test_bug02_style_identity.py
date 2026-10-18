# -*- coding: utf-8 -*-
"""BUG-02: panel writes task.style; paint must read that same object."""
from __future__ import annotations

import json
import os

from cutora.subtitle.stylepack import (
    apply_pack_to_task,
    bind_default_paint_style,
    builtin_dir,
    list_pack_dirs,
    load_pack,
    resolve_paint_style,
)


def _glass():
    root = builtin_dir().replace("\\", "/")
    for path in list_pack_dirs():
        if path.replace("\\", "/").startswith(root) and path.rstrip("\\/").endswith("glass-white"):
            return load_pack(path)
    raise AssertionError("glass-white missing")


def test_load_keeps_role_fx_object():
    """g12-shaped fork: both have fx, contents differ. Paint stays on role.style."""
    role_style = {"color_text": "#ffffff", "fx": {"anim": {"in": "bullet_reveal"}}}
    task_style = {"color_text": "#BBBBBB", "fx": {"anim": {"in": "fade"}}}
    task = {
        "style": task_style,
        "subtitle_roles": {"default": {"style": role_style}},
    }
    # Fixture now stores the painted look on task.style; default has no override.
    task = {
        "style": role_style,
        "subtitle_roles": {"default": {"name": "默认"}},
    }
    painted = resolve_paint_style(task, "default")
    assert painted is role_style
    assert painted["color_text"] == "#ffffff"
    painted["color_text"] = "#00FFAA"
    assert task["style"]["color_text"] == "#00FFAA"


def test_fresh_load_knob_reaches_paint():
    """Open a forked project, do not apply a pack, write color_text as the panel does.

    Panel load_data(task['style']) keeps that dict. If bind left task.style
    pointing at a different object than resolve_paint_style, this goes red.
    """
    from cutora.model.project_io import _normalize_project_data

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "golden", "g12_vertical_fx_path", "project.asproj.json",
    )
    data = json.loads(open(path, encoding="utf-8").read())
    _normalize_project_data(data)
    task = data["tasks"][0]
    painted = resolve_paint_style(task, "default")
    assert painted is task["style"], (id(painted), id(task["style"]))
    task["style"]["color_text"] = "#00AAFF"
    assert resolve_paint_style(task, "default")["color_text"] == "#00AAFF"


def test_after_pack_paint_style_is_task_style():
    task = {"style": {}, "subtitle_roles": {"default": {"name": "默认", "style": {}}}, "dims": [1080, 1920]}
    apply_pack_to_task(task, _glass())
    painted = resolve_paint_style(task, "default")
    assert painted is task["style"]
    roles = task["subtitle_roles"]["default"]["style"]
    assert roles is task["style"]
    task["style"]["color_text"] = "#00FFAA"
    assert resolve_paint_style(task, "default")["color_text"] == "#00FFAA"


def test_forked_role_style_is_rebound():
    task_style = {"color_text": "#111111", "fx": {"bg": {"type": "none"}}}
    role_style = {"color_text": "#222222", "fx": {"bg": {"type": "glass"}}}
    task = {
        "style": task_style,
        "subtitle_roles": {"default": {"style": role_style}},
    }
    bind_default_paint_style(task)
    assert resolve_paint_style(task, "default") is task_style
    assert task["style"] is task_style
    assert task["subtitle_roles"]["default"]["style"] is task_style


def test_stylepack_apply_command_then_paint_sees_write():
    from cutora.core.commands import get_bus, reset_bus_for_tests

    class _Win:
        def __init__(self, task):
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = None

        def push_undo(self, *a, **k):
            return None

    pack = _glass()
    task = {"style": {"color_text": "#ffffff"}, "dims": [1080, 1920], "segments": []}
    win = _Win(task)
    reset_bus_for_tests()
    bus = get_bus()
    bus.bind_window(win)
    bus.execute("stylepack.apply", {"path": pack["path"]}, source="cli")
    painted = resolve_paint_style(task, "default")
    assert painted is task["style"]
    painted["color_text"] = "#ABCDEF"
    assert resolve_paint_style(task, "default")["color_text"] == "#ABCDEF"
    reset_bus_for_tests()
