# -*- coding: utf-8 -*-
"""E5: empty project canvas follows first source; open prompts if mismatch."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_on_drop_applies_first_source_canvas():
    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    assert "_apply_canvas_from_first_source" in src
    assert "apply_canvas = not self.tasks" in src
    assert "apply_canvas=apply_canvas and idx == 0" in src
    assert "_prompt_canvas_if_sources_mismatch" in src
    bodies = open(
        os.path.join(REPO, "cutora", "ui", "command_bodies.py"), encoding="utf-8"
    ).read()
    assert "_prompt_canvas_if_sources_mismatch" in bodies
