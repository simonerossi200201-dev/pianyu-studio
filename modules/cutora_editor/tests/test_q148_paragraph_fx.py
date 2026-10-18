# -*- coding: utf-8 -*-
"""Paragraph keyframes and the left-right contain split. No Qt at import."""
from __future__ import annotations


def test_locate_overlay_text_and_element():
    from cutora.core.cmds.light import _kf_host
    from cutora.render.keyframe import bake_host, locate_overlay, set_point

    text = {"type": "text", "x": 10, "y": 20, "rotation": 0, "opacity": 255, "start": 0}
    el = {"kind": "image", "x": 3, "y": 4}
    image = {"type": "image", "elements": [el], "scale": 1.0, "start": 0}
    task = {"text_overlays": [text], "image_overlays": [image]}

    assert locate_overlay(task, text) == {
        "target": "overlay", "list": "text_overlays", "overlay_index": 0,
    }
    found = locate_overlay(task, el)
    assert found["list"] == "image_overlays"
    assert found["overlay_index"] == 0
    assert found["element_index"] == 0
    assert locate_overlay(task, {"x": 1}) is None

    host, _tr = _kf_host(task, found)
    assert host is el

    set_point(text, "x", 0.0, 10)
    set_point(text, "x", 1.0, 50)
    baked = bake_host(text, 1.0)
    assert abs(float(baked["x"]) - 50.0) < 1e-6
    assert text["x"] == 10
    assert abs(float(bake_host(text, 0.0)["x"]) - 10.0) < 1e-6
