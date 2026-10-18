# -*- coding: utf-8 -*-
"""T04f-5 stickers: license, catalog, command, registry, fade inheritance."""
from __future__ import annotations

import ast
import json
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


def test_emoji_font_license_and_catalog_range():
    lic = os.path.join(REPO, "resources", "fonts", "Noto_Color_Emoji", "OFL.txt")
    text = open(lic, "r", encoding="utf-8").read()
    assert "SIL Open Font License" in text
    font = os.path.join(
        REPO, "resources", "fonts", "Noto_Color_Emoji",
        "NotoColorEmoji-COLRv1.ttf")
    assert os.path.isfile(font)
    cat = json.loads(open(
        os.path.join(REPO, "resources", "stickers", "catalog.json"),
        encoding="utf-8").read())
    n = len(cat.get("items") or [])
    assert n >= 1000, n
    assert "googlefonts/noto-emoji" in str(cat.get("source") or "")
    ids = [it["id"] for it in cat["items"]]
    assert "1f525" in ids


def test_sticker_command_headless_undoable(tmp_path):
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    spec = get_registry().get("overlay.insert_sticker")
    assert spec.headless is True
    assert spec.undoable is True
    proj = tmp_path / "st.asproj.json"
    data = {
        "version": 2,
        "tasks": [{
            "video": str(tmp_path / "clip.mp4"),
            "duration": 8.0,
            "image_overlays": [],
            "timeline": {"width": 1080, "height": 1920, "fps": 30,
                         "sources": {}, "tracks": []},
        }],
    }
    proj.write_text(json.dumps(data), encoding="utf-8")
    bus = CommandBus(get_registry())
    out = bus.execute(
        "overlay.insert_sticker",
        {
            "sticker_id": "1f525",
            "project": str(proj),
            "media": str(tmp_path / "clip.mp4"),
        },
        source="cli",
    )
    assert out.get("ok") is True
    saved = json.loads(proj.read_text(encoding="utf-8"))
    el = saved["tasks"][0]["image_overlays"][0]["elements"][0]
    assert el["sticker_id"] == "1f525"
    assert el["kind"] == "image"
    assert el["anim_in"] == "fade"
    assert abs(float(el["w"]) - 0.12 * 1080) < 1.0


def test_sticker_registry_widgets():
    from cutora.ui.panels.sticker_schema import (
        sticker_panel_widget_names, sticker_property_widget_names,
    )
    assert "sticker_search" in sticker_panel_widget_names()
    assert "ov_sticker_anim_in" in sticker_property_widget_names()
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_stickers.py"), "--panel"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F5_STICKER_PANEL_OK" in (r.stdout or ""), out[-4000:]


def test_sticker_fade_child():
    r = subprocess.run(
        [PY, os.path.join(REPO, "tests", "test_t04f_stickers.py"), "--raster"],
        cwd=REPO, env=_child_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F5_STICKER_RASTER_OK" in (r.stdout or ""), out[-6000:]


def test_cv2_threads_pinned_at_startup_not_glass_import():
    glass = open(
        os.path.join(REPO, "cutora", "render", "textfx", "glass_thick.py"),
        encoding="utf-8").read()
    runtime = open(
        os.path.join(REPO, "cutora", "core", "runtime.py"),
        encoding="utf-8").read()
    assert "setNumThreads" not in glass
    assert "pin_cv2_num_threads" in runtime
    assert "setNumThreads" in runtime


def test_export_modules_do_not_import_stickers_network():
    for rel in (
        "cutora/render/export_renderer.py",
        "cutora/render/render_worker.py",
    ):
        path = os.path.join(REPO, rel)
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "stickers" not in a.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "stickers" not in node.module


def _panel_suite() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.panels.sticker_panel import StickerPanel
    from cutora.ui.panels.sticker_schema import (
        sticker_panel_widget_names, sticker_property_widget_names,
    )

    app = QApplication.instance() or QApplication(["t04f5-sticker-panel"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    print("T04F5_PANEL_APP", flush=True)
    panel = PropertyPanel()
    print("T04F5_PROP_OK", flush=True)
    missing = [a for a in sticker_property_widget_names() if getattr(panel, a, None) is None]
    assert missing == [], missing
    sp = StickerPanel(populate=False)
    print("T04F5_STICKER_OBJ", flush=True)
    missing2 = [a for a in sticker_panel_widget_names() if getattr(sp, a, None) is None]
    assert missing2 == [], missing2
    assert sp.sticker_tabs.count() >= 8, sp.sticker_tabs.count()
    print("T04F5_STICKER_PANEL_OK", flush=True)
    return 0


def _raster_suite() -> int:
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
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.raster import as_bgra

    app = QApplication.instance() or QApplication(["t04f5-sticker-raster"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    cw, ch = 400, 400
    img = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    ov = {"x": 0, "y": 0, "w": cw, "h": ch, "opacity": 255, "type": "image"}
    el = {
        "kind": "image", "sticker_id": "1f525",
        "x": 80, "y": 80, "w": 240, "h": 240, "opacity": 255,
        "fade_top": 0.35, "fade_bottom": 0.0, "fade_left": 0.25, "fade_right": 0.0,
    }
    FrameRenderer.draw_sticker(p, ov, el)
    p.end()
    _o, bgra = as_bgra(img)
    # top-left of sticker should be faded (low alpha relative to center)
    # sticker at (80,80)-(320,320); sample inside top edge vs center
    a_top = int(bgra[88, 200, 3])
    a_mid = int(bgra[200, 200, 3])
    a_left = int(bgra[200, 88, 3])
    print("STICKER_FADE a_top", a_top, "a_mid", a_mid, "a_left", a_left, flush=True)
    assert a_mid > 80, a_mid
    assert a_top < a_mid, (a_top, a_mid)
    assert a_left < a_mid, (a_left, a_mid)
    from cutora.render.stickers import raster_sticker, clear_raster_cache
    clear_raster_cache()
    a = raster_sticker("1f525", 128)
    b = raster_sticker("1f525", 128)
    from cutora.render.textfx.raster import as_bgra as _bgra
    _, aa = _bgra(a)
    clear_raster_cache()
    c = raster_sticker("1f525", 128)
    _, cc = _bgra(c)
    assert aa.tobytes() == cc.tobytes()
    print("T04F5_STICKER_RASTER_OK", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--panel" in sys.argv:
        raise SystemExit(_panel_suite())
    if "--raster" in sys.argv:
        raise SystemExit(_raster_suite())
    raise SystemExit(0)
