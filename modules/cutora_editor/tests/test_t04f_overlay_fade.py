# -*- coding: utf-8 -*-
"""T04f-2b overlay four-edge fade: registry, rule A, banding, command."""
from __future__ import annotations

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


def test_overlay_fade_registry_widgets():
    from cutora.ui.panels.overlay_fade_schema import overlay_fade_widget_names
    names = overlay_fade_widget_names()
    assert "ov_fade_top" in names
    assert "vid_fade_right" in names
    assert "ov_fade_sync" in names
    env = _child_env()
    cmd = [PY, os.path.join(REPO, "tests", "test_t04f_overlay_fade.py"), "--panel"]
    r = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F_2B_PANEL_OK" in (r.stdout or ""), out[-4000:]


def test_overlay_fade_render_child():
    env = _child_env()
    cmd = [PY, os.path.join(REPO, "tests", "test_t04f_overlay_fade.py"), "--raster"]
    r = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0 and "T04F_2B_RASTER_OK" in (r.stdout or ""), out[-6000:]


def test_overlay_set_fade_command_headless(tmp_path):
    import json
    from cutora.core.commands import CommandBus, get_registry, reset_bus_for_tests
    reset_bus_for_tests()
    proj = tmp_path / "fade.asproj.json"
    data = {
        "version": 2,
        "tasks": [{
            "video": str(tmp_path / "clip.mp4"),
            "image_overlays": [{
                "type": "image", "x": 0, "y": 0, "w": 200, "h": 100,
                "elements": [{
                    "kind": "shape", "shape": "rect",
                    "x": 0, "y": 0, "w": 200, "h": 100,
                    "fill_color": "#22D3EE",
                }],
            }],
            "timeline": {
                "width": 640, "height": 360, "fps": 30,
                "sources": {}, "tracks": [{
                    "id": "tr_img", "type": "image",
                    "clips": [],
                }],
            },
        }],
    }
    proj.write_text(json.dumps(data), encoding="utf-8")
    bus = CommandBus(get_registry())
    out = bus.execute(
        "overlay.set_fade",
        {
            "target": "element",
            "overlay_index": 0,
            "element_index": 0,
            "fade_top": 0.25,
            "fade_bottom": 0.0,
            "fade_left": 0.1,
            "fade_right": 0.0,
            "project": str(proj),
            "media": str(tmp_path / "clip.mp4"),
        },
        source="cli",
    )
    assert out.get("ok") is True
    saved = json.loads(proj.read_text(encoding="utf-8"))
    el = saved["tasks"][0]["image_overlays"][0]["elements"][0]
    assert abs(float(el["fade_top"]) - 0.25) < 1e-9
    assert abs(float(el["fade_left"]) - 0.1) < 1e-9


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
    from cutora.ui.panels.overlay_fade_schema import overlay_fade_widget_names

    app = QApplication.instance() or QApplication(["t04f-2b-panel"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)
    panel = PropertyPanel()
    missing = [a for a in overlay_fade_widget_names() if getattr(panel, a, None) is None]
    assert missing == [], missing
    assert panel.ov_fade_top.maximum() == 100
    assert panel.vid_fade_right.maximum() == 100
    assert panel.ov_fade_top_offset.minimum() == -100
    assert panel.vid_fade_left_offset.maximum() == 100
    assert panel.ov_feather.isHidden()
    assert panel.ov_feather_sl.isHidden()
    assert panel.vid_feather.isHidden()
    assert panel.vid_feather_sl.isHidden()
    print("T04F_2B_PANEL_OK", flush=True)
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
    from cutora.render.textfx.raster import as_bgra as raster_as_bgra

    app = QApplication.instance() or QApplication(["t04f-2b-raster"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    import tempfile
    solid = QImage(200, 400, QImage.Format.Format_ARGB32_Premultiplied)
    solid.fill(QColor("#22D3EE"))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    solid.save(tmp.name)

    def paint_block(el, fill="#F4EBD8", cw=220, ch=160):
        img = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(fill) if fill else 0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ov = {"x": 0, "y": 0, "w": cw, "h": ch, "opacity": 255, "type": "image"}
        FrameRenderer.draw_sticker(p, ov, el)
        p.end()
        return img

    el0 = {
        "kind": "image", "path": tmp.name,
        "x": 10, "y": 10, "w": 200, "h": 140,
        "opacity": 255,
    }
    a = paint_block(dict(el0))
    b = paint_block(dict(el0, fade_top=0.0, fade_bottom=0.0, fade_left=0.0, fade_right=0.0))
    _oa, va = raster_as_bgra(a)
    _ob, vb = raster_as_bgra(b)
    assert va.tobytes() == vb.tobytes()

    tall = {
        "kind": "image", "path": tmp.name,
        "x": 20, "y": 0, "w": 200, "h": 400,
        "opacity": 255,
        "fade_top": 0.5, "fade_bottom": 0.0, "fade_left": 0.0, "fade_right": 0.0,
    }
    faded = paint_block(tall, fill=None, cw=240, ch=400)
    _of, vf = raster_as_bgra(faded)
    col = vf[0:200, 120, 3].astype("int16")
    diffs = abs(col[1:] - col[:-1])
    assert int(diffs.max()) <= 2, int(diffs.max())
    assert float(diffs[:5].mean()) <= 1.5, float(diffs[:5].mean())
    assert int(vf[0, 120, 3]) < 8
    assert int(vf[220, 120, 3]) > 200

    from cutora.render.textfx.fade import apply_edge_fade
    src = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
    src.fill(QColor(34, 211, 238, 255))
    _os, src_bytes = raster_as_bgra(src)
    before = src_bytes.tobytes()
    out = apply_edge_fade(src, 0.4, 0.0, 0.25, 0.0)
    _oa2, after_view = raster_as_bgra(src)
    assert after_view.tobytes() == before
    assert out is not src

    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_bg, _glass_rect
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack
    w, h = 640, 360
    beige = QColor("#F4EBD8")
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    task = {"timeline": {"width": w, "height": h}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    st = task["style"]
    st["fontsize"] = 72
    st["fontsize_pct"] = 72.0 / float(min(w, h))
    st["pos_x"] = w / 2.0
    st["pos_y"] = h * 0.55
    fx = dict(st.get("fx") or {})
    bgfx = dict(fx.get("bg") or {})
    bgfx["fade"] = {"top": 0.35, "bottom": 0.0, "left": 0.25, "right": 0.0}
    fx["bg"] = bgfx
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    spec = from_style(st)
    event = {
        "text": "Place", "start": 0.0, "end": 2.0,
        "words": [{"word": "Place", "start": 0.0, "end": 2.0}], "id": "seg0",
    }
    lay = layout_event(event, st, spec, w, h)
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(beige)
    roi = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(beige)
    cp = QPainter(canvas)
    cp.drawImage(0, 0, bd)
    if roi is not None:
        cp.drawImage(lay.origin[0], lay.origin[1], roi)
    cp.end()
    _oc, cv = raster_as_bgra(canvas)
    rect = _glass_rect(lay, spec)
    y0 = max(0, int(round(rect.y())))
    x_mid = int(round(rect.x() + rect.width() / 2.0))
    luma = (cv[..., 2].astype("float32")
            + cv[..., 1].astype("float32")
            + cv[..., 0].astype("float32")) / 3.0
    top_luma = float(luma[y0, x_mid])
    assert abs(top_luma - 232.0) <= 1.0, top_luma
    row_luma = luma[y0:y0 + 10, x_mid]
    d_luma = abs(row_luma[1:] - row_luma[:-1])
    assert float(d_luma[:5].mean()) <= 1.5, float(d_luma[:5].mean())

    # Alpha at the first painted row (visible panel top).
    import numpy as np
    _or, rv = raster_as_bgra(roi)
    lx = int(round(rect.x() + rect.width() / 2.0 - lay.origin[0]))
    col_a = rv[:, lx, 3]
    nz = np.where(col_a > 0)[0]
    assert len(nz) > 10
    ly = int(nz[0])
    a_col = col_a[ly:ly + 12].astype("int16")
    assert int(a_col[0]) <= 8, int(a_col[0])

    # Banding: 400 px box, fade 0.5 → 200 px ramp, max slope ~1.9/255.
    from cutora.render.textfx.layout import LayoutResult, LineBox
    spec_t = from_style({"fx": {"bg": {
        "type": "box", "color": "#22D3EE", "alpha": 1.0,
        "radius_em": 0.0, "pad_em": [0.0, 0.0],
        "fade": {"top": 0.5, "bottom": 0.0, "left": 0.0, "right": 0.0},
    }}})
    lay_t = LayoutResult(
        lines=[LineBox("X", 20.0, 0.0, 200.0, 400.0, 400.0)],
        em=40.0, cx=120.0, cy=200.0,
        text_x=20.0, text_y=0.0, text_w=200.0, text_h=400.0,
        origin=(0, 0), roi_w=240, roi_h=400,
        hit_x=20.0, hit_y=0.0, hit_w=200.0, hit_h=400.0,
        seg_id="segT", n_units=1,
    )
    roi_t = raster_bg(lay_t, spec_t, None, 240, 400, skip_blur=True)
    assert roi_t is not None
    _ot, tv = raster_as_bgra(roi_t)
    tcol = tv[0:400, 120, 3].astype("int16")
    tdiffs = abs(tcol[1:] - tcol[:-1])
    assert int(tdiffs.max()) <= 2, int(tdiffs.max())
    assert float(tdiffs[:5].mean()) <= 1.5, float(tdiffs[:5].mean())
    assert int(tcol[0]) <= 8, int(tcol[0])
    assert int(tcol[220]) > 200, int(tcol[220])

    print("T04F_2B_RASTER_OK", flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--panel" in sys.argv:
        raise SystemExit(_panel_suite())
    if "--raster" in sys.argv:
        raise SystemExit(_raster_suite())
    raise SystemExit(0)
