# -*- coding: utf-8 -*-
"""T04f-5 platform safe zones: data-driven, preview-only, export-identical."""
from __future__ import annotations

import ast
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")
FFMPEG_DIR = os.path.join(REPO, "runtime", "ffmpeg")


def _child_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("PYTEST") or key == "CUTORA_PYTEST_ISOLATED":
            env.pop(key, None)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    path = env.get("PATH", "")
    if os.path.normcase(FFMPEG_DIR) not in os.path.normcase(path):
        env["PATH"] = FFMPEG_DIR + os.pathsep + path
    return env


def test_safe_zone_json_and_no_magic_in_loader():
    from cutora.render.safe_zones import load_safe_zones, presets
    data = load_safe_zones()
    ids = {p["id"] for p in presets(data)}
    need = {
        "youtube_landscape", "youtube_shorts", "tiktok",
        "instagram_reels", "instagram_square",
    }
    assert need <= ids, ids
    for p in presets(data):
        u = p["unsafe"]
        for k in ("top", "bottom", "left", "right"):
            assert k in u
        assert p.get("derived_on")
        assert p.get("source")
        assert p.get("aspect")
    src = open(
        os.path.join(REPO, "cutora", "render", "safe_zones.py"),
        encoding="utf-8").read()
    assert "0.22" not in src
    assert "0.18" not in src


def test_export_path_does_not_import_safe_zones():
    for rel in (
        "cutora/render/export_renderer.py",
        "cutora/render/frame_renderer.py",
        "cutora/render/render_worker.py",
    ):
        path = os.path.join(REPO, rel)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "safe_zones" not in node.module, rel
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "safe_zones" not in a.name, rel


def test_safe_zone_widgets_and_export_bytes():
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_safe_zones.py"), "--all"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F5_SAFE_OK" in (r.stdout or ""), out[-6000:]


def test_nudge_clears_bottom_band():
    from cutora.render.safe_zones import Band, nudge_center_y
    ch = 1920
    box = (200.0, 1500.0, 680.0, 200.0)
    bands = [Band("tiktok", "TikTok", "bottom", "TikTok 底部 UI", "#A78BFA",
                  0.0, 1536.0, 1080.0, 384.0)]
    ny = nudge_center_y(box, ch, bands)
    assert ny is not None
    new_bottom = ny + 100.0
    assert new_bottom <= 1536.0 + 0.5, (ny, new_bottom)


def _all_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    from cutora.ui.panels.safe_zone_schema import (
        required_preset_ids, safe_zone_caption_widget_names,
        safe_zone_window_widget_names,
    )
    from cutora.ui.panels.sticker_schema import sticker_property_widget_names
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.safe_zones import SETTINGS_MASTER, SETTINGS_PRESETS
    from cutora.core.history import _load_app_settings, _save_app_settings
    from cutora.render.textfx.raster import as_bgra
    from cutora.model.timeline import Timeline
    app = QApplication.instance() or QApplication(["t04f5-safe"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    panel = SubtitleStylePanel()
    missing = [a for a in safe_zone_caption_widget_names()
               if getattr(panel, a, None) is None]
    assert missing == [], missing
    prop = PropertyPanel()
    missing_p = [a for a in sticker_property_widget_names()
                 if getattr(prop, a, None) is None]
    assert missing_p == [], missing_p
    assert set(required_preset_ids()) >= {
        "youtube_landscape", "youtube_shorts", "tiktok",
        "instagram_reels", "instagram_square",
    }

    task = {
        "style": {
            "fontsize": 64, "pos_x": 540, "pos_y": 1600,
            "font_family": "Arimo", "bold": True,
            "color_text": "#F4EBD8",
        },
        "segments": [{
            "text": "hello studio", "start": 0.0, "end": 2.0, "id": "s0",
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "studio", "start": 0.4, "end": 0.8},
            ],
        }],
        "timeline": Timeline(1080, 1920, 30),
        "text_overlays": [],
        "image_overlays": [],
    }
    prev = _load_app_settings() or {}
    try:
        on_settings = dict(prev)
        on_settings[SETTINGS_MASTER] = True
        on_settings[SETTINGS_PRESETS] = {
            "tiktok": True, "youtube_shorts": True,
        }
        off_settings = dict(prev)
        off_settings[SETTINGS_MASTER] = False
        off_settings[SETTINGS_PRESETS] = {}
        _save_app_settings(on_settings)
        a = ExportRenderer.render_frame(task, 1080, 1920, 0.5)
        _save_app_settings(off_settings)
        b = ExportRenderer.render_frame(task, 1080, 1920, 0.5)
        _oa, va = as_bgra(a)
        _ob, vb = as_bgra(b)
        assert va.tobytes() == vb.tobytes()

        # preview chrome DOES differ
        from cutora.ui.canvas import VideoCanvas
        img_on = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
        img_on.fill(QColor("#0B1A33"))
        p = QPainter(img_on)
        _save_app_settings(on_settings)
        VideoCanvas._draw_safe_zones(None, p, 1080, 1920)
        p.end()
        img_off = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
        img_off.fill(QColor("#0B1A33"))
        p2 = QPainter(img_off)
        _save_app_settings(off_settings)
        VideoCanvas._draw_safe_zones(None, p2, 1080, 1920)
        p2.end()
        _oc, vc = as_bgra(img_on)
        _od, vd = as_bgra(img_off)
        assert vc.tobytes() != vd.tobytes()
    finally:
        _save_app_settings(prev)

    names = safe_zone_window_widget_names()
    assert "act_safe_zones" in names
    print("T04F5_SAFE_OK", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--all" in sys.argv:
        raise SystemExit(_all_suite())
    raise SystemExit(0)
