# -*- coding: utf-8 -*-
"""Overlay color / bg_enabled / stroke_width must survive open→save→open + paint.

T11a scope: STYLE_KEY_ALIASES folds subtitle dicts only. Overlay uses those
seven names as its own canonical keys. A migrate on text_overlays would
strip box and stroke; golden would still be green.
"""
from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")

COLOR = "#E81CFF"
BG = "#FF00AA"
STROKE = "#00E5FF"
STROKE_W = 10


def _overlay():
    return {
        "type": "text",
        "content": "Hi",
        "x": 80,
        "y": 80,
        "w": 480,
        "h": 220,
        "color": COLOR,
        "bg_enabled": True,
        "bg_color": BG,
        "bg_opacity": 255,
        "bg_padding": 24,
        "bg_radius": 4,
        "use_stroke": True,
        "stroke_width": STROKE_W,
        "stroke_color": STROKE,
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "start": 0.0,
        "end": 4.0,
        "opacity": 255,
    }


def _project(ov):
    return {
        "version": 2,
        "tasks": [{
            "style": {},
            "text_overlays": [ov],
            "timeline": {
                "width": 640, "height": 360, "fps": 30,
                "sources": {}, "tracks": [],
            },
            "segments": [],
        }],
    }


def _assert_overlay_keys(ov):
    assert ov["color"] == COLOR
    assert ov["bg_enabled"] is True
    assert ov["stroke_width"] == STROKE_W
    assert "color_text" not in ov
    assert "use_box" not in ov
    assert "border_width" not in ov


def test_overlay_roundtrip_keeps_overlay_names():
    from cutora.model.project_io import _normalize_project_data

    data = _project(_overlay())
    _normalize_project_data(data)
    ov = data["tasks"][0]["text_overlays"][0]
    _assert_overlay_keys(ov)
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
    again = json.loads(blob)
    _normalize_project_data(again)
    ov2 = again["tasks"][0]["text_overlays"][0]
    _assert_overlay_keys(ov2)
    for key in ("color", "bg_enabled", "stroke_width"):
        assert json.dumps(ov2[key]) == json.dumps(ov[key])


def _child_env():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_overlay_paint_box_and_stroke_after_roundtrip():
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--paint"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T11_OV_PAINT_OK" in (r.stdout or ""), out[-6000:]


def _count_magenta_cyan(view):
    # view is BGRA
    b, g, r = view[:, :, 0], view[:, :, 1], view[:, :, 2]
    mag = ((r > 180) & (b > 80) & (g < 80)).sum()
    cyan = ((g > 180) & (b > 180) & (r < 80)).sum()
    return int(mag), int(cyan)


def _paint(ov):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.raster import as_bgra

    img = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    FrameRenderer.draw_single_overlay(p, ov, current_time=1.0)
    p.end()
    _owned, view = as_bgra(img)
    return _count_magenta_cyan(view)


def _run_paint() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.model.project_io import _normalize_project_data
    from cutora.subtitle.style_schema import migrate_subtitle_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    app = QApplication.instance() or QApplication(["t11-ov-alias"])
    load_embedded_fonts()

    data = _project(_overlay())
    _normalize_project_data(data)
    blob = json.dumps(data, ensure_ascii=False)
    again = json.loads(blob)
    _normalize_project_data(again)
    ov = again["tasks"][0]["text_overlays"][0]
    _assert_overlay_keys(ov)
    mag, cyan = _paint(ov)
    assert mag > 200, mag
    assert cyan > 40, cyan

    bad = json.loads(json.dumps(ov))
    migrate_subtitle_style(bad)
    mag_bad, cyan_bad = _paint(bad)
    assert mag_bad == 0, mag_bad
    assert cyan_bad == 0, cyan_bad
    print("T11_OV_PAINT_OK mag=%d cyan=%d" % (mag, cyan), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--paint" in sys.argv:
        raise SystemExit(_run_paint())
    raise SystemExit("usage: --paint")
