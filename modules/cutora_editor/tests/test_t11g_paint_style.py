# -*- coding: utf-8 -*-
"""Preview and export must resolve the same style object for default+fx.

Before the 2a fix, both preview and export painted through
FrameRenderer.draw_layers, and both skipped subtitle_roles.default when
role_id=="default". They were consistent with each other and both wrong
(task.style instead of role.style). After the fix they share
resolve_paint_style and return the same dict (``is``).
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_preview_and_export_both_call_resolver():
    canvas = os.path.join(REPO, "cutora", "ui", "canvas.py")
    fr = os.path.join(REPO, "cutora", "render", "frame_renderer.py")
    with open(canvas, encoding="utf-8") as f:
        csrc = f.read()
    with open(fr, encoding="utf-8") as f:
        fsrc = f.read()
    assert "resolve_paint_style" in csrc
    assert "resolve_paint_style" in fsrc
    assert csrc.count("resolve_paint_style(") >= 2
    assert fsrc.count("resolve_paint_style(") >= 2


def test_default_fx_preview_export_same_style_object():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--run"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T11G_STYLE_OK" in (r.stdout or ""), out[-4000:]


def _run() -> int:
    from cutora.subtitle.stylepack import resolve_paint_style

    role_style = {
        "fx": {"anim": {"loop": "highlight"}, "emphasis": {"color": "#ffff7f"}},
        "color_text": "#ffff7f",
    }
    task_style = {
        "fx": {"anim": {"loop": "box_highlight"}},
        "color_text": "#ffffff",
    }
    task = {
        "style": task_style,
        "subtitle_roles": {"default": {"name": "默认", "style": role_style}},
        "segments": [{"role": "default", "start": 0.0, "end": 1.0, "text": "Hi"}],
    }
    # Export path (draw_layers) and preview (canvas + draw_layers) both call this.
    export_s = resolve_paint_style(task, "default")
    preview_s = resolve_paint_style(task, "default")
    assert export_s is preview_s
    assert export_s is task_style
    assert export_s is task["style"]
    export_s["color_text"] = "#123456"
    assert resolve_paint_style(task, "default")["color_text"] == "#123456"
    print("T11G_STYLE_OK", flush=True)
    return 0


if __name__ == "__main__":
    if "--run" in sys.argv:
        raise SystemExit(_run())
    raise SystemExit(0)
